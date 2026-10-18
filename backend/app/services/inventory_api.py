"""Inventory from the Toast API (docs/AUTOMATION_BATCH_CONTRACT.md items 1
and 6, plus the live stock overlay).

Three things live here, all read-only against Toast (GET only):

1. **API inventory snapshot** (item 1). `build_api_rows` turns
   `/stock/v1/inventory` + `/menus/v2/menus` + our own `orders` table into
   the same fields a Toast "retail items" CSV upload stores, and
   `store_api_snapshot` writes them as an `inventory_snapshots` set with
   `source_filename = "api:<iso time>"`. Rules, each checked against the real
   Oct 11 2026 CSV (see the contract's "As built" note for the numbers):
   - quantity: QUANTITY -> the count; OUT_OF_STOCK (Toast gives no count, it
     covers 0 AND negative) -> 0 with `api_out_of_stock` set and
     `csv_quantity` = the item's count in the latest CSV, so Negative
     inventory keeps the CSV's negative count (`effective_quantity`);
     an item missing from the stock list isn't count-tracked -> NULL, as the
     CSV has it ("in stock" with no number).
   - name: the catalog product name for an Each/Container variant (the menu
     item is "X - Each" / "X - Container", the CSV says "X"; a " - Each" /
     " - Container" suffix pair counts as a variant too); otherwise the CSV's
     own spelling while it still matches the menu name with or without the
     suffix, else the menu name.
   - price: NULL for size/open pricing, for a $0 price (Container rows are
     $0 in the menu, so the existing Container pairing - priced row + bare
     row sharing a name - keeps working), and for menu-specific pricing that
     differs between menus; else the menu price.
   - barcode: the menu `sku` holds only one barcode, so the CSV's ";"-list is
     kept while the sku is one of its codes; a different sku replaces it; an
     empty sku never wipes a CSV barcode (see merged_barcode).
   - category group / category / subcategory: the CSV's own placement when it
     is still one of the item's menu locations, else the deepest non-Container
     menu path (menu / group / subgroup).
   - cost, supplier, supplier_item_id, last_received_from,
     inventory_last_received: Toast's API doesn't serve them - carried forward
     from the item's latest CSV row (none for a brand-new item).
   - last 7/30/90 day orders (UNITS) and sales (DOLLARS, net_price): computed
     from `orders`, top-level non-voided lines (gotcha #11), business days
     D-N .. D-1. Toast's CSV figures run two days behind that (they match
     D-N-2 .. D-3 for ~95% of items), so ours are more current by design.
   - inventory_cost = qty x cost, inventory_value = qty x price, gross_margin
     = (price - cost) / price x 100, gross_profit = price - cost (the CSV's
     own relationships); days on hand = qty / (90-day units / 90), 0 at 0 or
     below.
   The nightly job (04:05 store time, after Toast's 4 AM closeout and the
   04:00 sales sync) only WRITES when INVENTORY_API_SNAPSHOTS is on, never
   over a CSV for the same date (CSV wins: `inventory_parser` also deletes a
   date's API rows when a CSV for that date is uploaded). Switch on or off it
   builds the set in memory and records a side-by-side comparison with the
   same day's CSV (`compare_rows`) - the owner's side-by-side week.

2. **Hourly barcode re-check** (item 6). `refresh_menu_cache` asks
   `/menus/v2/metadata` for lastUpdated and pulls `/menus/v2/menus` only when
   it changed, into `toast_menu_items`. `barcode_overlay` then gives the
   missing / invalid barcode reports the menu's current barcode for items
   whose menu data is newer than the latest snapshot, so a fixed item leaves
   the list before the next upload and a "fixed" close is confirmed
   (fix_issues.py uses `menu_recheck_moment` for came-back / confirmed).

3. **Live stock overlay**. With INVENTORY_API_SNAPSHOTS on, Supplier
   Projection, Reorder Candidates (and everything built on them: the critical
   vendors push, Vendor Calendar urgency, the weekly digest) and Fix Issues'
   Negative inventory / Refill from container read the current
   `/stock/v1/inventory` count on top of the latest snapshot
   (`overlay_live_stock`), cached LIVE_STOCK_CACHE_SECONDS (default 300).
   Any Toast failure quietly falls back to the snapshot.
"""

import datetime as dt
import json
import logging
import re
import threading  # noqa: F401 - an old name tests/compat still resolve
import time
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import httpx
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import fsum, group_order
from app.models import InventorySnapshot, JobRun, Order, ToastMenuItem
from app.services import inventory_api_switch
from app.timeutil import restaurant_timezone, restaurant_today
from app.tenancy import tenant_local, tenant_lock

logger = logging.getLogger(__name__)

API_SOURCE_PREFIX = "api:"
JOB_SNAPSHOT = "inventory_api_snapshot"
JOB_MENU_RECHECK = "menu_barcode_recheck"

