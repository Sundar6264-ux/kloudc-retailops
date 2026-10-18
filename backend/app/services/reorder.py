"""Reorder trigger: forecasted demand over vendor lead time vs. latest on-hand count.

No PAR levels (confirmed unused by the store - see spec). Vendor lead time is a
temporary manual input (query parameter) rather than pulled from
`vendors_reference`, because the Toast credentials currently in use don't have
Purchasing & Receiving API scope (confirmed via a live 403) - that table is
scaffolded but empty. Once vendor data is available, lead_time_days should be
looked up per-item/vendor from `vendors_reference` instead of passed in flat.

Only items with a known on-hand quantity (i.e. present in the latest inventory
snapshot with a non-null `inventory_quantity`) are considered - an item absent
from the snapshot might be a prepared kitchen item never tracked in the retail
export, not an out-of-stock retail product, so defaulting it to on_hand=0 would
falsely flag it as needing reorder.
"""

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import group_order
from app.demo import hide_demo_clause
from app.models import InventorySnapshot, InvoiceSubmission, PlacedOrder, PurchaseLogEntry
from app.services.category_overrides import apply_overrides
from app.services.forecast import forecast_daily_demand
from app.services.inventory_api import overlay_live_stock
from app.services.item_vendors import vendors_of
from app.services.product_match import code_fields
from app.services.vendor_schedule import decode_weekdays, get_vendor_schedule_by_name
from app.timeutil import to_restaurant_date

logger = logging.getLogger(__name__)

# Worst-first order for "the vendor's status is its worst item's status" -
# index doubles as a severity rank (0 = worst). check_count sits between
# order_now and can_wait: it's something to act on (fix the count), but not
# an ordering emergency on its own.
URGENCY_TIERS = [
    ("critical", "Critical"),
    ("order_now", "Order now"),
    ("check_count", "Check count"),
    # Not a stock tier: the vendor has an order on the way (ordered_vendors).
    # Ranked after the tiers that need action, so ordered vendors group
    # after the urgent ones.
    ("ordered", "Ordered"),
    ("can_wait", "Can wait"),
    ("low_priority", "Low priority"),
]
_URGENCY_LABEL = dict(URGENCY_TIERS)
_URGENCY_RANK = {key: i for i, (key, _label) in enumerate(URGENCY_TIERS)}

# Tier thresholds, agreed with the owner against real data (Sep 2026). Before
# this, 64% of reorder items showed 0 or less on hand, but 78 of those 245
# still sold in the last 7 days - the Toast count was wrong, not the shelf -
# and anything at 0 was critical regardless of demand, so nearly every vendor
# showed Critical (and then, with a 5-item vendor rule, nearly every vendor
# showed Order now).
CRITICAL_MIN_WEEKLY_SALES = 2.0  # critical only if it sells at least this a week
CRITICAL_MAX_DAYS_LEFT = 2.0  # ...and is empty or has at most this many days left
EMPTY_ORDER_NOW_MIN_WEEKLY_SALES = 1.0  # empty but slower than this -> low priority
# A vendor is critical with this many critical items, or with any critical
# item that sells FAST_SELLER_WEEKLY_SALES+ a week (one empty best-seller
# matters more than several slow ones). Fewer -> order now.
VENDOR_CRITICAL_MIN_ITEMS = 3
FAST_SELLER_WEEKLY_SALES = 7.0
# Vendors have minimum orders - nobody places an order for 1-2 items (owner,
# Sep 2026). A vendor is only Critical / Order now once it has at least this
# many items that are critical or order-now between them; fewer and it shows
# "Can wait" (its tier counts still show the urgent items).
VENDOR_MIN_ORDER_ITEMS = 5
# A vendor with no delivery in this many days is one the store stopped
# ordering from: never urgent, listed last and flagged inactive.
VENDOR_INACTIVE_AFTER_DAYS = 45
# "Ordered" (owner, Sep 2026): a vendor with a placed order (Mark placed) is
# waiting on that delivery, not urgent - its stock only changes once the
# delivery arrives. The status holds from the order until a delivery is
# recorded after it, or until the expected delivery + this many days passes.
ORDERED_GRACE_DAYS = 3
# Invoice types that mean goods arrived (a return or "something else" doesn't,
# and neither does a "transfer" - an internal store <-> container movement).
DELIVERY_INVOICE_TYPES = ("invoice", "untracked")
# Upload types that are never a vendor delivery at all (kept out of the
# "last delivery" activity below, which otherwise counts any upload).
NON_VENDOR_INVOICE_TYPES = ("transfer",)


