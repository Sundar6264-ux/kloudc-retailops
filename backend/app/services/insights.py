"""Insights from the Toast Analytics API - the numbers behind the ten
/api/insights endpoints (docs/INSIGHTS_CONTRACT.md), Loss watch / Odd checks
flags, and the compare lines for the daily push and the weekly digest.

Everything here reads the cache (services/insights_data.py); nothing calls
Toast except `compare` for today's live numbers. A page asking for dates the
cache doesn't have yet gets what there is with `stale: true` (and, in
production, one background fill starts).

Conventions (as built): money is a number in dollars (2 decimals); `share`
and `returning_rate` are fractions 0..1; every `pct` is a percent change
(8.0 = up 8%). Weeks start on Monday. "Yesterday" is the last business date
closed at the 04:00 closeout.
"""

import datetime as dt
import json
import statistics
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.insights_models import GuestVisit, LossWatchFlag
from app.models import NotificationLog, Order, User
from app.services import insights_data as data
from app.services.insights_data import FIRST_REAL_SALES_DATE, r2
from app.timeutil import restaurant_timezone, utcnow

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LONG_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

HEATMAP_WEEKS = (4, 8, 12)
HEATMAP_TOP = 5
MENU_LIST_SIZE = 20
# Rising / falling only judge items that sold at least this many units in
# the previous period (so 1 -> 3 isn't "up 200%").
TREND_MIN_PREV_QTY = 3.0
PREP_WEEKS = 8
PREP_TREND_DAYS = 28
PREP_TREND_DAMPENING = 0.5
PREP_TREND_MIN = 0.8
PREP_TREND_MAX = 1.2
PREP_MIN_EXPECTED = 0.5
# Loss watch: the "above N x the employee's median" rule needs this many of
# their checks with a discount / refund / void in the window, and never flags
# less than this amount.
MEDIAN_MIN_CHECKS = 5
# Rehearsal on real data (Oct 13): at $10 a single voided $15.99 dish was
# "unusual" for staff whose median is a $5 coupon - noise.
MEDIAN_MIN_AMOUNT = 25.0
FULL_COMP_MAX_TOTAL = 0.01
# Odd checks: a modification this long after payment counts...
MODIFIED_AFTER_PAID_MINUTES = 60
# ...unless this many checks of the same day were modified in the same minute
# (Toast's own batch updates, e.g. 89 checks of Sep 15 at 18:03 on Sep 22).
BATCH_MODIFY_MIN_CHECKS = 3
# Online / delivery / kiosk orders are modified when they're fulfilled - not odd.
FULFILMENT_REVENUE_CENTERS = ("online ordering", "no revenue center")
FULFILMENT_DINING_OPTIONS = ("pick up",)
FLAG_LOOKBACK_DAYS = 7
ALERT_NAMES_SHOWN = 3

LOSS_REASONS = ("full_comp", "large_discount", "large_refund", "large_void", "above_employee_median")
ODD_REASONS = ("open_long", "modified_after_paid", "large_refund")
GROCERY_MENUS = {"retail", "produce", "halal meat", "fresh flowers", "fish market"}
# The glossary term (app/glossary.py) for each reason code the API returns.
REASON_GLOSSARY_KEYS = {
    "loss": {r: ("insights_loss_above_median" if r == "above_employee_median" else f"insights_loss_{r}") for r in LOSS_REASONS},
    "odd_check": {r: f"insights_odd_{r}" for r in ODD_REASONS},
}


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class Loaded:
    """Cache entries for one report over some dates, plus what's missing.
    Dates Toast couldn't provide (an "unavailable" marker) are neither in
    `entries` nor `missing`: they're in `unavailable` and wait for the
    nightly retry instead of a fill on every page open."""

    def __init__(
        self,
        report: str,
        dates: List[dt.date],
        entries: Dict[dt.date, "data.Entry"],
        db: Optional[Session] = None,
    ):
        self.report = report
        self.dates = dates
        self._all = entries
        self.entries = {d: e for d, e in entries.items() if not e.unavailable}
        self.unavailable = sorted(d for d in dates if d in entries and entries[d].unavailable)
        self.missing = [d for d in dates if d not in entries]
        self.provisional = [d for d, e in self.entries.items() if e.provisional]
        self.db = db  # for the coverage note (budget, nightly) - envelope()
        self.fill_started = False

    @property
    def updated_at(self) -> Optional[dt.datetime]:
        times = [e.fetched_at for e in self.entries.values() if e.fetched_at]
        return max(times) if times else None

    def subset(self, dates: List[dt.date]) -> "Loaded":
        """The same cache entries, counted over `dates` only (Loss watch
        reads 30 days for the median but shows - and counts - the 7 / 14 /
        30 chosen)."""
        keep = set(dates)
        out = Loaded(self.report, dates, {d: e for d, e in self._all.items() if d in keep}, self.db)
        out.fill_started = self.fill_started
        return out


def _load(db: Session, report: str, dates: List[dt.date], fill: bool = True) -> Loaded:
    loaded = Loaded(report, dates, data.cached(db, report, dates, unavailable=True), db)
    if fill and (loaded.missing or loaded.provisional):
        loaded.fill_started = data.request_fill([(report, loaded.missing + loaded.provisional)])
    return loaded


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat()


# What each report holds, for the coverage note ("Toast couldn't provide
# check details for Oct 3").
REPORT_WORDS = {
    "metrics_day": "daily sales",
    "metrics_dining": "sales by channel",
    "metrics_hour": "hourly sales",
    "menu_item": "item sales",
    "menu_modifier": "add-on sales",
    "check": "check details",
    "guest": "card payments",
}
MISSING_DATES_SHOWN = 10
NOTE_RUNS_SHOWN = 3
# Coverage `pending`, most serious first (one value per answer).
PENDING_ORDER = ("failed", "waiting_budget", "tonight", "filling")
# How long after the closeout a missing just-closed day still counts as
# "arrives with the nightly" (it normally runs 04:30 and takes minutes).
TONIGHT_GRACE = dt.timedelta(hours=3)
NOT_CONFIGURED_NOTE = "Toast Analytics isn't set up on this server yet, so only numbers saved earlier can be shown."
NO_ANSWER_NOTE = (
    "Toast Analytics didn't answer just now, so this is the last copy we saved. It may be missing the newest numbers."
)
MENU_MAP_NOTE = (
    "The menu list from Toast isn't saved yet, so kitchen and grocery items can't be told apart. "
    "It's read automatically, at the latest with the {nightly} update."
)


def _nightly_time() -> str:
    s = get_settings()
    return data.fmt_time(
        dt.datetime.combine(dt.date.today(), dt.time(s.insights_nightly_hour, s.insights_nightly_minute), restaurant_timezone())
    )