SALES_WINDOWS = (7, 30, 90)
# Fields carried from the item's latest CSV row (no Toast API source).
CARRIED_FIELDS = ("cost", "supplier", "supplier_item_id", "last_received_from", "inventory_last_received")

# ---------------------------------------------------------------------------
# Multi-store M1 batch MT3: building the API rows, the comparison, storing
# the set, the nightly / hourly jobs and the live stock overlay are
# POS-neutral now (app/pos/sync/catalog.py, fed Item / StockLevel models by
# the store's connector). The functions here that take Toast JSON (or a Toast
# fetch function) keep their signatures and map it through the Toast adapter;
# the moved names forward (below). The DB inputs (latest CSV rows, sales by
# item, has_csv_for ...) and the barcode / status / upload-due reads stay here.
#
# Toast GETs and menu parsing: moved to the Toast adapter (multi-store M1,
# batch MT2) - client.py (toast_get, backoff, ToastFetchError, the one token
# cache), stock.py, catalog.py, timeparse.py under app/pos/adapters/toast/.
# Every moved name still works as inventory_api.<name> and patching it here
# patches the adapter (app/pos/compat.py). The names the code below calls by
# bare name are imported; the rest only forward.
# ---------------------------------------------------------------------------

from app.pos.adapters.toast import catalog as _toast_catalog  # noqa: E402
from app.pos.adapters.toast import client as _toast_client  # noqa: E402
from app.pos.adapters.toast import stock as _toast_stock  # noqa: E402
from app.pos.adapters.toast import timeparse as _toast_timeparse  # noqa: E402
from app.pos.adapters.toast.catalog import (  # noqa: E402
    MenuItemInfo,
    _codes,
    fetch_menu_last_updated,
    fetch_menus,
    menu_price,
    merged_barcode,
    parse_menus,
)
from app.pos.adapters.toast.client import ToastFetchError  # noqa: E402
from app.pos.adapters.toast.stock import fetch_stock, fetch_stock_live  # noqa: E402
from app.pos.adapters.toast.timeparse import parse_toast_time  # noqa: E402
from app.pos.compat import forwards_for, install_forwarding  # noqa: E402
from app.pos.sync import catalog as _catalog_sync  # noqa: E402

install_forwarding(
    __name__,
    {
        **forwards_for(
            _toast_client, ("ToastFetchError", "MAX_TRIES", "MAX_WAIT_SECONDS", "_wait_seconds", "_headers", "toast_get")
        ),
        **forwards_for(_toast_stock, ("STOCK_PATH", "LIVE_STOCK_TIMEOUT_SECONDS", "fetch_stock", "fetch_stock_live")),
        **forwards_for(
            _toast_catalog,
            (
                "MENUS_PATH",
                "METADATA_PATH",
                "SKIP_MENUS",
                "CONTAINER_MENU",
                "CONTAINER_OPTION",
                "fetch_menus",
                "fetch_menu_last_updated",
                "_VARIANT_SUFFIX",
                "MenuItemInfo",
                "parse_menus",
                "menu_price",
                "_codes",
                "merged_barcode",
            ),
        ),
        **forwards_for(_toast_timeparse, ("parse_toast_time",)),
        # POS-neutral since MT3 (app/pos/sync/catalog.py).
        **forwards_for(
            _catalog_sync,
            (
                "MIN_BUILD_SHARE",
                "implausible_build",
                "store_api_snapshot",
                "_insert",
                "COMPARE_FIELDS",
                "EXAMPLES_PER_FIELD",
                "_same",
                "_near",
                "_tracked",
                "_explained_by_sales",
                "PLACEMENT_FIELDS",
                "compare_rows",
                "effective_quantity",
                "_live_lock",
                "_live_fetch_lock",
                "_live",
                "LIVE_FAILURE_BACKOFF_SECONDS",
                "LIVE_FETCH_WAIT_SECONDS",
                "live_stock_enabled",
                "_cached_live_stock",
                "clear_live_stock_cache",
                "LiveStockSnapshot",
                "live_stock_meta",
            ),
        ),
    },
)

# Neutral names the code left here still calls by bare name (MT2 pattern:
# patching inventory_api.<name> updates this copy too).
from app.pos.sync.catalog import live_stock_meta  # noqa: E402


def _norm(v) -> str:
    return (v or "").strip().lower()


