"""Dedicated hosting: move ONE store to its own server, only with master
approval (contract section 12; owner C; runbook docs/DEDICATED_HOSTING_RUNBOOK.md).

    requested --approve--> approved --start--> migrating --complete--> done
        |                     |                    |                     |
        +--reject--> rejected  +--cancel--> cancelled                     +--rollback--> rolled_back

Everything here answers 409 ``dedicated_hosting_disabled`` while
``DEDICATED_HOSTING_ENABLED`` is false (the default), and a registry row can
never become ``dedicated`` then. Store admins may *request*; only masters
approve, start, complete or roll back. ``complete`` is the one step that
changes routing: it flips ``stores.hosting``/``api_base_url``/``db_host_ref``
so the shared service answers ``store_moved`` for that store (track B's
``app.tenancy.routing``) and the store lookup returns the new URL.
"""

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform import bridge
from app.platform.audit import audit
from app.platform.config import dedicated_enabled
from app.platform.errors import PlatformError
from app.platform.models import DedicatedHostingRequest, Store, utcnow
from app.platform.stores import store_by_code
from app.platform.urls import validate_origin_url

OPEN = ("requested", "approved", "migrating")
ROLLBACK_DAYS = 7


def require_enabled() -> None:
    if not dedicated_enabled():
        raise PlatformError(409, "dedicated_hosting_disabled")


def validate_api_base_url(value: Optional[str]) -> Optional[str]:
    """https, origin only, host under PLATFORM_DOMAIN or
    DIRECTORY_EXTRA_API_HOSTS, never the refused legacy domain (the shared
    rule in app/platform/urls.py)."""
    return validate_origin_url(value, "target_api_base_url")


def validate_db_host_ref(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    ref = str(value).strip()
    hosts = bridge._db_hosts()  # noqa: SLF001 - the one parser of STORE_DB_HOSTS
    if ref not in hosts:
        raise PlatformError(400, "invalid", f"STORE_DB_HOSTS has no entry {ref!r}.", field="target_db_host_ref")
    return ref


def request_view(r: DedicatedHostingRequest, code: str) -> Dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "id": r.id,
        "store_code": code,
        "status": r.status,
        "requested_by": r.requested_by,
        "reason": r.reason,
        "target_api_base_url": r.target_api_base_url,
        "target_db_host_ref": r.target_db_host_ref,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": iso(r.reviewed_at),
        "review_note": r.review_note,
        "created_at": iso(r.created_at),
        "completed_at": iso(r.completed_at),
        "rollback_until": iso(r.completed_at + dt.timedelta(days=ROLLBACK_DAYS)) if r.completed_at and r.status == "done" else None,
    }


