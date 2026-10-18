"""Per-store jobs, the scheduler in multi mode and leader election
(docs/PLATFORM_CONTRACT.md 6.1, 6.2, 18.2; track B)."""

import datetime as dt
import zoneinfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.tenancy import current_store_code, for_each_store, store_context, tenant_session
from app.tenancy import jobs as tjobs
from app.tenancy.testing import update_test_store
from tests.tenancy.conftest import PG_URL, PREFIX, _create_db, drop_db


@pytest.fixture
def clean_jobs():
    tjobs.detach_scheduler()
    tjobs.unregister_store_jobs()
    yield
    tjobs.detach_scheduler()
    tjobs.unregister_store_jobs()


def _user_count(code):
    from app.models import User

    with tenant_session(code, allow_inactive=True) as db:
        return db.query(User).count()


def test_for_each_store_runs_once_per_active_store_in_its_own_context(multi):
    from app.models import User

    update_test_store("GAMMA", status="suspended")

    def job(ref):
        assert current_store_code() == ref.code
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            db.add(User(username=f"job-{ref.code}", password_hash="x", is_admin=False, allowed_features="[]"))
            db.commit()
        finally:
            db.close()
        return ref.code

    assert for_each_store(job) == {"ALPHA": "ALPHA", "BETA": "BETA"}
    assert (_user_count("ALPHA"), _user_count("BETA"), _user_count("GAMMA")) == (1, 1, 0)
    # Writes stay in their own database.
    from app.models import User as U

    with tenant_session("ALPHA") as db:
        assert [u.username for u in db.query(U).all()] == ["job-ALPHA"]


def test_one_store_failing_never_stops_the_others(multi):
    def job(ref):
        if ref.code == "ALPHA":
            raise RuntimeError("boom")
        return "ok"

    out = for_each_store(job)
    assert isinstance(out["ALPHA"], RuntimeError) and out["BETA"] == out["GAMMA"] == "ok"
    assert list(for_each_store(job, stop_on_error=True)) == ["ALPHA"]
    out = for_each_store(lambda ref: current_store_code(), max_workers=3)
    assert out == {"ALPHA": "ALPHA", "BETA": "BETA", "GAMMA": "GAMMA"}


def test_pending_and_dedicated_stores_are_skipped(multi, monkeypatch):
    from app.config import process_settings

    update_test_store("BETA", status="pending")
    update_test_store("GAMMA", hosting="dedicated")
    monkeypatch.setattr(process_settings(), "dedicated_hosting_enabled", True)
    assert list(for_each_store(lambda r: 1)) == ["ALPHA"]
    # On GAMMA's own dedicated server only GAMMA runs.
    monkeypatch.setattr(process_settings(), "dedicated_store_codes", "GAMMA")
    assert list(for_each_store(lambda r: 1)) == ["GAMMA"]


def _fire(sched, job_id, after):
    job = sched.get_job(job_id)
    return job.trigger.get_next_fire_time(None, after)