def _placement(info: MenuItemInfo, carried) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Old name (Toast MenuItemInfo in): the POS-neutral rule is
    ``app.pos.sync.catalog.placement`` (multi-store M1 MT3)."""
    return _catalog_sync.placement(_toast_catalog.item_from_menu_info(info), carried)


# ---------------------------------------------------------------------------
# Inputs from our own DB
# ---------------------------------------------------------------------------


def is_api_source(source_filename: Optional[str]) -> bool:
    return bool(source_filename) and source_filename.startswith(API_SOURCE_PREFIX)


def _not_api():
    return or_(InventorySnapshot.source_filename.is_(None), ~InventorySnapshot.source_filename.like(API_SOURCE_PREFIX + "%"))


def latest_csv_date(db: Session, real_uploads_only: bool = True) -> Optional[dt.date]:
    """The latest date with CSV rows (not API-built; with real_uploads_only,
    not the Jun-Sep `backfill:` history either)."""
    stmt = select(func.max(InventorySnapshot.snapshot_date)).where(_not_api())
    if real_uploads_only:
        stmt = stmt.where(
            or_(InventorySnapshot.source_filename.is_(None), ~InventorySnapshot.source_filename.like("backfill:%"))
        )
    return db.execute(stmt).scalar()


def csv_rows_for(db: Session, day: dt.date) -> Dict[str, InventorySnapshot]:
    stmt = select(InventorySnapshot).where(InventorySnapshot.snapshot_date == day, _not_api())
    return {r.item_id: r for r in db.execute(stmt).scalars()}


def latest_csv_row_per_item(db: Session) -> Dict[str, InventorySnapshot]:
    """item_id -> its newest non-API snapshot row (the carry-forward source)."""
    newest = (
        select(InventorySnapshot.item_id, func.max(InventorySnapshot.snapshot_date).label("d"))
        .where(_not_api())
        .group_by(InventorySnapshot.item_id)
        .subquery()
    )
    stmt = select(InventorySnapshot).join(
        newest, and_(InventorySnapshot.item_id == newest.c.item_id, InventorySnapshot.snapshot_date == newest.c.d)
    )
    return {r.item_id: r for r in db.execute(stmt).scalars()}


def sales_by_item(db: Session, snapshot_date: dt.date, end_offset_days: int = 1) -> Dict[str, dict]:
    """item_id -> {"units": {7,30,90}, "dollars": {7,30,90}} over business
    days [D - N - offset + 1, D - offset] (offset 1 = through yesterday).
    Top-level, non-voided lines only (gotcha #11/#17)."""
    end = snapshot_date - dt.timedelta(days=end_offset_days)
    starts = {n: end - dt.timedelta(days=n - 1) for n in SALES_WINDOWS}
    cols = [Order.item_guid]
    for n in SALES_WINDOWS:
        cols.append(func.sum(case((Order.business_date >= starts[n], Order.quantity), else_=0)))
        cols.append(func.sum(case((Order.business_date >= starts[n], Order.net_price), else_=0)))
    stmt = (
        select(*cols)
        .where(
            Order.business_date >= starts[max(SALES_WINDOWS)],
            Order.business_date <= end,
            Order.voided.is_(False),
            Order.top_level(),
            Order.item_guid.is_not(None),
        )
        .group_by(Order.item_guid)
        .order_by(*group_order(Order.item_guid))
    )
    out = {}
    for row in db.execute(stmt):
        units, dollars = {}, {}
        for i, n in enumerate(SALES_WINDOWS):
            units[n] = round(float(row[1 + 2 * i] or 0), 3)
            dollars[n] = round(float(row[2 + 2 * i] or 0), 2)
        out[row[0]] = {"units": units, "dollars": dollars}
    return out


def csv_made_at(db: Session, day: dt.date) -> Optional[dt.datetime]:
    """When that day's CSV came in (its rows' upload time) - the file is
    exported from Toast just before, so it's the count's moment, give or take
    a few minutes."""
    value = db.execute(
        select(func.min(InventorySnapshot.uploaded_at)).where(InventorySnapshot.snapshot_date == day, _not_api())
    ).scalar()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value


def units_sold_between(db: Session, since: dt.datetime, until: dt.datetime) -> Dict[str, float]:
    """item_id -> units on non-voided lines paid between `since` and `until`
    (the opened time for a line with no payment time) - stock Toast took off
    after the CSV was made. Paid time first: on the Oct 11 rehearsal (CSV
    00:19, pull 14:17) it explained 182 of 187 differences vs 176 with the
    opened time (a scheduled order's opened time is its pickup). Modifier
    lines count too: stock moves for any line whose item is counted. Only
    what's synced (the 04:00 sync of yesterday covers the nightly window)."""
    when = func.coalesce(Order.paid_at, Order.opened_at)
    lo = since.date() - dt.timedelta(days=2)
    hi = until.date() + dt.timedelta(days=2)
    rows = db.execute(
        select(Order.item_guid, fsum(Order.quantity))
        .where(
            Order.business_date >= lo,
            Order.business_date <= hi,
            Order.voided.is_(False),
            Order.item_guid.is_not(None),
            when >= since,
            when <= until,
        )
        .group_by(Order.item_guid)
        .order_by(*group_order(Order.item_guid))
    )
    return {guid: round(float(units or 0), 3) for guid, units in rows if units}


def sales_synced_through(db: Session) -> Optional[dt.date]:
    return db.execute(select(func.max(Order.business_date))).scalar()


# ---------------------------------------------------------------------------
# Building the rows
# ---------------------------------------------------------------------------


def format_status(status: Optional[str], qty: Optional[float]) -> str:
    """The CSV's own wording: "932 in stock", "1,210 in stock",
    "0.485 in stock", "out of stock", or "in stock" (count not tracked).
    Old name (a Toast status string in): ``app.pos.sync.catalog.status_text``."""
    return _catalog_sync.status_text(_toast_stock.stock_status(status), qty)


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _r(v: Optional[float], digits: int = 2) -> Optional[float]:
    return None if v is None else round(v, digits)


def _toast_stock_fetch(fetch_fn: Optional[Callable[[], list]], lenient: bool = False):
    """A Toast-JSON stock fetch function (a test seam) as a StockLevel fetch;
    None stays None (= the store's connector)."""
    if fetch_fn is None:
        return None
    return lambda: _toast_stock.stock_levels(fetch_fn(), lenient=lenient)


def _toast_catalog_fetch(fetch_menus_fn: Optional[Callable[[], dict]]):
    """A Toast-JSON menus fetch function as a CatalogSnapshot fetch; None
    stays None (= the store's connector)."""
    if fetch_menus_fn is None:
        return None
    return lambda: _toast_catalog.catalog_snapshot(fetch_menus_fn())


def build_api_rows(
    db: Session,
    stock: list,
    menus: dict,
    snapshot_date: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    sales_end_offset_days: int = 1,
) -> Tuple[Dict[str, dict], dict]:
    """item_id -> InventorySnapshot field dict (not written), plus build
    stats, from Toast's /stock/v1/inventory + /menus/v2/menus JSON. The build
    itself is POS-neutral (``app.pos.sync.catalog.build_inventory_rows``,
    multi-store M1 MT3); this maps the Toast JSON through the adapter."""
    catalog = _toast_catalog.catalog_snapshot(menus)
    return _catalog_sync.build_inventory_rows(
        db,
        catalog.items,
        _toast_stock.stock_levels(stock),
        catalog.changed_at,
        snapshot_date,
        now,
        sales_end_offset_days,
    )


def has_csv_for(db: Session, day: dt.date) -> bool:
    return (
        db.execute(
            select(InventorySnapshot.id).where(InventorySnapshot.snapshot_date == day, _not_api()).limit(1)
        ).first()
        is not None
    )


# ---------------------------------------------------------------------------
# Jobs: the nightly snapshot and the hourly menu re-check
# ---------------------------------------------------------------------------


def _job(db: Session, name: str, day: Optional[dt.date] = None) -> JobRun:
    job = JobRun(job_name=name, status="running", business_date=day)
    db.add(job)
    db.commit()
    return job


def _finish(db: Session, job: JobRun, status: str, detail) -> JobRun:
    job.status = status
    job.detail = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    job.finished_at = dt.datetime.now(dt.timezone.utc)
    db.add(job)
    db.commit()
    return job


def run_api_snapshot(
    db: Session,
    write: Optional[bool] = None,
    snapshot_date: Optional[dt.date] = None,
    fetch_stock_fn: Callable[[], list] = None,
    fetch_menus_fn: Callable[[], dict] = None,
    now: Optional[dt.datetime] = None,
    rerun: Optional[dict] = None,
) -> JobRun:
    """The 04:05 job (``app.pos.sync.catalog.run_snapshot``, POS-neutral since
    multi-store M1 MT3). ``fetch_stock_fn`` / ``fetch_menus_fn`` return Toast
    JSON (test seams); without them the store's connector is asked."""
    return _catalog_sync.run_snapshot(
        db,
        write=write,
        snapshot_date=snapshot_date,
        fetch_stock=_toast_stock_fetch(fetch_stock_fn),
        fetch_catalog=_toast_catalog_fetch(fetch_menus_fn),
        now=now,
        rerun=rerun,
    )


# ---------------------------------------------------------------------------
# A CSV that lands after the 04:05 run: compare again, once per date
# ---------------------------------------------------------------------------
#
# The nightly comparison needs that day's CSV, and the file often comes in
# after 04:05 (Sep 24 06:49, Oct 2 07:58), so that night had nothing to
# compare and didn't count toward readiness. When it lands, Toast is asked
# again and the comparison recorded as a newer run of the same date (the
# readiness score reads a date's newest run). Only before
# INVENTORY_API_LATE_CSV_UNTIL_HOUR (store time, default 9): the store sells
# from ~09:30, and today's sales aren't synced until tomorrow's 04:00, so a
# later pull would show sales since the file as unexplained differences - a
# false "bad" night. Never for a past date (Toast's count has moved on).

LATE_CSV = "late_csv"
LATE_CSV_MAX_TRIES = 2
# A "running" row older than this is a run that died (restart mid-run).
STALE_RUN = dt.timedelta(hours=1)
_late_csv_lock = tenant_lock("inventory_api._late_csv_lock")


def _job_detail(job: JobRun) -> dict:
    try:
        detail = json.loads(job.detail) if job.detail else {}
    except ValueError:
        return {}
    return detail if isinstance(detail, dict) else {}


def late_csv_rerun_due(db: Session, day: dt.date, now: Optional[dt.datetime] = None) -> Tuple[bool, str]:
    """(due, why). Due when today's CSV is in, the date's nightly run finished
    fine without a comparison, nothing compared it since, no run of the date
    is in progress, fewer than LATE_CSV_MAX_TRIES re-runs were tried, and it's
    still before the cutoff hour."""
    cutoff = int(get_settings().inventory_api_late_csv_until_hour or 0)
    if cutoff <= 0:
        return False, "off"
    now = now or dt.datetime.now(dt.timezone.utc)
    local = now.astimezone(restaurant_timezone())
    if day != local.date():
        return False, "not_today"
    if local.hour >= cutoff:
        return False, "after_cutoff"
    if not has_csv_for(db, day):
        return False, "no_csv"
    runs = (
        db.execute(
            select(JobRun)
            .where(JobRun.job_name == JOB_SNAPSHOT, JobRun.business_date == day)
            .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        )
        .scalars()
        .all()
    )
    if not runs:
        return False, "no_nightly_run"  # the 04:05 run will compare it
    for job in runs:
        started = job.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=dt.timezone.utc)
        if job.status == "running" and (started is None or now - started < STALE_RUN):
            return False, "running"
    details = [(job, _job_detail(job)) for job in runs]
    if any(job.status == "success" and d.get("comparison") for job, d in details):
        return False, "already_compared"
    nightly = [job for job, d in details if not d.get("rerun") and job.status != "running"]
    if not nightly:
        return False, "no_nightly_run"
    if nightly[0].status != "success":
        return False, "nightly_failed"  # a failed night stays a bad night
    if sum(1 for _job, d in details if d.get("rerun")) >= LATE_CSV_MAX_TRIES:
        return False, "tries_used"
    return True, "due"


