"""Fresh-store test (StoreOps M2, MT8; plan 9.1, docs/M2_CONTRACT.md 3.5-3.6).

A brand-new SQLite file, every migration (`alembic upgrade head`), then
`python -m app.store init` for the fake store STOREOPS_TEST_TOAST with
packs/test_toast.json. Every text column of every table is then searched for
the legacy store's names (and the private staff-name list when this machine
has it): zero hits. Also: the stamp and the upload marker exist, a second
init is a no-op, and init with another code is refused.

Runs the real commands in child processes (their own settings, no .env).
"""

import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import pytest

from tests.store_fixtures import BACKEND, PRIVATE_DIR

NAMES = ["spice town", "spicetown", "street kitchen", "vernon", "shaz international"]


def _forbidden() -> list:
    words = list(NAMES)
    extra = PRIVATE_DIR / "forbidden_tokens.txt"
    if extra.is_file():
        words += [w.strip() for w in extra.read_text(encoding="utf-8").splitlines() if w.strip()]
    return words


def _env(tmp: pathlib.Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp),
        "DATABASE_URL": f"sqlite:///{tmp / 'store.db'}",
        "UPLOADS_ROOT": str(tmp / "uploads"),
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
        "PYTHONPATH": str(BACKEND),
    }
    return env


def _run(tmp: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(
        [sys.executable, *args], cwd=str(tmp), env=_env(tmp), capture_output=True, text=True, timeout=600
    )
    if check and done.returncode != 0:
        raise AssertionError(f"{args} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}")
    return done


MIGRATE = (
    "import sys; from alembic import command; from alembic.config import Config; "
    "cfg = Config(sys.argv[1] + '/alembic.ini'); cfg.set_main_option('script_location', sys.argv[1] + '/alembic'); "
    "command.upgrade(cfg, 'head')"
)


def _migrate(tmp: pathlib.Path) -> None:
    """`alembic upgrade head` on the new database (this checkout's migrations)."""
    _run(tmp, "-c", MIGRATE, str(BACKEND))


@pytest.fixture(scope="module")
def fresh_store(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fresh-store")
    _migrate(tmp)
    # Before init, the legacy seeded rows are there (that's what init removes).
    before = _hits(tmp / "store.db")
    init = _run(tmp, "-m", "app.store", "init", "--code", "STOREOPS_TEST_TOAST", "--pack", str(BACKEND / "packs" / "test_toast.json"))
    return tmp, before, json.loads(init.stdout)


def _hits(db_path: pathlib.Path) -> list:
    words = _forbidden()
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
    conn = sqlite3.connect(str(db_path))
    hits = []
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table in ("alembic_version", "sqlite_sequence"):
                continue
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            for col in cols:
                for (value,) in conn.execute(f'SELECT "{col}" FROM "{table}" WHERE typeof("{col}") = \'text\''):
                    m = pattern.search(value)
                    if m:
                        hits.append(f"{table}.{col}: {m.group(0)!r}")
    finally:
        conn.close()
    return hits


def test_init_removes_every_legacy_row(fresh_store):
    tmp, before, summary = fresh_store
    assert before, "the migrations should have seeded the legacy rows the test looks for"
    assert _hits(tmp / "store.db") == []
    assert summary["store_code"] == "STOREOPS_TEST_TOAST" and summary["pack_id"] == "storeops_test_toast"
    assert summary["removed"] == {
        "category_overrides": summary["removed"]["category_overrides"],
        "checklist_definition_versions": summary["removed"]["checklist_definition_versions"],
        "checklist_definitions": 3,
        "whatsapp_groups": 2,
    }
    assert summary["removed"]["category_overrides"] >= 2
    assert summary["inserted"] == {"checklist_definitions": 1, "whatsapp_groups": 1, "category_overrides": 1}


def test_pack_rows_stamp_and_marker(fresh_store):
    tmp, _, summary = fresh_store
    conn = sqlite3.connect(str(tmp / "store.db"))
    try:
        assert conn.execute("SELECT store_code, instance_id, created_by FROM store_identity").fetchall() == [
            ("STOREOPS_TEST_TOAST", summary["instance_id"], "store-init")
        ]
        assert conn.execute("SELECT checklist_type, created_by FROM checklist_definitions").fetchall() == [
            ("daily_closing", "store-init (storeops_test_toast)")
        ]
        assert conn.execute("SELECT key, label, builtin FROM whatsapp_groups").fetchall() == [("store", "Store staff group", 1)]
        rows = conn.execute("SELECT value, changed_by_name FROM setting_changes WHERE key = 'store_pack'").fetchall()
        assert len(rows) == 1 and json.loads(rows[0][0])["pack_id"] == "storeops_test_toast" and rows[0][1] == "store-init"
        teams = [r[0] for r in conn.execute("SELECT name FROM teams ORDER BY id")]
        assert teams == ["Everyone", "Grocery", "Kitchen", "Managers"]  # generic, kept
    finally:
        conn.close()
    marker = json.loads((tmp / "uploads" / ".store_identity").read_text())
    assert marker == {"store_code": "STOREOPS_TEST_TOAST", "instance_id": summary["instance_id"]}


def test_second_init_is_a_no_op(fresh_store):
    tmp, _, summary = fresh_store
    again = json.loads(
        _run(tmp, "-m", "app.store", "init", "--code", "STOREOPS_TEST_TOAST", "--pack", str(BACKEND / "packs" / "test_toast.json")).stdout
    )
    assert again["instance_id"] == summary["instance_id"]
    assert again["pack_written"] is False
    assert all(v == 0 for v in again["removed"].values()) and all(v == 0 for v in again["inserted"].values())


def test_init_with_another_code_is_refused(fresh_store):
    tmp, _, _ = fresh_store
    done = _run(tmp, "-m", "app.store", "init", "--code", "OTHER_STORE", "--pack", str(BACKEND / "packs" / "generic.json"), check=False)
    assert done.returncode == 1 and "belongs to STOREOPS_TEST_TOAST" in done.stderr


def test_identity_show(fresh_store):
    tmp, _, summary = fresh_store
    shown = json.loads(_run(tmp, "-m", "app.store", "identity", "show").stdout)
    assert shown["database"]["store_code"] == "STOREOPS_TEST_TOAST"
    assert shown["uploads_marker"]["instance_id"] == summary["instance_id"]


def test_init_refuses_an_unmigrated_database(tmp_path):
    done = _run(tmp_path, "-m", "app.store", "init", "--code", "ABC", "--pack", str(BACKEND / "packs" / "generic.json"), check=False)
    assert done.returncode == 1 and "alembic upgrade head" in done.stderr


def test_init_refuses_store_data_and_adopt_keeps_it(db, tmp_path, monkeypatch):
    """In-process: a database with orders is an existing store."""
    import datetime as dt

    from app.config import get_settings
    from app.models import Order
    from app.store.__main__ import InitError, store_init

    monkeypatch.setattr("app.store.__main__._db_revision", lambda db: "0062")
    monkeypatch.setattr("app.store.__main__._alembic_heads", lambda: ["0062"])
    settings = get_settings().model_copy(update={"uploads_root": str(tmp_path / "uploads")})
    db.add(
        Order(
            toast_order_guid="o1", toast_check_guid="c1", toast_selection_guid="s1",
            business_date=dt.date(2026, 1, 2), item_guid="i1", item_name="x", quantity=1,
        )
    )
    db.commit()
    pack = str(BACKEND / "packs" / "generic.json")
    with pytest.raises(InitError, match="already has store data"):
        store_init("ABC", pack, db=db, settings=settings)
    out = store_init("ABC", pack, adopt=True, db=db, settings=settings)
    assert out["removed"] == {} and out["store_code"] == "ABC"
    assert db.query(Order).count() == 1
