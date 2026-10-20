"""Fix Issues lists about stock (added Sep 2026, on top of the barcode /
duplicate / dead-stock lists in fix_issues.py):

  - slow_review: the availability-adjusted forecast (demand_forecast.py, the
    one Supplier Projection uses) classes the item "slow" AND flags it for
    review - the red "Review" chip on Supplier Projection.
  - dead_with_stock: that forecast classes the item "dead" and it still has
    stock on hand.
  - dead_zero_stock (Oct 2026): shelf + container is at exactly 0 and the
    item is the same "dead" class, OR (Oct 4) it hasn't sold in 90 days and
    isn't a brand-new catalog entry - not selling AND nothing left, so a
    candidate to stop reordering or deactivate in Toast. A negative count
    (shelf or container row) is left to Negative inventory, which comes
    first: the count has to be fixed before anyone can judge the item.
  - dead_new_arrival: a brand-new product (never sold) that arrived 5-30
    days ago (last received, or first showed up in the uploads) and still has
    stock. Restocks of items that have sold before are left out.
  - refill_from_container: the shelf (priced) row is down to 5 or less while
    the item's Container-location row still holds stock.

The forecast is NOT re-implemented here: this calls the same build_models /
forecast_summary pair Supplier Projection and Reorder use, with Supplier
Projection's default 3-month window, so an item flagged here shows the same
class and Review chip there. Running it for every item takes ~2s, so the
result is kept per upload and refreshed in the background every few minutes
(see forecasts_by_item).
"""

import datetime as dt
import logging
import time
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import group_order
from app.models import InventorySnapshot
from app.services import category_overrides
from app.services.demand_forecast import DEAD, SLOW, build_models
from app.services.forecast import forecast_daily_demand
from app.services.inventory_api import effective_quantity
from app.services.reorder import latest_inventory_by_item
from app.services.supplier_projection import (
    _REVIEW_FLAGS,
    _container_qty_by_name,
    forecast_summary,
    store_container_pairs,
)
from app.timeutil import restaurant_today, to_restaurant_date
from app.tenancy import spawn_in_store, tenant_local, tenant_lock

logger = logging.getLogger(__name__)

# Supplier Projection's default sales window on both the website and the app
# ("3 months"), so the class / Review flag match what staff see there.
FORECAST_LOOKBACK_DAYS = 90
FORECAST_CACHE_SECONDS = 600

# Refill from container: shelf at or below this, container above zero.
REFILL_STORE_MAX_QTY = 5

# Dead - newly arrived: arrived at least this many days before the latest
# upload, and at most this many (older than that it's ordinary dead stock -
# the forecast marks an item "dead" after ~28 in-stock days with no sale).
NEW_ARRIVAL_MIN_DAYS = 5
NEW_ARRIVAL_MAX_DAYS = 30

# Dead - zero stock, second way on (owner, Oct 4 2026: "add the 75 items"):
# at 0 with no sale in this many days, never sold included, whatever the
# forecast class. An item sitting at 0 never gets the 28 in-stock days the
# dead class needs, so it came out "slow" and was on no list at all...
ZERO_STOCK_NO_SALE_DAYS = 90
# ...unless it's a brand-new catalog entry still waiting for its first
# delivery. It counts as old once it has sold before, or it first showed up
# in the uploads, or was last received, at least this many days before the
# latest upload (_old_catalog_entry).
ZERO_STOCK_MIN_AGE_DAYS = 60

# Why an item is on Dead - zero stock (the row's `zero_stock_rule`).
ZERO_STOCK_RULE_DEAD = "dead"
ZERO_STOCK_RULE_NO_SALE = "no_sale"

# Same wording as the website's / app's Supplier Projection flag labels.
FLAG_LABELS = {
    "insufficient_history": "Not enough history",
    "prolonged_stockout": "Long stockout",
    "demand_spike": "Sales spike capped",
    "unusual_trend": "Unusual trend",
    "negative_inventory": "Negative on-hand",
    "missing_inventory_history": "No inventory counts",
    "intermittent": "Sells occasionally",
    "dead_inventory": "No sales in 60 days",
    "differs_from_history": "Very different from simple average",
}

_cache: dict = tenant_local("stock_issues._cache", dict)
_cache_lock = tenant_lock("stock_issues._cache_lock")
# Bumped by every clear (a category edit, a forced re-warm). A background
# refresh started before a clear must not write its result over an entry
# made after it: that data was computed with the old category rules.
# Per store (docs/PLATFORM_CONTRACT.md 6.5); read as the module attribute
# `_generation` (module __getattr__ below) by older callers and tests.
_gen_state: dict = tenant_local("stock_issues._generation", lambda: {"n": 0})