def rerun_for_late_csv(
    db: Session,
    day: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    fetch_stock_fn: Callable[[], list] = None,
    fetch_menus_fn: Callable[[], dict] = None,
) -> Optional[JobRun]:
    """Compare again when today's CSV came in after the nightly run (see
    above). Returns the new job_runs row, or None when not due. One at a
    time per process (an upload and the nightly's own end check can meet);
    a second caller just skips - the first one covers the date. Never
    writes inventory: with the CSV in, the API set is only compared."""
    day = day or restaurant_today()
    if not _late_csv_lock.acquire(blocking=False):
        return None
    try:
        due, why = late_csv_rerun_due(db, day, now)
        if not due:
            logger.info("Late CSV re-run for %s not due: %s", day, why)
            return None
        csv_at = csv_made_at(db, day)
        job = run_api_snapshot(
            db,
            write=False,
            snapshot_date=day,
            fetch_stock_fn=fetch_stock_fn,
            fetch_menus_fn=fetch_menus_fn,
            rerun={"reason": LATE_CSV, "csv_uploaded_at": _iso(csv_at)},
        )
        logger.info("Late CSV re-run for %s: %s", day, job.status)
        return job
    finally:
        _late_csv_lock.release()


def rerun_after_upload(bind, day: dt.date) -> None:
    """Background task after a CSV upload (routers/inventory.py). Only where
    the scheduled jobs run (SCHEDULER_ENABLED - the dev copy keeps off
    Toast). Never raises."""
    try:
        if not get_settings().scheduler_enabled:
            return
        db = Session(bind=bind)
        try:
            rerun_for_late_csv(db, day)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("Late CSV re-run after the upload failed")


