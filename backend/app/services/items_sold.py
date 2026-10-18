"""Items sold on a given calendar date - our own logic, not Toast's dashboard
number, chosen deliberately per the user's explicit direction on 2026-09-01
after checking both approaches against real data. Documented here precisely
since "how is the total computed" is exactly what's been asked to be clear
about.

**Exactly how a date's total is computed:**

1. Every line item (a sold product, or a priced modifier) has a "paid at"
   timestamp resolved in `sales_sync.py::_extract_line_items`: the check's
   own `paidDate`, falling back to the order's own `paidDate`, falling back
   (ONLY if the order/check is voided) to the order's `createdDate` just so
   a comped/voided row has *some* date - voided rows are excluded from
   revenue regardless of what date they land on. A real, non-voided order
   that simply hasn't been paid yet has no resolved paid_at and doesn't
   count toward any date's total until it's actually paid.
2. A line item counts toward date D if its resolved paid_at falls on D, in
   the restaurant's own timezone - **not** whichever calendar day Toast's
   own `businessDate` field happens to file the order under. Concretely: if
   a guest pays today for a pickup scheduled tomorrow, that sale counts as
   TODAY's - the day the money actually came in - not tomorrow's, even
   though Toast internally files the whole order under tomorrow.
3. Revenue for a date is the sum of every counted item's own net price
   (post discount - Toast spreads a check-level discount onto the items -
   pre-tax) - not each check's official total. Only top-level sold items
   count: a modifier/add-on line ("extra cheese", a spice level) is stored as
   its own row (`parent_selection_guid` set), but its price is ALREADY inside
   its parent item's price in Toast (checked against real checks on
   2026-10-11: 29 of 29 checks with priced modifiers match the top-level sum,
   0 match top-level + modifiers), so adding modifier rows double-counted
   them. This is a deliberate, known difference from Toast's own daily
   dashboard figure, which is bucketed by `businessDate` instead of payment
   date. Toast's item-level report (Analytics `menu`, by business date)
   equals our top-level stored sum once a date is fully synced - the sales
   accuracy job (sales_accuracy.py) keeps it that way.

The nightly cron only ever syncs *yesterday and earlier* (see scheduler.py) -
today's business date has no rows in `orders` yet. So when a range includes
today, the business dates around today (yesterday, today, tomorrow) are pulled
live from Toast and those live line items REPLACE whatever is stored for the
same business dates (a payment just after midnight belongs to yesterday's
business date; an order paid today for tomorrow's pickup to tomorrow's). Every
other date comes from the already-synced `orders` table. The live pull is
shared for `live_sales_cache_seconds` (default 60) so the Overview tile, Items
Sold and Delivery Review don't each re-hit Toast.
"""

import datetime as dt
import threading
import time
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Order
from app.pos.sync.sales import fetch_orders, line_rows
from app.services.sales_sync import _extract_line_items, fetch_orders_with_retry  # noqa: F401 - old names
from app.timeutil import restaurant_timezone, restaurant_today, to_restaurant_date

# business date -> (fetched at epoch, line items). Concurrent requests for the
# same date share one Toast pull (a per-date lock); _LIVE_LOCK only guards the
# dicts and is never held while Toast is being asked.
_LIVE_CACHE: dict = {}
_LIVE_LOCK = threading.Lock()
# business date -> (failed at epoch, short reason): after a failed pull every
# request fails fast for LIVE_FAILURE_BACKOFF_SECONDS instead of queueing up
# behind another slow Toast call (a hung Toast must never tie up the server).
_LIVE_FAILED: dict = {}
_DATE_LOCKS: dict = {}
LIVE_FAILURE_BACKOFF_SECONDS = 15
# How long a request waits for someone else's pull of the same date.
LIVE_LOCK_WAIT_SECONDS = 20
# A 429 on the live path waits at most this long before its one retry
# (the request is someone looking at the page).
LIVE_MAX_RETRY_WAIT_SECONDS = 5.0
# A forced refresh still reuses a pull younger than this (a double click or
# several open tabs never hammer Toast).
MIN_REFRESH_SECONDS = 10


class LiveSalesUnavailable(RuntimeError):
    """Toast didn't hand over today's orders just now (the router answers 503)."""


def clear_live_cache() -> None:
    with _LIVE_LOCK:
        _LIVE_CACHE.clear()
        _LIVE_FAILED.clear()


def _live_sleep(seconds: float) -> None:
    time.sleep(min(max(seconds, 0.0), LIVE_MAX_RETRY_WAIT_SECONDS))


