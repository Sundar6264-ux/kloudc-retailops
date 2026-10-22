"""Case sizes from the inventory file's receiving units, orders in cases on
Supplier Projection / the cart / the PO, and the one-tap Add's Undo.

Covers: the tolerant receiving-units parser (services/receiving_units.py),
the upload storing case_size / receiving_unit (migration 0063), API-built
rows carrying them, the suggested order in whole cases (the owner's
example: need 5, case of 24 -> 1 case), the additive projection / cart / PO
fields (`case_size`, `cases`, `units`, `qty_text`, `need_cases`,
`item_case_size`, `can_undo_add`), the PDF and email text, and the 0063
and 0064 round trip on SQLite and PostgreSQL.

The real file: set RETAIL_ITEMS_FILE=/path/to/retail-items.csv and
test_real_file runs against it.
"""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from app.models import CartItem, InventorySnapshot, Order, PlacedOrder
from app.services import order_suggestion as OS
from app.services import receiving_units as RU
from app.services.inventory_parser import parse_and_store
from app.services.po_send import cart_lines, compose, fill_case_from_cart
from app.db import engine
from tests.conftest import client_for, make_user

TODAY = dt.date.today()
PROJ = "/api/ordering/supplier-projection"
CART = "/api/ordering/cart"
CART_ITEMS = "/api/ordering/cart/items"
PDF = "/api/ordering/purchase-order/export-pdf"
MARK = "/api/ordering/cart/mark-placed"
VENDOR = "Babco"
BACKEND = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The parser (pure)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "units, qtys, size, name",
    [
        # The export's ";"-parallel shape.
        ("Each;Case", "1;24", 24.0, "Case"),
        ("Case;Each", "24;1", 24.0, "Case"),
        ("Each; Case", "1; 24", 24.0, "Case"),
        ("Each;Case", "1.0;24.0", 24.0, "Case"),
        # The real export's shapes: only a unit named like a case counts.
        ("Case", "24", 24.0, "Case"),
        ("Case;Case", "24;24", 24.0, "Case"),
        ("Case;Pound", "12;1", 12.0, "Case"),
        ("Case;Case;Pound", "6;12;1", 6.0, "Case"),
        ("Case;Ounce", "10;16", 10.0, "Case"),
        ("Case;Pack", "24;6", 24.0, "Case"),
        # Several different case sizes: the FIRST above 1, in file order.
        ("Case;Case;Case;Case", "24;30;35;60", 24.0, "Case"),
        ("Case;Case", "30;24", 30.0, "Case"),
        # A case quantity of 1 or less never counts (0.5 is half a case).
        ("Case;Case", "0.5;20", 20.0, "Case"),
        ("Case", "0.5", None, "Case"),
        # Pound / Ounce / Each / Pack (or any other unit) alone: no case size.
        ("Pound", "1", None, "Pound"),
        ("Ounce", "16", None, "Ounce"),
        ("Pack", "6", None, "Pack"),
        ("Each;Pack", "1;6", None, "Each"),
        ("Each;Box;Tray", "1;6;30", None, "Each"),
        ("Each;Case;Pallet", "1;12;480", 12.0, "Case"),
        # Only Each (= 1): no case.
        ("Each", "1", None, "Each"),
        ("Each", "", None, "Each"),
        # Blanks / junk.
        ("", "", None, None),
        (None, None, None, None),
        (float("nan"), float("nan"), None, None),
        ("Case", "0", None, "Case"),
        ("Case", "-3", None, "Case"),
        ("Case", "abc", None, "Case"),
        ("Case", "1", None, "Case"),  # a case of 1 says nothing
        ("Case", "99999999", None, "Case"),  # a pasted code, not a pack size
        # The number in the name.
        ("Case of 24", "", 24.0, "Case"),
        ("Case (24)", None, 24.0, "Case"),
        ("CS/12", "", 12.0, "CS"),
        ("24 ct", "", None, "24 ct"),  # not named like a case
        ("Case = 24 Each", "", 24.0, "Case"),
        ("Case 12 x 500g", "", 12.0, "Case"),
        # One quantity for "Each;Case": the case's.
        ("Each;Case", "24", 24.0, "Case"),
        # Other separators.
        ("Each, Case", "1, 24", 24.0, "Case"),
        ("Each|Case", "1|24", 24.0, "Case"),
        ("Each/Case", "1;24", 24.0, "Case"),
        # Quantities with words.
        ("Each;Case", "1 each;24 units", 24.0, "Case"),
        # Quantity only.
        ("", "24", 24.0, "Case"),
        # Thousands separator.
        ("Case", "1,000", 1000.0, "Case"),
    ],
)
def test_case_info_formats(units, qtys, size, name):
    info = RU.case_info(units, qtys)
    assert info.case_size == size
    assert info.unit_name == name


