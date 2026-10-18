"""Open Checks (renamed from Open Orders, owner Oct 4) and the Toast tab name
("show the tab name on all the open orders because tab name has the customer
name").

The tab name comes from each check's `tabName` on the Toast order (Toast
Orders API, Check object). No raw Toast payload is cached anywhere in this
app, so the sample orders below follow Toast's documented schema. Nothing
here calls Toast: the fetchers are monkeypatched.
"""

import datetime as dt
import json
import zoneinfo

import pytest

from app import features
from app.glossary import CATEGORIES, TERMS
from app.models import OpenOrderCache
from app.routers.orders import TAB_NAMES_CLIENT_HEADER
from app.services import notifications, open_orders
from app.services.notification_triggers import open_orders_morning_alerts
from app.services.open_orders import (
    _normalize_open_order,
    _tab_names,
    get_cached_open_orders,
    legacy_display_number,
    refresh_open_orders_cache,
    tab_label,
)
from app.timeutil import restaurant_today, utcnow
from tests.conftest import client_for, make_user

DART_UA = "Dart/3.5 (dart:io)"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"

# The fields every open check carried before this change (app builds 5-7 and
# the website read these) - all must still be there, same names.
OLD_KEYS = {
    "guid",
    "business_date",
    "display_number",
    "opened_at",
    "elapsed_minutes",
    "server_guid",
    "server_name",
    "num_guests",
    "approval_status",
    "total_amount",
    "num_checks",
    "line_items",
    "paid_at",
    "promised_at",
    "scheduled",
}


def _check(tab=..., **extra):
    """A Toast Check object (only the fields this code reads)."""
    c = {
        "guid": extra.pop("guid", "chk"),
        "entityType": "Check",
        "displayNumber": extra.pop("displayNumber", "1"),
        "totalAmount": extra.pop("totalAmount", 10.0),
        "selections": extra.pop("selections", []),
    }
    if tab is not ...:
        c["tabName"] = tab
    c.update(extra)
    return c


def _toast_order(guid="o1", number="12", checks=None, server="emp1"):
    return {
        "guid": guid,
        "entityType": "Order",
        "displayNumber": number,
        "createdDate": "2026-10-04T14:00:00.000+0000",
        "openedDate": "2026-10-04T14:00:00.000+0000",
        "closedDate": None,
        "voided": False,
        "deleted": False,
        "server": {"guid": server, "entityType": "RestaurantUser"},
        "numberOfGuests": 2,
        "approvalStatus": "APPROVED",
        "checks": checks if checks is not None else [_check("Ravi")],
    }


# --- extraction -------------------------------------------------------------


@pytest.mark.parametrize(
    "checks, expected",
    [
        ([_check("Ravi")], ["Ravi"]),
        # several checks: each different name once, in check order
        ([_check("Ravi"), _check("Priya", guid="c2"), _check("ravi ", guid="c3")], ["Ravi", "Priya"]),
        # blank / whitespace / missing / null / not a string -> nothing
        ([_check(""), _check("   "), _check(), _check(None), _check(123)], []),
        # inner whitespace collapsed
        ([_check("  Ravi \n  Kumar ")], ["Ravi Kumar"]),
        # deleted / voided checks don't count
        ([_check("Gone", deleted=True), _check("Void", voided=True), _check("Kept")], ["Kept"]),
        # Toast sometimes puts the order number in the tab - not a name
        ([_check("12"), _check("Anita")], ["Anita"]),
        ([], []),
    ],
)
def test_tab_names_from_toast_checks(checks, expected):
    assert _tab_names(_toast_order(checks=checks)) == expected


def test_tab_names_survive_odd_payloads():
    assert _tab_names({"displayNumber": "5"}) == []  # no checks key
    assert _tab_names({"checks": None}) == []
    assert _tab_names({"checks": ["not a dict", _check("Ok")]}) == ["Ok"]


def test_tab_label():
    assert tab_label(["Ravi"]) == "Ravi"
    assert tab_label(["Ravi", "Priya"]) == "Ravi, Priya"
    assert tab_label([]) is None
    assert tab_label(None) is None


