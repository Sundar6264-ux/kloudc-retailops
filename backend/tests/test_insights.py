"""Insights from the Toast Analytics API (docs/INSIGHTS_CONTRACT.md).

Every Toast call is a fake shaped like the real answers saved in the
evidence folder (raw/*.json: metrics day / HOUR / DINING_OPTION, menu
MENU_ITEM / MODIFIER, check/day, guest/payments - same field names and
types, e.g. businessDate "20261010", businessHour "14", checkPaidDateTime
"2026-10-10T16:29:17.261Z"). One test runs the real client end to end over a
mocked httpx transport. No real Toast, push or WhatsApp.
"""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import httpx
import pytest

from app.config import get_settings
from app.insights_models import AnalyticsCache, AnalyticsCallLog, GuestVisit, LossWatchFlag
from app.models import DeviceToken, JobRun, NotificationLog, Order
from app.pos.adapters.toast import analytics as toast_analytics
from app.services import insights, insights_data, notifications, push
from app.services.notification_triggers import TRIGGERS, run_alert_job
from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

TODAY = dt.date(2026, 10, 12)  # a Monday; yesterday = Sunday Oct 11
YESTERDAY = TODAY - dt.timedelta(days=1)
REST = "b52f24c9-9201-4902-8cea-1db5d9fd8f9d"
NY = __import__("zoneinfo").ZoneInfo("America/New_York")

# Card data the fake guest report carries - none of it may ever be stored or returned.
CARD_SECRETS = ("fp-ALPHA-raw", "fp-BRAVO-raw", "fp-CHARLIE-raw", "PAR-SECRET", "JANE Q CUSTOMER", '"4242"', "'4242'", "VISA")


def ymd(d):
    return d.strftime("%Y%m%d")


def pdate(s):
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))


def week_index(d):
    return (d - dt.date(2026, 5, 25)).days // 7


def day_net(d):
    return round(2000.0 + 100 * d.weekday() + 10 * week_index(d), 2)


def day_orders(d):
    return 80 + d.weekday()


HOURS = list(range(10, 21))


def hour_weights(d):
    w = {h: 1.0 for h in HOURS}
    w[14] = 3.0 if d.weekday() == 5 else 1.5
    return w


ITEMS = {
    "k1": ("Samosa Chaat", 6.99),
    "k2": ("Chicken 65 Dum Biryani", 14.99),
    "g1": ("Swad Dehraduni Basmati Rice 10lb - Each", 15.99),
    "g2": ("Coke Classic Can 12oz", 1.49),
}


def item_qty(guid, d):
    if guid == "k1":
        return 5 + (2 if d >= TODAY - dt.timedelta(days=28) else 0) + (3 if d.weekday() == 1 else 0)
    if guid == "k2":
        return 3
    if guid == "g1":
        return 2
    if guid == "g2":
        return 1 if d >= TODAY - dt.timedelta(days=28) else 4
    return 0


class FakeToast:
    """Stands in for toast_analytics.run_report: answers like Toast does."""

    def __init__(self):
        self.calls = []
        self.overrides = {}  # date -> extra net sales
        self.fail = {}  # kind -> exception to raise
        self.guest_cards = True

    def net(self, d):
        return round(day_net(d) + self.overrides.get(d, 0.0), 2)

    def __call__(self, kind, time_range, body, params=None, **kw):
        if kw.get("before_post"):  # like run_report: once per report POST
            kw["before_post"](1)
        start = pdate(body.get("startBusinessDate") or body["startDate"])
        end = pdate(body.get("endBusinessDate") or body["endDate"])
        self.calls.append(
            {"kind": kind, "range": time_range, "start": start, "end": end, "group": body.get("groupBy"), "params": params}
        )
        if kind in self.fail:
            raise self.fail[kind]
        if end > TODAY:
            raise toast_analytics.AnalyticsReportFailed("HTTP 400: Future dates are not allowed")
        if kind == "check" and start != end:
            raise toast_analytics.AnalyticsReportFailed("HTTP 400: End date and start date should be same")
        if params and params.get("aggregateBy") == "HOUR":
            assert time_range == "day" and start == end
        assert time_range == ("day" if start == end else "week")
        rows = []
        d = start
        while d <= end:
            rows.extend(self.rows(kind, d, body.get("groupBy"), params))
            d += dt.timedelta(days=1)
        return rows

    def metrics_row(self, d, net, orders, **extra):
        row = {
            "restaurantGuid": REST,
            "businessDate": ymd(d),
            "guestCount": orders + 1 if orders else 0,
            "voidOrdersCount": 1,
            "discountOrderCount": 3,
            "ordersCount": orders,
            "openOrdersCount": 0,
            "closedOrdersCount": orders,
            "netSalesAmount": net,
            "grossSalesAmount": round(net + 70, 2) if net is not None else None,
            "discountAmount": 50.0,
            "voidOrdersAmount": 20.0,
            "refundAmount": 5.0,
            "avgOrderValue": round(net / orders, 2) if orders and net is not None else None,
            "hourlyJobTotalHours": 40.0,
            "hourlyJobTotalPay": 600.0,
            "hourlyJobSalesPerLaborHour": 50.0,
        }
        row.update(extra)
        return row

    def rows(self, kind, d, group, params):
        net, orders = self.net(d), day_orders(d)
        if kind == "metrics" and params and params.get("aggregateBy") == "HOUR":
            w = hour_weights(d)
            total = sum(w.values())
            out = []
            for h in (0, 1, 2, 3):
                out.append(self.metrics_row(d, None, None, businessHour=str(h), voidOrdersCount=None, discountOrderCount=None,
                                            discountAmount=None, voidOrdersAmount=None, refundAmount=None, guestCount=None))
            out.append(self.metrics_row(d, 0, 0, businessHour="9", discountAmount=0, voidOrdersAmount=0, refundAmount=0))
            left_net, left_orders = net, orders
            for i, h in enumerate(HOURS):
                if i == len(HOURS) - 1:
                    hn, ho = round(left_net, 2), left_orders
                else:
                    hn, ho = round(net * w[h] / total, 2), int(orders * w[h] / total)
                left_net -= hn
                left_orders -= ho
                out.append(self.metrics_row(d, hn, ho, businessHour=str(h), discountAmount=0, voidOrdersAmount=0, refundAmount=0))
                out[-1]["discountAmount"] = 50.0 if h == 12 else 0
                out[-1]["voidOrdersAmount"] = 20.0 if h == 12 else 0
                out[-1]["refundAmount"] = 5.0 if h == 12 else 0
            return out
        if kind == "metrics" and group == ["DINING_OPTION"]:
            shares = [("In-Store Purchase", 0.7), ("Pick Up", 0.15), ("Online Ordering - Takeout", 0.10), ("DoorDash - Delivery", 0.05)]
            out = [self.metrics_row(d, 0.0, 0, diningOption="Catering - Delivery")]
            left = net
            for i, (name, share) in enumerate(shares):
                n = round(left, 2) if i == len(shares) - 1 else round(net * share, 2)
                left -= n
                out.append(self.metrics_row(d, n, max(1, int(orders * share)), diningOption=name))
            return out
        if kind == "metrics":
            return [self.metrics_row(d, net, orders)]
        if kind == "menu" and group == ["MENU_ITEM"]:
            out = []
            for g, (name, price) in ITEMS.items():
                q = item_qty(g, d)
                if q:
                    out.append(self.menu_row(d, q, price, menuItemGuid=g, menuItemName=name))
            return out
        if kind == "menu" and group == ["MODIFIER"]:
            return [
                self.menu_row(d, 2, 1.0, modifierGuid="m1", modifierName="Extra Cheese"),
                self.menu_row(d, 4, 0.0, modifierGuid="m2", modifierName="Mild"),
            ]
        if kind == "check":
            return check_rows(d)
        if kind == "guest/payments":
            return self.guest_rows(d)
        raise AssertionError(f"unexpected report {kind} {group} {params}")

    def menu_row(self, d, q, price, **extra):
        row = {
            "restaurantGuid": REST,
            "restaurantName": "Spice Town",
            "restaurantLocationName": None,
            "restaurantLocationCode": "",
            "businessDate": ymd(d),
            "netSalesAmount": round(q * price, 2),
            "grossSalesAmount": round(q * price, 2),
            "discountAmount": 0.0,
            "refundAmount": 0.0,
            "voidAmount": 0.0,
            "quantitySold": float(q),
            "averagePrice": price,
            "wasteCount": 0.0,
            "wasteAmount": 0.0,
        }
        row.update(extra)
        return row

    def guest_rows(self, d):
        cards = ["fp-CHARLIE-raw", "fp-CHARLIE-raw"]  # C every day, twice
        if d.weekday() == 6:
            cards.append("fp-ALPHA-raw")  # A every Sunday
        if d == YESTERDAY:
            cards.append("fp-BRAVO-raw")  # B only yesterday
        out = []
        for i, fp in enumerate(cards):
            out.append(
                {
                    "restaurantGuid": REST,
                    "restaurantName": "Spice Town",
                    "restaurantLocationName": None,
                    "restaurantLocationCode": "",
                    "paymentDate": ymd(d),
                    "orderGuid": f"o-{ymd(d)}-{i}",
                    "paymentGuid": f"p-{ymd(d)}-{i}",
                    "cardFingerprint": fp,
                    "cardLast4Digits": "4242",
                    "cardType": "VISA",
                    "paymentAccountReference": "PAR-SECRET-" + fp,
                    "cardHolderName": "JANE Q CUSTOMER",
                }
            )
        out.append(  # a cash / unknown payment: no card at all
            {
                "restaurantGuid": REST, "restaurantName": "Spice Town", "restaurantLocationName": None,
                "restaurantLocationCode": "", "paymentDate": ymd(d), "orderGuid": "o-cash", "paymentGuid": f"p-cash-{ymd(d)}",
                "cardFingerprint": None, "cardLast4Digits": None, "cardType": None, "paymentAccountReference": None,
                "cardHolderName": None,
            }
        )
        return out


