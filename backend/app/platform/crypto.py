"""Credential encryption (contract 8.4; owner C).

AES-256-GCM with a random 12-byte nonce per write and associated data that
binds the ciphertext to its store and kind (``storeops:<CODE>:<kind>:v1``),
so a row copied to another store does not decrypt.

Key ring: ``STOREOPS_SECRET_KEYS="k2:<base64 32 bytes>,k1:<...>"`` - the first
key encrypts, every listed key decrypts (rows carry their ``key_id``). The
legacy single ``STOREOPS_SECRET_KEY`` is the ring ``{"k1": ...}``. Only with
``APP_ENV=development`` and no ring set is a dev ring generated into
``STOREOPS_SECRET_KEY_FILE`` (default ``backend/.secrets/storeops_keyring``,
mode 0600, the folder gitignored). Losing the ring loses every saved
credential: production keeps it in the server's secret store.
"""

import base64
import datetime as dt
import os
import pathlib
import secrets
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.platform.config import pget

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
AAD_VERSION = "v1"


class KeyRingError(RuntimeError):
    """No usable key ring (production must set STOREOPS_SECRET_KEYS)."""


class DecryptError(RuntimeError):
    """The ciphertext does not open with this key ring and binding."""


@dataclass(frozen=True)
class KeyRing:
    keys: Tuple[Tuple[str, bytes], ...]  # (key_id, 32-byte key), active first
    source: str  # "env", "legacy", "dev-file"

    @property
    def active_id(self) -> str:
        return self.keys[0][0]

    def key(self, key_id: str) -> Optional[bytes]:
        for kid, k in self.keys:
            if kid == key_id:
                return k
        return None

    def ids(self) -> List[str]:
        return [kid for kid, _ in self.keys]


def _b64d(value: str) -> bytes:
    value = value.strip()
    pad = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + pad) if ("-" in value or "_" in value) else base64.b64decode(value + pad)
    except Exception as exc:  # noqa: BLE001
        raise KeyRingError("a key in the ring is not valid base64") from exc


def parse_ring(raw: str) -> List[Tuple[str, bytes]]:
    """``"k2:<b64>,k1:<b64>"`` -> [(id, key)]; refuses bad ids, sizes, duplicates."""
    out: List[Tuple[str, bytes]] = []
    seen = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        kid, sep, b64 = part.partition(":")
        kid = kid.strip()
        if not sep or not kid or len(kid) > 32 or not kid.replace("_", "").replace("-", "").isalnum():
            raise KeyRingError("each key must look like <id>:<base64 of 32 bytes>")
        key = _b64d(b64)
        if len(key) != 32:
            raise KeyRingError(f"key {kid} is {len(key)} bytes; AES-256 needs 32")
        if kid in seen:
            raise KeyRingError(f"key id {kid} appears twice")
        seen.add(kid)
        out.append((kid, key))
    return out


def gen_key(key_id: Optional[str] = None) -> str:
    """A new ``<id>:<base64>`` entry to put first in STOREOPS_SECRET_KEYS."""
    kid = key_id or ("k" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M"))
    return f"{kid}:{base64.b64encode(secrets.token_bytes(32)).decode('ascii')}"


def _dev_file() -> pathlib.Path:
    raw = str(pget("storeops_secret_key_file") or ".secrets/storeops_keyring")
    p = pathlib.Path(raw).expanduser()
    return p if p.is_absolute() else (BACKEND_DIR / p)


def _load_or_create_dev_ring() -> str:
    path = _dev_file()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Belt and braces next to the repository .gitignore: the folder ignores
    # itself, so the dev ring can never be committed.
    gi = path.parent / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    value = gen_key("dev1")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")
    return value


_lock = threading.Lock()
_ring: Optional[KeyRing] = None


def load_keyring() -> KeyRing:
    """Build the ring from settings/env (not cached)."""
    raw = str(pget("storeops_secret_keys") or "").strip()
    if raw:
        keys = parse_ring(raw)
        if keys:
            return KeyRing(tuple(keys), "env")
    legacy = str(pget("storeops_secret_key") or "").strip()
    if legacy:
        key = _b64d(legacy)
        if len(key) != 32:
            raise KeyRingError("STOREOPS_SECRET_KEY must be base64 of 32 bytes")
        return KeyRing((("k1", key),), "legacy")
    if str(pget("app_env") or "").strip().lower() == "development":
        return KeyRing(tuple(parse_ring(_load_or_create_dev_ring())), "dev-file")
    raise KeyRingError(
        "STOREOPS_SECRET_KEYS is not set. Generate one with `python -m app.platform gen-key` and keep it in the "
        "server's secret store (APP_ENV=development makes a local dev key ring instead)."
    )


def keyring() -> KeyRing:
    global _ring
    ring = _ring
    if ring is not None:
        return ring
    with _lock:
        if _ring is None:
            _ring = load_keyring()
        return _ring


def reset_keyring() -> None:
    """Forget the cached ring (tests, rotate-keys)."""
    global _ring
    with _lock:
        _ring = None


def aad_for(store_code: str, kind: str) -> bytes:
    return f"storeops:{store_code}:{kind}:{AAD_VERSION}".encode("utf-8")


def totp_aad(username: str) -> bytes:
    return f"storeops:platform:totp:{username.lower()}".encode("utf-8")


def encrypt(plaintext: bytes, aad: bytes, ring: Optional[KeyRing] = None) -> Dict[str, str]:
    """-> {"key_id", "nonce", "ciphertext"} (base64 text)."""
    ring = ring or keyring()
    kid, key = ring.keys[0]
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {
        "key_id": kid,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ct).decode("ascii"),
    }


def decrypt(key_id: str, nonce_b64: str, ciphertext_b64: str, aad: bytes, ring: Optional[KeyRing] = None) -> bytes:
    ring = ring or keyring()
    key = ring.key(key_id)
    if key is None:
        raise DecryptError(f"key {key_id} is not in the key ring")
    try:
        return AESGCM(key).decrypt(base64.b64decode(nonce_b64), base64.b64decode(ciphertext_b64), aad)
    except (InvalidTag, ValueError) as exc:
        raise DecryptError("ciphertext does not open (wrong key or wrong store/kind binding)") from exc


def seal_text(value: str, aad: bytes) -> str:
    """One string column (e.g. ``totp_secret_enc``): ``key_id.nonce.ct``."""
    sealed = encrypt(value.encode("utf-8"), aad)
    return f"{sealed['key_id']}.{sealed['nonce']}.{sealed['ciphertext']}"


def open_text(sealed: str, aad: bytes) -> str:
    try:
        kid, nonce, ct = sealed.split(".", 2)
    except ValueError as exc:
        raise DecryptError("not a sealed value") from exc
    return decrypt(kid, nonce, ct, aad).decode("utf-8")


def sealed_key_id(sealed: str) -> str:
    return sealed.split(".", 1)[0]
