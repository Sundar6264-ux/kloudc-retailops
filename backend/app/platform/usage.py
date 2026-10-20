"""Usage metering per store (docs/WEBSITE_READY_CONTRACT.md, interface I3; track B).

Counters (``record``) and the last-activity time (``touch_activity``) are
added up in memory and written to the platform database every 60 seconds
by a flush thread (``start()`` / ``stop()`` from the app lifespan, track C)
into ``store_usage_daily (store_code, day, metric, value)``. Gauges (users,
items, storage bytes, database bytes) are measured once a day per store by
the ``usage_gauges`` job (``register_periodic``) or on demand with
``measure_gauges(code)``.

Rules for callers:
- ``record`` and ``touch_activity`` are cheap and never raise, never open a
  database connection on the caller's thread, and are safe from any thread.
- Without a store (``code=None`` and no store context), or on a server
  without a platform database (single mode), they do nothing.
- Counters add up per store and UTC day; gauges are measured by the job,
  never recorded by callers.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Counters: callers add to them.
ORDERS_SYNCED = "orders_synced"  # order lines written by a POS sales sync
POS_API_CALLS = "pos_api_calls"  # HTTP requests sent to a POS API (any POS)
AI_CALLS = "ai_calls"  # requests sent to the AI provider
COUNTERS = (ORDERS_SYNCED, POS_API_CALLS, AI_CALLS)

# Gauges: measured by the usage job (callers never record these).
USERS = "users"  # store logins
ITEMS = "items"  # items in the latest catalog snapshot
STORAGE_BYTES = "storage_bytes"  # app.storage bytes_used() of the store
DATABASE_BYTES = "database_bytes"  # pg_database_size of the store database
GAUGES = (USERS, ITEMS, STORAGE_BYTES, DATABASE_BYTES)

LAST_ACTIVITY_AT = "last_activity_at"  # newest signed-in request (UTC ISO time)

METRICS = COUNTERS + GAUGES + (LAST_ACTIVITY_AT,)

FLUSH_SECONDS = 60
HISTORY_DAYS = 30
ACTIVITY_EVERY_SECONDS = 60  # at most one activity note per store per minute
MAX_BUFFERED_KEYS = 20000  # a platform database that stays down never grows memory without end

# --- in-memory buffer ---------------------------------------------------------

_lock = threading.Lock()
_counts: Dict[Tuple[str, dt.date, str], int] = {}
_activity: Dict[str, int] = {}  # store code -> newest epoch second
_activity_noted: Dict[str, float] = {}  # store code -> monotonic time of the last note
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _enabled() -> bool:
    try:
        from app.platform.db import is_configured

        return is_configured()
    except Exception:  # noqa: BLE001 - settings not loadable: metering is off
        return False


def _code(code: Optional[str]) -> Optional[str]:
    if code:
        return str(code).strip().upper() or None
    try:
        from app.tenancy.context import current_store_code

        return current_store_code()
    except Exception:  # noqa: BLE001 - no tenancy layer: no store
        return None


def record(metric: str, n: int = 1, *, code: Optional[str] = None) -> None:
    """Add ``n`` to counter ``metric`` of store ``code`` (default: the
    current store) for today (UTC). Never raises; unknown metrics and
    non-positive amounts are ignored."""
    try:
        if metric not in COUNTERS:
            return
        n = int(n)
        if n <= 0 or not _enabled():
            return
        store = _code(code)
        if not store:
            return
        key = (store, _today(), metric)
        with _lock:
            if key not in _counts and len(_counts) >= MAX_BUFFERED_KEYS:
                return
            _counts[key] = _counts.get(key, 0) + n
    except Exception:  # noqa: BLE001 - metering never breaks the caller
        logger.debug("usage.record failed", exc_info=True)


def touch_activity(*, code: Optional[str] = None) -> None:
    """Note that someone used store ``code`` now (at most one note per store
    per minute is kept; written with the next flush). Never raises."""
    try:
        if not _enabled():
            return
        store = _code(code)
        if not store:
            return
        mono = time.monotonic()
        with _lock:
            last = _activity_noted.get(store)
            if last is not None and mono - last < ACTIVITY_EVERY_SECONDS:
                return
            _activity_noted[store] = mono
            _activity[store] = max(_activity.get(store, 0), int(time.time()))
    except Exception:  # noqa: BLE001
        logger.debug("usage.touch_activity failed", exc_info=True)


# --- writing --------------------------------------------------------------------


def _upsert(db, rows: Iterable[Tuple[str, dt.date, str, int]], how: str) -> None:
    """``how``: "add" (counters), "set" (gauges) or "max" (last activity)."""
    from sqlalchemy import func

    from app.platform.data_models import StoreUsageDaily
    from app.platform.models import utcnow

    rows = list(rows)
    if not rows:
        return
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    now = utcnow()
    stmt = insert(StoreUsageDaily).values(
        [{"store_code": c, "day": d, "metric": m, "value": int(v), "updated_at": now} for c, d, m, v in rows]
    )
    current = StoreUsageDaily.__table__.c.value
    if how == "add":
        new_value = current + stmt.excluded.value
    elif how == "max":
        new_value = func.max(current, stmt.excluded.value) if dialect == "sqlite" else func.greatest(current, stmt.excluded.value)
    else:
        new_value = stmt.excluded.value
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["store_code", "day", "metric"],
            set_={"value": new_value, "updated_at": stmt.excluded.updated_at},
        )
    )


def flush() -> int:
    """Write what is buffered (one transaction). On failure the numbers go
    back into the buffer for the next try. Returns the rows written."""
    with _lock:
        counts = dict(_counts)
        activity = dict(_activity)
        _counts.clear()
        _activity.clear()
    if not counts and not activity:
        return 0
    try:
        from app.platform.db import platform_session

        day = _today()
        with platform_session() as db:
            _upsert(db, [(c, d, m, v) for (c, d, m), v in counts.items()], "add")
            _upsert(db, [(c, day, LAST_ACTIVITY_AT, v) for c, v in activity.items()], "max")
            db.commit()
        return len(counts) + len(activity)
    except Exception:  # noqa: BLE001 - keep the numbers for the next flush
        logger.warning("usage flush failed; kept for the next try", exc_info=True)
        with _lock:
            for key, v in counts.items():
                if key in _counts or len(_counts) < MAX_BUFFERED_KEYS:
                    _counts[key] = _counts.get(key, 0) + v
            for code, v in activity.items():
                _activity[code] = max(_activity.get(code, 0), v)
        return 0


def write_gauges(code: str, values: Dict[str, int], *, day: Optional[dt.date] = None) -> None:
    """Store today's gauge values of one store (the usage job)."""
    from app.platform.db import platform_session

    rows = [(code.upper(), day or _today(), k, int(v)) for k, v in values.items() if k in GAUGES and v is not None]
    with platform_session() as db:
        _upsert(db, rows, "set")
        db.commit()


