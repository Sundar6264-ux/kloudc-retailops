"""Usage metering per store (docs/WEBSITE_READY_CONTRACT.md, interface I3).

INTERFACE STUB landed with the website-ready contract. Track B owns this
module and replaces the bodies (buffered counters flushed to the platform
database, nightly gauges, ``snapshot``). Track C (storage bytes, POS calls
through shared HTTP) and track D (Clover API calls) call ``record`` only.

Rules for callers:
- ``record`` and ``touch_activity`` are cheap and never raise, never open a
  database connection on the caller's thread, and are safe from any thread.
- Without a store (``code=None`` and no store context) they do nothing.
- Counters add up per store and UTC day; gauges are measured by B's nightly
  job, not recorded by callers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Counters: callers add to them.
ORDERS_SYNCED = "orders_synced"  # order lines written by a POS sales sync
POS_API_CALLS = "pos_api_calls"  # HTTP requests sent to a POS API (any POS)
AI_CALLS = "ai_calls"  # requests sent to the AI provider
COUNTERS = (ORDERS_SYNCED, POS_API_CALLS, AI_CALLS)

# Gauges: measured by track B's usage job (callers never record these).
USERS = "users"  # active store logins
ITEMS = "items"  # items in the latest catalog snapshot
STORAGE_BYTES = "storage_bytes"  # app.storage bytes_used() of the store
DATABASE_BYTES = "database_bytes"  # pg_database_size of the store database
GAUGES = (USERS, ITEMS, STORAGE_BYTES, DATABASE_BYTES)

LAST_ACTIVITY_AT = "last_activity_at"  # newest signed-in request (UTC ISO time)

METRICS = COUNTERS + GAUGES + (LAST_ACTIVITY_AT,)


def record(metric: str, n: int = 1, *, code: Optional[str] = None) -> None:
    """Add ``n`` to counter ``metric`` of store ``code`` (default: the
    current store) for today (UTC). Stub: does nothing."""
    return None


def touch_activity(*, code: Optional[str] = None) -> None:
    """Note that someone used store ``code`` now (rate-limited inside: at
    most one write per store per minute). Stub: does nothing."""
    return None


def snapshot(code: str) -> Dict[str, Any]:
    """{"metrics": {metric: value}, "days": [{"day", metric: value...}],
    "updated_at"} for one store, read from the platform database only.
    Stub: empty."""
    return {"metrics": {}, "days": [], "updated_at": None}


def start() -> None:
    """Start the flush thread (lifespan). Stub: nothing to start."""
    return None


def stop() -> None:
    """Flush what is buffered and stop (lifespan shutdown). Stub: nothing."""
    return None


__all__ = (
    "AI_CALLS", "COUNTERS", "DATABASE_BYTES", "GAUGES", "ITEMS", "LAST_ACTIVITY_AT", "METRICS",
    "ORDERS_SYNCED", "POS_API_CALLS", "STORAGE_BYTES", "USERS",
    "record", "snapshot", "start", "stop", "touch_activity",
)
