"""Test helpers (docs/PLATFORM_CONTRACT.md 16): an in-memory registry and
settings source, so tests can run multi mode without the platform database.

    make_test_store("ALPHA", "postgresql+psycopg://storeops@127.0.0.1:5433/so_b_alpha")
    with as_store("ALPHA"):
        ...
"""

import re
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

from app.tenancy import engines as _engines
from app.tenancy import registry as _registry
from app.tenancy import settings as _settings
from app.tenancy.context import store_context
from app.tenancy.registry import StoreRef, normalize_store_code


class InMemoryRegistry:
    def __init__(self):
        self.stores: Dict[str, StoreRef] = {}
        self.lock = threading.Lock()

    def load(self, code: str) -> Optional[StoreRef]:
        return self.stores.get(normalize_store_code(code))

    def list(self, statuses: Iterable[str]) -> List[StoreRef]:
        statuses = tuple(statuses)
        return [s for s in self.stores.values() if s.status in statuses]

    def settings_version(self, code: str) -> int:
        ref = self.load(code)
        return ref.settings_version if ref else 0

    def put(self, ref: StoreRef) -> StoreRef:
        with self.lock:
            self.stores[ref.code] = ref
        _registry.invalidate_store(ref.code)
        return ref

    def update(self, code: str, **changes) -> StoreRef:
        ref = self.stores[normalize_store_code(code)].replace(**changes)
        return self.put(ref)


class InMemorySettingsSource:
    def __init__(self):
        self.settings: Dict[str, Dict[str, Any]] = {}
        self.credentials: Dict[str, Dict[str, Any]] = {}

    def store_settings(self, code: str) -> Dict[str, Any]:
        return dict(self.settings.get(normalize_store_code(code), {}))

    def credential_overrides(self, code: str) -> Dict[str, Any]:
        return dict(self.credentials.get(normalize_store_code(code), {}))


test_registry = InMemoryRegistry()
test_settings = InMemorySettingsSource()


def _db_name_from_url(url: str, code: str) -> str:
    tail = url.rsplit("/", 1)[-1].split("?", 1)[0]
    name = re.sub(r"[^a-z0-9_]", "_", tail.lower())[:63]
    return name or f"so_test_{code.lower()}"


def install() -> None:
    """Use the in-memory registry and settings source (multi mode tests)."""
    _registry.set_registry_source(test_registry)
    _settings.set_settings_source(test_settings)


def make_test_store(code: str, url: str, **fields) -> StoreRef:
    """Register store `code` whose database is `url` (in-memory registry,
    installed if needed). `fields` override StoreRef fields."""
    norm = normalize_store_code(code)
    ref = StoreRef(
        code=norm,
        name=fields.pop("name", norm.title()),
        status=fields.pop("status", "active"),
        hosting=fields.pop("hosting", "shared"),
        api_base_url=fields.pop("api_base_url", None),
        db_name=fields.pop("db_name", _db_name_from_url(url, norm)),
        db_host_ref=fields.pop("db_host_ref", None),
        timezone=fields.pop("timezone", "America/New_York"),
        pos_provider=fields.pop("pos_provider", "toast"),
        instance_id=fields.pop("instance_id", None),
        settings_version=fields.pop("settings_version", 1),
    )
    if fields:
        raise TypeError(f"unknown StoreRef fields: {sorted(fields)}")
    if _registry._source is not test_registry:
        install()
    _engines.set_url_override(norm, url)
    test_registry.put(ref)
    _settings.invalidate_store_settings(norm)
    return ref


def update_test_store(code: str, **changes) -> StoreRef:
    ref = test_registry.update(code, **changes)
    _settings.invalidate_store_settings(ref.code)
    return ref


def set_test_store_settings(code: str, values: Dict[str, Any], *, credentials: bool = False) -> None:
    """Per-store settings (or credential overrides) for `code`; bumps its
    settings_version like the platform does."""
    norm = normalize_store_code(code)
    target = test_settings.credentials if credentials else test_settings.settings
    target[norm] = dict(values)
    ref = test_registry.load(norm)
    if ref is not None:
        test_registry.put(ref.replace(settings_version=ref.settings_version + 1))
    _settings.invalidate_store_settings(norm)


def reset_test_stores() -> None:
    """Forget every test store, engine, cache and source."""
    from app.tenancy.cache import clear_tenant_caches

    for code in list(test_registry.stores):
        _engines.dispose_engine(code)
    test_registry.stores.clear()
    test_settings.settings.clear()
    test_settings.credentials.clear()
    _engines.clear_url_overrides()
    clear_tenant_caches()
    _registry.invalidate_store()
    _settings.invalidate_store_settings()


@contextmanager
def as_store(code: str) -> Iterator[None]:
    """Run the block as store `code` (= store_context, inactive allowed)."""
    with store_context(code, allow_inactive=True):
        yield


def stamp_store_database(url: str, code: str, *, create_schema: bool = False) -> str:
    """Write the store identity stamp into the database at `url` (and the
    whole store schema first when create_schema). Returns the instance id."""
    from sqlalchemy.orm import Session

    import app.models  # noqa: F401 - registers every table on Base
    from app.db import Base
    from app.store.identity import StoreIdentity, stamp_database

    engine = _engines.create_store_engine(url, tenant=False)
    try:
        if create_schema:
            Base.metadata.create_all(engine)
        else:
            StoreIdentity.__table__.create(engine, checkfirst=True)
        with Session(engine) as db:
            return stamp_database(db, normalize_store_code(code), "test")
    finally:
        engine.dispose()
