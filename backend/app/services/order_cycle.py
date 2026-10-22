"""Which delivery a placed order is for - ONE rule for every screen that asks
"is this vendor's order already placed?" (owner, Oct 22: Home's Orders due
and Critical tiles kept showing a vendor after its order was placed).

Used by:
  - the Vendor Calendar's order-due chips ("ordered"), and so the app's Home
    Orders due tile, the website Overview's Vendor schedule, the 9 AM
    order-due push and the morning summary (services/vendor_calendar.py);
  - the Reorder "Ordered" status, and so the Home Critical tile, the 9 AM
    critical-vendors push and the website's Need reordering tile
    (reorder.ordered_vendors);
  - the "Place the <vendor> order" team task (team/auto_tasks.py).

Every way of placing an order writes the same kind of row (PlacedOrder,
status "placed"): New order -> cart -> Send PO, the cart's Mark placed,
"Order already placed" / placed outside (Today tiles, Vendor Calendar,
Reorder). So the rule only has to look at that row:

  1. An order marked for a delivery ("Order already placed" on an
     order-due, `for_delivery_date`) is for exactly that delivery.
  2. Otherwise, with a Vendor Calendar schedule, it is for the first
     scheduled delivery it can still make: on or after the day it was placed
     plus the vendor's lead days. Placed in the small hours (before
     SMALL_HOURS_END store time) counts as the day before - that's the late
     note of the evening's order (real data: orders are often marked at
     1-4am).
  3. With no schedule there is no delivery to tie it to (the Reorder page's
     lead time decides how long it shows "Ordered").

Before this, the calendar tied an order to the first delivery on/after the
day it was placed (ignoring lead days), while Reorder used the lead days: an
order placed on a delivery day was given to that day's delivery (the previous
order), so the next order-due kept showing "Order today" while Critical said
"Ordered".
"""

import datetime as dt
from typing import Iterable, Optional

from app.timeutil import restaurant_timezone

# Marked before 5 AM store time = the previous day's order.
SMALL_HOURS_END = 5


def order_day(placed_at: dt.datetime) -> dt.date:
    """The store day an order belongs to (rule 2's small-hours shift)."""
    if placed_at.tzinfo is None:
        placed_at = placed_at.replace(tzinfo=dt.timezone.utc)
    local = placed_at.astimezone(restaurant_timezone())
    day = local.date()
    return day - dt.timedelta(days=1) if local.hour < SMALL_HOURS_END else day


def first_delivery_on_or_after(day: dt.date, weekdays: Iterable[int]) -> Optional[dt.date]:
    days = set(weekdays or ())
    if not days:
        return None
    for i in range(7):
        candidate = day + dt.timedelta(days=i)
        if candidate.weekday() in days:
            return candidate
    return None


def delivery_for_order(
    placed_at: dt.datetime,
    for_delivery_date: Optional[dt.date],
    weekdays: Iterable[int],
    lead_days: int,
) -> Optional[dt.date]:
    """The delivery a placed order is for (rules 1-3 above); None without a
    schedule and no delivery of its own."""
    if for_delivery_date is not None:
        return for_delivery_date
    return first_delivery_on_or_after(order_day(placed_at) + dt.timedelta(days=lead_days or 0), weekdays)
