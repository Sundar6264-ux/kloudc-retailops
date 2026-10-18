"""Checklist builder - admin API (docs/CHECKLIST_BUILDER_CONTRACT.md). Admins
create and edit checklists on the website; logic in
services/checklist_admin.py. Every route is admin-only.
"""

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import User
from app.services import checklist_admin as svc
from app.services import checklist_public as public_svc
from app.services import whatsapp_groups as groups_svc

router = APIRouter(prefix="/api/checklists/admin", tags=["checklists-admin"], dependencies=[Depends(require_admin)])


def _run(fn, *args):
    try:
        return fn(*args)
    except svc.AdminError as exc:
        # {"detail": "...first problem...", "problems": [...every problem...]}
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "problems": exc.problems})


def _body(payload) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Send a JSON object")
    return payload


@router.get("/meta")
def editor_meta(db: Session = Depends(get_db)):
    return svc.editor_meta(db)


@router.get("/definitions")
def list_definitions(db: Session = Depends(get_db)):
    return {"definitions": _run(svc.list_definitions, db)}


@router.post("/definitions")
def create_definition(
    payload: Optional[dict] = Body(None), user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    return _run(svc.create_definition, db, user, _body(payload))


@router.post("/definitions/order")
def reorder(
    order: List[str] = Body(..., embed=True), user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    result = _run(svc.reorder, db, user, order)
    return result if isinstance(result, JSONResponse) else {"definitions": result}


@router.get("/definitions/{checklist_type}")
def get_definition(checklist_type: str, db: Session = Depends(get_db)):
    return _run(svc.definition_detail, db, checklist_type)


@router.put("/definitions/{checklist_type}")
def save_draft(
    checklist_type: str,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _run(svc.save_draft, db, user, checklist_type, _body(payload))


@router.delete("/definitions/{checklist_type}/draft")
def discard_draft(checklist_type: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _run(svc.discard_draft, db, user, checklist_type)


@router.post("/definitions/{checklist_type}/publish")
def publish(
    checklist_type: str,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _run(svc.publish, db, user, checklist_type, _body(payload))


@router.post("/definitions/{checklist_type}/duplicate")
def duplicate(
    checklist_type: str,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _run(svc.duplicate, db, user, checklist_type, _body(payload))


@router.patch("/definitions/{checklist_type}")
def patch_definition(
    checklist_type: str,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _run(svc.patch, db, user, checklist_type, _body(payload))


@router.get("/definitions/{checklist_type}/versions/{version}")
def get_version(checklist_type: str, version: int, db: Session = Depends(get_db)):
    return _run(svc.version_detail, db, checklist_type, version)


@router.post("/definitions/{checklist_type}/revert/{version}")
def revert(
    checklist_type: str,
    version: int,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _run(svc.revert, db, user, checklist_type, version, _body(payload))


# --- Public checklist link (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md) -----------------
# Logic: services/checklist_public.py. Admin-only like the rest of this router.


@router.get("/public-links")
def list_public_links(db: Session = Depends(get_db)):
    """Every checklist with its link status (exists / enabled / url / last used)."""
    return {"links": _run(public_svc.list_links, db)}


@router.get("/definitions/{checklist_type}/public-link")
def get_public_link(checklist_type: str, db: Session = Depends(get_db)):
    """The link (exists: true) or the suggested settings for a new one (exists: false)."""
    return _run(public_svc.get_link_admin, db, checklist_type)


@router.put("/definitions/{checklist_type}/public-link")
def put_public_link(
    checklist_type: str,
    payload: Optional[dict] = Body(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create or update: any of enabled, slug, name_mode, allowed_names, max_per_hour."""
    return _run(public_svc.put_link, db, user, checklist_type, _body(payload))


@router.post("/definitions/{checklist_type}/public-link/rotate")
def rotate_public_link(checklist_type: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """A new token: the old URL stops working at once."""
    return _run(public_svc.rotate_link, db, user, checklist_type)


@router.delete("/definitions/{checklist_type}/public-link")
def delete_public_link(checklist_type: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _run(public_svc.delete_link, db, user, checklist_type)


# --- WhatsApp groups (docs/CHECKLIST_BUILDER_APP_AND_GROUPS_CONTRACT.md, section 1) --------
# Logic: services/whatsapp_groups.py. Admin-only like the rest of this router.

GROUPS = "/whatsapp-groups"


def _run_groups(fn, *args):
    try:
        return fn(*args)
    except groups_svc.GroupError as exc:
        content = {"detail": exc.detail}
        if exc.used_by is not None:
            content["used_by"] = exc.used_by
        return JSONResponse(status_code=exc.status_code, content=content)


@router.get(GROUPS)
def list_whatsapp_groups(db: Session = Depends(get_db)):
    """{items: [{key, label, chat_id_masked, configured, builtin, active, marketing, used_by}]}"""
    return _run_groups(groups_svc.list_groups, db)


@router.get(GROUPS + "/available")
def available_whatsapp_groups(refresh: bool = Query(False), db: Session = Depends(get_db)):
    """The groups the store's WhatsApp number is in (Whapi, cached 5 min):
    {items: [{chat_id, name, already_added_key}]}, plus `error` when
    WhatsApp isn't connected or Whapi failed - never a 500."""
    return groups_svc.available(db, refresh)


@router.post(GROUPS)
def create_whatsapp_group(
    payload: Optional[dict] = Body(None), user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """{chat_id, label, marketing?} -> the new group (key slugified from the label)."""
    return _run_groups(groups_svc.create_group, db, user, _body(payload))


@router.patch(GROUPS + "/{key}")
def patch_whatsapp_group(
    key: str, payload: Optional[dict] = Body(None), user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """{label?, active?, marketing?} (marketing = "Use for flyers")"""
    return _run_groups(groups_svc.patch_group, db, user, key, _body(payload))


@router.delete(GROUPS + "/{key}", status_code=204)
def delete_whatsapp_group(key: str, db: Session = Depends(get_db)):
    """400 for a built-in, 409 (with used_by) when a checklist uses it."""
    result = _run_groups(groups_svc.delete_group, db, key)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)
