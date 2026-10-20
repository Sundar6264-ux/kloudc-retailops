"""Connect with Clover: status, OAuth authorize + callback, manual token,
disconnect (docs/WEBSITE_READY_CONTRACT.md, track D3).

Prefix /api/settings/integrations/clover (mounted by app/platform/routes.py
install()). Store admins only, their own store (from the session, never the
request). "Test connection" is the shared ``POST /api/settings/integrations/
clover/test`` (connector_tests.test_clover); saved values are listed by the
shared ``GET /api/settings/integrations``.

- ``GET  /status``         what the Integrations page shows (never a secret).
- ``GET  /authorize-url``  a Clover sign-in link with a sealed, store-bound
  ``state`` (10 minutes, single use). 409 ``clover_app_not_configured`` until
  the owner sets CLOVER_APP_ID / CLOVER_APP_SECRET (off by default).
- ``GET  /callback``       Clover sends the merchant back here with ``code``,
  ``merchant_id`` and our ``state``. The state alone proves which store and
  admin asked (the browser may not carry the session). The code is exchanged
  for the token pair, stored atomically in the vault, and the browser goes
  back to Settings > Integrations with ``?clover=connected`` or
  ``?clover=error&reason=...``.
- ``POST /manual``         merchant ID + API token + region (single-merchant
  pilot). Saving re-runs the POS catch-up sync, like Toast keys.
- ``POST /disconnect``     forgets the tokens (audited).

Every write is audited (credential.saved / credential.deleted from the vault,
plus clover.connected / clover.disconnected here).
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth import require_admin
from app.models import User
from app.platform import credentials as creds
from app.platform.audit import audit
from app.platform.errors import PlatformError
from app.pos.adapters.clover import webhooks as _webhooks  # noqa: F401 - registers the clover_webhook_sync job

router = APIRouter(prefix="/api/settings/integrations/clover", tags=["settings"])

# The website uses a hash router: Settings > Integrations is /#/settings/integrations.
RETURN_PATH = "/#/settings/integrations"
MERCHANT_ID_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
REGIONS = ("us", "eu", "la", "sandbox")
NOT_CONFIGURED = (
    "Connect with Clover isn't set up on this service yet. Use an API token from your Clover "
    "dashboard instead, or ask support."
)


def _routes():
    # app.platform.routes imports this module in install(); import lazily.
    from app.platform import routes as r

    return r


def _auth_mod():
    from app.pos.adapters.clover import auth

    return auth


def _forget_connector(code: str) -> None:
    """The store's cached Clover connector re-reads its credentials now."""
    try:
        from app.pos import registry

        registry.reset_cache(code)
    except Exception:  # noqa: BLE001
        pass


def _status_dict(code: Optional[str]) -> dict:
    auth = _auth_mod()
    out = {
        "oauth_enabled": auth.oauth_enabled(),
        "environment": auth.environment(),
        "connected": False,
        "mode": None,
        "merchant_id": None,
        "merchant_name": None,
        "region": None,
        "token_expires_at": None,
        "last4": None,
        "status": "not_configured",
        "last_tested_at": None,
        "last_test_message": None,
        "editable": code is not None,
    }
    if code is None:
        env = auth._from_env()
        if env is not None:
            out.update(connected=True, mode="token", merchant_id=env.merchant_id, region=env.region, status="untested")
        return out
    view = creds.credential_view(code, "clover")
    public = view.public or {}
    out.update(
        connected=bool(view.configured and public.get("merchant_id")),
        mode=public.get("mode") or None,
        merchant_id=public.get("merchant_id") or None,
        merchant_name=public.get("merchant_name") or None,
        region=public.get("region") or None,
        token_expires_at=public.get("token_expires_at") or None,
        last4=view.last4,
        status=view.status,
        last_tested_at=view.last_tested_at,
        last_test_message=view.last_test_message,
    )
    return out


