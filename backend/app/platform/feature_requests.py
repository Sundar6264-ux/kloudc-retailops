"""Feature requests: a store asks for a store feature, a master decides
(docs/LABEL_PRINTING_FEATURE.md section 4; ``label_printing`` in v1).

    requested --approve--> approved      (sets label_printing_enabled=true + labels_url)
        |---------decline--> declined     (note required, shown to the store)
        |---------cancel---> cancelled    (store admin)
    declined / cancelled: the store may ask again (a new row)

The table is generic (``feature``), so other features can be requested the
same way later. Every step is audited. No push or email in v1: the store
sees the new state on its Label printing card.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform import bridge
from app.platform.audit import audit
from app.platform.errors import PlatformError
from app.platform.models import FeatureRequest, Store, utcnow
from app.platform.stores import put_settings, settings_values, store_by_code

LABEL_PRINTING = "label_printing"
FEATURES = {LABEL_PRINTING: "Label printing"}
STATUSES = ("requested", "approved", "declined", "cancelled")
NOTE_MAX = 1000

MSG_ALREADY_ON = "Label printing is already on for this store."
MSG_ALREADY_REQUESTED = "You already asked. We'll turn it on and let you know."
MSG_NEED_ADDRESS = "Add the label site address first."
MSG_NEED_NOTE = "Add a short note for the store."


# --- derived values from the raw stored settings -------------------------------------


def _ns(values: Dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        labels_url=values.get("labels_url") or "",
        label_printing_enabled=values.get("label_printing_enabled"),
        label_printing_options=values.get("label_printing_options"),
    )


def label_printing_state_of(values: Dict[str, Any]) -> str:
    """"on" / "off" / "on_no_address" from the stored settings (the same rule
    as app.store.settings_view.label_printing_state)."""
    from app.store import settings_view as sv

    return sv.label_printing_state(_ns(values))


def options_of(values: Dict[str, Any]) -> Dict[str, Any]:
    from app.store import settings_view as sv

    return sv.label_printing_options(_ns(values))


# --- rows ------------------------------------------------------------------------------


def request_view(row: FeatureRequest, code: str) -> Dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "id": row.id,
        "store_code": code,
        "feature": row.feature,
        "feature_label": FEATURES.get(row.feature, row.feature),
        "status": row.status,
        "requested_by": row.requested_by,
        "note": row.note,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": iso(row.reviewed_at),
        "review_note": row.review_note,
        "created_at": iso(row.created_at),
    }


def store_view(row: Optional[FeatureRequest]) -> Optional[Dict[str, Any]]:
    """What the store itself sees of its latest request (no master names)."""
    if row is None:
        return None
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "id": row.id,
        "status": row.status,
        "note": row.note,
        "created_at": iso(row.created_at),
        "reviewed_at": iso(row.reviewed_at),
        "review_note": row.review_note if row.status == "declined" else None,
    }


def open_request(db: Session, store_id: int, feature: str) -> Optional[FeatureRequest]:
    return db.execute(
        select(FeatureRequest).where(
            FeatureRequest.store_id == store_id, FeatureRequest.feature == feature, FeatureRequest.status == "requested"
        )
    ).scalar_one_or_none()


def latest_request(db: Session, store_id: int, feature: str) -> Optional[FeatureRequest]:
    return db.execute(
        select(FeatureRequest)
        .where(FeatureRequest.store_id == store_id, FeatureRequest.feature == feature)
        .order_by(FeatureRequest.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_requests(db: Session, status: Optional[str] = None, store_code: Optional[str] = None,
                  feature: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(FeatureRequest, Store.code).join(Store, Store.id == FeatureRequest.store_id).order_by(FeatureRequest.id.desc())
    if status:
        q = q.where(FeatureRequest.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if store_code:
        q = q.where(Store.code == store_code.strip().upper())
    if feature:
        q = q.where(FeatureRequest.feature == feature.strip())
    return [request_view(r, code) for r, code in db.execute(q.limit(500)).all()]


def _clean_note(note: Any) -> Optional[str]:
    if note is None:
        return None
    if not isinstance(note, str):
        raise PlatformError(400, "invalid", "The note must be text.", field="note")
    note = note.strip()
    if len(note) > NOTE_MAX:
        raise PlatformError(400, "invalid", f"Keep the note under {NOTE_MAX} characters.", field="note")
    return note or None


# --- store side -------------------------------------------------------------------------


def create(db: Session, code: str, *, actor: str, note: Any = None, feature: str = LABEL_PRINTING,
           client_ip: Optional[str] = None) -> Dict[str, Any]:
    """A store admin asks. 409 ``already_on`` / ``already_requested`` (the
    latter with the open request)."""
    if feature not in FEATURES:
        raise PlatformError(400, "invalid", "Unknown feature.", field="feature")
    note = _clean_note(note)
    store = store_by_code(db, code)
    if label_printing_state_of(settings_values(db, store)) == "on":
        raise PlatformError(409, "already_on", MSG_ALREADY_ON)
    existing = open_request(db, store.id, feature)
    if existing is not None:
        raise PlatformError(409, "already_requested", MSG_ALREADY_REQUESTED, request=store_view(existing))
    row = FeatureRequest(store_id=store.id, feature=feature, status="requested", requested_by=actor[:128], note=note,
                         created_at=utcnow())
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = open_request(db, store.id, feature)
        raise PlatformError(409, "already_requested", MSG_ALREADY_REQUESTED, request=store_view(existing)) from None
    audit("feature_request.requested", actor_type="store_user", actor_name=actor, store_code=store.code,
          target=f"feature_request:{row.id}", details={"feature": feature, "note": note}, client_ip=client_ip, db=db)
    db.commit()
    return store_view(row)


def cancel(db: Session, code: str, *, actor: str, feature: str = LABEL_PRINTING,
           client_ip: Optional[str] = None) -> Dict[str, Any]:
    store = store_by_code(db, code)
    row = open_request(db, store.id, feature)
    if row is None:
        raise PlatformError(409, "conflict", "There is no open request to cancel.")
    row.status = "cancelled"
    row.reviewed_by = actor[:128]
    row.reviewed_at = utcnow()
    audit("feature_request.cancel", actor_type="store_user", actor_name=actor, store_code=store.code,
          target=f"feature_request:{row.id}", details={"feature": feature}, client_ip=client_ip, db=db)
    db.commit()
    return store_view(row)


# --- master side ------------------------------------------------------------------------


def _load(db: Session, request_id: int):
    row = db.get(FeatureRequest, int(request_id))
    if row is None:
        raise PlatformError(404, "not_found")
    return row, db.get(Store, row.store_id)


def approve(db: Session, request_id: int, *, actor: str, labels_url: Optional[str] = None, note: Any = None,
            client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Turns the feature on (label_printing_enabled=true and the address)
    and marks the request approved, in one transaction."""
    row, store = _load(db, request_id)
    if row.status != "requested":
        raise PlatformError(409, "conflict", f"This request is {row.status}.")
    if store.status != "active":
        raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
    note = _clean_note(note)
    url = (labels_url or "").strip() if isinstance(labels_url, str) else labels_url
    if url not in (None, "") and not isinstance(url, str):
        raise PlatformError(400, "invalid", MSG_NEED_ADDRESS, field="labels_url")
    stored = settings_values(db, store).get("labels_url") or ""
    if not url and not stored:
        raise PlatformError(400, "invalid", MSG_NEED_ADDRESS, field="labels_url")
    updates: Dict[str, Any] = {"label_printing_enabled": True}
    if url:
        updates["labels_url"] = url
    try:
        row.status = "approved"
        row.reviewed_by = actor[:128]
        row.reviewed_at = utcnow()
        row.review_note = note
        db.flush()
        put_settings(db, store.code, updates, actor=actor, client_ip=client_ip, commit=False)
        audit("feature_request.approve", actor_type="platform_user", actor_name=actor, store_code=store.code,
              target=f"feature_request:{row.id}", details={"feature": row.feature, "labels_url": url or stored, "note": note},
              client_ip=client_ip, db=db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    bridge.store_changed(store.code)
    db.refresh(row)
    return request_view(row, store.code)


def decline(db: Session, request_id: int, *, actor: str, note: Any, client_ip: Optional[str] = None) -> Dict[str, Any]:
    row, store = _load(db, request_id)
    note = _clean_note(note)
    if not note:
        raise PlatformError(400, "invalid", MSG_NEED_NOTE, field="note")
    if row.status != "requested":
        raise PlatformError(409, "conflict", f"This request is {row.status}.")
    row.status = "declined"
    row.reviewed_by = actor[:128]
    row.reviewed_at = utcnow()
    row.review_note = note
    audit("feature_request.decline", actor_type="platform_user", actor_name=actor, store_code=store.code,
          target=f"feature_request:{row.id}", details={"feature": row.feature, "note": note}, client_ip=client_ip, db=db)
    db.commit()
    return request_view(row, store.code)


def close_open_on_manual_enable(db: Session, store: Store, *, actor: str, actor_type: str = "platform_user",
                                client_ip: Optional[str] = None) -> None:
    """A master turned label printing on by hand: the open request (if any)
    becomes approved, "Turned on by <master>". No commit (the caller's)."""
    row = open_request(db, store.id, LABEL_PRINTING)
    if row is None:
        return
    row.status = "approved"
    row.reviewed_by = actor[:128]
    row.reviewed_at = utcnow()
    row.review_note = f"Turned on by {actor}"[:1000]
    audit("feature_request.approve", actor_type=actor_type, actor_name=actor, store_code=store.code,
          target=f"feature_request:{row.id}", details={"feature": row.feature, "by_hand": True}, client_ip=client_ip, db=db)


def open_count(db: Session) -> int:
    from sqlalchemy import func

    return int(db.execute(select(func.count()).select_from(FeatureRequest).where(FeatureRequest.status == "requested")).scalar() or 0)

