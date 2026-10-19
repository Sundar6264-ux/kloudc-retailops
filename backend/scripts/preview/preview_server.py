"""Local preview of the shared StoreOps service (one server, a database per store).

    backend/.venv/bin/python scripts/preview/preview_server.py [--port 8870] [--reset] [--build-only]

/Users/sundar/storeops/preview.sh wraps this (starts PostgreSQL, builds the
website, then runs this). What it gives you on http://127.0.0.1:<port>:

- the platform (control plane) database and a master login (Store ID
  ``PLATFORM``) for the master console;
- STOREOPS_TEST_TOAST and STOREOPS_TEST_CLOVER, the two fake test stores,
  provisioned the way an approved store is (database, migrations, store-init
  with their pack, the owner's admin login) plus a staff login on the Toast
  store; they have no sales (their POS keys are blank, like a store that has
  not connected Toast/Clover yet);
- DEMO, the made-up demo store (scripts/demo/generate_demo_data.py: ~60 days
  of sales, stock, vendors, invoices, checklists and team data, all made up),
  registered as an active store with ``demo_mode`` on.

State lives in PostgreSQL databases named ``so_preview_*`` on the local dev
cluster and in the state folder (default ``~/storeops/.preview``: the key
ring, the generated logins, uploads). It is kept between runs; ``--reset``
drops it all and builds it again. Passwords are generated on the first build
and kept in ``<state>/logins.json`` (mode 600, outside git).

Nothing here reaches Toast, Clover, WhatsApp, SMTP, push or Anthropic:
schedulers and notifications are off, every outside base URL points at the
discard port, every httpx call is refused, and Settings > Integrations "Test
connection" answers from a fake ("preview fake").
"""

import argparse
import base64
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures" / "stores"
PREFIX = "so_preview"
PLATFORM_DB = f"{PREFIX}_platform"
STORE_PREFIX = f"{PREFIX}_s_"
DISCARD = "http://127.0.0.1:9"

# code -> (pack id, POS, owner login)
TEST_STORES = {
    "STOREOPS_TEST_TOAST": ("test_toast", "toast", "owner"),
    "STOREOPS_TEST_CLOVER": ("test_clover", "clover", "owner"),
}
DEMO_CODE = "DEMO"
DEMO_ADMIN = "store_manager"  # the demo data's own store admin (scripts/demo/demo_catalog.py)
DEMO_REVIEW = "appreview"  # the App Review login the generator makes
# fixture .env name -> store setting (docs/PLATFORM_CONTRACT.md 8.2)
ENV_TO_SETTING = {
    "STORE_NAME": "store_name",
    "STORE_SHORT_NAME": "store_short_name",
    "STORE_ADDRESS": "store_address",
    "STORE_PHONE": "store_phone",
    "STORE_DESCRIPTION": "store_description",
    "STORE_TIMEZONE": "store_timezone",
    "BRAND_COLOR": "brand_color",
    "BRAND_SECONDARY_COLOR": "brand_secondary_color",
    "POS_PROVIDER": "pos_provider",
    "LABELS_URL": "labels_url",
    "DEMO_MODE": "demo_mode",
}


def fixture_settings(code: str) -> dict:
    out = {}
    for line in (FIXTURES / f"{code}.env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ENV_TO_SETTING and value.strip():
            v = value.strip()
            out[ENV_TO_SETTING[key]] = {"true": True, "false": False}.get(v.lower(), v)
    # No outside AI calls from a preview, whatever the fixture says.
    out["ai_enabled"] = False
    return out


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=8870)
    p.add_argument("--pg-url", default="postgresql+psycopg://storeops@127.0.0.1:5433/postgres",
                   help="admin URL of the local cluster (must allow CREATE DATABASE)")
    p.add_argument("--state", default=str(pathlib.Path.home() / "storeops" / ".preview"))
    p.add_argument("--dist", default="", help="the built website (default <state>/dist)")
    p.add_argument("--reset", action="store_true", help="drop the preview databases and files, build again")
    p.add_argument("--build-only", action="store_true", help="build (if needed), print the logins, do not serve")
    p.add_argument("--drop", action="store_true", help="drop the preview databases and files, then stop")
    return p.parse_args()


