"""Session tokens bound to a store (docs/PLATFORM_CONTRACT.md 4.2).

A token is ``"<STORE_CODE>.<secret>"`` (a master's: ``"<PLATFORM_LOGIN_CODE>.<secret>"``);
databases keep only ``sha256(token)`` hex. The prefix only says WHERE to
look the session up - it is never trusted on its own: a forged prefix gives
a token that does not exist in that store's database.
"""

import hashlib
import secrets
from typing import Optional

from app.tenancy.registry import normalize_store_code


def make_token(scope_code: str) -> str:
    code = normalize_store_code(scope_code) or "_"
    return f"{code}.{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def token_scope_code(token: Optional[str]) -> Optional[str]:
    """The text before the first "." (upper case), or None (no prefix)."""
    if not token or "." not in token:
        return None
    prefix = token.split(".", 1)[0].strip()
    return prefix.upper() if prefix else None
