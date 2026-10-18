"""Vendor Price Comparison: for items where a real, vendor-attributed cost
has been captured (see delivery_review.py's confirm_delivery, or a manually
logged purchase with a unit cost entered by hand), flags cases where the
vendor you most recently bought an item from costs more than a different
vendor already on file for that same item.

This exists specifically because `inventory_snapshots.cost` alone can't
answer "which vendor is cheaper" - it's one blended cost per item, and an
item commonly lists several possible suppliers at once (semicolon-separated,
see gotcha #6), with no way to tell which one that cost actually belongs to.
`purchase_log` sidesteps that ambiguity: every entry names one specific
vendor a human actually confirmed, so its `unit_cost` is unambiguous. This
only ever surfaces real numbers a human confirmed or captured this way -
nothing here is estimated or fabricated.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PurchaseLogEntry
from app.services.reorder import latest_inventory_by_item


def get_vendor_price_comparison(db: Session) -> list[dict]:
    entries = list(
        db.execute(
            select(PurchaseLogEntry).where(
                PurchaseLogEntry.unit_cost.isnot(None),
                PurchaseLogEntry.supplier.isnot(None),
            )
        ).scalars()
    )

    by_item: dict[str, list[PurchaseLogEntry]] = defaultdict(list)
    for entry in entries:
        by_item[entry.item_id].append(entry)

    latest = latest_inventory_by_item(db)

    results = []
    for item_id, item_entries in by_item.items():
        vendors = {e.supplier for e in item_entries}
        if len(vendors) < 2:
            continue  # need cost from at least two different vendors to compare

        current = max(item_entries, key=lambda e: (e.received_date, e.id))
        others = [e for e in item_entries if e.supplier != current.supplier]
        cheapest_other = min(others, key=lambda e: e.unit_cost)

        if cheapest_other.unit_cost >= current.unit_cost:
            continue  # current vendor is already the cheapest (or tied) - nothing to flag

        snap = latest.get(item_id)
        results.append(
            {
                "item_id": item_id,
                "name": current.item_name or (snap.name if snap else item_id),
                "category": snap.category if snap else None,
                "current_vendor": current.supplier,
                "current_cost": round(current.unit_cost, 2),
                "current_date": current.received_date.isoformat(),
                "cheaper_vendor": cheapest_other.supplier,
                "cheaper_cost": round(cheapest_other.unit_cost, 2),
                "cheaper_date": cheapest_other.received_date.isoformat(),
                "potential_savings": round(current.unit_cost - cheapest_other.unit_cost, 2),
            }
        )

    results.sort(key=lambda r: r["potential_savings"], reverse=True)
    return results
