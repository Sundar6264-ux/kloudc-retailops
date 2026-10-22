"""Suggested order quantity (Oct 14 batch, X1): "how many should I order?"
next to every Supplier Projection item and every cart line.

Owner: "I add products to cart by looking at Supplier Projection but I don't
know how many to order because I don't see projection in the cart". The
suggestion is NOT a new forecast - it is the Need figure the projection page
already shows, turned into a cart quantity, with every number it is built
from listed beside it (the owner objected before to quantities that appeared
without an explanation - see the skill's "qty defaults to 1" session).

The rule (also in the glossary, `suggested_order_qty`, and on the Help page):

1. units = the projection's need to order over the order window: projected
   sales over the window + safety buffer - on hand (a negative count counts as
   0), rounded up to whole units (a shortfall under 0.1 rounds to 0). With
   the legacy method (no safety buffer) it is projected sales - on hand.
2. minus units of the same item already in OTHER vendors' carts (an item
   several vendors sell must not be ordered twice). Never below 0.
3. If the pack size is known (this vendor's cart line has Case of > 1, else
   - only when the item is NOT in this vendor's cart - the inventory file's
   receiving units give a case size, else the latest placed order from this
   vendor with that item had one), qty = units / Case of,
   rounded up to whole cases. Otherwise qty = units. An item already in this
   vendor's cart is always in that line's own unit (a Case of 1 there means
   units): "Use N" and the In cart box change only the line's qty, so a
   suggestion in another pack size would order N units where N cases were
   meant (VX12).
4. 0 = enough stock for the window. On hand not counted (Toast doesn't track
   the item): qty = the projected sales for the window, rounded up, and the
   basis says the stock was not subtracted.

When the vendor's next delivery has already been ordered (a placed order
for it - the Vendor Calendar's own rule), the basis says so instead of
"order by today", and an item on that order says how many are on order. The
quantity is not reduced by it (incoming stock isn't subtracted anywhere -
the owner's call); it is shown so it isn't ordered twice by mistake (VX12).

Nothing here writes anything, and the suggestion never changes what counts as
an order or whether a vendor is urgent (vendor minimum orders: that stays the
Reorder / calendar rules' business).
"""

import datetime as dt
import json
import math
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CartItem, InventorySnapshot, PlacedOrder
from app.services.demand_forecast import DEFAULT_CONFIG
from app.services.receiving_units import case_size_value
from app.services.supplier_projection import _container_qty_by_name
from app.services.vendor_schedule import decode_weekdays, get_vendor_schedule_by_name
from app.timeutil import restaurant_today, to_restaurant_date

