"""Registry operations for masters: list, detail, suspend, resume, archive,
store settings (contract 3.1, 3.4, 8.2, 13.1; owner C)."""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.plain_errors import field_label
from app.platform import bridge
from app.platform.audit import audit
from app.platform.errors import PlatformError
from app.platform.models import Store, StoreCredential, StoreSetting, SupportSession, utcnow

DEFAULT_MAINTENANCE = "This store is paused. Please contact the store owner."

# Columns on `stores` that mirror a setting (kept in step on write).
_MIRRORED = {"store_name": "name", "store_timezone": "timezone", "pos_provider": "pos_provider"}


def setting_allowed(key: str) -> bool:
    """Contract 8.2: STORE_SETTING_KEYS (track B), never a platform-only key."""
    t = bridge.tenancy(required=True)
    return key in t.STORE_SETTING_KEYS and key not in t.PLATFORM_ONLY_KEYS


def store_by_code(db: Session, code: str) -> Store:
    store = db.execute(select(Store).where(Store.code == (code or "").strip().upper())).scalar_one_or_none()
    if store is None:
        raise PlatformError(404, "unknown_store")
    return store


def store_row(store: Store) -> Dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "code": store.code,
        "name": store.name,
        "status": store.status,
        "status_reason": store.status_reason,
        "hosting": store.hosting,
        "api_base_url": store.api_base_url,
        "pos_provider": store.pos_provider,
        "timezone": store.timezone,
        "pack_id": store.pack_id,
        "created_at": iso(store.created_at),
        "created_by": store.created_by,
        "approved_at": iso(store.approved_at),
        "approved_by": store.approved_by,
        "schema_revision": store.schema_revision,
        "updated_at": iso(store.updated_at),
    }


def list_stores(db: Session, status: Optional[str] = None, q: Optional[str] = None) -> List[Dict[str, Any]]:
    query = select(Store).order_by(Store.code)
    if status:
        query = query.where(Store.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    rows = [store_row(s) for s in db.execute(query).scalars()]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["code"].lower() or needle in (r["name"] or "").lower()]
    return rows


def store_detail(db: Session, code: str) -> Dict[str, Any]:
    store = store_by_code(db, code)
    out = store_row(store)
    out.update(
        {
            "db_name": store.db_name,
            "db_host_ref": store.db_host_ref,
            "instance_id": store.instance_id,
            "provisioning_step": store.provisioning_step,
            "settings_version": store.settings_version,
            "maintenance_message": store.maintenance_message,
            "owner_username": store.owner_username,
            "min_build": {"ios": store.min_build_ios, "android": store.min_build_android},
            "credentials_configured": sorted(
                db.execute(select(StoreCredential.kind).where(StoreCredential.store_id == store.id)).scalars()
            ),
            "settings": settings_values(db, store),
            "support_sessions": [
                {
                    "id": s.id,
                    "store_username": s.store_username,
                    "started_at": s.started_at.isoformat(),
                    "expires_at": s.expires_at.isoformat(),
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                    "reason": s.reason,
                }
                for s in db.execute(
                    select(SupportSession).where(SupportSession.store_code == store.code).order_by(SupportSession.id.desc()).limit(10)
                ).scalars()
            ],
        }
    )
    return out


def _bump(store: Store) -> None:
    store.settings_version = int(store.settings_version or 1) + 1
    store.updated_at = utcnow()


def _transition(db: Session, code: str, allowed_from: tuple, to: str, *, actor: str, action: str,
                reason: Optional[str] = None, client_ip: Optional[str] = None) -> Store:
    store = store_by_code(db, code)
    if store.status not in allowed_from:
        raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
    before = store.status
    store.status = to
    if to == "suspended":
        store.status_reason = (reason or "").strip()[:500] or None
        store.maintenance_message = store.maintenance_message or DEFAULT_MAINTENANCE
    elif to == "active":
        store.status_reason = None
    elif to == "archived":
        store.status_reason = (reason or store.status_reason or "")[:500] or None
    _bump(store)
    audit(action, actor_type="platform_user", actor_name=actor, store_code=store.code,
          details={"from": before, "to": to, "reason": reason}, client_ip=client_ip, db=db)
    db.commit()
    return store


def suspend(db: Session, code: str, *, actor: str, reason: str, message: Optional[str] = None,
            client_ip: Optional[str] = None) -> Store:
    if not (reason or "").strip():
        raise PlatformError(400, "invalid", "Give a reason (it goes in the audit log).")
    store = store_by_code(db, code)
    if message:
        store.maintenance_message = message.strip()[:500]
    store = _transition(db, code, ("active",), "suspended", actor=actor, action="store.suspended", reason=reason,
                        client_ip=client_ip)
    bridge.store_stopped(store.code)
    return store