ARGS = parse_args()
STATE = pathlib.Path(ARGS.state).expanduser().resolve()
if STATE in (pathlib.Path.home(), pathlib.Path("/")) or str(STATE).startswith(str(BACKEND.parent)):
    raise SystemExit(f"refusing state folder {STATE} (use a folder outside the repository)")
STATE.mkdir(parents=True, exist_ok=True)
os.chmod(STATE, 0o700)
KEYRING_FILE = STATE / "keyring"
LOGINS_FILE = STATE / "logins.json"
UPLOADS = STATE / "uploads"
DIST = pathlib.Path(ARGS.dist).expanduser() if ARGS.dist else STATE / "dist"


def _url(db_name: str) -> str:
    return ARGS.pg_url.rsplit("/", 1)[0] + "/" + db_name


def _write_private(path: pathlib.Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _keyring() -> str:
    if KEYRING_FILE.is_file():
        return KEYRING_FILE.read_text().strip()
    value = "preview1:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    _write_private(KEYRING_FILE, value + "\n")
    return value


os.environ.update(
    {
        "TENANCY_MODE": "multi",
        "APP_ENV": "test",
        "PLATFORM_DATABASE_URL": _url(PLATFORM_DB),
        "STORE_DATABASE_URL_TEMPLATE": _url("{db_name}"),
        "PG_ADMIN_URL": ARGS.pg_url,
        "STORE_DB_PREFIX": STORE_PREFIX,
        "STOREOPS_SECRET_KEYS": _keyring(),
        "UPLOADS_ROOT": str(UPLOADS),
        "PLATFORM_PUBLIC_URL": f"http://127.0.0.1:{ARGS.port}",
        "PLATFORM_DOMAIN": "storeops.invalid",
        "SIGNUP_ENABLED": "true",
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "SESSION_COOKIE_SECURE": "false",  # plain http on 127.0.0.1
        "TENANT_MAX_ENGINES": "10",
        "FIREBASE_CREDENTIALS_FILE": "",
        "WHAPI_API_TOKEN": "",
        "GMAIL_APP_PASSWORD": "",
        "ANTHROPIC_API_KEY": "",
        "TOAST_CLIENT_ID": "",
        "TOAST_CLIENT_SECRET": "",
        "TOAST_ANALYTICS_CLIENT_ID": "",
        "TOAST_ANALYTICS_CLIENT_SECRET": "",
        "TOAST_API_BASE": DISCARD,
        "TOAST_ANALYTICS_BASE": DISCARD,
        "WHAPI_BASE_URL": DISCARD,
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": "9",
        "AI_ENABLED": "false",
    }
)
if DIST.is_dir():
    os.environ["FRONTEND_DIST_DIR"] = str(DIST)
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# --- nothing leaves the machine (before the app is imported) ------------------------------
import httpx  # noqa: E402

_REAL_CLIENT = httpx.Client


def _refuse_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"preview: outside call refused ({request.url.host})", request=request)