def _check(d, guid, number, server, total, discount=0.0, refund=0.0, paid="2026-10-11T16:00:00.000Z", modified=None,
           status="CLOSED", voided=False, rc="In-Store Purchase", dining="In-Store Purchase"):
    return {
        "restaurantGuid": REST,
        "orderNumber": number,
        "orderGuid": "o-" + guid,
        "checkNumber": number,
        "checkGuid": guid,
        "orderOpenedDate": ymd(d),
        "checkPaidDateTime": paid,
        "checkModifiedDateTime": modified or paid,
        "checkStatus": status,
        "checkVoidedStatus": voided,
        "diningOption": dining,
        "revenueCenter": rc,
        "serverName": server,
        "checkTotalAmount": total,
        "checkDiscountAmount": discount,
        "checkTaxAmount": 0.0,
        "checkTipAmount": 0.0,
        "checkGratuityAmount": 0,
        "checkRefundAmount": refund,
    }


def check_rows(d):
    paid = f"{d.isoformat()}T16:00:00.000Z"
    rows = [
        _check(d, f"c-{ymd(d)}-{i}", str(i), "Asha Kumar" if i % 2 else "Ben Lopez", 20.0 + i, paid=paid) for i in range(1, 7)
    ]
    rows.append(_check(d, f"c-{ymd(d)}-disc", "7", "Asha Kumar", 30.0, discount=5.0, paid=paid))
    if d != YESTERDAY:
        return rows
    y = d.isoformat()
    nxt = (d + dt.timedelta(days=1)).isoformat()
    rows += [
        _check(d, "c-comp", "101", "Ben Lopez", 0.0, discount=47.97, paid=f"{y}T17:00:00.000Z"),
        _check(d, "c-bigdisc", "102", "Asha Kumar", 40.0, discount=60.0, paid=f"{y}T17:05:00.000Z"),
        _check(d, "c-refund", "103", "Ben Lopez", 22.34, refund=30.0, paid=f"{y}T17:10:00.000Z"),
        _check(d, "c-medium", "104", "Asha Kumar", 25.0, discount=26.0, paid=f"{y}T17:15:00.000Z"),
        _check(d, "c-open", "105", "Ben Lopez", 64.08, paid=None, modified=f"{y}T23:11:55.845Z", status="OPEN"),
        _check(d, "c-slowpay", "106", "Asha Kumar", 18.74, paid=f"{y}T15:05:38.133Z"),
        _check(d, "c-mod", "107", "Asha Kumar", 14.22, paid=f"{y}T15:05:38.133Z", modified=f"{nxt}T09:30:54.829Z"),
        _check(d, "c-sameday", "114", "Asha Kumar", 9.22, paid=f"{y}T15:05:38.133Z", modified=f"{y}T16:30:54.829Z"),
        _check(d, "c-kiosk", "115", "Kiosk User", 11.75, paid=f"{y}T16:03:46.056Z", modified=f"{nxt}T09:15:05.116Z",
               dining="Pick Up"),
        _check(d, "c-batch1", "108", "Ben Lopez", 10.0, paid=f"{y}T15:00:00.000Z", modified=f"{nxt}T08:20:05.145Z"),
        _check(d, "c-batch2", "109", "Ben Lopez", 11.0, paid=f"{y}T15:00:00.000Z", modified=f"{nxt}T08:20:05.145Z"),
        _check(d, "c-batch3", "110", "Ben Lopez", 12.0, paid=f"{y}T15:00:00.000Z", modified=f"{nxt}T08:20:05.145Z"),
        _check(d, "c-online", "111", "Ben Lopez", 22.0, paid=f"{y}T14:55:45.315Z", modified=f"{y}T17:44:08.234Z",
               rc="Online Ordering", dining="Online Ordering - Takeout"),
        _check(d, "c-prepaid", "112", "Ben Lopez", 80.8, paid=f"{(d - dt.timedelta(days=3)).isoformat()}T23:54:49.678Z",
               modified=f"{y}T16:29:17.261Z", rc="In-Store Purchase"),
        _check(d, "c-void", "113", "Ben Lopez", 0.0, paid=None, status="OPEN", voided=True),
    ]
    return rows


