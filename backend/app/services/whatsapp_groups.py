"""WhatsApp groups a checklist's summary can be posted to
(docs/CHECKLIST_BUILDER_APP_AND_GROUPS_CONTRACT.md, section 1; table
whatsapp_groups, migration 0044; routes in routers/checklist_admin.py under
/api/checklists/admin/whatsapp-groups, admins only).

A checklist's settings.whatsapp_group holds a group `key`. Sending resolves
the key to a Whapi chat id here (resolve_chat_id):
- an active row with a chat_id -> that chat id;
- an active built-in row with chat_id NULL -> the env setting
  (WHAPI_SPICE_TOWN_/STREET_KITCHEN_CHECKLIST_GROUP_ID), exactly as before;
- unknown, inactive, empty or unconfigured -> "" (nothing is sent; the
  submission records "not set up", as an unconfigured env group always did).

The table is seeded by migration 0044; an empty table (a fresh test DB built
with create_all) is seeded with the two built-ins on first admin use, and
until then resolves as if they were there.

"Available on WhatsApp" lists the store number's groups from Whapi (GET
/groups, read-only), cached for 5 minutes. Nothing here ever sends a message.

Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md, section 5; migration
0046): an admin ticks `marketing` ("Use for flyers") on the groups a flyer may
go to - marketing_targets() is the only list services/flyers.py sends to.
Every group starts unticked, the two built-in staff groups included.
"""

import logging
import re
import threading
import time
from typing import Dict, List, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import ChecklistDefinition, User, WhatsAppGroup
from app.services import whapi_client
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

# (key, label): the built-in roles, the store pack's whatsapp_roles
# (builtin_groups()). BUILTIN_GROUPS still reads them (module __getattr__).


def builtin_groups() -> tuple:
    from app.store import pack as store_pack

    return tuple((r.key, r.label) for r in store_pack.whatsapp_roles())
MAX_LABEL = 60
MAX_KEY = 40
KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
# A Whapi / WhatsApp chat id: a group (1203...@g.us), a person
# (1860...@s.whatsapp.net / @c.us) or a channel (...@newsletter).
CHAT_ID_RE = re.compile(r"^[0-9][0-9-]{4,60}@(g\.us|s\.whatsapp\.net|c\.us|newsletter)$")
AVAILABLE_CACHE_SECONDS = 300
NOT_CONNECTED = "WhatsApp isn't connected"


