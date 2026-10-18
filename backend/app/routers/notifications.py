"""Push notification settings for the mobile app and the website: register/
unregister this phone, per-alert on/off switches (plus "Reset to
recommended"), a test send, and the user's own recent pushes. Only needs a
login - no feature gate, since every user manages their own phone; which
alerts they can see and switch is filtered per-feature in
services/notifications.py.

Admins only (Oct 15): the store defaults per alert and role, and per-person
push volume. None of the new write routes is on the demo guard's allowlist.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.models import User
from app.services import push
from app.services.notifications import (
    DefaultsError,
    defaults_state,
    prefs_payload,
    push_volume,
    recent_pushes,
    register_device,
    reset_prefs,
    reset_store_defaults,
    send_test,
    set_pref,
    set_store_default,
    unregister_device,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class DeviceRegisterRequest(BaseModel):
    token: str
    platform: Literal["ios", "android"]
    # The token this phone registered before FCM rotated it - removed so the
    # old one can't keep delivering to whoever it last belonged to.
    previous_token: Optional[str] = None


class DeviceUnregisterRequest(BaseModel):
    token: str
    # Platform contract 4.4: which store the phone was signed in to
    # (required on the shared service; ignored on a single-store server).
    store_code: Optional[str] = None


class NotificationPrefRequest(BaseModel):
    key: str
    enabled: bool


class StoreDefaultRequest(BaseModel):
    key: str
    role: Literal["staff", "admin"]
    # null = back to the recommended (catalog) default
    enabled: Optional[bool] = None


@router.post("/devices")
def register(body: DeviceRegisterRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Device token is required")
    register_device(db, user, token, body.platform)
    previous = (body.previous_token or "").strip()
    if previous and previous != token:
        unregister_device(db, previous)
    return {"ok": True}


@router.delete("/devices")
def unregister(token: str = Query(...), _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    unregister_device(db, token.strip())
    return {"ok": True}


@router.post("/unregister")
def unregister_without_session(body: DeviceUnregisterRequest):
    """Logout's "this phone stops getting alerts", even when the session has
    already expired (so the authenticated DELETE above can't be used).
    Needs no login on purpose: holding the FCM token is the proof, and the
    only thing it can do is detach that one token. Always 200, so it reveals
    nothing about which tokens exist - on the shared service also for an
    unknown or inactive store code (the body's ``store_code`` picks the
    store's database; nothing else does)."""
    from app.db import SessionLocal
    from app.tenancy import is_multi_tenant, normalize_store_code, tenant_session

    token = body.token.strip()
    if not token:
        return {"ok": True}
    if not is_multi_tenant():
        db = SessionLocal()
        try:
            unregister_device(db, token)
        finally:
            db.close()
        return {"ok": True}
    code = normalize_store_code(body.store_code or "")
    try:
        with tenant_session(code, allow_inactive=True) as db:
            unregister_device(db, token)
    except Exception:  # noqa: BLE001 - unknown / moved / unavailable store: same answer
        pass
    return {"ok": True}


@router.get("/prefs")
def read_prefs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return prefs_payload(db, user)


@router.put("/prefs")
def update_pref(body: NotificationPrefRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        set_pref(db, user, body.key, body.enabled)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown notification")
    return prefs_payload(db, user)


@router.post("/prefs/reset")
def reset_to_recommended(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Forget this user's own choices; the recommended defaults apply again.
    Refused for the App Review demo login (not allowlisted in demo_guard)."""
    reset_prefs(db, user)
    return prefs_payload(db, user)


@router.get("/recent")
def my_recent_pushes(
    limit: int = Query(50, ge=1, le=200), user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return {"items": recent_pushes(db, user, limit)}


@router.get("/defaults")
def read_store_defaults(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return defaults_state(db)


@router.put("/defaults")
def update_store_default(body: StoreDefaultRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return set_store_default(db, admin, body.key, body.role, body.enabled)
    except DefaultsError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/defaults/reset")
def reset_store_defaults_route(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return reset_store_defaults(db, admin)


@router.get("/volume")
def read_push_volume(
    days: int = Query(14, ge=1, le=90), _: User = Depends(require_admin), db: Session = Depends(get_db)
):
    return push_volume(db, days)


@router.post("/test")
def test_notification(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        sent = send_test(db, user)
    except push.PushNotConfigured:
        raise HTTPException(status_code=503, detail="Notifications aren't set up on the server yet.")
    return {"sent": sent}
