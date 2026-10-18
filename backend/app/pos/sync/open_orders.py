"""Open orders (shown as "Open Checks") - POS orders not yet closed (POS-neutral).

Moved from ``services/open_orders.py`` in multi-store M1 batch MT3; that name
still works (``app.services.open_orders``, forwarding here). The store's
connector hands over open ``Order`` models (``fetch_open_orders``: what "open"
means is the POS's rule - for Toast, no closedDate and not voided/deleted);
this module turns them into the rows the API and the cache always had.

Two ways to see this:
  - get_open_orders(business_date): a live, single-date pull straight from
    the POS (one API call) - stays fast, used for "today" in the UI.
  - The "all-time" view (every business date since real sales began) is too
    expensive to compute live on every page load (~100+ Toast API calls,
    several minutes) - see the real ~3.5 minute scan confirmed in this
    project's skill notes. Instead it's kept in `open_order_cache`
    (models.py), refreshed in the background on a schedule
    (scheduler.py) via refresh_open_orders_cache(), and read instantly by
    get_cached_open_orders(). The cache is a full-replace snapshot, not an
    append-only log - a row not present after a scan means that order is no
    longer open (closed, or fell outside the scanned range).
"""

import datetime as dt
import json
import logging
import time
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import JobRun, OpenOrderCache
from app.pos import registry
from app.pos.models import Order as PosOrder
from app.pos.sync.sales import line_rows
from app.timeutil import restaurant_today, utcnow
from app.tenancy import spawn_in_store, tenant_local, tenant_lock

# Same logger name as before the move (log lines and filters unchanged).
logger = logging.getLogger("app.services.open_orders")

JOB_NAME = "open_orders_scan"

_EMPLOYEE_CACHE: dict = tenant_local("open_orders._EMPLOYEE_CACHE", lambda: {"at": 0.0, "by_guid": {}})
_EMPLOYEE_CACHE_TTL_SECONDS = 600

# Real order volume starts at the store's FIRST_SALES_DATE (store setting;
# earlier dates are onboarding test orders). Scanning further back for an
# "all time" open-orders sweep would only spend POS API calls on dates that
# can't contain anything real. Without a FIRST_SALES_DATE the sweep starts at
# the oldest stored sale, at most FULL_SCAN_MAX_DAYS back (full_scan_start).
FULL_SCAN_MAX_DAYS = 365


def get_employees(force_refresh: bool = False) -> list[dict]:
    """[{guid, name}] for every active POS employee, cached briefly - the
    employee roster changes rarely, so there's no need to ask the POS (Toast's
    Labor API) on every single open-orders refresh.
    """
    now = time.time()
    if force_refresh or now - _EMPLOYEE_CACHE["at"] > _EMPLOYEE_CACHE_TTL_SECONDS:
        employees = registry.get_connector().fetch_employees()
        by_guid = {e.external_id: e.name for e in employees if e.active and e.external_id}
        _EMPLOYEE_CACHE["by_guid"] = by_guid
        _EMPLOYEE_CACHE["at"] = now
    return [{"guid": guid, "name": name} for guid, name in sorted(_EMPLOYEE_CACHE["by_guid"].items(), key=lambda kv: kv[1])]


def _order_total(order: PosOrder) -> float:
    """The checks' own totals added up (every check, as the POS states it)."""
    total = 0.0
    for check in order.checks:
        if check.total is not None:
            total += float(check.total)
    return round(total, 2)


def _line_items(order: PosOrder) -> list[dict]:
    """Top-level (non-modifier, non-voided) items on this order, from the same
    rows the sales sync writes (pos/sync/sales.line_rows), so modifier/voided
    handling can't drift between "what counts as a sold item" and "what shows
    up here" (see the modifier-double-counting gotcha in the project skill).
    """
    items = []
    for row in line_rows(order):
        if row["parent_selection_guid"] is not None or row["voided"]:
            continue
        items.append({"name": row["item_name"] or "Item", "quantity": row["quantity"]})
    return items


