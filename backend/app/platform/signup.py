"""Store sign-up requests (contract 3.2, 13.3; owner C).

A new store asks; a master approves (``provisioning.approve_signup``) or
rejects. The owner's password is bcrypt-hashed at once and only the hash is
kept; the requester gets a one-time status token (only its hash is kept) to
poll ``/api/signup/status``.
"""

import hashlib
import re
import secrets
import zoneinfo
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.platform.audit import audit
from app.platform.codes import POS_PROVIDERS, db_name_for, is_reserved, normalize_code
from app.platform.errors import PlatformError
from app.platform.models import SignupRequest, Store, utcnow

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
OWNER_PASSWORD_MIN = 10


def signup_open() -> bool:
    """Whether the public sign-up takes new requests: the console's platform
    setting (website ready A3) > SIGNUP_ENABLED > on. Read on every request,
    so the console switch works without a restart."""
    from app.platform.platform_settings import SIGNUP_ENABLED, get_platform_setting

    return bool(get_platform_setting(SIGNUP_ENABLED))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _text(value: Any, name: str, lo: int, hi: int, required: bool = True) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise PlatformError(400, "invalid", f"{name} is required.", field=name)
        return None
    if not isinstance(value, str):
        raise PlatformError(400, "invalid", f"{name} must be text.", field=name)
    v = " ".join(value.split()) if "\n" not in value else value.strip()
    if not lo <= len(v) <= hi:
        raise PlatformError(400, "invalid", f"{name} must be {lo}-{hi} characters.", field=name)
    if any(ord(c) < 32 and c not in "\n\t" for c in v):
        raise PlatformError(400, "invalid", f"{name} has characters that are not allowed.", field=name)
    return v


def code_status(db: Session, raw: Any) -> Tuple[Optional[str], bool, Optional[str]]:
    """(normalised code or None, available, reason)."""
    code = normalize_code(raw)
    if code is None:
        return None, False, "invalid"
    if is_reserved(code):
        return code, False, "reserved"
    if db.execute(select(Store.id).where(Store.code == code)).first() is not None:
        return code, False, "taken"
    try:
        dbn = db_name_for(code)
    except ValueError:
        return code, False, "invalid"
    if db.execute(select(Store.id).where(Store.db_name == dbn)).first() is not None:
        return code, False, "taken"
    pending = db.execute(
        select(SignupRequest.id).where(SignupRequest.requested_code == code, SignupRequest.status == "pending")
    ).first()
    if pending is not None:
        return code, False, "requested"
    return code, True, None


REASON_TEXT = {
    "invalid": "Codes are 3 to 24 letters, numbers or underscores.",
    "reserved": "That code is reserved.",
    "taken": "That code is already used by another store.",
    "requested": "Someone has already asked for that code.",
}


def validate(body: Dict[str, Any]) -> Dict[str, Any]:
    """The sign-up body (contract 13.3) -> clean values. Raises PlatformError 400."""
    if body.get("website"):
        # Honeypot: a real person never fills the hidden field.
        raise PlatformError(400, "invalid", "Some values are not valid.", field="website")
    if body.get("accept_terms") is not True:
        raise PlatformError(400, "invalid", "Please accept the terms.", field="accept_terms")
    out: Dict[str, Any] = {}
    out["store_name"] = _text(body.get("store_name"), "store_name", 2, 80)
    out["contact_name"] = _text(body.get("contact_name"), "contact_name", 2, 80)
    email = _text(body.get("contact_email"), "contact_email", 5, 200)
    if not EMAIL_RE.match(email or ""):
        raise PlatformError(400, "invalid", "That email address doesn't look right.", field="contact_email")
    out["contact_email"] = email.lower()
    out["contact_phone"] = _text(body.get("contact_phone"), "contact_phone", 5, 40, required=False)
    out["address"] = _text(body.get("address"), "address", 3, 200, required=False)
    out["notes"] = _text(body.get("notes"), "notes", 1, 2000, required=False)
    tz = _text(body.get("timezone"), "timezone", 3, 64)
    try:
        zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise PlatformError(400, "invalid", "Pick a time zone from the list.", field="timezone") from None
    out["timezone"] = tz
    pos = str(body.get("pos_provider") or "").strip().lower()
    if pos not in POS_PROVIDERS:
        raise PlatformError(400, "invalid", "Pick Toast, Clover or CSV.", field="pos_provider")
    out["pos_provider"] = pos
    uname = str(body.get("owner_username") or "").strip()
    if not USERNAME_RE.match(uname):
        raise PlatformError(
            400, "invalid", "Usernames are 3-64 letters, digits, dot, dash or underscore.", field="owner_username"
        )
    out["owner_username"] = uname
    pw = body.get("owner_password")
    if not isinstance(pw, str) or len(pw) < OWNER_PASSWORD_MIN:
        raise PlatformError(400, "invalid", f"The password needs at least {OWNER_PASSWORD_MIN} characters.", field="owner_password")
    if len(pw) > 256:
        raise PlatformError(400, "invalid", "That password is too long.", field="owner_password")
    if pw.strip().lower() in (uname.lower(), (out["store_name"] or "").lower()):
        raise PlatformError(400, "invalid", "Pick a password that is not your username or store name.", field="owner_password")
    out["owner_password"] = pw
    code = normalize_code(body.get("requested_code"))
    if code is None:
        raise PlatformError(400, "invalid_code", field="requested_code")
    out["requested_code"] = code
    return out


