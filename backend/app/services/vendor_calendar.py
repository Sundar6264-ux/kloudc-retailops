"""Vendor Order Calendar - turns the one-time manual schedule in
vendor_schedule.py (just a vendor's delivery weekday(s) + how many days'
lead time an order needs) into a recurring calendar of order-due and
delivery dates, with everything else derived automatically rather than
requiring ongoing manual upkeep:

- **What to order, and how much** isn't a fixed horizon someone has to keep
  re-picking - for each generated order-due date it's computed as "days
  until this vendor's *next* delivery after this one" (see
  `_next_occurrence`), then fed straight into the existing Supplier
  Projection forecast engine (`compute_supplier_projection`). A Mon/Thu
  vendor's Monday order therefore always covers exactly the 3 days until
  Thursday, and its Thursday order exactly the 4 days until the following
  Monday - never a generic "2 weeks" guess that ignores the vendor's real
  cadence.
- **Delivery confirmation** requires a real purchase_log entry for that
  vendor within CONFIRM_WINDOW_DAYS of the expected date - a submitted
  Invoice Submission does NOT count on its own (it's paperwork, not proof
  anyone actually received/counted real quantities; an earlier version of
  this function treated the two as equivalent and that was a real bug, not
  a deliberate simplification). "Missed" if the date has passed with no
  matching purchase_log entry; a future date with none yet is just
  "upcoming" - not missed until it's actually past.
- **Urgency** on an order-due date comes from Supplier Projection's own
  "need_to_order > 0" signal, computed with the horizon above and surfaced
  on the calendar ahead of time instead of waiting for someone to open that
  page and pick a supplier/duration by hand - but only once at least
  ORDER_MIN_ITEMS items need ordering. Vendors have minimum orders: nobody
  places an order for 1-2 items (owner, Sep 2026), so fewer items is
  `below_minimum` (the count still shows), never `urgent` - the same
  threshold as Reorder's vendor rule and the "Place the order" Team task.

None of this touches inventory_snapshots/orders/purchase_log directly beyond
what those existing modules already query - it only adds the recurrence and
confirmation-matching logic on top.
"""

import datetime as dt
from typing import Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import PlacedOrder, PurchaseLogEntry, VendorOrderSkip
from app.services.order_actions import placed_handled, skip_handled
from app.services.order_cycle import OrderCycles, order_delivery
from app.services.reorder import VENDOR_MIN_ORDER_ITEMS
from app.services import report_cache, supplier_projection
from app.services.supplier_projection import compute_supplier_projection, project_suppliers
from app.services.vendor_schedule import decode_weekdays, list_vendor_schedules
from app.timeutil import restaurant_today, to_restaurant_date, utcnow

# How many days on either side of an expected delivery date still counts as
# "that's the one" when matching against a real purchase_log/invoice record -
# deliveries don't always land on the exact calendar day. Same idea as
# reconciliation.py's DEFAULT_INVOICE_GAP_WINDOW_DAYS, kept separate since the
# two checks answer different questions and don't need to move together.
CONFIRM_WINDOW_DAYS = 2

# Sales history window fed into the demand forecast for every order-due
# date's need-to-order calculation - matches Supplier Projection's own
# shortest lookback preset.
DEMAND_LOOKBACK_DAYS = 30

# Vendors have minimum orders: an order-due is urgent only with at least this
# many items needing ordering (Reorder's VENDOR_MIN_ORDER_ITEMS; the order
# Team task's TEAM_AUTOTASK_ORDER_MIN_ITEMS defaults to the same 5).
ORDER_MIN_ITEMS = VENDOR_MIN_ORDER_ITEMS


def _next_occurrence(after: dt.date, weekdays: List[int]) -> Optional[dt.date]:
    """The next date strictly after `after` whose weekday is in `weekdays` -
    at most a week out, since any weekday pattern repeats within 7 days.
    """
    for offset in range(1, 8):
        candidate = after + dt.timedelta(days=offset)
        if candidate.weekday() in weekdays:
            return candidate
    return None


