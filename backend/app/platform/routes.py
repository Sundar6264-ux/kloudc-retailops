"""HTTP routes of the platform (contract 13; owner C).

- ``/api/platform/*``      masters only (platform session)
- ``/api/signup*``         public sign-up, code check, status, owner set-up link
- ``/api/public/stores/{code}``  public store lookup (M2 directory JSON)
- ``/api/settings/integrations/*``, ``/api/settings/store``,
  ``/api/settings/hosting/request``  store admin (store session)

``install(app)`` adds them and the JSON error handler; ``app/main.py`` calls
it (one include line).
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin, verify_password
from app.models import User
from app.platform import credentials as creds
from app.platform import hosting, masters, provisioning, signup, stores, throttle
from app.platform.audit import audit, list_audit
from app.platform.bridge import current_store_code, is_multi_tenant
from app.platform.config import dedicated_enabled, login_code, pget, platform_public_url
from app.platform.connector_tests import run_test
from app.platform.db import get_platform_db, platform_session
from app.platform.errors import PlatformError, platform_error_handler
from app.platform.models import PlatformUser, SignupRequest, Store

platform_router = APIRouter(prefix="/api/platform", tags=["platform"])
signup_router = APIRouter(prefix="/api/signup", tags=["signup"])
public_router = APIRouter(prefix="/api/public", tags=["public"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])

NO_STORE = {"Cache-Control": "no-store"}


def _rate(name: str, key: str, per_minute: int = 0, per_hour: int = 0) -> None:
    wait = throttle.check_rate(name, key, per_minute, per_hour)
    if wait is not None:
        raise PlatformError(429, "rate_limited", headers={"Retry-After": str(wait)}, retry_after=wait)


def platform_write(request: Request, user: PlatformUser = Depends(masters.require_master)) -> PlatformUser:
    """Masters' write routes: a session plus a per-master rate limit."""
    _rate("platform_write", str(user.id), per_minute=int(pget("platform_write_rate_per_minute") or 60))
    return user


def _ip(request: Request) -> str:
    return throttle.client_ip(request)


# =============================================================================
# /api/platform  (masters)
# =============================================================================


@platform_router.get("/config")
def platform_config(user: PlatformUser = Depends(masters.require_master_any)):
    return {
        "login_code": login_code(),
        "public_url": platform_public_url(),
        "dedicated_hosting_enabled": dedicated_enabled(),
        "require_totp": bool(pget("platform_require_totp")),
        "signup_enabled": bool(pget("signup_enabled")),
        "user": masters.user_view(user),
    }


# --- sign-up requests ---------------------------------------------------------


