"""RFC 6238 TOTP for master logins (contract 3.3, 4.5 step 3; optional).

30-second steps, 6 digits, SHA-1 (what authenticator apps expect); one step
of clock drift either way is accepted. The secret is stored encrypted
(``crypto.seal_text`` with ``crypto.totp_aad(username)``).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**DIGITS)).zfill(DIGITS)


def now_code(secret_b32: str, at: Optional[float] = None) -> str:
    return _code(secret_b32, int((at if at is not None else time.time()) // STEP))


def verify(secret_b32: str, code: Optional[str], at: Optional[float] = None, drift: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    counter = int((at if at is not None else time.time()) // STEP)
    return any(hmac.compare_digest(_code(secret_b32, counter + d), code) for d in range(-drift, drift + 1))


def otpauth_uri(secret_b32: str, username: str, issuer: str = "StoreOps") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"
