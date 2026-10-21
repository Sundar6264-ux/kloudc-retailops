"""Clover credentials and OAuth v2 (website-ready track D3).

Two ways a store connects:

- **Manual token** (``mode=token``): a merchant-generated API token, merchant
  ID and region, typed by the store admin (single-merchant pilot; Clover
  says such a token is meant for one merchant only). Multi mode keeps it in
  the credentials vault; single mode reads ``CLOVER_MERCHANT_ID``,
  ``CLOVER_API_TOKEN``, ``CLOVER_REGION`` from the environment.
- **Connect with Clover** (``mode=oauth``): the merchant approves our Clover
  app; the callback exchanges the code for a 30-minute access token and a
  refresh token, stored together. ``access_token_for(code)`` refreshes the
  pair before it expires - single flight per store in this process (a lock)
  and across processes (the credential row is locked while refreshing).
  Needs ``CLOVER_APP_ID`` + ``CLOVER_APP_SECRET`` (owner action; off by
  default) and ``CLOVER_ENVIRONMENT`` (sandbox | us | eu | la).

The OAuth ``state`` is sealed (AES-GCM, ``app.platform.crypto``): it names
the store and the admin, carries a nonce and expires after 10 minutes, so it
can't be forged, moved to another store or replayed after expiry; a used
nonce is refused again in this process.

Nothing here calls Clover in tests: the token endpoint goes through
``http_client()``, which tests replace.
"""

import datetime as dt
import json
import secrets as pysecrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from app.pos.adapters.clover.client import API_HOSTS, WEB_HOSTS, CloverAuthError, CloverError, region_of

STATE_TTL_SECONDS = 600
STATE_AAD = b"clover-oauth-state"
# Refresh when less than this is left of the 30-minute access token.
REFRESH_MARGIN_SECONDS = 120
TOKEN_TIMEOUT = 30.0
CALLBACK_PATH = "/api/settings/integrations/clover/callback"


class OAuthNotConfigured(RuntimeError):
    """CLOVER_APP_ID / CLOVER_APP_SECRET are not set (owner action)."""


class StateError(ValueError):
    """The OAuth state is missing, tampered with, expired or already used."""


@dataclass(frozen=True)
class CloverAuth:
    merchant_id: str
    region: str
    mode: str  # "token" | "oauth"
    access_token: str
    refresh_token: str = ""
    expires_at: Optional[dt.datetime] = None
    store_code: Optional[str] = None


def _pget(name: str) -> Any:
    from app.platform.config import pget

    return pget(name)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def app_id() -> str:
    return str(_pget("clover_app_id") or "").strip()


def app_secret() -> str:
    return str(_pget("clover_app_secret") or "").strip()


def oauth_enabled() -> bool:
    return bool(app_id() and app_secret())


def environment() -> str:
    return region_of(_pget("clover_environment") or "sandbox")


def redirect_url() -> str:
    explicit = str(_pget("clover_redirect_url") or "").strip()
    if explicit:
        return explicit
    from app.platform.config import platform_public_url

    return platform_public_url() + CALLBACK_PATH


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_used_lock = threading.Lock()
_used_nonces: Dict[str, float] = {}  # global-state: one-time OAuth state nonces (process-wide, expire in 10 min)


# Where the callback sends the browser back (pre-production gap G4): a fixed
# list, never a URL from the request. "web" = the website's Integrations page
# (the default, as before); "app" = the hand-back page that opens the app.
RETURN_TARGETS = ("web", "app")


def make_state(store_code: str, username: str, now: Optional[float] = None, return_to: str = "web") -> str:
    from app.platform import crypto

    now = time.time() if now is None else now
    body = {"c": store_code, "u": username, "n": pysecrets.token_urlsafe(12), "x": int(now) + STATE_TTL_SECONDS}
    if return_to in RETURN_TARGETS and return_to != "web":
        body["r"] = return_to  # sealed with the rest: the browser can't change it
    return crypto.seal_text(json.dumps(body, sort_keys=True), STATE_AAD)