def _runs_text(dates: Iterable[dt.date]) -> str:
    """[Oct 1, Oct 2, Oct 3, Oct 9] -> "Oct 1 to Oct 3 and Oct 9" (the
    first few runs, then "and N more days")."""
    runs: List[List[dt.date]] = []
    for d in sorted(set(dates)):
        if runs and runs[-1][1] + dt.timedelta(days=1) == d:
            runs[-1][1] = d
        else:
            runs.append([d, d])
    parts = [data.fmt_day(a) if a == b else f"{data.fmt_day(a)} to {data.fmt_day(b)}" for a, b in runs[:NOTE_RUNS_SHOWN]]
    rest = sum((b - a).days + 1 for a, b in runs[NOTE_RUNS_SHOWN:])
    if rest:
        parts.append(f"{rest} more day{'s' if rest != 1 else ''}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _when_text(t: dt.datetime, now: dt.datetime) -> str:
    tz = restaurant_timezone()
    day, today = t.astimezone(tz).date(), now.astimezone(tz).date()
    if day == today:
        return data.fmt_time(t)
    if day == today + dt.timedelta(days=1):
        return f"{data.fmt_time(t)} tomorrow"
    return f"{data.fmt_time(t)} on {data.fmt_day(day)}"


def _is_are(dates) -> Tuple[str, str]:
    return ("isn't", "It's") if len(set(dates)) == 1 else ("aren't", "They're")


def _coverage_status(loads: Iterable[Loaded], extra_stale: bool, extra_note: Optional[str]) -> dict:
    """Why a page's days are missing, in plain words: {missing_dates,
    pending, retry_at, note}. Only called when the answer is stale."""
    now = utcnow()
    today_bd = data.today_business_date()
    last_closed = today_bd - dt.timedelta(days=1)
    filling_now = data.is_filling()
    groups: Dict[str, Dict[dt.date, set]] = defaultdict(lambda: defaultdict(set))
    frees: List[dt.datetime] = []
    left: Dict[str, int] = {}
    nightly_done: Optional[bool] = None
    for x in loads:
        db = x.db
        family = data.REPORTS.get(x.report, {}).get("daily_family")
        for d in x.unavailable:
            groups["failed"][d].add(x.report)
        for d in x.missing:
            if d >= today_bd:
                groups["live"][d].add(x.report)
                continue
            if db is None:
                groups["later"][d].add(x.report)
                continue
            if d == last_closed and now < data.closeout_at(d) + TONIGHT_GRACE:
                if nightly_done is None:
                    nightly_done = data.nightly_done_since(db, data.closeout_at(d))
                if not nightly_done:
                    groups["tonight"][d].add(x.report)
                    continue
            if family:
                if family not in left:
                    left[family] = data.daily_left(db, family)
                priority = d >= today_bd - dt.timedelta(days=data.PRIORITY_DAYS)
                need = 1 if priority else data.daily_reserve(family) + 1
                if left[family] < need:
                    groups["waiting_budget"][d].add(x.report)
                    t = data.budget_frees_at(db, family, need)
                    if t is not None:
                        frees.append(t)
                    continue
            if x.fill_started or filling_now:
                groups["filling"][d].add(x.report)
            else:
                groups["later"][d].add(x.report)

    def words(g: Dict[dt.date, set]) -> str:
        reports = sorted({r for rs in g.values() for r in rs})
        return " and ".join(REPORT_WORDS.get(r, r) for r in reports[:2])

    nightly = _nightly_time()
    notes = []
    if "tonight" in groups:
        days = list(groups["tonight"])
        notes.append(f"Yesterday's numbers ({_runs_text(days)}) arrive after the {nightly} update.")
    if "filling" in groups:
        days = list(groups["filling"])
        notes.append(
            f"{_runs_text(days)} {'is' if len(days) == 1 else 'are'} being read from Toast now. "
            "Check back in a few minutes."
        )
    if "waiting_budget" in groups:
        days = list(groups["waiting_budget"])
        neg, it = _is_are(days)
        families = sorted({data.REPORTS[r]["daily_family"] for rs in groups["waiting_budget"].values() for r in rs})
        fam = families[0]
        what = REPORT_WORDS.get(fam, fam)
        when = data.next_fill_at(min(frees)) if frees else None
        # Only older days (not the last PRIORITY_DAYS): the hourly catch-up
        # never reads them and calls may still be left, kept for the newest
        # days (DAILY_RESERVE) - say that, not "used up" / "by 2:20 PM".
        older_only = all(d < today_bd - dt.timedelta(days=data.PRIORITY_DAYS) for d in days)
        why = "today's are used up" if left.get(fam, 0) <= 0 else "the calls left today are kept for the newest days"
        if older_only:
            tail = f"{it} added automatically, at the latest with the {nightly} update."
        elif when is None:
            tail = f"{it} added automatically."
        elif len(days) <= data.PRIORITY_DAYS:
            tail = f"{it} added automatically by {_when_text(when, now)}."
        else:
            tail = f"{it} added automatically, a few at a time, starting {_when_text(when, now)}."
        notes.append(
            f"{_runs_text(days)} {neg} in yet: Toast lets us read {what} "
            f"{data.daily_budget(fam)} times a day and {why}. {tail}"
        )
    if "failed" in groups:
        notes.append(
            f"Toast couldn't provide {words(groups['failed'])} for {_runs_text(groups['failed'])}. "
            f"We'll ask again with the {nightly} update."
        )
    if "later" in groups:
        days = list(groups["later"])
        neg, it = _is_are(days)
        notes.append(f"{_runs_text(days)} {neg} saved yet. {it} added automatically, at the latest with the {nightly} update.")
    if "live" in groups or (extra_stale and not extra_note and not notes):
        notes.append(NO_ANSWER_NOTE)
    if extra_note:
        notes.append(extra_note.format(nightly=nightly))
    missing = sorted({d for g in groups.values() for d in g}, reverse=True)
    pending = next((p for p in PENDING_ORDER if p in groups), None)
    return {
        "missing_dates": [d.isoformat() for d in missing[:MISSING_DATES_SHOWN]],
        "pending": pending,
        "retry_at": _iso(min(frees)) if pending == "waiting_budget" and frees else None,
        "note": " ".join(notes) or None,
    }


def envelope(payload: dict, *loads: Loaded, extra_stale: bool = False, extra_note: Optional[str] = None) -> dict:
    """The fields every answer carries. `note` (plain words, null when
    nothing is stale) and coverage `missing_dates` / `pending` / `retry_at`
    say exactly what's missing and when it comes - the app shows `note` as
    is. `pending`: "filling" (being read now), "waiting_budget" (Toast's
    daily limit; `retry_at` is when calls free up), "tonight" (yesterday,
    before the nightly update), "failed" (Toast couldn't provide it; retried
    nightly) or null."""
    configured = data.is_configured()
    missing = sum(len(x.missing) + len(x.unavailable) for x in loads)
    expected = sum(len(x.dates) for x in loads)
    times = [x.updated_at for x in loads if x.updated_at]
    stale = bool(extra_stale or missing or not configured)
    coverage = {
        "dates_expected": expected,
        "dates_cached": expected - missing,
        "missing_dates": [],
        "pending": None,
        "retry_at": None,
    }
    note = None
    if stale and not configured:
        note = NOT_CONFIGURED_NOTE
    elif stale:
        status = _coverage_status(loads, extra_stale, extra_note if extra_stale else None)
        note = status.pop("note")
        coverage.update(status)
    out = {
        "generated_at": utcnow().isoformat(),
        "stale": stale,
        "source": data.SOURCE,
        "configured": configured,
        "updated_at": _iso(max(times)) if times else None,
        # Only on an incomplete answer: the fill lock is process-wide (the
        # hourly catch-up / nightly for other reports), and the app shows
        # "still coming in from Toast" whenever this is true.
        "filling": stale and data.is_filling(),
        "coverage": coverage,
        "note": note,
    }
    out.update(payload)
    return out


def _pct(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base is None or abs(base) < 0.005:
        return None
    return round((cur - base) / abs(base) * 100.0, 1)


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _hour(row: dict) -> Optional[int]:
    try:
        h = int(str(row.get("businessHour")).strip())
    except (TypeError, ValueError):
        return None
    return h if 0 <= h <= 23 else None


def _week_starts(end: dt.date, weeks: int) -> List[dt.date]:
    last = _monday(end)
    return [last - dt.timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]


# ---------------------------------------------------------------------------
# 1. Hourly heatmap
# ---------------------------------------------------------------------------


def hourly_heatmap(db: Session, weeks: int, hide_sales: bool) -> dict:
    end = data.last_closed_date()
    dates = data.expected_dates(end - dt.timedelta(days=7 * weeks - 1), end)
    loaded = _load(db, "metrics_hour", dates)
    net = defaultdict(list)
    orders = defaultdict(list)
    days_per_dow = defaultdict(int)
    for d, entry in loaded.entries.items():
        dow = d.weekday()
        days_per_dow[dow] += 1
        hours = defaultdict(lambda: [0.0, 0.0])
        for row in entry.rows:
            h = _hour(row)
            if h is None:
                continue
            hours[h][0] += data._num(row.get("netSalesAmount"))
            hours[h][1] += data._num(row.get("ordersCount"))
        for h in range(24):
            net[(dow, h)].append(hours[h][0] if h in hours else 0.0)
            orders[(dow, h)].append(hours[h][1] if h in hours else 0.0)
    cells = []
    for dow in range(7):
        for h in range(24):
            n = days_per_dow.get(dow, 0)
            nv = net.get((dow, h), [])
            ov = orders.get((dow, h), [])
            cell = {
                "dow": dow,
                "hour": h,
                "avg_net_sales": r2(sum(nv) / n) if n else 0.0,
                "avg_orders": round(sum(ov) / n, 1) if n else 0.0,
                "max_net_sales": r2(max(nv)) if nv else 0.0,
                "open_days": sum(1 for x in ov if x > 0),
                "days": n,
            }
            cells.append(cell)
    key = "avg_orders" if hide_sales else "avg_net_sales"
    active = [c for c in cells if c["days"] and c["open_days"] * 2 >= c["days"] and c["avg_orders"] > 0]
    busiest = sorted(active, key=lambda c: (-c[key], c["dow"], c["hour"]))[:HEATMAP_TOP]
    quietest = sorted(active, key=lambda c: (c[key], c["dow"], c["hour"]))[:HEATMAP_TOP]

    def slim(c, with_net):
        out = {"dow": c["dow"], "hour": c["hour"]}
        if with_net:
            out["avg_net_sales"] = c["avg_net_sales"]
        out["avg_orders"] = c["avg_orders"]
        return out

    if hide_sales:
        cells_out = [{"dow": c["dow"], "hour": c["hour"], "avg_orders": c["avg_orders"]} for c in cells]
    else:
        cells_out = [
            {k: c[k] for k in ("dow", "hour", "avg_net_sales", "avg_orders", "max_net_sales")} for c in cells
        ]
    return envelope(
        {
            "weeks": weeks,
            "days": DAY_NAMES,
            "hours": list(range(24)),
            "cells": cells_out,
            "busiest": [slim(c, not hide_sales) for c in busiest],
            "quietest": [slim(c, not hide_sales) for c in quietest],
            "days_used": {DAY_NAMES[k]: v for k, v in sorted(days_per_dow.items())},
            "sales_hidden": hide_sales,
            "from": dates[0].isoformat() if dates else None,
            "to": end.isoformat(),
        },
        loaded,
    )


# ---------------------------------------------------------------------------
# 2. Channels (dining option)
# ---------------------------------------------------------------------------


def channel_of(dining_option: Optional[str]) -> str:
    """Toast dining option -> sales channel. On this store: In-Store Purchase
    (counter), Pick Up (the kiosk), Online Ordering - Takeout, DoorDash /
    Uber Eats - Delivery / Takeout, Catering - Delivery / Pick Up, and No
    Dining Option."""
    s = (dining_option or "").strip()
    low = s.lower()
    if not s or low == "no dining option":
        return "Other"
    if low.startswith("in-store") or low.startswith("in store"):
        return "In-store"
    if low in ("pick up", "pickup", "take out", "takeout"):
        return "Pick up"
    prefix = s.split(" - ", 1)[0].strip()
    if prefix.lower() == "online ordering":
        return "Online ordering"
    return prefix or "Other"


def channels(db: Session, weeks: int) -> dict:
    end = data.last_closed_date()
    starts = _week_starts(end, weeks)
    dates = data.expected_dates(starts[0], end)
    loaded = _load(db, "metrics_dining", dates)
    per_week: Dict[dt.date, Dict[str, List[float]]] = {s: defaultdict(lambda: [0.0, 0]) for s in starts}
    days_in_week: Dict[dt.date, int] = defaultdict(int)
    totals: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0])
    for d, entry in loaded.entries.items():
        ws = _monday(d)
        if ws not in per_week:
            continue
        days_in_week[ws] += 1
        for row in entry.rows:
            ch = channel_of(row.get("diningOption"))
            n = data._num(row.get("netSalesAmount"))
            o = int(data._num(row.get("ordersCount")))
            per_week[ws][ch][0] += n
            per_week[ws][ch][1] += o
            totals[ch][0] += n
            totals[ch][1] += o
    names = [c for c, _ in sorted(totals.items(), key=lambda kv: (-kv[1][0], kv[0])) if abs(_[0]) > 0.004 or _[1]]
    grand = sum(v[0] for v in totals.values())
    out_weeks = []
    for ws in starts:
        by = per_week[ws]
        total = sum(v[0] for v in by.values())
        out_weeks.append(
            {
                "week_start": ws.isoformat(),
                "total_net": r2(total),
                "by": [
                    {
                        "channel": ch,
                        "net_sales": r2(by[ch][0]) if ch in by else 0.0,
                        "orders": int(by[ch][1]) if ch in by else 0,
                        "share": round(by[ch][0] / total, 4) if ch in by and total > 0 else 0.0,
                    }
                    for ch in names
                ],
                "days": days_in_week.get(ws, 0),
                "partial": ws + dt.timedelta(days=6) > end,
            }
        )
    return envelope(
        {
            "weeks": out_weeks,
            "channels": names,
            "split": "dining_option",
            "totals": [
                {
                    "channel": ch,
                    "net_sales": r2(totals[ch][0]),
                    "orders": int(totals[ch][1]),
                    "share": round(totals[ch][0] / grand, 4) if grand > 0 else 0.0,
                }
                for ch in names
            ],
        },
        loaded,
    )


