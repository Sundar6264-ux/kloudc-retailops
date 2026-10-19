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
    "directory_extra_api_hosts": "",
    "uploads_root": "uploads",
    "session_lifetime_days": 14,
    "terms_url": "",
    "product_name": "KloudC RetailOps",
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


def is_production() -> bool:
    """APP_ENV is production (the Settings default)."""
    return str(pget("app_env") or "production").strip().lower() in PRODUCTION_ENVS


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
