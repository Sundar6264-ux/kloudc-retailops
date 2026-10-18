"""Ops fixes (E): similar products and other vendors in Supplier Projection.

- The offline matcher (services/similar_products.py + app/data/similar_products/)
  parses names, finds the same product across brands and sizes, and holds a
  quality floor on the hand-labelled set (tests/data/, real item names from
  the store's catalog - see tests/similar_products_eval.py).
- GET /api/ordering/similar-items and the additive `other_vendors` /
  `similar_count` fields on the Supplier Projection response.
"""

import datetime as dt
import time
import uuid

import pytest

from app.models import InventorySnapshot, Order
from app.services import similar_products as sp
from tests.conftest import client_for, make_user
from tests.similar_products_eval import FIXTURE_PATH, LABELS_PATH, evaluate, load_fixture, load_labels, split

TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def _fresh_similar_cache():
    sp.clear_similar_cache()
    yield
    sp.clear_similar_cache()


# --- name parsing -----------------------------------------------------------


@pytest.fixture(scope="module")
def vocab():
    return sp.load_vocab()


@pytest.mark.parametrize(
    "name, brand, size, tokens",
    [
        ("Deep Shredded Coconut 24oz", "Deep", "24oz", ["grated", "coconut"]),
        ("Deep Large ShredCoconut 12oz", "Deep", "12oz", ["large", "grated", "coconut"]),
        ("Daily Delight Grated Coconut 1Lb", "Daily Delight", "1lb", ["grated", "coconut"]),
        ("DD Coconut Chutney 283.5g", "Daily Delight", "283.5g", ["coconut", "chutney"]),
        ("LX. Frozen Shredded Coconut 400gm", "Laxmi", "400gm", ["frozen", "grated", "coconut"]),
        ("Swad Tuvar Dal 4lb", "Swad", "4lb", ["toor", "dal"]),
        ("Deep Arhar Dal 2lb", "Deep", "2lb", ["toor", "dal"]),
        ("Laxmi Jeera 200g", "Laxmi", "200g", ["cumin"]),
        ("Swad Semolina 2lb", "Swad", "2lb", ["rava"]),
        ("Laxmi Besan Gram Flour 10lb", "Laxmi", "10lb", ["besan"]),
        ("BLACK SALT POWDER SWAD 400GM", "Swad", "400gm", ["blacksalt", "powder"]),
        ("Haldiram'ss Pav Bhaji 300gms", "Haldiram's", "300gms", ["pavbhaji"]),
        ("TR Roghni Kulcha 150gx3", "TRS", "150g", ["roghni", "kulcha"]),
        ("Parle-G 376g", "Parle", "376g", ["parle", "biscuit"]),
    ],
)
def test_parse_name(vocab, name, brand, size, tokens):
    parsed = sp.parse_name(name, vocab)
    assert parsed.brand == brand
    assert parsed.size == size
    assert parsed.tokens == tokens


def test_weighed_items_and_offers_are_not_pack_sizes(vocab):
    assert sp.parse_name("Chicken Thigh Boneless in lbs", vocab).size == "per lb"
    assert sp.parse_name("Fresh Paan B4G1", vocab).size is None


def test_spelling_variants_only_fold_transliteration_style_edits():
    assert sp._spelling_variant("masla", "masala")
    assert sp._spelling_variant("idlli", "idli")
    assert sp._spelling_variant("curshed", "crushed")
    assert not sp._spelling_variant("makhan", "makhana")  # butter vs fox nuts
    assert not sp._spelling_variant("neera", "jeera")
    assert not sp._spelling_variant("better", "butter")


def test_vendor_refs_split_and_align_codes():
    assert sp.vendor_refs("Raja Foods;RBest Produce Inc", "RF1;RB9") == [
        {"name": "Raja Foods", "supplier_item_id": "RF1"},
        {"name": "RBest Produce Inc", "supplier_item_id": "RB9"},
    ]
    # Codes that don't line up with the vendors aren't guessed.
    assert sp.vendor_refs("A;B", "X") == [{"name": "A", "supplier_item_id": None}, {"name": "B", "supplier_item_id": None}]
    assert sp.other_vendor_refs("Raja Foods; RBest ;raja foods", None, "Raja Foods") == [
        {"name": "RBest", "supplier_item_id": None}
    ]
    assert sp.other_vendor_refs(None, None, "Raja Foods") == []


