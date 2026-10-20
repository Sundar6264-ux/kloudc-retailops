"""One SQLAlchemy engine per store database (docs/PLATFORM_CONTRACT.md 5).

- Engines are created lazily on first use and kept in an LRU of at most
  TENANT_MAX_ENGINES; when full, the least recently used engine with no
  checked-out connection is disposed. ``sweep_idle_engines`` (called at most
  once a minute from ``tenant_engine``, and by the scheduler leader)
  disposes engines idle for TENANT_ENGINE_IDLE_SECONDS. A busy engine is
  never disposed.
- PostgreSQL connections run in UTC with a statement timeout
  (``options=-c timezone=UTC -c statement_timeout=...``); SQLite keeps the
  M2 pragmas and ``check_same_thread=False``.
- The first open of a store's engine checks its ``store_identity`` stamp
  against the registry (code, and instance id when the registry has one).
  A mismatch raises StoreIdentityError, the engine is not kept, and the
  event ``store.identity_mismatch`` is emitted (audit, track C).
- Single mode: exactly one engine, ``app.db.engine`` (from DATABASE_URL).
"""

import json
import logging
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from typing import Callable, Dict, Iterator, List, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.tenancy import events
from app.tenancy.context import require_store, store_context
from app.tenancy.errors import StoreIdentityError, StoreUnavailable, UnknownStore
from app.tenancy.mode import is_multi_tenant
from app.tenancy.registry import StoreRef, get_store, normalize_store_code, single_store_code, valid_db_name

logger = logging.getLogger(__name__)

# SQLite pragmas (speed #14): negative cache_size = KiB.
SQLITE_CACHE_SIZE_KIB = -16384
SQLITE_MMAP_SIZE_BYTES = 268435456
SWEEP_EVERY_SECONDS = 60.0
# Connections the platform database and other tools may hold (budget check).
PLATFORM_CONNECTION_RESERVE = 10