def _cached_live(business_date: dt.date, max_age: float):
    """The cached pull if young enough; raises LiveSalesUnavailable while a
    recent failure is cooling down; else None (go ask Toast)."""
    with _LIVE_LOCK:
        now = time.time()
        hit = _LIVE_CACHE.get(business_date)
        if hit and now - hit[0] < max_age:
            return hit
        failed = _LIVE_FAILED.get(business_date)
        if failed and now - failed[0] < LIVE_FAILURE_BACKOFF_SECONDS:
            raise LiveSalesUnavailable(
                f"Toast didn't answer for {business_date.isoformat()} a moment ago ({failed[1]}) - try again shortly."
            )
    return None


def _live_line_items(business_date: dt.date, fresh: bool = False) -> tuple:
    """(line items, fetched-at epoch) for one business date, from Toast,
    cached for live_sales_cache_seconds."""
    ttl = max(0, get_settings().live_sales_cache_seconds)
    max_age = MIN_REFRESH_SECONDS if fresh else ttl
    hit = _cached_live(business_date, max_age)
    if hit:
        return hit[1], hit[0]
    with _LIVE_LOCK:
        date_lock = _DATE_LOCKS.setdefault(business_date, threading.Lock())
    if not date_lock.acquire(timeout=LIVE_LOCK_WAIT_SECONDS):
        raise LiveSalesUnavailable(f"Toast is slow to answer for {business_date.isoformat()} - try again shortly.")
    try:
        # Someone else may have pulled it while we waited for the lock.
        hit = _cached_live(business_date, max_age)
        if hit:
            return hit[1], hit[0]
        try:
            # The store's connector (multi-store M1 MT3): Order models, the
            # same rows the nightly sync writes (pos/sync/sales.line_rows).
            orders = fetch_orders(business_date, max_retries=1, sleep=_live_sleep)
        except Exception as exc:  # noqa: BLE001 - remembered, then re-raised
            with _LIVE_LOCK:
                _LIVE_FAILED[business_date] = (time.time(), type(exc).__name__)
            raise
        items = [li for order in orders for li in line_rows(order)]
        now = time.time()
        with _LIVE_LOCK:
            _LIVE_CACHE[business_date] = (now, items)
            _LIVE_FAILED.pop(business_date, None)
            for d in [d for d, (t, _) in _LIVE_CACHE.items() if now - t > 3600]:
                _LIVE_CACHE.pop(d, None)
        return items, now
    finally:
        date_lock.release()


def _add(agg: dict, item_guid, item_name, quantity: float, net_price: float) -> None:
    key = item_guid or item_name or "unknown"
    row = agg.setdefault(key, {"item_id": item_guid, "name": item_name, "quantity": 0.0, "revenue": 0.0})
    row["quantity"] += quantity
    row["revenue"] += net_price


def _items_sold_live(start_date: dt.date, end_date: dt.date, business_dates: list, fresh: bool = False) -> tuple:
    """Top-level, paid, non-voided live line items from `business_dates`
    whose payment falls in [start_date, end_date]. Returns (items, oldest
    fetched-at epoch)."""
    agg: dict = {}
    fetched = []
    for bd in business_dates:
        line_items, fetched_at = _live_line_items(bd, fresh=fresh)
        fetched.append(fetched_at)
        for li in line_items:
            if li["voided"] or li.get("parent_selection_guid"):
                continue
            paid_at = li.get("paid_at")
            if paid_at is None:
                continue  # not actually paid yet - doesn't count until it is
            if not (start_date <= to_restaurant_date(paid_at) <= end_date):
                continue
            _add(agg, li["item_guid"], li["item_name"], li["quantity"], li["net_price"])
    return list(agg.values()), (min(fetched) if fetched else None)


