"""Multi-store M1 batch MT3: the POS-neutral sync jobs (app/pos/sync/) fed by a
fake connector with synthetic `Order` / `Item` / `StockLevel` / `SalesDay`
models - no Toast JSON anywhere. A second POS plugs in exactly like this
(`registry.PROVIDERS`), so these tests are what "works for any POS" means:

- sales: one `orders` row per line (modifiers their own rows), void and
  paid-time rules, idempotent re-run, prune only on a non-empty answer, the
  `toast_sales_sync` job_runs name kept;
- open checks: live view, cache refresh, scan retries + pauses, employees;
- accuracy: day totals compared, a day re-synced, the report label kept;
- catalog: the API set built and written, CSV wins, the live overlay, the
  hourly catalog re-check;
- Items Sold "today" reads the connector.
"""

import datetime as dt
import json
from decimal import Decimal
from typing import List, Optional

import pytest

from app.config import get_settings
from app.models import InventorySnapshot, JobRun, OpenOrderCache, Order as OrderRow, ToastMenuItem
from app.pos import registry
from app.pos.base import BaseConnector, DayTotalsUnavailable
from app.pos.capabilities import Capability
from app.pos.models import (
    CatalogSnapshot,
    Category,
    Check,
    Employee,
    Item,
    Order,
    OrderLine,
    SalesDay,
    StockLevel,
    StockStatus,
)
from app.pos.sync import accuracy, catalog, open_orders, sales
from app.services import inventory_api, items_sold

D = Decimal
DAY = dt.date(2026, 10, 13)
UTC = dt.timezone.utc


def _t(h, m=0, day=13):
    return dt.datetime(2026, 10, day, h, m, tzinfo=UTC)


class FakeConnector(BaseConnector):
    """A made-up POS: answers from in-memory models, records every call."""

    source = "fake"
    display_name = "Fake POS"
    capabilities = frozenset(Capability) - {Capability.ORDERS_INCREMENTAL}

    def __init__(self):
        self.orders = {}  # date -> [Order]
        self.employees: List[Employee] = []
        self.totals = {}
        self.totals_error: Optional[Exception] = None
        self.day_totals_on = True
        self.items: List[Item] = []
        self.changed_at: Optional[dt.datetime] = None
        self.stock: List[StockLevel] = []
        self.calls = []

    def fetch_orders(self, business_date, *, max_retries=4, sleep=None):
        self.calls.append(("fetch_orders", business_date, max_retries, sleep))
        answer = self.orders.get(business_date, [])
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    def fetch_open_orders(self, business_dates=None, *, max_retries=None, pause_seconds=0.0):
        self.calls.append(("fetch_open_orders", tuple(business_dates), max_retries, pause_seconds))
        return [o for d in business_dates for o in self.orders.get(d, []) if not (o.closed or o.voided or o.deleted)]

    def fetch_employees(self):
        self.calls.append(("fetch_employees",))
        return list(self.employees)

    def has_day_totals(self):
        return self.day_totals_on

    def get_day_totals(self, start, end):
        self.calls.append(("get_day_totals", start, end))
        if self.totals_error:
            raise self.totals_error
        return dict(self.totals)

    def fetch_catalog_snapshot(self):
        self.calls.append(("fetch_catalog_snapshot",))
        return CatalogSnapshot(items=tuple(self.items), changed_at=self.changed_at)

    def catalog_changed_at(self):
        self.calls.append(("catalog_changed_at",))
        return self.changed_at

    def fetch_stock(self, *, live=False):
        self.calls.append(("fetch_stock", live))
        return list(self.stock)


@pytest.fixture
def pos(monkeypatch):
    fake = FakeConnector()
    monkeypatch.setitem(registry.PROVIDERS, "fake", lambda: fake)
    monkeypatch.setattr(get_settings(), "pos_provider", "fake")
    registry.reset_cache()
    open_orders._EMPLOYEE_CACHE.update({"at": 0.0, "by_guid": {}})
    catalog.clear_live_stock_cache()
    items_sold.clear_live_cache()
    yield fake
    registry.reset_cache()
    open_orders._EMPLOYEE_CACHE.update({"at": 0.0, "by_guid": {}})
    catalog.clear_live_stock_cache()
    items_sold.clear_live_cache()


