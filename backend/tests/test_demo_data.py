"""DEMO store data (plan 3.1a, M2 batch MT13): the made-up data generator,
the pack, the reset (build / check / swap) and the runner's safety rules.

The generator runs in a subprocess (it needs its own database), with a small
data set (``--days 10 --items 60``) to keep this file quick.
"""

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
GENERATOR = BACKEND / "scripts" / "demo" / "generate_demo_data.py"
SMOKE = BACKEND / "scripts" / "demo" / "smoke_demo.py"
PASSWORD = "test-review-pass-not-real"
# Origin-store names (hashed, tests/origin_names.py) and one real supplier the
# old seeds used.
REAL_SUPPLIER = re.compile(r"\bshaz international\b", re.I)


def _forbidden(value: str) -> bool:
    return has_origin_name(value) or bool(REAL_SUPPLIER.search(value))
SMALL = ["--days", "10", "--items", "60", "--end-date", "2026-10-18", "--seed", "7"]

sys.path.insert(0, str(BACKEND))
from scripts.demo import demo_catalog, reset_demo, run_demo  # noqa: E402
from tests.origin_names import has_origin_name  # noqa: E402
from scripts.demo.fake_pdf import BANNER  # noqa: E402
from scripts.demo.generate_demo_data import SAFE_OVERRIDES, ean13  # noqa: E402


def _generate(out: pathlib.Path, *extra: str, password: str = PASSWORD) -> subprocess.CompletedProcess:
    env = dict(os.environ, DEMO_REVIEW_PASSWORD=password)
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(out), *SMALL, *extra],
        cwd=str(BACKEND), env=env, capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo") / "data"
    proc = _generate(out)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return out


def _conn(folder: pathlib.Path):
    return sqlite3.connect(f"file:{folder / 'retailops.db'}?mode=ro", uri=True)


def test_counts_and_marker(dataset):
    meta = json.loads((dataset / "demo_dataset.json").read_text())
    assert meta["store_code"] == "DEMO"
    assert (meta["first_date"], meta["last_date"]) == ("2026-10-08", "2026-10-17")
    counts = meta["counts"]
    for key in ("orders", "order_lines", "catalog_uploads", "invoices", "checklist_submissions", "tasks",
                "messages", "placed_orders", "purchase_log", "cart_items"):
        assert counts[key] > 0, key
    assert counts["catalog_items"] == 60 and counts["vendors"] == 8
    assert reset_demo.is_demo_dataset(dataset)
    c = _conn(dataset)
    assert c.execute("SELECT count(DISTINCT item_id) FROM inventory_snapshots").fetchone()[0] == 60
    assert c.execute("SELECT count(*) FROM item_vendors").fetchone()[0] >= 55
    assert c.execute("SELECT DISTINCT source FROM orders").fetchall() == [("csv",)]
    assert c.execute("SELECT min(business_date), max(business_date) FROM orders").fetchone() == (
        "2026-10-08", "2026-10-17",
    )
    # Fix Issues has something to show: a negative count on the last upload.
    assert c.execute(
        "SELECT count(*) FROM inventory_snapshots WHERE snapshot_date = '2026-10-17' AND inventory_quantity < 0"
    ).fetchone()[0] >= 1


def test_nothing_from_a_real_store(dataset):
    """Every text value of every table: none of the names the fresh-store
    test forbids (contract 3.6), every e-mail on the reserved .example domain,
    every vendor visibly made up, people are roles."""
    c = _conn(dataset)
    hits = []
    for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for row in c.execute(f'SELECT * FROM "{table}"'):
            for value in row:
                if isinstance(value, str):
                    if _forbidden(value):
                        hits.append((table, value[:60]))
                    for email in re.findall(r"[\w.+-]+@[\w.-]+", value):
                        if not email.endswith(".example"):
                            hits.append((table, email))
    assert hits == []
    vendors = {v for (v,) in c.execute("SELECT vendor FROM vendor_schedules")}
    assert vendors == set(demo_catalog.VENDORS)
    assert all(re.search(r"\b(Demo|Sample|Example|Placeholder)\b", v) for v in vendors)
    users = {u for (u,) in c.execute("SELECT username FROM users")}
    assert users == {u for u, *_ in demo_catalog.STAFF} | {"appreview"}
    # The rows old migrations seed for one particular store are gone.
    assert c.execute("SELECT count(*) FROM category_overrides").fetchone()[0] == 0
    assert c.execute("SELECT key, label FROM whatsapp_groups").fetchall() == [("store", "Store staff group")]
    types = {t for (t,) in c.execute("SELECT checklist_type FROM checklist_definitions")}
    assert types == {"demo_opening", "demo_closing"}


def test_barcodes_are_in_store_numbers(dataset):
    c = _conn(dataset)
    codes = [b for (b,) in c.execute("SELECT DISTINCT barcode FROM inventory_snapshots WHERE barcode IS NOT NULL")]
    assert codes
    for code in codes:
        assert re.fullmatch(r"2\d{12}", code) and ean13(code[:12]) == code


