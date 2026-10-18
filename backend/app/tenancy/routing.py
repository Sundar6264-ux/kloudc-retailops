"""Which stores this server serves (docs/PLATFORM_CONTRACT.md 12).

A store normally lives on the shared service. With master approval a store
can be moved to its own (dedicated) server; the shared service then answers
409 ``store_moved`` for it and the dedicated server serves only that store.
"""

import logging
from typing import FrozenSet

from app.tenancy.registry import StoreRef, normalize_store_code

logger = logging.getLogger(__name__)


def dedicated_store_codes() -> FrozenSet[str]:
    """DEDICATED_STORE_CODES: set only on a dedicated server."""
    from app.config import process_settings

    raw = process_settings().dedicated_store_codes or ""
    return frozenset(normalize_store_code(c) for c in raw.split(",") if c.strip())


def dedicated_hosting_enabled() -> bool:
    from app.config import process_settings

    return bool(process_settings().dedicated_hosting_enabled)


def node_serves(store: StoreRef) -> bool:
    """True when this server may serve `store`.

    - a dedicated server (DEDICATED_STORE_CODES set) serves only the codes
      listed there - always, so a misconfigured one never serves the rest;
    - the shared service serves every shared store; a store marked
      ``dedicated`` only while DEDICATED_HOSTING_ENABLED is on (the flag off
      means no store can be dedicated: such a row is treated as shared).
    """
    own = dedicated_store_codes()
    if own:
        return store.code in own
    # --- DEDICATED HOSTING (owner approval required; DEDICATED_HOSTING_ENABLED) ---
    # See docs/DEDICATED_HOSTING_RUNBOOK.md. Off by default. With the flag on, a
    # store a master moved to its own server (stores.hosting = 'dedicated',
    # set by the approved hosting request's `complete` step) is no longer
    # served here: requests get 409 store_moved with its api_base_url.
    if store.hosting == "dedicated":
        if dedicated_hosting_enabled():
            return False
        logger.warning(
            "Store %s is marked dedicated but DEDICATED_HOSTING_ENABLED is off - served here as shared", store.code
        )
    # --- end DEDICATED HOSTING ---
    return True


def moved_api_base_url(store: StoreRef) -> str:
    """Where clients should go for `store`: its own server when it has one,
    else the shared service's public URL."""
    from app.config import platform_public_url

    if dedicated_store_codes() and store.code not in dedicated_store_codes():
        return platform_public_url()
    return (store.api_base_url or "").rstrip("/") or platform_public_url()


def store_moved_body(store: StoreRef) -> dict:
    return {
        "error": "store_moved",
        "message": "This store is served from another address. Sign in again there.",
        "store_code": store.code,
        "api_base_url": moved_api_base_url(store),
    }


def store_moved_response(store: StoreRef):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409, content=store_moved_body(store))