def resume(db: Session, code: str, *, actor: str, client_ip: Optional[str] = None) -> Store:
    store = _transition(db, code, ("suspended",), "active", actor=actor, action="store.resumed", client_ip=client_ip)
    bridge.store_changed(store.code)
    return store


def archive(db: Session, code: str, *, actor: str, confirm: str, reason: Optional[str] = None,
            client_ip: Optional[str] = None) -> Store:
    store = store_by_code(db, code)
    if (confirm or "").strip().upper() != store.code:
        raise PlatformError(400, "invalid", f"Type the store code ({store.code}) to confirm.")
    store = _transition(db, code, ("active", "suspended", "failed", "pending"), "archived", actor=actor,
                        action="store.archived", reason=reason, client_ip=client_ip)
    bridge.store_stopped(store.code)
    return store


def settings_values(db: Session, store: Store) -> Dict[str, Any]:
    out = {}
    for row in db.execute(select(StoreSetting).where(StoreSetting.store_id == store.id).order_by(StoreSetting.key)).scalars():
        try:
            out[row.key] = json.loads(row.value)
        except ValueError:
            out[row.key] = row.value
    return out


# Store settings the website and the app put into links and images. A store
# admin sets them, and the website is one origin for every store: only
# https:// addresses (no javascript:, data:, scheme-relative or http).
URL_SETTING_KEYS = ("logo_url", "icon_url", "labels_url")


def _https_setting(key: str, value: Any) -> str:
    raw = value if isinstance(value, str) else ""
    v = raw.strip()
    if not v:
        return ""
    try:
        parts = urlsplit(v)
    except ValueError:
        parts = None
    if (
        v != raw.strip(" ")
        or parts is None
        or parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or any(c.isspace() or ord(c) < 32 for c in v)
    ):
        raise PlatformError(400, "invalid", f"{field_label(key)}: must be a web address starting with https://.", field=key)
    return v


def _validate_settings(current: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Each value through the same pydantic validators as env values
    (contract 8.2; track B's ``validate_store_setting``); None = remove."""
    t = bridge.tenancy(required=True)
    out: Dict[str, Any] = {}
    for key, value in updates.items():
        if value is None:
            out[key] = None
            continue
        if key in URL_SETTING_KEYS:
            value = _https_setting(key, value)
        try:
            out[key] = t.validate_store_setting(key, value)
        except ValueError as exc:
            raise PlatformError(400, "invalid", str(exc), field=key) from None
    return out


def put_settings(db: Session, code: str, updates: Dict[str, Any], *, actor: str, actor_type: str = "platform_user",
                 client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Set (value) or remove (null) whitelisted store settings."""
    if not isinstance(updates, dict) or not updates:
        raise PlatformError(400, "invalid", "Send an object of setting -> value.")
    bad = sorted(k for k in updates if not isinstance(k, str) or not setting_allowed(k))
    if bad:
        raise PlatformError(400, "invalid", f"Not a store setting: {', '.join(bad)}", fields=bad)
    store = store_by_code(db, code)
    current = settings_values(db, store)
    clean = _validate_settings(current, updates)
    for mirrored, column in _MIRRORED.items():
        if mirrored in clean and clean[mirrored] in (None, ""):
            raise PlatformError(400, "invalid", f"{field_label(mirrored)} can't be blank.", field=mirrored)
    if "pos_provider" in clean and clean["pos_provider"] not in ("toast", "clover", "csv"):
        raise PlatformError(400, "invalid", "pos_provider must be toast, clover or csv.", field="pos_provider")
    changed = {}
    existing = {r.key: r for r in db.execute(select(StoreSetting).where(StoreSetting.store_id == store.id)).scalars()}
    for key, value in clean.items():
        row = existing.get(key)
        if value is None:
            if row is not None:
                db.delete(row)
                changed[key] = None
            continue
        encoded = json.dumps(value)
        if row is None:
            db.add(StoreSetting(store_id=store.id, key=key, value=encoded, updated_at=utcnow(), updated_by=actor))
        elif row.value == encoded:
            continue
        else:
            row.value, row.updated_at, row.updated_by = encoded, utcnow(), actor
        changed[key] = value
        if key in _MIRRORED:
            setattr(store, _MIRRORED[key], value)
    if changed:
        _bump(store)
        audit("store.settings_changed", actor_type=actor_type, actor_name=actor, store_code=store.code,
              details={"changed": changed}, client_ip=client_ip, db=db)
    db.commit()
    if changed:
        bridge.store_changed(store.code)
    return settings_values(db, store)
