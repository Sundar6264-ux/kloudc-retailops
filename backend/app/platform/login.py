"""One login for everyone: Store ID + username + password (contract 4.5;
owner C). Used by ``app/routers/auth.py``.

Order of checks, with one bcrypt check on every path (a dummy hash when the
user or store is unknown) and one message for every credential failure:

1. normalise ``store_code`` (single mode: blank or this server's STORE_CODE)
2. lockout (platform database) -> 429 ``locked``
3. ``PLATFORM`` -> master login (platform session)
4. unknown store / user / wrong password -> 401
5. good credentials but: store on another server -> 409 ``store_moved``;
   suspended -> 403 ``store_suspended``; pending / provisioning / failed ->
   403 ``store_not_ready``; archived -> 401 (as unknown)
6. session in the store's database; reply = the M2 ``/me`` body + scope
7. ``login_attempts`` row + audit ``login.ok`` / ``login.failed``
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from app.auth import create_session, verify_password
from app.models import User
from app.platform import masters
from app.platform.audit import audit
from app.platform.config import is_multi, login_code
from app.platform.errors import PlatformError

logger = logging.getLogger(__name__)

# One plain sentence for an unknown store, an unknown user and a wrong
# password alike (owner, 2026-10; replaces G30's per-case wording): the
# answer never says which of the three was wrong, so it can't be used to
# find out which Store IDs exist.
BAD_LOGIN = "That Store ID, username or password isn't right. Check all three and try again."
LEGACY_BAD_LOGIN = "Incorrect username or password"  # single mode, old clients (no store_code)


@dataclass
class LoginResult:
    scope: str  # "store" | "platform"
    token: str
    expires_at: dt.datetime
    body: Dict[str, Any] = field(default_factory=dict)


def _bad(legacy: bool = False) -> HTTPException:
    return HTTPException(status_code=401, detail=LEGACY_BAD_LOGIN if legacy else BAD_LOGIN)


def _err(status: int, code: str, message: Optional[str] = None, **extra) -> PlatformError:
    err = PlatformError(status, code, message, **extra)
    # Old clients read FastAPI's "detail".
    err.extra.setdefault("detail", err.message)
    return err


def _platform_ready() -> bool:
    from app.platform.db import is_configured

    return is_configured()


def _normalize(raw: Optional[str]) -> Optional[str]:
    from app.platform.codes import normalize_code

    if raw is None or not str(raw).strip():
        return ""
    return normalize_code(raw)


def _find_user(db, username: str) -> Optional[User]:
    return db.execute(select(User).where(func.lower(User.username) == (username or "").strip().lower())).scalar_one_or_none()


def _store_actor(code: str, username: str) -> str:
    return f"store:{code}:{(username or '').strip().lower()[:64]}"


class _Attempts:
    """Lockout check + recording against the platform database (skipped on a
    single-store server without one)."""

    def __init__(self, store_code: str, username: str, ip: str):
        self.code = store_code or "?"
        self.username = username
        self.ip = ip
        self.enabled = _platform_ready()

    def check(self) -> None:
        if not self.enabled:
            return
        from app.platform.db import platform_session
        from app.platform.throttle import lockout_retry_after

        with platform_session() as db:
            wait = lockout_retry_after(db, self.code, self.username, self.ip)
        if wait is not None:
            audit("login.locked", actor_type="platform_user" if self.code == login_code() else "store_user",
                  actor_name=_store_actor(self.code, self.username), store_code=None if self.code == login_code() else self.code,
                  details={"retry_after": wait}, client_ip=self.ip)
            raise _err(429, "locked", headers={"Retry-After": str(wait)}, retry_after=wait)

    def record(self, ok: bool, *, platform: bool = False, reason: Optional[str] = None) -> None:
        if not self.enabled:
            return
        from app.platform.db import platform_session
        from app.platform.throttle import record_attempt

        action = ("platform_login" if platform else "login") + (".ok" if ok else ".failed")
        try:
            with platform_session() as db:
                record_attempt(db, self.code, self.username, self.ip, ok)
                audit(action, actor_type="platform_user" if platform else "store_user",
                      actor_name=(f"platform:{self.username.strip().lower()}" if platform else _store_actor(self.code, self.username)),
                      store_code=None if platform else (self.code if self.code != "?" else None),
                      details={"reason": reason} if reason else {}, client_ip=self.ip, db=db)
                db.commit()
        except Exception:  # noqa: BLE001 - a login must not fail because the audit write did
            logger.exception("login attempt not recorded")


def login(
    *,
    store_code: Optional[str],
    username: str,
    password: str,
    totp: Optional[str],
    client_ip: str,
    me_out: Callable[[User], Dict[str, Any]],
) -> LoginResult:
    norm = _normalize(store_code)
    if not is_multi():
        return _single_login(norm, store_code, username, password, client_ip, me_out)

    attempts = _Attempts(norm or "?", username, client_ip)
    attempts.check()
    if norm is None or norm == "":
        masters.dummy_verify(password)
        attempts.record(False, reason="no_store")
        raise _bad()
    if norm == login_code():
        return _platform_login(username, password, totp, client_ip, attempts)
    return _store_login(norm, username, password, client_ip, attempts, me_out)


# --- single mode (M2 behaviour + Store ID check) ---------------------------------------


def _single_login(norm, raw_code, username, password, client_ip, me_out) -> LoginResult:
    from app.config import get_settings
    from app.db import SessionLocal

    legacy = raw_code is None or not str(raw_code).strip()
    own = (get_settings().store_code or "").strip().upper()
    attempts = _Attempts(own or "_", username, client_ip)
    attempts.check()
    if not legacy and (norm is None or norm != own):
        masters.dummy_verify(password)
        attempts.record(False, reason="wrong_store")
        raise _bad()
    db = SessionLocal()
    try:
        user = _find_user(db, username)
        if user is None:
            masters.dummy_verify(password)
            attempts.record(False, reason="unknown_user")
            raise _bad(legacy)
        if not verify_password(password, user.password_hash):
            attempts.record(False, reason="bad_password")
            raise _bad(legacy)
        token, expires = create_session(db, user.id)
        body = me_out(user)
        attempts.record(True)
    finally:
        db.close()
    body.update({"scope": "store", "store_code": body.get("store_code") or (own or None), "api_base_url": None})
    return LoginResult("store", token, expires, body)


# --- masters ----------------------------------------------------------------------------------


def _platform_login(username, password, totp, client_ip, attempts: _Attempts) -> LoginResult:
    from app.platform.db import platform_session

    if not _platform_ready():
        masters.dummy_verify(password)
        raise _bad()
    with platform_session() as db:
        user, reason = masters.authenticate(db, username, password, totp)
        if user is None:
            if reason == "totp_required":
                # Password was right: ask for the code (not a failed attempt).
                raise _err(401, "totp_required")
            attempts.record(False, platform=True, reason=reason)
            raise _bad()
        token, expires = masters.create_platform_session(db, user, client_ip)
        db.commit()
        view = masters.user_view(user)
    attempts.record(True, platform=True)
    return LoginResult(
        "platform",
        token,
        expires,
        {"scope": "platform", "store_code": login_code(), "user": view, "api_base_url": None,
         "totp_setup_required": bool(_require_totp()) and not view["totp_enrolled"]},
    )


def _require_totp() -> bool:
    from app.platform.config import require_totp

    return require_totp()


# --- stores -------------------------------------------------------------------------------------


def _store_login(code, username, password, client_ip, attempts: _Attempts, me_out) -> LoginResult:
    from app.platform.bridge import tenancy

    t = tenancy(required=True)
    ref = t.registry.find_store(code) if hasattr(t, "registry") else None
    if ref is None:
        try:
            ref = t.get_store(code)
        except Exception:  # noqa: BLE001 - UnknownStore / registry down: same answer
            ref = None
    if ref is None or ref.status == "archived":
        masters.dummy_verify(password)
        attempts.record(False, reason="unknown_store")
        raise _bad()
    if not t.node_serves(ref):
        # Its data is on its own server; this node can't check the password.
        masters.dummy_verify(password)
        from app.tenancy.routing import store_moved_body

        body = store_moved_body(ref)
        raise _err(409, "store_moved", body.get("message"), api_base_url=body.get("api_base_url"), store_code=ref.code)
    if ref.status in ("pending", "provisioning", "failed"):
        ok = _owner_matches_signup(code, username, password)
        attempts.record(False, reason="store_not_ready" if ok else "bad_password")
        if not ok:
            raise _bad()
        raise _err(403, "store_not_ready")
    # active / suspended: the user lives in the store's own database.
    with t.tenant_session(code, allow_inactive=True) as db:
        user = _find_user(db, username)
        if user is None:
            masters.dummy_verify(password)
            attempts.record(False, reason="unknown_user")
            raise _bad()
        if not verify_password(password, user.password_hash):
            attempts.record(False, reason="bad_password")
            raise _bad()
        if ref.status == "suspended":
            attempts.record(False, reason="store_suspended")
            msg = _maintenance_message(code)
            raise _err(403, "store_suspended", msg)
        token, expires = create_session(db, user.id)
        body = me_out(user)
    attempts.record(True)
    body.update({"scope": "store", "store_code": ref.code, "api_base_url": ref.api_base_url})
    return LoginResult("store", token, expires, body)


def _owner_matches_signup(code: str, username: str, password: str) -> bool:
    """A store still being set up: only its owner (sign-up credentials) gets
    told so; anyone else gets the usual 401."""
    from app.platform.db import platform_session
    from app.platform.models import SignupRequest, Store

    with platform_session() as db:
        store = db.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
        req = db.get(SignupRequest, store.signup_request_id) if store is not None and store.signup_request_id else None
        if req is None or (req.owner_username or "").lower() != (username or "").strip().lower():
            masters.dummy_verify(password)
            return False
        return verify_password(password, req.owner_password_hash or "!")


def _maintenance_message(code: str) -> Optional[str]:
    from app.platform.db import platform_session
    from app.platform.models import Store

    try:
        with platform_session() as db:
            store = db.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
            return store.maintenance_message if store is not None else None
    except Exception:  # noqa: BLE001
        return None


# --- support sessions ----------------------------------------------------------------------------


def support_login(token: str, me_out: Callable[[User], Dict[str, Any]]) -> LoginResult:
    """The website signs in with a master's support token (a store session
    made by ``POST /api/platform/stores/{code}/support-session``)."""
    from app.platform.bridge import tenancy
    from app.platform.masters import token_prefix

    if not is_multi():
        raise HTTPException(status_code=404, detail="Not found")
    t = tenancy(required=True)
    code = token_prefix(token)
    if not code or code == login_code():
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    try:
        with t.tenant_session(code) as db:
            import app.auth as store_auth

            lookup = getattr(store_auth, "lookup_session", None)
            if lookup is None:
                raise HTTPException(status_code=401, detail="Session expired or invalid")
            row = lookup(db, token)
            user = db.get(User, row.user_id) if row is not None else None
            if user is None:
                raise HTTPException(status_code=401, detail="Session expired or invalid")
            body = me_out(user)
            expires = row.expires_at
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - unknown / inactive store
        raise HTTPException(status_code=401, detail="Session expired or invalid") from None
    body.update({"scope": "store", "store_code": code, "support": True})
    return LoginResult("store", token, expires, body)
