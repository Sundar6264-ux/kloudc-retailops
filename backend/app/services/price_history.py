"""Price-change tracking across daily inventory snapshots.

`inventory_snapshots` has one row per item per upload date, so an item's price
history is just its rows ordered by date. This only has as much history as
there have been uploads - a single-snapshot database (e.g. day one) will
correctly report zero changes, not an error.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot
from app.services.category_overrides import load_rules, overridden_categories


def _snapshots_by_item(db: Session) -> dict[str, list]:
    # Selects only the columns used below (plain rows, not ORM objects) - loading
    # full InventorySnapshot objects for every row took ~15s once history was
    # backfilled to ~500k rows. Rows keep the same attribute names.
    stmt = select(
        InventorySnapshot.item_id,
        InventorySnapshot.snapshot_date,
        InventorySnapshot.price,
        InventorySnapshot.cost,
        InventorySnapshot.name,
        InventorySnapshot.category,
        InventorySnapshot.barcode,
    ).order_by(InventorySnapshot.item_id, InventorySnapshot.snapshot_date)
    by_item: dict[str, list] = {}
    for row in db.execute(stmt):
        by_item.setdefault(row.item_id, []).append(row)
    return by_item


def _category_by_item(db: Session) -> dict:
    """{item_id: category} for items whose category is corrected app-side
    (services/category_overrides.py) - shown instead of the snapshot's.
    Matched on each item's supplier and Toast categories in the latest upload (a column-only
    query, and skipped entirely when no rule is active)."""
    if not load_rules(db):
        return {}
    latest_date = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar_one_or_none()
    if latest_date is None:
        return {}
    rows = db.execute(
        select(
            InventorySnapshot.item_id,
            InventorySnapshot.supplier,
            InventorySnapshot.category_group,
            InventorySnapshot.category,
            InventorySnapshot.subcategory,
        ).where(InventorySnapshot.snapshot_date == latest_date)
    )
    # Toast's own categories too, for category mapping rules.
    matched = overridden_categories(
        db,
        {row.item_id: (row.supplier, row.category_group, row.category, row.subcategory) for row in rows},
    )
    return {item_id: category for item_id, (_group, category, _sub) in matched.items()}


def get_price_changed_items(db: Session) -> list[dict]:
    """Only items with at least one detected price change between consecutive
    snapshots - not the whole catalog.
    """
    by_item = _snapshots_by_item(db)
    overridden = _category_by_item(db)
    results = []

    for item_id, snaps in by_item.items():
        change_count = 0
        last_change = None
        prev = None
        for snap in snaps:
            if prev is not None and prev.price is not None and snap.price is not None and snap.price != prev.price:
                change_count += 1
                last_change = {"date": snap.snapshot_date.isoformat(), "old_price": prev.price, "new_price": snap.price}
            prev = snap

        if change_count == 0:
            continue

        latest = snaps[-1]
        results.append(
            {
                "item_id": item_id,
                "name": latest.name,
                "category": overridden.get(item_id, latest.category),
                "barcode": latest.barcode,
                "current_price": latest.price,
                "previous_price": last_change["old_price"],
                "last_change_date": last_change["date"],
                "change_count": change_count,
            }
        )

    results.sort(key=lambda r: r["last_change_date"], reverse=True)
    return results


def search_price_history(db: Session, query: str) -> list[dict]:
    """Items whose latest name or barcode matches `query` (case-insensitive
    substring) - or, since Oct 14, its Toast item id / a vendor SKU by the
    product_match rule - each with its full price/cost timeline across every snapshot
    date on file - regardless of whether the price ever actually changed,
    since a flat history ("never changed") is itself a useful answer here.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    by_item = _snapshots_by_item(db)
    overridden = _category_by_item(db)
    # Vendor SKUs come from the latest upload (the history loader stays
    # column-only - see _snapshots_by_item).
    from app.services.product_match import match_code
    from app.services.reorder import latest_inventory_by_item

    current = latest_inventory_by_item(db)
    matches = []
    for item_id, snaps in by_item.items():
        latest = snaps[-1]
        name_match = bool(latest.name) and needle in latest.name.lower()
        barcode_match = bool(latest.barcode) and needle in latest.barcode.lower()
        # W2: also the Toast item id and any vendor's SKU (and a barcode
        # without its leading zeros) - services/product_match.py.
        code_match = None
        if not (name_match or barcode_match):
            now = current.get(item_id)
            code_match = match_code(
                query,
                item_id=item_id,
                supplier_item_ids=getattr(now, "supplier_item_id", None),
                barcodes=latest.barcode,
            )
        if not (name_match or barcode_match or code_match):
            continue

        matches.append(
            {
                "item_id": item_id,
                "name": latest.name,
                "barcode": latest.barcode,
                "category": overridden.get(item_id, latest.category),
                "history": [
                    {"date": s.snapshot_date.isoformat(), "price": s.price, "cost": s.cost} for s in snaps
                ],
            }
        )

    matches.sort(key=lambda m: m["name"] or "")
    return matches
