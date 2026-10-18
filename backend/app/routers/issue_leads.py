"""Category & vendor leads admin page (docs/BATCH_OCT14_CONTRACT.md, W4).

Admins only, every route. The demo login is refused on every write by the
global demo guard (none of these routes are allowlisted). See
services/issue_leads.py for the rules.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import User
from app.services import issue_leads as svc

router = APIRouter(prefix="/api/issue-leads", tags=["issue-leads"], dependencies=[Depends(require_admin)])


class LeadEntry(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    user_id: Optional[int] = None


class FallbackEntry(BaseModel):
    user_id: Optional[int] = None


class LeadsSave(BaseModel):
    sections: List[LeadEntry] = Field(default_factory=list, max_length=1000)
    vendors: List[LeadEntry] = Field(default_factory=list, max_length=1000)
    fallback: Optional[FallbackEntry] = None


class SettingsSave(BaseModel):
    enabled: Optional[bool] = None
    time: Optional[str] = Field(default=None, max_length=10)
    default_hours: Optional[float] = None
    hours_by_user: Optional[Dict[str, Any]] = None
    hours_by_type: Optional[Dict[str, Any]] = None
    types_off: Optional[List[str]] = Field(default=None, max_length=50)


@router.get("")
def get_leads(db: Session = Depends(get_db)):
    return svc.leads_page(db)


@router.put("")
def save_leads(body: LeadsSave, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    data = body.model_dump()
    if body.fallback is None:
        data.pop("fallback", None)
    try:
        n = svc.save_leads(db, data, user)
    except svc.LeadsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**svc.leads_page(db), "saved_count": n}


@router.post("/accept-proposal")
def accept_proposal(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    n = svc.accept_proposal(db, user)
    return {**svc.leads_page(db), "saved_count": n}


@router.get("/preview")
def preview(db: Session = Depends(get_db)):
    return svc.preview(db)


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db)):
    return {"settings": svc.get_task_settings(db), "changed": svc.settings_meta(db)}


@router.put("/settings")
def save_settings(body: SettingsSave, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    try:
        settings = svc.save_task_settings(db, body.model_dump(exclude_none=True), user)
    except svc.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"settings": settings, "changed": svc.settings_meta(db)}


@router.post("/run-now")
def run_now(db: Session = Depends(get_db)):
    """Creates (or updates) today's lead tasks right away."""
    from app.config import get_settings

    if not get_settings().team_tasks_enabled:
        raise HTTPException(status_code=503, detail="Tasks are turned off right now")
    result = svc.run_daily_tasks(db, force=True)
    if result is None:
        raise HTTPException(status_code=409, detail="A run is already in progress. Try again in a moment.")
    return result
