"""Migration round trip on a fresh PostgreSQL cluster (platform contract 11).

    backend/.venv/bin/python scripts/pg/migration_roundtrip.py \
        [--pg-url postgresql+psycopg://storeops@127.0.0.1:5433/postgres] [--prefix so_mig]

Checks, each on databases it creates (named <prefix>_*) and drops at the end:

1. Platform database (``alembic -n platform``): upgrade head -> downgrade
   base (nothing left but alembic_version) -> upgrade head; the schema
   (tables, columns, indexes, constraints, triggers) is identical both times.
2. Store database (A's frozen baseline): upgrade an empty database to head;
   a second upgrade is a no-op; a downgrade below the baseline is refused
   and leaves the schema untouched.
3. Store revisions after the baseline: a scratch revision (a new table, a new
   column, an index) in a TEMPORARY copy of alembic/ - upgrade -> downgrade
   -> upgrade, the schema back to the head's exactly after the downgrade.
4. ``python -m app.platform migrate-all`` with A's migrator: two stores made
   by provisioning (create database, migrate, store-init, owner) end at head.

Exit code 1 on any failure.
"""

import argparse
import os
import pathlib
import shutil
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

ap = argparse.ArgumentParser()
ap.add_argument("--pg-url", default=os.environ.get("STOREOPS_TEST_PG_URL", "postgresql+psycopg://storeops@127.0.0.1:5433/postgres"))
ap.add_argument("--prefix", default="so_mig")
args = ap.parse_args()
PREFIX = args.prefix
if not PREFIX.startswith("so_") or not PREFIX.replace("_", "").isalnum():
    raise SystemExit("--prefix must look like so_<letters/digits>")
TMP = pathlib.Path(tempfile.mkdtemp(prefix="storeops-migcheck-"))


def url(db: str) -> str:
    return args.pg_url.rsplit("/", 1)[0] + "/" + db


PLATFORM_DB = f"{PREFIX}_platform"
os.environ.update(
    {
        "TENANCY_MODE": "multi",
        "APP_ENV": "test",
        "PLATFORM_DATABASE_URL": url(PLATFORM_DB),
        "STORE_DATABASE_URL_TEMPLATE": url("{db_name}"),
        "PG_ADMIN_URL": args.pg_url,
        "STORE_DB_PREFIX": f"{PREFIX}_s_",
        "STOREOPS_SECRET_KEYS": "mig1:" + __import__("base64").b64encode(os.urandom(32)).decode(),
        "UPLOADS_ROOT": str(TMP / "uploads"),
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "TENANT_MAX_ENGINES": "5",
    }
)

from sqlalchemy import create_engine, inspect, text  # noqa: E402

failures = []


def check(ok: bool, msg: str) -> None:
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        failures.append(msg)


def admin():
    return create_engine(args.pg_url, isolation_level="AUTOCOMMIT")


def create_db(name: str) -> str:
    assert name.startswith(PREFIX + "_")
    eng = admin()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"))
    eng.dispose()
    return url(name)


def drop_all() -> None:
    eng = admin()
    with eng.connect() as c:
        like = PREFIX.replace("_", r"\_") + r"\_%"
        for (name,) in c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p"), {"p": like}).all():
            assert name.startswith(PREFIX + "_"), name
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    eng.dispose()


def signature(db_url: str) -> dict:
    eng = create_engine(db_url)
    try:
        insp = inspect(eng)
        out = {}
        for t in sorted(insp.get_table_names()):
            if t == "alembic_version":
                continue
            out[t] = {
                "columns": [(c["name"], str(c["type"]), c["nullable"], str(c.get("default"))) for c in insp.get_columns(t)],
                "pk": insp.get_pk_constraint(t).get("constrained_columns"),
                "indexes": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in insp.get_indexes(t)),
                "unique": sorted((u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(t)),
                "checks": sorted((c["name"], c["sqltext"]) for c in insp.get_check_constraints(t)),
            }
        with eng.connect() as c:
            out["__triggers__"] = sorted(
                tuple(r) for r in c.execute(text(
                    "SELECT event_object_table, trigger_name, event_manipulation FROM information_schema.triggers"
                )).all()
            )
        return out
    finally:
        eng.dispose()


SCRATCH_REVISION = '''"""scratch revision for the round-trip check (never committed)

Revision ID: 9999_roundtrip
Revises: {head}
"""
import sqlalchemy as sa
from alembic import op

revision = "9999_roundtrip"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "roundtrip_probe",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.String(40), nullable=False, server_default=sa.text("'x'")),
        sa.Column("flag", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_roundtrip_probe_label", "roundtrip_probe", ["label"])
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("roundtrip_note", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.drop_column("roundtrip_note")
    op.drop_index("ix_roundtrip_probe_label", table_name="roundtrip_probe")
    op.drop_table("roundtrip_probe")
'''


