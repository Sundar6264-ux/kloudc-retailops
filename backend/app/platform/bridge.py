"""The platform package's only door into track B's ``app.tenancy`` (contract
section 16) and the store database URLs.

Every call is a lazy import, so the platform package imports cleanly in
``single`` mode and on a base where ``app.tenancy`` is not merged yet; the
multi-mode paths need it and say so (``TenancyMissing``).
"""

import json
import logging
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator, Optional

from app.platform.config import pget

logger = logging.getLogger(__name__)


class TenancyMissing(RuntimeError):
    """``app.tenancy`` (track B) is needed for this multi-mode path."""


def tenancy(required: bool = False):
    try:
        import app.tenancy as t  # noqa: PLC0415

        return t
    except ImportError:
        if required:
            raise TenancyMissing("app.tenancy (track B) is not installed; TENANCY_MODE=multi needs it") from None
        return None


# --- store database URLs (contract 5.1) ---------------------------------------


class StoreUrlError(RuntimeError):
    pass


def _db_hosts() -> dict:
    raw = str(pget("store_db_hosts") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise StoreUrlError("STORE_DB_HOSTS is not a JSON object") from None
    if not isinstance(data, dict):
        raise StoreUrlError("STORE_DB_HOSTS is not a JSON object")
    return data


def store_url(db_name: str, db_host_ref: Optional[str] = None) -> str:
    """The SQLAlchemy URL of one store database (template or a dedicated host)."""
    if db_host_ref:
        template = _db_hosts().get(db_host_ref)
        if not template:
            raise StoreUrlError(f"STORE_DB_HOSTS has no entry {db_host_ref!r}")
    else:
        template = str(pget("store_database_url_template") or "").strip()
        if not template:
            raise StoreUrlError("STORE_DATABASE_URL_TEMPLATE is not set")
    if "{db_name}" not in template:
        raise StoreUrlError("the store database URL template must contain {db_name}")
    return template.replace("{db_name}", db_name)


def admin_url() -> str:
    """Where CREATE DATABASE runs: PG_ADMIN_URL, else the template on db 'postgres'."""
    raw = str(pget("pg_admin_url") or "").strip()
    return raw or store_url("postgres")


# --- context / caches / jobs (no-ops without track B) -----------------------


@contextmanager
def store_context(code: str, *, allow_inactive: bool = True) -> Iterator[Any]:
    t = tenancy()
    if t is None:
        with nullcontext():
            yield None
        return
    with t.store_context(code, allow_inactive=allow_inactive) as ref:
        yield ref


def current_store_code() -> Optional[str]:
    t = tenancy()
    if t is None:
        from app.store.settings_view import store_code

        return store_code() or None
    return t.current_store_code()


def is_multi_tenant() -> bool:
    t = tenancy()
    if t is not None:
        return bool(t.is_multi_tenant())
    from app.platform.config import is_multi

    return is_multi()


def store_changed(code: str) -> None:
    """After any registry / settings / credential change of one store: drop
    its cached settings and registry entry, tell the scheduler."""
    t = tenancy()
    if t is None:
        return
    for name, args in (
        ("invalidate_store_settings", (code,)),
        ("on_store_changed", (code,)),
    ):
        fn = getattr(t, name, None)
        if fn is None:
            continue
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a cache refresh must not undo the write
            logger.exception("tenancy.%s(%s) failed", name, code)
    try:
        t.get_store(code, refresh=True)
    except Exception:  # noqa: BLE001 - unknown/archived is fine here
        pass


def store_stopped(code: str) -> None:
    """Suspend / archive: also close its engine and clear its caches."""
    store_changed(code)
    t = tenancy()
    if t is None:
        return
    for name in ("clear_tenant_caches", "dispose_engine"):
        fn = getattr(t, name, None)
        if fn is not None:
            try:
                fn(code)
            except Exception:  # noqa: BLE001
                logger.exception("tenancy.%s(%s) failed", name, code)


@contextmanager
def tenant_session(code: str):
    """A session on one store's database (B's registry engine)."""
    t = tenancy(required=True)
    with t.tenant_session(code) as db:
        yield db