def tab_names_of(display_number, checks) -> list:
    """Each check's tab name (the name typed on the tab - usually the
    customer's), in check order, trimmed and de-duplicated (case-insensitive).
    Deleted/voided checks are skipped, as are blank names and a tab name that
    is just the order's own number (Toast sometimes fills the tab with it,
    which would read "Check #12 - Tab: 12"). Missing on most counter orders.
    """
    number = str(display_number or "").strip()
    seen = set()
    names = []
    for c in checks:
        if c.deleted or c.voided:
            continue
        raw = c.tab_name
        if not isinstance(raw, str):
            continue
        name = " ".join(raw.split())
        if not name or name == number:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _tab_names(order: PosOrder) -> list:
    return tab_names_of(order.display_number, order.checks)


def tab_label(tab_names: Optional[list]) -> Optional[str]:
    """"Ravi" / "Ravi, Priya" for display, None when no check has a tab name."""
    names = [n for n in (tab_names or []) if n]
    return ", ".join(names) if names else None


def legacy_display_number(order: dict) -> Optional[str]:
    """The tab name folded into `display_number`, for app builds 5-7 that
    only show display_number ("Order #12" card/detail title, "#12" on the
    Home tile; never parsed or used as a key, only shown and substring-
    searched - see mobile open_order_card.dart / open_orders_screen.dart /
    today_card.dart / open_orders_filter.dart at 2ad4fbc). "12 · Tab: Ravi"
    -> "Order #12 · Tab: Ravi". Unchanged when there's no tab name."""
    number = order.get("display_number")
    label = order.get("tab_name")
    if not label:
        return number
    return f"{number if number not in (None, '') else '?'} \u00b7 Tab: {label}"


def _paid_at(order: PosOrder) -> Optional[dt.datetime]:
    """The order is only "paid" if every check on it has a payment time - a
    split-check order with one paid, one still open isn't fully settled.
    Returns the latest of the checks' payment times (when it became fully
    paid).
    """
    if not order.checks:
        return None
    paid_dates = []
    for c in order.checks:
        if c.paid_at is None:
            return None  # at least one check unpaid -> order not fully paid
        paid_dates.append(c.paid_at)
    return max(paid_dates)


def _promised_at(order: PosOrder) -> Optional[dt.datetime]:
    # The guest-facing commitment (e.g. a scheduled pickup/delivery time),
    # else the POS's own estimate. Same "future order" pattern as gotcha #12
    # in the project skill - a guest can pay tonight for tomorrow.
    return order.promised_at


def _placed_at(order: PosOrder) -> Optional[dt.datetime]:
    """When the order was actually placed. Toast's `openedDate` is NOT that for
    a scheduled order: a guest who pays on the 23rd for a pickup on the 25th
    gets openedDate = the 25th (real order #105, Sep 2026 - gotcha #12), so the
    app showed "opened" in the future and counted "open for" from it.
    The placement time (`createdDate`) is the real one; opened is a fallback."""
    return order.created_at or order.opened_at


def _is_scheduled(promised_at: Optional[dt.datetime], now: dt.datetime) -> bool:
    """Still waiting for a future pickup/delivery time - expected to be open,
    not something to chase."""
    return promised_at is not None and promised_at > now


def _normalize_open_order(o: PosOrder, business_date: dt.date, names_by_guid: dict, now: dt.datetime) -> dict:
    """One open order as the API / cache row (keys unchanged since app build 5)."""
    server_guid = o.server_external_id
    opened_at = _placed_at(o)
    elapsed_minutes = None
    if opened_at is not None:
        elapsed_minutes = round((now - opened_at).total_seconds() / 60)
    paid_at = _paid_at(o)
    promised_at = _promised_at(o)
    tab_names = _tab_names(o)
    return {
        "guid": o.external_id,
        "business_date": business_date.isoformat(),
        "display_number": o.display_number,
        "opened_at": opened_at.isoformat() if opened_at else None,
        "elapsed_minutes": elapsed_minutes,
        "server_guid": server_guid,
        "server_name": names_by_guid.get(server_guid, "Unknown"),
        "num_guests": o.guests,
        "approval_status": o.approval_status,
        "total_amount": _order_total(o),
        "num_checks": len(o.checks),
        "tab_names": tab_names,
        "tab_name": tab_label(tab_names),
        "line_items": _line_items(o),
        "paid_at": paid_at.isoformat() if paid_at else None,
        "promised_at": promised_at.isoformat() if promised_at else None,
        "scheduled": _is_scheduled(promised_at, now),
    }


