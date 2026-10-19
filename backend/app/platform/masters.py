"""Master admins: accounts, platform sessions, TOTP, support sessions
(contract 3.3, 4.2, 4.5 step 3, 13.1; owner C).

Masters are never rows in a store database and store users are never rows
here. A platform session token is ``"<PLATFORM_LOGIN_CODE>.<secret>"``; only
its sha256 is stored (``platform_sessions.token_hash``).
"""

import datetime as dt
import hashlib
import secrets
from typing import Any, Dict, Optional, Tuple

import segno
from fastapi import Cookie, Depends, Header, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE_NAME, extract_bearer_token, hash_password, verify_password
from app.platform import crypto, totp
from app.platform.audit import audit
from app.platform.config import login_code, pget
from app.platform.errors import PlatformError
from app.platform.models import PlatformSession, PlatformUser, SupportSession, utcnow

USERNAME_MIN, USERNAME_MAX = 3, 64
PASSWORD_MIN = 12
# bcrypt of a random value: unknown users still pay one bcrypt check, so the
# reply time does not tell whether a username exists.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    return token.split(".", 1)[0].upper() or None


def make_platform_token() -> str:
    return f"{login_code()}.{secrets.token_urlsafe(32)}"


def dummy_verify(password: str) -> None:
    verify_password(password or "", _DUMMY_HASH)


def _clean_username(raw: str) -> str:
    u = (raw or "").strip().lower()
    if not (USERNAME_MIN <= len(u) <= USERNAME_MAX) or not all(c.isalnum() or c in "._-@" for c in u):
        raise PlatformError(400, "invalid", f"Usernames are {USERNAME_MIN}-{USERNAME_MAX} letters, digits, . _ - @.")
    return u


def check_password_rules(password: str, minimum: int = PASSWORD_MIN) -> None:
    if not isinstance(password, str) or len(password) < minimum:
        raise PlatformError(400, "invalid", f"Passwords need at least {minimum} characters.")
    if len(password) > 256:
        raise PlatformError(400, "invalid", "That password is too long.")


def user_view(user: PlatformUser) -> Dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role,
        "is_active": bool(user.is_active),
        "totp_enrolled": bool(user.totp_secret_enc),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def create_master(
    db: Session,
    username: str,
    password: str,
    display_name: str = "",
    email: Optional[str] = None,
    *,
    actor: str,
    actor_type: str = "platform_user",
    client_ip: Optional[str] = None,
) -> PlatformUser:
    uname = _clean_username(username)
    check_password_rules(password)
    if db.execute(select(PlatformUser).where(func.lower(PlatformUser.username) == uname)).scalar_one_or_none():
        raise PlatformError(409, "conflict", "That username is already taken.")
    user = PlatformUser(
        username=uname,
        password_hash=hash_password(password),
        display_name=(display_name or uname).strip()[:120],
        email=(email or None) and email.strip().lower()[:200],
        role="master",
        is_active=True,
        created_at=utcnow(),
    )
    db.add(user)
    db.flush()
    audit(
        "platform_user.created", actor_type=actor_type, actor_name=actor, target=uname,
        details={"role": "master"}, client_ip=client_ip, db=db,
    )
    db.commit()
    return user


def find_master(db: Session, username: str) -> Optional[PlatformUser]:
    return db.execute(
        select(PlatformUser).where(func.lower(PlatformUser.username) == (username or "").strip().lower())
    ).scalar_one_or_none()


def authenticate(db: Session, username: str, password: str, totp_code: Optional[str]) -> Tuple[Optional[PlatformUser], Optional[str]]:
    """(user, None) on success; (None, reason) otherwise. Always one bcrypt."""
    user = find_master(db, username)
    if user is None or not user.is_active:
        dummy_verify(password)
        return None, "bad_login"
    if not verify_password(password or "", user.password_hash):
        return None, "bad_login"
    if user.totp_secret_enc:
        try:
            secret = crypto.open_text(user.totp_secret_enc, crypto.totp_aad(user.username))
        except crypto.DecryptError:
            return None, "bad_login"
        if not totp_code:
            return None, "totp_required"
        if not totp.verify(secret, totp_code):
            return None, "bad_login"
    return user, None


