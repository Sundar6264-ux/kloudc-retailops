"""Multi-store M1 batch MT2: the Toast code moved into app/pos/adapters/toast
as a pure move. These tests pin the move itself:

- every name the old modules had still resolves, to the same object;
- patching the OLD path (as older tests and any test written on another
  branch do) reaches the moved code, and patching the NEW path is seen
  through the old one; undo restores both;
- one token cache; log lines keep their logger names;
- the connector registry, capability checks and the shared 429 wait rule.

The Toast behaviour itself is covered by the re-pointed Toast tests
(test_inventory_api*.py, test_verify2_failure_modes.py, test_sales_accuracy_job.py,
test_insights*.py).
"""

import datetime as dt
import logging
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import httpx
import pytest

from app import toast_client
from app.config import get_settings
from app.pos import compat, registry
from app.pos.adapters.toast import analytics, catalog, client, csv_import, labor, orders, stock, timeparse
from app.pos.adapters.toast.connector import ToastConnector
from app.pos.base import BaseConnector, NotSupported, PosConnector
from app.pos.capabilities import TOAST_CAPABILITIES, Capability
from app.pos.http import retry_wait_seconds
from app.services import inventory_api, inventory_parser, toast_analytics

BACKEND = Path(__file__).resolve().parents[1]

# Every global each old module had at b9501ad (before the move), dunders left out.
OLD_NAMES = {
    "app.toast_client": [
        "ToastAPIError", "_TOKEN_CACHE", "_drop_token", "_extract_access_token", "dt", "fetch_employees",
        "fetch_orders_for_business_date", "get_access_token", "get_settings", "httpx", "time",
    ],
    "app.services.toast_analytics": [
        "AnalyticsError", "AnalyticsNotConfigured", "AnalyticsRateLimited", "AnalyticsReportFailed",
        "AnalyticsReportUnprocessable", "Callable", "ERA_PREFIX", "MENU_FIELDS", "METRIC_FIELDS", "Optional",
        "_DAILY_KINDS", "_LIMITS", "_base", "_blocked_until", "_body", "_calls", "_endpoint_key", "_error_text",
        "_family", "_lock", "_login", "_note_headers", "_now", "_num", "_parse_business_date", "_request",
        "_retry_after", "_sleep", "_sum_by_date", "_token", "_token_lock", "_wait_for_budget", "_week_chunks",
        "_ymd", "collections", "day_metrics", "dt", "get_settings", "httpx", "is_configured", "logger", "logging",
        "menu_day_totals", "menu_items_for_day", "reset_state", "run_report", "threading", "time",
    ],
    "app.services.inventory_api": [
        "API_SOURCE_PREFIX", "CARRIED_FIELDS", "COMPARE_CACHE_SECONDS", "COMPARE_FIELDS", "CONTAINER_MENU",
        "CONTAINER_OPTION", "Callable", "Dict", "EXAMPLES_PER_FIELD", "InventorySnapshot", "Iterable",
        "JOB_MENU_RECHECK", "JOB_SNAPSHOT", "JobRun", "LATE_CSV", "LATE_CSV_MAX_TRIES",
        "LIVE_FAILURE_BACKOFF_SECONDS", "LIVE_FETCH_WAIT_SECONDS", "LIVE_STOCK_SCREENS",
        "LIVE_STOCK_TIMEOUT_SECONDS", "List", "LiveStockSnapshot", "MAX_TRIES", "MAX_WAIT_SECONDS", "MENUS_PATH",
        "METADATA_PATH", "MIN_BUILD_SHARE", "MenuItemInfo", "Optional", "Order", "PLACEMENT_FIELDS",
        "SALES_WINDOWS", "SKIP_MENUS", "STALE_RUN", "STOCK_PATH", "Session", "ToastFetchError", "ToastMenuItem",
        "Tuple", "_BarcodeSnapshot", "_VARIANT_SUFFIX", "_after_new_snapshot", "_cached_live_stock", "_codes",
        "_compare_cache", "_compare_lock", "_explained_by_sales", "_finish", "_headers", "_insert", "_iso", "_job",
        "_job_detail", "_last_job", "_late_csv_lock", "_latest_uploaded_at", "_live", "_live_fetch_lock",
        "_live_lock", "_menu_cache", "_menu_cache_state", "_near", "_norm", "_not_api", "_placement", "_r",
        "_same", "_save_menu_cache", "_tracked", "_wait_seconds", "and_", "barcode_overlay", "build_api_rows",
        "case", "clear_live_stock_cache", "compare_now", "compare_rows", "csv_age_days", "csv_made_at",
        "csv_rows_for", "defaultdict", "delete", "dt", "effective_quantity", "fetch_menu_last_updated",
        "fetch_menus", "fetch_stock", "fetch_stock_live", "format_status", "func", "get_live_stock",
        "get_settings", "has_csv_for", "httpx", "implausible_build", "inventory_api_switch",
        "inventory_upload_due_alerts", "is_api_source", "json", "last_nightly_comparison", "late_csv_rerun_due",
        "latest_csv_date", "latest_csv_row_per_item", "live_quantity", "live_stock_enabled", "live_stock_meta",
        "logger", "logging", "menu_price", "menu_recheck_moment", "menu_shows_barcode_issue", "merged_barcode",
        "or_", "overlay_live_stock", "parse_menus", "parse_toast_time", "re", "refresh_menu_cache",
        "rerun_after_upload", "rerun_for_late_csv", "restaurant_timezone", "restaurant_today", "run_api_snapshot",
        "sales_by_item", "sales_synced_through", "select", "sqlite_insert", "status", "store_api_snapshot",
        "stored_menu_last_updated", "threading", "time", "toast_get", "units_sold_between", "upload_due",
    ],
    "app.services.inventory_parser": [
        "COLUMN_MAP", "InventoryParseError", "InventorySnapshot", "NUMERIC_FIELDS", "REQUIRED_SOURCE_COLUMNS",
        "Session", "XLS_NOT_SUPPORTED", "_load_dataframe", "_normalize_header", "delete", "dt", "io",
        "parse_and_store", "pd", "sqlite_insert",
    ],
}

