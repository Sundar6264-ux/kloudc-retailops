"""Which delivery a placed order is for - ONE rule for every screen that asks
"is this vendor's order already placed?" (owner, Oct 22: Home's Orders due
and Critical tiles kept showing a vendor after its order was placed). The
same rule as the other ops system's services/order_cycles.py, so the two
agree.

Used by:
  - the Vendor Calendar's order-due chips ("ordered"), and so the app's Home
    Orders due tile, the website Overview's Vendor schedule, the 9 AM
    order-due push and the morning summary (services/vendor_calendar.py);
  - the Reorder "Ordered" status, and so the Home Critical tile, the 9 AM
    critical-vendors push and the website's Need reordering tile
    (reorder.ordered_vendors);
  - the suggested-order basis line (order_suggestion.cycle_order);
  - the "Place the <vendor> order" team task (team/auto_tasks.py).

Every way of placing an order writes the same kind of row (PlacedOrder,
status "placed"): New order -> cart -> Send PO, the cart's Mark placed,
"Order already placed" / placed outside (Today tiles, Vendor Calendar,
Reorder). So the rule only has to look at that row:

  1. An order tied to a delivery ("Order already placed" on an order-due,
     `for_delivery_date`) is for exactly that delivery.
  2. Otherwise, with a Vendor Calendar schedule, it is for the scheduled
     delivery (on or after the day it was placed, store time) whose ORDER
     DAY (delivery - lead days) is closest to the day it was placed; a tie
     goes to the later delivery (orders go in early, not late). So an order
     marked at 4-5 AM on delivery day, for that day's delivery, is that
     day's (real data: Chetak, Oct 15 05:15), and one sent on a delivery day
     for the next delivery is the next one's.
  3. A delivery already recorded that day before the order went in
     (Delivery Review / logged purchase, or an uploaded delivery invoice) is
     done: the order is for a later delivery.
  4. No schedule: no delivery date (Reorder then uses its lead-time window).

Before this, the calendar tied an order to the first delivery on/after the
day it was placed (ignoring lead days) while Reorder used the lead days, so
an order sent on a delivery day for the next delivery cleared Critical but
left "Order due" on Home.
"""

import datetime as dt
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvoiceSubmission, PurchaseLogEntry, VendorSchedule
from app.timeutil import to_restaurant_date

# Invoice types that mean goods arrived (reorder.DELIVERY_INVOICE_TYPES).
_DELIVERY_INVOICE_TYPES = ("invoice", "untracked")


def vendor_key(name: Optional[str]) -> str:
    """Vendors match ignoring case and outer spaces (as everywhere here)."""
    return (name or "").strip().lower()


def _aware(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def _decode(raw) -> List[int]:
    from app.services.vendor_schedule import decode_weekdays

    try:
        return sorted(set(decode_weekdays(raw)))
    except Exception:  # noqa: BLE001 - a broken row is "no schedule"
        return []


def schedules_by_key(db: Session) -> Dict[str, Tuple[List[int], int]]:
    """vendor_key -> (delivery weekdays, lead days), active schedules only."""
    out: Dict[str, Tuple[List[int], int]] = {}
    for row in db.execute(select(VendorSchedule).where(VendorSchedule.active.is_(True))).scalars():
        weekdays = _decode(row.delivery_weekdays)
        if weekdays:
            out[vendor_key(row.vendor)] = (weekdays, row.lead_days or 0)
    return out


def delivery_records(db: Session, since: dt.datetime) -> Dict[str, List[dt.datetime]]:
    """vendor_key -> when a delivery was recorded (Delivery Review / logged
    purchase, or an uploaded delivery invoice), from `since` on. Demo uploads
    are no proof of delivery."""
    from app.demo import hide_demo_clause

    out: Dict[str, List[dt.datetime]] = {}
    for vendor, created in db.execute(
        select(PurchaseLogEntry.supplier, PurchaseLogEntry.created_at).where(PurchaseLogEntry.created_at >= since)
    ).all():
        if vendor and created is not None:
            out.setdefault(vendor_key(vendor), []).append(_aware(created))
    for vendor, created in db.execute(
        select(InvoiceSubmission.vendor, InvoiceSubmission.created_at).where(
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.submission_type.in_(_DELIVERY_INVOICE_TYPES),
            InvoiceSubmission.created_at >= since,
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),
        )
    ).all():
        if vendor and created is not None:
            out.setdefault(vendor_key(vendor), []).append(_aware(created))
    return out


def order_delivery(
    placed_at: dt.datetime,
    weekdays: Iterable[int],
    lead_days: int,
    for_delivery_date: Optional[dt.date] = None,
    recorded: Iterable[dt.datetime] = (),
) -> Optional[dt.date]:
    """The delivery date a placed order is for (rules 1-4 above), or None for
    a vendor without a schedule. `recorded`: when this vendor's deliveries
    were recorded (delivery_records)."""
    if for_delivery_date is not None:
        return for_delivery_date
    weekdays = set(weekdays or ())
    if not weekdays:
        return None
    lead = max(int(lead_days or 0), 0)
    placed_at = _aware(placed_at)
    placed_day = to_restaurant_date(placed_at)
    done_days = {to_restaurant_date(_aware(t)) for t in recorded if t is not None and _aware(t) <= placed_at}
    candidates = [
        placed_day + dt.timedelta(days=i)
        for i in range(0, 15 + lead)
        if (placed_day + dt.timedelta(days=i)).weekday() in weekdays
        and (placed_day + dt.timedelta(days=i)) not in done_days
    ]
    if not candidates:
        return None
    # Nearest order day (delivery - lead days); a tie goes to the later delivery.
    return min(candidates, key=lambda day: (abs(((day - dt.timedelta(days=lead)) - placed_day).days), -day.toordinal()))


class OrderCycles:
    """Loads schedules and delivery records once, then answers "which
    delivery is this order for" for many rows."""

    def __init__(self, db: Session, since: Optional[dt.datetime] = None):
        self.schedules = schedules_by_key(db)
        since = since or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        self.recorded = delivery_records(db, since)

    def schedule(self, vendor: Optional[str]) -> Tuple[List[int], int]:
        return self.schedules.get(vendor_key(vendor), ([], 0))

    def delivery_for(self, row) -> Optional[dt.date]:
        weekdays, lead = self.schedule(row.supplier)
        return order_delivery(
            row.placed_at,
            weekdays,
            lead,
            getattr(row, "for_delivery_date", None),
            self.recorded.get(vendor_key(row.supplier), ()),
        )