# --- measuring the gauges -----------------------------------------------------------


def _store_engine(code: str):
    """A short-lived engine on the store database (works for any status)."""
    from sqlalchemy import select

    from app.platform import bridge
    from app.platform.db import make_engine, platform_session
    from app.platform.models import Store

    with platform_session() as db:
        store = db.execute(select(Store).where(Store.code == code.upper())).scalar_one_or_none()
        if store is None:
            raise LookupError(code)
        url = bridge.store_url(store.db_name, store.db_host_ref)
    kwargs = {} if url.startswith("sqlite") else {"pool_size": 1, "max_overflow": 0}
    return make_engine(url, **kwargs)


def measure_gauges(code: str) -> Dict[str, int]:
    """Measure users, items, storage bytes and database bytes of one store
    and store them for today. Each measure that fails is left out."""
    from sqlalchemy import text

    code = code.upper()
    out: Dict[str, int] = {}
    eng = _store_engine(code)
    try:
        with eng.connect() as conn:
            for metric, sql in (
                (USERS, "SELECT count(*) FROM users"),
                (ITEMS, "SELECT count(DISTINCT item_id) FROM inventory_snapshots WHERE snapshot_date = "
                        "(SELECT max(snapshot_date) FROM inventory_snapshots)"),
            ):
                try:
                    out[metric] = int(conn.execute(text(sql)).scalar() or 0)
                except Exception:  # noqa: BLE001 - a missing table is "not measured"
                    conn.rollback()
            if eng.dialect.name == "postgresql":
                try:
                    out[DATABASE_BYTES] = int(conn.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
                except Exception:  # noqa: BLE001
                    conn.rollback()
    finally:
        eng.dispose()
    try:
        from app.storage import get_storage

        out[STORAGE_BYTES] = int(get_storage().bytes_used(code=code))
    except Exception:  # noqa: BLE001
        logger.warning("storage bytes of %s not measured", code, exc_info=True)
    write_gauges(code, out)
    return out


def measure_all() -> Dict[str, Dict[str, int]]:
    """Gauges of every active or suspended store (the platform-level job)."""
    from sqlalchemy import select

    from app.platform.db import platform_session
    from app.platform.models import Store

    with platform_session() as db:
        codes = list(db.execute(select(Store.code).where(Store.status.in_(("active", "suspended")))).scalars())
    out = {}
    for code in codes:
        try:
            out[code] = measure_gauges(code)
        except Exception:  # noqa: BLE001 - one store never stops the others
            logger.warning("usage gauges of %s failed", code, exc_info=True)
    return out


# --- reading ------------------------------------------------------------------------


def _iso_epoch(v: Optional[int]) -> Optional[str]:
    if not v:
        return None
    return dt.datetime.fromtimestamp(int(v), dt.timezone.utc).isoformat()


def snapshot(code: str, *, days: int = HISTORY_DAYS) -> Dict[str, Any]:
    """{"metrics": {metric: value}, "days": [{"day", metric: value...}],
    "updated_at"} for one store, read from the platform database only.

    ``metrics``: each counter added up over the last ``days`` days, each
    gauge at its newest measurement, ``last_activity_at`` as an ISO time.
    ``days``: newest first, only days that have a number."""
    from sqlalchemy import select

    from app.platform.data_models import StoreUsageDaily
    from app.platform.db import platform_session

    code = (code or "").strip().upper()
    if not _enabled():
        return {"metrics": {}, "days": [], "updated_at": None, "window_days": days}
    since = _today() - dt.timedelta(days=max(1, days) - 1)
    with platform_session() as db:
        rows = list(
            db.execute(
                select(StoreUsageDaily)
                .where(StoreUsageDaily.store_code == code, StoreUsageDaily.day >= since)
                .order_by(StoreUsageDaily.day.desc())
            ).scalars()
        )
        latest_gauges: Dict[str, int] = {}
        for metric in GAUGES + (LAST_ACTIVITY_AT,):
            row = db.execute(
                select(StoreUsageDaily)
                .where(StoreUsageDaily.store_code == code, StoreUsageDaily.metric == metric)
                .order_by(StoreUsageDaily.day.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is not None:
                latest_gauges[metric] = int(row.value)
    metrics: Dict[str, Any] = {m: 0 for m in COUNTERS}
    by_day: Dict[dt.date, Dict[str, Any]] = {}
    updated: Optional[dt.datetime] = None
    for r in rows:
        if r.metric in COUNTERS:
            metrics[r.metric] += int(r.value)
        entry = by_day.setdefault(r.day, {"day": r.day.isoformat()})
        entry[r.metric] = _iso_epoch(r.value) if r.metric == LAST_ACTIVITY_AT else int(r.value)
        if updated is None or (r.updated_at and r.updated_at > updated):
            updated = r.updated_at
    for g in GAUGES:
        metrics[g] = latest_gauges.get(g)
    metrics[LAST_ACTIVITY_AT] = _iso_epoch(latest_gauges.get(LAST_ACTIVITY_AT))
    return {
        "metrics": metrics,
        "days": [by_day[d] for d in sorted(by_day, reverse=True)],
        "updated_at": updated.isoformat() if updated else None,
        "window_days": days,
    }


# --- lifespan -----------------------------------------------------------------------


def _loop() -> None:
    while not _stop.wait(FLUSH_SECONDS):
        flush()


def start() -> None:
    """Start the flush thread (lifespan; does nothing without a platform DB)."""
    global _thread
    if not _enabled():
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="usage-flush", daemon=True)
        _thread.start()


def stop() -> None:
    """Flush what is buffered and stop (lifespan shutdown)."""
    global _thread
    _stop.set()
    t = _thread
    if t is not None:
        t.join(timeout=5)
    _thread = None
    if _enabled():
        flush()


def _reset_for_tests() -> None:
    with _lock:
        _counts.clear()
        _activity.clear()
        _activity_noted.clear()


# --- the daily job --------------------------------------------------------------------


def _register_jobs() -> None:
    from app import jobqueue

    @jobqueue.register("usage_gauges", max_attempts=2, timeout_seconds=600)
    def usage_gauges_job(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Per store (in the store's context) or for every store when the
        payload names none and there is no store context."""
        code = payload.get("store_code") or _code(None)
        if code:
            return {"store_code": code, "gauges": measure_gauges(code)}
        return {"stores": len(measure_all())}

    # 02:30 store time: after the nightly sales sync, before people start.
    jobqueue.register_periodic("usage_gauges", hour=2, minute=30, per_store=True)


_register_jobs()


__all__ = (
    "AI_CALLS", "COUNTERS", "DATABASE_BYTES", "GAUGES", "ITEMS", "LAST_ACTIVITY_AT", "METRICS",
    "ORDERS_SYNCED", "POS_API_CALLS", "STORAGE_BYTES", "USERS",
    "flush", "measure_all", "measure_gauges", "record", "snapshot", "start", "stop", "touch_activity",
    "write_gauges",
)
