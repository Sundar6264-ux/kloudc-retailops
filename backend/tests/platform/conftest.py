"""Platform tests (track C, docs/PLATFORM_CONTRACT.md 18.2, 18.3) on the
local PostgreSQL 16 cluster.

- One platform database per run, ``<prefix>_platform_<pid>`` (prefix from
  ``STOREOPS_TEST_DB_PREFIX``, default ``so_c``), upgraded with the platform
  Alembic env; tables truncated between tests.
- Store databases made by provisioning are ``<prefix>_t<pid>_<code>`` and
  dropped after each test that made them (and at the end of the run).
- Every test here runs with TENANCY_MODE=multi patched onto the process
  settings and the platform registry/settings sources installed; the root
  conftest's SQLite harness (single mode) is untouched.

``STOREOPS_TEST_PG_URL`` (default the local dev cluster) names the server;
without a reachable server the whole package is skipped.
"""

import os
import pathlib

import pytest
from sqlalchemy import create_engine, text

PG_URL = os.environ.get("STOREOPS_TEST_PG_URL", "postgresql+psycopg://storeops@127.0.0.1:5433/postgres")
PREFIX = os.environ.get("STOREOPS_TEST_DB_PREFIX", "so_c")
PID = os.getpid()
PLATFORM_DB = f"{PREFIX}_platform_{PID}"
STORE_PREFIX = f"{PREFIX}_t{PID}_"
TEST_RING = "t2:" + "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI=" + ",t1:" + "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="


def _url_for(db_name: str) -> str:
    return PG_URL.rsplit("/", 1)[0] + "/" + db_name


def _admin():
    return create_engine(PG_URL, isolation_level="AUTOCOMMIT")


def _pg_up() -> bool:
    try:
        eng = _admin()
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


def drop_test_store_databases() -> None:
    eng = _admin()
    try:
        with eng.connect() as c:
            names = [r[0] for r in c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
                                             {"p": STORE_PREFIX.replace("_", r"\_") + "%"})]
            for n in names:
                assert n.startswith(STORE_PREFIX)
                c.execute(text(f'DROP DATABASE "{n}" WITH (FORCE)'))
    finally:
        eng.dispose()


