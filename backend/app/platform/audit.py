"""The platform audit log (contract 3.6, 3.8; owner C).

Every platform action writes one append-only row. ``details`` is JSON and is
scrubbed: a key that looks like a secret (password, secret, token, key...)
is dropped, whatever the caller passed, so a secret can't land in the log by
mistake.
"""

import datetime as dt
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models import AuditLog, utcnow

logger = logging.getLogger(__name__)

ACTOR_TYPES = ("platform_user", "store_user", "signup", "system")
_SECRET_WORDS = ("password", "secret", "token", "api_key", "apikey", "ciphertext", "nonce", "hash", "totp")
# Keys allowed although they contain a word above (they hold names, not values).
_ALLOWED_KEYS = {"secrets_changed", "secrets_cleared", "token_kind", "has_totp", "key_id", "key_ids"}


def scrub(details: Any) -> Any:
    if isinstance(details, dict):
        out = {}
        for k, v in details.items():
            lk = str(k).lower()
            if lk not in _ALLOWED_KEYS and any(w in lk for w in _SECRET_WORDS):
                continue
            out[str(k)] = scrub(v)
        return out
    if isinstance(details, (list, tuple)):
        return [scrub(v) for v in details]
    if isinstance(details, (str, int, float, bool)) or details is None:
        return details
    return str(details)


def audit(
    action: str,
    *,
    actor_type: str,
    actor_id: Optional[Any] = None,
    actor_name: Optional[str] = None,
    store_code: Optional[str] = None,
    target: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
    db: Optional[Session] = None,
) -> None:
    """Write one row. With ``db`` the row joins the caller's transaction
    (committed with it); without, it is written and committed on its own
    session, so it survives a rollback of the caller's work."""
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unknown actor_type {actor_type!r}")
    row = AuditLog(
        at=utcnow(),
        actor_type=actor_type,
        actor_id=None if actor_id is None else str(actor_id)[:64],
        actor_name=(actor_name or None) and actor_name[:128],
        store_code=store_code,
        action=action[:64],
        target=(target or None) and str(target)[:128],
        details=json.dumps(scrub(details or {}), sort_keys=True, default=str),
        client_ip=(client_ip or None) and client_ip[:64],
        request_id=(request_id or None) and request_id[:64],
    )
    if db is not None:
        db.add(row)
        return
    from app.platform.db import platform_session

    try:
        with platform_session() as own:
            own.add(row)
            own.commit()
    except Exception:  # noqa: BLE001 - auditing must not break the action it records
        logger.exception("audit write failed: %s", action)


def audit_view(row: AuditLog) -> Dict[str, Any]:
    try:
        details = json.loads(row.details or "{}")
    except ValueError:
        details = {}
    return {
        "id": row.id,
        "at": row.at.isoformat() if row.at else None,
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "actor_name": row.actor_name,
        "store_code": row.store_code,
        "action": row.action,
        "target": row.target,
        "details": details,
        "client_ip": row.client_ip,
    }


def list_audit(
    db: Session,
    *,
    store: Optional[str] = None,
    action: Optional[str] = None,
    before: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Newest first; ``before`` is an id (keyset paging); max 200."""
    limit = max(1, min(int(limit or 50), 200))
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if store:
        q = q.where(AuditLog.store_code == store.strip().upper())
    if action:
        a = action.strip()
        q = q.where(AuditLog.action.like(a[:-1] + "%") if a.endswith("*") else AuditLog.action == a)
    if before:
        q = q.where(AuditLog.id < int(before))
    return [audit_view(r) for r in db.execute(q).scalars()]


def since(minutes: int) -> dt.datetime:
    return utcnow() - dt.timedelta(minutes=minutes)
