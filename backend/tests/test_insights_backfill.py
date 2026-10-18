"""Insights backfill fixes (Oct 15 batch): an honest coverage note, the
daily-budget reserve, the hourly catch-up, "unavailable" markers, Loss
watch counting only the chosen days, 12 weeks of channels and a truthful
nightly status. Toast is the FakeToast of test_insights.py - never called
for real; nothing is pushed or sent.
"""

import datetime as dt
import json

import pytest

from app import scheduler
from app.config import get_settings
from app.insights_models import AnalyticsCache, AnalyticsCallLog
from app.models import JobRun
from app.pos.adapters.toast import analytics as toast_analytics
from app.services import insights, insights_data
from app.timeutil import utcnow
from tests.test_insights import (  # noqa: F401 - pytest fixtures
    TODAY,
    YESTERDAY,
    _a_day_passes,
    admin_c,
    fake,
    filled,
)

NY = __import__("zoneinfo").ZoneInfo("America/New_York")


def _drop(db, report, day):
    db.query(AnalyticsCache).filter_by(report=report, business_date=day).delete()
    db.commit()


def _use_budget(db, family, n=None, ago=dt.timedelta(hours=1)):
    """`n` calls (default: the whole budget) made `ago`."""
    n = insights_data.daily_budget(family) if n is None else n
    at = utcnow() - ago
    for _ in range(n):
        db.add(AnalyticsCallLog(family=family, business_date=YESTERDAY, called_at=at, ok=1))
    db.commit()


def _calls(fake, kind):
    return [c for c in fake.calls if c["kind"] == kind]


def _day(d):
    return f"{d.strftime('%b')} {d.day}"


# --------------------------------------------------------------------------- the note


def test_complete_page_has_no_note(db, filled, admin_c):
    body = admin_c.get("/api/insights/loss-watch?days=30").json()
    assert body["stale"] is False and body["note"] is None
    cov = body["coverage"]
    assert cov["dates_cached"] == cov["dates_expected"] == 30
    assert cov["missing_dates"] == [] and cov["pending"] is None and cov["retry_at"] is None


def test_budget_used_up_says_which_day_and_when(db, filled, admin_c):
    _drop(db, "check", YESTERDAY)
    _use_budget(db, "check")
    body = admin_c.get("/api/insights/loss-watch?days=30").json()
    cov = body["coverage"]
    assert body["stale"] is True and cov["dates_cached"] == 29 and cov["dates_expected"] == 30
    assert cov["pending"] == "waiting_budget"
    assert cov["missing_dates"] == [YESTERDAY.isoformat()]
    frees = dt.datetime.fromisoformat(cov["retry_at"])
    assert dt.timedelta(hours=22) < frees - utcnow() <= dt.timedelta(hours=23)
    note = body["note"]
    assert _day(YESTERDAY) in note and "isn't in yet" in note
    assert "check details 30 times a day" in note and "added automatically by" in note
    assert "background" not in note
    # The fill time is the first catch-up (hh:20) or nightly (4:30) after the budget frees.
    when = insights_data.next_fill_at(frees)
    # Within the hour by day; up to 5 h 10 min when it frees after 23:20
    # (the next slot is then the 4:30 nightly) - this test runs at any hour.
    assert when >= frees and when - frees <= dt.timedelta(hours=5, minutes=10)
    assert insights_data.fmt_time(when) in note


def test_odd_checks_one_day_shows_the_pending_note(db, filled, admin_c):
    _drop(db, "check", YESTERDAY)
    _use_budget(db, "check")
    body = admin_c.get("/api/insights/odd-checks?days=1").json()
    assert body["stale"] is True and body["coverage"]["pending"] == "waiting_budget"
    assert body["note"].startswith(f"{_day(YESTERDAY)} isn't in yet")
    assert "0 of 1" not in body["note"]


