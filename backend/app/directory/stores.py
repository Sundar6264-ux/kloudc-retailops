"""Stores file: load, validate, and build the ``GET /v1/stores/{code}`` bodies
(M2 contract 4.1 and 4.2).

A bad file is refused as a whole: every problem is collected and reported in
one ``StoresFileError``. ``directory/stores.json`` (the real list) is
gitignored; ``directory/stores.example.json`` holds the fake stores.
"""

import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit

from app.directory.config import FORBIDDEN_DOMAIN, DirectoryConfig, is_forbidden_host

SCHEMA = 1
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,23}$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
STATUSES = ("active", "maintenance")

_STORE_KEYS = ("code", "name", "api_base_url", "status", "message", "branding", "min_build")
_REQUIRED_STORE_KEYS = ("code", "name", "api_base_url", "status", "branding")
# Fixed order: the bodies are compared byte for byte across service,
# static files and Worker.
BRANDING_KEYS = (
    "short_name",
    "logo_url",
    "icon_url",
    "primary_color",
    "secondary_color",
    "timezone",
    "pos_name",
)
_MAX_TEXT = 120
_MAX_MESSAGE = 500

# Messages are part of the contract's error bodies (the app may show them).
ERROR_MESSAGES = {
    "invalid_code": "That isn't a valid store code. Codes are 3 to 24 letters, numbers or underscores.",
    "unknown_store": "No store with that code.",
    "rate_limited": "Too many lookups. Try again in {seconds} seconds.",
    "directory_unavailable": "The store directory is unavailable right now. Try again shortly.",
    "not_found": "Not found.",
}


class StoresFileError(ValueError):
    """The stores file is missing or invalid; ``problems`` lists every issue."""

    def __init__(self, path: str, problems: List[str]):
        self.path = path
        self.problems = list(problems)
        super().__init__(f"stores file {path} refused: " + "; ".join(self.problems))


# Trimmed characters: ASCII whitespace only. Python's str.strip() and JS's
# trim() disagree outside ASCII (\x1c-\x1f, U+0085, U+FEFF), and the Worker
# must answer exactly like this service.
CODE_TRIM = " \t\n\r\x0b\x0c"


def normalize_code(raw: Any) -> Optional[str]:
    """Trim ASCII whitespace, upper-case, ``-`` -> ``_``, then the store-code
    pattern. Returns None when the result is not a valid code."""
    if not isinstance(raw, str):
        return None
    code = raw.strip(CODE_TRIM).upper().replace("-", "_")
    return code if CODE_RE.match(code) else None


def compact_json(obj: Any) -> bytes:
    """The one JSON encoding every directory form serves (and hashes)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def etag_for(body: bytes) -> str:
    """Same rule as /api/app/branding: first 32 hex of sha256 of the body."""
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def error_body(code: str, **extra: Any) -> bytes:
    message = ERROR_MESSAGES[code]
    if "retry_after" in extra:
        message = message.format(seconds=extra["retry_after"])
    payload: Dict[str, Any] = OrderedDict([("error", code), ("message", message)])
    payload.update(extra)
    return compact_json(payload)


def _timezone_ok(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class StoreRecord:
    code: str
    name: str
    api_base_url: str
    status: str
    message: Optional[str]
    branding: Tuple[Tuple[str, Any], ...]
    min_build: Tuple[Tuple[str, int], ...]

    def body(self, updated_at: str) -> "OrderedDict[str, Any]":
        return OrderedDict(
            [
                ("schema", SCHEMA),
                ("code", self.code),
                ("name", self.name),
                ("api_base_url", self.api_base_url),
                ("status", self.status),
                ("message", self.message),
                ("branding", OrderedDict(self.branding)),
                ("min_build", OrderedDict(self.min_build)),
                ("updated_at", updated_at),
            ]
        )


ZERO_MIN_BUILD: Tuple[Tuple[str, int], ...] = (("ios", 0), ("android", 0))


def connect_body(code: str, api_base_url: str, min_build: Tuple[Tuple[str, int], ...] = ZERO_MIN_BUILD) -> "OrderedDict[str, Any]":
    """The public lookup's body (as built 2026-10, PLATFORM_CONTRACT 13.4):
    only what a client needs to connect. No name, branding, message or
    timestamp, so a known store and an unknown code on the shared service
    read the same apart from ``code``. The status is always the neutral
    "active": a paused store's message comes only after a correct sign-in."""
    return OrderedDict(
        [
            ("schema", SCHEMA),
            ("code", code),
            ("api_base_url", api_base_url),
            ("status", "active"),
            ("min_build", OrderedDict(min_build)),
        ]
    )


