"""A CSV that lands after the 04:05 Toast run gets compared again
(inventory_api.rerun_for_late_csv), so that night counts toward Inventory
from Toast readiness. Late uploads are common (Sep 24 06:49, Oct 2 07:58).

Toast is never called: a fake `toast_get` serves the canned stock / menus of
test_inventory_api.py, and httpx is blocked."""

import datetime as dt
import json
import zoneinfo

import httpx
import pytest

from app.config import get_settings
from app.models import InventorySnapshot, JobRun
from app.pos.adapters.toast import client as toast_http
from app.services import inventory_api as ia
from app.services import inventory_api_readiness as rd
from tests.test_inventory_api import RICE, TEA, TODAY, FakeToast, csv_row

NY = zoneinfo.ZoneInfo("America/New_York")


def at(hour, minute=0, day=TODAY):
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=NY)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    def _no_network(*a, **k):
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    ia.clear_live_stock_cache()
    ia._compare_cache.clear()
    s = get_settings()
    monkeypatch.setattr(s, "inventory_api_snapshots", False)
    monkeypatch.setattr(s, "inventory_api_late_csv_until_hour", 9)
    for name, value in (
        ("inventory_api_ready_nights", 7),
        ("inventory_api_ready_qty_pct", 99.0),
        ("inventory_api_ready_price_pct", 99.0),
        ("inventory_api_ready_barcode_pct", 99.0),
        ("inventory_api_ready_category_pct", 100.0),
        ("inventory_api_ready_max_unexplained_qty", 10),
    ):
        monkeypatch.setattr(s, name, value)
    yield
    ia.clear_live_stock_cache()


@pytest.fixture
def toast(monkeypatch):
    fake = FakeToast()
    monkeypatch.setattr(toast_http, "toast_get", fake)
    monkeypatch.setattr(ia, "_after_new_snapshot", lambda: None)
    return fake


def nightly(db, status="success", detail=None, started=None, day=TODAY):
    """The 04:05 run of `day` (default: no CSV by then, switch off)."""
    if detail is None:
        detail = {"meta": {}, "write_enabled": False, "stored": {"written": 0, "skipped": "switch_off"}}
    started = started or at(4, 5, day).astimezone(dt.timezone.utc)
    job = JobRun(job_name=ia.JOB_SNAPSHOT, status=status, business_date=day, started_at=started, detail=json.dumps(detail))
    if status != "running":
        job.finished_at = started + dt.timedelta(minutes=3)
    db.add(job)
    db.commit()
    return job


def todays_csv(db, made=None):
    """Today's file, uploaded after the nightly run (06:49 by default)."""
    made = made or at(6, 49).astimezone(dt.timezone.utc)
    csv_row(db, RICE, "Rice 20lb", 30, day=TODAY, price=17.99, category="Grains", sub="Rice", uploaded_at=made)
    csv_row(db, TEA, "Tea 1kg", 14, day=TODAY, price=9.99, uploaded_at=made)
    db.commit()


def runs(db):
    return db.query(JobRun).filter(JobRun.job_name == ia.JOB_SNAPSHOT).order_by(JobRun.id).all()


def test_a_late_csv_is_compared_again_and_the_night_counts(db, toast):
    nightly(db)
    assert rd.readiness(db)["nights"][0]["result"] == "skipped"
    todays_csv(db)

    job = ia.rerun_for_late_csv(db, TODAY, now=at(7, 58))
    assert job is not None and job.status == "success", job.detail
    assert job.business_date == TODAY
    detail = json.loads(job.detail)
    assert detail["comparison"]["csv_date"] == TODAY.isoformat()
    assert detail["stored"] == {"written": 0, "skipped": "csv_exists"}
    assert detail["rerun"]["reason"] == ia.LATE_CSV and detail["rerun"]["csv_uploaded_at"]
    # Never writes inventory: only the CSV's rows exist for today.
    assert db.query(InventorySnapshot).filter(
        InventorySnapshot.snapshot_date == TODAY, InventorySnapshot.source_filename.like("api:%")
    ).count() == 0

    r = rd.readiness(db)
    tonight = r["nights"][0]
    assert tonight["date"] == TODAY.isoformat() and tonight["result"] in ("good", "bad")
    assert tonight["metrics"] is not None and tonight["rerun"] == ia.LATE_CSV
    assert len([n for n in r["nights"] if n["date"] == TODAY.isoformat()]) == 1