# The "(avg 9)" beside on hand: the average daily count over this many days.
AVG_ON_HAND_DAYS = 28
# Same tolerance as demand_forecast.ForecastConfig.round_down_below: a
# shortfall under 0.1 unit is "enough stock", not "order 1".
ROUND_DOWN_BELOW = DEFAULT_CONFIG.round_down_below
# How far back to look for a pack size on a placed order.
CASE_LOOKBACK_ORDERS = 60

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _norm(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def whole_units(value: Optional[float]) -> Optional[int]:
    """Round a shortfall up to whole units; under ROUND_DOWN_BELOW is 0."""
    if value is None:
        return None
    if not math.isfinite(value) or value < ROUND_DOWN_BELOW:
        return 0
    return int(math.ceil(value - ROUND_DOWN_BELOW - 1e-9))


def fmt_num(value: Optional[float]) -> str:
    """6 -> "6", 3.456 -> "3.5", -915.095 -> "-915.1"."""
    if value is None:
        return "-"
    r = round(float(value), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _fmt_rate(weekly: Optional[float]) -> str:
    if weekly is None:
        return "-"
    return fmt_num(weekly) if weekly < 10 else str(int(round(weekly)))


def _fmt_day(day: dt.date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {_MONTHS[day.month - 1]} {day.day}"


def window_label(days: int) -> str:
    named = {7: "1 week", 14: "2 weeks", 21: "3 weeks", 30: "1 month", 60: "2 months", 90: "3 months"}
    return named.get(days, f"{days} days")


# --------------------------------------------------------------------------
# Lookups (one query each, for a whole vendor / cart)
# --------------------------------------------------------------------------


def vendor_delivery(db: Session, vendor: str, today: dt.date) -> Optional[dict]:
    """The vendor's next delivery it can still be ordered for, from its Vendor
    Order Calendar schedule: {lead_days, next_delivery, order_by, label}. None
    when it has no active schedule."""
    schedule = get_vendor_schedule_by_name(db, vendor) if vendor else None
    if schedule is None or not schedule.active:
        return None
    weekdays = decode_weekdays(schedule.delivery_weekdays)
    if not weekdays:
        return None
    lead = schedule.lead_days or 0
    for i in range(0, 15 + lead):
        day = today + dt.timedelta(days=i)
        if day.weekday() in weekdays and (day - dt.timedelta(days=lead)) >= today:
            order_by = day - dt.timedelta(days=lead)
            when = "today" if order_by == today else _fmt_day(order_by)
            order = cycle_order(db, vendor, day, weekdays)
            if order is not None:
                placed_day = to_restaurant_date(order.placed_at)
                label = f"next delivery {_fmt_day(day)} (already ordered {_fmt_day(placed_day)})"
            else:
                label = f"next delivery {_fmt_day(day)} (order by {when})"
            return {
                "lead_days": lead,
                "next_delivery": day.isoformat(),
                "order_by": order_by.isoformat(),
                "label": label,
                # VX12 (additive): the placed order already covering this
                # delivery, if any - {order_id, placed_on, source}.
                "ordered": {
                    "order_id": order.id,
                    "placed_on": to_restaurant_date(order.placed_at).isoformat(),
                    "source": order.source or "app",
                }
                if order is not None
                else None,
                "_order_lines": _order_units(order) if order is not None else {},
            }
    return None


def cycle_order(db: Session, vendor: str, delivery: dt.date, weekdays: List[int]) -> Optional[PlacedOrder]:
    """The placed order (status "placed", outside orders included) for this
    vendor's `delivery`, by the shared rule (services/order_cycle.py - the
    Vendor Calendar and Reorder use the same one). None if not ordered yet.
    `weekdays` is kept for callers; the schedule's own days and lead are used."""
    from app.services.order_cycle import OrderCycles

    floor = dt.datetime.combine(delivery - dt.timedelta(days=22), dt.time.min, dt.timezone.utc)
    cycles = OrderCycles(db, since=floor)
    key = _norm(vendor)
    best = None
    for row in db.execute(
        select(PlacedOrder).where(
            PlacedOrder.status == "placed",
            (PlacedOrder.placed_at >= floor) | (PlacedOrder.for_delivery_date == delivery),
        )
    ).scalars():
        if _norm(row.supplier) != key or cycles.delivery_for(row) != delivery:
            continue
        if best is None or (row.placed_at, row.id) > (best.placed_at, best.id):
            best = row
    return best


def _order_units(order: PlacedOrder) -> Dict[str, float]:
    """item_id -> units (qty x Case of) on a placed order."""
    try:
        lines = json.loads(order.items_json or "[]")
    except ValueError:
        return {}
    out: Dict[str, float] = defaultdict(float)
    for line in lines if isinstance(lines, list) else []:
        if not isinstance(line, dict) or not line.get("item_id"):
            continue
        try:
            units = float(line.get("qty") or 0) * float(line.get("case_of") or 1)
        except (TypeError, ValueError):
            continue
        if math.isfinite(units) and units > 0:
            out[line["item_id"]] += units
    return dict(out)


def public_delivery(delivery: Optional[dict]) -> Optional[dict]:
    """The delivery object as sent to clients (without the internal lines)."""
    if delivery is None:
        return None
    return {k: v for k, v in delivery.items() if not k.startswith("_")}


def known_case_sizes(
    db: Session,
    vendor: str,
    item_ids: Iterable[str],
    cart_rows: Optional[List[CartItem]] = None,
    inventory_cases: Optional[Dict[str, float]] = None,
) -> Dict[str, Tuple[float, str]]:
    """item_id -> (case_of, source) for items whose pack size is known for
    this vendor: its cart line's Case of (> 1), else the case size from the
    inventory file's receiving units (`inventory_cases`, source
    "receiving_units"), else the newest placed order from this vendor
    listing the item with a Case of > 1. A Case of 1 is the default and says
    nothing, so it never counts as known. A POS without receiving units
    (Clover, a plain CSV) simply has no `inventory_cases`."""
    wanted = {i for i in item_ids if i}
    out: Dict[str, Tuple[float, str]] = {}
    if not wanted:
        return out
    key = _norm(vendor)
    rows = cart_rows if cart_rows is not None else db.execute(select(CartItem)).scalars().all()
    in_cart = set()
    for row in rows:
        if row.item_id in wanted and _norm(row.supplier) == key:
            # The cart line decides, Case of 1 included (= units): its qty is
            # what "Use N" / the In cart box change (VX12).
            in_cart.add(row.item_id)
            if (row.case_of or 0) > 1:
                out[row.item_id] = (float(row.case_of), "cart")
    missing = wanted - in_cart
    # Owner: "Order qty has to be in cases ALWAYS": the receiving units' case
    # size, for every item not already in this vendor's cart.
    for item_id in list(missing):
        size = case_size_value((inventory_cases or {}).get(item_id))
        if size is not None:
            out[item_id] = (size, "receiving_units")
            missing.discard(item_id)
    if not missing:
        return out
    orders = db.execute(
        select(PlacedOrder)
        .where(PlacedOrder.status == "placed")
        .order_by(PlacedOrder.placed_at.desc(), PlacedOrder.id.desc())
        .limit(CASE_LOOKBACK_ORDERS * 4)
    ).scalars()
    seen = 0
    for order in orders:
        if _norm(order.supplier) != key:
            continue
        seen += 1
        if seen > CASE_LOOKBACK_ORDERS:
            break
        try:
            lines = json.loads(order.items_json or "[]")
        except ValueError:
            continue
        for line in lines if isinstance(lines, list) else []:
            if not isinstance(line, dict):
                continue
            item_id = line.get("item_id")
            if item_id not in missing or item_id in out:
                continue
            try:
                case_of = float(line.get("case_of") or 0)
            except (TypeError, ValueError):
                continue
            if math.isfinite(case_of) and case_of > 1:
                out[item_id] = (case_of, "last_order")
    return out


def other_cart_units(
    vendor: str, item_ids: Iterable[str], cart_rows: List[CartItem]
) -> Dict[str, List[dict]]:
    """item_id -> [{vendor, units}] for the same item sitting in OTHER vendors'
    carts (units = qty x Case of)."""
    wanted = {i for i in item_ids if i}
    key = _norm(vendor)
    by_item: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in cart_rows:
        if row.item_id in wanted and _norm(row.supplier) != key:
            by_item[row.item_id][row.supplier] += float(row.qty or 0) * float(row.case_of or 1)
    return {
        item_id: [{"vendor": v, "units": round(u, 2)} for v, u in sorted(vendors.items()) if u > 0]
        for item_id, vendors in by_item.items()
    }


def avg_on_hand(
    db: Session, item_ids: Iterable[str], latest_inventory: dict, as_of: dt.date, days: int = AVG_ON_HAND_DAYS
) -> Dict[str, Tuple[float, int]]:
    """item_id -> (average daily on hand, days counted) over the `days`
    snapshot dates up to and including `as_of` - the W3 weekly table's rule
    (weekly_sales.daily_on_hand): only days the item's own row has a count,
    plus its Container twin's count that day; negatives averaged as is."""
    wanted = [i for i in item_ids if i]
    if not wanted:
        return {}
    containers = _container_qty_by_name(latest_inventory)
    twin: Dict[str, str] = {}
    for item_id in wanted:
        snap = latest_inventory.get(item_id)
        found = containers.get(snap.name) if snap is not None else None
        if found and found[0] != item_id:
            twin[item_id] = found[0]
    ids = sorted(set(wanted) | set(twin.values()))
    start = as_of - dt.timedelta(days=days - 1)
    end = as_of + dt.timedelta(days=1)
    counts: Dict[str, Dict[dt.date, float]] = defaultdict(dict)
    for chunk_start in range(0, len(ids), 800):
        chunk = ids[chunk_start : chunk_start + 800]
        stmt = select(
            InventorySnapshot.item_id, InventorySnapshot.snapshot_date, InventorySnapshot.inventory_quantity
        ).where(
            InventorySnapshot.item_id.in_(chunk),
            InventorySnapshot.snapshot_date >= start,
            InventorySnapshot.snapshot_date < end,
            InventorySnapshot.inventory_quantity.is_not(None),
        )
        for rid, day, qty in db.execute(stmt):
            counts[rid][day] = float(qty)
    out: Dict[str, Tuple[float, int]] = {}
    for item_id in wanted:
        own = counts.get(item_id, {})
        if not own:
            continue
        extra = counts.get(twin.get(item_id, ""), {})
        values = [qty + extra.get(day, 0.0) for day, qty in own.items()]
        out[item_id] = (round(sum(values) / len(values), 1), len(values))
    return out


# --------------------------------------------------------------------------
# The suggestion itself (pure - tested directly)
# --------------------------------------------------------------------------


def suggest(
    row: dict,
    horizon_days: int,
    case: Optional[Tuple[float, str]] = None,
    other_carts: Optional[List[dict]] = None,
    avg: Optional[Tuple[float, int]] = None,
    delivery: Optional[dict] = None,
) -> Optional[dict]:
    """The `suggested_order` object for one projection row (see the module
    docstring for the rule). None when the row has no projection for
    `horizon_days`."""
    projection = (row.get("projections") or {}).get(str(horizon_days))
    if projection is None:
        return None
    on_hand = row.get("on_hand_qty")
    stock_known = on_hand is not None
    projected = projection.get("projected_demand")
    if stock_known:
        need = projection.get("need_to_order")
        need_units = whole_units(need if need is not None else 0.0)
    else:
        need_units = whole_units(projected or 0.0)
    elsewhere = sum(o["units"] for o in (other_carts or []))
    units = max(0, need_units - whole_units(elsewhere)) if elsewhere > 0 else need_units

    case_of = case[0] if case else None
    qty = int(math.ceil(units / case_of - 1e-9)) if case_of and units > 0 else units

    weekly = row.get("avg_weekly_demand")
    parts = [f"sold ~{_fmt_rate(weekly)}/week" if weekly else "no sales in the sales window"]
    if stock_known:
        hand = f"on hand {fmt_num(on_hand)}"
        if on_hand < 0:
            hand += " (counted as 0)"
        if avg is not None:
            hand += f" (avg {fmt_num(avg[0])})"
        parts.append(hand)
    else:
        parts.append("on hand not counted - stock not subtracted")
    parts.append(delivery["label"] if delivery else "no delivery schedule")
    on_order = (delivery or {}).get("_order_lines", {}).get(row.get("item_id")) if delivery else None
    if on_order:
        parts.append(f"{fmt_num(on_order)} on order for {_fmt_day(dt.date.fromisoformat(delivery['next_delivery']))}")
    if other_carts:
        parts.append(
            ", ".join(f"{fmt_num(o['units'])} already in {o['vendor']}'s cart" for o in other_carts)
        )
    window = window_label(horizon_days)
    if qty == 0 and stock_known:
        parts.append(f"enough stock for {window}")
    elif qty == 0:
        parts.append(f"no sales expected in {window} - suggested 0")
    elif case_of:
        cases = "case" if qty == 1 else "cases"
        parts.append(
            f"suggested {qty} {cases} of {fmt_num(case_of)} ({fmt_num(qty * case_of)} units) for {window}"
        )
    else:
        parts.append(f"suggested {qty} for {window}")

    return {
        "horizon_days": horizon_days,
        # What goes in the cart's Qty box: cases when the pack size is known.
        "qty": qty,
        "units": units,
        "need_units": need_units,
        "case_of": case_of,
        "case_of_source": case[1] if case else None,
        # Additive: the same pack size under the PO lines' name (null = the
        # qty is in units).
        "case_size": case_of,
        "weekly_demand": weekly,
        "projected_demand": projected,
        "safety_buffer": projection.get("safety_buffer"),
        "on_hand": on_hand,
        "stock_known": stock_known,
        "avg_on_hand": avg[0] if avg else None,
        "avg_on_hand_days": avg[1] if avg else None,
        "other_carts": other_carts or [],
        "next_delivery": delivery["next_delivery"] if delivery else None,
        "order_by": delivery["order_by"] if delivery else None,
        "lead_days": delivery["lead_days"] if delivery else None,
        # VX12 (additive): the next delivery is already ordered / units of
        # this item on that order (shown, never subtracted).
        "delivery_ordered": bool(delivery and delivery.get("ordered")),
        "on_order_units": round(on_order, 2) if on_order else None,
        "enough_stock": stock_known and qty == 0,
        "basis_parts": parts,
        "basis": " · ".join(parts),
    }


def compact_basis(s: Optional[dict]) -> Optional[str]:
    """The cart line's short form: "~14/week · suggested 10 · on hand 6"."""
    if not s:
        return None
    parts = [f"~{_fmt_rate(s['weekly_demand'])}/week" if s["weekly_demand"] else "no sales"]
    if s["enough_stock"]:
        parts.append(f"enough for {window_label(s['horizon_days'])}")
    elif s["case_of"]:
        parts.append(f"suggested {s['qty']} x {fmt_num(s['case_of'])}")
    else:
        parts.append(f"suggested {s['qty']}")
    parts.append(f"on hand {fmt_num(s['on_hand'])}" if s["stock_known"] else "on hand not counted")
    return " · ".join(parts)


# --------------------------------------------------------------------------
# Attaching to the two responses
# --------------------------------------------------------------------------


def attach_to_projection(
    db: Session, vendor: str, items: List[dict], horizon_days: int, as_of: dt.date, latest_inventory: dict
) -> dict:
    """Adds `suggested_order` to each Supplier Projection item (for the
    response's first horizon = the page's order window) and returns the
    top-level `order_window` object."""
    cart_rows = db.execute(select(CartItem)).scalars().all()
    ids = [i["item_id"] for i in items]
    inventory_cases = {i["item_id"]: i.get("case_size") for i in items if i.get("case_size")}
    cases = known_case_sizes(db, vendor, ids, cart_rows, inventory_cases)
    others = other_cart_units(vendor, ids, cart_rows)
    avgs = avg_on_hand(db, ids, latest_inventory, as_of)
    # The next delivery is about the real today, even when the sales window
    # ends earlier (a custom lookback end date) - VX12.
    delivery = vendor_delivery(db, vendor, max(as_of, restaurant_today()))
    for item in items:
        item_id = item["item_id"]
        item["suggested_order"] = suggest(
            item, horizon_days, cases.get(item_id), others.get(item_id), avgs.get(item_id), delivery
        )
    return {
        "horizon_days": horizon_days,
        "label": window_label(horizon_days),
        "avg_on_hand_days": AVG_ON_HAND_DAYS,
        "delivery": public_delivery(delivery),
    }


def cart_projection_context(
    db: Session,
    rows: List[CartItem],
    lookback_days: int,
    horizon_days: int,
    as_of: dt.date,
    method: Optional[str] = None,
) -> Dict[int, dict]:
    """cart row id -> `projection` object for every cart line with an item in
    the latest upload: the line's vendor's suggestion (its own Case of when
    > 1) plus `compact` text. Hand-added lines get nothing."""
    from app.services.reorder import latest_inventory_by_item
    from app.services.supplier_projection import project_items

    ids = sorted({r.item_id for r in rows if r.item_id})
    if not ids:
        return {}
    projected = project_items(db, ids, lookback_days, [horizon_days], as_of, method)
    if not projected:
        return {}
    latest = latest_inventory_by_item(db)
    avgs = avg_on_hand(db, list(projected), latest, as_of)
    by_vendor: Dict[str, List[CartItem]] = defaultdict(list)
    for r in rows:
        if r.item_id in projected:
            by_vendor[r.supplier].append(r)
    out: Dict[int, dict] = {}
    for vendor, vendor_rows in by_vendor.items():
        vendor_ids = [r.item_id for r in vendor_rows]
        inventory_cases = {i: projected[i].get("case_size") for i in vendor_ids}
        cases = known_case_sizes(db, vendor, vendor_ids, rows, inventory_cases)
        others = other_cart_units(vendor, vendor_ids, rows)
        delivery = vendor_delivery(db, vendor, as_of)
        for r in vendor_rows:
            s = suggest(
                projected[r.item_id], horizon_days, cases.get(r.item_id), others.get(r.item_id),
                avgs.get(r.item_id), delivery,
            )
            if s is None:
                continue
            s["lookback_days"] = lookback_days
            s["compact"] = compact_basis(s)
            out[r.id] = s
    return out
