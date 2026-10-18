"""Location Transfer Review: after a daily inventory upload, lets the user
flag that stock was physically moved between an item's two inventory_snapshots
rows - the priced/sellable "Each" row and its bare "Container" storage-
location duplicate (see supplier_projection.py's `_container_qty_by_name`) -
then review a computer-suggested list of items that likely moved, in the
chosen direction, and confirm real quantities into the location transfer log.

Real upload history shows the Container row's on-hand count essentially never
changes on its own (checked all 26 real Container-split Retail items across
5 days of snapshots - every one was perfectly flat) - so unlike a delivery,
there's no reliable "this row's own count changed" signal to detect a
transfer from. Instead, this reuses the same signal Delivery Review already
established: the Each row's net-of-sales change (today_qty - prev_qty +
sold_today - see delivery_review.py's docstring for why sales must be netted
back in). A positive net swing beyond ordinary noise means stock appeared on
the shelf from somewhere; a negative one beyond what sales alone explain means
stock disappeared from the shelf into somewhere. Which direction is "expected"
is the one thing the human picks up front (checkbox + direction, same pattern
as Delivery Review's vendor picker) - this can't tell a transfer apart from an
unlogged delivery or an unlogged spoilage write-off on its own, which is
exactly why this is a *suggestion* a human confirms, never auto-logged.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot, LocationTransferLogEntry
from app.services.delivery_review import QTY_SWING_THRESHOLD, _latest_two_snapshot_dates
from app.services.items_sold import get_items_sold
from app.services.location_transfer_log import create_transfer_entry
from app.services.reorder import latest_inventory_by_item
from app.services.supplier_projection import _container_qty_by_name, _priced_row_by_name

DIRECTIONS = {"container_to_store", "store_to_container"}


def _each_item_by_name(latest_inventory: dict[str, InventorySnapshot]) -> dict[str, tuple[str, InventorySnapshot]]:
    """Name -> (item_id, snap) for the priced/sellable "Each" row - see
    supplier_projection._priced_row_by_name (shared with Refill from
    container and the Store vs Container report)."""
    return _priced_row_by_name(latest_inventory)


def _already_logged_items(db: Session, direction: str, transfer_date: dt.date) -> set:
    """Item ids already logged as moved in this direction on this date - so a
    reload doesn't re-offer them and a second Confirm can't log them twice."""
    return set(
        db.execute(
            select(LocationTransferLogEntry.item_id).where(
                LocationTransferLogEntry.direction == direction,
                LocationTransferLogEntry.transfer_date == transfer_date,
            )
        ).scalars()
    )


def get_transfer_candidates(db: Session, direction: str) -> Optional[dict]:
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")

    today_date, prev_date = _latest_two_snapshot_dates(db)
    if today_date is None:
        return None  # not enough snapshot history yet for even a one-day diff

    latest = latest_inventory_by_item(db, as_of_date=today_date)
    prev = latest_inventory_by_item(db, as_of_date=prev_date)

    each_by_name = _each_item_by_name(latest)
    container_by_name = _container_qty_by_name(latest)

    sold_by_item = {i["item_id"]: i["quantity"] for i in get_items_sold(db, today_date)["items"]}

    candidates = []
    for name, (each_id, each_snap) in each_by_name.items():
        container = container_by_name.get(name)
        if container is None:
            continue  # no Container counterpart for this item - nothing to transfer between
        if each_snap.inventory_quantity is None:
            continue

        today_qty = each_snap.inventory_quantity
        prev_snap = prev.get(each_id)
        prev_qty = prev_snap.inventory_quantity if prev_snap else None
        if prev_qty is None:
            continue  # can't diff without a prior count

        delta = today_qty - prev_qty
        sold_today = sold_by_item.get(each_id, 0.0)
        net_change = delta + sold_today

        if abs(net_change) <= QTY_SWING_THRESHOLD:
            continue

        # Only surface swings matching the direction the human flagged - a
        # positive swing (stock appeared) fits container_to_store; a negative
        # one (stock vanished beyond sales) fits store_to_container.
        if direction == "container_to_store" and net_change <= 0:
            continue
        if direction == "store_to_container" and net_change >= 0:
            continue

        container_item_id, container_qty = container
        container_prev_snap = prev.get(container_item_id)

        candidates.append(
            {
                "item_id": each_id,
                "name": name,
                "category": each_snap.category,
                "each_prev_qty": round(prev_qty, 2),
                "each_today_qty": round(today_qty, 2),
                "container_prev_qty": round(container_prev_snap.inventory_quantity, 2)
                if container_prev_snap and container_prev_snap.inventory_quantity is not None
                else None,
                "container_today_qty": round(container_qty, 2),
                "sold_today": round(sold_today, 2),
                "suggested_qty": round(abs(net_change), 2),
            }
        )

    already_logged = _already_logged_items(db, direction, today_date)
    skipped_logged = sum(1 for c in candidates if c["item_id"] in already_logged)
    candidates = [c for c in candidates if c["item_id"] not in already_logged]
    candidates.sort(key=lambda c: -c["suggested_qty"])

    return {
        "direction": direction,
        "prev_date": prev_date.isoformat(),
        "today_date": today_date.isoformat(),
        "count": len(candidates),
        "items": candidates,
        "already_logged_count": skipped_logged,
    }


def confirm_transfers(
    db: Session,
    direction: str,
    transfer_date: dt.date,
    items: list[dict],
    logged_by_user_id: Optional[int],
) -> int:
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")

    already_logged = _already_logged_items(db, direction, transfer_date)
    logged = 0
    for item in items:
        qty = item.get("quantity")
        if not qty or qty <= 0:
            continue
        if item["item_id"] in already_logged:
            continue  # confirmed before for this direction and day - never log twice
        already_logged.add(item["item_id"])
        create_transfer_entry(
            db,
            item_id=item["item_id"],
            item_name=item.get("item_name"),
            direction=direction,
            quantity=qty,
            transfer_date=transfer_date,
            notes="Confirmed via Location Transfer Review (qty suggested from inventory count "
            "diff, net of that day's sales)",
            logged_by_user_id=logged_by_user_id,
        )
        logged += 1
    return logged