def test_idempotent_per_date(db, toast):
    nightly(db)
    todays_csv(db)
    assert ia.rerun_for_late_csv(db, TODAY, now=at(7, 0)) is not None
    calls = len(toast.calls)
    # A second upload of the same day's file, and the nightly's end check.
    assert ia.rerun_for_late_csv(db, TODAY, now=at(7, 30)) is None
    assert ia.rerun_for_late_csv(db, TODAY, now=at(8, 0)) is None
    assert len(toast.calls) == calls and len(runs(db)) == 2
    assert ia.late_csv_rerun_due(db, TODAY, at(8, 0)) == (False, "already_compared")


def test_one_at_a_time_in_a_process(db, toast):
    nightly(db)
    todays_csv(db)
    assert ia._late_csv_lock.acquire(blocking=False)
    try:
        assert ia.rerun_for_late_csv(db, TODAY, now=at(7, 0)) is None
    finally:
        ia._late_csv_lock.release()
    assert toast.calls == [] and len(runs(db)) == 1


@pytest.mark.parametrize(
    "when, due",
    [((4, 30), True), ((8, 59), True), ((9, 0), False), ((14, 0), False)],
)
def test_only_before_the_cutoff_hour(db, when, due):
    """After ~9 the store sells, and today's sales aren't synced until the
    next 04:00 - a later pull would show them as unexplained differences."""
    nightly(db)
    todays_csv(db)
    assert ia.late_csv_rerun_due(db, TODAY, at(*when)) == ((True, "due") if due else (False, "after_cutoff"))


def test_cutoff_setting(db, monkeypatch):
    nightly(db)
    todays_csv(db)
    monkeypatch.setattr(get_settings(), "inventory_api_late_csv_until_hour", 0)
    assert ia.late_csv_rerun_due(db, TODAY, at(5)) == (False, "off")
    monkeypatch.setattr(get_settings(), "inventory_api_late_csv_until_hour", 11)
    assert ia.late_csv_rerun_due(db, TODAY, at(10, 30)) == (True, "due")


def test_not_due_cases(db):
    # No nightly run yet (upload before 04:05, or the run hasn't started):
    # the nightly run compares it itself.
    todays_csv(db)
    assert ia.late_csv_rerun_due(db, TODAY, at(4, 1)) == (False, "no_nightly_run")
    # A file for a past date: Toast's count has moved on since.
    yesterday = TODAY - dt.timedelta(days=1)
    nightly(db, day=yesterday)
    csv_row(db, TEA, "Tea 1kg", 14, day=yesterday)
    db.commit()
    assert ia.late_csv_rerun_due(db, yesterday, at(7)) == (False, "not_today")


def test_no_csv_for_today(db):
    nightly(db)
    csv_row(db, TEA, "Tea 1kg", 14)  # yesterday's file only
    db.add(InventorySnapshot(snapshot_date=TODAY, item_id=TEA, name="Tea", source_filename="api:2026-10-13T08:05:00"))
    db.commit()
    assert ia.late_csv_rerun_due(db, TODAY, at(7)) == (False, "no_csv")


def test_the_nightly_already_compared(db):
    todays_csv(db)
    nightly(db, detail={"meta": {}, "comparison": {"csv_date": TODAY.isoformat(), "fields": []}, "stored": {"skipped": "csv_exists"}})
    assert ia.late_csv_rerun_due(db, TODAY, at(7)) == (False, "already_compared")


def test_a_failed_nightly_stays_a_bad_night(db):
    todays_csv(db)
    nightly(db, status="failed", detail={"error": "Toast /stock/v1/inventory returned HTTP 500"})
    assert ia.late_csv_rerun_due(db, TODAY, at(7)) == (False, "nightly_failed")


def test_a_running_nightly_is_left_alone_unless_it_died(db):
    todays_csv(db)
    job = nightly(db, status="running", detail={}, started=at(6, 40).astimezone(dt.timezone.utc))
    assert ia.late_csv_rerun_due(db, TODAY, at(7)) == (False, "running")
    # Its own end check covers a file that came in meanwhile; a run that died
    # (restart mid-run) doesn't block the date for good - but there's still
    # no finished nightly run to stand in for.
    assert ia.late_csv_rerun_due(db, TODAY, at(8)) == (False, "no_nightly_run")
    nightly(db, started=at(4, 5).astimezone(dt.timezone.utc))
    assert ia.late_csv_rerun_due(db, TODAY, at(8)) == (True, "due")
    assert job.id