def test_normalized_open_check_is_additive():
    now = dt.datetime(2026, 10, 4, 15, 0, tzinfo=dt.timezone.utc)
    row = _normalize_open_order(
        _toast_order(checks=[_check("Ravi"), _check("Priya", guid="c2")]), dt.date(2026, 10, 4), {"emp1": "Sam"}, now
    )
    assert OLD_KEYS <= set(row)
    assert row["display_number"] == "12"  # untouched
    assert row["server_name"] == "Sam"
    assert row["num_checks"] == 2
    assert row["tab_names"] == ["Ravi", "Priya"]
    assert row["tab_name"] == "Ravi, Priya"

    bare = _normalize_open_order(_toast_order(checks=[_check()]), dt.date(2026, 10, 4), {}, now)
    assert bare["tab_names"] == [] and bare["tab_name"] is None


def test_legacy_display_number():
    assert legacy_display_number({"display_number": "12", "tab_name": "Ravi"}) == "12 · Tab: Ravi"
    assert legacy_display_number({"display_number": None, "tab_name": "Ravi"}) == "? · Tab: Ravi"
    assert legacy_display_number({"display_number": "12", "tab_name": None}) == "12"
    assert legacy_display_number({"display_number": None}) is None


# --- API --------------------------------------------------------------------


@pytest.fixture
def fake_toast(monkeypatch):
    """Replaces both Toast calls the open-checks code makes."""
    orders = [
        _toast_order("o1", "12", [_check("Ravi"), _check("Ravi", guid="c2")]),
        _toast_order("o2", "13", [_check()]),
        _toast_order("o3", None, [_check("Priya Venkataraman-Subramanian")]),
        {**_toast_order("o4", "14"), "closedDate": "2026-10-04T15:00:00.000+0000"},  # closed: not listed
    ]
    monkeypatch.setattr(open_orders, "fetch_orders_for_business_date", lambda d: [dict(o) for o in orders])
    monkeypatch.setattr(open_orders, "_fetch_with_retry", lambda d, max_retries=4: [dict(o) for o in orders])
    monkeypatch.setattr(
        open_orders, "fetch_employees", lambda: [{"guid": "emp1", "firstName": "Sam", "lastName": "K"}]
    )
    monkeypatch.setattr(open_orders.time, "sleep", lambda s: None)
    open_orders._EMPLOYEE_CACHE.update({"at": 0.0, "by_guid": {}})
    return orders


def _by_guid(body):
    return {o["guid"]: o for o in body["orders"]}


def test_live_day_view_adds_tab_names(fake_toast, admin_client):
    admin_client.headers["User-Agent"] = BROWSER_UA
    body = admin_client.get("/api/orders/open").json()
    assert body["count"] == 3
    rows = _by_guid(body)
    for row in rows.values():
        assert OLD_KEYS <= set(row)
    assert rows["o1"]["tab_names"] == ["Ravi"] and rows["o1"]["tab_name"] == "Ravi"
    assert rows["o1"]["display_number"] == "12"  # the website gets it raw
    assert rows["o2"]["tab_names"] == [] and rows["o2"]["tab_name"] is None
    assert rows["o3"]["display_number"] is None


def test_old_app_builds_get_the_tab_in_display_number(fake_toast, admin_client):
    """Builds 5-7 only show display_number ("Order #<it>") - so the tab rides
    along there for them; build 8 (header) and the website get it raw."""
    admin_client.headers["User-Agent"] = DART_UA
    rows = _by_guid(admin_client.get("/api/orders/open").json())
    assert rows["o1"]["display_number"] == "12 · Tab: Ravi"
    assert rows["o2"]["display_number"] == "13"  # no tab: unchanged
    assert rows["o3"]["display_number"] == "? · Tab: Priya Venkataraman-Subramanian"
    assert rows["o1"]["tab_names"] == ["Ravi"]  # new fields still there

    admin_client.headers[TAB_NAMES_CLIENT_HEADER] = "1"
    rows = _by_guid(admin_client.get("/api/orders/open").json())
    assert rows["o1"]["display_number"] == "12"


