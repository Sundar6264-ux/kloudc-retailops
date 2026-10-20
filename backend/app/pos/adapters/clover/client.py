"""Clover REST v3 client (website-ready track D1; multi-store plan 4.7, MT15).

Read-only GETs under ``/v3/merchants/{mId}`` with:

- region hosts: ``us`` (US and Canada) ``api.clover.com``, ``eu``
  ``api.eu.clover.com``, ``la`` ``api.la.clover.com``, ``sandbox``
  ``apisandbox.dev.clover.com``;
- bearer auth (a merchant API token, or an OAuth v2 access token that the
  caller refreshes - ``token`` may be a callable asked before every request);
- paging: ``limit=1000`` + ``offset`` until a page comes back short;
- time windows of at most 90 days (``windows()``) for order filters;
- rate limits: 16 requests/s and 5 concurrent per token, 50 requests/s and
  10 concurrent per app. One process-wide limiter per token and one for the
  app (``RateLimiter``); a cross-process app budget is out of scope
  (docs/WEBSITE_READY_CONTRACT.md 1, "Not in this job");
- 429: wait ``Retry-After`` (``app.pos.http.retry_wait_seconds``) and retry;
- a ``User-Agent`` naming the product;
- every request sent is metered as ``pos_api_calls`` (I3), never raising.

Errors: ``CloverAuthError`` (401/403), ``CloverError`` (other HTTP errors,
still rate limited, network) - texts never contain the token or a URL with
a secret. Nothing here touches the database.
"""

import contextlib
import datetime as dt
import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Sequence, Tuple, Union

import httpx

from app.pos import http as pos_http

logger = logging.getLogger(__name__)

API_HOSTS: Dict[str, str] = {
    "us": "https://api.clover.com",
    "eu": "https://api.eu.clover.com",
    "la": "https://api.la.clover.com",
    "sandbox": "https://apisandbox.dev.clover.com",
}
# Where a merchant signs in to approve an app (OAuth v2 authorize).
WEB_HOSTS: Dict[str, str] = {
    "us": "https://www.clover.com",
    "eu": "https://www.eu.clover.com",
    "la": "https://www.la.clover.com",
    "sandbox": "https://sandbox.dev.clover.com",
}
REGIONS = tuple(API_HOSTS)

PAGE_LIMIT = 1000
MAX_WINDOW_DAYS = 90
# Clover returns at most 100 elements of an expanded collection; an order
# with more line items is fetched again through /orders/{id}/line_items.
EXPANDED_LIMIT = 100
MAX_TRIES = 4
MAX_WAIT_SECONDS = 30.0
TIMEOUT = 60.0

TOKEN_RATE, TOKEN_CONCURRENCY = 16, 5
APP_RATE, APP_CONCURRENCY = 50, 10


def user_agent() -> str:
    try:
        from app.platform.config import pget

        product = str(pget("product_name") or "KloudC RetailOps")
    except Exception:  # noqa: BLE001
        product = "KloudC RetailOps"
    return f"{product.replace(' ', '')}/1.0 (+clover-connector)"


class CloverError(RuntimeError):
    """Clover answered with an error, kept on rate limiting, or could not be reached."""

    def __init__(self, message: str, status: Optional[int] = None):
        self.status = status
        super().__init__(message)


class CloverAuthError(CloverError):
    """The token was refused (HTTP 401/403): reconnect or enter a new token."""


def region_of(value: Any) -> str:
    r = str(value or "").strip().lower()
    return r if r in API_HOSTS else "us"


def api_base(region: str) -> str:
    return API_HOSTS[region_of(region)]


# ---------------------------------------------------------------------------
# Rate limiting (process-wide)
# ---------------------------------------------------------------------------


