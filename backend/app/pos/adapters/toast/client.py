"""Toast Standard API: login, the one token cache, and read-only GETs.

Moved unchanged from ``app/toast_client.py`` (login + token cache) and
``app/services/inventory_api.py`` (``toast_get`` and its 429 backoff) in
multi-store M1 batch MT2. Both old paths still work.

There is ONE token cache, ``_TOKEN_CACHE`` (keyed by client id), shared by
the orders, labor, stock and menus calls. ``toast_get`` used to pop that
cache from inside inventory_api; it now calls ``_drop_token()``, which pops
the same key from the same dict (behaviour identical).

The Analytics (ERA) API has its own credentials and its own token - see
``analytics.py``.

NOTE (kept from toast_client.py): field paths follow Toast's published API
shape (POST /authentication/v1/authentication/login for a client-credentials
token). Toast's response envelope has shifted across API versions before -
adjust ``_extract_access_token`` if a live call ever disagrees.
"""

import logging
import time

import httpx

from app.config import get_settings
from app.pos import http as pos_http
from app.tenancy import tenant_local

# Same logger name as before the move, so log lines (and any filter on them)
# stay identical: the 429 warning used to come from inventory_api.
logger = logging.getLogger("app.services.inventory_api")

# Per store, then client id (docs/PLATFORM_CONTRACT.md 6.5).
_TOKEN_CACHE: dict[str, tuple[str, float]] = tenant_local("toast_client.tokens", dict)


class ToastAPIError(RuntimeError):
    pass


class ToastFetchError(RuntimeError):
    pass


def _extract_access_token(payload: dict) -> tuple[str, int]:
    # Toast wraps the token in a "token" object; expiresIn is in seconds.
    token_obj = payload.get("token") or payload
    access_token = token_obj.get("accessToken")
    expires_in = token_obj.get("expiresIn", 3600)
    if not access_token:
        raise ToastAPIError(f"Unexpected Toast auth response shape: {payload!r}")
    return access_token, expires_in


def get_access_token(client: httpx.Client) -> str:
    settings = get_settings()
    cache_key = settings.toast_client_id
    cached = _TOKEN_CACHE.get(cache_key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    resp = client.post(
        f"{settings.toast_api_base}/authentication/v1/authentication/login",
        json={
            "clientId": settings.toast_client_id,
            "clientSecret": settings.toast_client_secret,
            "userAccessType": "TOAST_MACHINE_CLIENT",
        },
        timeout=30,
    )
    resp.raise_for_status()
    access_token, expires_in = _extract_access_token(resp.json())
    _TOKEN_CACHE[cache_key] = (access_token, time.time() + expires_in)
    return access_token


def _drop_token() -> None:
    """Forget the cached token (Toast answered 401: revoked or expired early)."""
    _TOKEN_CACHE.pop(get_settings().toast_client_id, None)


# ---------------------------------------------------------------------------
# Toast GETs (read-only), with rate-limit backoff
# ---------------------------------------------------------------------------

MAX_TRIES = 4
MAX_WAIT_SECONDS = 30.0


def _wait_seconds(resp: httpx.Response, attempt: int) -> float:
    return pos_http.retry_wait_seconds(resp, attempt, MAX_WAIT_SECONDS, reset_header="x-toast-ratelimit-reset")


def _headers(settings, token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Toast-Restaurant-External-ID": settings.toast_restaurant_guid,
    }


def toast_get(path: str, timeout: float = 90.0, max_tries: int = MAX_TRIES):
    """GET one Toast standard-API path as JSON. Backs off on 429 (Retry-After
    or x-toast-ratelimit-reset - prod shares these limits), up to `max_tries`
    attempts (1 = no retry: the live overlay never sleeps on a request); a
    401 (token revoked or expired early) logs in again once. Raises
    ToastFetchError when not configured or still failing - never with a
    secret in it."""
    settings = get_settings()
    if not (settings.toast_client_id and settings.toast_client_secret and settings.toast_restaurant_guid):
        raise ToastFetchError("Toast API credentials are not configured")
    try:
        with httpx.Client(event_hooks=pos_http.metering_hooks()) as client:
            headers = _headers(settings, get_access_token(client))
            relogged = False
            attempt = 0
            while attempt < max(1, max_tries):
                resp = client.get(f"{settings.toast_api_base}{path}", headers=headers, timeout=timeout)
                if resp.status_code == 401 and not relogged:
                    relogged = True
                    _drop_token()
                    headers = _headers(settings, get_access_token(client))
                    continue
                if resp.status_code == 429 and attempt < max_tries - 1:
                    wait = _wait_seconds(resp, attempt)
                    logger.warning("Toast 429 on %s, waiting %.1fs", path, wait)
                    time.sleep(wait)
                    attempt += 1
                    continue
                if resp.status_code >= 400:
                    raise ToastFetchError(f"Toast {path} returned HTTP {resp.status_code}")
                return resp.json()
    except httpx.HTTPError as exc:
        # Timeouts, refused connections, a failed login: no URL or body here.
        raise ToastFetchError(f"Toast {path}: {type(exc).__name__}") from exc
    raise ToastFetchError(f"Toast {path}: still rate limited")