def test_all_time_cache_stores_and_serves_tab_names(fake_toast, db, admin_client):
    job = refresh_open_orders_cache(db, full=False)
    assert job.status == "success"
    assert "3 open check(s)" in job.detail
    stored = {r.toast_order_guid: r for r in db.query(OpenOrderCache).all()}
    assert json.loads(stored["o1"].tab_names_json) == ["Ravi"]
    assert json.loads(stored["o2"].tab_names_json) == []

    admin_client.headers["User-Agent"] = BROWSER_UA
    body = admin_client.get("/api/orders/open/all-time").json()
    rows = _by_guid(body)
    assert rows["o1"]["tab_name"] == "Ravi" and rows["o1"]["display_number"] == "12"
    assert rows["o3"]["tab_names"] == ["Priya Venkataraman-Subramanian"]
    for row in rows.values():
        assert OLD_KEYS <= set(row)

    admin_client.headers["User-Agent"] = DART_UA
    rows = _by_guid(admin_client.get("/api/orders/open/all-time").json())
    assert rows["o1"]["display_number"] == "12 · Tab: Ravi"


def test_rows_cached_before_the_migration_read_as_no_tab(db):
    db.query(OpenOrderCache).delete()
    db.add(
        OpenOrderCache(
            toast_order_guid="old",
            business_date=restaurant_today(),
            display_number="7",
            total_amount=1.0,
            num_checks=1,
            tab_names_json=None,
            scanned_at=utcnow(),
        )
    )
    db.add(
        OpenOrderCache(
            toast_order_guid="junk",
            business_date=restaurant_today(),
            display_number="8",
            total_amount=1.0,
            num_checks=1,
            tab_names_json="{not json",
            scanned_at=utcnow(),
        )
    )
    db.commit()
    rows = {o["guid"]: o for o in get_cached_open_orders(db)["orders"]}
    assert rows["old"]["tab_names"] == [] and rows["old"]["tab_name"] is None
    assert rows["junk"]["tab_names"] == []


def test_csv_export_has_tab_column_and_new_name(fake_toast, admin_client):
    res = admin_client.get("/api/orders/open/export?mode=day")
    assert res.status_code == 200
    assert 'filename="open-checks-' in res.headers["content-disposition"]
    lines = res.text.splitlines()
    assert lines[0].startswith("Business Date,Check #,Tab,Opened At,")
    assert any(",12,Ravi," in line for line in lines[1:])
    all_time = admin_client.get("/api/orders/open/export?mode=all_time")
    assert 'filename="open-checks-all-time.csv"' in all_time.headers["content-disposition"]


def test_hidden_sales_user_still_gets_tab_names(fake_toast, db):
    user = make_user(db, "nosales_tab", features=["open_orders"])
    user.hide_sales = True
    db.commit()
    client = client_for(db, user)
    rows = _by_guid(client.get("/api/orders/open").json())
    assert rows["o1"]["total_amount"] is None
    assert rows["o1"]["tab_name"] == "Ravi"


# --- renamed texts (keys stay) ----------------------------------------------


def test_user_facing_names_say_open_checks():
    assert {"key": "open_orders", "label": "Open Checks"} in features.FEATURES
    alert = notifications.ALERTS_BY_KEY["open_orders_morning"]
    assert alert["label"].startswith("Open checks") and alert["route"] == "open_orders"
    cat = next(c for c in CATEGORIES if c["key"] == "open_orders")
    assert cat["label"] == "Open checks" and cat["where"] == "Open Checks"
    tab = next(t for t in TERMS if t["key"] == "open_order_tab_name")
    assert tab["category"] == "open_orders" and "customer" in tab["definition"]


def test_summary_alert_says_checks(db):
    db.query(OpenOrderCache).delete()
    db.add(
        OpenOrderCache(
            toast_order_guid="x",
            business_date=restaurant_today(),
            display_number="1",
            opened_at=dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.timezone.utc),  # 8 AM store time
            total_amount=1.0,
            num_checks=1,
            scanned_at=utcnow(),
        )
    )
    db.commit()
    nine_am = dt.datetime(2026, 10, 4, 9, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York"))
    (alert,) = open_orders_morning_alerts(db, nine_am)
    assert alert.title == "Opening: 1 open check"
    assert alert.key == "open_orders_morning"
    assert alert.body == "Tap to review open checks"
