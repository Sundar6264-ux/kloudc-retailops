"""Per-supplier demand projection across multiple horizons (1wk/2wk/3wk/month, etc.).

`inventory_snapshots.supplier` sometimes lists multiple suppliers for one item,
semicolon-separated (e.g. "KP Produce;Raja Foods;Bloom Florals") - a real quirk in
the Toast Retail export, not a bug. Split those out for the supplier list and
for matching which items belong to a chosen supplier.

The daily export also carries a second "Container" row for some items - same
item name, tracking on-hand quantity held in a different physical storage
location, but with no price/cost/supplier/sales of its own (it's not a
separately-sellable line, and it's why it's excluded from `matching_items`
below). There's no captured column that labels this directly - the Toast
export column that would (something under "storage locations" / "item multi
location id", currently ignored - see inventory_parser.py) was never
confirmed against a real file. What's confirmed against real data instead:
of 29 retail items sharing a name with another row, 26 fit "one priced row +
one bare row" exactly, and the bare row always has `price IS NULL` and no
supplier. The other 3 (e.g. Banana Leaves) have two real priced rows and are
genuinely different products, not an Each/Container split - left alone. See
`_container_qty_by_name`.
"""

import datetime as dt
import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import InventorySnapshot
from app.services.demand_forecast import DEFAULT_CONFIG, build_models, exception_flags, project_item
from app.services.forecast import forecast_daily_demand
from app.services.inventory_api import overlay_live_stock
from app.services.item_vendors import vendors_of
from app.services.reorder import latest_inventory_by_item
from app.services.similar_products import aligned_codes, other_vendor_refs, split_aligned
from app.services.vendor_schedule import decode_weekdays, get_vendor_schedule_by_name

logger = logging.getLogger(__name__)

_WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Flags that put an item in the "review before ordering" pile. The rest
# (e.g. intermittent) are informational.
_REVIEW_FLAGS = {
    "insufficient_history",
    "prolonged_stockout",
    "demand_spike",
    "unusual_trend",
    "negative_inventory",
    "differs_from_history",
}


def _split_suppliers(raw: Optional[str]) -> list[str]:
    # One vendor rule for the whole app (services/item_vendors.py, M1 MT5).
    # Other modules import this name - keep it.
    return vendors_of(raw)


def _supplier_item_id_for(snap, supplier: str) -> Optional[str]:
    """`supplier_item_id` is semicolon-separated in the same order as `supplier`
    when an item has multiple vendors - pick the entry at the matching index.
    Falls back to the raw (possibly single) value if the two lists don't line
    up 1:1, which is better than showing nothing.
    """
    suppliers = _split_suppliers(snap.supplier)
    item_ids = _split_suppliers(getattr(snap, "supplier_item_id", None))
    if not item_ids:
        return None
    # Empty slots keep their place ("Raja Foods;RBest;Restaurant Depot" with
    # ";2524;856977005124": Raja Foods has no code - not "2524; 8569...").
    slots = split_aligned(snap.supplier)
    codes = aligned_codes(snap.supplier, getattr(snap, "supplier_item_id", None))
    if codes is not None and supplier in slots:
        return codes[slots.index(supplier)]
    if len(suppliers) == len(item_ids) and supplier in suppliers:
        return item_ids[suppliers.index(supplier)]
    return item_ids[0] if len(item_ids) == 1 else "; ".join(item_ids)


def _container_qty_by_name(
    latest_inventory: dict[str, InventorySnapshot]
) -> dict[str, tuple[str, float]]:
    """Name -> (item_id, qty) for the bare Container-location duplicate of a
    priced item, per the heuristic in this module's docstring. Skips any name
    with more than one bare candidate - ambiguous, and not a pattern seen in
    real data yet, so safer to leave those items un-merged than guess.
    """
    candidates: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for item_id, snap in latest_inventory.items():
        if snap.price is None and not snap.supplier and snap.inventory_quantity is not None:
            candidates[snap.name].append((item_id, snap.inventory_quantity))
    return {name: matches[0] for name, matches in candidates.items() if len(matches) == 1}


def _priced_row_by_name(
    latest_inventory: dict[str, InventorySnapshot]
) -> dict[str, tuple[str, InventorySnapshot]]:
    """Name -> (item_id, snap) for the priced/sellable "store" row - the
    counterpart to `_container_qty_by_name`'s bare row. Same safety rule:
    skip any name with more than one priced match, since that means it's
    genuinely different products sharing a name (e.g. Banana Leaves), not a
    store/Container split.
    """
    candidates: dict[str, list[tuple[str, InventorySnapshot]]] = defaultdict(list)
    for item_id, snap in latest_inventory.items():
        if snap.price is not None:
            candidates[snap.name].append((item_id, snap))
    return {name: matches[0] for name, matches in candidates.items() if len(matches) == 1}


