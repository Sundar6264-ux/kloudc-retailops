"""The store registry: which stores exist, where their data lives
(docs/PLATFORM_CONTRACT.md 5, 16).

The registry itself is pluggable (``RegistrySource``):

- single mode (default): one ``StoreRef`` built from the process settings
  (STORE_CODE / DATABASE_URL / STORE_TIMEZONE / POS_PROVIDER). STORE_CODE
  blank (dev, tests) gives the placeholder code ``"_"``.
- multi mode: the platform database (``app/platform/sources.py``, track C)
  calls ``set_registry_source`` in the lifespan; tests use
  ``app.tenancy.testing`` (an in-memory source).

``get_store`` caches each answer for ``REGISTRY_CACHE_SECONDS``;
``invalidate_store`` (called by ``on_store_changed``) drops it at once.
"""

import dataclasses
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

from app.tenancy.errors import UnknownStore
from app.tenancy.mode import is_multi_tenant

logger = logging.getLogger(__name__)

STORE_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,23}$")
# The single-mode placeholder when STORE_CODE is blank (dev / tests).
BLANK_STORE_CODE = "_"
STORE_STATUSES = ("pending", "provisioning", "active", "suspended", "failed", "archived")
# Contract 4.1. PLATFORM_LOGIN_CODE is added by reserved_codes().
RESERVED_CODES = frozenset(
    {
        "PLATFORM", "MASTER", "ADMIN", "ROOT", "SYSTEM", "API", "APP", "WWW", "STATIC", "DIRECTORY",
        "SUPPORT", "HELP", "STOREOPS", "KLOUDC", "TEST", "NULL", "NONE",
    }
)
REGISTRY_CACHE_SECONDS = 10.0
_DB_NAME_RE = re.compile(r"^[a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class StoreRef:
    code: str  # upper case
    name: str
    status: str  # pending|provisioning|active|suspended|failed|archived
    hosting: str  # shared|dedicated
    api_base_url: Optional[str]
    db_name: str
    db_host_ref: Optional[str]
    timezone: str
    pos_provider: str
    instance_id: Optional[str]
    settings_version: int

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def replace(self, **changes) -> "StoreRef":
        return dataclasses.replace(self, **changes)


@runtime_checkable
class RegistrySource(Protocol):
    def load(self, code: str) -> Optional[StoreRef]: ...

    def list(self, statuses: Iterable[str]) -> List[StoreRef]: ...

    def settings_version(self, code: str) -> int: ...


def normalize_store_code(raw: Optional[str]) -> str:
    """Trim ASCII whitespace, upper-case, "-" -> "_" (contract 4.1). Does not
    validate; see is_valid_store_code."""
    return (raw or "").strip().upper().replace("-", "_")


def is_valid_store_code(code: str) -> bool:
    return bool(STORE_CODE_RE.match(code or ""))


def reserved_codes() -> frozenset:
    from app.config import process_settings

    return RESERVED_CODES | {process_settings().platform_login_code}


def valid_db_name(name: str) -> bool:
    return bool(_DB_NAME_RE.match(name or ""))


# --- single mode ---------------------------------------------------------------------


def single_store_code() -> str:
    from app.config import process_settings

    return (process_settings().store_code or "").strip().upper() or BLANK_STORE_CODE


def single_store_ref() -> StoreRef:
    """The one store of a single-mode process, from the process settings
    (read at call time, so tests that patch the settings see it)."""
    from app.config import process_settings

    s = process_settings()
    url = s.database_url or ""
    db_name = url.rsplit("/", 1)[-1] if "/" in url else url
    return StoreRef(
        code=single_store_code(),
        name=(s.store_name or "").strip() or s.product_name,
        status="active",
        hosting="shared",
        api_base_url=None,
        db_name=db_name,
        db_host_ref=None,
        timezone=s.store_timezone or s.toast_timezone,
        pos_provider=(s.pos_provider or "").strip().lower() or "toast",
        instance_id=None,
        settings_version=1,
    )


class SingleModeSource:
    """The single-mode registry: exactly one store."""

    def load(self, code: str) -> Optional[StoreRef]:
        ref = single_store_ref()
        code = normalize_store_code(code) or BLANK_STORE_CODE
        return ref if code == ref.code else None

    def list(self, statuses: Iterable[str]) -> List[StoreRef]:
        ref = single_store_ref()
        return [ref] if ref.status in tuple(statuses) else []

    def settings_version(self, code: str) -> int:
        return 1


# --- the active source and its cache ----------------------------------------------

_source: Optional[RegistrySource] = None
_cache: Dict[str, Tuple[Optional[StoreRef], float]] = {}
_cache_lock = threading.Lock()


def set_registry_source(source: Optional[RegistrySource]) -> None:
    """Install the multi-mode registry (C's platform source, or a test
    source). None removes it."""
    global _source
    with _cache_lock:
        _source = source
        _cache.clear()


def registry_source() -> RegistrySource:
    if not is_multi_tenant():
        return SingleModeSource()
    if _source is None:
        raise UnknownStore(None)
    return _source


def invalidate_store(code: Optional[str] = None) -> None:
    """Forget the cached registry row of `code` (all codes when None)."""
    with _cache_lock:
        if code is None:
            _cache.clear()
        else:
            _cache.pop(normalize_store_code(code), None)


def get_store(code: str, *, refresh: bool = False) -> StoreRef:
    """The store with this code. Raises UnknownStore. Multi mode: cached for
    REGISTRY_CACHE_SECONDS (an unknown code too, so a flood of forged tokens
    does not reach the platform database)."""
    norm = normalize_store_code(code)
    if not is_multi_tenant():
        ref = SingleModeSource().load(norm)
        if ref is None:
            raise UnknownStore(norm)
        return ref
    if not is_valid_store_code(norm):
        raise UnknownStore(norm)
    now = time.monotonic()
    if not refresh:
        with _cache_lock:
            hit = _cache.get(norm)
        if hit is not None and now - hit[1] < REGISTRY_CACHE_SECONDS:
            if hit[0] is None:
                raise UnknownStore(norm)
            return hit[0]
    ref = registry_source().load(norm)
    with _cache_lock:
        _cache[norm] = (ref, now)
    if ref is None:
        raise UnknownStore(norm)
    return ref


def find_store(code: Optional[str]) -> Optional[StoreRef]:
    """get_store, or None for an unknown / invalid code."""
    try:
        return get_store(code or "")
    except UnknownStore:
        return None


def list_stores(statuses: Iterable[str] = ("active",), hosting: Optional[str] = "shared") -> List[StoreRef]:
    """Stores with one of `statuses` (and `hosting`, None = any), not cached."""
    statuses = tuple(statuses)
    refs = registry_source().list(statuses)
    out = [r for r in refs if r.status in statuses and (hosting is None or r.hosting == hosting)]
    with _cache_lock:
        now = time.monotonic()
        for r in out:
            _cache[r.code] = (r, now)
    return sorted(out, key=lambda r: r.code)


def settings_version(code: str) -> int:
    return registry_source().settings_version(normalize_store_code(code))
