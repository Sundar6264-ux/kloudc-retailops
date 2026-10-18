"""PO sections page (web /po-sections, alias /categories; app build 5's More >
Product categories): edit the section each product prints under on the
vendor PO PDF. Gated on the `category_edits` feature (admins always pass).

Served on the EXISTING `/api/categories/*` paths in the exact JSON shapes the
released app build 5 parses and sends (services/po_section_rules.py): an
item's "category" is its PO section, its "toast_category" the automatic
section, category_group is always "PO section" and subcategory null. Group /
subcategory sent by the app are ignored. Web-only additions: section list,
renames / merges, move-all, change list + undo, cart line sections and the
cart PDF preview.

Toast category corrections (category_overrides) are no longer edited here.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.features import require_feature
from app.models import User
from app.services import po_section_rules as svc

router = APIRouter(prefix="/api/categories", tags=["po-sections"])
_gate = require_feature("category_edits")


class SectionValues(BaseModel):
    # Build 5 sends category_group / category / subcategory; `category` is
    # the section. The web may send `section` instead.
    category_group: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    section: Optional[str] = None

    def value(self) -> Optional[str]:
        return svc.clean(self.section) or svc.clean(self.category)


class BulkSectionBody(SectionValues):
    item_ids: List[str]


class BulkResetBody(BaseModel):
    item_ids: List[str]
    # True (build 5's "Reset to Toast", web "Back to automatic"): items a
    # vendor rule covers are pinned to their automatic section. False: only
    # remove the items' own rules (the vendor rule applies again).
    to_toast: bool = True


class VendorRuleBody(SectionValues):
    vendor: str
    note: Optional[str] = None


class MoveBody(SectionValues):
    vendor: Optional[str] = None
    from_section: Optional[str] = None


class RenameBody(BaseModel):
    from_section: str
    to_section: str
    note: Optional[str] = None


class UndoBody(BaseModel):
    batch: Optional[str] = None


class CartLineSectionBody(BaseModel):
    # Blank / null = back to automatic (catalog item) or Other (hand-added).
    section: Optional[str] = None


def _bad(exc: ValueError):
    if isinstance(exc, svc.UnknownItems):
        status = 404
    elif isinstance(exc, svc.NotLatestChange):
        status = 409
    else:
        status = 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/items")
def section_items(
    q: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None),
    category_group: Optional[str] = Query(None, description="Ignored (always PO section)"),
    category: Optional[str] = Query(None, description="Section the item prints under now"),
    subcategory: Optional[str] = Query(None, description="Ignored"),
    section: Optional[str] = Query(None, description="Same as category"),
    source: Optional[str] = Query(None, description="toast (automatic) | item | vendor | changed"),
    limit: int = Query(svc.DEFAULT_LIMIT, ge=1, le=svc.MAX_LIMIT),
    db: Session = Depends(get_db),
    _: User = Depends(_gate),
):
    if source == "automatic":
        source = "toast"
    if source and source not in svc.SOURCES + ("changed", "category"):
        raise HTTPException(status_code=400, detail="source must be toast, item, vendor or changed.")
    return svc.search_items(db, q, vendor, section or category, source, limit)


@router.get("/options")
def section_options(db: Session = Depends(get_db), _: User = Depends(_gate)):
    return svc.section_options(db)


@router.put("/items/{item_id}")
def set_item_section(item_id: str, body: SectionValues, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        result = svc.set_item_sections(db, [item_id], body.value(), user.username)
    except ValueError as exc:
        raise _bad(exc)
    return {"changed": result["changed"], "item": result["items"][0]}


@router.delete("/items/{item_id}")
def reset_item_section(
    item_id: str, to_toast: bool = Query(True), db: Session = Depends(get_db), user: User = Depends(_gate)
):
    try:
        result = svc.reset_items(db, [item_id], user.username, to_toast=to_toast)
    except ValueError as exc:
        raise _bad(exc)
    return {"changed": result["changed"], "item": result["items"][0] if result["items"] else None}


@router.post("/items/bulk")
def bulk_set_section(body: BulkSectionBody, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        return svc.set_item_sections(db, body.item_ids, body.value(), user.username)
    except ValueError as exc:
        raise _bad(exc)


@router.post("/items/reset")
def bulk_reset(body: BulkResetBody, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        return svc.reset_items(db, body.item_ids, user.username, to_toast=body.to_toast)
    except ValueError as exc:
        raise _bad(exc)


@router.post("/items/move")
def move_items(body: MoveBody, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        return svc.move_items(db, body.value(), user.username, vendor=body.vendor, from_section=body.from_section)
    except ValueError as exc:
        raise _bad(exc)


@router.get("/vendor-rules")
def vendor_rules(include_inactive: bool = Query(False), db: Session = Depends(get_db), _: User = Depends(_gate)):
    return {"items": svc.list_vendor_rules(db, include_inactive)}


@router.post("/vendor-rules")
def create_vendor_rule(body: VendorRuleBody, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        return svc.create_vendor_rule(db, body.vendor, body.value(), body.note, user.username)
    except ValueError as exc:
        raise _bad(exc)


@router.delete("/vendor-rules/{rule_id}")
def deactivate_vendor_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(_gate)):
    rule = svc.deactivate_rule(db, rule_id, user.username, "vendor")
    if rule is None:
        raise HTTPException(status_code=404, detail="Vendor rule not found.")
    return rule


@router.get("/sections")
def sections(db: Session = Depends(get_db), _: User = Depends(_gate)):
    return {"items": svc.list_sections(db)}


@router.get("/renames")
def renames(include_inactive: bool = Query(False), db: Session = Depends(get_db), _: User = Depends(_gate)):
    return {"items": svc.list_renames(db, include_inactive)}


@router.post("/renames")
def create_rename(body: RenameBody, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        return svc.create_rename(db, body.from_section, body.to_section, body.note, user.username)
    except ValueError as exc:
        raise _bad(exc)


@router.delete("/renames/{rule_id}")
def undo_rename(rule_id: int, db: Session = Depends(get_db), user: User = Depends(_gate)):
    rule = svc.deactivate_rule(db, rule_id, user.username, "rename")
    if rule is None:
        raise HTTPException(status_code=404, detail="Rename not found.")
    return rule


@router.get("/history")
def section_history(
    item_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    match_type: Optional[str] = Query(None, description="item_id | supplier | rename"),
    db: Session = Depends(get_db),
    _: User = Depends(_gate),
):
    if match_type and match_type not in ("item_id", "supplier", "rename", "item", "vendor"):
        raise HTTPException(status_code=400, detail="match_type must be item_id, supplier or rename.")
    return {"items": svc.history(db, item_id, limit, match_type)}


@router.get("/changes")
def recent_changes(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db), _: User = Depends(_gate)):
    return {"items": svc.changes(db, limit)}


@router.post("/changes/undo")
def undo_last_change(body: UndoBody, db: Session = Depends(get_db), user: User = Depends(_gate)):
    try:
        return svc.undo_last(db, user.username, body.batch)
    except ValueError as exc:
        raise _bad(exc)


@router.get("/cart-preview")
def cart_preview(supplier: Optional[str] = Query(None), db: Session = Depends(get_db), _: User = Depends(_gate)):
    return svc.cart_preview(db, supplier)


@router.put("/cart-lines/{cart_item_id}")
def set_cart_line_section(
    cart_item_id: int, body: CartLineSectionBody, db: Session = Depends(get_db), user: User = Depends(_gate)
):
    try:
        line = svc.set_cart_line_section(db, cart_item_id, body.section, user.username)
    except ValueError as exc:
        raise _bad(exc)
    if line is None:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    return line