def _urgency(
    on_hand_qty: Optional[float],
    avg_daily_demand: float,
    lead_time_days: float,
    recent_7_day_sales: Optional[float] = None,
) -> dict:
    """How soon a candidate needs attention. `lead_time_days` is the vendor's
    real days until its next delivery when a Vendor Calendar schedule exists,
    otherwise the lead time entered on the page.

      - check_count: 0 or less on hand but it sold in the last 7 days - the
        Toast count is wrong (you can't sell what isn't there), so fix the
        count rather than order blind. Only when recent sales are known
        (the availability forecast; legacy mode has no recent_7_day_sales).
      - critical: sells CRITICAL_MIN_WEEKLY_SALES+ a week AND is either truly
        empty (0 or less, no recent sales) or has CRITICAL_MAX_DAYS_LEFT days
        or less left - losing sales now, or within a couple of days.
      - order_now: runs out within the lead time, so it must go on this
        order (includes empty items selling 1-2 a week).
      - can_wait: runs out between the lead time and twice the lead time -
        can go on the next order.
      - low_priority: more than twice the lead time of stock, or empty and
        selling under EMPTY_ORDER_NOW_MIN_WEEKLY_SALES a week.
    """
    on_hand_qty = on_hand_qty or 0
    weekly = avg_daily_demand * 7
    if on_hand_qty <= 0:
        if recent_7_day_sales is not None and recent_7_day_sales > 0:
            key = "check_count"
        elif weekly >= CRITICAL_MIN_WEEKLY_SALES:
            key = "critical"
        elif weekly >= EMPTY_ORDER_NOW_MIN_WEEKLY_SALES:
            key = "order_now"
        else:
            key = "low_priority"
    else:
        days_of_cover = (on_hand_qty / avg_daily_demand) if avg_daily_demand > 0 else float("inf")
        if days_of_cover <= CRITICAL_MAX_DAYS_LEFT and weekly >= CRITICAL_MIN_WEEKLY_SALES:
            key = "critical"
        elif days_of_cover <= lead_time_days:
            key = "order_now"
        elif days_of_cover <= lead_time_days * 2:
            key = "can_wait"
        else:
            key = "low_priority"
    return {"tier": key, "label": _URGENCY_LABEL[key]}


def _item_urgency(c: dict, lead_time_days: float) -> dict:
    forecast = c.get("forecast") or {}
    return _urgency(c["on_hand_qty"], c["avg_daily_demand"], lead_time_days, forecast.get("recent_7_day_sales"))


def _vendor_urgency(items: list[dict], inactive: bool = False) -> dict:
    """The vendor's most urgent item tier, except:
      - Critical / Order now need VENDOR_MIN_ORDER_ITEMS urgent items (a real
        order, not 1-2 items); fewer -> the worst of the remaining tiers, at
        least "Can wait".
      - Critical also needs VENDOR_CRITICAL_MIN_ITEMS critical items or one
        critical fast seller; otherwise Order now.
      - An inactive vendor is always Low priority."""
    if inactive:
        return {"tier": "low_priority", "label": _URGENCY_LABEL["low_priority"]}
    urgent = [i for i in items if i["urgency"]["tier"] in ("critical", "order_now")]
    if len(urgent) < VENDOR_MIN_ORDER_ITEMS:
        rest = [_URGENCY_RANK[i["urgency"]["tier"]] for i in items if i not in urgent]
        rank = min(rest + ([_URGENCY_RANK["can_wait"]] if urgent else []))
        worst_tier = URGENCY_TIERS[rank][0]
        return {"tier": worst_tier, "label": _URGENCY_LABEL[worst_tier]}
    worst_tier = URGENCY_TIERS[min(_URGENCY_RANK[i["urgency"]["tier"]] for i in items)][0]
    if worst_tier == "critical":
        critical = [i for i in items if i["urgency"]["tier"] == "critical"]
        fast_seller_out = any(i["avg_daily_demand"] * 7 >= FAST_SELLER_WEEKLY_SALES for i in critical)
        if len(critical) < VENDOR_CRITICAL_MIN_ITEMS and not fast_seller_out:
            worst_tier = "order_now"
    return {"tier": worst_tier, "label": _URGENCY_LABEL[worst_tier]}


