"""CSV-only connector (POS_PROVIDER=csv, M2 batch MT13): no POS calls, only
uploaded catalog files; the M1 gating hides and drops everything else."""

import datetime as dt
import pathlib

import pytest

from app.config import get_settings
from app.models import InventorySnapshot, Order
from app.pos import gating, registry
from app.pos.adapters.csv_only import template
from app.pos.adapters.csv_only.connector import CSV_CAPABILITIES, CsvOnlyConnector, CsvParseError
from app.pos.base import NotSupported
from app.pos.capabilities import Capability
from app.services.inventory_parser import parse_and_store


@pytest.fixture
def csv_store(monkeypatch):
    monkeypatch.setattr(get_settings(), "pos_provider", "csv")
    registry.reset_cache()
    yield
    registry.reset_cache()


def test_registered_with_only_csv_import():
    conn = registry.get_connector("csv")
    assert isinstance(conn, CsvOnlyConnector)
    assert conn.source == "csv"
    assert conn.display_name == "CSV upload"
    assert conn.capabilities == CSV_CAPABILITIES == frozenset({Capability.CSV_CATALOG_IMPORT})
    health = conn.health()
    assert health.configured is True and "CSV" in health.detail


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.fetch_orders(dt.date(2026, 10, 1)),
        lambda c: c.fetch_orders_modified(dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 2)),
        lambda c: c.fetch_open_orders(),
        lambda c: c.fetch_employees(),
        lambda c: c.get_day_totals(dt.date(2026, 10, 1), dt.date(2026, 10, 2)),
        lambda c: c.fetch_catalog(),
        lambda c: c.fetch_catalog_snapshot(),
        lambda c: c.catalog_changed_at(),
        lambda c: c.fetch_stock(),
        lambda c: c.run_report("hourly", dt.date(2026, 10, 1), dt.date(2026, 10, 1)),
    ],
)
def test_every_pos_call_is_not_supported(call):
    with pytest.raises(NotSupported):
        call(CsvOnlyConnector())


def test_no_day_totals_and_no_http_client_in_the_package():
    assert CsvOnlyConnector().has_day_totals() is False
    folder = pathlib.Path(template.__file__).parent
    for path in folder.glob("*.py"):
        text = path.read_text()
        for word in ("httpx", "requests", "urllib", "socket", "toast_client"):
            assert f"import {word}" not in text and f"from {word}" not in text, (path.name, word)


def test_parse_template_file():
    content = template.template_csv(
        [
            {"item_id": "A-1", "name": "Rice 10 lb", "category": "Rice", "inventory_quantity": 12, "cost": "$8.50",
             "price": "12.99", "supplier": "Demo Vendor", "barcode": "2000000000008"},
            {"item_id": "", "name": "no id - skipped"},
        ]
    ).encode()
    rows = CsvOnlyConnector().parse_catalog_export("catalog.csv", content)
    assert [r.item_external_id for r in rows] == ["A-1"]
    fields = dict(rows[0].fields)
    assert fields["name"] == "Rice 10 lb"
    assert fields["inventory_quantity"] == 12.0
    assert fields["cost"] == 8.5
    assert fields["price"] == 12.99
    assert fields["supplier"] == "Demo Vendor"
    assert fields["subcategory"] is None


def test_aliases_and_errors():
    conn = CsvOnlyConnector()
    content = b"SKU,Item Name,Qty On Hand,Vendor,UPC,Something Else\nX9,Oats 42 oz,5,Demo Vendor,2000000000015,zz\n"
    rows = conn.parse_catalog_export("export.csv", content)
    fields = dict(rows[0].fields)
    assert rows[0].item_external_id == "X9"
    assert fields == {
        "barcode": "2000000000015",
        "inventory_quantity": 5.0,
        "name": "Oats 42 oz",
        "supplier": "Demo Vendor",
    }
    with pytest.raises(CsvParseError, match="Item ID"):
        conn.parse_catalog_export("x.csv", b"Name,Price\nA,1\n")
    with pytest.raises(CsvParseError, match=".xls"):
        conn.parse_catalog_export("x.xls", b"whatever")
    with pytest.raises(CsvParseError, match="empty"):
        conn.parse_catalog_export("x.csv", b"\n\n")


def test_canonical_csv_goes_through_the_existing_upload(db):
    """A file using aliases, renamed by canonical_csv, loads with today's
    Inventory upload code unchanged."""
    raw = b"SKU,Item Name,Qty On Hand,Vendor,UPC\nX9,Oats 42 oz,5,Demo Vendor,2000000000015\n"
    content = CsvOnlyConnector().canonical_csv("export.csv", raw)
    assert content.splitlines()[0] == b"Item ID,Name,Inventory Quantity,Supplier,Barcode"
    result = parse_and_store(db, "export.csv", content, dt.date(2026, 10, 1))
    assert result["rows_loaded"] == 1 and result["columns_ignored"] == []
    snap = db.query(InventorySnapshot).one()
    assert (snap.item_id, snap.name, snap.inventory_quantity, snap.supplier, snap.barcode) == (
        "X9", "Oats 42 oz", 5.0, "Demo Vendor", "2000000000015",
    )


def test_template_headers_are_all_known_to_the_upload_page():
    from app.pos.adapters.toast.csv_import import COLUMN_MAP

    for header, field in template.TEMPLATE_COLUMNS:
        assert COLUMN_MAP[header.lower()] == field


def test_gating_for_a_csv_store(csv_store):
    assert gating.pos_name() == "CSV upload"
    assert gating.capability_values() == ["csv_catalog_import"]
    keys = ["overview", "open_orders", "sales_insights", "inventory_reports", "invoice_submission"]
    assert gating.features_for_store(keys) == ["overview", "inventory_reports", "invoice_submission"]

    class Job:
        def __init__(self, jid, name):
            self.id = jid
            def func():
                pass

            func.__name__ = name
            self.func = func

    class Sched:
        def __init__(self):
            self.jobs = [Job("a", "run_daily_sales_sync"), Job("b", "run_alert_job"), Job("c", "run_insights_nightly")]

        def get_jobs(self):
            return list(self.jobs)

        def remove_job(self, jid):
            self.jobs = [j for j in self.jobs if j.id != jid]

    sched = Sched()
    assert gating.drop_unsupported_jobs(sched) == ["a", "c"]
    assert [j.id for j in sched.jobs] == ["b"]


def test_items_sold_today_on_a_csv_store_uses_stored_orders(csv_store, db, monkeypatch):
    """No live POS on a CSV store: today comes from the orders table, never
    from the connector (which would raise NotSupported)."""
    from app.services import items_sold
    from app.timeutil import restaurant_today

    today = restaurant_today()
    db.add(Order(toast_order_guid="o", toast_check_guid="c", toast_selection_guid="l", business_date=today,
                 item_guid="X9", item_name="Oats", quantity=2, unit_price=3, net_price=6, tax_amount=0,
                 source="csv"))
    db.commit()
    out = items_sold.get_items_sold(db, today, today)
    assert out["source"] == "stored"
    assert [(i["item_id"], i["quantity"]) for i in out["items"]] == [("X9", 2.0)]