def __getattr__(name: str):
    if name == "_generation":
        return _gen_state["n"]
    raise AttributeError(name)


def clear_forecast_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _gen_state["n"] += 1


def is_container_row(snap: InventorySnapshot) -> bool:
    """The bare Container-location duplicate of an item (no price, no
    supplier) - see supplier_projection._container_qty_by_name."""
    return snap.price is None and not snap.supplier


def _round(v: Optional[float], digits: int = 2) -> Optional[float]:
    return None if v is None else round(v, digits)


def _base_row(item_id: str, snap: InventorySnapshot) -> dict:
    return {"item_id": item_id, "name": snap.name, "category": snap.category, "supplier": snap.supplier}


def _combined_on_hand(snap: InventorySnapshot, container: Optional[tuple]) -> tuple[Optional[float], Optional[float]]:
    """(shelf + container on hand, container qty) - the same on-hand figure
    Supplier Projection shows."""
    container_qty = container[1] if container else None
    on_hand = snap.inventory_quantity
    if container_qty is not None:
        on_hand = (on_hand or 0.0) + container_qty
    return on_hand, container_qty


# --------------------------------------------------------------------------
# Forecast (shared by slow_review and dead_with_stock)
# --------------------------------------------------------------------------


def _compute_forecasts(db: Session, latest: dict, excluded_groups: set, as_of: dt.date) -> dict:
    containers = _container_qty_by_name(latest)
    universe = {
        item_id: snap
        for item_id, snap in latest.items()
        if not is_container_row(snap) and (snap.category_group or "").upper() not in excluded_groups
    }
    count_item_ids = {}
    for item_id, snap in universe.items():
        container = containers.get(snap.name)
        count_item_ids[item_id] = [item_id] + ([container[0]] if container else [])
    store_item_ids = [item_id for item_id, snap in latest.items() if snap.supplier]
    models = build_models(db, list(universe), count_item_ids, store_item_ids, FORECAST_LOOKBACK_DAYS, as_of)
    legacy = forecast_daily_demand(db, FORECAST_LOOKBACK_DAYS, as_of)

    out = {}
    for item_id, snap in universe.items():
        model = models.get(item_id)
        if model is None:
            continue
        on_hand, container_qty = _combined_on_hand(snap, containers.get(snap.name))
        legacy_daily = legacy[item_id]["avg_daily_demand"] if item_id in legacy else 0.0
        try:
            summary = forecast_summary(model, on_hand, legacy_daily)
        except Exception:
            logger.exception("Fix Issues forecast failed for item %s", item_id)
            continue
        out[item_id] = {"forecast": summary, "on_hand": on_hand, "container_qty": container_qty}
    return out


def forecasts_by_item(db: Session, latest: dict, excluded_groups: set) -> dict:
    """item_id -> {forecast (Supplier Projection's `forecast` object),
    on_hand (shelf + container), container_qty}. Empty when the forecast is
    switched off (PROJECTION_METHOD=legacy - Supplier Projection then shows
    no classes or Review chips either) or fails.

    Kept per (day, latest upload): a new upload or a new day recomputes it
    straight away (~2s). Within the same upload, a result older than
    FORECAST_CACHE_SECONDS is still served while a background thread
    refreshes it with the latest sales, so the Fix Issues page stays fast."""
    if get_settings().projection_method != "availability" or not latest:
        return {}
    as_of = restaurant_today()  # same "today" Supplier Projection uses
    latest_date = max(s.snapshot_date for s in latest.values())
    groups = tuple(sorted(excluded_groups))
    key = (
        str(db.get_bind().url),
        as_of,
        latest_date,
        db.execute(select(func.max(InventorySnapshot.id))).scalar(),
        groups,
    )
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            if hit["expires"] <= time.monotonic() and not hit["refreshing"]:
                hit["refreshing"] = True
                try:
                    # spawn_in_store: the thread keeps this store's context.
                    spawn_in_store(
                        _refresh_in_background,
                        db.get_bind(),
                        key,
                        set(excluded_groups),
                        as_of,
                        _gen_state["n"],
                        name="fix-issues-forecast-refresh",
                    )
                except Exception:  # e.g. no threads left - keep serving the old result
                    hit["refreshing"] = False
                    logger.exception("Could not start the Fix Issues forecast refresh")
            return hit["data"]
        # Computed under the lock so two page loads at once don't both run it.
        # The inventory is re-read here, with the category rules re-read too:
        # `latest` may have been loaded before a category edit that cleared
        # the cache, and caching a result built from it would serve the old
        # kitchen-group exclusions for FORECAST_CACHE_SECONDS.
        category_overrides.invalidate(db)
        data = _safe_compute(db, latest_inventory_by_item(db), set(excluded_groups), as_of)
        if data is None:
            return {}  # not cached - the next page load tries again
        _cache.clear()
        _cache[key] = {"data": data, "expires": time.monotonic() + FORECAST_CACHE_SECONDS, "refreshing": False}
        return data


