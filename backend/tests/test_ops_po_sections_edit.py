"""Editable PO sections (Sep 30): the owner edits the heading each product
prints under on the vendor PO PDF - per item, per vendor, or by renaming /
merging a section - through the EXISTING /api/categories/* endpoints, in the
JSON shapes the released app build 5 parses. Toast categories (and the
app-side category_overrides corrections) are untouched and keep feeding the
automatic section."""

import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest
from pypdf import PdfReader
from sqlalchemy import select, text

from app.models import CartItem, CategoryOverride, PoSectionRule
from app.services.po_section_rules import GROUP_LABEL, load_section_rules
from app.services.po_sections import OTHER, group_po_items, section_for
from app.services.reorder import latest_inventory_by_item
from tests.conftest import client_for, make_user
from tests.test_ops_category_overrides import add_rule, add_snap, raw_rows

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path(__file__).resolve().parent / "data"


def body(section):
    """What build 5 sends (CategoryValues.toJson)."""
    return {"category_group": GROUP_LABEL, "category": section, "subcategory": ""}


@pytest.fixture
def store(db):
    add_snap(db, "parle", "Parle-G Biscuits 800g", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies",
             supplier_item_id="RJ-1")
    add_snap(db, "jeera", "Deep Jeera 7oz", "Raja Foods;House of Spices", "Spices & Seasonings", "Whole Spices",
             supplier_item_id="RJ-2")
    add_snap(db, "atta", "Aashirvaad Atta 20lb", "Raja Foods", "Grains, Flours & Cereals", "Wheat Flour (Atta)")
    add_snap(db, "chai", "Wagh Bakri Tea 1kg", "House of Spices", "Tea, Coffee & Hot Beverages", "Black Tea")
    add_snap(db, "kitkat", "KitKat 4 Finger", "Raja Foods", "Snacks & Savories", "Chocolates & Candy")
    add_snap(db, "rusk", "KCB Plain Tea Rusk 7oz", "Shaz International", "Pantry Staples", "Roti, Naan, Wraps")
    add_snap(db, "samosa", "Veg Samosa", None, "Snacks", None, group="STREET KITCHEN", price=None)
    db.commit()
    # An existing Toast-category correction (stays, feeds the automatic section).
    add_rule(db, "supplier", "Shaz International")
    return db


@pytest.fixture
def editor(db):
    return client_for(db, make_user(db, "meera", features=["category_edits", "purchase_order_cart"]))


def items(client, **params):
    r = client.get("/api/categories/items", params=params)
    assert r.status_code == 200, r.text
    return {i["item_id"]: i for i in r.json()["items"]}


def overrides(db):
    db.expire_all()
    return [
        (r.id, r.match_type, r.match_value, r.category_group, r.category, r.subcategory, r.active)
        for r in db.execute(select(CategoryOverride).order_by(CategoryOverride.id)).scalars()
    ]


# --------------------------------------------------------------------------
# Automatic sections: new subcategories + the prod catalog
# --------------------------------------------------------------------------


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_new_subcategory_mappings():
    cases = [
        (("RETAIL", "Snacks & Savories", "Chocolates & Candy"), "Chocolates & Candy"),
        (("RETAIL", "Ready-to-Eat & Instant Foods", "Soups"), "Ready-to-Eat & Instant"),
        (("RETAIL", "Tea, Coffee & Hot Beverages", "Health & Malt Drinks"), "Tea & Coffee"),
        (("RETAIL", "Tea, Coffee & Hot Beverages", "Filter & Ground Coffee"), "Tea & Coffee"),
        (("RETAIL", "Dairy & Dairy Alternatives", "Eggs"), "Dairy"),
        (("RETAIL", "Household & Cleaning", "Laundry"), "Household & Cleaning"),
        (("PRODUCE", "Fresh Herbs", None), "Produce - Vegetables"),
    ]
    for (group, cat, sub), section in cases:
        assert section_for(group, cat, sub, "Some Item") == section, (cat, sub)
    # Frozen sweets no longer default to Frozen - Vegetables.
    assert section_for("RETAIL", "Frozen Foods", "Frozen Sweets & Mithai", "Haldiram Soan Papdi") == \
        "Frozen - Sweets & Desserts"
    assert section_for("RETAIL", "Frozen Foods", "Frozen Sweets & Mithai", "Vadilal Mango Kulfi") == \
        "Frozen - Ice Cream"
    from app.services import po_sections

    builtin = po_sections.BUILTIN_SECTIONS  # the active pack's, read now
    assert "Chocolates & Candy" in builtin and OTHER not in builtin


