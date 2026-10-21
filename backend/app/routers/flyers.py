"""Marketing flyers API (docs/MARKETING_FLYERS_CONTRACT.md, section 4).
Logic: services/flyers.py (library, files, send rows) and
services/flyer_sender.py (the background WhatsApp sender).

Every route needs the `marketing_flyers` feature (admins always have it).
The App Review demo login is refused outright - writes by the app-wide demo
guard (these routes are deliberately NOT in its allowlist), reads here.
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.db import get_db
from app.demo import is_demo_user
from app.demo_guard import demo_read_only_error
from app.features import user_has_feature
from app.models import User
from app.services import flyer_sender
from app.services import flyers as svc


def require_flyers(user: User = Depends(get_current_user)) -> User:
    if is_demo_user(user):
        raise demo_read_only_error()
    if not user_has_feature(user, svc.FEATURE):
        raise HTTPException(status_code=403, detail="You don't have access to this feature")
    return user


router = APIRouter(prefix="/api/flyers", tags=["flyers"], dependencies=[Depends(require_flyers)])

_FILE_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}


def _error(exc: svc.FlyerError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def _flyer(db: Session, flyer_id: int):
    try:
        return svc.get_flyer(db, flyer_id)
    except svc.FlyerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# /meta is registered before /{flyer_id} so it never matches as an id.
@router.get("/meta")
def flyers_meta(db: Session = Depends(get_db)):
    """{categories, groups (marketing only, no chat ids), whatsapp_connected,
    max_image_mb, max_pdf_mb, accept, max_groups_per_send, ...}"""
    return svc.meta(db)


@router.get("")
def list_flyers(
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(svc.DEFAULT_LIMIT, ge=1, le=svc.MAX_LIMIT),
    before_id: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
):
    """{items: [Flyer], next_before_id, counts} - newest first."""
    try:
        return svc.list_flyers(db, category or None, q, limit, before_id)
    except svc.FlyerError as exc:
        return _error(exc)


def _store_upload(db: Session, user: User, content_type: Optional[str], body: bytes):
    parts = svc.parse_upload(content_type, body)
    return svc.create_flyer(
        db,
        user,
        parts.data,
        parts.filename,
        parts.fields.get("title"),
        parts.fields.get("category"),
        parts.fields.get("caption"),
    )


@router.post("", status_code=201)
async def upload_flyer(request: Request, user: User = Depends(require_flyers), db: Session = Depends(get_db)):
    """Multipart: file, title, category, caption? -> 201 Flyer.
    No File(...) parameter on purpose: FastAPI would read (and spool to disk)
    the whole body before this runs. The size is checked from Content-Length
    first (as team/media.py does); then the body (at most ~26 MB) is parsed,
    validated, decoded and re-encoded in a worker thread - never on the
    event loop, which a slow multipart parse would otherwise freeze."""
    svc.check_request_size(request)
    body = await request.body()
    try:
        flyer = await run_in_threadpool(_store_upload, db, user, request.headers.get("content-type"), body)
    except svc.FlyerError as exc:
        return _error(exc)
    return svc.flyer_out(flyer)


@router.get("/{flyer_id}")
def get_flyer(flyer_id: int, db: Session = Depends(get_db)):
    """Flyer with `sends` (newest first)."""
    return svc.flyer_detail(db, _flyer(db, flyer_id))


@router.get("/{flyer_id}/file")
def flyer_file(flyer_id: int, db: Session = Depends(get_db)):
    flyer = _flyer(db, flyer_id)
    path = svc.flyer_file(flyer)
    if path is None:
        raise HTTPException(status_code=404, detail="The flyer's file is missing from the Flyers folder.")
    return FileResponse(
        path,
        media_type=flyer.mime,
        filename=svc.display_file_name(flyer),
        content_disposition_type="inline",
        headers=_FILE_HEADERS,
    )


@router.get("/{flyer_id}/thumb")
def flyer_thumb(flyer_id: int, db: Session = Depends(get_db)):
    """JPEG (or the generic PNG icon for a PDF that can't be rendered), at
    most 480 px."""
    flyer = _flyer(db, flyer_id)
    try:
        path, media_type = svc.thumbnail(flyer)
    except svc.FlyerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return FileResponse(path, media_type=media_type, headers=_FILE_HEADERS)


@router.patch("/{flyer_id}")
def update_flyer(
    flyer_id: int,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_flyers),
    db: Session = Depends(get_db),
):
    """{title?, category?, caption?} -> Flyer (a new title or category
    renames / moves the file)."""
    if payload is None or not isinstance(payload, dict):
        return _error(svc.FlyerError(400, "Send a JSON object."))
    try:
        flyer = svc.update_flyer(db, user, flyer_id, payload)
    except svc.FlyerError as exc:
        return _error(exc)
    return svc.flyer_detail(db, flyer)


@router.delete("/{flyer_id}", status_code=204)
def delete_flyer(flyer_id: int, user: User = Depends(require_flyers), db: Session = Depends(get_db)):
    """Soft delete: the file moves to _Deleted/<Category>/, never removed."""
    try:
        svc.delete_flyer(db, user, flyer_id)
    except svc.FlyerError as exc:
        return _error(exc)
    return Response(status_code=204)


@router.post("/{flyer_id}/send", status_code=202)
def send_flyer(
    flyer_id: int,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_flyers),
    db: Session = Depends(get_db),
):
    """{group_keys: [1-25 marketing group keys], idempotency_key} -> 202
    {batch_id, sends}. The same idempotency_key within 24 h returns the
    first batch and sends nothing again."""
    try:
        out, new_ids = svc.create_send_batch(db, user, flyer_id, payload)
    except svc.FlyerError as exc:
        return _error(exc)
    if new_ids:
        flyer_sender.enqueue(new_ids)
    return out


@router.post("/{flyer_id}/sends/{send_id}/retry", status_code=202)
def retry_send(flyer_id: int, send_id: int, user: User = Depends(require_flyers), db: Session = Depends(get_db)):
    """A failed send only -> 202 Send (queued again)."""
    try:
        out, ids = svc.retry_send(db, user, flyer_id, send_id)
    except svc.FlyerError as exc:
        return _error(exc)
    flyer_sender.enqueue(ids)
    return out
