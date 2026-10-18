"""Multi-store M1 batch MT3: the Toast adapter's mapping to the POS-neutral
models, checked against the code it replaced.

`tests/pre_mt3_reference.py` holds the Toast-JSON functions exactly as they
were before MT3. Every test here feeds the same Toast payloads to those and to
the new path (Toast adapter -> models -> POS-neutral rule) and requires the
SAME result, compared by `repr` (so 3 vs 3.0, True vs 1, a naive vs an aware
time all count as different):

- `orders` rows: old `_extract_line_items(raw)` vs `sales.line_rows(map_order(raw))`
  over 3,000 random Toast-shaped orders (modifiers to depth 3, voided /
  deleted orders and checks, missing / null / odd fields, bad timestamps);
- open checks rows: old `_normalize_open_order` vs the neutral one;
- tab names, employees, stock statuses, live quantities;
- the inventory build: old `build_api_rows` vs the new one, on the fixture
  catalog and 200 random stock lists.

No test here reaches the network (no Toast call is made at all).
"""

import datetime as dt
import random

import pytest

from app.pos.adapters.toast import catalog as toast_catalog
from app.pos.adapters.toast import labor as toast_labor
from app.pos.adapters.toast import orders as toast_orders
from app.pos.adapters.toast import stock as toast_stock
from app.pos.models import Check, Order, OrderLine, StockStatus
from app.pos.sync import catalog as catalog_sync
from app.pos.sync import open_orders as open_sync
from app.pos.sync import sales as sales_sync_new
from app.services import inventory_api as ia
from app.services import open_orders as open_orders_shim
from app.services import sales_sync as sales_sync_shim
from tests import pre_mt3_reference as ref
from tests.test_inventory_api import (  # noqa: F401 - fixtures
    GOOD,
    NEG,
    RICE,
    TEA,
    TODAY,
    _isolate,
    menus_json,
    stock_json,
    store,
)