def test_every_prod_catalog_combo_has_a_section():
    """Every (group, category, subcategory) in the prod catalog (Sep 30, with
    the category corrections applied) prints under a real section, except
    the kitchen / menu groups and the few non-stock categories."""
    combos = json.loads((DATA / "prod_category_combos_2026-09-30.json").read_text())
    assert len(combos) > 150
    kitchen_groups = {"CATERING MENU", "STREET KITCHEN", "EVENT MENU"}
    allowed_other = {"Outdoor Recreation", "Smokeless Tobacco", "Uninvoiced/Kitchen"}
    missing = []
    for group, cat, sub in combos:
        if group.upper() in kitchen_groups or cat in allowed_other:
            continue
        if section_for(group, cat, sub, "") == OTHER:
            missing.append((group, cat, sub))
    assert missing == []


# --------------------------------------------------------------------------
# Resolution: item > vendor > automatic, then renames; Other last
# --------------------------------------------------------------------------


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_resolution_order_and_renames(store, editor):
    rows = items(editor)
    assert rows["parle"]["category"] == "Biscuits, Cookies & Rusk" and rows["parle"]["source"] == "toast"
    # The Shaz correction (Toast category) feeds the automatic section.
    assert rows["rusk"]["category"] == "Biscuits, Cookies & Rusk"
    assert rows["kitkat"]["category"] == "Chocolates & Candy"

    editor.post("/api/categories/vendor-rules", json=dict(body("Raja Picks"), vendor="Raja Foods"))
    rows = items(editor)
    assert rows["parle"]["category"] == "Raja Picks" and rows["parle"]["source"] == "vendor"
    assert rows["parle"]["toast_category"] == "Biscuits, Cookies & Rusk"  # automatic
    assert rows["chai"]["source"] == "toast"

    editor.put("/api/categories/items/parle", json=body("Front Shelf"))
    rows = items(editor)
    assert rows["parle"]["category"] == "Front Shelf" and rows["parle"]["source"] == "item"
    assert rows["parle"]["vendor_rule"]["match_value"] == "Raja Foods"

    # Renames apply to whatever section resulted: automatic, vendor, item.
    for old, new in (("Front Shelf", "Shelf A"), ("Raja Picks", "Raja"), ("Tea & Coffee", "Chai & Coffee")):
        assert editor.post("/api/categories/renames", json={"from_section": old, "to_section": new}).status_code == 200
    rows = items(editor)
    assert rows["parle"]["category"] == "Shelf A"
    assert rows["atta"]["category"] == "Raja"
    assert rows["chai"]["category"] == "Chai & Coffee" and rows["chai"]["toast_category"] == "Chai & Coffee"


def test_vendor_rule_prefers_the_pos_supplier(store, editor):
    editor.post("/api/categories/vendor-rules", json=dict(body("Raja Aisle"), vendor="Raja Foods"))
    editor.post("/api/categories/vendor-rules", json=dict(body("HOS Aisle"), vendor="House of Spices"))
    latest = latest_inventory_by_item(store)
    store.info.clear()
    rules = load_section_rules(store)
    jeera = latest["jeera"]
    assert rules.section_for_snap("jeera", jeera, "Raja Foods") == "Raja Aisle"
    assert rules.section_for_snap("jeera", jeera, "House of Spices") == "HOS Aisle"
    # Without a PO (the page) the newest matching vendor rule wins.
    assert rules.section_for_snap("jeera", jeera) == "HOS Aisle"


def test_group_po_items_other_last_and_hand_added_line_sections(store, editor):
    editor.put("/api/categories/items/chai", json=body("Aardvark"))
    store.info.clear()
    lines = [
        {"name": "Wagh Bakri Tea 1kg", "supplier_item_id": None, "qty": 1},
        {"name": "Mystery Item", "supplier_item_id": None, "qty": 2},
        {"name": "New Pickle Jar", "supplier_item_id": None, "qty": 3},
    ]
    grouped = group_po_items(lines, latest_inventory_by_item(store), "House of Spices", load_section_rules(store),
                             {"new pickle jar": "Pickles & Chutneys"})
    assert [s for s, _ in grouped] == ["Aardvark", "Pickles & Chutneys", OTHER]
    # Other stays last even when a section sorts after it alphabetically.
    grouped = group_po_items(lines[1:2] + [{"name": "Zed", "qty": 1}], {}, "X", load_section_rules(store),
                             {"zed": "Zzz"})
    assert [s for s, _ in grouped] == ["Zzz", OTHER]


# --------------------------------------------------------------------------
# PDF: the item prints under its new heading
# --------------------------------------------------------------------------


