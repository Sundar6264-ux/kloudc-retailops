"""Sales accuracy: re-pull recent business dates that Toast changed after we
synced them (contract item 2, docs/AUTOMATION_BATCH_CONTRACT.md).

POS-neutral since multi-store M1 batch MT3 (moved from
``services/sales_accuracy.py``, which still works and forwards here): the POS's
own day totals come from the connector (``get_day_totals`` -> ``SalesDay``,
for Toast the two Analytics reports below), and dates are re-synced through
``pos/sync/sales``. job_runs names, details and fingerprints are unchanged.

The nightly sync (scheduler.run_daily_sales_sync) syncs each business date once
and never looks at it again, but Toast keeps changing closed days: a check
voided or discounted the next morning, an item removed, a late payment. Toast
says those edits reach its Analytics API within 2-4 hours (or up to 6 days for
orders edited long after their date - docs/apiAnalyticsDataDiscrepancies).

Each run (SALES_REPULL_TIMES, default 06:30 / 12:30 / 17:30 / 22:30 store time):

1. Asks Toast Analytics for the last SALES_REPULL_DAYS business dates (default
   14, yesterday back): the item-level day totals (`menu` report - the sum
   over sold items, which is exactly what our top-level, non-voided
   `orders.net_price` adds up to) and the day metrics (`metrics` report - net,
   gross, discount, void, refund, order counts).
2. Compares them with our stored totals per date and re-syncs ONLY the dates
   that need it, through the normal orders sync (sales_sync.sync_sales_for_date
   - an idempotent upsert by selection guid that also drops selections Toast
   no longer has). A date is re-synced when:
   - item_net / item_qty: our top-level net or quantity differs from Toast's
     item-level total;
   - orders: our count of orders differs from Toast's ordersCount;
   - analytics_changed: Toast's numbers for the date changed since the last
     check (something was edited, even if the totals happen to agree);
   - unpaid: we still hold unpaid line items for the date and last synced it
     more than UNPAID_RESYNC_HOURS ago (a payment may have landed - it moves
     the sale into Items Sold, which counts by payment date);
   - never_synced: no successful sync for the date yet.
   A date whose re-sync changed nothing and whose Toast numbers haven't moved
   since is not re-synced again until they do ("mismatch, waiting") - so a
   definitional difference or Analytics lag never makes us hammer Toast.
3. Records every comparison as a `job_runs` row (job_name "sales_accuracy",
   business_date = the date, detail = JSON with the before / after totals,
   reasons and verdict) plus one "sales_repull" summary row per run. The admin
   "Sales accuracy" card on Items Sold reads them (GET /api/sales/accuracy).

No Analytics credentials (TOAST_ANALYTICS_CLIENT_ID/SECRET unset; in general:
the connector's ``has_day_totals()`` is False) -> the **re-check without
Analytics** runs instead (pre-production 2026-10-21, docs/PARITY_INVESTIGATION.md:
a store without the keys silently kept every late Toast edit out):

- once per closed business day (the first run of the day, normally 06:30), it
  re-syncs the last RECHECK_DAYS (7) business dates that were synced before -
  late items on a closed check, late comps / discounts / re-prices, late
  payments all come in;
- at every run it re-syncs any date that still holds unpaid lines and was
  last synced more than UNPAID_RESYNC_HOURS ago (a payment may have landed).

About 7-10 small Orders API calls a day. Every pass is one ``sales_recheck``
job_runs row (business_date = the closed business "today" it ran for) whose
detail says, in plain words, what changed. The parity report and
``check-config`` read these to say the Analytics keys are missing. A POS
that can't fetch orders by business date gets neither job.
"""

