"""Platform settings a master can change in the console
(docs/WEBSITE_READY_CONTRACT.md, interface I5).

INTERFACE STUB landed with the website-ready contract. Track A owns this
module and adds the ``platform_settings`` table, validation, audit and the
console page; tracks B and C read values through ``get_platform_setting``
only. Security switches (two-step requirement, dedicated hosting, keys,
database URLs) are never editable here: they stay environment-only.

Precedence (track A): a value saved in the console, else the process
setting / environment (``app.platform.config.pget``), else the default below.
"""

from __future__ import annotations

from typing import Any

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


def default_of(key: str) -> Any:
    for k, v in _DEFAULTS:
        if k == key:
            return v
    raise KeyError(key)


def get_platform_setting(key: str) -> Any:
    """The current value of an editable platform setting. Stub: the process
    setting / environment when one exists, else the default."""
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


__all__ = (
    "DEFAULT_PLAN", "EDITABLE_KEYS", "EXPORT_LINK_HOURS", "OFFBOARD_GRACE_DAYS", "SIGNUP_ENABLED",
    "SUPPORT_EMAIL", "TERMS_URL", "default_of", "get_platform_setting",
)