def _pdf_text(client, supplier, lines):
    r = client.post("/api/ordering/purchase-order/export-pdf", json={"supplier": supplier, "items": lines})
    assert r.status_code == 200, r.text
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(r.content)).pages)
    # Headings are centred (Oct 14), so text extraction can pad them with
    # spaces; compare line by line.
    return "\n".join(line.strip() for line in text.splitlines())


def _heading_before(textdump, item_name):
    """The last known section heading printed before `item_name` (headings
    print in capitals)."""
    pos = textdump.index(item_name)
    best = None
    for heading in _headings:
        i = textdump.rfind("\n" + heading.upper() + "\n", 0, pos)
        if i >= 0 and (best is None or i > best[0]):
            best = (i, heading)
    return best[1] if best else None


_headings = []


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_pdf_prints_item_under_its_new_heading(store, editor):
    lines = [
        {"name": "Parle-G Biscuits 800g", "supplier_item_id": "RJ-1", "qty": 2},
        {"name": "Aashirvaad Atta 20lb", "supplier_item_id": None, "qty": 1},
        {"name": "Hand Added Rice Bag", "supplier_item_id": None, "qty": 4},
    ]
    _headings[:] = ["Biscuits, Cookies & Rusk", "Flours, Atta & Rava", "Checkout Counter", "Staples Aisle", OTHER]
    before = _pdf_text(editor, "Raja Foods", lines)
    assert _heading_before(before, "Parle-G Biscuits 800g") == "Biscuits, Cookies & Rusk"
    assert _heading_before(before, "Hand Added Rice Bag") == OTHER

    editor.put("/api/categories/items/parle", json=body("Checkout Counter"))
    editor.post("/api/categories/renames", json={"from_section": "Flours, Atta & Rava", "to_section": "Staples Aisle"})
    # A hand-added cart line given a section prints there instead of Other.
    cart = editor.post("/api/ordering/cart/items", json={"supplier": "Raja Foods", "items": [
        {"name": "Hand Added Rice Bag", "qty": 4}]}).json()
    r = editor.put("/api/categories/cart-lines/%d" % cart[0]["id"], json={"section": "staples aisle"})
    assert r.status_code == 200 and r.json()["section"] == "Staples Aisle" and r.json()["section_source"] == "line"

    after = _pdf_text(editor, "Raja Foods", lines)
    assert _heading_before(after, "Parle-G Biscuits 800g") == "Checkout Counter"
    assert _heading_before(after, "Aashirvaad Atta 20lb") == "Staples Aisle"
    assert _heading_before(after, "Hand Added Rice Bag") == "Staples Aisle"
    assert "BISCUITS, COOKIES & RUSK" not in after and "FLOURS, ATTA & RAVA" not in after


# --------------------------------------------------------------------------
# API: gate, build-5 shapes, edits
# --------------------------------------------------------------------------


def test_permission_gate(store, staff_client, editor, admin_client, anon_client):
    calls = [
        ("get", "/api/categories/items", {}),
        ("get", "/api/categories/options", {}),
        ("get", "/api/categories/vendor-rules", {}),
        ("get", "/api/categories/history", {}),
        ("get", "/api/categories/sections", {}),
        ("get", "/api/categories/renames", {}),
        ("get", "/api/categories/changes", {}),
        ("get", "/api/categories/cart-preview", {}),
        ("put", "/api/categories/items/parle", {"json": body("X")}),
        ("delete", "/api/categories/items/parle", {}),
        ("post", "/api/categories/items/bulk", {"json": dict(body("X"), item_ids=["parle"])}),
        ("post", "/api/categories/items/reset", {"json": {"item_ids": ["parle"]}}),
        ("post", "/api/categories/items/move", {"json": {"vendor": "Raja Foods", "section": "X"}}),
        ("post", "/api/categories/vendor-rules", {"json": dict(body("X"), vendor="Raja Foods")}),
        ("post", "/api/categories/renames", {"json": {"from_section": "Dairy", "to_section": "Milk"}}),
        ("post", "/api/categories/changes/undo", {"json": {}}),
        ("put", "/api/categories/cart-lines/1", {"json": {"section": "X"}}),
    ]
    for method, url, kw in calls:
        assert getattr(anon_client, method)(url, **kw).status_code == 401, url
        assert getattr(staff_client, method)(url, **kw).status_code == 403, url
    assert editor.get("/api/categories/items").status_code == 200
    assert admin_client.get("/api/categories/items").status_code == 200
    # The old Toast-category mapping-rule endpoints are gone.
    assert admin_client.get("/api/categories/mapping-rules").status_code in (404, 405)


