"""Category mapping rules (Sep 30): a "category" rule in category_overrides
moves every item Toast files under one category (Toast's raw values) to
another - now and in future uploads. Precedence: item rule > vendor rule >
category rule > Toast. inventory_snapshots keeps Toast's values."""

import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import text

from app.models import CategoryOverride
from app.routers import category_overrides as router_module
from app.services import category_overrides as co
from app.services.category_overrides import (
    OverriddenSnapshot,
    apply_overrides,
    category_match_label,
    decode_category_match,
    encode_category_match,
)
from app.services.price_history import _category_by_item
from app.services.reorder import latest_inventory_by_item
from app.timeutil import utcnow
from tests.conftest import client_for, make_user
from tests.test_ops_category_overrides import TODAY, YESTERDAY, add_snap, raw_rows

BACKEND = pathlib.Path(__file__).resolve().parents[1]
BCR = ("RETAIL", "Snacks & Savories", "Biscuits, Cookies & Rusk")


def add_cat_rule(db, match, target, active=True, created_by=None):
    rule = CategoryOverride(
        match_type="category",
        match_value=encode_category_match(*match),
        category_group=target[0],
        category=target[1],
        subcategory=target[2],
        active=active,
        created_at=utcnow(),
        created_by=created_by,
    )
    db.add(rule)
    db.commit()
    return rule


def add_rule(db, match_type, value, target=BCR):
    rule = CategoryOverride(
        match_type=match_type, match_value=value, category_group=target[0], category=target[1],
        subcategory=target[2], active=True, created_at=utcnow(),
    )
    db.add(rule)
    db.commit()
    return rule


def cats(snap):
    return (snap.category_group, snap.category, snap.subcategory)


