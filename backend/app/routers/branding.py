"""Public store branding (StoreOps M2, docs/M2_CONTRACT.md section 2).

- ``GET /api/app/branding``: the store's names, logo, colours, time zone, POS,
  capabilities, store features and legal links. No login, no cookies read,
  never 401; cached by clients (ETag + 304).
- ``GET /branding/logo.png`` and ``/branding/icon.png``: the bundled StoreOps
  placeholder images (a store with its own logo sets LOGO_URL / ICON_URL).

Registered before the website's static mount at "/" (app/main.py).
"""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from app.store.branding import branding_payload, compact_json, etag_for

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


def public_cache_headers(request: Request, cache_control: str) -> dict:
    """Cache headers of a public per-store answer. On the shared service the
    store may come from a login token or the X-Store header instead of
    ``?store=``; such an answer is private (a shared cache must never hand
    one store's answer to another store's URL), and every answer varies on
    them."""
    from app.tenancy import is_multi_tenant

    headers = {"Cache-Control": cache_control, "Vary": "Accept-Encoding, " + STORE_VARY}
    if is_multi_tenant() and "store" not in request.query_params:
        headers["Cache-Control"] = "private, max-age=300"
    return headers


def _request_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/branding")
def branding(request: Request) -> Response:
    body = compact_json(branding_payload(request_base=_request_base(request)))
    etag = etag_for(body)
    headers = {**public_cache_headers(request, CACHE_CONTROL), "ETag": etag}
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