@pytest.fixture(scope="session")
def platform_db_url():
    if not _pg_up():
        pytest.skip("PostgreSQL test cluster not reachable (STOREOPS_TEST_PG_URL)")
    assert PLATFORM_DB.startswith(PREFIX + "_")
    eng = _admin()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{PLATFORM_DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{PLATFORM_DB}"'))
    url = _url_for(PLATFORM_DB)
    from app.platform.db import upgrade_platform_db

    upgrade_platform_db(url)
    yield url
    from app.platform.db import reset_platform_engine

    reset_platform_engine()
    drop_test_store_databases()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{PLATFORM_DB}" WITH (FORCE)'))
    eng.dispose()


TABLES = (
    "owner_setup_tokens, support_sessions, login_attempts, dedicated_hosting_requests, audit_log, "
    "store_credentials, store_settings, platform_sessions, platform_users, stores, signup_requests"
)


def store_migrator():
    from app.migrate import upgrade_store_db  # track A: the PostgreSQL baseline + chain

    return upgrade_store_db


@pytest.fixture(autouse=True)
def platform_env(platform_db_url, monkeypatch, tmp_path):
    """Multi mode against this run's platform database."""
    from app.config import process_settings
    from app.platform import crypto, provisioning, throttle
    from app.platform.db import platform_engine, reset_platform_engine

    s = process_settings()
    values = {
        "tenancy_mode": "multi",
        "platform_database_url": platform_db_url,
        "store_database_url_template": _url_for("{db_name}"),
        "pg_admin_url": PG_URL,
        "store_db_prefix": STORE_PREFIX,
        "storeops_secret_keys": TEST_RING,
        "uploads_root": str(tmp_path / "uploads"),
        "platform_public_url": "https://app.storeops.test",
        "platform_domain": "storeops.test",
        "dedicated_hosting_enabled": False,
        "dedicated_store_codes": "",
        "signup_enabled": True,
        "platform_require_totp": False,
        "app_env": "test",
    }
    for k, v in values.items():
        monkeypatch.setattr(s, k, v)
    reset_platform_engine()
    crypto.reset_keyring()
    throttle.reset_limiters()
    monkeypatch.setattr(provisioning, "MIGRATOR", store_migrator())
    eng = platform_engine()
    with eng.begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    from app.platform import sources
    from app.tenancy import registry, settings as tsettings
    from app.tenancy.cache import clear_tenant_caches

    sources.configure(force=True)
    yield s
    from app.tenancy import engines

    engines.dispose_all_engines()
    registry.set_registry_source(None)
    tsettings.set_settings_source(None)
    clear_tenant_caches()
    sources._configured = False
    provisioning._results.clear()
    drop_test_store_databases()
    reset_platform_engine()
    crypto.reset_keyring()
    throttle.reset_limiters()


@pytest.fixture
def pdb():
    from app.platform.db import platform_session

    with platform_session() as db:
        yield db


@pytest.fixture
def client():
    """The app behind track B's TenantMiddleware (main.py adds it once B
    merges; until then the test adds it)."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.tenancy.middleware import TenantMiddleware

    has_mw = any(getattr(m, "cls", None) is TenantMiddleware for m in app.user_middleware)
    return TestClient(app if has_mw else TenantMiddleware(app))


MASTER_PASSWORD = "correct horse battery"


@pytest.fixture
def master(pdb):
    from app.platform.masters import create_master

    return create_master(pdb, "owner", MASTER_PASSWORD, "The Owner", actor="test", actor_type="system")


def master_login(client, username="owner", password=MASTER_PASSWORD):
    r = client.post("/api/auth/mobile-login", json={"store_code": "PLATFORM", "username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def master_client(client, master):
    """Its own TestClient (headers are per client), signed in as a master."""
    mc = client.__class__(client.app)
    token = master_login(mc)
    mc.headers.update({"Authorization": f"Bearer {token}"})
    mc.token = token
    return mc


SIGNUP = {
    "store_name": "Alpha Grocery",
    "requested_code": "alpha",
    "contact_name": "Ann Alpha",
    "contact_email": "Ann@Example.com",
    "contact_phone": "555-0100",
    "address": "1 Main St",
    "timezone": "America/Chicago",
    "pos_provider": "toast",
    "owner_username": "ann",
    "owner_password": "alpha-owner-pass",
    "notes": "please",
    "accept_terms": True,
    "website": "",
}


def signup_body(**changes):
    body = dict(SIGNUP)
    body.update(changes)
    return body


def provision(code="ALPHA", *, name=None, owner="ann", password="alpha-owner-pass", pack="test_toast",
              tz="America/Chicago"):
    """A store made the way a master approves one (inline, no HTTP)."""
    from app.auth import hash_password
    from app.platform import provisioning

    out = provisioning.create_store_manually(
        code=code, name=name or code.title() + " Store", pack_id=pack, timezone=tz, pos_provider="toast",
        owner_username=owner, owner_password_hash=hash_password(password), actor="platform:test", run="inline",
    )
    assert out["status"] == "active", out
    return out


def store_login(client, code, username, password):
    return client.post("/api/auth/mobile-login", json={"store_code": code, "username": username, "password": password})


def b_full() -> bool:
    """Track B's store-bound sessions (app.auth.lookup_session) are merged."""
    import app.auth

    return hasattr(app.auth, "lookup_session")


needs_b = pytest.mark.skipif(not b_full(), reason="needs track B's store-bound sessions (app.auth.lookup_session)")


@pytest.fixture
def uploads(platform_env) -> pathlib.Path:
    return pathlib.Path(platform_env.uploads_root)