MENUS = {
    "restaurantGuid": REST,
    "lastUpdated": "2026-10-11T15:41:42.977+0000",
    "menus": [
        {
            "name": "KITCHEN",
            "menuGroups": [
                {"name": "Chaat Bar", "menuItems": [{"guid": "k1", "name": "Samosa Chaat", "modifierGroupReferences": [1, 3]}], "menuGroups": []},
                {"name": "Dum Biryani", "menuItems": [{"guid": "k2", "name": "Chicken 65 Dum Biryani", "modifierGroupReferences": [3]}]},
                {"name": "Kati Roll (Mini Wrap) - Veg", "menuItems": [{"guid": "k3", "name": "Paneer Tikka Roll"}]},
                {"name": "Drinks - Soda / Sparkling", "menuItems": [{"guid": "g2", "name": "Coke Classic Can 12oz"}]},
                {"name": "Drinks - House Drinks", "menuItems": [{"guid": "k4", "name": "Mango Lassi"}]},
            ],
        },
        {
            "name": "RETAIL",
            "menuGroups": [
                {
                    "name": "Rice & Rice Products",
                    "menuItems": [{"guid": "g1", "name": "Swad Dehraduni Basmati Rice 10lb - Each"}],
                    "menuGroups": [{"name": "Basmati", "menuItems": [{"guid": "g3", "name": "Nested Rice"}]}],
                },
                {"name": "Beverages (Non-Alcoholic)", "menuItems": [{"guid": "g2", "name": "Coke Classic Can 12oz"}, {"guid": "k4", "name": "Mango Lassi"}]},
            ],
        },
        {"name": "KITCHEN (GH)", "menuGroups": [{"name": "Curry Boxes & Combos", "menuItems": [{"guid": "k5", "name": "Curry Box"}]}]},
        {"name": "Test Menu", "menuGroups": [{"name": "Tests", "menuItems": [{"guid": "t1", "name": "Test item"}]}]},
    ],
    "modifierGroupReferences": {
        "1": {"referenceId": 1, "name": "Add-ons", "modifierOptionReferences": [2]},
        "3": {"referenceId": 3, "name": "Spice level", "modifierOptionReferences": [4]},
    },
    "modifierOptionReferences": {
        "2": {"referenceId": 2, "guid": "m1", "name": "Extra Cheese"},
        "4": {"referenceId": 4, "guid": "m2", "name": "Mild"},
    },
}


@pytest.fixture
def fake(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "cid")
    monkeypatch.setattr(s, "toast_analytics_client_secret", "never-shown")
    monkeypatch.setattr(s, "toast_analytics_base", "https://analytics.test")
    monkeypatch.setattr(s, "toast_restaurant_guid", REST)
    monkeypatch.setattr(s, "insights_hmac_secret", "test-hmac-secret-0123456789abcdef")
    monkeypatch.setattr(insights_data, "today_business_date", lambda now=None: TODAY)
    f = FakeToast()
    monkeypatch.setattr(insights_data, "_run_report", f)
    f.spawned = []
    monkeypatch.setattr(insights_data, "_spawn", lambda fn: f.spawned.append(fn))
    insights_data._live_failed_until.clear()
    return f


def _orders(db):
    """Our synced orders for a few of yesterday's checks (opened time, voided lines)."""
    y = YESTERDAY

    def utc(h, m=0):
        return dt.datetime(y.year, y.month, y.day, h, m, tzinfo=dt.timezone.utc)

    rows = [
        ("c-slowpay", utc(10, 30), False, 18.74),
        ("c-open", utc(14, 0), False, 64.08),
        ("c-mod", utc(15, 0), False, 14.22),
        ("c-void", utc(18, 0), True, 12.96),
    ]
    for i, (guid, opened, voided, net) in enumerate(rows):
        db.add(
            Order(
                toast_order_guid="o-" + guid, toast_check_guid=guid, toast_selection_guid=f"sel-{i}",
                business_date=y, opened_at=opened, item_guid="k1", item_name="Samosa Chaat",
                quantity=1, unit_price=net, net_price=net, voided=voided,
            )
        )
    db.commit()


def _menu_map(db):
    return insights_data.refresh_menu_map(
        db, force=True, fetch_last_updated_fn=lambda: None, fetch_menus_fn=lambda: json.loads(json.dumps(MENUS))
    )


@pytest.fixture
def filled(db, fake):
    _orders(db)
    _menu_map(db)
    for _ in range(3):  # three nights fill the hourly history (40 a night)
        job = insights_data.run_nightly(db)
        assert job is not None
        _a_day_passes(db)  # the check / guest budget is per rolling 24 h
    return fake


def _a_day_passes(db):
    for row in db.query(AnalyticsCallLog).all():
        row.called_at = row.called_at - dt.timedelta(hours=25)
    db.commit()


@pytest.fixture
def admin_c(db):
    return client_for(db, make_user(db, "boss", is_admin=True))


@pytest.fixture
def staff_c(db):
    return client_for(db, make_user(db, "ravi", features=["sales_insights"]))


ENDPOINTS = [
    "/api/insights/hourly-heatmap?weeks=4",
    "/api/insights/channels?weeks=8",
    "/api/insights/compare",
    "/api/insights/check-trends?weeks=12",
    "/api/insights/loss-watch?days=30",
    "/api/insights/odd-checks?days=7",
    "/api/insights/menu-performance?days=28&group=all",
    "/api/insights/addons?days=28",
    "/api/insights/prep-forecast",
    "/api/insights/returning-customers?weeks=8",
]
ADMIN_ONLY = {"/api/insights/loss-watch?days=30", "/api/insights/odd-checks?days=7"}
COUNTS_OK_FOR_HIDDEN = {
    "/api/insights/hourly-heatmap?weeks=4",
    "/api/insights/prep-forecast",
    "/api/insights/returning-customers?weeks=8",
}


def _common(body):
    assert body["source"] == "toast_analytics"
    assert isinstance(body["stale"], bool)
    dt.datetime.fromisoformat(body["generated_at"])


# --------------------------------------------------------------------------- migration


def test_migration_0051_up_down_up(tmp_path):
    from app.db import Base

    backend = pathlib.Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    tables = ("analytics_cache", "analytics_call_log", "guest_visits", "loss_watch_flags")

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=backend, env=env, check=True, capture_output=True)

    def names():
        con = sqlite3.connect(db_file)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            con.close()

    run("upgrade", "0051")
    con = sqlite3.connect(db_file)
    try:
        for table in tables:
            migrated = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            assert migrated == {c.name for c in Base.metadata.tables[table].columns}, table
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            assert "AUTOINCREMENT" in sql, table
        uniques = [r[1] for r in con.execute("PRAGMA index_list(analytics_cache)") if r[2] == 1]
        cols = [[c[2] for c in con.execute(f"PRAGMA index_info({n})")] for n in uniques]
        assert ["report", "params_hash"] in cols
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0051"
    finally:
        con.close()
    run("downgrade", "0050")
    assert not set(tables) & names()
    run("upgrade", "head")
    assert set(tables) <= names()


# --------------------------------------------------------------------------- endpoints


