"""Public checklist link - the logged-out routes (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md).

Deliberately NO login dependency: a valid, enabled link ("{slug}-{code}")
is the only way in, and it reaches exactly one checklist's form, submit and photo
upload. Logic and the security notes: services/checklist_public.py.
The admin routes (configure / rotate / delete) live in
routers/checklist_admin.py behind require_admin.

Every response here carries no-store / no-referrer / noindex headers, errors
are short fixed messages (never a stack trace), and an unknown, disabled or
rotated link always gets the same 404.
"""

import logging
from typing import Optional
from html import escape as _html_escape
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import frontend_dist_dir
from app.db import SessionLocal, get_db
from app.services import checklist_public as svc
from app.store import settings_view as sv
from app.services.checklist_submission import (
    ChecklistStaleVersionError,
    ChecklistValidationError,
    PublicSubmitter,
    create_submission,
    queue_whatsapp,
    send_checklist_notification,
)
from app.team import media as media_svc
from app.team.source_sync import task_sources_changed

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/public/checklists", tags=["public-checklists"])
page_router = APIRouter(tags=["public-checklists"])

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    # The token is in the URL: never send it on to another site.
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
PAGE_CSP = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'"
)
SOMETHING_WRONG = "Something went wrong. Try again."


def _json(status: int, content: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=content, headers=SECURITY_HEADERS)


def _error(status: int, detail: str) -> JSONResponse:
    return _json(status, {"detail": detail})


def _not_active() -> JSONResponse:
    return _error(404, svc.NOT_ACTIVE)


def _ip(request: Request) -> str:
    return svc.client_ip(request.headers, request.client.host if request.client else None)


def _link_db(request: Request):
    """The store's database for a link lookup, or None when the shared
    service's ``?store=`` code is unknown, inactive or on another server
    (the tenancy middleware marks that). None reads exactly like a wrong
    link: same 404, same failed-lookup count (Store ID privacy, 2026-10)."""
    if getattr(request.state, "checklist_store_unknown", False):
        yield None
        return
    yield from get_db()


def _lookup(db: Optional[Session], key: str, ip: str):
    """The active link for {key} ("{slug}-{code}", or an old bare token), or
    None. Raises PublicError(429) once this IP has too many failed lookups;
    a miss counts as one. No database (an unknown Store ID) is a miss."""
    svc.check_failed(ip)
    link = svc.find_active_link(db, key) if db is not None else None
    if link is None:
        svc.record_failed(ip)
    return link


@public_router.get("/{token}")
def public_form(token: str, request: Request, db: Optional[Session] = Depends(_link_db)):
    """The form: {checklist_type, title, description, schema, name_mode, allowed_names}."""
    try:
        ip = _ip(request)
        svc.check_ip(ip)
        link = _lookup(db, token, ip)
        if link is None:
            return _not_active()
        view = svc.public_view(link)
        # Whose page this is (additive). Only behind a working link: the
        # public branding no longer names a store before sign-in (Store ID
        # privacy, 2026-10), and the website's checklist page shows this.
        view["store"] = {"name": sv.store_display_name(), "short_name": sv.store_short_name(),
                         "logo_url": sv.logo_path_or_url()}
        return _json(200, view)
    except svc.PublicError as exc:
        return _error(exc.status_code, exc.detail)
    except Exception:  # noqa: BLE001 - never leak details to a logged-out caller
        logger.exception("Public checklist form failed")
        return _error(500, SOMETHING_WRONG)


