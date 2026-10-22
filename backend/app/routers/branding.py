"""Store branding (KloudC RetailOps M2, docs/M2_CONTRACT.md section 2).

- ``GET /api/app/branding``: the store's names, logo, colours, time zone, POS,
  capabilities, store features and legal links. Never 401; cached by clients
  (ETag + 304). A single-store server answers its store to everyone (as
  before). The shared service (Store ID privacy, 2026-10) answers the
  store's own look only to a live session of that store (cookie or Bearer;
  ``Cache-Control: private``); anyone else - any ``?store=`` code, real or
  unknown, a forged or expired token - gets the product's neutral body
  (``store_specific: false``), identical for every code apart from the
  echoed ``store_code``.
- ``GET /branding/logo.png`` and ``/branding/icon.png``: the bundled KloudC RetailOps
  placeholder images (a store with its own logo sets LOGO_URL / ICON_URL).

Registered before the website's static mount at "/" (app/main.py).
"""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from app.store.branding import branding_payload, compact_json, etag_for, neutral_branding_payload

router = APIRouter(prefix="/api/app", tags=["app"])
assets_router = APIRouter(tags=["app"])

CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=86400"
ASSET_CACHE_CONTROL = "public, max-age=86400"
ASSETS_DIR = pathlib.Path(__file__).resolve().parents[1] / "assets" / "branding"
# Published name -> the bundled file (backend/app/assets/branding).
BUNDLED = {"logo.png": "logo-512.png", "icon.png": "app-icon-1024.png"}


# What else picks the store on the shared service besides the URL
# (app/tenancy/middleware.py): a shared cache must key on them too.
STORE_VARY = "X-Store, Authorization, Cookie"


def public_cache_headers(request: Request, cache_control: str, signed_in: bool = False) -> dict:
    """Cache headers of a public per-store answer. On the shared service the
    store may come from a login token or the X-Store header instead of
    ``?store=``; such an answer is private (a shared cache must never hand
    one store's answer to another store's URL), and every answer varies on
    them. A signed-in (store-specific) answer is always private there."""
    from app.tenancy import is_multi_tenant

    headers = {"Cache-Control": cache_control, "Vary": "Accept-Encoding, " + STORE_VARY}
    if is_multi_tenant() and (signed_in or "store" not in request.query_params):
        headers["Cache-Control"] = "private, max-age=300"
    return headers


def signed_in_store(request: Request) -> bool:
    """May this request see the store's own branding, config and legal?

    Single-store server: always (the server is the store; unchanged).
    Shared service: only when the request's session (cookie or Bearer, as
    the tenancy middleware reads it) is a live session in the database of
    the store that token selected. Unknown, inactive or moved stores, a
    master's token and forged or expired tokens all answer False."""
    from app.tenancy import is_multi_tenant

    if not is_multi_tenant():
        return True
    from app.tenancy.context import current_store
    from app.tenancy.middleware import request_token

    if current_store() is None:
        return False
    token = request_token(request.scope)
    if not token:
        return False
    from app.auth import lookup_session
    from app.db import SessionLocal

    try:
        db = SessionLocal()
    except Exception:  # noqa: BLE001 - store database refused / down: neutral
        return False
    try:
        return lookup_session(db, token) is not None
    except Exception:  # noqa: BLE001
        return False
    finally:
        db.close()


def requested_store_code(request: Request):
    """The valid Store ID the caller named (``?store=`` or X-Store), or None."""
    from app.tenancy.registry import is_valid_store_code, normalize_store_code

    raw = request.query_params.get("store") or request.headers.get("x-store")
    code = normalize_store_code(raw) if raw else None
    return code if code and is_valid_store_code(code) else None


def neutral_scope():
    """No store selected (the product's own answer)."""
    from app.tenancy.context import no_store

    return no_store()


def _request_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/branding")
def branding(request: Request) -> Response:
    from app.tenancy import is_multi_tenant

    base = _request_base(request)
    mine = signed_in_store(request)
    if mine:
        payload = branding_payload(request_base=base)
        if is_multi_tenant():
            # Additive, shared service only (a single-store server's body
            # stays exactly as before; no key = the store's own look).
            payload["store_specific"] = True
    else:
        payload = neutral_branding_payload(base, requested_store_code(request))
    body = compact_json(payload)
    etag = etag_for(body)
    headers = {**public_cache_headers(request, CACHE_CONTROL, signed_in=mine), "ETag": etag}
    wanted = [t.strip() for t in (request.headers.get("if-none-match") or "").split(",") if t.strip()]
    if etag in wanted or any(t == f"W/{etag}" for t in wanted) or "*" in wanted:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@assets_router.get("/branding/{name}", include_in_schema=False)
def branding_asset(name: str) -> Response:
    filename = BUNDLED.get(name)
    path = ASSETS_DIR / filename if filename else None
    if path is None or not path.is_file():
        return Response(status_code=404)
    return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": ASSET_CACHE_CONTROL})
