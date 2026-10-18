import json
from typing import List, Optional, Tuple

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import (
    clear_session_cookie,
    create_session,
    delete_all_sessions_for_user,
    delete_session,
    extract_bearer_token,
    get_current_user,
    hash_password,
    require_admin,
    set_session_cookie,
    verify_password,
    SESSION_COOKIE_NAME,
)
from app.db import get_db
from app.demo import DEMO_CANT_DELETE, is_demo_user
from app.features import FEATURE_KEYS, FEATURES, get_allowed_features
from app.models import User, UserSession
from app.pos.gating import capability_values, features_for_store, pos_name
from app.sales_access import effective_features
from app.services.notifications import delete_user_notification_data
from app.team.bootstrap import delete_user_team_data

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    is_admin: bool = False
    # Ignored when is_admin=True (an admin always has full access) - for a
    # non-admin, defaults to no access at all until the admin creating the
    # account explicitly grants some.
    allowed_features: List[str] = []
    # Keep sales numbers out of this person's web/app (app/sales_access.py).
    # Ignored when is_admin=True - admins always see sales.
    hide_sales: bool = False


class SetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class SetCanAssignRequest(BaseModel):
    can_assign: bool


class SetFeaturesRequest(BaseModel):
    allowed_features: List[str]


class SetHideSalesRequest(BaseModel):
    hide_sales: bool


class DeleteAccountRequest(BaseModel):
    password: str


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "allowed_features": get_allowed_features(user),
        # Team hub: may assign tasks to others (admins always can).
        "can_assign": bool(user.can_assign),
        # Admin-set: sales numbers hidden for this person (never for an
        # admin - see app/sales_access.py).
        "hide_sales": bool(user.hide_sales) and not user.is_admin,
        # Additive (build 7): an App Review demo login (app/demo.py) - the
        # app can say why sends/prints are switched off.
        "demo": is_demo_user(user),
    }


def _me_out(user: User) -> dict:
    """The signed-in user's own view: like _user_out, but allowed_features
    is the EFFECTIVE list - a hide_sales user loses the sales-only features
    (Items Sold), so every client, including installed app builds that don't
    know hide_sales yet, hides them. The admin Users list keeps the stored
    grants (_user_out) so unticking hide_sales restores them as they were.
    """
    out = _user_out(user)
    out["allowed_features"] = effective_features(user, out["allowed_features"])
    # Multi-store MT6 (additive): features this store's POS can't support are
    # left out too, and the POS name + capability list ride along so the
    # website and app can hide POS-only pages. For Spice Town (Toast, every
    # capability) allowed_features is unchanged.
    out["allowed_features"] = features_for_store(out["allowed_features"])
    out["pos_name"] = pos_name()
    out["capabilities"] = capability_values()
    return out


