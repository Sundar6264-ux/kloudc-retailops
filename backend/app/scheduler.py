"""Daily cron job wiring, via APScheduler running in-process with the API server.

Chosen over OS-level cron/node-cron because the job needs to write into the same
SQLite DB and be inspectable from the API (job status endpoint) without shelling
out or reading log files. The tradeoff: the scheduler only runs while the FastAPI
process is up, so the process needs to be kept alive (e.g. under systemd/pm2/a
container restart policy) for the daily job to fire reliably.
"""

import datetime as dt
import logging
import zoneinfo
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import JobRun
from app.pos.capabilities import Capability
from app.pos.gating import drop_unsupported_jobs, has_capability
from app.services.fix_issues import warm_fix_issues_cache
from app.services.notification_triggers import CART_REMOVAL_CHECK_MINUTES, OPEN_ORDER_SUMMARY_TIMES, run_alert_job
from app.services.open_orders import refresh_open_orders_cache
from app.services.sales_sync import JOB_NAME, sync_sales_for_date

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None
_notification_scheduler: Optional[BackgroundScheduler] = None
_team_scheduler: Optional[BackgroundScheduler] = None


def _dates_without_successful_sync(db: Session, start: dt.date, end: dt.date) -> list[dt.date]:
    """Every date in [start, end] that does NOT have a successful sales-sync run yet.

    A date can have a failed run (e.g. Toast rate-limited us) and still show up
    here - "missing" means "not yet confirmed synced", not "never attempted".
    """
    stmt = select(JobRun.business_date).where(
        JobRun.job_name == JOB_NAME,
        JobRun.status == "success",
        JobRun.business_date >= start,
        JobRun.business_date <= end,
    )
    synced = {row[0] for row in db.execute(stmt)}

    missing = []
    d = start
    while d <= end:
        if d not in synced:
            missing.append(d)
        d += dt.timedelta(days=1)
    return missing


def run_daily_sales_sync() -> None:
    """Runs at the scheduled hour, AND once right after the service starts up.

    Rather than only syncing "yesterday", this scans the trailing
    `sales_sync_backfill_days` window and (re-)syncs every date in it that
    doesn't have a confirmed-successful run - so a missed night (service was
    down, Toast had an outage, etc.) gets caught up automatically on the next
    run instead of silently staying missing forever.
    """
    settings = get_settings()
    tz = zoneinfo.ZoneInfo(settings.store_timezone)
    # "Yesterday" in the restaurant's local timezone - the most recent business
    # date that's actually closed out by the time this runs.
    through_date = (dt.datetime.now(tz) - dt.timedelta(days=1)).date()
    window_start = through_date - dt.timedelta(days=settings.sales_sync_backfill_days - 1)

    db = SessionLocal()
    try:
        missing_dates = _dates_without_successful_sync(db, window_start, through_date)
        if not missing_dates:
            logger.info("Sales sync: %s already up to date, nothing to backfill", through_date)
            return

        if len(missing_dates) > 1:
            logger.warning(
                "Sales sync: %d missing day(s) in the last %d days, backfilling: %s",
                len(missing_dates), settings.sales_sync_backfill_days, missing_dates,
            )

        for business_date in missing_dates:
            job = sync_sales_for_date(db, business_date)
            logger.info("Sales sync for %s: %s (%s)", business_date, job.status, job.detail)
    finally:
        db.close()
    # New sales change the Fix Issues Slow / Dead forecast: recompute it now
    # rather than on someone's first page load. Never raises.
    warm_fix_issues_cache(refresh=True)


def run_sales_repull_job() -> None:
    """Sales accuracy re-pull (services/sales_accuracy.py): compare the last
    SALES_REPULL_DAYS business dates with Toast Analytics and re-sync only the
    dates that changed. A no-op without Analytics credentials."""
    from app.services.sales_accuracy import run_sales_repull

    db = SessionLocal()
    try:
        job = run_sales_repull(db)
        if job is not None:
            logger.info("Sales accuracy re-pull: %s (%s)", job.status, job.detail)
    finally:
        db.close()