OLD_MODULES = {
    "app.toast_client": toast_client,
    "app.services.toast_analytics": toast_analytics,
    "app.services.inventory_api": inventory_api,
    "app.services.inventory_parser": inventory_parser,
}

# Where each moved name lives now.
MOVED = {
    "app.toast_client": {
        client: ["_TOKEN_CACHE", "ToastAPIError", "_extract_access_token", "get_access_token", "_drop_token"],
        labor: ["fetch_employees"],
        orders: ["fetch_orders_for_business_date"],
    },
    "app.services.inventory_api": {
        client: ["ToastFetchError", "MAX_TRIES", "MAX_WAIT_SECONDS", "_wait_seconds", "_headers", "toast_get"],
        stock: ["STOCK_PATH", "LIVE_STOCK_TIMEOUT_SECONDS", "fetch_stock", "fetch_stock_live"],
        catalog: [
            "MENUS_PATH", "METADATA_PATH", "SKIP_MENUS", "CONTAINER_MENU", "CONTAINER_OPTION", "fetch_menus",
            "fetch_menu_last_updated", "_VARIANT_SUFFIX", "MenuItemInfo", "parse_menus", "menu_price", "_codes",
            "merged_barcode",
        ],
        timeparse: ["parse_toast_time"],
    },
    "app.services.inventory_parser": {
        csv_import: [
            "COLUMN_MAP", "REQUIRED_SOURCE_COLUMNS", "NUMERIC_FIELDS", "InventoryParseError", "_normalize_header",
            "XLS_NOT_SUPPORTED", "_load_dataframe",
        ],
    },
}


# --- old names --------------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", sorted(OLD_NAMES))
def test_every_old_name_still_resolves(module_name):
    mod = OLD_MODULES[module_name]
    missing = [n for n in OLD_NAMES[module_name] if not hasattr(mod, n)]
    assert missing == []
    assert set(OLD_NAMES[module_name]) <= set(dir(mod))


@pytest.mark.parametrize("module_name", sorted(MOVED))
def test_moved_names_are_the_adapters_objects(module_name):
    mod = OLD_MODULES[module_name]
    table = compat.forwarded_names(mod)
    for owner, names in MOVED[module_name].items():
        for name in names:
            assert getattr(mod, name) is getattr(owner, name), name
            assert table[name] == owner.__name__, name


