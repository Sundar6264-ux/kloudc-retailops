"""Items Sold / sales export / every other `orders` consumer count a modifier's
price once (contract item 3), the orders sync follows later Toast edits
(voided, deleted, removed selections), and today's live pull is shared through
a short cache. Toast is mocked throughout - nothing here calls Toast.

Fixture shape: the real 2026-10-10 day from the Analytics probe
(~/.claude/jobs/e26a1611/tmp/toast-analytics): top-level item net $6,490.26,
priced modifiers $567.55, and the app's old Items Sold total (every row,
modifiers included) $7,057.81. Toast's Analytics item report said $6,490.26.
"""

import csv
import datetime as dt
import io

import httpx
import pytest

from app.models import JobRun, Order
from app.services import items_sold as items_sold_mod
from app.services import sales_sync
from app.services.export import export_csv
from app.services.forecast import forecast_daily_demand
from app.services.items_sold import get_items_sold
from app.services.weekly_digest import _sales_totals
from app.services.weekly_sales import weekly_sales_for_item

DAY = dt.date(2026, 10, 10)
PAID = "2026-10-10T18:00:00.000+0000"  # 2 PM in New York, same day
TOP_LEVEL_NET = 6490.26
MODIFIER_NET = 567.55
OLD_ITEMS_SOLD_TOTAL = 7057.81


def sel(guid, item, name, price, qty=1.0, voided=False, modifiers=()):
    return {
        "guid": guid,
        "item": {"guid": item},
        "displayName": name,
        "price": price,
        "quantity": qty,
        "tax": 0.0,
        "voided": voided,
        "modifiers": list(modifiers),
    }


def check(guid, selections, voided=False, deleted=False, paid=PAID):
    return {"guid": guid, "paidDate": paid, "voided": voided, "deleted": deleted, "selections": selections}


def order(guid, checks, voided=False, deleted=False, paid=PAID):
    return {
        "guid": guid,
        "openedDate": "2026-10-10T16:00:00.000+0000",
        "createdDate": "2026-10-10T16:00:00.000+0000",
        "paidDate": paid,
        "voided": voided,
        "deleted": deleted,
        "diningOption": {"guid": "dine-1"},
        "checks": checks,
    }


def oct10_orders():
    """Raw Toast orders shaped like Oct 10: a catering tray whose $767.55
    price already includes $567.55 of priced add-ons (one nested a level
    deeper), real top sellers, a selection voided later, a deleted check."""
    tray = sel(
        "s-tray",
        "i-tray",
        "Goat Biryani Party Tray",
        767.55,
        modifiers=[
            sel("m-raita", "i-raita", "Extra Raita Tray", 300.00),
            sel("m-hot", "i-hot", "Spice: Hot", 0.0),
            sel(
                "m-salan",
                "i-salan",
                "Add Mirchi Ka Salan Tray",
                267.55,
                modifiers=[sel("m-large", "i-large", "Large", 0.0)],
            ),
        ],
    )
    return [
        order("o-1", [check("c-1", [tray])]),
        order(
            "o-2",
            [
                check(
                    "c-2",
                    [
                        sel("s-chicken", "i-chicken", "Whole Chicken Clean & Cut", 377.89, qty=95.07),
                        sel("s-cilantro", "i-cilantro", "Cilantro Bunch", 54.08, qty=55),
                        sel("s-thigh", "i-thigh", "Chicken Thigh Boneless", 204.33, qty=45.51),
                        sel("s-goat", "i-goat", "Goat Mix", 292.23, qty=22.83),
                        sel("s-garland", "i-garland", "Puja Garland Thick", 39.52, voided=True),
                    ],
                )
            ],
        ),
        order("o-3", [check("c-3", [sel("s-rest", "i-rest", "Rest of the day's groceries", 4794.18, qty=1214.45)])]),
        order("o-4", [check("c-4", [sel("s-del", "i-del", "Deleted check item", 100.00)], deleted=True)]),
    ]


@pytest.fixture
def toast(monkeypatch):
    """Mocked Toast Orders API: {business date: [raw orders]}; records calls."""
    state = {"orders": {}, "calls": []}

    def fake_fetch(business_date):
        state["calls"].append(business_date)
        return state["orders"].get(business_date, [])

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", fake_fetch)
    items_sold_mod.clear_live_cache()
    yield state
    items_sold_mod.clear_live_cache()


def _sync(db, toast_state, day=DAY):
    toast_state["orders"][day] = oct10_orders() if day == DAY and day not in toast_state["orders"] else toast_state["orders"][day]
    return sales_sync.sync_sales_for_date(db, day)


