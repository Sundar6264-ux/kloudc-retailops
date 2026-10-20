"""Item vendors editor + CSV import (docs/WEBSITE_READY_CONTRACT.md, track D4).

Setup > Item vendors on the website (pages/ItemVendorsPage.jsx). Who
supplies each item, for a store whose POS has no vendor field (Clover, a
CSV-only store). A Toast store's vendors come from its inventory CSV (the
CSV wins), so there every write answers 409 and the page is read-only.

Store admins only (the Setup area). The demo login can read but never write
(app/demo_guard.py refuses every unlisted write).

- ``GET    /api/item-vendors``                 items + vendors (search, filter, page)
- ``PUT    /api/item-vendors/items/{item_id}`` replace one item's list
- ``POST   /api/item-vendors/import``          CSV (multipart ``file``); ``?apply=1`` writes
- ``GET    /api/item-vendors/export.csv``      every item and its vendors
- ``GET    /api/item-vendors/template.csv``    the columns, with one example row
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.auth import require_admin
from app.db import get_db
from app.models import User
from app.services import item_vendors as svc

router = APIRouter(prefix="/api/item-vendors", tags=["item-vendors"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
READ_ONLY = (
    "Vendors come from your inventory file's Supplier column for this store, so they can't be edited here. "
    "Change them in your POS export and upload it again."
)


def _pos_name() -> str:
    from app.pos.gating import pos_name

    return pos_name()


def _require_editable() -> None:
    if not svc.editor_editable():
        raise HTTPException(status_code=409, detail=READ_ONLY)


@router.get("")
def list_item_vendors(
    q: str = Query(default="", max_length=120),
    show: str = Query(default="all", pattern="^(all|missing|with)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    out = svc.list_editor_items(db, q=q, show=show, limit=limit, offset=offset)
    out["editable"] = svc.editor_editable()
    out["pos"] = _pos_name()
    out["read_only_reason"] = None if out["editable"] else READ_ONLY
    return out


class VendorIn(BaseModel):
    name: str = Field(default="", max_length=200)
    item_code: Optional[str] = Field(default=None, max_length=200)


class VendorsBody(BaseModel):
    vendors: List[VendorIn] = Field(default_factory=list, max_length=50)


@router.put("/items/{item_id}")
def put_item_vendors(
    item_id: str,
    body: VendorsBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_editable()
    if len(item_id) > 200:
        raise HTTPException(status_code=404, detail="No such item.")
    items = svc.catalog_items(db)
    row = items.get(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such item in the current inventory.")
    try:
        rows, changed = svc.set_item_vendors(db, item_id, [v.model_dump() for v in body.vendors])
    except svc.VendorEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.commit()
    return {**svc.item_view(row, rows), "changed": changed}


@router.post("/import")
async def import_item_vendors(
    request: Request,
    apply: bool = Query(default=False),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Preview (default) or apply a CSV of item -> vendor rows."""
    _require_editable()
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="The file is larger than 5 MB.")
    form = await request.form(max_files=1, max_fields=3)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="Attach the CSV file as 'file'.")
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        filename = upload.filename or ""
    finally:
        await form.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 5 MB.")
    from starlette.concurrency import run_in_threadpool

    def run() -> Dict[str, Any]:
        try:
            result = svc.import_vendors(db, filename, content, apply=apply)
        except svc.VendorEditError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if apply:
            db.commit()
        else:
            db.rollback()
        return result

    return await run_in_threadpool(run)


def _csv_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/export.csv")
def export_item_vendors(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _csv_response(svc.export_csv(db), "item-vendors.csv")


@router.get("/template.csv")
def item_vendors_template(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _csv_response(svc.export_csv(db, template=True), "item-vendors-template.csv")
