"""Backend test harness: a throwaway database per run (PostgreSQL by
default, platform contract 18.1; or SQLite), emptied before every test, real
app routes via FastAPI's TestClient, and logged-in clients for an admin and
two staff users.

The environment is set before anything from `app` is imported - settings
are read once (lru_cache) and the engine is created at import time. The
TestClient is never used as a context manager, so the app's lifespan (the
schedulers) never starts.

Run (PostgreSQL, the real suite; tests/pg_harness.py):
     cd backend && RETAILOPS_TEST_PG_URL=postgresql+psycopg://storeops@127.0.0.1:5433/postgres \
         RETAILOPS_TEST_DB_PREFIX=so_<track> .venv/bin/python -m pytest -q
Quick SQLite run (a fresh SQLite file per test, as before):
     RETAILOPS_TEST_DB=sqlite .venv/bin/python -m pytest -q
"""

import atexit
import os
import pathlib
import tempfile

from tests import pg_harness

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="retailops-tests-"))
TEST_DB_KIND = (os.environ.get("RETAILOPS_TEST_DB") or "postgres").strip().lower()
if TEST_DB_KIND in ("pg", "postgresql"):
    TEST_DB_KIND = "postgres"
if TEST_DB_KIND not in ("postgres", "sqlite"):
    raise SystemExit(f"RETAILOPS_TEST_DB={TEST_DB_KIND!r}: use 'postgres' (default) or 'sqlite'")
IS_PG = TEST_DB_KIND == "postgres"
if IS_PG:
    _TEST_DB = pg_harness.run_db_name()
    _TEST_DB_URL = pg_harness.create_run_database()
    atexit.register(pg_harness.drop_run_database, _TEST_DB)
else:
    _TEST_DB = str(_TMP / "test.db")
    _TEST_DB_URL = f"sqlite:///{_TEST_DB}"
