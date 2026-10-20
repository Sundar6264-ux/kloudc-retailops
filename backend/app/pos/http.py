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


# --- usage metering (website-ready C7, interface I3) ----------------------------------------


def record_pos_call(_request: Optional[httpx.Request] = None) -> None:
    """Count one HTTP request sent to a POS API for the current store
    (``usage.POS_API_CALLS``). Cheap, never raises, no-op without a store."""
    try:
        from app.platform import usage

        usage.record(usage.POS_API_CALLS)
    except Exception:  # noqa: BLE001 - metering must never break a POS call
        pass


def metering_hooks() -> dict:
    """``httpx.Client(event_hooks=metering_hooks())``: every request the
    client sends (retries and logins included) is counted once."""
    return {"request": [record_pos_call]}
