"""Fixtures for the tenancy tests (track B): real PostgreSQL store databases
on the local dev cluster (RETAILOPS_TEST_PG_URL), three stores in multi mode.

Databases are named ``so_b_<pid>_<store>`` (prefix RETAILOPS_TEST_DB_PREFIX,
default ``so_b``), created once per run with C collation and dropped at the
end. The tests skip when PostgreSQL is not reachable.
"""

import os
import pathlib

import pytest
from sqlalchemy import Boolean, create_engine, text
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql import expression

PG_URL = os.environ.get("RETAILOPS_TEST_PG_URL", "postgresql+psycopg://storeops@127.0.0.1:5433/postgres")
PREFIX = os.environ.get("RETAILOPS_TEST_DB_PREFIX", "so_b")
STORES = ("ALPHA", "BETA", "GAMMA")


def _db_name(code: str) -> str:
    return f"{PREFIX}_{os.getpid()}_{code.lower()}"


def _url(db_name: str) -> str:
    return PG_URL.rsplit("/", 1)[0] + "/" + db_name


def _admin_engine():
    return create_engine(PG_URL, isolation_level="AUTOCOMMIT")


def pg_available() -> bool:
    try:
        engine = _admin_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


def _create_db(name: str) -> None:
    assert name.startswith(PREFIX + "_"), name
    engine = _admin_engine()
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(
            text(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
        )
    engine.dispose()


def drop_db(name: str) -> None:
    assert name.startswith(PREFIX + "_"), name
    engine = _admin_engine()
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


def _create_schema(url: str) -> None:
    """Base.metadata.create_all on PostgreSQL. Boolean columns whose server
    default is the SQLite literal 0/1 get false/true for the duration (track
    A's baseline replaces this; noted for A: models.py:113 text("0"))."""
    import app.models  # noqa: F401
    import app.store.identity  # noqa: F401
    from app.db import Base

    patched = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            sd = col.server_default
            if isinstance(col.type, Boolean) and isinstance(sd, DefaultClause):
                raw = str(getattr(sd.arg, "text", sd.arg)).strip("'\"")
                if raw in ("0", "1"):
                    patched.append((col, sd))
                    col.server_default = DefaultClause(expression.true() if raw == "1" else expression.false())
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
        for col, sd in patched:
            col.server_default = sd


@pytest.fixture(scope="session")
def pg_store_dbs():
    """code -> URL of a PostgreSQL store database with the full schema."""
    if not pg_available():
        pytest.skip(f"PostgreSQL not reachable at {PG_URL}")
    out = {}
    try:
        for code in STORES:
            name = _db_name(code)
            _create_db(name)
            url = _url(name)
            _create_schema(url)
            out[code] = url
        yield out
    finally:
        from app.tenancy.engines import dispose_all_engines

        dispose_all_engines()
        for code in STORES:
            drop_db(_db_name(code))


def truncate_store(url: str) -> None:
    from app.db import Base

    names = [t.name for t in Base.metadata.sorted_tables if t.name != "store_identity"]
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
            conn.execute(text("DELETE FROM store_identity"))
    finally:
        engine.dispose()


@pytest.fixture
def multi(pg_store_dbs, monkeypatch, tmp_path):
    """Multi mode with stores ALPHA, BETA, GAMMA (active, empty, stamped),
    uploads under tmp_path. Yields {code: url}."""
    from app.config import process_settings
    from app.tenancy import testing
    from app.tenancy.cache import clear_tenant_caches
    from app.tenancy.engines import dispose_all_engines
    from app.tenancy.locks import reset_locks_for_tests

    s = process_settings()
    monkeypatch.setattr(s, "tenancy_mode", "multi")
    monkeypatch.setattr(s, "store_database_url_template", PG_URL.rsplit("/", 1)[0] + "/{db_name}")
    monkeypatch.setattr(s, "uploads_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(s, "platform_public_url", "https://app.retailops.test")
    monkeypatch.setattr(s, "dedicated_store_codes", "")
    monkeypatch.setattr(s, "dedicated_hosting_enabled", False)
    dispose_all_engines()
    clear_tenant_caches()
    for code, url in pg_store_dbs.items():
        truncate_store(url)
        testing.stamp_store_database(url, code)
        testing.make_test_store(code, url, name=f"{code.title()} Market")
    yield dict(pg_store_dbs)
    testing.reset_test_stores()
    dispose_all_engines()
    reset_locks_for_tests()
    from app.tenancy.registry import set_registry_source
    from app.tenancy.settings import set_settings_source

    set_registry_source(None)
    set_settings_source(None)


def seed_users(code: str, *names: str, admin: bool = True):
    """Users in store `code`'s database; returns {username: (id, token)}."""
    import json

    from app.auth import create_session
    from app.models import User
    from app.tenancy import tenant_session

    out = {}
    with tenant_session(code) as db:
        for name in names:
            u = User(username=name, password_hash="x", is_admin=admin, allowed_features=json.dumps([]))
            db.add(u)
            db.commit()
            db.refresh(u)
            token, _ = create_session(db, u.id)
            out[name] = (u.id, token)
    return out


def uploads_dir(tmp_path) -> pathlib.Path:
    return tmp_path / "uploads"