def _after_new_snapshot() -> None:
    """A new latest snapshot: Fix Issues' forecast is keyed on it - warm it
    (never raises). Imported lazily (fix_issues imports this module)."""
    try:
        from app.services.fix_issues import warm_fix_issues_cache

        warm_fix_issues_cache(refresh=True)
    except Exception:
        logger.exception("Fix Issues warm-up after the API snapshot failed")


def _save_menu_cache(
    db: Session, menu: Dict[str, MenuItemInfo], last_updated: Optional[dt.datetime], now: Optional[dt.datetime] = None
) -> int:
    """Old name (parsed Toast menu in): ``app.pos.sync.catalog.save_catalog_cache``."""
    items = [_toast_catalog.item_from_menu_info(info) for info in menu.values()]
    return _catalog_sync.save_catalog_cache(db, items, last_updated, now)



def stored_menu_last_updated(db: Session) -> Optional[dt.datetime]:
    return db.execute(select(func.max(ToastMenuItem.menu_last_updated))).scalar()


def refresh_menu_cache(
    db: Session,
    force: bool = False,
    fetch_last_updated_fn: Callable[[], Optional[dt.datetime]] = None,
    fetch_menus_fn: Callable[[], dict] = None,
    now: Optional[dt.datetime] = None,
) -> JobRun:
    """The hourly barcode re-check (``app.pos.sync.catalog.refresh_catalog_cache``,
    POS-neutral since MT3): one cheap metadata GET; the full menu only when
    Toast's lastUpdated moved (or nothing is cached yet). The fetch functions
    are Toast-JSON test seams; without them the store's connector is asked."""
    return _catalog_sync.refresh_catalog_cache(
        db,
        force=force,
        fetch_changed_at=fetch_last_updated_fn,
        fetch_catalog=_toast_catalog_fetch(fetch_menus_fn),
        now=now,
    )


