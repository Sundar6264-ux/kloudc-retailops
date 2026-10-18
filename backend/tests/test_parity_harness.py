"""The parity harness (scripts/parity, multi-store plan MT1): the diff tool,
the frozen clock, the fingerprint, and one end-to-end recording made twice
on a small database (must come out identical, without touching the source
file or the network)."""

import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest

PARITY = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "parity"
sys.path.insert(0, str(PARITY))

import parity_clock  # noqa: E402
import parity_diff  # noqa: E402
from parity_fingerprint import fingerprint  # noqa: E402


# --- helpers ---------------------------------------------------------------------------------


def _recording(root: pathlib.Path, endpoints=None, fp=None, alerts=None, jobs=None, meta=None) -> pathlib.Path:
    root.mkdir(parents=True)
    (root / "endpoints").mkdir()
    base_meta = {"now": "2026-10-15T12:00:00-04:00", "user": "boss", "top_vendors": ["A"], "python": "3.12.1"}
    base_meta.update(meta or {})
    (root / "meta.json").write_text(json.dumps(base_meta))
    for name, entry in (endpoints or {}).items():
        full = {"name": name, "path": f"/api/{name}", "params": {}, "status": 200}
        full.update(entry)
        (root / "endpoints" / f"{name}.json").write_text(json.dumps(full))
    (root / "fingerprint.json").write_text(json.dumps(fp or {"tables": {}}))
    (root / "alerts.json").write_text(json.dumps(alerts or {}))
    (root / "scheduler_jobs.json").write_text(json.dumps(jobs or {"main": []}))
    return root


def _diff(tmp_path, a_kwargs, b_kwargs, strict=False):
    a = _recording(tmp_path / "a", **a_kwargs)
    b = _recording(tmp_path / "b", **b_kwargs)
    return parity_diff.compare(a, b, strict=strict)


SALES = {"json": {"total_revenue": 5194.75, "items": [{"name": "Goat Mix", "quantity": 16.0}], "open": True}}


# --- diff tool -------------------------------------------------------------------------------


def test_identical_recordings_have_no_differences(tmp_path):
    rep = _diff(tmp_path, {"endpoints": {"sales": SALES}}, {"endpoints": {"sales": SALES}})
    assert rep.differences == [] and rep.additions == []


def test_a_changed_number_is_a_difference_with_its_path(tmp_path):
    changed = json.loads(json.dumps(SALES))
    changed["json"]["items"][0]["quantity"] = 17.0
    rep = _diff(tmp_path, {"endpoints": {"sales": SALES}}, {"endpoints": {"sales": changed}})
    assert [(d["path"], d["a"], d["b"]) for d in rep.differences] == [("json.items[0].quantity", 16.0, 17.0)]


def test_removed_key_list_length_and_status_are_differences(tmp_path):
    changed = {"status": 500, "json": {"items": [], "open": True}}
    rep = _diff(tmp_path, {"endpoints": {"sales": SALES}}, {"endpoints": {"sales": changed}})
    kinds = {(d["path"], d["what"]) for d in rep.differences}
    assert ("status", "changed") in kinds
    assert ("json.total_revenue", "missing in new") in kinds
    assert ("json.items.length", "list length") in kinds


def test_bool_versus_number_and_int_versus_float_are_type_changes(tmp_path):
    # 3 -> 3.0 breaks the app: its Dart models cast `json['id'] as int`.
    a = {"json": {"flag": True, "qty": 3, "price": 2.0, "same": 1.5}}
    b = {"json": {"flag": 1, "qty": 3.0, "price": 2, "same": 1.5}}
    rep = _diff(tmp_path, {"endpoints": {"x": a}}, {"endpoints": {"x": b}})
    assert [(d["path"], d["what"]) for d in rep.differences] == [
        ("json.flag", "type changed"),
        ("json.qty", "type changed"),
        ("json.price", "type changed"),
    ]


V2 = {"format_version": 2}


