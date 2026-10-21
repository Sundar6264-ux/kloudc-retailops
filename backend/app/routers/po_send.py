"""Vendor contacts and "Send PO" (docs/AUTOMATION_BATCH_CONTRACT.md item 8;
services/po_send.py has the rules).

Who may do what (decided for this batch):
- Everything here needs the Purchase Order Cart (`purchase_order_cart`) -
  the page Send PO lives on; admins always pass. Supplier Projection alone
  isn't enough, although it may add to the cart: sending emails a vendor in
  the store's name, so it goes with the cart page itself.
- Adding, editing and deleting vendor contacts: admins only.
- The App Review demo login is refused on every route here, reads included
  (vendor emails / phone numbers are real people's details). Its writes are
  also refused by the global demo guard (none of these are allowlisted).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.demo import is_demo_user
from app.features import user_has_feature
from app.models import User
from app.services import po_send as svc
from app.services.supplier_projection import list_suppliers

FEATURE = "purchase_order_cart"
DEMO_REFUSED = "The demo account can't see vendor contacts or send purchase orders."


def require_po_sender(user: User = Depends(get_current_user)) -> User:
    if is_demo_user(user):
        from app.demo_guard import demo_read_only_error

        raise demo_read_only_error(DEMO_REFUSED)
    if not user_has_feature(user, FEATURE):
        raise HTTPException(status_code=403, detail="You don't have access to this feature")
    return user


router = APIRouter(prefix="/api/ordering", tags=["po-send"], dependencies=[Depends(require_po_sender)])


class ContactIn(BaseModel):
    vendor: str = Field(..., max_length=500)
    contact_name: Optional[str] = Field(None, max_length=500)
    email: Optional[str] = Field(None, max_length=500)
    whatsapp: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=5000)
    send_po: bool = True
    # Default CC addresses for this contact's POs (W2; at most 10).
    cc: Optional[List[str]] = Field(None, max_length=50)


class ContactPatch(BaseModel):
    vendor: Optional[str] = Field(None, max_length=500)
    contact_name: Optional[str] = Field(None, max_length=500)
    email: Optional[str] = Field(None, max_length=500)
    whatsapp: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=5000)
    send_po: Optional[bool] = None
    cc: Optional[List[str]] = Field(None, max_length=50)


class SendIn(BaseModel):
    supplier: str = Field(..., min_length=1, max_length=500)
    idempotency_key: str = Field(..., max_length=200)
    recipients: List[str] = Field(default_factory=list, max_length=50)
    note: Optional[str] = Field(None, max_length=10000)
    channels: List[str] = Field(default_factory=lambda: ["email"], max_length=5)
    whatsapp_contact_id: Optional[int] = None
    # The person checked Gmail's Sent folder after a send that may have gone
    # out, and still wants to send.
    confirm_resend: bool = False
    # Per-send CC addresses (W2): any valid email, at most 10; pre-filled
    # from the preview's default_cc. Older clients send none.
    cc: List[str] = Field(default_factory=list, max_length=50)


def _error(exc: svc.PoSendError) -> JSONResponse:
    content = {"detail": exc.detail}
    if exc.code:
        content["code"] = exc.code
    return JSONResponse(status_code=exc.status_code, content=content)


# --- Vendor contacts -------------------------------------------------------


@router.get("/vendor-contacts")
def get_vendor_contacts(
    vendor: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = svc.list_contacts(db, vendor)
    return {
        "items": [svc.contact_out(r) for r in rows],
        # Vendor names to pick from: Toast's supplier list, plus any vendor in
        # the cart or with a contact already (hand-typed vendors).
        "vendors": sorted(
            {*list_suppliers(db), *(r.supplier for r in svc.list_cart(db)), *(r.vendor for r in svc.list_contacts(db))},
            key=lambda v: v.lower(),
        ),
        "can_edit": bool(user.is_admin),
        "whatsapp_enabled": svc.whatsapp_enabled(),
        "whatsapp_off_reason": None if svc.whatsapp_enabled() else svc.WHATSAPP_OFF_REASON,
        "email_configured": svc.email_configured(),
    }


@router.post("/vendor-contacts", dependencies=[Depends(require_admin)])
def post_vendor_contact(payload: ContactIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return svc.contact_out(svc.create_contact(db, payload.model_dump(), user))
    except svc.PoSendError as exc:
        return _error(exc)


@router.patch("/vendor-contacts/{contact_id}", dependencies=[Depends(require_admin)])
def patch_vendor_contact(
    contact_id: int,
    payload: ContactPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return svc.contact_out(svc.update_contact(db, contact_id, payload.model_dump(exclude_unset=True), user))
    except svc.PoSendError as exc:
        return _error(exc)


@router.delete("/vendor-contacts/{contact_id}", dependencies=[Depends(require_admin)])
def delete_vendor_contact(contact_id: int, db: Session = Depends(get_db)):
    try:
        svc.delete_contact(db, contact_id)
    except svc.PoSendError as exc:
        return _error(exc)
    return {"deleted": True}


# --- Send PO ----------------------------------------------------------------


@router.get("/po-send/preview")
def get_po_send_preview(
    supplier: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """What Send would email: recipients, subject, body (with the note's
    place marked) and the PDF's name - nothing is logged or sent."""
    try:
        return svc.preview(db, supplier, user)
    except svc.PoSendError as exc:
        return _error(exc)


@router.get("/po-send/preview.pdf")
def get_po_send_preview_pdf(supplier: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """The PDF Send would attach, for the preview. Unlike Download PDF this
    isn't logged as a "pdf" order row - it's only a look."""
    _rows, items = svc.cart_lines(db, supplier)
    if not items:
        raise HTTPException(status_code=400, detail=f"Nothing to send - {supplier} has no cart lines above 0.")
    pdf = svc.build_po_pdf(db, supplier, items)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{svc.pdf_filename(supplier)}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/po-send")
def post_po_send(payload: SendIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Email the vendor's PO and, once it went out, record it as placed and
    clear those cart lines. 200 = sent (or partly, or a repeat of a key that
    was already used - `duplicate`); 502 = not sent or not confirmed, cart
    untouched, with the send row in `send`."""
    try:
        row, duplicate = svc.send_po(
            db,
            payload.supplier,
            user,
            payload.idempotency_key,
            recipients=payload.recipients,
            note=payload.note,
            channels=payload.channels,
            whatsapp_contact_id=payload.whatsapp_contact_id,
            confirm_resend=payload.confirm_resend,
            cc=payload.cc,
        )
    except svc.PoSendError as exc:
        return _error(exc)
    out = svc.send_out(row)
    if row.status in (svc.STATUS_SENT, svc.STATUS_PARTLY):
        return {"sent": True, "duplicate": duplicate, "send": out}
    return JSONResponse(
        status_code=502,
        content={"detail": svc.failure_message(row), "code": row.status, "duplicate": duplicate, "send": out},
    )


@router.get("/po-sends")
def get_po_sends(
    supplier: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """The send log, newest first (optionally one vendor's)."""
    return {"items": [svc.send_out(r) for r in svc.list_sends(db, supplier, limit)]}