@dataclass(frozen=True)
class StoresFile:
    path: str
    stores: "OrderedDict[str, StoreRecord]"
    updated_at: str
    stamp: Tuple[int, int]  # (mtime_ns, size): reload when it changes

    def body(self, code: str) -> Optional[bytes]:
        """The full record (name, branding, ...). Not served any more: the
        lookup answers ``connect(code, shared_api_url)``."""
        rec = self.stores.get(code)
        return None if rec is None else compact_json(rec.body(self.updated_at))

    def connect(self, code: str, shared_api_url: str) -> bytes:
        """The lookup's body for a valid code: known or not, only what a
        client needs to connect (``connect_body``). An unknown code gets the
        shared service's address; a store on the shared service reads the
        same apart from its code (its min build comes from its server's
        /api/app/config). A store on its own server necessarily differs."""
        shared = shared_api_url.rstrip("/")
        rec = self.stores.get(code)
        if rec is None:
            return compact_json(connect_body(code, shared))
        on_shared = rec.api_base_url.rstrip("/") == shared
        return compact_json(connect_body(code, rec.api_base_url, ZERO_MIN_BUILD if on_shared else rec.min_build))


def _api_host_allowed(host: str, cfg: DirectoryConfig) -> bool:
    return host.endswith("." + cfg.platform_domain) or host in cfg.extra_api_hosts


def _check_url(value: Any, where: str, problems: List[str], *, origin_only: bool) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be an https:// URL")
        return None
    v = value.strip()
    try:
        parts = urlsplit(v)
        port = parts.port
    except ValueError:
        problems.append(f"{where}: not a valid URL")
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        problems.append(f"{where}: must be an https:// URL")
        return None
    if is_forbidden_host(host):
        problems.append(f"{where}: may not point at {FORBIDDEN_DOMAIN}")
        return None
    if parts.username or parts.password or parts.fragment:
        problems.append(f"{where}: no user info or fragment allowed")
        return None
    if origin_only:
        if parts.path or parts.query or port is not None or v.endswith("/"):
            problems.append(f"{where}: must be https://<host> with no path, port, query or trailing slash")
            return None
    return v


def _text(value: Any, where: str, problems: List[str], limit: int = _MAX_TEXT) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be a non-empty string")
        return None
    v = value.strip()
    if len(v) > limit:
        problems.append(f"{where}: longer than {limit} characters")
        return None
    return v


