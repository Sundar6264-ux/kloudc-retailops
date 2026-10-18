"""``GET /api/public/stores/{code}`` body (contract 13.4): byte-for-byte the
M2 directory shape (``app.directory.stores.StoreRecord``), built from the
registry row and its store settings."""

from typing import Optional, Tuple
from urllib.parse import urlsplit

from app.directory.stores import StoreRecord, compact_json, normalize_code
from app.platform.config import platform_public_url
from app.platform.models import Store

DEFAULT_PRIMARY = "#0F766E"
DEFAULT_SECONDARY = "#334155"


def _absolute(value, base: str) -> Optional[str]:
    v = (value or "").strip() if isinstance(value, str) else ""
    if not v:
        return None
    if v.startswith("/"):
        return base + v
    parts = urlsplit(v)
    return v if parts.scheme == "https" and parts.hostname else None


def _color(value, default: str) -> str:
    import re

    v = (value or "").strip() if isinstance(value, str) else ""
    return v.upper() if re.match(r"^#[0-9A-Fa-f]{6}$", v) else default


def build_record(store: Store, settings: dict) -> StoreRecord:
    from app.pos.gating import PROVIDERS

    base = store.api_base_url or platform_public_url()
    status = "active" if store.status == "active" else "maintenance"
    message = None
    if status == "maintenance":
        message = (store.maintenance_message or "This store is paused.")[:500]
    short = settings.get("store_short_name") or settings.get("store_name") or store.name
    branding = (
        ("short_name", str(short)[:120]),
        ("logo_url", _absolute(settings.get("logo_url"), base)),
        ("icon_url", _absolute(settings.get("icon_url"), base)),
        ("primary_color", _color(settings.get("brand_color"), DEFAULT_PRIMARY)),
        ("secondary_color", _color(settings.get("brand_secondary_color"), DEFAULT_SECONDARY)),
        ("timezone", store.timezone),
        ("pos_name", (PROVIDERS.get(store.pos_provider) or (store.pos_provider.title(),))[0]),
    )
    min_build = (
        ("ios", int(settings.get("min_supported_build_ios") or store.min_build_ios or 0)),
        ("android", int(settings.get("min_supported_build_android") or store.min_build_android or 0)),
    )
    return StoreRecord(
        code=store.code,
        name=str(settings.get("store_name") or store.name)[:120],
        api_base_url=base,
        status=status,
        message=message,
        branding=branding,
        min_build=min_build,
    )


def lookup_body(raw_code: str) -> Tuple[int, bytes]:
    """(status, body): 200 with the body, 400 invalid code, 404 unknown or
    not visible (pending / provisioning / failed / archived)."""
    from app.platform.sources import lookup_record, store_settings_dict

    code = normalize_code(raw_code)
    if code is None:
        return 400, b""
    store = lookup_record(code)
    if store is None or store.status not in ("active", "suspended"):
        return 404, b""
    record = build_record(store, store_settings_dict(code))
    updated = (store.updated_at or store.created_at).strftime("%Y-%m-%dT%H:%M:%SZ")
    return 200, compact_json(record.body(updated))
