"""Adversarial backend review of the automation batch (verify 2): Toast being
down, slow, rate limiting or answering short must never tie up the server,
hammer Toast, or become stored data. Every Toast call is mocked; nothing here
reaches the network (the inventory module's autouse guard fails any real
HTTP)."""

import datetime as dt
import json
import threading
import time
import zoneinfo

import httpx
import pytest

from app.config import get_settings
from app.models import InventorySnapshot, JobRun
from app.pos.adapters.toast import catalog as toast_catalog
from app.pos.adapters.toast import client as toast_http
from app.pos.adapters.toast import orders as toast_orders
from app.pos.adapters.toast import stock as toast_stock
from app.services import inventory_api as ia
from app.services import items_sold as items_sold_mod
from app.services import open_orders
from app.services import sales_accuracy
from app.services import sales_sync
from app.services.items_sold import LiveSalesUnavailable, get_items_sold
from tests.test_inventory_api import (  # noqa: F401  (fixtures: _isolate, store, toast)
    NEG,
    RICE,
    TODAY,
    _api_rows_on,
    _isolate,
    csv_row,
    stock_json,
    store,
    toast,
)

DAY = dt.date(2026, 10, 10)
ET = zoneinfo.ZoneInfo("America/New_York")


def _http_error(status: int, headers=None) -> httpx.HTTPStatusError:
    resp = httpx.Response(status, headers=headers or {}, request=httpx.Request("GET", "https://toast.example"))
    return httpx.HTTPStatusError(str(status), request=resp.request, response=resp)


@pytest.fixture
def live_sales(monkeypatch):
    """Toast Orders API fake for the live Items Sold path."""
    state = {"calls": [], "fail": None, "orders": []}

    def fake_fetch(business_date):
        state["calls"].append(business_date)
        if state["fail"] is not None:
            raise state["fail"]
        return list(state["orders"])

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", fake_fetch)
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: DAY)
    items_sold_mod.clear_live_cache()
    yield state
    items_sold_mod.clear_live_cache()


# --- Items Sold live: a failing Toast fails fast, never queues requests ----------------------


def test_live_sales_failure_is_remembered_briefly_then_retried(db, live_sales, monkeypatch):
    live_sales["fail"] = _http_error(500)
    with pytest.raises(httpx.HTTPStatusError):
        get_items_sold(db, DAY, DAY)
    calls = len(live_sales["calls"])
    assert calls == 1  # the first date failed; nothing else asked
    # Within the back-off: no Toast call at all, a clear error instead.
    with pytest.raises(LiveSalesUnavailable):
        get_items_sold(db, DAY, DAY)
    with pytest.raises(LiveSalesUnavailable):
        get_items_sold(db, DAY, DAY, fresh=True)  # Refresh doesn't bypass it either
    assert len(live_sales["calls"]) == calls
    # Back-off over and Toast back: it works again.
    monkeypatch.setattr(items_sold_mod, "LIVE_FAILURE_BACKOFF_SECONDS", 0)
    live_sales["fail"] = None
    result = get_items_sold(db, DAY, DAY)
    assert result["source"] == "live" and len(live_sales["calls"]) == calls + 3


def test_live_sales_429_waits_at_most_a_few_seconds(db, live_sales, monkeypatch):
    sleeps = []
    monkeypatch.setattr(items_sold_mod.time, "sleep", sleeps.append)
    live_sales["fail"] = _http_error(429, {"Retry-After": "120"})
    with pytest.raises(httpx.HTTPStatusError):
        get_items_sold(db, DAY, DAY)
    # One retry, and the request never sleeps Toast's full 2 minutes.
    assert len(live_sales["calls"]) == 2
    assert sleeps and max(sleeps) <= items_sold_mod.LIVE_MAX_RETRY_WAIT_SECONDS


def test_a_slow_pull_of_one_date_doesnt_block_other_requests(db, live_sales, monkeypatch):
    monkeypatch.setattr(items_sold_mod, "LIVE_LOCK_WAIT_SECONDS", 0.05)
    with items_sold_mod._LIVE_LOCK:
        lock = items_sold_mod._DATE_LOCKS.setdefault(DAY, threading.Lock())
    lock.acquire()  # someone else is mid-pull for DAY (Toast hanging)
    try:
        started = time.monotonic()
        with pytest.raises(LiveSalesUnavailable):
            items_sold_mod._live_line_items(DAY)
        assert time.monotonic() - started < 2
        # Another business date isn't held up by it.
        items, _ = items_sold_mod._live_line_items(DAY - dt.timedelta(days=1))
        assert items == []
    finally:
        lock.release()