ITEM_KEYS = {"item_id", "name", "supplier", "barcode", "category_group", "category", "subcategory",
             "toast_category_group", "toast_category", "toast_subcategory", "source", "rule", "vendor_rule",
             "fallback_rule"}
RULE_KEYS = {"id", "match_type", "match_value", "category_group", "category", "subcategory", "created_at",
             "created_by"}
FULL_RULE_KEYS = RULE_KEYS | {"note", "active", "updated_at", "updated_by"}


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_build5_shapes(store, editor):
    editor.post("/api/categories/vendor-rules", json=dict(body("Raja Picks"), vendor="Raja Foods"))
    r = editor.put("/api/categories/items/parle", json=body("Front Shelf"))
    assert r.status_code == 200 and set(r.json()) == {"changed", "item"}
    item = r.json()["item"]
    assert ITEM_KEYS <= set(item)
    assert (item["category_group"], item["subcategory"], item["toast_category_group"], item["toast_subcategory"]) == \
        (GROUP_LABEL, None, GROUP_LABEL, None)
    assert item["source"] in ("toast", "item", "vendor", "category")
    assert RULE_KEYS <= set(item["rule"]) and item["rule"]["match_type"] == "item_id"
    assert isinstance(item["rule"]["id"], int)
    assert item["vendor_rule"]["match_type"] == "supplier" and item["fallback_rule"]["match_value"] == "Raja Foods"

    page = editor.get("/api/categories/items", params={"source": "changed", "limit": 200}).json()
    assert set(page) >= {"total", "items"} and page["total"] == len(page["items"])
    assert all(i["source"] != "toast" for i in page["items"])

    opts = editor.get("/api/categories/options").json()
    assert opts["groups"] == [GROUP_LABEL]
    combos = {c["category"]: c for c in opts["combos"]}
    assert set(combos["Front Shelf"]) == {"category_group", "category", "subcategory", "items"}
    assert combos["Front Shelf"]["items"] == 1 and combos["Front Shelf"]["subcategory"] is None
    assert combos["Dairy"]["items"] == 0  # every automatic section is offered
    assert "Raja Foods" in opts["vendors"] and "House of Spices" in opts["vendors"]

    rules = editor.get("/api/categories/vendor-rules", params={"include_inactive": False}).json()
    assert FULL_RULE_KEYS | {"items"} <= set(rules["items"][0])
    reset = editor.delete("/api/categories/items/parle", params={"to_toast": False}).json()
    assert set(reset) == {"changed", "item"}
    bulk = editor.post("/api/categories/items/reset", json={"item_ids": ["parle"], "to_toast": True}).json()
    assert set(bulk) >= {"changed", "items"}
    hist = editor.get("/api/categories/history").json()["items"]
    assert FULL_RULE_KEYS <= set(hist[0])


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_set_equal_to_automatic_removes_item_rule(store, editor):
    editor.put("/api/categories/items/chai", json=body("Front Shelf"))
    r = editor.put("/api/categories/items/chai", json=body("tea & coffee")).json()
    assert r["changed"] == 1 and r["item"]["source"] == "toast" and r["item"]["category"] == "Tea & Coffee"
    assert store.execute(text("SELECT COUNT(*) FROM po_section_rules WHERE active = 1")).scalar() == 0
    # Same value again: nothing to do.
    assert editor.put("/api/categories/items/chai", json=body("Tea & Coffee")).json()["changed"] == 0
    # Build 5's group / subcategory are ignored.
    r = editor.put("/api/categories/items/chai", json={"category_group": "RETAIL", "category": "Front Shelf",
                                                     "subcategory": "Whatever"}).json()
    assert r["item"]["category"] == "Front Shelf" and r["item"]["subcategory"] is None


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_reset_semantics_mirror_the_old_ones(store, editor):
    editor.post("/api/categories/vendor-rules", json=dict(body("Raja Picks"), vendor="Raja Foods"))
    editor.put("/api/categories/items/parle", json=body("Front Shelf"))
    # to_toast=false: drop the item rule, the vendor rule applies again.
    item = editor.delete("/api/categories/items/parle", params={"to_toast": False}).json()["item"]
    assert item["source"] == "vendor" and item["category"] == "Raja Picks"
    # to_toast=true over a vendor rule: pinned to the automatic section.
    item = editor.delete("/api/categories/items/parle").json()["item"]
    assert item["source"] == "item" and item["category"] == item["toast_category"] == "Biscuits, Cookies & Rusk"
    # Again: no new rule.
    assert editor.delete("/api/categories/items/parle").json()["changed"] == 0
    # Setting it to the vendor rule's section removes the pin.
    item = editor.put("/api/categories/items/parle", json=body("Raja Picks")).json()["item"]
    assert item["source"] == "vendor"
    # Not covered by a vendor rule: reset just removes the item rule.
    editor.put("/api/categories/items/chai", json=body("Front Shelf"))
    item = editor.delete("/api/categories/items/chai").json()["item"]
    assert item["source"] == "toast"