# ---------------------------------------------------------------------------
# 3. Daily compare
# ---------------------------------------------------------------------------


def _day_totals(entry) -> Optional[dict]:
    if entry is None:
        return None
    t = data.sum_rows(entry.rows)
    orders = int(t["ordersCount"])
    net = r2(t["netSalesAmount"])
    return {
        "net_sales": net,
        "orders": orders,
        "guests": int(t["guestCount"]),
        "avg_check": r2(net / orders) if orders else None,
    }


def _business_hour_pos(hour: int) -> int:
    """Where a clock hour falls in the business day, which runs from the
    04:00 closeout to 03:59 the next morning (04:00 -> 0, 03:00 -> 23)."""
    return (hour - data.CLOSEOUT_HOUR) % 24


def _hours_through(db: Session, entry, now_local: dt.datetime) -> Optional[dict]:
    """A past day's totals up to the same time of day as `now_local`, from its
    hourly cache entry - so a partial today compares like with like: every
    business hour before the current one, plus the current hour's share for
    the minutes gone (at 12:45 today holds 12:00-12:45, so the base day
    counts three quarters of its 12 PM hour, not none of it). After midnight
    the business day isn't over yet: the hours since 04:00 all count."""
    if entry is None:
        return None
    pos = _business_hour_pos(now_local.hour)
    frac = (now_local.minute * 60 + now_local.second) / 3600.0
    net = orders = 0.0
    for r in entry.rows:
        h = _hour(r)
        if h is None:
            continue
        p = _business_hour_pos(h)
        weight = 1.0 if p < pos else (frac if p == pos else 0.0)
        if weight:
            net += data._num(r.get("netSalesAmount")) * weight
            orders += data._num(r.get("ordersCount")) * weight
    return {"net_sales": r2(net), "orders": int(round(orders))}


def compare(db: Session, day: Optional[dt.date] = None, now: Optional[dt.datetime] = None) -> dict:
    today_bd = data.today_business_date()
    day = day or today_bd - dt.timedelta(days=1)
    live_failed = False
    if day >= today_bd:
        entry, live_failed = data.live_fetch(db, "metrics_day", day)
        cur_entries = {day: entry} if entry is not None else {}
        cur = Loaded("metrics_day", [day], cur_entries)
    else:
        cur = _load(db, "metrics_day", [day])
    last_week = day - dt.timedelta(days=7)
    four = [day - dt.timedelta(days=7 * k) for k in range(1, 5)]
    last_year = day - dt.timedelta(days=364)
    base_dates = sorted(set(four + ([last_year] if last_year >= FIRST_REAL_SALES_DATE else [])))
    base_dates = [d for d in base_dates if d >= FIRST_REAL_SALES_DATE]
    base = _load(db, "metrics_day", base_dates)
    totals = _day_totals(cur.entries.get(day))
    partial = day >= today_bd
    through_hour = None
    loads = [cur, base]
    if partial:
        now_local = (now or dt.datetime.now(restaurant_timezone())).astimezone(restaurant_timezone())
        through_hour = now_local.hour
        # Today's partial total is only comparable with the same hours of the
        # earlier days: their hourly reports (fetched in the background when
        # missing). A day without one is left out rather than compared whole.
        hourly = _load(db, "metrics_hour", base_dates)
        loads.append(hourly)

    def base_totals(d: dt.date) -> Optional[dict]:
        if partial:
            return _hours_through(db, hourly.entries.get(d), now_local)
        return _day_totals(base.entries.get(d))

    cur_net = totals["net_sales"] if totals else None
    cur_orders = totals["orders"] if totals else None
    lw = base_totals(last_week)
    vs_last_week = {
        "date": last_week.isoformat(),
        "net_sales": lw["net_sales"] if lw else None,
        "pct": _pct(cur_net, lw["net_sales"]) if lw else None,
        "orders": lw["orders"] if lw else None,
        "orders_pct": _pct(cur_orders, lw["orders"]) if lw else None,
    }
    fours = [t for t in (base_totals(d) for d in four) if t and (t["net_sales"] or t["orders"])]
    avg_net = r2(sum(t["net_sales"] for t in fours) / len(fours)) if fours else None
    avg_orders = round(sum(t["orders"] for t in fours) / len(fours), 1) if fours else None
    vs_4wk = {
        "dates": [d.isoformat() for d in four],
        "weeks_used": len(fours),
        "net_sales": avg_net,
        "pct": _pct(cur_net, avg_net),
        "orders": avg_orders,
        "orders_pct": _pct(cur_orders, avg_orders),
    }
    vs_last_year = None
    if last_year >= FIRST_REAL_SALES_DATE:
        ly = base_totals(last_year)
        if ly:
            vs_last_year = {"date": last_year.isoformat(), "net_sales": ly["net_sales"], "pct": _pct(cur_net, ly["net_sales"])}
    return envelope(
        {
            "date": day.isoformat(),
            "weekday": LONG_DAY_NAMES[day.weekday()],
            "net_sales": cur_net,
            "orders": cur_orders,
            "avg_check": totals["avg_check"] if totals else None,
            "guests": totals["guests"] if totals else None,
            "vs_last_week": vs_last_week,
            "vs_4wk_avg": vs_4wk,
            "vs_last_year": vs_last_year,
            "partial": partial,
            "through_hour": through_hour,
        },
        *loads,
        extra_stale=live_failed,
    )