@router.get("/status")
def clover_status(user: User = Depends(require_admin)):
    from app.platform.bridge import current_store_code, is_multi_tenant

    if not is_multi_tenant():
        return _status_dict(None)
    code = current_store_code()
    if not code:
        raise PlatformError(401, "not_logged_in")
    return _status_dict(code)


def authorize_link(user: User) -> dict:
    r = _routes()
    code = r._store_admin_context(user)
    r._refuse_demo(user, code)
    auth = _auth_mod()
    if not auth.oauth_enabled():
        raise PlatformError(409, "clover_app_not_configured", NOT_CONFIGURED)
    r._rate("clover_authorize", code, per_minute=10)
    state = auth.make_state(code, user.username)
    return {"url": auth.authorize_url(state), "expires_in": auth.STATE_TTL_SECONDS}


@router.get("/authorize-url")
def clover_authorize_url(user: User = Depends(require_admin)):
    return authorize_link(user)


def _back(result: str, reason: Optional[str] = None) -> RedirectResponse:
    url = f"{RETURN_PATH}?clover={result}"
    if reason:
        url += f"&reason={reason}"
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
def clover_callback(
    request: Request,
    code: str = Query(default="", max_length=512),
    merchant_id: str = Query(default="", max_length=64),
    state: str = Query(default="", max_length=2048),
):
    """No session needed: the sealed state names the store and the admin."""
    auth = _auth_mod()
    r = _routes()
    try:
        body = auth.read_state(state)
    except auth.StateError as exc:
        return _back("error", "expired" if str(exc) == "expired" else "invalid")
    store_code = str(body["c"])
    actor = f"store:{store_code}:{body.get('u') or 'admin'}"
    if not code or not MERCHANT_ID_RE.match(merchant_id or ""):
        audit("clover.connect_failed", actor_type="store_user", actor_name=actor, store_code=store_code,
              target="clover", details={"reason": "missing_code"}, client_ip=r._ip(request))
        return _back("error", "denied")
    try:
        access, refresh, expires = auth.exchange_code(code)
    except auth.OAuthNotConfigured:
        return _back("error", "not_configured")
    except Exception as exc:  # noqa: BLE001 - CloverError / network: shown as "refused"
        audit("clover.connect_failed", actor_type="store_user", actor_name=actor, store_code=store_code,
              target="clover", details={"reason": type(exc).__name__}, client_ip=r._ip(request))
        return _back("error", "refused")
    try:
        creds.save_credential(
            store_code,
            "clover",
            {"merchant_id": merchant_id.upper(), "region": auth.environment(), "mode": "oauth",
             "token_expires_at": expires.isoformat(), "merchant_name": ""},
            {"access_token": access, "refresh_token": refresh},
            actor=actor,
            client_ip=r._ip(request),
        )
    except (creds.CredentialError, creds.StoreNotFound):
        return _back("error", "invalid")
    _forget_connector(store_code)
    audit("clover.connected", actor_type="store_user", actor_name=actor, store_code=store_code, target="clover",
          details={"mode": "oauth", "merchant_id": merchant_id.upper(), "environment": auth.environment()},
          client_ip=r._ip(request))
    return _back("connected")


class ManualBody(BaseModel):
    merchant_id: str = Field(default="", max_length=64)
    api_token: str = Field(default="", max_length=4096)
    region: str = Field(default="us", max_length=16)


