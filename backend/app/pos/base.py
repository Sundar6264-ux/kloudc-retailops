"""The POS connector interface (multi-store plan 4.3).

Every POS adapter (Toast today; Clover and "CSV only" later) implements
``PosConnector``. Sync jobs call it and write our own tables; features keep
reading those tables and never call a POS directly.

Rules:
- A method whose capability the connector lacks raises ``NotSupported``
  (routers turn it into HTTP 409). A POS that does not answer raises
  ``PosUnavailable`` (HTTP 503), like today's "Toast didn't answer" paths.
- Read-only: a connector never writes to the POS.
- Item ids are the same in orders and in the catalog (``external_id``).

M1 batch MT2 added the interface and the Toast connector; MT3 added the
normalized models (``app/pos/models.py``) the methods return, and the
POS-neutral sync jobs that call them (``app/pos/sync/``). Extras beyond plan
4.3, all keyword-only with defaults that keep the plain call working:
``fetch_orders(..., max_retries, sleep)`` and ``fetch_open_orders(...,
max_retries, pause_seconds)`` carry today's 429 retry and courtesy-pause
rules; ``fetch_catalog_snapshot()`` adds the catalog's change stamp;
``has_day_totals()`` says whether ``get_day_totals`` can answer at all
(Toast: Analytics credentials set).

A POS error is passed on unchanged in M1 (Toast's ``httpx`` errors and their
messages are what job_runs and the pages show today); mapping them to
``PosUnavailable`` comes with the routers' 409/503 work.
"""

import datetime as dt
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Protocol, Sequence, Tuple, runtime_checkable  # noqa: F401

from app.pos.capabilities import TOAST_CAPABILITIES, Capability  # noqa: F401 - re-exported


class PosError(RuntimeError):
    """Base for connector errors the app handles."""


class NotSupported(PosError):
    """This POS can't do that (a capability is missing) -> HTTP 409."""

    def __init__(self, capability: Capability, source: str = ""):
        self.capability = capability
        self.source = source
        where = f" for {source}" if source else ""
        super().__init__(f"Not supported{where}: {capability.value}")


class PosUnavailable(PosError):
    """The POS did not answer (down, timed out, credentials refused) -> HTTP 503."""


class DayTotalsUnavailable(PosError):
    """The POS's day-totals report failed (Toast: an Analytics error). Its
    text is "<label>: <reason>" - for Toast "Toast Analytics: ...", what the
    sales accuracy job has always recorded."""

    def __init__(self, label: str, cause: BaseException):
        self.label = label
        self.cause = cause
        super().__init__(f"{label}: {cause}")


@runtime_checkable
class PosConnector(Protocol):
    """What every POS adapter provides (plan 4.3), returning the models in
    ``app/pos/models.py``."""

    source: str  # "toast" | "clover" | "csv"
    display_name: str  # used in UI text: "Also in {pos}"
    capabilities: FrozenSet[Capability]

    def supports(self, capability: Capability) -> bool: ...

    def health(self) -> "ConnectorHealth": ...  # noqa: F821

    # sales
    def fetch_orders(
        self, business_date: dt.date, *, max_retries: int = 4, sleep: Optional[Callable[[float], None]] = None
    ) -> "List[Order]": ...  # noqa: F821

    def fetch_orders_modified(
        self, since: dt.datetime, until: dt.datetime, cursor: Optional[str] = None
    ) -> "Tuple[List[Order], Optional[str]]": ...  # noqa: F821

    def fetch_open_orders(
        self,
        business_dates: Optional[Sequence[dt.date]] = None,
        *,
        max_retries: Optional[int] = None,
        pause_seconds: float = 0.0,
    ) -> "List[Order]": ...  # noqa: F821

    def fetch_employees(self) -> "List[Employee]": ...  # noqa: F821

    def has_day_totals(self) -> bool: ...

    def get_day_totals(self, start: dt.date, end: dt.date) -> "Dict[dt.date, SalesDay]": ...  # noqa: F821

    # catalog and stock
    def fetch_catalog(self) -> "List[Item]": ...  # noqa: F821

    def fetch_catalog_snapshot(self) -> "CatalogSnapshot": ...  # noqa: F821

    def catalog_changed_at(self) -> Optional[dt.datetime]: ...

    def fetch_stock(self, *, live: bool = False) -> "List[StockLevel]": ...  # noqa: F821

    def parse_catalog_export(self, filename: str, content: bytes) -> "List[CatalogRow]": ...  # noqa: F821

    # analytics (Toast only in Step 1)
    def run_report(self, kind: str, start: dt.date, end: dt.date, **params: Any) -> "AnalyticsReport": ...  # noqa: F821


class BaseConnector:
    """Shared behaviour for adapters: capability checks, and a clear
    ``NotSupported`` for every method the POS can't do. An adapter overrides
    the methods it supports."""

    source: str = ""
    display_name: str = ""
    capabilities: FrozenSet[Capability] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        """Raise NotSupported unless this connector has the capability."""
        if capability not in self.capabilities:
            raise NotSupported(capability, self.source)

    def _not_yet(self, method: str, capability: Optional[Capability] = None):
        if capability is not None:
            self.require(capability)
        raise NotImplementedError(f"{self.source} connector: {method}() is not implemented")

    def health(self):
        return self._not_yet("health")

    def fetch_orders(self, business_date: dt.date, *, max_retries: int = 4, sleep=None):
        return self._not_yet("fetch_orders", Capability.ORDERS_BY_BUSINESS_DATE)

    def fetch_orders_modified(self, since: dt.datetime, until: dt.datetime, cursor: Optional[str] = None):
        return self._not_yet("fetch_orders_modified", Capability.ORDERS_INCREMENTAL)

    def fetch_open_orders(
        self,
        business_dates: Optional[Sequence[dt.date]] = None,
        *,
        max_retries: Optional[int] = None,
        pause_seconds: float = 0.0,
    ):
        return self._not_yet("fetch_open_orders", Capability.OPEN_ORDERS)

    def fetch_employees(self):
        return self._not_yet("fetch_employees", Capability.EMPLOYEES)

    def has_day_totals(self) -> bool:
        """Whether get_day_totals can answer at all (capability present; an
        adapter adds "credentials set")."""
        return self.supports(Capability.DAY_TOTALS)

    def get_day_totals(self, start: dt.date, end: dt.date):
        return self._not_yet("get_day_totals", Capability.DAY_TOTALS)

    def fetch_catalog(self):
        """The catalog's items (``fetch_catalog_snapshot().items``)."""
        self.require(Capability.CATALOG)
        return list(self.fetch_catalog_snapshot().items)

    def fetch_catalog_snapshot(self):
        return self._not_yet("fetch_catalog_snapshot", Capability.CATALOG)

    def catalog_changed_at(self) -> Optional[dt.datetime]:
        return self._not_yet("catalog_changed_at", Capability.CATALOG_CHANGE_STAMP)

    def fetch_stock(self, *, live: bool = False):
        return self._not_yet("fetch_stock", Capability.STOCK_LEVELS)

    def parse_catalog_export(self, filename: str, content: bytes):
        return self._not_yet("parse_catalog_export", Capability.CSV_CATALOG_IMPORT)

    def run_report(self, kind: str, start: dt.date, end: dt.date, **params: Any):
        return self._not_yet("run_report", Capability.ANALYTICS_REPORTS)