def _items_sold_stored(db: Session, start_date: dt.date, end_date: dt.date, skip_business_dates=()) -> list[dict]:
    # Widen the business_date window by a day on each side, since a row's
    # business_date can be one day off from its actual paid_at date - then do
    # the exact date match in Python against paid_at. Rows synced before
    # paid_at existed have it NULL; fall back to business_date for those so
    # old, never-re-synced history doesn't just disappear. Business dates
    # pulled live (skip_business_dates) are left out here - the live pull is
    # the fresher copy of exactly those rows.
    #
    # A check can also be paid DAYS away from its business date: a tab left
    # open and paid a week later, or catering paid days ahead of the pickup
    # date Toast files it under. Those rows are picked up by their paid_at
    # (the store-local [start, end] range in UTC) whatever their business
    # date - with only the +-1 day window they matched no date at all, and
    # the sales accuracy re-pull (which fills in paid_at for late payments)
    # made more of them vanish (verify 1, Oct 11 prod copy, business dates
    # since Aug 1: $1,810.51 in 35 orders before the re-pull, $5,157.89 in 62
    # after it).
    window_start = start_date - dt.timedelta(days=1)
    window_end = end_date + dt.timedelta(days=1)
    tz = restaurant_timezone()
    paid_from = dt.datetime.combine(start_date, dt.time.min, tz)
    paid_to = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, tz)
    stmt = select(
        Order.item_guid,
        Order.item_name,
        Order.quantity,
        Order.net_price,
        Order.paid_at,
        Order.business_date,
    ).where(
        or_(
            and_(Order.business_date >= window_start, Order.business_date <= window_end),
            and_(Order.paid_at >= paid_from, Order.paid_at < paid_to),
        ),
        Order.voided.is_(False),
        # Modifier rows: their price is already inside the parent item's.
        Order.top_level(),
    )
    if skip_business_dates:
        stmt = stmt.where(Order.business_date.not_in(list(skip_business_dates)))

    agg: dict = {}
    for item_guid, item_name, quantity, net_price, paid_at, business_date in db.execute(stmt):
        effective_date = to_restaurant_date(paid_at) if paid_at is not None else business_date
        if effective_date < start_date or effective_date > end_date:
            continue
        _add(agg, item_guid, item_name, quantity, net_price)
    return list(agg.values())


def _merge_items(*groups: list[dict]) -> list[dict]:
    agg: dict = {}
    for group in groups:
        for it in group:
            _add(agg, it["item_id"], it["name"], it["quantity"], it["revenue"])
    return list(agg.values())


def get_items_sold(db: Session, start_date: dt.date, end_date: Optional[dt.date] = None, fresh: bool = False) -> dict:
    """Items sold across [start_date, end_date] (inclusive). A single day is
    just a range where both ends are the same date. `end_date` is clamped to
    today - future dates have no sales yet.

    Today (if included in the range) is always pulled live from Toast, since
    the nightly cron only syncs yesterday and earlier; any earlier days in
    the range are served from the already-synced `orders` table. `fresh`
    skips the short live cache (the page's Refresh button).
    """
    end_date = min(end_date or start_date, restaurant_today())
    if end_date < start_date:
        start_date = end_date

    today = restaurant_today()
    groups = []
    live_at = None
    # A store whose POS has no live sales (CSV-only / DEMO, M2 MT13) serves
    # every day from the stored orders; Toast always has it, so this is
    # unchanged for Toast stores.
    from app.pos.capabilities import Capability
    from app.pos.gating import has_capability

    live = has_capability(Capability.LIVE_TODAY_SALES)
    if end_date >= today and not live:
        groups.append(_items_sold_stored(db, start_date, end_date))
    elif end_date >= today:
        live_dates = [today - dt.timedelta(days=1), today, today + dt.timedelta(days=1)]
        live_items, live_at = _items_sold_live(start_date, end_date, live_dates, fresh)
        groups.append(live_items)
        # Even for "today" alone: a stored check from an older business date
        # can have been paid today (a tab closed today, re-synced by the
        # sales accuracy job) - the live business dates are skipped, so
        # nothing is counted twice.
        groups.append(_items_sold_stored(db, start_date, end_date, skip_business_dates=live_dates))
    else:
        groups.append(_items_sold_stored(db, start_date, end_date))

    if not live:
        source = "stored"
    elif start_date == end_date == today:
        source = "live"  # pulled fresh from Toast just now, not yet in our DB
    elif end_date >= today:
        source = "mixed"  # stored history plus today, pulled live
    else:
        source = "stored"  # served entirely from the already-synced orders table

    items = _merge_items(*groups)

    for item in items:
        item["quantity"] = round(item["quantity"], 3)
        item["revenue"] = round(item["revenue"], 2)
        item["avg_price"] = round(item["revenue"] / item["quantity"], 2) if item["quantity"] else 0.0

    items.sort(key=lambda i: i["revenue"], reverse=True)

    result = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "source": source,
        "items": items,
        "total_quantity": round(sum(i["quantity"] for i in items), 3),
        "total_revenue": round(sum(i["revenue"] for i in items), 2),
    }
    if live_at is not None:
        # Additive: when the live part was pulled from Toast (UTC ISO).
        result["live_fetched_at"] = dt.datetime.fromtimestamp(live_at, dt.timezone.utc).isoformat()
    return result
