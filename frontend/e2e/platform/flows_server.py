"""The REAL StoreOps server in multi mode for the end-to-end flow checks
(flows.mjs): a throwaway PostgreSQL cluster prefix, bootstrapped the way the
owner does it (``python -m app.platform migrate-platform`` and
``create-master`` as separate processes), schedulers ON (leader election,
per-store jobs), no stores until the checks sign them up and a master
approves them.

    backend/.venv/bin/python frontend/e2e/platform/flows_server.py

Environment (run_flows.sh sets these):
  E2E_PG_URL        admin URL of the cluster (default: the local dev cluster)
  E2E_DB_PREFIX     database name prefix (default so_flows); every database
                    made here starts with it and is dropped on exit
  MOCK_PORT         port (default 8873)
  MOCK_DIST         the built website
  MOCK_MASTER_PASSWORD   random master password from run_flows.sh
  FLOWS_WORK        where toast_calls.jsonl is written

Nothing here reaches Toast, Clover, WhatsApp, SMTP, push or Anthropic: every
httpx client in this process answers from a fake (Toast hosts) or refuses
(anything else); the connection tests are fakes; notifications and team jobs
are off.
"""

import atexit
import base64
import json
import os
import pathlib
import secrets
import signal
import subprocess
import sys
import tempfile
import threading

BACKEND = pathlib.Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

PG_URL = os.environ.get("E2E_PG_URL", "postgresql+psycopg://storeops@127.0.0.1:5433/postgres")
PREFIX = os.environ.get("E2E_DB_PREFIX", "so_flows")
PORT = int(os.environ.get("MOCK_PORT", "8873"))
DIST = os.environ.get("MOCK_DIST", "")
MASTER_PW = os.environ["MOCK_MASTER_PASSWORD"]
WORK = pathlib.Path(os.environ.get("FLOWS_WORK") or tempfile.mkdtemp(prefix="storeops-flows-"))
if not PREFIX.startswith("so_") or not PREFIX.replace("_", "").isalnum():
    raise SystemExit(f"E2E_DB_PREFIX={PREFIX!r}: use so_<letters/digits>")

PLATFORM_DB = f"{PREFIX}_platform"
STORE_PREFIX = f"{PREFIX}_s_"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="storeops-flows-up-"))
CALLS = WORK / "toast_calls.jsonl"


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
        "STOREOPS_SECRET_KEYS": "flows1:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        "UPLOADS_ROOT": str(TMP / "uploads"),
        "PLATFORM_PUBLIC_URL": f"http://127.0.0.1:{PORT}",
        "PLATFORM_DOMAIN": "storeops.invalid",
        "SIGNUP_ENABLED": "true",
        "SCHEDULER_ENABLED": "true",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "TENANT_MAX_ENGINES": "10",
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

# --- every outside call is faked or refused (before the app is imported) ------------------
import httpx  # noqa: E402

_REAL_CLIENT = httpx.Client
_calls_lock = threading.Lock()


def _record(entry: dict) -> None:
    with _calls_lock, CALLS.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host or ""
    if not host.endswith("toasttab.com"):
        raise httpx.ConnectError(f"e2e: outside call refused ({host})", request=request)
    path = request.url.path
    if path.endswith("/authentication/v1/authentication/login"):
        body = json.loads(request.content)
        _record({"kind": "login", "client_id": body.get("clientId")})
        return httpx.Response(200, json={"token": {"accessToken": "tok-" + str(body.get("clientId")), "expiresIn": 3600}})
    guid = request.headers.get("Toast-Restaurant-External-ID")
    _record({"kind": "get", "path": path, "guid": guid, "auth": request.headers.get("Authorization")})
    if path.endswith("/orders/v2/ordersBulk") and request.url.params.get("page") == "1":
        day = request.url.params.get("businessDate", "20260101")
        paid = f"{day[:4]}-{day[4:6]}-{day[6:]}T18:00:00.000+0000"
        return httpx.Response(200, json=[{
            "guid": f"order-{guid}-{day}",
            "checks": [{"guid": f"check-{guid}-{day}", "paidDate": paid,
                        "selections": [{"guid": f"sel-{guid}-{day}", "item": {"guid": "item-1"},
                                        "quantity": 1, "price": 5.0}]}],
        }])
    return httpx.Response(200, json=[])


class _FakeClient(_REAL_CLIENT):
    def __init__(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(_handler)
        super().__init__(*a, **kw)


def _refuse(*a, **kw):
    raise httpx.ConnectError("e2e: outside call refused")


httpx.Client = _FakeClient
httpx.get = httpx.post = httpx.put = httpx.request = _refuse

from sqlalchemy import create_engine, text  # noqa: E402

_cli_env = dict(os.environ)


def _admin():
    return create_engine(PG_URL, isolation_level="AUTOCOMMIT")


def drop_all() -> None:
    try:
        from app.platform.db import reset_platform_engine
        from app.tenancy import engines

        engines.dispose_all_engines()
        reset_platform_engine()
    except Exception:  # noqa: BLE001
        pass
    eng = _admin()
    with eng.connect() as c:
        like = PREFIX.replace("_", r"\_") + r"\_%"
        for (name,) in c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p"), {"p": like}).all():
            assert name.startswith(PREFIX + "_"), name
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    eng.dispose()


def bootstrap() -> None:
    """What the owner runs on a new deployment (PLATFORM_CONTRACT owner actions)."""
    drop_all()
    eng = _admin()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{PLATFORM_DB}"'))
    eng.dispose()
    py = sys.executable
    subprocess.run([py, "-m", "app.platform", "migrate-platform"], check=True, env=_cli_env, cwd=BACKEND)
    subprocess.run([py, "-m", "app.platform", "create-master", "--username", "owner", "--display-name", "The Owner"],
                   check=True, env={**_cli_env, "STOREOPS_MASTER_PASSWORD": MASTER_PW}, cwd=BACKEND)

    from app.platform import connector_tests

    def fake(kind):
        def run(public, secrets_):
            return connector_tests.TestResult(True, f"Connected ({kind}, e2e fake).")

        return run

    for kind in list(connector_tests.TESTERS):
        connector_tests.TESTERS[kind] = fake(kind)


def main() -> None:
    atexit.register(drop_all)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    CALLS.write_text("")
    bootstrap()
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
