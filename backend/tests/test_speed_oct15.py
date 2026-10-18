"""Speed batch (Oct 15, docs/BATCH_OCT15_CONTRACT.md "As built - SPEED"):

#2 Skip this week / Remind me later no longer compute the Today report;
#3 the Today / reorder report is kept in memory and ends on any committed
   write to what it reads (order placed, sales, snapshots...) or after ~5 min;
#4 the Vendor Calendar projects every vendor in one pass and caches the counts;
#7 GET reorder-candidates?critical_only=1 (the Home Critical tile, small);
#8 GET /api/orders/open/all-time refreshes Toast in the background;
#14 SQLite pragmas; migration 0057 (inventory_snapshots composite index).
"""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import (
    CriticalVendorDismissal,
    InventorySnapshot,
    JobRun,
    Order,
    PlacedOrder,
    PurchaseLogEntry,
    VendorSchedule,
)
from app.services import critical_dismissals as svc
from app.services import open_orders, reorder, report_cache, supplier_projection, vendor_calendar
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REORDER = "/api/ordering/reorder-candidates"
DISMISS = "/api/reorder/critical-dismissals"
TODAY = dt.date.today()


def _snap(db, item_id, qty, supplier="Raja Foods", day=TODAY, name=None):
    db.add(InventorySnapshot(snapshot_date=day, item_id=item_id, name=name or item_id.title(), inventory_quantity=qty,
                             price=3.99, cost=2.0, supplier=supplier, category="Snacks", source_filename="f.csv"))


def _sale(db, day, item_id, qty, guid):
    db.add(Order(toast_order_guid="o" + guid, toast_check_guid="c" + guid, toast_selection_guid=guid,
                 business_date=day, item_guid=item_id, item_name=item_id, quantity=qty, unit_price=1.0,
                 net_price=qty, voided=False))


@pytest.fixture
def store(db):
    """Raja Foods: 6 items with 2 on hand selling 2 a day (a Critical
    vendor); Chetak: plenty on hand (not critical). Both delivered today."""
    for n in range(6):
        _snap(db, f"r{n}", 2)
        for back in range(1, 31):
            _sale(db, TODAY - dt.timedelta(days=back), f"r{n}", 2, f"r{n}-{back}")
    _snap(db, "c0", 500, supplier="Chetak")
    for back in range(1, 31):
        _sale(db, TODAY - dt.timedelta(days=back), "c0", 1, f"c0-{back}")
    for vendor in ("Raja Foods", "Chetak"):
        db.add(PurchaseLogEntry(item_id="x", item_name="x", supplier=vendor, quantity_received=1, received_date=TODAY,
                                created_at=utcnow()))
    db.commit()


@pytest.fixture
def computed(monkeypatch):
    """How many times the reorder report is really worked out."""
    calls = []
    real_avail, real_legacy = reorder._availability_candidates, reorder._legacy_candidates

    def avail(*a, **k):
        calls.append("availability")
        return real_avail(*a, **k)

    def legacy(*a, **k):
        calls.append("legacy")
        return real_legacy(*a, **k)

    monkeypatch.setattr(reorder, "_availability_candidates", avail)
    monkeypatch.setattr(reorder, "_legacy_candidates", legacy)
    return calls


def _vendors(body):
    return {g["vendor"]: g for g in body["vendors"]}


# --- report_cache ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,table",
    [
        ("INSERT INTO orders (a) VALUES (?)", "orders"),
        ('INSERT OR REPLACE INTO "inventory_snapshots" (a) VALUES (1)', "inventory_snapshots"),
        ("  UPDATE placed_orders SET status=? WHERE id=?", "placed_orders"),
        ("DELETE FROM purchase_log WHERE id = 3", "purchase_log"),
        ("SELECT * FROM orders", None),
        ("PRAGMA cache_size", None),
        ("", None),
    ],
)
def test_written_table(sql, table):
    assert report_cache.written_table(sql) == table


def test_cached_reuses_until_invalidated(db):
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    first = report_cache.cached(db, ("t",), compute)
    assert report_cache.cached(db, ("t",), compute) is first and len(calls) == 1
    report_cache.invalidate("test")
    assert report_cache.cached(db, ("t",), compute) == {"n": 2}