def _receipt_vendors(inventory: dict[str, InventorySnapshot]) -> tuple[dict[str, str], dict[str, dt.date]]:
    """Which vendor each Toast receiving code (`last_received_from`, e.g.
    "IN281") belongs to, and each vendor's latest delivery date.

    Toast only gives the code, not the vendor, so a code's vendor is the one
    its single-supplier items name (164 of 167 codes are unambiguous in real
    data, Sep 2026); a mixed code goes to its majority vendor."""
    votes: dict[str, dict[str, int]] = {}
    code_dates: dict[str, dt.date] = {}
    for snap in inventory.values():
        code = snap.last_received_from
        if not code:
            continue
        received = _as_date(snap.inventory_last_received)
        if received and (code not in code_dates or received > code_dates[code]):
            code_dates[code] = received
        suppliers = _split_suppliers(snap.supplier)
        if len(suppliers) == 1:
            counts = votes.setdefault(code, {})
            counts[suppliers[0]] = counts.get(suppliers[0], 0) + 1
    code_vendor = {code: max(counts, key=counts.get) for code, counts in votes.items()}
    last_delivery: dict[str, dt.date] = {}
    for code, vendor in code_vendor.items():
        day = code_dates.get(code)
        if day and (vendor not in last_delivery or day > last_delivery[vendor]):
            last_delivery[vendor] = day
    return code_vendor, last_delivery


def _as_datetime(value) -> Optional[dt.datetime]:
    """Toast's last-received value as a UTC-aware timestamp, or None when it
    has no time/zone (a bare date, or unparseable)."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or "T" not in value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # Python 3.9's fromisoformat takes exactly 0, 3 or 6 fraction digits.
    head, sep, tail = text.partition(".")
    if sep:
        digits = tail[: len(tail) - len(tail.lstrip("0123456789"))]
        text = f"{head}.{(digits + '000000')[:6]}{tail[len(digits):]}"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _as_date(value) -> Optional[dt.date]:
    """Toast's last-received value is an ISO string (or a datetime/date). A
    UTC timestamp ("...T23:20:41Z") is dated in store time - an evening
    receipt is that day's, not the next UTC day's."""
    if value is None:
        return None
    stamp = _as_datetime(value)
    if stamp is not None:
        return to_restaurant_date(stamp)
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _vendor_for_item(
    listed: list[str], code: Optional[str], code_vendor: dict[str, str], last_delivery: dict[str, dt.date]
) -> str:
    """One vendor for an item Toast lists under several: the vendor of its
    last delivery, else the listed vendor that delivered most recently, else
    the first listed. So one empty item no longer makes 3-4 vendors urgent."""
    if len(listed) == 1:
        return listed[0]
    by_code = code_vendor.get(code or "")
    if by_code in listed:
        return by_code
    recent = [v for v in listed if v in last_delivery]
    if recent:
        return max(recent, key=lambda v: last_delivery[v])
    return listed[0]


def _split_suppliers(raw: Optional[str]) -> list[str]:
    # One vendor rule for the whole app (services/item_vendors.py, M1 MT5).
    return vendors_of(raw)


def _vendor_key(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _delivery_schedule(db: Session, vendor: str) -> tuple[list[int], int]:
    """(delivery weekdays, lead days) from the vendor's active Vendor Calendar
    schedule; ([], 0) without one."""
    schedule = get_vendor_schedule_by_name(db, vendor)
    if schedule is None or not schedule.active:
        return [], 0
    weekdays = decode_weekdays(schedule.delivery_weekdays)
    return (weekdays, schedule.lead_days or 0) if weekdays else ([], 0)


def ordered_window_days(db: Session, vendor: str, placed_day: dt.date, lead_time_days: int) -> int:
    """How long a placed order keeps its vendor "ordered": days until the
    delivery it's for, plus ORDERED_GRACE_DAYS. With a Vendor Calendar
    schedule that's the first scheduled delivery the order still makes (its
    lead days before that delivery); without one, the page's lead time."""
    days = lead_time_days
    weekdays, lead = _delivery_schedule(db, vendor)
    if weekdays:
        days = next(
            i
            for i in range(0, 8 + lead)
            if (placed_day + dt.timedelta(days=i)).weekday() in weekdays and i >= lead
        )
    return days + ORDERED_GRACE_DAYS