def test_loss_watch_counts_only_the_chosen_days(db, filled, admin_c):
    old = YESTERDAY - dt.timedelta(days=20)
    _drop(db, "check", old)
    seven = admin_c.get("/api/insights/loss-watch?days=7").json()
    assert seven["coverage"]["dates_expected"] == 7 and seven["coverage"]["dates_cached"] == 7
    assert seven["stale"] is False and seven["note"] is None
    fourteen = admin_c.get("/api/insights/loss-watch?days=14").json()
    assert fourteen["coverage"]["dates_expected"] == 14 and fourteen["stale"] is False
    thirty = admin_c.get("/api/insights/loss-watch?days=30").json()
    assert thirty["stale"] is True and thirty["coverage"]["dates_cached"] == 29
    assert thirty["coverage"]["missing_dates"] == [old.isoformat()]


def test_just_closed_day_before_the_nightly_is_a_soft_note(db, filled, admin_c, monkeypatch):
    _drop(db, "metrics_day", YESTERDAY)
    # 04:20 the morning after: closed at 04:00, the 04:30 nightly hasn't run.
    at = insights_data.closeout_at(YESTERDAY) + dt.timedelta(minutes=20)
    monkeypatch.setattr(insights, "utcnow", lambda: at)
    for job in db.query(JobRun).filter_by(job_name=insights_data.NIGHTLY_JOB):
        job.started_at = at - dt.timedelta(days=1)
    db.commit()
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert body["coverage"]["pending"] == "tonight"
    assert body["note"] == f"Yesterday's numbers ({_day(YESTERDAY)}) arrive after the 4:30 AM update."
    # Once the nightly has run, the same gap isn't "tonight" any more.
    db.add(JobRun(job_name=insights_data.NIGHTLY_JOB, status="partial", started_at=at, finished_at=at))
    db.commit()
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert body["coverage"]["pending"] != "tonight" and "update" in body["note"]


def test_filling_only_while_a_fill_really_runs(db, fake, admin_c, monkeypatch):
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()  # dev: no fill starts
    assert body["filling"] is False and body["coverage"]["pending"] is None
    assert "being read" not in body["note"] and "at the latest with the 4:30 AM update" in body["note"]
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert len(fake.spawned) == 1
    assert body["coverage"]["pending"] == "filling" and "being read from Toast now" in body["note"]
    assert len(body["coverage"]["missing_dates"]) == insights.MISSING_DATES_SHOWN


def test_not_configured_and_no_answer_notes(db, admin_c):
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert body["note"] == insights.NOT_CONFIGURED_NOTE


def test_today_live_failure_note(db, fake, admin_c):
    fake.fail = {"metrics": toast_analytics.AnalyticsReportFailed("HTTP 500: boom")}
    body = admin_c.get(f"/api/insights/compare?date={TODAY.isoformat()}").json()
    assert body["stale"] is True and insights.NO_ANSWER_NOTE in body["note"]


def test_note_lists_runs_of_days():
    d = dt.date(2026, 10, 1)
    days = [d, d + dt.timedelta(days=1), d + dt.timedelta(days=2), d + dt.timedelta(days=8)]
    assert insights._runs_text(days) == "Oct 1 to Oct 3 and Oct 9"
    many = [d + dt.timedelta(days=3 * k) for k in range(6)]
    assert insights._runs_text(many) == "Oct 1, Oct 4, Oct 7 and 3 more days"


def test_next_fill_at_slots():
    def at(day, h, m):
        return dt.datetime.combine(day, dt.time(h, m), NY)

    day = dt.date(2026, 10, 15)
    assert insights_data.next_fill_at(at(day, 1, 17)) == at(day, 4, 30)
    assert insights_data.next_fill_at(at(day, 13, 5)) == at(day, 13, 20)
    assert insights_data.next_fill_at(at(day, 5, 20)) == at(day, 5, 20)
    assert insights_data.next_fill_at(at(day, 23, 30)) == at(day + dt.timedelta(days=1), 4, 30)


# --------------------------------------------------------------------------- the reserve


