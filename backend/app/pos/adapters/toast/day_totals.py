"""Toast's own day totals for the sales accuracy check (multi-store M1 MT3).

``day_totals(start, end)`` asks Toast Analytics for the item-level day totals
(the ``menu`` report) and then the day metrics (the ``metrics`` report) - the
same two calls, in the same order, ``services/sales_accuracy.py`` made before
M1 - and returns one POS-neutral ``SalesDay`` per business date either
report has. An Analytics failure comes back as ``DayTotalsUnavailable`` whose
text is "Toast Analytics: <reason>", the text the accuracy job always
recorded.
"""

import datetime as dt
from typing import Dict

from app.pos.adapters.toast import analytics as _analytics
from app.pos.base import DayTotalsUnavailable
from app.pos.models import SalesDay, to_decimal

LABEL = "Toast Analytics"


def _money(row: dict, key: str):
    return to_decimal(float(row.get(key) or 0))


def sales_day(day: dt.date, menu, metrics, has_menu: bool, has_metrics: bool) -> SalesDay:
    menu = menu or {}
    metrics = metrics or {}
    return SalesDay(
        business_date=day,
        has_item_totals=has_menu,
        has_day_metrics=has_metrics,
        item_net=_money(menu, "netSalesAmount"),
        item_qty=_money(menu, "quantitySold"),
        item_gross=_money(menu, "grossSalesAmount"),
        item_discounts=_money(menu, "discountAmount"),
        item_voids=_money(menu, "voidAmount"),
        net=_money(metrics, "netSalesAmount"),
        gross=_money(metrics, "grossSalesAmount"),
        discounts=_money(metrics, "discountAmount"),
        voids=_money(metrics, "voidOrdersAmount"),
        refunds=_money(metrics, "refundAmount"),
        orders=int(metrics.get("ordersCount") or 0),
        void_orders=int(metrics.get("voidOrdersCount") or 0),
        open_orders=int(metrics.get("openOrdersCount") or 0),
    )


def day_totals(start: dt.date, end: dt.date) -> Dict[dt.date, SalesDay]:
    try:
        menu = _analytics.menu_day_totals(start, end)
        metrics = _analytics.day_metrics(start, end)
    except _analytics.AnalyticsError as exc:
        raise DayTotalsUnavailable(LABEL, exc) from exc
    days = list(menu) + [d for d in metrics if d not in menu]
    return {d: sales_day(d, menu.get(d), metrics.get(d), d in menu, d in metrics) for d in days}