def _prev_occurrence(before: dt.date, weekdays: List[int]) -> Optional[dt.date]:
    """The latest date strictly before `before` on one of `weekdays`."""
    for i in range(1, 8):
        d = before - dt.timedelta(days=i)
        if d.weekday() in weekdays:
            return d
    return None


def _dates_in_range(weekdays: List[int], start: dt.date, end: dt.date) -> List[dt.date]:
    dates = []
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            dates.append(d)
        d += dt.timedelta(days=1)
    return dates


def _confirmed_near(dates: List[dt.date], target: dt.date, window_days: int) -> bool:
    lo, hi = target - dt.timedelta(days=window_days), target + dt.timedelta(days=window_days)
    return any(lo <= d <= hi for d in dates)


def skip_order(db: Session, vendor: str, delivery_date: dt.date, skipped_by_name: Optional[str]) -> None:
    """Marks the order for `vendor`'s `delivery_date` delivery as skipped.
    Idempotent - skipping an already-skipped order is a no-op."""
    vendor = vendor.strip()
    existing = db.execute(
        select(VendorOrderSkip).where(
            VendorOrderSkip.vendor == vendor, VendorOrderSkip.delivery_date == delivery_date
        )
    ).scalars().first()
    if existing is None:
        db.add(
            VendorOrderSkip(
                vendor=vendor, delivery_date=delivery_date, skipped_by_name=skipped_by_name, created_at=utcnow()
            )
        )
        db.commit()


def unskip_order(db: Session, vendor: str, delivery_date: dt.date) -> bool:
    row = db.execute(
        select(VendorOrderSkip).where(
            VendorOrderSkip.vendor == vendor.strip(), VendorOrderSkip.delivery_date == delivery_date
        )
    ).scalars().first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _need_counts(projections: List[dict], horizon_days: int) -> Tuple[int, float]:
    """(items needing ordering, total need_to_order) over one horizon."""
    needed = [i for i in projections if (i["projections"][str(horizon_days)]["need_to_order"] or 0) > 0]
    return len(needed), round(sum(i["projections"][str(horizon_days)]["need_to_order"] for i in needed), 2)


def need_to_order_counts(db: Session, horizons_by_vendor: Dict[str, set], today: dt.date) -> Dict[tuple, tuple]:
    """(vendor, horizon) -> (items needing ordering, total need_to_order),
    from Supplier Projection's forecast. Speed #4 (Oct 15): every vendor's
    projection is built in ONE pass (project_suppliers - shared inventory,
    sales and store profiles; it was one full pass per vendor, 1.8-3 s per
    calendar read) and each (vendor, horizon) count is kept in memory with
    the reports (report_cache) - Home's two-day read and the calendar's
    20-day read share them."""
    if compute_supplier_projection is not supplier_projection.compute_supplier_projection:
        # A stand-in projection (tests' stubs): per vendor, never cached.
        out = {}
        for vendor, horizons in horizons_by_vendor.items():
            rows = compute_supplier_projection(db, vendor, DEMAND_LOOKBACK_DAYS, sorted(horizons), today)
            out.update({(vendor, h): _need_counts(rows, h) for h in horizons})
        return out
    wanted = {
        ("vendor_need", vendor, horizon, DEMAND_LOOKBACK_DAYS, today): (vendor, horizon)
        for vendor, horizons in horizons_by_vendor.items()
        for horizon in horizons
    }

    def compute(missing: dict) -> dict:
        horizons: Dict[str, set] = {}
        for vendor, horizon in missing.values():
            horizons.setdefault(vendor, set()).add(horizon)
        projections = project_suppliers(
            db, {vendor: sorted(h) for vendor, h in horizons.items()}, DEMAND_LOOKBACK_DAYS, today
        )
        return {key: _need_counts(projections.get(vendor, []), horizon) for key, (vendor, horizon) in missing.items()}

    counts = report_cache.cached_many(db, wanted, compute)
    return {wanted[key]: value for key, value in counts.items()}