def create_platform_session(db: Session, user: PlatformUser, client_ip: Optional[str]) -> Tuple[str, dt.datetime]:
    token = make_platform_token()
    expires = utcnow() + dt.timedelta(hours=int(pget("platform_session_hours") or 12))
    db.add(PlatformSession(token_hash=token_hash(token), platform_user_id=user.id, created_at=utcnow(), expires_at=expires, client_ip=client_ip))
    user.last_login_at = utcnow()
    # Housekeeping: this user's expired sessions.
    db.execute(delete(PlatformSession).where(PlatformSession.platform_user_id == user.id, PlatformSession.expires_at < utcnow()))
    return token, expires


def delete_platform_session(db: Session, token: str) -> None:
    db.execute(delete(PlatformSession).where(PlatformSession.token_hash == token_hash(token)))
    db.commit()


def session_user(db: Session, token: Optional[str]) -> Optional[PlatformUser]:
    if not token or token_prefix(token) != login_code():
        return None
    row = db.get(PlatformSession, token_hash(token))
    if row is None or row.expires_at < utcnow():
        return None
    user = db.get(PlatformUser, row.platform_user_id)
    if user is None or not user.is_active:
        return None
    return user


def request_token(session_token: Optional[str], authorization: Optional[str]) -> Optional[str]:
    return session_token or extract_bearer_token(authorization)


def _master(
    request: Request,
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
) -> PlatformUser:
    from app.platform.db import platform_session

    token = request_token(session_token, authorization)
    if not token:
        raise PlatformError(401, "not_logged_in")
    if token_prefix(token) != login_code():
        # A store session (or anything else) on a platform route.
        raise PlatformError(403, "master_only")
    from app.platform.db import is_configured

    if not is_configured():
        raise PlatformError(404, "not_found", "Not available on this server.")
    with platform_session() as db:
        user = session_user(db, token)
    if user is None:
        raise PlatformError(401, "not_logged_in", "Session expired or invalid.")
    request.state.platform_user = user
    return user


def require_master_any(user: PlatformUser = Depends(_master)) -> PlatformUser:
    """A signed-in master, TOTP set up or not (the TOTP set-up routes)."""
    return user


def require_master(user: PlatformUser = Depends(_master)) -> PlatformUser:
    if bool(pget("platform_require_totp")) and not user.totp_secret_enc:
        raise PlatformError(403, "totp_setup_required")
    return user


def actor_name(user: PlatformUser) -> str:
    return f"platform:{user.username}"


# --- TOTP enrolment ------------------------------------------------------------

_PENDING_SUFFIX = ":pending"


def totp_begin(db: Session, user: PlatformUser) -> Dict[str, str]:
    """A new secret, shown once; becomes active only after ``totp_enable``."""
    secret = totp.new_secret()
    # Stateless enrolment: the new secret goes back to the client sealed
    # (bound to this username) and is stored only when a code confirms it.
    pending = crypto.seal_text(secret + _PENDING_SUFFIX, crypto.totp_aad(user.username))
    uri = totp.otpauth_uri(secret, user.username, str(pget("product_name") or "StoreOps"))
    # The same URI as a scannable QR code, drawn here so the website needs no
    # QR package: an SVG data URI it can put straight into an <img>.
    qr = segno.make(uri, error="m").svg_data_uri(scale=6, border=2, dark="#111827", light="#ffffff")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr, "pending": pending}


