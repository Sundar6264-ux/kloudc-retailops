"""Store vs Container report (Oct 1): only items with both a store (priced)
row and a Container row in the latest upload, paired exactly like Fix Issues'
Refill from container and Supplier Projection, with Low on shelf, search /
vendor filters, totals, CSV export and the inventory_reports gate."""

import datetime as dt
import os
import pathlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import glossary as g
from app.models import CategoryOverride, InventorySnapshot
from app.services import stock_issues
from app.services.fix_issues import get_issue_list
from app.services.reorder import latest_inventory_by_item
from app.services.store_vs_container import EXPORT_COLUMNS, get_store_vs_container
from app.services.supplier_projection import _split_suppliers, compute_supplier_projection
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

TODAY = dt.date.today()
OLDER = TODAY - dt.timedelta(days=3)
URL = "/api/reports/store-vs-container"

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DEV_DB = pathlib.Path(os.environ.get("ORIGIN_DEV_DB", BACKEND / "origin-dev.db"))


@pytest.fixture(autouse=True)
def _fresh_forecast_cache():
    stock_issues.clear_forecast_cache()
    yield
    stock_issues.clear_forecast_cache()


def snap(db, item_id, name, qty, day=TODAY, price=4.99, supplier="Acme", group="RETAIL", **extra):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=extra.pop("category", "Lentils"),
            subcategory=extra.pop("subcategory", "Dals"),
            inventory_quantity=qty,
            price=price,
            cost=2.0,
            supplier=supplier,
            supplier_item_id=extra.pop("supplier_item_id", None),
            barcode=extra.pop("barcode", "012345678905"),
            **extra,
        )
    )


def bare(db, item_id, name, qty, day=TODAY, group="RETAIL"):
    snap(db, item_id, name, qty, day=day, price=None, supplier=None, group=group)


@pytest.fixture
def store(db):
    # Paired items.
    snap(db, "toor", "Toor Dal 8lb", 3, supplier="Chetak", supplier_item_id="L206")
    bare(db, "toor-c", "Toor Dal 8lb", 20)
    snap(db, "atta", "Atta 10lb", 8, supplier="Raja Foods;Swadesi", supplier_item_id="R1;S2", category="Flours")
    bare(db, "atta-c", "Atta 10lb", 30)
    snap(db, "zero", "Zero Rice", 0)
    bare(db, "zero-c", "Zero Rice", 5)
    snap(db, "neg", "Neg Ghee", -2)
    bare(db, "neg-c", "Neg Ghee", 4)
    snap(db, "empty", "Empty Oil", 2)
    bare(db, "empty-c", "Empty Oil", 0)  # paired, but nothing in the container
    # Not paired.
    snap(db, "banana-1", "Banana Leaves", 3)  # two priced rows: different products
    snap(db, "banana-2", "Banana Leaves", 4, price=9.99)
    bare(db, "banana-c", "Banana Leaves", 50)
    bare(db, "lonely-c", "Lonely Container", 9)  # no store row
    snap(db, "shelf", "Shelf Only", 2)  # no container row
    snap(db, "twin", "Twin Bare", 1)  # two bare rows: ambiguous
    bare(db, "twin-c1", "Twin Bare", 5)
    bare(db, "twin-c2", "Twin Bare", 6)
    snap(db, "samosa", "Samosa", 1, group="KITCHEN")  # kitchen
    bare(db, "samosa-c", "Samosa", 10, group="KITCHEN")
    # Only in an older upload (deleted in Toast since): not listed.
    snap(db, "old", "Old Masala", 1, day=OLDER)
    bare(db, "old-c", "Old Masala", 7, day=OLDER)
    snap(db, "toor", "Toor Dal 8lb", 99, day=OLDER)  # an older count never wins
    db.commit()
    return db


def ids(items):
    return [i["item_id"] for i in items]


def test_only_items_in_both_store_and_container(store):
    data = get_store_vs_container(store)
    assert set(ids(data["items"])) == {"toor", "atta", "zero", "neg", "empty"}
    assert data["snapshot_date"] == TODAY.isoformat()