def ordered_vendors(
    db: Session,
    as_of_date: dt.date,
    lead_time_days: int,
    toast_last_delivery: Optional[dict] = None,
) -> dict:
    """Vendors with an order on the way, keyed by lowercased vendor name:
    {"vendor", "ordered_at" (ISO), "order_id", "ordered_until" (ISO date)}.

    A vendor is ordered when its latest "placed" order (Mark placed - a
    "pdf" row is only a preview) was placed on or before `as_of_date` and
      - no delivery was recorded after it: a Delivery Review / logged
        purchase or an uploaded invoice created after the order, or a Toast
        receipt (`toast_last_delivery`, vendor -> UTC timestamp, or a date =
        a later day) after it, and
      - it's not past its window (ordered_window_days).
    With a Vendor Calendar schedule, a delivery only counts from the order
    day + the vendor's lead days: a delivery recorded earlier than that is
    the previous order arriving (orders are often marked placed at 1-3am,
    and that day's delivery is last cycle's).
    So it clears by itself once the delivery is confirmed or the window ends.
    """
    latest: dict = {}
    for row in db.execute(
        select(PlacedOrder).where(PlacedOrder.status == "placed").order_by(PlacedOrder.placed_at.desc(), PlacedOrder.id.desc())
    ).scalars():
        key = _vendor_key(row.supplier)
        placed_day = to_restaurant_date(row.placed_at)
        # "Order already placed" on a PAST order-due (W1, tied to a delivery
        # before the day it was marked) is bookkeeping for that cycle, not an
        # order on the way - it never makes the vendor Ordered, nor hides an
        # earlier real order (Verify 1).
        for_delivery = getattr(row, "for_delivery_date", None)
        if for_delivery is not None and for_delivery < placed_day:
            continue
        if key and key not in latest and placed_day <= as_of_date:
            latest[key] = row
    if not latest:
        return {}

    # Every delivery timestamp per vendor (only the latest one before an
    # order's earliest-delivery day could be the wrong cycle, so keep all).
    delivered: dict = {}

    def seen(vendor: Optional[str], when) -> None:
        key = _vendor_key(vendor)
        if key in latest and when is not None:
            delivered.setdefault(key, []).append(when)

    placed_after = min(row.placed_at for row in latest.values())
    for vendor, created in db.execute(
        select(PurchaseLogEntry.supplier, PurchaseLogEntry.created_at).where(PurchaseLogEntry.created_at > placed_after)
    ).all():
        seen(vendor, created)
    for vendor, created in db.execute(
        select(InvoiceSubmission.vendor, InvoiceSubmission.created_at).where(
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.submission_type.in_(DELIVERY_INVOICE_TYPES),
            InvoiceSubmission.created_at > placed_after,
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),  # demo uploads are no proof of delivery
        )
    ).all():
        seen(vendor, created)
    toast = {_vendor_key(v): d for v, d in (toast_last_delivery or {}).items()}

    out = {}
    for key, row in latest.items():
        placed_day = to_restaurant_date(row.placed_at)
        _weekdays, lead = _delivery_schedule(db, row.supplier)
        earliest = placed_day + dt.timedelta(days=lead)
        if any(when > row.placed_at and to_restaurant_date(when) >= earliest for when in delivered.get(key, [])):
            continue
        receipt = toast.get(key)
        if isinstance(receipt, dt.datetime):
            if receipt > row.placed_at and to_restaurant_date(receipt) >= earliest:
                continue
        elif receipt is not None and receipt > placed_day and receipt >= earliest:
            continue
        until = placed_day + dt.timedelta(days=ordered_window_days(db, row.supplier, placed_day, lead_time_days))
        if as_of_date > until:
            continue
        out[key] = {
            "vendor": row.supplier,
            "ordered_at": row.placed_at.isoformat(),
            "order_id": row.id,
            "ordered_until": until.isoformat(),
            # W1: "outside" = "Order already placed" (no lines), else None.
            "source": getattr(row, "source", None),
        }
    return out


