"""Master console: masters, platform settings, store detail (docs/WEBSITE_READY_CONTRACT.md, track A).

Mounted once (app/platform/routes.py install() / app/main.py). Prefix
/api/platform; every route is masters-only with the guards of
app/platform/routes.py, and every write is audited.

- ``GET/POST /masters``, ``DELETE /masters/{id}``, ``POST /masters/{id}/reset-two-step``,
  ``POST /masters/me/two-step/move`` (A2)
- ``GET/PATCH /settings``, ``DELETE /settings/{key}`` (A3)
- ``GET /stores/{code}/health`` (A4)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.platform import masters
from app.platform import platform_settings as ps
from app.platform.db import get_platform_db
from app.platform.errors import PlatformError
from app.platform.models import PlatformUser

router = APIRouter(prefix="/api/platform", tags=["platform"])

NO_STORE = {"Cache-Control": "no-store"}


def _write(request: Request, user: PlatformUser = Depends(masters.require_master)) -> PlatformUser:
    from app.platform.routes import platform_write

    return platform_write(request, user)


def _ip(request: Request) -> str:
    from app.platform import throttle

    return throttle.client_ip(request)


# --- A2 masters -------------------------------------------------------------------------


@router.get("/masters")
def list_masters(user: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    return {"masters": masters.masters_view(db, user)}


class AddMasterBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=200)
    totp_code: Optional[str] = Field(default=None, max_length=12)


def _step_up_code(request: Request, body_code: Optional[str] = None) -> Optional[str]:
    return body_code or request.headers.get(masters.STEP_UP_HEADER)


@router.post("/masters", status_code=201)
def add_master(body: AddMasterBody, request: Request, user: PlatformUser = Depends(_write),
               db: Session = Depends(get_platform_db)):
    masters.check_step_up(user, _step_up_code(request, body.totp_code))
    created, password = masters.add_master(
        db, body.username, display_name=body.display_name or "", email=body.email, actor=user, client_ip=_ip(request)
    )
    out = masters.user_view(created)
    # Shown once: the new master signs in with it and must choose their own.
    out["temporary_password"] = password
    return JSONResponse(out, status_code=201, headers=NO_STORE)


@router.delete("/masters/{user_id}")
def remove_master(user_id: int, request: Request, user: PlatformUser = Depends(_write),
                  db: Session = Depends(get_platform_db)):
    masters.check_step_up(user, _step_up_code(request))
    removed = masters.remove_master(db, user_id, actor=user, client_ip=_ip(request))
    return {"ok": True, "username": removed.username}


@router.post("/masters/{user_id}/reset-two-step")
def reset_two_step(user_id: int, request: Request, user: PlatformUser = Depends(_write),
                   db: Session = Depends(get_platform_db)):
    masters.check_step_up(user, _step_up_code(request))
    target = masters.reset_two_step(db, user_id, actor=user, client_ip=_ip(request))
    return masters.user_view(target)


class MoveTwoStepBody(BaseModel):
    current_code: str = Field(min_length=6, max_length=10)


@router.post("/masters/me/two-step/move")
def move_two_step(body: MoveTwoStepBody, user: PlatformUser = Depends(masters.require_master_any),
                  db: Session = Depends(get_platform_db)):
    """Start moving two-step sign-in to a new phone: needs a code from the
    current one. Confirm with ``POST /users/me/totp/enable`` as at first set-up."""
    from app.platform.routes import _rate

    _rate("platform_totp", str(user.id), per_minute=5)
    return JSONResponse(masters.totp_begin(db, user, current_code=body.current_code), headers=NO_STORE)


# --- A3 platform settings ------------------------------------------------------------------


def _settings_payload() -> Dict[str, Any]:
    return {"settings": ps.describe_all()}


@router.get("/settings")
def get_platform_settings(_: PlatformUser = Depends(masters.require_master)):
    return _settings_payload()


@router.patch("/settings")
def patch_platform_settings(request: Request, body: Dict[str, Any] = Body(...), user: PlatformUser = Depends(_write),
                            db: Session = Depends(get_platform_db)):
    try:
        changed = ps.save_settings(db, body, actor=masters.actor_name(user), actor_id=user.id, client_ip=_ip(request))
    except ps.SettingError as e:
        raise PlatformError(400, "invalid", e.message, field=e.key) from None
    out = _settings_payload()
    out["changed"] = changed
    return out


@router.delete("/settings/{key}")
def reset_platform_setting(key: str, request: Request, user: PlatformUser = Depends(_write),
                           db: Session = Depends(get_platform_db)):
    try:
        ps.reset_setting(db, key, actor=masters.actor_name(user), actor_id=user.id, client_ip=_ip(request))
    except ps.SettingError as e:
        raise PlatformError(400, "invalid", e.message, field=e.key) from None
    return _settings_payload()


# --- A4 store health -------------------------------------------------------------------------

_EMPTY_DB = {"reachable": False, "revision": None, "jobs": [], "last_sign_in_at": None, "error": None}


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _store_db_health(code: str) -> Dict[str, Any]:
    """Revision, newest run of every scheduled job and the newest sign-in, read
    from the store's own database. A store database that cannot be reached is
    reported, never raised."""
    from app.models import JobRun, UserSession
    from app.platform.bridge import tenant_session

    out: Dict[str, Any] = dict(_EMPTY_DB)
    try:
        with tenant_session(code) as sdb:
            out["revision"] = sdb.execute(text("SELECT version_num FROM alembic_version")).scalar()
            newest = select(JobRun.job_name, func.max(JobRun.id).label("id")).group_by(JobRun.job_name).subquery()
            rows = sdb.execute(
                select(JobRun).join(newest, JobRun.id == newest.c.id).order_by(JobRun.job_name)
            ).scalars()
            out["jobs"] = [
                {
                    "job": r.job_name,
                    "status": r.status,
                    "started_at": _iso(r.started_at),
                    "finished_at": _iso(r.finished_at),
                    "business_date": _iso(r.business_date),
                    # Failures only, shortened: a success detail can carry sales figures.
                    "error": (r.detail or "")[:300] if r.status == "failed" else None,
                }
                for r in rows
            ]
            out["last_sign_in_at"] = _iso(sdb.execute(select(func.max(UserSession.created_at))).scalar())
            out["reachable"] = True
    except Exception as e:  # noqa: BLE001 - shown on the Overview tab, never a 500
        out["error"] = type(e).__name__
    return out


@router.get("/stores/{code}/health")
def store_health(code: str, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    from app.platform import credentials as creds
    from app.platform import stores

    store = stores.store_by_code(db, code)
    out = stores.store_row(store)
    out["provisioning_step"] = store.provisioning_step
    out["maintenance_message"] = store.maintenance_message
    keep = ("kind", "label", "configured", "status", "last_tested_at", "last_test_message")
    out["integrations"] = [{k: v.as_dict()[k] for k in keep} for v in creds.list_credentials(store.code, db=db)]
    ready = store.status not in ("pending", "provisioning", "failed")
    out["database"] = _store_db_health(store.code) if ready else dict(_EMPTY_DB)
    activity = None
    try:
        from app.platform import usage

        activity = (usage.snapshot(store.code) or {}).get(usage.LAST_ACTIVITY_AT)
    except Exception:  # noqa: BLE001 - usage not measured yet
        activity = None
    out["last_activity_at"] = activity or out["database"].get("last_sign_in_at")
    return JSONResponse(out, headers=NO_STORE)
