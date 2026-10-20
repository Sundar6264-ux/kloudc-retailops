"""Plans and soft limits (docs/WEBSITE_READY_CONTRACT.md, interface I4).

INTERFACE STUB landed with the website-ready contract. Track B owns this
module (plan per store, limits, ``limit_states``); track A's console and
platform settings read ``PLAN_IDS`` / ``plan_of`` / ``limit_states`` only.

Limits are soft: crossing one never blocks the store's work. It shows a
notice to the store's admins and a flag in the master console.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

CUSTOM = "custom"  # no limits; every store that existed before plans
TRIAL = "trial"
STARTER = "starter"
PRO = "pro"
PLAN_IDS = (CUSTOM, TRIAL, STARTER, PRO)
DEFAULT_PLAN = CUSTOM


def plan_of(code: str) -> str:
    """The plan id of store ``code``. Stub: always custom."""
    return CUSTOM


def limits_of(plan: str) -> Dict[str, Optional[int]]:
    """{limit key: value or None for unlimited}. Stub: unlimited."""
    return {}


def limit_states(code: str) -> List[Dict[str, Any]]:
    """[{"key", "label", "used", "limit", "state": "ok"|"near"|"over"}] for
    store ``code``, read from the platform database only. Stub: none."""
    return []


__all__ = ("CUSTOM", "DEFAULT_PLAN", "PLAN_IDS", "PRO", "STARTER", "TRIAL", "limit_states", "limits_of", "plan_of")