os.environ.update(
    {
        "DATABASE_URL": _TEST_DB_URL,
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "FIREBASE_CREDENTIALS_FILE": "",
        "TEAM_UPLOADS_DIR": str(_TMP / "uploads" / "team"),
        "INVOICE_UPLOADS_DIR": str(_TMP / "uploads" / "invoices"),
        # Never the real ~/Main Store Flyers.
        "FLYERS_DIR": str(_TMP / "flyers"),
        "WHAPI_API_TOKEN": "",
        "GMAIL_APP_PASSWORD": "",
        "ANTHROPIC_API_KEY": "",
        # Toast Analytics never configured in tests (tests set fake values
        # on the settings object and mock every HTTP call).
        "TOAST_ANALYTICS_CLIENT_ID": "",
        "TOAST_ANALYTICS_CLIENT_SECRET": "",
        # Nor the standard Toast API: the dev .env (and prod's) hold real
        # credentials, so a test path that forgot a mock would otherwise call
        # the live store's Toast. Tests that need "configured" set fake values.
        "TOAST_CLIENT_ID": "",
        "TOAST_CLIENT_SECRET": "",
        # The per-store AI switch (KloudC RetailOps M2) is off by default; the Ask bot
        # and digest tests predate it. Fake-store tests switch it per store.
        "AI_ENABLED": "true",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import create_session  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.team.models import Team, TeamMember  # noqa: E402
from app.timeutil import utcnow  # noqa: E402

# The default teams the 0026 migration seeds.
SEED_TEAMS = [
    ("Everyone", "#5B8DEF", True),
    ("Grocery", "#3ED598", False),
    ("Kitchen", "#F5A524", False),
    ("Managers", "#A78BFA", False),
]



def _refuse_a_real_database() -> None:
    """Stops the run before anything is dropped unless the app is wired to
    this run's throwaway database and settings. If something imports `app`
    before this file sets the environment (e.g. a `pytest -p` plugin that
    patches app code), the engine and the cached settings come from .env:
    the dev database here - which _fresh_db's drop_all wiped on Oct 4 - and
    the live database (and real WhatsApp token) in the prod checkout."""
    from app.config import get_settings

    settings = get_settings()
    problems = []
    if engine.url.database != _TEST_DB:
        problems.append(f"the database is {engine.url.database!r}")
    if settings.whapi_api_token:
        problems.append("a WhatsApp token is set")
    if pathlib.Path(settings.flyers_dir) != _TMP / "flyers":
        problems.append(f"the flyers folder is {settings.flyers_dir!r}")
    if problems:
        pytest.exit(
            "Refusing to run tests: "
            + "; ".join(problems)
            + ". Something imported `app` before tests/conftest.py set the test environment "
            "(a -p plugin?). Patch app code from inside a test or a conftest fixture instead.",
            returncode=3,
        )


_pg_schema_signature = None


def _pg_build_schema() -> None:
    """The store schema (the PostgreSQL baseline + the migration chain, like
    a new store's database) and the reset helper: once per run, and again
    after a test dropped or recreated a table (an "older database" test)."""
    global _pg_schema_signature
    from app.migrate import upgrade_store_db

    if _pg_schema_signature is not None:
        pg_harness.drop_schema(engine)
    upgrade_store_db(_TEST_DB_URL)
    pg_harness.install_reset_function(engine)
    _pg_schema_signature = pg_harness.schema_signature(engine)


@pytest.fixture(scope="session", autouse=True)
def _pg_run_database():
    yield
    if IS_PG:
        engine.dispose()
        pg_harness.drop_run_database(_TEST_DB)


@pytest.fixture(autouse=True)
def _fresh_db():
    from app.services.checklist_schemas import clear_cache as clear_checklist_cache

    _refuse_a_real_database()
    if IS_PG:
        if _pg_schema_signature is None or pg_harness.schema_signature(engine) != _pg_schema_signature:
            _pg_build_schema()
        pg_harness.reset(engine)
        # SQLite recreates the tables for every test, which ends the report
        # cache (its metadata after_create hook); truncating does not.
        from app.services import report_cache

        report_cache.invalidate("test database reset")
    else:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    # Checklist definitions are cached per process (checklist_schemas).
    clear_checklist_cache()
    db = SessionLocal()
    try:
        for name, color, everyone in SEED_TEAMS:
            db.add(Team(name=name, color=color, is_everyone=everyone, created_at=utcnow()))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_user(db, username, is_admin=False, features=None, can_assign=False):
    import json

    user = User(
        username=username,
        password_hash="x",  # never logged in with; tests use sessions directly
        is_admin=is_admin,
        allowed_features=json.dumps(features or []),
        can_assign=can_assign,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def client_for(db, user):
    token, _ = create_session(db, user.id)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    client.user = user
    client.token = token
    return client


@pytest.fixture
def admin(db):
    return make_user(db, "boss", is_admin=True)


@pytest.fixture
def staff(db):
    return make_user(db, "ravi", features=["team_tasks", "team_chat"])


@pytest.fixture
def staff2(db):
    return make_user(db, "sam", features=["team_tasks"])


@pytest.fixture
def admin_client(db, admin):
    return client_for(db, admin)


@pytest.fixture
def staff_client(db, staff):
    return client_for(db, staff)


@pytest.fixture
def staff2_client(db, staff2):
    return client_for(db, staff2)


@pytest.fixture
def anon_client():
    return TestClient(app)


@pytest.fixture
def team(db):
    """The Kitchen team, with no members yet."""
    return db.query(Team).filter(Team.name == "Kitchen").one()


def add_member(db, team, user):
    db.add(TeamMember(team_id=team.id, user_id=user.id, created_at=utcnow()))
    db.commit()


# --- No test reaches the outside world (multi-store M1, MT3) ----------------
# Blank credentials alone are not enough: a test path that forgot a mock
# still sends a request (MT3's connector methods work now, so calling one
# unpatched would ask Toast - it happened once, with empty credentials, and
# Toast answered 401). Every outbound connection and DNS lookup is refused for
# the whole run; loopback and Unix sockets stay allowed.
import socket  # noqa: E402

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _local(sock, address) -> bool:
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return True
    return isinstance(address, tuple) and bool(address) and str(address[0]) in _LOOPBACK


def _no_network_connect(self, address):
    if not _local(self, address):
        raise OSError(f"tests must not reach the network (connection to {address!r}); mock the call")
    return _real_connect(self, address)


def _no_network_connect_ex(self, address):
    if not _local(self, address):
        raise OSError(f"tests must not reach the network (connection to {address!r}); mock the call")
    return _real_connect_ex(self, address)


def _no_network_getaddrinfo(host, *args, **kwargs):
    if host is not None and str(host) not in _LOOPBACK:
        raise socket.gaierror(f"tests must not reach the network (DNS lookup of {host!r}); mock the call")
    return _real_getaddrinfo(host, *args, **kwargs)


def _install_network_guard():
    socket.socket.connect = _no_network_connect
    socket.socket.connect_ex = _no_network_connect_ex
    socket.getaddrinfo = _no_network_getaddrinfo


# Already while test modules are collected (imported), not only once the
# first test's fixtures run.
_install_network_guard()


@pytest.fixture(autouse=True, scope="session")
def _no_outside_network():
    _install_network_guard()
    yield
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    socket.getaddrinfo = _real_getaddrinfo


# --- Store pack (KloudC RetailOps M2) -------------------------------------------------
# Every test starts with the default pack (packs/generic.json) and no pinned
# pack. The suite's checklist tests were written against the legacy built-in
# checklists (services/checklist_builtin.py, what migration 0039 seeds), so
# the checklist fallback serves those here; a test marked `pack_checklists`
# gets the active store pack's checklists instead (the product behaviour).
# A module marked `suite_pack` gets tests/fixtures/packs/suite.json: made-up
# rules in the shape those older modules were written for (two WhatsApp roles,
# six flyer categories, kitchen groups). A test marked `legacy_pack` runs with
# the private legacy pack and settings (docs/M2_CONTRACT.md 1.6) - the tests
# that check the original store's own rules - and is skipped when they aren't
# on this machine (CI).


def pytest_configure(config):
    config.addinivalue_line("markers", "pack_checklists: serve the store pack's checklists, not the legacy fixtures")
    config.addinivalue_line("markers", "legacy_pack: needs the private legacy store pack (skipped without it)")
    config.addinivalue_line(
        "markers", "suite_pack: made-up rules in the older modules' shape (tests/fixtures/packs/suite.json)"
    )


@pytest.fixture(autouse=True)
def _store_pack_state(request, monkeypatch):
    from app.services import checklist_schemas
    from app.store import pack as store_pack

    from tests.store_fixtures import legacy_checklist_seeds, use_legacy_pack, use_suite_pack

    store_pack.set_override(None)
    store_pack.reload_pack()
    if request.node.get_closest_marker("pack_checklists") is None:
        monkeypatch.setattr(checklist_schemas, "store_seed_definitions", legacy_checklist_seeds)
    if request.node.get_closest_marker("suite_pack") is not None:
        use_suite_pack()
    if request.node.get_closest_marker("legacy_pack") is not None:
        use_legacy_pack(monkeypatch)
    checklist_schemas.clear_cache()
    yield
    store_pack.set_override(None)
    store_pack.reload_pack()
    checklist_schemas.clear_cache()
