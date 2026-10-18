"""Store vs Container report (owner, Oct 1: "shows only products present in
both store and container and their respective stock").

An item is listed only when the latest upload has both its store (priced)
row and its bare Container-location row. The pairing is
supplier_projection.store_container_pairs - the same one Fix Issues' Refill
from container and Location Transfer use, and the same container_qty
Supplier Projection adds to on-hand - so the numbers agree everywhere.
Categories are the effective (corrected) ones; kitchen groups are left out
like on Fix Issues. Items with a store count of 0 or below are kept.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.services.csv_util import rows_to_csv
from app.services.fix_issues import EXCLUDED_CATEGORY_GROUPS
from app.services.reorder import latest_inventory_by_item
from app.services.stock_issues import REFILL_STORE_MAX_QTY
from app.services.supplier_projection import _split_suppliers, store_container_pairs

# CSV column -> row field. Every row gets every column (rows_to_csv takes the
# header from the first row), and an empty report still gets the header.
EXPORT_COLUMNS = [
    ("Item", "name"),
    ("Vendor", "supplier"),
    ("Vendor Item Code", "supplier_item_id"),
    ("Category Group", "category_group"),
    ("Category", "category"),
    ("Subcategory", "subcategory"),
    ("Store Qty", "store_qty"),
    ("Container Qty", "container_qty"),
    ("Total Qty", "total_qty"),
    ("Price", "price"),
    ("Low On Shelf", "low_store"),
]


def _num(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _code(raw: Optional[str]) -> Optional[str]:
    """Toast's vendor item code(s) as exported, but None instead of bare
    separators (";;" when none of an item's vendors has a code)."""
    return raw if raw and raw.replace(";", "").strip() else None


def is_low_store(store_qty: Optional[float], container_qty: Optional[float]) -> bool:
    """Shelf at REFILL_STORE_MAX_QTY or less while the Container has stock -
    the Fix Issues "Refill from container" rule."""
    return (
        store_qty is not None
        and container_qty is not None
        and store_qty <= REFILL_STORE_MAX_QTY
        and container_qty > 0
    )


def _matches(row: dict, barcode: Optional[str], needle: str, all_codes: Optional[str] = None) -> bool:
    fields = (row["name"], row["supplier"], row["supplier_item_id"], row["category"], row["subcategory"], barcode)
    if any(needle in (f or "").lower() for f in fields):
        return True
    # W2: the Toast item id (store or Container row) and every vendor's SKU.
    from app.services.product_match import match_code

    return any(
        match_code(needle, item_id=item_id, supplier_item_ids=all_codes, barcodes=barcode)
        for item_id in (row.get("item_id"), row.get("container_item_id"))
    )


def get_store_vs_container(db: Session, search: Optional[str] = None, vendor: Optional[str] = None) -> dict:
    latest = latest_inventory_by_item(db)
    snapshot_date = max((s.snapshot_date for s in latest.values()), default=None)
    needle = (search or "").strip().lower()
    vendor_key = (vendor or "").strip().lower()

    items = []
    vendors = set()
    for store_id, snap, container_id, container_qty in store_container_pairs(latest):
        if (snap.category_group or "").upper() in EXCLUDED_CATEGORY_GROUPS:
            continue
        suppliers = _split_suppliers(snap.supplier)
        vendors.update(suppliers)
        store_qty = _num(snap.inventory_quantity)
        container = _num(container_qty)
        row = {
            "item_id": store_id,
            "name": snap.name,
            "supplier": snap.supplier,
            "supplier_item_id": _code(snap.supplier_item_id),
            "category_group": snap.category_group,
            "category": snap.category,
            "subcategory": snap.subcategory,
            "store_qty": store_qty,
            "container_qty": container,
            "total_qty": _num((store_qty or 0.0) + (container or 0.0)),
            "price": _num(snap.price),
            "low_store": is_low_store(store_qty, container),
            "store_item_id": store_id,
            "container_item_id": container_id,
            # Additive (W2): every vendor SKU + the barcode, for search.
            "all_supplier_item_ids": snap.supplier_item_id,
            "barcode": snap.barcode,
        }
        if vendor_key and vendor_key not in {s.lower() for s in suppliers}:
            continue
        if needle and not _matches(row, snap.barcode, needle, snap.supplier_item_id):
            continue
        items.append(row)

    items.sort(key=lambda r: (not r["low_store"], (r["name"] or "").lower(), r["item_id"]))
    return {
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "items": items,
        "totals": {
            "items": len(items),
            "store_qty": _num(sum(r["store_qty"] or 0.0 for r in items)),
            "container_qty": _num(sum(r["container_qty"] or 0.0 for r in items)),
        },
        # Additive: every vendor with a paired item (ignores the filters), for
        # the vendor picker.
        "vendors": sorted(vendors, key=str.lower),
    }


def _csv_value(field: str, value):
    if field == "low_store":
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer() and field != "price":
        return int(value)  # quantities: "20", not "20.0"
    return value


def export_csv(items: list[dict]) -> str:
    header = [label for label, _ in EXPORT_COLUMNS]
    if not items:
        return rows_to_csv([{h: None for h in header}]).splitlines()[0] + "\n"
    return rows_to_csv([{label: _csv_value(f, item.get(f)) for label, f in EXPORT_COLUMNS} for item in items])