import datetime as dt
import json
import logging
import time
from typing import Callable, Optional

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import fsum, group_order
from app.models import JobRun, Order
from app.pos import registry
from app.pos.base import DayTotalsUnavailable
from app.pos.capabilities import Capability
from app.pos.models import SalesDay
from app.pos.sync.sales import JOB_NAME as SALES_SYNC_JOB, sync_sales_for_date
from app.store import settings_view as sv
from app.timeutil import restaurant_timezone, restaurant_today, utcnow
from app.tenancy import tenant_lock

# Same logger name as before the move (log lines and filters unchanged).
logger = logging.getLogger("app.services.sales_accuracy")

RUN_JOB = "sales_repull"
CHECK_JOB = "sales_accuracy"
RECHECK_JOB = "sales_recheck"  # the re-check without Analytics (one row per pass)
RECHECK_DAYS = 7
DAILY_PREFIX = "Daily re-check"
NO_KEYS_WARNING = "Sales double-check is limited: Toast Analytics keys are missing"

# Real line items start at the store's FIRST_SALES_DATE (earlier dates are
# onboarding tests); no floor when it is blank. Read at call time:
# settings_view.sales_floor().
MAX_DAYS = 60
MONEY_TOLERANCE = 0.015
QTY_TOLERANCE = 0.005
UNPAID_RESYNC_HOURS = 6
KEEP_CHECK_DAYS = 120
RESYNC_PAUSE_SECONDS = 0.5

# The POS closeout hour (BUSINESS_DAY_CUTOFF_HOUR, default 04:00): a business
# date stays open until then the next morning, so a run between midnight and
# the cutoff must not treat the still-open business date as "yesterday" (it
# would compare, and re-sync, a day that is still taking orders).

_RUN_LOCK = tenant_lock("accuracy._RUN_LOCK")
_sleep: Callable[[float], None] = time.sleep


def closed_business_today(now: Optional[dt.datetime] = None) -> dt.date:
    """The store's calendar date, minus one before the 04:00 closeout - so
    "yesterday" of the result is always a closed business date."""
    local = (now or utcnow()).astimezone(restaurant_timezone())
    day = local.date()
    return day - dt.timedelta(days=1) if local.hour < sv.business_day_cutoff_hour() else day


def is_running() -> bool:
    return _RUN_LOCK.locked()


def _r2(v) -> float:
    return round(float(v or 0), 2)


def stored_day_totals(db: Session, start: dt.date, end: dt.date) -> dict:
    """{date: {net, qty, orders, lines, unpaid_lines}} over top-level,
    non-voided `orders` rows by business date - the same rows Items Sold
    counts (modifier prices live inside their parent's price)."""
    stmt = (
        select(
            Order.business_date,
            func.coalesce(fsum(Order.net_price), 0.0),
            func.coalesce(fsum(Order.quantity), 0.0),
            func.count(func.distinct(Order.toast_order_guid)),
            func.count(Order.id),
            func.sum(case((Order.paid_at.is_(None), 1), else_=0)),
        )
        .where(
            Order.business_date >= start,
            Order.business_date <= end,
            Order.voided.is_(False),
            Order.top_level(),
        )
        .group_by(Order.business_date)
        .order_by(*group_order(Order.business_date))
    )
    out = {}
    for d, net, qty, orders, lines, unpaid in db.execute(stmt):
        out[d] = {
            "net": _r2(net),
            "qty": round(float(qty or 0), 3),
            "orders": int(orders or 0),
            "lines": int(lines or 0),
            "unpaid_lines": int(unpaid or 0),
        }
    return out


def _empty_totals() -> dict:
    return {"net": 0.0, "qty": 0.0, "orders": 0, "lines": 0, "unpaid_lines": 0}


def _last_successful_sync(db: Session, start: dt.date, end: dt.date) -> dict:
    stmt = (
        select(JobRun.business_date, func.max(func.coalesce(JobRun.finished_at, JobRun.started_at)))
        .where(
            JobRun.job_name == SALES_SYNC_JOB,
            JobRun.status == "success",
            JobRun.business_date >= start,
            JobRun.business_date <= end,
        )
        .group_by(JobRun.business_date)
        .order_by(*group_order(JobRun.business_date))
    )
    out = {}
    for d, at in db.execute(stmt):
        if isinstance(at, str):  # func.max() skips the UTCDateTime type
            at = dt.datetime.fromisoformat(at)
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=dt.timezone.utc)
        out[d] = at
    return out


