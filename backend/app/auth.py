"""Password hashing, server-side sessions, and FastAPI auth dependencies.

Sessions are cookie-based (an opaque random token, looked up against the
`user_sessions` table) rather than JWTs - simpler for this scale, and lets an
admin instantly revoke a session (e.g. when resetting someone's password)
just by deleting the row, with no token-blacklist bookkeeping needed.

There is deliberately no "forgot password" flow anywhere in this module - per
spec, only an admin can set a user's password, and any user can change their
own password by providing their current one.
"""

import datetime as dt
import secrets
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, Header, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User, UserSession
from app.timeutil import utcnow

SESSION_COOKIE_NAME = "session_token"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session(db: Session, user_id: int) -> tuple[str, dt.datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + dt.timedelta(days=get_settings().session_lifetime_days)
    db.add(UserSession(token=token, user_id=user_id, expires_at=expires_at))
    db.commit()
    return token, expires_at


def delete_session(db: Session, token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token == token))
    db.commit()


def delete_all_sessions_for_user(db: Session, user_id: int) -> None:
    """Forces re-login everywhere - used when an admin resets someone's password,
    so a stolen/stale session can't keep using the old credential's access.
    """
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    db.commit()


def set_session_cookie(response: Response, token: str, expires_at: dt.datetime) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        expires=expires_at,
        path="/",
        domain=get_settings().session_cookie_domain or None,
    )


def clear_session_cookie(response: Response) -> None:
    # Must match the domain the cookie was SET with, or the browser won't
    # recognize this as the same cookie and it'll linger until natural
    # expiry - a real logout-doesn't-actually-log-out bug if omitted once
    # session_cookie_domain is set.
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", domain=get_settings().session_cookie_domain or None)


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Pulls the token out of an `Authorization: Bearer <token>` header - the
    mobile app's auth path (see /api/auth/mobile-login), which can't rely on
    the httpOnly session cookie the web app uses. Same underlying
    `user_sessions` token either way, just delivered differently.
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def get_current_user(
    session_token: str = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    token = session_token or extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in")

    session = db.get(UserSession, token)
    if session is None or session.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
