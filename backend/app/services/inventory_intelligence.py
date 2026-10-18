"""Phase 2 - Inventory intelligence: dead stock / slow-moving detection and
negative inventory. (The old gross margin report was removed on request.)

Both are pure queries over fields Toast already computes and hands back in
the daily "retail items" export (`inventory_days_on_hand`,
`last_90_day_sales`, `inventory_value`) - no new data
collection, no new forecasting logic, just surfacing numbers that already
exist in the latest `inventory_snapshots` row per item.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models import InventorySnapshot
from app.services.inventory_api import effective_quantity, overlay_live_stock
from app.services.reorder import latest_inventory_by_item

# An item sitting on the shelf for longer than this at its current sell-through
# rate is "slow-moving" - a common retail rule of thumb, not something Toast
# defines itself. Adjustable; there's nothing sacred about 90.
SLOW_MOVING_DAYS_THRESHOLD = 90


def get_dead_stock(db: Session) -> list[dict]:
    """Items with real on-hand inventory that either haven't sold at all in
    90 days ("dead") or would take an excessively long time to sell through
    at the current pace ("slow") - sorted by dollars tied up (inventory_value)
    descending, since that's the number that actually makes one worth acting
    on over another.
    """
    latest = latest_inventory_by_item(db)

    results = []
    for item_id, snap in latest.items():
        on_hand = snap.inventory_quantity
        if on_hand is None or on_hand <= 0:
            continue  # nothing on hand - out of stock, not "dead stock"

        sales_90d = snap.last_90_day_sales or 0
        days_on_hand = snap.inventory_days_on_hand

        is_dead = sales_90d <= 0
        is_slow = (
            not is_dead
            and days_on_hand is not None
            and days_on_hand >= SLOW_MOVING_DAYS_THRESHOLD
        )
        if not (is_dead or is_slow):
            continue

        results.append(
            {
                "item_id": item_id,
                "name": snap.name,
                "category": snap.category,
                "supplier": snap.supplier,
                "on_hand_qty": on_hand,
                "days_on_hand": days_on_hand,
                "last_90_day_sales": round(sales_90d, 2),
                "inventory_value": round(snap.inventory_value, 2) if snap.inventory_value is not None else None,
                "status": "Dead" if is_dead else "Slow",
                "inventory_snapshot_date": snap.snapshot_date.isoformat(),
            }
        )

    results.sort(key=lambda r: r["inventory_value"] or 0, reverse=True)
    return results


def get_negative_inventory(db: Session) -> list[dict]:
    """Items whose on-hand quantity is below zero in the latest upload - not
    physically possible (a shelf can't hold less than nothing), so each one
    is a real POS/counting discrepancy worth a physical recount, not a
    genuine stock level. Sorted by category/subcategory (grouped, per the
    original ask this report was built from) then most-negative first within
    each group, since that's the most likely to be a real miscount rather
    than off-by-one noise.
    """
    # Live Toast counts when INVENTORY_API_SNAPSHOTS is on (a received item
    # leaves the list straight away); an API "out of stock" row keeps the
    # latest CSV's negative count (inventory_api.effective_quantity).
    latest = overlay_live_stock(latest_inventory_by_item(db))

    results = []
    for item_id, snap in latest.items():
        on_hand = effective_quantity(snap)
        if on_hand is None or on_hand >= 0:
            continue

        results.append(
            {
                "item_id": item_id,
                "name": snap.name,
                "category": snap.category,
                "subcategory": snap.subcategory,
                "on_hand_qty": on_hand,
                "cost": snap.cost,
                "price": snap.price,
                "inventory_snapshot_date": snap.snapshot_date.isoformat(),
            }
        )

    results.sort(key=lambda r: (r["category"] or "zzz", r["subcategory"] or "zzz", r["on_hand_qty"]))
    return results