def install_sqlite_pragmas(engine: Engine) -> None:
    """WAL + busy timeout + the speed pragmas, on every new connection (M2
    db.py, unchanged).

    Default SQLite locking lets a reader's open transaction (e.g. a GUI tool
    browsing a table) block the backend's writes outright. WAL lets readers
    and a writer proceed concurrently, and busy_timeout makes remaining
    brief contention retry for up to 5 s instead of failing with "database
    is locked". cache_size: 16 MB of page cache per connection; mmap_size:
    read the file through the OS page cache; temp_store: sorts in memory.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute(f"PRAGMA cache_size={SQLITE_CACHE_SIZE_KIB}")
        cursor.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE_BYTES}")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()


def pool_mode() -> str:
    """DB_POOL_MODE: "direct" (default: the app keeps its own pools to
    PostgreSQL) or "pgbouncer" (the URLs point at PgBouncer in transaction
    pooling mode; docs/DEPLOY_MULTI_PROCESS.md)."""
    from app.platform.config import pget

    return "pgbouncer" if str(pget("db_pool_mode") or "direct").strip().lower() == "pgbouncer" else "direct"


def postgres_connect_args(*, direct: bool = False) -> dict:
    """psycopg connect arguments. Direct connections send the time zone and
    statement timeout as startup options. Through PgBouncer in transaction
    mode (``direct=False`` and DB_POOL_MODE=pgbouncer) there are no startup
    options (PgBouncer refuses unknown ones; set them per database / role,
    see the deploy doc) and no server-side prepared statements (a
    transaction-pooled connection is shared, so a prepared statement would
    land on the wrong server connection)."""
    from app.config import process_settings

    if not direct and pool_mode() == "pgbouncer":
        return {"prepare_threshold": None}
    timeout = int(process_settings().statement_timeout_ms or 0)
    options = "-c timezone=UTC"
    if timeout > 0:
        options += f" -c statement_timeout={timeout}"
    return {"options": options}


def create_store_engine(url: str, *, tenant: bool = True) -> Engine:
    """An engine for one store database. ``tenant=True`` uses the bounded
    per-store pool (multi mode); False keeps SQLAlchemy's default pool (the
    single-mode engine)."""
    from app.config import process_settings

    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        install_sqlite_pragmas(engine)
        return engine
    kwargs: dict = {}
    if url.startswith("postgresql"):
        kwargs["connect_args"] = postgres_connect_args()
    if tenant and url.startswith("postgresql") and pool_mode() == "pgbouncer":
        # PgBouncer pools the server connections for every process and
        # store: no idle per-store pool here (C5).
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
    elif tenant:
        s = process_settings()
        kwargs.update(
            pool_size=max(1, int(s.tenant_pool_size)),
            max_overflow=max(0, int(s.tenant_max_overflow)),
            pool_timeout=max(1, int(s.tenant_pool_timeout)),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    else:
        kwargs["pool_pre_ping"] = True
    from app.dbcompat import force_utc_sessions

    return force_utc_sessions(create_engine(url, **kwargs))


# --- URLs ------------------------------------------------------------------------------


def _store_db_hosts() -> Dict[str, str]:
    from app.config import process_settings

    raw = (process_settings().store_db_hosts or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise RuntimeError("STORE_DB_HOSTS is not a JSON object") from None
    if not isinstance(data, dict):
        raise RuntimeError("STORE_DB_HOSTS is not a JSON object")
    return {str(k): str(v) for k, v in data.items()}


# Test-only: store code -> URL (app.tenancy.testing.make_test_store).
_url_overrides: Dict[str, str] = {}


def set_url_override(code: str, url: str) -> None:
    _url_overrides[normalize_store_code(code)] = url


def clear_url_overrides() -> None:
    _url_overrides.clear()


def store_database_url(store: StoreRef) -> str:
    """The SQLAlchemy URL of `store`'s database. Multi mode:
    STORE_DATABASE_URL_TEMPLATE (or STORE_DB_HOSTS[db_host_ref] for a
    dedicated store) with {db_name} filled in. Single mode: DATABASE_URL."""
    from app.config import process_settings

    s = process_settings()
    if not is_multi_tenant():
        return s.database_url
    override = _url_overrides.get(store.code)
    if override:
        return override
    if not valid_db_name(store.db_name):
        raise ValueError(f"store {store.code}: invalid db_name {store.db_name!r}")
    if store.db_host_ref:
        template = _store_db_hosts().get(store.db_host_ref)
        if not template:
            raise RuntimeError(f"store {store.code}: STORE_DB_HOSTS has no entry {store.db_host_ref!r}")
    else:
        template = (s.store_database_url_template or "").strip()
        if not template:
            raise RuntimeError("STORE_DATABASE_URL_TEMPLATE is not set (required in multi mode)")
    if "{db_name}" not in template:
        raise RuntimeError("the store database URL template must contain {db_name}")
    return template.replace("{db_name}", store.db_name)


# --- the registry of engines ---------------------------------------------------------------


class _Entry:
    __slots__ = ("code", "engine", "maker", "url", "created", "last_used")

    def __init__(self, code: str, engine: Engine, url: str):
        self.code = code
        self.engine = engine
        self.maker = sessionmaker(autocommit=False, autoflush=False, bind=engine, info={"store_code": code})
        self.url = url
        self.created = self.last_used = time.monotonic()


_engines: "OrderedDict[str, _Entry]" = OrderedDict()
_guard = threading.RLock()
_create_locks: Dict[str, threading.Lock] = {}
_hooks: List[Callable[[str, Engine], None]] = []
_sweep = {"at": time.monotonic()}
_single = {"engine": None}


def _run_hooks(code: str, engine: Engine, hooks=None) -> None:
    for fn in list(hooks if hooks is not None else _hooks):
        try:
            fn(code, engine)
        except Exception:  # noqa: BLE001 - one bad hook must not block the store
            logger.exception("engine hook %r failed for %s", fn, code)


def register_engine_hook(fn: Callable[[str, Engine], None]) -> None:
    """Call ``fn(store_code, engine)`` for every engine: the ones already
    open now (and the single-mode engine) and every one created later."""
    with _guard:
        if fn in _hooks:
            return
        _hooks.append(fn)
        existing = [(e.code, e.engine) for e in _engines.values()]
        single = _single["engine"]
    if single is not None:
        _run_hooks(single_store_code(), single, [fn])
    for code, engine in existing:
        _run_hooks(code, engine, [fn])


def set_single_engine(engine: Optional[Engine]) -> None:
    """app.db registers the single-mode engine here (hooks run on it)."""
    _single["engine"] = engine
    if engine is not None:
        _run_hooks(single_store_code(), engine)


def _single_engine() -> Engine:
    import app.db

    engine = getattr(app.db, "engine", None)
    if engine is None:
        raise RuntimeError("no single-mode engine (TENANCY_MODE=multi at import?)")
    return engine


def _verify_identity(ref: StoreRef, engine: Engine) -> None:
    from app.store.identity import read_stamp

    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        raise StoreUnavailable(ref.code, type(exc.orig).__name__ if exc.orig else "") from exc
    with Session(engine) as db:
        stamp = read_stamp(db)
    problem = None
    if stamp is None:
        if ref.status == "active":
            problem = "the database has no store identity stamp"
    elif stamp.get("store_code") != ref.code:
        problem = f"the database belongs to {stamp.get('store_code')}"
    elif ref.instance_id and stamp.get("instance_id") != ref.instance_id:
        problem = f"instance {stamp.get('instance_id')} is not the registry's {ref.instance_id}"
    if problem is None and stamp is not None:
        from app.store.identity import check_store_marker
        from app.tenancy.paths import store_root

        try:
            check_store_marker(store_root(ref.code), ref.code, stamp["instance_id"])
        except StoreIdentityError as exc:
            problem = str(exc).rstrip(".")
    if problem:
        events.emit(
            "store.identity_mismatch",
            ref.code,
            {"db_name": ref.db_name, "problem": problem, "stamp_code": (stamp or {}).get("store_code")},
        )
        logger.error("Store %s: refusing its database (%s)", ref.code, problem)
        raise StoreIdentityError(f"Store {ref.code}: {problem}.")


def _evict_locked(keep: str) -> None:
    from app.config import process_settings

    limit = max(1, int(process_settings().tenant_max_engines))
    while len(_engines) > limit:
        victim = None
        for code, entry in _engines.items():  # oldest first
            if code != keep and entry.engine.pool.checkedout() == 0:
                victim = entry
                break
        if victim is None:
            logger.warning("All %d store engines are busy; over TENANT_MAX_ENGINES=%d for now", len(_engines), limit)
            return
        _engines.pop(victim.code, None)
        victim.engine.dispose()
        logger.info("Store engine %s closed (least recently used)", victim.code)


def _entry_for(code: Optional[str]) -> _Entry:
    if code is None:
        ref = require_store()
    else:
        ref = get_store(code)
    now = time.monotonic()
    with _guard:
        entry = _engines.get(ref.code)
        if entry is not None:
            entry.last_used = now
            _engines.move_to_end(ref.code)
    if entry is None:
        with _guard:
            create_lock = _create_locks.setdefault(ref.code, threading.Lock())
        with create_lock:
            with _guard:
                entry = _engines.get(ref.code)
            if entry is None:
                url = store_database_url(ref)
                engine = create_store_engine(url, tenant=True)
                try:
                    _verify_identity(ref, engine)
                except BaseException:
                    engine.dispose()
                    raise
                entry = _Entry(ref.code, engine, url)
                with _guard:
                    _engines[ref.code] = entry
                    _evict_locked(keep=ref.code)
                _run_hooks(ref.code, engine)
                logger.info("Store engine %s opened (%d open)", ref.code, len(_engines))
    if now - _sweep["at"] > SWEEP_EVERY_SECONDS:
        sweep_idle_engines()
    return entry


def tenant_engine(code: Optional[str] = None) -> Engine:
    """The engine of store `code` (None = the current store). Single mode:
    app.db.engine."""
    if not is_multi_tenant():
        if code is not None and normalize_store_code(code) not in (single_store_code(), ""):
            raise UnknownStore(code)
        return _single_engine()
    return _entry_for(code).engine


def tenant_sessionmaker(code: Optional[str] = None) -> sessionmaker:
    """The sessionmaker of store `code` (None = current); multi mode only."""
    return _entry_for(code).maker


@contextmanager
def tenant_session(
    code: Optional[str] = None, *, allow_inactive: bool = False, allow_remote: bool = False
) -> Iterator[Session]:
    """A session on store `code` (None = the current store) with that store
    selected for the block. Closed at the end; never commits by itself."""
    ctx = (
        nullcontext(require_store())
        if code is None
        else store_context(code, allow_inactive=allow_inactive, allow_remote=allow_remote)
    )
    with ctx as ref:
        if is_multi_tenant():
            db = _entry_for(ref.code).maker()
        else:
            import app.db

            db = app.db.SessionLocal()
        try:
            yield db
        finally:
            db.close()


def dispose_engine(code: str) -> None:
    """Close store `code`'s engine (suspend, delete, tests)."""
    norm = normalize_store_code(code)
    with _guard:
        entry = _engines.pop(norm, None)
    if entry is not None:
        entry.engine.dispose()


