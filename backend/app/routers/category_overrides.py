"""Category corrections (services/category_overrides.py).

`/api/admin/category-overrides`: raw admin API over the rules table. The
existing corrections keep applying everywhere (and feed the automatic PO
section), but no page edits them any more: `/api/categories/*` now edits PO
sections (routers/po_sections.py).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import CategoryOverride, User
from app.services.category_overrides import MATCH_TYPES, decode_category_match, invalidate, serialize_rule
from app.services.similar_products import clear_similar_cache
from app.services.stock_issues import clear_forecast_cache
from app.timeutil import utcnow

router = APIRouter(
    prefix="/api/admin/category-overrides", tags=["category-overrides"], dependencies=[Depends(require_admin)]
)


class CategoryOverrideCreate(BaseModel):
    match_type: str
    match_value: str
    category_group: str
    category: str
    subcategory: Optional[str] = None
    note: Optional[str] = None
    active: bool = True


class CategoryOverrideUpdate(BaseModel):
    match_value: Optional[str] = None
    category_group: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    note: Optional[str] = None
    active: Optional[bool] = None


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _changed(db: Session) -> None:
    invalidate(db)
    # Fix Issues keeps forecasts for 10 minutes and picks its items by
    # category_group, so a rule change must not wait for that to expire.
    clear_forecast_cache()
    # Similar products reads the overridden categories (department /
    # category boosts); its per-upload index wouldn't notice a rule change.
    clear_similar_cache()


def _check_match_value(match_type: str, value: str) -> None:
    if match_type == "category" and decode_category_match(value) is None:
        raise HTTPException(
            status_code=400,
            detail='A category rule\'s match_value is ["group", "category", subcategory] '
            '(subcategory null = any, "" = none).',
        )


@router.get("")
def list_overrides(db: Session = Depends(get_db)):
    rows = db.execute(select(CategoryOverride).order_by(CategoryOverride.id)).scalars().all()
    return {"items": [serialize_rule(r) for r in rows]}


@router.post("")
def create_override(
    body: CategoryOverrideCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    if body.match_type not in MATCH_TYPES:
        raise HTTPException(status_code=400, detail="match_type must be 'supplier', 'item_id' or 'category'.")
    values = {
        "match_value": _clean(body.match_value),
        "category_group": _clean(body.category_group),
        "category": _clean(body.category),
    }
    missing = [k for k, v in values.items() if v is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"Required: {', '.join(missing)}.")
    _check_match_value(body.match_type, values["match_value"])
    subcategory = _clean(body.subcategory)
    if body.match_type == "category" and body.subcategory is not None and subcategory is None:
        subcategory = ""  # category rules: "" = no subcategory (None keeps Toast's)
    row = CategoryOverride(
        match_type=body.match_type,
        subcategory=subcategory,
        note=_clean(body.note),
        active=body.active,
        created_at=utcnow(),
        created_by=user.username,
        **values,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _changed(db)
    return serialize_rule(row)


@router.patch("/{override_id}")
def update_override(
    override_id: int,
    body: CategoryOverrideUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = db.get(CategoryOverride, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Override not found.")
    fields = body.model_dump(exclude_unset=True)
    for name in ("match_value", "category_group", "category"):
        if name in fields:
            value = _clean(fields[name])
            if value is None:
                raise HTTPException(status_code=400, detail=f"{name} can't be empty.")
            if name == "match_value":
                _check_match_value(row.match_type, value)
            setattr(row, name, value)
    if "subcategory" in fields:
        subcategory = _clean(fields["subcategory"])
        if row.match_type == "category" and fields["subcategory"] is not None and subcategory is None:
            subcategory = ""
        row.subcategory = subcategory
    if "note" in fields:
        row.note = _clean(fields["note"])
    if fields.get("active") is not None:
        row.active = fields["active"]
    row.updated_at = utcnow()
    row.updated_by = user.username
    db.commit()
    db.refresh(row)
    _changed(db)
    return serialize_rule(row)