def test_bulk_set_reset_and_move(store, editor):
    r = editor.post("/api/categories/items/bulk", json=dict(body("Promo"), item_ids=["parle", "chai"])).json()
    assert r["changed"] == 2 and {i["category"] for i in r["items"]} == {"Promo"}
    assert editor.post("/api/categories/items/bulk", json=dict(body("Promo"), item_ids=["nope"])).status_code == 404
    r = editor.post("/api/categories/items/reset", json={"item_ids": ["parle", "chai"], "to_toast": True}).json()
    assert r["changed"] == 2 and all(i["source"] == "toast" for i in r["items"])

    r = editor.post("/api/categories/items/move", json={"vendor": "house of spices", "section": "HOS"}).json()
    assert r["matched"] == 2 and r["changed"] == 2
    assert {i["item_id"] for i in items(editor, category="hos").values()} == {"jeera", "chai"}
    r = editor.post("/api/categories/items/move", json={"from_section": "HOS", "section": "Dairy"}).json()
    assert r["changed"] == 2
    assert editor.post("/api/categories/items/move", json={"section": "X"}).status_code == 400


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_filters(store, editor):
    assert set(items(editor, category="Other")) == {"samosa"}
    assert set(items(editor, section="other")) == {"samosa"}
    assert set(items(editor, vendor="house of spices")) == {"jeera", "chai"}
    assert set(items(editor, q="PARLE")) == {"parle"}
    editor.put("/api/categories/items/samosa", json=body("Kitchen"))
    assert set(items(editor, source="changed")) == {"samosa"}
    assert set(items(editor, source="item")) == {"samosa"}
    assert "samosa" not in items(editor, source="toast")
    assert editor.get("/api/categories/items", params={"source": "bogus"}).status_code == 400


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_validation_and_spelling(store, editor):
    assert editor.put("/api/categories/items/parle", json=body("x" * 81)).status_code == 400
    assert editor.put("/api/categories/items/parle", json=body("Bad\nName")).status_code == 400
    assert editor.put("/api/categories/items/parle", json=body("   ")).status_code == 400
    assert editor.put("/api/categories/items/parle", json={"category_group": "PO section"}).status_code == 400
    assert editor.put("/api/categories/items/nope", json=body("Dairy")).status_code == 404
    assert editor.post("/api/categories/vendor-rules", json=dict(body("X"), vendor="")).status_code == 400
    assert editor.post("/api/categories/vendor-rules",
                       json=dict(body("X"), vendor="Raja Foods", note="n" * 501)).status_code == 400
    # Case-insensitive match reuses the existing spelling (web may send `section`).
    item = editor.put("/api/categories/items/parle", json={"section": "  dairy "}).json()["item"]
    assert item["category"] == "Dairy"
    rule = editor.post("/api/categories/vendor-rules", json=dict(body("spices - whole"), vendor="raja foods")).json()
    assert rule["category"] == "Spices - Whole" and rule["match_value"] == "Raja Foods"
    assert len("x" * 80) == 80 and editor.put("/api/categories/items/chai", json=body("x" * 80)).status_code == 200