def line(lid, item, qty, net, check="c1", parent=None, voided=False, tax="0", check_index=0):
    return OrderLine(
        external_id=lid,
        check_external_id=check,
        parent_external_id=parent,
        is_modifier=parent is not None,
        item_external_id=item,
        name=f"name-{item}",
        quantity=D(qty),
        net_amount=D(net),
        tax_amount=D(tax),
        voided=voided,
        check_index=check_index,
    )


def order(oid, lines, checks=None, **kw):
    kw.setdefault("business_date", DAY)
    kw.setdefault("opened_at", _t(15))
    kw.setdefault("created_at", _t(14, 50))
    return Order(external_id=oid, checks=tuple(checks or [Check("c1", paid_at=_t(16))]), lines=tuple(lines), **kw)


def _rows(db):
    return {r.toast_selection_guid: r for r in db.query(OrderRow).all()}


# ---------------------------------------------------------------------------
# sales
# ---------------------------------------------------------------------------


def test_sales_sync_writes_one_row_per_line(db, pos):
    pos.orders[DAY] = [
        order(
            "o1",
            [line("s1", "biryani", "2", "25.00", tax="1.6"), line("m1", "spicy", "1", "0", parent="s1"), line("s2", "chai", "0", "3")],
            dining_option_id="dine-in",
        ),
        order(
            "o2",
            [line("s3", "rice", "1", "17.99", check="cA", check_index=0), line("s4", "dal", "1", "6.99", check="cB", check_index=1)],
            checks=[Check("cA", paid_at=None, voided=True), Check("cB", paid_at=None)],
            paid_at=_t(18),
        ),
        order(
            "o3",
            [line("s5", "rice", "1", "17.99", check="cC", check_index=0)],
            checks=[Check("cC", paid_at=None, voided=True)],
            paid_at=None,
        ),
    ]
    job = sales.sync_sales_for_date(db, DAY)
    assert job.job_name == "toast_sales_sync" == sales.JOB_NAME
    assert job.status == "success" and job.detail == "6 line items synced from 3 orders"
    rows = _rows(db)
    s1, m1, s2, s3, s4, s5 = (rows[k] for k in ("s1", "m1", "s2", "s3", "s4", "s5"))
    assert (s1.toast_order_guid, s1.toast_check_guid, s1.business_date) == ("o1", "c1", DAY)
    assert (s1.quantity, s1.net_price, s1.unit_price, s1.tax_amount) == (2.0, 25.0, 12.5, 1.6)
    assert s1.parent_selection_guid is None and m1.parent_selection_guid == "s1"
    assert s1.dining_option == "dine-in" and s1.paid_at == _t(16) and s1.opened_at == _t(15)
    assert s2.unit_price == 3.0  # quantity 0: unit price = the net
    # A check without its own payment time: the order's (voided or not).
    assert s3.voided is True and s3.paid_at == _t(18)
    assert s4.voided is False and s4.paid_at == _t(18)
    # Voided, and no payment time anywhere: the placement time (cosmetic).
    assert s5.voided is True and s5.paid_at == _t(14, 50)


def test_unpaid_live_order_keeps_no_paid_time(db, pos):
    pos.orders[DAY] = [order("o1", [line("s1", "x", "1", "5")], checks=[Check("c1")], paid_at=None)]
    sales.sync_sales_for_date(db, DAY)
    assert _rows(db)["s1"].paid_at is None


def test_order_or_check_void_or_delete_voids_every_line(db, pos):
    pos.orders[DAY] = [
        order("o1", [line("a", "x", "1", "5")], deleted=True),
        order("o2", [line("b", "x", "1", "5")], checks=[Check("c1", deleted=True)]),
        order("o3", [line("c", "x", "1", "5", voided=True)]),
        order("o4", [line("d", "x", "1", "5")]),
    ]
    sales.sync_sales_for_date(db, DAY)
    assert {k: r.voided for k, r in _rows(db).items()} == {"a": True, "b": True, "c": True, "d": False}


