"""Client for Toast's Analytics API (`/era/v1`, scope enterprise-metrics:read).

Moved unchanged from `app/services/toast_analytics.py` in multi-store M1
batch MT2; the old module name still works (every name forwards here, so a
test patching `toast_analytics._sleep` / `_now` on the old path still works).

A different product from the Orders/Menus/Stock API in `client.py`:
its own client id/secret (settings TOAST_ANALYTICS_CLIENT_ID / _SECRET, base
TOAST_ANALYTICS_BASE), and every report is two steps:

1. POST /era/v1/<kind>/<timeRange> (or /era/v1/<kind> for a custom range)
   with the date range -> a `reportRequestGuid` (a bare JSON string).
2. GET /era/v1/<kind>/<reportRequestGuid> until it answers 200 with the rows.
   202 = still gathering (poll again later); 404 = the guid is invalid or
   expired (they live 7 days); 409 = Toast couldn't process it, ask for a new
   guid; 500 / 504 = Toast-side failure or timeout, worth one more try.

Rate limits (docs/apiAnalyticsRateLimiting, prod shares them with every job):
day/week report POSTs 10 per minute and 60 per hour per endpoint; custom,
month and year POSTs 10 per hour; check and payments POSTs 60 per DAY; result
GETs 30 per minute. This client keeps its own sliding-window budget per
endpoint family (so a busy job waits instead of tripping Toast's limit),
honours `x-toast-ratelimit-remaining` / `-reset` and backs off on 429
(Retry-After when Toast sends one).

Not configured (no client id or secret) -> `AnalyticsNotConfigured`, and the
callers fall back to what the app did before (see sales_accuracy.py). Secrets
never reach a log line or an exception message.

Only read-only reports are used (POST here creates a report request; it
changes nothing in Toast).
"""

import collections
import datetime as dt
import logging
import time
from typing import Callable, Optional

import httpx

from app.config import get_settings
from app.tenancy import tenant_local, tenant_lock

# Same logger name as before the move, so log lines stay identical.
logger = logging.getLogger("app.services.toast_analytics")

ERA_PREFIX = "/era/v1"


class AnalyticsError(RuntimeError):
    """Any Analytics failure the caller should record and move on from."""


class AnalyticsNotConfigured(AnalyticsError):
    pass


class AnalyticsRateLimited(AnalyticsError):
    """Our own budget (or Toast's) says no more calls for a while."""


class AnalyticsReportFailed(AnalyticsError):
    pass


class AnalyticsReportUnprocessable(AnalyticsReportFailed):
    """Toast kept answering 404/409 ("couldn't process it") for new requests
    of the same report: asking again right away won't help (insights_data
    then marks those dates unavailable and retries them once a night)."""


# (max calls, window seconds) per endpoint family - Toast's documented limits,
# kept a little under so a retry still fits.
_LIMITS: dict[str, list[tuple[int, float]]] = {
    "post_dayweek": [(9, 60.0), (55, 3600.0)],
    "post_long": [(9, 3600.0)],  # custom range, month, year
    "post_daily": [(4, 60.0), (55, 86400.0)],  # check, payout/payments, guest/payments
    "get": [(25, 60.0)],
    "login": [(10, 60.0)],
}
_DAILY_KINDS = ("check", "payout/payments", "guest/payments")

# Per store (docs/PLATFORM_CONTRACT.md 6.5): each store has its own
# Analytics credentials, so its own token and call budget.
_lock = tenant_lock("toast_analytics.calls")
_calls: dict[str, collections.deque] = tenant_local(
    "toast_analytics.calls", lambda: collections.defaultdict(collections.deque)
)
# Earliest time (epoch) the next call to an endpoint may go out, from Toast's
# own x-toast-ratelimit-* headers or a 429.
_blocked_until: dict[str, float] = tenant_local("toast_analytics.blocked_until", dict)
# {"token": (access token, expires at epoch) or None}
_token_state: dict = tenant_local("toast_analytics.token", lambda: {"token": None})
# Kept only so the old module path still lists the name (app.pos.compat); the
# live token is per store in _token_state above.
_token: Optional[tuple[str, float]] = None
_token_lock = tenant_lock("toast_analytics.token")