def latest_inventory_by_item(
    db: Session, as_of_date: Optional[dt.date] = None, apply_category_overrides: bool = True
) -> dict[str, InventorySnapshot]:
    """Every item that's actually in the most recent inventory upload - as of
    right now by default, or as of a given date (the most recent upload on or
    before it) when `as_of_date` is given, e.g. to answer "what was on hand
    going into this period" for reconciliation (services/reconciliation.py)
    rather than "what's on hand today".

    Deliberately requires an item to be present in that one specific upload
    date, not just "whatever its own most recent row ever was" - the Toast
    Retail export is a full catalog dump each time (every still-active item
    gets a row on every real upload), so an item that stops appearing in
    newer uploads has been deleted/deactivated in Toast, not just
    coincidentally unchanged. Verified against real data before this changed:
    real item counts per upload have been declining steadily (5539 -> 5125
    over about 3 weeks), not a one-off gap - confirming this is a genuine
    ongoing deletion trend, not upload flakiness. Before this fix, a deleted
    item's last real row stayed "the latest" for it forever, which meant it
    never dropped out of Dead Stock or any other report reading through this
    function (a real user report: a deleted Toast item kept showing up in
    Dead Stock indefinitely).

    A genuine missed-upload gap (nobody uploaded for a few days) is still
    handled the same as before: every item is equally affected by that gap
    (none of them get a newer row until the next real upload), so they all
    still share the same "most recent upload" date and none are excluded.

    Category overrides are applied unless `apply_category_overrides=False`
    (Toast's raw values are also on each overridden item as toast_category*).
    """
    max_date_query = select(func.max(InventorySnapshot.snapshot_date))
    if as_of_date is not None:
        max_date_query = max_date_query.where(InventorySnapshot.snapshot_date <= as_of_date)
    latest_upload_date = db.execute(max_date_query).scalar_one_or_none()
    if latest_upload_date is None:
        return {}

    stmt = select(InventorySnapshot).where(InventorySnapshot.snapshot_date == latest_upload_date)
    by_item = {row.item_id: row for row in db.execute(stmt).scalars()}
    if not apply_category_overrides:
        return by_item
    # App-side category corrections (services/category_overrides.py, e.g. all
    # of one supplier's items -> Biscuits, Cookies & Rusk). Overridden items
    # come back as read-only wrappers - the ORM rows are never modified, so
    # nothing can be flushed into inventory_snapshots.
    return apply_overrides(db, by_item)


def _with_urgency_and_value(candidates: list[dict], inventory_by_item: dict, lead_time_days: int) -> list[dict]:
    for c in candidates:
        c["urgency"] = _item_urgency(c, lead_time_days)
        snap = inventory_by_item.get(c["item_id"])
        cost = snap.cost if snap is not None else None
        c["estimated_order_value"] = round(c["shortfall"] * cost, 2) if cost is not None else None
        # Additive (Oct 14, W2): every vendor SKU + the barcode, for search.
        c.update(code_fields(snap, c))
    return candidates


def compute_reorder_candidates(
    db: Session,
    lookback_days: int,
    lead_time_days: int,
    as_of_date: Optional[dt.date] = None,
    method: Optional[str] = None,
) -> list[dict]:
    """Items that will run short within `lead_time_days`, across all suppliers.

    Uses the same availability-adjusted forecast as Supplier Projection
    (services/demand_forecast.py) so the two pages agree, unless `method`
    (or the PROJECTION_METHOD setting) is "legacy". The response fields are
    unchanged - web and mobile read item_id / name / avg_daily_demand /
    forecast_over_lead_time / on_hand_qty / shortfall / supplier / category /
    inventory_snapshot_date - and new data is only added (container_qty,
    safety_buffer, forecast). Any failure falls back to the old calculation.
    """
    as_of_date = as_of_date or dt.date.today()
    method = method or get_settings().projection_method
    if method == "availability":
        try:
            return _availability_candidates(db, lookback_days, lead_time_days, as_of_date)
        except Exception:
            logger.exception("Availability-adjusted reorder failed; using the legacy average")
    return _legacy_candidates(db, lookback_days, lead_time_days, as_of_date)


def report_key(lookback_days: int, lead_time_days: int, as_of_date: dt.date, method: Optional[str]) -> tuple:
    return ("reorder", lookback_days, lead_time_days, as_of_date, method or get_settings().projection_method)


def cached_report(
    db: Session,
    lookback_days: int,
    lead_time_days: int,
    as_of_date: dt.date,
    method: Optional[str] = None,
    candidates_fn=None,
    group_fn=None,
) -> dict:
    """{"candidates", "vendors"}: compute_reorder_candidates +
    group_candidates_by_vendor, kept in memory (services/report_cache.py,
    speed #3) until an order is placed, sales sync / an inventory upload
    lands, or ~5 minutes pass. Shared between requests: copy before changing
    anything (critical-dismissal annotation goes on copies of the groups).
    `candidates_fn` / `group_fn`: the caller's own references to the two
    functions (so a module that imported them keeps working the same)."""
    from app.services import report_cache

    candidates_fn = candidates_fn or compute_reorder_candidates
    group_fn = group_fn or group_candidates_by_vendor

    def compute() -> dict:
        candidates = candidates_fn(db, lookback_days, lead_time_days, as_of_date, method)
        return {"candidates": candidates, "vendors": group_fn(db, candidates, as_of_date, lead_time_days)}

    if candidates_fn is not compute_reorder_candidates or group_fn is not group_candidates_by_vendor:
        return compute()  # a stand-in (tests' stubs): its answer can change any time, never cached
    return report_cache.cached(db, report_key(lookback_days, lead_time_days, as_of_date, method), compute)


