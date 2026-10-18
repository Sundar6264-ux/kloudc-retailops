"""Admin page for the inventory-from-Toast-API work (services/inventory_api.py,
docs/AUTOMATION_BATCH_CONTRACT.md items 1 and 6): settings and job status,
the side-by-side comparison of an API-built snapshot with the CSV upload
(nightly, and "compare now"), and a manual barcode re-check.

Admins only. Nothing here writes inventory: "compare now" builds the API
set in memory; the barcode re-check only refreshes the toast_menu_items
cache (the same thing the hourly job does). PUT /switch turns "Use Toast for
inventory" on or off (services/inventory_api_switch.py) - effective at once;
the demo login is refused by the global demo guard."""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import JobRun, User
from app.pos.capabilities import Capability
from app.pos.gating import require_capability
from app.services import inventory_api as svc
from app.services import inventory_api_readiness, inventory_api_switch

router = APIRouter(
    prefix="/api/inventory-api",
    tags=["inventory-api"],
    # Multi-store MT6: inventory from the POS needs its catalog and live stock.
    dependencies=[Depends(require_admin), Depends(require_capability(Capability.CATALOG, Capability.STOCK_LEVELS))],
)

# A manual re-check right after another one only spends Toast's rate limit.
MANUAL_RECHECK_MIN_SECONDS = 120


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    return svc.status(db)


@router.get("/compare")
def compare(refresh: bool = Query(False), db: Session = Depends(get_db)):
    """API set built right now (nothing stored) vs the latest CSV upload.
    Two Toast GETs, cached 10 minutes; `refresh=true` rebuilds."""
    try:
        return svc.compare_now(db, refresh=refresh)
    except svc.ToastFetchError as exc:
        raise HTTPException(status_code=503, detail=f"Couldn't build the comparison: {exc}") from exc


@router.get("/compare/nightly")
def compare_nightly(db: Session = Depends(get_db)):
    """The newest 04:05 run that had the same day's CSV to compare with
    (null when there hasn't been one yet)."""
    return {"nightly": svc.last_nightly_comparison(db)}


@router.post("/menu-recheck", dependencies=[Depends(require_capability(Capability.CATALOG_CHANGE_STAMP))])
def menu_recheck(db: Session = Depends(get_db)):
    last = db.execute(
        select(JobRun.started_at)
        .where(JobRun.job_name == svc.JOB_MENU_RECHECK)
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(1)
    ).scalar()
    if last is not None:
        last = last if last.tzinfo else last.replace(tzinfo=dt.timezone.utc)
        if (dt.datetime.now(dt.timezone.utc) - last).total_seconds() < MANUAL_RECHECK_MIN_SECONDS:
            raise HTTPException(status_code=429, detail="Checked under two minutes ago - try again in a moment.")
    job = svc.refresh_menu_cache(db)
    if job.status != "success":
        raise HTTPException(status_code=503, detail="Toast's menu couldn't be checked right now.")
    return {"status": job.status, "detail": svc._last_job(db, svc.JOB_MENU_RECHECK)["detail"]}


class SwitchIn(BaseModel):
    on: bool
    note: Optional[str] = Field(default=None, max_length=inventory_api_switch.NOTE_MAX_CHARS)


def _switch_payload(db: Session) -> dict:
    state = inventory_api_switch.switch_state(db)
    r = inventory_api_readiness.readiness(db, switch_on=bool(state["on"]))
    return {
        **state,
        "history": inventory_api_switch.history(db),
        "readiness": {k: r[k] for k in ("ready", "streak", "needed", "last_comparison_at")},
    }


@router.get("/switch")
def get_switch(db: Session = Depends(get_db)):
    """The "Use Toast for inventory" switch: in force now, where it comes from
    ("page" = an admin's change here, "env" = INVENTORY_API_SNAPSHOTS while
    nobody has changed it here), and its change history (newest first)."""
    return _switch_payload(db)


@router.put("/switch")
def put_switch(body: SwitchIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Turn it on or off - applies to the next nightly run and the next page
    load at once (no restart). Recorded with who / when / before."""
    result = inventory_api_switch.set_switch(db, user, body.on, body.note)
    return {"changed": result["changed"], **_switch_payload(db)}