def test_vendor_rules_replace_and_turn_off(store, editor):
    first = editor.post("/api/categories/vendor-rules", json=dict(body("A"), vendor="Raja Foods")).json()
    assert first["items"] == 4  # parle, jeera, atta, kitkat
    second = editor.post("/api/categories/vendor-rules", json=dict(body("B"), vendor="RAJA FOODS")).json()
    listed = editor.get("/api/categories/vendor-rules").json()["items"]
    assert [r["id"] for r in listed] == [second["id"]]
    everything = editor.get("/api/categories/vendor-rules", params={"include_inactive": True}).json()["items"]
    assert {r["id"]: r["active"] for r in everything} == {first["id"]: False, second["id"]: True}
    off = editor.delete("/api/categories/vendor-rules/%d" % second["id"])
    assert off.status_code == 200 and off.json()["active"] is False and off.json()["items"] == 0
    assert editor.delete("/api/categories/vendor-rules/999").status_code == 404
    assert items(editor)["parle"]["source"] == "toast"


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_renames_merge_undo_and_cycles(store, editor):
    r = editor.post("/api/categories/renames", json={"from_section": "Tea & Coffee", "to_section": "Chai"})
    assert r.status_code == 200 and r.json()["merge"] is False
    sections = {s["section"]: s for s in editor.get("/api/categories/sections").json()["items"]}
    assert "Tea & Coffee" not in sections and sections["Chai"]["items"] == 1
    assert sections["Chai"]["renamed_from"][0]["from"] == "Tea & Coffee"
    assert list(sections)[-1] == OTHER

    # Merge: rename into an existing section (its spelling is reused).
    r = editor.post("/api/categories/renames", json={"from_section": "Chocolates & Candy",
                                                     "to_section": "biscuits, cookies & rusk"}).json()
    assert r["merge"] is True and r["to_section"] == "Biscuits, Cookies & Rusk"
    assert items(editor)["kitkat"]["category"] == "Biscuits, Cookies & Rusk"

    # Renaming back restores the old name instead of making a loop.
    editor.post("/api/categories/renames", json={"from_section": "Chai", "to_section": "Tea & Coffee"})
    assert items(editor)["chai"]["category"] == "Tea & Coffee"
    active = editor.get("/api/categories/renames").json()["items"]
    assert [(r["from_section"], r["to_section"]) for r in active] == [
        ("Chai", "Tea & Coffee"), ("Chocolates & Candy", "Biscuits, Cookies & Rusk")]

    # Undo a rename (turn it off).
    merge_id = [r["id"] for r in active if r["from_section"] == "Chocolates & Candy"][0]
    assert editor.delete("/api/categories/renames/%d" % merge_id).json()["active"] is False
    assert items(editor)["kitkat"]["category"] == "Chocolates & Candy"

    bad = [
        {"from_section": "Nope", "to_section": "X"},
        {"from_section": "Other", "to_section": "X"},
        {"from_section": "Dairy", "to_section": "other"},
        {"from_section": "Dairy", "to_section": "Dairy"},
        {"from_section": "Dairy", "to_section": "x" * 81},
    ]
    for b in bad:
        assert editor.post("/api/categories/renames", json=b).status_code == 400, b
    # A case-only rename is allowed.
    assert editor.post("/api/categories/renames", json={"from_section": "Dairy", "to_section": "DAIRY"}).status_code == 200


def test_history_and_undo_last_change(store, editor):
    editor.put("/api/categories/items/parle", json=body("Front Shelf"))
    editor.put("/api/categories/items/parle", json=body("Back Shelf"))
    hist = editor.get("/api/categories/history", params={"item_id": "parle"}).json()["items"]
    assert [(h["category"], h["active"]) for h in hist] == [("Back Shelf", True), ("Front Shelf", False)]
    assert hist[1]["updated_by"] == "meera" and hist[0]["created_by"] == "meera"
    assert hist[0]["item_name"] == "Parle-G Biscuits 800g"
    assert editor.get("/api/categories/history", params={"match_type": "bogus"}).status_code == 400

    changes = editor.get("/api/categories/changes").json()["items"]
    assert changes[0]["can_undo"] and not changes[1]["can_undo"]
    assert changes[0]["summary"] == "1 item to Back Shelf"
    # A stale undo (not the newest change) is refused.
    assert editor.post("/api/categories/changes/undo", json={"batch": changes[1]["batch"]}).status_code == 409
    r = editor.post("/api/categories/changes/undo", json={"batch": changes[0]["batch"]})
    assert r.status_code == 200
    assert items(editor)["parle"]["category"] == "Front Shelf"
    # Undo again = redo.
    editor.post("/api/categories/changes/undo", json={})
    assert items(editor)["parle"]["category"] == "Back Shelf"
    # Nothing is ever deleted.
    assert store.execute(text("SELECT COUNT(*) FROM po_section_rules")).scalar() == 4

    # Undo a bulk reset brings every rule back.
    editor.post("/api/categories/items/bulk", json=dict(body("Promo"), item_ids=["chai", "atta"]))
    editor.post("/api/categories/items/reset", json={"item_ids": ["chai", "atta", "parle"], "to_toast": True})
    assert {i["source"] for i in items(editor).values()} == {"toast"}
    editor.post("/api/categories/changes/undo", json={})
    rows = items(editor)
    assert (rows["chai"]["category"], rows["atta"]["category"], rows["parle"]["category"]) == \
        ("Promo", "Promo", "Back Shelf")