def list_requests(db: Session, status: Optional[str] = None, store_code: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(DedicatedHostingRequest, Store.code).join(Store, Store.id == DedicatedHostingRequest.store_id).order_by(
        DedicatedHostingRequest.id.desc()
    )
    if status:
        q = q.where(DedicatedHostingRequest.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if store_code:
        q = q.where(Store.code == store_code)
    return [request_view(r, code) for r, code in db.execute(q).all()]


def create_request(db: Session, code: str, *, actor: str, actor_type: str, reason: str,
                   target_api_base_url: Optional[str] = None, target_db_host_ref: Optional[str] = None,
                   client_ip: Optional[str] = None) -> Dict[str, Any]:
    require_enabled()
    store = store_by_code(db, code)
    if store.status not in ("active", "suspended"):
        raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
    if store.hosting == "dedicated":
        raise PlatformError(409, "conflict", "This store already has its own server.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise PlatformError(400, "invalid", "Say why the store needs its own server.", field="reason")
    row = DedicatedHostingRequest(
        store_id=store.id,
        requested_by=actor[:128],
        reason=reason[:2000],
        target_api_base_url=validate_api_base_url(target_api_base_url),
        target_db_host_ref=validate_db_host_ref(target_db_host_ref) if actor_type == "platform_user" else None,
        status="requested",
        created_at=utcnow(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise PlatformError(409, "conflict", "This store already has an open hosting request.") from None
    audit("hosting.requested", actor_type=actor_type, actor_name=actor, store_code=store.code, target=f"hosting:{row.id}",
          details={"reason": reason[:500], "target_api_base_url": row.target_api_base_url}, client_ip=client_ip, db=db)
    db.commit()
    return request_view(row, store.code)


def _load(db: Session, request_id: int):
    row = db.get(DedicatedHostingRequest, int(request_id))
    if row is None:
        raise PlatformError(404, "not_found")
    store = db.get(Store, row.store_id)
    return row, store


def _set_status(db, row, store, to, *, actor, action, allowed, note=None, client_ip=None, details=None):
    if row.status not in allowed:
        raise PlatformError(409, "conflict", f"This request is {row.status}.")
    before = row.status
    row.status = to
    if to in ("approved", "rejected", "cancelled"):
        row.reviewed_by = actor
        row.reviewed_at = utcnow()
        row.review_note = (note or "").strip()[:1000] or row.review_note
    audit(action, actor_type="platform_user", actor_name=actor, store_code=store.code, target=f"hosting:{row.id}",
          details={"from": before, "to": to, "note": note, **(details or {})}, client_ip=client_ip, db=db)


def approve(db: Session, request_id: int, *, actor: str, note: Optional[str] = None,
            target_api_base_url: Optional[str] = None, target_db_host_ref: Optional[str] = None,
            client_ip: Optional[str] = None) -> Dict[str, Any]:
    require_enabled()
    row, store = _load(db, request_id)
    if target_api_base_url:
        row.target_api_base_url = validate_api_base_url(target_api_base_url)
    if target_db_host_ref:
        row.target_db_host_ref = validate_db_host_ref(target_db_host_ref)
    _set_status(db, row, store, "approved", actor=actor, action="hosting.approved", allowed=("requested",), note=note,
                client_ip=client_ip)
    db.commit()
    return request_view(row, store.code)


def reject(db: Session, request_id: int, *, actor: str, note: Optional[str] = None,
           client_ip: Optional[str] = None) -> Dict[str, Any]:
    require_enabled()
    row, store = _load(db, request_id)
    _set_status(db, row, store, "rejected", actor=actor, action="hosting.rejected", allowed=("requested",), note=note,
                client_ip=client_ip)
    db.commit()
    return request_view(row, store.code)


def cancel(db: Session, request_id: int, *, actor: str, note: Optional[str] = None,
           client_ip: Optional[str] = None) -> Dict[str, Any]:
    require_enabled()
    row, store = _load(db, request_id)
    _set_status(db, row, store, "cancelled", actor=actor, action="hosting.cancelled", allowed=("requested", "approved", "migrating"),
                note=note, client_ip=client_ip)
    db.commit()
    return request_view(row, store.code)


def start(db: Session, request_id: int, *, actor: str, client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Runbook step 3: the copy is starting (the store stays on the shared
    service until ``complete``)."""
    require_enabled()
    row, store = _load(db, request_id)
    _set_status(db, row, store, "migrating", actor=actor, action="hosting.migrating", allowed=("approved",),
                client_ip=client_ip)
    db.commit()
    return request_view(row, store.code)


# --- DEDICATED HOSTING (owner approval required; DEDICATED_HOSTING_ENABLED) ---
# See docs/DEDICATED_HOSTING_RUNBOOK.md. Off by default.
# `complete` is the only code that turns a registry row `dedicated`; it runs
# only for an APPROVED (or migrating) request, i.e. after a master said yes,
# and only with the flag on. `rollback` flips it back within 7 days.
def complete(db: Session, request_id: int, *, actor: str, api_base_url: Optional[str] = None,
             db_host_ref: Optional[str] = None, client_ip: Optional[str] = None) -> Dict[str, Any]:
    require_enabled()
    row, store = _load(db, request_id)
    if row.status not in ("approved", "migrating"):
        raise PlatformError(409, "conflict", f"This request is {row.status}; it must be approved first.")
    target_url = validate_api_base_url(api_base_url) if api_base_url else row.target_api_base_url
    target_ref = validate_db_host_ref(db_host_ref) if db_host_ref else row.target_db_host_ref
    if not target_url:
        raise PlatformError(400, "invalid", "Give the new server's https:// address.", field="api_base_url")
    row.previous_api_base_url = store.api_base_url
    row.previous_db_host_ref = store.db_host_ref
    row.target_api_base_url, row.target_db_host_ref = target_url, target_ref
    _set_status(db, row, store, "done", actor=actor, action="hosting.completed", allowed=("approved", "migrating"),
                client_ip=client_ip, details={"api_base_url": target_url, "db_host_ref": target_ref})
    row.completed_at = utcnow()
    store.hosting = "dedicated"
    store.api_base_url = target_url
    store.db_host_ref = target_ref
    store.settings_version = int(store.settings_version or 1) + 1
    store.updated_at = utcnow()
    db.commit()
    # This node stops serving the store: its engine, caches and jobs go.
    bridge.store_stopped(store.code)
    return request_view(row, store.code)


def rollback(db: Session, request_id: int, *, actor: str, note: Optional[str] = None,
             client_ip: Optional[str] = None) -> Dict[str, Any]:
    require_enabled()
    row, store = _load(db, request_id)
    if row.status != "done":
        raise PlatformError(409, "conflict", f"This request is {row.status}; only a completed move can be rolled back.")
    if row.completed_at and utcnow() - row.completed_at > dt.timedelta(days=ROLLBACK_DAYS):
        raise PlatformError(409, "conflict", f"Rollback is only possible within {ROLLBACK_DAYS} days (see the runbook).")
    _set_status(db, row, store, "rolled_back", actor=actor, action="hosting.rolled_back", allowed=("done",), note=note,
                client_ip=client_ip)
    store.hosting = "shared"
    store.api_base_url = row.previous_api_base_url
    store.db_host_ref = row.previous_db_host_ref
    store.settings_version = int(store.settings_version or 1) + 1
    store.updated_at = utcnow()
    db.commit()
    bridge.store_changed(store.code)
    return request_view(row, store.code)
# --- END DEDICATED HOSTING ---


def node_serves(store_code: str, hosting: str) -> bool:
    """Reference rule (track B's ``app.tenancy.routing.node_serves`` is the
    one the middleware uses): a shared store on a shared node, or a store
    listed in DEDICATED_STORE_CODES on its dedicated node."""
    from app.platform.config import dedicated_store_codes

    mine = dedicated_store_codes()
    if mine:
        return store_code in mine
    return hosting != "dedicated"