def _previous_checks(db: Session, start: dt.date, end: dt.date) -> dict:
    """The latest recorded comparison per date, {date: detail dict}."""
    latest = (
        select(JobRun.business_date, func.max(JobRun.id).label("max_id"))
        .where(JobRun.job_name == CHECK_JOB, JobRun.business_date >= start, JobRun.business_date <= end)
        .group_by(JobRun.business_date)
        .subquery()
    )
    rows = db.execute(select(JobRun).join(latest, JobRun.id == latest.c.max_id)).scalars()
    out = {}
    for row in rows:
        try:
            out[row.business_date] = json.loads(row.detail or "{}")
        except ValueError:
            continue
    return out


def _analytics_view(day: SalesDay) -> dict:
    """The POS's numbers for one date as stored in the job_runs detail (and
    fingerprinted - keys, rounding and order are part of the stored data)."""
    return {
        "item_net": _r2(day.item_net),
        "item_qty": round(float(day.item_qty or 0), 3),
        "item_gross": _r2(day.item_gross),
        "item_discount": _r2(day.item_discounts),
        "item_void": _r2(day.item_voids),
        "net": _r2(day.net),
        "gross": _r2(day.gross),
        "discount": _r2(day.discounts),
        "void": _r2(day.voids),
        "refund": _r2(day.refunds),
        "orders": int(day.orders or 0),
        "void_orders": int(day.void_orders or 0),
        "open_orders": int(day.open_orders or 0),
    }


def _fingerprint(view: dict) -> str:
    return json.dumps(view, sort_keys=True)


def _differences(stored: dict, view: dict, has_metrics: bool) -> list:
    reasons = []
    if abs(stored["net"] - view["item_net"]) > MONEY_TOLERANCE:
        reasons.append("item_net")
    if abs(stored["qty"] - view["item_qty"]) > QTY_TOLERANCE:
        reasons.append("item_qty")
    if has_metrics and stored["orders"] != view["orders"]:
        reasons.append("orders")
    return reasons


def _record(db: Session, day: dt.date, detail: dict, status: str = "success") -> None:
    now = utcnow()
    db.add(
        JobRun(
            job_name=CHECK_JOB,
            status=status,
            business_date=day,
            started_at=now,
            finished_at=now,
            detail=json.dumps(detail, sort_keys=True),
        )
    )


def _window(days: Optional[int], today: dt.date) -> tuple:
    n = days or get_settings().sales_repull_days
    n = max(1, min(int(n), MAX_DAYS))
    end = today - dt.timedelta(days=1)
    start = max(sv.sales_floor(), end - dt.timedelta(days=n - 1))
    return start, end


def run_sales_repull(
    db: Session,
    days: Optional[int] = None,
    today: Optional[dt.date] = None,
    resync: Callable[[Session, dt.date], JobRun] = sync_sales_for_date,
) -> Optional[JobRun]:
    """One accuracy pass (see the module docstring). Returns the summary
    JobRun (``sales_repull``, or ``sales_recheck`` without Analytics keys),
    or None when the POS can do neither or a run is already in progress.
    Never raises: a failure is recorded on the summary row."""
    connector = registry.get_connector()
    analytics = connector.has_day_totals()
    if not analytics and not connector.supports(Capability.ORDERS_BY_BUSINESS_DATE):
        logger.info("Sales accuracy re-pull skipped: the POS has neither day totals nor orders by date")
        return None
    if not _RUN_LOCK.acquire(blocking=False):
        logger.info("Sales accuracy re-pull already running - skipped")
        return None
    try:
        if not analytics:
            logger.info("Sales accuracy: Toast Analytics is not configured - re-check without it")
            return _recheck(db, today or closed_business_today(), resync)
        return _run(db, days, today or closed_business_today(), resync)
    finally:
        _RUN_LOCK.release()