# Seams for tests (never sleep / never read the clock for real in unit tests).
_sleep: Callable[[float], None] = time.sleep
_now: Callable[[], float] = time.time


def is_configured() -> bool:
    s = get_settings()
    return bool(s.toast_analytics_client_id and s.toast_analytics_client_secret and s.toast_restaurant_guid)


def _base() -> str:
    return (get_settings().toast_analytics_base or "https://ws-api.toasttab.com").rstrip("/")


def reset_state() -> None:
    """Forget the token and the call budget (tests; a settings change)."""
    with _lock:
        _calls.clear()
        _blocked_until.clear()
    with _token_lock:
        _token_state["token"] = None


def _family(method: str, path: str) -> str:
    if path.startswith("/authentication"):
        return "login"
    if method == "GET":
        return "get"
    rest = path[len(ERA_PREFIX) + 1:] if path.startswith(ERA_PREFIX) else path
    for kind in _DAILY_KINDS:
        if rest.startswith(kind + "/") or rest == kind:
            return "post_daily"
    if rest.endswith("/day") or rest.endswith("/week"):
        return "post_dayweek"
    return "post_long"


def _endpoint_key(method: str, path: str) -> str:
    """Toast limits per endpoint: every result GET shares one budget per
    report kind, every POST is its own path."""
    if method == "GET" and path.startswith(ERA_PREFIX):
        kind = path[len(ERA_PREFIX) + 1:].split("/")[0]
        return f"GET {kind}"
    return f"{method} {path}"


def _wait_for_budget(method: str, path: str, max_wait: float) -> None:
    """Blocks until one more call fits every window for this endpoint, or
    raises AnalyticsRateLimited when that would take longer than max_wait."""
    family = _family(method, path)
    key = _endpoint_key(method, path)
    waited = 0.0
    while True:
        with _lock:
            now = _now()
            wait = max(0.0, _blocked_until.get(key, 0.0) - now)
            q = _calls[key]
            for limit, window in _LIMITS[family]:
                while q and q[0] <= now - 3600 * 24:
                    q.popleft()
                recent = [t for t in q if t > now - window]
                if len(recent) >= limit:
                    wait = max(wait, recent[-limit] + window - now)
            if wait <= 0:
                q.append(now)
                return
        if waited + wait > max_wait:
            raise AnalyticsRateLimited(
                f"Toast Analytics rate limit for {key}: next call in {int(wait)}s (budget wait {int(max_wait)}s)"
            )
        _sleep(wait)
        waited += wait


def _note_headers(method: str, path: str, resp: httpx.Response) -> None:
    """x-toast-ratelimit-remaining 0 -> hold this endpoint until -reset."""
    remaining = resp.headers.get("x-toast-ratelimit-remaining")
    reset = resp.headers.get("x-toast-ratelimit-reset")
    if remaining is None or reset is None:
        return
    try:
        if int(float(remaining)) > 0:
            return
        reset_at = float(reset)
    except ValueError:
        return
    if reset_at > 10_000_000_000:  # milliseconds
        reset_at /= 1000.0
    with _lock:
        key = _endpoint_key(method, path)
        _blocked_until[key] = max(_blocked_until.get(key, 0.0), min(reset_at, _now() + 3600))


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    ra = resp.headers.get("Retry-After")
    try:
        if ra is not None:
            return max(1.0, float(ra))
    except ValueError:
        pass
    return min(60.0, 5.0 * (2 ** attempt))


