"""Ops fixes (B): the margin report is gone everywhere, negative inventory is
still served for old app builds, and every report in the app's More > Reports
(and the web Inventory Reports page) has a CSV download."""

import csv
import datetime as dt
import io

import pytest

from app.models import Order, PurchaseLogEntry
from app.timeutil import restaurant_today, utcnow
from tests.conftest import client_for, make_user


def _csv(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    return list(csv.reader(io.StringIO(response.text)))


# --- margin report removed ------------------------------------------------


@pytest.mark.parametrize("path", ["/api/reports/margin", "/api/reports/margin/export"])
def test_margin_routes_are_gone(admin_client, path):
    # 410 + a message old app builds show (they still link the screen), not a bare 404.
    assert admin_client.get(path).status_code == 410


def test_margin_service_removed_but_dead_stock_and_negative_kept():
    from app.services import inventory_intelligence as ii

    assert not hasattr(ii, "get_margin_report")
    assert callable(ii.get_dead_stock)
    assert callable(ii.get_negative_inventory)


def test_ask_bot_has_no_margin_tool(db):
    from app.services.ask_bot import _build_tools

    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in _build_tools(db)}
    assert "margin_report" not in names
    assert "dead_stock" in names


def test_weekly_digest_data_has_no_margin(db):
    from app.services.weekly_digest import get_weekly_digest_data

    data = get_weekly_digest_data(db, dt.date(2026, 9, 1))
    assert "margin_losers" not in data
    assert "dead_stock" in data


def test_negative_inventory_endpoint_still_served(admin_client):
    r = admin_client.get("/api/reports/negative-inventory")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "items": []}
    assert admin_client.get("/api/reports/negative-inventory/export").status_code == 200


# --- existing exports still work -----------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/reports/vendor-price-comparison/export",
        "/api/inventory/price-changes/export",
        "/api/reconciliation/report/export?start=2026-09-01&end=2026-09-07",
    ],
)
def test_existing_exports(admin_client, path):
    r = admin_client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")


# --- Items Sold -------------------------------------------------------------


def _order(db, guid, name, qty, net, day):
    paid = dt.datetime.combine(day, dt.time(17, 0), tzinfo=dt.timezone.utc)
    db.add(
        Order(
            toast_order_guid=f"o-{guid}",
            toast_check_guid=f"c-{guid}",
            toast_selection_guid=f"s-{guid}",
            business_date=day,
            opened_at=paid,
            paid_at=paid,
            item_guid=f"item-{name}",
            item_name=name,
            quantity=qty,
            unit_price=net / qty,
            net_price=net,
            tax_amount=0,
            voided=False,
        )
    )


def test_items_sold_export(db, admin_client):
    day = restaurant_today() - dt.timedelta(days=5)
    _order(db, 1, "Samosa", 2, 6.0, day)
    _order(db, 2, "Samosa", 1, 3.0, day)
    _order(db, 3, "Chai, large", 4, 12.0, day)
    db.commit()

    rows = _csv(admin_client.get(f"/api/sales/items-sold/export?start={day}&end={day}"))
    assert rows[0] == ["Item", "Qty Sold", "Revenue", "Avg Price"]
    assert rows[1] == ["Chai, large", "4.0", "12.0", "3.0"]  # highest revenue first, comma kept quoted
    assert rows[2] == ["Samosa", "3.0", "9.0", "3.0"]


def test_items_sold_export_empty_still_has_headers(admin_client):
    day = restaurant_today() - dt.timedelta(days=5)
    rows = _csv(admin_client.get(f"/api/sales/items-sold/export?start={day}&end={day}"))
    assert rows == [["Item", "Qty Sold", "Revenue", "Avg Price"]]


def test_items_sold_export_rejects_backwards_range(admin_client):
    assert admin_client.get("/api/sales/items-sold/export?start=2026-09-05&end=2026-09-01").status_code == 400


def test_items_sold_export_is_gated(db):
    no_access = client_for(db, make_user(db, "nosales", features=["open_orders"]))
    has_access = client_for(db, make_user(db, "sales", features=["items_sold"]))
    day = restaurant_today() - dt.timedelta(days=5)
    assert no_access.get(f"/api/sales/items-sold/export?start={day}&end={day}").status_code == 403
    assert has_access.get(f"/api/sales/items-sold/export?start={day}&end={day}").status_code == 200


# --- Open Orders ------------------------------------------------------------