DAY = dt.date(2026, 10, 14)
NOW = dt.datetime(2026, 10, 14, 19, 30, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# A random Toast order generator (shapes from Toast's Orders API and the
# real payloads this app has seen; deliberately including odd values)
# ---------------------------------------------------------------------------


def _ts(rng):
    roll = rng.random()
    if roll < 0.55:
        return f"2026-10-{rng.randint(10, 16):02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}.{rng.randint(0, 999):03d}+0000"
    if roll < 0.65:
        return f"2026-10-14T1{rng.randint(0, 9)}:00:00.5-0400"
    if roll < 0.7:
        return "2026-10-14T18:28:45.000+00:00"
    if roll < 0.75:
        return "not a time"
    if roll < 0.8:
        return ""
    return None


def _num(rng, kind="money"):
    roll = rng.random()
    if roll < 0.08:
        return None
    if roll < 0.14:
        return 0
    if roll < 0.2:
        return rng.randint(1, 5)
    if roll < 0.24:
        return str(round(rng.uniform(0, 30), 2))
    if roll < 0.28:
        return 0.1 + 0.2  # float noise
    if kind == "qty":
        return round(rng.uniform(0.05, 4), rng.choice([0, 1, 3]))
    return round(rng.uniform(-5, 40), 2)


def _flag(rng, p=0.15):
    roll = rng.random()
    if roll < p:
        return True
    if roll < p + 0.1:
        return None
    if roll < p + 0.2:
        return "missing"
    return False


def _put(d, key, value):
    if value != "missing":
        d[key] = value


def _selection(rng, depth, counter):
    counter[0] += 1
    sel = {}
    if rng.random() > 0.03:
        sel["guid"] = f"sel-{counter[0]}"
    if rng.random() < 0.9:
        sel["item"] = {"guid": f"item-{rng.randint(1, 40)}"} if rng.random() > 0.05 else {}
    elif rng.random() < 0.5:
        sel["item"] = None
    if rng.random() < 0.8:
        sel["displayName"] = rng.choice(["Rice 20lb", "Chai", "Samosa", "", None])
    if rng.random() < 0.5:
        sel["name"] = rng.choice(["Rice", "Spice level", None])
    _put(sel, "quantity", _num(rng, "qty") if rng.random() > 0.05 else "missing")
    _put(sel, "price", _num(rng) if rng.random() > 0.05 else "missing")
    _put(sel, "tax", _num(rng) if rng.random() > 0.05 else "missing")
    _put(sel, "voided", _flag(rng, 0.1))
    if depth < 3 and rng.random() < 0.35:
        roll = rng.random()
        if roll < 0.1:
            sel["modifiers"] = None
        elif roll < 0.2:
            sel["modifiers"] = []
        else:
            sel["modifiers"] = [_selection(rng, depth + 1, counter) for _ in range(rng.randint(1, 3))]
    return sel


def _check(rng, counter, display_number):
    counter[0] += 1
    chk = {"guid": f"chk-{counter[0]}"} if rng.random() > 0.03 else {}
    _put(chk, "paidDate", _ts(rng) if rng.random() < 0.7 else "missing")
    _put(chk, "voided", _flag(rng, 0.1))
    _put(chk, "deleted", _flag(rng, 0.05))
    roll = rng.random()
    if roll < 0.5:
        chk["totalAmount"] = _num(rng)
    elif roll < 0.55:
        chk["totalAmount"] = True
    roll = rng.random()
    if roll < 0.25:
        chk["tabName"] = rng.choice(["Ravi", "ravi ", "  Priya   K ", "", "   ", str(display_number), 12, None])
    roll = rng.random()
    if roll < 0.08:
        chk["selections"] = None
    elif roll < 0.12:
        pass
    else:
        chk["selections"] = [_selection(rng, 0, counter) for _ in range(rng.randint(0, 6))]
    return chk


def random_order(rng, counter, guid=True):
    counter[0] += 1
    display = rng.choice(["12", "7", 13, None, ""])
    order = {"guid": f"ord-{counter[0]}"} if guid else {}
    for key in ("openedDate", "paidDate", "createdDate"):
        if rng.random() < 0.8:
            order[key] = _ts(rng)
    _put(order, "voided", _flag(rng, 0.08))
    _put(order, "deleted", _flag(rng, 0.05))
    roll = rng.random()
    if roll < 0.7:
        order["diningOption"] = {"guid": f"dining-{rng.randint(1, 4)}"}
    elif roll < 0.8:
        order["diningOption"] = None
    elif roll < 0.85:
        order["diningOption"] = {}
    if rng.random() < 0.3:
        order["closedDate"] = rng.choice([_ts(rng) or "2026-10-14T20:00:00.000+0000", "garbage"])
    if rng.random() < 0.3:
        order["promisedDate"] = _ts(rng)
    if rng.random() < 0.2:
        order["estimatedFulfillmentDate"] = _ts(rng)
    order["displayNumber"] = display
    if rng.random() < 0.7:
        order["numberOfGuests"] = rng.choice([1, 2, 0, None])
    if rng.random() < 0.6:
        order["approvalStatus"] = rng.choice(["APPROVED", "NEEDS_APPROVAL", None])
    roll = rng.random()
    if roll < 0.7:
        order["server"] = {"guid": rng.choice(["emp1", "emp2", "emp-gone"])}
    elif roll < 0.8:
        order["server"] = None
    roll = rng.random()
    if roll < 0.05:
        order["checks"] = None
    elif roll < 0.08:
        pass
    else:
        order["checks"] = [_check(rng, counter, display) for _ in range(rng.randint(0, 3))]
    return order


def _orders(seed, n):
    rng = random.Random(seed)
    counter = [0]
    return [random_order(rng, counter) for _ in range(n)]


# ---------------------------------------------------------------------------
# orders rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_orders_rows_match_the_pre_mt3_mapping_for_random_toast_orders(seed):
    orders = _orders(seed, 500)
    lines = 0
    for raw in orders:
        old = ref._extract_line_items(raw)
        new = sales_sync_new.line_rows(toast_orders.map_order(raw, DAY))
        assert repr(new) == repr(old), raw
        # The old module name gives the same rows too.
        assert repr(sales_sync_shim._extract_line_items(raw)) == repr(old)
        lines += len(old)
    assert lines > 1000  # the generator really produced lines


def test_flatten_selection_old_name_matches():
    rng = random.Random(42)
    counter = [0]
    opened = dt.datetime(2026, 10, 14, 15, 0, tzinfo=dt.timezone.utc)
    for _ in range(300):
        sel = _selection(rng, 0, counter)
        args = (sel, "o1", "c1", opened, rng.choice([None, opened]), rng.choice([None, "d1"]), rng.random() < 0.2, rng.random() < 0.2)
        old = ref._flatten_selection(*args, parent_selection_guid=None)
        new = sales_sync_shim._flatten_selection(*args, parent_selection_guid=None)
        assert repr(new) == repr(old)


def test_modifiers_are_lines_of_their_own_with_the_parent_id():
    raw = {
        "guid": "o1",
        "checks": [
            {
                "guid": "c1",
                "paidDate": "2026-10-14T18:00:00.000+0000",
                "selections": [
                    {
                        "guid": "s1",
                        "item": {"guid": "biryani"},
                        "displayName": "Biryani",
                        "quantity": 1,
                        "price": 12.5,
                        "tax": 0.8,
                        "modifiers": [
                            {"guid": "m1", "item": {"guid": "spicy"}, "displayName": "Spicy", "quantity": 1, "price": 1.0},
                        ],
                    }
                ],
            }
        ],
    }
    order = toast_orders.map_order(raw, DAY)
    assert [(ln.external_id, ln.parent_external_id, ln.is_modifier) for ln in order.lines] == [
        ("s1", None, False),
        ("m1", "s1", True),
    ]
    rows = sales_sync_new.line_rows(order)
    assert rows[1]["parent_selection_guid"] == "s1" and rows[0]["net_price"] == 12.5
    assert rows[0]["paid_at"] == dt.datetime(2026, 10, 14, 18, 0, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# open checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
def test_open_check_rows_match_the_pre_mt3_code(seed):
    names = {"emp1": "Sam K", "emp2": "Ravi (R)"}
    checked = 0
    for raw in _orders(100 + seed, 400):
        if raw.get("closedDate") or raw.get("voided") or raw.get("deleted"):
            assert not toast_orders.is_open(raw)
            continue
        assert toast_orders.is_open(raw)
        old = ref._normalize_open_order(raw, DAY, names, NOW)
        new = open_sync._normalize_open_order(toast_orders.map_order(raw, DAY), DAY, names, NOW)
        assert repr(new) == repr(old), raw
        assert repr(open_orders_shim._normalize_open_order(raw, DAY, names, NOW)) == repr(old)
        for fn in ("_tab_names", "_order_total", "_line_items", "_paid_at", "_promised_at", "_placed_at"):
            assert repr(getattr(open_orders_shim, fn)(raw)) == repr(getattr(ref, fn)(raw)), fn
        checked += 1
    assert checked > 150


def test_tab_names_odd_payloads_still_tolerated():
    for raw in ({"displayNumber": "5"}, {"checks": None}, {"checks": ["not a dict", {"tabName": "Ok"}]}):
        assert open_orders_shim._tab_names(raw) == ref._tab_names(raw)
    assert open_orders_shim._tab_names({"checks": ["not a dict", {"tabName": "Ok"}]}) == ["Ok"]


def test_employee_names_and_active_flag():
    raws = [
        {"guid": "e1", "firstName": "Sam", "lastName": "K", "chosenName": "Sammy"},
        {"guid": "e2", "firstName": " ", "lastName": None, "chosenName": "Bo"},
        {"guid": "e3"},
        {"guid": "e4", "firstName": "Gone", "deleted": True},
        {"firstName": "No guid"},
    ]
    for raw in raws:
        assert toast_labor._employee_name(raw) == ref._employee_name(raw)
    emps = [toast_labor.map_employee(r) for r in raws]
    assert [(e.external_id, e.name, e.active) for e in emps] == [
        ("e1", "Sam K (Sammy)", True),
        ("e2", "Bo", True),
        ("e3", "Unknown", True),
        ("e4", "Gone", False),
        (None, "No guid", True),
    ]


# ---------------------------------------------------------------------------
# stock + catalog
# ---------------------------------------------------------------------------


def test_stock_status_mapping():
    assert toast_stock.stock_status("QUANTITY") is StockStatus.COUNT
    assert toast_stock.stock_status("OUT_OF_STOCK") is StockStatus.OUT
    for other in (None, "", "IN_STOCK", "quantity"):
        assert toast_stock.stock_status(other) is StockStatus.UNTRACKED
    lvl = toast_stock.stock_level({"guid": "a", "status": "QUANTITY", "quantity": 85.39999999999989})
    assert float(lvl.quantity) == 85.39999999999989
    assert toast_stock.stock_level({"guid": "a", "status": "QUANTITY", "quantity": None}).quantity is None
    assert toast_stock.stock_level({"guid": "a", "status": "OUT_OF_STOCK", "quantity": -3}).quantity is None
    # The live path skips non-objects; the nightly build raises, as before.
    assert len(toast_stock.stock_levels([{"guid": "a"}, "junk"], lenient=True)) == 1
    with pytest.raises(AttributeError):
        toast_stock.stock_levels([{"guid": "a"}, "junk"])


def test_format_status_and_live_quantity_match_the_pre_mt3_code():
    from app.models import InventorySnapshot

    snaps = [
        InventorySnapshot(item_id="x", inventory_quantity=q, api_out_of_stock=f, csv_quantity=c)
        for q in (None, 0.0, 4.0, -2.0)
        for f in (None, True)
        for c in (None, -5.0, 3.0)
    ]
    for status in ("QUANTITY", "OUT_OF_STOCK", None, "OTHER"):
        for qty in (None, 0, 0.485, 932.0, 1210.0, 6, -1.5):
            assert ia.format_status(status, qty) == ref.format_status(status, qty)
            for snap in snaps:
                assert repr(ia.live_quantity(snap, status, qty)) == repr(ref.live_quantity(snap, status, qty))


def test_menu_items_carry_what_the_build_needs():
    snap = toast_catalog.catalog_snapshot(menus_json())
    by_id = {i.external_id: i for i in snap.items}
    rice = by_id[RICE]
    assert rice.name == "Rice 20lb" and float(rice.price) == 17.99 and rice.barcode == GOOD
    assert rice.variant_kind == "each" and rice.category.as_tuple() == ("RETAIL", "Grains", "Rice")
    assert by_id["rice-container"].price is None  # $0 -> no price
    assert by_id["biryani"].price is None and by_id["biryani"].price_rule == "SIZE_PRICE"
    assert "test-thing" not in by_id  # the test menu is skipped
    assert snap.changed_at == dt.datetime(2026, 10, 11, 15, 41, 42, 977000, tzinfo=dt.timezone.utc)
    assert [i.external_id for i in snap.items] == list(toast_catalog.parse_menus(menus_json()))


def _random_stock(rng):
    ids = [RICE, "rice-container", "dal-each", "dal-container", TEA, NEG, "brand-new", "street-dish", "biryani", "unknown-1"]
    rows = []
    for guid in ids:
        roll = rng.random()
        if roll < 0.1:
            continue
        row = {"guid": guid if rng.random() > 0.05 else ""}
        row["status"] = rng.choice(["QUANTITY", "QUANTITY", "OUT_OF_STOCK", "IN_STOCK", None])
        row["quantity"] = rng.choice([None, 0, 12.0, 85.39999999999989, -4.0, 3, round(rng.uniform(0.1, 60), 3)])
        rows.append(row)
    if rng.random() < 0.2:
        rows.append(dict(rows[0]) if rows else {"guid": TEA, "status": "QUANTITY", "quantity": 1.0})
    return rows


def test_inventory_build_matches_the_pre_mt3_build(store):
    now = dt.datetime(2026, 10, 15, 8, 5, 3, 120000, tzinfo=dt.timezone.utc)
    rng = random.Random(7)
    cases = [(stock_json(), menus_json())] + [(_random_stock(rng), menus_json()) for _ in range(200)]
    cases.append((stock_json(), menus_json(last_updated=None)))
    cases.append(([], menus_json()))
    for stock, menus in cases:
        old = ref.build_api_rows(store, stock, menus, TODAY, now)
        new = ia.build_api_rows(store, stock, menus, TODAY, now)
        assert repr(new) == repr(old)
        assert catalog_sync.implausible_build(store, toast_stock.stock_levels(stock), new[1]) == ia.implausible_build(
            store, stock, old[1]
        )


def test_placement_old_name_matches(store):
    menu = toast_catalog.parse_menus(menus_json())
    carried = ia.latest_csv_row_per_item(store)
    for guid, info in menu.items():
        assert ia._placement(info, carried.get(guid)) == ref._placement(info, carried.get(guid))
        assert ia._placement(info, None) == ref._placement(info, None)


def test_models_are_frozen():
    line = OrderLine("s", "c", None, False, "i", "n", toast_orders.to_decimal(1), toast_orders.to_decimal(2), toast_orders.to_decimal(0))
    with pytest.raises(Exception):
        line.voided = True  # type: ignore[misc]
    assert Order(external_id="o", business_date=DAY, checks=(Check("c"),), lines=(line,)).lines[0].quantity == 1