def test_key_order_is_checked_between_format_2_recordings(tmp_path):
    # The website lists Fix Issues by_reason in key order: a reorder shows.
    a = {"json": {"by_reason": {"No vendor": 3, "No price": 1}, "total": 4}}
    b = {"json": {"by_reason": {"No price": 1, "No vendor": 3}, "total": 4}}
    rep = _diff(tmp_path, {"endpoints": {"x": a}, "meta": V2}, {"endpoints": {"x": b}, "meta": V2})
    assert [(d["path"], d["what"]) for d in rep.differences] == [("json.by_reason", "key order changed")]
    # A new key in the middle is an addition, not a reorder.
    c = {"json": {"by_reason": {"No vendor": 3, "Store id": 0, "No price": 1}, "total": 4}}
    rep2 = _diff(tmp_path / "2", {"endpoints": {"x": a}, "meta": V2}, {"endpoints": {"x": c}, "meta": V2})
    assert rep2.differences == [] and [x["path"] for x in rep2.additions] == ["json.by_reason.Store id"]
    # A format 1 baseline (keys written sorted) can't be checked: noted.
    rep3 = _diff(tmp_path / "3", {"endpoints": {"x": a}}, {"endpoints": {"x": b}, "meta": V2})
    assert rep3.differences == [] and rep3.notes


def test_a_write_by_a_read_endpoint_shows_in_the_after_fingerprint(tmp_path):
    a = _recording(tmp_path / "a")
    b = _recording(tmp_path / "b")
    (a / "fingerprint_after.json").write_text(json.dumps(FP))
    wrote = {"tables": {"orders": {"rows": 2, "columns": {"id": "aa", "quantity": "XX"}}}}
    (b / "fingerprint_after.json").write_text(json.dumps(wrote))
    rep = parity_diff.compare(a, b)
    assert [(d["file"], d["path"]) for d in rep.differences] == [("fingerprint_after.json", "tables.orders.quantity")]


def test_content_type_and_download_name_changes_are_differences(tmp_path):
    a = {"text": "a\n", "content_type": "text/csv; charset=utf-8", "content_disposition": "attachment; filename=x.csv"}
    b = {"text": "a\n", "content_type": "text/plain; charset=utf-8", "content_disposition": "attachment; filename=y.csv"}
    rep = _diff(tmp_path, {"endpoints": {"csv": a}}, {"endpoints": {"csv": b}})
    assert {d["path"] for d in rep.differences} == {"content_type", "content_disposition"}


def test_a_new_json_key_is_an_allowed_addition_unless_strict(tmp_path):
    added = json.loads(json.dumps(SALES))
    added["json"]["ap_email_sent"] = True
    rep = _diff(tmp_path, {"endpoints": {"sales": SALES}}, {"endpoints": {"sales": added}})
    assert rep.differences == []
    assert [a["path"] for a in rep.additions] == ["json.ap_email_sent"]
    strict = _diff(tmp_path / "s", {"endpoints": {"sales": SALES}}, {"endpoints": {"sales": added}}, strict=True)
    assert [d["path"] for d in strict.differences] == ["json.ap_email_sent"]


def test_an_endpoint_missing_from_the_new_recording_is_a_difference(tmp_path):
    rep = _diff(tmp_path, {"endpoints": {"sales": SALES, "extra": SALES}}, {"endpoints": {"sales": SALES}})
    assert [d["what"] for d in rep.differences] == ["endpoint missing in new"]
    rep2 = _diff(tmp_path / "2", {"endpoints": {"sales": SALES}}, {"endpoints": {"sales": SALES, "new": SALES}})
    assert rep2.differences == [] and rep2.not_compared == ["endpoints/new.json"]


def test_text_answers_are_compared_line_by_line(tmp_path):
    a = {"text": "Item,Qty\nGoat Mix,16\nRice,2\n"}
    b = {"text": "Item,Qty\nGoat Mix,17\nRice,2\n"}
    rep = _diff(tmp_path, {"endpoints": {"csv": a}}, {"endpoints": {"csv": b}})
    assert [(d["path"], d["a"], d["b"]) for d in rep.differences] == [
        ("text_lines[1]", "Goat Mix,16", "Goat Mix,17")
    ]


FP = {"tables": {"orders": {"rows": 2, "columns": {"id": "aa", "quantity": "bb"}}}}


def test_fingerprint_new_column_or_table_is_an_addition(tmp_path):
    new = {
        "tables": {
            "orders": {"rows": 2, "columns": {"id": "aa", "quantity": "bb", "source": "cc"}},
            "sync_cursors": {"rows": 0, "columns": {"id": "dd"}},
        }
    }
    rep = _diff(tmp_path, {"fp": FP}, {"fp": new})
    assert rep.differences == []
    assert sorted(a["path"] for a in rep.additions) == ["tables.orders.source", "tables.sync_cursors"]