def _safe_compute(db: Session, latest: dict, excluded_groups: set, as_of: dt.date) -> Optional[dict]:
    try:
        return _compute_forecasts(db, latest, excluded_groups, as_of)
    except Exception:
        logger.exception("Fix Issues forecast failed; the slow / dead lists are empty for now")
        return None


def _refresh_in_background(bind, key: tuple, excluded_groups: set, as_of: dt.date, generation: int) -> None:
    db = Session(bind=bind)
    data = None
    try:
        data = _compute_forecasts(db, latest_inventory_by_item(db), excluded_groups, as_of)
    except Exception:
        logger.exception("Fix Issues forecast refresh failed; keeping the previous result")
    finally:
        db.close()
    with _cache_lock:
        entry = _cache.get(key)
        # A clear since this refresh started: the entry (if any) is newer
        # than our data - leave it alone.
        if entry is not None and generation == _gen_state["n"]:
            if data is not None:
                entry["data"] = data
            entry["expires"] = time.monotonic() + FORECAST_CACHE_SECONDS
            entry["refreshing"] = False


def _forecast_fields(fc: dict) -> dict:
    return {
        "classification": fc["classification"],
        "weekly_demand": round(fc["base_daily_demand"] * fc["trend_factor"] * 7, 2),
        "units_sold": fc["units_sold"],
        "sale_days": fc["sale_days"],
        "available_days": fc["available_days"],
        "window_days": fc["window_days"],
        "recent_14_day_sales": fc["recent_14_day_sales"],
        "flags": fc["flags"],
    }


def slow_review_items(latest: dict, forecasts: dict) -> list[dict]:
    """Slow + the Review flag, with stock on hand (owner, Oct 2026: "slow
    moving zero quantity need not be there"). Shelf + container at 0 or less
    is left out - a negative count has its own list, and an empty slow item
    is a reorder question, not a count / sales review. An item with no count
    at all (untracked, on_hand None) stays: there's nothing saying it's empty."""
    rows = []
    for item_id, f in forecasts.items():
        fc = f["forecast"]
        if fc["classification"] != SLOW or not fc["needs_review"] or item_id not in latest:
            continue
        if f["on_hand"] is not None and f["on_hand"] <= 0:
            continue
        review_flags = [flag for flag in fc["flags"] if flag in _REVIEW_FLAGS]
        rows.append(
            {
                **_base_row(item_id, latest[item_id]),
                "on_hand_qty": f["on_hand"],
                "container_qty": f["container_qty"],
                **_forecast_fields(fc),
                "review_flags": review_flags,
                "review_reason": ", ".join(FLAG_LABELS.get(flag, flag) for flag in review_flags),
            }
        )
    rows.sort(key=lambda r: (-(r["units_sold"] or 0), r["name"] or ""))
    return rows


def dead_with_stock_items(latest: dict, forecasts: dict) -> list[dict]:
    rows = []
    for item_id, f in forecasts.items():
        fc = f["forecast"]
        if fc["classification"] != DEAD or f["on_hand"] is None or f["on_hand"] <= 0 or item_id not in latest:
            continue
        snap = latest[item_id]
        rows.append(
            {
                **_base_row(item_id, snap),
                "on_hand_qty": f["on_hand"],
                "container_qty": f["container_qty"],
                "inventory_value": _round(snap.inventory_value),
                **_forecast_fields(fc),
            }
        )
    rows.sort(key=lambda r: -(r["inventory_value"] or 0))
    return rows


