"""App-side category overrides (ops fixes F, Sep 29): every Shaz International
item shows as RETAIL / Snacks & Savories / Biscuits, Cookies & Rusk everywhere,
while inventory_snapshots keeps Toast's real values."""

import datetime as dt
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import UnmappedInstanceError

from app.db import Base
from app.models import CategoryOverride, InventorySnapshot
from app.services import category_overrides as co
from app.services.category_overrides import OverriddenSnapshot, apply_overrides, toast_categories
from app.services.fix_issues import get_issue_list
from app.services.po_sections import group_po_items, section_for
from app.services.price_history import get_price_changed_items
from app.services.reorder import latest_inventory_by_item
from app.services.supplier_projection import compute_supplier_projection
from app.timeutil import utcnow

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TODAY = dt.date.today()
YESTERDAY = TODAY - dt.timedelta(days=1)
TARGET = ("RETAIL", "Snacks & Savories", "Biscuits, Cookies & Rusk")

# Real Shaz items and where Toast has them (dev DB, Sep 2026).
SHAZ_ITEMS = [
    ("shaz-rusk", "KCB Plain Tea Rusk 7oz", "Shaz International", "Snacks & Savories", "Biscuits & Cookies"),
    ("shaz-toast", "KCB Milky Toast 7oz", "Shaz International", "Pantry Staples", "Roti, Naan, Wraps"),
    ("shaz-sonfi", "KCB Sonfi Cake Rusk 20oz", "Shaz International", "Baking & Cooking Essentials", "Baking Essentials"),
    ("shaz-multi", "KCB Sweet Bakar Khani 283g", "Radhey Foods;Shaz International", "Snacks & Savories", "Sweets & Mithai"),
]


def add_snap(db, item_id, name, supplier, category, subcategory, day=TODAY, group="RETAIL", **extra):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=category,
            subcategory=subcategory,
            inventory_quantity=extra.pop("qty", 5.0),
            price=extra.pop("price", 3.99),
            cost=2.0,
            supplier=supplier,
            supplier_item_id=extra.pop("supplier_item_id", None),
            barcode=extra.pop("barcode", "012345678905"),
            **extra,
        )
    )


def add_rule(db, match_type, value, group="RETAIL", category="Snacks & Savories", sub="Biscuits, Cookies & Rusk",
             active=True):
    rule = CategoryOverride(
        match_type=match_type, match_value=value, category_group=group, category=category, subcategory=sub,
        active=active, created_at=utcnow(),
    )
    db.add(rule)
    db.commit()
    return rule


