"""Insights data layer (docs/INSIGHTS_CONTRACT.md): Toast Analytics reports,
cached per business date in `analytics_cache`, plus the nightly fill.

Everything goes through the existing client (`toast_analytics.run_report` -
its own token, rate budget and 429 handling); this module only decides WHAT
to ask for and keeps the answers.

Reports kept (REPORTS below), one cache row per business date:
- metrics_day     metrics, one row per date        (week calls, 1 per 7 days)
- metrics_dining  metrics grouped by dining option (week calls)
- metrics_hour    metrics aggregateBy=HOUR         (1 call per date; /day only)
- menu_item       menu grouped by MENU_ITEM        (week calls)
- menu_modifier   menu grouped by MODIFIER         (week calls)
- check           one row per check                (1 call per date; 60/day cap)
- guest           guest payments -> guest_visits   (week calls; 60/day cap)
  (card fingerprints are HMAC'd on arrival and nothing else from the row is
  kept - see store_guest_rows)
plus `menu_map` (no date): which menu / group each item is in and which items
are Street Kitchen, from the Menus API (`/menus/v2/menus`).

Freshness:
- A past date is fetched once and reused. A copy fetched before that date's
  04:00 closeout is "provisional" and fetched again.
- The nightly job re-pulls the week reports for the last REPULL_DAYS (14)
  days (Toast edits land up to 6 days later), and re-pulls an hourly or check
  report only when that date's day metrics changed since it was fetched.
- Today is fetched live (live_today_metrics) with a short cache.

Rate limits: day/week reports 10/min and 60/h per endpoint (the client waits
for its own budget); check and guest payment reports 60 a DAY, shared with
anything else using the credentials - our share is INSIGHTS_CHECK_DAILY_BUDGET
/ INSIGHTS_GUEST_DAILY_BUDGET, counted in `analytics_call_log` so a restart
can't reset it. Request paths never queue behind Toast: they read the cache
and, in production (SCHEDULER_ENABLED), start one background fill.

Budget order (fill_reserved): the last two closed dates first; older dates
only above DAILY_RESERVE. A date refused for budget is picked up by the
hourly catch-up (run_catchup) once calls free up - it calls Toast only when
something is missing. A date Toast can't process gets an "unavailable"
marker, retried once a night instead of on every page open.
"""

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import pathlib
import secrets
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.insights_models import AnalyticsCache, AnalyticsCallLog, GuestVisit
from app.models import JobRun
from app.services import toast_analytics
from app.services.sales_accuracy import FIRST_REAL_SALES_DATE, closed_business_today
from app.timeutil import restaurant_timezone, utcnow

logger = logging.getLogger(__name__)

SOURCE = "toast_analytics"
CACHE_VERSION = 1
NIGHTLY_JOB = "insights_nightly"
CATCHUP_JOB = "insights_catchup"
CLOSEOUT_HOUR = 4

# How far back each report is kept filled (days, ending yesterday).
REPULL_DAYS = 14
CHECK_REPULL_DAYS = 7
HISTORY_DAYS = {
    "metrics_day": 91,  # 13 weeks: check trends (12) + compare's 4-week average
    "metrics_dining": 84,  # channels: up to 12 weeks (the week so far included)
    "metrics_hour": 84,  # heatmap: up to 12 weeks
    "menu_item": 112,  # menu performance: 56 days + the 56 before
    "menu_modifier": 56,
    "check": 30,  # loss watch: 30 days
    "guest": 84,  # returning customers: 8 weeks + 4 weeks of history
}
# Calls one nightly run may spend per report (the client also waits for
# Toast's per-minute/per-hour limits on its own).
NIGHTLY_MAX_CALLS = {
    "metrics_day": 16,
    "metrics_dining": 12,
    "metrics_hour": 40,
    "menu_item": 20,
    "menu_modifier": 10,
}
BACKGROUND_MAX_CALLS = 12
# The check / guest payment reports have a daily cap (daily_budget). The last
# PRIORITY_DAYS closed dates always come first; older dates (history) may
# only spend what's above DAILY_RESERVE, so a big backfill - or a fill run
# before the 04:00 closeout - can't leave the newest day waiting 24 hours.
PRIORITY_DAYS = 2
DAILY_RESERVE = {"check": 4, "guest": 2}
# The catch-up (run_catchup): hourly at :20 from 05:20 to 23:20 store time.
# It looks at the last PRIORITY_DAYS closed dates in the cache and only calls
# Toast for what's missing while the budget has room.
CATCHUP_HOURS = tuple(range(5, 24))
CATCHUP_MINUTE = 20
# fill()'s error when it had dates to fetch and no calls to spend.
NO_CALLS_LEFT = "no calls left"

GUEST_RETENTION_DAYS = 400
CHECK_RETENTION_DAYS = 120
CACHE_RETENTION_DAYS = 400
CALL_LOG_RETENTION_DAYS = 7

METRIC_KEEP = (
    "netSalesAmount",
    "grossSalesAmount",
    "discountAmount",
    "voidOrdersAmount",
    "refundAmount",
    "ordersCount",
    "guestCount",
    "voidOrdersCount",
    "discountOrderCount",
)
CHECK_KEEP = (
    "orderNumber",
    "orderGuid",
    "checkNumber",
    "checkGuid",
    "orderOpenedDate",
    "checkPaidDateTime",
    "checkModifiedDateTime",
    "checkStatus",
    "checkVoidedStatus",
    "diningOption",
    "revenueCenter",
    "serverName",
    "checkTotalAmount",
    "checkDiscountAmount",
    "checkTaxAmount",
    "checkTipAmount",
    "checkGratuityAmount",
    "checkRefundAmount",
)
# Guest payment fields that identify a card or a person: never stored,
# returned or logged. Only the fingerprint is used, and only as an HMAC.
CARD_FIELDS = ("cardFingerprint", "cardLast4Digits", "cardType", "paymentAccountReference", "cardHolderName")