def arrow(pct: Optional[float]) -> str:
    if pct is None:
        return ""
    if pct > 0:
        return f"▲{abs(pct):.0f}%"
    if pct < 0:
        return f"▼{abs(pct):.0f}%"
    return "0%"


def compare_line(cmp: dict) -> Optional[str]:
    """"Sales vs last week ▲8% · vs 4-week avg ▼3%" (None without data)."""
    parts = []
    lw = (cmp.get("vs_last_week") or {}).get("pct")
    if lw is not None:
        parts.append(f"vs last {cmp.get('weekday', 'week')[:3]} {arrow(lw)}")
    four = (cmp.get("vs_4wk_avg") or {}).get("pct")
    if four is not None:
        parts.append(f"vs 4-week avg {arrow(four)}")
    if not parts:
        return None
    return " · ".join(parts)


def money(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"${v:,.0f}"


def week_compare(db: Session, end: dt.date) -> Optional[dict]:
    """The 7 days ending `end` vs the 7 before and the 4 weeks before, from
    the cache (Toast Analytics net sales). None when the week isn't cached.

    The digest asks for the week ending today, which is still open (and never
    in the cache): the week then ends on the last closed business date
    instead - `week_start` / `week_end` say which 7 days it is."""
    end = min(end, data.last_closed_date())

    def total(a: dt.date) -> Optional[dict]:
        dates = data.daterange(a, a + dt.timedelta(days=6))
        have = data.cached(db, "metrics_day", dates)
        if len(have) < len([d for d in dates if d >= FIRST_REAL_SALES_DATE]) or not have:
            return None
        t = data.sum_rows([r for e in have.values() for r in e.rows])
        return {"net_sales": r2(t["netSalesAmount"]), "orders": int(t["ordersCount"])}

    start = end - dt.timedelta(days=6)
    cur = total(start)
    if cur is None:
        return None
    prev = total(start - dt.timedelta(days=7))
    olders = [t for t in (total(start - dt.timedelta(days=7 * k)) for k in range(1, 5)) if t]
    avg = r2(sum(t["net_sales"] for t in olders) / len(olders)) if olders else None
    return {
        "source": data.SOURCE,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "net_sales": cur["net_sales"],
        "orders": cur["orders"],
        "vs_last_week": {"net_sales": prev["net_sales"] if prev else None, "pct": _pct(cur["net_sales"], prev["net_sales"]) if prev else None},
        "vs_4wk_avg": {"net_sales": avg, "pct": _pct(cur["net_sales"], avg), "weeks_used": len(olders)},
    }


def week_compare_line(wc: Optional[dict]) -> Optional[str]:
    if not wc:
        return None
    parts = [f"Toast Analytics: {money(wc['net_sales'])} net sales for the week"]
    lw = wc["vs_last_week"]["pct"]
    if lw is not None:
        parts.append(f"{arrow(lw)} vs the week before")
    four = wc["vs_4wk_avg"]["pct"]
    if four is not None:
        parts.append(f"{arrow(four)} vs the 4-week average")
    return ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# 4. Check trends
# ---------------------------------------------------------------------------


def check_trends(db: Session, weeks: int) -> dict:
    end = data.last_closed_date()
    dates = data.expected_dates(end - dt.timedelta(days=7 * weeks - 1), end)
    loaded = _load(db, "metrics_day", dates)
    days = []
    weekly: Dict[dt.date, dict] = {}
    for d in dates:
        entry = loaded.entries.get(d)
        if entry is None:
            continue
        t = _day_totals(entry)
        days.append({"date": d.isoformat(), **{k: t[k] for k in ("avg_check", "guests", "orders", "net_sales")}})
        w = weekly.setdefault(_monday(d), {"net_sales": 0.0, "orders": 0, "guests": 0, "days": 0})
        w["net_sales"] += t["net_sales"]
        w["orders"] += t["orders"]
        w["guests"] += t["guests"]
        w["days"] += 1
    out_weekly = [
        {
            "week_start": ws.isoformat(),
            "avg_check": r2(w["net_sales"] / w["orders"]) if w["orders"] else None,
            "guests": w["guests"],
            "orders": w["orders"],
            "net_sales": r2(w["net_sales"]),
            "days": w["days"],
            "partial": ws + dt.timedelta(days=6) > end,
        }
        for ws, w in sorted(weekly.items())
    ]
    return envelope({"weeks": weeks, "days": days, "weekly": out_weekly}, loaded)


# ---------------------------------------------------------------------------
# Checks (Loss watch, Odd checks)
# ---------------------------------------------------------------------------


def _ts(raw) -> Optional[dt.datetime]:
    if not raw:
        return None
    try:
        v = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)


def _check_db_facts(db: Session, guids: List[str]) -> Tuple[Dict[str, dt.datetime], Dict[str, float]]:
    """From our synced orders: when each check was opened (earliest line) and
    the voided item lines' net on it (Toast's check report has no void
    amount - a voided check shows a $0 total)."""
    opened: Dict[str, dt.datetime] = {}
    voids: Dict[str, float] = {}
    for i in range(0, len(guids), 500):
        part = guids[i : i + 500]
        for g, at in db.execute(
            select(Order.toast_check_guid, func.min(Order.opened_at))
            .where(Order.toast_check_guid.in_(part))
            .group_by(Order.toast_check_guid)
        ):
            if isinstance(at, str):
                at = dt.datetime.fromisoformat(at)
            if at is not None and at.tzinfo is None:
                at = at.replace(tzinfo=dt.timezone.utc)
            if at is not None:
                opened[g] = at
        for g, v in db.execute(
            select(Order.toast_check_guid, func.sum(Order.net_price))
            .where(
                Order.toast_check_guid.in_(part),
                Order.voided.is_(True),
                Order.top_level(),
            )
            .group_by(Order.toast_check_guid)
        ):
            voids[g] = r2(v)
    return opened, voids


def load_checks(db: Session, dates: List[dt.date], fill: bool = True) -> Tuple[Loaded, List[dict]]:
    loaded = _load(db, "check", dates, fill=fill)
    rows = []
    for d, entry in loaded.entries.items():
        for r in entry.rows:
            rows.append((d, entry.fetched_at, r))
    guids = [r.get("checkGuid") for _, _, r in rows if r.get("checkGuid")]
    opened, voids = _check_db_facts(db, guids)
    checks = []
    for d, fetched_at, r in rows:
        g = r.get("checkGuid")
        voided = bool(r.get("checkVoidedStatus"))
        discount = r2(r.get("checkDiscountAmount"))
        refund = r2(r.get("checkRefundAmount"))
        void = voids.get(g, 0.0)
        checks.append(
            {
                "date": d,
                "check_guid": g,
                "check_number": str(r.get("checkNumber") or r.get("orderNumber") or ""),
                "employee": (r.get("serverName") or "").strip() or "Unknown",
                "total": r2(r.get("checkTotalAmount")),
                "discount": discount,
                "refund": refund,
                "void": void,
                "voided": voided,
                "status": r.get("checkStatus"),
                "opened_at": opened.get(g),
                "paid_at": _ts(r.get("checkPaidDateTime")),
                "modified_at": _ts(r.get("checkModifiedDateTime")),
                "revenue_center": r.get("revenueCenter"),
                "dining_option": r.get("diningOption"),
                "fetched_at": fetched_at,
                "loss": r2(discount + refund + void),
            }
        )
    checks.sort(key=lambda c: (c["date"], c["check_number"]))
    return loaded, checks


