"""Per-store Settings (docs/PLATFORM_CONTRACT.md 8).

In multi mode, inside a store context, ``get_settings()`` returns that
store's merged ``Settings``:

    env / .env (the process settings)                         lowest
      < the registry row (store name, timezone, POS)
      < platform store_settings (STORE_SETTING_KEYS only)
      < store_credentials (decrypted; CREDENTIAL_SETTING_KEYS only)
      < derived per-store values (store_code, upload paths,
        public_host = PLATFORM_PUBLIC_URL)                     highest

PLATFORM_ONLY_KEYS can never be overridden per store. The merged object is
cached per (store, settings_version); the version is re-read at most every
SETTINGS_REFRESH_SECONDS, and ``invalidate_store_settings`` drops it at
once. Tests may ``monkeypatch.setattr(get_settings(), ...)``: that patches
the cached object of the current store.
"""

import logging
import threading
import time
from typing import Any, Dict, FrozenSet, Optional, Protocol, Tuple, runtime_checkable

from pydantic import ValidationError

from app.tenancy.registry import StoreRef, get_store, normalize_store_code, settings_version

logger = logging.getLogger(__name__)

_EXPLICIT_STORE_KEYS = frozenset(
    {
        "store_name", "store_short_name", "store_address", "store_phone", "store_email", "store_description",
        "logo_url", "icon_url", "brand_color", "brand_secondary_color", "store_timezone", "currency",
        "store_locale", "demo_mode", "pilot_mode", "business_day_cutoff_hour", "first_sales_date", "pos_provider",
        "ap_inbox_email", "labels_url", "ai_enabled", "ai_consent_notice", "email_from_name",
        "po_whatsapp_enabled", "sales_sync_hour", "sales_sync_minute", "sales_sync_backfill_days",
        "sales_repull_enabled", "sales_repull_days", "sales_repull_times", "insights_nightly_enabled",
        "insights_nightly_hour", "insights_nightly_minute", "inventory_api_snapshots",
        "inventory_api_snapshot_hour", "inventory_api_snapshot_minute", "open_orders_full_scan_hour",
        "open_orders_full_scan_minute", "open_orders_today_refresh_minutes", "projection_method",
        "min_supported_build_ios", "min_supported_build_android",
    }
)
_STORE_KEY_PREFIXES = ("team_", "loss_watch_", "odd_check_")

# Settings fields filled from store_credentials (contract 8.3).
CREDENTIAL_SETTING_KEYS: FrozenSet[str] = frozenset(
    {
        "toast_client_id", "toast_restaurant_guid", "toast_api_base", "toast_analytics_client_id",
        "toast_client_secret", "toast_analytics_client_secret", "whapi_base_url", "whatsapp_group_chat_ids",
        "whapi_api_token", "smtp_host", "smtp_port", "smtp_ssl", "smtp_username", "email_from_name",
        "smtp_password", "anthropic_api_key",
    }
)

# Never taken from the process env in multi mode: one store's credentials,
# identity or outside addresses in the platform's .env must not reach every
# other store. They start at the Settings class default, then the store's
# own layers fill them.
_EXPLICIT_PRIVATE_KEYS = frozenset(
    {
        "gmail_address", "gmail_app_password", "whapi_chat_id", "whapi_store_checklist_group_id",
        "whapi_kitchen_checklist_group_id", "toast_invoice_email", "ap_inbox_email", "insights_hmac_secret",
        "store_name", "store_short_name", "store_address", "store_phone", "store_email", "store_description",
        "logo_url", "icon_url", "document_logo_file", "labels_url", "public_base_url", "email_from_name",
        "first_sales_date", "demo_mode", "pilot_mode",
    }
)

