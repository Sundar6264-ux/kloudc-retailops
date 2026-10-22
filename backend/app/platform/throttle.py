"""Login lockout and rate limits (contract 13.2, 13.3, 13.5; owner C).

- Login lockout is kept in the platform database (``login_attempts``), so it
  holds across processes: 5 failures for (store, username) in 15 minutes, or
  30 failures from one client IP in 15 minutes -> locked for 15 minutes.
- Request rate limits (sign-up, code checks, store lookup, integration tests,
  platform writes) are in-memory sliding windows per process
  (``app.directory.ratelimit``): they slow a burst down; the lockout is the
  part that must hold across processes.
"""

import datetime as dt
import threading
from typing import Dict, Optional, Tuple

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.directory.ratelimit import SlidingWindowLimiter
from app.platform.config import pget
from app.platform.models import LoginAttempt, utcnow


def client_ip(request: Request) -> str:
    """The client's IP as a rate-limit key: one rule for every limit
    (app/platform/client_ip.py - the client-IP header only from a trusted
    proxy, IPv6 grouped by /64)."""
    from app.platform.client_ip import client_ip as _client_ip

    return _client_ip(request)


# --- login lockout (platform DB) --------------------------------------------


def _window() -> dt.timedelta:
    return dt.timedelta(minutes=int(pget("login_lockout_minutes") or 15))


def lockout_retry_after(db: Session, store_code: str, username: str, ip: str) -> Optional[int]:
    """Seconds until another try is allowed, or None when not locked.

    Locked when the failures inside the window reach the limit; it opens
    again once the oldest failure that keeps the count at the limit leaves
    the window (a success does not reset an IP lock, only the user lock)."""
    now = utcnow()
    window = _window()
    start = now - window
    user_limit = int(pget("login_lockout_failures") or 5)
    ip_limit = int(pget("login_ip_lockout_failures") or 30)
    uname = (username or "").strip().lower()[:64]
    waits = []
    if user_limit > 0:
        last_ok = db.execute(
            select(func.max(LoginAttempt.at)).where(
                LoginAttempt.store_code == store_code,
                LoginAttempt.username_lower == uname,
                LoginAttempt.ok.is_(True),
                LoginAttempt.at >= start,
            )
        ).scalar()
        floor = max(start, last_ok) if last_ok is not None else start
        fails = list(
            db.execute(
                select(LoginAttempt.at)
                .where(
                    LoginAttempt.store_code == store_code,
                    LoginAttempt.username_lower == uname,
                    LoginAttempt.ok.is_(False),
                    LoginAttempt.at >= floor,
                )
                .order_by(LoginAttempt.at.desc())
                .limit(user_limit)
            ).scalars()
        )
        if len(fails) >= user_limit:
            waits.append(fails[-1] + window - now)
    if ip_limit > 0 and ip:
        fails = list(
            db.execute(
                select(LoginAttempt.at)
                .where(LoginAttempt.client_ip == ip, LoginAttempt.ok.is_(False), LoginAttempt.at >= start)
                .order_by(LoginAttempt.at.desc())
                .limit(ip_limit)
            ).scalars()
        )
        if len(fails) >= ip_limit:
            waits.append(fails[-1] + window - now)
    if not waits:
        return None
    seconds = max(w.total_seconds() for w in waits)
    return max(1, int(seconds + 0.999)) if seconds > 0 else None


def record_attempt(db: Session, store_code: str, username: str, ip: str, ok: bool) -> None:
    db.add(
        LoginAttempt(
            at=utcnow(),
            store_code=(store_code or "?")[:24],
            username_lower=(username or "").strip().lower()[:64],
            client_ip=(ip or "unknown")[:64],
            ok=bool(ok),
        )
    )


def prune_login_attempts(db: Session, days: int = 7) -> int:
    """The platform job: drop attempts older than ``days``."""
    n = db.execute(delete(LoginAttempt).where(LoginAttempt.at < utcnow() - dt.timedelta(days=days))).rowcount
    # Label site hand-off codes (60 s each, docs/LABEL_PRINTING_FEATURE.md 4): kept one day.
    from app.platform.models import LabelHandoff

    db.execute(delete(LabelHandoff).where(LabelHandoff.created_at < utcnow() - dt.timedelta(days=1)))
    db.commit()
    return int(n or 0)


# --- in-memory limiters ------------------------------------------------------

_limiters: Dict[Tuple[str, int, int], SlidingWindowLimiter] = {}
_limiters_lock = threading.Lock()


def limiter(name: str, per_minute: int, per_hour: int) -> SlidingWindowLimiter:
    key = (name, int(per_minute), int(per_hour))
    lim = _limiters.get(key)
    if lim is None:
        with _limiters_lock:
            lim = _limiters.get(key)
            if lim is None:
                lim = SlidingWindowLimiter(int(per_minute), int(per_hour))
                _limiters[key] = lim
    return lim


def reset_limiters() -> None:
    """Tests."""
    with _limiters_lock:
        _limiters.clear()


def check_lookup_rate(ip: str, code: str) -> Optional[int]:
    """The public store lookup's limit (pre-production gap G13). None =
    allowed (and counted), else seconds to wait.

    - The same Store ID from the same address: ``DIRECTORY_STORE_RATE_*``
      (60 a minute, 1000 an hour) - a store's shared Wi-Fi, or every user
      behind one tunnel address, signing in after an outage.
    - A Store ID this address has not asked for in the last hour also counts
      against ``DIRECTORY_RATE_*`` (10 a minute, 100 an hour): how many
      different codes one address may try, so codes still can't be listed
      quickly.
    """
    norm = (code or "").strip().upper()[:32] or "-"
    store_lim = limiter("lookup_store", int(pget("directory_store_rate_per_minute") or 60),
                        int(pget("directory_store_rate_per_hour") or 1000))
    key = f"{ip}|{norm}"
    if store_lim.recent(key) == 0:
        wait = check_rate("lookup", ip, int(pget("directory_rate_per_minute") or 10),
                          int(pget("directory_rate_per_hour") or 100))
        if wait is not None:
            return wait
    return store_lim.check(key)


def check_rate(name: str, key: str, per_minute: int = 0, per_hour: int = 0) -> Optional[int]:
    """None = allowed (and counted), else seconds to wait."""
    return limiter(name, per_minute, per_hour).check(key)