def test_a_committed_write_to_a_tracked_table_ends_the_cache(db):
    calls = []
    compute = lambda: calls.append(1) or len(calls)  # noqa: E731
    report_cache.cached(db, ("t",), compute)
    # Untracked table: kept.
    db.add(JobRun(job_name="open_orders_scan", status="success"))
    db.commit()
    report_cache.cached(db, ("t",), compute)
    assert len(calls) == 1
    # Rolled back: kept.
    db.add(PlacedOrder(supplier="Raja Foods", item_count=0, items_json="[]", status="placed"))
    db.flush()
    db.rollback()
    report_cache.cached(db, ("t",), compute)
    assert len(calls) == 1
    # An UPDATE of an existing row (no new id - the fingerprint alone would miss it).
    _snap(db, "x", 1)
    db.commit()
    report_cache.cached(db, ("t",), compute)
    assert len(calls) == 2
    db.query(InventorySnapshot).update({"inventory_quantity": 5})
    db.commit()
    report_cache.cached(db, ("t",), compute)
    assert len(calls) == 3


def test_a_write_from_another_process_changes_the_fingerprint(db):
    calls = []
    compute = lambda: calls.append(1) or len(calls)  # noqa: E731
    report_cache.cached(db, ("t",), compute)
    con = sqlite3.connect(engine.url.database)  # not through this app's engine
    try:
        con.execute(
            "INSERT INTO placed_orders (supplier, item_count, items_json, placed_at, status) "
            "VALUES ('Raja Foods', 0, '[]', '2026-10-14 12:00:00', 'placed')"
        )
        con.commit()
    finally:
        con.close()
    report_cache.cached(db, ("t",), compute)
    assert len(calls) == 2


def test_ttl_and_a_write_during_the_computation(db, monkeypatch):
    calls = []
    compute = lambda: calls.append(1) or len(calls)  # noqa: E731
    report_cache.cached(db, ("t",), compute)
    monkeypatch.setattr(report_cache, "TTL_SECONDS", 0)
    report_cache.cached(db, ("t",), compute)
    assert len(calls) == 2
    monkeypatch.setattr(report_cache, "TTL_SECONDS", 300)

    def racing():
        calls.append(1)
        report_cache.invalidate("a write landed meanwhile")
        return len(calls)

    report_cache.cached(db, ("r",), racing)
    report_cache.cached(db, ("r",), compute)  # the racing value was not kept
    assert len(calls) == 4