def _clean_features(keys: List[str]) -> str:
    unknown = [k for k in keys if k not in FEATURE_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown feature key(s): {', '.join(unknown)}")
    # De-dup while preserving the canonical FEATURES order, for a stable stored value.
    keep = set(keys)
    return json.dumps([f["key"] for f in FEATURES if f["key"] in keep])


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(func.lower(User.username) == body.username.strip().lower())
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    token, expires_at = create_session(db, user.id)
    set_session_cookie(response, token, expires_at)
    return _me_out(user)


@router.post("/mobile-login")
def mobile_login(body: LoginRequest, db: Session = Depends(get_db)):
    """Same credential check as /login, but for the mobile app: no cookie is
    set (a Capacitor WebView doesn't carry it reliably, and biometric re-auth
    needs an explicit value to store in Keychain/Keystore anyway) - the raw
    session token is returned in the body instead, sent back by the client as
    `Authorization: Bearer <token>` on every request. Deliberately a separate
    endpoint rather than adding `token` to /login's response: doing that
    there would hand any web-page XSS a readable-by-JS token where today only
    an httpOnly cookie exists, a real regression to the web app's security
    model for no benefit to it.
    """
    user = db.execute(
        select(User).where(func.lower(User.username) == body.username.strip().lower())
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    token, expires_at = create_session(db, user.id)
    return {"token": token, "expires_at": expires_at.isoformat(), **_me_out(user)}


@router.post("/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
):
    token = session_token or extract_bearer_token(authorization)
    if token:
        delete_session(db, token)
    clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _me_out(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    user.password_hash = hash_password(body.new_password)
    db.add(user)
    db.commit()
    return {"status": "ok"}


@router.get("/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    return [_user_out(u) for u in users]


@router.post("/users")
def create_user(
    body: CreateUserRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(User).where(func.lower(User.username) == body.username.strip().lower())
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="That username is already taken")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
        allowed_features=_clean_features(body.allowed_features),
        hide_sales=bool(body.hide_sales) and not body.is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.get("/features")
def list_features(_: User = Depends(require_admin)):
    """The full catalog of admin-grantable dashboard features, for rendering
    the permission checklist on the Users page. Admin-only, same as the rest
    of user management - a non-admin has no reason to see the full catalog,
    only their own granted subset (already returned by /me).
    """
    return {"features": FEATURES}


@router.patch("/users/{user_id}/features")
def set_user_features(
    user_id: int,
    body: SetFeaturesRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    target.allowed_features = _clean_features(body.allowed_features)
    db.add(target)
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.patch("/users/{user_id}/can-assign")
def set_user_can_assign(
    user_id: int,
    body: SetCanAssignRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Team hub: lets a non-admin (a shift lead) assign tasks to others."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    target.can_assign = body.can_assign
    db.add(target)
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.patch("/users/{user_id}/hide-sales")
def set_user_hide_sales(
    user_id: int,
    body: SetHideSalesRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Turn "Hide sales numbers" on/off for a non-admin (app/sales_access.py).
    Takes effect on that person's next request; their open page/app picks
    up the new menu on its next /me."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_admin and body.hide_sales:
        raise HTTPException(status_code=400, detail="Admins always see sales - this option is for non-admin users.")
    target.hide_sales = body.hide_sales
    db.add(target)
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.post("/users/{user_id}/set-password")
def set_user_password(
    user_id: int,
    body: SetPasswordRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    target.password_hash = hash_password(body.new_password)
    db.add(target)
    db.commit()
    # Force re-login everywhere for that account - an old session shouldn't
    # keep working once the password's been changed out from under it.
    delete_all_sessions_for_user(db, user_id)
    return {"status": "ok"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard delete, not a soft-delete/trash like Invoice Submission has -
    matches this app's existing "admin-managed accounts, no self-service"
    philosophy rather than adding a whole new recovery workflow for
    something an admin already has to deliberately confirm. Every place a
    user is referenced elsewhere (InvoiceSubmission.submitted_by_user_id,
    PurchaseLogEntry.logged_by_user_id, etc.) is a plain nullable int
    already paired with a denormalized name snapshot specifically so
    historical records keep reading correctly after this - see those
    columns' own comments.
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="You can't delete your own account")
    if target.is_admin:
        if _admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="Can't delete the last remaining admin")

    _purge_user(db, target, last_admin=(400, "Can't delete the last remaining admin"))
    return {"deleted": user_id}


LAST_ADMIN_SELF = "You are the last admin - make someone else an admin first."


def _purge_user(db: Session, target: User, last_admin: Optional[Tuple[int, str]] = None) -> None:
    """Deletes an account and everything that would keep acting for it -
    shared by the admin delete and self-service delete-account.

    The callers' "not the last admin" check runs before any write, so two
    deletions racing each other (two admins deleting themselves, or each
    other) could both pass it. Counting again inside the write - after this
    delete, before the commit - closes that: SQLite lets one writer in at a
    time, so the second one sees the first one's deletion and is refused
    with `last_admin` (status, detail) instead of leaving no admin at all."""
    user_id = target.id
    was_admin = bool(target.is_admin)
    # Immediately revokes any session that account is currently using -
    # same as set_user_password's own reasoning. (Not via
    # delete_all_sessions_for_user, which commits: everything here is one
    # transaction, so a refused deletion leaves the account fully intact.)
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    # Their phones must stop getting store alerts too.
    delete_user_notification_data(db, user_id)
    delete_user_team_data(db, user_id)
    db.delete(target)
    if was_admin and last_admin is not None:
        db.flush()
        if _admin_count(db) == 0:
            db.rollback()
            raise HTTPException(status_code=last_admin[0], detail=last_admin[1])
    db.commit()


def _admin_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(User).where(User.is_admin.is_(True))).scalar_one()


@router.post("/delete-account")
def delete_my_account(
    body: DeleteAccountRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service account deletion (App Store guideline 5.1.1(v)). Same
    clean-up as the admin delete: sessions, push tokens and alert prefs,
    team memberships, DMs and blocks go; history keeps the name snapshot.
    The App Review demo login can't delete itself (it would lock the
    reviewer out mid-review), and the last admin can't either."""
    if is_demo_user(user):
        raise HTTPException(status_code=403, detail=DEMO_CANT_DELETE)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Wrong password.")
    if user.is_admin and _admin_count(db) <= 1:
        raise HTTPException(status_code=409, detail=LAST_ADMIN_SELF)
    _purge_user(db, user, last_admin=(409, LAST_ADMIN_SELF))
    clear_session_cookie(response)
    return {"ok": True}
