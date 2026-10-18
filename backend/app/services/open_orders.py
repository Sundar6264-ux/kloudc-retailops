"""Old home of Open Orders ("Open Checks") - moved, this name still works.

Multi-store M1 batch MT3 split it in two:

    the cache, scans, live view, employee list, freshness   -> app/pos/sync/open_orders.py
    (POS-neutral: reads open Order models from the connector)
    Toast's order JSON -> Order, "open" = no closedDate and
    not voided/deleted, the scan's 429 retry (_fetch_with_retry),
    fetch_orders_for_business_date, _parse_toast_timestamp  -> app/pos/adapters/toast/orders.py
    fetch_employees, _employee_name                          -> app/pos/adapters/toast/labor.py

Every name below forwards to its new home (``app.pos.compat``): reading
``open_orders.X`` gives the new module's current X, and patching
``open_orders.X`` patches it there - so the tests' fakes for
``fetch_orders_for_business_date``, ``_fetch_with_retry`` and
``fetch_employees`` (Toast JSON) still feed ``get_open_orders`` and
``refresh_open_orders_cache``, through the Toast connector.

The helpers that read one Toast order JSON object (``_normalize_open_order``,
``_tab_names``, ``_order_total``, ``_line_items``, ``_paid_at``,
``_promised_at``, ``_placed_at``, ``_extract_line_items``) keep their old
signatures here: the Toast mapping plus the neutral rule, same results.
"""

import datetime as dt
import json  # noqa: F401 - the old module exposed these names
import logging  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
from typing import Optional

import httpx  # noqa: F401

from app.pos.adapters.toast import labor as _toast_labor
from app.pos.adapters.toast import orders as _toast_orders
from app.pos.compat import forwards_for, install_forwarding
from app.pos.sync import open_orders as _open_orders
from app.services.sales_sync import _extract_line_items  # noqa: F401 - old name, Toast JSON in

# Names that read Toast order JSON stay defined below (old signatures).
_RAW_HELPERS = (
    "_normalize_open_order",
    "_tab_names",
    "_order_total",
    "_line_items",
    "_paid_at",
    "_promised_at",
    "_placed_at",
)

install_forwarding(
    __name__,
    {
        **forwards_for(
            _open_orders,
            [
                "FIRST_REAL_SALES_DATE", "JOB_NAME", "LIVE_MAX_AGE_SECONDS", "_EMPLOYEE_CACHE",
                "FULL_SCAN_MAX_DAYS", "full_scan_start",
                "_EMPLOYEE_CACHE_TTL_SECONDS", "_cached_tab_names", "_is_scheduled", "_live_last_failure",
                "_live_refresh_lock", "_refresh_today", "_replace_cache_rows", "_scan_open_orders",
                "_today_is_stale", "ensure_today_fresh", "get_cached_open_orders", "get_employees",
                "get_open_orders", "legacy_display_number", "refresh_open_orders_cache",
                "refresh_today_in_background", "tab_label", "logger", "JobRun", "OpenOrderCache", "Session",
                "delete", "select", "get_settings", "restaurant_today", "utcnow",
            ],
        ),
        **forwards_for(
            _toast_orders, ("fetch_orders_for_business_date", "_fetch_with_retry", "_parse_toast_timestamp")
        ),
        **forwards_for(_toast_labor, ("fetch_employees", "_employee_name")),
    },
)


def _order(o: dict, business_date: Optional[dt.date] = None):
    return _toast_orders.map_order(o, business_date)


def _normalize_open_order(o: dict, business_date: dt.date, names_by_guid: dict, now: dt.datetime) -> dict:
    """One Toast order JSON object as an open checks row."""
    return _open_orders._normalize_open_order(_order(o, business_date), business_date, names_by_guid, now)


def _tab_names(o: dict) -> list:
    """Each check's Toast `tabName`, cleaned (see
    ``app.pos.sync.open_orders.tab_names_of``). Odd payloads are tolerated:
    no checks, ``checks: None``, a check that isn't an object (skipped)."""
    checks = [_toast_orders.map_check(c) for c in (o.get("checks") or []) if isinstance(c, dict)]
    return _open_orders.tab_names_of(o.get("displayNumber"), checks)


def _order_total(order: dict) -> float:
    return _open_orders._order_total(_order(order))


def _line_items(order: dict) -> list[dict]:
    return _open_orders._line_items(_order(order))


def _paid_at(o: dict) -> Optional[dt.datetime]:
    return _open_orders._paid_at(_order(o))


def _promised_at(o: dict) -> Optional[dt.datetime]:
    return _open_orders._promised_at(_order(o))


def _placed_at(o: dict) -> Optional[dt.datetime]:
    return _open_orders._placed_at(_order(o))