def main() -> None:
    from app import migrate
    from app.platform.db import reset_platform_engine, upgrade_platform_db

    drop_all()
    # 1. platform DB
    from alembic import command
    from alembic.config import Config

    purl = create_db(PLATFORM_DB)

    def pcfg():
        cfg = Config(str(BACKEND / "alembic.ini"), ini_section="platform")
        cfg.set_main_option("script_location", str(BACKEND / "alembic_platform"))
        cfg.set_main_option("sqlalchemy.url", purl.replace("%", "%%"))
        cfg.attributes["db_url"] = purl
        cfg.attributes["configure_logger"] = False
        return cfg

    rev = upgrade_platform_db(purl)
    s1 = signature(purl)
    check(rev == "p0001" and len(s1) > 5, f"platform: upgrade head -> {rev} ({len(s1) - 1} tables)")
    command.downgrade(pcfg(), "base")
    s0 = signature(purl)
    check(set(s0) == {"__triggers__"} and not s0["__triggers__"], f"platform: downgrade base leaves no tables ({sorted(s0)})")
    rev = upgrade_platform_db(purl)
    check(rev == "p0001" and signature(purl) == s1, "platform: upgrade head again -> identical schema")
    reset_platform_engine()

    # 2. store DB from the baseline
    surl = create_db(f"{PREFIX}_store_rt")
    head = migrate.store_head_revision()
    rev = migrate.upgrade_store_db(surl)
    s_head = signature(surl)
    check(rev == head, f"store: empty database -> {rev} (head {head}, {len(s_head) - 1} tables)")
    check(migrate.upgrade_store_db(surl) == head and signature(surl) == s_head, "store: second upgrade is a no-op")
    try:
        migrate.downgrade_store_db(surl, "0061")
        refused = False
    except migrate.BaselineError:
        refused = True
    except Exception as exc:  # noqa: BLE001
        refused = "baseline" in str(exc).lower()
    check(refused, "store: downgrade below the PostgreSQL baseline is refused")
    check(migrate.current_store_revision(surl) == head and signature(surl) == s_head,
          "store: the refused downgrade changed nothing")

    # 3. a revision after the baseline: upgrade / downgrade / upgrade
    scratch = TMP / "alembic"
    shutil.copytree(migrate.ALEMBIC_DIR, scratch, ignore=shutil.ignore_patterns("__pycache__"))
    (scratch / "versions" / "9999_roundtrip.py").write_text(SCRATCH_REVISION.format(head=head), encoding="utf-8")
    real_dir = migrate.ALEMBIC_DIR
    migrate.ALEMBIC_DIR = scratch
    try:
        rev = migrate.upgrade_store_db(surl)
        s_new = signature(surl)
        check(rev == "9999_roundtrip" and "roundtrip_probe" in s_new
              and any(c[0] == "roundtrip_note" for c in s_new["users"]["columns"]),
              f"store: upgrade to a post-baseline revision -> {rev}")
        rev = migrate.downgrade_store_db(surl, head)
        check(rev == head and signature(surl) == s_head, f"store: downgrade back to {head} -> schema identical to head")
        rev = migrate.upgrade_store_db(surl)
        check(rev == "9999_roundtrip" and signature(surl) == s_new, "store: upgrade again -> identical schema")
    finally:
        migrate.ALEMBIC_DIR = real_dir

    # 4. provisioning + migrate-all (C's command, A's migrator)
    from app.auth import hash_password
    from app.platform import provisioning
    from app.platform.__main__ import migrate_all
    from app.platform.sources import configure

    configure(force=True)
    check(provisioning.MIGRATOR in (migrate.upgrade_store_db, provisioning.default_migrator),
          "provisioning migrates with A's upgrade_store_db")
    for code in ("MIG_ONE", "MIG_TWO"):
        out = provisioning.create_store_manually(
            code=code, name=code.title(), pack_id="test_toast", timezone="America/New_York", pos_provider="toast",
            owner_username="owner", owner_password_hash=hash_password("roundtrip-pass-1"), actor="cli:migcheck",
            run="inline",
        )
        check(out["status"] == "active", f"provision {code}: {out['status']}")
    res = migrate_all()
    stores = res.get("stores") or {}
    check(res.get("ok") and set(stores) >= {"MIG_ONE", "MIG_TWO"}, f"migrate-all: ok={res.get('ok')} stores={sorted(stores)}")
    for code in ("MIG_ONE", "MIG_TWO"):
        db_url = url(f"{PREFIX}_s_{code.lower()}")
        check(migrate.current_store_revision(db_url) == head, f"{code} at head after migrate-all")
    try:
        from app.tenancy import engines

        engines.dispose_all_engines()
    except Exception:  # noqa: BLE001
        pass
    reset_platform_engine()


try:
    main()
finally:
    drop_all()
    shutil.rmtree(TMP, ignore_errors=True)
print(f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