def test_case_sizes_in_file_order():
    assert RU.case_info("Case;Case;Case;Case", "24;30;35;60").case_sizes == (24.0, 30.0, 35.0, 60.0)
    assert RU.case_info("Case;Case;Case", "30;24;30").case_sizes == (30.0, 24.0)
    assert RU.case_info("Case;Case", "0.5;20").case_sizes == (20.0,)
    assert RU.case_info("Pound", "1").case_sizes == ()
    assert RU.case_sizes_text((24.0, 30.0, 35.5)) == "24;30;35.5"
    assert RU.case_sizes_text(()) is None
    assert RU.case_sizes_list("24;30;35;60") == [24.0, 30.0, 35.0, 60.0]
    assert RU.case_sizes_list(None) is None and RU.case_sizes_list("") is None


def test_case_info_keeps_the_raw_cells():
    assert RU.case_info("Each;Case", "1;24").raw == "Each;Case | 1;24"
    assert RU.case_info("", "24").raw == "24"
    assert RU.case_info(None, None).raw is None


def test_cases_for_units_rounds_up_to_whole_cases():
    assert RU.cases_for_units(5, 24) == (1, 24.0)  # need 5 -> 1 case
    assert RU.cases_for_units(24, 24) == (1, 24.0)
    assert RU.cases_for_units(25, 24) == (2, 24.0)
    assert RU.cases_for_units(0, 24) == (0, 24.0)
    assert RU.cases_for_units(-3, 24) == (0, 24.0)
    assert RU.cases_for_units(5, None) == (5, None)  # no case size: units
    assert RU.cases_for_units(5, 1) == (5, None)


def test_qty_text_and_po_line_fields():
    assert RU.qty_text(2, 24) == "2 cases (48 units)"
    assert RU.qty_text(1, 24) == "1 case (24 units)"
    assert RU.qty_text(5, 1) == "5"
    assert RU.qty_text(2.5, None) == "2.5"
    assert RU.po_line_case_fields(2, 24) == {"case_size": 24.0, "cases": 2.0, "units": 48.0,
                                             "qty_text": "2 cases (48 units)"}
    assert RU.po_line_case_fields(5, 1) == {"case_size": None, "cases": None, "units": 5.0, "qty_text": "5"}


# --------------------------------------------------------------------------
# Upload -> inventory_snapshots
# --------------------------------------------------------------------------

CSV = (
    "Item ID,Name,Supplier,Inventory Quantity,Price,Receiving Units,Receiving Unit Quantities\n"
    "noodles,Noodles 300g,Babco,5,3.99,Each;Case,1;24\n"
    "rice,Rice 10lb,Babco,2,19.99,Each,1\n"
    "dal,Toor Dal,Babco,9,4.99,,\n"
    "cilantro,Cilantro Bunch,Babco,3,0.99,Case;Case;Case;Case,24;30;35;60\n"
)


def test_upload_stores_case_size(db):
    result = parse_and_store(db, "retail-items.csv", CSV.encode(), TODAY)
    assert "receiving units" in result["columns_used"]
    assert "receiving unit quantities" in result["columns_used"]
    assert "receiving units" not in result["columns_ignored"]
    assert result["rows_with_case_size"] == 2
    rows = {r.item_id: r for r in db.query(InventorySnapshot).all()}
    assert rows["noodles"].case_size == 24.0 and rows["noodles"].receiving_unit == "Case"
    assert rows["noodles"].receiving_units_raw == "Each;Case | 1;24"
    assert rows["rice"].case_size is None and rows["rice"].receiving_unit == "Each"
    assert rows["dal"].case_size is None and rows["dal"].receiving_unit is None
    assert rows["noodles"].case_sizes == "24"
    assert rows["cilantro"].case_size == 24.0 and rows["cilantro"].case_sizes == "24;30;35;60"
    assert rows["rice"].case_sizes is None


