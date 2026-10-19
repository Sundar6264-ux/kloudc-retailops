"""Store codes (contract 4.1): normalising, the reserved list, database names."""

import re
from typing import Any, Optional

from app.platform.config import login_code, pget

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,23}$")
# Same trim as the M2 directory (ASCII whitespace only, like the Worker).
CODE_TRIM = " \t\n\r\x0b\x0c"

RESERVED_CODES = frozenset(
    {
        "PLATFORM",
        "MASTER",
        "ADMIN",
        "ROOT",
        "SYSTEM",
        "API",
        "APP",
        "WWW",
        "STATIC",
        "DIRECTORY",
        "SUPPORT",
        "HELP",
        "STOREOPS",
        "RETAILOPS",
        "KLOUDC",
        "TEST",
        "NULL",
        "NONE",
    }
)

STATUSES = ("pending", "provisioning", "active", "suspended", "failed", "archived")
POS_PROVIDERS = ("toast", "clover", "csv")


def normalize_code(raw: Any) -> Optional[str]:
    """Trim, upper-case, ``-`` -> ``_``; None when not a valid code shape."""
    if not isinstance(raw, str):
        return None
    code = raw.strip(CODE_TRIM).upper().replace("-", "_")
    return code if CODE_RE.fullmatch(code) else None


def is_reserved(code: str) -> bool:
    return code in RESERVED_CODES or code == login_code()


def db_name_for(code: str) -> str:
    """``STORE_DB_PREFIX`` + the code in lower case (max 63, PG's limit)."""
    prefix = str(pget("store_db_prefix") or "so_")
    name = (prefix + code.lower())[:63]
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"database name {name!r} is not a plain identifier")
    return name