def test_invoice_files_are_marked_sample_pdfs(dataset):
    c = _conn(dataset)
    rows = c.execute("SELECT stored_filename, file_hash, whatsapp_error, toast_email_error FROM invoice_submissions").fetchall()
    assert rows
    for name, sha, wa_err, email_err in rows:
        data = (dataset / "uploads" / "invoices" / name).read_bytes()
        assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
        assert BANNER.encode() in data
        assert hashlib.sha256(data).hexdigest() == sha
        assert wa_err == email_err == "Demo account - not sent"


def _digest(folder: pathlib.Path) -> str:
    c = _conn(folder)
    h = hashlib.sha256()
    for table in ("orders", "inventory_snapshots", "item_vendors", "invoice_submissions", "checklist_submissions",
                  "tasks", "task_events", "messages", "placed_orders", "purchase_log", "po_cart_items",
                  "vendor_schedules", "vendor_contacts", "announcements"):
        for row in c.execute(f'SELECT * FROM "{table}" ORDER BY 1'):
            h.update(repr(row).encode())
    for path in sorted((folder / "uploads").rglob("*")):
        # .store_identity (MT8) carries this data set's random instance id,
        # which differs by design on every build.
        if path.is_file() and path.name != ".store_identity":
            h.update(path.name.encode() + path.read_bytes())
    return h.hexdigest()


def test_same_seed_same_data(dataset, tmp_path):
    again = tmp_path / "again"
    proc = _generate(again)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert _digest(again) == _digest(dataset)


def test_refusals(tmp_path):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("not ours")
    proc = _generate(busy)
    assert proc.returncode == 1 and "not empty" in proc.stderr
    assert (busy / "keep.txt").read_text() == "not ours"
    proc = _generate(tmp_path / "short", password="short")
    assert proc.returncode == 1 and "DEMO_REVIEW_PASSWORD" in proc.stderr


def test_the_app_works_on_the_data(dataset, tmp_path):
    """smoke_demo.py: sign in as the review login, every parameterless GET
    answers without a 5xx, the main pages have data, writes are refused."""
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(dataset, copy)
    env = dict(os.environ, DEMO_REVIEW_PASSWORD=PASSWORD)
    proc = subprocess.run([sys.executable, str(SMOKE), "--data", str(copy)], cwd=str(BACKEND), env=env,
                          capture_output=True, text=True)
    report = json.loads(proc.stdout)
    assert report["errors"] == [] and proc.returncode == 0
    assert report["checked"] > 50
    assert report["me"]["demo"] is True and report["me"]["capabilities"] == ["csv_catalog_import"]


# --- the pack ------------------------------------------------------------------------------


def test_demo_pack():
    from app.services import checklist_engine as engine

    pack = json.loads((BACKEND / "packs" / "demo.json").read_text())
    assert (pack["pack_version"], pack["pack_id"]) == (1, "demo")
    assert not _forbidden(json.dumps(pack))
    roles = {r["key"] for r in pack["whatsapp_roles"]}
    for c in pack["checklists"]:
        assert engine.validate_schema(engine.normalize_schema(c["schema"])) == []
        assert engine.validate_settings(c["settings"], sorted(roles)) == []
        assert c["settings"]["whatsapp_group"] in roles | {None}
    leads = {l["key"] for l in pack["issue_leads"]["leads"]} | {pack["issue_leads"]["fallback"]["key"]}
    assert all(p["lead"] in leads for p in pack["issue_leads"]["section_proposals"].values())
    try:
        from app.store.pack import load_pack_file  # MT8
    except ImportError:
        return
    load_pack_file(str(BACKEND / "packs" / "demo.json"))


# --- reset and runner -------------------------------------------------------------------


def test_reset_only_touches_demo_folders(tmp_path):
    foreign = tmp_path / "store-data"
    foreign.mkdir()
    (foreign / "retailops.db").write_bytes(b"")
    with pytest.raises(reset_demo.ResetError, match="not a DEMO data folder"):
        reset_demo.reset(foreign)
    assert (foreign / "retailops.db").exists()

    marked = tmp_path / "marked"
    reset_demo.prepare_data_dir(marked)
    live = marked / "live"
    live.mkdir()
    sqlite3.connect(live / "retailops.db").execute("CREATE TABLE orders (id INTEGER)").connection.commit()
    (live / "demo_dataset.json").write_text('{"store_code": "DEMO"}')  # no DB marker row
    with pytest.raises(reset_demo.ResetError, match="not a generated DEMO data set"):
        reset_demo.check_replaceable(live)
    with pytest.raises(reset_demo.ResetError):
        reset_demo.reset(marked)
    assert (live / "retailops.db").exists()


