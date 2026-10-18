"""Category corrections (category_overrides) after the Sep 30 change: the
Product categories page now edits PO sections (tests/test_ops_po_sections_edit.py),
so corrections are only changed through the admin API - they still apply
everywhere and still record who / when. inventory_snapshots keeps Toast's
values whatever is edited."""

import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import text

from app.features import FEATURE_KEYS, FEATURES
from app.services.fix_issues import get_issue_list
from app.services.supplier_projection import compute_supplier_projection
from tests.conftest import client_for, make_user
from tests.test_ops_category_overrides import add_rule, add_snap, raw_rows

BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def store(db):
    add_snap(db, "rusk", "KCB Plain Tea Rusk 7oz", "Shaz International", "Pantry Staples", "Roti, Naan, Wraps")
    add_snap(db, "toast", "KCB Milky Toast 7oz", "Radhey Foods;Shaz International", "Pantry Staples",
             "Roti, Naan, Wraps")
    add_snap(db, "parle", "Parle-G Biscuits 800g", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies",
             barcode="8901719101038")
    add_snap(db, "atta", "Aashirvaad Atta 20lb", "Raja Foods", "Flours & Grains", "Atta", barcode=None)
    add_snap(db, "samosa", "Veg Samosa", None, "Snacks", None, group="STREET KITCHEN", price=None)
    db.commit()
    add_rule(db, "supplier", "Shaz International")
    return db


@pytest.fixture
def editor(db):
    return client_for(db, make_user(db, "meera", features=["category_edits"]))


def test_feature_key_listed_and_not_auto_granted(admin_client, staff):
    assert "category_edits" in FEATURE_KEYS
    assert {"key": "category_edits", "label": "Edit PO sections"} in FEATURES
    catalog = admin_client.get("/api/auth/features").json()["features"]
    assert "category_edits" in [f["key"] for f in catalog]
    assert "category_edits" not in (staff.allowed_features or "")


def test_admin_api_records_who(store, admin_client):
    r = admin_client.post("/api/admin/category-overrides", json={
        "match_type": "item_id", "match_value": "atta", "category_group": "RETAIL", "category": "Bakery"})
    assert r.json()["created_by"] == "boss"
    r = admin_client.patch(f"/api/admin/category-overrides/{r.json()['id']}", json={"active": False})
    assert r.json()["updated_by"] == "boss" and r.json()["updated_at"]


def test_correction_shows_in_supplier_projection_and_fix_issues(store, admin_client):
    for item_id, cat, sub in (("atta", "Bakery", "Rusk"), ("parle", "Tea, Coffee & Hot Beverages", "Rusk")):
        assert admin_client.post("/api/admin/category-overrides", json={
            "match_type": "item_id", "match_value": item_id, "category_group": "RETAIL", "category": cat,
            "subcategory": sub}).status_code == 200
    rows = {r["item_id"]: r for r in compute_supplier_projection(store, "Raja Foods", 14, [14])}
    assert rows["atta"]["category"] == "Bakery"
    assert rows["parle"]["category"] == "Tea, Coffee & Hot Beverages"
    body = admin_client.get("/api/ordering/supplier-projection", params={"supplier": "Raja Foods"}).json()
    listed = {i["item_id"]: i for i in body["items"]}
    assert listed["atta"]["category"] == "Bakery"
    # The corrected category feeds the automatic PO section.
    assert listed["parle"]["po_section"] == "Tea & Coffee"

    # atta has no barcode -> Fix Issues missing-barcode list.
    store.expire_all()
    missing = {r["item_id"]: r for r in get_issue_list(store, "missing_barcode")["open"]}
    product = missing["atta"].get("product") or missing["atta"]
    assert (product["category_group"], product["category"], product["subcategory"]) == ("RETAIL", "Bakery", "Rusk")


def test_admin_api_clears_caches(store, admin_client, monkeypatch):
    import app.routers.category_overrides as router

    calls = []
    monkeypatch.setattr(router, "clear_forecast_cache", lambda: calls.append("forecast"))
    monkeypatch.setattr(router, "clear_similar_cache", lambda: calls.append("similar"))
    admin_client.post("/api/admin/category-overrides", json={
        "match_type": "item_id", "match_value": "atta", "category_group": "RETAIL", "category": "Bakery"})
    assert calls == ["forecast", "similar"]


def test_snapshots_never_change(store, editor):
    before = raw_rows(store)
    body = {"category_group": "PO section", "category": "Bakery", "subcategory": ""}
    editor.put("/api/categories/items/parle", json=body)
    editor.post("/api/categories/items/bulk", json=dict(body, item_ids=["atta", "rusk", "samosa"]))
    editor.delete("/api/categories/items/rusk")
    editor.post("/api/categories/vendor-rules", json=dict(body, vendor="Raja Foods"))
    rules = editor.get("/api/categories/vendor-rules").json()["items"]
    editor.delete(f"/api/categories/vendor-rules/{rules[0]['id']}")
    editor.post("/api/categories/items/reset", json={"item_ids": ["parle", "atta", "samosa"]})
    editor.get("/api/categories/items")
    editor.get("/api/categories/options")
    store.expire_all()
    assert raw_rows(store) == before
    assert store.execute(text("SELECT COUNT(*) FROM inventory_snapshots")).scalar() == len(before)


# --------------------------------------------------------------------------
# Migration 0036
# --------------------------------------------------------------------------


def test_migration_0036_adds_audit_columns(tmp_path):
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True)

    run("upgrade", "0036")

    def cols():
        con = sqlite3.connect(db_file)
        try:
            return {row[1] for row in con.execute("PRAGMA table_info(category_overrides)")}
        finally:
            con.close()

    assert {"created_by", "updated_at", "updated_by"} <= cols()
    con = sqlite3.connect(db_file)
    try:
        # The seeded Shaz rule survives, with no who (made before 0036).
        assert con.execute("SELECT match_value, created_by FROM category_overrides").fetchall() == [
            ("Shaz International", None)]
    finally:
        con.close()
    down = subprocess.run([str(alembic), "show", "0036"], cwd=BACKEND, env=env, check=True, capture_output=True,
                          text=True).stdout
    parent = [line for line in down.splitlines() if line.startswith("Parent:")][0].split()[-1]
    run("downgrade", parent)
    assert not {"created_by", "updated_at", "updated_by"} & cols()
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 1
    finally:
        con.close()
    run("upgrade", "head")