def test_fingerprint_changed_checksum_rows_or_dropped_column_is_a_difference(tmp_path):
    new = {"tables": {"orders": {"rows": 3, "columns": {"id": "aa", "quantity": "XX"}}}}
    rep = _diff(tmp_path, {"fp": FP}, {"fp": new})
    assert {d["what"] for d in rep.differences} == {"row count", "column checksum"}
    dropped = {"tables": {"orders": {"rows": 2, "columns": {"id": "aa"}}}}
    rep2 = _diff(tmp_path / "2", {"fp": FP}, {"fp": dropped})
    assert [d["what"] for d in rep2.differences] == ["column missing in new"]


def test_a_new_scheduled_job_or_alert_is_never_an_allowed_addition(tmp_path):
    job = {"id": "daily_sales_sync", "func": "run_daily_sales_sync", "trigger": "cron[hour='4', minute='0']"}
    extra = {"id": "clover_catalog_nightly", "func": "x", "trigger": "cron[hour='3']"}
    rep = _diff(tmp_path, {"jobs": {"main": [job]}}, {"jobs": {"main": [extra, job]}})
    # Keyed by id: exactly one difference for the one added job.
    assert [(d["path"], d["what"]) for d in rep.differences] == [("main.clover_catalog_nightly", "added")]
    alert = {"title": "3 vendors critical", "body": "A, B, C"}
    rep2 = _diff(
        tmp_path / "2",
        {"alerts": {"triggers_at_0900": {"critical_vendors": [alert]}}},
        {"alerts": {"triggers_at_0900": {"critical_vendors": [alert, alert]}}},
    )
    assert [d["what"] for d in rep2.differences] == ["list length"]


def test_recordings_with_another_now_user_or_python_are_not_comparable(tmp_path):
    rep = _diff(
        tmp_path,
        {"meta": {"python": "3.12.14"}},
        {"meta": {"now": "2026-10-16T12:00:00-04:00", "python": "3.9.6"}},
    )
    assert {d["path"] for d in rep.differences} == {"now", "python"}
    same_minor = _diff(tmp_path / "2", {"meta": {"python": "3.12.1"}}, {"meta": {"python": "3.12.14"}})
    assert same_minor.differences == []


def test_cli_exit_codes(tmp_path):
    a = _recording(tmp_path / "a", endpoints={"sales": SALES})
    b = _recording(tmp_path / "b", endpoints={"sales": SALES})
    assert parity_diff.main([str(a), str(b)]) == 0
    changed = _recording(tmp_path / "c", endpoints={"sales": {"json": {}}})
    out = tmp_path / "report.json"
    assert parity_diff.main([str(a), str(changed), "--json", str(out)]) == 1
    assert json.loads(out.read_text())["differences"]
    assert parity_diff.main([str(a), str(tmp_path / "nothing")]) == 2


# --- frozen clock ----------------------------------------------------------------------------


def test_frozen_clock_freezes_every_now_and_keeps_real_types():
    ny = zoneinfo.ZoneInfo("America/New_York")
    instant = dt.datetime(2026, 10, 15, 12, 0, tzinfo=ny)
    real_datetime, real_date = dt.datetime, dt.date
    parity_clock.freeze(instant)
    try:
        assert dt.datetime.now(dt.timezone.utc) == instant
        assert dt.datetime.now(ny).hour == 12
        assert dt.datetime.utcnow() == dt.datetime(2026, 10, 15, 16, 0)
        made = dt.datetime(2026, 1, 2, 3, 4)
        assert type(made) is real_datetime and isinstance(made, dt.datetime)
        assert type(dt.date(2026, 1, 2)) is real_date and isinstance(real_date(2026, 1, 2), dt.date)
        assert isinstance(made, dt.date)  # a datetime is a date, as for the real classes
        assert type(dt.datetime.fromisoformat("2026-10-15T01:02:03")) is real_datetime
        assert type(dt.datetime.combine(real_date(2026, 1, 2), dt.time(5), ny)) is real_datetime
    finally:
        parity_clock.unfreeze()
    assert dt.datetime is real_datetime and dt.date is real_date


def test_freeze_needs_an_aware_datetime():
    with pytest.raises(ValueError):
        parity_clock.freeze(dt.datetime(2026, 10, 15, 12, 0))
    assert parity_clock.frozen_at() is None


# --- fingerprint -----------------------------------------------------------------------------