def get_open_orders(business_date: dt.date, employee_guid: Optional[str] = None) -> list[dict]:
    """Currently-open orders for one business date: opened, not voided/
    deleted, and never closed. Optionally filtered to one employee (matched
    against the order's server). Live pull, one POS call - used for the fast
    single-date view (normally "today").
    """
    employees = get_employees()
    names_by_guid = {e["guid"]: e["name"] for e in employees}

    orders = registry.get_connector().fetch_open_orders([business_date])
    now = utcnow()
    results = []
    for o in orders:
        if employee_guid and o.server_external_id != employee_guid:
            continue
        results.append(_normalize_open_order(o, business_date, names_by_guid, now))
    results.sort(key=lambda r: r["opened_at"] or "")
    return results


# The courtesy pause after each date of a scan.
SCAN_PAUSE_SECONDS = 0.15


def _scan_open_orders(start: dt.date, end: dt.date, max_retries: int = 4) -> list[dict]:
    """One POS call per date in [start, end] (plus pagination on busy days),
    retried on rate limits, with a small courtesy delay between requests.
    This is the genuinely slow part (real-world: ~100+ calls, minutes) -
    callers should not run this inline on a request a user is waiting on;
    see refresh_open_orders_cache.
    """
    employees = get_employees()
    names_by_guid = {e["guid"]: e["name"] for e in employees}
    now = utcnow()

    dates = []
    d = start
    while d <= end:
        dates.append(d)
        d += dt.timedelta(days=1)
    orders = registry.get_connector().fetch_open_orders(
        dates, max_retries=max_retries, pause_seconds=SCAN_PAUSE_SECONDS
    )
    return [_normalize_open_order(o, o.business_date, names_by_guid, now) for o in orders]


def _replace_cache_rows(db: Session, orders: list[dict], business_dates: Optional[list[dt.date]] = None) -> None:
    """Replaces cache rows with fresh `orders`. If `business_dates` is given,
    only rows for those dates are deleted first (a partial/"today" refresh
    that must leave older cached days untouched); otherwise every existing
    row is cleared first (a full rescan, where "not present in `orders`
    anymore" correctly means "no longer open").
    """
    now = utcnow()
    if business_dates is not None:
        db.execute(delete(OpenOrderCache).where(OpenOrderCache.business_date.in_(business_dates)))
    else:
        db.execute(delete(OpenOrderCache))
    for o in orders:
        db.add(
            OpenOrderCache(
                toast_order_guid=o["guid"],
                external_order_id=o["guid"],  # POS-neutral copy (migration 0060)
                business_date=dt.date.fromisoformat(o["business_date"]),
                display_number=o["display_number"],
                opened_at=dt.datetime.fromisoformat(o["opened_at"]) if o["opened_at"] else None,
                server_guid=o["server_guid"],
                server_name=o["server_name"],
                num_guests=o["num_guests"],
                approval_status=o["approval_status"],
                total_amount=o["total_amount"],
                num_checks=o["num_checks"],
                line_items_json=json.dumps(o["line_items"]),
                tab_names_json=json.dumps(o.get("tab_names") or []),
                paid_at=dt.datetime.fromisoformat(o["paid_at"]) if o["paid_at"] else None,
                promised_at=dt.datetime.fromisoformat(o["promised_at"]) if o["promised_at"] else None,
                scanned_at=now,
            )
        )
    db.commit()