REPORTS: Dict[str, dict] = {
    "metrics_day": {"kind": "metrics", "per": "week", "group_by": None, "keep": METRIC_KEEP},
    "metrics_dining": {
        "kind": "metrics",
        "per": "week",
        "group_by": ["DINING_OPTION"],
        "keep": METRIC_KEEP + ("diningOption",),
    },
    "metrics_hour": {
        "kind": "metrics",
        "per": "day",
        "params": {"aggregateBy": "HOUR"},
        "keep": METRIC_KEEP + ("businessHour",),
    },
    "menu_item": {
        "kind": "menu",
        "per": "week",
        "group_by": ["MENU_ITEM"],
        "keep": ("menuItemGuid", "menuItemName", "quantitySold", "netSalesAmount", "grossSalesAmount"),
    },
    "menu_modifier": {
        "kind": "menu",
        "per": "week",
        "group_by": ["MODIFIER"],
        "keep": ("modifierGuid", "modifierName", "quantitySold", "netSalesAmount"),
    },
    "check": {"kind": "check", "per": "day", "keep": CHECK_KEEP, "daily_family": "check"},
    "guest": {"kind": "guest/payments", "per": "week", "daily_family": "guest"},
}

# Seams for tests: never call Toast or sleep for real in unit tests.
_run_report: Callable = toast_analytics.run_report
_spawn: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(
    target=fn, name="insights-fill", daemon=True
).start()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def r2(v) -> float:
    return round(_num(v) + 0.0, 2)


def parse_date(raw) -> Optional[dt.date]:
    return toast_analytics._parse_business_date(raw)


def daterange(start: dt.date, end: dt.date) -> List[dt.date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def today_business_date(now: Optional[dt.datetime] = None) -> dt.date:
    """The business date that is open right now (yesterday's date before the
    04:00 closeout)."""
    return closed_business_today(now)


def last_closed_date(now: Optional[dt.datetime] = None) -> dt.date:
    return today_business_date(now) - dt.timedelta(days=1)


def closeout_at(day: dt.date) -> dt.datetime:
    """When business date `day` is closed: 04:00 store time the next day."""
    tz = restaurant_timezone()
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(CLOSEOUT_HOUR), tz).astimezone(dt.timezone.utc)


def is_configured() -> bool:
    return toast_analytics.is_configured()


def fingerprint(row: Optional[dict]) -> Optional[str]:
    """What "this date's numbers changed" is judged on (the day metrics)."""
    if not row:
        return None
    return json.dumps(
        [
            r2(row.get("netSalesAmount")),
            r2(row.get("discountAmount")),
            r2(row.get("voidOrdersAmount")),
            r2(row.get("refundAmount")),
            int(_num(row.get("ordersCount"))),
        ]
    )


def sum_rows(rows: Iterable[dict], fields: Iterable[str] = METRIC_KEEP) -> dict:
    fields = tuple(fields)
    out = {f: 0.0 for f in fields}
    for r in rows or []:
        for f in fields:
            out[f] += _num(r.get(f))
    return {f: round(v, 4) for f, v in out.items()}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class Entry:
    __slots__ = ("day", "rows", "meta", "fetched_at")

    def __init__(self, day, rows, meta, fetched_at):
        self.day = day
        self.rows = rows
        self.meta = meta
        self.fetched_at = fetched_at

    @property
    def provisional(self) -> bool:
        if self.day is None or self.fetched_at is None or self.unavailable:
            return False
        return self.fetched_at < closeout_at(self.day)

    @property
    def unavailable(self) -> bool:
        """A marker, not data: Toast couldn't process this date's report
        (mark_unavailable). Retried once a night by run_nightly."""
        return bool(self.meta.get("unavailable"))


def params_hash(report: str, day: Optional[dt.date], extra: str = "") -> str:
    raw = f"{report}|v{CACHE_VERSION}|{day.isoformat() if day else '-'}|{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def store(db: Session, report: str, day: Optional[dt.date], rows: list, meta: Optional[dict] = None, extra: str = "") -> None:
    payload = dict(meta or {})
    payload["rows"] = rows
    now = utcnow()
    stmt = sqlite_insert(AnalyticsCache).values(
        report=report,
        params_hash=params_hash(report, day, extra),
        business_date=day,
        fetched_at=now,
        payload_json=json.dumps(payload, separators=(",", ":"), default=str),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["report", "params_hash"],
        set_={"fetched_at": now, "payload_json": stmt.excluded.payload_json, "business_date": day},
    )
    db.execute(stmt)


def _entry(row: AnalyticsCache) -> Entry:
    try:
        payload = json.loads(row.payload_json or "{}")
    except ValueError:
        payload = {}
    rows = payload.pop("rows", []) if isinstance(payload, dict) else []
    fetched = row.fetched_at
    if fetched is not None and fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=dt.timezone.utc)
    return Entry(row.business_date, rows or [], payload if isinstance(payload, dict) else {}, fetched)


def cached(db: Session, report: str, dates: Iterable[dt.date], unavailable: bool = False) -> Dict[dt.date, Entry]:
    """Cache entries by date. "Unavailable" markers are left out unless
    `unavailable` (they hold no numbers - a page must not read them as a
    day with zero sales)."""
    dates = sorted(set(dates))
    if not dates:
        return {}
    out: Dict[dt.date, Entry] = {}
    hashes = {params_hash(report, d): d for d in dates}
    keys = list(hashes)
    for i in range(0, len(keys), 400):
        rows = db.execute(
            select(AnalyticsCache).where(AnalyticsCache.report == report, AnalyticsCache.params_hash.in_(keys[i : i + 400]))
        ).scalars()
        for row in rows:
            d = hashes.get(row.params_hash)
            if d is not None:
                e = _entry(row)
                if unavailable or not e.unavailable:
                    out[d] = e
    return out


