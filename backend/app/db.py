import datetime as dt
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import DateTime, TypeDecorator

from app.config import get_settings


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
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value

settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")

# SQLite pragmas (speed #14): negative cache_size = KiB.
SQLITE_CACHE_SIZE_KIB = -16384
SQLITE_MMAP_SIZE_BYTES = 268435456

connect_args = {"check_same_thread": False} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)

if is_sqlite:
    # Default SQLite locking lets a reader's open transaction (e.g. a GUI tool
    # like DB Browser for SQLite, just browsing a table) block the backend's
    # writes outright. WAL mode lets readers and a writer proceed concurrently
    # instead, and busy_timeout makes any remaining brief contention retry for
    # up to 5s instead of failing immediately with "database is locked".
    #
    # Speed (#14, Oct 15) - per connection, all safe for WAL and readers:
    # cache_size: 16 MB of page cache (default 2 MB; the forecast reads tens
    # of MB of orders/snapshots). Per connection, so modest: the pool can
    # hold up to 15 and the server has 8 GB.
    # mmap_size: read the 380+ MB file through the OS page cache (shared by
    # every connection, not extra memory per connection).
    # temp_store: sorts / temp b-trees in memory instead of temp files.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute(f"PRAGMA cache_size={SQLITE_CACHE_SIZE_KIB}")
        cursor.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE_BYTES}")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
