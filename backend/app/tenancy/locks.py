"""Per-store locks (docs/PLATFORM_CONTRACT.md 6.4).

``keyed_lock(name)`` returns the lock for (name, current store): two stores
never wait on each other, one store's work is still serialised. A
module-level lock that used to be ``threading.Lock()`` becomes
``tenant_lock(name)``: a lazy proxy that resolves the current store's lock
each time it is acquired, so call sites (``with _lock:``,
``_lock.acquire(blocking=False)`` / ``release()``) do not change.

``advisory_lock(db, name)`` additionally serialises across processes on
PostgreSQL (``pg_advisory_xact_lock``, held until the transaction ends);
on SQLite it holds ``keyed_lock(name)`` for the block.
"""

import hashlib
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple, Union

from app.tenancy.context import store_code_of

_Lockish = Union["threading.Lock", "threading.RLock"]
_locks: Dict[Tuple[str, Optional[str], bool], _Lockish] = {}
_guard = threading.Lock()


def keyed_lock(name: str, *, store: Optional[str] = None, reentrant: bool = False):
    """The lock for (`name`, `store` or the current store). Same arguments ->
    the same lock object. Without a store (multi mode, no context) the
    platform-level lock of that name."""
    key = (name, store_code_of(store), bool(reentrant))
    lock = _locks.get(key)
    if lock is None:
        with _guard:
            lock = _locks.get(key)
            if lock is None:
                lock = threading.RLock() if reentrant else threading.Lock()
                _locks[key] = lock
    return lock


class TenantLock:
    """A module-level lock whose real lock is per store (see module doc)."""

    def __init__(self, name: str, *, reentrant: bool = False):
        self.name = name
        self.reentrant = reentrant
        self._held = threading.local()

    def _stack(self) -> List:
        stack = getattr(self._held, "stack", None)
        if stack is None:
            stack = self._held.stack = []
        return stack

    def current(self):
        """The real lock for the current store."""
        return keyed_lock(self.name, reentrant=self.reentrant)

    def of(self, store: Optional[str]):
        """The real lock of store `store` (None: the current store's)."""
        return keyed_lock(self.name, store=store, reentrant=self.reentrant)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        lock = self.current()
        ok = lock.acquire(blocking, timeout)
        if ok:
            self._stack().append(lock)
        return ok

    def release(self) -> None:
        """Releases what this thread acquired; a plain (non-reentrant) lock
        may also be released by another thread, as with threading.Lock -
        that thread must be in the same store (spawn_in_store)."""
        stack = self._stack()
        if stack:
            stack.pop().release()
            return
        self.current().release()

    def locked(self) -> bool:
        lock = self.current()
        if hasattr(lock, "locked"):
            return lock.locked()
        if lock.acquire(blocking=False):  # RLock without locked() (Python < 3.13)
            lock.release()
            return False
        return True

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()

    def __repr__(self) -> str:
        return f"<TenantLock {self.name!r} reentrant={self.reentrant}>"


def tenant_lock(name: str, *, reentrant: bool = False) -> TenantLock:
    """A module-level per-store lock (drop-in for ``threading.Lock()`` /
    ``threading.RLock()``)."""
    return TenantLock(name, reentrant=reentrant)


def advisory_key(name: str, store: Optional[str] = None) -> int:
    """A signed 64-bit key for pg_advisory_*lock, stable across processes."""
    digest = hashlib.sha256(f"storeops:{store_code_of(store) or '-'}:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _is_postgres(db) -> bool:
    try:
        bind = db.get_bind()
    except Exception:  # noqa: BLE001 - an unbound session: not PostgreSQL
        return False
    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


@contextmanager
def advisory_lock(db, name: str) -> Iterator[None]:
    """Serialise `name` for the current store across threads AND processes.
    PostgreSQL: ``pg_advisory_xact_lock`` inside `db`'s transaction (released
    at commit / rollback, not at the end of the block) plus the in-process
    keyed lock for the block. SQLite: the keyed lock only."""
    lock = keyed_lock(f"advisory:{name}", reentrant=True)  # nested blocks (po_cart) are fine
    with lock:
        if _is_postgres(db):
            from sqlalchemy import text

            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": advisory_key(name)})
        yield


def reset_locks_for_tests() -> None:
    with _guard:
        _locks.clear()
