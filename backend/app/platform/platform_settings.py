"""Platform settings a master can change in the console
(docs/WEBSITE_READY_CONTRACT.md, interface I5; track A, item A3).

Tracks B and C read values through ``get_platform_setting`` only. Security
switches (two-step requirement, dedicated hosting, keys, database URLs) are
never editable here: they stay environment-only.

Precedence: a value saved in the console (``platform_settings`` table, p0003),
else the process setting / environment (``app.platform.config.pget``), else
the default below. Saved values are cached in this process for a few seconds
(``CACHE_SECONDS``) and dropped at once on a save here, so a change made in
the console reaches every request of this process straight away and other
processes within the cache time - no restart.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.platform.models import PlatformBase, utcnow

# key -> default. Every key here is safe to change at run time.
SIGNUP_ENABLED = "signup_enabled"
TERMS_URL = "terms_url"
SUPPORT_EMAIL = "support_email"
DEFAULT_PLAN = "default_plan"
OFFBOARD_GRACE_DAYS = "offboard_grace_days"
EXPORT_LINK_HOURS = "export_link_hours"

EDITABLE_KEYS = (SIGNUP_ENABLED, TERMS_URL, SUPPORT_EMAIL, DEFAULT_PLAN, OFFBOARD_GRACE_DAYS, EXPORT_LINK_HOURS)

_DEFAULTS = (
    (SIGNUP_ENABLED, True),
    (TERMS_URL, ""),
    (SUPPORT_EMAIL, "support@kloudc.com"),
    (DEFAULT_PLAN, "custom"),
    (OFFBOARD_GRACE_DAYS, 30),
    (EXPORT_LINK_HOURS, 24),
)

# What the console shows for each key (label, help, kind, limits).
SPECS: Dict[str, Dict[str, Any]] = {
    SIGNUP_ENABLED: {
        "label": "New store sign-ups",
        "help": "When off, the public sign-up page says sign-ups are closed. Existing stores are not affected.",
        "kind": "bool",
    },
    TERMS_URL: {
        "label": "Terms page link",
        "help": "Shown on the sign-up page. Leave empty to hide the link.",
        "kind": "url",
    },
    SUPPORT_EMAIL: {
        "label": "Support email",
        "help": "Shown to store owners wherever the website says how to reach support.",
        "kind": "email",
    },
    DEFAULT_PLAN: {
        "label": "Plan for new stores",
        "help": "The plan a newly approved store starts on. Existing stores keep theirs.",
        "kind": "choice",
    },
    OFFBOARD_GRACE_DAYS: {
        "label": "Days before an archived store's data is deleted",
        "help": "After a store is archived its data stays this many days, then it is deleted for good.",
        "kind": "int",
        "min": 7,
        "max": 365,
    },
    EXPORT_LINK_HOURS: {
        "label": "Hours a download link works",
        "help": "How long the Download link of a data export works. After that a new export is needed.",
        "kind": "int",
        "min": 1,
        "max": 168,
    },
}

CACHE_SECONDS = 5.0
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PlatformSettingRow(PlatformBase):
    """p0003. One row per key a master changed in the console."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_by: Mapped[Optional[str]] = mapped_column(Text)


class SettingError(ValueError):
    def __init__(self, key: str, message: str):
        super().__init__(message)
        self.key = key
        self.message = message


def default_of(key: str) -> Any:
    for k, v in _DEFAULTS:
        if k == key:
            return v
    raise KeyError(key)


# --- the saved (console) layer ---------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "values": None}


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["values"] = None


def _load_saved() -> Dict[str, Any]:
    from app.platform.db import is_configured, platform_session

    if not is_configured():
        return {}
    with platform_session() as db:
        rows = db.execute(select(PlatformSettingRow)).scalars().all()
        out = {}
        for r in rows:
            try:
                out[r.key] = json.loads(r.value_json)["v"]
            except (ValueError, KeyError, TypeError):
                continue  # a damaged row: ignored, the environment/default wins
        return out


