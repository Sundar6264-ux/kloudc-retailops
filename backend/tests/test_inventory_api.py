"""Inventory from the Toast API (services/inventory_api.py; contract items 1
and 6 and the live stock overlay). Every Toast call is mocked: `toast_get`
is replaced by a fake that serves canned JSON and records the paths, and a
guard fails the test if anything reaches httpx."""

import datetime as dt
import json
import uuid

import httpx
import pytest

from app.config import get_settings
from app.models import InventorySnapshot, IssueResolution, JobRun, Order, ToastMenuItem
from app.pos.adapters.toast import catalog as toast_catalog
from app.pos.adapters.toast import client as toast_http
from app.pos.adapters.toast import stock as toast_stock
from app.pos.adapters.toast import timeparse as toast_time
from app.services import inventory_api as ia
from app.services import stock_issues
from app.services.barcode_report import get_invalid_barcodes, get_missing_barcodes
from app.services.fix_issues import get_issue_list
from app.services.inventory_intelligence import get_negative_inventory
from app.services.inventory_parser import parse_and_store
from app.services.notification_triggers import TRIGGERS
from app.services.notifications import ALERTS_BY_KEY
from app.services.reorder import latest_inventory_by_item
from app.services.supplier_projection import compute_supplier_projection
from app.timeutil import restaurant_today

TODAY = restaurant_today()
CSV_DAY = TODAY - dt.timedelta(days=1)
GOOD = "012345678905"  # valid UPC-A
GOOD2 = "036000291452"
BAD = "012345678901"  # wrong check digit

