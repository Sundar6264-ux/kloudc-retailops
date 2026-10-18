"""Multi-store M1 batch MT3: the old service modules are thin shims over
app/pos/sync and the Toast adapter. These tests pin that:

- every name each old module had before MT3 (aa7688e) still resolves;
- the moved names ARE the new modules' objects (forwarded, not copied);
- patching the OLD path (monkeypatch / mock.patch, as the older tests and the
  tests written on the parallel MT4-MT6 branches do) reaches the moved code,
  and undo restores it;
- no import cycle, whichever module a process imports first;
- the job_runs names the status widget reads are unchanged.
"""

import datetime as dt
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from app.pos import compat
from app.pos.adapters.toast import labor as toast_labor
from app.pos.adapters.toast import orders as toast_orders
from app.pos.sync import accuracy as accuracy_sync
from app.pos.sync import catalog as catalog_sync
from app.pos.sync import open_orders as open_sync
from app.pos.sync import sales as sales_sync_new
from app.services import inventory_api, items_sold, open_orders, sales_accuracy, sales_sync

BACKEND = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 10, 14)

# Every global each module had at aa7688e (before MT3), dunders left out.
OLD_NAMES = {
    "app.services.sales_sync": [
        "Callable", "JOB_NAME", "JobRun", "Optional", "Order", "Session", "_extract_line_items", "_flatten_selection",
        "_parse_toast_timestamp", "_prune_missing", "_upsert_line_items", "delete", "dt",
        "fetch_orders_for_business_date", "fetch_orders_with_retry", "httpx", "latest_job_run", "logger", "logging",
        "select", "sqlite_insert", "sync_sales_for_date", "time",
    ],
    "app.services.open_orders": [
        "FIRST_REAL_SALES_DATE", "JOB_NAME", "JobRun", "LIVE_MAX_AGE_SECONDS", "OpenOrderCache", "Optional", "Session",
        "_EMPLOYEE_CACHE", "_EMPLOYEE_CACHE_TTL_SECONDS", "_cached_tab_names", "_employee_name", "_extract_line_items",
        "_fetch_with_retry", "_is_scheduled", "_line_items", "_live_last_failure", "_live_refresh_lock",
        "_normalize_open_order", "_order_total", "_paid_at", "_parse_toast_timestamp", "_placed_at", "_promised_at",
        "_refresh_today", "_replace_cache_rows", "_scan_open_orders", "_tab_names", "_today_is_stale", "delete", "dt",
        "ensure_today_fresh", "fetch_employees", "fetch_orders_for_business_date", "get_cached_open_orders",
        "get_employees", "get_open_orders", "get_settings", "httpx", "json", "legacy_display_number", "logger",
        "logging", "refresh_open_orders_cache", "refresh_today_in_background", "restaurant_today", "select",
        "tab_label", "threading", "time", "utcnow",
    ],
    "app.services.sales_accuracy": [
        "CHECK_JOB", "CLOSEOUT_HOUR", "Callable", "FIRST_REAL_SALES_DATE", "JobRun", "KEEP_CHECK_DAYS", "MAX_DAYS",
        "MONEY_TOLERANCE", "Optional", "Order", "QTY_TOLERANCE", "RESYNC_PAUSE_SECONDS", "RUN_JOB", "SALES_SYNC_JOB",
        "Session", "UNPAID_RESYNC_HOURS", "_RUN_LOCK", "_analytics_view", "_differences", "_empty_totals",
        "_fingerprint", "_last_successful_sync", "_previous_checks", "_r2", "_record", "_run", "_sleep", "_window",
        "accuracy_report", "case", "closed_business_today", "delete", "dt", "func", "get_settings", "is_running",
        "json", "logger", "logging", "restaurant_timezone", "restaurant_today", "run_sales_repull", "select",
        "stored_day_totals", "sync_sales_for_date", "threading", "time", "toast_analytics", "utcnow",
    ],
    "app.services.items_sold": [
        "LIVE_FAILURE_BACKOFF_SECONDS", "LIVE_LOCK_WAIT_SECONDS", "LIVE_MAX_RETRY_WAIT_SECONDS",
        "LiveSalesUnavailable", "MIN_REFRESH_SECONDS", "Optional", "Order", "Session", "_DATE_LOCKS", "_LIVE_CACHE",
        "_LIVE_FAILED", "_LIVE_LOCK", "_add", "_cached_live", "_extract_line_items", "_items_sold_live",
        "_items_sold_stored", "_live_line_items", "_live_sleep", "_merge_items", "and_", "clear_live_cache", "dt",
        "fetch_orders_with_retry", "get_items_sold", "get_settings", "or_", "restaurant_timezone",
        "restaurant_today", "select", "threading", "time", "to_restaurant_date",
    ],
}