def saved_values() -> Dict[str, Any]:
    """Values saved in the console (cached for ``CACHE_SECONDS``). Never raises:
    no platform database, or one that is down, means no saved values."""
    now = time.monotonic()
    with _cache_lock:
        if _cache["values"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return dict(_cache["values"])
    try:
        values = _load_saved()
    except Exception:  # noqa: BLE001 - table missing (before p0003) / DB down: env + defaults
        values = {}
    with _cache_lock:
        _cache["values"], _cache["at"] = values, now
    return dict(values)


def _env_value(key: str) -> Any:
    default = default_of(key)
    from app.platform.config import _coerce, pget

    try:
        value = pget(key)
    except Exception:  # noqa: BLE001 - settings not loadable: the default
        value = None
    if value is None:
        return default
    if isinstance(value, str) and not isinstance(default, str):
        return _coerce(value, default)  # an environment value for a bool / int key
    return value


def get_platform_setting(key: str) -> Any:
    """The current value of an editable platform setting: console > environment > default."""
    default_of(key)  # KeyError for a key that is not editable
    saved = saved_values()
    if key in saved:
        return saved[key]
    return _env_value(key)


def source_of(key: str) -> str:
    """Where the current value comes from: ``console``, ``environment`` or ``default``."""
    if key in saved_values():
        return "console"
    return "environment" if os.environ.get(key.upper()) is not None else "default"


# --- validation + save ------------------------------------------------------------------


def _plan_ids() -> tuple:
    try:
        from app.platform.plans import PLAN_IDS

        return tuple(PLAN_IDS)
    except Exception:  # noqa: BLE001
        return ("custom", "trial", "starter", "pro")


def validate(key: str, value: Any) -> Any:
    """The cleaned value, or SettingError with a plain message."""
    if key not in EDITABLE_KEYS:
        raise SettingError(key, "That setting cannot be changed here.")
    spec = SPECS[key]
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingError(key, "Choose on or off.")
        return value
    if kind == "int":
        if isinstance(value, bool):
            raise SettingError(key, "Enter a whole number.")
        if isinstance(value, int):
            n = value
        elif isinstance(value, str) and re.fullmatch(r"\s*\d{1,6}\s*", value):
            n = int(value)
        else:
            raise SettingError(key, "Enter a whole number.")
        if not (spec["min"] <= n <= spec["max"]):
            raise SettingError(key, f"Enter a number from {spec['min']} to {spec['max']}.")
        return n
    if not isinstance(value, str):
        raise SettingError(key, "Enter text.")
    v = value.strip()
    if kind == "url":
        if v and (len(v) > 500 or not re.match(r"^https://[^\s/]+\.[^\s]+$", v)):
            raise SettingError(key, "Enter a full https:// link, or leave it empty.")
        return v
    if kind == "email":
        if not v or len(v) > 200 or not _EMAIL.match(v):
            raise SettingError(key, "Enter an email address like support@example.com.")
        return v.lower()
    if kind == "choice":
        ids = _plan_ids()
        if v not in ids:
            raise SettingError(key, "Choose one of: " + ", ".join(ids) + ".")
        return v
    raise SettingError(key, "That setting cannot be changed here.")  # pragma: no cover


def describe_all() -> List[Dict[str, Any]]:
    """Every editable key for the console page, in display order."""
    out = []
    for key in EDITABLE_KEYS:
        spec = SPECS[key]
        item = {
            "key": key,
            "label": spec["label"],
            "help": spec["help"],
            "kind": spec["kind"],
            "value": get_platform_setting(key),
            "default": default_of(key),
            "source": source_of(key),
        }
        if spec["kind"] == "int":
            item["min"], item["max"] = spec["min"], spec["max"]
        if spec["kind"] == "choice":
            item["choices"] = list(_plan_ids())
        out.append(item)
    return out


def save_settings(
    db: Session,
    changes: Dict[str, Any],
    *,
    actor: str,
    actor_id: Optional[int] = None,
    client_ip: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Validate every change first (all or nothing), then save and audit each
    changed key with its old and new value. Returns the changed keys."""
    from app.platform.audit import audit

    if not isinstance(changes, dict) or not changes:
        raise SettingError("", "Nothing to save.")
    cleaned = {key: validate(key, value) for key, value in changes.items()}
    changed = []
    for key, new in cleaned.items():
        old = get_platform_setting(key)
        if old == new and source_of(key) != "console":
            continue  # no change and nothing to pin: the environment/default stays in charge
        row = db.get(PlatformSettingRow, key)
        if row is None:
            row = PlatformSettingRow(key=key)
            db.add(row)
        row.value_json = json.dumps({"v": new})
        row.updated_at = utcnow()
        row.updated_by = actor
        if old != new:
            changed.append({"key": key, "old": old, "new": new})
            audit(
                "platform.setting_changed", actor_type="platform_user", actor_id=actor_id, actor_name=actor,
                target=key, details={"old": old, "new": new}, client_ip=client_ip, db=db,
            )
    db.commit()
    invalidate_cache()
    return changed


def reset_setting(db: Session, key: str, *, actor: str, actor_id: Optional[int] = None,
                  client_ip: Optional[str] = None) -> bool:
    """Drop the console value so the environment / default applies again (audited)."""
    from app.platform.audit import audit

    if key not in EDITABLE_KEYS:
        raise SettingError(key, "That setting cannot be changed here.")
    row = db.get(PlatformSettingRow, key)
    if row is None:
        return False
    old = get_platform_setting(key)
    db.delete(row)
    new = _env_value(key)
    audit(
        "platform.setting_reset", actor_type="platform_user", actor_id=actor_id, actor_name=actor,
        target=key, details={"old": old, "new": new}, client_ip=client_ip, db=db,
    )
    db.commit()
    invalidate_cache()
    return True


__all__ = (
    "DEFAULT_PLAN", "EDITABLE_KEYS", "EXPORT_LINK_HOURS", "OFFBOARD_GRACE_DAYS", "SIGNUP_ENABLED",
    "SUPPORT_EMAIL", "TERMS_URL", "default_of", "get_platform_setting",
)