# --- quality on real item names ---------------------------------------------------


# The labelled catalog fixture was a real store's catalog export, so it is
# not part of the StoreOps repository. These tests run when a catalog fixture
# is present at tests/data/ (see tests/similar_products_eval.py) and skip
# otherwise. TODO(M2): replace with a synthetic catalog + labels.
requires_catalog_fixture = pytest.mark.skipif(
    not (FIXTURE_PATH.exists() and LABELS_PATH.exists()),
    reason="labelled catalog fixture not present (store data is not kept in the repo)",
)


@pytest.fixture(scope="module")
def fixture_index():
    return sp.SimilarIndex(load_fixture())


@requires_catalog_fixture
def test_deep_coconut_finds_other_brands(fixture_index):
    deep = next(i for i in fixture_index.items if i.name == "Deep Shredded Coconut 24oz")
    matches = fixture_index.similar(deep.item_id)
    names = [m.item.name for m in matches]
    assert "Daily Delight Grated Coconut 1Lb" in names
    assert len(matches) == 8
    # Every match is frozen grated/shredded coconut - never milk, chutney or oil.
    for m in matches:
        assert {"grated", "coconut"} <= set(m.parsed.tokens), m.item.name
    daily = next(m for m in matches if m.item.name.startswith("Daily Delight"))
    assert daily.reason == "Same product, other brand"


@requires_catalog_fixture
def test_matches_exclude_self_container_and_kitchen(fixture_index):
    for q in load_labels()["queries"][:30]:
        for m in fixture_index.similar(q["item_id"]):
            assert m.item.item_id != q["item_id"]
            assert not m.item.is_container_row
            assert (m.item.category_group or "").upper() not in {"STREET KITCHEN", "CATERING MENU"}


@requires_catalog_fixture
def test_quality_floor_on_labelled_set(fixture_index):
    """Measured when frozen (fixture): all P@5 0.94, recall@8 0.86; held-out
    half P@5 0.92, recall@8 0.77. After the verifier's rules (one-off words
    down-weighted, fresh vs packaged, more brand aliases): all P@5 0.955,
    recall@8 0.876; held-out P@5 0.949, recall@8 0.817. Floors leave a
    little room."""
    labels = load_labels()
    assert len(labels["queries"]) >= 60
    overall = evaluate(fixture_index, labels)
    assert overall["precision_at_5"] >= 0.88
    assert overall["recall_at_8_grade2"] >= 0.80
    assert overall["false_alarms_on_no_match_queries"] <= 2
    held_out = evaluate(fixture_index, split(labels, "test"))
    assert held_out["precision_at_5"] >= 0.85
    assert held_out["recall_at_8_grade2"] >= 0.70


@requires_catalog_fixture
def test_index_builds_fast_and_lookups_are_instant():
    items = load_fixture()
    started = time.perf_counter()
    index = sp.SimilarIndex(items)
    assert time.perf_counter() - started < 2.0
    started = time.perf_counter()
    for item in index.items[:200]:
        index.similar(item.item_id)
    assert (time.perf_counter() - started) / 200 < 0.02


# --- API ------------------------------------------------------------------------------


def snap(db, item_id, name, qty=5.0, price=4.99, supplier="Raja Foods", group="RETAIL", day=TODAY, **extra):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=extra.pop("category", "Frozen Foods"),
            inventory_quantity=qty,
            price=price,
            cost=extra.pop("cost", 2.5),
            supplier=supplier,
            **extra,
        )
    )


def sale(db, item_id, day, qty=1.0, voided=False):
    db.add(
        Order(
            toast_order_guid=str(uuid.uuid4()),
            toast_check_guid=str(uuid.uuid4()),
            toast_selection_guid=str(uuid.uuid4()),
            business_date=day,
            item_guid=item_id,
            item_name=item_id,
            quantity=qty,
            voided=voided,
        )
    )


