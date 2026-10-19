"""The REAL KloudC RetailOps server in multi mode for the website platform checks
(platform.mjs), instead of mock_server.mjs: a throwaway platform database and
one PostgreSQL database per store on a local cluster, the stores the checks
expect, and fakes for every outside service.

    backend/.venv/bin/python frontend/e2e/platform/real_server.py

Environment (run_real.sh sets these):
  E2E_PG_URL        admin URL of the cluster (default: the local dev cluster)
  E2E_DB_PREFIX     database name prefix (default so_e2e); every database made
                    here starts with it and is dropped on exit
  MOCK_PORT         port (default 8872)
  MOCK_DIST         the built website
  MOCK_STORE_PASSWORD / MOCK_MASTER_PASSWORD   random passwords from run_real.sh

Stores: DEMO_GROCERY (Toast; users owner (admin) + staff), PAUSED_MART
(suspended), MOVED_MART (moved to https://moved.retailops.invalid; needs
DEDICATED_HOSTING_ENABLED, so the Hosting tab is "on" in this run),
CLOVER_MART (Clover). Master: owner. Nothing here calls Toast, Clover,
WhatsApp, SMTP, push or Anthropic: credentials are blank, the schedulers are
off and the connection tests are replaced by fakes.
"""

import atexit
import base64
import os
import pathlib
import secrets
import signal
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

PG_URL = os.environ.get("E2E_PG_URL", "postgresql+psycopg://storeops@127.0.0.1:5433/postgres")
PREFIX = os.environ.get("E2E_DB_PREFIX", "so_e2e")
PORT = int(os.environ.get("MOCK_PORT", "8872"))
DIST = os.environ.get("MOCK_DIST", "")
STORE_PW = os.environ["MOCK_STORE_PASSWORD"]
MASTER_PW = os.environ["MOCK_MASTER_PASSWORD"]
if not PREFIX.startswith("so_") or not PREFIX.replace("_", "").isalnum():
    raise SystemExit(f"E2E_DB_PREFIX={PREFIX!r}: use so_<letters/digits>")

PLATFORM_DB = f"{PREFIX}_platform"
STORE_PREFIX = f"{PREFIX}_s_"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="retailops-platform-e2e-"))


def _url(db_name: str) -> str:
    return PG_URL.rsplit("/", 1)[0] + "/" + db_name


os.environ.update(
    {
        "TENANCY_MODE": "multi",
        "APP_ENV": "test",
        "PLATFORM_DATABASE_URL": _url(PLATFORM_DB),
        "STORE_DATABASE_URL_TEMPLATE": _url("{db_name}"),
        "PG_ADMIN_URL": PG_URL,
        "STORE_DB_PREFIX": STORE_PREFIX,
        # A throwaway key ring for this run only (never written to disk).
        "RETAILOPS_SECRET_KEYS": "e2e1:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        "UPLOADS_ROOT": str(TMP / "uploads"),
        "PLATFORM_PUBLIC_URL": f"http://127.0.0.1:{PORT}",
        "PLATFORM_DOMAIN": "retailops.invalid",
        "DEDICATED_HOSTING_ENABLED": "true",
        "SIGNUP_ENABLED": "true",
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "TENANT_MAX_ENGINES": "10",
        # Many logins and lookups from one address in a few minutes.
        "DIRECTORY_RATE_PER_MINUTE": "1000",
        "DIRECTORY_RATE_PER_HOUR": "100000",
        "LOGIN_IP_LOCKOUT_FAILURES": "1000",
        "SIGNUP_RATE_PER_HOUR": "100",
        "PLATFORM_WRITE_RATE_PER_MINUTE": "1000",
        "FIREBASE_CREDENTIALS_FILE": "",
        "WHAPI_API_TOKEN": "",
        "GMAIL_APP_PASSWORD": "",
        "ANTHROPIC_API_KEY": "",
        "TOAST_CLIENT_ID": "",
        "TOAST_CLIENT_SECRET": "",
        "TOAST_ANALYTICS_CLIENT_ID": "",
        "TOAST_ANALYTICS_CLIENT_SECRET": "",
    }
)
if DIST:
    os.environ["FRONTEND_DIST_DIR"] = DIST

from sqlalchemy import create_engine, text  # noqa: E402


def _admin():
    return create_engine(PG_URL, isolation_level="AUTOCOMMIT")


def drop_all() -> None:
    from app.platform.db import reset_platform_engine

    try:
        from app.tenancy import engines

        engines.dispose_all_engines()
    except Exception:  # noqa: BLE001
        pass
    reset_platform_engine()
    eng = _admin()
    with eng.connect() as c:
        like = PREFIX.replace("_", r"\_") + r"\_%"
        for (name,) in c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p"), {"p": like}).all():
            assert name.startswith(PREFIX + "_"), name
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    eng.dispose()


def seed() -> None:
    from app.auth import hash_password
    from app.models import User
    from app.platform import connector_tests, provisioning, stores
    from app.platform.db import platform_session, upgrade_platform_db
    from app.platform.masters import create_master
    from app.platform.models import Store
    from app.platform.sources import configure
    from app.tenancy import tenant_session

    drop_all()
    eng = _admin()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{PLATFORM_DB}"'))
    eng.dispose()
    upgrade_platform_db(_url(PLATFORM_DB))
    configure(force=True)

    with platform_session() as pdb:
        create_master(pdb, "owner", MASTER_PW, "The Owner", actor="e2e", actor_type="system")

    def make(code, name, pack, pos):
        out = provisioning.create_store_manually(
            code=code, name=name, pack_id=pack, timezone="America/Chicago", pos_provider=pos,
            owner_username="owner", owner_password_hash=hash_password(STORE_PW), actor="platform:e2e", run="inline",
        )
        assert out["status"] == "active", out

    make("DEMO_GROCERY", "Demo Grocery", "test_toast", "toast")
    make("PAUSED_MART", "Paused Mart", "test_toast", "toast")
    make("MOVED_MART", "Moved Mart", "test_toast", "toast")
    make("CLOVER_MART", "Clover Mart", "test_clover", "clover")
    with tenant_session("DEMO_GROCERY") as db:
        db.add(User(username="staff", password_hash=hash_password(STORE_PW + "-staff"), is_admin=False,
                    allowed_features="[]"))
        db.commit()
    with platform_session() as pdb:
        stores.suspend(pdb, "PAUSED_MART", actor="platform:e2e", reason="e2e: a paused store")
        moved = pdb.query(Store).filter(Store.code == "MOVED_MART").one()
        moved.hosting = "dedicated"
        moved.api_base_url = "https://moved.retailops.invalid"
        pdb.commit()
    from app.platform.bridge import store_changed

    store_changed("MOVED_MART")

    # Connection tests: fakes (never Toast / WhatsApp / SMTP / Anthropic).
    def fake(kind):
        def run(public, secrets_):
            return connector_tests.TestResult(True, f"Connected ({kind}, e2e fake).")

        return run

    for kind in list(connector_tests.TESTERS):
        connector_tests.TESTERS[kind] = fake(kind)


def main() -> None:
    atexit.register(drop_all)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    seed()
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