def cached_one(db: Session, report: str, day: Optional[dt.date] = None, extra: str = "") -> Optional[Entry]:
    row = db.execute(
        select(AnalyticsCache).where(
            AnalyticsCache.report == report, AnalyticsCache.params_hash == params_hash(report, day, extra)
        )
    ).scalars().first()
    return _entry(row) if row is not None else None


def expected_dates(start: dt.date, end: dt.date) -> List[dt.date]:
    """Business dates worth asking about: real sales began FIRST_REAL_SALES_DATE."""
    return daterange(max(start, FIRST_REAL_SALES_DATE), end)


def needs_fetch(entry: Optional[Entry], day: dt.date, today_bd: dt.date) -> bool:
    if entry is None:
        return True
    if day >= today_bd:
        age = (utcnow() - entry.fetched_at).total_seconds() if entry.fetched_at else 1e9
        return age > get_settings().insights_today_cache_seconds
    return entry.provisional


# ---------------------------------------------------------------------------
# Daily-capped budget (check, guest payments)
# ---------------------------------------------------------------------------


def daily_budget(family: str) -> int:
    s = get_settings()
    return int(s.insights_check_daily_budget if family == "check" else s.insights_guest_daily_budget)


def daily_used(db: Session, family: str) -> int:
    since = utcnow() - dt.timedelta(hours=24)
    return int(
        db.execute(
            select(func.count(AnalyticsCallLog.id)).where(
                AnalyticsCallLog.family == family, AnalyticsCallLog.called_at >= since
            )
        ).scalar()
        or 0
    )


def daily_left(db: Session, family: str) -> int:
    return max(0, daily_budget(family) - daily_used(db, family))


def daily_reserve(family: str) -> int:
    return max(0, int(DAILY_RESERVE.get(family, 0)))


def budget_frees_at(db: Session, family: str, need: int = 1) -> Optional[dt.datetime]:
    """When at least `need` calls of `family` are free again (the rolling 24 h
    window lets the oldest calls go), or None when they already are."""
    since = utcnow() - dt.timedelta(hours=24)
    times = list(
        db.execute(
            select(AnalyticsCallLog.called_at)
            .where(AnalyticsCallLog.family == family, AnalyticsCallLog.called_at >= since)
            .order_by(AnalyticsCallLog.called_at)
        ).scalars()
    )
    extra = need - (daily_budget(family) - len(times))
    if extra <= 0:
        return None
    if extra > len(times):
        return None  # more than the whole budget: never
    t = times[extra - 1]
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t + dt.timedelta(hours=24)


def next_fill_at(when: dt.datetime) -> dt.datetime:
    """The first scheduled fill at or after `when` (the 04:30 nightly or an
    hourly catch-up), in UTC - when a day waiting for budget gets read."""
    tz = restaurant_timezone()
    s = get_settings()
    local = when.astimezone(tz)
    slots = []
    for k in range(3):
        day = local.date() + dt.timedelta(days=k)
        slots.append(dt.datetime.combine(day, dt.time(s.insights_nightly_hour, s.insights_nightly_minute), tz))
        slots.extend(dt.datetime.combine(day, dt.time(h, CATCHUP_MINUTE), tz) for h in CATCHUP_HOURS)
    return min(t for t in slots if t >= local).astimezone(dt.timezone.utc)


class BudgetExhausted(toast_analytics.AnalyticsRateLimited):
    pass


def _spend(db: Session, family: str, day: Optional[dt.date]) -> AnalyticsCallLog:
    if daily_left(db, family) <= 0:
        raise BudgetExhausted(f"Insights daily budget for {family} reports is used up ({daily_budget(family)} a day)")
    row = AnalyticsCallLog(family=family, business_date=day, called_at=utcnow(), ok=0)
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _trim(rows: list, keep: Iterable[str]) -> list:
    keep = tuple(keep)
    return [{k: r.get(k) for k in keep if k in r} for r in rows or [] if isinstance(r, dict)]


def _row_date(report: str, row: dict) -> Optional[dt.date]:
    if report == "guest":
        return parse_date(row.get("paymentDate"))
    if report == "check":
        return parse_date(row.get("orderOpenedDate"))
    return parse_date(row.get("businessDate"))


def _chunks(dates: List[dt.date]) -> List[Tuple[dt.date, dt.date]]:
    """Sorted dates -> runs of consecutive dates, each at most 7 long (one
    `week` report each). Newest run first."""
    runs: List[Tuple[dt.date, dt.date]] = []
    for d in sorted(set(dates)):
        if runs and runs[-1][1] + dt.timedelta(days=1) == d and (d - runs[-1][0]).days < 7:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return list(reversed(runs))


def _body(report: str, start: dt.date, end: dt.date) -> dict:
    spec = REPORTS[report]
    if report == "guest":
        return {
            "startDate": toast_analytics._ymd(start),
            "endDate": toast_analytics._ymd(end),
            "restaurantIds": [get_settings().toast_restaurant_guid],
            "excludedRestaurantIds": [],
        }
    return toast_analytics._body(start, end, spec.get("group_by"))


