"""Live updates across server processes (docs/WEBSITE_READY_CONTRACT.md C4).

With one server process (the default, ``LIVE_UPDATES_MODE=local``) every
"something changed" signal stays inside the process, exactly as before:
nothing here runs and ``publish`` does nothing.

With ``LIVE_UPDATES_MODE=postgres`` (multi mode, PostgreSQL platform
database) a signal is also sent to every other process with PostgreSQL
``NOTIFY`` on channel ``retailops_live``; each process runs one listener
thread (``LISTEN``) that hands it to the local subscribers. Uses:

- ``team``: a Team change was committed -> wake that store's long-polls
  (``app/team/changes.py``) in every web process.
- ``store_changed`` / ``store_startup``: a store's settings, status or POS
  keys changed in a web process -> the scheduler leader reconciles its jobs
  and re-runs the catch-ups (``app/tenancy/jobs.py``).
- ``jobs``: a job was queued -> idle workers claim it now instead of at
  their next poll.

A process ignores its own messages (it already acted locally). Delivery is
best effort: a listener that loses its connection reconnects with backoff;
anything missed is caught by the existing safety nets (long-polls end after
at most 20 s, the leader reconciles every 5 minutes, workers poll).

LISTEN needs a session connection: behind PgBouncer in transaction mode set
``PLATFORM_DIRECT_URL`` to the database itself (``direct_url``).
"""

from __future__ import annotations

import json
import logging
import queue as _queue
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CHANNEL = "retailops_live"
MAX_PAYLOAD_BYTES = 7900  # PostgreSQL's NOTIFY limit is 8000
ORIGIN = uuid.uuid4().hex[:12]  # this process

Handler = Callable[[Optional[str], Dict[str, Any]], None]

_handlers: Dict[str, List[Handler]] = {}
_handlers_lock = threading.Lock()
_state: Dict[str, Any] = {"listener": None, "publisher": None}
_stats = {"published": 0, "received": 0, "reconnects": 0, "connected": False, "last_error": None}


def mode() -> str:
    """"postgres" when LIVE_UPDATES_MODE=postgres in multi mode with a
    PostgreSQL platform database, else "local"."""
    from app.platform.config import is_multi, pget

    if str(pget("live_updates_mode") or "local").strip().lower() != "postgres" or not is_multi():
        return "local"
    return "postgres" if str(pget("platform_database_url") or "").startswith("postgresql") else "local"


def direct_url() -> str:
    """The platform database URL for session-level features (LISTEN, the
    leader's advisory lock): PLATFORM_DIRECT_URL, else PLATFORM_DATABASE_URL."""
    from app.platform.config import pget

    return str(pget("platform_direct_url") or "").strip() or str(pget("platform_database_url") or "").strip()


