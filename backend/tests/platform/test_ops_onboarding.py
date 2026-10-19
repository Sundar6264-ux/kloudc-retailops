"""Onboarding a store that already runs on another system (app/ops):
pilot mode per store, the SQLite import, the private pack loader, the
parity check, readiness in multi mode and the whole-service backup."""

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, pool, text

from tests.platform.conftest import provision, store_login

BACKEND = pathlib.Path(__file__).resolve().parents[2]
CODE = "ALPHA"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_db(tmp_path) -> pathlib.Path:
    """A single-store SQLite database at head, as another system left it:
    its own users, sales, sign-in sessions, phones and identity stamp."""
    db_file = tmp_path / "source.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}", TENANCY_MODE="single")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
    con = sqlite3.connect(db_file)
    try:
        con.executescript(
            """
            INSERT INTO users (id, username, password_hash, is_admin, allowed_features, can_assign, hide_sales, created_at)
                VALUES (1, 'owner', 'x', 1, '[]', 1, 0, '2026-10-01 12:00:00.000000'),
                       (4, 'clerk', 'y', 0, '[]', 0, 1, '2026-10-02 01:02:03.456789');
            INSERT INTO orders (id, toast_order_guid, toast_check_guid, toast_selection_guid, business_date, item_guid,
                                item_name, quantity, unit_price, net_price, tax_amount, voided, created_at, updated_at,
                                source, is_modifier)
                VALUES (1, 'o1', 'c1', 's1', '2026-10-10', 'i1', 'Tea', 2, 1.5, 3.0, 0.21, 0,
                        '2026-10-10 14:05:00.000000', '2026-10-10 14:05:00.000000', 'toast', 0),
                       (2, 'o1', 'c1', 's2', '2026-10-10', 'i2', 'Rice', 1, 9.99, 9.99, 0.0, 0,
                        '2026-10-10 14:05:00.000000', '2026-10-10 14:05:00.000000', 'toast', 0),
                       (3, 'o2', 'c2', 's3', '2026-10-11', 'i1', 'Tea', 1, 1.5, 1.5, 0.1, 1,
                        '2026-10-11 10:00:00.000000', '2026-10-11 10:00:00.000000', 'toast', 0);
            INSERT INTO user_sessions (token, user_id, created_at, expires_at)
                VALUES ('old-session', 1, '2026-10-01 12:00:00.000000', '2030-01-01 00:00:00.000000');
            INSERT INTO device_tokens (id, user_id, token, platform, created_at, last_seen_at)
                VALUES (1, 1, 'old-phone', 'ios', '2026-10-01 12:00:00.000000', '2026-10-01 12:00:00.000000');
            INSERT INTO store_identity (id, store_code, instance_id, created_at, created_by)
                VALUES (1, 'OLD_STORE', 'abc', '2026-10-01 12:00:00.000000', 'store-init');
            """
        )
        con.commit()
    finally:
        con.close()
    return db_file


def _details(row) -> dict:
    return row.details if isinstance(row.details, dict) else json.loads(row.details or "{}")


def _store_url(code=CODE) -> str:
    from app.platform import bridge
    from app.platform.db import platform_session
    from app.platform.models import Store

    with platform_session() as db:
        store = db.query(Store).filter(Store.code == code).one()
        return bridge.store_url(store.db_name, store.db_host_ref)


def _scalar(url, sql, **params):
    eng = create_engine(url, poolclass=pool.NullPool)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql), params).scalar()
    finally:
        eng.dispose()


# --- pilot mode ------------------------------------------------------------------


def test_pilot_mode_command_is_per_store_and_blocks_sends_in_that_store(pdb, capsys):
    from app.config import get_settings
    from app.platform.__main__ import main
    from app.services import whapi_client
    from app.tenancy import store_context

    provision(CODE)
    provision("BETA", owner="bert")
    assert main(["pilot-mode", CODE, "on"]) == 0
    assert "pilot mode ON" in capsys.readouterr().out
    with store_context(CODE):
        assert get_settings().pilot_mode is True
        with pytest.raises(whapi_client.WhapiPilotBlocked):
            whapi_client.send_whatsapp_message("hi", to="1203@g.us")
    with store_context("BETA"):
        assert get_settings().pilot_mode is False, "only that store"
    assert main(["pilot-mode", CODE, "off"]) == 0
    with store_context(CODE):
        assert get_settings().pilot_mode is False


# --- import ----------------------------------------------------------------------