_EXPLICIT_PLATFORM_KEYS = frozenset(
    {
        "database_url", "tenancy_mode", "app_env", "platform_database_url", "store_database_url_template",
        "store_db_hosts", "store_db_prefix", "pg_admin_url", "tenant_pool_size", "tenant_max_overflow",
        "tenant_pool_timeout", "tenant_max_engines", "tenant_engine_idle_seconds", "statement_timeout_ms",
        "settings_refresh_seconds", "scheduler_max_workers", "platform_public_url", "platform_login_code",
        "retailops_secret_keys", "retailops_secret_key", "retailops_secret_key_file", "signup_enabled",
        "signup_rate_per_hour", "login_lockout_failures", "login_lockout_minutes", "login_ip_lockout_failures",
        "platform_require_totp", "dedicated_hosting_enabled", "dedicated_store_codes", "private_packs_dir",
        "cors_origins", "platform_domain", "product_name", "company_name", "app_bundle_id", "apple_team_id",
        "android_cert_sha256", "firebase_credentials_file", "scheduler_enabled", "notifications_enabled",
        "team_jobs_enabled", "frontend_dist_dir", "uploads_root", "store_pack_file", "store_identity_adopt",
        "store_code", "public_host", "log_format", "log_level", "log_dir", "security_headers",
    }
)
_PLATFORM_KEY_PREFIXES = ("session_", "directory_")


def _all_fields() -> FrozenSet[str]:
    from app.config import Settings

    return frozenset(Settings.model_fields)


def _platform_only() -> FrozenSet[str]:
    fields = _all_fields()
    return frozenset(
        k for k in fields if k in _EXPLICIT_PLATFORM_KEYS or k.startswith(_PLATFORM_KEY_PREFIXES)
    ) | (_EXPLICIT_PLATFORM_KEYS & fields)


def _store_keys() -> FrozenSet[str]:
    fields = _all_fields()
    keys = {k for k in fields if k in _EXPLICIT_STORE_KEYS or k.startswith(_STORE_KEY_PREFIXES)}
    return frozenset(keys - _platform_only())


PLATFORM_ONLY_KEYS: FrozenSet[str] = _platform_only()
# team_jobs_enabled starts with team_ but is platform-only: removed above.
STORE_SETTING_KEYS: FrozenSet[str] = _store_keys()
STORE_PRIVATE_KEYS: FrozenSet[str] = (CREDENTIAL_SETTING_KEYS | _EXPLICIT_PRIVATE_KEYS) & _all_fields()


@runtime_checkable
class SettingsSource(Protocol):
    def store_settings(self, code: str) -> Dict[str, Any]: ...

    def credential_overrides(self, code: str) -> Dict[str, Any]: ...


class EmptySettingsSource:
    def store_settings(self, code: str) -> Dict[str, Any]:
        return {}

    def credential_overrides(self, code: str) -> Dict[str, Any]:
        return {}


_source: SettingsSource = EmptySettingsSource()
# code -> (settings_version, Settings, checked at (monotonic))
_cache: Dict[str, Tuple[int, Any, float]] = {}
_lock = threading.Lock()


def set_settings_source(source: Optional[SettingsSource]) -> None:
    """C's platform source (store_settings + decrypted credentials); None
    resets to no per-store values."""
    global _source
    with _lock:
        _source = source if source is not None else EmptySettingsSource()
        _cache.clear()


def invalidate_store_settings(code: Optional[str] = None) -> None:
    with _lock:
        if code is None:
            _cache.clear()
        else:
            _cache.pop(normalize_store_code(code), None)


def _filtered(values: Dict[str, Any], allowed: FrozenSet[str], what: str, code: str) -> Dict[str, Any]:
    out = {}
    for key, value in (values or {}).items():
        if key in allowed and key not in PLATFORM_ONLY_KEYS:
            out[key] = value
        else:
            logger.warning("Store %s: %s key %r ignored (not allowed per store)", code, what, key)
    return out


def derived_values(ref: StoreRef) -> Dict[str, Any]:
    """The per-store values nobody sets by hand (highest layer)."""
    from app.config import platform_public_url, process_settings
    from app.tenancy.paths import uploads_base

    root = uploads_base() / ref.code
    return {
        "store_code": ref.code,
        "uploads_root": str(root),
        "team_uploads_dir": str(root / "team"),
        "invoice_uploads_dir": str(root / "invoices"),
        "flyers_dir": str(root / "flyers"),
        "insights_hmac_key_file": str(root / ".keys" / "insights_hmac.key"),
        "public_host": platform_public_url(process_settings()),
        # Per-store pack override files are a single-mode (parity / dev) tool.
        "store_pack_file": "",
    }


