"""The platform database: engine, sessions, migrations (contract 3, 11.2).

``PLATFORM_DATABASE_URL`` names it (PostgreSQL; ``so_platform`` in
production). The engine is created lazily, connects in UTC and is small: the
platform database holds the registry, settings, credentials and the audit
log, read rarely (registry and settings are cached by B for 10 s).
"""

import pathlib
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.platform.config import pget

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_DIR = BACKEND_DIR / "alembic_platform"


class PlatformNotConfigured(RuntimeError):
    """PLATFORM_DATABASE_URL is not set."""


_lock = threading.Lock()
_engine: Optional[Engine] = None
_engine_url: Optional[str] = None
_factory: Optional[sessionmaker] = None


def platform_url() -> str:
    url = str(pget("platform_database_url") or "").strip()
    if not url:
        raise PlatformNotConfigured("PLATFORM_DATABASE_URL is not set (TENANCY_MODE=multi needs the platform database).")
    return url


def make_engine(url: str, **kwargs) -> Engine:
    """An engine for the platform database (or, for provisioning, a store
    database): UTC sessions on PostgreSQL."""
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, **kwargs)
    connect_args = kwargs.pop("connect_args", {})
    connect_args.setdefault("options", "-c timezone=UTC")
    from app.dbcompat import force_utc_sessions  # PGTZ would override the option alone

    return force_utc_sessions(create_engine(url, pool_pre_ping=True, connect_args=connect_args, **kwargs))


def platform_engine() -> Engine:
    global _engine, _engine_url, _factory
    url = platform_url()
    eng = _engine
    if eng is not None and _engine_url == url:
        return eng
    with _lock:
        if _engine is not None and _engine_url == url:
            return _engine
        if _engine is not None:
            _engine.dispose()
        _engine = make_engine(url, pool_size=3, max_overflow=5, pool_recycle=1800) if not url.startswith(
            "sqlite"
        ) else make_engine(url)
        _engine_url = url
        _factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
        return _engine


def reset_platform_engine() -> None:
    """Drop the cached engine (tests switch PLATFORM_DATABASE_URL)."""
    global _engine, _engine_url, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_url = None
        _factory = None


def PlatformSessionLocal() -> Session:  # noqa: N802 - mirrors app.db.SessionLocal
    platform_engine()
    assert _factory is not None
    return _factory()


@contextmanager
def platform_session() -> Iterator[Session]:
    """A platform session; commits are the caller's; always closed."""
    db = PlatformSessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_platform_db() -> Iterator[Session]:
    """FastAPI dependency. A server without a platform database (single
    mode) answers 404 for every platform route."""
    if not is_configured():
        from app.platform.errors import PlatformError

        raise PlatformError(404, "not_found", "Not available on this server.")
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_configured() -> bool:
    return bool(str(pget("platform_database_url") or "").strip())


# --- migrations -------------------------------------------------------------


def _alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"), ini_section="platform")
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["platform_url"] = url
    return cfg


def upgrade_platform_db(url: Optional[str] = None, revision: str = "head") -> str:
    """Upgrade the platform database; returns the revision now applied."""
    from alembic import command

    from app.migrate import ALEMBIC_LOCK

    url = url or platform_url()
    with ALEMBIC_LOCK:  # Alembic's proxies are per process (app/migrate.py)
        command.upgrade(_alembic_config(url), revision)
    return current_platform_revision(url) or ""


def downgrade_platform_db(url: Optional[str], revision: str) -> Optional[str]:
    from alembic import command

    from app.migrate import ALEMBIC_LOCK

    url = url or platform_url()
    with ALEMBIC_LOCK:  # Alembic's proxies are per process (app/migrate.py)
        command.downgrade(_alembic_config(url), revision)
    return current_platform_revision(url)


def current_platform_revision(url: Optional[str] = None) -> Optional[str]:
    url = url or platform_url()
    eng = make_engine(url)
    try:
        with eng.connect() as conn:
            try:
                return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:  # noqa: BLE001 - no table yet
                return None
    finally:
        eng.dispose()


def platform_head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config("sqlite://")).get_current_head() or ""