def test_upload_without_the_columns_leaves_them_null(db):
    csv = "Item ID,Name,Inventory Quantity\nnoodles,Noodles 300g,5\n"
    result = parse_and_store(db, "old.csv", csv.encode(), TODAY)
    assert result["rows_with_case_size"] is None
    row = db.query(InventorySnapshot).one()
    assert row.case_size is None and row.receiving_unit is None


def test_reupload_updates_case_size(db):
    parse_and_store(db, "a.csv", CSV.encode(), TODAY)
    parse_and_store(db, "b.csv", CSV.replace("1;24", "1;12").encode(), TODAY)
    assert db.query(InventorySnapshot).filter_by(item_id="noodles").one().case_size == 12.0


def test_api_rows_carry_case_size_from_the_latest_csv():
    from app.services import inventory_api as ia

    assert {"case_size", "receiving_unit", "receiving_units_raw", "case_sizes"} <= set(ia.CARRIED_FIELDS)


def _real_files():
    paths = []
    if os.environ.get("RETAIL_ITEMS_FILE"):
        paths.append(os.environ["RETAIL_ITEMS_FILE"])
    return [p for p in paths if os.access(p, os.R_OK)]


def test_real_file(db):
    """The real export (RETAIL_ITEMS_FILE), when present: the owner's
    examples come out as agreed, and the count of items with a case size is
    printed (pytest -s)."""
    files = _real_files()
    if not files:
        pytest.skip("no real retail-items file (set RETAIL_ITEMS_FILE)")
    path = files[-1]
    with open(path, "rb") as f:
        result = parse_and_store(db, os.path.basename(path), f.read(), TODAY)
    assert result["rows_loaded"] > 100
    assert "receiving units" in result["columns_used"]
    assert "receiving unit quantities" in result["columns_used"]
    rows = db.query(InventorySnapshot).all()
    sized = [r for r in rows if r.case_size is not None]
    assert result["rows_with_case_size"] == len(sized) > 0
    assert all(1 < r.case_size <= RU.MAX_CASE_SIZE for r in sized)
    print(f"\nreal file: {len(rows)} items, {len(sized)} with a case size, "
          f"{sum(1 for r in sized if r.case_sizes and ';' in r.case_sizes)} with other case sizes too")

    def one(name):
        found = [r for r in rows if r.name == name]
        assert found, name
        # An item listed twice (one row with blank cells): the row with the cells.
        return next((r for r in found if r.receiving_units_raw), found[0])

    assert one("Maggi Masala Noodles 280g").case_size == 24.0
    cilantro = one("Cilantro Bunch")
    assert cilantro.case_size == 24.0 and RU.case_sizes_list(cilantro.case_sizes) == [24.0, 30.0, 35.0, 60.0]
    assert one("Tomato Regular in LBs").case_size == 4.0
    assert one("Okra Indian in LBs").case_size == 12.0
    assert one("Onion Red 2Lb").case_size == 20.0
    assert one("Whole Chicken Clean & Cut").case_size is None


# --------------------------------------------------------------------------
# Suggestion / projection in cases
# --------------------------------------------------------------------------


def _row(need, on_hand=5.0, horizon=21):
    return {"item_id": "noodles", "avg_weekly_demand": 3.3, "on_hand_qty": on_hand,
            "projections": {str(horizon): {"projected_demand": 10.0, "need_to_order": need}}}


def test_noodles_need_5_is_1_case_of_24():
    s = OS.suggest(_row(5.0), 21, case=(24.0, "receiving_units"))
    assert s["qty"] == 1 and s["units"] == 5 and s["case_of"] == 24.0 and s["case_size"] == 24.0
    assert s["case_of_source"] == "receiving_units"
    assert "suggested 1 case of 24 (24 units) for 3 weeks" in s["basis"]


def test_known_case_sizes_order_cart_then_receiving_units_then_last_order(db):
    db.add(CartItem(supplier=VENDOR, item_id="in_cart_units", name="A", qty=4, case_of=1))
    db.add(CartItem(supplier=VENDOR, item_id="in_cart_case", name="B", qty=1, case_of=6))
    db.add(PlacedOrder(supplier=VENDOR, status="placed", item_count=1, placed_at=dt.datetime.now(dt.timezone.utc),
                       items_json=json.dumps([{"name": "C", "qty": 1, "item_id": "last", "case_of": 10},
                                              {"name": "D", "qty": 1, "item_id": "both", "case_of": 10}])))
    db.commit()
    inv = {"in_cart_units": 24.0, "in_cart_case": 24.0, "both": 24.0, "inv_only": 12.0, "one": 1.0}
    got = OS.known_case_sizes(db, VENDOR, ["in_cart_units", "in_cart_case", "last", "both", "inv_only", "one"],
                              inventory_cases=inv)
    # The cart line decides (its Case of 1 = units, VX12).
    assert "in_cart_units" not in got
    assert got["in_cart_case"] == (6.0, "cart")
    # Receiving units beat an old order's Case of.
    assert got["both"] == (24.0, "receiving_units")
    assert got["inv_only"] == (12.0, "receiving_units")
    assert got["last"] == (10.0, "last_order")
    assert "one" not in got