async def _read_body(request: Request, limit: int) -> bytes:
    raw = request.headers.get("content-length")
    if raw is None:
        raise svc.PublicError(411, "Send a Content-Length.")
    try:
        length = int(raw)
    except ValueError:
        raise svc.PublicError(400, "Bad Content-Length.")
    if length > limit:
        raise svc.PublicError(413, "That's too much data for one checklist.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise svc.PublicError(413, "That's too much data for one checklist.")
    return bytes(body)


def _submit(db: Session, link, body: dict, background_tasks: BackgroundTasks) -> JSONResponse:
    svc.check_link_hourly(db, link)
    try:
        name = svc.resolve_name(link, body["name"])
    except ValueError as exc:
        return _error(400, str(exc))
    submitter = PublicSubmitter(link.id, name, media_svc.PUBLIC_PENDING_OWNER_TYPE, body["media_keys"])
    try:
        # Exactly the app's submit (validation, flags, duplicate replay,
        # stored row); only the submitter differs.
        entry = create_submission(db, None, link.checklist_type, body["answers"], body["definition_version"], submitter)
    except ChecklistStaleVersionError as exc:
        return _error(409, str(exc))
    except ChecklistValidationError as exc:
        return _error(400, str(exc))
    except ValueError:
        return _not_active()
    # Same WhatsApp step as the app's submit (routers/checklists.py).
    queue_whatsapp(background_tasks, entry, send_checklist_notification)
    svc.mark_used(db, link)
    task_sources_changed("checklist")  # its Team task closes now (Oct 15)
    return _json(200, {"ok": True, "all_clear": entry.all_clear, "issue_summary": entry.issue_summary})


@public_router.post("/{token}/submit")
async def public_submit(token: str, request: Request, background_tasks: BackgroundTasks, db: Optional[Session] = Depends(_link_db)):
    """{name, answers, definition_version?, media_keys?} -> {ok, all_clear, issue_summary}
    only. Same validation / flags / WhatsApp / alerts as the app's submit."""
    try:
        ip = _ip(request)
        svc.check_ip(ip)
        link = _lookup(db, token, ip)
        if link is None:
            return _not_active()
        body = svc.parse_submit_body(await _read_body(request, svc.MAX_SUBMIT_BYTES))
        response = await run_in_threadpool(_submit, db, link, body, background_tasks)
        response.background = background_tasks
        return response
    except svc.PublicError as exc:
        return _error(exc.status_code, exc.detail)
    except Exception:  # noqa: BLE001
        logger.exception("Public checklist submit failed")
        return _error(500, SOMETHING_WRONG)


@public_router.post("/{token}/media")
async def public_media(token: str, request: Request, db: Optional[Session] = Depends(_link_db)):
    """A photo for a photo question (multipart field `file`; same checks and
    10 MB cap as staff photos). Returns {id, key, width, height} - no URL:
    the photo can't be viewed through the link. Send `id` in the answer and
    {id: key} in the submit's media_keys."""
    try:
        ip = _ip(request)
        svc.check_ip(ip)
        link = _lookup(db, token, ip)
        if link is None:
            return _not_active()
        if not svc.has_photo_question(link):
            return _error(400, "This checklist has no photo questions.")
        svc.check_media_rate(link)
        try:
            media_svc.check_request_size(request)
        except HTTPException as exc:
            return _error(exc.status_code, str(exc.detail))
        try:
            form = await request.form(max_files=1, max_fields=2)
        except StarletteHTTPException:  # fastapi's HTTPException is a subclass, not this
            # Starlette's own 400 for a malformed body / too many files or
            # fields - a client mistake, not a server error.
            return _error(400, "Send one photo as 'file'.")
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return _error(422, "Attach the photo as 'file'.")
            try:
                media = await run_in_threadpool(
                    media_svc.save_upload, db, None, upload, media_svc.PUBLIC_PENDING_OWNER_TYPE, link.id
                )
            except HTTPException as exc:
                return _error(exc.status_code, str(exc.detail))
        finally:
            await form.close()
        return _json(200, {"id": media.id, "key": media.token, "width": media.width, "height": media.height})
    except svc.PublicError as exc:
        return _error(exc.status_code, exc.detail)
    except Exception:  # noqa: BLE001
        logger.exception("Public checklist photo upload failed")
        return _error(500, SOMETHING_WRONG)


@public_router.get("/{token}/images/{media_token}")
def public_form_image(token: str, media_token: str, request: Request, db: Optional[Session] = Depends(_link_db)):
    """A picture the form itself shows (an image item added in the builder) -
    never an uploaded answer photo."""
    try:
        ip = _ip(request)
        svc.check_ip(ip, page=True)
        link = _lookup(db, token, ip)
        media = svc.form_image(db, link, media_token) if link is not None else None
        if media is None:
            return _not_active()
        headers = dict(SECURITY_HEADERS)
        headers["Cache-Control"] = "private, max-age=300"
        response = media_svc.media_response(media, headers)  # local file or object storage
        return response if response is not None else _not_active()
    except svc.PublicError as exc:
        return _error(exc.status_code, exc.detail)
    except Exception:  # noqa: BLE001
        logger.exception("Public checklist image failed")
        return _not_active()


# --- The page ------------------------------------------------------------------------

_NOT_ACTIVE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Link not active</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#faf7f2;color:#2a211b;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
main{max-width:420px;text-align:center}
h1{font-size:1.4rem;margin:0 0 .5rem}
p{margin:0;color:#6b5d52;line-height:1.5}
.brand{display:flex;align-items:center;justify-content:center;gap:10px;font-size:1.3rem;font-weight:700;margin:0 0 1.25rem}
.brand img{width:36px;height:36px;border-radius:8px}
</style></head>
<body><main><div class="brand"><img src="%s" alt="" width="36" height="36"><span>%s</span></div><h1>%s</h1><p>%s</p></main></body></html>"""


def _page_headers(csp: str) -> dict:
    headers = dict(SECURITY_HEADERS)
    headers["Content-Security-Policy"] = csp
    return headers


def _page_look():
    """(logo, name) for the small message pages. The shared service always
    shows the product's own look: the store of a /c link is in the path, not
    the request's session, and a message page must never say which stores
    exist (Store ID privacy, 2026-10). A single-store server shows its store."""
    from app.tenancy import is_multi_tenant

    if is_multi_tenant():
        from app.tenancy.context import no_store

        with no_store():
            return sv.logo_path_or_url(), sv.store_display_name()
    return sv.logo_path_or_url(), sv.store_display_name()


def _message_page(status: int, title: str, text: str) -> HTMLResponse:
    logo, name = _page_look()
    img_src = "'self'"
    parts = urlsplit(logo)
    if parts.scheme in ("http", "https") and parts.netloc:
        img_src += f" {parts.scheme}://{parts.netloc}"
    return HTMLResponse(
        _NOT_ACTIVE_HTML % (_html_escape(logo, quote=True), _html_escape(name), title, text),
        status_code=status,
        headers=_page_headers(
            f"default-src 'none'; img-src {img_src}; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        ),
    )


def not_active_page() -> HTMLResponse:
    return _message_page(404, "This link is not active", "Ask your manager for the current checklist link.")


def _find_page_link(path: str):
    """Single mode: /c/{slug}-{code}. Shared service (platform contract
    4.4): /c/{STORE_CODE}/{slug}-{code}, looked up in that store's database
    (an unknown or inactive store is the same 404 as a wrong link)."""
    from app.tenancy import is_multi_tenant

    if not is_multi_tenant():
        if "/" in path:
            return None
        db = SessionLocal()
        try:
            return svc.find_page_link(db, path)
        finally:
            db.close()
    from app.tenancy import UnknownStore, normalize_store_code, tenant_session
    from app.tenancy.errors import StoreMoved, StoreNotActive

    code, sep, key = path.partition("/")
    if not sep or not key or "/" in key:
        return None
    try:
        with tenant_session(normalize_store_code(code)) as db:
            return svc.find_page_link(db, key)
    except (UnknownStore, StoreNotActive, StoreMoved):
        return None


@page_router.get("/c/{path:path}", include_in_schema=False)
def public_page(path: str, request: Request) -> Response:
    """The public page: the website's own index.html (it shows only the
    checklist form for /c/... paths - see frontend PublicChecklistPage), or
    a small "not active" page with a 404. Takes any /c/... path (so /c/,
    /c/a/b and the like get the friendly page, not the static mount's JSON
    404); one trailing slash (some chat apps add it) is accepted."""
    ip = _ip(request)
    try:
        svc.check_ip(ip, page=True)
        svc.check_failed(ip)
    except svc.PublicError as exc:
        return _message_page(429, "Too many tries", exc.detail)
    if path.endswith("/"):
        path = path[:-1]
    try:
        link = _find_page_link(path)
    except Exception:  # noqa: BLE001
        logger.exception("Public checklist page lookup failed")
        link = None
    if link is None:
        svc.record_failed(ip)
        return not_active_page()
    index = frontend_dist_dir() / "index.html"
    if not index.is_file():
        return _message_page(503, "Checklist page unavailable", "Try again in a few minutes.")
    return FileResponse(index, media_type="text/html", headers=_page_headers(PAGE_CSP))


@page_router.get("/c", include_in_schema=False)
def public_page_root(request: Request) -> Response:
    """/c (no link) - same 404 as a wrong link."""
    try:
        svc.check_ip(_ip(request), page=True)
    except svc.PublicError as exc:
        return _message_page(429, "Too many tries", exc.detail)
    return not_active_page()