def build_store_settings(ref: StoreRef, base=None):
    """The merged Settings of `ref` (not cached)."""
    from app.config import Settings, process_settings

    base = base if base is not None else process_settings()
    merged = base.model_dump()
    for key in STORE_PRIVATE_KEYS:
        merged[key] = Settings.model_fields[key].default
    registry_layer = {"store_name": ref.name, "pos_provider": ref.pos_provider}
    if ref.timezone:
        registry_layer["store_timezone"] = ref.timezone
    store_layer = _filtered(_source.store_settings(ref.code), STORE_SETTING_KEYS, "store setting", ref.code)
    cred_layer = _filtered(_source.credential_overrides(ref.code), CREDENTIAL_SETTING_KEYS, "credential", ref.code)
    layers = [registry_layer, store_layer, cred_layer]
    derived = derived_values(ref)
    for attempt in range(3):
        values = dict(merged)
        for layer in layers:
            values.update(layer)
        values.update(derived)
        if "store_timezone" in values and values["store_timezone"] != base.store_timezone:
            values["toast_timezone"] = values["store_timezone"]
        if "ap_inbox_email" in store_layer:
            values["toast_invoice_email"] = store_layer["ap_inbox_email"]
        try:
            return Settings.model_validate(values)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e.get("loc")}
            logger.error("Store %s: invalid per-store settings %s ignored", ref.code, sorted(bad) or exc)
            if not bad or attempt == 2:
                layers = []
            else:
                layers = [{k: v for k, v in layer.items() if k not in bad} for layer in layers]
                if "store_timezone" in bad or "toast_timezone" in bad:
                    layers = [{k: v for k, v in layer.items() if k != "store_timezone"} for layer in layers]
    values = dict(merged)
    values.update(derived)
    values["toast_timezone"] = values["store_timezone"] = ref.timezone or base.store_timezone
    return Settings.model_validate(values)


def store_settings_for(code: str):
    """What get_settings() returns inside store `code` (multi mode)."""
    return _settings_for_ref(get_store(code))


def _settings_for_ref(ref: StoreRef):
    from app.config import process_settings

    base = process_settings()
    now = time.monotonic()
    refresh = max(0.0, float(base.settings_refresh_seconds))
    with _lock:
        hit = _cache.get(ref.code)
    if hit is not None and now - hit[2] < refresh:
        return hit[1]
    try:
        version = settings_version(ref.code)
    except Exception:  # noqa: BLE001 - registry briefly unreachable: keep what we have
        logger.exception("Store %s: settings version check failed", ref.code)
        version = hit[0] if hit is not None else ref.settings_version
    if hit is not None and hit[0] == version:
        with _lock:
            _cache[ref.code] = (version, hit[1], now)
        return hit[1]
    settings = build_store_settings(ref, base)
    with _lock:
        _cache[ref.code] = (version, settings, now)
    return settings


def current_store_settings(base):
    """config.get_settings() in multi mode: the current store's Settings, or
    `base` (platform level) without a store."""
    from app.tenancy.context import _store_var

    ref = _store_var.get()
    if ref is None:
        return base
    return _settings_for_ref(ref)


def validate_store_setting(key: str, value: Any) -> Any:
    """For C's settings API: the validated value of one store setting.
    Raises ValueError (answer 400) for a key that is not allowed or a bad
    value, using the same validators as env values."""
    from app.config import Settings, process_settings

    if key not in STORE_SETTING_KEYS:
        raise ValueError(f"{key} is not a store setting")
    values = process_settings().model_dump()
    values[key] = value
    if key == "store_timezone":
        values["toast_timezone"] = value
    try:
        validated = Settings.model_validate(values)
    except ValidationError as exc:
        # One plain sentence naming the field (G16), never validator text.
        from app.plain_errors import setting_error_message

        errors = [e for e in exc.errors() if key in [p for p in e.get("loc", ()) if isinstance(p, str)]] or exc.errors()
        raise ValueError(setting_error_message(key, errors)) from None
    return getattr(validated, key)
