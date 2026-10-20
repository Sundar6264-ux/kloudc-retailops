"""Store data: usage, plans, exports, off-boarding (docs/WEBSITE_READY_CONTRACT.md, track B).

Mounted once by app/platform/routes.py ``install()``.

Masters (``/api/platform``; same guards as app/platform/routes.py, every
write audited):
    GET  /usage/summary                          plan + limit state of every store (Stores list flag)
    GET  /stores/{code}/usage                    usage numbers, days, plan and limits
    POST /stores/{code}/usage/measure            measure the gauges now
    PUT  /stores/{code}/plan                     {"plan"}
    GET  /stores/{code}/exports                  data exports
    POST /stores/{code}/exports                  Export all data
    POST /stores/{code}/exports/{id}/link        a Download link
    GET  /stores/{code}/offboarding              state + timeline
    POST /stores/{code}/offboarding              {"confirm", "reason"}: archive, final export, grace period
    POST /stores/{code}/offboarding/cancel       {"restore"}
    POST /stores/{code}/offboarding/purge        {"confirm", "totp_code"}: Delete store data

The store's own owner/admin (``/api/settings``; the store comes from the
session, never from the request):
    GET  /usage                                  usage, plan and near/over notices
    GET  /exports                                data exports
    POST /exports                                Export all data
    POST /exports/{id}/link                      a Download link
    GET  /exports/{id}/download?token=           the file (the signed link itself; no session needed)
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.models import User
from app.platform import billing, export, masters, offboarding, plans, usage
from app.platform.audit import audit
from app.platform.bridge import current_store_code, is_multi_tenant
from app.platform.db import get_platform_db, is_configured
from app.platform.errors import PlatformError
from app.platform.models import PlatformUser, Store
from app.platform.routes import NO_STORE, _ip, _rate, platform_write
from app.platform.stores import store_by_code

# BILLING_PROVIDER=stripe without its keys refuses to start (B3).
billing.startup_check()

router = APIRouter(prefix="/api/platform", tags=["platform"])
store_router = APIRouter(prefix="/api/settings", tags=["settings"])


def _usage_body(code: str) -> Dict[str, Any]:
    snap = usage.snapshot(code)
    return {"store_code": code, **snap, "plan": plans.plan_view(code, metrics=snap["metrics"])}


# =============================================================================
# masters
# =============================================================================


@router.get("/usage/summary")
def usage_summary(_: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    out = []
    for code in db.execute(select(Store.code).order_by(Store.code)).scalars():
        snap = usage.snapshot(code, days=1)
        view = plans.plan_view(code, metrics=snap["metrics"])
        out.append({"store_code": code, "plan": view["plan"], "plan_label": view["label"], "state": view["state"],
                    "last_activity_at": snap["metrics"].get(usage.LAST_ACTIVITY_AT)})
    return out


@router.get("/stores/{code}/usage")
def store_usage(code: str, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    store = store_by_code(db, code)
    return _usage_body(store.code)


@router.post("/stores/{code}/usage/measure")
def measure_usage(code: str, request: Request, user: PlatformUser = Depends(platform_write),
                  db: Session = Depends(get_platform_db)):
    store = store_by_code(db, code)
    if store.status not in ("active", "suspended"):
        raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
    usage.flush()
    try:
        usage.measure_gauges(store.code)
    except Exception:  # noqa: BLE001 - the store database is not reachable
        raise PlatformError(409, "conflict", "The store's data could not be measured right now.") from None
    audit("store.usage_measured", actor_type="platform_user", actor_name=masters.actor_name(user),
          store_code=store.code, client_ip=_ip(request))
    return _usage_body(store.code)


class PlanBody(BaseModel):
    plan: str = Field(min_length=1, max_length=16)


@router.put("/stores/{code}/plan")
def put_plan(code: str, body: PlanBody, request: Request, user: PlatformUser = Depends(platform_write),
             db: Session = Depends(get_platform_db)):
    try:
        plans.check_plan(body.plan)
    except plans.UnknownPlan as exc:
        raise PlatformError(400, "invalid", str(exc), field="plan") from None
    view = plans.set_plan(db, code, body.plan, actor=masters.actor_name(user), client_ip=_ip(request))
    store_code = code.strip().upper()
    try:
        billing.get_billing().plan_changed(store_code, view["plan"])
    except Exception as exc:  # noqa: BLE001 - billing never undoes a plan change
        audit("store.billing_failed", actor_type="system", store_code=store_code, details={"error": str(exc)[:300]})
    return _usage_body(store_code)


@router.get("/stores/{code}/exports")
def master_exports(code: str, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    store = store_by_code(db, code)
    return {"store_code": store.code, "exports": export.list_exports(store.code), "link_hours": export.link_hours(),
            "keep_days": export.KEEP_DAYS}


@router.post("/stores/{code}/exports", status_code=202)
def master_export(code: str, request: Request, user: PlatformUser = Depends(platform_write)):
    return export.request_export(code, actor=masters.actor_name(user), actor_type="platform_user", client_ip=_ip(request))


@router.post("/stores/{code}/exports/{export_id}/link")
def master_export_link(code: str, export_id: int, request: Request, user: PlatformUser = Depends(platform_write)):
    return export.create_link(code.strip().upper(), export_id, actor=masters.actor_name(user),
                              actor_type="platform_user", client_ip=_ip(request))


@router.get("/stores/{code}/offboarding")
def get_offboarding(code: str, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    store_by_code(db, code)
    return offboarding.view(code)


class OffboardBody(BaseModel):
    confirm: str = Field(min_length=1, max_length=24)
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/stores/{code}/offboarding")
def start_offboarding(code: str, body: OffboardBody, request: Request, user: PlatformUser = Depends(platform_write)):
    return offboarding.start(code, actor=masters.actor_name(user), confirm=body.confirm, reason=body.reason,
                             client_ip=_ip(request))


class CancelBody(BaseModel):
    restore: bool = False


@router.post("/stores/{code}/offboarding/cancel")
def cancel_offboarding(code: str, request: Request, body: CancelBody = Body(default_factory=CancelBody),
                       user: PlatformUser = Depends(platform_write)):
    return offboarding.cancel(code, actor=masters.actor_name(user), restore=body.restore, client_ip=_ip(request))


class PurgeBody(BaseModel):
    confirm: str = Field(min_length=1, max_length=24)
    totp_code: Optional[str] = Field(default=None, max_length=12)


@router.post("/stores/{code}/offboarding/purge")
def purge_store_data(code: str, body: PurgeBody, request: Request, user: PlatformUser = Depends(platform_write)):
    _rate("store_purge", str(user.id), per_minute=5)
    return offboarding.purge(code, actor=masters.actor_name(user), confirm=body.confirm, master=user,
                             totp_code=body.totp_code, client_ip=_ip(request))


# =============================================================================
# the store's own owner/admin
# =============================================================================


def _own_store(user: User) -> Optional[str]:
    """The admin's store from the session; None on a single-store server."""
    if not is_multi_tenant() or not is_configured():
        return None
    code = current_store_code()
    if not code:
        raise PlatformError(401, "not_logged_in")
    return code