def loss_reasons(checks: List[dict]) -> Dict[str, List[str]]:
    """check_guid -> reasons, see LOSS_REASONS / the glossary."""
    s = get_settings()
    per_emp = defaultdict(list)
    for c in checks:
        if c["loss"] > 0:
            per_emp[c["employee"]].append(c["loss"])
    medians = {e: statistics.median(v) for e, v in per_emp.items() if len(v) >= MEDIAN_MIN_CHECKS}
    out = {}
    for c in checks:
        reasons = []
        if not c["voided"] and c["discount"] > 0 and c["total"] <= FULL_COMP_MAX_TOTAL:
            reasons.append("full_comp")
        if c["discount"] >= s.loss_watch_large_discount:
            reasons.append("large_discount")
        if c["refund"] >= s.loss_watch_large_refund:
            reasons.append("large_refund")
        if c["void"] >= s.loss_watch_large_void:
            reasons.append("large_void")
        med = medians.get(c["employee"])
        if (
            med is not None
            and c["loss"] >= MEDIAN_MIN_AMOUNT
            and c["loss"] > s.loss_watch_median_multiple * med
        ):
            reasons.append("above_employee_median")
        if reasons and c["check_guid"]:
            out[c["check_guid"]] = reasons
    return out


def odd_reasons(checks: List[dict]) -> Dict[str, Tuple[List[str], Optional[int], bool]]:
    """check_guid -> (reasons, minutes_open, modified_after_paid)."""
    s = get_settings()
    tz = restaurant_timezone()
    batch = defaultdict(int)
    for c in checks:
        if c["modified_at"] is not None:
            batch[(c["date"], c["modified_at"].strftime("%Y%m%d%H%M"))] += 1
    out = {}
    for c in checks:
        if c["voided"] or not c["check_guid"]:
            continue
        minutes = None
        if c["opened_at"] is not None:
            end = c["paid_at"] if c["paid_at"] is not None else (c["fetched_at"] if c["status"] == "OPEN" else None)
            if end is not None:
                m = (end - c["opened_at"]).total_seconds() / 60.0
                minutes = int(round(m)) if m >= 0 else None
        reasons = []
        if minutes is not None and minutes > s.odd_check_open_hours * 60:
            reasons.append("open_long")
        modified = False
        if c["paid_at"] is not None and c["modified_at"] is not None:
            gap = (c["modified_at"] - c["paid_at"]).total_seconds() / 60.0
            batched = batch[(c["date"], c["modified_at"].strftime("%Y%m%d%H%M"))] >= BATCH_MODIFY_MIN_CHECKS
            prepaid = c["paid_at"].astimezone(tz).date() < c["date"]
            fulfilment = (c["revenue_center"] or "").strip().lower() in FULFILMENT_REVENUE_CENTERS or (
                c["dining_option"] or ""
            ).strip().lower() in FULFILMENT_DINING_OPTIONS
            # Same-day edits after payment are routine here (the kitchen
            # bumping a Street Kitchen order a while later updates the check;
            # real data Oct 8-12: 5 such checks, none with a refund or a
            # change in total). What's worth a look is a check edited after
            # its business day was closed out.
            after_close = c["modified_at"] >= data.closeout_at(c["date"])
            if (
                gap > MODIFIED_AFTER_PAID_MINUTES
                and after_close
                and not batched
                and not prepaid
                and (not fulfilment or c["refund"] > 0)
            ):
                modified = True
                reasons.append("modified_after_paid")
        if c["refund"] >= s.odd_check_large_refund:
            reasons.append("large_refund")
        out[c["check_guid"]] = (reasons, minutes, modified)
    return out


def _flag_key(kind: str, check_guid: str) -> str:
    return f"{kind}:{check_guid}"


def alert_dedupe_key(flag_id: int) -> str:
    return f"loss_watch:flag:{flag_id}"


def _sync_flags(db: Session, kind: str, dates: List[dt.date], computed: Dict[str, dict]) -> Dict[str, LossWatchFlag]:
    """Upsert the flags computed for `dates`; drop ones that no longer apply
    unless someone acknowledged them or an alert already named them."""
    if not dates:
        return {}
    existing = {
        f.flag_key: f
        for f in db.execute(
            select(LossWatchFlag).where(LossWatchFlag.kind == kind, LossWatchFlag.business_date.in_(dates))
        ).scalars()
    }
    alerted = set(
        db.execute(
            select(NotificationLog.dedupe_key).where(
                NotificationLog.dedupe_key.in_([alert_dedupe_key(f.id) for f in existing.values()] or ["-"])
            )
        ).scalars()
    )
    for key, f in existing.items():
        if key not in computed and f.acked_at is None and alert_dedupe_key(f.id) not in alerted:
            db.delete(f)
    out = {}
    for key, c in computed.items():
        f = existing.get(key)
        if f is None:
            f = LossWatchFlag(flag_key=key, kind=kind, created_at=utcnow())
            db.add(f)
        f.business_date = c["date"]
        f.check_guid = c["check_guid"]
        f.check_number = c["check_number"]
        f.employee = c["employee"]
        f.reasons = ",".join(c["reasons"])
        f.amount = c["amount"]
        out[key] = f
    db.commit()
    return out


def refresh_flags(db: Session, today_bd: Optional[dt.date] = None, days: int = FLAG_LOOKBACK_DAYS) -> dict:
    """Recompute Loss watch and Odd checks flags for the last `days` closed
    dates from the cache (no Toast calls). The median rule uses 30 days."""
    today_bd = today_bd or data.today_business_date()
    end = today_bd - dt.timedelta(days=1)
    window = data.expected_dates(end - dt.timedelta(days=29), end)
    flag_dates = data.expected_dates(end - dt.timedelta(days=days - 1), end)
    loaded, checks = load_checks(db, window, fill=False)
    flag_dates = [d for d in flag_dates if d in loaded.entries]
    loss = loss_reasons(checks)
    odd = odd_reasons(checks)
    loss_c, odd_c = {}, {}
    for c in checks:
        if c["date"] not in flag_dates:
            continue
        g = c["check_guid"]
        if g in loss:
            loss_c[_flag_key("loss", g)] = {**c, "reasons": loss[g], "amount": c["loss"]}
        if g in odd and odd[g][0]:
            odd_c[_flag_key("odd_check", g)] = {**c, "reasons": odd[g][0], "amount": c["refund"] or c["total"]}
    _sync_flags(db, "loss", flag_dates, loss_c)
    _sync_flags(db, "odd_check", flag_dates, odd_c)
    return {"dates": len(flag_dates), "loss": len(loss_c), "odd_checks": len(odd_c)}


def _flag_info(f: Optional[LossWatchFlag]) -> dict:
    if f is None:
        return {"id": None, "acked": False, "acked_at": None, "acked_by": None}
    return {"id": f.id, "acked": f.acked_at is not None, "acked_at": _iso(f.acked_at), "acked_by": f.acked_by_name}


def _flags_for(db: Session, kind: str, dates: List[dt.date]) -> Dict[str, LossWatchFlag]:
    if not dates:
        return {}
    return {
        f.flag_key: f
        for f in db.execute(
            select(LossWatchFlag).where(LossWatchFlag.kind == kind, LossWatchFlag.business_date.in_(dates))
        ).scalars()
    }