def test_toast_analytics_forwards_everything_it_held():
    table = compat.forwarded_names(toast_analytics)
    for name in OLD_NAMES["app.services.toast_analytics"]:
        if name in ("collections", "dt", "httpx", "logging", "threading", "time"):
            continue  # imported modules: the same module objects either way
        assert table.get(name) == analytics.__name__, name
        assert getattr(toast_analytics, name) is getattr(analytics, name), name


def test_from_imports_of_old_paths_still_work():
    from app.services.inventory_api import ToastFetchError, fetch_stock_live, parse_menus  # noqa: F401
    from app.services.inventory_parser import XLS_NOT_SUPPORTED, InventoryParseError  # noqa: F401
    from app.services.toast_analytics import AnalyticsError, run_report  # noqa: F401
    from app.toast_client import ToastAPIError, fetch_employees, fetch_orders_for_business_date  # noqa: F401

    assert ToastFetchError is client.ToastFetchError and run_report is analytics.run_report
    assert fetch_orders_for_business_date is orders.fetch_orders_for_business_date
    assert InventoryParseError is csv_import.InventoryParseError


@pytest.mark.parametrize(
    "module_name",
    [
        "app.toast_client",
        "app.services.toast_analytics",
        "app.services.inventory_api",
        "app.services.inventory_parser",
        "app.pos.adapters.toast.orders",
        "app.pos.adapters.toast.labor",
        "app.pos.adapters.toast.stock",
        "app.pos.adapters.toast.catalog",
        "app.pos.adapters.toast.analytics",
        "app.pos.adapters.toast.csv_import",
        "app.pos.adapters.toast.connector",
        "app.pos.registry",
    ],
)
def test_each_module_imports_first_in_a_fresh_interpreter(module_name):
    """No import cycle: whichever module a process imports first works."""
    out = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"], cwd=BACKEND, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]


# --- patching -------------------------------------------------------------------------------


