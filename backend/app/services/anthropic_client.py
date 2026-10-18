"""Shared Claude API client construction for every feature that calls it
(ask_bot.py, weekly_digest.py) - one place to check for a configured API key
so every feature fails the same clear way if it's missing, instead of each
reinventing the same check.
"""

import anthropic

from app.config import get_settings


class AnthropicNotConfigured(Exception):
    pass


def get_anthropic_client() -> anthropic.Anthropic:
    api_key = get_settings().anthropic_api_key
    if not api_key:
        raise AnthropicNotConfigured("ANTHROPIC_API_KEY isn't set in backend/.env.")
    return anthropic.Anthropic(api_key=api_key)