def test_a_backfill_before_the_closeout_leaves_the_reserve_for_yesterday(db, fake, monkeypatch):
    # 01:01, before the 04:00 closeout: the open business date is still the
    # day before, so a warm-up run backfills history up to TODAY - 2...
    monkeypatch.setattr(insights_data, "today_business_date", lambda now=None: YESTERDAY)
    insights_data.run_nightly(db)
    early = _calls(fake, "check")
    assert len(early) == 30 - insights_data.DAILY_RESERVE["check"]
    assert insights_data.daily_left(db, "check") == insights_data.DAILY_RESERVE["check"]
    # ...and the real 04:30 nightly still has calls for the day that just closed.
    monkeypatch.setattr(insights_data, "today_business_date", lambda now=None: TODAY)
    fake.calls.clear()
    job = insights_data.run_nightly(db)
    assert [c["start"] for c in _calls(fake, "check")][0] == YESTERDAY
    assert insights_data.cached(db, "check", [YESTERDAY])
    assert json.loads(job.detail)["check"].get("held_for_reserve")
    assert insights_data.daily_left(db, "check") >= insights_data.DAILY_RESERVE["check"] - 1


def test_page_fill_keeps_the_reserve(db, fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    old = [YESTERDAY - dt.timedelta(days=k) for k in range(5, 25)]
    _use_budget(db, "check", 30 - 6)  # 6 left, reserve 4
    insights_data.request_fill([("check", old)])
    fake.spawned[0]()
    assert len(_calls(fake, "check")) == 2
    assert insights_data.daily_left(db, "check") == insights_data.DAILY_RESERVE["check"]


def test_fill_with_no_calls_left_says_so(db, fake):
    res = insights_data.fill(db, "check", [YESTERDAY], max_calls=0, today_bd=TODAY)
    assert res["error"] == insights_data.NO_CALLS_LEFT and res["budget"] is True and res["pending"] == 1
    # Nothing to do -> no error.
    assert insights_data.fill(db, "check", [], max_calls=0, today_bd=TODAY)["error"] is None


# --------------------------------------------------------------------------- the nightly's status


def test_nightly_is_partial_when_yesterday_waits_for_budget(db, filled):
    _drop(db, "check", YESTERDAY)
    _use_budget(db, "check")
    job = insights_data.run_nightly(db)
    detail = json.loads(job.detail)
    assert job.status == "partial"
    assert detail["check"]["error"] == insights_data.NO_CALLS_LEFT
    assert any(w.startswith(f"check: {_day(YESTERDAY)} waiting for budget (frees ") for w in detail["warnings"])


def test_nightly_success_when_everything_is_in(db, filled):
    job = insights_data.run_nightly(db)
    assert job.status == "success" and "warnings" not in json.loads(job.detail)


# --------------------------------------------------------------------------- catch-up


def test_catch_up_makes_no_toast_call_when_nothing_is_missing(db, filled):
    filled.calls.clear()
    assert insights_data.run_catchup(db) == {}
    assert filled.calls == []
    assert db.query(JobRun).filter_by(job_name=insights_data.CATCHUP_JOB).count() == 0


def test_catch_up_reads_the_refused_day_once_the_budget_frees(db, filled):
    _drop(db, "check", YESTERDAY)
    _use_budget(db, "check")
    filled.calls.clear()
    detail = insights_data.run_catchup(db)
    assert filled.calls == [] and detail["check"]["error"] == insights_data.NO_CALLS_LEFT
    assert db.query(JobRun).filter_by(job_name=insights_data.CATCHUP_JOB).count() == 0  # no call, no row
    _a_day_passes(db)
    detail = insights_data.run_catchup(db)
    assert [(c["kind"], c["start"]) for c in filled.calls] == [("check", YESTERDAY)]
    assert detail["check"]["dates"] == 1 and "flags" in detail
    job = db.query(JobRun).filter_by(job_name=insights_data.CATCHUP_JOB).one()
    assert job.status == "success"


def test_catch_up_skips_while_another_fill_runs(db, filled):
    _drop(db, "check", YESTERDAY)
    with insights_data._fill_lock:
        assert insights_data.run_catchup(db) is None


def test_catch_up_is_scheduled_hourly(monkeypatch):
    added = {}

    class FakeScheduler:
        def __init__(self, **kw):
            pass

        def add_job(self, fn, trigger=None, id=None, **kw):
            added[id] = (fn, trigger)

        def start(self):
            pass

        def shutdown(self, wait=False):
            pass

    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    try:
        scheduler.start_scheduler()
    finally:
        monkeypatch.setattr(scheduler, "_scheduler", None)
    fn, trigger = added["insights_catchup"]
    assert fn is scheduler.run_insights_catchup
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == ",".join(str(h) for h in range(5, 24)) and fields["minute"] == "20"
    assert "insights_nightly" in added


# --------------------------------------------------------------------------- unavailable markers


def test_unprocessable_day_is_marked_and_retried_only_nightly(db, filled, admin_c, monkeypatch):
    _drop(db, "check", YESTERDAY)
    _a_day_passes(db)
    filled.fail = {"check": toast_analytics.AnalyticsReportUnprocessable("could not be processed (HTTP 409)")}
    res = insights_data.fill(db, "check", [YESTERDAY], max_calls=3, today_bd=TODAY)
    assert res["unavailable"] == 1 and res["pending"] == 0
    # Pages don't read the marker as a $0 day, and don't spend calls on it again.
    assert insights_data.cached(db, "check", [YESTERDAY]) == {}
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    filled.spawned.clear()
    body = admin_c.get("/api/insights/odd-checks?days=7").json()
    assert filled.spawned == []
    assert body["stale"] is True and body["coverage"]["pending"] == "failed"
    assert body["coverage"]["missing_dates"] == [YESTERDAY.isoformat()]
    assert body["note"] == f"Toast couldn't provide check details for {_day(YESTERDAY)}. We'll ask again with the 4:30 AM update."
    # The catch-up leaves it for the nightly.
    filled.calls.clear()
    insights_data.run_catchup(db)
    assert _calls(filled, "check") == []
    # The nightly retries it; once Toast answers, it's a normal day again.
    filled.fail = {}
    job = insights_data.run_nightly(db)
    assert [c["start"] for c in _calls(filled, "check")] == [YESTERDAY]
    assert insights_data.cached(db, "check", [YESTERDAY])
    assert job.status == "success"
    assert admin_c.get("/api/insights/odd-checks?days=7").json()["stale"] is False


def test_real_client_raises_unprocessable_after_new_requests(monkeypatch):
    from tests.test_sales_accuracy_job import Clock, Script, login_ok

    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "cid")
    monkeypatch.setattr(s, "toast_analytics_client_secret", "never-shown")
    monkeypatch.setattr(s, "toast_analytics_base", "https://analytics.test")
    # is_configured() also needs the restaurant GUID; without this the test
    # only passed where a .env happened to set TOAST_RESTAURANT_GUID.
    monkeypatch.setattr(s, "toast_restaurant_guid", "restaurant-guid")
    clock = Clock()
    monkeypatch.setattr(toast_analytics, "_sleep", clock.sleep)
    monkeypatch.setattr(toast_analytics, "_now", clock.now)
    toast_analytics.reset_state()
    script = login_ok(Script())
    script.on("POST", "/era/v1/check/day", (200, "g-1"), (200, "g-2"), (200, "g-3"))
    script.on("GET", "/era/v1/check/g-1", (409, {"message": "could not process"}))
    script.on("GET", "/era/v1/check/g-2", (409, {"message": "could not process"}))
    script.on("GET", "/era/v1/check/g-3", (409, {"message": "could not process"}))
    try:
        with pytest.raises(toast_analytics.AnalyticsReportUnprocessable):
            toast_analytics.run_report("check", "day", {}, client=script.client())
    finally:
        toast_analytics.reset_state()
    assert issubclass(toast_analytics.AnalyticsReportUnprocessable, toast_analytics.AnalyticsReportFailed)


# --------------------------------------------------------------------------- channels


def test_channels_twelve_weeks_stay_filled(db, filled, admin_c):
    body = admin_c.get("/api/insights/channels?weeks=12").json()
    assert body["stale"] is False and body["note"] is None
    assert insights_data.HISTORY_DAYS["metrics_dining"] >= 7 * 12