def dispose_all_engines() -> None:
    with _guard:
        entries = list(_engines.values())
        _engines.clear()
    for entry in entries:
        entry.engine.dispose()


def sweep_idle_engines(now: Optional[float] = None) -> List[str]:
    """Dispose engines idle longer than TENANT_ENGINE_IDLE_SECONDS with no
    checked-out connection. Returns the codes closed."""
    from app.config import process_settings

    now = time.monotonic() if now is None else now
    idle = max(1, int(process_settings().tenant_engine_idle_seconds))
    closed: List[_Entry] = []
    with _guard:
        _sweep["at"] = now
        for code, entry in list(_engines.items()):
            if now - entry.last_used > idle and entry.engine.pool.checkedout() == 0:
                closed.append(_engines.pop(code))
    for entry in closed:
        entry.engine.dispose()
        logger.info("Store engine %s closed (idle)", entry.code)
    return [e.code for e in closed]


def engine_stats() -> Dict[str, dict]:
    """code -> {"checked_out", "pool_size", "last_used"} (monotonic seconds)."""
    with _guard:
        entries = list(_engines.values())
    out = {}
    for e in entries:
        pool = e.engine.pool
        out[e.code] = {
            "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else 0,
            "pool_size": pool.size() if hasattr(pool, "size") else 0,
            "last_used": e.last_used,
        }
    return out