def _legacy_candidates(db: Session, lookback_days: int, lead_time_days: int, as_of_date: dt.date) -> list[dict]:
    demand_by_item = forecast_daily_demand(db, lookback_days, as_of_date)
    # Live Toast counts on top of the latest snapshot when
    # INVENTORY_API_SNAPSHOTS is on (inventory_api.overlay_live_stock).
    inventory_by_item = overlay_live_stock(latest_inventory_by_item(db))

    candidates = []
    for item_id, demand in demand_by_item.items():
        snap = inventory_by_item.get(item_id)
        if snap is None or snap.inventory_quantity is None:
            continue  # no known on-hand count - can't assess reorder need

        forecast_over_lead_time = demand["avg_daily_demand"] * lead_time_days
        shortfall = forecast_over_lead_time - snap.inventory_quantity
        if shortfall <= 0:
            continue

        candidates.append(
            {
                "item_id": item_id,
                "name": demand["name"] or snap.name,
                "avg_daily_demand": round(demand["avg_daily_demand"], 3),
                "forecast_over_lead_time": round(forecast_over_lead_time, 2),
                "on_hand_qty": snap.inventory_quantity,
                "shortfall": round(shortfall, 2),
                "supplier": snap.supplier,
                "category": snap.category,
                "inventory_snapshot_date": snap.snapshot_date.isoformat(),
            }
        )

    candidates.sort(key=lambda c: c["shortfall"], reverse=True)
    return _with_urgency_and_value(candidates, inventory_by_item, lead_time_days)


def _availability_candidates(db: Session, lookback_days: int, lead_time_days: int, as_of_date: dt.date) -> list[dict]:
    # Imported here: supplier_projection imports this module.
    from app.services.demand_forecast import DEFAULT_CONFIG, build_models, project_item
    from app.services.supplier_projection import _container_qty_by_name, forecast_summary

    demand_by_item = forecast_daily_demand(db, lookback_days, as_of_date)
    # Live Toast counts on top of the latest snapshot when
    # INVENTORY_API_SNAPSHOTS is on (inventory_api.overlay_live_stock).
    inventory_by_item = overlay_live_stock(latest_inventory_by_item(db))
    container_qty_by_name = _container_qty_by_name(inventory_by_item)

    # Same universe as before: sold in the window, with a known on-hand count.
    universe = {
        item_id: inventory_by_item[item_id]
        for item_id in demand_by_item
        if item_id in inventory_by_item and inventory_by_item[item_id].inventory_quantity is not None
    }
    count_item_ids = {}
    for item_id, snap in universe.items():
        container = container_qty_by_name.get(snap.name)
        count_item_ids[item_id] = [item_id] + ([container[0]] if container else [])
    store_item_ids = [item_id for item_id, snap in inventory_by_item.items() if snap.supplier]
    models = build_models(db, list(universe), count_item_ids, store_item_ids, lookback_days, as_of_date)

    candidates = []
    for item_id, snap in universe.items():
        demand = demand_by_item[item_id]
        container = container_qty_by_name.get(snap.name)
        container_qty = container[1] if container else None
        on_hand = snap.inventory_quantity + (container_qty or 0.0)
        try:
            model = models[item_id]
            p = project_item(model, lead_time_days, as_of_date, on_hand, DEFAULT_CONFIG)
            if not p["recommended_order"]:
                continue
            daily = model["base_daily_demand"] * model["trend_factor"]
            row = {
                "item_id": item_id,
                "name": demand["name"] or snap.name,
                "avg_daily_demand": round(daily, 3),
                "forecast_over_lead_time": round(p["projected_demand"], 2),
                "on_hand_qty": on_hand,
                # Whole units to order, safety buffer included.
                "shortfall": p["recommended_order"],
                "supplier": snap.supplier,
                "category": snap.category,
                "inventory_snapshot_date": snap.snapshot_date.isoformat(),
                "container_qty": container_qty,
                "safety_buffer": round(p["safety_buffer"], 2),
                "forecast": forecast_summary(model, on_hand, demand["avg_daily_demand"]),
            }
        except Exception:
            logger.exception("Availability-adjusted reorder failed for item %s; using the legacy average", item_id)
            forecast_over_lead_time = demand["avg_daily_demand"] * lead_time_days
            shortfall = forecast_over_lead_time - snap.inventory_quantity
            if shortfall <= 0:
                continue
            row = {
                "item_id": item_id,
                "name": demand["name"] or snap.name,
                "avg_daily_demand": round(demand["avg_daily_demand"], 3),
                "forecast_over_lead_time": round(forecast_over_lead_time, 2),
                "on_hand_qty": snap.inventory_quantity,
                "shortfall": round(shortfall, 2),
                "supplier": snap.supplier,
                "category": snap.category,
                "inventory_snapshot_date": snap.snapshot_date.isoformat(),
            }
        candidates.append(row)

    candidates.sort(key=lambda c: c["shortfall"], reverse=True)
    return _with_urgency_and_value(candidates, inventory_by_item, lead_time_days)