def _snap(db, item_id, name, qty, case_size=None, supplier=VENDOR, unit="Case", sizes=None):
    db.add(InventorySnapshot(snapshot_date=TODAY, item_id=item_id, name=name, inventory_quantity=qty, price=3.99,
                             cost=2.0, supplier=supplier, category="Noodles", source_filename="f.csv",
                             case_size=case_size, receiving_unit=unit if case_size else "Each",
                             case_sizes=sizes))


def _sale(db, day, item_id, qty, guid):
    db.add(Order(toast_order_guid="o" + guid, toast_check_guid="c" + guid, toast_selection_guid=guid,
                 business_date=day, item_guid=item_id, item_name=item_id, quantity=qty, unit_price=1.0,
                 net_price=qty, voided=False))


@pytest.fixture
def noodles(db):
    # 28 sold in 28 days (legacy 1/day): 21-day need = 21 - 5 on hand = 16 units.
    _snap(db, "noodles", "Noodles 300g", 5, case_size=24.0, sizes="24;30")
    _snap(db, "rice", "Rice 10lb", 5)
    for n in range(28):
        day = TODAY - dt.timedelta(days=n + 1)
        _sale(db, day, "noodles", 1, f"m-{n}")
        _sale(db, day, "rice", 1, f"r-{n}")
    db.commit()


def _projection(client):
    start = (TODAY - dt.timedelta(days=28)).isoformat()
    r = client.get(PROJ, params={"supplier": VENDOR, "start": start, "horizons": "21", "method": "legacy"})
    assert r.status_code == 200, r.text
    return {i["item_id"]: i for i in r.json()["items"]}


def test_projection_orders_case_items_in_cases(db, admin_client, noodles):
    items = _projection(admin_client)
    m = items["noodles"]
    assert m["case_size"] == 24.0 and m["receiving_unit"] == "Case"
    assert m["case_sizes"] == [24.0, 30.0]
    assert m["projections"]["21"]["need_to_order"] == 16  # units, unchanged
    assert m["projections"]["21"]["need_cases"] == 1
    s = m["suggested_order"]
    assert s["qty"] == 1 and s["case_of"] == 24.0 and s["case_of_source"] == "receiving_units"
    # No case size: units, and no need_cases.
    r = items["rice"]
    assert r["case_size"] is None and "need_cases" not in r["projections"]["21"]
    assert r["suggested_order"]["qty"] == 16 and r["suggested_order"]["case_of"] is None


def test_projection_need_cases_with_the_availability_method(db, admin_client, noodles):
    start = (TODAY - dt.timedelta(days=28)).isoformat()
    r = admin_client.get(PROJ, params={"supplier": VENDOR, "start": start, "horizons": "21"})
    m = next(i for i in r.json()["items"] if i["item_id"] == "noodles")
    need = m["projections"]["21"]["need_to_order"]
    assert m["projections"]["21"]["need_cases"] == RU.cases_for_units(need, 24)[0]


# --------------------------------------------------------------------------
# Cart
# --------------------------------------------------------------------------


@pytest.fixture
def buyer(db):
    return client_for(db, make_user(db, "buyer", features=["supplier_projection", "purchase_order_cart"]))