def totp_enable(db: Session, user: PlatformUser, pending: str, code: str, client_ip: Optional[str]) -> None:
    try:
        raw = crypto.open_text(pending, crypto.totp_aad(user.username))
    except crypto.DecryptError:
        raise PlatformError(400, "invalid", "That set-up has expired. Start again.") from None
    if not raw.endswith(_PENDING_SUFFIX):
        raise PlatformError(400, "invalid", "That set-up has expired. Start again.")
    secret = raw[: -len(_PENDING_SUFFIX)]
    if not totp.verify(secret, code):
        raise PlatformError(400, "invalid", "That code is not right. Check the time on your phone and try again.")
    db_user = db.get(PlatformUser, user.id)
    db_user.totp_secret_enc = crypto.seal_text(secret, crypto.totp_aad(user.username))
    audit("platform_user.totp_enabled", actor_type="platform_user", actor_id=user.id, actor_name=actor_name(user), target=user.username, client_ip=client_ip, db=db)
    db.commit()


# --- support sessions (impersonate for support) ---------------------------------


def _store_session_key(token: str) -> str:
    """How ``user_sessions.token`` holds a store token: hashed once track B's
    ``app.auth.token_hash`` is in (contract 4.2), raw before that."""
    import app.auth as store_auth

    fn = getattr(store_auth, "token_hash", None)
    return fn(token) if fn is not None else token


def support_minutes(requested: Optional[int]) -> int:
    default = int(pget("platform_support_minutes") or 30)
    if requested is None:
        return default
    return max(5, min(int(requested), 120))


def start_support_session(
    master: PlatformUser,
    store_code: str,
    *,
    reason: str,
    username: Optional[str] = None,
    minutes: Optional[int] = None,
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """A time-limited store session as one of the store's users (default:
    its first admin) for a support case; audited on both ends."""
    from app.models import User, UserSession
    from app.platform.bridge import tenant_session
    from app.platform.db import platform_session

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise PlatformError(400, "invalid", "Say why (at least 5 characters): it goes in the audit log.")
    mins = support_minutes(minutes)
    expires = utcnow() + dt.timedelta(minutes=mins)
    from app.auth import create_session

    with tenant_session(store_code) as sdb:
        q = select(User)
        if username:
            q = q.where(func.lower(User.username) == username.strip().lower())
        else:
            q = q.where(User.is_admin.is_(True)).order_by(User.id)
        target = sdb.execute(q.limit(1)).scalar_one_or_none()
        if target is None:
            raise PlatformError(404, "not_found", "No such user in that store.")
        token, _ = create_session(sdb, target.id)
        row = sdb.get(UserSession, _store_session_key(token))
        if row is not None:
            row.expires_at = expires
            sdb.commit()
        target_name = target.username
    with platform_session() as pdb:
        pdb.add(
            SupportSession(
                platform_user_id=master.id,
                store_code=store_code,
                store_username=target_name,
                token_hash=token_hash(token),
                reason=reason[:500],
                started_at=utcnow(),
                expires_at=expires,
            )
        )
        audit(
            "store.support_session_started", actor_type="platform_user", actor_id=master.id,
            actor_name=actor_name(master), store_code=store_code, target=target_name,
            details={"minutes": mins, "reason": reason[:500]}, client_ip=client_ip, db=pdb,
        )
        pdb.commit()
    return {"token": token, "store_code": store_code, "username": target_name, "expires_at": expires.isoformat(), "minutes": mins}


def end_support_session(master: PlatformUser, session_id: int, client_ip: Optional[str] = None) -> bool:
    from app.models import UserSession
    from app.platform.bridge import tenant_session
    from app.platform.db import platform_session

    with platform_session() as pdb:
        row = pdb.get(SupportSession, session_id)
        if row is None:
            return False
        code, thash = row.store_code, row.token_hash
        if row.ended_at is None:
            row.ended_at = utcnow()
        audit(
            "store.support_session_ended", actor_type="platform_user", actor_id=master.id,
            actor_name=actor_name(master), store_code=code, target=row.store_username, client_ip=client_ip, db=pdb,
        )
        pdb.commit()
    # The store row is keyed by the token (or its hash); we only kept the
    # sha256, which is exactly the key once tokens are stored hashed.
    try:
        with tenant_session(code) as sdb:
            sdb.query(UserSession).filter(UserSession.token == thash).delete()
            sdb.commit()
    except Exception:  # noqa: BLE001 - it expires on its own anyway
        pass
    return True
