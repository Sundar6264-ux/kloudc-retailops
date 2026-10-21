"""Platform settings (docs/PLATFORM_CONTRACT.md section 17).

Track B added these fields to ``app.config.Settings``. Every read here goes
through ``pget(name)``: the process Settings attribute when it exists, else
the environment variable of the same name upper-cased, else the default
below. Tests override a value by patching ``process_settings()`` (or with
``monkeypatch.setenv`` for the few knobs that are not Settings fields).

A few platform-only knobs are not in section 17 (they only matter to this
package); they follow the same rule: ``PLATFORM_SESSION_HOURS``,
``PLATFORM_SUPPORT_MINUTES``, ``SIGNUP_RATE_OVERALL_PER_HOUR``,
``OWNER_SETUP_LINK_HOURS``, ``PLATFORM_WRITE_RATE_PER_MINUTE``,
``INTEGRATION_TEST_RATE_PER_MINUTE``.
"""

import os
from typing import Any, Dict, List

DEFAULTS: Dict[str, Any] = {
    "tenancy_mode": "single",
    "app_env": "production",
    "platform_database_url": "",
    "store_database_url_template": "",
    "store_db_hosts": "",
    "store_db_prefix": "so_",
    "pg_admin_url": "",
    "platform_public_url": "",
    "platform_login_code": "PLATFORM",
    "retailops_secret_keys": "",
    "retailops_secret_key": "",
    "retailops_secret_key_file": ".secrets/retailops_keyring",
    "signup_enabled": True,
    "signup_rate_per_hour": 5,
    "signup_rate_overall_per_hour": 50,
    "login_lockout_failures": 5,
    "login_lockout_minutes": 15,
    "login_ip_lockout_failures": 30,
    "platform_require_totp": None,  # None = on in production (require_totp below)
    "dedicated_hosting_enabled": False,
    "dedicated_store_codes": "",
    "private_packs_dir": "",
    "platform_session_hours": 12,
    "platform_support_minutes": 30,
    "owner_setup_link_hours": 72,
    "platform_write_rate_per_minute": 60,
    "integration_test_rate_per_minute": 5,
    # Existing Settings fields read here too (same rule).
    "platform_domain": "retailops.invalid",
    "directory_client_ip_header": "",
    "directory_rate_per_minute": 10,
    "directory_rate_per_hour": 100,
    # Store lookup (/api/public/stores/{code}) of a Store ID this address
    # already asked for: many phones on one store Wi-Fi (or behind the
    # tunnel) share an address. directory_rate_* above then limit how many
    # different Store IDs one address may try (pre-production gap G13).
    "directory_store_rate_per_minute": 60,
    "directory_store_rate_per_hour": 1000,
    "directory_extra_api_hosts": "",
    "uploads_root": "uploads",
    "session_lifetime_days": 14,
    "terms_url": "",
    "product_name": "KloudC RetailOps",
    # --- website-ready track A (console) - docs/WEBSITE_READY_CONTRACT.md 2 ---
    # --- website-ready track B (exports, plans, billing) ---
    "billing_provider": "none",  # "none" | "stripe" (stub until the owner adds keys)
    "stripe_secret_key": "",  # secret: environment only, never logged or returned
    "stripe_webhook_secret": "",  # secret: environment only
    # --- website-ready track C (storage, job queue, live updates) ---
    "storage_backend": "local",  # "local" | "s3"
    "job_queue_mode": "inline",  # "inline" | "queue"
    "live_updates_mode": "local",  # "local" | "postgres" (LISTEN/NOTIFY)
    "storage_s3_endpoint": "",  # e.g. https://<account>.r2.cloudflarestorage.com
    "storage_s3_bucket": "",
    "storage_s3_access_key_id": "",  # secret-ish: never logged or returned
    "storage_s3_secret_access_key": "",  # secret: never logged or returned
    "storage_s3_region": "auto",  # R2 uses "auto"
    "storage_s3_prefix": "stores",  # keys are <prefix>/<STORE_CODE>/<key>
    "storage_presign": True,  # S3: direct download links (False = the server streams)
    "db_pool_mode": "direct",  # "direct" | "pgbouncer" (transaction pooling)
    "platform_direct_url": "",  # session connection for LISTEN + the leader lock (PgBouncer)
    # --- website-ready track D (Clover) ---
    "clover_app_id": "",
    "clover_app_secret": "",
    "clover_environment": "sandbox",  # "sandbox" | "us" | "eu" | "la"
    # Where Clover sends the merchant back (blank: PLATFORM_PUBLIC_URL +
    # /api/settings/integrations/clover/callback). Must match the Clover app.
    "clover_redirect_url": "",
    # Single-store mode only (multi mode keeps these in the store's vault):
    # a merchant API token for a one-merchant server. Never returned by an API.
    "clover_merchant_id": "",
    "clover_api_token": "",
    "clover_region": "us",  # "us" | "eu" | "la" | "sandbox"
    # Clover webhooks (D6): off by default; the auth code Clover shows once
    # the webhook URL is verified. Never returned by an API.
    "clover_webhooks_enabled": False,
    "clover_webhook_auth": "",
    # Pre-production G4: the app's URL scheme for the Connect with Clover
    # hand-back page (/api/settings/integrations/clover/app-return).
    "app_url_scheme": "kloudcretailops",
}