@pytest.fixture
def store(db):
    # Toast's raw categories, as in the Sep 29 upload.
    add_snap(db, "parle", "Parle-G Biscuits 800g", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies")
    add_snap(db, "oreo", "Oreo 14oz", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies")
    add_snap(db, "kitkat", "Kit Kat 4 Finger", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies")
    add_snap(db, "rusk", "KCB Plain Tea Rusk 7oz", "Shaz International", "Snacks & Savories", "Biscuits & Cookies")
    add_snap(db, "chips", "Lays Magic Masala", "Raja Foods", "Snacks & Savories", "Chips")
    add_snap(db, "mint", "Fresh Mint", "Farm Co", "Produce", "Fresh Herbs")
    add_snap(db, "okra", "Okra", "Farm Co", "Produce", "Vegetables")
    add_snap(db, "chandan", "Chandan Powder", "Pooja Co", "Powders & Rituals", "Chandan")
    add_snap(db, "sindoor", "Sindoor 50g", "Pooja Co", "Powders & Rituals", "Sindoor")
    add_snap(db, "tulsi", "Tulsi Plant", "Nursery", "Plants", None, group="PRODUCE")
    add_snap(db, "kit", "Puja Kit", "Pooja Co", "Spiritual & Cultural Products", None)
    add_snap(db, "roli", "Roli", "Pooja Co", "Spiritual & Cultural Products", "Powders & Rituals")
    add_snap(db, "atta", "Aashirvaad Atta 20lb", "Raja Foods", "Flours & Grains", "Atta")
    db.commit()
    return db


def latest(db):
    co.invalidate(db)
    return latest_inventory_by_item(db)


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------


def test_match_value_encoding_round_trip():
    exact = encode_category_match(" RETAIL ", "Snacks & Savories", "Biscuits & Cookies")
    assert json.loads(exact) == ["RETAIL", "Snacks & Savories", "Biscuits & Cookies"]
    assert decode_category_match(exact) == ("RETAIL", "Snacks & Savories", "Biscuits & Cookies")
    assert decode_category_match(encode_category_match("PRODUCE", "Plants", None)) == ("PRODUCE", "Plants", None)
    assert decode_category_match(encode_category_match("PRODUCE", "Plants", "")) == ("PRODUCE", "Plants", "")
    assert category_match_label(encode_category_match("PRODUCE", "Plants", None)) == "PRODUCE / Plants / any subcategory"
    assert category_match_label(encode_category_match("PRODUCE", "Plants", "")) == "PRODUCE / Plants / no subcategory"
    for bad in ("", "Shaz", "[]", '["A", "B"]', '["A", "", null]', '[1, "B", null]', '["A", "B", 3]', "{}"):
        assert decode_category_match(bad) is None, bad


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_exact_rule_maps_toast_category(store):
    add_cat_rule(store, ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), BCR)
    items = latest(store)
    for item_id in ("parle", "oreo", "kitkat"):
        assert isinstance(items[item_id], OverriddenSnapshot)
        assert cats(items[item_id]) == BCR
        assert items[item_id].toast_subcategory == "Biscuits & Cookies"
    assert cats(items["chips"]) == ("RETAIL", "Snacks & Savories", "Chips")
    assert not isinstance(items["atta"], OverriddenSnapshot)


def test_match_is_case_insensitive_and_trimmed(store):
    add_cat_rule(store, (" retail", "SNACKS & SAVORIES ", "biscuits & cookies"), BCR)
    assert cats(latest(store)["parle"]) == BCR


def test_leading_space_toast_name_still_matches(db):
    # e.g. " Men's Grooming" - uploads trim names, and rules compare trimmed.
    add_snap(db, "razor", "Razor", "X", " Men's Grooming", "Shaving Creams & Razors")
    db.commit()
    add_cat_rule(db, ("RETAIL", "Men's Grooming", None), ("RETAIL", "Men's Grooming", None))
    assert cats(latest(db)["razor"]) == ("RETAIL", "Men's Grooming", "Shaving Creams & Razors")


def test_any_subcategory_wildcard(store):
    add_cat_rule(store, ("RETAIL", "Powders & Rituals", None),
                 ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor"))
    items = latest(store)
    assert cats(items["chandan"]) == ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor")
    assert cats(items["sindoor"]) == ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor")


def test_no_subcategory_matches_only_items_without_one(store):
    add_cat_rule(store, ("RETAIL", "Spiritual & Cultural Products", ""), ("RETAIL", "Pooja Essentials", "Pooja Essentials"))
    items = latest(store)
    assert cats(items["kit"]) == ("RETAIL", "Pooja Essentials", "Pooja Essentials")
    assert not isinstance(items["roli"], OverriddenSnapshot)


def test_exact_rule_wins_over_wildcard_and_newest_wins(store):
    add_cat_rule(store, ("RETAIL", "Spiritual & Cultural Products", "Powders & Rituals"),
                 ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor"))
    add_cat_rule(store, ("RETAIL", "Spiritual & Cultural Products", None), ("RETAIL", "Pooja Essentials", "Old"))
    add_cat_rule(store, ("RETAIL", "Spiritual & Cultural Products", None),
                 ("RETAIL", "Pooja Essentials", "Pooja Essentials"))
    items = latest(store)
    assert cats(items["roli"]) == ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor")
    assert cats(items["kit"]) == ("RETAIL", "Pooja Essentials", "Pooja Essentials")


def test_target_subcategory_set_none_or_keep(store):
    add_cat_rule(store, ("RETAIL", "Produce", "Vegetables"), ("PRODUCE", "Vegetables", ""))
    add_cat_rule(store, ("RETAIL", "Produce", "Fresh Herbs"), ("PRODUCE", "Fresh Herbs", None))
    add_cat_rule(store, ("PRODUCE", "Plants", None), ("RETAIL", "Plants & Seeds", "Plants"))
    items = latest(store)
    assert cats(items["okra"]) == ("PRODUCE", "Vegetables", "")  # no subcategory
    assert cats(items["mint"]) == ("PRODUCE", "Fresh Herbs", "Fresh Herbs")  # keeps Toast's
    assert cats(items["tulsi"]) == ("RETAIL", "Plants & Seeds", "Plants")


def test_inactive_and_invalid_rules_are_ignored(store):
    add_cat_rule(store, ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), BCR, active=False)
    store.add(CategoryOverride(match_type="category", match_value="not json", category_group="X", category="Y",
                               active=True, created_at=utcnow()))
    store.commit()
    assert not isinstance(latest(store)["parle"], OverriddenSnapshot)


def test_precedence_item_over_vendor_over_category(store):
    add_cat_rule(store, ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), ("RETAIL", "Mapped", "Cat"))
    add_rule(store, "supplier", "Shaz International", ("RETAIL", "Vendor", "V"))
    add_rule(store, "item_id", "kitkat", ("RETAIL", "Snacks & Savories", "Chocolates & Candy"))
    items = latest(store)
    assert cats(items["parle"]) == ("RETAIL", "Mapped", "Cat")  # category rule
    assert cats(items["rusk"]) == ("RETAIL", "Vendor", "V")  # vendor rule wins
    assert cats(items["kitkat"]) == ("RETAIL", "Snacks & Savories", "Chocolates & Candy")  # item rule wins
    assert cats(items["chips"]) == ("RETAIL", "Snacks & Savories", "Chips")  # Toast
    rules = co.load_rules(store)
    # Without Toast's categories only item / vendor rules are looked at.
    assert rules.rule_for("parle", "Raja Foods") is None


def test_matches_toast_raw_values_not_corrected_ones(store):
    # A -> B and B -> C: an item Toast has in A lands in B, never chained on to C.
    add_cat_rule(store, ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), BCR)
    add_cat_rule(store, BCR, ("RETAIL", "Elsewhere", None))
    # An item rule putting chips into Biscuits & Cookies isn't then mapped either.
    add_rule(store, "item_id", "chips", ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"))
    items = latest(store)
    assert cats(items["parle"]) == BCR
    assert cats(items["chips"]) == ("RETAIL", "Snacks & Savories", "Biscuits & Cookies")
    # Applying again to already-wrapped rows changes nothing.
    again = apply_overrides(store, items)
    assert again["parle"] is items["parle"]


def test_snapshot_rows_untouched(store, admin_client):
    before = raw_rows(store)
    add_cat_rule(store, ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), BCR)
    items = latest(store)
    with pytest.raises(AttributeError):
        items["parle"].category = "Hacked"
    assert admin_client.get("/api/categories/items").status_code == 200
    assert admin_client.post("/api/admin/category-overrides", json={
        "match_type": "category", "match_value": encode_category_match("RETAIL", "Produce", None),
        "category_group": "PRODUCE", "category": "Vegetables", "subcategory": "",
    }).status_code == 200
    store.commit()
    store.expire_all()
    assert raw_rows(store) == before


def test_no_rules_still_returns_same_dict(store):
    raw = latest_inventory_by_item(store, apply_category_overrides=False)
    assert apply_overrides(store, raw) is raw


def test_price_history_uses_category_rules(store):
    add_snap(store, "parle", "Parle-G Biscuits 800g", "Raja Foods", "Snacks & Savories", "Biscuits & Cookies",
             day=YESTERDAY, price=2.0)
    store.commit()
    add_cat_rule(store, ("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), ("RETAIL", "Biscuits", None))
    co.invalidate(store)
    assert _category_by_item(store)["parle"] == "Biscuits"
    # The old {item_id: supplier} form still works (item / vendor rules only).
    assert co.overridden_categories(store, {"parle": "Raja Foods"}) == {}


# --------------------------------------------------------------------------
# Admin API
# --------------------------------------------------------------------------


def test_admin_api_validates_category_match_value(store, admin_client):
    bad = admin_client.post("/api/admin/category-overrides", json={
        "match_type": "category", "match_value": "Snacks", "category_group": "RETAIL", "category": "X"})
    assert bad.status_code == 400
    ok = admin_client.post("/api/admin/category-overrides", json={
        "match_type": "category", "match_value": encode_category_match("RETAIL", "Produce", None),
        "category_group": "PRODUCE", "category": "Vegetables", "subcategory": ""})
    assert ok.status_code == 200, ok.text
    assert ok.json()["subcategory"] == ""  # "" = no subcategory, kept as given
    assert cats(latest(store)["okra"]) == ("PRODUCE", "Vegetables", "")
    rid = ok.json()["id"]
    assert admin_client.patch("/api/admin/category-overrides/%d" % rid, json={"match_value": "x"}).status_code == 400


# --------------------------------------------------------------------------
# Seeded rules (migration 0037)
# --------------------------------------------------------------------------


def _migration():
    path = BACKEND / "alembic" / "versions" / "0037_category_mapping_rules.py"
    spec = importlib.util.spec_from_file_location("m0037", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic(db_file):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return lambda *args: subprocess.run(  # noqa: E731
        [str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True
    )


def test_seed_rules_cover_the_merge_plan():
    seeds = {match: target for match, target in _migration().SEED_RULES}
    assert seeds[("RETAIL", "Snacks & Savories", "Biscuits & Cookies")] == BCR
    for sub in ("Aha Snacks", "GPR Snacks", "Telugu Snacks"):
        assert seeds[("RETAIL", "Snacks & Savories", sub)] == ("RETAIL", "Snacks & Savories", "Namkeen")
    for sub in ("Fruits", "Vegetables", "Leafy Vegetables", "Roots", "Fresh Herbs"):
        assert seeds[("RETAIL", "Produce", sub)] == ("PRODUCE", sub, "")
    assert seeds[("PRODUCE", "Plants", None)] == ("RETAIL", "Plants & Seeds", "")
    assert seeds[("RETAIL", "Powders & Rituals", None)] == ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor")
    assert seeds[("RETAIL", "Festival & Cultural", "Prayer Kits")] == ("RETAIL", "Pooja Essentials", "Prayer Kits")
    assert seeds[("RETAIL", "Health & Wellness", "Pain Relief Balms & Ointments")][2] == "Pain Relief"
    assert seeds[("RETAIL", "Health & Wellness", "Health Supplements")][2] == "Vitamins & Supplements"
    essences = ("RETAIL", "Baking & Cooking Essentials", "Food Colors & Essences")
    assert seeds[("RETAIL", "Pantry Staples", "Cooking Essences & Flavors")] == essences
    assert seeds[("RETAIL", "Baking & Cooking Essentials", "Vanilla")] == essences
    assert seeds[("RETAIL", "Spiritual & Cultural Products", None)] == ("RETAIL", "Pooja Essentials", "Pooja Essentials")
    assert seeds[("RETAIL", "Spiritual & Cultural Products", "Powders & Rituals")][2] == "Powders, Chandan & Sindoor"
    assert len(seeds) == len(_migration().SEED_RULES)  # no duplicate keys
    # Encoded exactly as the app encodes (and decodes) a category rule.
    for match, _ in _migration().SEED_RULES:
        assert _migration()._encode(match) == encode_category_match(*match)


def test_migration_0037_seeds_and_downgrade(tmp_path):
    db_file = tmp_path / "m.db"
    run = _alembic(db_file)
    run("upgrade", "0037")
    migration = _migration()
    con = sqlite3.connect(db_file)
    try:
        rows = con.execute(
            "SELECT match_type, match_value, category_group, category, subcategory, active, created_by, note "
            "FROM category_overrides WHERE match_type = 'category' ORDER BY id"
        ).fetchall()
        assert len(rows) == len(migration.SEED_RULES)
        for row, (match, target) in zip(rows, migration.SEED_RULES):
            assert row[:7] == ("category", encode_category_match(*match), *target, 1, migration.SEEDED_BY)
            assert "CATEGORY_MERGE_PLAN" in row[7]
        # The Shaz vendor rule is still there.
        assert con.execute("SELECT COUNT(*) FROM category_overrides WHERE match_type = 'supplier'").fetchone()[0] == 1
        # A rule someone adds later survives a downgrade.
        con.execute(
            "INSERT INTO category_overrides (match_type, match_value, category_group, category, subcategory, "
            "created_at, active, created_by) VALUES ('category', ?, 'RETAIL', 'X', NULL, '2026-09-30', 1, 'meera')",
            (encode_category_match("RETAIL", "Y", None),),
        )
        con.commit()
    finally:
        con.close()
    run("downgrade", "0036")
    con = sqlite3.connect(db_file)
    try:
        left = con.execute("SELECT match_type, created_by FROM category_overrides ORDER BY id").fetchall()
        assert left == [("supplier", None), ("category", "meera")]
    finally:
        con.close()
    run("upgrade", "head")


def test_seeded_rules_keep_current_placements(tmp_path):
    """Every item the seeds match that already has an item rule keeps it;
    an item with no rule of its own moves (the whole point)."""
    db_file = tmp_path / "s.db"
    _alembic(db_file)("upgrade", "head")
    con = sqlite3.connect(db_file)
    for item_id, cat, sub, supplier in [
        ("placed", "Snacks & Savories", "Biscuits & Cookies", "Raja Foods"),
        ("new", "Snacks & Savories", "Biscuits & Cookies", "Raja Foods"),
        ("herb", "Produce", "Fresh Herbs", "Farm Co"),
        ("shaz", "Snacks & Savories", "Biscuits & Cookies", "Shaz International"),
    ]:
        con.execute(
            "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, category_group, category, subcategory, "
            "supplier, uploaded_at) VALUES (?, ?, ?, 'RETAIL', ?, ?, ?, '2026-09-30')",
            (TODAY.isoformat(), item_id, item_id, cat, sub, supplier),
        )
    con.execute(
        "INSERT INTO category_overrides (match_type, match_value, category_group, category, subcategory, created_at, "
        "active) VALUES ('item_id', 'placed', 'RETAIL', 'Snacks & Savories', 'Chocolates & Candy', '2026-09-30', 1)"
    )
    con.commit()
    con.close()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_file}")
    session = sessionmaker(bind=engine)()
    try:
        items = latest_inventory_by_item(session)
        assert cats(items["placed"]) == ("RETAIL", "Snacks & Savories", "Chocolates & Candy")
        assert cats(items["new"]) == BCR
        assert cats(items["shaz"]) == BCR  # vendor rule
        assert cats(items["herb"]) == ("PRODUCE", "Fresh Herbs", "")
        before = session.execute(text("SELECT category, subcategory FROM inventory_snapshots ORDER BY id")).fetchall()
        assert ("Snacks & Savories", "Biscuits & Cookies") in before
    finally:
        session.close()
        engine.dispose()


def test_fix_list_script_includes_category_rules(tmp_path):
    db_file = tmp_path / "f.db"
    _alembic(db_file)("upgrade", "head")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, category_group, category, subcategory, "
        "supplier, uploaded_at) VALUES (?, 'okra', 'Okra', 'RETAIL', 'Produce', 'Vegetables', 'Farm', '2026-09-30')",
        (TODAY.isoformat(),),
    )
    con.execute(
        "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, category_group, category, subcategory, "
        "supplier, uploaded_at) VALUES (?, 'bhindi', 'Bhindi', 'PRODUCE', 'Vegetables', NULL, 'Farm', '2026-09-30')",
        (TODAY.isoformat(),),
    )
    con.commit()
    con.close()
    script = BACKEND / "scripts" / "category_fix_list.py"
    out = subprocess.run([sys.executable, str(script), str(db_file)], check=True, capture_output=True, text=True)
    assert "Okra,Farm,RETAIL,Produce,Vegetables,PRODUCE,Vegetables,,yes,okra" in out.stdout
    assert "Bhindi" not in out.stdout