# --------------------------------------------------------------------------
# Cart + Supplier Projection (additive fields)
# --------------------------------------------------------------------------


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_cart_sections_and_preview(store, editor, admin_client):
    added = editor.post("/api/ordering/cart/items", json={"supplier": "Raja Foods", "items": [
        {"item_id": "parle", "name": "Parle-G Biscuits 800g", "qty": 1},
        {"name": "Hand Added Thing", "qty": 2},
    ]}).json()
    cart = editor.get("/api/ordering/cart").json()
    lines = {i["name"]: i for i in cart["suppliers"][0]["items"]}
    assert lines["Parle-G Biscuits 800g"]["section"] == "Biscuits, Cookies & Rusk"
    assert lines["Parle-G Biscuits 800g"]["section_source"] == "automatic"
    assert (lines["Hand Added Thing"]["section"], lines["Hand Added Thing"]["section_source"]) == (OTHER, "other")

    # From the cart: a catalog line gets a normal item rule.
    parle_line = [a for a in added if a["item_id"] == "parle"][0]
    r = editor.put("/api/categories/cart-lines/%d" % parle_line["id"], json={"section": "Cart Pick"}).json()
    assert r["section"] == "Cart Pick" and r["section_source"] == "item"
    assert items(editor)["parle"]["rule"]["match_type"] == "item_id"
    # Blank = back to automatic.
    r = editor.put("/api/categories/cart-lines/%d" % parle_line["id"], json={"section": None}).json()
    assert r["section"] == "Biscuits, Cookies & Rusk"
    hand = [a for a in added if a["item_id"] is None][0]
    editor.put("/api/categories/cart-lines/%d" % hand["id"], json={"section": "Dairy"})
    assert store.execute(text("SELECT section FROM po_cart_items WHERE id = :i"), {"i": hand["id"]}).scalar() == "Dairy"
    assert editor.put("/api/categories/cart-lines/999", json={"section": "X"}).status_code == 404

    preview = editor.get("/api/categories/cart-preview", params={"supplier": "Raja Foods"}).json()
    assert preview["suppliers"] == [{"supplier": "Raja Foods", "lines": 2}]
    assert [s["section"] for s in preview["sections"]] == ["Biscuits, Cookies & Rusk", "Dairy"]

    proj = admin_client.get("/api/ordering/supplier-projection", params={"supplier": "Raja Foods"}).json()
    assert {i["item_id"]: i["po_section"] for i in proj["items"]}["kitkat"] == "Chocolates & Candy"


# --------------------------------------------------------------------------
# Safety: snapshots and category_overrides untouched; migration
# --------------------------------------------------------------------------


def test_snapshots_and_category_overrides_untouched(store, editor):
    snaps = raw_rows(store)
    before = overrides(store)
    editor.put("/api/categories/items/parle", json=body("Front Shelf"))
    editor.post("/api/categories/items/bulk", json=dict(body("Promo"), item_ids=["rusk", "atta", "samosa"]))
    editor.post("/api/categories/vendor-rules", json=dict(body("Shaz Shelf"), vendor="Shaz International"))
    editor.post("/api/categories/renames", json={"from_section": "Promo", "to_section": "Promo 2"})
    editor.post("/api/categories/items/reset", json={"item_ids": ["parle", "rusk"], "to_toast": True})
    editor.post("/api/categories/items/move", json={"vendor": "Raja Foods", "section": "Raja"})
    editor.post("/api/categories/changes/undo", json={})
    store.expire_all()
    assert raw_rows(store) == snaps
    assert overrides(store) == before
    # The Shaz correction still applies to the Toast category everywhere.
    assert latest_inventory_by_item(store)["rusk"].category == "Snacks & Savories"


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_missing_table_falls_back_to_automatic(store):
    store.execute(text("DROP TABLE po_section_rules"))
    store.commit()
    store.info.clear()
    rules = load_section_rules(store)
    assert not rules
    assert rules.section_for_snap("chai", latest_inventory_by_item(store)["chai"]) == "Tea & Coffee"


def _alembic(db_file):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True)

    return run


def test_migration_0038_up_and_down(tmp_path):
    db_file = tmp_path / "m.db"
    run = _alembic(db_file)
    run("upgrade", "0037")
    con = sqlite3.connect(db_file)
    try:
        overrides_before = con.execute("SELECT * FROM category_overrides ORDER BY id").fetchall()
        assert overrides_before
    finally:
        con.close()
    run("upgrade", "0038")
    con = sqlite3.connect(db_file)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(po_section_rules)")}
        from app.db import Base

        assert cols == {c.name for c in Base.metadata.tables["po_section_rules"].columns}
        assert "section" in {row[1] for row in con.execute("PRAGMA table_info(po_cart_items)")}
        assert con.execute("SELECT * FROM category_overrides ORDER BY id").fetchall() == overrides_before
    finally:
        con.close()
    run("downgrade", "0037")
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "po_section_rules" not in tables
        assert "section" not in {row[1] for row in con.execute("PRAGMA table_info(po_cart_items)")}
        assert con.execute("SELECT * FROM category_overrides ORDER BY id").fetchall() == overrides_before
    finally:
        con.close()
    run("upgrade", "head")