def _at_zero_stock(snap: InventorySnapshot, f: dict) -> bool:
    """Shelf + container at exactly 0. Never with a shelf or Container row
    below 0 (Negative inventory's - it lists a Container row under the
    container's own item_id, so this is checked by row, not by total), and
    never an item with no count at all (untracked, on_hand None): nothing
    says that one is empty."""
    on_hand = f["on_hand"]
    if on_hand is None or on_hand != 0:
        return False
    # An API-built row Toast reported out of stock is stored as 0 but keeps
    # the latest CSV's negative count (inventory_api.effective_quantity).
    shelf = effective_quantity(snap)
    container_qty = f["container_qty"]
    return not ((shelf is not None and shelf < 0) or (container_qty is not None and container_qty < 0))


def _old_catalog_entry(
    first_seen: Optional[dt.date], received: Optional[dt.date], last_sold: Optional[dt.date], old_by: dt.date
) -> bool:
    """Not a brand-new catalog entry still waiting for its first delivery:
    it has sold before (anything that ever sold is not a new product - the
    same test Dead - newly arrived uses), or it first showed up in the
    uploads, or was last received, on/before `old_by`.

    A sale counts on its own because first-seen can't prove an item old:
    the backfilled days (Jun-Aug) leave out items Toast wasn't counting
    then, so an old item can first show up on Aug 24, the first real upload
    (on Oct 4, Thums Up Glass Light 300ml - sold Jun 3, nothing since, at 0 -
    was held back as "brand new" that way)."""
    if last_sold is not None:
        return True
    return any(day is not None and day <= old_by for day in (first_seen, received))


def _first_upload_dates(db: Session, item_ids: list) -> dict:
    """{item_id: the first upload it EVER appeared in}, over all history.
    Backfilled days count too: they list items that were in Toast then."""
    out = {}
    for i in range(0, len(item_ids), 500):
        rows = db.execute(
            select(InventorySnapshot.item_id, func.min(InventorySnapshot.snapshot_date))
            .where(InventorySnapshot.item_id.in_(item_ids[i : i + 500]))
            .group_by(InventorySnapshot.item_id)
            .order_by(*group_order(InventorySnapshot.item_id))
        ).all()
        out.update({item_id: day for item_id, day in rows})
    return out


def dead_zero_stock_items(
    db: Session, latest: dict, forecasts: dict, last_sold: dict, today: Optional[dt.date] = None
) -> list[dict]:
    """Not selling and nothing left (shelf + container at exactly 0): stop
    reordering it, deactivate it in Toast, or reorder on purpose if it
    should sell. Two ways on, given as the row's `zero_stock_rule`:

      - "dead": the forecast classes it dead (the dead_with_stock rule: no
        sale in 60 days while in stock);
      - "no_sale" (owner, Oct 4): any other forecast class (in practice
        "slow": sitting at 0, it never had the 28 in-stock days the dead
        class needs) with no sale in the last ZERO_STOCK_NO_SALE_DAYS days
        - never sold included - and not a brand-new catalog entry still
        waiting for its first delivery: it has sold before, or it first
        showed up in the uploads, or was last received,
        ZERO_STOCK_MIN_AGE_DAYS+ days before the latest upload
        (_old_catalog_entry).

    Never overlaps another stock list: Dead with stock, Dead - newly arrived
    and Slow moving - Review all need stock above 0 (Slow moving - Review
    also takes an item with no count), Refill from container needs stock in
    the container, and an item whose shelf or Container row is below 0 is
    left to Negative inventory (that count has to be fixed first).

    `last_sold` is {item_id: last business date sold} (all history, real
    sale lines only). `today` defaults to the forecast's "today" (the one
    Supplier Projection uses), so the 90 days end where its sales window
    ends. Each row also gets `first_seen_date`, the first upload the item
    was in (additive, Oct 4)."""
    if not latest or not forecasts:
        return []
    latest_date = max(s.snapshot_date for s in latest.values())
    today = today or restaurant_today()
    sold_since = today - dt.timedelta(days=ZERO_STOCK_NO_SALE_DAYS)
    old_by = latest_date - dt.timedelta(days=ZERO_STOCK_MIN_AGE_DAYS)

    picked = []
    for item_id, f in forecasts.items():
        snap = latest.get(item_id)
        if snap is None or not _at_zero_stock(snap, f):
            continue
        if f["forecast"]["classification"] == DEAD:
            picked.append((item_id, snap, f, ZERO_STOCK_RULE_DEAD))
            continue
        sold = last_sold.get(item_id)
        if sold is None or sold < sold_since:
            picked.append((item_id, snap, f, ZERO_STOCK_RULE_NO_SALE))
    first_seen = _first_upload_dates(db, [p[0] for p in picked])

    rows = []
    for item_id, snap, f, rule in picked:
        seen = first_seen.get(item_id)
        if rule == ZERO_STOCK_RULE_NO_SALE and not _old_catalog_entry(
            seen, _received_date(snap.inventory_last_received), last_sold.get(item_id), old_by
        ):
            continue  # brand new: still waiting for its first delivery
        rows.append(
            {
                **_base_row(item_id, snap),
                "on_hand_qty": f["on_hand"],
                "container_qty": f["container_qty"],
                "last_received": snap.inventory_last_received,
                "zero_stock_rule": rule,
                "first_seen_date": seen.isoformat() if seen else None,
                **_forecast_fields(f["forecast"]),
            }
        )
    # Longest-quiet items first: most stock-days without a sale, then name.
    rows.sort(key=lambda r: (-(r["available_days"] or 0), r["name"] or ""))
    return rows


