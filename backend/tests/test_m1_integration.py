"""Multi-store M1 integration: the pieces built in parallel (MT2 connector,
MT3 neutral sync, MT4 neutral ids, MT5 item_vendors, MT6 gating) working
together after the merge.

- MT3's sales sync fills MT4's columns from the connector's models and its
  `source` (for Toast exactly what MT4's `neutral_line_values` gives);
- MT4's bookkeeping (pos_item_map, sync_cursors) is written under the
  connector's source;
- MT6's gating reads the store's connector from MT2's registry;
- MT5's item_vendors is filled by the CSV upload whose reader moved to the
  Toast adapter in MT2.
"""

import datetime as dt
import random
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.item_vendor_models import ItemVendor
from app.models import OpenOrderCache, Order as OrderRow
from app.pos import gating, registry
from app.pos.adapters.toast import orders as toast_orders
from app.pos.base import BaseConnector
from app.pos.capabilities import TOAST_CAPABILITIES, Capability
from app.pos.models import Check, Order, OrderLine
from app.pos.neutral_ids import SALES_STREAM, get_cursor, neutral_line_values
from app.pos.sync import open_orders as sync_open_orders
from app.pos.sync import sales
from app.services import sales_sync
from app.services.inventory_parser import parse_and_store

D = Decimal
DAY = dt.date(2026, 10, 13)
UTC = dt.timezone.utc


class _Pos(BaseConnector):
    source = "otherpos"
    display_name = "Other POS"
    capabilities = frozenset({Capability.ORDERS_BY_BUSINESS_DATE, Capability.CATALOG})

    def __init__(self):
        self.orders = {}

    def fetch_orders(self, business_date, *, max_retries=4, sleep=None):
        return list(self.orders.get(business_date, []))


@pytest.fixture
def other_pos(monkeypatch):
    fake = _Pos()
    monkeypatch.setitem(registry.PROVIDERS, "otherpos", lambda: fake)
    monkeypatch.setattr(get_settings(), "pos_provider", "otherpos")
    registry.reset_cache()
    yield fake
    registry.reset_cache()


def _line(lid, item, parent=None, check="c1"):
    return OrderLine(
        external_id=lid, check_external_id=check, parent_external_id=parent, is_modifier=parent is not None,
        item_external_id=item, name=item, quantity=D("1"), net_amount=D("2.5"), tax_amount=D("0"), check_index=0,
    )


def _order(oid, lines, **kw):
    return Order(
        external_id=oid, business_date=DAY, opened_at=dt.datetime(2026, 10, 13, 15, tzinfo=UTC),
        checks=(Check("c1", paid_at=dt.datetime(2026, 10, 13, 16, tzinfo=UTC)),), lines=tuple(lines), **kw,
    )


# --- MT3 sync -> MT4 columns ---------------------------------------------------------------------


def _random_toast_order(rng, n):
    def sel(depth):
        s = {
            "guid": f"s{n}-{rng.randrange(10**9)}",
            "item": {"guid": f"i{rng.randrange(50)}"} if rng.random() < 0.9 else None,
            "displayName": "x", "quantity": rng.choice([0, 1, 2, 1.5]), "price": rng.choice([0, 3.5, 12.99]),
            "tax": 0.1, "voided": rng.random() < 0.1,
        }
        if depth < 2 and rng.random() < 0.4:
            s["modifiers"] = [sel(depth + 1) for _ in range(rng.randrange(1, 3))]
        return s

    return {
        "guid": f"o{n}",
        "openedDate": "2026-10-13T15:00:00.000+0000",
        "diningOption": {"guid": rng.choice(["dine", "togo", None])} if rng.random() < 0.8 else None,
        "voided": rng.random() < 0.05,
        "checks": [
            {"guid": f"c{n}-{k}", "selections": [sel(0) for _ in range(rng.randrange(0, 4))]}
            for k in range(rng.randrange(1, 3))
        ],
    }


def test_toast_neutral_rows_from_models_equal_mt4_rule():
    rng = random.Random(11)
    lines_seen = modifiers_seen = 0
    for n in range(400):
        order = toast_orders.map_order(_random_toast_order(rng, n), DAY)
        rows = sales.line_rows(order)
        neutral = sales.neutral_line_rows(order, "toast")
        assert neutral == [neutral_line_values(r) for r in rows]
        lines_seen += len(rows)
        modifiers_seen += sum(1 for r in neutral if r["is_modifier"])
    assert lines_seen > 1000 and modifiers_seen > 100


def test_toast_sync_writes_the_same_neutral_columns_as_mt4(db, monkeypatch):
    raw = [
        {"guid": "o1", "openedDate": "2026-10-13T15:00:00.000+0000", "diningOption": {"guid": "dine"},
         "checks": [{"guid": "c1", "selections": [
             {"guid": "s1", "item": {"guid": "i1"}, "quantity": 1, "price": 5.0, "tax": 0.3,
              "modifiers": [{"guid": "m1", "item": {"guid": "i2"}, "quantity": 1, "price": 0.5, "tax": 0}]}]}]},
    ]
    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", lambda d: raw)
    job = sales_sync.sync_sales_for_date(db, DAY)
    assert job.status == "success", job.detail
    got = {
        r.toast_selection_guid: (r.source, r.external_order_id, r.external_check_id, r.external_line_id,
                                 r.parent_external_line_id, r.is_modifier, r.dining_option_id, r.channel)
        for r in db.query(OrderRow).all()
    }
    assert got == {
        "s1": ("toast", "o1", "c1", "s1", None, False, "dine", None),
        "m1": ("toast", "o1", "c1", "m1", "s1", True, "dine", None),
    }
    assert get_cursor(db, "toast", SALES_STREAM) == DAY.isoformat()
    mapped = set(db.execute(text("SELECT source, external_id FROM pos_item_map")).all())
    assert {("toast", "i1"), ("toast", "i2")} <= mapped