MODULES = {
    "app.services.sales_sync": sales_sync,
    "app.services.open_orders": open_orders,
    "app.services.sales_accuracy": sales_accuracy,
    "app.services.items_sold": items_sold,
}

# Where the moved names live now (a sample of each kind; the full tables are
# in the shims).
MOVED = {
    "app.services.sales_sync": {
        sales_sync_new: ["JOB_NAME", "sync_sales_for_date", "_upsert_line_items", "_prune_missing", "latest_job_run"],
        toast_orders: ["fetch_orders_for_business_date", "fetch_orders_with_retry", "_parse_toast_timestamp"],
    },
    "app.services.open_orders": {
        open_sync: [
            "JOB_NAME", "FIRST_REAL_SALES_DATE", "_EMPLOYEE_CACHE", "get_open_orders", "refresh_open_orders_cache",
            "get_cached_open_orders", "ensure_today_fresh", "refresh_today_in_background", "_live_refresh_lock",
            "_live_last_failure", "tab_label", "legacy_display_number", "get_employees",
        ],
        toast_orders: ["fetch_orders_for_business_date", "_fetch_with_retry"],
        toast_labor: ["fetch_employees", "_employee_name"],
    },
    "app.services.sales_accuracy": {
        accuracy_sync: [
            "CHECK_JOB", "RUN_JOB", "run_sales_repull", "accuracy_report", "_sleep", "utcnow", "_RUN_LOCK",
            "closed_business_today", "FIRST_REAL_SALES_DATE", "SALES_SYNC_JOB",
        ],
    },
    "app.services.inventory_api": {
        catalog_sync: [
            "compare_rows", "store_api_snapshot", "implausible_build", "effective_quantity", "LiveStockSnapshot",
            "live_stock_meta", "clear_live_stock_cache", "LIVE_FETCH_WAIT_SECONDS", "_live",
        ],
    },
}


@pytest.mark.parametrize("module_name", sorted(OLD_NAMES))
def test_every_old_name_still_resolves(module_name):
    mod = MODULES[module_name]
    missing = [n for n in OLD_NAMES[module_name] if not hasattr(mod, n)]
    assert missing == []
    assert set(OLD_NAMES[module_name]) <= set(dir(mod))


@pytest.mark.parametrize("module_name", sorted(MOVED))
def test_moved_names_are_the_new_modules_objects(module_name):
    mod = sys.modules[module_name]
    table = compat.forwarded_names(mod)
    for owner, names in MOVED[module_name].items():
        for name in names:
            assert getattr(mod, name) is getattr(owner, name), name
            assert table[name] == owner.__name__, name


def test_job_runs_names_are_unchanged():
    assert sales_sync.JOB_NAME == "toast_sales_sync"
    assert open_orders.JOB_NAME == "open_orders_scan"
    assert (sales_accuracy.RUN_JOB, sales_accuracy.CHECK_JOB) == ("sales_repull", "sales_accuracy")
    assert (inventory_api.JOB_SNAPSHOT, inventory_api.JOB_MENU_RECHECK) == ("inventory_api_snapshot", "menu_barcode_recheck")


def test_old_from_imports_still_work():
    from app.services.open_orders import (  # noqa: F401
        _normalize_open_order,
        _tab_names,
        get_cached_open_orders,
        legacy_display_number,
        refresh_open_orders_cache,
        tab_label,
    )
    from app.services.sales_accuracy import FIRST_REAL_SALES_DATE, closed_business_today  # noqa: F401
    from app.services.sales_sync import JOB_NAME, _extract_line_items, sync_sales_for_date  # noqa: F401

    assert sync_sales_for_date is sales_sync_new.sync_sales_for_date
    assert refresh_open_orders_cache is open_sync.refresh_open_orders_cache


@pytest.mark.parametrize(
    "module_name",
    [
        "app.services.sales_sync",
        "app.services.open_orders",
        "app.services.sales_accuracy",
        "app.services.items_sold",
        "app.services.inventory_api",
        "app.pos.models",
        "app.pos.sync.sales",
        "app.pos.sync.open_orders",
        "app.pos.sync.accuracy",
        "app.pos.sync.catalog",
        "app.pos.adapters.toast.orders",
        "app.pos.adapters.toast.day_totals",
        "app.pos.adapters.toast.connector",
        "app.pos.barcodes",
    ],
)
def test_each_module_imports_first_in_a_fresh_interpreter(module_name):
    out = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"], cwd=BACKEND, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]