def _conninfo(url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def subscribe(event: str, fn: Handler) -> None:
    """Call ``fn(store_code, data)`` for every ``event`` from ANOTHER process."""
    with _handlers_lock:
        lst = _handlers.setdefault(event, [])
        if fn not in lst:
            lst.append(fn)


def unsubscribe(event: str, fn: Handler) -> None:
    with _handlers_lock:
        if fn in _handlers.get(event, []):
            _handlers[event].remove(fn)


def publish(event: str, store_code: Optional[str], data: Optional[Dict[str, Any]] = None) -> bool:
    """Send ``event`` to the other processes (postgres mode). Never raises,
    never blocks: a background thread sends it. False in local mode."""
    try:
        if mode() != "postgres":
            return False
        body = json.dumps({"e": event, "s": store_code, "o": ORIGIN, "d": data or {}}, default=str)
        if len(body.encode()) > MAX_PAYLOAD_BYTES:
            body = json.dumps({"e": event, "s": store_code, "o": ORIGIN, "d": {}})
        _publisher().put(body)
        return True
    except Exception:  # noqa: BLE001 - a lost signal is caught by the safety nets
        logger.debug("live update not published", exc_info=True)
        return False


def dispatch(raw: str) -> bool:
    """Hand one NOTIFY payload to the local subscribers (False = ignored:
    our own message or not ours)."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(msg, dict) or msg.get("o") == ORIGIN:
        return False
    event = str(msg.get("e") or "")
    with _handlers_lock:
        handlers = list(_handlers.get(event, []))
    _stats["received"] += 1
    for fn in handlers:
        try:
            fn(msg.get("s"), msg.get("d") or {})
        except Exception:  # noqa: BLE001 - one subscriber never stops the others
            logger.exception("live update handler for %s failed", event)
    return True


# --- publisher -----------------------------------------------------------------------------


class _Publisher:
    def __init__(self, url: str):
        self.url = url
        self.q: "_queue.Queue[Optional[str]]" = _queue.Queue(maxsize=10000)
        self.thread = threading.Thread(target=self._run, name="livebus-publisher", daemon=True)
        self.thread.start()

    def put(self, body: str) -> None:
        try:
            self.q.put_nowait(body)
        except _queue.Full:
            logger.warning("live updates: publish queue full, dropping a signal")

    def _run(self) -> None:
        import psycopg

        conn = None
        while True:
            body = self.q.get()
            if body is None:
                break
            for attempt in range(2):
                try:
                    if conn is None or conn.closed:
                        conn = psycopg.connect(_conninfo(self.url), autocommit=True, connect_timeout=5)
                    conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, body))
                    _stats["published"] += 1
                    break
                except Exception as exc:  # noqa: BLE001
                    _stats["last_error"] = type(exc).__name__
                    try:
                        if conn is not None:
                            conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    conn = None
                    if attempt:
                        logger.warning("live updates: a signal was not sent (%s)", type(exc).__name__)
        if conn is not None:
            conn.close()

    def stop(self) -> None:
        self.q.put(None)
        self.thread.join(timeout=2)


def _publisher() -> _Publisher:
    pub = _state["publisher"]
    if pub is None:
        with _handlers_lock:
            pub = _state["publisher"]
            if pub is None:
                pub = _state["publisher"] = _Publisher(direct_url())
    return pub


# --- listener ------------------------------------------------------------------------------


class Listener:
    """LISTEN on CHANNEL in a daemon thread; reconnects with backoff."""

    def __init__(self, url: str):
        self.url = url
        self._stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="livebus-listener", daemon=True)

    def start(self) -> "Listener":
        self.thread.start()
        return self

    def _run(self) -> None:
        import psycopg

        delay = 1.0
        while not self._stop.is_set():
            try:
                with psycopg.connect(_conninfo(self.url), autocommit=True, connect_timeout=5) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    _stats["connected"] = True
                    self.ready.set()
                    delay = 1.0
                    while not self._stop.is_set():
                        for note in conn.notifies(timeout=0.5, stop_after=100):
                            dispatch(note.payload)
            except Exception as exc:  # noqa: BLE001 - reconnect
                _stats["connected"] = False
                _stats["last_error"] = type(exc).__name__
                if self._stop.is_set():
                    break
                _stats["reconnects"] += 1
                logger.warning("live updates: listener lost its connection (%s); retrying in %.0f s",
                               type(exc).__name__, delay)
                self._stop.wait(delay)
                delay = min(delay * 2, 30.0)
        _stats["connected"] = False

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=3)


def start() -> bool:
    """Start the listener (postgres mode; lifespan / worker start). False in
    local mode."""
    if mode() != "postgres" or _state["listener"] is not None:
        return False
    _state["listener"] = Listener(direct_url()).start()
    return True


def stop() -> None:
    lst, _state["listener"] = _state["listener"], None
    if lst is not None:
        lst.stop()
    pub, _state["publisher"] = _state["publisher"], None
    if pub is not None:
        pub.stop()


def status() -> Dict[str, Any]:
    """For the System panel and readiness."""
    return {
        "mode": mode(),
        "listening": _state["listener"] is not None and bool(_stats["connected"]),
        "published": _stats["published"],
        "received": _stats["received"],
        "reconnects": _stats["reconnects"],
        "last_error": _stats["last_error"],
    }