@pytest.fixture
def store(db):
    snap(db, "deep", "Deep Shredded Coconut 24oz", qty=2, supplier="Chetak;Raja Foods", supplier_item_id="C1;R1")
    snap(db, "dd", "Daily Delight Grated Coconut 1Lb", qty=33, price=5.99, cost=4.22, supplier="Raja Foods")
    snap(db, "laxmi", "Laxmi Grated Coconut 400g", qty=0, supplier="House of Spices;Raja Foods", supplier_item_id="L1;L2")
    # Container duplicate of Laxmi: its stock counts towards Laxmi's on hand.
    snap(db, "laxmi-c", "Laxmi Grated Coconut 400g", qty=12, price=None, supplier=None)
    snap(db, "milk", "Deep Coconut Milk 400ml", supplier="Chetak")
    snap(db, "kitchen", "Grated Coconut Chutney Plate", group="STREET KITCHEN", supplier=None, category="Kitchen")
    snap(db, "old", "Vadilal Grated Coconut 312g", day=TODAY - dt.timedelta(days=3))  # not in the latest upload
    for d in range(1, 11):
        sale(db, "dd", TODAY - dt.timedelta(days=d), 2)
    sale(db, "dd", TODAY - dt.timedelta(days=40), 50)  # outside the 30 days
    sale(db, "dd", TODAY - dt.timedelta(days=2), 9, voided=True)
    db.commit()


