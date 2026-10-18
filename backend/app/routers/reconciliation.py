import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.features import require_feature
from app.models import User
from app.schemas import (
    DeliveryConfirmRequest,
    PurchaseLogCreate,
    PurchaseLogImportResult,
    PurchaseLogOut,
)
from app.services.csv_util import rows_to_csv
from app.services.delivery_review import confirm_delivery, get_delivery_candidates
from app.services.delivery_suggestions import DEFAULT_DAYS as SUGGEST_DEFAULT_DAYS
from app.services.delivery_suggestions import MAX_DAYS as SUGGEST_MAX_DAYS
from app.services.delivery_suggestions import get_suggested_deliveries
from app.services.suggestion_dismissals import dismiss, undismiss
from app.services.purchase_log import (
    create_purchase_entry,
    delete_purchase_entry,
    delivery_history,
    list_purchase_entries,
)
from app.services.purchase_log_import import PurchaseLogImportError, import_purchase_log
from app.timeutil import restaurant_today
from app.services.reconciliation import DEFAULT_INVOICE_GAP_WINDOW_DAYS, get_invoice_gaps, get_reconciliation, get_reconciliation_demo

router = APIRouter(
    prefix="/api/reconciliation", tags=["reconciliation"], dependencies=[Depends(get_current_user)]
)