def get_vendor_calendar(db: Session, start_date: dt.date, end_date: dt.date) -> dict:
    schedules = list_vendor_schedules(db, active_only=True)
    today = restaurant_today()

    # Pulled once and reused for every vendor's confirmation check below.
    # Deliberately purchase_log ONLY, not invoice submissions too - an
    # invoice being submitted is paperwork, not proof anyone actually
    # counted/received real quantities. Treating the two as equivalent
    # (an earlier version of this function did, via an OR) meant a delivery
    # could show "confirmed" here purely because an invoice was uploaded,
    # even if nobody had gone through Delivery Review or logged a purchase
    # at all - a real user-reported bug, not a hypothetical one.
    purchase_dates_by_vendor: dict[str, List[dt.date]] = {}
    for supplier, received_date in db.execute(
        select(PurchaseLogEntry.supplier, PurchaseLogEntry.received_date).where(PurchaseLogEntry.supplier.is_not(None))
    ):
        purchase_dates_by_vendor.setdefault(supplier.strip().lower(), []).append(received_date)

    # Every order-due date for a vendor uses the same sales window (the same
    # `today`), only the horizon differs - so project each vendor ONCE with
    # all of its horizons instead of once per order-due date.
    horizons_by_vendor: dict[str, set] = {}
    for schedule in schedules:
        weekdays = decode_weekdays(schedule.delivery_weekdays)
        scan_end = end_date + dt.timedelta(days=schedule.lead_days)
        for delivery_date in _dates_in_range(weekdays, start_date, scan_end) if weekdays else []:
            next_delivery = _next_occurrence(delivery_date, weekdays)
            horizon_days = (next_delivery - delivery_date).days if next_delivery else 7
            if start_date <= delivery_date - dt.timedelta(days=schedule.lead_days) <= end_date:
                horizons_by_vendor.setdefault(schedule.vendor, set()).add(horizon_days)
    need_by_vendor = need_to_order_counts(db, horizons_by_vendor, today)

    # Skipped order cycles, keyed by (vendor, delivery date). The window is
    # generous on purpose: an order-due date inside the range can be for a
    # delivery a few lead days past it.
    # W1: a skip is "Skip this week" (reason NULL) or "Other" (reason
    # "other", with a note) - both skip the cycle.
    skipped = {
        (row.vendor.strip().lower(), row.delivery_date): row
        for row in db.execute(
            select(VendorOrderSkip).where(
                VendorOrderSkip.delivery_date >= start_date - dt.timedelta(days=7),
                VendorOrderSkip.delivery_date <= end_date + dt.timedelta(days=60),
            )
        ).scalars()
    }

    # Placed orders (Mark placed, or "Order already placed" - source
    # outside; a "pdf" row is only a preview), per vendor: an order-due chip
    # whose order was already placed shows "ordered". A row with a
    # for_delivery_date (marked from that order-due) counts for exactly that
    # delivery; the rest by the day they were placed (below).
    placed_by_vendor: dict = {}
    for row in db.execute(
        select(PlacedOrder).where(
            PlacedOrder.status == "placed",
            or_(
                PlacedOrder.placed_at
                >= dt.datetime.combine(start_date - dt.timedelta(days=21), dt.time.min, dt.timezone.utc),
                PlacedOrder.for_delivery_date >= start_date - dt.timedelta(days=7),
            ),
        )
    ).scalars():
        if row.supplier:
            placed_by_vendor.setdefault(row.supplier.strip().lower(), []).append(
                (to_restaurant_date(row.placed_at), row.placed_at.isoformat(), row)
            )

    # Deliveries recorded per vendor (rule 3 of the shared order rule: a
    # delivery recorded before an order went in is done).
    cycles = OrderCycles(
        db, since=dt.datetime.combine(start_date - dt.timedelta(days=28), dt.time.min, dt.timezone.utc)
    )

    events = []
    for schedule in schedules:
        weekdays = decode_weekdays(schedule.delivery_weekdays)
        if not weekdays:
            continue
        vendor_key = schedule.vendor.strip().lower()

        # Scan past end_date by this vendor's lead_days so an order-due date
        # that lands inside the requested window is still generated even
        # when the delivery it's covering falls just after the window.
        scan_end = end_date + dt.timedelta(days=schedule.lead_days)
        for delivery_date in _dates_in_range(weekdays, start_date, scan_end):
            next_delivery = _next_occurrence(delivery_date, weekdays)
            horizon_days = (next_delivery - delivery_date).days if next_delivery else 7

            order_due_date = delivery_date - dt.timedelta(days=schedule.lead_days)
            if start_date <= order_due_date <= end_date:
                needed_count, total_need = need_by_vendor.get((schedule.vendor, horizon_days), (0, 0))
                skip_row = skipped.get((vendor_key, delivery_date))
                is_skipped = skip_row is not None
                # The order for this delivery: one shared rule for every
                # screen (services/order_cycle.py) - a row marked for this
                # delivery, or the delivery whose order day is closest to
                # the day it was placed. Each order counts for ONE delivery.
                placed = [
                    (at, row)
                    for _day, at, row in placed_by_vendor.get(vendor_key, [])
                    if order_delivery(
                        row.placed_at,
                        weekdays,
                        schedule.lead_days,
                        row.for_delivery_date,
                        cycles.recorded.get(vendor_key, ()),
                    )
                    == delivery_date
                ]
                ordered_at, order_row = max(placed, key=lambda p: (p[0], p[1].id)) if placed else (None, None)
                if skip_row is not None:
                    handled = skip_handled(skip_row)
                elif order_row is not None:
                    handled = placed_handled(order_row)
                else:
                    handled = None
                events.append(
                    {
                        "type": "order_due",
                        "date": order_due_date.isoformat(),
                        "vendor": schedule.vendor,
                        "for_delivery_date": delivery_date.isoformat(),
                        "horizon_days": horizon_days,
                        "items_needing_order": needed_count,
                        "total_need_to_order": total_need,
                        # A skipped order is a decision, not something to chase.
                        # Fewer than ORDER_MIN_ITEMS items is not an order yet
                        # (vendor minimum orders) - below_minimum instead.
                        "urgent": needed_count >= ORDER_MIN_ITEMS and not is_skipped and ordered_at is None,
                        # Additive (Oct 14): 1..ORDER_MIN_ITEMS-1 items need
                        # ordering - shown as a count, never as urgent.
                        "below_minimum": 0 < needed_count < ORDER_MIN_ITEMS and not is_skipped and ordered_at is None,
                        "min_order_items": ORDER_MIN_ITEMS,
                        "skipped": is_skipped,
                        # Already placed (Mark placed): not something to chase.
                        "ordered": ordered_at is not None,
                        "ordered_at": ordered_at,
                        # W1 (additive): how this order was dealt with -
                        # {kind: skip | other | placed_outside | placed,
                        # label, note, by_name, at, order_id, can_undo}.
                        "ordered_source": (order_row.source or "app") if order_row is not None else None,
                        "ordered_order_id": order_row.id if order_row is not None else None,
                        "skip_reason": handled["kind"] if skip_row is not None else None,
                        "handled": handled,
                    }
                )

            if start_date <= delivery_date <= end_date:
                confirmed = _confirmed_near(
                    purchase_dates_by_vendor.get(vendor_key, []), delivery_date, CONFIRM_WINDOW_DAYS
                )
                skip_row = skipped.get((vendor_key, delivery_date))
                is_skipped = skip_row is not None
                if confirmed:
                    status = "delivered"
                elif is_skipped:
                    # Not "missed" - nobody expected it. Older app builds
                    # show unknown statuses as a plain upcoming delivery.
                    status = "skipped"
                elif delivery_date < today:
                    status = "missed"
                else:
                    status = "upcoming"
                events.append(
                    {
                        "type": "delivery",
                        "date": delivery_date.isoformat(),
                        "vendor": schedule.vendor,
                        "status": status,
                        "skipped": is_skipped,
                        # W1 (additive): "skip" | "other" | None, and the note.
                        "skip_reason": skip_handled(skip_row)["kind"] if skip_row is not None else None,
                        "skip_note": skip_row.note if skip_row is not None else None,
                    }
                )

    events.sort(key=lambda e: (e["date"], e["vendor"], e["type"]))
    return {"start": start_date.isoformat(), "end": end_date.isoformat(), "events": events}