def test_items_sold_route_answers_503_when_toast_is_down(admin_client, live_sales):
    live_sales["fail"] = httpx.ConnectTimeout("timed out")
    r = admin_client.get("/api/sales/items-sold", params={"start": DAY.isoformat()})
    assert r.status_code == 503 and "Toast" in r.json()["detail"]
    r = admin_client.get("/api/sales/items-sold", params={"start": DAY.isoformat()})
    assert r.status_code == 503
    # A past range never touches Toast and still works.
    r = admin_client.get("/api/sales/items-sold", params={"start": "2026-10-01", "end": "2026-10-02"})
    assert r.status_code == 200 and r.json()["source"] == "stored"


# --- Live stock overlay: never waits long, never blocks the status ---------------------------


def test_live_stock_uses_a_short_timeout_and_no_429_sleeps(store, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    seen = []

    def fake(path, timeout=90.0, max_tries=4):
        seen.append((path, timeout, max_tries))
        return stock_json()

    monkeypatch.setattr(toast_http, "toast_get", fake)
    assert ia.get_live_stock() is not None
    assert seen == [(toast_stock.STOCK_PATH, toast_stock.LIVE_STOCK_TIMEOUT_SECONDS, 1)]


def test_live_stock_failure_backs_off_and_falls_back(store, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    calls = []

    def boom(path, timeout=90.0, max_tries=4):
        calls.append(path)
        raise toast_http.ToastFetchError("Toast /stock/v1/inventory: ReadTimeout")

    monkeypatch.setattr(toast_http, "toast_get", boom)
    latest = {RICE: object()}
    assert ia.overlay_live_stock(latest) is latest
    assert ia.overlay_live_stock(latest) is latest
    assert calls == [toast_stock.STOCK_PATH]  # the second page view didn't ask Toast again
    assert ia.live_stock_meta() == {"live": False, "as_of": None}


def test_a_hung_stock_fetch_never_blocks_pages_or_the_status(store, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    monkeypatch.setattr(ia, "LIVE_FETCH_WAIT_SECONDS", 0.05)
    ia._live_fetch_lock.acquire()  # another request is stuck asking Toast
    try:
        started = time.monotonic()
        assert ia.get_live_stock(lambda: pytest.fail("must not fetch twice at once")) is None
        assert ia.live_stock_meta() == {"live": False, "as_of": None}
        assert time.monotonic() - started < 2
    finally:
        ia._live_fetch_lock.release()


def test_toast_get_logs_in_again_once_on_401(monkeypatch):
    from app.pos.adapters.toast import client as toast_client

    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id")
    monkeypatch.setattr(s, "toast_client_secret", "secret")
    monkeypatch.setattr(s, "toast_restaurant_guid", "guid")
    tokens = iter(["old", "new"])
    monkeypatch.setattr(toast_client, "get_access_token", lambda client: next(tokens))
    seen = []

    def fake_get(self, url, headers=None, timeout=None):
        seen.append(headers["Authorization"])
        status = 401 if headers["Authorization"] == "Bearer old" else 200
        return httpx.Response(status, json=[{"guid": "a"}], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert toast_http.toast_get(toast_stock.STOCK_PATH) == [{"guid": "a"}]
    assert seen == ["Bearer old", "Bearer new"]


def test_toast_get_turns_network_errors_into_a_plain_fetch_error(monkeypatch):
    from app.pos.adapters.toast import client as toast_client

    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id")
    monkeypatch.setattr(s, "toast_client_secret", "top-secret-value")
    monkeypatch.setattr(s, "toast_restaurant_guid", "guid")
    monkeypatch.setattr(toast_client, "get_access_token", lambda client: "tok")

    def hang(self, url, headers=None, timeout=None):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx.Client, "get", hang)
    with pytest.raises(toast_http.ToastFetchError) as err:
        toast_http.toast_get(toast_stock.STOCK_PATH)
    assert "ReadTimeout" in str(err.value) and "top-secret-value" not in str(err.value)


# --- API snapshot: a short Toast answer is never stored ---------------------------------------


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    monkeypatch.setattr(ia, "_after_new_snapshot", lambda: None)


def test_empty_stock_list_is_not_written(store, toast, switch_on):
    toast.stock = []
    job = ia.run_api_snapshot(store)
    assert job.status == "failed"
    stored = json.loads(job.detail)["stored"]
    assert stored["skipped"] == "implausible" and "empty" in stored["reason"]
    assert _api_rows_on(store, TODAY) == 0


def test_cut_short_stock_list_is_not_written(store, toast, switch_on):
    toast.stock = [r for r in stock_json() if r["guid"] == RICE]  # 1 counted item vs 6 in the CSV
    job = ia.run_api_snapshot(store)
    assert job.status == "failed"
    assert json.loads(job.detail)["stored"]["skipped"] == "implausible"
    assert _api_rows_on(store, TODAY) == 0
    # Yesterday's CSV is still the latest snapshot for every item.
    from app.services.reorder import latest_inventory_by_item

    assert latest_inventory_by_item(store)[RICE].snapshot_date == TODAY - dt.timedelta(days=1)


def test_a_full_answer_is_still_written(store, toast, switch_on):
    job = ia.run_api_snapshot(store)
    assert job.status == "success" and _api_rows_on(store, TODAY) > 0


def test_csv_landing_mid_write_wins(store, toast, switch_on, monkeypatch):
    """A CSV upload committing between the job's CSV check and its write: the
    second check, under the write lock, keeps the API rows out."""
    checks = iter([False, True])
    monkeypatch.setattr(ia, "has_csv_for", lambda db, day: next(checks))
    rows, _meta = ia.build_api_rows(store, stock_json(), toast_catalog.fetch_menus(), TODAY)
    assert ia.store_api_snapshot(store, rows, TODAY) == {"written": 0, "skipped": "csv_exists"}
    assert _api_rows_on(store, TODAY) == 0


# --- Open checks on request: a failure isn't retried on every page view ----------------------


def test_open_checks_refresh_backs_off_after_a_failure(db, monkeypatch):
    calls = []

    def failing_refresh(db_, full=False, max_retries=4):
        calls.append(max_retries)
        job = JobRun(job_name=open_orders.JOB_NAME, status="failed", detail="429")
        db_.add(job)
        db_.commit()
        return job

    monkeypatch.setattr(open_orders, "refresh_open_orders_cache", failing_refresh)
    monkeypatch.setitem(open_orders._live_last_failure, "at", None)
    s = get_settings()
    monkeypatch.setattr(s, "scheduler_enabled", True)
    monkeypatch.setattr(s, "toast_client_id", "x")
    assert open_orders.ensure_today_fresh(db) is False
    assert open_orders.ensure_today_fresh(db) is False
    assert calls == [0]  # one Toast attempt, with no 429 sleeps on the request
    open_orders._live_last_failure["at"] = time.monotonic() - 3600
    open_orders.ensure_today_fresh(db)
    assert calls == [0, 0]


# --- Re-pull window: never a still-open business date ----------------------------------------


@pytest.mark.parametrize(
    "local, expected",
    [
        (dt.datetime(2026, 10, 11, 3, 59, tzinfo=ET), dt.date(2026, 10, 10)),
        (dt.datetime(2026, 10, 11, 4, 0, tzinfo=ET), dt.date(2026, 10, 11)),
        (dt.datetime(2026, 10, 11, 22, 30, tzinfo=ET), dt.date(2026, 10, 11)),
        # DST ends Nov 1 2026 at 2 AM: 1:30 happens twice, both before closeout.
        (dt.datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=ET), dt.date(2026, 10, 31)),
        (dt.datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ET), dt.date(2026, 10, 31)),
        (dt.datetime(2026, 11, 1, 6, 30, tzinfo=ET), dt.date(2026, 11, 1)),
    ],
)
def test_repull_today_respects_the_4am_closeout(local, expected):
    assert sales_accuracy.closed_business_today(local.astimezone(dt.timezone.utc)) == expected


def test_repull_run_before_closeout_skips_the_open_business_date(db, monkeypatch):
    monkeypatch.setattr(sales_accuracy.toast_analytics, "is_configured", lambda: True)
    seen = {}

    def fake_menu(start, end):
        seen["end"] = end
        return {}

    monkeypatch.setattr(sales_accuracy.toast_analytics, "menu_day_totals", fake_menu)
    monkeypatch.setattr(sales_accuracy.toast_analytics, "day_metrics", lambda s, e: {})
    one_am = dt.datetime(2026, 10, 11, 1, 0, tzinfo=ET).astimezone(dt.timezone.utc)
    monkeypatch.setattr(sales_accuracy, "utcnow", lambda: one_am)
    job = sales_accuracy.run_sales_repull(db, days=3, resync=lambda d, day: pytest.fail("nothing to re-sync"))
    assert job.status == "success"
    assert seen["end"] == dt.date(2026, 10, 9)  # Oct 10 is still open until 4 AM Oct 11


# --- Standard Toast client: a revoked / early-expired token logs in again once ---------------


def test_orders_fetch_logs_in_again_once_on_401(monkeypatch):
    from app.pos.adapters.toast import client as toast_client

    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id")
    monkeypatch.setattr(s, "toast_client_secret", "secret")
    monkeypatch.setattr(s, "toast_restaurant_guid", "guid")
    monkeypatch.setitem(toast_client._TOKEN_CACHE, "id", ("stale", time.time() + 3600))
    logins = []

    def fake_post(self, url, json=None, timeout=None):
        logins.append(url)
        return httpx.Response(200, json={"token": {"accessToken": "fresh", "expiresIn": 3600}}, request=httpx.Request("POST", url))

    seen = []

    def fake_get(self, url, headers=None, params=None, timeout=None):
        seen.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer stale":
            return httpx.Response(401, request=httpx.Request("GET", url))
        return httpx.Response(200, json=[{"guid": "o-1"}], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert toast_orders.fetch_orders_for_business_date(DAY) == [{"guid": "o-1"}]
    assert seen == ["Bearer stale", "Bearer fresh"] and len(logins) == 1


def test_orders_fetch_gives_up_on_a_second_401(monkeypatch):
    from app.pos.adapters.toast import client as toast_client

    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id")
    monkeypatch.setattr(s, "toast_client_secret", "secret")
    monkeypatch.setattr(toast_client, "get_access_token", lambda client: "tok")

    def always_401(self, url, headers=None, params=None, timeout=None):
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", always_401)
    with pytest.raises(httpx.HTTPStatusError):
        toast_orders.fetch_orders_for_business_date(DAY)


# --- Barcode lists between uploads: the menu must itself show the problem --------------------


def _invalid_second_code_store(db):
    from tests.test_inventory_api import BAD, BARCODE_CSV_DAY, GOOD

    # Fixed day before the fixed Oct 11 close / menu times (see BARCODE_CSV_DAY).
    csv_row(db, RICE, "Rice 20lb", 30, day=BARCODE_CSV_DAY, barcode=f"{GOOD};{BAD}")  # bad 2nd code; the menu's sku is the 1st
    db.commit()


def test_fixed_invalid_second_code_doesnt_come_back_on_a_menu_change(db, toast):
    from app.models import IssueResolution
    from app.services.fix_issues import get_issue_list
    from tests.test_inventory_api import GOOD, menus_json

    _invalid_second_code_store(db)
    db.add(
        IssueResolution(
            issue_type="invalid_barcode", issue_key=RICE, item_id=RICE, item_name="Rice 20lb",
            reason="fixed", closed_by_name="boss", closed_at=dt.datetime(2026, 10, 11, 18, 0, tzinfo=dt.timezone.utc),
        )
    )
    db.commit()
    toast.last_updated = "2026-10-11T19:00:00.000+0000"  # some later menu change
    toast.menus = menus_json(last_updated=toast.last_updated, rice_sku=GOOD)
    ia.refresh_menu_cache(db)
    data = get_issue_list(db, "invalid_barcode", today=dt.date(2026, 10, 11))
    # The menu only has the (good) first code: it can't show the bad 2nd one
    # is still there, so the close waits for the next upload to judge it.
    assert all(r["item_id"] != RICE for r in data["open"])
    assert any(r["issue_key"] == RICE for r in data["closed"])


def test_fixed_invalid_code_still_in_the_menu_comes_back(db, toast):
    from app.models import IssueResolution
    from app.services.fix_issues import get_issue_list
    from tests.test_inventory_api import BAD, menus_json

    _invalid_second_code_store(db)
    db.add(
        IssueResolution(
            issue_type="invalid_barcode", issue_key=RICE, item_id=RICE, item_name="Rice 20lb",
            reason="fixed", closed_by_name="boss", closed_at=dt.datetime(2026, 10, 11, 18, 0, tzinfo=dt.timezone.utc),
        )
    )
    db.commit()
    toast.last_updated = "2026-10-11T19:00:00.000+0000"
    toast.menus = menus_json(last_updated=toast.last_updated, rice_sku=BAD)
    ia.refresh_menu_cache(db)
    data = get_issue_list(db, "invalid_barcode", today=dt.date(2026, 10, 11))
    row = next(r for r in data["open"] if r["item_id"] == RICE)
    assert row["came_back"] and row["reopened"]["how"] == "fixed_but_still_showing"


def test_menu_shows_barcode_issue_rules(db, toast):
    from tests.test_inventory_api import BAD, GOOD, TEA, menus_json

    toast.menus = menus_json(tea_sku="", rice_sku=BAD)
    ia.refresh_menu_cache(db)
    assert ia.menu_shows_barcode_issue(db, "missing_barcode", TEA) is True
    assert ia.menu_shows_barcode_issue(db, "invalid_barcode", RICE) is True
    assert ia.menu_shows_barcode_issue(db, "missing_barcode", RICE) is False
    assert ia.menu_shows_barcode_issue(db, "missing_barcode", "not-on-any-menu") is False
    toast.last_updated = "2026-10-11T19:00:00.000+0000"
    toast.menus = menus_json(last_updated=toast.last_updated, rice_sku=GOOD)
    ia.refresh_menu_cache(db)
    assert ia.menu_shows_barcode_issue(db, "invalid_barcode", RICE) is False


# --- Re-sync: an order Toast moves to another business date is stored once --------------------


def _moved_order(guid="o-move"):
    return {
        "guid": guid,
        "openedDate": "2026-10-10T16:00:00.000+0000",
        "createdDate": "2026-10-10T16:00:00.000+0000",
        "paidDate": "2026-10-10T18:00:00.000+0000",
        "diningOption": {"guid": "dine-1"},
        "checks": [
            {
                "guid": "c-move",
                "paidDate": "2026-10-10T18:00:00.000+0000",
                "selections": [
                    {"guid": "s-move", "item": {"guid": "i-move"}, "displayName": "Moved", "price": 10.0, "quantity": 1.0, "tax": 0.0}
                ],
            }
        ],
    }


@pytest.mark.parametrize("order_of_syncs", ["new_date_first", "old_date_first"])
def test_order_moved_between_business_dates_is_stored_once(db, monkeypatch, order_of_syncs):
    from app.models import Order

    nxt = DAY + dt.timedelta(days=1)
    answers = {DAY: [_moved_order(), _moved_order("o-stays")], nxt: []}
    # o-stays keeps DAY non-empty (an empty answer never prunes a day).
    answers[DAY][1]["checks"][0]["guid"] = "c-stays"
    answers[DAY][1]["checks"][0]["selections"][0]["guid"] = "s-stays"
    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", lambda d: [dict(o) for o in answers[d]])
    assert sales_sync.sync_sales_for_date(db, DAY).status == "success"
    # Toast moves the order to the next business date.
    answers[nxt] = [answers[DAY][0]]
    answers[DAY] = [answers[DAY][1]]
    days = [nxt, DAY] if order_of_syncs == "new_date_first" else [DAY, nxt]
    for d in days:
        assert sales_sync.sync_sales_for_date(db, d).status == "success"
    rows = db.query(Order).filter(Order.toast_selection_guid == "s-move").all()
    assert len(rows) == 1 and rows[0].business_date == nxt
    assert db.query(Order).filter(Order.toast_selection_guid == "s-stays").one().business_date == DAY