@platform_router.get("/signups")
def list_signups(
    status: Optional[str] = "pending",
    _: PlatformUser = Depends(masters.require_master),
    db: Session = Depends(get_platform_db),
):
    q = select(SignupRequest).order_by(SignupRequest.id.desc()).limit(500)
    if status and status != "all":
        q = q.where(SignupRequest.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    return [signup.request_view(r) for r in db.execute(q).scalars()]


@platform_router.get("/signups/{request_id}")
def get_signup(request_id: int, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    req = db.get(SignupRequest, request_id)
    if req is None:
        raise PlatformError(404, "not_found")
    out = signup.request_view(req)
    code, available, reason = signup.code_status(db, req.requested_code) if req.status == "pending" else (req.requested_code, None, None)
    if req.status == "pending":
        # Its own pending row makes the code look "requested": not a clash.
        out["code_available"] = available or reason == "requested"
        out["code_reason"] = None if out["code_available"] else reason
    return out


class ApproveBody(BaseModel):
    code: Optional[str] = None
    pack_id: Optional[str] = None
    timezone: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=1000)


@platform_router.post("/signups/{request_id}/approve", status_code=202)
def approve_signup(request_id: int, request: Request, body: ApproveBody = Body(default_factory=ApproveBody),
                   user: PlatformUser = Depends(platform_write)):
    return provisioning.approve_signup(
        request_id, actor=masters.actor_name(user), overrides=body.model_dump(exclude_none=True), client_ip=_ip(request)
    )


class NoteBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


@platform_router.post("/signups/{request_id}/reject")
def reject_signup(request_id: int, request: Request, body: NoteBody = Body(default_factory=NoteBody),
                  user: PlatformUser = Depends(platform_write), db: Session = Depends(get_platform_db)):
    return signup.request_view(signup.reject(db, request_id, actor=masters.actor_name(user), note=body.note, client_ip=_ip(request)))


# --- stores ---------------------------------------------------------------------


@platform_router.get("/stores")
def list_stores(status: Optional[str] = None, q: Optional[str] = None,
                _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    return stores.list_stores(db, status, q)


@platform_router.get("/stores/{code}")
def get_store(code: str, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    out = stores.store_detail(db, code)
    out["setup_link_pending"] = None
    result = provisioning.last_result(out["code"])
    if result and result.get("setup_url"):
        # Shown to masters until the process restarts (never stored).
        out["setup_link_pending"] = result["setup_url"]
    return out


@platform_router.post("/stores/{code}/retry-provisioning", status_code=202)
def retry_provisioning(code: str, request: Request, user: PlatformUser = Depends(platform_write)):
    return provisioning.retry_provisioning(code.upper(), actor=masters.actor_name(user), client_ip=_ip(request))


class SuspendBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    message: Optional[str] = Field(default=None, max_length=500)


@platform_router.post("/stores/{code}/suspend")
def suspend_store(code: str, body: SuspendBody, request: Request, user: PlatformUser = Depends(platform_write),
                  db: Session = Depends(get_platform_db)):
    return stores.store_row(stores.suspend(db, code, actor=masters.actor_name(user), reason=body.reason,
                                           message=body.message, client_ip=_ip(request)))


@platform_router.post("/stores/{code}/resume")
def resume_store(code: str, request: Request, user: PlatformUser = Depends(platform_write),
                 db: Session = Depends(get_platform_db)):
    return stores.store_row(stores.resume(db, code, actor=masters.actor_name(user), client_ip=_ip(request)))


class ArchiveBody(BaseModel):
    confirm: str
    reason: Optional[str] = Field(default=None, max_length=500)


@platform_router.post("/stores/{code}/archive")
def archive_store(code: str, body: ArchiveBody, request: Request, user: PlatformUser = Depends(platform_write),
                  db: Session = Depends(get_platform_db)):
    return stores.store_row(stores.archive(db, code, actor=masters.actor_name(user), confirm=body.confirm,
                                           reason=body.reason, client_ip=_ip(request)))


@platform_router.get("/stores/{code}/settings")
def get_store_settings(code: str, _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    return stores.settings_values(db, stores.store_by_code(db, code))


@platform_router.put("/stores/{code}/settings")
def put_store_settings(code: str, request: Request, body: Dict[str, Any] = Body(...),
                       user: PlatformUser = Depends(platform_write), db: Session = Depends(get_platform_db)):
    return stores.put_settings(db, code, body, actor=masters.actor_name(user), client_ip=_ip(request))


@platform_router.get("/stores/{code}/integrations")
def get_store_integrations(code: str, _: PlatformUser = Depends(masters.require_master),
                           db: Session = Depends(get_platform_db)):
    """Masters see status and masked values only, like the store admin."""
    store = stores.store_by_code(db, code)
    return [v.as_dict() for v in creds.list_credentials(store.code, db=db)]


class SupportBody(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    username: Optional[str] = Field(default=None, max_length=64)
    minutes: Optional[int] = Field(default=None, ge=5, le=120)


@platform_router.post("/stores/{code}/support-session")
def start_support(code: str, body: SupportBody, request: Request, user: PlatformUser = Depends(platform_write),
                  db: Session = Depends(get_platform_db)):
    """Impersonate for support: a time-limited store session (default 30
    minutes, max 120) as the store's first admin or a named user. Audited.
    The website signs in with it through ``POST /api/auth/support-login``."""
    store = stores.store_by_code(db, code)
    if store.status != "active":
        raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
    if not is_multi_tenant():
        raise PlatformError(409, "platform_mode_required", "Support sessions need TENANCY_MODE=multi.")
    out = masters.start_support_session(user, store.code, reason=body.reason, username=body.username,
                                        minutes=body.minutes, client_ip=_ip(request))
    return out


@platform_router.post("/support-sessions/{session_id}/end")
def end_support(session_id: int, request: Request, user: PlatformUser = Depends(platform_write)):
    if not masters.end_support_session(user, session_id, client_ip=_ip(request)):
        raise PlatformError(404, "not_found")
    return {"ok": True}


class SetupLinkBody(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)


@platform_router.post("/stores/{code}/owner-setup-link")
def owner_setup_link(code: str, request: Request, body: SetupLinkBody = Body(default_factory=SetupLinkBody),
                     user: PlatformUser = Depends(platform_write), db: Session = Depends(get_platform_db)):
    """A one-time link for the store's owner (or a named user) to set a new
    password; shown once, valid OWNER_SETUP_LINK_HOURS (72). Not emailed."""
    store = stores.store_by_code(db, code)
    if store.status not in ("active", "suspended"):
        raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
    username = (body.username or store.owner_username or "").strip()
    if not username:
        raise PlatformError(400, "invalid", "Name the user.")
    url = provisioning.issue_setup_link(db, store, username, actor=masters.actor_name(user))
    db.commit()
    return {"store_code": store.code, "username": username, "setup_url": url}


# --- dedicated hosting --------------------------------------------------------------


@platform_router.get("/hosting-requests")
def list_hosting(status: Optional[str] = None, store: Optional[str] = None,
                 _: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    return {"enabled": dedicated_enabled(), "requests": hosting.list_requests(db, status, (store or "").upper() or None)}


class HostingRequestBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    target_api_base_url: Optional[str] = None
    target_db_host_ref: Optional[str] = None


@platform_router.post("/stores/{code}/hosting-requests")
def master_hosting_request(code: str, body: HostingRequestBody, request: Request,
                           user: PlatformUser = Depends(platform_write), db: Session = Depends(get_platform_db)):
    return hosting.create_request(db, code, actor=masters.actor_name(user), actor_type="platform_user",
                                  reason=body.reason, target_api_base_url=body.target_api_base_url,
                                  target_db_host_ref=body.target_db_host_ref, client_ip=_ip(request))


class HostingReviewBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    target_api_base_url: Optional[str] = None
    target_db_host_ref: Optional[str] = None
    api_base_url: Optional[str] = None
    db_host_ref: Optional[str] = None


@platform_router.post("/hosting-requests/{request_id}/{action}")
def hosting_action(request_id: int, action: str, request: Request,
                   body: HostingReviewBody = Body(default_factory=HostingReviewBody),
                   user: PlatformUser = Depends(platform_write), db: Session = Depends(get_platform_db)):
    actor, ip = masters.actor_name(user), _ip(request)
    if action == "approve":
        return hosting.approve(db, request_id, actor=actor, note=body.note, target_api_base_url=body.target_api_base_url,
                               target_db_host_ref=body.target_db_host_ref, client_ip=ip)
    if action == "reject":
        return hosting.reject(db, request_id, actor=actor, note=body.note, client_ip=ip)
    if action == "cancel":
        return hosting.cancel(db, request_id, actor=actor, note=body.note, client_ip=ip)
    if action == "start":
        return hosting.start(db, request_id, actor=actor, client_ip=ip)
    if action == "complete":
        return hosting.complete(db, request_id, actor=actor, api_base_url=body.api_base_url,
                                db_host_ref=body.db_host_ref, client_ip=ip)
    if action == "rollback":
        return hosting.rollback(db, request_id, actor=actor, note=body.note, client_ip=ip)
    raise PlatformError(404, "not_found")


# --- audit, packs, masters ---------------------------------------------------------------


@platform_router.get("/audit")
def get_audit(store: Optional[str] = None, action: Optional[str] = None, before: Optional[int] = None,
              limit: int = Query(default=50, ge=1, le=200), _: PlatformUser = Depends(masters.require_master),
              db: Session = Depends(get_platform_db)):
    return list_audit(db, store=store, action=action, before=before, limit=limit)


@platform_router.get("/packs")
def get_packs(_: PlatformUser = Depends(masters.require_master)):
    return provisioning.list_packs()


@platform_router.get("/users")
def list_masters(_: PlatformUser = Depends(masters.require_master), db: Session = Depends(get_platform_db)):
    return [masters.user_view(u) for u in db.execute(select(PlatformUser).order_by(PlatformUser.username)).scalars()]


class NewMasterBody(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    email: Optional[str] = None


@platform_router.post("/users", status_code=201)
def create_master(body: NewMasterBody, request: Request, user: PlatformUser = Depends(platform_write),
                  db: Session = Depends(get_platform_db)):
    created = masters.create_master(db, body.username, body.password, body.display_name or "", body.email,
                                    actor=masters.actor_name(user), client_ip=_ip(request))
    return masters.user_view(created)


class PasswordBody(BaseModel):
    current: str
    new: str


@platform_router.post("/users/me/password")
def change_master_password(body: PasswordBody, request: Request,
                           user: PlatformUser = Depends(masters.require_master_any),
                           db: Session = Depends(get_platform_db)):
    _rate("platform_password", str(user.id), per_minute=5)
    me = db.get(PlatformUser, user.id)
    if not verify_password(body.current, me.password_hash):
        raise PlatformError(400, "invalid", "The current password is not right.")
    masters.check_password_rules(body.new)
    me.password_hash = hash_password(body.new)
    from sqlalchemy import delete

    from app.platform.models import PlatformSession

    token = request.cookies.get("session_token") or (request.headers.get("authorization") or "").partition(" ")[2].strip()
    # Other sessions of this master end; this one stays.
    db.execute(delete(PlatformSession).where(PlatformSession.platform_user_id == me.id,
                                             PlatformSession.token_hash != masters.token_hash(token or "")))
    audit("platform_user.password_changed", actor_type="platform_user", actor_id=me.id,
          actor_name=masters.actor_name(me), target=me.username, client_ip=_ip(request), db=db)
    db.commit()
    return {"ok": True}


@platform_router.post("/users/me/totp/setup")
def totp_setup(user: PlatformUser = Depends(masters.require_master_any), db: Session = Depends(get_platform_db)):
    _rate("platform_totp", str(user.id), per_minute=5)
    return masters.totp_begin(db, user)


class TotpEnableBody(BaseModel):
    pending: str
    code: str


@platform_router.post("/users/me/totp/enable")
def totp_enable(body: TotpEnableBody, request: Request, user: PlatformUser = Depends(masters.require_master_any),
                db: Session = Depends(get_platform_db)):
    _rate("platform_totp", str(user.id), per_minute=5)
    masters.totp_enable(db, user, body.pending, body.code, _ip(request))
    return {"ok": True, "totp_enrolled": True}


# =============================================================================
# /api/signup  (public)
# =============================================================================


def _signup_enabled() -> None:
    if not bool(pget("signup_enabled")) or not is_multi_tenant():
        raise PlatformError(404, "signup_disabled")


def _lookup_rate(request: Request) -> None:
    _rate("lookup", _ip(request), per_minute=int(pget("directory_rate_per_minute") or 10),
          per_hour=int(pget("directory_rate_per_hour") or 100))


@signup_router.post("", status_code=202)
def create_signup(request: Request, body: Dict[str, Any] = Body(...), db: Session = Depends(get_platform_db)):
    _signup_enabled()
    ip = _ip(request)
    _rate("signup_ip", ip, per_hour=int(pget("signup_rate_per_hour") or 5))
    _rate("signup_all", "*", per_hour=int(pget("signup_rate_overall_per_hour") or 50))
    return signup.create_request(db, body, ip)


@signup_router.get("/code-available")
def code_available(request: Request, code: str = "", db: Session = Depends(get_platform_db)):
    _signup_enabled()
    _lookup_rate(request)
    norm, available, reason = signup.code_status(db, code)
    out: Dict[str, Any] = {"code": norm or code.strip().upper()[:24], "available": available}
    if reason:
        out["reason"] = reason
        out["message"] = signup.REASON_TEXT.get(reason)
    return out


@signup_router.get("/status")
def signup_status(request: Request, request_id: str = "", token: str = "", db: Session = Depends(get_platform_db)):
    _signup_enabled()
    _lookup_rate(request)
    return signup.status_for(db, request_id, token)


class OwnerSetupBody(BaseModel):
    store_code: str
    token: str
    password: str


@signup_router.post("/owner-setup")
def owner_setup(body: OwnerSetupBody, request: Request):
    """Redeem a one-time setup link (``/#/setup?store=&token=``)."""
    if not is_multi_tenant():
        raise PlatformError(404, "not_found")
    _rate("owner_setup", _ip(request), per_hour=10)
    username = provisioning.redeem_setup_link(body.store_code, body.token, body.password, client_ip=_ip(request))
    return {"ok": True, "store_code": body.store_code.strip().upper(), "username": username,
            "login_url": provisioning.login_url(body.store_code.strip().upper())}


# =============================================================================
# /api/public/stores/{code}  (public store lookup, M2 directory shape)
# =============================================================================


@public_router.get("/stores/{code}")
def store_lookup(code: str, request: Request) -> Response:
    from app.directory.main import CACHE_200, CACHE_404, NO_STORE as DIR_NO_STORE, _if_none_match, _json
    from app.directory.stores import error_body, etag_for
    from app.platform.lookup import lookup_body

    wait = throttle.check_rate("lookup", _ip(request), int(pget("directory_rate_per_minute") or 10),
                               int(pget("directory_rate_per_hour") or 100))
    if wait is not None:
        return _json(429, error_body("rate_limited", retry_after=wait), DIR_NO_STORE, **{"Retry-After": str(wait)})
    status, body = lookup_body(code)
    if status == 400:
        return _json(400, error_body("invalid_code"), DIR_NO_STORE)
    if status == 404:
        return _json(404, error_body("unknown_store"), CACHE_404)
    etag = etag_for(body)
    if _if_none_match(request, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_200, "Access-Control-Allow-Origin": "*"})
    return _json(200, body, CACHE_200, ETag=etag)


# =============================================================================
# /api/settings/*  (store admin)
# =============================================================================


def _store_admin_context(user: User) -> str:
    """The admin's own store (from the session, never from the request)."""
    if not is_multi_tenant():
        raise PlatformError(409, "platform_mode_required")
    code = current_store_code()
    if not code:
        raise PlatformError(401, "not_logged_in")
    return code


def _refuse_demo(user: User, code: str) -> None:
    from app.demo import is_demo_user
    from app.store.settings_view import demo_mode

    if code == "DEMO" or is_demo_user(user) or demo_mode():
        raise PlatformError(403, "demo_read_only")


def _store_actor(code: str, user: User) -> str:
    return f"store:{code}:{user.username}"


@settings_router.get("/integrations")
def list_integrations(user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    return [v.as_dict() for v in creds.list_credentials(code)]


class IntegrationBody(BaseModel):
    public: Dict[str, Any] = Field(default_factory=dict)
    secrets: Dict[str, Any] = Field(default_factory=dict)
    clear: List[str] = Field(default_factory=list)


def _kind(kind: str) -> str:
    if kind not in creds.KINDS:
        raise PlatformError(404, "not_found", "Unknown integration.")
    return kind


@settings_router.get("/integrations/{kind}")
def get_integration(kind: str, user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    return creds.credential_view(code, _kind(kind)).as_dict()


@settings_router.put("/integrations/{kind}")
def put_integration(kind: str, body: IntegrationBody, request: Request, user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    _refuse_demo(user, code)
    kind = _kind(kind)
    if kind == "clover":
        raise PlatformError(501, "not_available")
    _rate("integrations_write", code, per_minute=30)
    try:
        view = creds.save_credential(code, kind, body.public, body.secrets, clear=body.clear,
                                     actor=_store_actor(code, user), client_ip=_ip(request))
    except creds.CredentialError as exc:
        raise PlatformError(400, "invalid", str(exc)) from None
    return view.as_dict()


@settings_router.post("/integrations/{kind}/test")
def test_integration(kind: str, request: Request, body: Optional[IntegrationBody] = Body(default=None),
                     user: User = Depends(require_admin)):
    """Tests the stored values, or unsaved values in the body (blank secret
    fields fall back to the stored ones). Records the result when it tested
    what is stored. 5 per minute per store."""
    code = _store_admin_context(user)
    _refuse_demo(user, code)
    kind = _kind(kind)
    _rate("integrations_test", code, per_minute=int(pget("integration_test_rate_per_minute") or 5))
    stored_public, stored_secrets = creds.stored_values(code, kind)
    unsaved = body is not None and (body.public or body.secrets)
    try:
        public = creds.clean_public(kind, body.public, stored_public) if unsaved else stored_public
        new_secrets = creds.clean_secrets(kind, body.secrets) if unsaved else {}
    except creds.CredentialError as exc:
        raise PlatformError(400, "invalid", str(exc)) from None
    secrets_now = {**stored_secrets, **new_secrets}
    result = run_test(kind, public, secrets_now)
    creds.record_test_result(code, kind, result.ok, result.message, actor=_store_actor(code, user),
                             client_ip=_ip(request), saved=not unsaved)
    return {"kind": kind, "ok": result.ok, "message": result.message, "tested": "unsaved" if unsaved else "saved"}


@settings_router.delete("/integrations/{kind}")
def delete_integration(kind: str, request: Request, user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    _refuse_demo(user, code)
    kind = _kind(kind)
    _rate("integrations_write", code, per_minute=30)
    deleted = creds.delete_credential(code, kind, actor=_store_actor(code, user), client_ip=_ip(request))
    return {"kind": kind, "deleted": deleted}


@settings_router.post("/integrations/clover/connect")
def clover_connect(user: User = Depends(require_admin)):
    _store_admin_context(user)
    # M3 replaces this with the Clover OAuth v2 redirect (authorize_url) and
    # GET /api/settings/integrations/clover/callback (contract 9.3).
    raise PlatformError(501, "not_available")


# Store admin: the whitelisted, non-secret store settings (contract 13.5 "should").
STORE_ADMIN_SETTING_KEYS = (
    "store_name", "store_short_name", "store_address", "store_phone", "store_email", "brand_color",
    "brand_secondary_color", "logo_url", "icon_url", "ai_enabled", "labels_url", "email_from_name",
)


@settings_router.get("/store")
def get_store_settings_admin(user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    from app.platform.sources import store_settings_dict

    values = store_settings_dict(code)
    return {"store_code": code, "settings": {k: values.get(k) for k in STORE_ADMIN_SETTING_KEYS}}


@settings_router.patch("/store")
def patch_store_settings_admin(request: Request, body: Dict[str, Any] = Body(...), user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    _refuse_demo(user, code)
    _rate("store_settings_write", code, per_minute=30)
    bad = sorted(k for k in body if k not in STORE_ADMIN_SETTING_KEYS)
    if bad:
        raise PlatformError(400, "invalid", f"Not editable here: {', '.join(bad)}", fields=bad)
    with platform_session() as db:
        values = stores.put_settings(db, code, body, actor=_store_actor(code, user), actor_type="store_user",
                                     client_ip=_ip(request))
    return {"store_code": code, "settings": {k: values.get(k) for k in STORE_ADMIN_SETTING_KEYS}}


@settings_router.post("/hosting/request")
def store_hosting_request(body: HostingRequestBody, request: Request, user: User = Depends(require_admin)):
    """A store admin asks for a dedicated server; a master decides."""
    code = _store_admin_context(user)
    _refuse_demo(user, code)
    _rate("hosting_request", code, per_hour=5)
    with platform_session() as db:
        return hosting.create_request(db, code, actor=_store_actor(code, user), actor_type="store_user",
                                      reason=body.reason, target_api_base_url=body.target_api_base_url,
                                      client_ip=_ip(request))


@settings_router.get("/hosting")
def store_hosting_status(user: User = Depends(require_admin)):
    code = _store_admin_context(user)
    with platform_session() as db:
        store = stores.store_by_code(db, code)
        return {
            "enabled": dedicated_enabled(),
            "hosting": store.hosting,
            "api_base_url": store.api_base_url,
            "requests": hosting.list_requests(db, None, code)[:5],
        }


# =============================================================================


def _audit_tenancy_event(action: str, store_code: Optional[str], details: Dict[str, Any]) -> None:
    from app.platform.db import is_configured

    if is_configured():
        audit(action, actor_type="system", store_code=store_code, details=details)


def _subscribe_tenancy_events() -> None:
    """Track B's events (e.g. store.identity_mismatch) become audit rows."""
    try:
        from app.tenancy import events
    except ImportError:
        return
    events.add_listener(_audit_tenancy_event)


def install(app: FastAPI) -> None:
    """Routers + the JSON error handler. In multi mode also installs the
    platform registry/settings sources into app.tenancy."""
    app.add_exception_handler(PlatformError, platform_error_handler)
    _subscribe_tenancy_events()
    app.include_router(platform_router)
    app.include_router(signup_router)
    app.include_router(public_router)
    app.include_router(settings_router)
    from app.platform.config import is_multi

    if is_multi():
        from app.platform.sources import configure
        from app.platform.startup import startup_checks

        startup_checks()
        configure()
