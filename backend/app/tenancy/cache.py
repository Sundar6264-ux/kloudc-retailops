"""Per-store caches and state (docs/PLATFORM_CONTRACT.md 6.5).

- ``tenant_cache(name, maxsize, ttl)``: a small LRU cache per store.
- ``tenant_state(name, factory)``: one mutable object per store (the old
  module-level ``_state`` dicts).
- ``tenant_local(name, factory)``: a module-level proxy for such an object -
  ``_cache: dict = tenant_local("x", dict)`` keeps every call site
  (``_cache[k]``, ``_cache.get``, ``_cache.clear()``...) unchanged while the
  data is per store.

Without a store (multi mode, no context) everything uses a platform-level
bucket (key None); store data is never reachable from there because store
sessions need a store.
"""

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Hashable, Iterator, Optional, TypeVar

from app.tenancy.context import current_store_code
from app.tenancy.registry import normalize_store_code

T = TypeVar("T")
_MISSING = object()


class _Bucket:
    __slots__ = ("lock", "data")

    def __init__(self):
        self.lock = threading.RLock()
        self.data: "OrderedDict[Hashable, tuple]" = OrderedDict()


class TenantCache:
    """An LRU (+ optional TTL) cache whose entries are per store."""

    def __init__(self, name: str, maxsize: int = 256, ttl: Optional[float] = None):
        self.name = name
        self.maxsize = max(1, int(maxsize))
        self.ttl = ttl
        self._buckets: Dict[Optional[str], _Bucket] = {}
        self._guard = threading.Lock()

    def _bucket(self, code: Optional[str] = _MISSING) -> _Bucket:  # type: ignore[assignment]
        key = current_store_code() if code is _MISSING else code
        b = self._buckets.get(key)
        if b is None:
            with self._guard:
                b = self._buckets.get(key)
                if b is None:
                    b = self._buckets[key] = _Bucket()
        return b

    @property
    def lock(self) -> "threading.RLock":
        """The current store's lock for this cache."""
        return self._bucket().lock

    def get(self, key: Hashable, default: Any = None) -> Any:
        b = self._bucket()
        with b.lock:
            entry = b.data.get(key, _MISSING)
            if entry is _MISSING:
                return default
            value, at = entry
            if self.ttl is not None and time.monotonic() - at > self.ttl:
                del b.data[key]
                return default
            b.data.move_to_end(key)
            return value

    def set(self, key: Hashable, value: Any) -> None:
        b = self._bucket()
        with b.lock:
            b.data[key] = (value, time.monotonic())
            b.data.move_to_end(key)
            while len(b.data) > self.maxsize:
                b.data.popitem(last=False)

    def pop(self, key: Hashable, default: Any = None) -> Any:
        b = self._bucket()
        with b.lock:
            entry = b.data.pop(key, _MISSING)
        return default if entry is _MISSING else entry[0]

    def get_or_set(self, key: Hashable, factory: Callable[[], Any]) -> Any:
        b = self._bucket()
        with b.lock:
            value = self.get(key, _MISSING)
            if value is _MISSING:
                value = factory()
                self.set(key, value)
            return value

    def __contains__(self, key: Hashable) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __len__(self) -> int:
        b = self._bucket()
        with b.lock:
            return len(b.data)

    def clear(self) -> None:
        """Current store only."""
        b = self._bucket()
        with b.lock:
            b.data.clear()

    def clear_store(self, code: Optional[str]) -> None:
        b = self._buckets.get(code)
        if b is not None:
            with b.lock:
                b.data.clear()

    def clear_all_stores(self) -> None:
        with self._guard:
            buckets = list(self._buckets.values())
        for b in buckets:
            with b.lock:
                b.data.clear()

    def stores(self) -> list:
        return [k for k, b in list(self._buckets.items()) if b.data]


_caches: Dict[str, TenantCache] = {}
_states: Dict[str, Dict[Optional[str], Any]] = {}
_factories: Dict[str, Callable[[], Any]] = {}
_registry_lock = threading.Lock()


def tenant_cache(name: str, *, maxsize: int = 256, ttl: Optional[float] = None) -> TenantCache:
    """The per-store cache called `name` (same name -> same object)."""
    with _registry_lock:
        cache = _caches.get(name)
        if cache is None:
            cache = _caches[name] = TenantCache(name, maxsize=maxsize, ttl=ttl)
        return cache


def tenant_state(name: str, factory: Callable[[], T], *, store: Any = _MISSING) -> T:
    """The current store's object called `name` (or store `store`'s), created
    with `factory()` on first use."""
    code = current_store_code() if store is _MISSING else (normalize_store_code(store) if store else None)
    per_store = _states.get(name)
    if per_store is not None:
        obj = per_store.get(code, _MISSING)
        if obj is not _MISSING:
            return obj
    with _registry_lock:
        per_store = _states.setdefault(name, {})
        _factories.setdefault(name, factory)
        obj = per_store.get(code, _MISSING)
        if obj is _MISSING:
            obj = per_store[code] = factory()
        return obj


def clear_tenant_caches(code: Optional[str] = None) -> None:
    """Drop every tenant cache entry and state object of store `code`
    (normalised), or of every store when None (tests). State objects are
    re-created by their factory on next use."""
    with _registry_lock:
        caches = list(_caches.values())
        states = list(_states.values())
    if code is None:
        for c in caches:
            c.clear_all_stores()
        with _registry_lock:
            for per_store in states:
                per_store.clear()
        return
    norm = normalize_store_code(code)
    for c in caches:
        c.clear_store(norm)
    with _registry_lock:
        for per_store in states:
            per_store.pop(norm, None)


class TenantLocal:
    """A module-level stand-in for a mutable object (dict, list, set...) whose
    real object is per store: attribute access, item access, iteration,
    ``len``, ``in`` and truthiness all go to the current store's object."""

    __slots__ = ("_name", "_factory")

    def __init__(self, name: str, factory: Callable[[], Any]):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_factory", factory)

    def current(self) -> Any:
        return tenant_state(self._name, self._factory)

    def of(self, store: Optional[str]) -> Any:
        """Store `store`'s object (None: the current store's)."""
        if store is None:
            return self.current()
        return tenant_state(self._name, self._factory, store=store)

    def __getattr__(self, attr: str) -> Any:
        return getattr(self.current(), attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        setattr(self.current(), attr, value)

    def __getitem__(self, key):
        return self.current()[key]

    def __setitem__(self, key, value) -> None:
        self.current()[key] = value

    def __delitem__(self, key) -> None:
        del self.current()[key]

    def __contains__(self, key) -> bool:
        return key in self.current()

    def __iter__(self) -> Iterator:
        return iter(self.current())

    def __len__(self) -> int:
        return len(self.current())

    def __bool__(self) -> bool:
        return bool(self.current())

    def __eq__(self, other) -> bool:
        return self.current() == other

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"<TenantLocal {self._name!r}: {self.current()!r}>"


def tenant_local(name: str, factory: Callable[[], Any]) -> Any:
    """A per-store module-level object (see TenantLocal). Typed Any so
    ``_cache: dict = tenant_local("x", dict)`` reads naturally."""
    return TenantLocal(name, factory)