def test_store_jobs_follow_each_stores_timezone_across_dst(multi, clean_jobs):
    update_test_store("ALPHA", timezone="America/New_York")
    update_test_store("BETA", timezone="Asia/Kolkata")
    update_test_store("GAMMA", status="suspended")
    ran = []

    def nightly():
        ran.append(current_store_code())

    tjobs.register_store_job(
        "nightly", nightly, lambda ref: CronTrigger(hour=4, minute=0, timezone=zoneinfo.ZoneInfo(ref.timezone))
    )
    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.attach_scheduler("main", sched)
    stats = tjobs.reconcile_store_jobs()
    assert stats["main"]["added"] == 2
    assert sorted(j.id for j in sched.get_jobs()) == ["nightly:ALPHA", "nightly:BETA"]
    ny = zoneinfo.ZoneInfo("America/New_York")
    off_a = tjobs.store_jitter_seconds("ALPHA")
    # The night before the US DST change (2026-11-01 02:00 EDT -> EST).
    after = dt.datetime(2026, 10, 31, 12, 0, tzinfo=dt.timezone.utc)
    nxt = _fire(sched, "nightly:ALPHA", after)
    assert nxt.astimezone(ny) == dt.datetime(2026, 11, 1, 4, 0, tzinfo=ny) + dt.timedelta(seconds=off_a)
    assert nxt.astimezone(dt.timezone.utc).hour == 9  # 04:00 EST = 09:00 UTC (was 08:00 in EDT)
    before = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)
    assert _fire(sched, "nightly:ALPHA", before).astimezone(dt.timezone.utc).hour == 8
    kol = _fire(sched, "nightly:BETA", after)
    assert kol.astimezone(zoneinfo.ZoneInfo("Asia/Kolkata")).time() == (
        dt.datetime(2000, 1, 1, 4, 0) + dt.timedelta(seconds=tjobs.store_jitter_seconds("BETA"))
    ).time()
    # The job function runs inside its store.
    sched.get_job("nightly:BETA").func()
    assert ran == ["BETA"]
    assert 0 <= off_a <= tjobs.MAX_JITTER_SECONDS


def test_reconcile_follows_store_changes(multi, clean_jobs):
    tjobs.register_store_job("nightly", lambda: None, lambda ref: CronTrigger(hour=4, timezone=ref.timezone))
    tjobs.register_store_startup_job("catchup", lambda: None, 10)
    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.attach_scheduler("main", sched)
    tjobs.reconcile_store_jobs()
    ids = {j.id for j in sched.get_jobs()}
    assert {"nightly:ALPHA", "nightly:BETA", "nightly:GAMMA", "catchup:ALPHA"} <= ids
    # Suspend BETA: its jobs go. Timezone change of GAMMA: rescheduled.
    update_test_store("BETA", status="suspended")
    update_test_store("GAMMA", timezone="Europe/London")
    tjobs.on_store_changed("BETA")
    tjobs.on_store_changed("GAMMA")
    ids = {j.id for j in sched.get_jobs()}
    assert "nightly:BETA" not in ids and "nightly:ALPHA" in ids
    assert "Europe/London" in repr(sched.get_job("nightly:GAMMA").trigger)
    # Startup jobs are added once per store per process.
    stats = tjobs.reconcile_store_jobs()
    assert stats["main"]["startup"] == 0
    # A store approved later gets its startup catch-up and jobs.
    update_test_store("BETA", status="active")
    stats = tjobs.reconcile_store_jobs()
    assert stats["main"]["added"] == 1 and stats["main"]["startup"] == 0  # BETA was seen before


def test_job_of_a_store_suspended_meanwhile_is_skipped(multi):
    ran = []
    runner = tjobs.store_job_runner(lambda: ran.append(1), "ALPHA")
    update_test_store("ALPHA", status="suspended")
    assert runner() is None and ran == []


def test_capability_gating_is_per_store(multi, clean_jobs):
    from app.scheduler import declare_main_store_jobs

    update_test_store("BETA", pos_provider="clover")  # no adapter: no POS jobs
    update_test_store("GAMMA", pos_provider="csv")
    declare_main_store_jobs()
    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.attach_scheduler("main", sched)
    tjobs.reconcile_store_jobs()
    ids = {j.id for j in sched.get_jobs()}
    assert "daily_sales_sync:ALPHA" in ids and "inventory_api_snapshot:ALPHA" in ids
    assert not any(i.endswith(":BETA") and not i.startswith("startup_fix") for i in ids), sorted(ids)
    assert "daily_sales_sync:GAMMA" not in ids
    assert "startup_fix_issues_warmup:BETA" in ids  # POS-neutral