def _parse_store(raw: Any, idx: int, cfg: DirectoryConfig, problems: List[str]) -> Optional[StoreRecord]:
    where = f"stores[{idx}]"
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be an object")
        return None
    before = len(problems)
    unknown = sorted(set(raw) - set(_STORE_KEYS))
    if unknown:
        problems.append(f"{where}: unknown keys {unknown}")
    for k in _REQUIRED_STORE_KEYS:
        if k not in raw:
            problems.append(f"{where}: missing {k!r}")
    if len(problems) > before:
        return None

    code = raw["code"]
    if not isinstance(code, str) or not CODE_RE.match(code):
        problems.append(f"{where}.code: {code!r} does not match {CODE_RE.pattern}")
        code = None
    else:
        where = f"stores[{idx}] ({code})"
    name = _text(raw["name"], f"{where}.name", problems)

    api = _check_url(raw["api_base_url"], f"{where}.api_base_url", problems, origin_only=True)
    if api is not None:
        host = (urlsplit(api).hostname or "").lower()
        if not _api_host_allowed(host, cfg):
            problems.append(
                f"{where}.api_base_url: host {host!r} is not under .{cfg.platform_domain} "
                "and not in DIRECTORY_EXTRA_API_HOSTS"
            )
            api = None

    status = raw["status"]
    if status not in STATUSES:
        problems.append(f"{where}.status: must be one of {list(STATUSES)}")
    message = raw.get("message")
    if message is not None:
        message = _text(message, f"{where}.message", problems, limit=_MAX_MESSAGE)

    branding_raw = raw["branding"]
    branding: List[Tuple[str, Any]] = []
    if not isinstance(branding_raw, dict):
        problems.append(f"{where}.branding: must be an object")
    else:
        extra = sorted(set(branding_raw) - set(BRANDING_KEYS))
        missing = [k for k in BRANDING_KEYS if k not in branding_raw]
        if extra:
            problems.append(f"{where}.branding: unknown keys {extra}")
        if missing:
            problems.append(f"{where}.branding: missing {missing}")
        if not extra and not missing:
            b = branding_raw
            branding.append(("short_name", _text(b["short_name"], f"{where}.branding.short_name", problems)))
            for k in ("logo_url", "icon_url"):
                v = b[k]
                if v is not None:
                    v = _check_url(v, f"{where}.branding.{k}", problems, origin_only=False)
                branding.append((k, v))
            for k in ("primary_color", "secondary_color"):
                v = b[k]
                if not isinstance(v, str) or not HEX_RE.match(v):
                    problems.append(f"{where}.branding.{k}: must be #RRGGBB")
                branding.append((k, v))
            tz = b["timezone"]
            if not isinstance(tz, str) or not _timezone_ok(tz):
                problems.append(f"{where}.branding.timezone: {tz!r} is not an IANA time zone")
            branding.append(("timezone", tz))
            branding.append(("pos_name", _text(b["pos_name"], f"{where}.branding.pos_name", problems)))

    mb_raw = raw.get("min_build", {"ios": 0, "android": 0})
    min_build: List[Tuple[str, int]] = []
    if not isinstance(mb_raw, dict) or set(mb_raw) != {"ios", "android"}:
        problems.append(f"{where}.min_build: must be {{\"ios\": <int>, \"android\": <int>}}")
    else:
        for k in ("ios", "android"):
            v = mb_raw[k]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                problems.append(f"{where}.min_build.{k}: must be a whole number >= 0")
            min_build.append((k, v))

    if len(problems) > before:
        return None
    return StoreRecord(
        code=code,
        name=name,
        api_base_url=api,
        status=status,
        message=message,
        branding=tuple(branding),
        min_build=tuple(min_build),
    )


def parse_stores(data: Any, cfg: DirectoryConfig, path: str = "<stores>") -> "OrderedDict[str, StoreRecord]":
    """Validate the parsed JSON of a stores file. Raises StoresFileError."""
    problems: List[str] = []
    if not isinstance(data, dict):
        raise StoresFileError(path, ["top level must be an object"])
    if data.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    extra = sorted(set(data) - {"schema", "stores"})
    if extra:
        problems.append(f"unknown top-level keys {extra}")
    stores_raw = data.get("stores")
    if not isinstance(stores_raw, list):
        problems.append("stores must be a list")
        stores_raw = []
    out: "OrderedDict[str, StoreRecord]" = OrderedDict()
    for idx, raw in enumerate(stores_raw):
        rec = _parse_store(raw, idx, cfg, problems)
        if rec is None:
            continue
        if rec.code in out:
            problems.append(f"stores[{idx}]: duplicate code {rec.code}")
            continue
        out[rec.code] = rec
    if problems:
        raise StoresFileError(path, problems)
    return out


def updated_at_from_mtime_ns(mtime_ns: int) -> str:
    ts = _dt.datetime.fromtimestamp(mtime_ns // 1_000_000_000, tz=_dt.timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def file_stamp(path: pathlib.Path) -> Tuple[int, int]:
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


def load_stores_file(path: Union[str, "os.PathLike[str]"], cfg: DirectoryConfig) -> StoresFile:
    p = pathlib.Path(path)
    try:
        stamp = file_stamp(p)
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise StoresFileError(str(p), ["file not found (copy directory/stores.example.json to start)"])
    except OSError as exc:
        raise StoresFileError(str(p), [f"cannot read: {exc}"])
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise StoresFileError(str(p), [f"not valid JSON: {exc}"])
    stores = parse_stores(data, cfg, str(p))
    return StoresFile(path=str(p), stores=stores, updated_at=updated_at_from_mtime_ns(stamp[0]), stamp=stamp)