def _small_db(path: pathlib.Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, item TEXT, qty REAL);
        INSERT INTO orders (item, qty) VALUES ('Goat Mix', 1.5), ('Rice', 2), (NULL, 0);
        CREATE TABLE alembic_version (version_num TEXT);
        INSERT INTO alembic_version VALUES ('0059');
        """
    )
    conn.commit()
    conn.close()


def test_fingerprint_is_per_column_so_an_added_column_leaves_the_rest_alone(tmp_path):
    db = tmp_path / "f.db"
    _small_db(db)
    before = fingerprint(str(db))
    assert set(before["tables"]) == {"orders"}  # alembic_version skipped on purpose
    assert before["tables"]["orders"]["rows"] == 3
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE orders ADD COLUMN source TEXT DEFAULT 'toast'")
    conn.commit()
    conn.close()
    after = fingerprint(str(db))
    for col in ("id", "item", "qty"):
        assert after["tables"]["orders"]["columns"][col] == before["tables"]["orders"]["columns"][col]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE orders SET qty = 2.5 WHERE item = 'Rice'")
    conn.commit()
    conn.close()
    assert fingerprint(str(db))["tables"]["orders"]["columns"]["qty"] != before["tables"]["orders"]["columns"]["qty"]


# --- end to end ------------------------------------------------------------------------------


def _seed_app_db(path: pathlib.Path) -> None:
    """A small database with the app's real schema, one admin and a little
    sales / inventory history."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import InventorySnapshot, Order, User

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(username="owner", password_hash="x", is_admin=True, allowed_features="[]"))
        for day in range(1, 8):
            d = dt.date(2026, 10, 14) - dt.timedelta(days=day - 1)
            for n, (item, qty, price) in enumerate([("Goat Mix", 2.5, 12.99), ("Rice 10lb", 1, 18.99)]):
                s.add(
                    Order(
                        toast_order_guid=f"o{day}",
                        toast_check_guid=f"c{day}",
                        toast_selection_guid=f"s{day}-{n}",
                        business_date=d,
                        item_guid=f"item-{n}",
                        item_name=item,
                        quantity=qty,
                        unit_price=price,
                        net_price=round(qty * price, 2),
                    )
                )
        for n, (name, supplier) in enumerate([("Goat Mix", "Halal Meats"), ("Rice 10lb", "Raja Foods;KP Produce")]):
            s.add(
                InventorySnapshot(
                    snapshot_date=dt.date(2026, 10, 14), item_id=f"item-{n}", name=name, supplier=supplier
                )
            )
        s.commit()
    engine.dispose()


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_recording_twice_is_identical_and_never_touches_the_source(tmp_path):
    src = tmp_path / "store.db"
    _seed_app_db(src)
    before = _sha(src)
    script = str(PARITY / "parity_record.py")
    outs = []
    for run in ("one", "two"):
        out = tmp_path / run
        done = subprocess.run(
            [sys.executable, script, "--db", str(src), "--out", str(out), "--scratch-root", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
        outs.append(out)
    assert _sha(src) == before  # the source file was only read
    assert not list(tmp_path.glob("parity-*"))  # scratch copies cleaned up

    meta = json.loads((outs[0] / "meta.json").read_text())
    assert meta["now"] == "2026-10-15T12:00:00-04:00"  # noon after the newest business date
    assert meta["user"] == "owner"
    assert meta["top_vendors"] == ["Halal Meats", "KP Produce", "Raja Foods"]
    assert meta["blocked_network"] == []
    assert len(meta["endpoints"]) > 100
    day = json.loads((outs[0] / "endpoints" / "sales.items_sold.d-1.json").read_text())
    assert day["status"] == 200 and day["json"]["start_date"] == "2026-10-14"
    assert {i["name"] for i in day["json"]["items"]} == {"Goat Mix", "Rice 10lb"}
    jobs = json.loads((outs[0] / "scheduler_jobs.json").read_text())
    assert "daily_sales_sync" in {j["id"] for j in jobs["main"]}
    assert "notify_morning_summary" in {j["id"] for j in jobs["notifications"]}

    assert meta["format_version"] == 2
    # Endpoint answers keep the server's key order (not sorted).
    raw = (outs[0] / "endpoints" / "sales.items_sold.d-1.json").read_text()
    assert list(json.loads(raw)) == ["name", "path", "params", "status", "content_type", "content_disposition", "json"]

    report = parity_diff.compare(outs[0], outs[1])
    assert report.differences == [], parity_diff.render(report, outs[0], outs[1])
    assert report.notes == []  # key order was checked
    # Read endpoints wrote nothing but the harness's own login row.
    before = json.loads((outs[0] / "fingerprint.json").read_text())["tables"]
    after = json.loads((outs[0] / "fingerprint_after.json").read_text())["tables"]
    assert [t for t in before if before[t]["rows"] != after[t]["rows"]] == ["user_sessions"]
    assert "token" not in after["user_sessions"]["columns"]
