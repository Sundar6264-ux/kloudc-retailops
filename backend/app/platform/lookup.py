"""``GET /api/public/stores/{code}`` body (contract 13.4, "As built"
2026-10): only what a client needs to connect - code, api_base_url,
min_build and a neutral status (``app.directory.stores.connect_body``).

The lookup reveals nothing about whether a store exists: an unknown,
archived or not-yet-ready code gets the same 200, the same shape and the
shared service's own api_base_url, exactly like a real store on the shared
service. Names, logos, colours and the POS name come only after sign-in
(``/api/auth/me``, ``/api/app/branding`` with the session). A store on its
own dedicated server necessarily answers with its own address (accepted).
"""

from typing import Tuple

from app.directory.stores import ZERO_MIN_BUILD, compact_json, connect_body, normalize_code
from app.platform.config import platform_public_url

# Statuses whose own server address is given out (a dedicated store that
# is live or paused); every other status reads as an unknown code.
_LISTED = ("active", "suspended")


def lookup_body(raw_code: str) -> Tuple[int, bytes]:
    """(status, body): 200 with the body for every valid code, 400 for a
    code that can't be a Store ID."""
    from app.platform.sources import lookup_record

    code = normalize_code(raw_code)
    if code is None:
        return 400, b""
    shared = platform_public_url()
    # One registry read on every path (similar timing for known and unknown).
    store = lookup_record(code)
    own = (store.api_base_url or "").strip().rstrip("/") if store is not None and store.status in _LISTED else ""
    if own and own != shared:
        min_build = (("ios", int(store.min_build_ios or 0)), ("android", int(store.min_build_android or 0)))
        return 200, compact_json(connect_body(code, own, min_build))
    return 200, compact_json(connect_body(code, shared, ZERO_MIN_BUILD))
