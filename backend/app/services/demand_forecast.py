"""Availability-adjusted demand forecast for Supplier Projection.

Replaces "units sold / calendar days" with "units sold / days the product was
actually on the shelf", so a stockout no longer makes an item look like it
doesn't sell. The pipeline, per item:

1. Availability per day, from the daily inventory snapshots (snapshot_date D
   holds the OPENING count for business day D - checked against real data:
   open(D) - sold(D) == open(D+1) for 97% of item-days with no receipt).
     - opening > 0                       -> available (1.0)
     - opening > 0, closing <= 0, sold   -> sold out that day: available for
       the share of a normal day's store traffic that had happened by the
       item's last sale (floor MIN_AVAILABILITY)
     - opening <= 0, no sale, and no sale anywhere in that run of <= 0 days
                                         -> stockout, censored (0.0)
     - opening <= 0 but it sold that day, or on another day of the same
       <= 0 run                          -> the count is wrong, not the
       shelf empty (Toast lets on-hand go negative; on real data items sell
       on <= 0 days as often as on > 0 days), so it's treated as available
     - no row in that day's upload (item not in the Toast catalog yet)
                                         -> not counted (0.0)
     - a row with no count (untracked item), or before snapshots began
                                         -> treated as available
2. Demand = recency-weighted units sold / recency-weighted available days
   (28-day half-life), with each day's share also divided by its
   day-of-week factor. Equivalent to weighting each day's adjusted demand
   (sold / availability) by weight x availability, so a sliver of
   availability can't turn one sale into an absurd rate.
3. Day-of-week factors: store-wide (all supplier items), and for REGULAR
   items blended with the item's own pattern, shrunk toward the store's.
4. Dampened, clamped trend for REGULAR items only.
5. SKU class, from the share of available days with a sale:
     REGULAR      sells most days - own weekday pattern, trend, 28-day half-life
     INTERMITTENT sells now and then - store weekday pattern, no trend
                  (so a few quiet days are not read as demand disappearing)
     SLOW         a handful of sales - plain average, no safety buffer
     DEAD         nothing sold for 60 available days - forecast 0, flagged
6. Each future day forecast individually (base x DOW x trend), summed over
   the horizon, plus a modest variability-based safety buffer, minus on-hand.
7. Exceptions flagged for human review.

Everything here is additive: the caller (supplier_projection.py) keeps the
exact response fields the web dashboard and the mobile app already read, and
falls back to the old calculation if this raises.
"""

import datetime as dt
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot, Order
from app.timeutil import restaurant_timezone


@dataclass(frozen=True)
class ForecastConfig:
    half_life_days: float = 28.0
    intermittent_half_life_days: float = 28.0
    min_availability: float = 0.25  # floor for a sold-out day's availability share
    # Day-of-week: an item's own pattern counts as much as the store's once
    # it has this many available days on a given weekday.
    dow_shrink_days: float = 8.0
    # Trend: recent window vs. the older baseline right before it.
    trend_recent_days: int = 14
    trend_baseline_days: int = 46  # days 15-60 back
    trend_dampening: float = 0.5
    trend_min: float = 0.80
    trend_max: float = 1.25
    # Classification (share of available days with a sale).
    regular_min_sale_share: float = 0.5
    regular_min_sale_days: int = 8
    slow_max_sale_days: int = 3
    slow_max_sale_share: float = 0.10
    dead_quiet_days: int = 60
    dead_min_available_days: float = 28.0
    insufficient_available_days: float = 14.0
    # Safety buffer: z x daily std-dev x sqrt(horizon), capped at a share of
    # projected demand so it never becomes a big blanket percentage.
    safety_z_regular: float = 0.84
    safety_z_intermittent: float = 0.5
    safety_z_stockout_bonus: float = 0.25
    safety_max_share: float = 0.5
    # A single day's sales above max(spike_floor, spike_multiple x the
    # item's median selling day) is capped there (bulk/catering buys).
    spike_multiple: float = 5.0
    spike_floor: float = 6.0
    # Anything below this after rounding isn't worth an order line.
    round_down_below: float = 0.1


DEFAULT_CONFIG = ForecastConfig()

