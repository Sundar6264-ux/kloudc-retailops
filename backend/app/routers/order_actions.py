"""Today-tab order actions (docs/BATCH_OCT14_CONTRACT.md W1;
services/order_actions.py). Admins only; the demo login is refused by the
global demo guard (not allowlisted).

- POST   /api/order-actions                     {vendor, action, delivery_date?, note?}
    action "placed" = Order already placed (delivery_date optional: from an
    Orders due row it ties the order to that delivery), "skip" = Skip this
    week, "other" = Other (note required). skip / other need delivery_date
    (the order's for_delivery_date).
- DELETE /api/order-actions/placed/{order_id}   Undo "Order already placed".
- DELETE /api/order-actions/skip?vendor=&delivery_date=   Undo Skip / Other.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.models import User
from app.services import order_actions as svc
from app.team.source_sync import task_sources_changed

router = APIRouter(
    prefix="/api/order-actions",
    tags=["order-actions"],
    dependencies=[Depends(get_current_user), Depends(require_admin)],
)


class OrderActionCreate(BaseModel):
    vendor: str = Field(..., max_length=200)
    action: str  # placed | skip | other
    delivery_date: Optional[dt.date] = None
    note: Optional[str] = None


def _raise(err: svc.OrderActionError):
    raise HTTPException(status_code=err.status_code, detail=err.detail)


@router.post("")
def post_order_action(
    payload: OrderActionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    action = (payload.action or "").strip().lower()
    if action not in svc.ACTIONS:
        raise HTTPException(status_code=400, detail="Pick Order already placed, Skip this week or Other.")
    try:
        if action == "placed":
            row, duplicate = svc.place_outside(db, user, payload.vendor, payload.delivery_date, payload.note)
            task_sources_changed("order")  # its Team task closes now (Oct 15)
            return svc.action_out(action, row.supplier, payload.delivery_date, svc.placed_handled(row), duplicate)
        if payload.delivery_date is None:
            raise svc.OrderActionError(400, "Which order? Send the delivery date it's for.")
        reason = svc.SKIP_REASON_OTHER if action == "other" else None
        row = svc.skip_cycle(db, user, payload.vendor, payload.delivery_date, reason, payload.note)
        task_sources_changed("order")  # its Team task closes now (Oct 15)
        return svc.action_out(action, row.vendor, payload.delivery_date, svc.skip_handled(row), False)
    except svc.OrderActionError as err:
        _raise(err)


@router.delete("/placed/{order_id}")
def delete_placed_outside(order_id: int, db: Session = Depends(get_db)):
    try:
        row = svc.undo_outside(db, order_id)
    except svc.OrderActionError as err:
        _raise(err)
    return {
        "undone": True,
        "order_id": order_id,
        "vendor": row.supplier,
        "message": f"{row.supplier}: no longer marked as placed.",
    }


@router.delete("/skip")
def delete_skip(
    vendor: str = Query(..., min_length=1, max_length=200),
    delivery_date: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    if not svc.unskip_cycle(db, vendor, delivery_date):
        raise HTTPException(status_code=404, detail="That order isn't skipped or hidden.")
    return {
        "undone": True,
        "vendor": vendor.strip(),
        "delivery_date": delivery_date.isoformat(),
        "message": f"{vendor.strip()}: the order is due again.",
    }