def read_state(state: str, now: Optional[float] = None, *, consume: bool = True) -> Dict[str, Any]:
    from app.platform import crypto

    now = time.time() if now is None else now
    if not state or len(state) > 2048:
        raise StateError("missing")
    try:
        body = json.loads(crypto.open_text(state, STATE_AAD))
    except (crypto.DecryptError, crypto.KeyRingError, ValueError):
        raise StateError("invalid") from None
    if not isinstance(body, dict) or not body.get("c") or not body.get("n"):
        raise StateError("invalid")
    if int(body.get("x") or 0) < now:
        raise StateError("expired")
    if consume:
        with _used_lock:
            for n, exp in list(_used_nonces.items()):
                if exp < now:
                    del _used_nonces[n]
            if body["n"] in _used_nonces:
                raise StateError("used")
            _used_nonces[body["n"]] = float(body["x"])
    return body


def authorize_url(state: str) -> str:
    if not oauth_enabled():
        raise OAuthNotConfigured("CLOVER_APP_ID / CLOVER_APP_SECRET are not set")
    query = urlencode({"client_id": app_id(), "redirect_uri": redirect_url(), "state": state})
    return f"{WEB_HOSTS[environment()]}/oauth/v2/authorize?{query}"


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


def http_client() -> httpx.Client:
    """The HTTP client for Clover's token endpoint (tests replace this)."""
    return httpx.Client(timeout=TOKEN_TIMEOUT)


def _expiry(value: Any, default_seconds: int = 1800) -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    try:
        n = int(value)
    except (TypeError, ValueError):
        return now + dt.timedelta(seconds=default_seconds)
    if n > 10**11:  # milliseconds
        n //= 1000
    return dt.datetime.fromtimestamp(n, tz=dt.timezone.utc)