def _call(db: Session, report: str, start: dt.date, end: dt.date, max_budget_wait: float, max_wait: float) -> list:
    spec = REPORTS[report]
    family = spec.get("daily_family")
    logs: List[AnalyticsCallLog] = []
    kw = {}
    if family:
        # Every POST counts against Toast's 60 a day, retries and new requests
        # after a 409 included (run_report may make several for one report),
        # so each one is logged - and refused once the budget is spent.
        def before_post(attempt: int) -> None:
            logs.append(_spend(db, family, start))

        kw["before_post"] = before_post
    rows = _run_report(
        spec["kind"],
        "day" if start == end else "week",
        _body(report, start, end),
        spec.get("params"),
        max_wait=max_wait,
        max_budget_wait=max_budget_wait,
        **kw,
    )
    if logs:
        logs[-1].ok = 1
        db.commit()
    return rows if isinstance(rows, list) else []


def fetch_range(
    db: Session,
    report: str,
    start: dt.date,
    end: dt.date,
    max_budget_wait: float = 120.0,
    max_wait: float = 300.0,
    day_fingerprints: Optional[Dict[dt.date, Optional[str]]] = None,
) -> int:
    """One Toast call for [start, end] (one date for the per-day reports),
    stored per date. Returns the number of dates stored."""
    raw = _call(db, report, start, end, max_budget_wait, max_wait)
    dates = daterange(start, end)
    if report == "guest":
        n = store_guest_rows(db, raw, dates)
        db.commit()
        return n
    by_date: Dict[dt.date, list] = {d: [] for d in dates}
    keep = REPORTS[report]["keep"]
    for row in raw:
        d = _row_date(report, row) if report != "check" else start
        if d in by_date:
            by_date[d].append(row)
    for d, rows in by_date.items():
        meta = {}
        trimmed = _trim(rows, keep)
        if report == "metrics_hour":
            meta["fp"] = fingerprint(sum_rows(trimmed))
        elif report == "check":
            meta["fp"] = (day_fingerprints or {}).get(d)
        store(db, report, d, trimmed, meta)
    db.commit()
    return len(by_date)


def dates_to_fetch(
    db: Session,
    report: str,
    dates: List[dt.date],
    today_bd: dt.date,
    refresh: Iterable[dt.date] = (),
    retry_unavailable: bool = False,
) -> List[dt.date]:
    """Dates to ask Toast for (database only). A date marked unavailable is
    skipped unless `retry_unavailable` (the nightly's one retry a night)."""
    have = cached(db, report, dates, unavailable=True)
    refresh = set(refresh)
    out = []
    for d in dates:
        e = have.get(d)
        if e is not None and e.unavailable:
            if retry_unavailable:
                out.append(d)
        elif d in refresh or needs_fetch(e, d, today_bd):
            out.append(d)
    return out


def mark_unavailable(db: Session, report: str, dates: Iterable[dt.date], error: str) -> int:
    """Toast couldn't process these dates' report (it kept answering 404/409
    for new requests): store a dated marker so page opens stop spending the
    budget on them; the nightly retries them once a night."""
    n = 0
    for d in dates:
        store(db, report, d, [], {"unavailable": True, "error": str(error)[:200]})
        n += 1
    db.commit()
    return n


def fill(
    db: Session,
    report: str,
    dates: List[dt.date],
    *,
    max_calls: int,
    today_bd: Optional[dt.date] = None,
    refresh: Iterable[dt.date] = (),
    max_budget_wait: float = 120.0,
    day_fingerprints: Optional[Dict[dt.date, Optional[str]]] = None,
    retry_unavailable: bool = False,
) -> dict:
    """Fetch every date in `dates` that is missing / provisional (or listed
    in `refresh`), newest first, spending at most `max_calls` Toast calls.
    Never raises; returns {calls, dates, error, pending} (+ `budget` when the
    error is only the daily budget, `unavailable` when dates were marked)."""
    today_bd = today_bd or today_business_date()
    dates = [d for d in dates if d < today_bd]  # today is live_fetch's job
    todo = dates_to_fetch(db, report, dates, today_bd, refresh, retry_unavailable)
    out = {"calls": 0, "dates": 0, "error": None, "pending": len(todo)}
    if not todo:
        return out
    if max_calls <= 0:
        # Dates to fetch and nothing to spend: say so (a zero-call "success"
        # hid a day waiting 24 hours for the budget).
        out["error"] = NO_CALLS_LEFT
        out["budget"] = True
        return out
    if REPORTS[report]["per"] == "day":
        chunks = [(d, d) for d in sorted(todo, reverse=True)]
    else:
        chunks = _chunks(todo)
    for start, end in chunks:
        if out["calls"] >= max_calls:
            break
        try:
            out["dates"] += fetch_range(
                db, report, start, end, max_budget_wait=max_budget_wait, day_fingerprints=day_fingerprints
            )
            out["calls"] += 1
        except toast_analytics.AnalyticsError as exc:
            db.rollback()
            out["error"] = str(exc)[:300]
            if isinstance(exc, BudgetExhausted):
                out["budget"] = True
            # Rate limited / out of budget / not configured: stop this report.
            if isinstance(exc, (toast_analytics.AnalyticsRateLimited, toast_analytics.AnalyticsNotConfigured)):
                out["stopped"] = True
                break
            out["calls"] += 1
            if isinstance(exc, toast_analytics.AnalyticsReportUnprocessable):
                failed = [d for d in daterange(start, end) if d in set(todo)]
                out["unavailable"] = out.get("unavailable", 0) + mark_unavailable(db, report, failed, out["error"])
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            db.rollback()
            logger.exception("Insights: fetching %s %s..%s failed", report, start, end)
            out["error"] = str(exc)[:300]
            out["stopped"] = True
            break
    out["pending"] = len(dates_to_fetch(db, report, dates, today_bd)) if out["calls"] else out["pending"]
    return out


