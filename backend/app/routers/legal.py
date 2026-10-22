"""GET /api/app/legal - legal links and the AI consent notice (MT13).

Public (no login, never 401): the app shows the links on the login screen and
the AI notice before the first Ask Inventory Bot question. Nothing secret is
in the body (only booleans and public URLs/text).
"""

from fastapi import APIRouter, Request, Response

from app.legal import legal_payload

router = APIRouter(prefix="/api/app", tags=["app"])


@router.get("/legal")
def get_legal(request: Request, response: Response) -> dict:
    """Shared service (Store ID privacy, 2026-10): the store's own answer
    only to a live session of that store; anyone else gets the product's,
    the same for every Store ID."""
    from app.routers.branding import neutral_scope, public_cache_headers, signed_in_store

    mine = signed_in_store(request)
    response.headers.update(public_cache_headers(request, "public, max-age=300", signed_in=mine))
    if mine:
        return legal_payload()
    with neutral_scope():
        return legal_payload()


# --- /terms: the Terms of Service draft (pre-production 2026-10-21) -------------------
# Served by the server itself so TERMS_URL=https://app.kloudc.com/terms works
# without the kloudc.com website. The text is app/assets/legal/terms.html,
# identical to docs/legal/TERMS_DRAFT.html (the copy to paste into WordPress).
# Public, logged out, no store needed; registered before the static mount.

import pathlib  # noqa: E402

from fastapi.responses import HTMLResponse  # noqa: E402

TERMS_FILE = pathlib.Path(__file__).resolve().parents[1] / "assets" / "legal" / "terms.html"

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terms of Service - KloudC RetailOps</title>
<style>
body{{margin:0;background:#f7f7f5;color:#1f2328;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:24px 16px 48px;background:#fff}}
h1{{font-size:1.6rem;line-height:1.25}} h2{{font-size:1.15rem;margin-top:1.8em}} a{{color:#0b6bcb}}
@media (prefers-color-scheme: dark){{body{{background:#111;color:#e6e6e6}} main{{background:#1a1a1a}} a{{color:#6cb4ff}}}}
</style></head>
<body><main>
{body}
</main></body></html>
"""

page_router = APIRouter(tags=["legal"])


def terms_html() -> str:
    return _PAGE.format(body=TERMS_FILE.read_text(encoding="utf-8"))


@page_router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms_page() -> HTMLResponse:
    return HTMLResponse(terms_html(), headers={"Cache-Control": "public, max-age=3600"})