_FAKE_ORDER = {
    "guid": "g1",
    "business_date": "2026-09-29",
    "display_number": "42",
    "opened_at": "2026-09-29T15:00:00+00:00",
    "elapsed_minutes": 30,
    "server_guid": "s1",
    "server_name": "Ravi",
    "num_guests": 2,
    "approval_status": "APPROVED",
    "total_amount": 18.5,
    "num_checks": 1,
    "tab_names": ["Priya"],
    "tab_name": "Priya",
    "line_items": [{"name": "Biryani", "quantity": 2.0}, {"name": "Lassi", "quantity": 1}],
    "paid_at": None,
    "promised_at": None,
    "scheduled": False,
}


def test_open_orders_export_day(monkeypatch, admin_client):
    calls = []

    def fake(date, employee_guid=None):
        calls.append((date, employee_guid))
        return [_FAKE_ORDER]

    monkeypatch.setattr("app.routers.orders.get_open_orders", fake)
    rows = _csv(admin_client.get("/api/orders/open/export?date=2026-09-29&employee_guid=s1"))
    assert calls == [(dt.date(2026, 9, 29), "s1")]
    assert rows[0][:4] == ["Business Date", "Check #", "Tab", "Opened At"]
    assert rows[1][1] == "42"
    assert rows[1][2] == "Priya"
    assert rows[1][3] == "2026-09-29 11:00"  # 15:00 UTC shown in store-local (EDT) time
    assert rows[1][4] == "Ravi"
    assert rows[1][10] == ""  # not paid yet - blank, not "None"
    assert rows[1][9] == "2x Biryani; 1x Lassi"
    assert rows[1][12] == "No"


def test_open_orders_export_all_time_empty(admin_client):
    rows = _csv(admin_client.get("/api/orders/open/export?mode=all_time"))
    assert len(rows) == 1 and rows[0][0] == "Business Date"


def test_open_orders_export_is_gated(db):
    no_access = client_for(db, make_user(db, "noorders", features=["items_sold"]))
    assert no_access.get("/api/orders/open/export?mode=all_time").status_code == 403


# --- Reconciliation ---------------------------------------------------------


def test_invoice_gaps_export(monkeypatch, admin_client):
    monkeypatch.setattr(
        "app.routers.reconciliation.get_invoice_gaps",
        lambda db, start, end, window: [
            {
                "id": 7,
                "vendor": "Shaz International",
                "submission_type": "return",
                "total_amount": 40.0,
                "submission_date": "2026-09-10",
                "submitted_by_name": "sam",
                "file_url": "https://example/secret",
            }
        ],
    )
    rows = _csv(admin_client.get("/api/reconciliation/invoice-gaps/export?start=2026-09-01&end=2026-09-30"))
    assert rows == [
        ["Date", "Vendor", "Type", "Total", "Submitted By"],
        ["2026-09-10", "Shaz International", "Return", "40.0", "sam"],
    ]


def test_invoice_gaps_export_empty(admin_client):
    rows = _csv(admin_client.get("/api/reconciliation/invoice-gaps/export?start=2026-09-01&end=2026-09-30"))
    assert rows == [["Date", "Vendor", "Type", "Total", "Submitted By"]]


def test_purchases_export(db, admin_client):
    db.add(
        PurchaseLogEntry(
            item_id="i1",
            item_name="Basmati 10lb",
            supplier="Vendor A",
            quantity_received=5,
            unit_cost=12.5,
            received_date=dt.date(2026, 9, 20),
            notes="two bags torn,\nreplaced",
            created_at=utcnow(),
        )
    )
    db.commit()
    rows = _csv(admin_client.get("/api/reconciliation/purchases/export"))
    assert rows[0] == ["Received", "Item", "Vendor", "Qty Received", "Unit Cost", "Notes"]
    assert rows[1] == ["2026-09-20", "Basmati 10lb", "Vendor A", "5.0", "12.5", "two bags torn,\nreplaced"]


def test_reconciliation_exports_are_gated(db):
    no_access = client_for(db, make_user(db, "norecon", features=["items_sold"]))
    assert no_access.get("/api/reconciliation/purchases/export").status_code == 403
    assert (
        no_access.get("/api/reconciliation/invoice-gaps/export?start=2026-09-01&end=2026-09-30").status_code == 403
    )


def test_open_orders_export_day_toast_failure_is_clear(monkeypatch, admin_client):
    def boom(date, employee_guid=None):
        raise RuntimeError("toast down")

    monkeypatch.setattr("app.routers.orders.get_open_orders", boom)
    r = admin_client.get("/api/orders/open/export?mode=day")
    assert r.status_code == 502
    assert "Toast" in r.json()["detail"]