def test_row_fields_and_quantities(store):
    rows = {r["item_id"]: r for r in get_store_vs_container(store)["items"]}
    toor = rows["toor"]
    assert toor == {
        "item_id": "toor",
        "name": "Toor Dal 8lb",
        "supplier": "Chetak",
        "supplier_item_id": "L206",
        "category_group": "RETAIL",
        "category": "Lentils",
        "subcategory": "Dals",
        "store_qty": 3.0,
        "container_qty": 20.0,
        "total_qty": 23.0,
        "price": 4.99,
        "low_store": True,
        "store_item_id": "toor",
        "container_item_id": "toor-c",
        # Additive (W2): for searching by code.
        "all_supplier_item_ids": "L206",
        "barcode": "012345678905",
    }
    assert rows["neg"]["store_qty"] == -2 and rows["neg"]["total_qty"] == 2


def test_low_store_rule(store):
    low = {r["item_id"]: r["low_store"] for r in get_store_vs_container(store)["items"]}
    assert low == {"toor": True, "zero": True, "neg": True, "atta": False, "empty": False}


def test_ambiguous_names_skipped(store):
    names = {r["name"] for r in get_store_vs_container(store)["items"]}
    assert not names & {"Banana Leaves", "Twin Bare", "Lonely Container", "Shelf Only", "Samosa", "Old Masala"}


def test_default_sort_low_first_then_name(store):
    assert ids(get_store_vs_container(store)["items"]) == ["neg", "toor", "zero", "atta", "empty"]


def test_totals(store):
    totals = get_store_vs_container(store)["totals"]
    assert totals == {"items": 5, "store_qty": 3 + 8 + 0 - 2 + 2, "container_qty": 20 + 30 + 5 + 4 + 0}


def test_search_and_vendor_filters(store, admin_client):
    data = admin_client.get(URL, params={"search": "TOOR"}).json()
    assert ids(data["items"]) == ["toor"]
    assert ids(admin_client.get(URL, params={"search": "l206"}).json()["items"]) == ["toor"]
    # A second vendor in Toast's ";"-separated list matches too.
    data = admin_client.get(URL, params={"vendor": "swadesi"}).json()
    assert ids(data["items"]) == ["atta"]
    assert data["totals"] == {"items": 1, "store_qty": 8, "container_qty": 30}
    assert data["vendors"] == ["Acme", "Chetak", "Raja Foods", "Swadesi"]
    assert admin_client.get(URL, params={"vendor": "Nobody"}).json()["items"] == []


def test_effective_categories_and_kitchen_from_a_correction(store):
    for item_id, group, category in (("toor", "RETAIL", "Pulses"), ("atta", "KITCHEN", "Kitchen")):
        store.add(
            CategoryOverride(
                match_type="item_id", match_value=item_id, category_group=group, category=category,
                subcategory=None, active=True, created_at=utcnow(),
            )
        )
    store.commit()
    rows = {r["item_id"]: r for r in get_store_vs_container(store)["items"]}
    assert rows["toor"]["category"] == "Pulses"
    assert "atta" not in rows


def test_low_store_equals_the_refill_list(store):
    low = {r["item_id"]: r for r in get_store_vs_container(store)["items"] if r["low_store"]}
    refill = {r["item_id"]: r for r in get_issue_list(store, "refill_from_container")["open"]}
    assert set(low) == set(refill)
    for item_id, row in refill.items():
        assert low[item_id]["store_qty"] == row["store_qty"]
        assert low[item_id]["container_qty"] == row["container_qty"]
        assert low[item_id]["container_item_id"] == row["container_item_id"]


def test_container_qty_equals_supplier_projection(store):
    proj = {p["item_id"]: p for p in compute_supplier_projection(store, "Acme", 30, [7], method="legacy")}
    for row in get_store_vs_container(store)["items"]:
        if "Acme" in _split_suppliers(row["supplier"]):
            assert proj[row["item_id"]]["container_qty"] == row["container_qty"]
            assert proj[row["item_id"]]["on_hand_qty"] == row["total_qty"]


