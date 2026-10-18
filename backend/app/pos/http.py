"""Shared HTTP helpers for POS adapters (multi-store plan 4.2).

M1 keeps each adapter's own request loop exactly as it was; what is shared
so far is the "how long to wait after a 429" rule, so a second POS (Clover
sends Retry-After) does not grow its own copy. Re-login on 401 stays in each
adapter because the login call differs per POS.
"""

import time
from typing import Optional

import httpx


def retry_wait_seconds(
    resp: httpx.Response,
    attempt: int,
    max_wait: float,
    reset_header: Optional[str] = None,
) -> float:
    """Seconds to wait before retrying a rate-limited request:

    1. ``Retry-After`` (seconds), if the POS sent a number;
    2. else ``reset_header`` (an epoch time the limit resets at), at least 1 s;
    3. else 2 s, 4 s, 6 s ... by attempt.

    Always capped at ``max_wait``.
    """
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), max_wait)
        except ValueError:
            pass
    if reset_header:
        reset = resp.headers.get(reset_header)
        if reset:
            try:
                return min(max(float(reset) - time.time(), 1.0), max_wait)
            except ValueError:
                pass
    return min(2.0 * (attempt + 1), max_wait)
