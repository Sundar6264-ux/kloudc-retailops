"""Today-tab order actions (docs/BATCH_OCT14_CONTRACT.md W1, migration 0053).

Admins act on an order without opening anything, from the Today card's
Critical and Orders due tiles (app), and from the website's Reorder
Candidates and Vendor Calendar pages:

- **Order already placed** (`placed`): the order went in some other way
  (phone, WhatsApp, the vendor's rep). Recorded as a `placed_orders` row
  with status "placed" and source "outside" (no lines, nothing taken from
  the cart) - exactly what Mark placed records, so everything that already
  follows Mark placed follows this too, with no second rule to keep in step:
    - Vendor Calendar: that order-due is "ordered" (from an Orders due row
      the row is tied to its delivery via `for_delivery_date`);
    - Reorder Candidates: the vendor is "Ordered" until a delivery is
      recorded or the ordered window ends - so it leaves the Critical tile
      and the 9 AM critical-vendors push by itself;
    - the 9 AM order-due push and the Team vendor-order task skip it.
  Undo deletes that row (only an "outside" row - an order placed from the
  cart is never deleted here).
- **Skip this week** (`skip`): the existing Vendor Calendar skip of that
  order cycle (`vendor_order_skips`, keyed by the delivery date).
- **Other** (`other`, a note required): hides the order for this cycle - a
  `vendor_order_skips` row with reason "other" and the note, so the
  calendar, the order-due push, the Team task and older app builds (which
  leave out `skipped` orders) all treat it as handled.
  Undo of either deletes the skip row.

Store-wide, admins only, like the critical-vendor dismissals; the App
Review demo login is refused by the global demo guard (these routes are not
allowlisted).
"""

import datetime as dt
import threading
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PlacedOrder, User, VendorOrderSkip
from app.services.purchase_order_log import SOURCE_OUTSIDE, STATUS_PLACED
from app.services.vendor_schedule import decode_weekdays, list_vendor_schedules
from app.timeutil import to_restaurant_date, utcnow

ACTIONS = ("placed", "skip", "other")
ACTION_LABELS = {
    "placed": "Order already placed",
    "skip": "Skip this week",
    "other": "Other",
}
# What the order-due's `handled.kind` says, and how it reads.
HANDLED_LABELS = {
    "placed_outside": "Order already placed",
    "placed": "Order placed",
    "skip": "Skipped this week",
    "other": "Other",
}
SKIP_REASON_OTHER = "other"
NOTE_MAX_CHARS = 500
# A second "Order already placed" for the same vendor (no delivery date)
# within this long is the same tap (double tap / retry): no second row.
DUPLICATE_WINDOW = dt.timedelta(minutes=2)

_write_lock = threading.Lock()


class OrderActionError(Exception):
    """Turned into an HTTP error by the router; `detail` is ready to show."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def vendor_key(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def clean_note(note: Optional[str]) -> Optional[str]:
    if note is None:
        return None
    text = str(note).strip()
    if len(text) > NOTE_MAX_CHARS:
        raise OrderActionError(400, f"Keep the note to {NOTE_MAX_CHARS} characters or fewer.")
    return text or None


def _schedule_for(db: Session, vendor: str):
    key = vendor_key(vendor)
    for row in list_vendor_schedules(db):
        if vendor_key(row.vendor) == key:
            return row
    return None


def resolve_vendor(db: Session, vendor: str) -> str:
    """The vendor's own spelling: its Vendor Calendar schedule's, else the
    latest inventory upload's (Toast). 404 when neither knows it."""
    name = (vendor or "").strip()
    if not name:
        raise OrderActionError(400, "Pick a vendor.")
    schedule = _schedule_for(db, name)
    if schedule is not None:
        return schedule.vendor
    # Imported here: supplier_projection is heavy and imports this package's
    # neighbours.
    from app.services.supplier_projection import list_suppliers

    suppliers = {vendor_key(s): s for s in list_suppliers(db)}
    if vendor_key(name) not in suppliers:
        raise OrderActionError(404, f"Unknown vendor: {name}")
    return suppliers[vendor_key(name)]


