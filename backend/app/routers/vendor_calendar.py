import datetime as dt

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.features import require_feature
from app.models import User
from app.schemas import VendorOrderSkipRequest, VendorScheduleCreate, VendorScheduleUpdate
from app.services.schedule_suggestions import (
    MAX_ACCEPT_KEYS,
    accept_all,
    compute_schedule_suggestions,
    find_schedule_for_vendor,
    schedule_key,
    schedule_write_lock,
)
from app.services.suggestion_dismissals import dismiss, undismiss
from app.services.vendor_calendar import get_vendor_calendar, skip_order, unskip_order
from app.services.vendor_schedule import (
    create_vendor_schedule,
    delete_vendor_schedule,
    get_vendor_schedule_by_name,
    list_vendor_schedules,
    schedule_out,
    update_vendor_schedule,
)

router = APIRouter(
    prefix="/api/vendor-calendar", tags=["vendor-calendar"], dependencies=[Depends(get_current_user)]
)

_access = Depends(require_feature("vendor_calendar"))


@router.get("/schedules", dependencies=[_access])
def get_schedules(db: Session = Depends(get_db)):
    return {"items": [schedule_out(r) for r in list_vendor_schedules(db)]}


@router.post("/schedules", dependencies=[_access])
def post_schedule(body: VendorScheduleCreate, db: Session = Depends(get_db)):
    if not body.delivery_weekdays:
        raise HTTPException(status_code=400, detail="Pick at least one delivery day.")
    if body.lead_days < 0:
        raise HTTPException(status_code=400, detail="Lead days can't be negative.")
    # Any spelling ("raja foods", "Sri Sai Imports, Inc" vs "Inc.") counts:
    # a second schedule for the same vendor is never created. Check + insert
    # under Add all's lock, so a double tap or Add all running meanwhile can't
    # slip a second row in (VX12).
    with schedule_write_lock:
        existing = get_vendor_schedule_by_name(db, body.vendor) or find_schedule_for_vendor(db, body.vendor)
        if existing is not None:
            raise HTTPException(status_code=400, detail=f"{existing.vendor} already has a schedule - edit it instead.")
        try:
            row = create_vendor_schedule(db, body.vendor, body.delivery_weekdays, body.lead_days)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"{body.vendor.strip()} already has a schedule - edit it instead.")
    return schedule_out(row)


