"""Pilot mode: a store being onboarded next to the system it runs on today.

With PILOT_MODE on (a store setting: the master console's store settings,
``python -m app.platform pilot-mode CODE on``, or PILOT_MODE=true in a
single-store .env) the store keeps syncing from its POS and every page works,
but nothing is sent out from it: no WhatsApp message or media, no email (PO
emails, invoice emails) and no push notification. The live system keeps
doing the sending until the store switches over.

The block sits in the transports themselves (app/services/whapi_client.py,
toast_email.py, po_send.py, push.py), so every caller - a page, a scheduled
job, a retry - is covered. Each transport raises its own "not configured"
error with PILOT_NOT_SENT, which every caller already handles as "not sent,
nothing to retry" (a push is a dry run, a submission records the reason).

In multi mode a send with no store selected is refused the same way (fail
closed): without a store there is no pilot setting to read, and the
platform-level settings must never decide that a store may send.
"""

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CHANNELS = ("whatsapp", "email", "push")
PILOT_NOT_SENT = "Pilot mode is on for this store: nothing is sent ({channel})."
NO_STORE_NOT_SENT = "No store is selected: nothing is sent ({channel})."

_lock = threading.Lock()
_blocked: Dict[str, int] = {}


def pilot_mode(settings=None) -> bool:
    """The current store's PILOT_MODE (read with getattr: works on any
    Settings-like object)."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    return bool(getattr(settings, "pilot_mode", False))


def _store_code() -> str:
    try:
        from app.store.settings_view import store_code

        return store_code() or "-"
    except Exception:  # noqa: BLE001 - only for the log line
        return "-"


def no_store_selected() -> bool:
    """Multi mode with no store context (a raw thread, a platform-level
    caller): there is no store whose pilot setting could be read - the
    platform-level settings would say "not in pilot" - so nothing is sent
    (fail closed). Always False in single mode."""
    try:
        from app.tenancy.context import current_store
        from app.tenancy.mode import is_multi_tenant

        return is_multi_tenant() and current_store() is None
    except Exception:  # noqa: BLE001 - tenancy not importable: single-store behaviour
        return False


def sends_blocked() -> bool:
    """True while nothing may be sent (pilot mode, or no store in multi mode)."""
    return no_store_selected() or pilot_mode()


def _count(code: str, channel: str) -> None:
    with _lock:
        key = f"{code}:{channel}"
        _blocked[key] = _blocked.get(key, 0) + 1


def blocked(channel: str, what: str = "") -> Optional[str]:
    """None when the store may send on `channel`; else the reason (and the
    block is counted and logged). Call it before any outbound send."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    if no_store_selected():
        _count("-", channel)
        logger.error("Send refused: %s with no store selected%s (a send must run inside its store)",
                     channel, f" ({what})" if what else "")
        return NO_STORE_NOT_SENT.format(channel=channel)
    if not pilot_mode():
        return None
    code = _store_code()
    _count(code, channel)
    logger.info("Pilot mode: %s not sent for store %s%s", channel, code, f" ({what})" if what else "")
    return PILOT_NOT_SENT.format(channel=channel)


def blocked_counts() -> Dict[str, int]:
    """{"<STORE>:<channel>": sends blocked since start} (for the logs/tests)."""
    with _lock:
        return dict(_blocked)


def reset_counts() -> None:
    with _lock:
        _blocked.clear()