# --------------------------------------------------------------------------
# Dead - newly arrived
# --------------------------------------------------------------------------


def _received_date(raw: Optional[str]) -> Optional[dt.date]:
    """Toast's "last received" timestamp (ISO, UTC) as a store-local date."""
    if not raw:
        return None
    try:
        stamp = dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return to_restaurant_date(stamp)


def _first_seen_dates(db: Session, since: dt.date, item_ids) -> dict:
    """{item_id: the first upload it EVER appeared in}, for those of
    `item_ids` whose first appearance is on/after `since`. Items in the very
    first upload on file are left out (history starts there, so they're not
    "new").

    Looks at all history, not just the stretch since `since`: the backfilled
    days (Toast INV_SNAPSHOT reports) list ~500 fewer items than a real
    upload, so "missing from the first day of the stretch" alone would make
    hundreds of old items look new. Only items missing from the first upload
    of the stretch are looked up (by item_id, indexed), which keeps this fast."""
    first_in_window = db.execute(
        select(func.min(InventorySnapshot.snapshot_date)).where(InventorySnapshot.snapshot_date >= since)
    ).scalar()
    if first_in_window is None:
        return {}
    present = set(
        db.execute(
            select(InventorySnapshot.item_id).where(InventorySnapshot.snapshot_date == first_in_window)
        ).scalars()
    )
    candidates = [i for i in item_ids if i not in present]
    if not candidates:
        return {}
    history_start = db.execute(select(func.min(InventorySnapshot.snapshot_date))).scalar()
    out = {}
    for i in range(0, len(candidates), 500):
        rows = db.execute(
            select(InventorySnapshot.item_id, func.min(InventorySnapshot.snapshot_date))
            .where(InventorySnapshot.item_id.in_(candidates[i : i + 500]))
            .group_by(InventorySnapshot.item_id)
            .order_by(*group_order(InventorySnapshot.item_id))
        ).all()
        out.update({item_id: day for item_id, day in rows if day >= since and day != history_start})
    return out


def dead_new_arrival_items(db: Session, latest: dict, last_sold: dict) -> list[dict]:
    """Brand-new products that haven't sold since arriving: arrived
    NEW_ARRIVAL_MIN_DAYS-NEW_ARRIVAL_MAX_DAYS days before the latest upload,
    stock on hand, and no sale ever (owner, Sep 29 - an item that has sold
    before is a restock, not a new product, so it's left out even if it
    hasn't moved since this delivery). Arrival = the later of Toast's "last
    received" date and the day the item first showed up in an upload (looked
    at over the last month only).

    `arrival_kind` ("New item") and `last_sold_before_arrival` (None) are
    kept on every row for older app builds that still read them."""
    if not latest:
        return []
    latest_date = max(s.snapshot_date for s in latest.values())
    first_seen = _first_seen_dates(db, latest_date - dt.timedelta(days=NEW_ARRIVAL_MAX_DAYS + 1), list(latest))
    containers = _container_qty_by_name(latest)

    rows = []
    for item_id, snap in latest.items():
        if is_container_row(snap):
            continue
        received = _received_date(snap.inventory_last_received)
        seen = first_seen.get(item_id)
        candidates = [(d, how) for d, how in ((received, "received"), (seen, "first_upload")) if d is not None]
        if not candidates:
            continue
        arrived, how = max(candidates, key=lambda c: c[0])
        days = (latest_date - arrived).days
        if days < NEW_ARRIVAL_MIN_DAYS or days > NEW_ARRIVAL_MAX_DAYS:
            continue
        on_hand, container_qty = _combined_on_hand(snap, containers.get(snap.name))
        if on_hand is None or on_hand <= 0:
            continue
        # Brand-new products only (owner, Sep 29): anything that has ever
        # sold is a restock, not "newly arrived", even if it hasn't moved
        # since this delivery.
        sold = last_sold.get(item_id)
        if sold is not None:
            continue
        rows.append(
            {
                **_base_row(item_id, snap),
                "on_hand_qty": on_hand,
                "container_qty": container_qty,
                "inventory_value": _round(snap.inventory_value),
                "arrived_date": arrived.isoformat(),
                "arrived_via": how,
                "days_since_arrival": days,
                # Kept for clients that show it: always a never-sold item now.
                "arrival_kind": "New item",
                "last_sold_before_arrival": None,
            }
        )
    rows.sort(key=lambda r: (-r["days_since_arrival"], r["name"] or ""))
    return rows