REGULAR, INTERMITTENT, SLOW, DEAD = "regular", "intermittent", "slow", "dead"


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------


def _chunks(values: list, size: int = 800):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _load_daily_sales(db: Session, item_ids: list[str], start: dt.date, end: dt.date):
    """{item: {date: (qty, last_sale_local_hour)}} for [start, end)."""
    tz = restaurant_timezone()
    out: dict[str, dict[dt.date, list]] = defaultdict(dict)
    for chunk in _chunks(item_ids):
        stmt = select(Order.item_guid, Order.business_date, Order.quantity, Order.opened_at).where(
            Order.item_guid.in_(chunk),
            Order.voided.is_(False),
            Order.top_level(),
            Order.business_date >= start,
            Order.business_date < end,
        )
        for item_id, day, qty, opened_at in db.execute(stmt):
            cell = out[item_id].setdefault(day, [0.0, None])
            cell[0] += float(qty or 0)
            if opened_at is not None:
                opened = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=dt.timezone.utc)
                hour = opened.astimezone(tz).hour
                if cell[1] is None or hour > cell[1]:
                    cell[1] = hour
    return out


def _load_opening_counts(db: Session, item_ids: list[str], start: dt.date, end: dt.date):
    """({item: {date: qty}}, {item: set(dates listed)}, set(all snapshot
    dates)) for snapshot dates in [start, end] (inclusive: the snapshot on
    `end` is the closing count of the last day in the window). An item is
    "listed" on a date when it has a row in that day's upload, even with a
    blank count."""
    counts: dict[str, dict[dt.date, float]] = defaultdict(dict)
    listed: dict[str, set] = defaultdict(set)
    for chunk in _chunks(item_ids):
        stmt = select(
            InventorySnapshot.item_id, InventorySnapshot.snapshot_date, InventorySnapshot.inventory_quantity
        ).where(
            InventorySnapshot.item_id.in_(chunk),
            InventorySnapshot.snapshot_date >= start,
            InventorySnapshot.snapshot_date <= end,
        )
        for item_id, day, qty in db.execute(stmt):
            listed[item_id].add(day)
            if qty is not None:
                counts[item_id][day] = float(qty)
    snapshot_dates = set(
        db.execute(
            select(InventorySnapshot.snapshot_date)
            .where(InventorySnapshot.snapshot_date >= start, InventorySnapshot.snapshot_date <= end)
            .distinct()
        ).scalars()
    )
    return counts, listed, snapshot_dates


def _store_profiles(db: Session, item_ids: list[str], start: dt.date, end: dt.date):
    """Store-wide day-of-week factors (mean 1.0) and the cumulative share of
    a normal day's units sold by the end of each local hour, from the sales
    of `item_ids` (all supplier-listed items, not just one supplier's, so a
    small supplier still gets a stable pattern)."""
    tz = restaurant_timezone()
    by_weekday = [0.0] * 7
    by_hour = [0.0] * 24
    days_per_weekday = [0] * 7
    d = start
    while d < end:
        days_per_weekday[d.weekday()] += 1
        d += dt.timedelta(days=1)
    for chunk in _chunks(item_ids):
        stmt = select(Order.business_date, Order.quantity, Order.opened_at).where(
            Order.item_guid.in_(chunk),
            Order.voided.is_(False),
            Order.top_level(),
            Order.business_date >= start,
            Order.business_date < end,
        )
        for day, qty, opened_at in db.execute(stmt):
            q = float(qty or 0)
            by_weekday[day.weekday()] += q
            if opened_at is not None:
                opened = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=dt.timezone.utc)
                by_hour[opened.astimezone(tz).hour] += q

    rates = [by_weekday[w] / days_per_weekday[w] if days_per_weekday[w] else 0.0 for w in range(7)]
    mean_rate = sum(rates) / 7
    dow = [r / mean_rate if mean_rate > 0 and r > 0 else 1.0 for r in rates]
    dow = _normalize(dow)

    total = sum(by_hour)
    cumulative = []
    running = 0.0
    for h in range(24):
        running += by_hour[h]
        cumulative.append(running / total if total > 0 else 1.0)
    return dow, cumulative


def _normalize(factors: list[float]) -> list[float]:
    mean = sum(factors) / len(factors)
    return [f / mean for f in factors] if mean > 0 else [1.0] * len(factors)