# --- patching the old paths reaches the moved code ----------------------------------------------


def _raw_order(guid="o1", selection="s1"):
    return {
        "guid": guid,
        "checks": [{"guid": "c1", "paidDate": "2026-10-14T18:00:00.000+0000", "selections": [{"guid": selection, "item": {"guid": "x"}, "quantity": 1, "price": 5.0}]}],
    }


def test_old_path_fetch_patch_feeds_the_neutral_sync(db, monkeypatch):
    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", lambda d: [_raw_order()])
    assert toast_orders.fetch_orders_for_business_date(DAY) == [_raw_order()]
    job = sales_sync.sync_sales_for_date(db, DAY)
    assert job.status == "success" and job.detail == "1 line items synced from 1 orders"


def test_old_path_retry_patch_feeds_the_open_checks_scan(db, monkeypatch):
    monkeypatch.setattr(open_orders, "_fetch_with_retry", lambda d, max_retries=4: [_raw_order()])
    monkeypatch.setattr(open_orders, "fetch_employees", lambda: [{"guid": "e1", "firstName": "Sam"}])
    monkeypatch.setattr(open_orders.time, "sleep", lambda s: None)
    open_orders._EMPLOYEE_CACHE.update({"at": 0.0, "by_guid": {}})
    try:
        job = open_orders.refresh_open_orders_cache(db, full=False)
        assert job.status == "success" and job.detail.endswith("1 open check(s)")
        assert open_orders.get_employees() == [{"guid": "e1", "name": "Sam"}]
    finally:
        open_orders._EMPLOYEE_CACHE.update({"at": 0.0, "by_guid": {}})


def test_old_path_analytics_patch_feeds_the_accuracy_job(db, monkeypatch):
    calls = []
    monkeypatch.setattr(sales_accuracy.toast_analytics, "is_configured", lambda: True)
    monkeypatch.setattr(sales_accuracy.toast_analytics, "menu_day_totals", lambda a, b: calls.append("menu") or {})
    monkeypatch.setattr(sales_accuracy.toast_analytics, "day_metrics", lambda a, b: calls.append("metrics") or {})
    monkeypatch.setattr(sales_accuracy, "_sleep", lambda s: None)
    summary = sales_accuracy.run_sales_repull(db, days=3, today=DAY)
    assert summary.status == "success" and calls == ["menu", "metrics"]


def test_analytics_error_text_is_unchanged(db, monkeypatch):
    def boom(a, b):
        raise sales_accuracy.toast_analytics.AnalyticsRateLimited("budget used up")

    monkeypatch.setattr(sales_accuracy.toast_analytics, "is_configured", lambda: True)
    monkeypatch.setattr(sales_accuracy.toast_analytics, "menu_day_totals", boom)
    summary = sales_accuracy.run_sales_repull(db, days=3, today=DAY)
    assert summary.status == "failed" and summary.detail == "Toast Analytics: budget used up"


def test_old_path_seam_patches_reach_the_moved_code(monkeypatch):
    monkeypatch.setattr(sales_accuracy, "utcnow", lambda: dt.datetime(2026, 10, 14, 3, 0, tzinfo=dt.timezone.utc))
    assert accuracy_sync.utcnow() == dt.datetime(2026, 10, 14, 3, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(inventory_api, "LIVE_FETCH_WAIT_SECONDS", 0.25)
    assert catalog_sync.LIVE_FETCH_WAIT_SECONDS == 0.25
    monkeypatch.setattr(open_orders, "LIVE_MAX_AGE_SECONDS", 7)
    assert open_sync.LIVE_MAX_AGE_SECONDS == 7


def test_mock_patch_on_old_path_works_and_restores():
    original = open_sync.refresh_open_orders_cache
    with mock.patch.object(open_orders, "refresh_open_orders_cache") as fake:
        assert open_sync.refresh_open_orders_cache is fake
    assert open_sync.refresh_open_orders_cache is original and open_orders.refresh_open_orders_cache is original


def test_inventory_api_helper_patch_reaches_the_neutral_store(db, monkeypatch):
    """store_api_snapshot moved, but it still asks inventory_api.has_csv_for -
    a test that patches that (the CSV-lands-meanwhile race) still works."""
    answers = iter([False, True])
    monkeypatch.setattr(inventory_api, "has_csv_for", lambda db, day: next(answers))
    assert inventory_api.store_api_snapshot(db, {"x": {}}, DAY) == {"written": 0, "skipped": "csv_exists"}