def test_rerun_is_idempotent_and_follows_edits(db, pos):
    pos.orders[DAY] = [order("o1", [line("s1", "x", "1", "5"), line("s2", "y", "1", "6")])]
    sales.sync_sales_for_date(db, DAY)
    ids = {k: r.id for k, r in _rows(db).items()}
    pos.orders[DAY] = [order("o1", [line("s1", "x", "1", "5", voided=True)])]
    job = sales.sync_sales_for_date(db, DAY)
    db.expire_all()
    rows = _rows(db)
    assert set(rows) == {"s1"} and rows["s1"].id == ids["s1"] and rows["s1"].voided is True
    assert job.detail == "1 line items synced from 1 orders; 1 no longer in Toast removed"


def test_an_empty_answer_never_prunes(db, pos):
    pos.orders[DAY] = [order("o1", [line("s1", "x", "1", "5")])]
    sales.sync_sales_for_date(db, DAY)
    pos.orders[DAY] = []
    job = sales.sync_sales_for_date(db, DAY)
    assert job.status == "success" and job.detail == "0 line items synced from 0 orders"
    assert set(_rows(db)) == {"s1"}


def test_a_line_without_an_id_is_skipped_and_prune_can_be_off(db, pos):
    pos.orders[DAY] = [order("o1", [line("", "x", "1", "5"), line("s1", "y", "1", "6")])]
    assert sales.sync_sales_for_date(db, DAY).detail == "1 line items synced from 1 orders"
    pos.orders[DAY] = [order("o1", [line("s2", "y", "1", "6")])]
    sales.sync_sales_for_date(db, DAY, prune=False)
    assert set(_rows(db)) == {"s1", "s2"}


def test_a_pos_failure_is_recorded_on_the_job_row(db, pos):
    pos.orders[DAY] = RuntimeError("POS said no")
    job = sales.sync_sales_for_date(db, DAY)
    assert job.status == "failed" and job.detail == "POS said no"
    assert db.query(OrderRow).count() == 0
    assert pos.calls[-1] == ("fetch_orders", DAY, 4, None)


def test_old_module_name_runs_the_same_job(db, pos):
    from app.services import sales_sync

    pos.orders[DAY] = [order("o1", [line("s1", "x", "1", "5")])]
    assert sales_sync.sync_sales_for_date is sales.sync_sales_for_date
    assert sales_sync.sync_sales_for_date(db, DAY).status == "success"
    assert sales_sync.latest_job_run(db, sales_sync.JOB_NAME).business_date == DAY


# ---------------------------------------------------------------------------
# open checks
# ---------------------------------------------------------------------------


def _open(oid, **kw):
    kw.setdefault("display_number", "12")
    kw.setdefault("server_external_id", "e1")
    kw.setdefault("checks", [Check("c1", total=D("12.50"), tab_name=" Ravi ")])
    return order(oid, [line(oid + "-s", "x", "2", "5"), line(oid + "-m", "y", "1", "0", parent=oid + "-s")], **kw)


def test_open_checks_live_view(pos):
    today = DAY
    pos.employees = [Employee("e1", "Sam K"), Employee("e2", "Gone", active=False), Employee(None, "No id")]
    pos.orders[today] = [
        _open("o1", promised_at=_t(23), guests=2, approval_status="APPROVED"),
        _open("o2", server_external_id="e2", checks=[Check("c1", total=D("3"), paid_at=_t(15)), Check("c2", total=D("4"), paid_at=_t(16))]),
        _open("o3", closed=True),
    ]
    rows = open_orders.get_open_orders(today)
    assert [r["guid"] for r in rows] == ["o1", "o2"]
    o1, o2 = rows
    assert o1["server_name"] == "Sam K" and o2["server_name"] == "Unknown"  # e2 is not active
    assert o1["opened_at"] == _t(14, 50).isoformat()  # placement time, not opened
    assert o1["tab_names"] == ["Ravi"] and o1["tab_name"] == "Ravi" and o1["num_guests"] == 2
    assert o1["total_amount"] == 12.5 and o1["num_checks"] == 1 and o1["approval_status"] == "APPROVED"
    assert o1["line_items"] == [{"name": "name-x", "quantity": 2.0}]  # the modifier is not an item
    assert o1["paid_at"] is None and o2["paid_at"] == _t(16).isoformat()  # every check paid -> latest
    assert o1["promised_at"] == _t(23).isoformat()
    assert open_orders.get_open_orders(today, employee_guid="e2")[0]["guid"] == "o2"
    assert ("fetch_open_orders", (today,), None, 0.0) in pos.calls


