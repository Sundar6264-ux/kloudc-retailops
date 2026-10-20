"""The REAL server in multi mode for the System panel screens (system.mjs):
a throwaway platform database on the local cluster, one master, the job
queue and live updates switched on, and a seeded queue (done, waiting,
retrying, failed jobs; a running worker and a silent one). Object storage
settings are placeholders: the System panel only reads them, nothing calls
S3 / R2 or any other outside service.

    E2E_DB_PREFIX=so_wrc_e2e MOCK_PORT=8883 MOCK_MASTER_PASSWORD=... \\
        backend/.venv/bin/python frontend/e2e/system/system_server.py
"""

import atexit
import base64
import datetime as dt
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
PREFIX = os.environ.get("E2E_DB_PREFIX", "so_wrc_e2e")
PORT = int(os.environ.get("MOCK_PORT", "8883"))
DIST = os.environ.get("MOCK_DIST", "")
MASTER_PW = os.environ["MOCK_MASTER_PASSWORD"]
if not PREFIX.startswith("so_") or not PREFIX.replace("_", "").isalnum():
    raise SystemExit(f"E2E_DB_PREFIX={PREFIX!r}: use so_<letters/digits>")

PLATFORM_DB = f"{PREFIX}_platform"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="retailops-system-e2e-"))


def _url(db_name: str) -> str:
    return PG_URL.rsplit("/", 1)[0] + "/" + db_name


os.environ.update(
    {
        "TENANCY_MODE": "multi",
        "APP_ENV": "test",
        "PLATFORM_DATABASE_URL": _url(PLATFORM_DB),
        "STORE_DATABASE_URL_TEMPLATE": _url("{db_name}"),
        "PG_ADMIN_URL": PG_URL,
        "STORE_DB_PREFIX": f"{PREFIX}_s_",
        "RETAILOPS_SECRET_KEYS": "e2e1:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        "UPLOADS_ROOT": str(TMP / "uploads"),
        "PLATFORM_PUBLIC_URL": f"http://127.0.0.1:{PORT}",
        "PLATFORM_DOMAIN": "retailops.invalid",
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "TENANT_MAX_ENGINES": "5",
        "PLATFORM_WRITE_RATE_PER_MINUTE": "1000",
        "LOGIN_IP_LOCKOUT_FAILURES": "1000",
        "DIRECTORY_RATE_PER_MINUTE": "1000",
        "DIRECTORY_RATE_PER_HOUR": "100000",
        # Track C switches, on for this run only.
        "JOB_QUEUE_MODE": "queue",
        "LIVE_UPDATES_MODE": "postgres",
        "STORAGE_BACKEND": os.environ.get("E2E_STORAGE_BACKEND", "s3"),
        "STORAGE_S3_ENDPOINT": "https://example-account.r2.cloudflarestorage.com",
        "STORAGE_S3_BUCKET": "kloudc-store-files",
        "STORAGE_S3_ACCESS_KEY_ID": "placeholder-key-id",
        "STORAGE_S3_SECRET_ACCESS_KEY": "placeholder-secret",
        "FIREBASE_CREDENTIALS_FILE": "",
        "WHAPI_API_TOKEN": "",
        "GMAIL_APP_PASSWORD": "",
        "ANTHROPIC_API_KEY": "",
        "TOAST_CLIENT_ID": "",
        "TOAST_CLIENT_SECRET": "",
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
        from app import livebus

        livebus.stop()
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
    from app.jobqueue import queue
    from app.platform.db import platform_engine, platform_session, upgrade_platform_db
    from app.platform.masters import create_master
    from app.platform.sources import configure

    drop_all()
    eng = _admin()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{PLATFORM_DB}"'))
    eng.dispose()
    upgrade_platform_db(_url(PLATFORM_DB))
    configure(force=True)
    with platform_session() as pdb:
        create_master(pdb, "owner", MASTER_PW, "The Owner", actor="e2e", actor_type="system")

    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        ("store_job", "DEMO_GROCERY", "done", 1, 2, None, now - dt.timedelta(hours=3)),
        ("store_job", "CORNER_MART", "done", 1, 2, None, now - dt.timedelta(minutes=50)),
        ("store_export", "DEMO_GROCERY", "dead", 3, 3,
         "TimeoutError: the store database did not answer within 60 s", now - dt.timedelta(minutes=34)),
        ("store_job", "CORNER_MART", "failed", 1, 2,
         "ToastFetchError: Toast answered 503 (service unavailable)", now - dt.timedelta(minutes=2)),
        ("jobqueue_maintenance", None, "queued", 0, 1, None, now - dt.timedelta(seconds=20)),
        ("store_export", "CORNER_MART", "running", 1, 3, None, now - dt.timedelta(minutes=1)),
    ]
    with platform_engine().begin() as c:
        for kind, store, status, attempts, max_attempts, error, at in rows:
            c.execute(
                text(
                    "INSERT INTO jobs (kind, store_code, payload, status, priority, attempts, max_attempts, "
                    "timeout_seconds, run_after, locked_by, heartbeat_at, error, created_at, started_at, finished_at, "
                    "updated_at) VALUES (:k, :s, '{}', :st, 0, :a, :m, 900, :ra, :lb, :hb, :e, :at, :sa, :fa, :at)"
                ),
                {
                    "k": kind, "s": store, "st": status, "a": attempts, "m": max_attempts, "e": error, "at": at,
                    "ra": at + dt.timedelta(minutes=1) if status == "failed" else at,
                    "lb": "web-1.internal:4120:a1b2c3d4" if status == "running" else None,
                    "hb": now if status == "running" else None,
                    "sa": at if status in ("done", "dead", "failed", "running") else None,
                    "fa": at + dt.timedelta(seconds=40) if status in ("done", "dead", "failed") else None,
                },
            )
    queue.register_worker("web-1.internal:4120:a1b2c3d4", "web-1.internal", 4120, None, 2)
    queue.worker_counts("web-1.internal:4120:a1b2c3d4", done=41, failed=2)
    queue.register_worker("web-2.internal:3311:e5f6a7b8", "web-2.internal", 3311, ["store_export"], 1)
    with platform_engine().begin() as c:
        c.execute(text("UPDATE job_workers SET heartbeat_at = :t WHERE id LIKE 'web-2%'"),
                  {"t": now - dt.timedelta(minutes=6)})


def main() -> None:
    atexit.register(drop_all)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    seed()
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