def test_import_copies_verifies_restamps_and_keeps_the_owner(tmp_path, pdb, client):
    from app.ops.store_import import import_sqlite
    from app.platform.models import AuditLog, Store

    provision(CODE)
    src = _source_db(tmp_path)
    before = _sha(src)
    work = tmp_path / "work"
    report = import_sqlite(CODE, src, workdir=work, log=lambda *a: None)
    assert _sha(src) == before, "the source is only read"
    assert list(work.iterdir()) == [], "the snapshot is deleted afterwards"
    assert report["differences"] == 0 and report["tables"] > 50
    assert report["previous_stamp"] == "OLD_STORE"
    assert report["cleared"] == {"user_sessions": 1, "device_tokens": 1}
    assert report["owner_added"] is True
    assert set(report["timings_s"]) >= {"snapshot", "upgrade_snapshot", "copy_and_verify", "total"}

    url = _store_url()
    instance = pdb.query(Store).filter(Store.code == CODE).one().instance_id
    assert _scalar(url, "SELECT store_code FROM store_identity") == CODE
    assert _scalar(url, "SELECT instance_id FROM store_identity") == instance
    assert _scalar(url, "SELECT count(*) FROM orders") == 3
    assert _scalar(url, "SELECT count(*) FROM users") == 3  # owner, clerk + the provisioned owner login
    assert _scalar(url, "SELECT count(*) FROM user_sessions") == 0
    audit = pdb.query(AuditLog).filter(AuditLog.action == "store.imported").one()
    assert audit.store_code == CODE and _details(audit)["snapshot_sha256"]
    # The provisioned owner can still sign in, to the imported data.
    r = store_login(client, CODE, "ann", "alpha-owner-pass")
    assert r.status_code == 200, r.text


def test_import_refuses_a_store_with_data_or_open_connections(tmp_path, pdb):
    import psycopg

    from app.ops.store_import import StoreImportError, import_sqlite

    provision(CODE)
    src = _source_db(tmp_path)
    import_sqlite(CODE, src, log=lambda *a: None)
    with pytest.raises(StoreImportError, match="already has data"):
        import_sqlite(CODE, src, log=lambda *a: None)
    scripts = BACKEND / "scripts" / "pg"
    sys.path.insert(0, str(scripts))
    import sqlite_pg

    with psycopg.connect(sqlite_pg.libpq_conninfo(_store_url())):
        with pytest.raises(StoreImportError, match="other connection"):
            import_sqlite(CODE, src, replace=True, log=lambda *a: None)
    report = import_sqlite(CODE, src, replace=True, log=lambda *a: None)
    assert report["replaced"] is True and report["differences"] == 0
    with pytest.raises(StoreImportError, match="no store"):
        import_sqlite("NOPE", src, log=lambda *a: None)