def test_another_pos_sync_fills_neutral_columns_under_its_source(db, other_pos):
    other_pos.orders[DAY] = [
        _order("A1", [_line("L1", "sku-1"), _line("L2", "sku-2", parent="L1")], channel="online", dining_option_id="web"),
        _order("A2", [_line("L3", "sku-3")]),
    ]
    job = sales.sync_sales_for_date(db, DAY)
    assert job.status == "success", job.detail
    rows = {r.external_line_id: r for r in db.query(OrderRow).all()}
    assert {r.source for r in rows.values()} == {"otherpos"}
    assert (rows["L2"].is_modifier, rows["L2"].parent_external_line_id, rows["L1"].is_modifier) == (True, "L1", False)
    assert (rows["L1"].channel, rows["L1"].dining_option_id, rows["L3"].channel) == ("online", "web", None)
    assert rows["L3"].external_order_id == "A2" and rows["L3"].external_check_id == "c1"
    # MT4 bookkeeping under the connector's source, not "toast".
    assert get_cursor(db, "otherpos", SALES_STREAM) == DAY.isoformat()
    assert get_cursor(db, "toast", SALES_STREAM) is None
    mapped = set(db.execute(text("SELECT source, external_id FROM pos_item_map")).all())
    assert {("otherpos", "sku-1"), ("otherpos", "sku-2"), ("otherpos", "sku-3")} <= mapped
    assert not any(s == "toast" for s, _ in mapped)


def test_resync_without_channel_keeps_a_stored_channel(db, other_pos):
    other_pos.orders[DAY] = [_order("A1", [_line("L1", "sku-1")], channel="pickup")]
    sales.sync_sales_for_date(db, DAY)
    other_pos.orders[DAY] = [_order("A1", [_line("L1", "sku-1")])]
    sales.sync_sales_for_date(db, DAY)
    db.expire_all()
    assert db.query(OrderRow).one().channel == "pickup"


def test_open_checks_cache_writes_the_neutral_order_id(db):
    sync_open_orders._replace_cache_rows(db, [{
        "guid": "oo1", "business_date": DAY.isoformat(), "display_number": "7", "opened_at": None,
        "server_guid": None, "server_name": None, "num_guests": 1, "approval_status": None, "total_amount": 1.0,
        "num_checks": 1, "line_items": [], "tab_names": [], "paid_at": None, "promised_at": None,
    }])
    db.commit()
    row = db.query(OpenOrderCache).one()
    assert (row.toast_order_guid, row.external_order_id) == ("oo1", "oo1")


# --- MT6 gating <- MT2 registry --------------------------------------------------------------------


def test_main_store_gating_comes_from_the_toast_connector():
    assert gating.pos_provider() == "toast"
    assert gating.pos_name() == registry.get_connector().display_name == "Toast"
    assert gating.store_capabilities() == registry.get_connector().capabilities == TOAST_CAPABILITIES


def test_gating_follows_a_registered_connector(other_pos):
    assert gating.pos_provider() == "otherpos"
    assert gating.pos_name() == "Other POS"
    assert gating.store_capabilities() == other_pos.capabilities
    assert gating.capability_values() == ["orders_by_business_date", "catalog"]
    assert gating.features_for_store(["overview", "open_orders", "sales_insights"]) == ["overview"]


@pytest.mark.parametrize("raw", ["", "   ", " Toast "])
def test_blank_or_padded_provider_is_toast_in_both(monkeypatch, raw):
    monkeypatch.setattr(get_settings(), "pos_provider", raw)
    registry.reset_cache()
    try:
        assert registry.provider_name() == gating.pos_provider() == "toast"
        assert gating.store_capabilities() == TOAST_CAPABILITIES
    finally:
        registry.reset_cache()


# --- MT5 item_vendors <- CSV upload (reader in the MT2 Toast adapter) -----------------------------

CSV = (
    b"Item ID,Name,Supplier,Supplier Item ID,Inventory Quantity\n"
    b"tea,Tea,Raja Foods;RBest,;2524,4\n"
    b"rice,Rice,Chetak,,9\n"
)


def test_csv_upload_fills_item_vendors(db):
    result = parse_and_store(db, "retail-items.csv", CSV, DAY)
    assert result["rows_loaded"] == 2
    got = sorted((v.item_id, v.rank, v.vendor_name, v.vendor_item_code) for v in db.query(ItemVendor).all())
    assert got == [("rice", 1, "Chetak", None), ("tea", 1, "Raja Foods", None), ("tea", 2, "RBest", "2524")]