def test_per_store_settings_shape_the_jobs(multi, clean_jobs):
    from app.scheduler import declare_main_store_jobs
    from app.tenancy.testing import set_test_store_settings

    set_test_store_settings("ALPHA", {"sales_sync_hour": 6, "insights_nightly_enabled": False})
    set_test_store_settings("BETA", {"sales_repull_times": "07:15,19:45"})
    declare_main_store_jobs()
    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.attach_scheduler("main", sched)
    tjobs.reconcile_store_jobs()
    ids = {j.id for j in sched.get_jobs()}
    assert "insights_nightly:ALPHA" not in ids and "insights_nightly:BETA" in ids
    assert "hour='6'" in repr(sched.get_job("daily_sales_sync:ALPHA").trigger)
    assert "hour='4'" in repr(sched.get_job("daily_sales_sync:BETA").trigger)
    rep = repr(sched.get_job("sales_repull:BETA").trigger)
    assert "hour='7', minute='15'" in rep and "hour='19', minute='45'" in rep


def test_multi_tenant_schedulers_start_with_every_stores_jobs(multi, clean_jobs, monkeypatch):
    from app import scheduler
    from app.config import process_settings

    s = process_settings()
    for flag in ("scheduler_enabled", "notifications_enabled", "team_jobs_enabled"):
        monkeypatch.setattr(s, flag, True)
    started = scheduler.start_multi_tenant_schedulers()
    try:
        assert set(started) == {"main", "notifications", "team", "platform"}
        main_ids = {j.id for j in started["main"].get_jobs()}
        notif_ids = {j.id for j in started["notifications"].get_jobs()}
        team_ids = {j.id for j in started["team"].get_jobs()}
        for code in ("ALPHA", "BETA", "GAMMA"):
            assert f"daily_sales_sync:{code}" in main_ids
            assert f"notify_morning_summary:{code}" in notif_ids
            assert f"notify_closing_checklist:{code}" in notif_ids
            assert f"team_team_changes_prune:{code}" in team_ids
        # Interval jobs once, running every store per tick.
        assert "open_orders_today_refresh" in main_ids and "notify_invoice_failed" in notif_ids
        assert {j.id for j in started["platform"].get_jobs()} == {
            "tenancy_reconcile_store_jobs",
            "tenancy_sweep_idle_engines",
        }
        from app.pos.gating import job_func_name

        assert job_func_name(started["main"].get_job("daily_sales_sync:ALPHA")) == "run_daily_sales_sync"
    finally:
        scheduler.shutdown_multi_tenant_schedulers()
    assert tjobs.attached_schedulers() == {}


def test_each_store_job_runs_every_store(multi):
    ran = []

    def tick():
        ran.append(current_store_code())

    tjobs.each_store_job(tick)()
    assert ran == ["ALPHA", "BETA", "GAMMA"]


# --- leader election ----------------------------------------------------------------------


@pytest.fixture
def leader_db():
    from tests.tenancy.conftest import pg_available

    if not pg_available():
        pytest.skip("no PostgreSQL")
    import os

    name = f"{PREFIX}_{os.getpid()}_leader"
    _create_db(name)
    yield PG_URL.rsplit("/", 1)[0] + "/" + name
    drop_db(name)


def test_only_one_leader(leader_db):
    from app.tenancy.leader import Leadership

    key = 0x5354_0001
    first, second = Leadership(leader_db, key), Leadership(leader_db, key)
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is False
        assert first.alive()
        first.release()
        assert second.try_acquire() is True
    finally:
        first.release()
        second.release()


def test_leader_loop_starts_and_stops_the_schedulers(leader_db):
    from app.tenancy.leader import LeaderLoop

    events = []
    key = 0x5354_0002
    a = LeaderLoop(leader_db, lambda: events.append("a-elected"), lambda: events.append("a-lost"), key=key)
    b = LeaderLoop(leader_db, lambda: events.append("b-elected"), lambda: events.append("b-lost"), key=key)
    try:
        a.tick()
        b.tick()
        assert events == ["a-elected"] and a.is_leader and not b.is_leader
        # A's connection dies: it notices, stops its schedulers; B takes over.
        a.leadership._conn.invalidate()
        a.tick()
        assert events[-1] == "a-lost" and not a.is_leader
        b.tick()
        assert events[-1] == "b-elected"
    finally:
        a.stop()
        b.stop()