def fill_reserved(
    db: Session,
    report: str,
    dates: List[dt.date],
    *,
    max_calls: int,
    today_bd: Optional[dt.date] = None,
    **kw,
) -> dict:
    """fill(), with the daily budget split for the check / guest reports:
    the last PRIORITY_DAYS closed dates first (any call left), then older
    dates only with what's above DAILY_RESERVE. Other reports: plain fill()."""
    family = REPORTS[report].get("daily_family")
    if not family:
        return fill(db, report, dates, max_calls=max_calls, today_bd=today_bd, **kw)
    today_bd = today_bd or today_business_date()
    newest = today_bd - dt.timedelta(days=PRIORITY_DAYS)
    recent = [d for d in dates if newest <= d < today_bd]
    older = [d for d in dates if d < newest]
    out = fill(db, report, recent, max_calls=min(max_calls, daily_left(db, family)), today_bd=today_bd, **kw)
    if not older or out.get("stopped"):
        return out
    spare = min(max_calls - out["calls"], daily_left(db, family) - daily_reserve(family))
    if spare <= 0:
        waiting = len(
            dates_to_fetch(db, report, older, today_bd, kw.get("refresh", ()), kw.get("retry_unavailable", False))
        )
        if waiting:
            out["pending"] += waiting
            out["held_for_reserve"] = waiting
        return out
    rest = fill(db, report, older, max_calls=spare, today_bd=today_bd, **kw)
    out["calls"] += rest["calls"]
    out["dates"] += rest["dates"]
    out["pending"] += rest["pending"]
    out["error"] = out["error"] or rest["error"]
    for k in ("budget", "stopped"):
        if rest.get(k):
            out[k] = True
    if rest.get("unavailable"):
        out["unavailable"] = out.get("unavailable", 0) + rest["unavailable"]
    return out


# ---------------------------------------------------------------------------
# Returning customers: HMAC'd card fingerprints only
# ---------------------------------------------------------------------------

_key_lock = threading.Lock()
_key_cache: Dict[str, bytes] = {}


def _hmac_key() -> bytes:
    s = get_settings()
    if s.insights_hmac_secret:
        return s.insights_hmac_secret.encode()
    path = pathlib.Path(s.insights_hmac_key_file).expanduser()
    cache_key = str(path.resolve())
    with _key_lock:
        if cache_key in _key_cache:
            return _key_cache[cache_key]
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(secrets.token_hex(32))
            except FileExistsError:
                pass
        key = path.read_text().strip().encode()
        if len(key) < 32:
            raise RuntimeError("INSIGHTS_HMAC_KEY_FILE is too short to be a secret")
        _key_cache[cache_key] = key
        return key


def card_hash(fingerprint_value: str) -> str:
    return hmac.new(_hmac_key(), ("card:" + fingerprint_value).encode(), hashlib.sha256).hexdigest()[:40]


def store_guest_rows(db: Session, raw: list, dates: List[dt.date]) -> int:
    """Guest payment rows -> guest_visits (date, HMAC) + one cache marker per
    date with counts only. The raw rows (card fingerprint, last 4, type, PAR,
    name) are dropped here and never stored or logged."""
    per_day: Dict[dt.date, Dict[str, int]] = {d: {} for d in dates}
    payments = {d: 0 for d in dates}
    card_payments = {d: 0 for d in dates}
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        d = parse_date(row.get("paymentDate"))
        if d not in per_day:
            continue
        payments[d] += 1
        fp = row.get("cardFingerprint") or row.get("paymentAccountReference")
        if not fp:
            continue
        card_payments[d] += 1
        h = card_hash(str(fp))
        per_day[d][h] = per_day[d].get(h, 0) + 1
    db.execute(delete(GuestVisit).where(GuestVisit.visit_date.in_(dates)))
    batch = [
        {"visit_date": d, "card_hash": h, "payments": n} for d, cards in per_day.items() for h, n in cards.items()
    ]
    for i in range(0, len(batch), 500):
        db.execute(sqlite_insert(GuestVisit).values(batch[i : i + 500]))
    for d in dates:
        store(
            db,
            "guest",
            d,
            [],
            {"payments": payments[d], "card_payments": card_payments[d], "cards": len(per_day[d])},
        )
    return len(dates)


# ---------------------------------------------------------------------------
# Menu map (Menus API): menu / group per item, Street Kitchen items, modifiers
# ---------------------------------------------------------------------------

KITCHEN_MENU_PREFIX = "street kitchen"
SKIP_MENUS = {"toast e-commerce autogenerated - do not edit", "test menu", "container"}
MENU_MAP_KEY = "menu_map"


def is_kitchen_path(menu: str, group: str) -> bool:
    """Street Kitchen = any group of a menu named "STREET KITCHEN..." (the
    store's two kitchen menus, the main one and the "(GH)" copy), except its
    bottled / canned drinks groups ("Drinks - Soda / Sparkling", "Drinks -
    Energy", "Drinks - Coconut Water", "Drinks - Water"), which are retail
    stock. House drinks (lassi, chai) are made in the kitchen and count."""
    m = (menu or "").strip().lower()
    g = (group or "").strip().lower()
    if not m.startswith(KITCHEN_MENU_PREFIX):
        return False
    return not (g.startswith("drinks - ") and "house" not in g)


