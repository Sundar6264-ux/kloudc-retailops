"""RFC 6238 TOTP for master logins (contract 3.3, 4.5 step 3; optional).

30-second steps, 6 digits, SHA-1 (what authenticator apps expect); one step
of clock drift either way is accepted. The secret is stored encrypted
(``crypto.seal_text`` with ``crypto.totp_aad(username)``).

``verify_once`` is what sign-in and the step-up checks use: a code (a time
step) is accepted once per master and purpose, so a code someone saw or
phished cannot be used again inside its 90-second window (OWASP ASVS 2.8.4;
docs/SECURITY_REVIEW.md SR-03). The used steps are kept in this process:
the web server runs one process (deploy/pilot-mac, docker/entrypoint.sh).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from typing import Dict, Optional, Tuple
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


_used_lock = threading.Lock()
_used_steps: Dict[Tuple[str, str], int] = {}  # global-state: last TOTP step used per (master, purpose), process-wide


def matched_step(secret_b32: str, code: Optional[str], at: Optional[float] = None, drift: int = 1) -> Optional[int]:
    """The time step ``code`` belongs to (within ``drift``), or None."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    counter = int((at if at is not None else time.time()) // STEP)
    for d in range(-drift, drift + 1):
        if hmac.compare_digest(_code(secret_b32, counter + d), code):
            return counter + d
    return None


def verify_once(secret_b32: str, code: Optional[str], *, key: str, purpose: str = "login",
                at: Optional[float] = None, drift: int = 1) -> bool:
    """``verify`` that accepts each time step once per (``key``, ``purpose``):
    the same code - or an older one - is refused after it has been used."""
    step = matched_step(secret_b32, code, at=at, drift=drift)
    if step is None:
        return False
    slot = (str(key).lower(), purpose)
    now_step = int((at if at is not None else time.time()) // STEP)
    with _used_lock:
        last = _used_steps.get(slot)
        if last is not None and step <= last:
            return False
        _used_steps[slot] = step
        if len(_used_steps) > 1000:  # forget steps that can no longer match
            for k, v in list(_used_steps.items()):
                if v < now_step - drift - 1:
                    del _used_steps[k]
    return True


def reset_used() -> None:
    """Tests."""
    with _used_lock:
        _used_steps.clear()


def otpauth_uri(secret_b32: str, username: str, issuer: str = "KloudC RetailOps") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"