class _NoNetworkClient(_REAL_CLIENT):
    def __init__(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(_refuse_handler)
        super().__init__(*a, **kw)


def _refuse(*a, **kw):
    raise httpx.ConnectError("preview: outside call refused")


httpx.Client = _NoNetworkClient
httpx.get = httpx.post = httpx.put = httpx.request = _refuse

from sqlalchemy import create_engine, text  # noqa: E402


def _admin():
    return create_engine(ARGS.pg_url, isolation_level="AUTOCOMMIT")


def _preview_databases() -> list:
    eng = _admin()
    try:
        with eng.connect() as c:
            like = PREFIX.replace("_", r"\_") + r"\_%"
            rows = c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p ORDER BY datname"), {"p": like})
            return [r[0] for r in rows]
    finally:
        eng.dispose()


def drop_all() -> None:
    eng = _admin()
    try:
        with eng.connect() as c:
            for name in _preview_databases():
                assert name.startswith(PREFIX + "_"), name
                c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        eng.dispose()
    shutil.rmtree(UPLOADS, ignore_errors=True)
    LOGINS_FILE.unlink(missing_ok=True)


def _password() -> str:
    return secrets.token_urlsafe(12)


def _build_demo(logins: dict) -> None:
    """The DEMO store's made-up data -> its own PostgreSQL database, then the
    platform row (the generator already ran store-init with the demo pack)."""
    from app.auth import hash_password
    from app.platform.db import platform_session
    from app.platform.models import Store, StoreSetting
    from app.platform.codes import db_name_for
    from app.platform.provisioning import store_uploads_root

    db_name = db_name_for(DEMO_CODE)
    eng = _admin()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{db_name}" TEMPLATE template0 ENCODING \'UTF8\' LC_COLLATE \'C\' LC_CTYPE \'C\''))
    eng.dispose()
    review_pw, admin_pw = _password(), _password()
    with tempfile.TemporaryDirectory(prefix="storeops-preview-demo-") as tmp:
        tmp_path = pathlib.Path(tmp)
        pw_file = tmp_path / "review_password"
        _write_private(pw_file, review_pw)
        out = tmp_path / "build"
        # A clean environment: the generator is a single-store (SQLite) process.
        env = {"HOME": str(pathlib.Path.home()), "PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"}
        proc = subprocess.run(
            [sys.executable, "scripts/demo/generate_demo_data.py", "--out", str(out), "--days", "60",
             "--review-password-file", str(pw_file), "--pg-url", _url(db_name)],
            cwd=str(BACKEND), env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"demo data failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}")
        root = store_uploads_root(DEMO_CODE)
        root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out / "uploads", root, dirs_exist_ok=True)
    store_eng = create_engine(_url(db_name))
    with store_eng.begin() as c:
        c.execute(text("UPDATE users SET password_hash = :h, is_admin = true WHERE username = :u"),
                  {"h": hash_password(admin_pw), "u": DEMO_ADMIN})
        instance_id = c.execute(text("SELECT instance_id FROM store_identity WHERE id = 1")).scalar()
        revision = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    store_eng.dispose()
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    settings = fixture_settings(DEMO_CODE)
    with platform_session() as pdb:
        store = Store(
            code=DEMO_CODE, name=settings["store_name"], status="active", hosting="shared", db_name=db_name,
            timezone=settings["store_timezone"], pos_provider="csv", pack_id="demo", settings_version=1,
            instance_id=(instance_id or "")[:32] or None, schema_revision=revision, provisioning_step="activate",
            owner_username=DEMO_ADMIN, created_at=now, created_by="preview", approved_by="preview",
            approved_at=now, updated_at=now,
        )
        pdb.add(store)
        pdb.flush()
        for key, value in settings.items():
            pdb.add(StoreSetting(store_id=store.id, key=key, value=json.dumps(value), updated_at=now,
                                 updated_by="preview"))
        pdb.commit()
    logins[DEMO_CODE] = [
        {"username": DEMO_ADMIN, "password": admin_pw, "role": "store admin (demo mode: sends are off)"},
        {"username": DEMO_REVIEW, "password": review_pw, "role": "App Review login"},
    ]


