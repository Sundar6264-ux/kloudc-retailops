"""The public branding body (``GET /api/app/branding``, M2 contract section 2).

What the app and the website need before anyone logs in: the store's names,
logo, colours, time zone, POS, capabilities, store features and legal links.
Nothing secret is ever in it - only booleans derived from secrets (the store
features).
"""

import hashlib
import json
from typing import Optional

from app.config import get_settings
from app.pos import gating
from app.store import settings_view as sv

SCHEMA = 1


def _or_none(value: Optional[str]) -> Optional[str]:
    """Blank -> None. Page URLs (legal links) are kept as written; origins
    (public_host, labels_url) never end with "/" (settings_view)."""
    value = (value or "").strip()
    return value or None


def branding_payload(request_base: Optional[str] = None, s=None) -> dict:
    """The branding body. `request_base` (the request's own base URL) is only
    used when the store has neither PUBLIC_HOST nor STORE_CODE (dev)."""
    s = s if s is not None else get_settings()
    code = sv.store_code(s) or None
    public_host = sv.effective_public_host(s, request_base) or None
    return {
        "schema": SCHEMA,
        "store_code": code,
        "name": sv.store_display_name(s),
        "short_name": sv.store_short_name(s),
        "product": {"name": sv.product_name(s), "company": sv.company_name(s)},
        "logo_url": sv.logo_url(s, request_base),
        "icon_url": sv.icon_url(s, request_base),
        "primary_color": (getattr(s, "brand_color", "") or "#004BAD").upper(),
        "secondary_color": (getattr(s, "brand_secondary_color", "") or "#2EA3F2").upper(),
        "timezone": sv.store_timezone_name(s),
        "currency": (getattr(s, "currency", "") or "USD").strip().upper(),
        "locale": (getattr(s, "store_locale", "") or "en-US").strip(),
        "address": sv.store_address(s) or None,
        "phone": sv.store_phone(s) or None,
        "pos_name": gating.pos_name(),
        "capabilities": gating.capability_values(),
        "store_features": sv.store_features(s),
        "labels_url": sv.labels_url(s) or None,
        "public_host": public_host,
        "legal": {
            "company": sv.company_name(s),
            "privacy_url": _or_none(getattr(s, "privacy_url", "")),
            "terms_url": _or_none(getattr(s, "terms_url", "")),
            "support_url": _or_none(getattr(s, "support_url", "")),
        },
        "min_build": {
            "ios": int(getattr(s, "min_supported_build_ios", 0) or 0),
            "android": int(getattr(s, "min_supported_build_android", 0) or 0),
        },
        "store_url": {"ios": sv.app_store_url(s), "android": sv.play_store_url(s)},
        "demo": sv.demo_mode(s),
        # Pre-production (additive, G9; after the M2 keys): the POS id
        # ("toast" / "clover" / "csv") and the word to show before sign-in
        # ("your POS" for a CSV-only store, never "CSV upload").
        "pos_provider": _pos_provider(),
        "pos_word": _pos_word(),
    }


def _pos_provider() -> Optional[str]:
    from app.help_content import store_pos

    return store_pos()


def _pos_word() -> str:
    from app.help_content import pos_word

    return pos_word()


def compact_json(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def etag_for(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