class GroupError(Exception):
    def __init__(self, status_code: int, detail: str, used_by: Optional[List[str]] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.used_by = used_by


def _env_chat_id(key: str) -> str:
    """A role's chat id from the settings (WHATSAPP_GROUP_CHAT_IDS, or the two
    legacy WHAPI_*_CHECKLIST_GROUP_ID settings for "store" / "kitchen")."""
    from app.store.settings_view import whatsapp_role_chat_ids

    return whatsapp_role_chat_ids(get_settings()).get(key, "") or ""


def ensure_seeded(db: Session) -> None:
    """Adds the store pack's built-in roles to an empty table (a fresh
    database; a store set up with `python -m app.store init` already has them)."""
    if db.execute(select(func.count(WhatsAppGroup.id))).scalar_one():
        return
    now = utcnow()
    for key, label in builtin_groups():
        db.add(WhatsAppGroup(key=key, label=label, chat_id=None, builtin=True, active=True,
                             created_at=now, created_by="built-in"))
    db.commit()


def _chat_id_for_row(row: WhatsAppGroup) -> str:
    if not row.active:
        return ""
    if row.chat_id:
        return row.chat_id
    return _env_chat_id(row.key) if row.builtin else ""


def resolve_chat_id(key: Optional[str], db: Optional[Session] = None) -> str:
    """settings.whatsapp_group -> the Whapi chat id to post to, "" for none."""
    if not key:
        return ""
    own = db is None
    session = SessionLocal() if own else db
    try:
        try:
            row = session.execute(select(WhatsAppGroup).where(WhatsAppGroup.key == key)).scalar_one_or_none()
            empty = row is None and not session.execute(select(func.count(WhatsAppGroup.id))).scalar_one()
        except (OperationalError, ProgrammingError):
            # The table isn't there yet (database not migrated): built-ins only.
            session.rollback()
            row, empty = None, True
        if row is not None:
            return _chat_id_for_row(row)
        if empty and key in dict(builtin_groups()):
            return _env_chat_id(key)
        return ""
    finally:
        if own:
            session.close()


def active_keys(db: Session) -> List[str]:
    ensure_seeded(db)
    return list(
        db.execute(select(WhatsAppGroup.key).where(WhatsAppGroup.active.is_(True)).order_by(WhatsAppGroup.id)).scalars()
    )


def _rows(db: Session) -> List[WhatsAppGroup]:
    ensure_seeded(db)
    return list(db.execute(select(WhatsAppGroup).order_by(WhatsAppGroup.builtin.desc(), WhatsAppGroup.id)).scalars())


def meta_groups(db: Session) -> List[dict]:
    """GET /meta whatsapp_groups: active groups, [{key, label, builtin}]."""
    return [{"key": r.key, "label": r.label, "builtin": bool(r.builtin)} for r in _rows(db) if r.active]


def mask_chat_id(chat_id: Optional[str]) -> Optional[str]:
    """Only the last 6 characters of the id itself (plus its @domain):
    "120363012345678901@g.us" -> "…678901@g.us"."""
    if not chat_id:
        return None
    local, at, domain = chat_id.partition("@")
    return f"…{local[-6:]}{at}{domain}"


def used_by(db: Session) -> Dict[str, List[str]]:
    """group key -> titles of the checklists (active or not) set to post to it."""
    import json

    from app.services.checklist_admin import ensure_seeded as ensure_checklists_seeded

    ensure_checklists_seeded(db)

    out: Dict[str, List[str]] = {}
    rows = db.execute(
        select(ChecklistDefinition.title, ChecklistDefinition.settings_json).order_by(
            ChecklistDefinition.sort_order, ChecklistDefinition.title, ChecklistDefinition.id
        )
    ).all()
    for title, settings_json in rows:
        try:
            key = (json.loads(settings_json or "{}") or {}).get("whatsapp_group")
        except (ValueError, AttributeError):
            continue
        if isinstance(key, str) and key:
            out.setdefault(key, []).append(title)
    return out


def group_out(row: WhatsAppGroup, users: Optional[List[str]] = None) -> dict:
    chat_id = row.chat_id or (_env_chat_id(row.key) if row.builtin else "")
    return {
        "key": row.key,
        "label": row.label,
        "chat_id_masked": mask_chat_id(chat_id),
        # Additive: True when sending to it would reach a chat (active rows
        # with a chat id, or a built-in whose env setting is filled in).
        "configured": bool(chat_id),
        "builtin": bool(row.builtin),
        "active": bool(row.active),
        # Additive (flyers): may receive marketing flyers.
        "marketing": bool(row.marketing),
        "used_by": users or [],
    }


def list_groups(db: Session) -> dict:
    users = used_by(db)
    return {"items": [group_out(r, users.get(r.key)) for r in _rows(db)]}


def _get(db: Session, key: str) -> WhatsAppGroup:
    ensure_seeded(db)
    row = db.execute(select(WhatsAppGroup).where(WhatsAppGroup.key == key)).scalar_one_or_none()
    if row is None:
        raise GroupError(404, "WhatsApp group not found")
    return row


def _clean_label(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GroupError(400, "Give the group a name")
    label = " ".join(value.split())
    if len(label) > MAX_LABEL:
        raise GroupError(400, f"The name can be at most {MAX_LABEL} characters")
    return label


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:MAX_KEY].strip("_")


def _known_chat_ids(db: Session) -> Dict[str, str]:
    """chat id -> group key, for every row (a built-in counts with its env id)."""
    out: Dict[str, str] = {}
    for row in _rows(db):
        chat_id = row.chat_id or (_env_chat_id(row.key) if row.builtin else "")
        if chat_id:
            out.setdefault(chat_id, row.key)
    return out


def _marketing_flag(payload: dict) -> Optional[bool]:
    """The optional `marketing` field: None when absent, else a real bool."""
    if "marketing" not in payload:
        return None
    if not isinstance(payload["marketing"], bool):
        raise GroupError(400, "marketing must be true or false")
    return payload["marketing"]


def create_group(db: Session, user: User, payload: dict) -> dict:
    chat_id = payload.get("chat_id")
    if not isinstance(chat_id, str) or not CHAT_ID_RE.match(chat_id.strip()):
        raise GroupError(400, "That doesn't look like a WhatsApp chat id (e.g. 120363012345678901@g.us)")
    chat_id = chat_id.strip()
    label = _clean_label(payload.get("label"))
    marketing = bool(_marketing_flag(payload))
    key = slugify(label)
    if not key:
        raise GroupError(400, "Use some letters or numbers in the name")
    existing = _known_chat_ids(db).get(chat_id)
    if existing is not None:
        raise GroupError(409, f'That WhatsApp group is already added (as "{_get(db, existing).label}")')
    if db.execute(select(WhatsAppGroup.id).where(WhatsAppGroup.key == key)).first() is not None:
        raise GroupError(409, f'A group with a name like "{label}" already exists - choose another name')
    now = utcnow()
    row = WhatsAppGroup(key=key, label=label, chat_id=chat_id, builtin=False, active=True,
                        marketing=marketing, created_at=now, created_by=user.username)
    db.add(row)
    db.commit()
    db.refresh(row)
    return group_out(row)


def patch_group(db: Session, user: User, key: str, payload: dict) -> dict:
    row = _get(db, key)
    if "label" in payload:
        row.label = _clean_label(payload["label"])
    if "active" in payload:
        if not isinstance(payload["active"], bool):
            raise GroupError(400, "active must be true or false")
        row.active = payload["active"]
    marketing = _marketing_flag(payload)
    if marketing is not None:
        row.marketing = marketing
    row.updated_at = utcnow()
    row.updated_by = user.username
    db.commit()
    db.refresh(row)
    return group_out(row, used_by(db).get(row.key))


def marketing_targets(db: Session) -> List[dict]:
    """The groups a flyer may go to: active, ticked `marketing`, and with a
    chat id to send to (a built-in whose env setting is blank has none) -
    [{key, label, chat_id}] in label order. chat_id is for the sender only;
    it is never returned to a client."""
    out = []
    for row in _rows(db):
        if not row.marketing:
            continue
        chat_id = _chat_id_for_row(row)
        if chat_id:
            out.append({"key": row.key, "label": row.label, "chat_id": chat_id})
    out.sort(key=lambda g: (g["label"].lower(), g["key"]))
    return out


def delete_group(db: Session, key: str) -> None:
    row = _get(db, key)
    if row.builtin:
        raise GroupError(400, "A built-in group can't be deleted. Turn it off instead.")
    users = used_by(db).get(key) or []
    if users:
        raise GroupError(
            409,
            f"Used by {', '.join(users)}. Choose another group for {'it' if len(users) == 1 else 'them'} first.",
            used_by=users,
        )
    db.delete(row)
    db.commit()


# --- Available on WhatsApp ----------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Dict[str, object] = {"at": 0.0, "groups": None}


def clear_available_cache() -> None:
    with _cache_lock:
        _cache["at"], _cache["groups"] = 0.0, None


def _whapi_groups(refresh: bool) -> List[dict]:
    with _cache_lock:
        fresh = _cache["groups"] is not None and time.monotonic() - float(_cache["at"]) < AVAILABLE_CACHE_SECONDS
        if fresh and not refresh:
            return list(_cache["groups"])  # type: ignore[arg-type]
    groups = whapi_client.list_groups()
    with _cache_lock:
        _cache["at"], _cache["groups"] = time.monotonic(), groups
    return list(groups)


def available(db: Session, refresh: bool = False) -> dict:
    """{items: [{chat_id, name, already_added_key}]} - never raises: when
    WhatsApp isn't set up or Whapi fails, items is [] with an `error`."""
    try:
        groups = _whapi_groups(refresh)
    except whapi_client.WhapiNotConfigured:
        return {"items": [], "error": NOT_CONNECTED}
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Whapi group list failed: %s", exc)
        return {"items": [], "error": "Couldn't load the groups from WhatsApp right now. Try again in a minute."}
    known = _known_chat_ids(db)
    items = [{"chat_id": g["chat_id"], "name": g["name"], "already_added_key": known.get(g["chat_id"])} for g in groups]
    items.sort(key=lambda g: (g["already_added_key"] is not None, (g["name"] or "").lower()))
    return {"items": items}


def __getattr__(name):
    if name == "BUILTIN_GROUPS":
        return builtin_groups()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