def store_container_pairs(
    latest_inventory: dict[str, InventorySnapshot]
) -> list[tuple[str, InventorySnapshot, str, float]]:
    """Every item that has both a store (priced) row and a Container row:
    (store_item_id, store_snap, container_item_id, container_qty), paired by
    exact name - one bare row (`_container_qty_by_name`) and exactly one
    priced row (`_priced_row_by_name`). The single pairing behind Fix Issues'
    Refill from container, Location Transfer and the Store vs Container
    report, so their numbers always agree (and agree with the container_qty
    Supplier Projection adds to on-hand)."""
    priced = _priced_row_by_name(latest_inventory)
    pairs = []
    for name, (container_id, container_qty) in _container_qty_by_name(latest_inventory).items():
        store = priced.get(name)
        if store is None:
            continue
        pairs.append((store[0], store[1], container_id, container_qty))
    return pairs


def list_suppliers(db: Session) -> list[str]:
    """Every vendor named in the latest upload, sorted. Cached with the
    reports (report_cache: it changes with an upload, ~100 ms to build);
    each caller gets its own copy of the list."""
    from app.services import report_cache

    def compute() -> list:
        names = set()
        for snap in latest_inventory_by_item(db).values():
            for name in _split_suppliers(snap.supplier):
                names.add(name)
        return sorted(names)

    return list(report_cache.cached(db, ("suppliers",), compute))


def protection_days(db: Session, supplier: str, today: dt.date) -> Optional[int]:
    """Days the stock has to last from `today` - until the order placed at
    the NEXT order cycle arrives (lead time + time to the next cycle) - from
    the vendor's delivery schedule on the Vendor Order Calendar. None when
    the vendor has no schedule set up."""
    schedule = get_vendor_schedule_by_name(db, supplier)
    if schedule is None or not schedule.active:
        return None
    weekdays = decode_weekdays(schedule.delivery_weekdays)
    if not weekdays:
        return None
    lead = dt.timedelta(days=schedule.lead_days or 0)
    # First delivery that can still be ordered for, then the one after it.
    first = next(
        d
        for d in (today + dt.timedelta(days=i) for i in range(0, 15 + schedule.lead_days))
        if d.weekday() in weekdays and d - lead >= today
    )
    second = next(
        d for d in (first + dt.timedelta(days=i) for i in range(1, 8)) if d.weekday() in weekdays
    )
    return (second - today).days


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


def _legacy_fields(legacy_daily: float, horizons_days: list[int], on_hand: Optional[float]) -> dict:
    """The original calculation: average over calendar days x horizon."""
    projections = {}
    for horizon in horizons_days:
        projected_demand = round(legacy_daily * horizon, 2)
        need_to_order = round(max(0.0, projected_demand - on_hand), 2) if on_hand is not None else None
        projections[str(horizon)] = {
            "projected_demand": projected_demand,
            "need_to_order": need_to_order,
        }
    return {
        "avg_daily_demand": round(legacy_daily, 3),
        "avg_weekly_demand": round(legacy_daily * 7, 2),
        "projections": projections,
    }


def _availability_fields(
    model: dict, horizons_days: list[int], as_of_date: dt.date, on_hand: Optional[float], legacy_daily: float
) -> dict:
    daily = model["base_daily_demand"] * model["trend_factor"]
    projections = {}
    for horizon in horizons_days:
        p = project_item(model, horizon, as_of_date, on_hand, DEFAULT_CONFIG)
        projections[str(horizon)] = {
            "projected_demand": round(p["projected_demand"], 2),
            # Whole units, already including the safety buffer and net of
            # on-hand (negative counts treated as 0).
            "need_to_order": p["recommended_order"],
            "safety_buffer": round(p["safety_buffer"], 2),
            "target_inventory": round(p["target_inventory"], 2),
            "shortfall": _round_or_none(p["shortfall"], 2),
        }
    return {
        "avg_daily_demand": round(daily, 3),
        "avg_weekly_demand": round(daily * 7, 2),
        "projections": projections,
        "forecast": forecast_summary(model, on_hand, legacy_daily),
    }