def test_toast_failing_on_the_rerun_skips_the_night_and_retries_once(db, toast, monkeypatch):
    nightly(db)
    todays_csv(db)

    def boom(*a, **k):
        raise toast_http.ToastFetchError("Toast /stock/v1/inventory returned HTTP 503")

    monkeypatch.setattr(toast_http, "toast_get", boom)
    job = ia.rerun_for_late_csv(db, TODAY, now=at(7))
    assert job.status == "failed" and json.loads(job.detail)["rerun"]["reason"] == ia.LATE_CSV
    tonight = rd.readiness(db)["nights"][0]
    assert tonight["result"] == "skipped" and "Toast failed" in tonight["note"] and "HTTP 503" in tonight["note"]
    assert rd.readiness(db)["reset"] is None  # not a bad night

    assert ia.rerun_for_late_csv(db, TODAY, now=at(7, 30)).status == "failed"
    assert ia.late_csv_rerun_due(db, TODAY, at(8)) == (False, "tries_used")
    assert len(runs(db)) == 1 + ia.LATE_CSV_MAX_TRIES


def test_a_failed_rerun_does_not_break_a_streak(db):
    """Six good nights, then today's 04:05 run had no CSV and the late
    re-run hit a Toast error: still six, no reset."""
    from tests.test_inventory_api_readiness import comparison

    for n in range(6, 0, -1):
        d = TODAY - dt.timedelta(days=n)
        nightly(db, day=d, detail={"meta": {}, "comparison": comparison(), "stored": {"skipped": "csv_exists"}})
    nightly(db)
    nightly(db, status="failed", detail={"error": "timeout", "rerun": {"reason": ia.LATE_CSV}}, started=at(7).astimezone(dt.timezone.utc))
    r = rd.readiness(db)
    assert (r["streak"], r["reset"]) == (6, None)


def test_upload_starts_the_rerun_in_the_background(db, admin_client, monkeypatch):
    from app.routers import inventory as inventory_router

    monkeypatch.setattr(inventory_router, "warm_fix_issues_cache", lambda bind: None)
    calls = []
    monkeypatch.setattr(ia, "rerun_for_late_csv", lambda session, day: calls.append(day))
    csv = b"Item ID,Name,Price,Quantity\nup-1,Uploaded Item,1.99,3\n"

    # The dev copy (scheduler off) never asks Toast.
    monkeypatch.setattr(get_settings(), "scheduler_enabled", False)
    res = admin_client.post("/api/inventory/upload", files={"file": ("items.csv", csv, "text/csv")})
    assert res.status_code == 200, res.text
    assert calls == []

    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    res = admin_client.post("/api/inventory/upload", files={"file": ("items.csv", csv, "text/csv")})
    assert res.status_code == 200, res.text
    assert calls == [TODAY]

    # A failed upload doesn't.
    res = admin_client.post("/api/inventory/upload", files={"file": ("bad.csv", b"Name\nx\n", "text/csv")})
    assert res.status_code == 400 and calls == [TODAY]


def test_the_background_task_never_raises(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)

    def boom(session, day):
        raise RuntimeError("db locked")

    monkeypatch.setattr(ia, "rerun_for_late_csv", boom)
    ia.rerun_after_upload(db.get_bind(), TODAY)  # logged, not raised


def test_the_nightly_job_checks_for_a_file_that_came_in_while_it_ran(db, monkeypatch):
    from app import scheduler

    seen = []

    class Job:
        status = "success"
        business_date = TODAY

    monkeypatch.setattr(scheduler, "_dates_without_successful_sync", lambda *a: [])
    monkeypatch.setattr(ia, "run_api_snapshot", lambda session: Job())
    monkeypatch.setattr(ia, "rerun_for_late_csv", lambda session, day: seen.append(day))
    scheduler.run_inventory_api_snapshot()
    assert seen == [TODAY]

    # A failure there is logged, not raised into the scheduler.
    def boom(session, day):
        raise RuntimeError("x")

    monkeypatch.setattr(ia, "rerun_for_late_csv", boom)
    scheduler.run_inventory_api_snapshot()
