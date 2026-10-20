"""``CloverConnector``: the PosConnector for Clover (website-ready track D2;
multi-store plan 4.7, MT15-MT17).

Read-only. Credentials come from ``auth.fresh_auth()`` at call time (the
current store's vault row in multi mode, ``CLOVER_*`` in single mode), so
one cached instance serves the store whatever its token does. A store that
is not connected gets ``PosUnavailable`` from every POS call (jobs record
it; pages show "not connected").

Business dates are derived (Clover has none): the merchant's time zone from
``/properties`` (cached for an hour per merchant, falling back to the store's
time zone) and the store's ``BUSINESS_DAY_CUTOFF_HOUR``. ``fetch_orders(day)``
asks for the orders created in that business day's [start, end) window.
"""

import datetime as dt
import threading
import time
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from app.config import get_settings
from app.pos.adapters.clover import auth as _auth
from app.pos.adapters.clover import mapping as _map
from app.pos.adapters.clover.client import CloverAuthError, CloverClient, CloverError
from app.pos.base import BaseConnector, PosUnavailable
from app.pos.capabilities import CLOVER_CAPABILITIES, Capability
from app.pos.models import CatalogSnapshot, ConnectorHealth, Employee, Order, StockLevel

PROPERTIES_TTL_SECONDS = 3600