# --------------------------------------------------------------------------
# Per-item model
# --------------------------------------------------------------------------


def _availability_by_day(days, opening, sales, hour_share, cfg: ForecastConfig, unlisted=frozenset()):
    """[(date, availability, kind)] where kind is one of available / sellout /
    count_wrong / censored / unlisted / unknown. `unlisted` = dates with an
    upload that doesn't include this item (not in the Toast catalog yet)."""
    result = []
    # Pre-compute which <= 0 runs contain a sale (count wrong, not empty shelf).
    run_has_sale = {}
    run_days: list = []
    for d in days + [None]:
        qty = opening.get(d) if d is not None else None
        in_run = d is not None and qty is not None and qty <= 0
        if in_run:
            run_days.append(d)
            continue
        if run_days:
            has_sale = any(sales.get(x, (0.0, None))[0] > 0 for x in run_days)
            for x in run_days:
                run_has_sale[x] = has_sale
            run_days = []

    for d in days:
        qty_open = opening.get(d)
        sold = sales.get(d, (0.0, None))[0]
        if qty_open is None and d in unlisted and sold == 0:
            result.append((d, 0.0, "unlisted"))
        elif qty_open is None:
            result.append((d, 1.0, "unknown"))
        elif qty_open > 0:
            qty_close = opening.get(d + dt.timedelta(days=1))
            last_hour = sales.get(d, (0.0, None))[1]
            if qty_close is not None and qty_close <= 0 and sold > 0 and last_hour is not None:
                share = max(cfg.min_availability, min(1.0, hour_share[last_hour]))
                result.append((d, share, "sellout" if share < 1.0 else "available"))
            else:
                result.append((d, 1.0, "available"))
        elif run_has_sale.get(d):
            result.append((d, 1.0, "count_wrong"))
        else:
            result.append((d, 0.0, "censored"))
    return result


def _weight(age_days: int, half_life: Optional[float]) -> float:
    return 1.0 if not half_life else 0.5 ** (age_days / half_life)


def _cap_spikes(values: list[float], cfg: ForecastConfig) -> tuple[list[float], bool]:
    nonzero = [v for v in values if v > 0]
    if len(nonzero) < 3:
        return values, False
    cap = max(cfg.spike_floor, cfg.spike_multiple * statistics.median(nonzero))
    capped = [min(v, cap) for v in values]
    return capped, capped != values