def _login(client: httpx.Client) -> str:
    s = get_settings()
    with _token_lock:
        token = _token_state["token"]
        if token and token[1] > _now() + 120:
            return token[0]
        _wait_for_budget("POST", "/authentication/v1/authentication/login", max_wait=60)
        resp = client.post(
            _base() + "/authentication/v1/authentication/login",
            json={
                "clientId": s.toast_analytics_client_id,
                "clientSecret": s.toast_analytics_client_secret,
                "userAccessType": "TOAST_MACHINE_CLIENT",
            },
            timeout=60,
        )
        if resp.status_code != 200:
            # Never echo the body: some auth errors repeat the request.
            raise AnalyticsError(f"Toast Analytics login failed (HTTP {resp.status_code})")
        tok = (resp.json() or {}).get("token") or {}
        access = tok.get("accessToken")
        if not access:
            raise AnalyticsError("Toast Analytics login answered without a token")
        expires_in = float(tok.get("expiresIn") or 3600)
        _token_state["token"] = (access, _now() + expires_in)
        return access


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    max_budget_wait: float = 120.0,
    max_429: int = 3,
) -> httpx.Response:
    """One authenticated call with budget, header and 429 handling. Returns
    the response for every status other than 401 (re-login once) and 429."""
    relogged = False
    attempt = 0
    while True:
        token = _login(client)
        _wait_for_budget(method, path, max_wait=max_budget_wait)
        resp = client.request(
            method,
            _base() + path,
            json=json_body,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=180,
        )
        _note_headers(method, path, resp)
        if resp.status_code == 401 and not relogged:
            with _token_lock:
                _token_state["token"] = None
            relogged = True
            continue
        if resp.status_code == 429:
            wait = _retry_after(resp, attempt)
            with _lock:
                key = _endpoint_key(method, path)
                _blocked_until[key] = max(_blocked_until.get(key, 0.0), _now() + wait)
            attempt += 1
            if attempt > max_429:
                raise AnalyticsRateLimited(f"Toast Analytics answered 429 for {method} {path} {attempt} times")
            logger.info("Toast Analytics 429 on %s %s, waiting %.0fs", method, path, wait)
            continue
        return resp


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(body, dict) and body.get("message"):
        return f"HTTP {resp.status_code}: {str(body['message'])[:200]}"
    return f"HTTP {resp.status_code}"


def run_report(
    kind: str,
    time_range: Optional[str],
    body: dict,
    params: Optional[dict] = None,
    *,
    max_wait: float = 300.0,
    max_recreates: int = 2,
    client: Optional[httpx.Client] = None,
    max_budget_wait: float = 120.0,
    before_post: Optional[Callable[[int], None]] = None,
) -> list:
    """Create a report and poll it until Toast hands back the rows.

    kind: "metrics", "menu", "check", ... ; time_range: "day" / "week" /
    "month" / "year", or None for the custom-range endpoint.
    Raises AnalyticsNotConfigured, AnalyticsRateLimited or
    AnalyticsReportFailed (with Toast's own message, never a secret).
    max_budget_wait: how long one call may wait for our own rate budget
    before giving up with AnalyticsRateLimited (a request path passes a few
    seconds so it fails fast instead of queueing).
    before_post: called with the attempt number (1, 2, ...) before every
    report-creating POST, retries and new requests included - each one counts
    against Toast's limit (the 60-a-day reports: insights_data keeps its own
    count with it). Raising from it stops the report.
    """
    if not is_configured():
        raise AnalyticsNotConfigured("Toast Analytics credentials are not set (TOAST_ANALYTICS_CLIENT_ID/SECRET)")
    post_path = f"{ERA_PREFIX}/{kind}" + (f"/{time_range}" if time_range else "")
    own_client = client is None
    client = client or httpx.Client()
    started = _now()
    try:
        recreates = 0
        server_errors = 0
        posts = 0
        while True:
            posts += 1
            if before_post is not None:
                before_post(posts)
            resp = _request(
                client, "POST", post_path, json_body=body, params=params, max_budget_wait=max_budget_wait
            )
            if resp.status_code in (500, 502, 503, 504) and server_errors < 2:
                server_errors += 1
                _sleep(5.0 * server_errors)
                continue
            if resp.status_code != 200:
                raise AnalyticsReportFailed(f"Toast Analytics {post_path}: {_error_text(resp)}")
            guid = resp.json()
            if not isinstance(guid, str) or not guid:
                raise AnalyticsReportFailed(f"Toast Analytics {post_path}: no reportRequestGuid in the answer")

            delay = 2.0
            recreate = False
            while True:
                if _now() - started > max_wait:
                    raise AnalyticsReportFailed(
                        f"Toast Analytics {kind} report still not ready after {int(max_wait)}s"
                    )
                _sleep(delay)
                resp = _request(client, "GET", f"{ERA_PREFIX}/{kind}/{guid}", max_budget_wait=max_budget_wait)
                if resp.status_code == 200:
                    data = resp.json()
                    if data is None:
                        return []
                    return data if isinstance(data, list) else [data]
                if resp.status_code == 202:
                    delay = min(delay * 1.5, 15.0)
                    continue
                if resp.status_code in (404, 409):
                    # 404: guid invalid/expired; 409: Toast couldn't process
                    # it. Both say: ask for a new guid.
                    recreate = True
                    break
                if resp.status_code in (500, 502, 503, 504) and server_errors < 3:
                    server_errors += 1
                    delay = min(delay * 2, 30.0)
                    continue
                raise AnalyticsReportFailed(f"Toast Analytics {kind} report: {_error_text(resp)}")
            if recreate:
                recreates += 1
                if recreates > max_recreates:
                    raise AnalyticsReportUnprocessable(
                        f"Toast Analytics {kind} report could not be processed ({_error_text(resp)}) "
                        f"after {max_recreates} new requests"
                    )
                logger.info("Toast Analytics %s report %s -> new request", kind, _error_text(resp))
                continue
    finally:
        if own_client:
            client.close()