def test_open_checks_cache_refresh_today(db, pos, monkeypatch):
    monkeypatch.setattr(open_orders, "restaurant_today", lambda: DAY)
    pos.orders[DAY] = [_open("o1")]
    job = open_orders.refresh_open_orders_cache(db, full=False, max_retries=0)
    assert job.job_name == "open_orders_scan" and job.status == "success"
    assert job.detail == f"today ({DAY}) refresh: 1 open check(s)"
    row = db.query(OpenOrderCache).one()
    assert row.toast_order_guid == "o1" and json.loads(row.tab_names_json) == ["Ravi"]
    assert ("fetch_open_orders", (DAY,), 0, open_orders.SCAN_PAUSE_SECONDS) in pos.calls
    assert open_orders.get_cached_open_orders(db)["orders"][0]["tab_name"] == "Ravi"


def test_full_scan_asks_every_date_with_retries(db, pos, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "first_sales_date", "2026-05-26")  # FIRST_SALES_DATE (KloudC RetailOps M2)
    monkeypatch.setattr(open_orders, "restaurant_today", lambda: open_orders.FIRST_REAL_SALES_DATE + dt.timedelta(days=2))
    old_day = open_orders.FIRST_REAL_SALES_DATE
    pos.orders[old_day] = [_open("stuck", business_date=old_day)]
    job = open_orders.refresh_open_orders_cache(db, full=True)
    assert job.status == "success" and "1 open check(s)" in job.detail
    call = [c for c in pos.calls if c[0] == "fetch_open_orders"][0]
    assert len(call[1]) == 3 and call[2] == 4 and call[3] == 0.15
    assert db.query(OpenOrderCache).one().business_date == old_day


# ---------------------------------------------------------------------------
# accuracy
# ---------------------------------------------------------------------------


def _stored_day(db, day, net, qty=1.0):
    db.add(
        OrderRow(
            toast_order_guid=f"o-{day}",
            toast_check_guid="c",
            toast_selection_guid=f"s-{day}",
            business_date=day,
            quantity=qty,
            net_price=net,
            paid_at=_t(12, day=day.day),
        )
    )
    db.add(JobRun(job_name=sales.JOB_NAME, status="success", business_date=day, finished_at=_t(4, day=day.day)))
    db.commit()


def test_accuracy_resyncs_a_day_that_differs(db, pos):
    d1, d2 = dt.date(2026, 10, 11), dt.date(2026, 10, 12)
    _stored_day(db, d1, 10.0)
    _stored_day(db, d2, 20.0)
    pos.totals = {
        d1: SalesDay(d1, has_item_totals=True, has_day_metrics=True, item_net=D("10.00"), item_qty=D("1"), orders=1),
        d2: SalesDay(d2, has_item_totals=True, item_net=D("25.00"), item_qty=D("1")),
    }
    pos.orders[d2] = [order("o-fixed", [line("s-new", "x", "1", "25")], business_date=d2)]
    summary = accuracy.run_sales_repull(db, days=2, today=dt.date(2026, 10, 13))
    assert summary.job_name == "sales_repull" and summary.status == "success"
    assert "2 day(s) checked, 1 match, 1 fixed" in summary.detail
    checks = {r.business_date: json.loads(r.detail) for r in db.query(JobRun).filter_by(job_name="sales_accuracy")}
    assert checks[d1]["verdict"] == "match" and checks[d2]["verdict"] == "fixed"
    assert checks[d2]["analytics"]["item_net"] == 25.0 and checks[d2]["analytics"]["orders"] == 0
    assert sorted(checks[d2]["analytics"]) == sorted([
        "item_net", "item_qty", "item_gross", "item_discount", "item_void", "net", "gross", "discount", "void",
        "refund", "orders", "void_orders", "open_orders",
    ])
    assert ("get_day_totals", d1, d2) in pos.calls


