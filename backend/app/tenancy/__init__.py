"""StoreOps tenancy: one shared service, one database per store
(docs/PLATFORM_CONTRACT.md sections 4-8, 12, 16).

Single mode (TENANCY_MODE=single, the default) keeps the M2 behaviour: one
store from DATABASE_URL + STORE_CODE, and every helper here works on it.
Multi mode selects the store per request (TenantMiddleware) or per job
(store_context / for_each_store) and routes sessions, settings, caches,
locks and files to that store.
"""

from app.tenancy.cache import (
    TenantCache,
    TenantLocal,
    clear_tenant_caches,
    tenant_cache,
    tenant_local,
    tenant_state,
)
from app.tenancy.context import (
    current_scope,
    current_store,
    current_store_block,
    current_store_code,
    no_store,
    platform_scope,
    require_store,
    run_in_store,
    spawn_in_store,
    store_context,
)
from app.tenancy.engines import (
    dispose_engine,
    engine_stats,
    register_engine_hook,
    store_database_url,
    sweep_idle_engines,
    tenant_engine,
    tenant_session,
)
from app.tenancy.errors import (
    NoStoreContext,
    StoreIdentityError,
    StoreMoved,
    StoreNotActive,
    StoreUnavailable,
    UnknownStore,
)
from app.tenancy.jobs import (
    each_store_job,
    for_each_store,
    on_store_changed,
    reconcile_store_jobs,
    register_store_job,
    register_store_startup_job,
)
from app.tenancy.locks import TenantLock, advisory_lock, keyed_lock, tenant_lock
from app.tenancy.mode import is_multi_tenant
from app.tenancy.paths import store_path, store_root
from app.tenancy.registry import (
    RESERVED_CODES,
    RegistrySource,
    StoreRef,
    get_store,
    is_valid_store_code,
    list_stores,
    normalize_store_code,
    set_registry_source,
)
from app.tenancy.routing import node_serves, store_moved_response
from app.tenancy.settings import (
    PLATFORM_ONLY_KEYS,
    STORE_SETTING_KEYS,
    SettingsSource,
    invalidate_store_settings,
    set_settings_source,
    store_settings_for,
    validate_store_setting,
)
from app.tenancy.tokens import make_token, token_hash, token_scope_code

__all__ = [
    "NoStoreContext", "UnknownStore", "StoreNotActive", "StoreMoved", "StoreIdentityError", "StoreUnavailable",
    "is_multi_tenant",
    "StoreRef", "RegistrySource", "get_store", "list_stores", "set_registry_source", "normalize_store_code",
    "is_valid_store_code", "RESERVED_CODES",
    "current_store", "current_store_code", "require_store", "current_scope", "current_store_block",
    "store_context", "platform_scope", "no_store", "run_in_store", "spawn_in_store",
    "tenant_engine", "tenant_session", "store_database_url", "dispose_engine", "engine_stats",
    "register_engine_hook", "sweep_idle_engines",
    "for_each_store", "register_store_job", "register_store_startup_job", "reconcile_store_jobs",
    "on_store_changed", "each_store_job",
    "keyed_lock", "advisory_lock", "tenant_lock", "TenantLock",
    "TenantCache", "TenantLocal", "tenant_cache", "tenant_state", "tenant_local", "clear_tenant_caches",
    "store_root", "store_path",
    "STORE_SETTING_KEYS", "PLATFORM_ONLY_KEYS", "store_settings_for", "invalidate_store_settings",
    "set_settings_source", "SettingsSource", "validate_store_setting",
    "node_serves", "store_moved_response",
    "make_token", "token_hash", "token_scope_code",
]