def forecast_item(
    days: list[dt.date],
    opening: dict,
    sales: dict,
    store_dow: list[float],
    hour_share: list[float],
    as_of: dt.date,
    cfg: ForecastConfig = DEFAULT_CONFIG,
    unlisted=frozenset(),
) -> dict:
    """Model for one item over the lookback `days` (oldest first, all < as_of)."""
    avail = _availability_by_day(days, opening, sales, hour_share, cfg, unlisted)
    raw_qty = [sales.get(d, (0.0, None))[0] for d, _, _ in avail]
    qty, spiked = _cap_spikes(raw_qty, cfg)
    obs = [(d, a, kind, q) for (d, a, kind), q in zip(avail, qty)]

    available = [(d, a, q) for d, a, kind, q in obs if a > 0]
    available_days = sum(a for _, a, _ in available)
    sale_days = sum(1 for _, _, q in available if q > 0)
    sale_share = sale_days / len(available) if available else 0.0
    counts = defaultdict(int)
    for _, _, kind, _ in obs:
        counts[kind] += 1

    quiet_window = [(d, a, q) for d, a, q in available if (as_of - d).days <= cfg.dead_quiet_days]
    quiet_available = sum(a for _, a, _ in quiet_window)
    quiet_sales = sum(q for _, _, q in quiet_window)

    if sale_days == 0 and quiet_available >= cfg.dead_min_available_days:
        klass = DEAD
    elif quiet_sales == 0 and quiet_available >= cfg.dead_min_available_days:
        klass = DEAD
    elif sale_days >= cfg.regular_min_sale_days and sale_share >= cfg.regular_min_sale_share:
        klass = REGULAR
    elif sale_days <= cfg.slow_max_sale_days or sale_share < cfg.slow_max_sale_share:
        klass = SLOW
    else:
        klass = INTERMITTENT

    # Day-of-week: REGULAR items blend in their own pattern.
    dow = list(store_dow)
    if klass == REGULAR:
        wk_q = [0.0] * 7
        wk_a = [0.0] * 7
        for d, a, q in available:
            wk_q[d.weekday()] += q
            wk_a[d.weekday()] += a
        total_rate = sum(wk_q) / sum(wk_a) if sum(wk_a) > 0 else 0.0
        if total_rate > 0:
            blended = []
            for w in range(7):
                own = (wk_q[w] / wk_a[w]) / total_rate if wk_a[w] > 0 else store_dow[w]
                n = wk_a[w]
                blended.append((n * own + cfg.dow_shrink_days * store_dow[w]) / (n + cfg.dow_shrink_days))
            dow = _normalize(blended)

    half_life = {
        REGULAR: cfg.half_life_days,
        INTERMITTENT: cfg.intermittent_half_life_days,
        SLOW: None,
        DEAD: None,
    }[klass]

    num = den = 0.0
    for d, a, q in available:
        w = _weight((as_of - d).days, half_life)
        num += w * q
        den += w * a * dow[d.weekday()]
    base = num / den if den > 0 else 0.0

    trend = 1.0
    raw_trend = None
    if klass == REGULAR:
        recent = [(d, a, q) for d, a, q in available if (as_of - d).days <= cfg.trend_recent_days]
        older = [
            (d, a, q)
            for d, a, q in available
            if cfg.trend_recent_days < (as_of - d).days <= cfg.trend_recent_days + cfg.trend_baseline_days
        ]
        r_den = sum(a * dow[d.weekday()] for d, a, _ in recent)
        o_den = sum(a * dow[d.weekday()] for d, a, _ in older)
        if r_den >= 7 and o_den >= 14:
            r_rate = sum(q for _, _, q in recent) / r_den
            o_rate = sum(q for _, _, q in older) / o_den
            if o_rate > 0:
                growth = r_rate / o_rate - 1
                raw_trend = 1 + cfg.trend_dampening * growth
                trend = min(cfg.trend_max, max(cfg.trend_min, raw_trend))

    if klass == DEAD:
        base = 0.0

    # Day-to-day variability of adjusted demand around the model, for the
    # safety buffer (fully/mostly available days only).
    residuals = []
    weights = []
    for d, a, q in available:
        if a >= 0.5:
            expected = base * dow[d.weekday()] * trend
            residuals.append(q / a - expected)
            weights.append(_weight((as_of - d).days, half_life))
    daily_sd = 0.0
    if len(residuals) >= 7 and sum(weights) > 0:
        daily_sd = math.sqrt(sum(w * r * r for w, r in zip(weights, residuals)) / sum(weights))

    recent_7 = sum(q for (d, _, _), q in zip(avail, raw_qty) if (as_of - d).days <= 7)
    recent_14 = sum(q for (d, _, _), q in zip(avail, raw_qty) if (as_of - d).days <= 14)

    # Current censored run (days since the item was last on the shelf).
    current_stockout_days = 0
    for _, _, kind, _ in reversed(obs):
        if kind != "censored":
            break
        current_stockout_days += 1

    return {
        "classification": klass,
        "base_daily_demand": base,
        "trend_factor": trend,
        "raw_trend_factor": raw_trend,
        "dow_factors": dow,
        "daily_sd": daily_sd,
        "available_days": available_days,
        "window_days": len(days),
        "sale_days": sale_days,
        "units_sold": sum(raw_qty),
        "recent_7_day_sales": recent_7,
        "recent_14_day_sales": recent_14,
        "censored_days": counts["censored"],
        "sellout_days": counts["sellout"],
        "count_wrong_days": counts["count_wrong"],
        "unknown_days": counts["unknown"],
        "unlisted_days": counts["unlisted"],
        "current_stockout_days": current_stockout_days,
        "spike_capped": spiked,
        "max_day_sales": max(raw_qty) if raw_qty else 0.0,
    }