def _daily_recheck_done(db: Session, today: dt.date) -> bool:
    return (
        db.execute(
            select(JobRun.id)
            .where(
                JobRun.job_name == RECHECK_JOB,
                JobRun.business_date == today,
                JobRun.detail.like(DAILY_PREFIX + "%"),
            )
            .limit(1)
        ).first()
        is not None
    )


def _change_text(before: dict, after: dict) -> str:
    parts = []
    lines = after["lines"] - before["lines"]
    if lines:
        parts.append(f"{lines:+d} line(s)")
    net = _r2(after["net"] - before["net"])
    if abs(net) > MONEY_TOLERANCE:
        parts.append(f"net {net:+.2f}")
    paid = before["unpaid_lines"] - after["unpaid_lines"]
    if paid > 0:
        parts.append(f"{paid} line(s) now paid")
    return ", ".join(parts) or "amounts changed"


def _recheck(db: Session, today: dt.date, resync) -> JobRun:
    """The re-check without Analytics (module docstring)."""
    start, end = _window(RECHECK_DAYS, today)
    daily = not _daily_recheck_done(db, today)
    summary = JobRun(job_name=RECHECK_JOB, status="running", business_date=today)
    db.add(summary)
    db.commit()
    db.refresh(summary)
    resynced, changed, failed = [], [], []
    try:
        if end < start:
            summary.status = "success"
            summary.detail = (DAILY_PREFIX if daily else "Re-check") + ": nothing to check yet"
            return summary
        before_all = stored_day_totals(db, start, end)
        last_sync = _last_successful_sync(db, start, end)
        now = utcnow()
        d = start
        while d <= end:
            day = d
            d += dt.timedelta(days=1)
            synced_at = last_sync.get(day)
            if synced_at is None:
                continue  # never synced: the nightly sync's job, not ours
            stored = before_all.get(day, _empty_totals())
            unpaid_due = bool(
                stored["unpaid_lines"] and (now - synced_at).total_seconds() > UNPAID_RESYNC_HOURS * 3600
            )
            if not (daily or unpaid_due):
                continue
            job = resync(db, day)
            resynced.append(day)
            if job.status != "success":
                failed.append(day)
            else:
                after = stored_day_totals(db, day, day).get(day, _empty_totals())
                if after != stored:
                    changed.append(f"{day.isoformat()} {_change_text(stored, after)}")
            _sleep(RESYNC_PAUSE_SECONDS)
        cutoff = utcnow() - dt.timedelta(days=KEEP_CHECK_DAYS)
        db.execute(delete(JobRun).where(JobRun.job_name == RECHECK_JOB, JobRun.started_at < cutoff))
        label = DAILY_PREFIX if daily else "Re-check"
        text = (
            f"{label} without Toast Analytics keys, {start}..{end}: {len(resynced)} day(s) re-synced, "
            f"{len(changed)} changed, {len(failed)} failed"
        )
        if changed:
            text += " - " + "; ".join(changed)
        summary.status = "failed" if failed else "success"
        summary.detail = text[:2000]
    except Exception as exc:  # noqa: BLE001 - recorded, never crashes the scheduler
        db.rollback()
        logger.exception("Sales re-check failed")
        summary.status = "failed"
        summary.detail = ((DAILY_PREFIX if daily else "Re-check") + f" failed: {exc}")[:500]
    finally:
        summary.finished_at = utcnow()
        db.add(summary)
        db.commit()
        db.refresh(summary)
    return summary


