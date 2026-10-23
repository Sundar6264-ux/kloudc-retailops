"""Label printing: the store feature, its Request card, and the sign-in check
the label site asks KloudC for (docs/LABEL_PRINTING_FEATURE.md).

Store routes (signed-in store user):
- ``GET  /api/label-printing``                 status for the Label printing card
- ``POST /api/label-printing/request``         a store admin asks for it
- ``POST /api/label-printing/request/cancel``  ... or takes the ask back
- ``POST /api/labels/handoff``                 a one-time code that opens the label site (website)

For the label site only:
- ``GET  /api/labels/session``                 "who is this token?" (section 2.2)
- ``POST /api/labels/handoff/redeem``          turn a hand-off code into that answer (3.4)

Tokens are never logged, never stored raw and never in a URL. The label site
proves nothing about itself: it only asks what the token's owner could ask
with ``/me``.
"""

import datetime as dt
import hashlib
import json
import logging
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Cookie, Depends, Header, Request, Response
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE_NAME, extract_bearer_token, get_current_user, lookup_session
from app.db import get_db
from app.demo import is_demo_user
from app.models import User
from app.platform import throttle
from app.platform.errors import PlatformError
from app.store import settings_view as sv

logger = logging.getLogger(__name__)

router = APIRouter(tags=["label-printing"])

NO_STORE = {"Cache-Control": "no-store"}
CACHE_SECONDS = 60
HANDOFF_SECONDS = 60
SESSION_PER_TOKEN_PER_MINUTE = 120
SESSION_PER_STORE_PER_MINUTE = 3000
HANDOFF_PER_USER_PER_MINUTE = 20
REDEEM_PER_MINUTE = 300
REQUEST_PER_STORE_PER_HOUR = 5

MSG_OFF = "Label printing is turned off for this store."
MSG_OFF_ASK = "Label printing isn't on for this store yet. Ask for it under Label printing."
MSG_SHARED_ONLY = "Label printing requests are handled on the shared service."


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rate(name: str, key: str, per_minute: int = 0, per_hour: int = 0) -> None:
    wait = throttle.check_rate(name, key, per_minute, per_hour)
    if wait is not None:
        raise PlatformError(429, "rate_limited", headers={"Retry-After": str(wait), **NO_STORE}, retry_after=wait)


def _multi() -> bool:
    from app.tenancy.mode import is_multi_tenant

    return is_multi_tenant()


def _store_code() -> str:
    from app.tenancy.context import current_store_code

    return (current_store_code() or sv.store_code() or "").upper()


