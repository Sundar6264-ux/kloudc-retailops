"""Directory settings (M2 contract 1.1, "Directory service only" rows).

The fields live on ``app.config.Settings`` (added by MT7). Until MT7 merges,
each value is read with ``getattr(get_settings(), field, None)`` and falls back
to the environment variable, then to the documented default, so the directory
works the same before and after the merge (contract rule 0.8).
"""

import logging
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple
from urllib.parse import urlsplit

# Contract rule 0.3: nothing StoreOps serves or links to may live under the
# legacy store's domain. The one literal lives in app.config (MT7).
from app.config import REFUSED_DOMAIN as FORBIDDEN_DOMAIN

DEFAULT_PLATFORM_DOMAIN = "storeops.invalid"
DEFAULT_BUNDLE_ID = "com.kloudc.storeops"

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
TEAM_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
BUNDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
FINGERPRINT_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


log = logging.getLogger(__name__)

_APP_STORE_LISTING_RE = re.compile(r"^https://apps\.apple\.com/.*\bid\d+")


class DirectoryConfigError(ValueError):
    """A directory setting has a value the service refuses to start with."""


def is_forbidden_host(host: Optional[str]) -> bool:
    """True for FORBIDDEN_DOMAIN and any host under it (rule 0.3)."""
    h = (host or "").strip().lower().rstrip(".")
    return h == FORBIDDEN_DOMAIN or h.endswith("." + FORBIDDEN_DOMAIN)


def _setting(name: str, default: Any) -> Any:
    try:
        from app.config import get_settings

        value = getattr(get_settings(), name, None)
    except Exception:  # pragma: no cover - a broken .env must not hide the env fallback
        value = None
    if value is None:
        value = os.environ.get(name.upper())
    if value is None:
        return default
    return value


def _as_int(name: str, value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise DirectoryConfigError(f"{name.upper()} must be a whole number, got {value!r}")


def _split_list(value: Any) -> Tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value or "").split(",")
    return tuple(i.strip() for i in items if str(i).strip())


def _repo_root() -> pathlib.Path:
    # backend/app/directory/config.py -> repository root
    return pathlib.Path(__file__).resolve().parents[3]


def resolve_stores_path(raw: str) -> pathlib.Path:
    """``directory/stores.json`` is relative to the working directory; when
    that does not exist (the service is usually started from ``backend/``),
    the same relative path under the repository root is used."""
    p = pathlib.Path(raw).expanduser()
    if p.is_absolute() or p.exists():
        return p
    alt = _repo_root() / p
    return alt if alt.exists() else p