def group_candidates_by_vendor(
    db: Session, candidates: list[dict], as_of_date: dt.date, lead_time_days: int = 7
) -> list[dict]:
    """Reorder Candidates, grouped by vendor instead of one flat list. An
    item Toast lists under several vendors (`"Babco;JMR Distributors LLC"`)
    goes under ONE of them - the vendor it was last received from (see
    _vendor_for_item) - so a single empty item can't make several vendors
    look urgent.

    A vendor's status comes from _vendor_urgency: it needs enough urgent items
    for a real order (VENDOR_MIN_ORDER_ITEMS) before it shows Critical / Order
    now, and a vendor with no delivery in VENDOR_INACTIVE_AFTER_DAYS is
    flagged `inactive`, never urgent, and listed last. A vendor with a Vendor
    Calendar schedule has its items re-judged against its real days until
    next delivery. `tier_counts` gives how many items fall in each tier, so a
    vendor card can show the full picture ("5 critical · 12 order now")
    rather than one label. Vendors needing the most attention sort first.

    A vendor with an order on the way (ordered_vendors) shows "Ordered"
    instead: its items are per-vendor copies with tier "ordered" plus
    `ordered_at` / `ordered_order_id` (the flat candidates list keeps each
    item's real stock tier), the vendor carries the same fields plus
    `ordered_until`, and it sorts after the vendors that need action.
    `lead_time_days` is the page's lead time, used for the ordered window of
    a vendor with no Vendor Calendar schedule.
    """
    # Imported here, not at module level: supplier_projection imports
    # latest_inventory_by_item from this module, so a top-level import here
    # would be circular.
    from app.services.supplier_projection import protection_days

    inventory = latest_inventory_by_item(db, as_of_date)
    code_vendor, last_delivery = _receipt_vendors(inventory)
    # Toast receipts as real timestamps (a receipt later the same day as an
    # order can then be told apart from one before it).
    toast_receipts: dict = dict(last_delivery)
    for snap in inventory.values():
        vendor = code_vendor.get(snap.last_received_from or "")
        stamp = _as_datetime(snap.inventory_last_received) if vendor else None
        if stamp is not None:
            prev = toast_receipts.get(vendor)
            if not isinstance(prev, dt.datetime) or stamp > prev:
                toast_receipts[vendor] = stamp
    ordered_by_vendor = ordered_vendors(db, as_of_date, lead_time_days, toast_receipts)
    # Deliveries confirmed in the app and orders placed from the cart count
    # as activity too (a vendor delivering only untracked items still shows).
    for vendor, day in db.execute(
        select(PurchaseLogEntry.supplier, func.max(PurchaseLogEntry.received_date))
        .group_by(PurchaseLogEntry.supplier)
        .order_by(*group_order(PurchaseLogEntry.supplier))
    ).all():
        if vendor and day and (vendor not in last_delivery or day > last_delivery[vendor]):
            last_delivery[vendor] = day
    # An uploaded invoice (Upload Invoice, not in the trash) is proof the
    # vendor delivered - even when the delivery was never received in Toast.
    # The typed vendor is matched to the list name ignoring case/spacing.
    known = {v.strip().lower(): v for c in candidates for v in _split_suppliers(c.get("supplier"))}
    known.update({v.strip().lower(): v for v in last_delivery})
    for typed, uploaded_at in db.execute(
        select(InvoiceSubmission.vendor, func.max(InvoiceSubmission.created_at))
        .where(
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.submission_type.not_in(NON_VENDOR_INVOICE_TYPES),
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),  # demo uploads are no proof of delivery
        )
        .group_by(InvoiceSubmission.vendor)
        .order_by(*group_order(InvoiceSubmission.vendor))
    ).all():
        if not typed or uploaded_at is None:
            continue
        vendor = known.get(typed.strip().lower(), typed.strip())
        day = to_restaurant_date(uploaded_at)
        if vendor not in last_delivery or day > last_delivery[vendor]:
            last_delivery[vendor] = day

    groups: dict[str, dict] = {}
    for c in candidates:
        listed = _split_suppliers(c.get("supplier"))
        snap = inventory.get(c["item_id"])
        vendor = (
            _vendor_for_item(listed, snap.last_received_from if snap else None, code_vendor, last_delivery)
            if listed
            else "Unassigned"
        )
        group = groups.setdefault(
            vendor,
            {"vendor": vendor, "item_count": 0, "estimated_order_value": None, "items": []},
        )
        group["item_count"] += 1
        group["items"].append(c)
        if c["estimated_order_value"] is not None:
            group["estimated_order_value"] = round(
                (group["estimated_order_value"] or 0) + c["estimated_order_value"], 2
            )

    # Confirmed orders only: a "pdf" row is just a PO PDF preview, not an
    # order (see PlacedOrder.status / purchase_order_log.py).
    last_placed_by_vendor = dict(
        db.execute(
            select(PlacedOrder.supplier, func.max(PlacedOrder.placed_at))
            .where(PlacedOrder.status == "placed")
            .group_by(PlacedOrder.supplier)
            .order_by(*group_order(PlacedOrder.supplier))
        ).all()
    )

    out = []
    for vendor, group in groups.items():
        group["protection_days"] = None if vendor == "Unassigned" else protection_days(db, vendor, as_of_date)
        if group["protection_days"] is not None:
            # This vendor has a real delivery schedule: judge its items against
            # the days until its next delivery instead of the page's lead time.
            # Copies, since an item listed under several vendors can land in a
            # different tier for each.
            group["items"] = [
                {**item, "urgency": _item_urgency(item, group["protection_days"])} for item in group["items"]
            ]
        ordered = None if vendor == "Unassigned" else ordered_by_vendor.get(_vendor_key(vendor))
        if ordered is not None:
            # Waiting on this vendor's delivery: stock numbers stay as they are
            # (informational), only the status changes. Copies, so the item
            # keeps its real status under any other vendor / the flat list.
            group["items"] = [
                {
                    **item,
                    "urgency": {"tier": "ordered", "label": _URGENCY_LABEL["ordered"]},
                    "ordered_at": ordered["ordered_at"],
                    "ordered_order_id": ordered["order_id"],
                }
                for item in group["items"]
            ]
        group["ordered_at"] = ordered["ordered_at"] if ordered else None
        group["ordered_order_id"] = ordered["order_id"] if ordered else None
        group["ordered_until"] = ordered["ordered_until"] if ordered else None
        group["ordered_source"] = ordered.get("source") if ordered else None
        tier_counts = {key: 0 for key, _label in URGENCY_TIERS}
        for item in group["items"]:
            tier_counts[item["urgency"]["tier"]] += 1
        group["tier_counts"] = tier_counts
        last_placed = last_placed_by_vendor.get(vendor)
        last_seen = last_delivery.get(vendor)
        if last_placed is not None:
            placed_day = to_restaurant_date(last_placed)
            last_seen = placed_day if last_seen is None or placed_day > last_seen else last_seen
        group["last_delivery_date"] = last_delivery[vendor].isoformat() if vendor in last_delivery else None
        # No delivery or placed order for VENDOR_INACTIVE_AFTER_DAYS (or none on
        # record at all, e.g. "Unassigned"): a vendor the store stopped using.
        group["inactive"] = last_seen is None or (as_of_date - last_seen).days > VENDOR_INACTIVE_AFTER_DAYS
        if ordered is not None and not group["inactive"]:
            group["urgency"] = {"tier": "ordered", "label": _URGENCY_LABEL["ordered"]}
        else:
            group["urgency"] = _vendor_urgency(group["items"], group["inactive"])
        # Most urgent items first (critical at the top), then biggest shortfall.
        group["items"].sort(key=lambda c: (_URGENCY_RANK[c["urgency"]["tier"]], -c["shortfall"]))
        group["last_placed_at"] = last_placed.isoformat() if last_placed else None
        out.append(group)

    out.sort(
        key=lambda g: (
            g["inactive"],
            _URGENCY_RANK[g["urgency"]["tier"]],
            -g["tier_counts"]["critical"],
            -g["item_count"],
        )
    )
    return out
