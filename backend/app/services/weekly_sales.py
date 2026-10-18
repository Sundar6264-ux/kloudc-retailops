"""Per-item weekly sales breakdown - the actual numbers behind a demand average,
for the "click the avg/day or avg/week figure to see what it's made of" view in
Supplier Projection (and the item details in Fix Issues / Reorder). Computed on
demand per item (not upfront for a whole supplier's item list), since it's only
needed when a user expands one row.

Each week also carries the item's average on-hand count over the days of that
week (Oct 14 batch, W3) so "sold 10" can be read next to "avg on hand 24":

- Source: the daily `inventory_snapshots` rows (CSV uploads, the June-August
  backfill and Toast API rows alike - one row per item per date). Snapshot D is
  the OPENING count of business day D (services/demand_forecast.py), so the
  counts averaged for a week are the snapshots dated inside that week.
- The bare Container-location twin (`supplier_projection._container_qty_by_name`
  on the latest upload, the pairing Supplier Projection / Reorder add to on
  hand) is added in on each day it has a count.
- Only days with a tracked quantity count (the item's own row has a number).
  `on_hand_days` says how many; `period_days` is how many days of the week are
  inside the requested window. No tracked day -> `avg_on_hand` None ("-").
- Toast's own number is averaged as is: a negative count stays negative, and an
  API "out of stock" row is its stored 0.
"""

import datetime as dt
from collections import defaultdict
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot, Order


def container_twin_id(db: Session, item_id: str) -> Optional[str]:
    """The item_id of `item_id`'s bare Container-location row on the latest
    upload, or None. Same rule as `supplier_projection._container_qty_by_name`
    (no price, no vendor, a count, and the only such row with that name),
    looked up for one name instead of loading the whole upload."""
    latest = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar_one_or_none()
    if latest is None:
        return None
    name = db.execute(
        select(InventorySnapshot.name).where(
            InventorySnapshot.snapshot_date == latest, InventorySnapshot.item_id == item_id
        )
    ).scalar_one_or_none()
    if name is None:
        return None
    rows = db.execute(
        select(InventorySnapshot.item_id, InventorySnapshot.price, InventorySnapshot.supplier).where(
            InventorySnapshot.snapshot_date == latest,
            InventorySnapshot.name == name,
            InventorySnapshot.inventory_quantity.is_not(None),
        )
    ).all()
    bare = [rid for rid, price, supplier in rows if price is None and not supplier]
    if len(bare) != 1 or bare[0] == item_id:
        return None
    return bare[0]


def daily_on_hand(
    db: Session, item_id: str, start_date: dt.date, end_date: dt.date
) -> tuple[dict, Optional[str]]:
    """({date: on-hand count}, container item_id or None) for snapshot dates
    in [start_date, end_date). A date is present only when the item's own row
    has a count; its Container twin's count is added when it has one that day."""
    container_id = container_twin_id(db, item_id)
    ids = [item_id] + ([container_id] if container_id else [])
    own: dict[dt.date, float] = {}
    extra: dict[dt.date, float] = {}
    stmt = select(
        InventorySnapshot.item_id, InventorySnapshot.snapshot_date, InventorySnapshot.inventory_quantity
    ).where(
        InventorySnapshot.item_id.in_(ids),
        InventorySnapshot.snapshot_date >= start_date,
        InventorySnapshot.snapshot_date < end_date,
        InventorySnapshot.inventory_quantity.is_not(None),
    )
    for rid, day, qty in db.execute(stmt):
        if rid == item_id:
            own[day] = float(qty)
        else:
            extra[day] = float(qty)
    return {day: qty + extra.get(day, 0.0) for day, qty in own.items()}, container_id


def _monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def weekly_sales_for_item(
    db: Session,
    item_guid: str,
    start_date: dt.date,
    end_date: dt.date,
    include_empty_weeks: bool = False,
) -> list[dict]:
    """Weeks (Monday-start) between start_date and end_date (exclusive), with
    actual quantity/revenue sold in each - real top-level sales only (no
    modifier lines, matching forecast.py's definition of "sold item") - plus
    the week's average on-hand count (see the module docstring).

    By default only weeks with a sale are listed (what app builds 5-9 expect).
    `include_empty_weeks` lists every week of the window too, with 0 sold,
    so a week the item sat on the shelf (or was out of stock) unsold shows -
    except leading weeks before the item had any count or sale.
    """
    stmt = select(Order.business_date, Order.quantity, Order.net_price).where(
        Order.item_guid == item_guid,
        Order.voided.is_(False),
        Order.top_level(),
        Order.business_date >= start_date,
        Order.business_date < end_date,
    )

    weeks: dict[dt.date, dict] = defaultdict(lambda: {"quantity": 0.0, "revenue": 0.0})
    for business_date, quantity, net_price in db.execute(stmt):
        week_start = _monday(business_date)
        weeks[week_start]["quantity"] += quantity
        weeks[week_start]["revenue"] += net_price

    on_hand, _container = daily_on_hand(db, item_guid, start_date, end_date)
    counts_by_week: dict[dt.date, list] = defaultdict(list)
    for day, qty in on_hand.items():
        counts_by_week[_monday(day)].append(qty)

    if include_empty_weeks and end_date > start_date:
        week = _monday(start_date)
        last = _monday(end_date - dt.timedelta(days=1))
        seen_any = False
        while week <= last:
            if week in weeks or counts_by_week.get(week):
                seen_any = True
            if seen_any:
                _ = weeks[week]  # a defaultdict: this lists the (empty) week
            week += dt.timedelta(days=7)

    rows = []
    for week_start, v in sorted(weeks.items()):
        first = max(week_start, start_date)
        last = min(week_start + dt.timedelta(days=6), end_date - dt.timedelta(days=1))
        period_days = max((last - first).days + 1, 0)
        counts = counts_by_week.get(week_start, [])
        rows.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": (week_start + dt.timedelta(days=6)).isoformat(),
                "quantity": round(v["quantity"], 2),
                "revenue": round(v["revenue"], 2),
                # Additive (W3): average on-hand over the week's tracked days.
                "avg_on_hand": round(sum(counts) / len(counts), 1) if counts else None,
                "on_hand_days": len(counts),
                "period_days": period_days,
            }
        )
    return rows