def _token_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{API_HOSTS[environment()]}{path}"
    try:
        with http_client() as client:
            resp = client.post(url, json=body, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CloverError(f"Clover token endpoint: {type(exc).__name__}") from exc
    if resp.status_code in (400, 401, 403):
        raise CloverAuthError(f"Clover refused the request (HTTP {resp.status_code})", resp.status_code)
    if resp.status_code >= 400:
        raise CloverError(f"Clover token endpoint returned HTTP {resp.status_code}", resp.status_code)
    try:
        data = resp.json()
    except ValueError as exc:
        raise CloverError("Clover token endpoint: the answer was not JSON") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise CloverError("Clover answered without an access token")
    return data


def exchange_code(code: str) -> Tuple[str, str, dt.datetime]:
    """Authorization code -> (access token, refresh token, access expiry)."""
    if not oauth_enabled():
        raise OAuthNotConfigured("CLOVER_APP_ID / CLOVER_APP_SECRET are not set")
    data = _token_post("/oauth/v2/token", {"client_id": app_id(), "client_secret": app_secret(), "code": code})
    return str(data["access_token"]), str(data.get("refresh_token") or ""), _expiry(data.get("access_token_expiration"))


def refresh_pair(refresh_token: str) -> Tuple[str, str, dt.datetime]:
    if not oauth_enabled():
        raise OAuthNotConfigured("CLOVER_APP_ID / CLOVER_APP_SECRET are not set")
    if not refresh_token:
        raise CloverAuthError("Clover connection expired: connect again")
    data = _token_post("/oauth/v2/refresh", {"client_id": app_id(), "refresh_token": refresh_token})
    return (
        str(data["access_token"]),
        str(data.get("refresh_token") or refresh_token),
        _expiry(data.get("access_token_expiration")),
    )


# ---------------------------------------------------------------------------
# Reading the store's credentials
# ---------------------------------------------------------------------------


def _parse_time(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _from_vault(code: str) -> Optional[CloverAuth]:
    from app.platform import credentials as creds

    try:
        public, secrets_now = creds.stored_values(code, "clover")
    except creds.StoreNotFound:
        return None
    token = secrets_now.get("access_token") or ""
    if not token or not public.get("merchant_id"):
        return None
    return CloverAuth(
        merchant_id=str(public.get("merchant_id")),
        region=region_of(public.get("region")),
        mode=str(public.get("mode") or "token"),
        access_token=token,
        refresh_token=secrets_now.get("refresh_token") or "",
        expires_at=_parse_time(public.get("token_expires_at")),
        store_code=code,
    )


def _from_env() -> Optional[CloverAuth]:
    merchant = str(_pget("clover_merchant_id") or "").strip()
    token = str(_pget("clover_api_token") or "").strip()
    if not merchant or not token:
        return None
    return CloverAuth(merchant_id=merchant, region=region_of(_pget("clover_region") or "us"), mode="token", access_token=token)


def current_auth() -> Optional[CloverAuth]:
    """The current store's Clover credentials, or None (not connected)."""
    from app.platform.config import is_multi
    from app.tenancy.context import current_store_code

    code = current_store_code()
    if is_multi() and code:
        return _from_vault(code)
    return _from_env()


def _needs_refresh(expires_at: Optional[dt.datetime], now: Optional[dt.datetime] = None) -> bool:
    if expires_at is None:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    return (expires_at - now).total_seconds() < REFRESH_MARGIN_SECONDS


_refresh_locks_guard = threading.Lock()
_refresh_locks: Dict[str, threading.Lock] = {}  # global-state: one refresh at a time per store (keyed by store code)


def _store_lock(code: str) -> threading.Lock:
    with _refresh_locks_guard:
        lock = _refresh_locks.get(code)
        if lock is None:
            lock = _refresh_locks[code] = threading.Lock()
        return lock


def refresh_now(code: str, *, force: bool = False) -> CloverAuth:
    """Refresh the store's OAuth pair if it is about to expire (``force``:
    regardless - Clover answered 401). Single flight: one refresh per store
    at a time here, and the row lock covers other processes."""
    from app.platform import credentials as creds

    with _store_lock(code):
        before = _from_vault(code)
        if before is None:
            raise CloverAuthError("Clover is not connected")
        if before.mode != "oauth":
            return before
        seen_token = before.access_token

        def needs(public: Dict[str, Any], secrets_now: Dict[str, str]) -> bool:
            if force:
                # Someone else already replaced the token we were refused with.
                return secrets_now.get("access_token") == seen_token
            return _needs_refresh(_parse_time(public.get("token_expires_at")))

        def do(public: Dict[str, Any], secrets_now: Dict[str, str]):
            access, refresh, expires = refresh_pair(secrets_now.get("refresh_token") or "")
            return {"token_expires_at": expires.isoformat()}, {"access_token": access, "refresh_token": refresh}

        creds.refresh_token_pair(code, "clover", needs, do)
        after = _from_vault(code)
        if after is None:
            raise CloverAuthError("Clover is not connected")
        return after


def fresh_auth() -> CloverAuth:
    """The current store's credentials with a usable access token."""
    auth = current_auth()
    if auth is None:
        raise CloverAuthError("Clover is not connected: connect it in Setup > Integrations")
    if auth.mode == "oauth" and auth.store_code and _needs_refresh(auth.expires_at):
        auth = refresh_now(auth.store_code)
    return auth


def token_provider(auth: CloverAuth) -> Tuple[Callable[[], str], Optional[Callable[[], None]]]:
    """(token callable, on-401 callback) for CloverClient. A manual token is
    used as is; an OAuth token is refreshed when due, and once more when
    Clover answers 401 (revoked or expired early)."""
    if auth.mode != "oauth" or not auth.store_code:
        return (lambda: auth.access_token), None
    code = auth.store_code
    holder = [auth]

    def provide() -> str:
        cur = holder[0]
        if _needs_refresh(cur.expires_at):
            holder[0] = cur = refresh_now(code)
        return cur.access_token

    def unauthorized() -> None:
        holder[0] = refresh_now(code, force=True)

    return provide, unauthorized