@router.post("/purchases", response_model=PurchaseLogOut, dependencies=[Depends(require_feature("reconciliation"))])
def log_purchase(
    body: PurchaseLogCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_purchase_entry(
        db,
        item_id=body.item_id,
        item_name=body.item_name,
        supplier=body.supplier,
        quantity_received=body.quantity_received,
        unit_cost=body.unit_cost,
        received_date=body.received_date,
        notes=body.notes,
        logged_by_user_id=user.id,
    )


@router.post(
    "/purchases/upload",
    response_model=PurchaseLogImportResult,
    dependencies=[Depends(require_feature("reconciliation"))],
)
async def upload_purchase_log(
    file: UploadFile = File(...),
    default_received_date: Optional[dt.date] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        content = await file.read()
        return import_purchase_log(
            db,
            filename=file.filename or "upload",
            content=content,
            default_received_date=default_received_date,
            logged_by_user_id=user.id,
        )
    except PurchaseLogImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/purchases", dependencies=[Depends(require_feature("reconciliation"))])
def get_purchases(
    start: Optional[dt.date] = Query(None),
    end: Optional[dt.date] = Query(None),
    db: Session = Depends(get_db),
):
    entries = list_purchase_entries(db, start, end)
    return {"count": len(entries), "items": [PurchaseLogOut.model_validate(e) for e in entries]}


@router.delete("/purchases/{entry_id}", dependencies=[Depends(require_feature("reconciliation"))])
def remove_purchase(entry_id: int, db: Session = Depends(get_db)):
    if not delete_purchase_entry(db, entry_id):
        raise HTTPException(status_code=404, detail="Purchase log entry not found")
    return {"deleted": entry_id}


@router.get("/delivery-candidates", dependencies=[Depends(require_feature("delivery_review"))])
def delivery_candidates(vendor: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    result = get_delivery_candidates(db, vendor)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Not enough inventory upload history yet to compare against (need at least two "
            "snapshot dates).",
        )
    return result


# Read-only summary for the Delivery Review page's empty state. Gated on
# delivery_review (not reconciliation, which /purchases needs) so anyone who
# can confirm deliveries can also see what was confirmed recently.
@router.get("/delivery-history", dependencies=[Depends(require_feature("delivery_review"))])
def get_delivery_history(days: int = Query(14, ge=1, le=90), db: Session = Depends(get_db)):
    today = restaurant_today()
    return {"days": days, "through": today.isoformat(), "deliveries": delivery_history(db, days, today)}


# --- Suggested deliveries (docs/AUTOMATION_BATCH_CONTRACT.md item 4) -------------------------
# Read-only suggestions; confirming one is the plain /delivery-confirm below,
# so the purchase log gets exactly what a manual review writes.


class SuggestionDismissRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=300)


@router.get("/suggested-deliveries", dependencies=[Depends(require_feature("delivery_review"))])
def suggested_deliveries(
    days: int = Query(SUGGEST_DEFAULT_DAYS, ge=1, le=SUGGEST_MAX_DAYS),
    include_dismissed: bool = Query(False),
    db: Session = Depends(get_db),
):
    return get_suggested_deliveries(db, days=days, include_dismissed=include_dismissed)


@router.post("/suggested-deliveries/dismiss", dependencies=[Depends(require_feature("delivery_review"))])
def dismiss_suggested_delivery(
    body: SuggestionDismissRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    row = dismiss(db, "delivery", body.key.strip(), user)
    return {"key": row.suggestion_key, "dismissed": True, "dismissed_by": row.dismissed_by_name}


@router.delete("/suggested-deliveries/dismiss", dependencies=[Depends(require_feature("delivery_review"))])
def undo_dismiss_suggested_delivery(key: str = Query(..., min_length=1, max_length=300), db: Session = Depends(get_db)):
    if not undismiss(db, "delivery", key.strip()):
        raise HTTPException(status_code=404, detail="That suggestion isn't dismissed.")
    return {"key": key.strip(), "dismissed": False}


@router.post("/delivery-confirm", dependencies=[Depends(require_feature("delivery_review"))])
def delivery_confirm(
    payload: DeliveryConfirmRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logged = confirm_delivery(
        db,
        vendor=payload.vendor,
        received_date=payload.received_date,
        items=[item.model_dump() for item in payload.items],
        logged_by_user_id=user.id,
    )
    return {"logged": logged}


@router.get("/demo", dependencies=[Depends(require_feature("reconciliation"))])
def demo(db: Session = Depends(get_db)):
    result = get_reconciliation_demo(db)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Not enough inventory upload history yet to build a demo (need at least two "
            "snapshot dates).",
        )
    return result


@router.get("/report", dependencies=[Depends(require_feature("reconciliation"))])
def report(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    items = get_reconciliation(db, start, end)
    return {"start": start.isoformat(), "end": end.isoformat(), "count": len(items), "items": items}


@router.get("/invoice-gaps", dependencies=[Depends(require_feature("reconciliation"))])
def invoice_gaps(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    window_days: int = Query(DEFAULT_INVOICE_GAP_WINDOW_DAYS, ge=0, le=30),
    db: Session = Depends(get_db),
):
    items = get_invoice_gaps(db, start, end, window_days)
    return {"start": start.isoformat(), "end": end.isoformat(), "count": len(items), "items": items}


@router.get("/report/export", dependencies=[Depends(require_feature("reconciliation"))])
def report_export(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    items = get_reconciliation(db, start, end)
    return Response(
        content=rows_to_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="reconciliation-{start}-{end}.csv"'},
    )


@router.get("/invoice-gaps/export", dependencies=[Depends(require_feature("reconciliation"))])
def invoice_gaps_export(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    window_days: int = Query(DEFAULT_INVOICE_GAP_WINDOW_DAYS, ge=0, le=30),
    db: Session = Depends(get_db),
):
    """Invoices Without a Logged Purchase as a CSV (the invoice file link is
    left out - it needs a login and means nothing in a spreadsheet)."""
    rows = [
        {
            "Date": g["submission_date"],
            "Vendor": g["vendor"],
            "Type": "Invoice" if g["submission_type"] == "invoice" else "Return",
            "Total": g["total_amount"],
            "Submitted By": g["submitted_by_name"],
        }
        for g in get_invoice_gaps(db, start, end, window_days)
    ]
    return Response(
        content=rows_to_csv(rows) or "Date,Vendor,Type,Total,Submitted By\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="invoice-gaps-{start}-{end}.csv"'},
    )


@router.get("/purchases/export", dependencies=[Depends(require_feature("reconciliation"))])
def purchases_export(
    start: Optional[dt.date] = Query(None),
    end: Optional[dt.date] = Query(None),
    db: Session = Depends(get_db),
):
    """The purchase log (newest first) as a CSV - the list under Log a Purchase."""
    rows = [
        {
            "Received": e.received_date.isoformat(),
            "Item": e.item_name or e.item_id,
            "Vendor": e.supplier,
            "Qty Received": e.quantity_received,
            "Unit Cost": e.unit_cost,
            "Notes": e.notes,
        }
        for e in list_purchase_entries(db, start, end)
    ]
    return Response(
        content=rows_to_csv(rows) or "Received,Item,Vendor,Qty Received,Unit Cost,Notes\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="purchase-log.csv"'},
    )