RICE = "rice-each"
RICE_C = "rice-container"
DAL = "dal-each"
DAL_C = "dal-container"
TEA = "tea"
NEG = "negative-item"
UNTRACKED = "street-dish"
BIRYANI = "biryani"
NEWITEM = "brand-new"
GH_ONLY = "gh-only"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No real Toast: any httpx request fails the test. Live caches reset."""

    def _no_network(*a, **k):
        raise AssertionError("a test reached the network")

    # Real HTTP goes through HTTPTransport; the app's TestClient has its own.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    ia.clear_live_stock_cache()
    ia._compare_cache.clear()
    ia._menu_cache_state.clear()
    stock_issues.clear_forecast_cache()
    s = get_settings()
    monkeypatch.setattr(s, "inventory_api_snapshots", False)
    monkeypatch.setattr(s, "menu_barcode_recheck_minutes", 60)
    monkeypatch.setattr(s, "inventory_csv_max_age_days", 7)
    yield
    ia.clear_live_stock_cache()


def _item(guid, name, price=4.99, sku="", plu="", strategy="BASE_PRICE", product=None, option=None):
    cp = None
    if product:
        cp = {
            "product": {"id": product, "name": name.rsplit(" - ", 1)[0], "type": "OPTION"},
            "productVariant": {
                "name": name,
                "selectedOptions": [{"name": "Multi-location Item", "value": {"name": option}}],
            },
        }
    return {
        "guid": guid,
        "name": name,
        "price": price,
        "pricingStrategy": strategy,
        "sku": sku,
        "plu": plu,
        "catalogProductInfo": cp,
    }


def menus_json(last_updated="2026-10-11T15:41:42.977+0000", tea_sku=GOOD2, rice_sku=GOOD):
    return {
        "lastUpdated": last_updated,
        "menus": [
            {
                "name": "RETAIL",
                "menuGroups": [
                    {
                        "name": "Grains",
                        "menuItems": [],
                        "menuGroups": [
                            {
                                "name": "Rice",
                                "menuItems": [
                                    _item(RICE, "Rice 20lb - Each", 17.99, sku=rice_sku, product="p1", option="Each"),
                                    _item(RICE_C, "Rice 20lb - Container", 0.0, product="p1", option="Container"),
                                    # A pair with only the name suffix (no catalog info).
                                    _item(DAL, "Dal 4lb - Each", 6.99, sku=GOOD),
                                    _item(DAL_C, "Dal 4lb - Container", 0.0),
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Tea",
                        "menuItems": [
                            _item(TEA, "Tea 1kg", 9.99, sku=tea_sku, plu="Some Vendor"),
                            _item(NEG, "Negative Thing", 2.49, sku=GOOD),
                            _item(NEWITEM, "Brand New Snack", 3.49, sku=""),
                        ],
                    },
                ],
            },
            {"name": "CONTAINER", "menuGroups": [{"name": "Back", "menuItems": [_item(RICE_C, "Rice 20lb - Container", 0.0, product="p1", option="Container")]}]},
            {
                "name": "MARKET KITCHEN",
                "menuGroups": [
                    {
                        "name": "Mains",
                        "menuItems": [
                            _item(UNTRACKED, "Paneer Wrap", 8.99, strategy="MENU_SPECIFIC_PRICE"),
                            _item(BIRYANI, "Chicken Biryani", None, strategy="SIZE_PRICE"),
                        ],
                    }
                ],
            },
            {"name": "MARKET KITCHEN (GH)", "menuGroups": [{"name": "Mains", "menuItems": [_item(UNTRACKED, "Paneer Wrap", 10.95, strategy="MENU_SPECIFIC_PRICE"), _item(GH_ONLY, "GH Only Dish", 5.0)]}]},
            {"name": "Test Menu", "menuGroups": [{"name": "X", "menuItems": [_item("test-thing", "Test", 1.0)]}]},
        ],
    }


def stock_json(rice=31.0, tea_status="QUANTITY", tea_qty=12.0):
    rows = [
        {"guid": RICE, "status": "QUANTITY", "quantity": rice},
        {"guid": RICE_C, "status": "QUANTITY", "quantity": 36.0},
        {"guid": DAL, "status": "QUANTITY", "quantity": 1.0},
        {"guid": DAL_C, "status": "QUANTITY", "quantity": 20.0},
        {"guid": NEG, "status": "OUT_OF_STOCK", "quantity": None},
        {"guid": NEWITEM, "status": "QUANTITY", "quantity": 24.0},
    ]
    if tea_status == "QUANTITY":
        rows.append({"guid": TEA, "status": "QUANTITY", "quantity": tea_qty})
    else:
        rows.append({"guid": TEA, "status": tea_status, "quantity": None})
    return rows


def csv_row(db, item_id, name, qty, day=CSV_DAY, price=4.99, supplier="Acme", group="RETAIL", category="Tea", sub=None, **extra):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=category,
            subcategory=sub,
            inventory_quantity=qty,
            price=price,
            cost=extra.pop("cost", 2.0),
            supplier=supplier,
            supplier_item_id=extra.pop("supplier_item_id", None),
            barcode=extra.pop("barcode", GOOD),
            last_received_from=extra.pop("last_received_from", "IN100"),
            inventory_last_received=extra.pop("inventory_last_received", "2026-10-01T10:00:00Z"),
            source_filename=extra.pop("source_filename", f"retail-items-{day}.csv"),
            **extra,
        )
    )


def sale(db, item_id, day, qty=1.0, net=None, modifier=False, voided=False):
    db.add(
        Order(
            toast_order_guid=str(uuid.uuid4()),
            toast_check_guid=str(uuid.uuid4()),
            toast_selection_guid=str(uuid.uuid4()),
            parent_selection_guid="parent" if modifier else None,
            business_date=day,
            item_guid=item_id,
            item_name=item_id,
            quantity=qty,
            net_price=net if net is not None else qty * 2.0,
            voided=voided,
        )
    )


@pytest.fixture
def store(db):
    """Yesterday's CSV upload for the catalog above."""
    csv_row(db, RICE, "Rice 20lb", 30, price=17.99, supplier="Swadesi;House of Spices", supplier_item_id="R1;R2", category="Grains", sub="Rice", cost=12.0)
    csv_row(db, RICE_C, "Rice 20lb", 36, price=None, supplier=None, category="Grains", sub="Rice", barcode=None)
    csv_row(db, DAL, "Dal 4lb", 1, price=6.99, category="Grains", sub="Rice")
    csv_row(db, DAL_C, "Dal 4lb", 20, price=None, supplier=None, category="Grains", sub="Rice", barcode=None)
    csv_row(db, TEA, "Tea 1kg", 14, price=9.99, barcode=f"{GOOD2};{GOOD}", cost=5.0)
    csv_row(db, NEG, "Negative Thing", -3, price=2.49)
    csv_row(db, UNTRACKED, "Paneer Wrap", None, price=None, supplier=None, group="MARKET KITCHEN", category="Mains", barcode=None)
    csv_row(db, BIRYANI, "Chicken Biryani", None, price=None, supplier=None, group="MARKET KITCHEN", category="Mains", barcode=None)
    for day in range(1, 95, 3):
        sale(db, TEA, TODAY - dt.timedelta(days=day), qty=1.0, net=9.99)
    sale(db, TEA, TODAY - dt.timedelta(days=1), qty=5.0, net=5 * 9.99, modifier=True)  # modifier: never counted
    sale(db, TEA, TODAY - dt.timedelta(days=2), qty=4.0, net=40.0, voided=True)  # voided: never counted
    sale(db, TEA, TODAY, qty=7.0, net=70.0)  # today: not in the windows yet
    db.commit()
    return db