# --------------------------------------------------------------------------
# Refill from container
# --------------------------------------------------------------------------


def refill_from_container_items(latest: dict) -> list[dict]:
    """Shelf (priced) row at REFILL_STORE_MAX_QTY or less while its
    Container-location row holds stock. Pairs rows exactly like Supplier
    Projection / Location Transfer: one priced row + one bare row per name;
    names with two priced rows (different products) are skipped."""
    rows = []
    for item_id, snap, container_id, container_qty in store_container_pairs(latest):
        store_qty = snap.inventory_quantity
        if store_qty is None or store_qty > REFILL_STORE_MAX_QTY or container_qty <= 0:
            continue
        rows.append(
            {
                **_base_row(item_id, snap),
                "store_qty": store_qty,
                "container_qty": container_qty,
                "container_item_id": container_id,
                "on_hand_qty": store_qty,
            }
        )
    rows.sort(key=lambda r: (r["store_qty"], -r["container_qty"], r["name"] or ""))
    return rows


# --------------------------------------------------------------------------
# Recurring problems: did the problem clear after an item was closed?
# --------------------------------------------------------------------------
#
# A negative count or a low shelf is a condition that comes and goes. A
# close like "Sold before the delivery was received" or "Keeping it in the
# container for now" explains THIS occurrence; if a later upload shows the
# problem cleared and it then comes back, it's a new occurrence and belongs
# in the open list again (fix_issues.get_issue_list). Each function takes
# {issue_key: (row, closed_date)} and returns the keys whose problem cleared
# in an upload dated after closed_date (an upload is the opening count of
# its day, so the close-day upload itself was taken before the close).


def negative_cleared_since(db: Session, pending: dict) -> set:
    if not pending:
        return set()
    ids = {row["item_id"] for row, _ in pending.values()}
    since = min(day for _, day in pending.values())
    ok_days: dict = {}
    for item_id, day in db.execute(
        select(InventorySnapshot.item_id, InventorySnapshot.snapshot_date).where(
            InventorySnapshot.item_id.in_(ids),
            InventorySnapshot.snapshot_date > since,
            InventorySnapshot.inventory_quantity >= 0,
            # Not cleared: an API row at 0 only because Toast said "out of
            # stock" while the latest CSV still had it negative.
            or_(
                InventorySnapshot.api_out_of_stock.is_not(True),
                InventorySnapshot.csv_quantity.is_(None),
                InventorySnapshot.csv_quantity >= 0,
            ),
        )
    ):
        ok_days.setdefault(item_id, []).append(day)
    return {
        key for key, (row, closed) in pending.items() if any(day > closed for day in ok_days.get(row["item_id"], ()))
    }


def refill_cleared_since(db: Session, pending: dict) -> set:
    if not pending:
        return set()
    ids = set()
    for row, _ in pending.values():
        ids.add(row["item_id"])
        if row.get("container_item_id"):
            ids.add(row["container_item_id"])
    since = min(day for _, day in pending.values())
    qty = {
        (item_id, day): q
        for item_id, day, q in db.execute(
            select(
                InventorySnapshot.item_id, InventorySnapshot.snapshot_date, InventorySnapshot.inventory_quantity
            ).where(InventorySnapshot.item_id.in_(ids), InventorySnapshot.snapshot_date > since)
        )
    }
    cleared = set()
    for key, (row, closed) in pending.items():
        for (item_id, day), store_qty in qty.items():
            if item_id != row["item_id"] or day <= closed or store_qty is None:
                continue
            container_qty = qty.get((row.get("container_item_id"), day))
            if store_qty > REFILL_STORE_MAX_QTY or (container_qty is not None and container_qty <= 0):
                cleared.add(key)
                break
    return cleared