def open_engine_codes() -> List[str]:
    with _guard:
        return list(_engines.keys())


def check_connection_budget(max_connections: Optional[int] = None) -> int:
    """Startup check (multi mode): TENANT_MAX_ENGINES x (pool + overflow) +
    PLATFORM_CONNECTION_RESERVE must fit the server's max_connections.
    Raises RuntimeError when it does not. Returns the connections needed."""
    from app.config import process_settings

    s = process_settings()
    if pool_mode() == "pgbouncer":
        # PgBouncer's pool sizes bound the server connections, not ours.
        return PLATFORM_CONNECTION_RESERVE
    need = int(s.tenant_max_engines) * (int(s.tenant_pool_size) + int(s.tenant_max_overflow)) + PLATFORM_CONNECTION_RESERVE
    if max_connections is None:
        url = (s.platform_database_url or "").strip()
        if not url.startswith("postgresql"):
            return need
        engine = create_engine(url, connect_args=postgres_connect_args())
        try:
            from sqlalchemy import text

            with engine.connect() as conn:
                max_connections = int(conn.execute(text("SHOW max_connections")).scalar())
        finally:
            engine.dispose()
    if need > max_connections:
        raise RuntimeError(
            f"Store database pools need up to {need} connections (TENANT_MAX_ENGINES={s.tenant_max_engines} x "
            f"(TENANT_POOL_SIZE={s.tenant_pool_size} + TENANT_MAX_OVERFLOW={s.tenant_max_overflow}) + "
            f"{PLATFORM_CONNECTION_RESERVE}) but PostgreSQL allows {max_connections}. Lower TENANT_MAX_ENGINES "
            "or raise max_connections."
        )
    return need