class FakeToast:
    def __init__(self, menus=None, stock=None, last_updated="2026-10-11T15:41:42.977+0000"):
        self.menus = menus or menus_json()
        self.stock = stock or stock_json()
        self.last_updated = last_updated
        self.calls = []

    def __call__(self, path, timeout=90.0, max_tries=4):
        self.calls.append(path)
        if path == toast_stock.STOCK_PATH:
            return self.stock
        if path == toast_catalog.MENUS_PATH:
            return self.menus
        if path == toast_catalog.METADATA_PATH:
            return {"restaurantGuid": "x", "lastUpdated": self.last_updated}
        raise AssertionError(f"unexpected Toast path {path}")


@pytest.fixture
def toast(monkeypatch):
    fake = FakeToast()
    monkeypatch.setattr(toast_http, "toast_get", fake)
    return fake


# ---------------------------------------------------------------------------
# Building the rows
# ---------------------------------------------------------------------------


def test_build_rows_follow_the_csv_rules(store, toast):
    rows, meta = ia.build_api_rows(store, toast_stock.fetch_stock(), toast_catalog.fetch_menus(), TODAY)

    # Not inventory: Test Menu skipped, a GH-only dish is outside the CSV's groups.
    assert "test-thing" not in rows and GH_ONLY not in rows
    assert meta["stats"]["skipped_not_inventory"] == 1

    rice, rice_c = rows[RICE], rows[RICE_C]
    assert rice["name"] == rice_c["name"] == "Rice 20lb"  # variant -> product name
    assert rice["price"] == 17.99 and rice_c["price"] is None  # $0 Container -> blank
    assert (rice["category_group"], rice["category"], rice["subcategory"]) == ("RETAIL", "Grains", "Rice")
    assert rice["inventory_quantity"] == 31.0 and rice["inventory_status"] == "31 in stock"
    # Carried from the CSV.
    assert rice["supplier"] == "Swadesi;House of Spices" and rice["supplier_item_id"] == "R1;R2"
    assert rice["cost"] == 12.0 and rice["last_received_from"] == "IN100"
    assert rice["inventory_cost"] == 372.0 and rice["inventory_value"] == round(31 * 17.99, 2)
    assert rice["gross_margin"] == round((17.99 - 12.0) / 17.99 * 100, 2) and rice["gross_profit"] == round(17.99 - 12.0, 2)
    assert rice["source_filename"].startswith("api:") and rice["api_out_of_stock"] is None

    # Suffix-only pair (no catalog info) is a pair too.
    assert rows[DAL]["name"] == rows[DAL_C]["name"] == "Dal 4lb" and rows[DAL_C]["price"] is None

    # Barcode: sku is the first code of the CSV list -> keep the list.
    assert rows[TEA]["barcode"] == f"{GOOD2};{GOOD}"
    assert rows[NEWITEM]["barcode"] is None  # empty sku, no CSV = missing


    # Out of stock: 0, flagged, CSV count kept.
    neg = rows[NEG]
    assert neg["inventory_quantity"] == 0.0 and neg["api_out_of_stock"] is True and neg["csv_quantity"] == -3
    assert neg["inventory_status"] == "out of stock"
    assert ia.effective_quantity(InventorySnapshot(**{k: v for k, v in neg.items()})) == -3

    # Not count-tracked (no stock row): NULL, "in stock".
    assert rows[UNTRACKED]["inventory_quantity"] is None and rows[UNTRACKED]["inventory_status"] == "in stock"
    # Menu-specific price that differs between menus, and size pricing -> blank.
    assert rows[UNTRACKED]["price"] is None and rows[BIRYANI]["price"] is None

    # A new item with no CSV yet: menu data only, nothing carried.
    new = rows[NEWITEM]
    assert new["supplier"] is None and new["cost"] is None and new["category_group"] == "RETAIL"
    assert meta["stats"]["new_items_no_csv"] == 1


