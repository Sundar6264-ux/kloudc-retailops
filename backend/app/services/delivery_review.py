"""Delivery Review: after a daily inventory upload, lets the user flag that a
delivery came in and, per vendor, review a computer-suggested list of items
that likely arrived - either brand new to the catalog or with a quantity
swing big enough to be notable - then confirm real quantities into the
purchase log.

The suggested quantity is the count diff between today's and the previous
upload, *plus* whatever sold that same day (closing = opening + purchased -
sold, so purchased = delta + sold) - not just the raw count diff. Without
netting sales back in, a delivery on a day with real sales would always look
smaller than it actually was (e.g. 100 on hand yesterday, 400 delivered and
50 sold today nets a count diff of only +350, not +400) - confirmed against
real data on Cilantro Bunch: delta +246, 67 sold that day, suggested 313.
`sold_today` comes from `items_sold.py` (live from Toast for today, from the
synced `orders` table for a past date).

This is deliberately NOT an automatic purchase-log writer, even though the
suggested quantity above is now the mathematically "correct" one for that
single day (i.e. confirming it unedited makes that day's reconciliation
variance exactly 0 for that item) - it's a *suggestion* a human reviews and
can edit before anything is actually written, same as any other purchase-log
entry, because the suggestion still trusts the inventory count and today's
sales figure as ground truth, and a human with the actual invoice in hand
may know better (see reconciliation.py's own docstring for the formula this
whole flow is built around).

Confirming a delivery also captures that item's `cost` from that day's
inventory file, tagged to the vendor the human just confirmed - see
vendor_cost.py for why this (not `inventory_snapshots.cost` alone) is the
real, unambiguous per-vendor cost signal the app relies on for Vendor Price
Comparison.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot, PurchaseLogEntry
from app.services.category_overrides import apply_overrides
from app.services.items_sold import get_items_sold
from app.services.purchase_log import create_purchase_entry
from app.services.supplier_projection import _split_suppliers, _supplier_item_id_for

# Items whose net change (count diff + sold today, either direction) since
# the previous upload is this small or smaller aren't surfaced - ordinary
# day-to-day noise, not something worth a human's attention. Arbitrary,
# user-chosen threshold.
QTY_SWING_THRESHOLD = 5


def _latest_two_snapshot_dates(db: Session) -> tuple[Optional[dt.date], Optional[dt.date]]:
    dates = (
        db.execute(select(InventorySnapshot.snapshot_date).distinct().order_by(InventorySnapshot.snapshot_date.desc()))
        .scalars()
        .all()
    )
    if len(dates) < 2:
        return None, None
    return dates[0], dates[1]  # today, previous


def _already_logged_items(db: Session, vendor: str, received_date: dt.date) -> set:
    """Item ids already in the purchase log for this vendor on this date. Used
    so a reload after Confirm doesn't offer the same items again, and a second
    Confirm can't log them twice (real review finding, Sep 2026)."""
    return set(
        db.execute(
            select(PurchaseLogEntry.item_id).where(
                PurchaseLogEntry.supplier == vendor,
                PurchaseLogEntry.received_date == received_date,
            )
        ).scalars()
    )


def get_delivery_candidates(db: Session, vendor: str) -> Optional[dict]:
    today_date, prev_date = _latest_two_snapshot_dates(db)
    if today_date is None:
        return None  # not enough snapshot history yet for even a one-day diff

    # Category overrides (services/category_overrides.py) - read-only wrappers.
    today_snaps = apply_overrides(db, {
        row.item_id: row
        for row in db.execute(
            select(InventorySnapshot).where(InventorySnapshot.snapshot_date == today_date)
        ).scalars()
    })
    prev_snaps = {
        row.item_id: row
        for row in db.execute(
            select(InventorySnapshot).where(InventorySnapshot.snapshot_date == prev_date)
        ).scalars()
    }

    # A raw count diff alone understates what actually arrived whenever the
    # item also sold that same day (closing = opening + purchased - sold, so
    # purchased = delta + sold) - e.g. 100 on hand yesterday, 400 delivered
    # and 50 sold today nets a count diff of only +350, not +400. Netting
    # sold back in here recovers the real received quantity instead of
    # leaving the reviewer to notice and manually correct it every time.
    sold_by_item = {i["item_id"]: i["quantity"] for i in get_items_sold(db, today_date)["items"]}

    already_logged = _already_logged_items(db, vendor, today_date)
    skipped_logged = 0
    candidates = []
    for item_id, snap in today_snaps.items():
        if vendor not in _split_suppliers(snap.supplier):
            continue
        if item_id in already_logged:
            skipped_logged += 1
            continue
        if snap.inventory_quantity is None:
            continue  # no on-hand count to compare, nothing to suggest

        today_qty = snap.inventory_quantity
        prev_snap = prev_snaps.get(item_id)
        is_new = prev_snap is None

        if is_new:
            delta = today_qty
        elif prev_snap.inventory_quantity is None:
            continue  # existed before but had no known count then - can't diff
        else:
            delta = today_qty - prev_snap.inventory_quantity

        sold_today = sold_by_item.get(item_id, 0.0)
        net_change = delta + sold_today

        if not is_new and abs(net_change) <= QTY_SWING_THRESHOLD:
            continue

        candidates.append(
            {
                "item_id": item_id,
                "name": snap.name,
                "category": snap.category,
                "supplier_item_id": _supplier_item_id_for(snap, vendor),
                "prev_qty": prev_snap.inventory_quantity if prev_snap else None,
                "today_qty": today_qty,
                "delta": round(delta, 2),
                "sold_today": round(sold_today, 2),
                "is_new": is_new,
                "suggested_qty": round(max(net_change, 0), 2),
                "cost": snap.cost,
            }
        )

    # New items first (least ambiguous), then biggest swings first.
    candidates.sort(key=lambda c: (not c["is_new"], -abs(c["delta"] + c["sold_today"])))

    return {
        "vendor": vendor,
        "prev_date": prev_date.isoformat(),
        "today_date": today_date.isoformat(),
        "count": len(candidates),
        "items": candidates,
        "already_logged_count": skipped_logged,
    }


def confirm_delivery(
    db: Session,
    vendor: str,
    received_date: dt.date,
    items: list[dict],
    logged_by_user_id: Optional[int],
) -> int:
    # Cost is looked up server-side from that day's real inventory snapshot,
    # not trusted from the client, same as every other suggested value here.
    item_ids = [item["item_id"] for item in items]
    costs = {
        row.item_id: row.cost
        for row in db.execute(
            select(InventorySnapshot).where(
                InventorySnapshot.snapshot_date == received_date,
                InventorySnapshot.item_id.in_(item_ids),
            )
        ).scalars()
    }

    already_logged = _already_logged_items(db, vendor, received_date)
    logged = 0
    for item in items:
        qty = item.get("quantity_received")
        if not qty or qty <= 0:
            continue
        if item["item_id"] in already_logged:
            continue  # confirmed before for this vendor and day - never log twice
        already_logged.add(item["item_id"])
        create_purchase_entry(
            db,
            item_id=item["item_id"],
            item_name=item.get("item_name"),
            supplier=vendor,
            quantity_received=qty,
            unit_cost=costs.get(item["item_id"]),
            received_date=received_date,
            notes="Confirmed via Delivery Review (qty suggested from inventory count diff, "
            "cost captured from that day's inventory file)",
            logged_by_user_id=logged_by_user_id,
        )
        logged += 1
    return logged