def test_cart_line_case_fields(db, buyer, noodles):
    r = buyer.post(CART_ITEMS, json={"supplier": VENDOR, "items": [
        {"item_id": "noodles", "name": "Noodles 300g", "qty": 2, "case_of": 24},
        {"item_id": "rice", "name": "Rice 10lb", "qty": 5},
    ]})
    assert r.status_code == 200, r.text
    added = {i["name"]: i for i in r.json()}
    assert added["Noodles 300g"]["case_size"] == 24.0 and added["Noodles 300g"]["cases"] == 2.0
    assert added["Noodles 300g"]["qty_text"] == "2 cases (48 units)" and added["Noodles 300g"]["total_units"] == 48
    assert added["Rice 10lb"]["case_size"] is None and added["Rice 10lb"]["cases"] is None
    assert added["Rice 10lb"]["qty_text"] == "5"
    lines = {i["name"]: i for s in buyer.get(CART).json()["suppliers"] for i in s["items"]}
    assert lines["Noodles 300g"]["item_case_size"] == 24.0 and lines["Noodles 300g"]["receiving_unit"] == "Case"
    assert lines["Rice 10lb"]["item_case_size"] is None
    assert lines["Noodles 300g"]["item_case_sizes"] == [24.0, 30.0] and lines["Rice 10lb"]["item_case_sizes"] is None
    # Old fields unchanged.
    assert lines["Noodles 300g"]["qty"] == 2 and lines["Noodles 300g"]["case_of"] == 24


def test_case_size_alone_is_taken_as_case_of(db, buyer, noodles):
    r = buyer.post(CART_ITEMS, json={"supplier": VENDOR, "items": [
        {"item_id": "noodles", "name": "Noodles 300g", "qty": 1, "case_size": 24}]})
    assert r.status_code == 200 and r.json()[0]["case_of"] == 24


def test_units_qty_is_never_turned_into_cases(db, buyer, noodles):
    # A client that adds in units (Reorder, an older app build) keeps its units:
    # the server never applies the item's case size by itself.
    r = buyer.post(CART_ITEMS, json={"supplier": VENDOR, "items": [{"item_id": "noodles", "name": "Noodles 300g", "qty": 16}]})
    line = r.json()[0]
    assert line["case_of"] == 1 and line["qty"] == 16 and line["qty_text"] == "16"
    # ... and GET /cart says the item comes in cases of 24 (the cart offers to switch).
    lines = {i["name"]: i for s in buyer.get(CART).json()["suppliers"] for i in s["items"]}
    assert lines["Noodles 300g"]["item_case_size"] == 24.0


def test_undo_own_add_without_removal_rights(db, buyer, noodles):
    r = buyer.post(CART_ITEMS, json={"supplier": VENDOR, "items": [
        {"item_id": "noodles", "name": "Noodles 300g", "qty": 1, "case_of": 24}]})
    line = r.json()[0]
    assert line["can_undo_add"] is True
    # Someone else (also without removal rights) can't.
    other = client_for(db, make_user(db, "other", features=["purchase_order_cart"]))
    assert other.delete(f"{CART_ITEMS}/{line['id']}").status_code == 403
    assert buyer.delete(f"{CART_ITEMS}/{line['id']}").status_code == 200
    assert db.query(CartItem).count() == 0


def test_undo_window_closes(db, buyer, noodles):
    r = buyer.post(CART_ITEMS, json={"supplier": VENDOR, "items": [
        {"item_id": "noodles", "name": "Noodles 300g", "qty": 1, "case_of": 24}]})
    row = db.get(CartItem, r.json()[0]["id"])
    row.added_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
    db.commit()
    assert buyer.delete(f"{CART_ITEMS}/{row.id}").status_code == 403
    assert buyer.patch(f"{CART_ITEMS}/{row.id}", json={"qty": 0}).status_code == 403
    # Stepping is still fine.
    assert buyer.patch(f"{CART_ITEMS}/{row.id}", json={"qty": 2}).json()["qty_text"] == "2 cases (48 units)"


# --------------------------------------------------------------------------
# PO lines, PDF, email
# --------------------------------------------------------------------------


@pytest.fixture
def cart(db, noodles):
    db.add(CartItem(supplier=VENDOR, item_id="noodles", name="Noodles 300g", supplier_item_id="MG300", qty=2, case_of=24))
    db.add(CartItem(supplier=VENDOR, item_id="rice", name="Rice 10lb", supplier_item_id="R10", qty=5, case_of=1))
    db.commit()


def test_po_lines_carry_cases(db, cart):
    _rows, items = cart_lines(db, VENDOR)
    by = {i["name"]: i for i in items}
    assert by["Noodles 300g"]["case_size"] == 24.0 and by["Noodles 300g"]["cases"] == 2.0
    assert by["Noodles 300g"]["units"] == 48.0 and by["Noodles 300g"]["qty_text"] == "2 cases (48 units)"
    assert by["Rice 10lb"]["case_size"] is None and by["Rice 10lb"]["cases"] is None
    assert by["Noodles 300g"]["qty"] == 2  # qty = cases, as before