def build_menu_map(menus: dict) -> dict:
    data = menus.get("data") if isinstance(menus, dict) and "menus" not in menus else menus
    data = data or {}
    items: Dict[str, dict] = {}
    item_groups: Dict[str, set] = {}

    def walk(groups, menu_name, top):
        for g in groups or []:
            gname = (g.get("name") or "").strip()
            top_name = top or gname
            for it in g.get("menuItems") or []:
                guid = it.get("guid")
                if not guid:
                    continue
                kitchen = is_kitchen_path(menu_name, top_name)
                cur = items.get(guid)
                if cur is None:
                    cur = items[guid] = {
                        "name": (it.get("name") or "").strip(),
                        "menu": menu_name,
                        "group": top_name,
                        "kitchen": kitchen,
                        "menus": [],
                    }
                elif kitchen and not cur["kitchen"]:
                    cur.update({"menu": menu_name, "group": top_name, "kitchen": True})
                if menu_name not in cur["menus"]:
                    cur["menus"].append(menu_name)
                for ref in it.get("modifierGroupReferences") or []:
                    item_groups.setdefault(guid, set()).add(ref)
            walk(g.get("menuGroups"), menu_name, top_name)

    for menu in data.get("menus") or []:
        name = (menu.get("name") or "").strip()
        if name.lower() in SKIP_MENUS:
            continue
        walk(menu.get("menuGroups"), name, None)

    group_refs = data.get("modifierGroupReferences") or {}
    option_refs = data.get("modifierOptionReferences") or {}
    modifiers: Dict[str, dict] = {}
    parents_by_group: Dict[str, set] = {}
    for item_guid, refs in item_groups.items():
        for ref in refs:
            parents_by_group.setdefault(str(ref), set()).add(item_guid)
    for ref, grp in group_refs.items():
        if not isinstance(grp, dict):
            continue
        for oref in grp.get("modifierOptionReferences") or []:
            opt = (option_refs.get(str(oref)) or option_refs.get(oref)) if isinstance(option_refs, dict) else None
            if not isinstance(opt, dict) or not opt.get("guid"):
                continue
            entry = modifiers.setdefault(opt["guid"], {"name": (opt.get("name") or "").strip(), "groups": [], "parents": set()})
            gname = (grp.get("name") or "").strip()
            if gname and gname not in entry["groups"]:
                entry["groups"].append(gname)
            entry["parents"].update(parents_by_group.get(str(ref), set()))
    for entry in modifiers.values():
        entry["parents"] = sorted(entry["parents"])
    return {
        "items": items,
        "modifiers": modifiers,
        "menu_last_updated": data.get("lastUpdated"),
        "kitchen_rule": "Street Kitchen menus, without their bottled/canned drinks groups",
    }


def menu_map(db: Session) -> Optional[dict]:
    entry = cached_one(db, MENU_MAP_KEY)
    if entry is None:
        return None
    out = dict(entry.meta)
    out["fetched_at"] = entry.fetched_at
    return out


def refresh_menu_map(
    db: Session,
    force: bool = False,
    fetch_last_updated_fn: Optional[Callable[[], Optional[dt.datetime]]] = None,
    fetch_menus_fn: Optional[Callable[[], dict]] = None,
) -> dict:
    """One cheap metadata GET; the full menu only when Toast's lastUpdated
    moved (or nothing is cached). Uses the standard Toast API credentials
    (the same calls as the hourly barcode re-check)."""
    from app.services import inventory_api

    s = get_settings()
    if fetch_menus_fn is None and not (s.toast_client_id and s.toast_client_secret):
        return {"skipped": "Toast API not configured"}
    current = menu_map(db)
    try:
        remote = (fetch_last_updated_fn or inventory_api.fetch_menu_last_updated)()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}
    if current and not force and remote is not None:
        stored = inventory_api.parse_toast_time(current.get("menu_last_updated"))
        if stored is not None and remote <= stored:
            return {"changed": False}
    menus = (fetch_menus_fn or inventory_api.fetch_menus)()
    built = build_menu_map(menus)
    if not built["menu_last_updated"] and remote is not None:
        built["menu_last_updated"] = remote.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    store(db, MENU_MAP_KEY, None, [], built)
    db.commit()
    return {"changed": True, "items": len(built["items"]), "kitchen": sum(1 for v in built["items"].values() if v["kitchen"])}


# ---------------------------------------------------------------------------
# Live today (request path: fail fast, never queue)
# ---------------------------------------------------------------------------

_live_locks: Dict[str, threading.Lock] = {}
_live_locks_guard = threading.Lock()
_live_failed_until: Dict[str, float] = {}
LIVE_LOCK_TIMEOUT = 5.0
LIVE_FAILURE_MEMO_SECONDS = 60.0


def _live_lock(key: str) -> threading.Lock:
    with _live_locks_guard:
        return _live_locks.setdefault(key, threading.Lock())


def live_fetch(db: Session, report: str, day: dt.date) -> Tuple[Optional[Entry], bool]:
    """(entry, failed): `day`'s report from the cache when fresh enough, else
    one quick Toast call. A failure is remembered briefly and the cached copy
    (if any) is returned with failed=True."""
    today_bd = today_business_date()
    have = cached(db, report, [day]).get(day)
    if not needs_fetch(have, day, today_bd):
        return have, False
    if not is_configured():
        return have, True
    key = f"{report}:{day}"
    if _live_failed_until.get(key, 0) > time.time():
        return have, True
    lock = _live_lock(key)
    if not lock.acquire(timeout=LIVE_LOCK_TIMEOUT):
        return have, True
    try:
        have = cached(db, report, [day]).get(day)
        if not needs_fetch(have, day, today_bd):
            return have, False
        fetch_range(db, report, day, day, max_budget_wait=3.0, max_wait=45.0)
        _live_failed_until.pop(key, None)
        return cached(db, report, [day]).get(day), False
    except Exception as exc:  # noqa: BLE001 - the page shows the cached copy
        db.rollback()
        logger.warning("Insights: live %s for %s failed (%s)", report, day, type(exc).__name__)
        _live_failed_until[key] = time.time() + LIVE_FAILURE_MEMO_SECONDS
        return have, True
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Background fill (request paths) and the nightly job
# ---------------------------------------------------------------------------

