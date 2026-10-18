"""``ToastConnector``: the PosConnector for Toast (multi-store plan 4.6).

M1 batch MT2 moved the Toast code into this adapter; MT3 makes the connector
return the POS-neutral models (``app/pos/models.py``) and points the sync
jobs (``app/pos/sync/``) at it.

Every Toast call goes through the adapter module that owns it, looked up at
call time (``_orders.fetch_orders_with_retry(...)``, never a name imported
here), so a test that patches a Toast function - on its new module or on its
old module name (``sales_sync.fetch_orders_for_business_date``,
``open_orders.fetch_employees``, ``inventory_api.fetch_stock`` ...) - is seen
by the connector, exactly as it was by the old code.

Errors are passed on as Toast raised them (httpx errors, ``ToastFetchError``),
because job_runs rows and pages show their text today. Only an Analytics
failure in ``get_day_totals`` is wrapped (``DayTotalsUnavailable``, same text).
"""

import datetime as dt
import time
from typing import Callable, FrozenSet, List, Optional, Sequence

from app.config import get_settings
from app.pos.adapters.toast import analytics as _analytics
from app.pos.adapters.toast import catalog as _catalog
from app.pos.adapters.toast import day_totals as _day_totals
from app.pos.adapters.toast import labor as _labor
from app.pos.adapters.toast import orders as _orders
from app.pos.adapters.toast import stock as _stock
from app.pos.base import BaseConnector
from app.pos.capabilities import TOAST_CAPABILITIES, Capability
from app.pos.models import CatalogSnapshot, ConnectorHealth, Employee, Order, StockLevel


class ToastConnector(BaseConnector):
    source = "toast"
    display_name = "Toast"
    capabilities: FrozenSet[Capability] = TOAST_CAPABILITIES

    def is_configured(self) -> bool:
        """Standard API credentials present (the same test toast_get makes)."""
        s = get_settings()
        return bool(s.toast_client_id and s.toast_client_secret and s.toast_restaurant_guid)

    def health(self) -> ConnectorHealth:
        configured = self.is_configured()
        return ConnectorHealth(
            source=self.source,
            configured=configured,
            detail=None if configured else "TOAST_CLIENT_ID / TOAST_CLIENT_SECRET / TOAST_RESTAURANT_GUID not set",
        )

    # --- sales ---------------------------------------------------------------------------------

    def fetch_orders(
        self,
        business_date: dt.date,
        *,
        max_retries: int = 4,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> List[Order]:
        """Every order of one business date (ordersBulk), Toast's 429 retried
        ``max_retries`` times (``sleep`` waits; default time.sleep)."""
        self.require(Capability.ORDERS_BY_BUSINESS_DATE)
        if sleep is None:
            raw = _orders.fetch_orders_with_retry(business_date, max_retries=max_retries)
        else:
            raw = _orders.fetch_orders_with_retry(business_date, max_retries=max_retries, sleep=sleep)
        return [_orders.map_order(o, business_date) for o in raw]

    def fetch_open_orders(
        self,
        business_dates: Optional[Sequence[dt.date]] = None,
        *,
        max_retries: Optional[int] = None,
        pause_seconds: float = 0.0,
    ) -> List[Order]:
        """Open orders (never closed, not voided or deleted) of each business
        date, one ordersBulk call per date. ``max_retries`` None = one plain
        call per date (the live single-day view); a number = the open checks
        scan's 429 retry. ``pause_seconds`` is waited after each date (the
        scan's courtesy pause). Default dates: today's business date."""
        self.require(Capability.OPEN_ORDERS)
        if business_dates is None:
            from app.timeutil import restaurant_today

            business_dates = [restaurant_today()]
        out: List[Order] = []
        for d in business_dates:
            if max_retries is None:
                raw = _orders.fetch_orders_for_business_date(d)
            else:
                raw = _orders._fetch_with_retry(d, max_retries=max_retries)
            # Closed / voided / deleted orders are dropped before mapping, as
            # the open checks code always skipped them before reading them.
            out.extend(_orders.map_order(o, d) for o in raw if _orders.is_open(o))
            if pause_seconds:
                time.sleep(pause_seconds)
        return out

    def fetch_employees(self) -> List[Employee]:
        """Every Toast employee, deleted ones with ``active`` False."""
        self.require(Capability.EMPLOYEES)
        return [_labor.map_employee(e) for e in _labor.fetch_employees()]

    def has_day_totals(self) -> bool:
        """Toast Analytics credentials are set (else the accuracy job is off)."""
        return self.supports(Capability.DAY_TOTALS) and bool(_analytics.is_configured())

    def get_day_totals(self, start: dt.date, end: dt.date):
        self.require(Capability.DAY_TOTALS)
        return _day_totals.day_totals(start, end)

    # --- catalog and stock ---------------------------------------------------------------------

    def fetch_catalog_snapshot(self) -> CatalogSnapshot:
        """The whole menu (``/menus/v2/menus``) as Items + its lastUpdated."""
        self.require(Capability.CATALOG)
        return _catalog.catalog_snapshot(_catalog.fetch_menus())

    def catalog_changed_at(self) -> Optional[dt.datetime]:
        """When the menu last changed in Toast (``/menus/v2/metadata``)."""
        self.require(Capability.CATALOG_CHANGE_STAMP)
        return _catalog.fetch_menu_last_updated()

    def fetch_stock(self, *, live: bool = False) -> List[StockLevel]:
        """``/stock/v1/inventory`` as StockLevels. ``live``: someone is waiting
        for a page - short timeout, no 429 sleeps (``fetch_stock_live``)."""
        self.require(Capability.STOCK_LEVELS)
        if live:
            return _stock.stock_levels(_stock.fetch_stock_live(), lenient=True)
        return _stock.stock_levels(_stock.fetch_stock())