def loss_watch(db: Session, days: int) -> dict:
    end = data.last_closed_date()
    dates = data.expected_dates(end - dt.timedelta(days=days - 1), end)
    window = data.expected_dates(end - dt.timedelta(days=29), end)
    loaded, all_checks = load_checks(db, sorted(set(window) | set(dates)))
    reasons = loss_reasons(all_checks)
    checks = [c for c in all_checks if c["date"] in set(dates)]
    computed = {
        _flag_key("loss", c["check_guid"]): {**c, "reasons": reasons[c["check_guid"]], "amount": c["loss"]}
        for c in checks
        if c["check_guid"] in reasons
    }
    flags = _sync_flags(db, "loss", [d for d in dates if d in loaded.entries], computed)
    flags.update({k: v for k, v in _flags_for(db, "loss", dates).items() if k not in flags})
    metrics = data.cached(db, "metrics_day", dates)

    daily = []
    for d in dates:
        if d not in loaded.entries:
            continue
        day_checks = [c for c in checks if c["date"] == d]
        live = [c for c in day_checks if not c["voided"]]
        m = metrics.get(d)
        toast_void = r2(data.sum_rows(m.rows)["voidOrdersAmount"]) if m and m.rows else None
        item_voids = r2(sum(c["void"] for c in day_checks))
        daily.append(
            {
                "date": d.isoformat(),
                "discount": r2(sum(c["discount"] for c in live)),
                "void": toast_void if toast_void is not None else item_voids,
                "refund": r2(sum(c["refund"] for c in live)),
                "comp_estimate": r2(
                    sum(c["discount"] for c in live if c["discount"] > 0 and c["total"] <= FULL_COMP_MAX_TOTAL)
                ),
                "discount_checks": sum(1 for c in live if c["discount"] > 0),
                "void_checks": sum(1 for c in day_checks if c["voided"] or c["void"] > 0),
                "checks": len(live),
                "item_voids": item_voids,
            }
        )

    per_emp: Dict[str, dict] = {}
    losses = defaultdict(list)
    for c in all_checks:
        if c["loss"] > 0:
            losses[c["employee"]].append(c["loss"])
    for c in checks:
        e = per_emp.setdefault(
            c["employee"], {"employee": c["employee"], "checks": 0, "discount": 0.0, "void": 0.0, "refund": 0.0, "flagged": 0}
        )
        e["checks"] += 1
        e["discount"] += c["discount"]
        e["void"] += c["void"]
        e["refund"] += c["refund"]
        if c["check_guid"] in reasons:
            e["flagged"] += 1
    by_employee = []
    for e in sorted(per_emp.values(), key=lambda e: (-(e["discount"] + e["void"] + e["refund"]), e["employee"])):
        v = losses.get(e["employee"], [])
        by_employee.append(
            {
                "employee": e["employee"],
                "checks": e["checks"],
                "discount": r2(e["discount"]),
                "void": r2(e["void"]),
                "refund": r2(e["refund"]),
                "unusual": e["flagged"] > 0,
                "flagged": e["flagged"],
                "median_loss": r2(statistics.median(v)) if len(v) >= MEDIAN_MIN_CHECKS else None,
            }
        )

    flagged = []
    for c in sorted(checks, key=lambda c: (c["date"], c["loss"]), reverse=True):
        rs = reasons.get(c["check_guid"])
        if not rs:
            continue
        f = flags.get(_flag_key("loss", c["check_guid"]))
        flagged.append(
            {
                "date": c["date"].isoformat(),
                "check_number": c["check_number"],
                "employee": c["employee"],
                "total": c["total"],
                "discount": c["discount"],
                "refund": c["refund"],
                "void": c["void"],
                "reason_flag": rs[0],
                "reasons": rs,
                **_flag_info(f),
            }
        )
    s = get_settings()
    return envelope(
        {
            "days": days,
            "daily": list(reversed(daily)),
            "by_employee": by_employee,
            "flagged_checks": flagged,
            "thresholds": {
                "large_discount": s.loss_watch_large_discount,
                "large_refund": s.loss_watch_large_refund,
                "large_void": s.loss_watch_large_void,
                "median_multiple": s.loss_watch_median_multiple,
            },
        },
        loaded.subset(dates),
    )


def odd_checks(db: Session, days: int) -> dict:
    end = data.last_closed_date()
    dates = data.expected_dates(end - dt.timedelta(days=days - 1), end)
    loaded, checks = load_checks(db, dates)
    reasons = odd_reasons(checks)
    computed = {}
    for c in checks:
        r = reasons.get(c["check_guid"])
        if r and r[0]:
            computed[_flag_key("odd_check", c["check_guid"])] = {**c, "reasons": r[0], "amount": c["refund"] or c["total"]}
    flags = _sync_flags(db, "odd_check", [d for d in dates if d in loaded.entries], computed)
    items = []
    for c in sorted(checks, key=lambda c: (c["date"], c["check_number"]), reverse=True):
        r = reasons.get(c["check_guid"])
        if not r or not r[0]:
            continue
        f = flags.get(_flag_key("odd_check", c["check_guid"]))
        items.append(
            {
                "date": c["date"].isoformat(),
                "check_number": c["check_number"],
                "opened_at": _iso(c["opened_at"]),
                "paid_at": _iso(c["paid_at"]),
                "minutes_open": r[1],
                "modified_after_paid": r[2],
                "refund": c["refund"],
                "total": c["total"],
                "employee": c["employee"],
                "why": r[0],
                "status": c["status"],
                "modified_at": _iso(c["modified_at"]),
                **_flag_info(f),
            }
        )
    s = get_settings()
    return envelope(
        {
            "days": days,
            "items": items,
            "thresholds": {"open_hours": s.odd_check_open_hours, "large_refund": s.odd_check_large_refund},
        },
        loaded,
    )


def ack_flag(db: Session, flag_id: int, user: User) -> Optional[dict]:
    f = db.get(LossWatchFlag, flag_id)
    if f is None:
        return None
    if f.acked_at is None:
        f.acked_at = utcnow()
        f.acked_by_user_id = user.id
        f.acked_by_name = user.username
        db.commit()
    return {
        "id": f.id,
        "kind": f.kind,
        "date": f.business_date.isoformat(),
        "check_number": f.check_number,
        "reasons": [r for r in (f.reasons or "").split(",") if r],
        "acked": True,
        "acked_at": _iso(f.acked_at),
        "acked_by": f.acked_by_name,
    }


# ---------------------------------------------------------------------------
# 7. Menu performance, 8. Add-ons
# ---------------------------------------------------------------------------