def _toast_creds(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id")
    monkeypatch.setattr(s, "toast_client_secret", "secret")
    monkeypatch.setattr(s, "toast_restaurant_guid", "guid")


def test_old_path_token_patch_reaches_moved_orders_and_labor(monkeypatch):
    _toast_creds(monkeypatch)
    monkeypatch.setattr(toast_client, "get_access_token", lambda c: "patched-old-path")
    assert client.get_access_token is toast_client.get_access_token
    seen = []

    def fake_get(self, url, headers=None, params=None, timeout=None):
        seen.append(headers["Authorization"])
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert toast_client.fetch_orders_for_business_date(dt.date(2026, 10, 14)) == []
    assert toast_client.fetch_employees() == []
    assert seen == ["Bearer patched-old-path", "Bearer patched-old-path"]


def test_patch_undo_restores_the_adapter(monkeypatch):
    real = client.get_access_token
    with monkeypatch.context() as m:
        m.setattr(toast_client, "get_access_token", lambda c: "x")
        assert client.get_access_token is not real
    assert client.get_access_token is real and toast_client.get_access_token is real


def test_mock_patch_on_old_path_works_and_restores():
    real = analytics.menu_day_totals
    with mock.patch("app.services.toast_analytics.menu_day_totals", return_value={"x": 1}):
        assert analytics.menu_day_totals(None, None) == {"x": 1}
        assert toast_analytics.menu_day_totals is analytics.menu_day_totals
    assert analytics.menu_day_totals is real and toast_analytics.menu_day_totals is real


def test_old_path_toast_get_patch_reaches_stock_menus_and_the_code_left_in_inventory_api(monkeypatch):
    calls = []

    def fake(path, timeout=90.0, max_tries=4):
        calls.append((path, max_tries))
        if path == stock.STOCK_PATH:
            return [{"guid": "a", "status": "QUANTITY", "quantity": 3}]
        if path == catalog.METADATA_PATH:
            return {"lastUpdated": "2026-10-11T15:41:42.977+0000"}
        return {"menus": []}

    monkeypatch.setattr(inventory_api, "toast_get", fake)  # the old path, as older tests do
    assert stock.fetch_stock() == [{"guid": "a", "status": "QUANTITY", "quantity": 3}]
    assert inventory_api.fetch_menus() == {"menus": []}
    assert inventory_api.fetch_stock_live()[0]["guid"] == "a"
    assert catalog.fetch_menu_last_updated() == dt.datetime(2026, 10, 11, 15, 41, 42, 977000, tzinfo=dt.timezone.utc)
    assert (stock.STOCK_PATH, 1) in calls  # the live fetch never retries


def test_new_path_patch_is_seen_through_the_old_path(monkeypatch):
    fake = lambda path, timeout=90.0, max_tries=4: []  # noqa: E731
    monkeypatch.setattr(client, "toast_get", fake)
    assert inventory_api.toast_get is fake
    assert inventory_api.fetch_stock() == []


def test_old_path_patch_also_updates_the_copy_inventory_api_calls_by_bare_name(monkeypatch):
    """overlay_live_stock (left in inventory_api) calls fetch_stock_live by
    bare name; patching inventory_api.fetch_stock_live must reach it, as before."""
    fake = lambda: []  # noqa: E731
    monkeypatch.setattr(inventory_api, "fetch_stock_live", fake)
    assert vars(inventory_api)["fetch_stock_live"] is fake
    assert stock.fetch_stock_live is fake
    monkeypatch.undo()
    assert vars(inventory_api)["fetch_stock_live"] is stock.fetch_stock_live is inventory_api.fetch_stock_live


def test_old_path_constant_patch_reaches_the_adapter(monkeypatch):
    monkeypatch.setattr(inventory_api, "MAX_WAIT_SECONDS", 2.0)
    resp = httpx.Response(429, headers={"Retry-After": "30"}, request=httpx.Request("GET", "https://x"))
    assert client._wait_seconds(resp, 0) == 2.0


def test_names_not_moved_stay_local(monkeypatch):
    # (LIVE_FETCH_WAIT_SECONDS was the example here until MT3 moved the live
    # stock overlay to app/pos/sync/catalog.py.)
    monkeypatch.setattr(inventory_api, "COMPARE_CACHE_SECONDS", 5)
    assert "COMPARE_CACHE_SECONDS" not in compat.forwarded_names(inventory_api)
    assert vars(inventory_api)["COMPARE_CACHE_SECONDS"] == 5


# --- one token cache, logs ----------------------------------------------------------------------


def test_one_token_cache_and_a_401_drops_the_shared_token(monkeypatch):
    _toast_creds(monkeypatch)
    assert toast_client._TOKEN_CACHE is client._TOKEN_CACHE
    monkeypatch.setitem(client._TOKEN_CACHE, "id", ("stale", time.time() + 3600))
    logins = []

    def fake_post(self, url, json=None, timeout=None):
        logins.append(url)
        return httpx.Response(200, json={"token": {"accessToken": "fresh", "expiresIn": 3600}}, request=httpx.Request("POST", url))

    def fake_get(self, url, headers=None, timeout=None):
        status = 401 if headers["Authorization"] == "Bearer stale" else 200
        return httpx.Response(status, json=[{"guid": "a"}], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert inventory_api.toast_get(stock.STOCK_PATH) == [{"guid": "a"}]
    assert len(logins) == 1 and toast_client._TOKEN_CACHE["id"][0] == "fresh"


def test_log_lines_keep_their_old_logger_names():
    assert client.logger.name == "app.services.inventory_api"
    assert analytics.logger.name == "app.services.toast_analytics"
    assert inventory_api.logger is logging.getLogger("app.services.inventory_api")
    assert toast_analytics.logger is logging.getLogger("app.services.toast_analytics")


def test_toast_get_429_warning_still_logged_under_inventory_api(monkeypatch, caplog):
    _toast_creds(monkeypatch)
    monkeypatch.setattr(client, "get_access_token", lambda c: "tok")
    answers = [
        httpx.Response(429, headers={"Retry-After": "1"}, request=httpx.Request("GET", "https://x")),
        httpx.Response(200, json=[], request=httpx.Request("GET", "https://x")),
    ]
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, headers=None, timeout=None: answers.pop(0))
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    with caplog.at_level(logging.WARNING, logger="app.services.inventory_api"):
        assert client.toast_get(stock.STOCK_PATH) == []
    assert [r.name for r in caplog.records] == ["app.services.inventory_api"]


# --- shared 429 rule ----------------------------------------------------------------------------


def _resp(**headers):
    return httpx.Response(429, headers=headers, request=httpx.Request("GET", "https://x"))


def test_retry_wait_rule_is_the_old_toast_rule(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    reset = "x-toast-ratelimit-reset"
    assert retry_wait_seconds(_resp(**{"Retry-After": "3"}), 0, 30.0, reset) == 3.0
    assert retry_wait_seconds(_resp(**{"Retry-After": "99"}), 0, 30.0, reset) == 30.0
    assert retry_wait_seconds(_resp(**{"Retry-After": "soon", reset: "1005"}), 0, 30.0, reset) == 5.0
    assert retry_wait_seconds(_resp(**{reset: "1000.2"}), 0, 30.0, reset) == 1.0  # at least 1 s
    assert retry_wait_seconds(_resp(**{reset: "junk"}), 2, 30.0, reset) == 6.0
    assert retry_wait_seconds(_resp(), 20, 30.0, reset) == 30.0
    assert retry_wait_seconds(_resp(**{reset: "1005"}), 0, 30.0) == 2.0  # no reset header named
    assert client._wait_seconds(_resp(**{reset: "1005"}), 0) == 5.0


# --- connector + registry -------------------------------------------------------------------------


def test_pos_provider_defaults_to_toast():
    assert get_settings().pos_provider == "toast"
    assert registry.provider_name() == "toast"


def test_get_connector_gives_the_toast_connector():
    registry.reset_cache()
    conn = registry.get_connector()
    assert isinstance(conn, ToastConnector) and isinstance(conn, PosConnector)
    assert conn.source == "toast" and conn.display_name == "Toast"
    assert conn.capabilities == TOAST_CAPABILITIES
    assert registry.get_connector() is conn and registry.get_connector(" Toast ") is conn


def test_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "pos_provider", "square")
    with pytest.raises(registry.UnknownPosProvider):
        registry.get_connector()
    monkeypatch.setattr(get_settings(), "pos_provider", "")
    assert registry.provider_name() == "toast"


def test_missing_capability_raises_not_supported_and_run_report_is_not_built_yet():
    conn = ToastConnector()
    assert not conn.supports(Capability.ORDERS_INCREMENTAL)
    with pytest.raises(NotSupported) as err:
        conn.fetch_orders_modified(dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 2))
    assert err.value.capability is Capability.ORDERS_INCREMENTAL and "toast" in str(err.value)
    # MT3 implemented fetch_orders / fetch_open_orders / fetch_employees /
    # fetch_catalog / fetch_stock / get_day_totals (tests/test_pos_sync_neutral.py
    # and tests/test_pos_toast_mapping.py, with fakes - never call them here
    # unpatched: they would ask Toast). Insights still calls the Analytics
    # client directly, so run_report waits.
    with pytest.raises(NotImplementedError):
        conn.run_report("menu", dt.date(2026, 10, 1), dt.date(2026, 10, 2))


def test_base_connector_without_capabilities_refuses_everything():
    class Bare(BaseConnector):
        source = "csv"

    with pytest.raises(NotSupported):
        Bare().fetch_orders(dt.date(2026, 10, 14))
    with pytest.raises(NotSupported):
        Bare().catalog_changed_at()


def test_catalog_changed_at_uses_the_menu_metadata_call(monkeypatch):
    calls = []

    def fake(path, timeout=90.0, max_tries=4):
        calls.append(path)
        return {"lastUpdated": "2026-10-11T15:41:42.977+0000"}

    monkeypatch.setattr(client, "toast_get", fake)
    assert ToastConnector().catalog_changed_at() == dt.datetime(2026, 10, 11, 15, 41, 42, 977000, tzinfo=dt.timezone.utc)
    assert calls == [catalog.METADATA_PATH]


def test_toast_connector_is_configured_follows_the_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "toast_client_id", "")
    assert ToastConnector().is_configured() is False
    _toast_creds(monkeypatch)
    assert ToastConnector().is_configured() is True