@router.post("/manual")
def clover_manual(body: ManualBody, request: Request, user: User = Depends(require_admin)):
    """Save a merchant API token. A blank token keeps the saved one (only
    when the merchant ID and region stay the same)."""
    r = _routes()
    code = r._store_admin_context(user)
    r._refuse_demo(user, code)
    r._rate("integrations_write", code, per_minute=30)
    merchant = (body.merchant_id or "").strip().upper()
    region = (body.region or "us").strip().lower()
    if not MERCHANT_ID_RE.match(merchant):
        raise PlatformError(400, "invalid", "Enter the Merchant ID from your Clover dashboard (letters and digits).",
                            field="merchant_id")
    if region not in REGIONS:
        raise PlatformError(400, "invalid", "Choose the Clover region: US/Canada, Europe, Latin America or sandbox.",
                            field="region")
    stored_public, stored_secrets = creds.stored_values(code, "clover")
    same_place = (stored_public.get("merchant_id") == merchant and stored_public.get("region") == region
                  and stored_public.get("mode") == "token")
    token = (body.api_token or "").strip()
    if not token and not (same_place and stored_secrets.get("access_token")):
        raise PlatformError(400, "invalid", "Enter the API token.", field="api_token")
    try:
        creds.save_credential(
            code, "clover",
            {"merchant_id": merchant, "region": region, "mode": "token", "token_expires_at": "",
             "merchant_name": stored_public.get("merchant_name", "") if same_place else ""},
            {"access_token": token} if token else {},
            clear=["refresh_token"] if stored_secrets.get("refresh_token") else [],
            actor=r._store_actor(code, user), client_ip=r._ip(request),
        )
    except creds.CredentialError as exc:
        raise PlatformError(400, "invalid", str(exc)) from None
    _forget_connector(code)
    audit("clover.connected", actor_type="store_user", actor_name=r._store_actor(code, user), store_code=code,
          target="clover", details={"mode": "token", "merchant_id": merchant, "region": region},
          client_ip=r._ip(request))
    return _status_dict(code)


@router.post("/disconnect")
def clover_disconnect(request: Request, user: User = Depends(require_admin)):
    r = _routes()
    code = r._store_admin_context(user)
    r._refuse_demo(user, code)
    r._rate("integrations_write", code, per_minute=30)
    public, _ = creds.stored_values(code, "clover")
    deleted = creds.delete_credential(code, "clover", actor=r._store_actor(code, user), client_ip=r._ip(request))
    _forget_connector(code)
    if deleted:
        audit("clover.disconnected", actor_type="store_user", actor_name=r._store_actor(code, user), store_code=code,
              target="clover", details={"mode": public.get("mode"), "merchant_id": public.get("merchant_id")},
              client_ip=r._ip(request))
    return {**_status_dict(code), "disconnected": deleted}


# --- Webhooks (D6, off by default) -------------------------------------------


def _rate_webhook(request: Request) -> None:
    """Unauthenticated webhook calls (the verification POST, a wrong auth
    code): 20 a minute per client address."""
    r = _routes()
    r._rate("clover_webhook_unauth", r._ip(request), per_minute=20)


@router.post("/webhook")
async def clover_webhook(request: Request):
    """Clover's webhook. 404 unless CLOVER_WEBHOOKS_ENABLED; the one-time
    verification POST is logged for the owner; every other POST needs the
    X-Clover-Auth code. Never says which stores exist."""
    import hmac
    import json
    import logging

    from app.pos.adapters.clover import webhooks

    if not webhooks.enabled():
        raise PlatformError(404, "not_found")
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise PlatformError(413, "too_large")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise PlatformError(400, "invalid") from None
    if isinstance(payload, dict) and payload.get("verificationCode") and not payload.get("merchants"):
        # Only letters, digits, - and _ reach the log: a forged line break
        # cannot write fake log lines (docs/SECURITY_REVIEW.md SR-06).
        code = re.sub(r"[^A-Za-z0-9_-]", "", str(payload.get("verificationCode")))[:64]
        if not code:
            raise PlatformError(400, "invalid")
        _rate_webhook(request)
        logging.getLogger(__name__).warning("Clover webhook verification code (paste it into the Clover app): %s", code)
        return {"ok": True}
    expected = webhooks.auth_code()
    given = request.headers.get("x-clover-auth") or ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        _rate_webhook(request)  # wrong codes are slowed down per address
        raise PlatformError(401, "unauthorized")
    from starlette.concurrency import run_in_threadpool

    result = await run_in_threadpool(webhooks.handle, payload if isinstance(payload, dict) else {})
    return {"ok": True, "queued": len(result["queued"])}