def test_email_says_cases_and_units(db, cart):
    _rows, items = cart_lines(db, VENDOR)
    # Mixed: 2 cases of noodles + 5 units of rice - never "7 cases".
    _subject, body = compose(VENDOR, items)
    assert "2 items, 2 cases and 5 units in total." in body
    assert "Some lines are in cases and some in units - each line says which, e.g. 2 cases (48 units) or 5 units." in body
    assert "Quantities are in cases" not in body and "7 cases" not in body
    # Case lines only.
    _s, cases_only = compose(VENDOR, [i for i in items if i["case_size"] is not None])
    assert "1 item, 2 cases in total. Quantities are in cases, e.g. 2 cases (48 units).\n\n" in cases_only
    # Unit lines only (Case of 1 / no case size).
    _s, units_only = compose(VENDOR, [i for i in items if i["case_size"] is None])
    assert "1 item, 5 units in total. Quantities are in units.\n\n" in units_only
    assert "case" not in units_only.split("Please reply")[0].lower()


def test_qty_totals_keep_cases_and_units_apart():
    from app.services.po_send import qty_totals, whatsapp_caption

    mixed = [
        {"qty": 6, "case_of": 24}, {"qty": 5, "case_size": 12}, {"qty": 10, "case_of": 1}, {"qty": 8},
        {"qty": 1, "case_of": None},
    ]
    t = qty_totals(mixed)
    assert (t["cases"], t["units"], t["case_lines"], t["unit_lines"]) == (11, 19, 2, 3)
    assert t["text"] == "11 cases and 19 units"
    assert qty_totals([{"qty": 1, "case_of": 6}])["text"] == "1 case"
    assert qty_totals([{"qty": 1}])["text"] == "1 unit"
    assert qty_totals([])["text"] == "0 units"
    assert "5 items, 11 cases and 19 units. Please confirm." in whatsapp_caption(VENDOR, mixed)
    assert ", 1 case. Please confirm." in whatsapp_caption(VENDOR, [{"qty": 1, "case_of": 6}])
    assert ", 3 units. Please confirm." in whatsapp_caption(VENDOR, [{"qty": 3, "case_of": 1}])


def test_fill_case_from_cart_for_old_clients(db, cart):
    # An older app build sends name / code / qty only.
    got = fill_case_from_cart(db, VENDOR, [
        {"name": "Noodles 300g", "supplier_item_id": "MG300", "qty": 2},
        {"name": "Noodles 300g", "supplier_item_id": "MG300", "qty": 3},  # qty differs: not that line
        {"name": "Rice 10lb", "qty": 5},
        {"name": "Typed", "qty": 1, "case_of": 6},  # its own wins
    ])
    assert got[0]["case_size"] == 24.0 and got[0]["qty_text"] == "2 cases (48 units)" and got[0]["item_id"] == "noodles"
    assert got[1]["case_size"] is None and got[1]["qty_text"] == "3"
    assert got[2]["case_size"] is None
    assert got[3]["case_size"] == 6.0 and got[3]["cases"] == 1.0


def test_pdf_and_placed_order_lines(db, cart, monkeypatch):
    seen = {}
    from app.services import po_export

    real = po_export.export_simple_po_pdf

    def spy(supplier, items, sections=None, **kw):
        seen["items"] = items
        return real(supplier, items, sections, **kw) if sections is not None else real(supplier, items, **kw)

    monkeypatch.setattr("app.services.po_send.export_simple_po_pdf", spy)
    buyer = client_for(db, make_user(db, "b2", is_admin=True))
    r = buyer.post(PDF, json={"supplier": VENDOR, "items": [
        {"name": "Noodles 300g", "supplier_item_id": "MG300", "qty": 2},
        {"name": "Rice 10lb", "supplier_item_id": "R10", "qty": 5},
    ]})
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    by = {i["name"]: i for i in seen["items"]}
    # The PDF's lines carry the case fields (its Cases / Units per case columns).
    assert by["Noodles 300g"]["case_size"] == 24.0 and by["Noodles 300g"]["cases"] == 2.0
    assert "case_size" not in by["Rice 10lb"] and "cases" not in by["Rice 10lb"]
    # The logged "pdf" row carries the case fields too.
    pdf_row = db.query(PlacedOrder).one()
    lines = {i["name"]: i for i in json.loads(pdf_row.items_json)}
    assert lines["Noodles 300g"]["cases"] == 2.0 and lines["Noodles 300g"]["case_size"] == 24.0

    r = buyer.post(MARK, params={"supplier": VENDOR})
    assert r.status_code == 200, r.text
    db.expire_all()
    order = db.query(PlacedOrder).filter_by(status="placed").one()
    lines = {i["name"]: i for i in json.loads(order.items_json)}
    assert lines["Noodles 300g"]["case_size"] == 24.0 and lines["Noodles 300g"]["cases"] == 2.0
    assert lines["Noodles 300g"]["qty_text"] == "2 cases (48 units)"
    assert lines["Rice 10lb"]["case_size"] is None and lines["Rice 10lb"]["qty"] == 5


