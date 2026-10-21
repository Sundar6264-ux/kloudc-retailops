"""Toast Analytics client (services/toast_analytics.py) and the sales accuracy
re-pull (services/sales_accuracy.py), contract item 2. Every HTTP call is a
mocked httpx transport; the Orders API fetch is monkeypatched. No real Toast.
"""

import datetime as dt
import json
import threading

import httpx
import pytest

from app.config import get_settings
from app.models import JobRun, Order
from app.pos.adapters.toast import analytics as toast_analytics
from app.services import open_orders, sales_accuracy, sales_sync

SECRET = "s3cr3t-value-never-shown"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def analytics(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "client-id")
    monkeypatch.setattr(s, "toast_analytics_client_secret", SECRET)
    monkeypatch.setattr(s, "toast_analytics_base", "https://analytics.test")
    monkeypatch.setattr(s, "toast_restaurant_guid", "rest-guid")
    clock = Clock()
    monkeypatch.setattr(toast_analytics, "_sleep", clock.sleep)
    monkeypatch.setattr(toast_analytics, "_now", clock.now)
    toast_analytics.reset_state()
    yield clock
    toast_analytics.reset_state()


class Script:
    """A mock Toast: answers each (method, path-prefix) from a queue."""

    def __init__(self):
        self.routes = []
        self.requests = []

    def on(self, method, path_prefix, *responses):
        self.routes.append([method, path_prefix, list(responses)])
        return self

    def handler(self, request):
        self.requests.append(request)
        for method, prefix, responses in self.routes:
            if request.method == method and request.url.path.startswith(prefix) and responses:
                spec = responses.pop(0) if len(responses) > 1 else responses[0]
                if callable(spec):
                    return spec(request)
                status, body, headers = (spec + ({},))[:3] if isinstance(spec, tuple) else (200, spec, {})
                return httpx.Response(status, json=body, headers=headers)
        return httpx.Response(599, json={"message": "unscripted"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def count(self, method, path_prefix):
        return sum(1 for r in self.requests if r.method == method and r.url.path.startswith(path_prefix))


LOGIN = ("POST", "/authentication/v1/authentication/login")


def login_ok(script):
    return script.on(*LOGIN, (200, {"token": {"accessToken": "tok", "expiresIn": 3600}}))


# --- client ------------------------------------------------------------------------------


def test_not_configured_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(get_settings(), "toast_analytics_client_id", "")
    assert toast_analytics.is_configured() is False
    with pytest.raises(toast_analytics.AnalyticsNotConfigured):
        toast_analytics.run_report("metrics", "day", {})


def test_create_poll_202_then_200_and_token_is_cached(analytics):
    script = login_ok(Script())
    script.on("POST", "/era/v1/metrics/day", (200, "guid-1"))
    script.on("GET", "/era/v1/metrics/guid-1", (202, {"status": "IN_PROGRESS"}), (202, {}), (200, [{"businessDate": "20261010"}]))
    with script.client() as c:
        rows = toast_analytics.run_report("metrics", "day", {"x": 1}, client=c)
        assert rows == [{"businessDate": "20261010"}]
        script.on("POST", "/era/v1/menu/day", (200, "guid-2"))
        script.on("GET", "/era/v1/menu/guid-2", (200, []))
        assert toast_analytics.run_report("menu", "day", {}, client=c) == []
    assert script.count(*LOGIN) == 1  # one token for both reports
    assert script.count("GET", "/era/v1/metrics/guid-1") == 3
    assert all(r.headers["Authorization"] == "Bearer tok" for r in script.requests if r.url.path.startswith("/era"))


@pytest.mark.parametrize("status", [404, 409])
def test_expired_or_unprocessable_guid_asks_for_a_new_one(analytics, status):
    script = login_ok(Script())
    script.on("POST", "/era/v1/menu/week", (200, "old"), (200, "new"))
    script.on("GET", "/era/v1/menu/old", (status, {"message": "The reportRequestGUID is invalid"}))
    script.on("GET", "/era/v1/menu/new", (200, [{"businessDate": "20261009"}]))
    with script.client() as c:
        assert toast_analytics.run_report("menu", "week", {}, client=c) == [{"businessDate": "20261009"}]
    assert script.count("POST", "/era/v1/menu/week") == 2


def test_gives_up_after_too_many_new_requests(analytics):
    script = login_ok(Script())
    script.on("POST", "/era/v1/menu/day", (200, "g"))
    script.on("GET", "/era/v1/menu/g", (409, {"message": "unable to process"}))
    with script.client() as c, pytest.raises(toast_analytics.AnalyticsReportFailed, match="could not be processed"):
        toast_analytics.run_report("menu", "day", {}, client=c, max_recreates=2)
    assert script.count("POST", "/era/v1/menu/day") == 3


def test_504_is_retried(analytics):
    script = login_ok(Script())
    script.on("POST", "/era/v1/metrics/day", (200, "g"))
    script.on("GET", "/era/v1/metrics/g", (504, {}), (200, [{"a": 1}]))
    with script.client() as c:
        assert toast_analytics.run_report("metrics", "day", {}, client=c) == [{"a": 1}]


def test_bad_request_reports_toasts_message(analytics):
    script = login_ok(Script())
    script.on("POST", "/era/v1/metrics/day", (400, {"message": "Future dates are not allowed"}))
    with script.client() as c, pytest.raises(toast_analytics.AnalyticsReportFailed, match="Future dates"):
        toast_analytics.run_report("metrics", "day", {}, client=c)


def test_429_waits_retry_after(analytics):
    clock = analytics
    script = login_ok(Script())
    script.on("POST", "/era/v1/metrics/day", (429, {}, {"Retry-After": "17"}), (200, "g"))
    script.on("GET", "/era/v1/metrics/g", (200, []))
    with script.client() as c:
        toast_analytics.run_report("metrics", "day", {}, client=c)
    assert 17.0 in clock.sleeps


def test_ratelimit_headers_hold_the_endpoint_until_reset(analytics):
    clock = analytics
    reset_at = clock.now() + 40
    script = login_ok(Script())
    script.on(
        "POST",
        "/era/v1/metrics/day",
        (200, "g1", {"x-toast-ratelimit-remaining": "0", "x-toast-ratelimit-reset": str(int(reset_at))}),
        (200, "g2"),
    )
    script.on("GET", "/era/v1/metrics/", (200, []))
    with script.client() as c:
        toast_analytics.run_report("metrics", "day", {}, client=c)
        before = clock.now()
        toast_analytics.run_report("metrics", "day", {}, client=c)
    assert clock.now() - before >= 30  # waited for the reset before the 2nd POST


def test_own_budget_spaces_day_and_week_posts(analytics):
    """Toast allows 10 day/week report POSTs a minute: the 10th waits."""
    clock = analytics
    for _ in range(9):
        toast_analytics._wait_for_budget("POST", "/era/v1/metrics/day", max_wait=0)
    t0 = clock.now()
    toast_analytics._wait_for_budget("POST", "/era/v1/metrics/day", max_wait=120)
    assert clock.now() - t0 >= 59
    # A different endpoint has its own budget.
    toast_analytics._wait_for_budget("POST", "/era/v1/menu/day", max_wait=0)


def test_budget_refuses_a_long_wait(analytics):
    for _ in range(9):
        toast_analytics._wait_for_budget("POST", "/era/v1/metrics", max_wait=0)  # custom: 10/hour
    with pytest.raises(toast_analytics.AnalyticsRateLimited):
        toast_analytics._wait_for_budget("POST", "/era/v1/metrics", max_wait=60)


def test_check_reports_have_a_daily_budget():
    assert toast_analytics._family("POST", "/era/v1/check/day") == "post_daily"
    assert toast_analytics._family("POST", "/era/v1/guest/payments/week") == "post_daily"
    assert toast_analytics._family("POST", "/era/v1/menu/week") == "post_dayweek"
    assert toast_analytics._family("POST", "/era/v1/menu") == "post_long"
    assert toast_analytics._family("GET", "/era/v1/menu/abc") == "get"


def test_401_logs_in_again_once(analytics):
    script = Script()
    script.on(*LOGIN, (200, {"token": {"accessToken": "old", "expiresIn": 3600}}), (200, {"token": {"accessToken": "new", "expiresIn": 3600}}))
    script.on("POST", "/era/v1/metrics/day", lambda r: httpx.Response(401 if r.headers["Authorization"] == "Bearer old" else 200, json="g"))
    script.on("GET", "/era/v1/metrics/g", (200, []))
    with script.client() as c:
        toast_analytics.run_report("metrics", "day", {}, client=c)
    assert script.count(*LOGIN) == 2


def test_login_failure_never_leaks_the_secret(analytics):
    script = Script().on(*LOGIN, (401, {"message": f"bad secret {SECRET}"}))
    with script.client() as c, pytest.raises(toast_analytics.AnalyticsError) as exc:
        toast_analytics.run_report("metrics", "day", {}, client=c)
    assert SECRET not in str(exc.value)
    body = json.loads(script.requests[0].content)
    assert body["userAccessType"] == "TOAST_MACHINE_CLIENT" and body["clientId"] == "client-id"


def test_day_helpers_use_week_chunks_and_sum_per_day(analytics, monkeypatch):
    calls = []

    def fake_run(kind, time_range, body, params=None, **kw):
        calls.append((kind, time_range, body["startBusinessDate"], body["endBusinessDate"], body.get("groupBy")))
        start = dt.datetime.strptime(body["startBusinessDate"], "%Y%m%d").date()
        end = dt.datetime.strptime(body["endBusinessDate"], "%Y%m%d").date()
        rows = []
        d = start
        while d <= end:
            rows.append({"businessDate": d.strftime("%Y%m%d"), "netSalesAmount": 10.0, "quantitySold": 2})
            rows.append({"businessDate": d.strftime("%Y%m%d"), "netSalesAmount": 0.5, "quantitySold": 1})
            d += dt.timedelta(days=1)
        return rows

    monkeypatch.setattr(toast_analytics, "run_report", fake_run)
    out = toast_analytics.menu_day_totals(dt.date(2026, 9, 27), dt.date(2026, 10, 10))
    assert [c[1] for c in calls] == ["week", "week"]
    assert calls[0][2:4] == ("20260927", "20261003") and calls[1][2:4] == ("20261004", "20261010")
    assert body_restaurant_ok(analytics)
    assert out[dt.date(2026, 10, 10)]["netSalesAmount"] == 10.5
    assert out[dt.date(2026, 10, 10)]["quantitySold"] == 3
    assert len(out) == 14
    calls.clear()
    toast_analytics.day_metrics(dt.date(2026, 10, 10), dt.date(2026, 10, 10))
    assert calls == [("metrics", "day", "20261010", "20261010", None)]


def body_restaurant_ok(_clock):
    body = toast_analytics._body(dt.date(2026, 10, 1), dt.date(2026, 10, 2), ["MENU_ITEM"])
    return body["restaurantIds"] == ["rest-guid"] and body["excludedRestaurantIds"] == [] and body["groupBy"] == ["MENU_ITEM"]


# --- the re-pull job ---------------------------------------------------------------------

TODAY = dt.date(2026, 10, 11)
D9 = dt.date(2026, 10, 9)
D10 = dt.date(2026, 10, 10)
PAID = "2026-10-10T18:00:00.000+0000"


def _order(guid, sels, paid=PAID):
    return {
        "guid": guid,
        "openedDate": PAID,
        "createdDate": PAID,
        "paidDate": paid,
        "checks": [{"guid": "c-" + guid, "paidDate": paid, "selections": sels}],
    }


def _sel(guid, price, qty=1.0, voided=False, modifiers=()):
    return {"guid": guid, "item": {"guid": "i-" + guid}, "displayName": guid, "price": price, "quantity": qty,
            "tax": 0, "voided": voided, "modifiers": list(modifiers)}


@pytest.fixture
def repull(monkeypatch, analytics):
    """Toast orders (mock) + Analytics day totals (mock) for the job."""
    state = {
        "orders": {},
        "menu": {},
        "metrics": {},
        "fetches": [],
        "analytics_calls": 0,
    }

    def fake_fetch(d):
        state["fetches"].append(d)
        return state["orders"].get(d, [])

    def fake_menu(start, end):
        state["analytics_calls"] += 1
        return {d: v for d, v in state["menu"].items() if start <= d <= end}

    def fake_metrics(start, end):
        return {d: v for d, v in state["metrics"].items() if start <= d <= end}

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", fake_fetch)
    monkeypatch.setattr(toast_analytics, "menu_day_totals", fake_menu)
    monkeypatch.setattr(toast_analytics, "day_metrics", fake_metrics)
    monkeypatch.setattr(sales_accuracy, "_sleep", lambda s: None)
    return state


def _set_toast(state, day, orders, item_net, qty, n_orders=None):
    state["orders"][day] = orders
    state["menu"][day] = {"netSalesAmount": item_net, "quantitySold": qty}
    state["metrics"][day] = {"netSalesAmount": item_net, "ordersCount": n_orders if n_orders is not None else len(orders)}


def _checks(db, day):
    return [json.loads(r.detail) for r in db.query(JobRun).filter_by(job_name="sales_accuracy", business_date=day).order_by(JobRun.id)]


def test_repull_without_analytics_runs_the_recheck_not_the_comparison(db, monkeypatch):
    # Pre-production 2026-10-21: no Analytics keys no longer means "do
    # nothing" (tests/test_sales_recheck_no_analytics.py has the details).
    monkeypatch.setattr(get_settings(), "toast_analytics_client_id", "")
    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", lambda d: [])
    job = sales_accuracy.run_sales_repull(db, days=3, today=TODAY)
    assert job is not None and job.job_name == "sales_recheck"
    assert db.query(JobRun).filter_by(job_name="sales_accuracy").count() == 0


def test_repull_resyncs_only_the_changed_day(db, repull):
    # Both days synced last night.
    _set_toast(repull, D9, [_order("a", [_sel("a1", 10.0), _sel("a2", 5.0)])], 15.0, 2)
    _set_toast(repull, D10, [_order("b", [_sel("b1", 100.0, modifiers=[_sel("b1m", 20.0)]), _sel("b2", 50.0)])], 150.0, 2)
    sales_sync.sync_sales_for_date(db, D9)
    sales_sync.sync_sales_for_date(db, D10)
    # This morning b2 was voided in Toast; Analytics already shows it.
    repull["orders"][D10] = [_order("b", [_sel("b1", 100.0, modifiers=[_sel("b1m", 20.0)]), _sel("b2", 50.0, voided=True)])]
    repull["menu"][D10] = {"netSalesAmount": 100.0, "quantitySold": 1}
    repull["fetches"].clear()

    summary = sales_accuracy.run_sales_repull(db, days=2, today=TODAY)

    assert summary.status == "success"
    assert repull["fetches"] == [D10]  # D9 matched: never re-fetched
    d9, d10 = _checks(db, D9)[-1], _checks(db, D10)[-1]
    assert d9["verdict"] == "match" and d9["resynced"] is False and d9["reasons"] == []
    assert d10["verdict"] == "fixed" and d10["resynced"] is True
    assert "item_net" in d10["reasons"] and "item_qty" in d10["reasons"]
    assert d10["stored_before"]["net"] == 150.0 and d10["stored_after"]["net"] == 100.0
    assert d10["diff_before"] == 50.0 and d10["diff_after"] == 0.0
    assert "1 fixed" in summary.detail and "1 re-synced" in summary.detail
    # Stored modifiers are never part of the comparison (b1m's $20 is inside b1).
    assert db.query(Order).filter_by(toast_selection_guid="b1m").one().net_price == 20.0

    # Next run, nothing changed anywhere: no Toast order fetch at all.
    repull["fetches"].clear()
    sales_accuracy.run_sales_repull(db, days=2, today=TODAY)
    assert repull["fetches"] == []
    assert _checks(db, D10)[-1]["verdict"] == "match"


def test_lagging_analytics_never_makes_us_hammer_toast(db, repull):
    _set_toast(repull, D10, [_order("b", [_sel("b1", 100.0)])], 100.0, 1)
    sales_sync.sync_sales_for_date(db, D10)
    # Toast Analytics hasn't caught up with an edit we already have.
    repull["menu"][D10] = {"netSalesAmount": 130.0, "quantitySold": 2}
    repull["fetches"].clear()
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert repull["fetches"] == [D10]
    assert _checks(db, D10)[-1]["verdict"] == "mismatch"
    # Same Toast numbers, same stored data: wait, don't re-sync again.
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert repull["fetches"] == [D10]
    last = _checks(db, D10)[-1]
    assert last["waiting"] is True and last["verdict"] == "mismatch"
    # Analytics moves (caught up / another edit): re-check right away.
    repull["menu"][D10] = {"netSalesAmount": 100.0, "quantitySold": 1}
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert repull["fetches"] == [D10, D10]
    last = _checks(db, D10)[-1]
    assert "analytics_changed" in last["reasons"] and last["verdict"] == "match"


def test_order_count_difference_is_rechecked_once_but_not_a_mismatch(db, repull):
    _set_toast(repull, D10, [_order("b", [_sel("b1", 100.0)])], 100.0, 1, n_orders=2)
    sales_sync.sync_sales_for_date(db, D10)
    repull["fetches"].clear()
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    first = _checks(db, D10)[-1]
    assert first["reasons"] == ["orders"] and first["resynced"] is True and first["verdict"] == "match"
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert repull["fetches"] == [D10]


def test_unpaid_rows_are_rechecked_after_a_while(db, repull):
    unpaid = _order("u", [_sel("u1", 40.0)], paid=None)
    _set_toast(repull, D10, [unpaid], 40.0, 1)
    sales_sync.sync_sales_for_date(db, D10)
    assert db.query(Order).filter_by(toast_selection_guid="u1").one().paid_at is None
    repull["fetches"].clear()
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert repull["fetches"] == []  # synced moments ago
    job = db.query(JobRun).filter_by(job_name=sales_sync.JOB_NAME).one()
    job.finished_at = job.finished_at - dt.timedelta(hours=7)
    db.commit()
    repull["orders"][D10] = [_order("u", [_sel("u1", 40.0)])]  # paid now
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert repull["fetches"] == [D10]
    assert "unpaid" in _checks(db, D10)[-1]["reasons"]
    db.expire_all()
    assert db.query(Order).filter_by(toast_selection_guid="u1").one().paid_at is not None


def test_never_synced_day_is_synced(db, repull):
    _set_toast(repull, D10, [_order("b", [_sel("b1", 100.0)])], 100.0, 1)
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    last = _checks(db, D10)[-1]
    assert "never_synced" in last["reasons"] and last["verdict"] == "fixed"


def test_failed_resync_is_recorded(db, repull, monkeypatch):
    _set_toast(repull, D10, [_order("b", [_sel("b1", 100.0)])], 120.0, 1)
    sales_sync.sync_sales_for_date(db, D10)

    def boom(d):
        raise RuntimeError("Toast is down")

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", boom)
    summary = sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert summary.status == "failed" and "1 failed" in summary.detail
    last = _checks(db, D10)[-1]
    assert last["resync_status"] == "failed" and last["verdict"] == "mismatch"


def test_analytics_failure_is_recorded_not_raised(db, repull, monkeypatch):
    def down(start, end):
        raise toast_analytics.AnalyticsRateLimited("next call in 600s")

    monkeypatch.setattr(toast_analytics, "menu_day_totals", down)
    summary = sales_accuracy.run_sales_repull(db, days=3, today=TODAY)
    assert summary.status == "failed" and "next call in 600s" in summary.detail
    assert db.query(JobRun).filter_by(job_name="sales_accuracy").count() == 0


def test_one_run_at_a_time(db, repull):
    sales_accuracy._RUN_LOCK.acquire()
    try:
        assert sales_accuracy.run_sales_repull(db, days=1, today=TODAY) is None
    finally:
        sales_accuracy._RUN_LOCK.release()


def test_window_is_yesterday_back_and_never_before_real_sales(monkeypatch):
    from app.config import get_settings

    assert sales_accuracy._window(14, TODAY) == (dt.date(2026, 9, 27), D10)
    assert sales_accuracy._window(500, TODAY)[0] == TODAY - dt.timedelta(days=sales_accuracy.MAX_DAYS)
    # No FIRST_SALES_DATE: no floor.
    assert sales_accuracy._window(30, dt.date(2026, 6, 1))[0] == dt.date(2026, 5, 2)
    monkeypatch.setattr(get_settings(), "first_sales_date", "2026-05-26")
    assert sales_accuracy._window(30, dt.date(2026, 6, 1))[0] == sales_accuracy.FIRST_REAL_SALES_DATE == dt.date(2026, 5, 26)


# --- endpoints -------------------------------------------------------------------------------


def test_accuracy_report_is_admin_only(db, repull, admin_client, staff_client):
    _set_toast(repull, D10, [_order("b", [_sel("b1", 100.0)])], 100.0, 1)
    sales_sync.sync_sales_for_date(db, D10)
    sales_accuracy.run_sales_repull(db, days=1, today=TODAY)
    assert staff_client.get("/api/sales/accuracy").status_code == 403
    r = admin_client.get("/api/sales/accuracy?days=120")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["running"] is False
    row = next(d for d in body["days"] if d["business_date"] == "2026-10-10")
    assert row["verdict"] == "match" and row["toast_item_net"] == 100.0 and row["ours_before"] == 100.0
    assert body["runs"][0]["status"] == "success"


def test_run_now_needs_analytics(admin_client, staff_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "toast_analytics_client_id", "")
    assert staff_client.post("/api/sales/accuracy/run").status_code == 403
    r = admin_client.post("/api/sales/accuracy/run")
    assert r.status_code == 409 and "not set up" in r.json()["detail"]


def test_run_now_starts_in_the_background(admin_client, analytics, monkeypatch):
    ran = threading.Event()
    seen = {}

    def fake_run(db, days=None):
        seen["days"] = days
        ran.set()

    monkeypatch.setattr(sales_accuracy, "run_sales_repull", fake_run)
    r = admin_client.post("/api/sales/accuracy/run?days=30")
    assert r.status_code == 202 and r.json() == {"started": True}
    assert ran.wait(5) and seen["days"] == 30
    assert admin_client.post("/api/sales/accuracy/run?days=999").status_code == 422


def test_items_sold_fresh_param_is_accepted(admin_client, monkeypatch):
    seen = {}

    def fake(db, start, end, fresh=False):
        seen["fresh"] = fresh
        return {"items": [], "start_date": str(start), "end_date": str(end)}

    import app.routers.sales as sales_router

    monkeypatch.setattr(sales_router, "get_items_sold", fake)
    assert admin_client.get("/api/sales/items-sold?start=2026-10-01&fresh=true").status_code == 200
    assert seen["fresh"] is True


# --- scheduler wiring -------------------------------------------------------------------------


def test_scheduler_registers_the_repull_times(monkeypatch):
    import app.scheduler as sched

    added = []

    class FakeScheduler:
        def __init__(self, **kw):
            pass

        def add_job(self, func, trigger=None, id=None, **kw):
            added.append((id, func))

        def start(self):
            pass

        def shutdown(self, wait=False):
            pass

    s = get_settings()
    monkeypatch.setattr(s, "scheduler_enabled", True)
    monkeypatch.setattr(s, "sales_repull_times", "06:30, 12:30")
    monkeypatch.setattr(sched, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(sched, "_scheduler", None)
    sched.start_scheduler()
    ids = {i for i, _ in added}
    assert {"sales_repull_0630", "sales_repull_1230"} <= ids
    assert dict(added)["sales_repull_0630"] is sched.run_sales_repull_job
    added.clear()
    monkeypatch.setattr(sched, "_scheduler", None)
    monkeypatch.setattr(s, "sales_repull_enabled", False)
    sched.start_scheduler()
    assert not any(i.startswith("sales_repull") for i, _ in added)
    monkeypatch.setattr(sched, "_scheduler", None)


# --- open checks: live on request ------------------------------------------------------------


def test_open_checks_refresh_today_on_request_only_in_production(db, monkeypatch):
    calls = []

    def fake_refresh(db_, full=False, max_retries=4):
        calls.append(full)
        job = JobRun(job_name=open_orders.JOB_NAME, status="success")
        db_.add(job)
        db_.commit()
        return job

    monkeypatch.setattr(open_orders, "refresh_open_orders_cache", fake_refresh)
    s = get_settings()
    # Tests / dev: scheduler off -> never calls Toast.
    assert open_orders.ensure_today_fresh(db) is False and calls == []
    monkeypatch.setattr(s, "scheduler_enabled", True)
    monkeypatch.setattr(s, "toast_client_id", "x")
    assert open_orders.ensure_today_fresh(db) is True and calls == [False]
    # A scan seconds ago is fresh enough.
    assert open_orders.ensure_today_fresh(db) is False and calls == [False]
    old = db.query(JobRun).filter_by(job_name=open_orders.JOB_NAME).one()
    old.started_at = old.started_at - dt.timedelta(minutes=5)
    db.commit()
    assert open_orders.ensure_today_fresh(db) is True and calls == [False, False]
