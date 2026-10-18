"""Critical vendors: Close / Skip this week / Remind me later
(docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md; services/critical_dismissals.py).

- GET /api/reorder/critical-dismissals: anyone with Reorder Candidates sees
  the active dismissals (+ the choices the actions offer); an admin's
  ?include_history=1 adds the last 60 days.
- POST /api/reorder/critical-dismissals, DELETE .../{id} (Show again):
  admins only. The App Review demo login is refused by the demo guard
  (these routes are deliberately not in its allowlist).
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.demo import is_demo_user
from app.features import require_feature
from app.models import User
from app.services import critical_dismissals as svc
from app.team.source_sync import task_sources_changed
from app.services.order_actions import outside_orders_active
from app.timeutil import restaurant_today

router = APIRouter(prefix="/api/reorder", tags=["reorder"], dependencies=[Depends(get_current_user)])


class CriticalDismissalCreate(BaseModel):
    vendor: str
    kind: str  # close | skip_week | snooze
    reason: Optional[str] = None  # close: ordered | not_ordering | counts_wrong | other
    note: Optional[str] = None
    # snooze: later_today | tomorrow | in_3_days | date (+ `date`); or an explicit `until`.
    preset: Optional[str] = None
    date: Optional[dt.date] = None
    until: Optional[dt.datetime] = None


@router.get("/critical-dismissals", dependencies=[Depends(require_feature("reorder_candidates"))])
def get_critical_dismissals(
    include_history: bool = Query(False, description="Admins: also the dismissals of the last 60 days"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = svc.utcnow()
    # The App Review demo login's writes are refused (demo guard), so it
    # can't edit even if it were an admin.
    can_edit = bool(user.is_admin) and not is_demo_user(user)
    body = {"items": svc.list_active(db, now), **svc.options_out(now), "can_edit": can_edit}
    # W1 (additive): vendors "Ordered" right now because someone used "Order
    # already placed" - the Critical vendors sheet lists them with Undo.
    try:
        body["placed_outside"] = outside_orders_active(db, restaurant_today(), svc.TODAY_LEAD_TIME_DAYS)
    except Exception:  # noqa: BLE001 - only a convenience list
        svc.logger.exception("Critical dismissals: outside orders lookup failed")
        db.rollback()
        body["placed_outside"] = []
    if include_history and user.is_admin:
        body["history"] = svc.list_history(db, now)
    return body


@router.post("/critical-dismissals", dependencies=[Depends(require_admin)])
def post_critical_dismissal(
    payload: CriticalDismissalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = svc.utcnow()
    try:
        row, warning = svc.create_dismissal(
            db,
            user,
            payload.vendor,
            payload.kind,
            reason=payload.reason,
            note=payload.note,
            preset=payload.preset,
            day=payload.date,
            until=payload.until,
            now=now,
        )
    except svc.DismissalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail)
    if row.kind == "skip_week":
        task_sources_changed("order")  # its order task is cancelled now (Oct 15)
    return {**svc.dismissal_out(row, now), "warning": warning}


@router.delete(
    "/critical-dismissals/{dismissal_id}", status_code=204, dependencies=[Depends(require_admin)]
)
def delete_critical_dismissal(
    dismissal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not svc.show_again(db, user, dismissal_id):
        raise HTTPException(status_code=404, detail="That hidden vendor wasn't found.")
    return Response(status_code=204)