def test_pdf_shows_the_case_columns(db, cart):
    import io

    from pypdf import PdfReader

    from app.services.po_send import build_po_pdf

    _rows, items = cart_lines(db, VENDOR)
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(build_po_pdf(db, VENDOR, items))).pages)
    assert "Cases" in text and "Units per case" in text
    # Each line's Qty says what it counts, like the email.
    assert "2 cases" in text and "5 units" in text
    # Units only: plain numbers, as before (the email says "Quantities are in units").
    plain = "".join(
        p.extract_text()
        for p in PdfReader(io.BytesIO(build_po_pdf(db, VENDOR, [i for i in items if i["case_size"] is None]))).pages
    )
    assert "Units per case" not in plain and "5 units" not in plain


# --------------------------------------------------------------------------
# Migration 0063 round trip (SQLite chain, and PostgreSQL from the baseline)
# --------------------------------------------------------------------------

NEW_COLS = {"case_size", "receiving_unit", "receiving_units_raw", "case_sizes"}


def test_0063_0064_up_down_up_sqlite(tmp_path):
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True)

    def cols():
        con = sqlite3.connect(db_file)
        try:
            return {r[1] for r in con.execute("PRAGMA table_info(inventory_snapshots)")}
        finally:
            con.close()

    run("upgrade", "0064")
    assert NEW_COLS <= cols()
    con = sqlite3.connect(db_file)
    con.execute("INSERT INTO inventory_snapshots (snapshot_date, item_id, name, case_size, receiving_unit, uploaded_at)"
                " VALUES ('2026-10-22', 'noodles', 'Noodles', 24, 'Case', '2026-10-22 06:00:00')")
    con.commit()
    con.close()
    run("downgrade", "0062")
    assert not (NEW_COLS & cols())
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT name FROM inventory_snapshots").fetchall() == [("Noodles",)]
    con.close()
    run("upgrade", "0064")
    assert NEW_COLS <= cols()


@pytest.fixture
def scratch_pg_db():
    """An extra empty PostgreSQL database for this test (run prefix), dropped after."""
    import psycopg

    from tests import pg_harness

    name = f"{pg_harness.run_db_name()}_cs"
    with psycopg.connect(pg_harness._libpq_conninfo(pg_harness.admin_url()), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
    try:
        yield pg_harness.app_url(name)
    finally:
        with psycopg.connect(pg_harness._libpq_conninfo(pg_harness.admin_url()), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL only (RETAILOPS_TEST_DB=postgres)")
def test_0063_0064_up_down_up_postgres(scratch_pg_db):
    from sqlalchemy import create_engine, inspect, pool, text

    from app.migrate import current_store_revision, downgrade_store_db, upgrade_store_db

    def cols(eng):
        return {c["name"] for c in inspect(eng).get_columns("inventory_snapshots")}

    assert upgrade_store_db(scratch_pg_db, "0064") == "0064"
    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        assert NEW_COLS <= cols(eng)
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, case_size, receiving_unit, uploaded_at)"
                " VALUES ('2026-10-22', 'noodles', 'Noodles', 24, 'Case', now())"))
        assert downgrade_store_db(scratch_pg_db, "0062") == "0062"
        assert not (NEW_COLS & cols(eng))
        with eng.connect() as conn:
            assert conn.execute(text("SELECT name FROM inventory_snapshots")).scalars().all() == ["Noodles"]
        assert upgrade_store_db(scratch_pg_db, "0064") == "0064"
        assert current_store_revision(scratch_pg_db) == "0064"
        assert NEW_COLS <= cols(eng)
    finally:
        eng.dispose()