def _require_own_store(user: User) -> str:
    code = _own_store(user)
    if code is None:
        raise PlatformError(409, "platform_mode_required",
                            "Data export is part of the shared service; on this server ask whoever runs it.")
    return code


def _refuse_demo(user: User, code: str) -> None:
    from app.platform.routes import _refuse_demo as refuse

    refuse(user, code)


def _store_actor(code: str, user: User) -> str:
    return f"store:{code}:{user.username}"


@store_router.get("/usage")
def own_usage(user: User = Depends(require_admin)):
    code = _own_store(user)
    if code is None:
        return {"available": False, "message": "Usage is measured on the shared service."}
    body = _usage_body(code)
    body["plan"].pop("plans", None)  # the plan list is for masters
    return {"available": True, **body}


@store_router.get("/exports")
def own_exports(user: User = Depends(require_admin)):
    code = _own_store(user)
    if code is None:
        return {"available": False, "exports": []}
    rows = export.list_exports(code)
    for row in rows:  # a store sees its own people; masters and jobs are "KloudC"
        if not str(row.get("requested_by") or "").startswith(f"store:{code}:"):
            row["requested_by"] = "KloudC"
    return {"available": True, "store_code": code, "exports": rows,
            "link_hours": export.link_hours(), "keep_days": export.KEEP_DAYS}


@store_router.post("/exports", status_code=202)
def own_export(request: Request, user: User = Depends(require_admin)):
    code = _require_own_store(user)
    _refuse_demo(user, code)
    _rate("store_export", code, per_hour=6)
    return export.request_export(code, actor=_store_actor(code, user), actor_type="store_user", client_ip=_ip(request))


@store_router.post("/exports/{export_id}/link")
def own_export_link(export_id: int, request: Request, user: User = Depends(require_admin)):
    code = _require_own_store(user)
    _refuse_demo(user, code)
    _rate("store_export_link", code, per_minute=20)
    return export.create_link(code, export_id, actor=_store_actor(code, user), actor_type="store_user",
                              client_ip=_ip(request))


@store_router.get("/exports/{export_id}/download")
def export_download(export_id: int, request: Request, token: str = ""):
    """The signed Download link itself (no session: the link is the key)."""
    if not is_configured():
        raise PlatformError(404, "not_found")
    _rate("export_download", _ip(request), per_minute=30)
    stream, filename, size = export.open_download(export_id, token, client_ip=_ip(request))

    def chunks():
        try:
            while True:
                chunk = stream.read(export.CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            stream.close()

    headers = {**NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"',
               "X-Content-Type-Options": "nosniff"}
    if size:
        headers["Content-Length"] = str(size)
    return StreamingResponse(chunks(), media_type="application/zip", headers=headers)
