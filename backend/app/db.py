import datetime as dt
from collections.abc import Generator
from typing import Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import DateTime, TypeDecorator

from app.config import get_settings
from app.tenancy import engines as _tenancy_engines
from app.tenancy.context import current_scope, current_store, current_store_block, require_store
from app.tenancy.errors import StoreIdentityError, StoreUnavailable
from app.tenancy.mode import is_multi_tenant
from app.tenancy.registry import single_store_code


class UTCDateTime(TypeDecorator):
    """DateTime that's always stored and returned as UTC.

    SQLite has no native timestamp/timezone type - SQLAlchemy stores DateTime
    values as plain ISO text and, on read, hands back a *naive* datetime with
    no tzinfo at all, even though every timestamp this app writes is UTC (see
    `utcnow()` in models.py). Naive datetimes get serialized to JSON without a
    'Z'/offset, and a browser parses a timezone-less ISO string as *local*
    time - so a value that's actually UTC ends up displayed as if it were
    already in the viewer's own timezone, off by however many hours that is.
    This type re-attaches UTC on the way out (and normalizes to naive-UTC on
    the way in) so every consumer - the API, CSV export, everywhere - gets an
    unambiguous, correctly-marked timestamp.

    PostgreSQL (platform contract 1.3): the column is `timestamptz`, and a
    naive value would be read in the connection's TimeZone - wrong unless it
    is UTC. So on PostgreSQL the value is bound *aware* (UTC; a naive value is
    taken as UTC, as everywhere in this app), and an aware value read back in
    any session zone is converted to UTC. The stored instant never depends on
    the connection's TimeZone setting.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or not isinstance(value, dt.datetime):
            return value
        if dialect.name == "postgresql":
            if value.tzinfo is None:
                return value.replace(tzinfo=dt.timezone.utc)
            return value.astimezone(dt.timezone.utc)
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None or not isinstance(value, dt.datetime):
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        if value.utcoffset() != dt.timedelta(0) or value.tzinfo is not dt.timezone.utc:
            value = value.astimezone(dt.timezone.utc)
        return value

# --- Engines and sessions (owner B; docs/PLATFORM_CONTRACT.md 5.3) -----------------------
# Single mode (default): one engine from DATABASE_URL, exactly as in M2.
# Multi mode: no process-wide engine (None); every session is opened on the
# CURRENT store's engine (app/tenancy/engines.py).

settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")

# Kept for callers that read them (values unchanged).
SQLITE_CACHE_SIZE_KIB = _tenancy_engines.SQLITE_CACHE_SIZE_KIB
SQLITE_MMAP_SIZE_BYTES = _tenancy_engines.SQLITE_MMAP_SIZE_BYTES

engine: Optional[Engine] = (
    None if settings.tenancy_mode == "multi" else _tenancy_engines.create_store_engine(settings.database_url, tenant=False)
)
_tenancy_engines.set_single_engine(engine)


class _TenantSessionLocal:
    """``SessionLocal()``: a Session on the current store's database.

    Single mode: the M2 sessionmaker bound to ``engine``. Multi mode: the
    current store's sessionmaker; raises NoStoreContext without a store.
    Every session carries ``info["store_code"]`` (the change feed reads it).
    ``kw`` / ``configure`` act on the single-mode sessionmaker, as before.
    """

    def __init__(self):
        self._single = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    @property
    def kw(self):
        return self._single.kw

    def configure(self, **kwargs) -> None:
        self._single.configure(**kwargs)

    def __call__(self, **kwargs) -> Session:
        if not is_multi_tenant():
            session = self._single(**kwargs)
            session.info.setdefault("store_code", single_store_code())
            return session
        ref = require_store()
        return _tenancy_engines.tenant_sessionmaker(ref.code)(**kwargs)


SessionLocal = _TenantSessionLocal()


class Base(DeclarativeBase):
    pass


def _store_access_error(status: int, error: str, message: str):
    """An HTTPException whose answer also carries {"error", "message"}
    (app/main.py renders it; "detail" stays the message for old clients)."""
    from fastapi import HTTPException

    exc = HTTPException(status_code=status, detail=message)
    exc.error_code = error  # type: ignore[attr-defined]
    return exc


def _store_session_or_http_error() -> Session:
    """SessionLocal() for a request; multi mode without a store -> 401 (or
    403 for an inactive store / a master's session), a store whose database
    is refused or down -> 503."""
    if not is_multi_tenant():
        return SessionLocal()
    from fastapi import HTTPException

    if current_store() is None:
        block = current_store_block()
        if block == "suspended":
            raise _store_access_error(403, "store_suspended", "This store is suspended. Contact support.")
        if block:
            raise _store_access_error(403, "store_not_ready", "This store is not ready yet.")
        if current_scope() == "platform":
            raise _store_access_error(403, "platform_scope", "Sign in with a store login to use this page.")
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        return SessionLocal()
    except StoreIdentityError:
        raise _store_access_error(503, "store_unavailable", "This store's data is not available right now.")
    except StoreUnavailable:
        raise _store_access_error(503, "store_unavailable", "This store's data is not available right now. Try again soon.")


def get_db() -> Generator[Session, None, None]:
    """The current store's session (FastAPI dependency)."""
    db = _store_session_or_http_error()
    try:
        yield db
    finally:
        db.close()


def get_optional_db() -> Generator[Optional[Session], None, None]:
    """Like get_db, but yields None instead of answering 401 when there is no
    store (multi mode) - for app-wide dependencies that also run on
    logged-out and platform routes (app/demo_guard.py)."""
    if is_multi_tenant() and current_store() is None:
        yield None
        return
    db = _store_session_or_http_error()
    try:
        yield db
    finally:
        db.close()


def get_engine(store_code: Optional[str] = None) -> Engine:
    """The engine of `store_code` (None = the current store)."""
    return _tenancy_engines.tenant_engine(store_code)