def _stored_top_level(db, day=DAY):
    rows = db.query(Order).filter(
        Order.business_date == day, Order.voided.is_(False), Order.parent_selection_guid.is_(None)
    )
    return round(sum(r.net_price for r in rows), 2)


# --- the bug, proven on the fixture ---------------------------------------------------


def test_fixture_reproduces_the_oct_10_double_count(db, toast):
    job = _sync(db, toast)
    assert job.status == "success"
    non_voided = db.query(Order).filter(Order.business_date == DAY, Order.voided.is_(False)).all()
    assert round(sum(r.net_price for r in non_voided), 2) == OLD_ITEMS_SOLD_TOTAL
    mods = [r for r in non_voided if r.parent_selection_guid]
    assert round(sum(r.net_price for r in mods), 2) == MODIFIER_NET
    # The nested modifier points at its own parent modifier, not the item.
    assert db.query(Order).filter_by(toast_selection_guid="m-large").one().parent_selection_guid == "m-salan"
    assert _stored_top_level(db) == TOP_LEVEL_NET


def test_items_sold_stored_counts_each_item_once(db, toast):
    _sync(db, toast)
    result = get_items_sold(db, DAY, DAY)
    assert result["source"] == "stored"
    assert result["total_revenue"] == TOP_LEVEL_NET
    names = {i["name"] for i in result["items"]}
    assert "Extra Raita Tray" not in names and "Spice: Hot" not in names and "Large" not in names
    tray = next(i for i in result["items"] if i["name"] == "Goat Biryani Party Tray")
    assert tray["revenue"] == 767.55 and tray["quantity"] == 1
    assert result["total_quantity"] == round(1 + 95.07 + 55 + 45.51 + 22.83 + 1214.45, 3)


def test_items_sold_live_counts_each_item_once(db, toast, monkeypatch):
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: DAY)
    toast["orders"][DAY] = oct10_orders()
    result = get_items_sold(db, DAY, DAY)
    assert result["source"] == "live"
    assert result["total_revenue"] == TOP_LEVEL_NET
    assert "live_fetched_at" in result
    # Yesterday, today and tomorrow's business dates are pulled.
    assert sorted(toast["calls"]) == [DAY - dt.timedelta(days=1), DAY, DAY + dt.timedelta(days=1)]


def test_sales_export_has_no_modifier_rows_and_adds_up(db, toast):
    _sync(db, toast)
    rows = list(csv.DictReader(io.StringIO(export_csv(db, DAY, DAY))))
    names = {r["Item"] for r in rows}
    assert "Extra Raita Tray" not in names and "Large" not in names
    assert len(rows) == 8  # 7 top-level selections incl. the voided one + the deleted check's item
    paid = [float(r["Net Price"]) for r in rows if r["Voided"] == "False"]
    assert round(sum(paid), 2) == TOP_LEVEL_NET


def test_every_other_orders_consumer_matches_the_top_level_sum(db, toast):
    """Gotcha 17: grep of every `orders` consumer (forecast, demand_forecast,
    weekly_sales, weekly_digest, fix_issues last-sold, similar_products) -
    they already filter parent_selection_guid; this keeps it that way."""
    _sync(db, toast)
    assert _sales_totals(db, DAY, DAY)["revenue"] == TOP_LEVEL_NET
    demand = forecast_daily_demand(db, 7, DAY + dt.timedelta(days=1))
    assert "i-raita" not in demand and "i-salan" not in demand
    assert round(sum(v["total_quantity"] for v in demand.values()), 3) == round(
        1 + 95.07 + 55 + 45.51 + 22.83 + 1214.45, 3
    )
    weeks = weekly_sales_for_item(db, "i-tray", DAY - dt.timedelta(days=7), DAY + dt.timedelta(days=7))
    assert round(sum(w["revenue"] for w in weeks), 2) == 767.55


def test_items_sold_route_returns_the_fixed_total(db, toast, admin_client):
    _sync(db, toast)
    r = admin_client.get(f"/api/sales/items-sold?start={DAY}&end={DAY}")
    assert r.status_code == 200
    assert r.json()["total_revenue"] == TOP_LEVEL_NET
    csv_text = admin_client.get(f"/api/sales/items-sold/export?start={DAY}&end={DAY}").text
    assert "Extra Raita Tray" not in csv_text


# --- the sync follows later edits in Toast ----------------------------------------------


def test_deleted_order_or_check_never_counts(db, toast):
    toast["orders"][DAY] = [order("o-x", [check("c-x", [sel("s-x", "i-x", "Gone", 50.0)])], deleted=True)]
    _sync(db, toast)
    assert db.query(Order).filter_by(toast_selection_guid="s-x").one().voided is True
    _sync(db, toast)  # idempotent
    assert db.query(Order).filter_by(toast_selection_guid="s-x").one().voided is True
    assert get_items_sold(db, DAY, DAY)["total_revenue"] == 0