def test_accuracy_off_without_day_totals_and_report_failure_label(db, pos):
    pos.day_totals_on = False
    # Without day totals (no Analytics keys) a POS with orders by date gets the
    # re-check instead (pre-production P); the comparison stays off.
    recheck = accuracy.run_sales_repull(db, days=2, today=DAY)
    assert recheck is not None and recheck.job_name == accuracy.RECHECK_JOB
    assert accuracy.accuracy_report(db)["configured"] is False
    pos.day_totals_on = True
    pos.totals_error = DayTotalsUnavailable("Fake Reports", RuntimeError("busy"))
    summary = accuracy.run_sales_repull(db, days=2, today=DAY)
    assert summary.status == "failed" and summary.detail == "Fake Reports: busy"


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

YDAY = DAY - dt.timedelta(days=1)


def _csv(db, item_id, name, qty, group="RETAIL", category="Tea", barcode="012345678905;036000291452", cost=2.0, day=YDAY):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=category,
            inventory_quantity=qty,
            price=9.99,
            cost=cost,
            supplier="Acme;Raja",
            barcode=barcode,
            source_filename=f"retail-{day}.csv",
        )
    )
    db.commit()


def _items():
    return [
        Item(
            "tea",
            "Tea 1kg",
            barcodes=("036000291452",),
            price=D("9.99"),
            category=Category("RETAIL", "Beverages"),
            locations=(Category("RETAIL", "Beverages"), Category("retail ", "TEA")),
            other_names=("Tea 1kg", "Tea 1kg"),
        ),
        Item("neg", "Negative Thing", barcodes=("999",), price=None, category=Category("RETAIL", "Tea")),
        Item("dish", "Kitchen Dish", category=Category("MARKET KITCHEN", "Mains")),
    ]


def test_catalog_build_from_models(db, pos):
    _csv(db, "tea", "TEA 1KG", 14)
    _csv(db, "neg", "Negative Thing", -3)
    stock = [
        StockLevel("tea", StockStatus.COUNT, D("85.39999999999989")),
        StockLevel("neg", StockStatus.OUT),
        StockLevel(None, StockStatus.COUNT, D("1")),
        StockLevel("not-in-catalog", StockStatus.UNTRACKED),
    ]
    rows, meta = catalog.build_inventory_rows(db, _items(), stock, _t(3), DAY, _t(8, day=13))
    tea, neg = rows["tea"], rows["neg"]
    assert "dish" not in rows and meta["stats"]["skipped_not_inventory"] == 1  # not an inventory group
    assert tea["inventory_quantity"] == 85.4 and tea["inventory_status"] == "85.4 in stock"
    assert (tea["category_group"], tea["category"]) == ("RETAIL", "Tea")  # the CSV's placement is a location
    assert tea["name"] == "TEA 1KG"  # the CSV's spelling of a POS name
    assert tea["barcode"] == "012345678905;036000291452"  # the POS code is one of the CSV's
    assert tea["supplier"] == "Acme;Raja" and tea["cost"] == 2.0  # carried from the CSV
    assert neg["inventory_quantity"] == 0.0 and neg["api_out_of_stock"] is True and neg["csv_quantity"] == -3
    assert neg["inventory_status"] == "out of stock" and neg["barcode"] == "999" and neg["price"] is None
    assert meta["stats"]["stock_rows"] == 3 and meta["stats"]["stock_not_in_menu"] == 1
    assert meta["menu_last_updated"] == _t(3).isoformat() and meta["source_filename"] == "api:2026-10-13T08:00:00Z"


def test_catalog_job_writes_unless_a_csv_exists(db, pos, monkeypatch):
    monkeypatch.setattr(inventory_api, "_after_new_snapshot", lambda: None)
    _csv(db, "tea", "Tea 1kg", 14)
    _csv(db, "neg", "Negative Thing", -3)
    pos.items = _items()
    pos.changed_at = _t(3)
    pos.stock = [StockLevel("tea", StockStatus.COUNT, D("12")), StockLevel("neg", StockStatus.OUT)]
    job = catalog.run_snapshot(db, write=True, snapshot_date=DAY, now=_t(8))
    detail = json.loads(job.detail)
    assert job.job_name == "inventory_api_snapshot" and job.status == "success"
    assert detail["stored"] == {"written": 2, "skipped": None}
    assert db.query(InventorySnapshot).filter_by(snapshot_date=DAY).count() == 2
    assert {r.item_id: r.sku for r in db.query(ToastMenuItem)} == {"tea": "036000291452", "neg": "999", "dish": None}
    # A CSV for the date wins: never written over, only compared.
    _csv(db, "tea", "Tea 1kg", 11, day=DAY + dt.timedelta(days=1))
    job = catalog.run_snapshot(db, write=True, snapshot_date=DAY + dt.timedelta(days=1), now=_t(8, day=14))
    detail = json.loads(job.detail)
    assert detail["stored"] == {"written": 0, "skipped": "csv_exists"} and detail["comparison"]["items_in_both"] == 1