class RateLimiter:
    """At most ``rate`` starts in any 1-second window and ``concurrency``
    requests in flight. ``clock`` / ``sleep`` are injectable (tests use a fake
    clock). Thread safe."""

    def __init__(
        self,
        rate: int,
        concurrency: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.rate = int(rate)
        self.concurrency = int(concurrency)
        self.clock = clock
        self.sleep = sleep
        self._starts: Deque[float] = deque()
        self._cond = threading.Condition()
        self._in_flight = 0

    def _wait_rate(self) -> None:
        while True:
            with self._cond:
                now = self.clock()
                while self._starts and now - self._starts[0] >= 1.0:
                    self._starts.popleft()
                if len(self._starts) < self.rate:
                    self._starts.append(now)
                    return
                wait = 1.0 - (now - self._starts[0])
            self.sleep(max(wait, 0.001))

    def acquire(self) -> None:
        with self._cond:
            while self._in_flight >= self.concurrency:
                self._cond.wait(timeout=1.0)
            self._in_flight += 1
        try:
            self._wait_rate()
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        with self._cond:
            self._in_flight = max(0, self._in_flight - 1)
            self._cond.notify()

    @contextlib.contextmanager
    def slot(self) -> Iterator[None]:
        self.acquire()
        try:
            yield
        finally:
            self.release()

    @property
    def in_flight(self) -> int:
        return self._in_flight


_limiters_lock = threading.Lock()
# Process-wide on purpose (contract I6 / "Not in this job": one budget per
# process). Keys are a hash of the token, never the token itself.
_token_limiters: Dict[str, RateLimiter] = {}  # global-state: process-wide Clover rate budget per token hash
_app_limiter: Dict[str, RateLimiter] = {}  # global-state: process-wide Clover per-app rate budget


def _token_key(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def token_limiter(token: str) -> RateLimiter:
    key = _token_key(token)
    with _limiters_lock:
        lim = _token_limiters.get(key)
        if lim is None:
            lim = _token_limiters[key] = RateLimiter(TOKEN_RATE, TOKEN_CONCURRENCY)
        return lim


def app_limiter() -> RateLimiter:
    with _limiters_lock:
        lim = _app_limiter.get("app")
        if lim is None:
            lim = _app_limiter["app"] = RateLimiter(APP_RATE, APP_CONCURRENCY)
        return lim


def reset_limiters() -> None:
    with _limiters_lock:
        _token_limiters.clear()
        _app_limiter.clear()


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------


def to_ms(value: dt.datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return int(value.timestamp() * 1000)


def from_ms(value: Any) -> Optional[dt.datetime]:
    if value in (None, ""):
        return None
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000.0, tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def windows(
    start: dt.datetime, end: dt.datetime, max_days: int = MAX_WINDOW_DAYS
) -> List[Tuple[dt.datetime, dt.datetime]]:
    """[start, end) split into consecutive half-open windows of at most
    ``max_days`` days (Clover refuses longer order filters). Empty when
    end <= start."""
    out: List[Tuple[dt.datetime, dt.datetime]] = []
    step = dt.timedelta(days=max_days)
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

Params = Union[Dict[str, Any], Sequence[Tuple[str, Any]]]


def _meter() -> None:
    try:
        from app.platform import usage

        usage.record(usage.POS_API_CALLS)
    except Exception:  # noqa: BLE001 - metering never breaks a sync
        pass


class CloverClient:
    """GETs for one merchant. ``token``: the bearer token, or a callable
    returning it (OAuth: refreshed by the caller before it expires).
    ``transport``: an httpx transport (tests use ``httpx.MockTransport``)."""

    def __init__(
        self,
        merchant_id: str,
        token: Union[str, Callable[[], str]],
        region: str = "us",
        *,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
        max_tries: int = MAX_TRIES,
        timeout: float = TIMEOUT,
        limiters: Optional[Sequence[RateLimiter]] = None,
        on_unauthorized: Optional[Callable[[], None]] = None,
    ):
        if not merchant_id:
            raise CloverError("Clover merchant ID is not set")
        self.merchant_id = str(merchant_id).strip()
        self._token = token
        self.region = region_of(region)
        self.base = api_base(self.region)
        self.transport = transport
        self.sleep = sleep
        self.max_tries = max(1, int(max_tries))
        self.timeout = timeout
        self._limiters = limiters
        self._on_unauthorized = on_unauthorized
        self.calls = 0

    # --- plumbing ------------------------------------------------------------------------------

    def token(self) -> str:
        tok = self._token() if callable(self._token) else self._token
        if not tok:
            raise CloverAuthError("Clover is not connected (no access token)")
        return tok

    def _limiters_for(self, token: str) -> Sequence[RateLimiter]:
        if self._limiters is not None:
            return self._limiters
        return (app_limiter(), token_limiter(token))

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=self.timeout)

    def url(self, path: str) -> str:
        if path and not path.startswith("/"):
            path = "/" + path
        return f"{self.base}/v3/merchants/{self.merchant_id}{path}"

    def get(self, path: str, params: Optional[Params] = None) -> Any:
        """One GET under the merchant, as JSON. 429 waits and retries; one
        401 asks ``on_unauthorized`` (OAuth refresh) and tries once more."""
        label = path.split("?", 1)[0]
        retried_auth = False
        attempt = 0
        with self._client() as client:
            while True:
                token = self.token()
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": user_agent(),
                }
                lims = self._limiters_for(token)
                try:
                    with contextlib.ExitStack() as stack:
                        for lim in lims:
                            stack.enter_context(lim.slot())
                        self.calls += 1
                        _meter()
                        resp = client.get(self.url(path), params=params, headers=headers)
                except httpx.HTTPError as exc:
                    raise CloverError(f"Clover {label}: {type(exc).__name__}") from exc
                if resp.status_code in (401, 403):
                    if resp.status_code == 401 and not retried_auth and self._on_unauthorized is not None:
                        retried_auth = True
                        self._on_unauthorized()
                        continue
                    raise CloverAuthError(
                        f"Clover refused the token for {label} (HTTP {resp.status_code})", resp.status_code
                    )
                if resp.status_code == 429:
                    if attempt < self.max_tries - 1:
                        wait = pos_http.retry_wait_seconds(resp, attempt, MAX_WAIT_SECONDS)
                        logger.warning("Clover 429 on %s, waiting %.1fs", label, wait)
                        self.sleep(wait)
                        attempt += 1
                        continue
                    raise CloverError(f"Clover {label}: still rate limited", 429)
                if resp.status_code >= 400:
                    raise CloverError(f"Clover {label} returned HTTP {resp.status_code}", resp.status_code)
                try:
                    return resp.json()
                except ValueError as exc:
                    raise CloverError(f"Clover {label}: the answer was not JSON") from exc

    def paged(self, path: str, params: Optional[Params] = None, limit: int = PAGE_LIMIT) -> Iterator[dict]:
        """Every element of a list endpoint: ``limit`` per page, ``offset``
        moving on until a page comes back shorter than ``limit``."""
        base: List[Tuple[str, Any]] = list(params.items()) if isinstance(params, dict) else list(params or [])
        offset = 0
        while True:
            page = self.get(path, base + [("limit", limit), ("offset", offset)])
            elements = (page or {}).get("elements") or []
            for el in elements:
                yield el
            if len(elements) < limit:
                return
            offset += len(elements)

    # --- endpoints -----------------------------------------------------------------------------

    def merchant(self) -> dict:
        return self.get("") or {}

    def properties(self) -> dict:
        return self.get("/properties") or {}

    def items(self) -> List[dict]:
        return list(self.paged("/items", [("expand", "itemStock,categories,tags")]))

    def item_stocks(self) -> List[dict]:
        return list(self.paged("/item_stocks"))

    def employees(self) -> List[dict]:
        return list(self.paged("/employees"))

    ORDER_EXPAND = "lineItems,lineItems.modifications,lineItems.discounts,payments,refunds,orderType,employee"

    def orders(
        self,
        start: dt.datetime,
        end: dt.datetime,
        *,
        field: str = "createdTime",
        extra_filters: Sequence[str] = (),
    ) -> List[dict]:
        """Orders whose ``field`` (createdTime / modifiedTime) is in
        [start, end), split into windows of at most 90 days, with their line
        items (an order with more than 100 is completed from
        ``/orders/{id}/line_items``)."""
        out: List[dict] = []
        seen = set()
        for a, b in windows(start, end):
            params: List[Tuple[str, Any]] = [
                ("filter", f"{field}>={to_ms(a)}"),
                ("filter", f"{field}<{to_ms(b)}"),
                ("expand", self.ORDER_EXPAND),
            ]
            params += [("filter", f) for f in extra_filters]
            for order in self.paged("/orders", params):
                oid = order.get("id")
                if oid in seen:
                    continue
                seen.add(oid)
                out.append(self.complete_line_items(order))
        return out

    def complete_line_items(self, order: dict) -> dict:
        lines = ((order.get("lineItems") or {}).get("elements")) or []
        if len(lines) < EXPANDED_LIMIT or not order.get("id"):
            return order
        full = list(self.paged(f"/orders/{order['id']}/line_items", [("expand", "modifications,discounts")]))
        order = dict(order)
        order["lineItems"] = {"elements": full}
        return order
