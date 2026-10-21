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


def authorize_link(user: User, return_to: str = "web") -> dict:
    r = _routes()
    code = r._store_admin_context(user)
    r._refuse_demo(user, code)
    auth = _auth_mod()
    if return_to not in auth.RETURN_TARGETS:
        raise PlatformError(400, "invalid", "Unknown return target.", field="return_to")
    if not auth.oauth_enabled():
        raise PlatformError(409, "clover_app_not_configured", NOT_CONFIGURED)
    r._rate("clover_authorize", code, per_minute=10)
    state = auth.make_state(code, user.username, return_to=return_to)
    return {"url": auth.authorize_url(state), "expires_in": auth.STATE_TTL_SECONDS, "return_to": return_to}


@router.get("/authorize-url")
def clover_authorize_url(
    return_to: str = Query(default="web", max_length=8),
    user: User = Depends(require_admin),
):
    """``?return_to=app`` (additive, G4): after Clover, the browser lands on
    the hand-back page (``/app-return``) that opens the app, instead of the
    website. Only "web" (default) and "app" are accepted."""
    return authorize_link(user, return_to=(return_to or "web").strip().lower())


# Results and reasons the callback reports (the hand-back page shows only these).
RESULTS = ("connected", "error")
REASONS = ("expired", "invalid", "denied", "not_configured", "refused")
APP_RETURN_PATH = "/api/settings/integrations/clover/app-return"
DEFAULT_APP_SCHEME = "kloudcretailops"
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]{2,31}$")


def _back(result: str, reason: Optional[str] = None, return_to: Optional[str] = None) -> RedirectResponse:
    base = APP_RETURN_PATH if return_to == "app" else RETURN_PATH
    url = f"{base}?clover={result}"
    if reason:
        url += f"&reason={reason}"
    return RedirectResponse(url, status_code=303)


def app_url_scheme() -> str:
    """The app's URL scheme for the hand-back link (``APP_URL_SCHEME``,
    default "kloudcretailops"); a malformed value falls back to the default."""
    from app.platform.config import pget

    raw = str(pget("app_url_scheme") or "").strip().lower()
    return raw if _SCHEME_RE.match(raw) else DEFAULT_APP_SCHEME


_APP_RETURN_STYLE = (
    "body{margin:0;font:17px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
    "background:#f8fafc;color:#0f172a}"
    "main{max-width:28rem;margin:0 auto;padding:3rem 1rem}"
    "h1{font-size:1.4rem;margin:0 0 1rem}"
    "a.btn{display:block;text-align:center;padding:.85rem;border-radius:.5rem;background:#004bad;"
    "color:#fff;text-decoration:none;font-weight:600;margin-top:1.5rem}"
    "@media (prefers-color-scheme:dark){body{background:#0f172a;color:#e2e8f0}}"
)


def _app_return_csp() -> str:
    import base64
    import hashlib

    digest = base64.b64encode(hashlib.sha256(_APP_RETURN_STYLE.encode("utf-8")).digest()).decode("ascii")
    return (f"default-src 'none'; style-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'")


@router.get("/app-return", include_in_schema=False)
def clover_app_return(
    clover: str = Query(default="", max_length=16),
    reason: str = Query(default="", max_length=32),
):
    """The hand-back page after Connect with Clover started in the app
    (G4). No session, no store data: it only repeats an allowlisted result,
    then opens ``<APP_URL_SCHEME>://clover?clover=...`` (automatically, and
    with a button). Anything else in the query is ignored."""
    from fastapi.responses import HTMLResponse

    result = clover if clover in RESULTS else "error"
    why = reason if (result == "error" and reason in REASONS) else ""
    target = f"{app_url_scheme()}://clover?clover={result}" + (f"&reason={why}" if why else "")
    if result == "connected":
        title = "Clover is connected"
        line = "Going back to the app. If it doesn't open, tap the button below."
    else:
        title = "Clover was not connected"
        line = "Go back to the app and try again. If it keeps failing, use an API token instead."
    body = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<meta http-equiv="refresh" content="0;url={target}">'
        f"<title>{title}</title><style>{_APP_RETURN_STYLE}</style></head>"
        f"<body><main><h1>{title}</h1><p>{line}</p>"
        f'<a class="btn" href="{target}">Open the app</a>'
        "<p>You can close this page.</p></main></body></html>\n"
    )
    headers = {"Cache-Control": "no-store", "Content-Security-Policy": _app_return_csp(),
               "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
    return HTMLResponse(body, headers=headers)


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
    ret = body.get("r") if body.get("r") in auth.RETURN_TARGETS else None
    store_code = str(body["c"])
    actor = f"store:{store_code}:{body.get('u') or 'admin'}"
    if not code or not MERCHANT_ID_RE.match(merchant_id or ""):
        audit("clover.connect_failed", actor_type="store_user", actor_name=actor, store_code=store_code,
              target="clover", details={"reason": "missing_code"}, client_ip=r._ip(request))
        return _back("error", "denied", ret)
    try:
        access, refresh, expires = auth.exchange_code(code)
    except auth.OAuthNotConfigured:
        return _back("error", "not_configured", ret)
    except Exception as exc:  # noqa: BLE001 - CloverError / network: shown as "refused"
        audit("clover.connect_failed", actor_type="store_user", actor_name=actor, store_code=store_code,
              target="clover", details={"reason": type(exc).__name__}, client_ip=r._ip(request))
        return _back("error", "refused", ret)
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
        return _back("error", "invalid", ret)
    _forget_connector(store_code)
    audit("clover.connected", actor_type="store_user", actor_name=actor, store_code=store_code, target="clover",
          details={"mode": "oauth", "merchant_id": merchant_id.upper(), "environment": auth.environment()},
          client_ip=r._ip(request))
    return _back("connected", None, ret)


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
