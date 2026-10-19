"""Values derived from the store settings, computed in one place so every
caller agrees (docs/M2_CONTRACT.md section 1.2).

Every function takes an optional Settings object (``s``); without one it reads
``get_settings()`` at call time, so tests that patch the settings object see
their change.
"""

import datetime as dt
import json
import logging
import re
from typing import Dict, List, NamedTuple, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# Store features (contract 1.3), in their fixed order.
STORE_FEATURES = ("ap_inbox", "labels", "whatsapp", "ai")
DEFAULT_DESCRIPTION = "a retail store"
DEFAULT_LOGO_PATH = "/branding/logo.png"
DEFAULT_ICON_PATH = "/branding/icon.png"


def _s(s=None):
    return s if s is not None else get_settings()


def _get(s, name: str, default=""):
    return getattr(s, name, default)


def _clean(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value))


# --- Names ---------------------------------------------------------------------------


def product_name(s=None) -> str:
    return _clean(_get(_s(s), "product_name", "KloudC RetailOps")) or "KloudC RetailOps"


def company_name(s=None) -> str:
    return _clean(_get(_s(s), "company_name", "KloudC LLC")) or "KloudC LLC"


def product_credit(s=None) -> str:
    """"KloudC RetailOps by KloudC LLC" - the small product line under emails
    and PDFs the product sends for a store."""
    return f"{product_name(s)} by {company_name(s)}"


def store_display_name(s=None) -> str:
    """STORE_NAME, else the product name. Never blank."""
    s = _s(s)
    return _clean(_get(s, "store_name")) or product_name(s)


def store_short_name(s=None) -> str:
    s = _s(s)
    return _clean(_get(s, "store_short_name")) or store_display_name(s)


def store_description(s=None) -> str:
    """One phrase for AI prompts: "a ... store in ..."."""
    return _clean(_get(_s(s), "store_description")) or DEFAULT_DESCRIPTION


def store_slug(s=None) -> str:
    """The display name as a file-name prefix: lower case, letters and digits
    only ("Test Toast Market" -> "testtoastmarket")."""
    return re.sub(r"[^a-z0-9]+", "", store_display_name(s).lower()) or "retailops"


def store_address(s=None) -> str:
    return _clean(_get(_s(s), "store_address"))


def store_phone(s=None) -> str:
    return _clean(_get(_s(s), "store_phone"))


def store_code(s=None) -> str:
    return _clean(_get(_s(s), "store_code")).upper()


# --- Hosts and URLs ------------------------------------------------------------------


def store_host_label(s=None) -> str:
    """"retailops-test-toast" for RETAILOPS_TEST_TOAST; "" without a code."""
    return store_code(s).lower().replace("_", "-")


def platform_domain(s=None) -> str:
    return _clean(_get(_s(s), "platform_domain", "retailops.invalid")).strip(".").lower() or "retailops.invalid"


def effective_public_host(s=None, request_base: Optional[str] = None) -> str:
    """PUBLIC_HOST, else https://<host-label>.<platform domain>, else (no
    store code: dev) the request's own base URL; "" when none is known."""
    s = _s(s)
    own = _clean(_get(s, "public_host")).rstrip("/")
    if own:
        return own
    label = store_host_label(s)
    if label:
        return f"https://{label}.{platform_domain(s)}"
    return (request_base or "").rstrip("/")


def effective_public_base_url(s=None, request_base: Optional[str] = None) -> str:
    """PUBLIC_BASE_URL (public file links), else the public host."""
    s = _s(s)
    own = _clean(_get(s, "public_base_url")).rstrip("/")
    return own or effective_public_host(s, request_base)


def absolute_url(path_or_url: str, s=None, request_base: Optional[str] = None) -> str:
    """"/branding/logo.png" -> "<public host>/branding/logo.png"; an absolute
    URL is returned as is (no trailing slash)."""
    value = _clean(path_or_url)
    if re.match(r"^https?://", value, re.I):
        return value.rstrip("/")
    host = effective_public_host(s, request_base)
    if not value.startswith("/"):
        value = "/" + value
    return f"{host}{value}"


def logo_url(s=None, request_base: Optional[str] = None) -> str:
    s = _s(s)
    return absolute_url(_clean(_get(s, "logo_url")) or DEFAULT_LOGO_PATH, s, request_base)


def icon_url(s=None, request_base: Optional[str] = None) -> str:
    s = _s(s)
    return absolute_url(_clean(_get(s, "icon_url")) or DEFAULT_ICON_PATH, s, request_base)