def build() -> dict:
    from app.auth import hash_password
    from app.models import User
    from app.platform import provisioning, stores
    from app.platform.bridge import store_changed
    from app.platform.db import platform_session, upgrade_platform_db
    from app.platform.masters import create_master
    from app.platform.sources import configure
    from app.tenancy import tenant_session

    drop_all()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    eng = _admin()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{PLATFORM_DB}" TEMPLATE template0 ENCODING \'UTF8\' LC_COLLATE \'C\' LC_CTYPE \'C\''))
    eng.dispose()
    upgrade_platform_db(_url(PLATFORM_DB))
    configure(force=True)

    logins: dict = {"PLATFORM": [], "url": f"http://127.0.0.1:{ARGS.port}"}
    master_pw = _password()
    with platform_session() as pdb:
        create_master(pdb, "master", master_pw, "Preview master", actor="preview", actor_type="system")
    logins["PLATFORM"].append({"username": "master", "password": master_pw, "role": "master (control plane)"})

    for code, (pack, pos, owner) in TEST_STORES.items():
        settings = fixture_settings(code)
        pw = _password()
        out = provisioning.create_store_manually(
            code=code, name=settings["store_name"], pack_id=pack, timezone=settings["store_timezone"],
            pos_provider=pos, owner_username=owner, owner_password_hash=hash_password(pw), actor="preview",
            run="inline",
        )
        if out.get("status") != "active":
            raise SystemExit(f"provisioning {code} failed: {out}")
        with platform_session() as pdb:
            stores.put_settings(pdb, code, settings, actor="preview", actor_type="system")
            pdb.commit()
        logins[code] = [{"username": owner, "password": pw, "role": "store admin (owner)"}]
    staff_pw = _password()
    with tenant_session("STOREOPS_TEST_TOAST") as db:
        db.add(User(username="staff", password_hash=hash_password(staff_pw), is_admin=False,
                    allowed_features=json.dumps(["overview", "items_sold", "reorder_candidates", "vendor_calendar",
                                                 "team_tasks", "team_chat", "daily_checklists", "help"])))
        db.commit()
    logins["STOREOPS_TEST_TOAST"].append({"username": "staff", "password": staff_pw, "role": "staff"})

    _build_demo(logins)
    for code in [*TEST_STORES, DEMO_CODE]:
        store_changed(code)
    _write_private(LOGINS_FILE, json.dumps(logins, indent=1) + "\n")
    return logins


def print_logins(logins: dict) -> None:
    url = f"http://127.0.0.1:{ARGS.port}"
    print("\nStoreOps preview (one shared server, a PostgreSQL database per store)")
    print(f"  Website:  {url}/   (one login page: Store ID + Username + Password)")
    print(f"  Sign-up:  {url}/#/signup   Master console: sign in with Store ID PLATFORM")
    for code, rows in logins.items():
        if code == "url":
            continue
        for r in rows:
            print(f"  Store ID {code:<22} user {r['username']:<16} password {r['password']:<18} ({r['role']})")
    print(f"  Logins are also in {LOGINS_FILE}")
    print("  App (simulator): flutter run --dart-define=APP_ENV=development "
          f"--dart-define=PLATFORM_API_URL={url}")
    print("  Nothing here calls Toast, Clover, WhatsApp, email, push or AI; Test connection is a fake.\n")


def main() -> None:
    if ARGS.drop:
        drop_all()
        print("preview databases and files dropped")
        return
    have = set(_preview_databases())
    if ARGS.reset or PLATFORM_DB not in have or not LOGINS_FILE.is_file():
        print("building the preview (platform database, 3 stores, made-up DEMO data; ~1-2 minutes)...", flush=True)
        logins = build()
    else:
        logins = json.loads(LOGINS_FILE.read_text())
    print_logins(logins)
    if ARGS.build_only:
        return

    from app.platform import connector_tests

    def fake(kind):
        def run(public, secrets_):
            return connector_tests.TestResult(True, f"Connected ({kind}, preview fake: nothing was called).")

        return run

    for kind in list(connector_tests.TESTERS):
        connector_tests.TESTERS[kind] = fake(kind)

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=ARGS.port, log_level="warning")


if __name__ == "__main__":
    main()