@router.patch("/schedules/{schedule_id}", dependencies=[_access])
def patch_schedule(schedule_id: int, body: VendorScheduleUpdate, db: Session = Depends(get_db)):
    if body.delivery_weekdays is not None and not body.delivery_weekdays:
        raise HTTPException(status_code=400, detail="Pick at least one delivery day.")
    if body.lead_days is not None and body.lead_days < 0:
        raise HTTPException(status_code=400, detail="Lead days can't be negative.")
    row = update_vendor_schedule(
        db, schedule_id, delivery_weekdays=body.delivery_weekdays, lead_days=body.lead_days, active=body.active
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Vendor schedule not found.")
    return schedule_out(row)


@router.delete("/schedules/{schedule_id}", dependencies=[_access])
def remove_schedule(schedule_id: int, db: Session = Depends(get_db)):
    if not delete_vendor_schedule(db, schedule_id):
        raise HTTPException(status_code=404, detail="Vendor schedule not found.")
    return {"deleted": schedule_id}


# --- Suggested schedules (docs/AUTOMATION_BATCH_CONTRACT.md item 7) ----------------------------
# Read-only: Accept / Edit create the schedule through POST /schedules above,
# which refuses a vendor that already has one - an admin's schedule is never
# overwritten. Registered before the bare "" route only for readability.


class ScheduleSuggestionDismiss(BaseModel):
    vendor: str = Field(..., min_length=1, max_length=200)
    delivery_weekdays: List[int]


@router.get("/schedule-suggestions", dependencies=[_access])
def get_schedule_suggestions(include_dismissed: bool = Query(False), db: Session = Depends(get_db)):
    return compute_schedule_suggestions(db, include_dismissed=include_dismissed)


@router.post("/schedule-suggestions/dismiss", dependencies=[_access])
def post_dismiss_schedule_suggestion(
    body: ScheduleSuggestionDismiss, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if not body.delivery_weekdays or any(w < 0 or w > 6 for w in body.delivery_weekdays):
        raise HTTPException(status_code=400, detail="Weekdays must be 0 (Mon) to 6 (Sun).")
    key = schedule_key(body.vendor, body.delivery_weekdays)
    row = dismiss(db, "vendor_schedule", key, user)
    return {"key": key, "dismissed": True, "dismissed_by": row.dismissed_by_name}


class ScheduleSuggestionsAcceptAll(BaseModel):
    keys: List[str] = Field(..., min_length=1, max_length=MAX_ACCEPT_KEYS)


@router.post("/schedule-suggestions/accept-all", dependencies=[_access])
def post_accept_all_schedule_suggestions(
    body: ScheduleSuggestionsAcceptAll, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """"Add all suggestions": the keys the person confirmed. Each still-offered
    suggestion becomes a schedule exactly as suggested; a vendor that has a
    schedule by now (any spelling, paused too) or a key no longer suggested is
    skipped and listed - an existing schedule is never overwritten."""
    keys = [k.strip() for k in body.keys if isinstance(k, str) and k.strip()]
    if not keys or any(len(k) > 300 for k in keys):
        raise HTTPException(status_code=400, detail="Send the suggestion keys to add.")
    return accept_all(db, keys, user)


@router.delete("/schedule-suggestions/dismiss", dependencies=[_access])
def delete_dismiss_schedule_suggestion(key: str = Query(..., min_length=1, max_length=300), db: Session = Depends(get_db)):
    if not undismiss(db, "vendor_schedule", key):
        raise HTTPException(status_code=404, detail="That suggestion isn't dismissed.")
    return {"key": key, "dismissed": False}


@router.get("", dependencies=[_access])
def get_calendar(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    include_ordered: bool = Query(False),
    db: Session = Depends(get_db),
):
    if end < start:
        raise HTTPException(status_code=400, detail="end must be on or after start.")
    result = get_vendor_calendar(db, start, end)
    if not include_ordered:
        # Released app builds (<= 5) don't know the "ordered" flag and would
        # still count an already-placed order in Home's "Orders due" tile, so
        # by default an order-due whose order is placed is left out. The
        # website (and build 6+) ask for them to show a green "Ordered" chip.
        result = _without_ordered_order_dues(result)
    return result


def _without_ordered_order_dues(result):
    def keep(e):
        return not (isinstance(e, dict) and e.get("type") == "order_due" and e.get("ordered"))

    if isinstance(result, dict):
        return {**result, **{k: [e for e in v if keep(e)] for k, v in result.items() if isinstance(v, list)}}
    if isinstance(result, list):
        return [e for e in result if keep(e)]
    return result


@router.post("/skips", dependencies=[_access])
def post_skip(body: VendorOrderSkipRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Skip the order for one vendor delivery (idempotent)."""
    if not body.vendor.strip():
        raise HTTPException(status_code=400, detail="Vendor is required.")
    skip_order(db, body.vendor, body.delivery_date, user.username)
    return {"vendor": body.vendor.strip(), "delivery_date": body.delivery_date.isoformat(), "skipped": True}


@router.delete("/skips", dependencies=[_access])
def delete_skip(
    vendor: str = Query(..., min_length=1),
    delivery_date: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    """Undo a skip."""
    if not unskip_order(db, vendor, delivery_date):
        raise HTTPException(status_code=404, detail="That order isn't skipped.")
    return {"vendor": vendor.strip(), "delivery_date": delivery_date.isoformat(), "skipped": False}