@pytest.fixture
def store(db):
    for item_id, name, supplier, category, sub in SHAZ_ITEMS:
        add_snap(db, item_id, name, supplier, category, sub, day=YESTERDAY, price=3.49)
        add_snap(db, item_id, name, supplier, category, sub, supplier_item_id=f"SZ-{item_id}")
    add_snap(db, "other", "Parle-G Biscuits 800g", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies")
    add_snap(db, "samosa", "Veg Samosa", None, "Snacks", None, group="MARKET KITCHEN", price=None)
    db.commit()
    add_rule(db, "supplier", "Shaz International")
    return db


def raw_rows(db):
    """inventory_snapshots exactly as stored (plain SQL, no ORM)."""
    return db.execute(
        text("SELECT id, item_id, snapshot_date, category_group, category, subcategory FROM inventory_snapshots ORDER BY id")
    ).fetchall()


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_supplier_rule_covers_every_shaz_item_incl_multi_vendor(store):
    latest = latest_inventory_by_item(store)
    for item_id, *_ in SHAZ_ITEMS:
        snap = latest[item_id]
        assert isinstance(snap, OverriddenSnapshot), item_id
        assert (snap.category_group, snap.category, snap.subcategory) == TARGET
    # Everything else reads through unchanged.
    assert latest["shaz-toast"].name == "KCB Milky Toast 7oz"
    assert latest["shaz-toast"].supplier_item_id == "SZ-shaz-toast"
    assert latest["shaz-toast"].snapshot_date == TODAY
    assert toast_categories(latest["shaz-toast"]) == ("RETAIL", "Pantry Staples", "Roti, Naan, Wraps")
    assert latest["shaz-toast"].toast_category == "Pantry Staples"
    # Non-Shaz items are the plain ORM rows, untouched.
    assert isinstance(latest["other"], InventorySnapshot)
    assert latest["other"].subcategory == "Biscuits & Cookies"
    assert latest["samosa"].category_group == "MARKET KITCHEN"
    # Raw values on request.
    raw = latest_inventory_by_item(store, apply_category_overrides=False)
    assert raw["shaz-toast"].category == "Pantry Staples"


def test_supplier_match_is_case_insensitive_and_trimmed(db):
    add_snap(db, "a", "A", " radhey foods ; SHAZ INTERNATIONAL ", "Pantry Staples", "Roti, Naan, Wraps")
    add_snap(db, "b", "B", "Shaz International Foods", "Pantry Staples", "Roti, Naan, Wraps")  # different vendor
    db.commit()
    add_rule(db, "supplier", "  shaz international ")
    latest = latest_inventory_by_item(db)
    assert latest["a"].category == "Snacks & Savories"
    assert latest["b"].category == "Pantry Staples"


def test_item_id_rule_wins_over_supplier_rule(store):
    add_rule(store, "item_id", "shaz-multi", category="Snacks & Savories", sub="Sweets & Mithai")
    co.invalidate(store)
    latest = latest_inventory_by_item(store)
    assert latest["shaz-multi"].subcategory == "Sweets & Mithai"
    assert latest["shaz-rusk"].subcategory == "Biscuits, Cookies & Rusk"


def test_null_subcategory_keeps_the_items_own(db):
    add_snap(db, "x", "X", "Acme", "Pantry Staples", "Roti, Naan, Wraps")
    db.commit()
    add_rule(db, "item_id", "x", group="RETAIL", category="Bakery", sub=None)
    snap = latest_inventory_by_item(db)["x"]
    assert (snap.category, snap.subcategory) == ("Bakery", "Roti, Naan, Wraps")


def test_inactive_rule_is_ignored(db):
    add_snap(db, "shaz-rusk", "KCB Plain Tea Rusk 7oz", "Shaz International", "Snacks & Savories", "Biscuits & Cookies")
    db.commit()
    add_rule(db, "supplier", "Shaz International", active=False)
    latest = latest_inventory_by_item(db)
    assert isinstance(latest["shaz-rusk"], InventorySnapshot)
    assert latest["shaz-rusk"].subcategory == "Biscuits & Cookies"


def test_no_rules_returns_the_same_dict(db):
    add_snap(db, "x", "X", "Acme", "Pantry Staples", None)
    db.commit()
    raw = latest_inventory_by_item(db, apply_category_overrides=False)
    assert apply_overrides(db, raw) is raw


def test_rules_are_cached_per_session(store, monkeypatch):
    latest_inventory_by_item(store)
    calls = []
    monkeypatch.setattr(co, "Rules", lambda rows: calls.append(rows) or pytest.fail("rules reloaded"))
    latest_inventory_by_item(store)
    assert calls == []


# --------------------------------------------------------------------------
# Safety: inventory_snapshots never changes
# --------------------------------------------------------------------------


def test_wrapper_is_read_only(store):
    snap = latest_inventory_by_item(store)["shaz-rusk"]
    with pytest.raises(AttributeError):
        snap.category = "Oops"
    with pytest.raises(AttributeError):
        snap.inventory_quantity = 0
    with pytest.raises(UnmappedInstanceError):
        store.add(snap)  # not a mapped object - can never be flushed


def test_db_rows_unchanged_after_full_request_cycle(store, admin_client):
    before = raw_rows(store)
    assert ("RETAIL", "Pantry Staples", "Roti, Naan, Wraps") in [tuple(r[3:]) for r in before]

    r = admin_client.get("/api/ordering/supplier-projection", params={"supplier": "Shaz International"})
    assert r.status_code == 200, r.text
    r = admin_client.get("/api/ordering/reorder-candidates", params={"lookback_days": 14, "lead_time_days": 7})
    assert r.status_code == 200, r.text
    for issue_type in ("missing_barcode", "negative_inventory", "slow_review", "dead_with_stock"):
        assert admin_client.get(f"/api/issues/{issue_type}").status_code == 200
        assert admin_client.get(f"/api/issues/{issue_type}/export").status_code == 200
    assert admin_client.get("/api/issues/summary").status_code == 200
    assert admin_client.get("/api/inventory/items/search", params={"q": "KCB"}).status_code == 200
    assert admin_client.get("/api/inventory/price-changes").status_code == 200
    for report in ("dead-stock", "negative-inventory", "missing-barcodes", "duplicate-products",
                   "vendor-price-comparison"):
        assert admin_client.get(f"/api/reports/{report}").status_code == 200
        assert admin_client.get(f"/api/reports/{report}/export").status_code == 200
    # The PO export also commits (it logs the PDF) - a flush happens with
    # overridden items loaded in the same session.
    r = admin_client.post(
        "/api/ordering/purchase-order/export-pdf",
        json={"supplier": "Shaz International",
              "items": [{"name": "KCB Plain Tea Rusk 7oz", "supplier_item_id": "SZ-shaz-rusk", "qty": 2}]},
    )
    assert r.status_code == 200, r.text
    # And a session that reads overridden items, then commits other work.
    latest = latest_inventory_by_item(store)
    assert latest["shaz-toast"].category == "Snacks & Savories"
    add_rule(store, "item_id", "other", category="Snacks & Savories")
    store.commit()

    store.expire_all()
    assert raw_rows(store) == before


# --------------------------------------------------------------------------
# Reports / lists / PO sections see the new category
# --------------------------------------------------------------------------


def test_supplier_projection_shows_new_category(store, admin_client):
    rows = compute_supplier_projection(store, "Shaz International", 14, [14])
    by_id = {r["item_id"]: r for r in rows}
    assert set(by_id) == {i[0] for i in SHAZ_ITEMS}
    assert {r["category"] for r in rows} == {"Snacks & Savories"}
    body = admin_client.get("/api/ordering/supplier-projection", params={"supplier": "Shaz International"}).json()
    items = body["items"] if isinstance(body, dict) else body
    assert {i["category"] for i in items} == {"Snacks & Savories"}


def test_fix_issues_shows_new_category(db):
    add_snap(db, "shaz-toast", "KCB Milky Toast 7oz", "Shaz International", "Pantry Staples", "Roti, Naan, Wraps",
             barcode=None)
    add_snap(db, "shaz-neg", "KCB Morning Toast 7oz", "Shaz International", "Pantry Staples", "Roti, Naan, Wraps",
             qty=-3)
    db.commit()
    add_rule(db, "supplier", "Shaz International")
    missing = {r["item_id"]: r for r in get_issue_list(db, "missing_barcode")["open"]}
    row = missing["shaz-toast"]
    product = row.get("product") or row
    assert (product["category_group"], product["category"], product["subcategory"]) == TARGET
    negative = {r["item_id"]: r for r in get_issue_list(db, "negative_inventory")["open"]}
    assert negative["shaz-neg"]["category"] == "Snacks & Savories"


def test_price_history_shows_new_category(store):
    rows = {r["item_id"]: r for r in get_price_changed_items(store)}
    assert rows["shaz-toast"]["category"] == "Snacks & Savories"


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_po_sections_biscuits_cookies_rusk(store):
    assert section_for(*TARGET, "KCB Plain Tea Rusk 7oz") == "Biscuits, Cookies & Rusk"
    # Toast's own Biscuits & Cookies subcategory prints in the same section.
    assert section_for("RETAIL", "Snacks & Savories", "Biscuits & Cookies", "Parle-G Biscuits 800g") == \
        "Biscuits, Cookies & Rusk"
    # Before the override Toast would have put the tea toast with the rotis.
    assert section_for("RETAIL", "Pantry Staples", "Roti, Naan, Wraps", "KCB Milky Toast 7oz") == "Rotis, Naan & Wraps"

    items = [
        {"name": "KCB Plain Tea Rusk 7oz", "supplier_item_id": "SZ-shaz-rusk", "qty": 2},
        {"name": "KCB Milky Toast 7oz", "supplier_item_id": "SZ-shaz-toast", "qty": 3},
        {"name": "KCB Sonfi Cake Rusk 20oz", "supplier_item_id": "", "qty": 1},
        {"name": "KCB Sweet Bakar Khani 283g", "supplier_item_id": "SZ-shaz-multi", "qty": 1},
    ]
    sections = group_po_items(items, latest_inventory_by_item(store), "Shaz International")
    assert [s for s, _ in sections] == ["Biscuits, Cookies & Rusk"]
    assert [i["name"] for i in sections[0][1]] == sorted(i["name"] for i in items)


# --------------------------------------------------------------------------
# Admin API
# --------------------------------------------------------------------------


def test_admin_api_add_list_deactivate(db, admin_client, staff_client):
    add_snap(db, "x", "KCB Veg Cake Rusk 10oz", "Shaz International", "Baking & Cooking Essentials",
             "Baking Essentials")
    db.commit()
    assert staff_client.get("/api/admin/category-overrides").status_code == 403

    bad = admin_client.post("/api/admin/category-overrides", json={
        "match_type": "vendor", "match_value": "x", "category_group": "RETAIL", "category": "C"})
    assert bad.status_code == 400
    r = admin_client.post("/api/admin/category-overrides", json={
        "match_type": "supplier", "match_value": " Shaz International ", "category_group": "RETAIL",
        "category": "Snacks & Savories", "subcategory": "Biscuits, Cookies & Rusk", "note": "owner"})
    assert r.status_code == 200, r.text
    rule = r.json()
    assert rule["match_value"] == "Shaz International" and rule["active"] is True
    assert [i["id"] for i in admin_client.get("/api/admin/category-overrides").json()["items"]] == [rule["id"]]
    assert admin_client.get("/api/inventory/items/search", params={"q": "veg cake"}).json()["items"][0][
        "category"] == "Snacks & Savories"

    r = admin_client.patch(f"/api/admin/category-overrides/{rule['id']}", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert admin_client.get("/api/inventory/items/search", params={"q": "veg cake"}).json()["items"][0][
        "category"] == "Baking & Cooking Essentials"
    assert admin_client.patch("/api/admin/category-overrides/999", json={"active": False}).status_code == 404
    assert admin_client.patch(f"/api/admin/category-overrides/{rule['id']}", json={"category": " "}).status_code == 400


# --------------------------------------------------------------------------
# Migration + fix-list script
# --------------------------------------------------------------------------


def _alembic(db_file):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return lambda *args: subprocess.run(  # noqa: E731
        [str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True
    )


def test_migration_0034_upgrade_seed_downgrade(tmp_path):
    db_file = tmp_path / "m.db"
    run = _alembic(db_file)
    run("upgrade", "0034")
    con = sqlite3.connect(db_file)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(category_overrides)")}
        # 0036 adds created_by / updated_at / updated_by (tests/test_ops_category_edits.py).
        assert cols == {c.name for c in Base.metadata.tables["category_overrides"].columns} - {
            "created_by", "updated_at", "updated_by"}
        seeded = con.execute(
            "SELECT match_type, match_value, category_group, category, subcategory, active FROM category_overrides"
        ).fetchall()
        assert seeded == [("supplier", "Sample Biscuit Supplier", *TARGET, 1)]
    finally:
        con.close()
    run("downgrade", "0033")
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 'category_overrides'").fetchone() is None
    finally:
        con.close()
    run("upgrade", "head")


def test_fix_list_script_reads_only(tmp_path):
    db_file = tmp_path / "s.db"
    _alembic(db_file)("upgrade", "head")
    con = sqlite3.connect(db_file)
    for item_id, name, supplier, category, sub in SHAZ_ITEMS:
        con.execute(
            "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, category_group, category, subcategory, "
            "supplier, uploaded_at) VALUES (?, ?, ?, 'RETAIL', ?, ?, ?, '2026-09-22')",
            (TODAY.isoformat(), item_id, name, category, sub, supplier),
        )
    con.execute(
        "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, category_group, category, subcategory, "
        "supplier, uploaded_at) VALUES (?, 'done', 'KCB Already Right', 'RETAIL', 'Snacks & Savories', "
        "'Biscuits, Cookies & Rusk', 'Shaz International', '2026-09-22')",
        (TODAY.isoformat(),),
    )
    # The seeded rule is the example supplier's; these items need their own.
    con.execute(
        "INSERT INTO category_overrides (match_type, match_value, category_group, category, subcategory, "
        "created_at, active) VALUES ('supplier', 'Shaz International', 'RETAIL', 'Snacks & Savories', "
        "'Biscuits, Cookies & Rusk', '2026-09-22', 1)"
    )
    con.commit()
    con.close()
    before = db_file.read_bytes()

    script = BACKEND / "scripts" / "category_fix_list.py"
    out = subprocess.run([sys.executable, str(script), str(db_file)], check=True, capture_output=True, text=True)
    lines = out.stdout.strip().splitlines()
    assert lines[0].startswith("item_name,supplier,current_category_group")
    assert len(lines) == 1 + len(SHAZ_ITEMS)
    assert "KCB Milky Toast 7oz,Shaz International,RETAIL,Pantry Staples" in out.stdout
    assert "KCB Already Right" not in out.stdout
    out_all = subprocess.run([sys.executable, str(script), str(db_file), "--all"], check=True, capture_output=True,
                             text=True)
    assert "KCB Already Right" in out_all.stdout
    assert db_file.read_bytes() == before