_TRUE = {"1", "true", "yes", "on"}


def _coerce(raw: str, default: Any) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in _TRUE
    if isinstance(default, int):
        try:
            return int(raw.strip())
        except ValueError:
            return default
    return raw


def pget(name: str) -> Any:
    """One platform setting (see the module docstring for the lookup order)."""
    default = DEFAULTS.get(name)
    try:
        # The process (platform-level) settings: platform values are never
        # per store (contract 8.1 PLATFORM_ONLY_KEYS).
        from app.config import process_settings

        s = process_settings()
        if name in type(s).model_fields:
            return getattr(s, name)
    except Exception:  # noqa: BLE001 - settings not loadable (CLI before .env): env only
        pass
    raw = os.environ.get(name.upper())
    if raw is None:
        return default
    return _coerce(raw, default)


def is_multi() -> bool:
    return str(pget("tenancy_mode") or "single").strip().lower() == "multi"


def login_code() -> str:
    return (str(pget("platform_login_code") or "PLATFORM")).strip().upper() or "PLATFORM"


def platform_public_url() -> str:
    """The shared service's origin (no trailing slash)."""
    url = str(pget("platform_public_url") or "").strip().rstrip("/")
    if url:
        return url
    return f"https://app.{str(pget('platform_domain') or 'retailops.invalid').strip()}"


def dedicated_enabled() -> bool:
    return bool(pget("dedicated_hosting_enabled"))


def dedicated_store_codes() -> List[str]:
    raw = str(pget("dedicated_store_codes") or "")
    return [c.strip().upper() for c in raw.split(",") if c.strip()]


def extra_api_hosts() -> List[str]:
    raw = str(pget("directory_extra_api_hosts") or "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


PRODUCTION_ENVS = ("production", "prod")
# The only APP_ENV values that are NOT production. Anything else - blank, a
# typo ("producton"), "staging" - is production, so a mistyped value can't
# switch the start-up checks and the TOTP requirement off (fail closed).
NON_PRODUCTION_ENVS = ("development", "dev", "test")


def env_is_production(value) -> bool:
    """True unless `value` (an APP_ENV) is one of NON_PRODUCTION_ENVS."""
    return str(value or "production").strip().lower() not in NON_PRODUCTION_ENVS


def is_production() -> bool:
    """APP_ENV is production: the Settings default, and any value that is not
    development / dev / test."""
    return env_is_production(pget("app_env"))


def require_totp() -> bool:
    """Masters must use two-step sign-in: PLATFORM_REQUIRE_TOTP when set,
    else ON in production and off elsewhere (dev, test, the preview)."""
    value = pget("platform_require_totp")
    if isinstance(value, str):
        value = value.strip().lower()
        if not value:
            value = None
        else:
            value = value in _TRUE
    if value is None:
        return is_production()
    return bool(value)