_fill_lock = threading.Lock()


def is_filling() -> bool:
    return _fill_lock.locked()


def request_fill(wants: List[Tuple[str, List[dt.date]]]) -> bool:
    """Start one background fill for the dates a page is missing - production
    only (SCHEDULER_ENABLED, so dev never spends the shared Toast limits) and
    one at a time. Returns True when a fill is running."""
    if not wants or not get_settings().scheduler_enabled or not is_configured():
        return False
    if _fill_lock.locked():
        return True

    def run():
        if not _fill_lock.acquire(blocking=False):
            return
        db = SessionLocal()
        try:
            today_bd = today_business_date()
            for report, dates in wants:
                if report == "menu_map":
                    try:
                        refresh_menu_map(db)
                    except Exception:  # noqa: BLE001
                        db.rollback()
                        logger.exception("Insights: menu map refresh failed")
                    continue
                result = fill_reserved(
                    db, report, dates, max_calls=BACKGROUND_MAX_CALLS, today_bd=today_bd, max_budget_wait=60.0
                )
                logger.info("Insights background fill %s: %s", report, result)
        finally:
            db.close()
            _fill_lock.release()

    _spawn(run)
    return True


def _metrics_fingerprints(db: Session, dates: List[dt.date]) -> Dict[dt.date, Optional[str]]:
    have = cached(db, "metrics_day", dates)
    return {d: fingerprint(sum_rows(e.rows)) if e.rows else None for d, e in have.items()}


def _changed_dates(db: Session, report: str, dates: List[dt.date], fps: Dict[dt.date, Optional[str]]) -> List[dt.date]:
    """Dates whose day metrics moved since `report` was fetched for them."""
    have = cached(db, report, dates)
    out = []
    for d in dates:
        e = have.get(d)
        if e is None:
            continue
        now_fp = fps.get(d)
        if now_fp is not None and e.meta.get("fp") != now_fp:
            out.append(d)
    return out


def purge_old(db: Session, today: Optional[dt.date] = None) -> dict:
    today = today or today_business_date()
    out = {}
    out["guest_visits"] = db.execute(
        delete(GuestVisit).where(GuestVisit.visit_date < today - dt.timedelta(days=GUEST_RETENTION_DAYS))
    ).rowcount
    out["check"] = db.execute(
        delete(AnalyticsCache).where(
            AnalyticsCache.report == "check",
            AnalyticsCache.business_date < today - dt.timedelta(days=CHECK_RETENTION_DAYS),
        )
    ).rowcount
    out["cache"] = db.execute(
        delete(AnalyticsCache).where(
            AnalyticsCache.business_date.is_not(None),
            AnalyticsCache.business_date < today - dt.timedelta(days=CACHE_RETENTION_DAYS),
        )
    ).rowcount
    out["call_log"] = db.execute(
        delete(AnalyticsCallLog).where(
            AnalyticsCallLog.called_at < utcnow() - dt.timedelta(days=CALL_LOG_RETENTION_DAYS)
        )
    ).rowcount
    db.commit()
    return out


def fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_time(t: dt.datetime) -> str:
    local = t.astimezone(restaurant_timezone())
    return local.strftime("%I:%M %p").lstrip("0")


def last_day_warnings(db: Session, today_bd: dt.date) -> List[str]:
    """One line per report whose last closed date still isn't in the cache,
    e.g. "check: Oct 13 waiting for budget (frees 1:17 AM)"."""
    y = today_bd - dt.timedelta(days=1)
    if y < FIRST_REAL_SALES_DATE:
        return []
    out = []
    for report, spec in REPORTS.items():
        e = cached(db, report, [y], unavailable=True).get(y)
        if e is not None and e.unavailable:
            out.append(f"{report}: {fmt_day(y)} unavailable from Toast (tried again tomorrow night)")
            continue
        if not dates_to_fetch(db, report, [y], today_bd):
            continue
        family = spec.get("daily_family")
        if family and daily_left(db, family) <= 0:
            frees = budget_frees_at(db, family)
            out.append(f"{report}: {fmt_day(y)} waiting for budget" + (f" (frees {fmt_time(frees)})" if frees else ""))
        else:
            out.append(f"{report}: {fmt_day(y)} not read yet")
    return out


def nightly_done_since(db: Session, since: dt.datetime) -> bool:
    """Has a nightly run finished since `since` (whatever its status)?"""
    return (
        db.execute(
            select(func.count(JobRun.id)).where(
                JobRun.job_name == NIGHTLY_JOB, JobRun.started_at >= since, JobRun.finished_at.is_not(None)
            )
        ).scalar()
        or 0
    ) > 0