def test_an_empty_stock_answer_is_never_stored(db, pos, monkeypatch):
    _csv(db, "tea", "Tea 1kg", 14)
    pos.items = _items()
    job = catalog.run_snapshot(db, write=True, snapshot_date=DAY, now=_t(8))
    assert job.status == "failed" and json.loads(job.detail)["stored"]["skipped"] == "implausible"


def test_catalog_recheck_pulls_only_when_changed(db, pos):
    pos.items = _items()
    pos.changed_at = _t(3)
    first = catalog.refresh_catalog_cache(db)
    assert json.loads(first.detail) == {"changed": True, "items": 3, "menu_last_updated": _t(3).isoformat()}
    second = catalog.refresh_catalog_cache(db)
    assert json.loads(second.detail)["changed"] is False
    assert [c[0] for c in pos.calls].count("fetch_catalog_snapshot") == 1


def test_live_overlay_from_stock_levels(db, pos, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    from app.services import inventory_api_switch

    monkeypatch.setattr(inventory_api_switch, "is_on", lambda: True)
    snap = InventorySnapshot(item_id="neg", inventory_quantity=0.0, api_out_of_stock=True, csv_quantity=-3.0)
    tea = InventorySnapshot(item_id="tea", inventory_quantity=14.0)
    other = InventorySnapshot(item_id="other", inventory_quantity=2.0)
    pos.stock = [StockLevel("tea", StockStatus.COUNT, D("9.5")), StockLevel("neg", StockStatus.OUT)]
    out = catalog.overlay_live_stock({"tea": tea, "neg": snap, "other": other})
    assert out["tea"].inventory_quantity == 9.5 and out["tea"].inventory_status == "9.5 in stock"
    assert out["neg"].inventory_quantity == -3.0 and out["neg"].inventory_status == "out of stock"
    assert out["other"] is other
    assert ("fetch_stock", True) in pos.calls
    assert catalog.live_stock_meta()["live"] is True


# ---------------------------------------------------------------------------
# Items Sold "today"
# ---------------------------------------------------------------------------


def test_items_sold_live_reads_the_connector(pos):
    pos.orders[DAY] = [order("o1", [line("s1", "x", "2", "10"), line("m1", "y", "1", "1", parent="s1")])]
    items, _at = items_sold._live_line_items(DAY)
    assert [i["toast_selection_guid"] for i in items] == ["s1", "m1"]
    call = [c for c in pos.calls if c[0] == "fetch_orders"][0]
    assert call[2] == 1 and call[3] is items_sold._live_sleep


def test_full_scan_without_a_first_sales_date_starts_at_the_oldest_sale(db, pos, monkeypatch):
    """No FIRST_SALES_DATE (a new store): the sweep starts at the oldest stored
    business date, at most FULL_SCAN_MAX_DAYS back; with no sales, today."""
    from app.models import Order
    from app.pos.sync import open_orders as sync_open_orders

    today = dt.date(2026, 10, 18)
    assert sync_open_orders.full_scan_start(db, today) == today
    db.add(Order(toast_order_guid="o", toast_check_guid="c", toast_selection_guid="s",
                 business_date=dt.date(2026, 10, 1), item_guid="i", item_name="x", quantity=1))
    db.commit()
    assert sync_open_orders.full_scan_start(db, today) == dt.date(2026, 10, 1)
    assert sync_open_orders.full_scan_start(db, dt.date(2028, 1, 1)) == dt.date(2028, 1, 1) - dt.timedelta(days=365)