def test_import_command_needs_confirm_for_replace(tmp_path, pdb, capsys):
    from app.platform.__main__ import main

    provision(CODE)
    src = _source_db(tmp_path)
    assert main(["import-sqlite", CODE, "--sqlite", str(src), "--replace"]) == 1
    assert "--confirm ALPHA" in capsys.readouterr().err
    assert main(["import-sqlite", CODE, "--sqlite", str(src)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["differences"] == 0 and "table_rows" not in out


# --- private pack ----------------------------------------------------------------


def test_private_pack_must_live_outside_git_and_loads_once(tmp_path, pdb):
    from app.ops.private_pack import PrivatePackError, load_private_pack
    from app.platform.models import AuditLog

    provision(CODE)
    in_repo = BACKEND / "packs" / "test_clover.json"
    with pytest.raises(PrivatePackError, match="inside the git checkout"):
        load_private_pack(CODE, in_repo)
    private = tmp_path / "private" / "store.pack.json"
    private.parent.mkdir()
    shutil.copy(in_repo, private)
    out = load_private_pack(CODE, private)
    assert out["written"] is True and out["pack_id"] and out["restart_needed"] is True
    again = load_private_pack(CODE, private)
    assert again["written"] is False
    url = _store_url()
    assert _scalar(url, "SELECT count(*) FROM setting_changes WHERE key = 'store_pack'") >= 2  # init + private
    rows = pdb.query(AuditLog).filter(AuditLog.action == "store.pack_loaded").all()
    assert len(rows) == 2 and _details(rows[0])["file"] == "store.pack.json"
    assert "menu" not in json.dumps(_details(rows[0])), "never the pack's content"
    bad = tmp_path / "bad.json"
    bad.write_text("{\"pack_version\": 99}")
    with pytest.raises(PrivatePackError, match="not valid"):
        load_private_pack(CODE, bad)


# --- parity ----------------------------------------------------------------------


def test_parity_is_clean_after_import_and_finds_a_changed_day(tmp_path, pdb, capsys):
    from app.ops.parity import run_parity
    from app.ops.store_import import import_sqlite

    provision(CODE)
    src = _source_db(tmp_path)
    import_sqlite(CODE, src, log=lambda *a: None)
    logs = tmp_path / "logs"
    today = dt.date(2026, 10, 12)
    report = run_parity(CODE, src, days=5, today=today, log_dir=str(logs))
    assert report["ok"] is True and report["days_compared"] == 5
    assert report["totals"] == {"store_lines": 2, "reference_lines": 2}
    con = sqlite3.connect(src)
    con.execute("UPDATE orders SET net_price = 4.0 WHERE id = 1")
    con.commit()
    con.close()
    report = run_parity(CODE, src, days=5, today=today, log_dir=str(logs))
    assert report["ok"] is False
    assert report["differences"] == [{"date": "2026-10-10", "metric": "net_sales", "store": 12.99, "reference": 13.99}]
    lines = (logs / f"parity-{CODE}.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["ok"] is False
    from app.platform.models import AuditLog

    audits = pdb.query(AuditLog).filter(AuditLog.action == "parity.run").all()
    assert len(audits) == 2 and "12.99" not in json.dumps(_details(audits[1])), "no amounts in the audit log"


# --- readiness (multi mode) ---------------------------------------------------------


def test_ready_in_multi_mode_reports_stores_behind(pdb, client):
    from app.platform.models import Store

    provision(CODE)
    r = client.get("/api/ready")
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert checks["platform_database"] == "ok" and checks["platform_migrations"] == "ok"
    assert checks["store_migrations"] == "ok" and checks["active_stores"] == "1"
    store = pdb.query(Store).filter(Store.code == CODE).one()
    store.schema_revision = "0061"
    pdb.commit()
    r = client.get("/api/ready")
    assert r.status_code == 503, (r.text, pdb.query(Store.schema_revision, Store.status, Store.hosting).all())
    assert r.json()["checks"]["store_migrations"].startswith("fail: 1 store(s) behind")
    assert CODE not in r.text, "no store codes on a logged-out route"


def test_production_check_finds_the_master(pdb, master, platform_env, monkeypatch):
    from app.ops import prod_checks

    monkeypatch.setattr(platform_env, "app_env", "production")
    monkeypatch.setattr(platform_env, "signup_enabled", True)
    monkeypatch.setattr(platform_env, "platform_require_totp", None)  # the production default: on
    assert prod_checks._masters_exist() is True
    errors = [f.key for f in prod_checks.check_config(platform_env) if f.level == "error"]
    assert errors == [], errors


# --- whole-service backup ------------------------------------------------------------


def _pg_bin():
    for candidate in (os.environ.get("PG_BIN"), "/opt/homebrew/opt/postgresql@16/bin", "/usr/lib/postgresql/16/bin"):
        if candidate and (pathlib.Path(candidate) / "pg_dump").exists():
            return candidate
    found = shutil.which("pg_dump")
    return str(pathlib.Path(found).parent) if found else None


def test_backup_all_dumps_platform_and_every_store_then_drills(tmp_path, pdb, platform_env):
    pg_bin = _pg_bin()
    if pg_bin is None:
        pytest.skip("pg_dump not installed")
    sys.path.insert(0, str(BACKEND / "scripts" / "backup"))
    import backup_all

    provision(CODE)
    provision("BETA", owner="bert")
    dest = tmp_path / "backups"
    out = backup_all.backup_all(platform_env.platform_database_url, str(dest), pg_bin=pg_bin,
                                now=dt.datetime(2026, 10, 19, 3, 15))
    assert out["ok"], out
    assert out["databases"] == 3 and out["failed"] == []
    assert (dest / "_platform" / "PLATFORM-20261019-031500.dump").is_file()
    assert (dest / CODE / f"{CODE}-20261019-031500.dump").is_file()
    assert (dest / "BETA" / "BETA-20261019-031500.dump").is_file()
    assert json.loads((dest / "last_backup_all.json").read_text())["ok"] is True
    drill = backup_all.drill_all(platform_env.platform_database_url, str(dest), pg_bin=pg_bin)
    assert drill["ok"], drill
    assert drill["databases"] == 3
    one = backup_all.drill_all(platform_env.platform_database_url, str(dest), pg_bin=pg_bin, store="beta")
    assert one["databases"] == 1 and one["ok"]