def _check_cycle(db: Session, vendor: str, delivery_date: dt.date) -> str:
    """An order cycle is a delivery date on the vendor's schedule. Returns
    the schedule's spelling of the vendor."""
    schedule = _schedule_for(db, vendor)
    if schedule is None:
        raise OrderActionError(404, f"{vendor.strip()} has no delivery schedule on the Vendor Calendar.")
    weekdays = decode_weekdays(schedule.delivery_weekdays)
    if delivery_date.weekday() not in weekdays:
        raise OrderActionError(
            400, f"{schedule.vendor} doesn't deliver on {delivery_date.strftime('%a %b')} {delivery_date.day}."
        )
    return schedule.vendor


# --- Order already placed ----------------------------------------------------------------


def _existing_outside(
    db: Session, vendor: str, for_delivery_date: Optional[dt.date], now: dt.datetime
) -> Optional[PlacedOrder]:
    stmt = select(PlacedOrder).where(
        func.lower(func.trim(PlacedOrder.supplier)) == vendor_key(vendor),
        PlacedOrder.status == STATUS_PLACED,
        PlacedOrder.source == SOURCE_OUTSIDE,
    )
    if for_delivery_date is not None:
        stmt = stmt.where(PlacedOrder.for_delivery_date == for_delivery_date)
    else:
        stmt = stmt.where(PlacedOrder.placed_at >= now - DUPLICATE_WINDOW)
    return db.execute(stmt.order_by(PlacedOrder.placed_at.desc(), PlacedOrder.id.desc())).scalars().first()


