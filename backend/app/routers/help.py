"""Help content (docs/DESIGN_SYSTEM.md 5.2; app/help_content.py).

GET /api/help         any signed-in user: pages, guides, getting started, FAQ,
                      troubleshooting and contact, this store's words filled in.
GET /api/public/help  no sign-in: only the logged-out pages (sign in, sign-up,
                      owner set-up, public checklist) and contact, generic words.

Both send an ETag (content version + hash) and answer If-None-Match with 304,
gzip when asked. The app keeps its bundled copy unless the server's version
is newer. The glossary (status + concept terms) stays at /api/glossary.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app import help_content
from app.auth import get_current_user

router = APIRouter(prefix="/api/help", tags=["help"], dependencies=[Depends(get_current_user)])
public_router = APIRouter(prefix="/api/public/help", tags=["help"])


def _respond(request: Request, body: dict, cache: str) -> Response:
    etag = help_content.etag_for(body)
    headers = {"ETag": etag, "Cache-Control": cache, "Vary": "Accept-Encoding"}
    if etag in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=headers)
    raw, gz = help_content.encoded(body, request.headers.get("accept-encoding", ""))
    if gz:
        headers["Content-Encoding"] = "gzip"
    return Response(content=raw, media_type="application/json", headers=headers)


@router.get("")
def get_help(request: Request) -> Response:
    return _respond(request, help_content.help_body(), "private, no-cache")


@public_router.get("")
def get_public_help(request: Request) -> Response:
    return _respond(request, help_content.public_help_body(), "public, max-age=300")