def test_sales_windows_are_top_level_non_voided_through_yesterday(store, toast):
    rows, meta = ia.build_api_rows(store, toast_stock.fetch_stock(), toast_catalog.fetch_menus(), TODAY)
    tea = rows[TEA]
    sold_days = [d for d in range(1, 95, 3)]
    for n in (7, 30, 90):
        expected = sum(1 for d in sold_days if d <= n)
        assert tea[f"last_{n}_day_orders"] == expected, n
        assert tea[f"last_{n}_day_sales"] == round(expected * 9.99, 2), n
    assert meta["sales_through"] == (TODAY - dt.timedelta(days=1)).isoformat()
    # Days on hand = qty / (90-day units / 90).
    units90 = tea["last_90_day_orders"]
    assert tea["inventory_days_on_hand"] == round(12.0 / (units90 / 90.0), 1)
    assert rows[NEG]["inventory_days_on_hand"] == 0.0


def test_csv_placement_is_kept_while_the_menu_still_has_it(store, toast):
    menus = menus_json()
    # Tea also listed somewhere deeper - the CSV's own placement still wins.
    menus["menus"][0]["menuGroups"][0]["menuGroups"][0]["menuItems"].append(_item(TEA, "Tea 1kg", 9.99, sku=GOOD2))
    rows, _ = ia.build_api_rows(store, toast_stock.fetch_stock(), menus, TODAY)
    assert (rows[TEA]["category"], rows[TEA]["subcategory"]) == ("Tea", None)


def test_format_status_matches_the_csv_wording():
    assert ia.format_status("QUANTITY", 932.0) == "932 in stock"
    assert ia.format_status("QUANTITY", 1210.0) == "1,210 in stock"
    assert ia.format_status("QUANTITY", 0.485) == "0.485 in stock"
    assert ia.format_status("OUT_OF_STOCK", 0.0) == "out of stock"
    assert ia.format_status(None, None) == "in stock"


def test_toast_time_without_colon_offset():
    assert toast_time.parse_toast_time("2026-10-11T15:41:42.977+0000") == dt.datetime(2026, 10, 11, 15, 41, 42, 977000, tzinfo=dt.timezone.utc)
    assert toast_time.parse_toast_time(None) is None and toast_time.parse_toast_time("junk") is None


# ---------------------------------------------------------------------------
# Storing: CSV always wins
# ---------------------------------------------------------------------------


def _api_rows_on(db, day):
    return db.query(InventorySnapshot).filter(InventorySnapshot.snapshot_date == day, InventorySnapshot.source_filename.like("api:%")).count()


def test_switch_off_writes_nothing_and_records_a_comparison(store, toast):
    csv_row(store, TEA, "Tea 1kg", 12, day=TODAY, price=9.99, barcode=f"{GOOD2};{GOOD}")
    store.commit()
    job = ia.run_api_snapshot(store)
    assert job.status == "success" and _api_rows_on(store, TODAY) == 0
    detail = json.loads(job.detail)
    assert detail["stored"] == {"written": 0, "skipped": "csv_exists"}
    qty = next(f for f in detail["comparison"]["fields"] if f["field"] == "inventory_quantity")
    assert qty["compared"] == 1 and qty["same"] == 1
    assert ia.last_nightly_comparison(store)["comparison"]["items_in_both"] == 1
    # And the menu cache was refreshed from the same pull.
    assert store.query(ToastMenuItem).count() > 0


def test_switch_off_without_a_csv_today(store, toast):
    job = ia.run_api_snapshot(store)
    assert json.loads(job.detail)["stored"] == {"written": 0, "skipped": "switch_off"}
    assert _api_rows_on(store, TODAY) == 0