def test_similar_items_endpoint(db, admin_client, store):
    res = admin_client.get("/api/ordering/similar-items", params={"item_id": "deep"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "Deep Shredded Coconut 24oz"
    assert body["brand"] == "Deep" and body["size"] == "24oz"
    assert body["product_type"] == "grated coconut"
    by_id = {i["item_id"]: i for i in body["items"]}
    assert set(by_id) == {"dd", "laxmi"}  # not milk, not kitchen, not the container row, not a deleted item
    dd = by_id["dd"]
    assert dd["reason"] == "Same product, other brand"
    assert dd["units_30d"] == 20.0  # voided and older sales left out
    assert dd["price"] == 5.99 and dd["cost"] == 4.22 and dd["on_hand_qty"] == 33
    assert dd["vendors"] == [{"name": "Raja Foods", "supplier_item_id": None}]
    laxmi = by_id["laxmi"]
    assert laxmi["on_hand_qty"] == 12 and laxmi["container_qty"] == 12
    assert laxmi["vendors"] == [
        {"name": "House of Spices", "supplier_item_id": "L1"},
        {"name": "Raja Foods", "supplier_item_id": "L2"},
    ]
    assert laxmi["units_30d"] == 0


def test_similar_items_404_and_limit(db, admin_client, store):
    assert admin_client.get("/api/ordering/similar-items", params={"item_id": "nope"}).status_code == 404
    res = admin_client.get("/api/ordering/similar-items", params={"item_id": "deep", "limit": 1})
    assert len(res.json()["items"]) == 1


def test_similar_items_is_gated_like_supplier_projection(db, store):
    outsider = client_for(db, make_user(db, "noproj", features=["team_tasks"]))
    assert outsider.get("/api/ordering/similar-items", params={"item_id": "deep"}).status_code == 403
    buyer = client_for(db, make_user(db, "buyer", features=["supplier_projection"]))
    assert buyer.get("/api/ordering/similar-items", params={"item_id": "deep"}).status_code == 200


def test_projection_has_other_vendors_and_similar_count(db, admin_client, store):
    res = admin_client.get(
        "/api/ordering/supplier-projection", params={"supplier": "Raja Foods", "horizons": "14", "method": "legacy"}
    )
    assert res.status_code == 200, res.text
    items = {i["item_id"]: i for i in res.json()["items"]}
    assert items["deep"]["other_vendors"] == [{"name": "Chetak", "supplier_item_id": "C1"}]
    assert items["deep"]["supplier_item_id"] == "R1"
    assert items["laxmi"]["other_vendors"] == [{"name": "House of Spices", "supplier_item_id": "L1"}]
    assert items["dd"]["other_vendors"] == []
    assert items["deep"]["similar_count"] == 2
    assert items["dd"]["similar_count"] == 2


def test_index_is_rebuilt_after_a_same_day_reupload(db, admin_client, store):
    first = admin_client.get("/api/ordering/similar-items", params={"item_id": "deep"}).json()
    assert {i["item_id"] for i in first["items"]} == {"dd", "laxmi"}
    row = db.query(InventorySnapshot).filter_by(item_id="milk").one()
    row.name = "Deep Grated Coconut 400g"  # renamed in Toast and re-uploaded the same day
    db.commit()
    second = admin_client.get("/api/ordering/similar-items", params={"item_id": "deep"}).json()
    assert "milk" in {i["item_id"] for i in second["items"]}


# --- verifier additions ------------------------------------------------------------


def test_vendor_codes_keep_empty_slots_and_skip_junk():
    # Toast keeps an empty slot for a vendor with no code; dropping it would
    # shift every later code onto the wrong vendor (real row: Tomato Plum).
    assert sp.vendor_refs("Raja Foods;RBest Produce Inc;Restaurant Depot", ";2524;856977005124") == [
        {"name": "Raja Foods", "supplier_item_id": None},
        {"name": "RBest Produce Inc", "supplier_item_id": "2524"},
        {"name": "Restaurant Depot", "supplier_item_id": "856977005124"},
    ]
    assert sp.vendor_refs("A;B", "1;2;") == [
        {"name": "A", "supplier_item_id": "1"},
        {"name": "B", "supplier_item_id": "2"},
    ]
    # The Sep 20 upload put item codes / a placeholder in the Supplier column.
    assert sp.vendor_refs("ACT_SUP_UPDT;Raja Foods", ";R1") == [{"name": "Raja Foods", "supplier_item_id": "R1"}]
    assert sp.vendor_refs("30128", None) == [] and sp.vendor_refs("5RR2", "x") == []
    assert not sp.is_junk_vendor("Singh & Singh") and not sp.is_junk_vendor("SpiceTown - Street Kitchen")
    # Listed twice in Toast -> one vendor, keeping the code it has.
    assert sp.vendor_refs("House of Spices;House of Spices", ";HS1") == [
        {"name": "House of Spices", "supplier_item_id": "HS1"}
    ]
    assert sp.other_vendor_refs("Raja Foods;RBest Produce Inc", ";2524", "raja foods") == [
        {"name": "RBest Produce Inc", "supplier_item_id": "2524"}
    ]


def test_projection_supplier_item_id_respects_empty_slots(db, admin_client):
    snap(db, "tom", "Tomato Plum in LBs", supplier="Raja Foods;RBest Produce Inc;Restaurant Depot",
         supplier_item_id=";2524;856977005124", category="Produce")
    db.commit()
    for vendor, code in [("Raja Foods", None), ("Restaurant Depot", "856977005124")]:
        res = admin_client.get(
            "/api/ordering/supplier-projection", params={"supplier": vendor, "horizons": "14", "method": "legacy"}
        )
        row = next(i for i in res.json()["items"] if i["item_id"] == "tom")
        assert row["supplier_item_id"] == code, vendor
    assert [v["name"] for v in row["other_vendors"]] == ["Raja Foods", "RBest Produce Inc"]


def _mini_index(rows):
    return sp.SimilarIndex(
        sp.CatalogItem(item_id=i, name=n, category_group=g, category=c, supplier="V", price=1.0)
        for i, n, g, c in rows
    )


def test_fresh_produce_is_not_matched_to_packaged_goods_or_event_menu():
    index = _mini_index([
        ("t1", "Tomato Regular in LBs", "RETAIL", "Produce"),
        ("t2", "Tomato Plum in LBs", "RETAIL", "Produce"),
        ("t3", "Balaji Tomato 30gm", "RETAIL", "Snacks & Savories"),
        ("t4", "Tomato Plant Each", "RETAIL", "Plants & Seeds"),
        ("l1", "Deep Dai Mango Lassi 11fl.oz", "RETAIL", "Beverages (Non-Alcoholic)"),
        ("l2", "Mango Lassi", "Event Menu", "Tea & Drinks"),
        ("l3", "Deep Dai Mango Lassi 32fl.oz", "RETAIL", "Beverages (Non-Alcoholic)"),
    ])
    assert [m.item.item_id for m in index.similar("t1")] == ["t2"]
    assert [m.item.item_id for m in index.similar("l1")] == ["l3"]


def test_brand_aliases_and_one_off_words_dont_hide_matches():
    index = _mini_index([
        ("r1", "Brookbond Red Label Tea 500g", "RETAIL", "Tea"),
        ("r2", "Red Label Natural Care 500g", "RETAIL", "Tea"),
        ("r3", "Taj Mahal Tea 1kg", "RETAIL", "Tea"),
        ("w1", "Laxmi Wet Tamarind 400g", "RETAIL", "Pantry Staples"),
        ("w2", "TR Tamarind 400g", "RETAIL", "Condiments & Sauces"),
        ("w3", "Mumtaz Tamarind 14oz", "RETAIL", "Condiments & Sauces"),
        ("w4", "Deep Tamarind Date Chutney 18oz", "RETAIL", "Condiments & Sauces"),
        ("w5", "Laxmi Tamarind Slab 300gm", "RETAIL", "Pantry Staples"),
        ("m1", "Shan Meat Tenderizer 40G", "RETAIL", "Spices & Seasonings"),
        ("m2", "Shan Meat Masala 100gm", "RETAIL", "Spices & Seasonings"),
        ("m3", "MDH Meat Masala 100G", "RETAIL", "Spices & Seasonings"),
        ("h1", "LG Hing Powder 100g", "RETAIL", "Spices & Seasonings"),
        ("h2", "Jabson Peanut Hing Jeera 140g", "RETAIL", "Snacks & Savories"),
        ("h3", "Heeng Powder Deep 100g", "RETAIL", "Spices & Seasonings"),
    ])
    assert index.parsed["r1"].brand == "Red Label"
    assert {"r2", "r3"} <= {m.item.item_id for m in index.similar("r1")}
    assert "w2" in {m.item.item_id for m in index.similar("w1")}
    assert index.similar("m1") == []  # a tenderizer is not a meat masala
    assert [m.item.item_id for m in index.similar("h1")] == ["h3"]  # not the hing-jeera snack


def test_category_override_change_clears_similar_cache(db, admin_client, store):
    first = admin_client.get("/api/ordering/similar-items", params={"item_id": "deep"}).json()
    assert next(i for i in first["items"] if i["item_id"] == "dd")["category"] == "Frozen Foods"
    r = admin_client.post("/api/admin/category-overrides", json={
        "match_type": "item_id", "match_value": "dd", "category_group": "RETAIL", "category": "Frozen Coconut"})
    assert r.status_code == 200, r.text
    second = admin_client.get("/api/ordering/similar-items", params={"item_id": "deep"}).json()
    assert next(i for i in second["items"] if i["item_id"] == "dd")["category"] == "Frozen Coconut"


def test_index_built_late_in_a_request_uses_current_category_rules(db, store):
    """A long request (Supplier Projection) loads category rules into its
    session first; a rule added meanwhile clears the similar cache. The
    index that request then builds must use the new rule, not cache the
    session's stale copy until the next upload."""
    from app.models import CategoryOverride
    from app.services.category_overrides import load_rules
    from app.services.reorder import latest_inventory_by_item
    from app.services.similar_products import clear_similar_cache
    from app.db import SessionLocal

    latest_inventory_by_item(db)  # request start: rules cached in this session
    assert not load_rules(db)
    other = SessionLocal()
    try:
        other.add(CategoryOverride(match_type="item_id", match_value="dd", category_group="RETAIL",
                                   category="Frozen Coconut", active=True, created_at=dt.datetime.now(dt.timezone.utc),
                                   created_by="t"))
        other.commit()
    finally:
        other.close()
    clear_similar_cache()  # what the category router does after the commit
    index = sp.get_index(db)
    assert index.by_id["dd"].category == "Frozen Coconut"
