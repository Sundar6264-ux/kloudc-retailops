"""The read endpoints the parity harness records, as an admin.

Names are relative ("d-1", "top3") so two recordings made with a different
fixed "now" still line up name for name. Only GETs that read stored data:
nothing here asks Toast for live data (Items Sold uses past dates only, Open
Checks uses the cached all-time list, Insights answer from their cache), and
the network is blocked anyway.

To add an endpoint: append it here, re-record the baseline (README.md), and
say so in the batch's "As built" note - a recording made before the change
lacks it and the diff tool reports it as "only in B" (not a difference).
"""

import datetime as dt
from typing import Dict, List, NamedTuple, Optional


class Call(NamedTuple):
    name: str
    path: str
    params: Optional[Dict[str, str]] = None


def _d(day: dt.date) -> str:
    return day.isoformat()


def build_calls(today: dt.date, top_vendors: List[str], issue_types: List[str]) -> List[Call]:
    calls: List[Call] = []
    add = calls.append

    def ago(n: int) -> str:
        return _d(today - dt.timedelta(days=n))

    def ahead(n: int) -> str:
        return _d(today + dt.timedelta(days=n))

    # --- Who am I / app shell (MT6 adds keys here: additive only) ---------
    add(Call("auth.me", "/api/auth/me"))
    add(Call("auth.features", "/api/auth/features"))
    add(Call("app.config", "/api/app/config"))
    add(Call("glossary", "/api/glossary"))
    add(Call("jobs.status", "/api/jobs/status"))

    # --- Sales by day / Overview sales / Items Sold ----------------------
    # The Overview "Sales yesterday" tile is Items Sold for one day; one call
    # per day for the last 14 days is "sales by day".
    for n in range(1, 15):
        add(Call(f"sales.items_sold.d-{n}", "/api/sales/items-sold", {"start": ago(n), "end": ago(n)}))
    for n in (21, 30, 60, 90):
        add(Call(f"sales.items_sold.d-{n}", "/api/sales/items-sold", {"start": ago(n), "end": ago(n)}))
    add(Call("sales.items_sold.range_7", "/api/sales/items-sold", {"start": ago(7), "end": ago(1)}))
    add(Call("sales.items_sold.range_30", "/api/sales/items-sold", {"start": ago(30), "end": ago(1)}))
    add(Call("sales.items_sold_export.range_7", "/api/sales/items-sold/export", {"start": ago(7), "end": ago(1)}))
    add(Call("sales.export_csv.range_14", "/api/sales/export", {"start": ago(14), "end": ago(1), "format": "csv"}))
    add(Call("sales.accuracy.30", "/api/sales/accuracy", {"days": "30"}))

    # --- Reorder / Forecast / critical -----------------------------------
    for lookback, lead in ((30, 7), (90, 14)):
        for critical in (False, True):
            params = {"lookback_days": str(lookback), "lead_time_days": str(lead)}
            name = f"ordering.reorder.{lookback}_{lead}"
            if critical:
                params["critical_only"] = "true"
                name += ".critical_only"
            add(Call(name, "/api/ordering/reorder-candidates", params))
    add(Call("ordering.forecast.30", "/api/ordering/forecast", {"lookback_days": "30"}))
    add(Call("ordering.suppliers", "/api/ordering/suppliers"))
    add(Call("reorder.critical_dismissals", "/api/reorder/critical-dismissals", {"include_history": "1"}))

    # --- Supplier Projection, top vendors --------------------------------
    for i, vendor in enumerate(top_vendors, start=1):
        add(Call(f"ordering.supplier_projection.top{i:02d}", "/api/ordering/supplier-projection", {"supplier": vendor}))
    if top_vendors:
        add(Call("categories.cart_preview.top01", "/api/categories/cart-preview", {"supplier": top_vendors[0]}))

    # --- Vendor Calendar --------------------------------------------------
    add(Call("vendor_calendar.2_days", "/api/vendor-calendar", {"start": _d(today), "end": ahead(1)}))
    add(Call("vendor_calendar.20_days", "/api/vendor-calendar", {"start": _d(today), "end": ahead(19)}))
    add(
        Call(
            "vendor_calendar.20_days.with_ordered",
            "/api/vendor-calendar",
            {"start": _d(today), "end": ahead(19), "include_ordered": "true"},
        )
    )
    add(Call("vendor_calendar.schedules", "/api/vendor-calendar/schedules"))
    add(Call("vendor_calendar.schedule_suggestions", "/api/vendor-calendar/schedule-suggestions"))
    add(Call("inventory.invoice_vendors_today", "/api/inventory/invoice-vendors-today"))

    # --- Fix Issues -------------------------------------------------------
    add(Call("issues.reasons", "/api/issues/reasons"))
    add(Call("issues.summary", "/api/issues/summary"))
    for issue_type in issue_types:
        add(Call(f"issues.list.{issue_type}", f"/api/issues/{issue_type}"))
    add(Call("issues.report.30", "/api/issues/report", {"start": ago(29), "end": _d(today)}))

    # --- Open Checks (the cached all-time list; never the live one) -------
    add(Call("orders.open_all_time", "/api/orders/open/all-time"))

    # --- Inventory reports -------------------------------------------------
    add(Call("inventory.price_changes", "/api/inventory/price-changes"))
    add(Call("reports.store_vs_container", "/api/reports/store-vs-container"))
    add(Call("reports.missing_barcodes", "/api/reports/missing-barcodes"))
    add(Call("reports.invalid_barcodes", "/api/reports/invalid-barcodes"))
    add(Call("reports.dead_stock", "/api/reports/dead-stock"))
    add(Call("reports.negative_inventory", "/api/reports/negative-inventory"))
    add(Call("reports.duplicate_products", "/api/reports/duplicate-products"))
    add(Call("reports.vendor_price_comparison", "/api/reports/vendor-price-comparison"))

    # --- Placed orders / cart / PO send -----------------------------------
    add(Call("ordering.orders", "/api/ordering/orders"))
    add(Call("ordering.orders.placed", "/api/ordering/orders", {"status": "placed", "limit": "200"}))
    add(Call("ordering.orders_recent", "/api/ordering/orders/recent"))
    add(Call("ordering.cart", "/api/ordering/cart"))
    add(Call("ordering.po_sends", "/api/ordering/po-sends"))
    add(Call("ordering.vendor_contacts", "/api/ordering/vendor-contacts"))

    # --- Insights (cache only: Analytics is not configured here) ----------
    add(Call("insights.hourly_heatmap", "/api/insights/hourly-heatmap"))
    add(Call("insights.channels", "/api/insights/channels"))
    add(Call("insights.compare", "/api/insights/compare"))
    add(Call("insights.compare.d-1", "/api/insights/compare", {"date": ago(1)}))
    add(Call("insights.check_trends", "/api/insights/check-trends"))
    add(Call("insights.loss_watch", "/api/insights/loss-watch"))
    add(Call("insights.odd_checks", "/api/insights/odd-checks"))
    add(Call("insights.menu_performance", "/api/insights/menu-performance"))
    add(Call("insights.menu_performance.kitchen", "/api/insights/menu-performance", {"group": "kitchen"}))
    add(Call("insights.addons", "/api/insights/addons"))
    add(Call("insights.prep_forecast", "/api/insights/prep-forecast"))
    add(Call("insights.returning_customers", "/api/insights/returning-customers"))

    # --- PO sections (categories) -----------------------------------------
    add(Call("categories.sections", "/api/categories/sections"))
    add(Call("categories.options", "/api/categories/options"))
    add(Call("categories.vendor_rules", "/api/categories/vendor-rules", {"include_inactive": "true"}))
    add(Call("categories.renames", "/api/categories/renames", {"include_inactive": "true"}))
    add(Call("categories.items", "/api/categories/items"))
    add(Call("categories.history", "/api/categories/history"))
    add(Call("categories.changes", "/api/categories/changes"))

    # --- Inventory from Toast (admin status pages; stored data only) ------
    add(Call("inventory_api.status", "/api/inventory-api/status"))
    add(Call("inventory_api.compare_nightly", "/api/inventory-api/compare/nightly"))
    add(Call("inventory_api.switch", "/api/inventory-api/switch"))

    # --- Team / invoices / notifications ----------------------------------
    add(Call("team.task_summary", "/api/team/task-summary"))
    add(Call("team.task_stats.30", "/api/team/task-stats", {"days": "30"}))
    add(Call("team.tasks", "/api/team/tasks"))
    add(Call("invoices.list", "/api/invoices"))
    add(Call("invoices.close_suggestions", "/api/invoices/close-suggestions"))
    add(Call("notifications.defaults", "/api/notifications/defaults"))
    add(Call("checklists.overview", "/api/checklists/overview"))
    add(Call("issue_leads", "/api/issue-leads"))
    return calls


# Every Fix Issues list (app/services/fix_issues.py ISSUE_TYPES as of Oct 15).
# A fixed list on purpose - not read from the app - so a later batch that
# loses a type shows up as a 404 difference instead of silently vanishing.
ISSUE_TYPES = [
    "missing_barcode",
    "invalid_barcode",
    "duplicate",
    "dead_stock",
    "negative_inventory",
    "refill_from_container",
    "slow_review",
    "dead_with_stock",
    "dead_zero_stock",
    "dead_new_arrival",
]