# --------------------------------------------------------------------------
# Verifier fixes (Sep 30): cart headings = PDF, supplier context, undo window
# --------------------------------------------------------------------------


def test_cart_heading_matches_pdf_for_multi_vendor_item_and_name_matched_line(store, editor):
    # jeera is Raja Foods;House of Spices. Two vendor rules; HOS is newest.
    editor.post("/api/categories/vendor-rules", json=dict(body("Raja Aisle"), vendor="Raja Foods"))
    editor.post("/api/categories/vendor-rules", json=dict(body("HOS Aisle"), vendor="House of Spices"))
    added = editor.post("/api/ordering/cart/items", json={"supplier": "Raja Foods", "items": [
        {"item_id": "jeera", "name": "Deep Jeera 7oz", "supplier_item_id": "RJ-2", "qty": 1},
        # Hand-added, but its name is a catalog item: the PDF prints it as that item.
        {"name": "Aashirvaad Atta 20lb", "qty": 1},
    ]}).json()
    jeera_line = [a for a in added if a["item_id"] == "jeera"][0]
    lines = {i["name"]: i for i in editor.get("/api/ordering/cart").json()["suppliers"][0]["items"]}
    assert lines["Deep Jeera 7oz"]["section"] == "Raja Aisle"
    assert lines["Aashirvaad Atta 20lb"]["section"] == "Raja Aisle"  # atta is a Raja item -> vendor rule
    # On Raja's cart, moving jeera to "HOS Aisle" must really change Raja's PDF
    # (the page's view, newest vendor rule, already says HOS Aisle).
    r = editor.put("/api/categories/cart-lines/%d" % jeera_line["id"], json={"section": "HOS Aisle"}).json()
    assert r["section"] == "HOS Aisle" and r["section_source"] == "item"
    preview = editor.get("/api/categories/cart-preview", params={"supplier": "Raja Foods"}).json()
    by_name = {l["name"]: s["section"] for s in preview["sections"] for l in s["lines"]}
    lines = {i["name"]: i for i in editor.get("/api/ordering/cart").json()["suppliers"][0]["items"]}
    assert {n: l["section"] for n, l in lines.items()} == by_name


def test_undo_sees_deactivation_of_an_old_rule(store, editor, monkeypatch):
    from app.services import po_section_rules as svc

    rule = editor.post("/api/categories/vendor-rules", json=dict(body("Raja Aisle"), vendor="Raja Foods")).json()
    for n in range(12):
        editor.put("/api/categories/items/chai", json=body("Shelf %d" % n))
    # The vendor rule is now older than the newest-ids window.
    monkeypatch.setattr(svc, "_CHANGES_WINDOW", 5)
    editor.delete("/api/categories/vendor-rules/%d" % rule["id"])
    changes = editor.get("/api/categories/changes").json()["items"]
    assert changes[0]["summary"] == "Vendor rule off: Raja Foods" and changes[0]["can_undo"]
    editor.post("/api/categories/changes/undo", json={"batch": changes[0]["batch"]})
    rows = items(editor)
    assert rows["parle"]["source"] == "vendor" and rows["chai"]["category"] == "Shelf 11"


def test_section_names_collapse_inner_spaces(store, editor):
    editor.put("/api/categories/items/parle", json=body("Front  Shelf"))
    editor.put("/api/categories/items/chai", json=body("front shelf"))
    rows = items(editor)
    assert rows["parle"]["category"] == "Front Shelf" and rows["chai"]["category"] == "Front Shelf"


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_frozen_sweets_category_beats_roll_and_sandwich_keywords():
    from app.services.po_sections import section_for

    for name in ("Haldiram's Kaju Roll 340g", "JF Mango Sandwich 11oz", "Laxmi Kaju Roll"):
        assert (
            section_for("RETAIL", "Frozen Foods", "Frozen Sweets & Mithai", name)
            == "Frozen - Sweets & Desserts"
        )
    # Other frozen subcategories still go name-first (Toast misfiles them).
    assert section_for("RETAIL", "Frozen Foods", "Frozen Vegetables", "Deep Spring Roll") == "Frozen - Breads & Snacks"
