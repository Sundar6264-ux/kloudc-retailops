"""In-memory cache for the heavy forecast reports (docs/BATCH_OCT15_CONTRACT.md,
speed #3 / #4).

The Today report (reorder-candidates at 30/7, also behind Close on the
Critical sheet) and the Vendor Calendar's need-to-order counts re-run the
demand forecast on every request: 1.5-3 s of CPU each on prod, and the one
uvicorn process shares one GIL, so two of them at once take twice as long.
Their inputs change a few times a day (sales sync, an inventory upload, an
order placed), not per request - so a result is kept here and reused until
one of these happens:

  - a write to a table the reports read, from anywhere in this process
    (sales sync, inventory upload / inventory from Toast, Mark placed,
    "Order already placed", invoices, Delivery Review, category edits, a
    vendor schedule, the Inventory-from-Toast switch): seen on the engine's
    COMMIT, so the next request already reads the new data (_TRACKED_TABLES);
  - the cheap data fingerprint changed (data_version: max ids + the sync
    jobs' runs - catches a script writing the database from outside this
    process);
  - the live Toast stock overlay refreshed or was switched (live token);
  - TTL_SECONDS passed (a backstop: the reports also depend on the date);
  - invalidate() was called.

Single process only (prod runs one uvicorn worker - critic §0.2): the cache,
its locks and the invalidation are per process. Two workers would each keep
their own copy and each would only see its own writes.

Callers get the SAME object every time - never mutate a cached value (copy
the parts you change, as ordering.get_reorder_candidates does for the
critical-dismissal annotation).
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db import Base
from app.tenancy import is_multi_tenant, register_engine_hook, tenant_local, tenant_lock

logger = logging.getLogger(__name__)

TTL_SECONDS = 300  # ~5 min (owner: speed first; data changes invalidate sooner)
MAX_ENTRIES = 32  # a handful of report settings + one entry per scheduled vendor

# Tables the cached reports read. A committed INSERT/UPDATE/DELETE on any of
# them through this process's engine ends every cached result.
_TRACKED_TABLES = frozenset(
    {
        "orders",
        "inventory_snapshots",
        "placed_orders",
        "purchase_log",
        "invoice_submissions",
        "vendor_schedules",
        "category_overrides",
        "setting_changes",
    }
)
# Sync jobs whose runs are part of the fingerprint (a backfill script started
# outside the server writes these too).
_SYNC_JOBS = ("toast_sales_sync", "sales_repull", "inventory_upload", "inventory_api_snapshot")

_WRITE_RE = re.compile(
    r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+[\"`\[]?(\w+)",
    re.IGNORECASE,
)

_lock = tenant_lock("report_cache._lock")
# Per store (docs/PLATFORM_CONTRACT.md 6.5): one store's writes end only that
# store's cached reports.
_entries: "OrderedDict[Hashable, Tuple[float, int, Any, Any]]" = tenant_local(
    "report_cache._entries", OrderedDict
)  # key -> (at, gen, version, value)
_key_locks: dict = tenant_local("report_cache._key_locks", dict)
_state = tenant_local("report_cache._state", lambda: {"generation": 0, "hits": 0, "misses": 0})


def generation() -> int:
    with _lock:
        return _state["generation"]


def invalidate(reason: str = "", store: Optional[str] = None) -> None:
    """Ends every cached result of the current store (or of `store`; the
    next request recomputes)."""
    with _lock.of(store):
        _state.of(store)["generation"] += 1
        _entries.of(store).clear()
    if reason:
        logger.debug("Report cache cleared: %s", reason)


def stats() -> dict:
    with _lock:
        return {"entries": len(_entries), "generation": _state["generation"], "hits": _state["hits"],
                "misses": _state["misses"]}


def written_table(statement: str) -> Optional[str]:
    """The table a write statement changes (lowercase), else None."""
    if not statement or statement.lstrip()[:1] in ("S", "s", "P", "p"):  # SELECT / PRAGMA: the bulk
        return None
    match = _WRITE_RE.match(statement)
    return match.group(1).lower() if match else None


# --- Invalidation hooks (registered once, at import) ------------------------------------------


def _after_cursor_execute(conn, _cursor, statement, _parameters, _context, _executemany):
    table = written_table(statement)
    if table is not None and table in _TRACKED_TABLES:
        conn.info["report_cache_dirty"] = True


def _on_commit(conn):
    if conn.info.pop("report_cache_dirty", False):
        # The engine's own store (multi mode); the single store otherwise.
        invalidate("tracked table committed", store=getattr(conn.engine, "_report_cache_store", None))


def _on_rollback(conn):
    conn.info.pop("report_cache_dirty", None)


def _on_schema_change(*_args, **_kwargs):
    # A new or dropped database (tests recreate theirs for every test): ids
    # and counts restart, so nothing cached may survive it.
    invalidate("tables created/dropped")


def _attach(code: str, engine) -> None:
    """Engine hook (app.tenancy.register_engine_hook): every store engine,
    and the single-mode engine, gets the invalidation listeners."""
    engine._report_cache_store = code if is_multi_tenant() else None
    if not event.contains(engine, "commit", _on_commit):
        event.listen(engine, "after_cursor_execute", _after_cursor_execute)
        event.listen(engine, "commit", _on_commit)
        event.listen(engine, "rollback", _on_rollback)


register_engine_hook(_attach)
event.listen(Base.metadata, "after_create", _on_schema_change)
event.listen(Base.metadata, "after_drop", _on_schema_change)


# --- Fingerprint ------------------------------------------------------------------------------


def _live_token() -> Any:
    """Which live Toast stock the overlay is serving (None = switched off)."""
    try:
        from app.services.inventory_api import live_stock_meta

        meta = live_stock_meta()
    except Exception:  # noqa: BLE001 - e.g. the settings table not migrated yet
        return "unknown"
    return None if meta is None else meta.get("as_of") or False


def data_version(db: Session) -> Tuple:
    """A cheap (~1 ms) fingerprint of the reports' inputs. Writes made in
    this process are caught on commit anyway; this is for the rest."""
    try:
        jobs = ", ".join(f"'{name}'" for name in _SYNC_JOBS)
        row = db.execute(
            text(
                "SELECT (SELECT max(id) FROM orders), (SELECT max(id) FROM inventory_snapshots), "
                "(SELECT max(snapshot_date) FROM inventory_snapshots), "
                "(SELECT count(*) || ':' || ifnull(max(id), 0) FROM placed_orders), "
                "(SELECT max(id) FROM purchase_log), (SELECT max(id) FROM invoice_submissions), "
                f"(SELECT max(id) || ':' || ifnull(max(finished_at), '') FROM job_runs WHERE job_name IN ({jobs}))"
            )
        ).first()
        version = tuple(row) if row is not None else ()
    except Exception:  # noqa: BLE001 - never block a report on its cache
        db.rollback()
        version = ("unreadable", time.monotonic())
    return version + (_live_token(),)


# --- Get / put ----------------------------------------------------------------------------------


def peek(key: Hashable, version: Tuple) -> Tuple[bool, Any]:
    """(found, value) for a still-valid entry; never computes."""
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return False, None
        at, gen, entry_version, value = entry
        if gen != _state["generation"] or entry_version != version or now - at >= TTL_SECONDS:
            del _entries[key]
            return False, None
        _entries.move_to_end(key)
        _state["hits"] += 1
        return True, value


def put(key: Hashable, version: Tuple, value: Any, gen: int) -> None:
    """Stores `value` computed from data at `version`, unless something was
    invalidated since `gen` was read (then the value may already be old)."""
    with _lock:
        if gen != _state["generation"]:
            return
        _entries[key] = (time.monotonic(), gen, version, value)
        _entries.move_to_end(key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)


def _key_lock(key: Hashable) -> threading.Lock:
    with _lock:
        lock = _key_locks.get(key)
        if lock is None:
            if len(_key_locks) > MAX_ENTRIES * 4:
                _key_locks.clear()  # only ever a few keys; never grows unbounded
            lock = _key_locks[key] = threading.Lock()
        return lock


def cached_many(
    db: Session, wanted: Dict[Hashable, Any], compute: Callable[[Dict[Hashable, Any]], Dict[Hashable, Any]]
) -> Dict[Hashable, Any]:
    """key -> value for every key of `wanted` (key -> what compute needs to
    build it): the cached ones as they are, all the missing ones from ONE
    `compute(missing)` call (it returns key -> value for them)."""
    version = data_version(db)
    out: Dict[Hashable, Any] = {}
    missing: Dict[Hashable, Any] = {}
    for key, arg in wanted.items():
        found, value = peek(key, version)
        if found:
            out[key] = value
        else:
            missing[key] = arg
    if missing:
        gen = generation()
        with _lock:
            _state["misses"] += len(missing)
        built = compute(missing)
        stored_version = version[:-1] + (_live_token(),)
        for key in missing:
            if key in built:
                put(key, stored_version, built[key], gen)
                out[key] = built[key]
    return out


def cached(db: Session, key: Hashable, compute: Callable[[], Any]) -> Any:
    """`compute()`'s result for `key`, from the cache when still valid. Two
    requests missing at once compute it once (the second waits and reuses)."""
    version = data_version(db)
    found, value = peek(key, version)
    if found:
        return value
    with _key_lock(key):
        found, value = peek(key, version)
        if found:
            return value
        gen = generation()
        with _lock:
            _state["misses"] += 1
        value = compute()
        # The live overlay may have refreshed while computing: store it under
        # the token the value was really built with.
        put(key, version[:-1] + (_live_token(),), value, gen)
        return value