def logo_path_or_url(s=None) -> str:
    """For HTML served by this server: the logo as set, else the bundled one
    (a path on this server)."""
    return _clean(_get(_s(s), "logo_url")) or DEFAULT_LOGO_PATH


def labels_url(s=None) -> str:
    return _clean(_get(_s(s), "labels_url")).rstrip("/")


def play_store_url(s=None) -> str:
    s = _s(s)
    own = _clean(_get(s, "play_store_url"))
    if own:
        return own
    bundle = _clean(_get(s, "app_bundle_id", "com.kloudc.retailops")) or "com.kloudc.retailops"
    return f"https://play.google.com/store/apps/details?id={bundle}"


def app_store_url(s=None) -> Optional[str]:
    return _clean(_get(_s(s), "app_store_url")) or None


# --- Store rules -----------------------------------------------------------------------


def first_sales_date(s=None) -> Optional[dt.date]:
    """FIRST_SALES_DATE, or None (no floor)."""
    raw = _clean(_get(_s(s), "first_sales_date"))
    return dt.date.fromisoformat(raw) if raw else None


def sales_floor(s=None) -> dt.date:
    """FIRST_SALES_DATE, or date.min when there is no floor (safe in max()
    and comparisons)."""
    return first_sales_date(s) or dt.date.min


def business_day_cutoff_hour(s=None) -> int:
    value = _get(_s(s), "business_day_cutoff_hour", 4)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 4


def store_timezone_name(s=None) -> str:
    s = _s(s)
    return _clean(_get(s, "store_timezone")) or _clean(_get(s, "toast_timezone")) or "America/New_York"


# --- Store features (contract 1.3) ------------------------------------------------------


def ap_inbox_address(s=None) -> str:
    s = _s(s)
    value = _get(s, "ap_inbox_email", None)
    if value is None:
        value = _get(s, "toast_invoice_email", "")
    return _clean(value)


def ai_enabled(s=None) -> bool:
    return bool(_get(_s(s), "ai_enabled", False))


def store_features(s=None) -> List[str]:
    s = _s(s)
    on = {
        "ap_inbox": bool(ap_inbox_address(s)),
        "labels": bool(labels_url(s)),
        "whatsapp": bool(_clean(_get(s, "whapi_api_token"))),
        "ai": ai_enabled(s),
    }
    return [f for f in STORE_FEATURES if on[f]]


def has_store_feature(feature: str, s=None) -> bool:
    return feature in store_features(s)


def demo_mode(s=None) -> bool:
    return bool(_get(_s(s), "demo_mode", False))


# --- WhatsApp roles -----------------------------------------------------------------------


def whatsapp_role_chat_ids(s=None) -> Dict[str, str]:
    """Role key -> Whapi chat id from the settings: the two legacy settings
    (roles "store" and "kitchen"), overridden by WHATSAPP_GROUP_CHAT_IDS."""
    s = _s(s)
    out: Dict[str, str] = {}
    legacy = {
        "store": _clean(_get(s, "whapi_store_checklist_group_id")),
        "kitchen": _clean(_get(s, "whapi_kitchen_checklist_group_id")),
    }
    out.update({k: v for k, v in legacy.items() if v})
    raw = _clean(_get(s, "whatsapp_group_chat_ids"))
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            logger.error("WHATSAPP_GROUP_CHAT_IDS is not a JSON object - ignored")
        else:
            for key, value in parsed.items():
                if isinstance(key, str) and isinstance(value, str) and value.strip():
                    out[key.strip()] = value.strip()
    return out


# --- Email --------------------------------------------------------------------------------


class SmtpConfig(NamedTuple):
    host: str
    port: int
    ssl: bool
    username: str
    password: str
    from_name: str


def smtp_config(s=None) -> SmtpConfig:
    s = _s(s)
    return SmtpConfig(
        host=_clean(_get(s, "smtp_host", "smtp.gmail.com")) or "smtp.gmail.com",
        port=int(_get(s, "smtp_port", 465) or 465),
        ssl=bool(_get(s, "smtp_ssl", True)),
        username=_clean(_get(s, "smtp_username")) or _clean(_get(s, "gmail_address")),
        password=_get(s, "smtp_password", "") or _get(s, "gmail_app_password", "") or "",
        from_name=_clean(_get(s, "email_from_name")) or store_display_name(s),
    )


def email_fallback_domain(s=None) -> str:
    s = _s(s)
    return _clean(_get(s, "email_fallback_domain")) or platform_domain(s)


def store_email(s=None) -> str:
    return _clean(_get(_s(s), "store_email"))