def _repull_times(raw: str) -> list:
    """"06:30,12:30" -> [(6, 30), (12, 30)]; bad entries are skipped."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour, minute = (int(x) for x in part.split(":", 1))
        except ValueError:
            logger.warning("SALES_REPULL_TIMES: ignoring %r (want HH:MM)", part)
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            out.append((hour, minute))
    return out


def run_open_orders_full_scan() -> None:
    """Re-scans every business date since real sales began for still-open
    orders (see open_orders.py::FIRST_REAL_SALES_DATE) and fully replaces
    `open_order_cache`. This is the only path that can notice an order
    stuck open on some OLD date - the frequent today-only refresh below
    never looks at a date again once it's no longer "today". Slow (minutes,
    ~100+ Toast calls), which is exactly why it only runs once a day rather
    than on every dashboard load.
    """
    db = SessionLocal()
    try:
        job = refresh_open_orders_cache(db, full=True)
        logger.info("Open orders full scan: %s (%s)", job.status, job.detail)
    finally:
        db.close()


def run_open_orders_today_refresh() -> None:
    """Cheap refresh (one Toast call) of just today's open orders - covers
    the vast majority of real changes (an order opening or closing) without
    the cost of the full historical scan above.
    """
    db = SessionLocal()
    try:
        job = refresh_open_orders_cache(db, full=False)
        logger.info("Open orders today refresh: %s (%s)", job.status, job.detail)
    finally:
        db.close()


def run_open_orders_summary() -> None:
    """Refresh today's open orders from Toast, then send the opening /
    mid-day / closing summary. A failed refresh still sends the summary from
    the last good cache (at most one refresh interval old)."""
    try:
        run_open_orders_today_refresh()
    except Exception:
        logger.exception("Open orders summary: refresh before the summary failed")
    run_alert_job("open_orders_morning")


def run_inventory_api_snapshot() -> None:
    """04:05: the inventory snapshot from the Toast API (services/
    inventory_api.py). Writes it only with the switch on (the Inventory from
    Toast page's setting, else INVENTORY_API_SNAPSHOTS - read at run time) and no
    CSV for today; always records a side-by-side comparison with today's
    CSV when there is one. Waits (up to ~20 min) for the 04:00 sales sync of
    yesterday, since the 7/30/90-day sales are computed from it."""
    import time as _time

    from app.services.inventory_api import rerun_for_late_csv, run_api_snapshot

    yesterday = dt.datetime.now(zoneinfo.ZoneInfo(get_settings().store_timezone)).date() - dt.timedelta(days=1)
    for _ in range(20):
        db = SessionLocal()
        try:
            if not _dates_without_successful_sync(db, yesterday, yesterday):
                break
        finally:
            db.close()
        _time.sleep(60)
    db = SessionLocal()
    try:
        job = run_api_snapshot(db)
        logger.info("Inventory API snapshot: %s", job.status)
        # A CSV uploaded while this run waited for Toast missed it: compare
        # again now (the upload's own re-run skipped a running date).
        rerun_for_late_csv(db, job.business_date)
    except Exception:
        logger.exception("Inventory API snapshot: late CSV check failed")
    finally:
        db.close()


def run_menu_barcode_recheck() -> None:
    """Hourly: Toast menu metadata, then the full menu only if it changed
    (services/inventory_api.py, contract item 6)."""
    from app.services.inventory_api import refresh_menu_cache

    db = SessionLocal()
    try:
        job = refresh_menu_cache(db)
        logger.info("Menu barcode re-check: %s (%s)", job.status, job.detail)
    finally:
        db.close()


def run_insights_nightly() -> None:
    """04:30 (after the 04:00 closeout and sales sync): Toast Analytics
    reports for Insights (services/insights_data.py) - yesterday, re-pulls
    of recent days that changed, a little more history, the menu map, then
    Loss watch / Odd checks flags. A no-op without Analytics credentials."""
    from app.services.insights_data import run_nightly

    db = SessionLocal()
    try:
        job = run_nightly(db)
        if job is not None:
            logger.info("Insights nightly: %s", job.status)
    finally:
        db.close()


def run_insights_catchup() -> None:
    """Hourly 05:20-23:20: reads the last two closed days of each Insights
    report that are missing (e.g. refused for Toast's daily limit) as soon as
    the budget frees. Checks the cache first: no Toast call when nothing is
    missing (services/insights_data.run_catchup)."""
    from app.services.insights_data import run_catchup

    db = SessionLocal()
    try:
        run_catchup(db)
    finally:
        db.close()


def run_morning_summary() -> None:
    """09:00: one Morning summary push per person instead of ~6 separate
    ones (notification_triggers.run_morning_job, which also sends each 9 AM
    alert on its own to whoever turned it on). Refreshes today's open orders
    first, as the opening open-checks summary did; the Fix Issues reopen
    honours SCHEDULER_ENABLED, as run_fix_issues_reopened does."""
    from app.services.notification_triggers import run_morning_job

    try:
        if has_capability(Capability.OPEN_ORDERS):  # multi-store MT6; always true for Toast
            run_open_orders_today_refresh()
    except Exception:
        logger.exception("Morning summary: open orders refresh failed")
    run_morning_job(include_fix_issues=get_settings().scheduler_enabled)


def run_fix_issues_reopened() -> None:
    """Move Fix Issues "more time" closes whose week is up back to open,
    then one combined push (alert fix_issues_reopened). Not scheduled since
    Oct 15 - run_morning_summary does this at 09:00; kept for manual runs. Lives on the
    notification scheduler (NOTIFICATIONS_ENABLED) and also honours
    SCHEDULER_ENABLED, like the contract asks. With either off, the reopen
    still happens lazily whenever the list is read."""
    if not get_settings().scheduler_enabled:
        logger.info("fix_issues_reopened skipped (SCHEDULER_ENABLED=false); lists reopen lazily on read")
        return
    run_alert_job("fix_issues_reopened")


def start_scheduler() -> Optional[BackgroundScheduler]:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false) - no scheduled jobs will run")
        return None
    tz = zoneinfo.ZoneInfo(settings.store_timezone)

    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        run_daily_sales_sync,
        trigger=CronTrigger(hour=settings.sales_sync_hour, minute=settings.sales_sync_minute, timezone=tz),
        id="daily_sales_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # Also run the same (gap-aware) sync once shortly after the service starts up -
    # a restart (crash, reboot, Mac was asleep at 4am) is exactly when a day is
    # most likely to have been missed, and otherwise it'd sit missing until the
    # next scheduled run, up to ~24h later.
    scheduler.add_job(
        run_daily_sales_sync,
        trigger=DateTrigger(run_date=dt.datetime.now(tz) + dt.timedelta(seconds=10)),
        id="startup_catchup_sync",
        replace_existing=True,
    )

    # Open orders cache: one full history sweep a day, plus a frequent
    # cheap today-only refresh (see run_open_orders_*'s own docstrings for
    # why it's split this way).
    scheduler.add_job(
        run_open_orders_full_scan,
        trigger=CronTrigger(
            hour=settings.open_orders_full_scan_hour,
            minute=settings.open_orders_full_scan_minute,
            timezone=tz,
        ),
        id="open_orders_full_scan",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_open_orders_today_refresh,
        trigger=IntervalTrigger(minutes=settings.open_orders_today_refresh_minutes),
        id="open_orders_today_refresh",
        replace_existing=True,
        # At a 1-minute interval (OPEN_ORDERS_TODAY_REFRESH_MINUTES=1 in
        # prod) a slow Toast response must never stack runs: one at a time,
        # and missed ticks collapse into a single catch-up run.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    # Populate the cache shortly after startup too, same reasoning as the
    # sales-sync catchup above - otherwise a freshly restarted service shows
    # an empty "all-time" view until the next scheduled tick (up to 24h for
    # the full scan). Runs the cheap today-only refresh immediately (fast,
    # so the dashboard has *something* right away) and the full scan a bit
    # after that (slow, shouldn't block/compete with startup).
    scheduler.add_job(
        run_open_orders_today_refresh,
        trigger=DateTrigger(run_date=dt.datetime.now(tz) + dt.timedelta(seconds=15)),
        id="startup_open_orders_today",
        replace_existing=True,
    )
    scheduler.add_job(
        run_open_orders_full_scan,
        trigger=DateTrigger(run_date=dt.datetime.now(tz) + dt.timedelta(seconds=30)),
        id="startup_open_orders_full_scan",
        replace_existing=True,
    )

    # Sales accuracy re-pull: a few times a day (after close, then while the
    # day's edits land in Toast Analytics). Nothing at startup - it's not
    # time-critical and Analytics has tight hourly limits.
    if settings.sales_repull_enabled:
        for hour, minute in _repull_times(settings.sales_repull_times):
            scheduler.add_job(
                run_sales_repull_job,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
                id=f"sales_repull_{hour:02d}{minute:02d}",
                replace_existing=True,
                misfire_grace_time=1800,
                coalesce=True,
                max_instances=1,
            )

    # Insights from Toast Analytics: one nightly fill after the closeout.
    if settings.insights_nightly_enabled:
        scheduler.add_job(
            run_insights_nightly,
            trigger=CronTrigger(
                hour=settings.insights_nightly_hour, minute=settings.insights_nightly_minute, timezone=tz
            ),
            id="insights_nightly",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        from app.services.insights_data import CATCHUP_HOURS, CATCHUP_MINUTE

        scheduler.add_job(
            run_insights_catchup,
            trigger=CronTrigger(
                hour=",".join(str(h) for h in CATCHUP_HOURS), minute=CATCHUP_MINUTE, timezone=tz
            ),
            id="insights_catchup",
            replace_existing=True,
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
        )

    # Inventory from the Toast API (contract items 1 and 6): the nightly
    # snapshot / side-by-side comparison, and the hourly barcode re-check.
    scheduler.add_job(
        run_inventory_api_snapshot,
        trigger=CronTrigger(
            hour=settings.inventory_api_snapshot_hour, minute=settings.inventory_api_snapshot_minute, timezone=tz
        ),
        id="inventory_api_snapshot",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    if settings.menu_barcode_recheck_minutes > 0:
        scheduler.add_job(
            run_menu_barcode_recheck,
            trigger=IntervalTrigger(minutes=settings.menu_barcode_recheck_minutes),
            id="menu_barcode_recheck",
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )

    # Fix Issues' Slow / Dead forecast is kept in memory, so a restart
    # empties it: work it out shortly after startup (after the sales
    # catch-up above has had a head start) instead of on the first page load.
    scheduler.add_job(
        warm_fix_issues_cache,
        trigger=DateTrigger(run_date=dt.datetime.now(tz) + dt.timedelta(seconds=20)),
        id="startup_fix_issues_warmup",
        replace_existing=True,
    )

    # Multi-store MT6: a POS job is registered only when the store's POS has
    # what it needs (app/pos/gating.py). Removes nothing for a Toast store.
    drop_unsupported_jobs(scheduler)
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started: daily_sales_sync at %02d:%02d, open_orders_full_scan at %02d:%02d, "
        "open_orders_today_refresh every %dmin (%s)",
        settings.sales_sync_hour,
        settings.sales_sync_minute,
        settings.open_orders_full_scan_hour,
        settings.open_orders_full_scan_minute,
        settings.open_orders_today_refresh_minutes,
        settings.store_timezone,
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def start_notification_scheduler() -> Optional[BackgroundScheduler]:
    """Push-notification alert jobs (services/notification_triggers.py), in
    their own scheduler so they're governed by NOTIFICATIONS_ENABLED rather
    than SCHEDULER_ENABLED: none of them call Toast, so the dev instance -
    which turns the main scheduler off to protect the Toast rate limit - can
    still run them. Quiet hours are enforced inside run_alert_job itself, so
    a schedule here can never send at night by mistake.
    """
    global _notification_scheduler
    if _notification_scheduler is not None:
        return _notification_scheduler

    settings = get_settings()
    if not settings.notifications_enabled:
        logger.info("Notification scheduler disabled (NOTIFICATIONS_ENABLED=false)")
        return None
    tz = zoneinfo.ZoneInfo(settings.store_timezone)

    scheduler = BackgroundScheduler(timezone=tz)
    # The 9:00 alerts (order_due, critical_vendors, inventory_upload_due,
    # inventory_api_ready, loss_watch, daily_sales_summary, fix_issues_reopened,
    # the opening open-checks count) all run in run_morning_summary below.
    jobs = [
        ("closing_checklist", CronTrigger(hour=21, minute=30, timezone=tz), 1200),
        ("invoice_failed", IntervalTrigger(minutes=10), 300),
        ("cart_removal_request", IntervalTrigger(minutes=CART_REMOVAL_CHECK_MINUTES), 300),
    ]
    for key, trigger, grace in jobs:
        scheduler.add_job(
            run_alert_job,
            trigger=trigger,
            args=[key],
            id=f"notify_{key}",
            replace_existing=True,
            misfire_grace_time=grace,
            coalesce=True,
        )
    scheduler.add_job(
        run_morning_summary,
        trigger=CronTrigger(hour=9, minute=0, timezone=tz),
        id="notify_morning_summary",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    # Open orders: mid-day / closing summaries (the opening one is in the
    # morning job), each after a fresh today refresh so the count is current.
    for hour, minute in OPEN_ORDER_SUMMARY_TIMES:
        if (hour, minute) == (9, 0):
            continue
        scheduler.add_job(
            run_open_orders_summary,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=f"notify_open_orders_{hour:02d}{minute:02d}",
            replace_existing=True,
            misfire_grace_time=1800,
            coalesce=True,
        )
    drop_unsupported_jobs(scheduler)  # multi-store MT6, as in start_scheduler
    scheduler.start()
    _notification_scheduler = scheduler
    logger.info(
        "Notification scheduler started: morning summary (+ its opt-in 9 AM alerts) 09:00, closing_checklist 21:30, invoice_failed + cart_removal_request every 10min, open orders summaries 16:00 / 21:30; "
        "quiet hours 22:00-07:00 (%s)",
        settings.store_timezone,
    )
    return scheduler


def start_team_scheduler() -> Optional[BackgroundScheduler]:
    """Team hub jobs (change-feed prune, recurring tasks, reminders) - every
    job registered in app/team/jobs.py. Governed by TEAM_JOBS_ENABLED, not
    SCHEDULER_ENABLED: none of them call Toast, so dev runs them too.
    """
    global _team_scheduler
    if _team_scheduler is not None:
        return _team_scheduler

    settings = get_settings()
    if not settings.team_jobs_enabled:
        logger.info("Team scheduler disabled (TEAM_JOBS_ENABLED=false)")
        return None
    from app.team.jobs import TEAM_JOBS

    tz = zoneinfo.ZoneInfo(settings.store_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    for job in TEAM_JOBS:
        scheduler.add_job(
            job.func,
            trigger=job.trigger(tz),
            id=f"team_{job.id}",
            replace_existing=True,
            misfire_grace_time=job.misfire_grace_time,
            coalesce=True,
        )
    scheduler.start()
    _team_scheduler = scheduler
    logger.info("Team scheduler started: %s", ", ".join(j.id for j in TEAM_JOBS))
    return scheduler


def shutdown_team_scheduler() -> None:
    global _team_scheduler
    if _team_scheduler is not None:
        _team_scheduler.shutdown(wait=False)
        _team_scheduler = None


def shutdown_notification_scheduler() -> None:
    global _notification_scheduler
    if _notification_scheduler is not None:
        _notification_scheduler.shutdown(wait=False)
        _notification_scheduler = None


# =====================================================================================
# Multi mode (TENANCY_MODE=multi; docs/PLATFORM_CONTRACT.md 6.2)
# =====================================================================================
# The same jobs as above, but for every store: time-of-day jobs are declared
# once per store (job id "<job>:<CODE>", each store's own timezone and
# settings, a 0-120 s per-store offset), interval jobs are registered once
# and run for every store on each tick, the startup catch-ups run per store
# when the scheduler first sees it. Only the leader process (advisory lock,
# app/tenancy/leader.py) starts these schedulers. Single mode above is
# unchanged.

_platform_scheduler: Optional[BackgroundScheduler] = None


def _store_tz():
    return zoneinfo.ZoneInfo(get_settings().store_timezone)


def _cron(hour, minute):
    """A trigger factory: hour:minute in the current store's timezone,
    hour/minute read from the store's settings when given as callables."""

    def make(_ref):
        s = get_settings()
        h = hour(s) if callable(hour) else hour
        m = minute(s) if callable(minute) else minute
        return CronTrigger(hour=h, minute=m, timezone=_store_tz())

    return make


def _alert_job(key: str):
    """run_alert_job(key) as a no-argument job that keeps the name
    run_alert_job (POS gating reads job function names)."""
    import functools

    @functools.wraps(run_alert_job)
    def job() -> None:
        run_alert_job(key)

    return job


def _sales_repull_trigger(_ref):
    s = get_settings()
    if not s.sales_repull_enabled:
        return None
    times = _repull_times(s.sales_repull_times)
    if not times:
        return None
    from apscheduler.triggers.combining import OrTrigger

    tz = _store_tz()
    triggers = [CronTrigger(hour=h, minute=m, timezone=tz) for h, m in times]
    return triggers[0] if len(triggers) == 1 else OrTrigger(triggers)


def _insights_trigger(_ref):
    s = get_settings()
    if not s.insights_nightly_enabled:
        return None
    return CronTrigger(hour=s.insights_nightly_hour, minute=s.insights_nightly_minute, timezone=_store_tz())


def _insights_catchup_trigger(_ref):
    if not get_settings().insights_nightly_enabled:
        return None
    from app.services.insights_data import CATCHUP_HOURS, CATCHUP_MINUTE

    return CronTrigger(hour=",".join(str(h) for h in CATCHUP_HOURS), minute=CATCHUP_MINUTE, timezone=_store_tz())


def declare_main_store_jobs() -> None:
    from app.tenancy import register_store_job, register_store_startup_job

    register_store_job(
        "daily_sales_sync",
        run_daily_sales_sync,
        _cron(lambda s: s.sales_sync_hour, lambda s: s.sales_sync_minute),
        misfire_grace_time=3600,
    )
    register_store_job(
        "open_orders_full_scan",
        run_open_orders_full_scan,
        _cron(lambda s: s.open_orders_full_scan_hour, lambda s: s.open_orders_full_scan_minute),
        misfire_grace_time=3600,
    )
    register_store_job("sales_repull", run_sales_repull_job, _sales_repull_trigger, misfire_grace_time=1800)
    register_store_job("insights_nightly", run_insights_nightly, _insights_trigger, misfire_grace_time=3600)
    register_store_job("insights_catchup", run_insights_catchup, _insights_catchup_trigger, misfire_grace_time=1800)
    register_store_job(
        "inventory_api_snapshot",
        run_inventory_api_snapshot,
        _cron(lambda s: s.inventory_api_snapshot_hour, lambda s: s.inventory_api_snapshot_minute),
        misfire_grace_time=3600,
    )
    register_store_startup_job("startup_catchup_sync", run_daily_sales_sync, 10)
    register_store_startup_job("startup_open_orders_today", run_open_orders_today_refresh, 15)
    register_store_startup_job("startup_fix_issues_warmup", warm_fix_issues_cache, 20)
    register_store_startup_job("startup_open_orders_full_scan", run_open_orders_full_scan, 30)


def declare_notification_store_jobs() -> None:
    from app.tenancy import register_store_job

    register_store_job(
        "notify_closing_checklist",
        _alert_job("closing_checklist"),
        _cron(21, 30),
        scheduler="notifications",
        misfire_grace_time=1200,
        max_instances=1,
    )
    register_store_job(
        "notify_morning_summary", run_morning_summary, _cron(9, 0), scheduler="notifications", misfire_grace_time=3600
    )
    for hour, minute in OPEN_ORDER_SUMMARY_TIMES:
        if (hour, minute) == (9, 0):
            continue
        register_store_job(
            f"notify_open_orders_{hour:02d}{minute:02d}",
            run_open_orders_summary,
            _cron(hour, minute),
            scheduler="notifications",
            misfire_grace_time=1800,
        )


def declare_team_store_jobs() -> None:
    from app.team.jobs import TEAM_JOBS
    from app.tenancy import register_store_job

    for job in TEAM_JOBS:
        register_store_job(
            f"team_{job.id}",
            job.func,
            (lambda _ref, job=job: job.trigger(_store_tz())),
            scheduler="team",
            misfire_grace_time=job.misfire_grace_time,
        )


def declare_all_store_jobs() -> None:
    """Every per-store job declaration (a worker process runs queued
    ``store_job`` rows by job id without starting any scheduler)."""
    declare_main_store_jobs()
    declare_notification_store_jobs()
    declare_team_store_jobs()


def _periodic_enqueuer(kind: str):
    """A scheduler job that hands daily job `kind` to app.jobqueue (a worker
    in queue mode, a thread inline). Per-store: runs inside the store's
    context (store_job_runner), so the job is the store's."""

    def run() -> None:
        from app import jobqueue
        from app.tenancy import current_store_code

        code = current_store_code()
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        try:
            jobqueue.enqueue(kind, {}, code=code, dedupe_key=f"periodic:{kind}:{code or '-'}:{day}")
        except Exception:  # noqa: BLE001 - tomorrow's run tries again
            logger.exception("Daily job %s not queued (store %s)", kind, code)

    run.__name__ = f"periodic_{kind}"
    return run


def add_periodic_jobs(platform: BackgroundScheduler) -> int:
    """app.jobqueue.register_periodic jobs: per-store ones as store jobs on
    the "platform" scheduler (store time zone), platform ones as UTC cron
    jobs. Returns how many kinds were added."""
    from app.jobqueue import periodic_jobs
    from app.jobqueue.kinds import load_all
    from app.tenancy import register_store_job

    load_all()
    n = 0
    for kind, spec in sorted(periodic_jobs().items()):
        if spec.per_store:
            register_store_job(
                f"periodic_{kind}", _periodic_enqueuer(kind), _cron(spec.hour, spec.minute),
                scheduler="platform", misfire_grace_time=3600,
            )
        else:
            platform.add_job(
                _periodic_enqueuer(kind),
                trigger=CronTrigger(hour=spec.hour, minute=spec.minute, timezone=dt.timezone.utc),
                id=f"periodic_{kind}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
        n += 1
    return n


def _new_multi_scheduler() -> BackgroundScheduler:
    from apscheduler.executors.pool import ThreadPoolExecutor

    workers = max(1, int(get_settings().scheduler_max_workers))
    return BackgroundScheduler(
        timezone=dt.timezone.utc,
        executors={"default": ThreadPoolExecutor(max_workers=workers)},
        job_defaults={"coalesce": True, "max_instances": 1},
    )


def start_multi_tenant_schedulers() -> dict:
    """Multi mode, on the leader: build and start the schedulers with every
    store's jobs. Returns {name: scheduler} of the ones started."""
    global _scheduler, _notification_scheduler, _team_scheduler, _platform_scheduler
    from app.tenancy import each_store_job, reconcile_store_jobs, sweep_idle_engines
    from app.tenancy.jobs import RECONCILE_EVERY_MINUTES, attach_scheduler, unregister_store_jobs

    shutdown_multi_tenant_schedulers()
    settings = get_settings()  # platform level: no store selected here
    unregister_store_jobs()
    started = {}
    if settings.scheduler_enabled:
        main = _new_multi_scheduler()
        declare_main_store_jobs()
        main.add_job(
            each_store_job(run_open_orders_today_refresh),
            trigger=IntervalTrigger(minutes=settings.open_orders_today_refresh_minutes),
            id="open_orders_today_refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        if settings.menu_barcode_recheck_minutes > 0:
            main.add_job(
                each_store_job(run_menu_barcode_recheck),
                trigger=IntervalTrigger(minutes=settings.menu_barcode_recheck_minutes),
                id="menu_barcode_recheck",
                replace_existing=True,
                misfire_grace_time=600,
                coalesce=True,
                max_instances=1,
            )
        attach_scheduler("main", main)
        _scheduler = started["main"] = main
    else:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false) - no scheduled store jobs will run")
    if settings.notifications_enabled:
        notif = _new_multi_scheduler()
        declare_notification_store_jobs()
        for key, minutes in (("invoice_failed", 10), ("cart_removal_request", CART_REMOVAL_CHECK_MINUTES)):
            notif.add_job(
                each_store_job(_alert_job(key)),
                trigger=IntervalTrigger(minutes=minutes),
                id=f"notify_{key}",
                replace_existing=True,
                misfire_grace_time=300,
                coalesce=True,
                max_instances=1,
            )
        attach_scheduler("notifications", notif)
        _notification_scheduler = started["notifications"] = notif
    if settings.team_jobs_enabled:
        team = _new_multi_scheduler()
        declare_team_store_jobs()
        attach_scheduler("team", team)
        _team_scheduler = started["team"] = team
    platform = BackgroundScheduler(timezone=dt.timezone.utc)
    platform.add_job(
        reconcile_store_jobs,
        trigger=IntervalTrigger(minutes=RECONCILE_EVERY_MINUTES),
        id="tenancy_reconcile_store_jobs",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    platform.add_job(
        sweep_idle_engines,
        trigger=IntervalTrigger(minutes=1),
        id="tenancy_sweep_idle_engines",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # Control-plane jobs (track C, app/platform/jobs.py): once per
    # deployment, no store context, cron in UTC.
    from app.platform.jobs import PLATFORM_JOBS

    for job_id, func, cron in PLATFORM_JOBS:
        platform.add_job(
            func,
            trigger=CronTrigger(timezone=dt.timezone.utc, **cron),
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
    # Daily jobs of app.jobqueue (track B's usage gauges and purge, the
    # queue's own maintenance): enqueued here, run by a worker in queue mode.
    try:
        add_periodic_jobs(platform)
    except Exception:  # noqa: BLE001 - a broken job module must not stop the store jobs
        logger.exception("Daily jobs (app.jobqueue) not scheduled")
    attach_scheduler("platform", platform)
    _platform_scheduler = started["platform"] = platform
    try:
        stats = reconcile_store_jobs()
        logger.info("Store jobs: %s", stats)
    except Exception:  # noqa: BLE001 - the 5-minute reconcile retries
        logger.exception("Store jobs: first reconcile failed")
    for sched in started.values():
        sched.start()
    return started


def shutdown_multi_tenant_schedulers() -> None:
    global _scheduler, _notification_scheduler, _team_scheduler, _platform_scheduler
    from app.tenancy.jobs import detach_scheduler

    detach_scheduler()
    for sched in (_scheduler, _notification_scheduler, _team_scheduler, _platform_scheduler):
        if sched is not None and getattr(sched, "running", False):
            sched.shutdown(wait=False)
    _scheduler = _notification_scheduler = _team_scheduler = _platform_scheduler = None