def create_request(db: Session, body: Dict[str, Any], client_ip: Optional[str]) -> Dict[str, Any]:
    clean = validate(body)
    code, available, reason = code_status(db, clean["requested_code"])
    if not available:
        raise PlatformError(409, "code_unavailable", REASON_TEXT.get(reason or "", "That code is not available."), reason=reason)
    token = secrets.token_urlsafe(24)
    req = SignupRequest(
        requested_code=code,
        store_name=clean["store_name"],
        contact_name=clean["contact_name"],
        contact_email=clean["contact_email"],
        contact_phone=clean["contact_phone"],
        address=clean["address"],
        timezone=clean["timezone"],
        pos_provider=clean["pos_provider"],
        owner_username=clean["owner_username"],
        owner_password_hash=hash_password(clean["owner_password"]),
        notes=clean["notes"],
        status="pending",
        status_token_hash=_hash(token),
        client_ip=client_ip,
        created_at=utcnow(),
    )
    db.add(req)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise PlatformError(409, "code_unavailable", REASON_TEXT["requested"], reason="requested") from None
    audit(
        "signup.created", actor_type="signup", actor_id=req.id, actor_name=clean["contact_email"],
        store_code=code, target=f"signup:{req.id}",
        details={"store_name": clean["store_name"], "pos_provider": clean["pos_provider"], "timezone": clean["timezone"]},
        client_ip=client_ip, db=db,
    )
    db.commit()
    return {"request_id": req.id, "status_token": token, "status": "pending"}


def status_for(db: Session, request_id: Any, token: Any) -> Dict[str, Any]:
    """The pending page's poll. Wrong id or token -> 404 (no enumeration)."""
    try:
        rid = int(request_id)
    except (TypeError, ValueError):
        raise PlatformError(404, "not_found") from None
    req = db.get(SignupRequest, rid)
    if req is None or not isinstance(token, str) or not secrets.compare_digest(req.status_token_hash, _hash(token)):
        raise PlatformError(404, "not_found")
    out: Dict[str, Any] = {"status": req.status}
    if req.status == "approved":
        store = db.get(Store, req.store_id) if req.store_id else None
        out["store_code"] = store.code if store else req.requested_code
        # Approved but still being set up -> the page keeps polling.
        out["store_status"] = store.status if store else "provisioning"
        if store is not None and store.status == "active":
            out["message"] = f"Your store is ready. Sign in with Store ID {store.code}."
        else:
            out["message"] = "Approved. Your store is being set up; this takes a minute."
    elif req.status == "rejected":
        out["message"] = req.review_note or "This request was not approved."
    elif req.status == "pending":
        out["message"] = "Waiting for approval."
    return out


def request_view(req: SignupRequest) -> Dict[str, Any]:
    """For masters: everything but the password hash and the status token."""
    return {
        "id": req.id,
        "requested_code": req.requested_code,
        "store_name": req.store_name,
        "contact_name": req.contact_name,
        "contact_email": req.contact_email,
        "contact_phone": req.contact_phone,
        "address": req.address,
        "timezone": req.timezone,
        "pos_provider": req.pos_provider,
        "owner_username": req.owner_username,
        "notes": req.notes,
        "status": req.status,
        "reviewed_by": req.reviewed_by,
        "reviewed_at": req.reviewed_at.isoformat() if req.reviewed_at else None,
        "review_note": req.review_note,
        "store_id": req.store_id,
        "client_ip": req.client_ip,
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }


def reject(db: Session, request_id: int, *, actor: str, note: Optional[str], client_ip: Optional[str] = None) -> SignupRequest:
    req = db.get(SignupRequest, request_id)
    if req is None:
        raise PlatformError(404, "not_found")
    if req.status != "pending":
        raise PlatformError(409, "conflict", f"This request is already {req.status}.")
    req.status = "rejected"
    req.reviewed_by = actor
    req.reviewed_at = utcnow()
    req.review_note = (note or "").strip()[:1000] or None
    audit(
        "signup.rejected", actor_type="platform_user", actor_name=actor, store_code=req.requested_code,
        target=f"signup:{req.id}", details={"note": req.review_note}, client_ip=client_ip, db=db,
    )
    db.commit()
    return req