def test_two_requests_missing_at_once_compute_once(db):
    calls = []
    gate = threading.Event()

    def slow():
        calls.append(1)
        gate.wait(5)
        return "v"

    results = []

    def request():
        session = SessionLocal()
        try:
            results.append(report_cache.cached(session, ("slow",), slow))
        finally:
            session.close()

    threads = [threading.Thread(target=request) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(10)
    assert results == ["v", "v"] and len(calls) == 1


# --- #3 reorder-candidates cached --------------------------------------------------------------


def test_reorder_report_is_computed_once_and_ends_on_order_placed(db, admin_client, store, computed):
    first = admin_client.get(REORDER, params={"lookback_days": 30, "lead_time_days": 7}).json()
    assert _vendors(first)["Raja Foods"]["urgency"]["tier"] == "critical"
    second = admin_client.get(REORDER, params={"lookback_days": 30, "lead_time_days": 7}).json()
    assert len(computed) == 1 and second["vendors"] == first["vendors"]
    # Another setting is its own entry.
    admin_client.get(REORDER, params={"lookback_days": 14, "lead_time_days": 3})
    assert len(computed) == 2
    # "Order already placed" -> placed_orders row -> the vendor shows Ordered at once.
    r = admin_client.post("/api/order-actions", json={"vendor": "Raja Foods", "action": "placed"})
    assert r.status_code == 200, r.text
    third = admin_client.get(REORDER, params={"lookback_days": 30, "lead_time_days": 7}).json()
    assert len(computed) == 3
    assert _vendors(third)["Raja Foods"]["urgency"]["tier"] == "ordered"


@pytest.mark.parametrize("change", ["sale", "snapshot", "delivery"])
def test_new_sales_stock_or_deliveries_end_the_cache(db, admin_client, store, computed, change):
    admin_client.get(REORDER)
    if change == "sale":
        _sale(db, TODAY - dt.timedelta(days=1), "c0", 50, "late")
    elif change == "snapshot":
        db.query(InventorySnapshot).filter_by(item_id="r0").update({"inventory_quantity": 100})
    else:
        db.add(PurchaseLogEntry(item_id="r1", item_name="r1", supplier="Raja Foods", quantity_received=5,
                                received_date=TODAY, created_at=utcnow()))
    db.commit()
    admin_client.get(REORDER)
    assert len(computed) == 2


def test_dismissal_annotation_never_lands_on_the_cached_groups(db, admin_client, store):
    r = admin_client.post(DISMISS, json={"vendor": "Raja Foods", "kind": "snooze", "preset": "tomorrow"})
    assert r.status_code == 200, r.text
    body = admin_client.get(REORDER).json()
    assert _vendors(body)["Raja Foods"]["critical_dismissed"] is True
    cached = reorder.cached_report(db, 30, 7, TODAY)["vendors"]
    assert all("critical_dismissed" not in g for g in cached)
    # Show again: the next read (same cached report) says so.
    admin_client.delete(f"{DISMISS}/{r.json()['id']}")
    assert _vendors(admin_client.get(REORDER).json())["Raja Foods"]["critical_dismissed"] is False


# --- #7 critical_only ------------------------------------------------------------------------------


def test_critical_only_is_the_home_tile_list(db, admin_client, store):
    full = admin_client.get(REORDER).json()
    assert {g["vendor"] for g in full["vendors"]} >= {"Raja Foods"} and full["candidates"]
    assert full["critical_only"] is False
    admin_client.post(DISMISS, json={"vendor": "Raja Foods", "kind": "skip_week"})
    small = admin_client.get(REORDER, params={"critical_only": 1}).json()
    assert small["critical_only"] is True and small["candidates"] == []
    assert [g["vendor"] for g in small["vendors"]] == ["Raja Foods"]
    assert small["vendors"][0]["critical_dismissed"] is True  # hidden ones included, like the app's tile
    expected = [g for g in full["vendors"] if not g["inactive"] and g["urgency"]["tier"] == "critical"]
    assert [g["items"] for g in small["vendors"]] == [g["items"] for g in expected]
    # Same top-level fields as the full report (old builds' parser).
    assert set(small) == set(full)


def test_critical_only_for_the_demo_login_is_a_read(db):
    demo = make_user(db, "app_store_review", features=["reorder_candidates"])
    r = client_for(db, demo).get(REORDER, params={"critical_only": 1})
    assert r.status_code == 200


# --- #2 skip / snooze without the report ----------------------------------------------------


def test_skip_and_snooze_do_not_compute_the_report(db, admin, admin_client, store, computed):
    r = admin_client.post(DISMISS, json={"vendor": "raja foods", "kind": "skip_week", "note": "next week"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vendor"] == "Raja Foods" and body["kind"] == "skip_week" and body["note"] == "next week"
    assert body["warning"] is None  # not known without the report
    r = admin_client.post(DISMISS, json={"vendor": "Chetak", "kind": "snooze", "preset": "tomorrow"})
    assert r.status_code == 200 and r.json()["warning"] is None
    assert computed == []
    # Same response shape as before (old builds).
    for key in ("id", "vendor", "kind", "kind_label", "until", "ends_label", "created_by_name", "warning"):
        assert key in body
    # Not a supplier: only then is the report asked (it may be "Unassigned").
    assert admin_client.post(DISMISS, json={"vendor": "Nobody", "kind": "skip_week"}).status_code == 404
    assert len(computed) == 1


def test_skip_uses_a_cached_report_for_the_warning(db, admin_client, store, computed):
    admin_client.get(REORDER)  # the Home tile / Reorder page read
    r = admin_client.post(DISMISS, json={"vendor": "Chetak", "kind": "skip_week"})
    assert r.json()["warning"] == svc.NOT_CRITICAL_WARNING
    r = admin_client.post(DISMISS, json={"vendor": "Raja Foods", "kind": "skip_week"})
    assert r.json()["warning"] is None  # critical
    assert len(computed) == 1


def test_close_still_stores_the_critical_items_and_settles_came_back(db, admin_client, store, computed):
    r = admin_client.post(DISMISS, json={"vendor": "Raja Foods", "kind": "close", "reason": "ordered"})
    assert r.status_code == 200, r.text
    assert r.json()["critical_item_count"] == 6 and len(computed) == 1
    # A second close on the cached report: no new computation.
    admin_client.post(DISMISS, json={"vendor": "Raja Foods", "kind": "close", "reason": "counts_wrong"})
    assert len(computed) == 1
    # Something new turns critical -> the next Today report read ends the close.
    _snap(db, "r9", 1)
    for back in range(1, 31):
        _sale(db, TODAY - dt.timedelta(days=back), "r9", 3, f"r9-{back}")
    db.commit()
    body = admin_client.get(REORDER).json()
    assert _vendors(body)["Raja Foods"]["critical_dismissed"] is False
    db.expire_all()
    newest = db.query(CriticalVendorDismissal).order_by(CriticalVendorDismissal.id.desc()).first()
    assert newest.cleared_reason == "new_critical_item"


def test_skip_settles_an_expired_one_without_the_report(db, admin, admin_client, store):
    past = utcnow() - dt.timedelta(hours=1)
    db.add(CriticalVendorDismissal(vendor="Raja Foods", kind="snooze", until=past, created_by_user_id=admin.id,
                                   created_by_name="boss", created_at=past - dt.timedelta(hours=2)))
    db.commit()
    admin_client.post(DISMISS, json={"vendor": "Raja Foods", "kind": "skip_week"})
    db.expire_all()
    old = db.query(CriticalVendorDismissal).order_by(CriticalVendorDismissal.id).first()
    assert old.cleared_reason == "expired"


def test_a_vendor_only_the_report_knows_still_works(db, admin, monkeypatch):
    groups = [{"vendor": "Unassigned", "urgency": {"tier": "can_wait"}, "items": [], "inactive": True}]
    monkeypatch.setattr(svc, "today_report_groups", lambda db_, now=None: groups)
    monkeypatch.setattr(svc, "list_suppliers", lambda db_: ["Raja Foods"])
    row, warning = svc.create_dismissal(db, admin, "unassigned", "skip_week")
    assert row.vendor == "Unassigned" and warning == svc.NOT_CRITICAL_WARNING


def test_skip_is_still_refused_for_the_demo_login(db):
    demo = make_user(db, "app_store_review", is_admin=True, features=["reorder_candidates"])
    r = client_for(db, demo).post(DISMISS, json={"vendor": "Raja Foods", "kind": "skip_week"})
    assert r.status_code == 403


# --- #4 vendor calendar ----------------------------------------------------------------------------


def _schedule(db, vendor, weekdays):
    db.add(VendorSchedule(vendor=vendor, delivery_weekdays=json.dumps(weekdays), lead_days=1, active=True,
                          created_at=utcnow()))
    db.commit()


def test_project_suppliers_matches_one_projection_per_vendor(db, store):
    want = {"Raja Foods": [3, 4], "Chetak": [7]}
    together = supplier_projection.project_suppliers(db, want, 30, TODAY)
    for vendor, horizons in want.items():
        alone = supplier_projection.compute_supplier_projection(db, vendor, 30, horizons, TODAY)
        assert together[vendor] == alone


def test_vendor_calendar_projects_once_and_caches(db, store, monkeypatch):
    _schedule(db, "Raja Foods", [0, 3])
    _schedule(db, "Chetak", list(range(7)))
    calls = []
    real = supplier_projection.project_suppliers

    def counting(db_, horizons, *a, **k):
        calls.append(sorted(horizons))
        return real(db_, horizons, *a, **k)

    monkeypatch.setattr(vendor_calendar, "project_suppliers", counting)
    start = TODAY
    first = vendor_calendar.get_vendor_calendar(db, start, start + dt.timedelta(days=13))
    assert len(calls) == 1 and calls[0] == ["Chetak", "Raja Foods"]  # one pass for both vendors
    again = vendor_calendar.get_vendor_calendar(db, start, start + dt.timedelta(days=13))
    assert again == first and len(calls) == 1
    # Home's two-day read reuses the same per-vendor counts.
    vendor_calendar.get_vendor_calendar(db, start, start + dt.timedelta(days=1))
    assert len(calls) == 1
    raja = [e for e in first["events"] if e["type"] == "order_due" and e["vendor"] == "Raja Foods"]
    assert raja and all(e["items_needing_order"] == 6 and e["urgent"] for e in raja)
    # The same numbers as the per-vendor path.
    rows = supplier_projection.compute_supplier_projection(db, "Raja Foods", 30, [raja[0]["horizon_days"]], TODAY)
    assert raja[0]["total_need_to_order"] == round(
        sum(r["projections"][str(raja[0]["horizon_days"])]["need_to_order"] or 0 for r in rows), 2
    )
    # A new snapshot ends the cached counts.
    db.query(InventorySnapshot).filter_by(item_id="r0").update({"inventory_quantity": 1000})
    db.commit()
    vendor_calendar.get_vendor_calendar(db, start, start + dt.timedelta(days=1))
    assert len(calls) == 2


def test_vendor_calendar_placed_and_skips_are_never_stale(db, store):
    _schedule(db, "Raja Foods", list(range(7)))
    first = vendor_calendar.get_vendor_calendar(db, TODAY, TODAY)
    due = next(e for e in first["events"] if e["type"] == "order_due")
    assert due["ordered"] is False and due["skipped"] is False
    vendor_calendar.skip_order(db, "Raja Foods", dt.date.fromisoformat(due["for_delivery_date"]), "boss")
    due = next(e for e in vendor_calendar.get_vendor_calendar(db, TODAY, TODAY)["events"] if e["type"] == "order_due")
    assert due["skipped"] is True


# --- #8 open orders ---------------------------------------------------------------------------------


@pytest.fixture
def toast_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "scheduler_enabled", True)
    monkeypatch.setattr(s, "toast_client_id", "x")
    monkeypatch.setitem(open_orders._live_last_failure, "at", None)


def test_open_orders_read_does_not_wait_for_toast(db, admin_client, toast_on, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow_refresh(db_, full=False, max_retries=4):
        started.set()
        release.wait(5)
        job = JobRun(job_name=open_orders.JOB_NAME, status="success")
        db_.add(job)
        db_.commit()
        return job

    monkeypatch.setattr(open_orders, "refresh_open_orders_cache", slow_refresh)
    t0 = time.monotonic()
    r = admin_client.get("/api/orders/open/all-time")
    assert r.status_code == 200 and time.monotonic() - t0 < 2
    assert set(r.json()) >= {"mode", "orders", "count", "last_refreshed_at", "last_job_status"}
    assert started.wait(5)
    # One refresh at a time: a second stale read doesn't start another.
    assert open_orders.refresh_today_in_background(db) is False
    release.set()
    for _ in range(50):
        if not open_orders._live_refresh_lock.locked():
            break
        time.sleep(0.05)
    assert not open_orders._live_refresh_lock.locked()
    db.expire_all()
    assert db.query(JobRun).filter_by(job_name=open_orders.JOB_NAME, status="success").count() == 1
    # Fresh now: nothing started.
    assert open_orders.refresh_today_in_background(db) is False


def test_background_refresh_failure_backs_off_and_releases(db, toast_on, monkeypatch):
    def boom(db_, full=False, max_retries=4):
        raise RuntimeError("toast down")

    monkeypatch.setattr(open_orders, "refresh_open_orders_cache", boom)
    assert open_orders.refresh_today_in_background(db) is True
    for _ in range(50):
        if not open_orders._live_refresh_lock.locked():
            break
        time.sleep(0.05)
    assert not open_orders._live_refresh_lock.locked()
    assert open_orders._live_last_failure["at"] is not None
    assert open_orders.refresh_today_in_background(db) is False  # cooling down


def test_background_refresh_off_in_dev_and_tests(db):
    assert open_orders.refresh_today_in_background(db) is False


# --- #14 pragmas, #6 migration ---------------------------------------------------------------------


def test_sqlite_pragmas_on_every_connection(db):
    con = db.connection()
    assert con.exec_driver_sql("PRAGMA cache_size").scalar() == -16384
    assert con.exec_driver_sql("PRAGMA temp_store").scalar() == 2  # MEMORY
    assert con.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_migration_0057_adds_the_snapshot_index_and_reruns(tmp_path):
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *args: subprocess.run(  # noqa: E731
        [str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True
    )
    run("upgrade", "0057")
    con = sqlite3.connect(db_file)
    try:
        cols = [r[2] for r in con.execute("PRAGMA index_info(ix_inventory_snapshots_item_date_qty)")]
        assert cols == ["item_id", "snapshot_date", "inventory_quantity"]
        plan = " ".join(
            str(r[-1])
            for r in con.execute(
                "EXPLAIN QUERY PLAN SELECT item_id, snapshot_date, inventory_quantity FROM inventory_snapshots "
                "WHERE item_id IN ('a', 'b') AND snapshot_date >= '2026-09-01' AND snapshot_date <= '2026-10-01'"
            )
        )
        assert "ix_inventory_snapshots_item_date_qty" in plan
    finally:
        con.close()
    run("downgrade", "0056")
    con = sqlite3.connect(db_file)
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'ix_inventory_snapshots_item_date_qty'").fetchone()
    # Created by hand before the migration ran: still fine.
    con.execute("CREATE INDEX ix_inventory_snapshots_item_date_qty ON inventory_snapshots (item_id, snapshot_date, inventory_quantity)")
    con.commit()
    con.close()
    run("upgrade", "0057")