def _iso_z(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fresh_values(code: str) -> Optional[Dict[str, Any]]:
    """The store's stored settings straight from the platform (multi mode),
    so the card shows a master's change at once; None in single mode."""
    if not _multi() or not code:
        return None
    from app.platform.sources import store_settings_dict

    return store_settings_dict(code)


def _state_now(code: str) -> Dict[str, Any]:
    values = _fresh_values(code)
    if values is None:
        s = None
        state = sv.label_printing_state(s)
        return {"state": state, "url": sv.labels_url(s) or None, "enabled": sv.has_store_feature("labels", s)}
    from app.platform.feature_requests import label_printing_state_of

    state = label_printing_state_of(values)
    url = (values.get("labels_url") or "").strip().rstrip("/") or None
    return {"state": state, "url": url, "enabled": state == "on"}


def session_body(user: User, expires_at: Optional[dt.datetime], s=None) -> Dict[str, Any]:
    """The answer of GET /api/labels/session (contract 2.2), also what a
    hand-off code is turned into."""
    demo = is_demo_user(user)
    return {
        "store_code": _store_code(),
        "user": {"id": user.id, "username": user.username, "display_name": getattr(user, "display_name", None) or user.username},
        "is_admin": bool(user.is_admin),
        "demo": demo,
        "can_print": not demo,
        "label_printing": {"enabled": sv.has_store_feature("labels", s), "options": sv.label_printing_options(s)},
        "session_expires_at": _iso_z(expires_at),
        "cache_seconds": CACHE_SECONDS,
    }


# --- the store's Label printing card ---------------------------------------------------


@router.get("/api/label-printing")
def label_printing_status(user: User = Depends(get_current_user)):
    code = _store_code()
    now = _state_now(code)
    request_view = None
    if _multi() and code:
        from app.platform.db import platform_session
        from app.platform.feature_requests import LABEL_PRINTING, latest_request, store_view
        from app.platform.stores import store_by_code

        with platform_session() as db:
            store = store_by_code(db, code)
            request_view = store_view(latest_request(db, store.id, LABEL_PRINTING))
    can_request = bool(user.is_admin) and not is_demo_user(user) and not sv.demo_mode() and now["state"] != "on" and _multi()
    return Response(
        content=json.dumps(
            {
                "enabled": now["enabled"],
                "state": now["state"],
                "url": now["url"],
                "can_request": can_request,
                "request": request_view,
            }
        ),
        media_type="application/json",
        headers=NO_STORE,
    )


def _store_admin(user: User) -> str:
    if not user.is_admin:
        raise PlatformError(403, "admin_only")
    if not _multi():
        raise PlatformError(409, "platform_mode_required", MSG_SHARED_ONLY)
    code = _store_code()
    if not code:
        raise PlatformError(401, "not_logged_in")
    if code == "DEMO" or is_demo_user(user) or sv.demo_mode():
        from app.platform.errors import MESSAGES

        raise PlatformError(403, "demo_read_only", detail=MESSAGES["demo_read_only"])
    return code


def _actor(code: str, user: User) -> str:
    return f"store:{code}:{user.username}"


@router.post("/api/label-printing/request")
def request_label_printing(request: Request, body: Optional[Dict[str, Any]] = Body(default=None),
                           user: User = Depends(get_current_user)):
    code = _store_admin(user)
    from app.platform import feature_requests
    from app.platform.db import platform_session

    note = (body or {}).get("note") if isinstance(body, dict) else None
    _rate("label_printing_request", code, per_hour=REQUEST_PER_STORE_PER_HOUR)
    with platform_session() as db:
        out = feature_requests.create(db, code, actor=_actor(code, user), note=note, client_ip=throttle.client_ip(request))
    return Response(content=json.dumps(out), media_type="application/json", headers=NO_STORE)


@router.post("/api/label-printing/request/cancel")
def cancel_label_printing_request(request: Request, user: User = Depends(get_current_user)):
    code = _store_admin(user)
    from app.platform import feature_requests
    from app.platform.db import platform_session

    with platform_session() as db:
        out = feature_requests.cancel(db, code, actor=_actor(code, user), client_ip=throttle.client_ip(request))
    return Response(content=json.dumps(out), media_type="application/json", headers=NO_STORE)


# --- the label site's sign-in check (contract 2.2) -------------------------------------


def _request_token(session_token: Optional[str], authorization: Optional[str]) -> Optional[str]:
    # The same order as get_current_user: the cookie, else the Bearer header.
    return session_token or extract_bearer_token(authorization)


def _session_rate(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Runs before the session check: 120 a minute per token (key: its
    sha256). The 3000-a-minute store limit is counted in the route, after the
    token proved to be a real session: counted here, made-up ``CODE.<junk>``
    tokens from anyone could use it up and stop every label site sign-in
    check for that store (security check, 2026-10-22)."""
    token = _request_token(session_token, authorization)
    if not token:
        return
    _rate("labels_session_token", _sha(token), per_minute=SESSION_PER_TOKEN_PER_MINUTE)


@router.get("/api/labels/session", dependencies=[Depends(_session_rate)])
def labels_session(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _rate("labels_session_store", _store_code() or "-", per_minute=SESSION_PER_STORE_PER_MINUTE)
    if not sv.has_store_feature("labels"):
        raise PlatformError(403, "label_printing_off", MSG_OFF, headers=NO_STORE)
    row = lookup_session(db, _request_token(session_token, authorization))
    body = session_body(user, row.expires_at if row is not None else None)
    return Response(content=json.dumps(body), media_type="application/json", headers=NO_STORE)


# --- website hand-off (contract 3.4) ----------------------------------------------------


@router.post("/api/labels/handoff")
def labels_handoff(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _multi():
        raise PlatformError(409, "platform_mode_required", MSG_SHARED_ONLY)
    code = _store_code()
    _rate("labels_handoff", f"{code}:{user.id}", per_minute=HANDOFF_PER_USER_PER_MINUTE)
    url = sv.labels_url()
    if not (sv.has_store_feature("labels") and url):
        raise PlatformError(403, "label_printing_off", MSG_OFF, headers=NO_STORE)
    row = lookup_session(db, _request_token(session_token, authorization))
    identity = session_body(user, row.expires_at if row is not None else None)
    secret = secrets.token_urlsafe(32)
    from app.platform.db import platform_session
    from app.platform.models import LabelHandoff, utcnow

    now = utcnow()
    with platform_session() as pdb:
        pdb.add(
            LabelHandoff(
                code_hash=_sha(secret),
                store_code=code,
                identity=json.dumps(identity, separators=(",", ":")),
                created_at=now,
                expires_at=now + dt.timedelta(seconds=HANDOFF_SECONDS),
            )
        )
        pdb.commit()
    out = {"url": f"{url}/kloudc/enter?code={secret}", "expires_in": HANDOFF_SECONDS}
    return Response(content=json.dumps(out), media_type="application/json", headers=NO_STORE)


@router.post("/api/labels/handoff/redeem")
def labels_handoff_redeem(
    request: Request,
    body: Optional[Dict[str, Any]] = Body(default=None),
    x_store: Optional[str] = Header(default=None, alias="X-Store"),
):
    """The label site (no session) turns a code into the 2.2 answer. Single
    use, 60 seconds, only for the store named in X-Store.

    The limit is per caller address (throttle.client_ip): one shared bucket
    let anyone on the internet use it up with junk codes and stop every
    store's website hand-off (security check, 2026-10-22). Codes are 32
    random bytes, so guessing is not the risk."""
    _rate("labels_handoff_redeem", throttle.client_ip(request), per_minute=REDEEM_PER_MINUTE)
    if not _multi():
        raise PlatformError(409, "platform_mode_required", MSG_SHARED_ONLY)
    secret = (body or {}).get("code") if isinstance(body, dict) else None
    store = (x_store or "").strip().upper()
    if not isinstance(secret, str) or not secret.strip() or len(secret) > 200 or not store:
        raise PlatformError(404, "code_unknown", "That link isn't valid. Open label printing again.", headers=NO_STORE)
    from app.platform.db import platform_session
    from app.platform.models import LabelHandoff, utcnow

    digest = _sha(secret.strip())
    now = utcnow()
    with platform_session() as pdb:
        row = pdb.get(LabelHandoff, digest)
        if row is None or row.store_code != store:
            raise PlatformError(404, "code_unknown", "That link isn't valid. Open label printing again.", headers=NO_STORE)
        done = pdb.execute(
            update(LabelHandoff)
            .where(LabelHandoff.code_hash == digest, LabelHandoff.used_at.is_(None), LabelHandoff.expires_at > now)
            .values(used_at=now)
        ).rowcount
        pdb.commit()
        if done != 1:
            raise PlatformError(410, "code_used", "That link was already used or has run out. Open label printing again.",
                                headers=NO_STORE)
        identity = json.loads(row.identity)
    from app.tenancy.settings import store_settings_for

    try:
        s = store_settings_for(store)
    except Exception:  # noqa: BLE001 - store gone or paused since the code was made
        s = None
    if s is None or not sv.has_store_feature("labels", s):
        raise PlatformError(403, "label_printing_off", MSG_OFF, headers=NO_STORE)
    identity["label_printing"] = {"enabled": True, "options": sv.label_printing_options(s)}
    return Response(content=json.dumps(identity), media_type="application/json", headers=NO_STORE)