def forecast_summary(model: dict, on_hand: Optional[float], legacy_daily: float) -> dict:
    """The additive `forecast` detail object (also used by Reorder)."""
    flags = exception_flags(model, on_hand, legacy_daily, model["has_count_history"])
    return {
        "method": "availability",
        "classification": model["classification"],
        "base_daily_demand": round(model["base_daily_demand"], 3),
        "trend_factor": round(model["trend_factor"], 3),
        "dow_factors": {name: round(f, 2) for name, f in zip(_WEEKDAY_NAMES, model["dow_factors"])},
        "availability_pct": round(100 * model["available_days"] / model["window_days"], 1)
        if model["window_days"]
        else None,
        "available_days": round(model["available_days"], 1),
        "window_days": model["window_days"],
        "sale_days": model["sale_days"],
        "units_sold": round(model["units_sold"], 2),
        "recent_7_day_sales": round(model["recent_7_day_sales"], 2),
        "recent_14_day_sales": round(model["recent_14_day_sales"], 2),
        "stockout_days": model["censored_days"],
        "sellout_days": model["sellout_days"],
        "current_stockout_days": model["current_stockout_days"],
        "legacy_avg_daily_demand": round(legacy_daily, 3),
        "flags": flags,
        "needs_review": bool(set(flags) & _REVIEW_FLAGS),
    }


def compute_supplier_projection(
    db: Session,
    supplier: str,
    lookback_days: int,
    horizons_days: list[int],
    as_of_date: Optional[dt.date] = None,
    method: Optional[str] = None,
) -> list[dict]:
    """Every item listed against `supplier`, with demand and need-to-order
    per horizon.

    The default method ("availability", see demand_forecast.py) counts demand
    only over days the item was actually on the shelf. "legacy" is the old
    average-over-calendar-days calculation. Both return the same fields the
    web dashboard and the mobile app read (avg_daily_demand,
    avg_weekly_demand, on_hand_qty, projections[h].projected_demand /
    need_to_order, ...); the new method only ADDS fields (`forecast`, and
    extra keys inside each projection). If it fails for any reason the old
    calculation is used, so the screen never breaks.
    """
    as_of_date = as_of_date or dt.date.today()
    method = method or get_settings().projection_method

    # Live Toast counts on top of the latest snapshot when
    # INVENTORY_API_SNAPSHOTS is on (inventory_api.overlay_live_stock).
    latest_inventory = overlay_live_stock(latest_inventory_by_item(db))
    matching_items = {
        item_id: snap
        for item_id, snap in latest_inventory.items()
        if supplier in _split_suppliers(snap.supplier)
    }
    results = _project_rows(
        db, latest_inventory, matching_items, supplier, lookback_days, horizons_days, as_of_date, method
    )
    _sort_rows(results, horizons_days)
    return results


def _sort_rows(results: list[dict], horizons_days: list[int]) -> None:
    max_horizon = str(max(horizons_days))
    results.sort(
        key=lambda r: r["projections"][max_horizon]["need_to_order"]
        if r["projections"][max_horizon]["need_to_order"] is not None
        else r["projections"][max_horizon]["projected_demand"],
        reverse=True,
    )


def project_suppliers(
    db: Session,
    horizons_by_supplier: dict[str, list[int]],
    lookback_days: int,
    as_of_date: Optional[dt.date] = None,
    method: Optional[str] = None,
) -> dict[str, list[dict]]:
    """supplier -> exactly what compute_supplier_projection returns for it,
    for several suppliers at once (speed #4, the Vendor Calendar): the
    latest inventory (+ live overlay), the sales window, the store-wide
    profiles and one forecast model per item are built ONCE for all of them
    instead of once per supplier. A model depends only on its own item, so
    the numbers are the same."""
    as_of_date = as_of_date or dt.date.today()
    method = method or get_settings().projection_method
    latest_inventory = overlay_live_stock(latest_inventory_by_item(db))
    matching_by_supplier = {
        supplier: {
            item_id: snap for item_id, snap in latest_inventory.items() if supplier in _split_suppliers(snap.supplier)
        }
        for supplier in horizons_by_supplier
    }
    union: dict = {}
    for matching in matching_by_supplier.values():
        union.update(matching)
    demand_by_item = forecast_daily_demand(db, lookback_days, as_of_date)
    models = _models_for(db, latest_inventory, union, lookback_days, as_of_date, method, "the vendor calendar")
    out = {}
    for supplier, matching in matching_by_supplier.items():
        horizons = list(horizons_by_supplier[supplier])
        if method == "availability" and matching and models is None:
            # The shared build failed: each supplier on its own, as before.
            rows = _project_rows(db, latest_inventory, matching, supplier, lookback_days, horizons, as_of_date, method)
        else:
            rows = _project_rows(
                db, latest_inventory, matching, supplier, lookback_days, horizons, as_of_date, method,
                demand_by_item=demand_by_item, models=models or {},
            )
        _sort_rows(rows, horizons)
        out[supplier] = rows
    return out