# ---------------------------------------------------------------------------
# Item 6: barcodes from the latest menu check
# ---------------------------------------------------------------------------

_menu_cache_state: dict = tenant_local("inventory_api._menu_cache_state", dict)


def _menu_cache(db: Session) -> dict:
    """{"items": {item_id: sku}, "fetched_at", "menu_last_updated"} - re-read
    only when the table changed (cheap max() check)."""
    marker = db.execute(select(func.max(ToastMenuItem.fetched_at), func.count(ToastMenuItem.item_id))).first()
    key = (str(db.get_bind().url), marker[0], marker[1])
    if _menu_cache_state.get("key") == key:
        return _menu_cache_state["value"]
    items = dict(db.execute(select(ToastMenuItem.item_id, ToastMenuItem.sku)).all()) if marker[1] else {}
    value = {
        "items": items,
        "fetched_at": marker[0],
        "menu_last_updated": stored_menu_last_updated(db) if marker[1] else None,
    }
    _menu_cache_state.clear()
    _menu_cache_state.update({"key": key, "value": value})
    return value


def _latest_uploaded_at(latest: dict) -> Optional[dt.datetime]:
    stamps = [getattr(s, "uploaded_at", None) for s in latest.values()]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        return None
    best = max(stamps)
    return best if best.tzinfo else best.replace(tzinfo=dt.timezone.utc)


def menu_recheck_moment(db: Session, latest: dict) -> Optional[dt.datetime]:
    """When Toast's menu (as last re-checked) was last changed - but only if
    that check is newer than the latest snapshot; otherwise None (the
    snapshot is the freshest barcode source)."""
    if not latest:
        return None
    cache = _menu_cache(db)
    fetched = cache["fetched_at"]
    if fetched is None:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=dt.timezone.utc)
    uploaded = _latest_uploaded_at(latest)
    if uploaded is not None and fetched <= uploaded:
        return None
    moment = cache["menu_last_updated"] or fetched
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def menu_shows_barcode_issue(db: Session, issue_type: str, item_id: Optional[str]) -> bool:
    """Whether Toast's menu ITSELF (as last re-checked) still shows this
    barcode problem - the bar for bringing back a "fixed" close between
    uploads. The menu's sku is only the first barcode, so it can't prove a
    problem it doesn't show: an invalid 2nd code of a ";"-list (kept from the
    CSV while the sku is one of its codes) or an item missing from the menu
    waits for the next upload instead of coming back on a menu change."""
    skus = _menu_cache(db)["items"]
    if not item_id or item_id not in skus:
        return False
    sku = (skus[item_id] or "").strip()
    if issue_type == "missing_barcode":
        return not sku
    if issue_type == "invalid_barcode":
        from app.services.barcode_report import validate_barcode  # lazy: barcode_report imports this module

        return bool(sku) and not validate_barcode(sku)["valid"]
    return False


class _BarcodeSnapshot:
    __slots__ = ("_snap", "barcode", "barcode_from_menu_at")

    def __init__(self, snap, barcode, at):
        object.__setattr__(self, "_snap", snap)
        object.__setattr__(self, "barcode", barcode)
        object.__setattr__(self, "barcode_from_menu_at", at)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "_snap"), name)

    def __setattr__(self, name, value):
        raise AttributeError("read-only snapshot view")