def test_export_columns(store, admin_client):
    res = admin_client.get(URL + "/export")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    lines = res.text.splitlines()
    header = [label for label, _ in EXPORT_COLUMNS]
    assert lines[0].split(",") == header
    assert len(lines) == 6
    assert all(len(line.split(",")) == len(header) for line in lines if '"' not in line)
    assert lines[1] == "Neg Ghee,Acme,,RETAIL,Lentils,Dals,-2,4,2,4.99,Yes"
    atta = next(line for line in lines if line.startswith("Atta"))
    assert atta.endswith(",No")


def test_export_empty_is_header_only(admin_client):
    res = admin_client.get(URL + "/export")
    assert res.text == ",".join(label for label, _ in EXPORT_COLUMNS) + "\n"
    assert admin_client.get(URL).json() == {
        "snapshot_date": None,
        "items": [],
        "totals": {"items": 0, "store_qty": 0, "container_qty": 0},
        "vendors": [],
    }


def test_export_neutralizes_formulas(db, admin_client):
    snap(db, "evil", "=HYPERLINK(1)", 1)
    bare(db, "evil-c", "=HYPERLINK(1)", 3)
    db.commit()
    lines = admin_client.get(URL + "/export").text.splitlines()
    assert lines[1].startswith("'=HYPERLINK(1)")


def test_gate(store, db, anon_client):
    assert anon_client.get(URL).status_code == 401
    no_access = client_for(db, make_user(db, "noreports", features=["overview"]))
    assert no_access.get(URL).status_code == 403
    assert no_access.get(URL + "/export").status_code == 403
    granted = client_for(db, make_user(db, "reports", features=["inventory_reports"]))
    assert granted.get(URL).status_code == 200
    assert granted.get(URL + "/export").status_code == 200


def test_glossary_term_quotes_the_threshold():
    term = g.TERMS_BY_KEY["store_vs_container"]
    assert str(stock_issues.REFILL_STORE_MAX_QTY) in term["rule"]
    assert term["category"] in {c["key"] for c in g.CATEGORIES}


# ---------------------------------------------------------- real data


@pytest.fixture
def dev_copy(tmp_path):
    """A copy of the dev database (sqlite backup API, source opened read-only)."""
    if not DEV_DB.exists():
        pytest.skip(f"no dev database at {DEV_DB}")
    target = tmp_path / "dev-copy.db"
    src = sqlite3.connect(f"file:{DEV_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    engine = create_engine(f"sqlite:///{target}")
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_numbers_match_refill_and_projection_on_dev_data(dev_copy):
    data = get_store_vs_container(dev_copy)
    items = {r["item_id"]: r for r in data["items"]}
    if not items:
        pytest.skip("dev database has no store/container pairs")
    latest = latest_inventory_by_item(dev_copy)
    refill = {r["item_id"]: r for r in stock_issues.refill_from_container_items(latest)}
    kitchen = {"KITCHEN", "CATERING MENU", "EVENT MENU"}
    refill = {k: v for k, v in refill.items() if (latest[k].category_group or "").upper() not in kitchen}
    assert {k for k, r in items.items() if r["low_store"]} == set(refill)
    for item_id, row in refill.items():
        assert items[item_id]["store_qty"] == round(row["store_qty"], 2)
        assert items[item_id]["container_qty"] == round(row["container_qty"], 2)

    checked = 0
    for vendor in data["vendors"]:
        proj = {p["item_id"]: p for p in compute_supplier_projection(dev_copy, vendor, 90, [7], method="legacy")}
        for item_id, row in items.items():
            if vendor in _split_suppliers(row["supplier"]):
                assert round(proj[item_id]["container_qty"], 2) == row["container_qty"], (vendor, row["name"])
                checked += 1
    assert checked >= len([r for r in items.values() if r["supplier"]])


def test_blank_vendor_codes_are_none(db):
    snap(db, "tea", "Tea 1kg", 4, supplier="A;B;C", supplier_item_id=";;")
    bare(db, "tea-c", "Tea 1kg", 3)
    db.commit()
    assert get_store_vs_container(db)["items"][0]["supplier_item_id"] is None
