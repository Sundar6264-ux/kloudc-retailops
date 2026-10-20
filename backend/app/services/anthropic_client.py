"""Shared Claude API client construction for every feature that calls it
(ask_bot.py, weekly_digest.py) - one place to check for a configured API key
so every feature fails the same clear way if it's missing, instead of each
reinventing the same check.
"""

from typing import Optional

import anthropic
import httpx

from app.config import get_settings


class AnthropicNotConfigured(Exception):
    pass


def get_anthropic_client() -> anthropic.Anthropic:
    api_key = get_settings().anthropic_api_key
    if not api_key:
        raise AnthropicNotConfigured("ANTHROPIC_API_KEY isn't set in backend/.env.")
    # Every request sent to the AI provider (retries included) is counted for
    # the store's usage (website ready: interface I3, ``usage.AI_CALLS``).
    return anthropic.Anthropic(
        api_key=api_key,
        http_client=anthropic.DefaultHttpxClient(event_hooks={"request": [record_ai_call]}),
    )


def record_ai_call(_request: Optional[httpx.Request] = None) -> None:
    """Count one AI request for the current store. Never raises."""
    try:
        from app.platform import usage

        usage.record(usage.AI_CALLS)
    except Exception:  # noqa: BLE001 - metering must never break an AI call
        pass