def _ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _body(start: dt.date, end: dt.date, group_by: Optional[list] = None) -> dict:
    body = {
        "startBusinessDate": _ymd(start),
        "endBusinessDate": _ymd(end),
        "restaurantIds": [get_settings().toast_restaurant_guid],
        "excludedRestaurantIds": [],
    }
    if group_by:
        body["groupBy"] = group_by
    return body


def _week_chunks(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """[start, end] split into runs of at most 7 days - the `week` time range
    (10 per minute) is far cheaper than custom ranges (10 per hour)."""
    chunks = []
    d = start
    while d <= end:
        last = min(end, d + dt.timedelta(days=6))
        chunks.append((d, last))
        d = last + dt.timedelta(days=1)
    return chunks


def _parse_business_date(raw) -> Optional[dt.date]:
    if raw is None:
        return None
    s = str(raw)
    try:
        if len(s) == 8 and s.isdigit():
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


METRIC_FIELDS = (
    "netSalesAmount",
    "grossSalesAmount",
    "discountAmount",
    "voidOrdersAmount",
    "refundAmount",
    "ordersCount",
    "voidOrdersCount",
    "openOrdersCount",
    "closedOrdersCount",
    "guestCount",
)
MENU_FIELDS = ("netSalesAmount", "grossSalesAmount", "discountAmount", "voidAmount", "refundAmount", "quantitySold")


def _sum_by_date(rows: list, fields: tuple) -> dict:
    out: dict = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = _parse_business_date(r.get("businessDate"))
        if d is None:
            continue
        agg = out.setdefault(d, {f: 0.0 for f in fields})
        for f in fields:
            agg[f] += _num(r.get(f))
    for agg in out.values():
        for f in fields:
            agg[f] = round(agg[f], 4)
    return out


def day_metrics(start: dt.date, end: dt.date) -> dict:
    """Toast's own per-business-date sales metrics (net/gross/discount/void/
    refund amounts and order counts), {date: {...}}. A date Toast has no row
    for (no sales) is simply absent."""
    rows: list = []
    for a, b in _week_chunks(start, end):
        rows.extend(run_report("metrics", "day" if a == b else "week", _body(a, b)))
    return _sum_by_date(rows, METRIC_FIELDS)


def menu_day_totals(start: dt.date, end: dt.date) -> dict:
    """Item-level sales per business date - the sum over every menu item
    (top-level selections, modifiers not counted separately, discounts
    prorated onto items), {date: {netSalesAmount, quantitySold, ...}}. This is
    the number our stored `orders` (top-level, non-voided net_price) should
    equal once a date is fully synced."""
    rows: list = []
    for a, b in _week_chunks(start, end):
        rows.extend(run_report("menu", "day" if a == b else "week", _body(a, b)))
    return _sum_by_date(rows, MENU_FIELDS)


def menu_items_for_day(day: dt.date) -> list:
    """One business date's per-item rows (menuItemGuid, menuItemName,
    quantitySold, netSalesAmount, ...)."""
    return run_report("menu", "day", _body(day, day, ["MENU_ITEM"]))
