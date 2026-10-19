"""Isolation for the parity harness: environment, network guard, thread guard.

The harness boots the real app in-process against a COPY of a database. This
module makes sure that boot can never reach the outside world or the live
store's files:

* `harness_env()` - the environment set before `app` is imported (settings
  are read once). Schedulers off, every outside-service secret blank, upload
  and flyer folders inside the scratch directory, a fixed Insights hashing
  key (so nothing creates insights_hmac.key), and no .env: the process runs
  with the scratch directory as its working directory, where there is none.
* `install_network_guard()` - every outbound socket connection (Toast, Whapi,
  SMTP, Firebase, Anthropic, DNS) raises instead of connecting. Unix sockets
  stay allowed (the event loop's self-pipe). Blocked attempts are counted
  and reported, so a code path that tries the network shows up in the
  recording instead of silently changing it.
* `install_thread_guard()` - background threads started by app code (the
  Open checks refresh, the Insights fill, the Fix Issues forecast refresh)
  are recorded but not run: they would call Toast (blocked anyway) and
  write job rows at a time that depends on thread scheduling, which would
  make two recordings differ for no reason. Threads started by the test
  client / anyio itself run normally.
"""

import os
import socket
import threading
from typing import Dict, List

# Every setting that could reach an outside service, or the live store's
# files, pinned to "off" / a scratch path. Mirrors tests/conftest.py.
BLANK_SECRETS = {
    "TOAST_CLIENT_ID": "",
    "TOAST_CLIENT_SECRET": "",
    "TOAST_RESTAURANT_GUID": "",
    "TOAST_ANALYTICS_CLIENT_ID": "",
    "TOAST_ANALYTICS_CLIENT_SECRET": "",
    "WHAPI_API_TOKEN": "",
    "WHAPI_CHAT_ID": "",
    "WHAPI_STORE_CHECKLIST_GROUP_ID": "",
    "WHAPI_KITCHEN_CHECKLIST_GROUP_ID": "",
    "GMAIL_ADDRESS": "",
    "GMAIL_APP_PASSWORD": "",
    "ANTHROPIC_API_KEY": "",
    "FIREBASE_CREDENTIALS_FILE": "",
}


def harness_env(db_path: str, scratch_dir: str, db_url: str = "") -> Dict[str, str]:
    """`db_url`: record against this database instead of the SQLite file -
    the PostgreSQL copy of it (parity_record.py --pg-url). The PostgreSQL
    driver connects through libpq (C), which the Python socket guard below
    does not see; the URL is checked to be on this machine instead."""
    env = dict(BLANK_SECRETS)
    env.update(
        {
            "DATABASE_URL": db_url or f"sqlite:///{db_path}",
            "SCHEDULER_ENABLED": "false",
            "NOTIFICATIONS_ENABLED": "false",
            "TEAM_JOBS_ENABLED": "false",
            "TEAM_UPLOADS_DIR": os.path.join(scratch_dir, "uploads", "team"),
            "INVOICE_UPLOADS_DIR": os.path.join(scratch_dir, "uploads", "invoices"),
            "FLYERS_DIR": os.path.join(scratch_dir, "flyers"),
            # Fixed so the Insights code never generates (and writes) a key.
            "INSIGHTS_HMAC_SECRET": "parity-harness-fixed-key-not-a-secret",
            "INSIGHTS_HMAC_KEY_FILE": os.path.join(scratch_dir, "insights_hmac.key"),
            # No built website: nothing to serve, nothing read from frontend/.
            "FRONTEND_DIST_DIR": os.path.join(scratch_dir, "no-frontend"),
            # The store's zone, pinned (same as prod's .env and the default).
            "TOAST_TIMEZONE": "America/New_York",
            # The process-local zone too: naive datetime.now() / date.today()
            # follow it, so a recording doesn't depend on the machine's zone.
            "TZ": "America/New_York",
            # set/dict iteration order of strings must not vary between runs.
            "PYTHONHASHSEED": "0",
        }
    )
    return env


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------

_blocked: List[str] = []
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


class NetworkBlocked(OSError):
    pass


def _is_unix(sock) -> bool:
    return getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX


def _guarded_connect(self, address):
    if _is_unix(self):
        return _real_connect(self, address)
    _blocked.append(f"connect {address!r}")
    raise NetworkBlocked(f"parity harness: outbound connection to {address!r} blocked")


def _guarded_connect_ex(self, address):
    if _is_unix(self):
        return _real_connect_ex(self, address)
    _blocked.append(f"connect {address!r}")
    raise NetworkBlocked(f"parity harness: outbound connection to {address!r} blocked")


def _guarded_getaddrinfo(host, *args, **kwargs):
    _blocked.append(f"dns {host!r}")
    raise NetworkBlocked(f"parity harness: DNS lookup of {host!r} blocked")


def install_network_guard() -> None:
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.getaddrinfo = _guarded_getaddrinfo


def blocked_attempts() -> List[str]:
    return list(_blocked)


# ---------------------------------------------------------------------------
# Thread guard
# ---------------------------------------------------------------------------

_suppressed: List[str] = []
_real_thread_start = threading.Thread.start


def _from_app(thread: threading.Thread) -> bool:
    target = getattr(thread, "_target", None)
    module = getattr(target, "__module__", "") or ""
    return module == "app" or module.startswith("app.")


def _guarded_start(self):
    if _from_app(self):
        _suppressed.append(f"{self.name} ({getattr(self._target, '__qualname__', '?')})")
        return None
    return _real_thread_start(self)


def install_thread_guard() -> None:
    threading.Thread.start = _guarded_start


def suppressed_threads() -> List[str]:
    return list(_suppressed)