def place_outside(
    db: Session,
    user: User,
    vendor: str,
    for_delivery_date: Optional[dt.date] = None,
    note: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> Tuple[PlacedOrder, bool]:
    """"Order already placed": a placed order with no lines, source outside.
    Returns (row, duplicate) - the same cycle marked twice (or a double tap)
    returns the row already there."""
    now = now or utcnow()
    note = clean_note(note)
    if for_delivery_date is not None:
        name = _check_cycle(db, vendor, for_delivery_date)
    else:
        name = resolve_vendor(db, vendor)
    with _write_lock:
        existing = _existing_outside(db, name, for_delivery_date, now)
        if existing is not None:
            return existing, True
        row = PlacedOrder(
            supplier=name,
            item_count=0,
            items_json="[]",
            placed_by_user_id=user.id,
            placed_by_name=user.username,
            placed_at=now,
            status=STATUS_PLACED,
            source=SOURCE_OUTSIDE,
            note=note,
            for_delivery_date=for_delivery_date,
        )
        db.add(row)
        db.commit()
    db.refresh(row)
    return row, False


def undo_outside(db: Session, order_id: int) -> PlacedOrder:
    """Undo "Order already placed": deletes that row. Returns the deleted
    row (detached) for the answer. Only an outside row - an order placed from
    the cart has lines and history and is never deleted here."""
    with _write_lock:
        row = db.get(PlacedOrder, order_id)
        if row is None:
            raise OrderActionError(404, "That order wasn't found (it may already be undone).")
        if row.source != SOURCE_OUTSIDE:
            raise OrderActionError(
                400, "Only an order marked \"Order already placed\" can be undone here."
            )
        db.delete(row)
        db.commit()
    return row


# --- Skip this week / Other ---------------------------------------------------------------


def skip_cycle(
    db: Session,
    user: User,
    vendor: str,
    delivery_date: dt.date,
    reason: Optional[str] = None,
    note: Optional[str] = None,
) -> VendorOrderSkip:
    """Skip this week (reason None) or Other (reason "other", note
    required) for the order for `vendor`'s `delivery_date` delivery. A cycle
    already skipped is updated (Skip -> Other or the other way round)."""
    note = clean_note(note)
    if reason not in (None, SKIP_REASON_OTHER):
        raise OrderActionError(400, "Pick Skip this week or Other.")
    if reason == SKIP_REASON_OTHER and not note:
        raise OrderActionError(400, "Add a note for Other.")
    name = _check_cycle(db, vendor, delivery_date)
    with _write_lock:
        row = db.execute(
            select(VendorOrderSkip).where(
                func.lower(func.trim(VendorOrderSkip.vendor)) == vendor_key(name),
                VendorOrderSkip.delivery_date == delivery_date,
            )
        ).scalars().first()
        if row is None:
            row = VendorOrderSkip(vendor=name, delivery_date=delivery_date, created_at=utcnow())
            db.add(row)
        row.reason = reason
        row.note = note
        row.skipped_by_name = user.username
        row.skipped_by_user_id = user.id
        db.commit()
    db.refresh(row)
    return row


def unskip_cycle(db: Session, vendor: str, delivery_date: dt.date) -> bool:
    """Undo Skip this week / Other. False = that order wasn't skipped."""
    with _write_lock:
        rows = list(
            db.execute(
                select(VendorOrderSkip).where(
                    func.lower(func.trim(VendorOrderSkip.vendor)) == vendor_key(vendor),
                    VendorOrderSkip.delivery_date == delivery_date,
                )
            ).scalars()
        )
        for row in rows:
            db.delete(row)
        db.commit()
    return bool(rows)


# --- Output ------------------------------------------------------------------------------


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def skip_handled(row: VendorOrderSkip) -> dict:
    kind = SKIP_REASON_OTHER if row.reason == SKIP_REASON_OTHER else "skip"
    return {
        "kind": kind,
        "label": HANDLED_LABELS[kind],
        "note": row.note,
        "by_name": row.skipped_by_name,
        "at": _iso(row.created_at),
        "order_id": None,
        "can_undo": True,
    }


def placed_handled(row: PlacedOrder) -> dict:
    outside = row.source == SOURCE_OUTSIDE
    kind = "placed_outside" if outside else "placed"
    return {
        "kind": kind,
        "label": HANDLED_LABELS[kind],
        "note": row.note,
        "by_name": row.placed_by_name,
        "at": _iso(row.placed_at),
        "order_id": row.id,
        # An order placed from the cart is undone on Placed Orders, not here.
        "can_undo": outside,
    }


def action_out(action: str, vendor: str, delivery_date: Optional[dt.date], handled: dict, duplicate: bool) -> dict:
    """POST /api/order-actions answer."""
    when = f" for the {delivery_date.strftime('%a %b')} {delivery_date.day} delivery" if delivery_date else ""
    messages = {
        "placed": f"{vendor}: order marked as already placed{when}.",
        "skip": f"{vendor}: order skipped{when}.",
        "other": f"{vendor}: order hidden{when}.",
    }
    return {
        "action": action,
        "action_label": ACTION_LABELS[action],
        "vendor": vendor,
        "delivery_date": delivery_date.isoformat() if delivery_date else None,
        "order_id": handled.get("order_id"),
        "note": handled.get("note"),
        "handled": handled,
        "duplicate": duplicate,
        "message": messages[action],
    }


def outside_orders_active(db: Session, today: dt.date, lead_time_days: int) -> List[dict]:
    """Vendors that are "Ordered" right now because of an "Order already
    placed" (the Critical vendors sheet's list, with Undo)."""
    from app.services.reorder import ordered_vendors

    ordered = ordered_vendors(db, today, lead_time_days)
    ids = [o["order_id"] for o in ordered.values() if o.get("source") == SOURCE_OUTSIDE]
    if not ids:
        return []
    rows: Dict[int, PlacedOrder] = {
        r.id: r for r in db.execute(select(PlacedOrder).where(PlacedOrder.id.in_(ids))).scalars()
    }
    out = []
    for o in ordered.values():
        row = rows.get(o["order_id"])
        if row is None:
            continue
        out.append(
            {
                "order_id": row.id,
                "vendor": row.supplier,
                "placed_at": _iso(row.placed_at),
                "placed_by_name": row.placed_by_name,
                "note": row.note,
                "for_delivery_date": row.for_delivery_date.isoformat() if row.for_delivery_date else None,
                "ordered_until": o["ordered_until"],
                "placed_day": to_restaurant_date(row.placed_at).isoformat(),
            }
        )
    out.sort(key=lambda r: r["placed_at"] or "", reverse=True)
    return out
