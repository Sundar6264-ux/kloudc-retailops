"""The current store of a request, job or thread (docs/PLATFORM_CONTRACT.md 4.3, 6.6).

A ``contextvars.ContextVar`` holds the selected ``StoreRef``. Starlette
copies the context into the threadpool (sync endpoints, sync dependencies,
``BackgroundTasks``, ``run_in_threadpool``); raw threads must be started
with ``spawn_in_store`` so they keep it.

Single mode: there is always exactly one store, so ``current_store()``
returns it even when nothing was set.
"""

import contextvars
import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, TypeVar

from app.tenancy.errors import NoStoreContext, StoreMoved, StoreNotActive, UnknownStore
from app.tenancy.mode import is_multi_tenant
from app.tenancy.registry import StoreRef, get_store, normalize_store_code, single_store_ref

logger = logging.getLogger(__name__)

T = TypeVar("T")

_store_var: contextvars.ContextVar[Optional[StoreRef]] = contextvars.ContextVar("retailops_store", default=None)
# "store" | "platform" | "none" (only meaningful when no store is set).
_scope_var: contextvars.ContextVar[str] = contextvars.ContextVar("retailops_scope", default="none")
# The status of a store a request's token points at, when that store is not
# active (the auth dependency answers 403 store_suspended / store_not_ready).
_block_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("retailops_store_block", default=None)


def current_store() -> Optional[StoreRef]:
    ref = _store_var.get()
    if ref is not None:
        return ref
    if not is_multi_tenant():
        return single_store_ref()
    return None


def current_store_code() -> Optional[str]:
    ref = current_store()
    return ref.code if ref is not None else None


def require_store() -> StoreRef:
    ref = current_store()
    if ref is None:
        raise NoStoreContext()
    return ref


def current_scope() -> str:
    """"store" (a store is selected), "platform" (a master's session) or "none"."""
    if _store_var.get() is not None or not is_multi_tenant():
        return "store"
    return _scope_var.get()


def current_store_block() -> Optional[str]:
    """Status of the inactive store this request's token points at, or None."""
    return _block_var.get()


def _check_servable(ref: StoreRef, allow_inactive: bool, allow_remote: bool) -> None:
    if not allow_inactive and ref.status != "active":
        raise StoreNotActive(ref.code, ref.status)
    if not allow_remote:
        from app.tenancy.routing import node_serves

        if not node_serves(ref):
            raise StoreMoved(ref)


@contextmanager
def store_context(code: str, *, allow_inactive: bool = False, allow_remote: bool = False) -> Iterator[StoreRef]:
    """Select the store `code` for the block. Raises UnknownStore,
    StoreNotActive (unless allow_inactive) or StoreMoved (a dedicated store
    this node does not serve, unless allow_remote)."""
    ref = get_store(code)
    _check_servable(ref, allow_inactive, allow_remote)
    with _use_store(ref):
        yield ref


@contextmanager
def _use_store(ref: StoreRef) -> Iterator[StoreRef]:
    tok_store = _store_var.set(ref)
    tok_scope = _scope_var.set("store")
    tok_block = _block_var.set(None)
    try:
        yield ref
    finally:
        _block_var.reset(tok_block)
        _scope_var.reset(tok_scope)
        _store_var.reset(tok_store)


@contextmanager
def platform_scope() -> Iterator[None]:
    """A master's request: no store, scope "platform"."""
    tok_store = _store_var.set(None)
    tok_scope = _scope_var.set("platform")
    try:
        yield
    finally:
        _scope_var.reset(tok_scope)
        _store_var.reset(tok_store)


@contextmanager
def blocked_store(status: str) -> Iterator[None]:
    """The request's token points at a store that is not active: no store
    context, the status kept for the auth dependency (contract 4.3)."""
    tok_store = _store_var.set(None)
    tok_block = _block_var.set(status)
    try:
        yield
    finally:
        _block_var.reset(tok_block)
        _store_var.reset(tok_store)


@contextmanager
def no_store() -> Iterator[None]:
    """Clear any store selection for the block (platform-level work)."""
    tok_store = _store_var.set(None)
    tok_scope = _scope_var.set("none")
    try:
        yield
    finally:
        _scope_var.reset(tok_scope)
        _store_var.reset(tok_store)


def run_in_store(code: str, fn: Callable[..., T], *args, **kwargs) -> T:
    with store_context(code):
        return fn(*args, **kwargs)


def spawn_in_store(fn: Callable[..., Any], *args, name: str = "", **kwargs) -> threading.Thread:
    """Start a daemon thread running ``fn(*args, **kwargs)`` inside a copy of
    the CURRENT context (so it keeps the caller's store). Returns the started
    thread."""
    ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=ctx.run,
        args=(fn, *args),
        kwargs=kwargs,
        name=name or getattr(fn, "__name__", "retailops-thread"),
        daemon=True,
    )
    thread.start()
    return thread


def store_code_of(code: Optional[str]) -> Optional[str]:
    """`code` normalised, or the current store's code when None."""
    if code is not None:
        return normalize_store_code(code)
    return current_store_code()


__all__ = [
    "current_store",
    "current_store_code",
    "require_store",
    "current_scope",
    "current_store_block",
    "store_context",
    "platform_scope",
    "blocked_store",
    "no_store",
    "run_in_store",
    "spawn_in_store",
    "store_code_of",
    "UnknownStore",
]