def project_item(model: dict, horizon_days: int, start: dt.date, on_hand: Optional[float], cfg: ForecastConfig):
    """Forecast each day of the horizon individually and turn it into a
    recommended order quantity. `on_hand` None = not quantity-tracked."""
    dow = model["dow_factors"]
    projected = sum(
        model["base_daily_demand"] * dow[(start + dt.timedelta(days=i)).weekday()] * model["trend_factor"]
        for i in range(horizon_days)
    )

    klass = model["classification"]
    z = {REGULAR: cfg.safety_z_regular, INTERMITTENT: cfg.safety_z_intermittent}.get(klass, 0.0)
    if z and (model["censored_days"] or model["sellout_days"]):
        z += cfg.safety_z_stockout_bonus
    safety = z * model["daily_sd"] * math.sqrt(horizon_days)
    safety = min(safety, cfg.safety_max_share * projected)

    target = projected + safety
    if on_hand is None:
        return {"projected_demand": projected, "safety_buffer": safety, "target_inventory": target,
                "shortfall": None, "recommended_order": None}
    shortfall = max(0.0, target - max(on_hand, 0.0))
    recommended = 0 if shortfall < cfg.round_down_below else math.ceil(shortfall - cfg.round_down_below)
    return {
        "projected_demand": projected,
        "safety_buffer": safety,
        "target_inventory": target,
        "shortfall": shortfall,
        "recommended_order": float(recommended),
    }


def exception_flags(model: dict, on_hand: Optional[float], legacy_daily: float, has_count: bool) -> list[str]:
    flags = []
    if model["available_days"] < DEFAULT_CONFIG.insufficient_available_days:
        flags.append("insufficient_history")
    if model["current_stockout_days"] >= 7 or (
        model["window_days"] and model["censored_days"] / model["window_days"] > 0.5
    ):
        flags.append("prolonged_stockout")
    if model["spike_capped"]:
        flags.append("demand_spike")
    raw = model["raw_trend_factor"]
    if raw is not None and (raw > DEFAULT_CONFIG.trend_max or raw < DEFAULT_CONFIG.trend_min):
        flags.append("unusual_trend")
    if on_hand is not None and on_hand < 0:
        flags.append("negative_inventory")
    if not has_count:
        flags.append("missing_inventory_history")
    if model["classification"] == INTERMITTENT:
        flags.append("intermittent")
    if model["classification"] == DEAD:
        flags.append("dead_inventory")
    new_weekly = model["base_daily_demand"] * model["trend_factor"] * 7
    old_weekly = legacy_daily * 7
    if abs(new_weekly - old_weekly) >= 3 and (
        old_weekly == 0 or new_weekly == 0 or max(new_weekly, old_weekly) / min(new_weekly, old_weekly) >= 3
    ):
        flags.append("differs_from_history")
    return flags


def build_models(
    db: Session,
    item_ids: list[str],
    count_item_ids: dict[str, list[str]],
    store_item_ids: list[str],
    lookback_days: int,
    as_of: dt.date,
    cfg: ForecastConfig = DEFAULT_CONFIG,
) -> dict[str, dict]:
    """Models for `item_ids`. `count_item_ids[item]` lists the inventory rows
    whose counts add up to that item's shelf count (itself plus a Container
    duplicate, if any)."""
    start = as_of - dt.timedelta(days=lookback_days)
    days = [start + dt.timedelta(days=i) for i in range(lookback_days)]
    store_dow, hour_share = _store_profiles(db, store_item_ids, start, as_of)
    sales = _load_daily_sales(db, item_ids, start, as_of)
    all_count_ids = sorted({cid for ids in count_item_ids.values() for cid in ids} | set(item_ids))
    counts, listed, snapshot_dates = _load_opening_counts(db, all_count_ids, start, as_of)

    models = {}
    for item_id in item_ids:
        opening: dict[dt.date, float] = {}
        for cid in count_item_ids.get(item_id, [item_id]):
            for day, qty in counts.get(cid, {}).items():
                opening[day] = opening.get(day, 0.0) + qty
        unlisted = snapshot_dates - listed.get(item_id, set())
        model = forecast_item(
            days, opening, sales.get(item_id, {}), store_dow, hour_share, as_of, cfg, frozenset(unlisted)
        )
        model["has_count_history"] = bool(opening)
        models[item_id] = model
    return models