class CloverConnector(BaseConnector):
    source = "clover"
    display_name = "Clover"
    capabilities: FrozenSet[Capability] = CLOVER_CAPABILITIES
    # app.pos.gating: until the store connects Clover, its POS pages and jobs
    # are off (as with no adapter at all).
    requires_connection = True
    CONFIGURED_TTL_SECONDS = 30.0

    def __init__(self, client_factory: Optional[Callable[[], CloverClient]] = None):
        # Tests pass a factory returning a client on a fake transport.
        self._client_factory = client_factory
        self._props: Dict[str, Tuple[float, dict]] = {}
        self._props_lock = threading.Lock()
        self._configured: Optional[Tuple[float, bool]] = None

    # --- plumbing ------------------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Credentials present (cached 30 s: the gates ask on every request)."""
        if self._client_factory is not None:
            return True
        now = time.monotonic()
        hit = self._configured
        if hit is not None and now - hit[0] < self.CONFIGURED_TTL_SECONDS:
            return hit[1]
        try:
            ok = _auth.current_auth() is not None
        except Exception:  # noqa: BLE001 - platform DB down: not usable now
            ok = False
        self._configured = (now, ok)
        return ok

    def forget_connection(self) -> None:
        self._configured = None

    def client(self) -> CloverClient:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            auth = _auth.fresh_auth()
        except CloverAuthError as exc:
            raise PosUnavailable(str(exc)) from exc
        except _auth.OAuthNotConfigured as exc:
            raise PosUnavailable("Clover connection expired and the Clover app is not set up") from exc
        provide, on_401 = _auth.token_provider(auth)
        return CloverClient(auth.merchant_id, provide, auth.region, on_unauthorized=on_401)

    def _call(self, fn):
        try:
            return fn(self.client())
        except PosUnavailable:
            raise
        except CloverAuthError as exc:
            raise PosUnavailable(f"{exc}. Reconnect Clover in Setup > Integrations.") from exc

    def properties(self, client: CloverClient) -> dict:
        key = client.merchant_id
        now = time.monotonic()
        with self._props_lock:
            hit = self._props.get(key)
            if hit and now - hit[0] < PROPERTIES_TTL_SECONDS:
                return hit[1]
        try:
            props = client.properties()
        except CloverAuthError:
            raise
        except CloverError:
            props = {}
        with self._props_lock:
            self._props[key] = (now, props)
        return props

    def _tz_cutoff(self, client: CloverClient):
        s = get_settings()
        props = self.properties(client)
        tz = _map.zone(props.get("timezone"), s.store_timezone or s.toast_timezone)
        return tz, int(getattr(s, "business_day_cutoff_hour", 4) or 0)

    def health(self) -> ConnectorHealth:
        configured = self.is_configured()
        return ConnectorHealth(
            source=self.source,
            configured=configured,
            detail=None if configured else "Clover is not connected (Setup > Integrations)",
        )

    # --- sales ---------------------------------------------------------------------------------

    def fetch_orders(self, business_date: dt.date, *, max_retries: int = 4, sleep=None) -> List[Order]:
        """Every order created in one business date (store cutoff, merchant
        time zone), with lines, modifiers, refunds and order type."""
        self.require(Capability.ORDERS_BY_BUSINESS_DATE)

        def run(client: CloverClient) -> List[Order]:
            client.max_tries = max(1, int(max_retries))
            if sleep is not None:
                client.sleep = sleep
            tz, cutoff = self._tz_cutoff(client)
            start, end = _map.business_day_bounds(business_date, tz, cutoff)
            raw = client.orders(start, end, field="createdTime")
            return [_map.map_order(o, tz, cutoff, business_date) for o in raw if not o.get("deleted")]

        return self._call(run)

    def fetch_orders_modified(
        self, since: dt.datetime, until: dt.datetime, cursor: Optional[str] = None
    ) -> Tuple[List[Order], Optional[str]]:
        """Orders modified in [since or cursor, until), in windows of at most
        90 days. The returned cursor is ``until`` (ISO): the next call goes on
        from there."""
        self.require(Capability.ORDERS_INCREMENTAL)
        start = since
        if cursor:
            try:
                start = max(since, dt.datetime.fromisoformat(cursor))
            except ValueError:
                pass

        def run(client: CloverClient):
            tz, cutoff = self._tz_cutoff(client)
            raw = client.orders(start, until, field="modifiedTime")
            return [_map.map_order(o, tz, cutoff) for o in raw if not o.get("deleted")], until.isoformat()

        return self._call(run)

    def fetch_open_orders(
        self,
        business_dates: Optional[Sequence[dt.date]] = None,
        *,
        max_retries: Optional[int] = None,
        pause_seconds: float = 0.0,
    ) -> List[Order]:
        """Open orders (``state=open``) created in the given business dates
        (default: today's)."""
        self.require(Capability.OPEN_ORDERS)
        if business_dates is None:
            from app.timeutil import restaurant_today

            business_dates = [restaurant_today()]

        def run(client: CloverClient) -> List[Order]:
            if max_retries is not None:
                client.max_tries = max(1, int(max_retries))
            tz, cutoff = self._tz_cutoff(client)
            out: List[Order] = []
            for d in business_dates:
                start, end = _map.business_day_bounds(d, tz, cutoff)
                raw = client.orders(start, end, field="createdTime", extra_filters=("state=open",))
                out.extend(_map.map_order(o, tz, cutoff, d) for o in raw if _map.is_open(o))
                if pause_seconds:
                    time.sleep(pause_seconds)
            return out

        return self._call(run)

    def fetch_employees(self) -> List[Employee]:
        self.require(Capability.EMPLOYEES)
        return self._call(lambda c: [_map.map_employee(e) for e in c.employees()])

    # --- catalog and stock ---------------------------------------------------------------------

    def fetch_catalog_snapshot(self) -> CatalogSnapshot:
        """Every item with its categories and stock. Clover has no cheap
        "menu changed" stamp: ``changed_at`` is the newest item modifiedTime."""
        self.require(Capability.CATALOG)

        def run(client: CloverClient) -> CatalogSnapshot:
            items = [_map.map_item(i) for i in client.items() if i.get("id")]
            stamps = [i.updated_at for i in items if i.updated_at]
            return CatalogSnapshot(items=tuple(items), changed_at=max(stamps) if stamps else None)

        return self._call(run)

    def fetch_stock(self, *, live: bool = False) -> List[StockLevel]:
        """Signed stock counts (``/item_stocks``); items without a stock row
        are not tracked. ``live``: one try, no 429 sleeps."""
        self.require(Capability.STOCK_LEVELS)

        def run(client: CloverClient) -> List[StockLevel]:
            if live:
                client.max_tries = 1
                client.timeout = 15.0
            now = dt.datetime.now(dt.timezone.utc)
            return _map.stock_from_item_stocks(client.item_stocks(), now)

        return self._call(run)

    # --- merchant ------------------------------------------------------------------------------

    def merchant_facts(self) -> dict:
        """Name, time zone and summary hour (shown after Test connection)."""
        return self._call(lambda c: _map.merchant_facts(c.merchant(), self.properties(c)))