def _optional_https_url(name: str, value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    parts = urlsplit(v)
    if parts.scheme != "https" or not parts.hostname:
        raise DirectoryConfigError(f"{name.upper()} must be an https:// URL or blank")
    if is_forbidden_host(parts.hostname):
        raise DirectoryConfigError(f"{name.upper()} may not point at {FORBIDDEN_DOMAIN}")
    return v.rstrip("/")


@dataclass(frozen=True)
class DirectoryConfig:
    stores_file: str = "directory/stores.json"
    platform_domain: str = DEFAULT_PLATFORM_DOMAIN
    extra_api_hosts: Tuple[str, ...] = ()
    rate_per_minute: int = 10
    rate_per_hour: int = 100
    client_ip_header: str = ""
    product_name: str = "StoreOps"
    app_bundle_id: str = DEFAULT_BUNDLE_ID
    apple_team_id: str = ""
    android_cert_sha256: Tuple[str, ...] = field(default_factory=tuple)
    app_store_url: str = ""
    play_store_url: str = ""

    def __post_init__(self) -> None:
        dom = (self.platform_domain or "").strip().lower().rstrip(".")
        if not _DOMAIN_RE.match(dom):
            raise DirectoryConfigError(f"PLATFORM_DOMAIN {self.platform_domain!r} is not a domain name")
        if is_forbidden_host(dom):
            raise DirectoryConfigError(f"PLATFORM_DOMAIN may not be {FORBIDDEN_DOMAIN} or under it")
        object.__setattr__(self, "platform_domain", dom)

        hosts = []
        for h in self.extra_api_hosts:
            hl = h.strip().lower().rstrip(".")
            if not _DOMAIN_RE.match(hl):
                raise DirectoryConfigError(f"DIRECTORY_EXTRA_API_HOSTS entry {h!r} is not a host name")
            if is_forbidden_host(hl):
                raise DirectoryConfigError(f"DIRECTORY_EXTRA_API_HOSTS may not include {FORBIDDEN_DOMAIN}")
            hosts.append(hl)
        object.__setattr__(self, "extra_api_hosts", tuple(hosts))

        if self.rate_per_minute < 0 or self.rate_per_hour < 0:
            raise DirectoryConfigError("DIRECTORY_RATE_PER_MINUTE / _PER_HOUR must be 0 (off) or more")
        if self.client_ip_header and not _HEADER_RE.match(self.client_ip_header):
            raise DirectoryConfigError("DIRECTORY_CLIENT_IP_HEADER must be a header name")

        if not BUNDLE_RE.match(self.app_bundle_id or ""):
            raise DirectoryConfigError(f"APP_BUNDLE_ID {self.app_bundle_id!r} is not a bundle id")
        team = (self.apple_team_id or "").strip().upper()
        if team and not TEAM_ID_RE.match(team):
            raise DirectoryConfigError("APPLE_TEAM_ID must be 10 letters/digits (Apple Developer > Membership)")
        object.__setattr__(self, "apple_team_id", team)
        prints = []
        for fp in self.android_cert_sha256:
            f = fp.strip().upper()
            if not FINGERPRINT_RE.match(f):
                raise DirectoryConfigError(
                    "ANDROID_CERT_SHA256 entries must be SHA-256 fingerprints like AB:CD:...(32 pairs)"
                )
            prints.append(f)
        object.__setattr__(self, "android_cert_sha256", tuple(prints))
        # Store links on the open-in-app page must be this app's own listing:
        # an App Store URL without an "id<N>" (a search page) or a Play URL
        # for another package is ignored with a warning, never shown.
        app_store = _optional_https_url("app_store_url", self.app_store_url)
        if app_store and not _APP_STORE_LISTING_RE.match(app_store):
            log.warning("APP_STORE_URL is not an App Store listing (https://apps.apple.com/.../id<N>); not shown")
            app_store = ""
        object.__setattr__(self, "app_store_url", app_store)
        play = _optional_https_url("play_store_url", self.play_store_url)
        if play and not re.search(r"[?&]id=" + re.escape(self.app_bundle_id) + r"(&|$)", play):
            log.warning("PLAY_STORE_URL is not the listing for %s; the default listing URL is used", self.app_bundle_id)
            play = ""
        object.__setattr__(self, "play_store_url", play)

    @property
    def effective_play_store_url(self) -> str:
        return self.play_store_url or f"https://play.google.com/store/apps/details?id={self.app_bundle_id}"

    @property
    def stores_path(self) -> pathlib.Path:
        return resolve_stores_path(self.stores_file)

    @classmethod
    def from_settings(cls, **overrides: Any) -> "DirectoryConfig":
        values = dict(
            stores_file=str(_setting("directory_stores_file", "directory/stores.json") or "directory/stores.json"),
            platform_domain=str(_setting("platform_domain", DEFAULT_PLATFORM_DOMAIN) or DEFAULT_PLATFORM_DOMAIN),
            extra_api_hosts=_split_list(_setting("directory_extra_api_hosts", "")),
            rate_per_minute=_as_int("directory_rate_per_minute", _setting("directory_rate_per_minute", 10)),
            rate_per_hour=_as_int("directory_rate_per_hour", _setting("directory_rate_per_hour", 100)),
            client_ip_header=str(_setting("directory_client_ip_header", "") or "").strip(),
            product_name=str(_setting("product_name", "StoreOps") or "StoreOps"),
            app_bundle_id=str(_setting("app_bundle_id", DEFAULT_BUNDLE_ID) or DEFAULT_BUNDLE_ID),
            apple_team_id=str(_setting("apple_team_id", "") or ""),
            android_cert_sha256=_split_list(_setting("android_cert_sha256", "")),
            app_store_url=str(_setting("app_store_url", "") or ""),
            play_store_url=str(_setting("play_store_url", "") or ""),
        )
        values.update(overrides)
        return cls(**values)