def test_every_endpoint_answers_with_the_contract_shapes(db, filled, admin_c):
    for url in ENDPOINTS:
        res = admin_c.get(url)
        assert res.status_code == 200, (url, res.text)
        _common(res.json())

    heat = admin_c.get("/api/insights/hourly-heatmap?weeks=4").json()
    assert heat["stale"] is False and heat["days"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert heat["hours"] == list(range(24)) and len(heat["cells"]) == 168
    assert set(heat["cells"][0]) == {"dow", "hour", "avg_net_sales", "avg_orders", "max_net_sales"}
    assert (heat["busiest"][0]["dow"], heat["busiest"][0]["hour"]) == (5, 14)  # Saturday 2 PM
    sat14 = next(c for c in heat["cells"] if c["dow"] == 5 and c["hour"] == 14)
    sats = [YESTERDAY - dt.timedelta(days=1 + 7 * k) for k in range(4)]
    assert sat14["avg_net_sales"] == pytest.approx(sum(round(day_net(d) * 3 / 13, 2) for d in sats) / 4, abs=0.01)
    assert all(c["hour"] in HOURS for c in heat["quietest"])

    ch = admin_c.get("/api/insights/channels?weeks=8").json()
    assert ch["channels"] == ["In-store", "Pick up", "Online ordering", "DoorDash"]
    assert len(ch["weeks"]) == 8 and ch["weeks"][-1]["week_start"] == "2026-10-05"
    last = ch["weeks"][-1]
    assert sum(b["share"] for b in last["by"]) == pytest.approx(1.0, abs=0.001)
    assert last["total_net"] == pytest.approx(sum(day_net(TODAY - dt.timedelta(days=k)) for k in range(1, 8)), abs=0.02)
    assert {b["channel"]: b["share"] for b in last["by"]}["In-store"] == pytest.approx(0.7, abs=0.001)

    cmp = admin_c.get("/api/insights/compare").json()
    assert cmp["date"] == "2026-10-11" and cmp["net_sales"] == day_net(YESTERDAY)
    assert cmp["orders"] == day_orders(YESTERDAY) and cmp["guests"] == day_orders(YESTERDAY) + 1
    assert cmp["avg_check"] == round(day_net(YESTERDAY) / day_orders(YESTERDAY), 2)
    lw = YESTERDAY - dt.timedelta(days=7)
    assert cmp["vs_last_week"]["date"] == lw.isoformat() and cmp["vs_last_week"]["net_sales"] == day_net(lw)
    assert cmp["vs_last_week"]["pct"] == round((day_net(YESTERDAY) - day_net(lw)) / day_net(lw) * 100, 1)
    four = [day_net(YESTERDAY - dt.timedelta(days=7 * k)) for k in range(1, 5)]
    assert cmp["vs_4wk_avg"]["net_sales"] == round(sum(four) / 4, 2)
    assert cmp["vs_last_year"] is None  # before the store's first sales

    tr = admin_c.get("/api/insights/check-trends?weeks=12").json()
    assert len(tr["days"]) == 84 and tr["days"][-1]["date"] == "2026-10-11"
    assert set(tr["days"][0]) == {"date", "avg_check", "guests", "orders", "net_sales"}
    assert len(tr["weekly"]) == 12 and all(not w["partial"] for w in tr["weekly"])

    mp = admin_c.get("/api/insights/menu-performance?days=28&group=kitchen").json()
    assert mp["top"][0]["item_guid"] == "k1" and mp["top"][0]["menu_group"] == "Chaat Bar"
    assert set(mp["top"][0]) >= {"item_guid", "name", "menu_group", "qty", "net_sales", "prev_qty", "change_pct", "last_sold"}
    assert [r["item_guid"] for r in mp["rising"]] == ["k1"] and mp["falling"] == []
    assert {r["item_guid"] for r in mp["not_sold"]} == {"k3", "k4", "k5"}
    assert {r["item_guid"] for r in mp["top"]} == {"k1", "k2"}
    groc = admin_c.get("/api/insights/menu-performance?days=28&group=grocery").json()
    assert {r["item_guid"] for r in groc["top"]} == {"g1", "g2"} and groc["not_sold"] == []
    assert [r["item_guid"] for r in groc["falling"]] == ["g2"] and groc["falling"][0]["change_pct"] == -75.0

    ad = admin_c.get("/api/insights/addons?days=28").json()
    m1 = next(m for m in ad["modifiers"] if m["name"] == "Extra Cheese")
    k1_qty = sum(item_qty("k1", YESTERDAY - dt.timedelta(days=k)) for k in range(28))
    assert m1["group"] == "Add-ons" and m1["qty"] == 56 and m1["attach_rate"] == round(56 / k1_qty, 3)
    mild = next(m for m in ad["modifiers"] if m["name"] == "Mild")
    assert mild["attach_rate"] == round(112 / (k1_qty + 3 * 28), 3)
    # More add-ons than parent items sold = the menu doesn't list every parent: unknown, not 133%.
    mm = insights_data.menu_map(db)
    mm["modifiers"]["m2"]["parents"] = ["k2"]  # 4 Mild a day vs 3 biryanis
    mm.pop("fetched_at")
    insights_data.store(db, insights_data.MENU_MAP_KEY, None, [], mm)
    db.commit()
    ad = admin_c.get("/api/insights/addons?days=28").json()
    assert next(m for m in ad["modifiers"] if m["name"] == "Mild")["attach_rate"] is None

    pf = admin_c.get("/api/insights/prep-forecast").json()
    assert pf["date"] == "2026-10-13" and "last 8 Tuesdays" in pf["basis"]
    k1 = next(i for i in pf["items"] if i["item_guid"] == "k1")
    assert set(k1) >= {"item_guid", "name", "expected_qty", "low", "high", "by_hour"}
    factor = min(1.2, 1 + (208 / 152 - 1) * 0.5)
    assert k1["expected_qty"] == round(9 * factor, 1)
    assert k1["by_hour"] and all(h["hour"] in HOURS for h in k1["by_hour"])
    assert sum(h["qty"] for h in k1["by_hour"]) == pytest.approx(k1["expected_qty"], abs=0.3)
    assert all(i["item_guid"].startswith("k") for i in pf["items"])  # kitchen only

    rc = admin_c.get("/api/insights/returning-customers?weeks=8").json()
    wk = rc["weeks"][-1]
    assert wk["week_start"] == "2026-10-05" and wk["card_customers"] == 3 and wk["returning"] == 2
    assert wk["returning_rate"] == round(2 / 3, 4)
    assert wk["avg_visits"] == round((7 + 1 + 1) / 3, 2)  # C daily, A Sunday, B once
    assert rc["overall"]["card_customers"] == 3


def test_loss_watch_and_odd_checks(db, filled, admin_c):
    lw = admin_c.get("/api/insights/loss-watch?days=30").json()
    assert set(lw) >= {"daily", "by_employee", "flagged_checks"}
    y = next(d for d in lw["daily"] if d["date"] == "2026-10-11")
    assert set(y) >= {"date", "discount", "void", "refund", "comp_estimate", "discount_checks", "void_checks"}
    assert y["comp_estimate"] == 47.97 and y["void"] == 20.0 and y["void_checks"] == 1
    assert y["discount"] == round(5 + 47.97 + 60 + 26, 2) and y["refund"] == 30.0
    flagged = {f["check_number"]: f for f in lw["flagged_checks"]}
    assert set(flagged) == {"101", "102", "103", "104"}
    assert flagged["101"]["reason_flag"] == "full_comp"
    assert flagged["102"]["reasons"] == ["large_discount", "above_employee_median"]
    assert flagged["103"]["reason_flag"] == "large_refund"
    assert flagged["104"]["reasons"] == ["above_employee_median"]
    assert all(f["id"] and f["acked"] is False for f in flagged.values())
    emp = {e["employee"]: e for e in lw["by_employee"]}
    assert emp["Asha Kumar"]["unusual"] is True and emp["Asha Kumar"]["median_loss"] == 5.0
    assert set(lw["by_employee"][0]) >= {"employee", "checks", "discount", "void", "refund", "unusual"}

    odd = admin_c.get("/api/insights/odd-checks?days=7").json()
    items = {i["check_number"]: i for i in odd["items"]}
    assert set(items) == {"103", "105", "106", "107"}
    assert items["106"]["why"] == ["open_long"] and items["106"]["minutes_open"] == 276
    assert items["105"]["why"] == ["open_long"] and items["105"]["paid_at"] is None
    assert items["107"]["why"] == ["modified_after_paid"] and items["107"]["modified_after_paid"] is True
    assert items["103"]["why"] == ["large_refund"]
    assert set(items["107"]) >= {
        "date", "check_number", "opened_at", "paid_at", "minutes_open", "modified_after_paid", "refund", "total", "employee", "why",
    }


def test_acknowledge_a_flag(db, filled, admin_c, staff_c):
    lw = admin_c.get("/api/insights/loss-watch?days=30").json()
    fid = lw["flagged_checks"][0]["id"]
    assert staff_c.post(f"/api/insights/loss-watch/{fid}/ack").status_code == 403
    res = admin_c.post(f"/api/insights/loss-watch/{fid}/ack")
    assert res.status_code == 200 and res.json()["acked"] is True and res.json()["acked_by"] == "boss"
    again = admin_c.post(f"/api/insights/loss-watch/{fid}/ack").json()
    assert again["acked_at"] == res.json()["acked_at"]
    assert admin_c.post("/api/insights/loss-watch/999999/ack").status_code == 404
    lw = admin_c.get("/api/insights/loss-watch?days=30").json()
    assert next(f for f in lw["flagged_checks"] if f["id"] == fid)["acked"] is True


# --------------------------------------------------------------------------- permissions


def test_permissions(db, filled):
    nobody = client_for(db, make_user(db, "nobody", features=["items_sold"]))
    staff = client_for(db, make_user(db, "ravi", features=["sales_insights"]))
    hidden_user = make_user(db, "cashier", features=["sales_insights"])
    hidden_user.hide_sales = True
    hidden_admin = make_user(db, "boss2", is_admin=True)
    hidden_admin.hide_sales = True
    db.commit()
    hidden = client_for(db, hidden_user)
    admin_hidden = client_for(db, hidden_admin)
    for url in ENDPOINTS:
        assert nobody.get(url).status_code == 403, url
        assert staff.get(url).status_code == (403 if url in ADMIN_ONLY else 200), url
        want = 200 if url in COUNTS_OK_FOR_HIDDEN else 403
        assert hidden.get(url).status_code == want, url
        assert admin_hidden.get(url).status_code == 200, url  # admins always see sales
    assert nobody.post("/api/insights/loss-watch/1/ack").status_code == 403
    heat = hidden.get("/api/insights/hourly-heatmap?weeks=4").json()
    text = json.dumps(heat)
    assert "net_sales" not in text and heat["sales_hidden"] is True
    assert set(heat["cells"][0]) == {"dow", "hour", "avg_orders"}
    assert heat["busiest"] and set(heat["busiest"][0]) == {"dow", "hour", "avg_orders"}
    for url in ("/api/insights/prep-forecast", "/api/insights/returning-customers?weeks=8"):
        assert "net_sales" not in hidden.get(url).text and "$" not in hidden.get(url).text
    assert staff.get("/api/insights/hourly-heatmap?weeks=5").status_code == 422
    assert staff.get("/api/insights/menu-performance?group=bakery").status_code == 422
    assert staff.get("/api/insights/compare?date=2026-12-01").status_code == 400


def test_demo_login_reads_but_never_acknowledges(db, filled, monkeypatch):
    demo = make_user(db, "app_store_review", is_admin=True)
    c = client_for(db, demo)
    assert c.get("/api/insights/loss-watch?days=30").status_code == 200
    fid = db.query(LossWatchFlag).first().id
    res = c.post(f"/api/insights/loss-watch/{fid}/ack")
    assert res.status_code == 403
    assert db.get(LossWatchFlag, fid).acked_at is None


def test_feature_is_listed_and_never_auto_granted(db):
    from app.features import FEATURES, get_allowed_features

    assert {"key": "sales_insights", "label": "Sales insights"} in FEATURES
    assert "sales_insights" not in get_allowed_features(make_user(db, "x", features=["overview"]))


# --------------------------------------------------------------------------- caching / stale


def test_not_configured_answers_stale_never_500(db, admin_c):
    assert insights_data.is_configured() is False
    assert insights_data.run_nightly(db) is None
    for url in ENDPOINTS:
        res = admin_c.get(url)
        assert res.status_code == 200, url
        body = res.json()
        assert body["stale"] is True and body["configured"] is False


def test_cached_answers_survive_toast_failing(db, filled, admin_c):
    before = admin_c.get("/api/insights/check-trends?weeks=4").json()
    filled.fail = {k: toast_analytics.AnalyticsReportFailed("HTTP 500: boom") for k in ("metrics", "menu", "check", "guest/payments")}
    job = insights_data.run_nightly(db)
    assert job.status == "failed"
    after = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert after["days"] == before["days"] and after["stale"] is False
    # Today live: Toast down and nothing cached -> stale, no numbers, no 500.
    res = admin_c.get(f"/api/insights/compare?date={TODAY.isoformat()}")
    assert res.status_code == 200 and res.json()["stale"] is True and res.json()["net_sales"] is None


def test_past_dates_are_reused_and_only_changed_days_repulled(db, filled, monkeypatch):
    monkeypatch.setattr(get_settings(), "insights_check_daily_budget", 500)
    filled.calls.clear()
    insights_data.run_nightly(db)
    by = lambda kind, **kw: [c for c in filled.calls if c["kind"] == kind and all(c.get(k) == v for k, v in kw.items())]
    # Week reports: just the 14-day re-pull window (2 week calls each).
    assert len(by("metrics", group=None, params=None)) == 2
    assert len(by("metrics", group=["DINING_OPTION"])) == 2
    assert len(by("menu", group=["MENU_ITEM"])) == 2 and len(by("menu", group=["MODIFIER"])) == 2
    # Hourly / check: nothing changed -> nothing re-pulled.
    assert by("metrics", params={"aggregateBy": "HOUR"}) == [] and by("check") == []

    changed = YESTERDAY - dt.timedelta(days=2)
    filled.overrides[changed] = 123.45
    filled.calls.clear()
    insights_data.run_nightly(db)
    assert [c["start"] for c in by("metrics", params={"aggregateBy": "HOUR"})] == [changed]
    assert [c["start"] for c in by("check")] == [changed]
    filled.calls.clear()
    insights_data.run_nightly(db)
    assert by("metrics", params={"aggregateBy": "HOUR"}) == [] and by("check") == []


def test_provisional_copy_is_fetched_again(db, fake):
    day = YESTERDAY
    insights_data.store(db, "metrics_day", day, [{"netSalesAmount": 1.0, "ordersCount": 1}])
    db.commit()
    row = db.query(AnalyticsCache).filter_by(report="metrics_day").one()
    row.fetched_at = insights_data.closeout_at(day) - dt.timedelta(hours=1)  # fetched while the day was open
    db.commit()
    entry = insights_data.cached(db, "metrics_day", [day])[day]
    assert entry.provisional and insights_data.needs_fetch(entry, day, TODAY)
    res = insights_data.fill(db, "metrics_day", [day], max_calls=5, today_bd=TODAY)
    assert res["calls"] == 1
    entry = insights_data.cached(db, "metrics_day", [day])[day]
    assert not entry.provisional and entry.rows[0]["netSalesAmount"] == day_net(day)


def test_today_is_live_with_a_short_cache(db, fake, admin_c):
    url = f"/api/insights/compare?date={TODAY.isoformat()}"
    first = admin_c.get(url).json()
    assert first["partial"] is True and first["net_sales"] == day_net(TODAY)
    assert len(fake.calls) == 1 and fake.calls[0]["range"] == "day" and fake.calls[0]["start"] == TODAY
    admin_c.get(url)
    assert len(fake.calls) == 1, "second look within the cache window reuses the pull"
    row = db.query(AnalyticsCache).filter_by(report="metrics_day", business_date=TODAY).one()
    row.fetched_at = row.fetched_at - dt.timedelta(seconds=get_settings().insights_today_cache_seconds + 5)
    db.commit()
    admin_c.get(url)
    assert len(fake.calls) == 2


def test_missing_dates_start_one_background_fill_in_production_only(db, fake, admin_c, monkeypatch):
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert body["stale"] is True and body["coverage"]["dates_cached"] == 0
    assert fake.spawned == []  # SCHEDULER_ENABLED=false (dev/tests): never calls Toast on its own
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    admin_c.get("/api/insights/check-trends?weeks=4")
    assert len(fake.spawned) == 1
    fake.spawned[0]()  # run the fill here
    body = admin_c.get("/api/insights/check-trends?weeks=4").json()
    assert body["stale"] is False and len(body["days"]) == 28


# --------------------------------------------------------------------------- rate limits / budget


def test_daily_budget_caps_check_and_guest_reports(db, fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "insights_check_daily_budget", 5)
    monkeypatch.setattr(get_settings(), "insights_guest_daily_budget", 2)
    monkeypatch.setattr(insights_data, "DAILY_RESERVE", {})  # the reserve has its own test
    insights_data.run_nightly(db)
    checks = [c for c in fake.calls if c["kind"] == "check"]
    guests = [c for c in fake.calls if c["kind"] == "guest/payments"]
    assert len(checks) == 5 and [c["start"] for c in checks][0] == YESTERDAY  # newest first
    assert len(guests) == 2
    assert db.query(AnalyticsCallLog).filter_by(family="check").count() == 5
    # The budget is in the database: a second run (or a restart) spends nothing.
    fake.calls.clear()
    insights_data.run_nightly(db)
    assert not [c for c in fake.calls if c["kind"] in ("check", "guest/payments")]
    assert insights_data.daily_left(db, "check") == 0
    with pytest.raises(insights_data.BudgetExhausted):
        insights_data.fetch_range(db, "check", YESTERDAY, YESTERDAY)
    # A day later the budget is back.
    for row in db.query(AnalyticsCallLog).all():
        row.called_at = row.called_at - dt.timedelta(hours=25)
    db.commit()
    assert insights_data.daily_left(db, "check") == 5


def test_rate_limited_report_stops_without_blocking_the_others(db, fake, admin_c):
    fake.fail = {"menu": toast_analytics.AnalyticsRateLimited("Toast Analytics rate limit for POST /era/v1/menu/week")}
    job = insights_data.run_nightly(db)
    detail = json.loads(job.detail)
    assert "rate limit" in detail["menu_item"]["error"] and detail["menu_item"]["calls"] == 0
    assert len([c for c in fake.calls if c["kind"] == "menu"]) == 2  # one try each, then stop
    assert detail["metrics_day"]["error"] is None and detail["metrics_day"]["calls"] == 13
    res = admin_c.get("/api/insights/menu-performance")
    assert res.status_code == 200 and res.json()["stale"] is True


def test_report_budget_wait_is_short_on_request_paths(db, fake, monkeypatch):
    seen = []
    real = fake.__call__

    def spy(kind, time_range, body, params=None, **kw):
        seen.append(kw.get("max_budget_wait"))
        return real(kind, time_range, body, params, **kw)

    monkeypatch.setattr(insights_data, "_run_report", spy)
    insights_data.live_fetch(db, "metrics_day", TODAY)
    assert seen == [3.0]


# --------------------------------------------------------------------------- privacy


def test_no_card_data_is_stored_or_returned(db, filled, admin_c):
    dump = []
    for table in ("analytics_cache", "guest_visits", "analytics_call_log", "loss_watch_flags", "job_runs"):
        dump.extend(str(r) for r in db.execute(__import__("sqlalchemy").text(f"SELECT * FROM {table}")).all())
    for url in ENDPOINTS:
        dump.append(admin_c.get(url).text)
    blob = "\n".join(dump)
    for secret in CARD_SECRETS + ("cardFingerprint", "cardLast4Digits", "paymentAccountReference", "cardHolderName"):
        assert secret not in blob, secret
    hashes = {v.card_hash for v in db.query(GuestVisit).all()}
    assert len(hashes) == 3 and all(len(h) == 40 for h in hashes)
    assert insights_data.card_hash("fp-ALPHA-raw") in hashes
    marker = insights_data.cached(db, "guest", [YESTERDAY])[YESTERDAY]
    assert marker.rows == [] and marker.meta == {"payments": 5, "card_payments": 4, "cards": 3}


def test_hmac_depends_on_the_server_secret(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "insights_hmac_secret", "one-secret-one-secret-one-secret")
    a = insights_data.card_hash("fp")
    monkeypatch.setattr(s, "insights_hmac_secret", "two-secret-two-secret-two-secret")
    assert insights_data.card_hash("fp") != a
    # No secret configured: a random key file, created once with 0600, reused.
    monkeypatch.setattr(s, "insights_hmac_secret", "")
    key_file = tmp_path / "k" / "insights_hmac.key"
    monkeypatch.setattr(s, "insights_hmac_key_file", str(key_file))
    first = insights_data.card_hash("fp")
    assert key_file.exists() and oct(key_file.stat().st_mode & 0o777) == "0o600"
    insights_data._key_cache.clear()
    assert insights_data.card_hash("fp") == first


def test_guest_visits_are_kept_400_days_at_most(db, fake):
    old = TODAY - dt.timedelta(days=401)
    db.add(GuestVisit(visit_date=old, card_hash="x" * 40, payments=1))
    db.add(GuestVisit(visit_date=TODAY - dt.timedelta(days=399), card_hash="y" * 40, payments=1))
    insights_data.store(db, "check", TODAY - dt.timedelta(days=121), [{"serverName": "Old Name"}])
    db.commit()
    out = insights_data.purge_old(db, TODAY)
    assert out["guest_visits"] == 1 and out["check"] == 1
    assert {v.card_hash for v in db.query(GuestVisit).all()} == {"y" * 40}


# --------------------------------------------------------------------------- thresholds


def _c(**kw):
    base = {
        "date": YESTERDAY, "check_guid": "g", "check_number": "1", "employee": "E", "total": 20.0, "discount": 0.0,
        "refund": 0.0, "void": 0.0, "voided": False, "status": "CLOSED", "opened_at": None, "paid_at": None,
        "modified_at": None, "revenue_center": "In-Store Purchase", "dining_option": "In-Store Purchase",
        "fetched_at": None,
    }
    base.update(kw)
    base["loss"] = round(base["discount"] + base["refund"] + base["void"], 2)
    return base


def test_loss_thresholds(monkeypatch):
    rs = insights.loss_reasons
    assert rs([_c(discount=49.99)]) == {}
    assert rs([_c(discount=50.0)]) == {"g": ["large_discount"]}
    assert rs([_c(total=0.0, discount=8.0)]) == {"g": ["full_comp"]}
    assert rs([_c(total=0.0, discount=8.0, voided=True)]) == {}
    assert rs([_c(refund=25.0)]) == {"g": ["large_refund"]} and rs([_c(refund=24.99)]) == {}
    assert rs([_c(void=50.0)]) == {"g": ["large_void"]}
    history = [_c(check_guid=f"h{i}", discount=5.0) for i in range(5)]
    assert rs(history + [_c(discount=25.0)])["g"] == ["above_employee_median"]
    assert "g" not in rs(history + [_c(discount=24.99)])  # under the $25 floor
    mid = [_c(check_guid=f"m{i}", discount=10.0) for i in range(5)]
    assert "g" not in rs(mid + [_c(discount=30.0)])  # not above 3x
    assert "g" not in rs(history[:3] + [_c(discount=40.0)])  # too few checks for a median
    low = [_c(check_guid=f"l{i}", discount=1.0) for i in range(5)]
    assert "g" not in rs(low + [_c(discount=9.0)])  # under the floor
    monkeypatch.setattr(get_settings(), "loss_watch_large_discount", 30.0)
    assert rs([_c(discount=30.0)]) == {"g": ["large_discount"]}


def test_odd_check_thresholds(monkeypatch):
    t = lambda h, m=0: dt.datetime(2026, 10, 11, h, m, tzinfo=dt.timezone.utc)
    rs = insights.odd_reasons
    assert rs([_c(opened_at=t(12), paid_at=t(15, 1))])["g"][0] == ["open_long"]
    assert rs([_c(opened_at=t(12), paid_at=t(15))])["g"][0] == []
    monkeypatch.setattr(get_settings(), "odd_check_open_hours", 2.0)
    assert rs([_c(opened_at=t(12), paid_at=t(14, 1))])["g"][0] == ["open_long"]
    # A scheduled order's open time is the pickup time (after payment): never "open long".
    assert rs([_c(opened_at=t(20), paid_at=t(15))])["g"][1] is None
    after_close = dt.datetime(2026, 10, 12, 8, 0, tzinfo=dt.timezone.utc)  # 4 AM store time
    assert rs([_c(paid_at=t(15), modified_at=after_close)])["g"][0] == ["modified_after_paid"]
    assert rs([_c(paid_at=t(15), modified_at=after_close - dt.timedelta(seconds=1))])["g"][0] == []
    assert rs([_c(paid_at=t(15), modified_at=t(18))])["g"][0] == [], "same-day edits are routine"
    kiosk = _c(paid_at=t(15), modified_at=after_close, dining_option="Pick Up")
    assert rs([kiosk])["g"][0] == []
    online = _c(paid_at=t(15), modified_at=after_close, revenue_center="Online Ordering")
    assert rs([online])["g"][0] == []
    assert rs([dict(online, refund=30.0)])["g"][0] == ["modified_after_paid", "large_refund"]
    batch = [_c(check_guid=f"b{i}", paid_at=t(15), modified_at=after_close) for i in range(3)]
    assert all(v[0] == [] for v in rs(batch).values())


def test_menu_map_kitchen_rule():
    mm = insights_data.build_menu_map(MENUS)
    items = mm["items"]
    assert items["k1"]["kitchen"] and items["k1"]["group"] == "Chaat Bar"
    assert items["k4"]["kitchen"], "house drinks are made in the kitchen"
    assert items["k5"]["kitchen"], "the (GH) Street Kitchen menu counts"
    assert not items["g2"]["kitchen"], "canned drinks are retail stock"
    assert not items["g1"]["kitchen"] and items["g3"]["group"] == "Rice & Rice Products"
    assert "t1" not in items
    assert mm["modifiers"]["m1"] == {"name": "Extra Cheese", "groups": ["Add-ons"], "parents": ["k1"]}
    assert mm["modifiers"]["m2"]["parents"] == ["k1", "k2"]
    # The Menus API answer as the saved evidence file wraps it ({"data": {...}}).
    assert insights_data.build_menu_map({"data": MENUS})["items"].keys() == items.keys()


def test_channel_names():
    assert insights.channel_of("In-Store Purchase") == "In-store"
    assert insights.channel_of("Pick Up") == "Pick up"
    assert insights.channel_of("Online Ordering - Takeout") == "Online ordering"
    assert insights.channel_of("DoorDash - Delivery") == insights.channel_of("DoorDash - Takeout") == "DoorDash"
    assert insights.channel_of("Uber Eats - Delivery") == "Uber Eats"
    assert insights.channel_of("Catering - Pick Up") == "Catering"
    assert insights.channel_of("No Dining Option") == insights.channel_of(None) == "Other"


# --------------------------------------------------------------------------- alerts


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


def _device(db, user):
    db.add(DeviceToken(user_id=user.id, token=f"tok-{user.username}", platform="ios"))
    db.commit()


def test_loss_watch_alert_is_one_9am_push_to_admins(db, filled, pushes):
    admin = make_user(db, "boss", is_admin=True)
    staff = make_user(db, "ravi", features=["sales_insights"])
    demo = make_user(db, "app_store_review", is_admin=True)
    for u in (admin, staff, demo):
        _device(db, u)
        if u.is_admin:  # off by default since Oct 15 (a Morning summary line)
            notifications.set_pref(db, u, "loss_watch", True)
    assert notifications.ALERTS_BY_KEY["loss_watch"]["admin_only"] is True
    assert notifications.ALERTS_BY_KEY["loss_watch"]["route"] == "loss_watch"
    assert "loss_watch" in TRIGGERS
    nine = dt.datetime(2026, 10, 12, 9, 0, tzinfo=NY)
    assert run_alert_job("loss_watch", now=nine.replace(hour=23), db=db) == 0  # quiet hours
    assert pushes == []
    assert run_alert_job("loss_watch", now=nine, db=db) == 1
    (p,) = pushes
    assert p["tokens"] == ["tok-boss"] and p["data"]["route"] == "loss_watch" and p["data"]["date"] == "2026-10-11"
    assert p["title"] == "Loss watch: 7 checks to review"
    assert "1 full comp" in p["body"] and "2 checks open too long" in p["body"] and "Asha Kumar" in p["body"]
    assert run_alert_job("loss_watch", now=nine, db=db) == 0, "each flag is in one push only"
    assert len(pushes) == 1


def test_acknowledged_flags_never_push(db, filled, pushes):
    admin = make_user(db, "boss", is_admin=True)
    _device(db, admin)
    insights.refresh_flags(db, TODAY)
    for f in db.query(LossWatchFlag).all():
        insights.ack_flag(db, f.id, admin)
    assert run_alert_job("loss_watch", now=dt.datetime(2026, 10, 12, 9, 0, tzinfo=NY), db=db) == 0
    assert pushes == []


def test_daily_sales_summary_push_skips_hidden_sales(db, filled, pushes):
    admin = make_user(db, "boss", is_admin=True)
    staff = make_user(db, "ravi", features=["sales_insights"])
    hidden = make_user(db, "cashier", features=["sales_insights"])
    hidden.hide_sales = True
    demo = make_user(db, "app_store_review", is_admin=True)
    db.commit()
    for u in (admin, staff, hidden, demo):
        _device(db, u)
        notifications.set_pref(db, u, "daily_sales_summary", True)  # off by default since Oct 15
    assert run_alert_job("daily_sales_summary", now=dt.datetime(2026, 10, 12, 9, 0, tzinfo=NY), db=db) == 2
    (p,) = pushes
    assert p["tokens"] == ["tok-boss", "tok-ravi"]
    assert p["title"] == f"Sunday: ${day_net(YESTERDAY):,.0f} net sales"
    assert "vs last Sun ▲" in p["body"] and "vs 4-week avg" in p["body"] and f"{day_orders(YESTERDAY)} orders" in p["body"]


def test_weekly_digest_carries_the_compare(db, filled):
    from app.services import weekly_digest

    data = weekly_digest.get_weekly_digest_data(db, YESTERDAY)
    tc = data["sales"]["toast_compare"]
    week = [YESTERDAY - dt.timedelta(days=k) for k in range(7)]
    assert tc["net_sales"] == round(sum(day_net(d) for d in week), 2)
    assert tc["vs_last_week"]["pct"] is not None and tc["vs_4wk_avg"]["weeks_used"] == 4
    narrative = {"sales": "Sales were steady."}
    weekly_digest._append_compare_line(narrative, data)
    assert narrative["sales"].startswith("Sales were steady. Toast Analytics: $")
    assert "vs the week before" in narrative["sales"]
    # Hide-sales readers never get it (the whole sales block is dropped).
    narrative = {"sales": None}
    weekly_digest._append_compare_line(narrative, {"sales": None})
    assert narrative["sales"] is None


# --------------------------------------------------------------------------- real client


def test_real_client_end_to_end_for_guest_payments(db, monkeypatch):
    """The real toast_analytics.run_report over a mocked transport: the guest
    report goes to /era/v1/guest/payments/week with startDate/endDate, and
    only HMACs come out the other side."""
    from tests.test_sales_accuracy_job import Clock, Script, login_ok

    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "cid")
    monkeypatch.setattr(s, "toast_analytics_client_secret", "never-shown")
    monkeypatch.setattr(s, "toast_analytics_base", "https://analytics.test")
    monkeypatch.setattr(s, "toast_restaurant_guid", REST)
    monkeypatch.setattr(s, "insights_hmac_secret", "test-hmac-secret-0123456789abcdef")
    clock = Clock()
    monkeypatch.setattr(toast_analytics, "_sleep", clock.sleep)
    monkeypatch.setattr(toast_analytics, "_now", clock.now)
    toast_analytics.reset_state()
    rows = FakeToast().guest_rows(YESTERDAY) + FakeToast().guest_rows(YESTERDAY - dt.timedelta(days=1))
    script = login_ok(Script())
    script.on("POST", "/era/v1/guest/payments/week", (200, "g-guest"))
    script.on("GET", "/era/v1/guest/payments/g-guest", (202, {}), (200, rows))
    script.on("POST", "/era/v1/metrics/day", (200, "g-hour"))
    script.on("GET", "/era/v1/metrics/g-hour", (200, FakeToast().rows("metrics", YESTERDAY, None, {"aggregateBy": "HOUR"})))
    client = script.client()
    monkeypatch.setattr(
        insights_data, "_run_report", lambda *a, **k: toast_analytics.run_report(*a, client=client, **k)
    )
    insights_data.fetch_range(db, "guest", YESTERDAY - dt.timedelta(days=1), YESTERDAY)
    insights_data.fetch_range(db, "metrics_hour", YESTERDAY, YESTERDAY)
    post = next(r for r in script.requests if r.url.path == "/era/v1/guest/payments/week")
    body = json.loads(post.content)
    assert body["startDate"] == "20261010" and body["endDate"] == "20261011" and body["restaurantIds"] == [REST]
    hour_post = next(r for r in script.requests if r.url.path == "/era/v1/metrics/day")
    assert hour_post.url.params["aggregateBy"] == "HOUR"
    assert db.query(GuestVisit).count() == 4  # C both days, A Sunday, B yesterday
    assert db.query(AnalyticsCallLog).filter_by(family="guest", ok=1).count() == 1
    entry = insights_data.cached(db, "metrics_hour", [YESTERDAY])[YESTERDAY]
    assert sum(r["netSalesAmount"] or 0 for r in entry.rows) == pytest.approx(day_net(YESTERDAY))
    toast_analytics.reset_state()