def refresh_open_orders_cache(db: Session, full: bool = False, max_retries: int = 4) -> JobRun:
    """Rescans the POS and replaces `open_order_cache`.

    full=False (the frequent, cheap tick): only re-scans today's business
    date - covers the vast majority of real status changes (an order opening
    or closing), since a day that's weeks old essentially never changes.
    full=True (the once-daily sweep): re-scans the entire history from
    FIRST_REAL_SALES_DATE, the only way to catch an order that's been stuck
    open on an OLDER date that a today-only refresh would never look at
    again once that date rolled off "today".
    """
    job = JobRun(job_name=JOB_NAME, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        if full:
            end = restaurant_today()
            start = full_scan_start(db, end)
            orders = _scan_open_orders(start, end)
            _replace_cache_rows(db, orders, business_dates=None)
            job.detail = f"full scan {start}..{end}: {len(orders)} open check(s)"
        else:
            today = restaurant_today()
            orders = _scan_open_orders(today, today, max_retries=max_retries)
            _replace_cache_rows(db, orders, business_dates=[today])
            job.detail = f"today ({today}) refresh: {len(orders)} open check(s)"
        job.status = "success"
    except Exception as exc:  # noqa: BLE001 - log and record, don't crash the caller
        db.rollback()
        logger.exception("Open orders cache refresh failed (full=%s)", full)
        job.status = "failed"
        job.detail = str(exc)
    finally:
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        db.refresh(job)
    return job


# On-demand freshness for the cached view (contract "live reports"): a read of
# the all-time list refreshes today's part first when the last scan is older
# than this. One refresh at a time; anyone arriving meanwhile gets the cache.
LIVE_MAX_AGE_SECONDS = 120
_live_refresh_lock = tenant_lock("open_orders._live_refresh_lock")
# Monotonic time of the last on-demand refresh that failed: a failed scan
# writes no "success" row, so without this every request would ask Toast
# again while it's down or rate limiting us.
_live_last_failure: dict = tenant_local("open_orders._live_last_failure", lambda: {"at": None})


def _today_is_stale(db: Session, max_age_seconds: int) -> bool:
    """Should a read refresh today's part? Only where the scheduler runs
    (production): dev and tests keep SCHEDULER_ENABLED=false to stay off the
    shared Toast rate limit."""
    settings = get_settings()
    if not (settings.scheduler_enabled and settings.toast_client_id):
        return False
    last = db.execute(
        select(JobRun.started_at)
        .where(JobRun.job_name == JOB_NAME, JobRun.status.in_(["success", "running"]))
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is not None and (utcnow() - last).total_seconds() < max_age_seconds:
        return False
    failed_at = _live_last_failure["at"]
    if failed_at is not None and time.monotonic() - failed_at < max_age_seconds:
        return False
    return True


def _refresh_today(db: Session) -> bool:
    """One today-only refresh; the caller holds _live_refresh_lock."""
    try:
        # No 429 sleeps here: someone is waiting for the page, and the
        # cached list is a fine answer meanwhile.
        job = refresh_open_orders_cache(db, full=False, max_retries=0)
        ok = job.status == "success"
    except Exception:  # noqa: BLE001 - the cached list is still served
        logger.exception("On-demand open checks refresh failed")
        ok = False
    _live_last_failure["at"] = None if ok else time.monotonic()
    return ok


def ensure_today_fresh(db: Session, max_age_seconds: int = LIVE_MAX_AGE_SECONDS) -> bool:
    """Refreshes today's open checks (one Toast call) if the last scan is
    older than max_age_seconds, before returning. Returns True when it
    refreshed. Never raises. (GET /open/all-time no longer waits for this:
    refresh_today_in_background.)"""
    if not _today_is_stale(db, max_age_seconds):
        return False
    if not _live_refresh_lock.acquire(blocking=False):
        return False
    try:
        return _refresh_today(db)
    finally:
        _live_refresh_lock.release()


def refresh_today_in_background(db: Session, max_age_seconds: int = LIVE_MAX_AGE_SECONDS, session_factory=None) -> bool:
    """Stale-while-revalidate (speed #8, Oct 15): when today's part is older
    than max_age_seconds, start the same one-call refresh on a background
    thread (its own session) and return at once - the caller serves the
    cached list now and the next read gets the fresh one. The Home "Open
    orders" tile no longer waits ~0.9 s for Toast. True = a refresh was
    started. Never raises."""
    try:
        if not _today_is_stale(db, max_age_seconds):
            return False
    except Exception:  # noqa: BLE001 - the cached list is still served
        logger.exception("Open checks freshness check failed")
        return False
    if not _live_refresh_lock.acquire(blocking=False):
        return False  # one already running

    def run() -> None:
        try:
            if session_factory is None:
                from app.db import SessionLocal

                session = SessionLocal()
            else:
                session = session_factory()
            try:
                _refresh_today(session)
            finally:
                session.close()
        except Exception:  # noqa: BLE001
            logger.exception("Background open checks refresh failed")
        finally:
            _live_refresh_lock.release()

    try:
        spawn_in_store(run, name="open-checks-refresh")  # keeps this store's context
    except Exception:  # noqa: BLE001 - e.g. no threads left: serve the cache
        _live_refresh_lock.release()
        logger.exception("Could not start the open checks refresh")
        return False
    return True


def _cached_tab_names(r: OpenOrderCache) -> list:
    # NULL for rows cached before migration 0045, until the next scan.
    if not r.tab_names_json:
        return []
    try:
        names = json.loads(r.tab_names_json)
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str) and n] if isinstance(names, list) else []