def barcode_overlay(db: Session, latest: dict) -> dict:
    """latest (item_id -> snapshot) with each item's barcode from the latest
    hourly menu check when that check is newer than the snapshot (see
    merged_barcode). Items the menu doesn't list keep their own barcode."""
    if not get_settings().menu_barcode_recheck_minutes:
        return latest
    moment = menu_recheck_moment(db, latest)
    if moment is None:
        return latest
    skus = _menu_cache(db)["items"]
    out = dict(latest)
    for item_id, snap in latest.items():
        if item_id not in skus:
            continue
        code = merged_barcode(skus[item_id], snap.barcode)
        if (code or None) != ((snap.barcode or "").strip() or None):
            out[item_id] = _BarcodeSnapshot(snap, code, moment)
    return out


# ---------------------------------------------------------------------------
# Negative counts and the live stock overlay: POS-neutral since multi-store M1
# MT3 (app/pos/sync/catalog.py; effective_quantity, LiveStockSnapshot,
# live_stock_meta ... forward there). The three below take Toast JSON / a
# Toast status string, as they always did.
# ---------------------------------------------------------------------------


def get_live_stock(fetch_fn: Callable[[], list] = None) -> Optional[dict]:
    """``app.pos.sync.catalog.get_live_stock``; ``fetch_fn`` returns Toast
    /stock/v1/inventory JSON (a test seam), else the connector is asked."""
    return _catalog_sync.get_live_stock(_toast_stock_fetch(fetch_fn, lenient=True))


def live_quantity(snap, status: Optional[str], qty) -> Optional[float]:
    """``app.pos.sync.catalog.live_level_quantity`` for a Toast status string."""
    return _catalog_sync.live_level_quantity(snap, _toast_stock.stock_status(status), qty)


def overlay_live_stock(latest: dict, fetch_fn: Callable[[], list] = None) -> dict:
    """latest (item_id -> snapshot) with live counts when the switch is on and
    Toast answered; otherwise `latest` itself, unchanged. An item missing from
    Toast's stock list (count not tracked) keeps its snapshot figure.
    (``app.pos.sync.catalog.overlay_live_stock``; ``fetch_fn`` returns Toast
    JSON - a test seam - else the connector is asked.)"""
    return _catalog_sync.overlay_live_stock(latest, _toast_stock_fetch(fetch_fn, lenient=True))


# ---------------------------------------------------------------------------
# Upload-due alert + status for the admin page
# ---------------------------------------------------------------------------


def csv_age_days(db: Session, today: Optional[dt.date] = None) -> Tuple[Optional[dt.date], Optional[int]]:
    today = today or restaurant_today()
    last = latest_csv_date(db)
    return last, ((today - last).days if last else None)


def upload_due(db: Session, today: Optional[dt.date] = None) -> Optional[dict]:
    """None while the latest CSV upload is fresh enough; otherwise what the
    alert / admin page say."""
    max_age = int(get_settings().inventory_csv_max_age_days or 0)
    if max_age <= 0:
        return None
    last, age = csv_age_days(db, today)
    if last is not None and age is not None and age < max_age:
        return None
    return {"last_csv_date": last.isoformat() if last else None, "age_days": age, "max_age_days": max_age}


def _last_job(db: Session, name: str) -> Optional[dict]:
    job = db.execute(
        select(JobRun).where(JobRun.job_name == name).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(1)
    ).scalars().first()
    if job is None:
        return None
    try:
        detail = json.loads(job.detail) if job.detail else None
    except ValueError:
        detail = job.detail
    return {
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "business_date": job.business_date.isoformat() if job.business_date else None,
        "detail": detail,
    }


def last_nightly_comparison(db: Session) -> Optional[dict]:
    """The newest 04:05 run that had a same-day CSV to compare with."""
    jobs = db.execute(
        select(JobRun)
        .where(JobRun.job_name == JOB_SNAPSHOT, JobRun.status == "success")
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(30)
    ).scalars()
    for job in jobs:
        try:
            detail = json.loads(job.detail or "{}")
        except ValueError:
            continue
        if isinstance(detail, dict) and detail.get("comparison"):
            return {
                "run_at": job.started_at.isoformat() if job.started_at else None,
                "meta": detail.get("meta"),
                "comparison": detail["comparison"],
            }
    return None