def _models_for(
    db: Session,
    latest_inventory: dict,
    matching_items: dict,
    lookback_days: int,
    as_of_date: dt.date,
    method: str,
    label: str,
) -> Optional[dict]:
    """The availability models for `matching_items`, or None (legacy method,
    nothing to model, or the forecast failed - then the legacy average)."""
    if method != "availability" or not matching_items:
        return None
    container_qty_by_name = _container_qty_by_name(latest_inventory)
    try:
        count_item_ids = {}
        for item_id, snap in matching_items.items():
            container = container_qty_by_name.get(snap.name)
            count_item_ids[item_id] = [item_id] + ([container[0]] if container else [])
        store_item_ids = [item_id for item_id, snap in latest_inventory.items() if snap.supplier]
        return build_models(db, list(matching_items), count_item_ids, store_item_ids, lookback_days, as_of_date)
    except Exception:
        logger.exception("Availability-adjusted forecast failed for %s; using the legacy average", label)
        return None


def _project_rows(
    db: Session,
    latest_inventory: dict,
    matching_items: dict,
    supplier: str,
    lookback_days: int,
    horizons_days: list[int],
    as_of_date: dt.date,
    method: str,
    demand_by_item: Optional[dict] = None,
    models: Optional[dict] = None,
) -> list[dict]:
    """The per-item rows of `compute_supplier_projection` for `matching_items`
    (unsorted). Shared with `project_items` (the cart's projection context,
    services/order_suggestion.py), so both read the exact same numbers.
    `demand_by_item` / `models`: already built for these items (and maybe
    more) by project_suppliers."""
    if demand_by_item is None:
        demand_by_item = forecast_daily_demand(db, lookback_days, as_of_date)
    container_qty_by_name = _container_qty_by_name(latest_inventory)

    if models is None:
        models = _models_for(db, latest_inventory, matching_items, lookback_days, as_of_date, method, supplier)

    results = []
    for item_id, snap in matching_items.items():
        demand = demand_by_item.get(item_id)
        legacy_daily = demand["avg_daily_demand"] if demand else 0.0
        on_hand = snap.inventory_quantity  # may be None - not all items are quantity-tracked

        container = container_qty_by_name.get(snap.name)
        container_qty = container[1] if container else None
        if container_qty is not None:
            on_hand = (on_hand or 0.0) + container_qty

        row = {
            "item_id": item_id,
            "name": snap.name,
            "category": snap.category,
            "on_hand_qty": on_hand,
            "container_qty": container_qty,
            "supplier_item_id": _supplier_item_id_for(snap, supplier),
            # Additive: the item's other vendors (Toast lists several,
            # ";"-separated) with each one's own item code when known.
            "other_vendors": other_vendor_refs(snap.supplier, getattr(snap, "supplier_item_id", None), supplier),
            "inventory_snapshot_date": snap.snapshot_date.isoformat(),
            # Additive (Oct 14, W2): every vendor SKU + the barcode, for search.
            "all_supplier_item_ids": getattr(snap, "supplier_item_id", None),
            "barcode": getattr(snap, "barcode", None),
        }

        model = models.get(item_id) if models else None
        fields = None
        if model is not None:
            try:
                fields = _availability_fields(model, horizons_days, as_of_date, on_hand, legacy_daily)
            except Exception:
                logger.exception("Availability-adjusted projection failed for item %s; using the legacy average", item_id)
        if fields is None:
            fields = _legacy_fields(legacy_daily, horizons_days, on_hand)
        row.update(fields)
        results.append(row)

    return results


def project_items(
    db: Session,
    item_ids: list[str],
    lookback_days: int,
    horizons_days: list[int],
    as_of_date: Optional[dt.date] = None,
    method: Optional[str] = None,
) -> dict[str, dict]:
    """item_id -> the Supplier Projection row for each of `item_ids` that is in
    the latest upload, whichever vendor(s) list it. Vendor-specific fields
    (supplier_item_id, other_vendors) are not meaningful here - the numbers
    (on hand, demand, projections, forecast) are exactly what the vendor's
    projection shows. Used for the cart lines' projection context."""
    as_of_date = as_of_date or dt.date.today()
    method = method or get_settings().projection_method
    latest_inventory = overlay_live_stock(latest_inventory_by_item(db))
    wanted = {i for i in item_ids if i}
    matching_items = {item_id: snap for item_id, snap in latest_inventory.items() if item_id in wanted}
    if not matching_items:
        return {}
    rows = _project_rows(db, latest_inventory, matching_items, "", lookback_days, horizons_days, as_of_date, method)
    return {r["item_id"]: r for r in rows}