def recheck_status(db: Session) -> dict:
    """What the sales double-check is doing, for warnings (parity report,
    accuracy card): {analytics: bool, warning: str|None, last_recheck: ...}."""
    analytics = registry.get_connector().has_day_totals()
    last = db.execute(
        select(JobRun).where(JobRun.job_name == RECHECK_JOB).order_by(JobRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "analytics": bool(analytics),
        "warning": None if analytics else NO_KEYS_WARNING,
        "last_recheck": (
            {
                "started_at": last.started_at.isoformat() if last.started_at else None,
                "status": last.status,
                "detail": last.detail,
            }
            if last is not None
            else None
        ),
    }


def _run(db: Session, days: Optional[int], today: dt.date, resync) -> JobRun:
    start, end = _window(days, today)
    summary = JobRun(job_name=RUN_JOB, status="running")
    db.add(summary)
    db.commit()
    db.refresh(summary)
    counts = {"checked": 0, "match": 0, "fixed": 0, "mismatch": 0, "resynced": 0, "failed": 0}
    try:
        if end < start:
            summary.status = "success"
            summary.detail = "nothing to check yet"
            return summary
        totals = registry.get_connector().get_day_totals(start, end)

        before = stored_day_totals(db, start, end)
        last_sync = _last_successful_sync(db, start, end)
        previous = _previous_checks(db, start, end)
        now = utcnow()

        d = start
        while d <= end:
            day = d
            d += dt.timedelta(days=1)
            stored = before.get(day, _empty_totals())
            if day not in totals and stored["lines"] == 0:
                continue  # closed / no sales on either side
            pos_day = totals.get(day) or SalesDay(business_date=day)
            has_metrics = pos_day.has_day_metrics
            view = _analytics_view(pos_day)
            fp = _fingerprint(view)
            prev = previous.get(day) or {}
            reasons = _differences(stored, view, has_metrics)
            synced_at = last_sync.get(day)
            if synced_at is None:
                reasons.append("never_synced")
            if prev.get("fingerprint") and prev["fingerprint"] != fp:
                reasons.append("analytics_changed")
            if (
                stored["unpaid_lines"]
                and synced_at is not None
                and (now - synced_at).total_seconds() > UNPAID_RESYNC_HOURS * 3600
            ):
                reasons.append("unpaid")

            # Already re-synced against these exact Toast numbers and nothing
            # moved on our side either -> wait for Toast to change.
            waiting = bool(
                reasons
                and prev.get("resynced")
                and prev.get("fingerprint") == fp
                and prev.get("stored_after") == stored
                and "never_synced" not in reasons
                and "unpaid" not in reasons
            )

            detail = {
                "checked_at": now.isoformat(),
                "analytics": view,
                "fingerprint": fp,
                "stored_before": stored,
                "stored_after": stored,
                "reasons": reasons,
                "resynced": False,
                "diff_before": _r2(stored["net"] - view["item_net"]),
                "diff_after": _r2(stored["net"] - view["item_net"]),
            }
            status = "success"
            if reasons and not waiting:
                job = resync(db, day)
                counts["resynced"] += 1
                detail["resynced"] = True
                detail["resync_status"] = job.status
                detail["resync_detail"] = (job.detail or "")[:300]
                if job.status != "success":
                    status = "failed"
                    counts["failed"] += 1
                after = stored_day_totals(db, day, day).get(day, _empty_totals())
                detail["stored_after"] = after
                detail["diff_after"] = _r2(after["net"] - view["item_net"])
                _sleep(RESYNC_PAUSE_SECONDS)
            elif waiting:
                detail["waiting"] = True
                detail["resynced"] = prev.get("resynced", False)

            # The verdict is about the item-level totals (what Items Sold and
            # the forecasts use); an order-count difference alone is shown,
            # and re-synced once, but doesn't make a day "differ".
            after_reasons = _differences(detail["stored_after"], view, has_metrics)
            item_before = [r for r in _differences(stored, view, False)]
            item_after = [r for r in after_reasons if r != "orders"]
            if item_after:
                verdict = "mismatch"
            elif item_before and detail["resynced"] and not waiting:
                verdict = "fixed"
            else:
                verdict = "match"
            detail["verdict"] = verdict
            detail["remaining"] = after_reasons
            counts["checked"] += 1
            counts[verdict] += 1
            _record(db, day, detail, status=status)
            db.commit()

        cutoff = utcnow() - dt.timedelta(days=KEEP_CHECK_DAYS)
        db.execute(delete(JobRun).where(JobRun.job_name.in_([CHECK_JOB, RUN_JOB]), JobRun.started_at < cutoff))
        summary.status = "success" if counts["failed"] == 0 else "failed"
        summary.detail = (
            f"{start}..{end}: {counts['checked']} day(s) checked, {counts['match']} match, "
            f"{counts['fixed']} fixed, {counts['mismatch']} still differ; "
            f"{counts['resynced']} re-synced, {counts['failed']} failed"
        )
    except DayTotalsUnavailable as exc:
        # "Toast Analytics: <reason>" (the connector's label), as always.
        db.rollback()
        summary.status = "failed"
        summary.detail = str(exc)
        logger.warning("Sales accuracy re-pull: %s", exc.cause)
    except Exception as exc:  # noqa: BLE001 - recorded, never crashes the scheduler
        db.rollback()
        logger.exception("Sales accuracy re-pull failed")
        summary.status = "failed"
        summary.detail = str(exc)[:500]
    finally:
        summary.finished_at = utcnow()
        db.add(summary)
        db.commit()
        db.refresh(summary)
    return summary


def accuracy_report(db: Session, days: int = 30) -> dict:
    """For the admin view: the latest comparison per business date in the
    last `days` days, newest first, plus the last runs."""
    days = max(1, min(int(days), KEEP_CHECK_DAYS))
    today = restaurant_today()
    start = today - dt.timedelta(days=days)
    previous = _previous_checks(db, start, today)
    rows = []
    for day in sorted(previous, reverse=True):
        det = previous[day]
        a = det.get("analytics") or {}
        before = det.get("stored_before") or {}
        after = det.get("stored_after") or {}
        rows.append(
            {
                "business_date": day.isoformat(),
                "checked_at": det.get("checked_at"),
                "verdict": det.get("verdict"),
                "reasons": det.get("reasons") or [],
                "remaining": det.get("remaining") or [],
                "resynced": bool(det.get("resynced")),
                "waiting": bool(det.get("waiting")),
                "resync_status": det.get("resync_status"),
                "toast_item_net": a.get("item_net"),
                "toast_item_qty": a.get("item_qty"),
                "toast_net": a.get("net"),
                "toast_orders": a.get("orders"),
                "toast_discount": a.get("discount"),
                "toast_refund": a.get("refund"),
                "ours_before": before.get("net"),
                "ours_after": after.get("net"),
                "orders_ours": after.get("orders"),
                "diff_before": det.get("diff_before"),
                "diff_after": det.get("diff_after"),
            }
        )
    runs = (
        db.execute(select(JobRun).where(JobRun.job_name == RUN_JOB).order_by(JobRun.id.desc()).limit(5))
        .scalars()
        .all()
    )
    settings = get_settings()
    status = recheck_status(db)
    return {
        "configured": registry.get_connector().has_day_totals(),
        # Additive (pre-production): without the keys the re-check runs and
        # this plain line says so; None when the keys are there.
        "warning": status["warning"],
        "last_recheck": status["last_recheck"],
        "enabled": settings.sales_repull_enabled and settings.scheduler_enabled,
        "running": is_running(),
        "window_days": settings.sales_repull_days,
        "times": [t.strip() for t in settings.sales_repull_times.split(",") if t.strip()],
        "runs": [
            {
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "status": r.status,
                "detail": r.detail,
            }
            for r in runs
        ],
        "days": rows,
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