def _menu_totals(loaded: Loaded, dates: Iterable[dt.date]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for d in dates:
        e = loaded.entries.get(d)
        if e is None:
            continue
        for r in e.rows:
            g = r.get("menuItemGuid")
            if not g:
                continue
            t = out.setdefault(g, {"qty": 0.0, "net": 0.0, "name": None, "last": None})
            q = data._num(r.get("quantitySold"))
            t["qty"] += q
            t["net"] += data._num(r.get("netSalesAmount"))
            t["name"] = r.get("menuItemName") or t["name"]
            if q > 0 and (t["last"] is None or d > t["last"]):
                t["last"] = d
    return out


def _in_group(guid: str, group: str, mm: Optional[dict]) -> bool:
    if group == "all":
        return True
    if not mm:
        return False
    info = (mm.get("items") or {}).get(guid)
    if info is None:
        return False
    if group == "kitchen":
        return bool(info.get("kitchen"))
    menus = {m.strip().lower() for m in info.get("menus") or [info.get("menu") or ""]}
    return not info.get("kitchen") and bool(menus & GROCERY_MENUS)


def _menu_map_or_fill(db: Session) -> Optional[dict]:
    mm = data.menu_map(db)
    if mm is None:
        data.request_fill([("menu_map", [])])
    return mm


def menu_performance(db: Session, days: int, group: str) -> dict:
    end = data.last_closed_date()
    cur_dates = data.expected_dates(end - dt.timedelta(days=days - 1), end)
    prev_dates = data.expected_dates(end - dt.timedelta(days=2 * days - 1), end - dt.timedelta(days=days))
    loaded = _load(db, "menu_item", sorted(set(cur_dates) | set(prev_dates)))
    mm = _menu_map_or_fill(db)
    cur = _menu_totals(loaded, cur_dates)
    prev = _menu_totals(loaded, prev_dates)
    items_map = (mm or {}).get("items") or {}

    def row(g: str) -> dict:
        c = cur.get(g) or {"qty": 0.0, "net": 0.0, "name": None, "last": None}
        p = prev.get(g) or {"qty": 0.0, "net": 0.0, "name": None, "last": None}
        info = items_map.get(g) or {}
        last = max([x for x in (c["last"], p["last"]) if x], default=None)
        return {
            "item_guid": g,
            "name": c["name"] or p["name"] or info.get("name"),
            "menu_group": info.get("group"),
            "qty": round(c["qty"], 2),
            "net_sales": r2(c["net"]),
            "prev_qty": round(p["qty"], 2),
            "change_pct": _pct(c["qty"], p["qty"]) if p["qty"] > 0 else None,
            "last_sold": last.isoformat() if last else None,
            "prev_net_sales": r2(p["net"]),
        }

    guids = [g for g in set(cur) | set(prev) if _in_group(g, group, mm)]
    rows = [row(g) for g in guids]
    sold = [r for r in rows if r["qty"] > 0]
    top = sorted(sold, key=lambda r: (-r["net_sales"], -r["qty"], r["name"] or ""))[:MENU_LIST_SIZE]
    bottom = sorted(sold, key=lambda r: (r["net_sales"], r["qty"], r["name"] or ""))[:MENU_LIST_SIZE]
    judged = [r for r in rows if r["prev_qty"] >= TREND_MIN_PREV_QTY and r["change_pct"] is not None]
    rising = sorted([r for r in judged if r["change_pct"] > 0], key=lambda r: (-r["change_pct"], r["name"] or ""))[:MENU_LIST_SIZE]
    falling = sorted([r for r in judged if r["change_pct"] < 0], key=lambda r: (r["change_pct"], r["name"] or ""))[:MENU_LIST_SIZE]
    not_sold = []
    if group in ("all", "kitchen") and mm:
        sold_now = {g for g, c in cur.items() if c["qty"] > 0}
        for g, info in items_map.items():
            if info.get("kitchen") and g not in sold_now:
                r = row(g)
                r["name"] = r["name"] or info.get("name")
                not_sold.append(r)
        not_sold.sort(key=lambda r: (r["last_sold"] or "", r["name"] or ""))
    return envelope(
        {
            "days": days,
            "group": group,
            "period": {"from": cur_dates[0].isoformat() if cur_dates else None, "to": end.isoformat()},
            "prev_period": {
                "from": prev_dates[0].isoformat() if prev_dates else None,
                "to": prev_dates[-1].isoformat() if prev_dates else None,
            },
            "top": top,
            "bottom": bottom,
            "rising": rising,
            "falling": falling,
            "not_sold": not_sold,
            "menu_map": mm is not None,
            "kitchen_rule": (mm or {}).get("kitchen_rule"),
        },
        loaded,
        extra_stale=(mm is None and group != "all"),
        extra_note=MENU_MAP_NOTE,
    )


def addons(db: Session, days: int) -> dict:
    end = data.last_closed_date()
    dates = data.expected_dates(end - dt.timedelta(days=days - 1), end)
    mods = _load(db, "menu_modifier", dates)
    items = _load(db, "menu_item", dates)
    mm = _menu_map_or_fill(db)
    item_qty = {g: t["qty"] for g, t in _menu_totals(items, dates).items()}
    mod_map = (mm or {}).get("modifiers") or {}
    by_name: Dict[str, List[str]] = defaultdict(list)
    for g, info in mod_map.items():
        by_name[(info.get("name") or "").strip().lower()].append(g)
    # Toast's MODIFIER report files some choices under ANOTHER modifier's
    # guid (one chosen on the same item) with their own name: real data, Oct
    # 10 - the guid of size "Regular" on rows "Regular" x5 and "Butter Masala
    # Sauce" x5, while our orders have that sauce right on the platters. So a
    # row is (guid, name): the option itself when the name is the guid's own,
    # else the option found by name (on that option's items when the name is
    # used more than once). Keyed by guid alone, those choices were added to
    # the other modifier under whichever name came first ("Chicken" 174 sold
    # vs 21 on our orders, "Butter Masala Sauce" 70 vs 184).
    agg: Dict[object, dict] = {}

    def add(key, guid, name, q, net):
        t = agg.setdefault(key, {"qty": 0.0, "net": 0.0, "name": name or None, "guid": guid})
        t["qty"] += q
        t["net"] += net
        t["name"] = t["name"] or name or None

    misfiled = []
    for d in dates:
        e = mods.entries.get(d)
        if e is None:
            continue
        for r in e.rows:
            g = r.get("modifierGuid")
            name = (r.get("modifierName") or "").strip()
            if not g and not name:
                continue
            q, net = data._num(r.get("quantitySold")), data._num(r.get("netSalesAmount"))
            info = mod_map.get(g) if g else None
            if info is not None and (not name or (info.get("name") or "").strip().lower() == name.lower()):
                add(g, g, name, q, net)
            else:
                misfiled.append((g, name, q, net))
    for g, name, q, net in misfiled:
        candidates = sorted(by_name.get(name.lower(), []))
        # The guid Toast filed it under is another choice on the same item:
        # keep the same-named options offered on that option's items.
        on_items = set(((mod_map.get(g) or {}).get("parents") or []) if g else [])
        narrowed = [c for c in candidates if on_items & set(mod_map[c].get("parents") or [])]
        if narrowed:
            candidates = narrowed
        groups = {((mod_map[c].get("groups") or [None])[0]) for c in candidates}
        real = None
        if len(candidates) == 1:
            real = candidates[0]
        elif candidates and len(groups) == 1:
            # Same name, same group name (one option per item's group):
            # counted with the one chosen most often under its own guid.
            real = max(candidates, key=lambda c: ((agg.get(c) or {}).get("qty", 0.0), c))
        if real is not None:
            add(real, real, name, q, net)
        else:
            add(("name", name.lower() or g), g, name, q, net)
    out = []
    for key, t in agg.items():
        info = (mod_map.get(t["guid"]) or {}) if key == t["guid"] else {}
        parents = info.get("parents") or []
        parent_qty = sum(item_qty.get(p, 0.0) for p in parents) if parents else 0.0
        out.append(
            {
                "modifier_guid": t["guid"],
                "name": t["name"] or info.get("name"),
                "group": (info.get("groups") or [None])[0],
                "qty": round(t["qty"], 2),
                "net_sales": r2(t["net"]),
                # Times chosen over the quantity sold of the items that offer
                # it. Above 1 means Toast's menu doesn't list every item that
                # offers it: unknown rather than a wrong number.
                "attach_rate": round(t["qty"] / parent_qty, 3) if parent_qty > 0 and t["qty"] <= parent_qty else None,
                "parent_qty": round(parent_qty, 2) if parents else None,
            }
        )
    out.sort(key=lambda m: (-m["qty"], -m["net_sales"], m["name"] or ""))
    return envelope({"days": days, "modifiers": out, "menu_map": mm is not None}, mods, items)


# ---------------------------------------------------------------------------
# 9. Prep forecast
# ---------------------------------------------------------------------------


def _percentile(values: List[float], q: float) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    k = (len(v) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def trimmed_mean(values: List[float]) -> float:
    """Drop the single highest and lowest when there are 5 or more."""
    v = sorted(values)
    if len(v) >= 5:
        v = v[1:-1]
    return sum(v) / len(v) if v else 0.0


def _fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def prep_forecast(db: Session, day: Optional[dt.date] = None) -> dict:
    today_bd = data.today_business_date()
    day = day or today_bd + dt.timedelta(days=1)
    last_closed = today_bd - dt.timedelta(days=1)
    samples = [day - dt.timedelta(days=7 * k) for k in range(1, PREP_WEEKS + 1)]
    samples = [d for d in samples if FIRST_REAL_SALES_DATE <= d <= last_closed]
    anchor = min(day, today_bd)
    recent = data.expected_dates(anchor - dt.timedelta(days=PREP_TREND_DAYS), anchor - dt.timedelta(days=1))
    older = data.expected_dates(
        anchor - dt.timedelta(days=2 * PREP_TREND_DAYS), anchor - dt.timedelta(days=PREP_TREND_DAYS + 1)
    )
    loaded = _load(db, "menu_item", sorted(set(samples) | set(recent) | set(older)))
    hourly = _load(db, "metrics_hour", samples)
    mm = _menu_map_or_fill(db)
    items_map = (mm or {}).get("items") or {}
    used = [d for d in samples if d in loaded.entries and loaded.entries[d].rows]
    per_item: Dict[str, Dict[dt.date, float]] = defaultdict(dict)
    names: Dict[str, str] = {}
    for d in used:
        for r in loaded.entries[d].rows:
            g = r.get("menuItemGuid")
            if not g or not (items_map.get(g) or {}).get("kitchen"):
                continue
            per_item[g][d] = per_item[g].get(d, 0.0) + data._num(r.get("quantitySold"))
            # `used` runs newest first: keep the newest name Toast reported
            # (an item renamed since - "Veg Samosa Each" -> "Veg Samosa - 1pc"
            # - showed its old name).
            names[g] = names.get(g) or r.get("menuItemName")
    rec = _menu_totals(loaded, recent)
    old = _menu_totals(loaded, older)

    shares = None
    hour_orders = defaultdict(float)
    for d in samples:
        e = hourly.entries.get(d)
        if e is None:
            continue
        for r in e.rows:
            h = _hour(r)
            if h is not None:
                hour_orders[h] += data._num(r.get("ordersCount"))
    total_orders = sum(hour_orders.values())
    if total_orders > 0:
        shares = {h: v / total_orders for h, v in sorted(hour_orders.items()) if v > 0}

    items = []
    for g, by_day in per_item.items():
        values = [by_day.get(d, 0.0) for d in used]
        base = trimmed_mean(values)
        r_qty = (rec.get(g) or {}).get("qty", 0.0)
        o_qty = (old.get(g) or {}).get("qty", 0.0)
        factor = 1.0
        if o_qty >= 5:
            factor = 1 + (r_qty / o_qty - 1) * PREP_TREND_DAMPENING
            factor = min(PREP_TREND_MAX, max(PREP_TREND_MIN, factor))
        expected = base * factor
        if expected < PREP_MIN_EXPECTED:
            continue
        info = items_map.get(g) or {}
        items.append(
            {
                "item_guid": g,
                # The menu's current name first: what the kitchen sees on the POS.
                "name": info.get("name") or names.get(g),
                "menu_group": info.get("group"),
                "expected_qty": round(expected, 1),
                "low": round(_percentile(values, 0.25) * factor, 1),
                "high": round(_percentile(values, 0.75) * factor, 1),
                "by_hour": (
                    [{"hour": h, "qty": round(expected * sh, 1)} for h, sh in shares.items() if round(expected * sh, 1) > 0]
                    if shares
                    else None
                ),
                "trend_factor": round(factor, 2),
                "samples": [round(v, 2) for v in values],
            }
        )
    items.sort(key=lambda i: (-i["expected_qty"], i["name"] or ""))
    weekday = LONG_DAY_NAMES[day.weekday()]
    basis = (
        f"Based on the last {len(used)} {weekday}s ({_fmt_day(min(used))} to {_fmt_day(max(used))}): "
        "the average with the highest and lowest day left out, adjusted for the last 4 weeks' trend."
        if used
        else f"No {weekday}s in the sales history yet."
    )
    return envelope(
        {
            "date": day.isoformat(),
            "weekday": weekday,
            "basis": basis,
            "sample_dates": [d.isoformat() for d in used],
            "by_hour_source": "store_hourly_share" if shares else None,
            "items": items,
            "menu_map": mm is not None,
        },
        loaded,
        hourly,
        extra_stale=mm is None,
        extra_note=MENU_MAP_NOTE,
    )


# ---------------------------------------------------------------------------
# 10. Returning customers (counts only)
# ---------------------------------------------------------------------------


def returning_customers(db: Session, weeks: int) -> dict:
    end = data.last_closed_date()
    starts = _week_starts(end, weeks)
    window_start = starts[0]
    dates = data.expected_dates(window_start, end)
    loaded = _load(db, "guest", dates)
    first_seen: Dict[str, dt.date] = {}
    for h, first in db.execute(
        select(GuestVisit.card_hash, func.min(GuestVisit.visit_date)).group_by(GuestVisit.card_hash)
    ):
        if isinstance(first, str):
            first = dt.date.fromisoformat(first)
        first_seen[h] = first
    visits = db.execute(
        select(GuestVisit.visit_date, GuestVisit.card_hash).where(
            GuestVisit.visit_date >= window_start, GuestVisit.visit_date <= end
        )
    ).all()
    history_from = db.execute(select(func.min(GuestVisit.visit_date))).scalar()
    per_week: Dict[dt.date, List[Tuple[dt.date, str]]] = defaultdict(list)
    for d, h in visits:
        per_week[_monday(d)].append((d, h))

    def stats(rows: List[Tuple[dt.date, str]], since: dt.date) -> dict:
        customers = {h for _, h in rows}
        days_per = defaultdict(set)
        for d, h in rows:
            days_per[h].add(d)
        returning = {h for h in customers if first_seen.get(h, since) < since or len(days_per[h]) >= 2}
        n = len(customers)
        return {
            "card_customers": n,
            "returning": len(returning),
            "returning_rate": round(len(returning) / n, 4) if n else None,
            "avg_visits": round(sum(len(v) for v in days_per.values()) / n, 2) if n else None,
        }

    out_weeks = []
    for ws in starts:
        rows = per_week.get(ws, [])
        w = stats(rows, ws)
        w.update(
            {
                "week_start": ws.isoformat(),
                "days": sum(1 for d in loaded.entries if _monday(d) == ws),
                "partial": ws + dt.timedelta(days=6) > end,
            }
        )
        out_weeks.append({k: w[k] for k in ("week_start", "card_customers", "returning", "returning_rate", "avg_visits", "days", "partial")})
    overall = stats([(d, h) for d, h in visits], window_start)
    overall.update({"from": window_start.isoformat(), "to": end.isoformat()})
    if isinstance(history_from, str):
        history_from = dt.date.fromisoformat(history_from)
    return envelope(
        {
            "weeks": out_weeks,
            "overall": overall,
            "history_from": history_from.isoformat() if history_from else None,
            "retention_days": data.GUEST_RETENTION_DAYS,
        },
        loaded,
    )


# ---------------------------------------------------------------------------
# Alerts (services/notification_triggers.py)
# ---------------------------------------------------------------------------

REASON_WORDS = {
    "full_comp": ("full comp", "full comps"),
    "large_discount": ("large discount", "large discounts"),
    "large_refund": ("large refund", "large refunds"),
    "large_void": ("large void", "large voids"),
    "above_employee_median": ("unusual for the employee", "unusual for the employee"),
    "open_long": ("check open too long", "checks open too long"),
    "modified_after_paid": ("changed after the day closed", "changed after the day closed"),
}


def pending_alert_flags(db: Session, today: dt.date) -> List[LossWatchFlag]:
    since = today - dt.timedelta(days=FLAG_LOOKBACK_DAYS)
    flags = db.execute(
        select(LossWatchFlag)
        .where(LossWatchFlag.business_date >= since, LossWatchFlag.acked_at.is_(None))
        .order_by(LossWatchFlag.business_date, LossWatchFlag.id)
    ).scalars().all()
    if not flags:
        return []
    sent = set(
        db.execute(
            select(NotificationLog.dedupe_key).where(
                NotificationLog.dedupe_key.in_([alert_dedupe_key(f.id) for f in flags])
            )
        ).scalars()
    )
    return [f for f in flags if alert_dedupe_key(f.id) not in sent]


def loss_watch_alert_text(flags: List[LossWatchFlag]) -> Tuple[str, str]:
    """One line per push: each check counted once, under its most serious
    reason (a check can be both a Loss watch and an Odd checks flag)."""
    order = LOSS_REASONS + tuple(r for r in ODD_REASONS if r not in LOSS_REASONS)
    per_check: Dict[str, set] = defaultdict(set)
    for f in flags:
        per_check[f.check_guid or f.flag_key].update(r for r in (f.reasons or "").split(",") if r)
    counts: Dict[str, int] = defaultdict(int)
    for reasons in per_check.values():
        first = next((r for r in order if r in reasons), None)
        if first:
            counts[first] += 1
    n = len(per_check)
    title = f"Loss watch: {n} check{'s' if n != 1 else ''} to review"
    parts = []
    for key in order:
        if counts.get(key):
            one, many = REASON_WORDS.get(key, (key, key))
            parts.append(f"{counts[key]} {one if counts[key] == 1 else many}")
    names = list(dict.fromkeys(f.employee for f in flags if f.employee and f.employee != "Unknown"))
    body = ", ".join(parts)
    if names:
        shown = ", ".join(names[:ALERT_NAMES_SHOWN])
        if len(names) > ALERT_NAMES_SHOWN:
            shown += f" + {len(names) - ALERT_NAMES_SHOWN} more"
        body += f" ({shown})"
    return title, body
