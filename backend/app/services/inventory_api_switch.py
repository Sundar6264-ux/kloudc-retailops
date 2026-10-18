"""The "Use Toast for inventory" switch (owner, Oct 13 2026): an admin turns
Inventory from Toast on or off on its page, effective immediately - no
restart, no .env edit.

What it switches is exactly what INVENTORY_API_SNAPSHOTS switched before
(services/inventory_api.py): the 04:05 job WRITING the snapshot built from
Toast (never over a CSV for that day), and the live stock overlay on Supplier
Projection, Reorder Candidates and Fix Issues' Negative / Refill lists.

**Precedence:** the newest `setting_changes` row for SWITCH_KEY wins. Until an
admin has changed it on the page (no row), the .env value
INVENTORY_API_SNAPSHOTS (default false) is used - it is only the starting
value. Once set here, editing .env no longer changes it.

Read on every use (one indexed query on a tiny table, no cache), so a change
reaches the nightly job, the overlay and every worker at once. If the table
can't be read (e.g. code deployed before migration 0052 ran) the .env value
is used and the error logged.
"""

import json
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.setting_models import SettingChange
from app.timeutil import utcnow
from app.tenancy import tenant_lock

logger = logging.getLogger(__name__)

SWITCH_KEY = "inventory_api_snapshots"
NOTE_MAX_CHARS = 500
HISTORY_LIMIT = 50
# One change at a time (the backend is one process): two admins pressing at
# once must each record the value really in force before them, never a
# "turned on, was on" row.
_change_lock = tenant_lock("inventory_api_switch._change_lock")


def env_default() -> bool:
    return bool(get_settings().inventory_api_snapshots)


def _newest(db: Session) -> Optional[SettingChange]:
    return (
        db.execute(
            select(SettingChange)
            .where(SettingChange.key == SWITCH_KEY)
            # id, not changed_at: ids only grow (AUTOINCREMENT), so the
            # newest change wins even if the clock is ever set back.
            .order_by(SettingChange.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _value(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    try:
        return bool(json.loads(raw))
    except ValueError:
        return None


def _read(db: Optional[Session]) -> Optional[SettingChange]:
    """The newest row, or None (never set here / unreadable)."""
    own = db is None
    if own:
        from app.db import SessionLocal

        db = SessionLocal()
    try:
        row = _newest(db)
        if row is not None and own:
            db.expunge(row)
        return row
    except Exception:  # noqa: BLE001 - fall back to .env, never break a page
        logger.exception("Inventory from Toast switch: couldn't read setting_changes; using INVENTORY_API_SNAPSHOTS")
        if not own:
            db.rollback()
        return None
    finally:
        if own:
            db.close()


def is_on(db: Optional[Session] = None) -> bool:
    """The switch in force: the newest admin change, else the .env value."""
    row = _read(db)
    value = _value(row.value) if row is not None else None
    return env_default() if value is None else value


def switch_state(db: Session) -> dict:
    row = _read(db)
    value = _value(row.value) if row is not None else None
    from_db = value is not None
    return {
        "on": value if from_db else env_default(),
        "source": "page" if from_db else "env",
        "env_default": env_default(),
        "changed_at": row.changed_at.isoformat() if from_db and row.changed_at else None,
        "changed_by": row.changed_by_name if from_db else None,
        "note": row.note if from_db else None,
    }


def history(db: Session, limit: int = HISTORY_LIMIT) -> List[dict]:
    rows = db.execute(
        select(SettingChange)
        .where(SettingChange.key == SWITCH_KEY)
        .order_by(SettingChange.id.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "id": r.id,
            "on": _value(r.value),
            "previous_on": _value(r.previous_value),
            "previous_source": "page" if r.previous_source == "db" else r.previous_source,
            "changed_at": r.changed_at.isoformat() if r.changed_at else None,
            "changed_by": r.changed_by_name,
            "note": r.note,
        }
        for r in rows
    ]


def set_switch(db: Session, user: User, on: bool, note: Optional[str] = None) -> dict:
    """Records the change (who / when / what it was before) and returns
    {"changed": bool, "state": ...}. Setting the value already in force from
    an earlier change here is a no-op (no history row). Setting the .env
    value explicitly does write a row: from then on the page decides, not
    .env."""
    note = (note or "").strip()[:NOTE_MAX_CHARS] or None
    with _change_lock:
        # Read inside the lock (and after ending any read the session already
        # had open, so it sees the change another request just committed).
        db.commit()
        current = switch_state(db)
        if current["source"] == "page" and current["on"] == bool(on):
            return {"changed": False, "state": current}
        db.add(
            SettingChange(
                key=SWITCH_KEY,
                value=json.dumps(bool(on)),
                previous_value=json.dumps(bool(current["on"])),
                previous_source="db" if current["source"] == "page" else "env",
                changed_by_user_id=user.id,
                changed_by_name=user.username,
                changed_at=utcnow(),
                note=note,
            )
        )
        db.commit()
    logger.info("Inventory from Toast switch turned %s by %s", "on" if on else "off", user.username)
    _after_change()
    return {"changed": True, "state": switch_state(db)}


def _after_change() -> None:
    """Drop the cached live counts: switched on, the first page view asks
    Toast afresh; switched off, nothing live is served (live_stock_meta reads
    the switch too)."""
    try:
        from app.services.inventory_api import clear_live_stock_cache

        clear_live_stock_cache()
    except Exception:  # noqa: BLE001
        logger.exception("Inventory from Toast switch: clearing the live stock cache failed")