def get_cached_open_orders(db: Session, employee_guid: Optional[str] = None) -> dict:
    """Instant read of the background-refreshed cache - no live Toast calls."""
    stmt = select(OpenOrderCache)
    if employee_guid:
        stmt = stmt.where(OpenOrderCache.server_guid == employee_guid)
    rows = db.execute(stmt).scalars().all()

    bounds_stmt = select(
        JobRun.started_at, JobRun.finished_at, JobRun.status, JobRun.detail
    ).where(JobRun.job_name == JOB_NAME).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(1)
    last_job = db.execute(bounds_stmt).first()

    scanned_from = min((r.business_date for r in rows), default=None)
    scanned_to = max((r.business_date for r in rows), default=None)
    last_refreshed_at = max((r.scanned_at for r in rows), default=None)

    orders = [
        {
            "guid": r.toast_order_guid,
            "business_date": r.business_date.isoformat(),
            "display_number": r.display_number,
            "opened_at": r.opened_at.isoformat() if r.opened_at else None,
            # r.opened_at already comes back UTC-aware (UTCDateTime re-attaches
            # tzinfo on read - see db.py), so this is a plain aware-aware diff.
            "elapsed_minutes": (
                round((utcnow() - r.opened_at).total_seconds() / 60) if r.opened_at else None
            ),
            "server_guid": r.server_guid,
            "server_name": r.server_name,
            "num_guests": r.num_guests,
            "approval_status": r.approval_status,
            "total_amount": r.total_amount,
            "num_checks": r.num_checks,
            "tab_names": _cached_tab_names(r),
            "tab_name": tab_label(_cached_tab_names(r)),
            "line_items": json.loads(r.line_items_json) if r.line_items_json else [],
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
            "promised_at": r.promised_at.isoformat() if r.promised_at else None,
            "scheduled": _is_scheduled(r.promised_at, utcnow()),
        }
        for r in rows
    ]
    orders.sort(key=lambda o: o["opened_at"] or "")
    return {
        "scanned_from": scanned_from.isoformat() if scanned_from else None,
        "scanned_to": scanned_to.isoformat() if scanned_to else None,
        "last_refreshed_at": last_refreshed_at.isoformat() if last_refreshed_at else None,
        "last_job_status": last_job.status if last_job else None,
        "orders": orders,
    }


def __getattr__(name):
    # The old constants, now store settings (FIRST_SALES_DATE,
    # BUSINESS_DAY_CUTOFF_HOUR), read at call time. FIRST_REAL_SALES_DATE is
    # date.min when the store has no floor.
    from app.store import settings_view as _sv

    if name == "FIRST_REAL_SALES_DATE":
        return _sv.sales_floor()
    if name == "CLOSEOUT_HOUR":
        return _sv.business_day_cutoff_hour()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def full_scan_start(db: Session, today: dt.date) -> dt.date:
    """Where the "all time" sweep starts: FIRST_SALES_DATE when set, else the
    oldest stored business date (at most FULL_SCAN_MAX_DAYS back), else today."""
    from sqlalchemy import func

    from app.models import Order
    from app.store import settings_view as _sv

    floor = _sv.first_sales_date()
    if floor is not None:
        return floor
    oldest = db.execute(select(func.min(Order.business_date))).scalar()
    if oldest is None:
        return today
    if isinstance(oldest, str):
        oldest = dt.date.fromisoformat(oldest[:10])
    return max(oldest, today - dt.timedelta(days=FULL_SCAN_MAX_DAYS))