def test_glossary_covers_every_reason_and_quotes_the_defaults():
    from app import glossary as g
    from app.config import Settings

    d = {name: f.default for name, f in Settings.model_fields.items()}
    for kind, keys in insights.REASON_GLOSSARY_KEYS.items():
        for reason, key in keys.items():
            assert key in g.TERMS_BY_KEY, (kind, reason, key)
    text = lambda k: g.TERMS_BY_KEY[k]["definition"] + " " + (g.TERMS_BY_KEY[k]["rule"] or "")
    assert f"${d['loss_watch_large_discount']:g}" in text("insights_loss_large_discount")
    assert f"${d['loss_watch_large_refund']:g}" in text("insights_loss_large_refund")
    assert f"{d['odd_check_open_hours']:g} hours" in text("insights_odd_open_long")
    assert f"{insights_data.GUEST_RETENTION_DAYS} days" in text("insights_card_customers")
    assert f"{insights.PREP_WEEKS} weeks" in text("insights_prep_expected")
    assert "4:30 AM" in text("insights_source")
    for key in ("insights_source", "insights_stale", "insights_heatmap", "insights_channels", "insights_compare",
                "insights_check_trends", "insights_loss_watch", "insights_odd_checks", "insights_menu",
                "insights_addons", "insights_prep", "insights_returning"):
        assert g.TERMS_BY_KEY[key]["category"] == "insights"