def test_resync_voids_and_removes_what_changed_in_toast(db, toast):
    _sync(db, toast)
    assert _stored_top_level(db) == TOP_LEVEL_NET
    # Next morning in Toast: the chicken is voided, the cilantro line is
    # removed from the order, and the whole tray order is gone from the date
    # (moved to another day).
    edited = oct10_orders()
    o2 = edited[1]["checks"][0]["selections"]
    o2[0]["voided"] = True
    del o2[1]
    edited = [o for o in edited if o["guid"] != "o-1"]
    toast["orders"][DAY] = edited
    job = sales_sync.sync_sales_for_date(db, DAY)
    assert job.status == "success"
    assert "no longer in Toast removed" in job.detail
    assert db.query(Order).filter_by(toast_selection_guid="s-chicken").one().voided is True
    assert db.query(Order).filter_by(toast_selection_guid="s-cilantro").count() == 0
    # the tray and all its modifier rows (nested one too) are gone
    assert db.query(Order).filter(Order.toast_order_guid == "o-1").count() == 0
    assert _stored_top_level(db) == round(TOP_LEVEL_NET - 767.55 - 377.89 - 54.08, 2)
    assert get_items_sold(db, DAY, DAY)["total_revenue"] == _stored_top_level(db)


def test_empty_toast_answer_never_wipes_a_day(db, toast):
    _sync(db, toast)
    before = db.query(Order).filter(Order.business_date == DAY).count()
    toast["orders"][DAY] = []
    job = sales_sync.sync_sales_for_date(db, DAY)
    assert job.status == "success"
    assert db.query(Order).filter(Order.business_date == DAY).count() == before


def test_prune_only_touches_the_synced_business_date(db, toast):
    other = DAY - dt.timedelta(days=1)
    toast["orders"][other] = [order("o-y", [check("c-y", [sel("s-y", "i-y", "Yesterday", 10.0)])])]
    _sync(db, toast, other)
    _sync(db, toast)
    assert db.query(Order).filter_by(toast_selection_guid="s-y").count() == 1


def test_sync_retries_toast_429(db, monkeypatch):
    calls = []

    def flaky(business_date):
        calls.append(business_date)
        if len(calls) < 3:
            resp = httpx.Response(429, headers={"Retry-After": "0"}, request=httpx.Request("GET", "https://x"))
            raise httpx.HTTPStatusError("429", request=resp.request, response=resp)
        return oct10_orders()

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", flaky)
    waits = []
    assert len(sales_sync.fetch_orders_with_retry(DAY, sleep=waits.append)) == 4
    assert len(calls) == 3 and waits == [0.0, 0.0]


def test_sync_gives_up_on_other_errors(db, monkeypatch):
    def boom(business_date):
        resp = httpx.Response(500, request=httpx.Request("GET", "https://x"))
        raise httpx.HTTPStatusError("500", request=resp.request, response=resp)

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", boom)
    job = sales_sync.sync_sales_for_date(db, DAY)
    assert job.status == "failed"
    assert db.query(JobRun).filter_by(job_name=sales_sync.JOB_NAME, status="failed").count() == 1


# --- live today: correct, shared, refreshable --------------------------------------------


def test_live_pull_is_cached_and_refresh_skips_the_cache(db, toast, monkeypatch):
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: DAY)
    toast["orders"][DAY] = oct10_orders()
    get_items_sold(db, DAY, DAY)
    get_items_sold(db, DAY, DAY)
    assert len(toast["calls"]) == 3  # second request served from the cache
    get_items_sold(db, DAY, DAY, fresh=True)
    assert len(toast["calls"]) == 3  # a refresh within 10 s reuses the pull too
    monkeypatch.setattr(items_sold_mod, "MIN_REFRESH_SECONDS", 0)
    toast["orders"][DAY] = oct10_orders()[:1]
    result = get_items_sold(db, DAY, DAY, fresh=True)
    assert len(toast["calls"]) == 6
    assert result["total_revenue"] == 767.55


def test_live_data_replaces_stored_rows_for_the_same_business_dates(db, toast, monkeypatch):
    """A range ending today: yesterday's business date is pulled live too, and
    its live rows replace the stored (possibly stale) copy - never both."""
    _sync(db, toast)  # DAY stored with the full Oct 10 picture
    today = DAY + dt.timedelta(days=1)
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: today)
    # Live, DAY lost the tray (voided after our sync).
    live_day = oct10_orders()
    live_day[0]["checks"][0]["selections"][0]["voided"] = True
    toast["orders"][DAY] = live_day
    result = get_items_sold(db, DAY, today)
    assert result["source"] == "mixed"
    assert result["total_revenue"] == round(TOP_LEVEL_NET - 767.55, 2)


