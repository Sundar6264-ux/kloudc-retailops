"""Tenancy events for the audit log (track C subscribes; docs/PLATFORM_CONTRACT.md 3.6).

``emit("store.identity_mismatch", store_code=..., details={...})`` calls every
listener; a failing listener is logged and never breaks the caller.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

Listener = Callable[[str, Optional[str], Dict[str, Any]], None]
_listeners: List[Listener] = []


def add_listener(fn: Listener) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Listener) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def emit(action: str, store_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    for fn in list(_listeners):
        try:
            fn(action, store_code, dict(details or {}))
        except Exception:  # noqa: BLE001 - an audit hook must never break the caller
            logger.exception("tenancy event listener failed for %s", action)