def test_switch_on_writes_the_snapshot_and_reports_use_it(store, toast, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    monkeypatch.setattr(ia, "_after_new_snapshot", lambda: None)
    job = ia.run_api_snapshot(store)
    assert job.status == "success", job.detail
    assert _api_rows_on(store, TODAY) == 9
    latest = latest_inventory_by_item(store)
    assert latest[RICE].snapshot_date == TODAY and latest[RICE].inventory_quantity == 31.0
    # Negative inventory keeps the CSV's -3 for the out-of-stock item.
    neg = {r["item_id"]: r for r in get_negative_inventory(store)}
    assert neg[NEG]["on_hand_qty"] == -3
    # Running again replaces this date's API rows, never duplicates them.
    ia.run_api_snapshot(store)
    assert _api_rows_on(store, TODAY) == 9


def test_never_writes_over_a_csv_of_the_same_date(store, toast, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    csv_row(store, TEA, "Tea 1kg", 99, day=TODAY)
    store.commit()
    rows, _ = ia.build_api_rows(store, toast_stock.fetch_stock(), toast_catalog.fetch_menus(), TODAY)
    assert ia.store_api_snapshot(store, rows, TODAY) == {"written": 0, "skipped": "csv_exists"}
    assert _api_rows_on(store, TODAY) == 0
    assert latest_inventory_by_item(store)[TEA].inventory_quantity == 99


def test_a_csv_upload_replaces_that_dates_api_rows(store, toast):
    rows, _ = ia.build_api_rows(store, toast_stock.fetch_stock(), toast_catalog.fetch_menus(), TODAY)
    ia.store_api_snapshot(store, rows, TODAY)
    assert _api_rows_on(store, TODAY) == 9
    csv = "Item ID,Name,Inventory Quantity,Price\ntea,Tea 1kg,5,9.99\n".encode()
    result = parse_and_store(store, "retail-items.csv", csv, TODAY)
    assert result["api_rows_replaced"] == 9
    left = store.query(InventorySnapshot).filter(InventorySnapshot.snapshot_date == TODAY).all()
    assert [(r.item_id, r.inventory_quantity, r.api_out_of_stock) for r in left] == [(TEA, 5.0, None)]


def test_a_failed_pull_is_recorded_and_writes_nothing(store, monkeypatch):
    def boom(path, timeout=90.0, max_tries=4):
        raise toast_http.ToastFetchError("Toast /stock/v1/inventory returned HTTP 500")

    monkeypatch.setattr(toast_http, "toast_get", boom)
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    job = ia.run_api_snapshot(store)
    assert job.status == "failed" and "HTTP 500" in job.detail
    assert _api_rows_on(store, TODAY) == 0


def test_negative_close_is_not_cleared_by_an_out_of_stock_api_row(store, toast, monkeypatch):
    """A negative-inventory close covers one occurrence. An API row at 0 only
    because Toast said "out of stock" (CSV still negative) is not "cleared"."""
    monkeypatch.setattr(ia, "_after_new_snapshot", lambda: None)
    rows, _ = ia.build_api_rows(store, toast_stock.fetch_stock(), toast_catalog.fetch_menus(), TODAY)
    ia.store_api_snapshot(store, rows, TODAY)
    pending = {NEG: ({"item_id": NEG}, CSV_DAY - dt.timedelta(days=1))}
    assert stock_issues.negative_cleared_since(store, pending) == set()


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_compare_counts_and_examples(store, toast):
    rows, _ = ia.build_api_rows(store, toast_stock.fetch_stock(), toast_catalog.fetch_menus(), TODAY)
    csv = ia.csv_rows_for(store, CSV_DAY)
    out = ia.compare_rows(rows, csv, CSV_DAY)
    assert out["items_in_both"] == 8 and out["only_in_api"] == 1 and out["only_in_csv"] == 0
    assert out["only_in_api_examples"] == [{"item_id": NEWITEM, "name": "Brand New Snack"}]
    assert out["out_of_stock_with_negative_csv"] == 1
    by = {f["field"]: f for f in out["fields"]}
    qty = by["inventory_quantity"]
    # Rice 30 -> 31 and Tea 14 -> 12 differ; NEG (OOS vs -3) agrees.
    assert qty["differ"] == 2 and {e["item_id"] for e in qty["examples"]} == {RICE, TEA}
    assert by["name"]["same"] == 8 and by["price"]["same"] == 8 and by["barcode"]["same"] == 8
    assert "within_5pct" in by["last_30_day_orders"]


def test_compare_now_is_cached_and_never_writes(store, toast):
    first = ia.compare_now(store)
    calls = list(toast.calls)
    again = ia.compare_now(store)
    assert again is first and toast.calls == calls  # cached
    ia.compare_now(store, refresh=True)
    assert len(toast.calls) == 2 * len(calls)
    assert _api_rows_on(store, TODAY) == 0


# ---------------------------------------------------------------------------
# Item 6: hourly barcode re-check
# ---------------------------------------------------------------------------


# The barcode close / menu-change tests use fixed Oct 11 times, so their CSV
# upload must be fixed too (the day before), not "yesterday": once the real
# date passed Oct 12 a CSV_DAY upload was newer than the menu change and the
# tests changed meaning.
BARCODE_CSV_DAY = dt.date(2026, 10, 10)


def _barcode_store(db):
    csv_row(db, TEA, "Tea 1kg", 14, day=BARCODE_CSV_DAY, barcode=None)  # missing in the CSV
    csv_row(db, RICE, "Rice 20lb", 30, day=BARCODE_CSV_DAY, barcode=BAD)  # invalid in the CSV
    db.commit()


def test_menu_recheck_only_pulls_the_menu_when_it_changed(db, toast):
    _barcode_store(db)
    job = ia.refresh_menu_cache(db)
    assert json.loads(job.detail)["changed"] is True and toast.calls == [toast_catalog.METADATA_PATH, toast_catalog.MENUS_PATH]
    job = ia.refresh_menu_cache(db)
    assert json.loads(job.detail)["changed"] is False and toast.calls[-1] == toast_catalog.METADATA_PATH
    toast.last_updated = "2026-10-11T19:00:00.000+0000"
    toast.menus = menus_json(last_updated=toast.last_updated)
    ia.refresh_menu_cache(db)
    assert toast.calls[-1] == toast_catalog.MENUS_PATH


def test_fixed_barcodes_leave_the_lists_after_the_menu_check(db, toast):
    _barcode_store(db)
    assert {r["item_id"] for r in get_missing_barcodes(db)} == {TEA}
    assert {r["item_id"] for r in get_invalid_barcodes(db)} == {RICE}
    ia.refresh_menu_cache(db)  # menu now has GOOD2 for tea and GOOD for rice
    assert get_missing_barcodes(db) == []
    assert get_invalid_barcodes(db) == []


def test_menu_check_older_than_the_upload_is_ignored(db, toast):
    _barcode_store(db)
    ia.refresh_menu_cache(db)
    # A newer CSV upload after the check: the upload is the fresher source.
    csv_row(db, TEA, "Tea 1kg", 14, day=TODAY, barcode=None)
    db.commit()
    assert {r["item_id"] for r in get_missing_barcodes(db)} == {TEA}


def test_recheck_switch_off_ignores_the_menu(db, toast, monkeypatch):
    _barcode_store(db)
    ia.refresh_menu_cache(db)
    monkeypatch.setattr(get_settings(), "menu_barcode_recheck_minutes", 0)
    assert {r["item_id"] for r in get_missing_barcodes(db)} == {TEA}


def _close_fixed(db, issue_type, item_id, at):
    db.add(
        IssueResolution(
            issue_type=issue_type, issue_key=item_id, item_id=item_id, item_name=item_id,
            reason="fixed", closed_by_name="boss", closed_at=at,
        )
    )
    db.commit()


def test_fixed_close_comes_back_when_the_newer_menu_still_shows_it(db, toast):
    _barcode_store(db)
    # Closed "Barcode added in Toast" an hour before the menu's last change...
    _close_fixed(db, "missing_barcode", TEA, dt.datetime(2026, 10, 11, 18, 0, tzinfo=dt.timezone.utc))
    toast.last_updated = "2026-10-11T19:00:00.000+0000"
    toast.menus = menus_json(last_updated=toast.last_updated, tea_sku="")  # ...but still no barcode
    ia.refresh_menu_cache(db)
    data = get_issue_list(db, "missing_barcode", today=dt.date(2026, 10, 11))
    row = next(r for r in data["open"] if r["item_id"] == TEA)
    assert row["came_back"] and row["reopened"]["how"] == "fixed_but_still_showing"
    assert row["reopened"]["at"].startswith("2026-10-11T19:00")
    assert data["menu_checked_at"].startswith("2026-10-11T19:00")


def test_fixed_close_is_confirmed_when_the_menu_has_the_barcode(db, toast):
    _barcode_store(db)
    _close_fixed(db, "missing_barcode", TEA, dt.datetime(2026, 10, 11, 18, 0, tzinfo=dt.timezone.utc))
    toast.last_updated = "2026-10-11T19:00:00.000+0000"
    toast.menus = menus_json(last_updated=toast.last_updated)
    ia.refresh_menu_cache(db)
    data = get_issue_list(db, "missing_barcode", today=dt.date(2026, 10, 11))
    assert data["open"] == []
    closed = next(r for r in data["closed"] if r["issue_key"] == TEA)
    assert closed["no_longer_detected"] and closed["fix_confirmed"] is True


def test_close_after_the_menus_last_change_stays_closed(db, toast):
    _barcode_store(db)
    toast.menus = menus_json(tea_sku="")
    ia.refresh_menu_cache(db)  # menu lastUpdated 15:41 UTC
    _close_fixed(db, "missing_barcode", TEA, dt.datetime(2026, 10, 11, 18, 0, tzinfo=dt.timezone.utc))
    data = get_issue_list(db, "missing_barcode", today=dt.date(2026, 10, 11))
    assert all(r["item_id"] != TEA for r in data["open"])


# ---------------------------------------------------------------------------
# Live stock overlay
# ---------------------------------------------------------------------------


def test_live_overlay_off_by_default(store, toast):
    items = compute_supplier_projection(store, "Acme", 30, [7])
    tea = next(i for i in items if i["item_id"] == TEA)
    assert tea["on_hand_qty"] == 14  # the CSV
    assert toast_stock.STOCK_PATH not in toast.calls


def test_live_overlay_on_uses_toast_counts_and_caches(store, toast, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    items = compute_supplier_projection(store, "Acme", 30, [7])
    tea = next(i for i in items if i["item_id"] == TEA)
    assert tea["on_hand_qty"] == 12.0
    neg = {r["item_id"]: r for r in get_negative_inventory(store)}
    assert neg[NEG]["on_hand_qty"] == -3  # OUT_OF_STOCK keeps a known negative count
    compute_supplier_projection(store, "Acme", 30, [7])
    assert toast.calls.count(toast_stock.STOCK_PATH) == 1  # cached
    meta = ia.live_stock_meta()
    assert meta["live"] is True and meta["as_of"]


def test_live_received_item_leaves_negative_inventory(store, toast, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    toast.stock = stock_json() + [{"guid": NEG, "status": "QUANTITY", "quantity": 5.0}]
    toast.stock = [r for r in toast.stock if not (r["guid"] == NEG and r["status"] == "OUT_OF_STOCK")]
    assert all(r["item_id"] != NEG for r in get_negative_inventory(store))


def test_live_overlay_falls_back_when_toast_fails(store, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)

    def boom(path, timeout=90.0, max_tries=4):
        raise toast_http.ToastFetchError("down")

    monkeypatch.setattr(toast_http, "toast_get", boom)
    items = compute_supplier_projection(store, "Acme", 30, [7])
    assert next(i for i in items if i["item_id"] == TEA)["on_hand_qty"] == 14


def test_live_quantity_rules():
    snap = InventorySnapshot(item_id="x", inventory_quantity=-2.0)
    assert ia.live_quantity(snap, "OUT_OF_STOCK", None) == -2.0
    assert ia.live_quantity(InventorySnapshot(item_id="x", inventory_quantity=4.0), "OUT_OF_STOCK", None) == 0.0
    assert ia.live_quantity(snap, "QUANTITY", 6) == 6.0
    flagged = InventorySnapshot(item_id="x", inventory_quantity=0.0, api_out_of_stock=True, csv_quantity=-5.0)
    assert ia.live_quantity(flagged, "OUT_OF_STOCK", None) == -5.0
    # A live count on top of a flagged row wins over the carried CSV count.
    view = ia.LiveStockSnapshot(flagged, 3.0, "3 in stock", None)
    assert ia.effective_quantity(view) == 3.0
    with pytest.raises(AttributeError):
        view.inventory_quantity = 1


def test_reorder_and_projection_responses_carry_live_stock(admin_client, store, toast, monkeypatch):
    r = admin_client.get("/api/ordering/reorder-candidates")
    assert r.status_code == 200 and r.json()["live_stock"] is None
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    r = admin_client.get("/api/ordering/supplier-projection", params={"supplier": "Acme"})
    assert r.status_code == 200 and r.json()["live_stock"]["live"] is True


# ---------------------------------------------------------------------------
# Upload-due alert
# ---------------------------------------------------------------------------


def test_upload_due_alert(db):
    now = dt.datetime.combine(TODAY, dt.time(9, 0))
    assert "inventory_upload_due" in TRIGGERS and ALERTS_BY_KEY["inventory_upload_due"]["features"] == ["overview"]
    alerts = TRIGGERS["inventory_upload_due"](db, now)
    assert len(alerts) == 1 and "No inventory file" in alerts[0].body
    csv_row(db, TEA, "Tea", 1, day=TODAY - dt.timedelta(days=7))
    db.commit()
    alerts = TRIGGERS["inventory_upload_due"](db, now)
    assert len(alerts) == 1 and "7 days ago" in alerts[0].body
    assert alerts[0].dedupe_keys == [f"inventory_upload_due:{TODAY.isoformat()}"]
    csv_row(db, TEA, "Tea", 1, day=TODAY - dt.timedelta(days=6))
    db.commit()
    assert TRIGGERS["inventory_upload_due"](db, now) == []


def test_api_and_backfill_rows_do_not_count_as_an_upload(db):
    csv_row(db, TEA, "Tea", 1, day=TODAY, source_filename="api:2026-10-11T08:05:00Z")
    csv_row(db, RICE, "Rice", 1, day=TODAY - dt.timedelta(days=1), source_filename="backfill:INV.csv")
    csv_row(db, DAL, "Dal", 1, day=TODAY - dt.timedelta(days=9))
    db.commit()
    assert ia.upload_due(db)["age_days"] == 9


# ---------------------------------------------------------------------------
# Admin API + Toast GET helper
# ---------------------------------------------------------------------------


def test_admin_routes(admin_client, staff_client, store, toast):
    assert staff_client.get("/api/inventory-api/status").status_code == 403
    st = admin_client.get("/api/inventory-api/status").json()
    assert st["settings"]["inventory_api_snapshots"] is False and st["latest_csv_date"] == CSV_DAY.isoformat()
    assert st["live_stock_screens"]
    cmp = admin_client.get("/api/inventory-api/compare").json()
    assert cmp["comparison"]["items_in_both"] == 8
    assert admin_client.get("/api/inventory-api/compare/nightly").json() == {"nightly": None}
    assert admin_client.post("/api/inventory-api/menu-recheck").status_code == 200
    assert admin_client.post("/api/inventory-api/menu-recheck").status_code == 429  # too soon


def test_compare_route_reports_toast_trouble(admin_client, store, monkeypatch):
    def boom(path, timeout=90.0, max_tries=4):
        raise toast_http.ToastFetchError("Toast /stock/v1/inventory returned HTTP 503")

    monkeypatch.setattr(toast_http, "toast_get", boom)
    r = admin_client.get("/api/inventory-api/compare")
    assert r.status_code == 503


def test_toast_get_backs_off_on_429_and_only_gets(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id")
    monkeypatch.setattr(s, "toast_client_secret", "secret")
    monkeypatch.setattr(s, "toast_restaurant_guid", "guid")
    monkeypatch.setattr("app.pos.adapters.toast.client.get_access_token", lambda client: "tok")
    seen, sleeps = [], []
    responses = [
        httpx.Response(429, headers={"Retry-After": "3"}, request=httpx.Request("GET", "https://x")),
        httpx.Response(200, json=[{"guid": "a"}], request=httpx.Request("GET", "https://x")),
    ]

    def fake_get(self, url, headers=None, timeout=None):
        seen.append(url)
        return responses.pop(0)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    monkeypatch.setattr(toast_http.time, "sleep", lambda s: sleeps.append(s))
    assert toast_http.toast_get(toast_stock.STOCK_PATH) == [{"guid": "a"}]
    assert sleeps == [3.0] and len(seen) == 2 and all(u.endswith(toast_stock.STOCK_PATH) for u in seen)


def test_toast_get_without_credentials(monkeypatch):
    monkeypatch.setattr(get_settings(), "toast_client_id", "")
    with pytest.raises(toast_http.ToastFetchError):
        toast_http.toast_get(toast_stock.STOCK_PATH)


def test_merged_barcode_rules():
    assert toast_catalog.merged_barcode(GOOD, f"{GOOD2};{GOOD}") == f"{GOOD2};{GOOD}"  # sku in the list: keep the list
    assert toast_catalog.merged_barcode(GOOD, BAD) == GOOD  # corrected in Toast
    assert toast_catalog.merged_barcode(GOOD, None) == GOOD  # added in Toast
    assert toast_catalog.merged_barcode("", GOOD) == GOOD  # an empty sku never wipes a barcode
    assert toast_catalog.merged_barcode(None, None) is None
