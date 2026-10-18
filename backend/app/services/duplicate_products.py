"""Possible duplicate product records in the latest inventory snapshot.

Most items sharing an exact name are NOT a data-entry mistake - they're the
Container-location split already understood and handled by
supplier_projection.py's `_container_qty_by_name` (one real priced/sellable
row plus a second bare row tracking on-hand count in a different physical
storage location - a real, confirmed quirk of the Toast Retail export, not a
defect). This report deliberately excludes those names, since flagging them
here would just be re-surfacing a known-fine pattern as if it were a
problem. What's left - two or more real PRICED rows sharing one name - is
what's actually worth a human's second look.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.services.reorder import latest_inventory_by_item
from app.services.supplier_projection import _container_qty_by_name


def get_duplicate_products(db: Session) -> list[dict]:
    latest = latest_inventory_by_item(db)
    container_names = set(_container_qty_by_name(latest).keys())

    by_name: dict[str, list] = defaultdict(list)
    for snap in latest.values():
        if snap.name:
            by_name[snap.name].append(snap)

    results = []
    for name, snaps in by_name.items():
        if name in container_names:
            continue  # a known, legitimate Container-location split
        priced = [s for s in snaps if s.price is not None]
        if len(priced) < 2:
            continue  # only one (or zero) real priced row - not a duplicate

        for snap in priced:
            results.append(
                {
                    "item_id": snap.item_id,
                    "name": snap.name,
                    "category": snap.category,
                    "supplier": snap.supplier,
                    "price": snap.price,
                    "on_hand_qty": snap.inventory_quantity,
                    "inventory_snapshot_date": snap.snapshot_date.isoformat(),
                }
            )

    results.sort(key=lambda r: (r["name"] or "", r["item_id"]))
    return results