def test_build_and_swap(dataset, tmp_path, monkeypatch):
    """swap(): next -> live, the old live removed; a half-built next/ is refused."""
    import shutil

    data = tmp_path / "demo-data"
    reset_demo.prepare_data_dir(data)
    with pytest.raises(reset_demo.ResetError, match="not a finished"):
        reset_demo.swap(data)
    shutil.copytree(dataset, data / "next")
    reset_demo.swap(data)
    assert reset_demo.is_demo_dataset(data / "live") and not (data / "next").exists()
    shutil.copytree(dataset, data / "next")
    (data / "next" / "new.txt").write_text("second")
    reset_demo.swap(data)
    assert (data / "live" / "new.txt").exists() and not (data / "old").exists()


def test_runner_forces_the_demo_settings(tmp_path):
    base = {
        "WHAPI_API_TOKEN": "x-token",
        "ANTHROPIC_API_KEY": "k",
        "GMAIL_APP_PASSWORD": "p",
        "TOAST_CLIENT_SECRET": "s",
        "WHAPI_BASE_URL": "https://gate.example",
        "DEMO_USERNAMES": "someone",
    }
    env = run_demo.server_env(tmp_path, "America/Chicago", base)
    for key, value in SAFE_OVERRIDES.items():
        assert env[key] == value
    assert env["WHAPI_API_TOKEN"] == env["ANTHROPIC_API_KEY"] == env["GMAIL_APP_PASSWORD"] == ""
    assert env["WHAPI_BASE_URL"] == env["TOAST_API_BASE"] == "http://127.0.0.1:9"
    assert (env["STORE_CODE"], env["POS_PROVIDER"], env["DEMO_MODE"]) == ("DEMO", "csv", "true")
    assert env["DATABASE_URL"] == f"sqlite:///{tmp_path / 'live' / 'retailops.db'}"
    assert env["DEMO_USERNAMES"] == "appreview,someone"
    assert env["STORE_TIMEZONE"] == "America/Chicago"
    with pytest.raises(run_demo.RunnerError, match="only runs the DEMO store"):
        run_demo.server_env(tmp_path, "America/Chicago", {"STORE_CODE": "XYZ_MARKET"})
    with pytest.raises(run_demo.RunnerError, match="DEMO_MODE is off"):
        run_demo.server_env(tmp_path, "America/Chicago", {"DEMO_MODE": "false"})


def test_next_reset_time_in_store_time():
    ny = zoneinfo.ZoneInfo("America/New_York")
    at = dt.time(3, 30)
    utc = dt.timezone.utc
    # 01:00 local -> today 03:30; 04:00 local -> tomorrow 03:30.
    now = dt.datetime(2026, 10, 18, 1, 0, tzinfo=ny).astimezone(utc)
    assert run_demo.next_reset(now, at, ny) == dt.datetime(2026, 10, 18, 3, 30, tzinfo=ny).astimezone(utc)
    now = dt.datetime(2026, 10, 18, 4, 0, tzinfo=ny).astimezone(utc)
    assert run_demo.next_reset(now, at, ny) == dt.datetime(2026, 10, 19, 3, 30, tzinfo=ny).astimezone(utc)
    # Across the November DST change the local time stays 03:30.
    now = dt.datetime(2026, 10, 31, 12, 0, tzinfo=ny).astimezone(utc)
    nxt = run_demo.next_reset(dt.datetime(2026, 11, 1, 4, 0, tzinfo=ny).astimezone(utc), at, ny)
    assert nxt.astimezone(ny).time() == at and nxt.astimezone(ny).date() == dt.date(2026, 11, 2)
    assert run_demo.next_reset(now, at, ny).astimezone(ny).date() == dt.date(2026, 11, 1)


def test_reset_end_to_end(tmp_path, monkeypatch):
    """reset(): a new folder (holding only launchd's logs/) is marked, built,
    smoke-checked and swapped in; the review password is created once
    (mode 600) and kept for the next reset."""
    monkeypatch.delenv("DEMO_REVIEW_PASSWORD", raising=False)
    monkeypatch.setattr(reset_demo, "setting", lambda name: os.environ.get(name, ""))  # ignore any .env
    data = tmp_path / "demo-data"
    (data / "logs").mkdir(parents=True)
    info = reset_demo.reset(data, seed=3, days=8, items=40, do_swap=True, end_date="2026-10-18")
    assert info["counts"]["catalog_items"] == 40
    assert (data / reset_demo.MARKER).is_file() and reset_demo.is_demo_dataset(data / "live")
    pw = data / "review_password"
    assert oct(pw.stat().st_mode & 0o777) == "0o600"
    first = pw.read_text()
    reset_demo.reset(data, seed=3, days=8, items=40, do_swap=True, end_date="2026-10-19")
    assert pw.read_text() == first
    meta = json.loads((data / "live" / "demo_dataset.json").read_text())
    assert meta["last_date"] == "2026-10-18"
    assert sorted(p.name for p in data.iterdir()) == sorted([reset_demo.MARKER, "live", "logs", "review_password"])