def test_payment_after_midnight_counts_on_its_own_day(db, toast, monkeypatch):
    """Toast's business day closes at 4 AM: a check paid at 1 AM belongs to
    yesterday's business date but was paid today - Items Sold counts it today."""
    today = DAY + dt.timedelta(days=1)
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: today)
    late = order(
        "o-late",
        [check("c-late", [sel("s-late", "i-late", "Late snack", 12.5)], paid="2026-10-11T05:00:00.000+0000")],
        paid="2026-10-11T05:00:00.000+0000",
    )
    toast["orders"][DAY] = [late]
    assert get_items_sold(db, today, today)["total_revenue"] == 12.5
    assert get_items_sold(db, DAY, DAY)["total_revenue"] == 0


def test_unpaid_live_items_wait_until_paid(db, toast, monkeypatch):
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: DAY)
    unpaid = order("o-u", [check("c-u", [sel("s-u", "i-u", "Pickup tomorrow", 40.0)], paid=None)], paid=None)
    toast["orders"][DAY] = [unpaid]
    assert get_items_sold(db, DAY, DAY)["total_revenue"] == 0


# --- checks paid days away from their business date (verify 1) -----------------------

TAB_DAY = dt.date(2026, 10, 3)


def test_check_paid_days_after_its_business_date_counts_on_the_paid_day(db, toast, monkeypatch):
    """A tab opened on Oct 3 and paid 4 days later: once a re-sync stores the
    real paid_at, Items Sold counts it on the day it was paid - before the
    fix it fell outside the +-1 business-day window and counted on no day."""
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: DAY)
    paid_late = "2026-10-07T16:00:00.000+0000"  # Oct 7, noon in New York
    toast["orders"][TAB_DAY] = [
        order("o-tab", [check("c-tab", [sel("s-tab", "i-tab", "Catering tab", 250.0)], paid=paid_late)], paid=paid_late)
    ]
    sales_sync.sync_sales_for_date(db, TAB_DAY)
    paid_day = dt.date(2026, 10, 7)
    assert get_items_sold(db, TAB_DAY, TAB_DAY)["total_revenue"] == 0
    assert get_items_sold(db, paid_day, paid_day)["total_revenue"] == 250.0
    # A range covering both counts it exactly once; the days between, never.
    assert get_items_sold(db, TAB_DAY, paid_day)["total_revenue"] == 250.0
    assert get_items_sold(db, dt.date(2026, 10, 5), dt.date(2026, 10, 6))["total_revenue"] == 0


def test_check_paid_days_before_its_business_date_counts_on_the_paid_day(db, toast, monkeypatch):
    """Catering paid on Oct 7 for an Oct 10 pickup (Toast files it under the
    pickup's business date)."""
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: DAY + dt.timedelta(days=2))
    paid_early = "2026-10-07T22:00:00.000+0000"  # Oct 7, 6 PM in New York
    toast["orders"][DAY] = [
        order("o-pre", [check("c-pre", [sel("s-pre", "i-pre", "Party tray", 180.0)], paid=paid_early)], paid=paid_early)
    ]
    sales_sync.sync_sales_for_date(db, DAY)
    assert get_items_sold(db, dt.date(2026, 10, 7), dt.date(2026, 10, 7))["total_revenue"] == 180.0
    assert get_items_sold(db, DAY, DAY)["total_revenue"] == 0


def test_late_payment_today_counts_once_alongside_the_live_pull(db, toast, monkeypatch):
    """"Today" alone: a stored check from an older business date paid today
    (re-synced by the accuracy job) counts today, once; the live business
    dates themselves are never also counted from storage."""
    today = dt.date(2026, 10, 7)
    monkeypatch.setattr(items_sold_mod, "restaurant_today", lambda: today)
    paid_today = "2026-10-07T16:00:00.000+0000"
    toast["orders"][TAB_DAY] = [
        order("o-tab", [check("c-tab", [sel("s-tab", "i-tab", "Catering tab", 250.0)], paid=paid_today)], paid=paid_today)
    ]
    sales_sync.sync_sales_for_date(db, TAB_DAY)
    # Yesterday's business date is stored AND pulled live: counted once (live).
    yesterday = today - dt.timedelta(days=1)
    toast["orders"][yesterday] = [
        order("o-y", [check("c-y", [sel("s-y", "i-y", "Late chai", 3.0)], paid=paid_today)], paid=paid_today)
    ]
    sales_sync.sync_sales_for_date(db, yesterday)
    result = get_items_sold(db, today, today)
    assert result["source"] == "live"
    assert result["total_revenue"] == 253.0