def status(db: Session) -> dict:
    from app.services import inventory_api_readiness  # lazy: it imports this module

    s = get_settings()
    switch = inventory_api_switch.switch_state(db)
    last_csv, age = csv_age_days(db)
    latest_api = db.execute(
        select(func.max(InventorySnapshot.snapshot_date)).where(InventorySnapshot.source_filename.like(API_SOURCE_PREFIX + "%"))
    ).scalar()
    latest_any = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar()
    cache = _menu_cache(db)
    return {
        "settings": {
            # The switch in force (the page's setting, else .env) - see
            # "switch" for where it comes from.
            "inventory_api_snapshots": bool(switch["on"]),
            "snapshot_time": f"{s.inventory_api_snapshot_hour:02d}:{s.inventory_api_snapshot_minute:02d}",
            "inventory_csv_max_age_days": s.inventory_csv_max_age_days,
            "menu_barcode_recheck_minutes": s.menu_barcode_recheck_minutes,
            "live_stock_cache_seconds": s.live_stock_cache_seconds,
            "scheduler_enabled": bool(s.scheduler_enabled),
        },
        "latest_snapshot_date": latest_any.isoformat() if latest_any else None,
        "latest_snapshot_source": "api" if latest_api and latest_any and latest_api == latest_any else "csv",
        "latest_api_snapshot_date": latest_api.isoformat() if latest_api else None,
        "latest_csv_date": last_csv.isoformat() if last_csv else None,
        "csv_age_days": age,
        "upload_due": upload_due(db),
        "menu_check": {
            "items": len(cache["items"]),
            "fetched_at": cache["fetched_at"].isoformat() if cache["fetched_at"] else None,
            "menu_last_updated": cache["menu_last_updated"].isoformat() if cache["menu_last_updated"] else None,
            "last_run": _last_job(db, JOB_MENU_RECHECK),
        },
        "last_snapshot_run": _last_job(db, JOB_SNAPSHOT),
        "live_stock": live_stock_meta(),
        "live_stock_screens": LIVE_STOCK_SCREENS,
        # Additive (Oct 13): the page switch and the nightly readiness score.
        "switch": switch,
        "readiness": inventory_api_readiness.readiness(db, switch_on=bool(switch["on"])),
    }


# Exactly where the live overlay is used (shown on the admin page and in Help).
LIVE_STOCK_SCREENS = [
    "Supplier Projection (on hand, need to order)",
    "Reorder Candidates (on hand, shortfall, urgency) - and what is built on it: the 9 AM critical vendors push, Vendor Calendar order-due counts, the weekly digest",
    "Fix Issues: Negative inventory and Refill from container",
]


# ---------------------------------------------------------------------------
# On-demand comparison ("Compare now" on the admin page)
# ---------------------------------------------------------------------------

_compare_lock = tenant_lock("inventory_api._compare_lock")
_compare_cache: dict = tenant_local("inventory_api._compare_cache", dict)
COMPARE_CACHE_SECONDS = 600


def compare_now(db: Session, refresh: bool = False, fetch_stock_fn=None, fetch_menus_fn=None) -> dict:
    """Builds the API set in memory right now (nothing written) and compares
    it with the latest CSV upload. Two POS GETs (stock + catalog); cached
    COMPARE_CACHE_SECONDS. Quantities move with every sale since the CSV
    was exported, so on-hand differences grow through the day.
    (``fetch_*_fn`` return Toast JSON - test seams; else the connector.)"""
    with _compare_lock:
        hit = _compare_cache.get("value")
        if hit and not refresh and time.monotonic() - _compare_cache["at"] < COMPARE_CACHE_SECONDS:
            return hit
        csv_day = latest_csv_date(db)
        if csv_day is None:
            raise ToastFetchError("No CSV upload to compare with yet")
        stock = (_toast_stock_fetch(fetch_stock_fn) or _catalog_sync._default_stock)()
        catalog = (_toast_catalog_fetch(fetch_menus_fn) or _catalog_sync._default_catalog)()
        rows, meta = _catalog_sync.build_inventory_rows(db, catalog.items, stock, catalog.changed_at, restaurant_today())
        value = {
            "built_at": meta["built_at"],
            "meta": meta,
            "comparison": _catalog_sync.compare_rows(rows, csv_rows_for(db, csv_day), csv_day),
        }
        _compare_cache.update({"value": value, "at": time.monotonic()})
        return value


# ---------------------------------------------------------------------------
# Push alert: "Inventory upload / cost refresh due"
# ---------------------------------------------------------------------------


def inventory_upload_due_alerts(db: Session, now: dt.datetime) -> list:
    """One push a day (9:00) while the latest CSV upload is
    INVENTORY_CSV_MAX_AGE_DAYS or more old: cost, vendor codes and last
    received dates only come from that file, even with the API snapshot on."""
    from app.services.notifications import Alert  # lazy: keeps this module light

    due = upload_due(db, now.date())
    if due is None:
        return []
    if due["last_csv_date"]:
        body = f"Last inventory file: {due['last_csv_date']} ({due['age_days']} days ago). Upload one to refresh costs and vendor codes."
    else:
        body = "No inventory file uploaded yet. Upload one to load costs and vendor codes."
    return [
        Alert(
            key="inventory_upload_due",
            dedupe_keys=[f"inventory_upload_due:{now.date().isoformat()}"],
            title="Inventory upload due",
            body=body,
        )
    ]