def run_catchup(db: Session, now: Optional[dt.datetime] = None) -> Optional[dict]:
    """Hourly 05:20-23:20 (CATCHUP_HOURS): the last PRIORITY_DAYS closed
    dates of every report are checked in the cache - no Toast call - and only
    what's missing (or provisional) is fetched, while the budget has room. So
    a day refused for budget is read within the hour after the budget frees,
    not the next night or on someone's page open. Dates marked unavailable
    wait for the nightly. Writes a job_runs row only when it called Toast."""
    if not is_configured():
        return None
    if not _fill_lock.acquire(blocking=False):
        return None  # the nightly or a page's fill is running
    try:
        today_bd = today_business_date(now)
        wanted = expected_dates(today_bd - dt.timedelta(days=PRIORITY_DAYS), today_bd - dt.timedelta(days=1))
        todo = {r: dates_to_fetch(db, r, wanted, today_bd) for r in REPORTS}
        todo = {r: v for r, v in todo.items() if v}
        if not todo:
            return {}
        started = utcnow()
        detail: dict = {}
        for report, dates in todo.items():
            family = REPORTS[report].get("daily_family")
            cap = len(dates)
            if family:
                cap = min(cap, daily_left(db, family))
            detail[report] = fill(db, report, dates, max_calls=cap, today_bd=today_bd, max_budget_wait=60.0)
        calls = sum(v.get("calls", 0) for v in detail.values())
        if detail.get("check", {}).get("dates"):
            try:
                from app.services import insights

                detail["flags"] = insights.refresh_flags(db, today_bd=today_bd)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.exception("Insights catch-up: flags refresh failed")
                detail["flags"] = {"error": str(exc)[:200]}
        logger.info("Insights catch-up: %s", detail)
        if calls:
            errors = [k for k, v in detail.items() if isinstance(v, dict) and v.get("error") and not v.get("budget")]
            short = [k for k, v in detail.items() if isinstance(v, dict) and (v.get("budget") or v.get("pending"))]
            job = JobRun(
                job_name=CATCHUP_JOB,
                status="failed" if errors else ("partial" if short else "success"),
                started_at=started,
                finished_at=utcnow(),
                detail=json.dumps(detail, default=str)[:20000],
            )
            db.add(job)
            db.commit()
        return detail
    except Exception:  # noqa: BLE001 - never crashes the scheduler
        db.rollback()
        logger.exception("Insights catch-up failed")
        return None
    finally:
        _fill_lock.release()


_NIGHTLY_LOCK = threading.Lock()


def run_nightly(db: Session, now: Optional[dt.datetime] = None, refresh_menu: bool = True) -> Optional[JobRun]:
    """The 04:30 job: yesterday's reports, re-pulls of recent days that
    changed, a little more history, the menu map, Loss watch / Odd checks
    flags and the retention purge. Never raises; one job_runs row."""
    if not is_configured():
        logger.info("Insights nightly skipped: Toast Analytics is not configured")
        return None
    if not _NIGHTLY_LOCK.acquire(blocking=False):
        return None
    job = JobRun(job_name=NIGHTLY_JOB, status="running", started_at=utcnow())
    db.add(job)
    db.commit()
    detail: dict = {}
    try:
        with _fill_lock:
            today_bd = today_business_date(now)
            y = today_bd - dt.timedelta(days=1)
            recent = expected_dates(y - dt.timedelta(days=REPULL_DAYS - 1), y)

            def window(report):
                return expected_dates(y - dt.timedelta(days=HISTORY_DAYS[report] - 1), y)

            # Dates marked unavailable (Toast couldn't process them) get their
            # one retry a night here; page opens and the catch-up skip them.
            for report in ("metrics_day", "metrics_dining", "menu_item", "menu_modifier"):
                detail[report] = fill(
                    db, report, window(report), max_calls=NIGHTLY_MAX_CALLS[report], today_bd=today_bd, refresh=recent,
                    retry_unavailable=True,
                )
            fps = _metrics_fingerprints(db, window("metrics_hour"))
            changed = _changed_dates(db, "metrics_hour", recent, fps)
            detail["metrics_hour"] = fill(
                db, "metrics_hour", window("metrics_hour"), max_calls=NIGHTLY_MAX_CALLS["metrics_hour"],
                today_bd=today_bd, refresh=changed, retry_unavailable=True,
            )
            detail["metrics_hour"]["changed"] = [d.isoformat() for d in changed]
            check_recent = expected_dates(y - dt.timedelta(days=CHECK_REPULL_DAYS - 1), y)
            check_fps = _metrics_fingerprints(db, window("check"))
            changed = _changed_dates(db, "check", check_recent, check_fps)
            # Daily-capped reports: the last closed dates first, history only
            # above the reserve (fill_reserved).
            detail["check"] = fill_reserved(
                db, "check", window("check"), max_calls=daily_budget("check"), today_bd=today_bd,
                refresh=changed, day_fingerprints=check_fps, retry_unavailable=True,
            )
            detail["check"]["changed"] = [d.isoformat() for d in changed]
            detail["guest"] = fill_reserved(
                db, "guest", window("guest"), max_calls=daily_budget("guest"), today_bd=today_bd,
                retry_unavailable=True,
            )
            if refresh_menu:
                try:
                    detail["menu_map"] = refresh_menu_map(db)
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    detail["menu_map"] = {"error": str(exc)[:200]}
        try:
            from app.services import insights

            detail["flags"] = insights.refresh_flags(db, today_bd=today_bd)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("Insights: flags refresh failed")
            detail["flags"] = {"error": str(exc)[:200]}
        detail["purged"] = purge_old(db, today_bd)
        detail["budget_left"] = {"check": daily_left(db, "check"), "guest": daily_left(db, "guest")}
        warnings = last_day_warnings(db, today_bd)
        if warnings:
            detail["warnings"] = warnings
        # "failed" for a real error; "partial" when the only trouble is the
        # daily budget or yesterday still missing (a zero-call fill used to
        # say "success" while Loss watch waited a day).
        errors = [k for k, v in detail.items() if isinstance(v, dict) and v.get("error") and not v.get("budget")]
        short = [k for k, v in detail.items() if isinstance(v, dict) and v.get("budget")]
        job.status = "failed" if errors else ("partial" if warnings or short else "success")
    except Exception as exc:  # noqa: BLE001 - recorded, never crashes the scheduler
        db.rollback()
        logger.exception("Insights nightly failed")
        job.status = "failed"
        detail["error"] = str(exc)[:500]
    finally:
        job.finished_at = utcnow()
        job.detail = json.dumps(detail, default=str)[:20000]
        db.add(job)
        db.commit()
        _NIGHTLY_LOCK.release()
    return job
