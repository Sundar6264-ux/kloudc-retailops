"""W2 (docs/BATCH_OCT14_CONTRACT.md): every product search also finds an item
by its Toast item id, any vendor's SKU (';'-separated) or a barcode.

- services/product_match.py: the shared rule (the same cases run in the
  website's and the app's tests - frontend/src/productMatchCases.json).
- GET /api/inventory/items/search: code matches, `matched_on` /
  `matched_code` / `all_supplier_item_ids` (additive), exact codes first.
- Lists the pages filter on the device get the codes as additive fields:
  Reorder Candidates and Supplier Projection items, Items Sold rows.
- Server-side searches: price history, PO sections, Store vs Container,
  placed orders, the Ask bot's `search`.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from app.models import InventorySnapshot, Order
from app.services import product_match
from tests.conftest import client_for, make_user

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend" / "src" / "productMatchCases.json").read_text()
)
SEARCH = "/api/inventory/items/search"
OLD_KEYS = ("item_id", "name", "category", "supplier", "supplier_item_id", "barcode", "price", "cost", "on_hand_qty")

CILANTRO = "fb25bea8-2205-4ff0-9c72-4d62bbb2f4d6"
TOMATO = "b37be37f-b136-4e6c-8317-31b01b2ca070"
ROTI = "06a285fe-a08d-4215-8b42-07517035a3a4"


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: repr(c["q"]))
def test_shared_rule_cases(case):
    item = CASES["item"]
    got = product_match.match_code(case["q"], item["item_id"], item["supplier_item_ids"], item["barcodes"])
    assert (list(got) if got else None) == case["expect"]


def test_split_and_code_fields():
    assert product_match.split_codes(" A;;B ; ") == ["A", "B"]
    assert product_match.split_codes(None) == []

    class Snap:
        supplier_item_id = "X1;X2"
        barcode = "0123"

    assert product_match.code_fields(Snap()) == {"all_supplier_item_ids": "X1;X2", "barcode": "0123"}
    # A key the row already has is left alone.
    assert product_match.code_fields(Snap(), {"barcode": "keep"}) == {"all_supplier_item_ids": "X1;X2"}
    assert product_match.code_fields(None) == {"all_supplier_item_ids": None, "barcode": None}


def _snap(db, day, item_id, name, qty=5, supplier="Raja Foods", code=None, barcode=None, price=3.0):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group="PRODUCE",
            category="Herbs",
            inventory_quantity=qty,
            price=price,
            cost=1.0,
            supplier=supplier,
            supplier_item_id=code,
            barcode=barcode,
        )
    )


@pytest.fixture
def catalog(db):
    today = dt.date.today()
    _snap(
        db,
        today,
        CILANTRO,
        "Cilantro Bunch",
        supplier="Raja Foods;Restaurant Depot;RBest Produce Inc",
        code="DHANA 60 BUNCH (LOCAL);;2659M",
        barcode="018625167909",
    )
    _snap(
        db,
        today,
        TOMATO,
        "Tomato Plum in LBs",
        supplier="Raja Foods;RBest Produce Inc;Restaurant Depot",
        code=";2524;856977005124",
        barcode="023625724656",
    )
    _snap(
        db,
        today,
        ROTI,
        "Shri Big Whole Wheat Roti 25pc",
        supplier="Arya International",
        code="S-R-BF-WHOLE",
        barcode="793573010292;793573604293",
    )
    # A product whose NAME contains a code typed elsewhere: name matches stay.
    _snap(db, today, "name-2524", "Masala 2524 Blend", supplier="Chetak", code="CH1")
    db.commit()


@pytest.fixture
def buyer(db):
    return client_for(db, make_user(db, "buyer", features=["purchase_order_cart"]))


def _search(client, **params):
    r = client.get(SEARCH, params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_items_search_by_toast_id_supplier_code_and_barcode(buyer, catalog):
    by_id = _search(buyer, q=CILANTRO.upper())
    assert [i["item_id"] for i in by_id] == [CILANTRO]
    assert by_id[0]["matched_on"] == "item_id" and by_id[0]["matched_code"] == CILANTRO
    for key in OLD_KEYS + ("in_stock_qty", "container_qty", "container_of", "all_supplier_item_ids"):
        assert key in by_id[0]

    sku = _search(buyer, q="2659m")
    assert [i["item_id"] for i in sku] == [CILANTRO]
    assert (sku[0]["matched_on"], sku[0]["matched_code"]) == ("supplier_item_id", "2659M")
    assert sku[0]["all_supplier_item_ids"] == "DHANA 60 BUNCH (LOCAL);;2659M"

    # A barcode typed without its leading zero, and the second of a ';' list.
    assert [i["item_id"] for i in _search(buyer, q="18625167909")] == [CILANTRO]
    roti = _search(buyer, q="793573604293")
    assert [i["item_id"] for i in roti] == [ROTI] and roti[0]["matched_on"] == "barcode"


def test_exact_code_first_then_names_then_partial_codes(buyer, catalog):
    items = _search(buyer, q="2524")
    # Tomato's SKU is exactly 2524; the masala has it in its name.
    assert [i["item_id"] for i in items] == [TOMATO, "name-2524"]
    assert items[0]["matched_on"] == "supplier_item_id"
    assert items[1]["matched_on"] == "name" and items[1]["matched_code"] is None

    # Name searches are unchanged: same items, name order.
    names = _search(buyer, q="roti")
    assert [i["name"] for i in names] == ["Shri Big Whole Wheat Roti 25pc"]
    assert names[0]["matched_on"] == "name"


def test_short_or_wordy_queries_dont_match_ids(buyer, catalog):
    assert _search(buyer, q="fb25") == []  # too short for part of a Toast id
    assert _search(buyer, q="facade") == []  # no digit: never part of a GUID
    assert [i["item_id"] for i in _search(buyer, q="fb25bea8")] == [CILANTRO]


def test_supplier_filter_still_applies_and_resolves_that_vendors_code(buyer, catalog):
    # "2659M" is RBest's code (third vendor); in Raja Foods' list the item
    # still matches by code and shows Raja Foods' own SKU.
    raja = _search(buyer, q="2659M", supplier="Raja Foods")
    assert [i["item_id"] for i in raja] == [CILANTRO]
    assert raja[0]["supplier_item_id"] == "DHANA 60 BUNCH (LOCAL)"
    assert _search(buyer, q="2659M", supplier="Chetak") == []


def test_reorder_and_projection_rows_carry_codes(admin_client, catalog, db):
    today = dt.date.today()
    for i in range(14):
        day = today - dt.timedelta(days=i + 1)
        db.add(
            Order(
                toast_order_guid=f"o{i}",
                toast_check_guid=f"c{i}",
                toast_selection_guid=f"s{i}",
                business_date=day,
                item_guid=CILANTRO,
                item_name="Cilantro Bunch",
                quantity=10,
                net_price=10.0,
                voided=False,
            )
        )
    db.commit()
    r = admin_client.get("/api/ordering/reorder-candidates", params={"lookback_days": 14, "lead_time_days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    found = [c for c in body["candidates"] if c["item_id"] == CILANTRO]
    grouped = [c for g in body["vendors"] for c in g["items"] if c["item_id"] == CILANTRO]
    assert found and grouped, body
    assert grouped[0]["all_supplier_item_ids"] == "DHANA 60 BUNCH (LOCAL);;2659M"
    assert found[0]["all_supplier_item_ids"] == "DHANA 60 BUNCH (LOCAL);;2659M"
    assert found[0]["barcode"] == "018625167909"

    r = admin_client.get(
        "/api/ordering/supplier-projection", params={"supplier": "Raja Foods", "lookback_days": 14, "horizons": "7"}
    )
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["item_id"] == CILANTRO)
    assert item["supplier_item_id"] == "DHANA 60 BUNCH (LOCAL)"  # unchanged: this vendor's code
    assert item["all_supplier_item_ids"] == "DHANA 60 BUNCH (LOCAL);;2659M"
    assert item["barcode"] == "018625167909"


def test_items_sold_rows_carry_codes(admin_client, catalog, db):
    day = dt.date.today() - dt.timedelta(days=3)
    db.add(
        Order(
            toast_order_guid="o1",
            toast_check_guid="c1",
            toast_selection_guid="s1",
            business_date=day,
            item_guid=ROTI,
            item_name="Shri Big Whole Wheat Roti 25pc",
            quantity=2,
            net_price=8.0,
            voided=False,
        )
    )
    db.commit()
    r = admin_client.get("/api/sales/items-sold", params={"start": day.isoformat(), "end": day.isoformat()})
    assert r.status_code == 200, r.text
    row = next(i for i in r.json()["items"] if i["item_id"] == ROTI)
    assert row["all_supplier_item_ids"] == "S-R-BF-WHOLE"
    assert row["barcode"] == "793573010292;793573604293"
    assert row["quantity"] == 2


def test_price_history_po_sections_and_store_vs_container_search_codes(admin_client, catalog):
    r = admin_client.get("/api/inventory/price-history", params={"search": "S-R-BF"})
    assert r.status_code == 200, r.text
    assert [i["item_id"] for i in r.json()["items"]] == [ROTI]
    r = admin_client.get("/api/inventory/price-history", params={"search": "856977005124"})
    assert [i["item_id"] for i in r.json()["items"]] == [TOMATO]

    r = admin_client.get("/api/categories/items", params={"q": "2659m"})
    assert r.status_code == 200, r.text
    assert [i["item_id"] for i in r.json()["items"]] == [CILANTRO]


def test_placed_orders_search_by_toast_item_id(admin_client, db):
    from app.models import PlacedOrder

    db.add(
        PlacedOrder(
            supplier="Raja Foods",
            status="placed",
            item_count=1,
            placed_at=dt.datetime.now(dt.timezone.utc),
            items_json=json.dumps([{"name": "Cilantro Bunch", "supplier_item_id": "D60", "qty": 2, "item_id": CILANTRO}]),
        )
    )
    db.commit()
    r = admin_client.get("/api/ordering/orders", params={"search": CILANTRO[:8]})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert admin_client.get("/api/ordering/orders", params={"search": "zzzz-9999"}).json()["total"] == 0


def test_ask_bot_search_filter_matches_codes():
    from app.services.ask_bot import _limit

    rows = [
        {"item_id": CILANTRO, "name": "Cilantro Bunch", "all_supplier_item_ids": "D60;2659M", "barcode": None},
        {"item_id": ROTI, "name": "Roti", "supplier_item_id": "S-R-BF-WHOLE"},
    ]
    assert [i["item_id"] for i in _limit(rows, "2659m")["items"]] == [CILANTRO]
    assert [i["item_id"] for i in _limit(rows, "s-r-bf")["items"]] == [ROTI]
    assert [i["item_id"] for i in _limit(rows, "roti")["items"]] == [ROTI]
