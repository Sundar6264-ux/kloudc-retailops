"""Public checklist link (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md).

Owner, Oct 1: part-time service staff fill the kitchen service checklist from
a link, without an app account. An admin turns a link on per checklist; the
URL is {PUBLIC_BASE_URL}/c/{slug}-{code}, e.g. /c/kitchen-4821 (owner, Oct 1:
"something easily memorizable" - a short word plus 4 digits).

What a link can do, and nothing else:
- GET  /api/public/checklists/{key}            that checklist's current form
- POST /api/public/checklists/{key}/submit     one submission for it
- POST /api/public/checklists/{key}/media      a photo for one of its photo questions
- GET  /api/public/checklists/{key}/images/{media_token}
                                                a picture the form itself shows
- GET  /c/{slug}-{code}                         the page (routers/checklist_public.py)

{key} is the whole "{slug}-{code}" (e.g. "kitchen-4821"). A link made before
the short codes has a 22-character token in place of the code; it keeps
working (as "{slug}-{token}", or the bare token the first page version sent)
until it's rotated, which gives it a short code.

Security rules (keep them when changing anything here):
- The code is 4 random digits (secrets; no 0000/1234-style codes), unique
  across links. 4 digits alone is easy to guess, so a lookup needs BOTH the
  slug and the code, every link's slug and code are compared in constant
  time (no early exit, no lookup by token), and an IP with 10 failed
  lookups in 10 minutes gets 429 for every lookup until that drains
  (FAILED_IP_LIMIT; plus FAILED_GLOBAL_LIMIT for all IPs together, against
  forged CF-Connecting-IP). Old 22-character tokens (~131 bits) also still
  match on their own.
- Unknown, disabled, rotated or deleted links, a checklist that's inactive,
  and a malformed token all get the exact same 404 (NOT_ACTIVE).
- A submission goes through checklist_submission.create_submission, the same
  code as the app (validation, flags, duplicate replay, WhatsApp, 9:30 PM
  alert, nightly task), with submitted_by_user_id 0 and the picked name.
- The name is plain text: letters (any language), spaces and . ' - only,
  2-60 characters (clean_name). It can't carry HTML or WhatsApp formatting.
- Rate limits: per IP (CF-Connecting-IP when Cloudflare sends it), a global
  cap for all public traffic, the link's own max_per_hour submissions, and a
  per-link cap on photo uploads.
- Photos uploaded through a link are bound to that link and to the uploader
  (a key returned only to them); once attached they're readable only by
  logged-in users who can see the submission. Nothing serves them publicly.
"""

import datetime as dt
import hmac
import ipaddress
import json
import re
import secrets
import string
import threading
import time
import unicodedata
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ChecklistDefinition, ChecklistPublicLink, ChecklistSubmission, User
from app.services import checklist_schemas
from app.team.media import PUBLIC_PENDING_OWNER_TYPE
from app.team.models import TeamMedia
from app.timeutil import utcnow
from app.store.settings_view import effective_public_base_url

NOT_ACTIVE = "This link is not active."

# Links made before the short codes (Oct 1): 22 random letters/digits.
TOKEN_LENGTH = 22
_TOKEN_ALPHABET = string.ascii_letters + string.digits
_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{%d}$" % TOKEN_LENGTH)
# New links and rotations: 4 digits.
CODE_LENGTH = 4
_CODE_RE = re.compile(r"^[0-9]{%d}$" % CODE_LENGTH)
# "{slug}-{code or old token}" as the public API receives it.
_KEY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?-(?:[0-9]{%d}|[A-Za-z0-9]{%d})$" % (CODE_LENGTH, TOKEN_LENGTH))
_SHORT_KEY_ANY_CASE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?-[0-9]{%d}$" % CODE_LENGTH)
_MEDIA_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

SLUG_MIN, SLUG_MAX = 2, 20
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

NAME_MIN, NAME_MAX = 2, 60
MAX_NAMES = 30
NAME_MODES = ("list", "free_text")
NAME_PUNCTUATION = " .'-"

DEFAULT_MAX_PER_HOUR = 6
MAX_PER_HOUR_LIMIT = 60

# Per IP, every public API call (form, submit, photo): REQUESTS per WINDOW.
IP_LIMIT = (20, 600)
# Per IP, the page itself and the pictures in the form (cheap, more of them).
IP_PAGE_LIMIT = (60, 600)
# All public traffic together - bounds abuse even if CF-Connecting-IP is
# forged by someone reaching the server without going through Cloudflare.
GLOBAL_LIMIT = (600, 600)
# Failed lookups (unknown / wrong / disabled link) per IP, and for all IPs
# together: over either, every lookup is 429 until the window drains, so the
# 4-digit code can't be walked.
FAILED_IP_LIMIT = (10, 600)
FAILED_GLOBAL_LIMIT = (200, 600)
# Photo uploads per link per hour.
MEDIA_PER_LINK_HOUR = 40

# Request body caps (the photo upload uses team media's own caps).
MAX_SUBMIT_BYTES = 256 * 1024
MAX_ANSWERS = 1000
MAX_ANSWER_KEY = 200
MAX_ANSWER_VALUE = 10000
MAX_MEDIA_KEYS = 50

# One-word link defaults for the legacy built-in checklist types, prefilled
# in the admin panel when no link exists yet (never created automatically).
# No staff names: the allowed-name list always starts empty.
SUGGESTED = {
    "street_kitchen_fsa": {"slug": "kitchen"},
    "street_kitchen_closing": {"slug": "chef"},
    "spice_town_closing": {"slug": "store"},
}

RATE_LIMITED = "Too many tries from this device. Wait a few minutes and try again."
LINK_LIMITED = "This checklist link has reached its limit for this hour. Try again later."


class PublicError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Rate limiting ------------------------------------------------------------------


class SlidingWindowLimiter:
    """In-process sliding window (one backend process serves everything)."""

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> bool:
        """Records one request; False when it's over the limit (then it isn't
        recorded, so a blocked client's window still drains)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) > 50000:
                    self._prune(now, window)
                q = self._hits[key] = deque()
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def _prune(self, now: float, window: int) -> None:
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - window]:
            del self._hits[key]

    def full(self, key: str, limit: int, window: int) -> bool:
        """True when `key` already has `limit` hits in the window (records nothing)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return False
            while q and q[0] <= now - window:
                q.popleft()
            return len(q) >= limit

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def _limit_key(ip: str) -> str:
    """The rate-limit identity of an address: an IPv4 address as is, an IPv6
    address as its /64. One IPv6 client (a phone, a VPS) usually holds a
    whole /64, so keying on the full address would let it walk the 4-digit
    code with a fresh address per guess and never hit FAILED_IP_LIMIT."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{addr}/64", strict=False))
    return str(addr)


def client_ip(headers, peer: Optional[str]) -> str:
    """Cloudflare's CF-Connecting-IP when present and valid, else the TCP
    peer - as a rate-limit key (an IPv6 address becomes its /64, see
    _limit_key)."""
    raw = (headers.get("cf-connecting-ip") or "").strip()
    if raw:
        try:
            return _limit_key(str(ipaddress.ip_address(raw)))
        except ValueError:
            pass
    return _limit_key(peer) if peer else "unknown"


def check_ip(ip: str, page: bool = False) -> None:
    """Raises PublicError(429) when this IP (or all public traffic) is over its limit."""
    limit, window = IP_PAGE_LIMIT if page else IP_LIMIT
    bucket = "page" if page else "api"
    # Per IP FIRST: a request already over its own IP's limit must not count
    # toward the global cap, or one client hammering the link (no forged
    # header needed) would use up the global budget and lock everyone out.
    if not limiter.hit(f"ip:{bucket}:{ip}", limit, window):
        raise PublicError(429, RATE_LIMITED)
    if not limiter.hit(f"global:{bucket}", GLOBAL_LIMIT[0], GLOBAL_LIMIT[1]):
        raise PublicError(429, RATE_LIMITED)


def check_failed(ip: str) -> None:
    """Raises PublicError(429) when this IP (or everyone together) has too
    many failed link lookups. Called before every lookup."""
    if limiter.full(f"fail:ip:{ip}", FAILED_IP_LIMIT[0], FAILED_IP_LIMIT[1]):
        raise PublicError(429, RATE_LIMITED)
    if limiter.full("fail:global", FAILED_GLOBAL_LIMIT[0], FAILED_GLOBAL_LIMIT[1]):
        raise PublicError(429, RATE_LIMITED)


def record_failed(ip: str) -> None:
    """One failed lookup (unknown, wrong, disabled or rotated link) from this IP."""
    limiter.hit(f"fail:ip:{ip}", FAILED_IP_LIMIT[0], FAILED_IP_LIMIT[1])
    limiter.hit("fail:global", FAILED_GLOBAL_LIMIT[0], FAILED_GLOBAL_LIMIT[1])


# --- Names ----------------------------------------------------------------------------


def _name_char_ok(ch: str) -> bool:
    return ch in NAME_PUNCTUATION or unicodedata.category(ch)[0] in ("L", "M")


def clean_name(raw) -> str:
    """A person's name as plain text, or ValueError. NFC-normalized, spaces
    collapsed; 2-60 characters; letters (any script), spaces and . ' - only;
    at least two letters."""
    if not isinstance(raw, str):
        raise ValueError("Enter your name.")
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not name:
        raise ValueError("Enter your name.")
    if len(name) < NAME_MIN or len(name) > NAME_MAX:
        raise ValueError(f"Your name must be {NAME_MIN} to {NAME_MAX} characters.")
    if not all(_name_char_ok(ch) for ch in name):
        raise ValueError("Use only letters, spaces, dots, apostrophes and hyphens in your name.")
    if sum(1 for ch in name if unicodedata.category(ch)[0] == "L") < 2:
        raise ValueError("Enter your name.")
    return name


def display_name(stored: Optional[str]) -> str:
    """A stored public name made safe for plain-text output (WhatsApp)."""
    text = " ".join(unicodedata.normalize("NFC", stored or "").split())
    text = "".join(ch for ch in text if _name_char_ok(ch))[:NAME_MAX].strip()
    return text or "Unknown"


def resolve_name(link: ChecklistPublicLink, raw) -> str:
    """The name to store for a submission on this link (ValueError if not accepted)."""
    if link.name_mode == "list":
        names = allowed_names(link)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Pick your name.")
        wanted = " ".join(unicodedata.normalize("NFC", raw).split()).casefold()
        for name in names:
            if name.casefold() == wanted:
                return name
        raise ValueError("Pick your name from the list.")
    return clean_name(raw)


def allowed_names(link: ChecklistPublicLink) -> List[str]:
    try:
        names = json.loads(link.allowed_names_json or "[]")
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


# --- Tokens and lookup ----------------------------------------------------------------


def new_token() -> str:
    """An old-style 22-character token (only tests and old rows use these now)."""
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def is_trivial_code(code: str) -> bool:
    """0000/1111-style (one digit), 1234/9876-style (a straight run), or
    1212-style (a repeated pair): too easy to guess first."""
    digits = [int(ch) for ch in code]
    steps = {b - a for a, b in zip(digits, digits[1:])}
    return len(steps) == 1 and steps <= {-1, 0, 1} or code[:2] == code[2:]


def is_short_code(token: Optional[str]) -> bool:
    return bool(token) and bool(_CODE_RE.match(token))


def new_code(db: Session, avoid: Optional[str] = None) -> str:
    """A random 4-digit code (secrets), not trivial, not used by any link, not `avoid`."""
    used = set(db.execute(select(ChecklistPublicLink.token)).scalars().all())
    if avoid:
        used.add(avoid)
    for _ in range(1000):
        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        if code[0] != "0" and not is_trivial_code(code) and code not in used:
            return code
    raise RuntimeError("No free link code")


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def find_active_link(db: Session, key) -> Optional[ChecklistPublicLink]:
    """The enabled link for this key whose checklist is active and published,
    else None. `key` is "{slug}-{code}" (both must match the link's current
    slug and code), or a bare old-style 22-character token (the first page
    version sent only that; a 4-digit code alone never matches). Every link
    is compared in constant time (no early exit)."""
    slug, token = "", ""
    # Short links are typed from memory: /c/Kitchen-4821 (a phone often
    # capitalises the first letter) means /c/kitchen-4821. Only the short
    # form is lowercased - an old long token is case-sensitive.
    if isinstance(key, str) and _SHORT_KEY_ANY_CASE_RE.match(key):
        key = key.lower()
    if isinstance(key, str) and _KEY_RE.match(key):
        slug, token = split_path(key)
    elif isinstance(key, str) and _TOKEN_RE.match(key):
        token = key
    if not token:
        token = "!" * TOKEN_LENGTH
    found = None
    for link in db.execute(select(ChecklistPublicLink)).scalars().all():
        token_ok = _eq(token, link.token or "")
        slug_ok = _eq(slug, link.slug or "")
        legacy_bare = not slug and not is_short_code(link.token)
        if token_ok and (slug_ok or legacy_bare) and found is None:
            found = link
    if found is None or not found.enabled:
        return None
    try:
        checklist_schemas.get_schema(found.checklist_type)
    except ValueError:
        return None
    return found


def split_path(path: str) -> Tuple[str, str]:
    """"kitchen-service-AbC..." -> ("kitchen-service", "AbC...") (token has no "-")."""
    slug, sep, token = (path or "").rpartition("-")
    return (slug, token) if sep else ("", path or "")


def find_page_link(db: Session, path: str) -> Optional[ChecklistPublicLink]:
    """The link for a page URL /c/{slug}-{code}: code AND current slug must match."""
    slug, token = split_path(path)
    if not slug or not token:
        return None
    return find_active_link(db, f"{slug}-{token}")


def link_key(link: ChecklistPublicLink) -> str:
    return f"{link.slug}-{link.token}"


def link_path(link: ChecklistPublicLink) -> str:
    """/c/{slug}-{code}; on the shared service (platform contract 4.4)
    /c/{STORE_CODE}/{slug}-{code}, so the page knows which store it is."""
    from app.tenancy import current_store_code, is_multi_tenant

    if is_multi_tenant():
        return f"/c/{current_store_code()}/{link_key(link)}"
    return f"/c/{link_key(link)}"


def link_url(link: ChecklistPublicLink) -> str:
    from app.tenancy import is_multi_tenant

    if is_multi_tenant():
        from app.config import platform_public_url

        return f"{platform_public_url()}{link_path(link)}"
    base = effective_public_base_url()
    return f"{base}{link_path(link)}"


# --- Public form ----------------------------------------------------------------------

_TEAM_MEDIA_URL_RE = re.compile(r"^/api/team/media/([A-Za-z0-9_-]+)$")


def _public_image_url(key: str, url):
    if isinstance(url, str):
        match = _TEAM_MEDIA_URL_RE.match(url.strip())
        if match:
            return f"/api/public/checklists/{key}/images/{match.group(1)}"
    return url


def public_schema(link: ChecklistPublicLink) -> dict:
    """The current published schema (same JSON as /api/checklists/schema),
    with settings cut to what the form uses and the form's own pictures
    pointed at the public image route."""
    schema = json.loads(json.dumps(checklist_schemas.get_schema(link.checklist_type)))
    settings = schema.get("settings") or {}
    schema["settings"] = {"all_as_usual": settings.get("all_as_usual", True)}
    for section in schema.get("sections") or []:
        for item in section.get("items") or []:
            if "image_url" in item:
                item["image_url"] = _public_image_url(link_key(link), item["image_url"])
    return schema


def public_view(link: ChecklistPublicLink) -> dict:
    schema = public_schema(link)
    return {
        "checklist_type": link.checklist_type,
        "title": schema.get("title"),
        "description": schema.get("description"),
        "schema": schema,
        "name_mode": link.name_mode,
        "allowed_names": allowed_names(link) if link.name_mode == "list" else [],
    }


def has_photo_question(link: ChecklistPublicLink) -> bool:
    from app.services import checklist_engine as engine

    schema = checklist_schemas.items_schema(link.checklist_type)
    return any(item.get("type") == "file_upload" for _, item in engine.iter_items(schema))


def form_image(db: Session, link: ChecklistPublicLink, media_token: str) -> Optional[TeamMedia]:
    """A picture the form shows (an "image" item of the CURRENT version of this
    link's checklist, uploaded in the builder) - nothing else."""
    if not isinstance(media_token, str) or not _MEDIA_TOKEN_RE.match(media_token):
        return None
    wanted = f"/api/team/media/{media_token}"
    schema = checklist_schemas.get_schema(link.checklist_type)
    used = any(
        isinstance(item.get("image_url"), str) and item["image_url"].strip() == wanted
        for section in schema.get("sections") or []
        for item in section.get("items") or []
    )
    if not used:
        return None
    definition_id = db.execute(
        select(ChecklistDefinition.id).where(ChecklistDefinition.checklist_type == link.checklist_type)
    ).scalar_one_or_none()
    media = db.execute(select(TeamMedia).where(TeamMedia.token == media_token)).scalars().first()
    if media is None or definition_id is None:
        return None
    if media.owner_type != "checklist_definition" or media.owner_id != definition_id:
        return None
    return media


# --- Submissions ----------------------------------------------------------------------


def parse_submit_body(raw: bytes) -> dict:
    """{name, answers, definition_version?, media_keys?} with every field
    type- and size-checked (PublicError 400/422 otherwise)."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # RecursionError: deeply nested JSON ("[[[[..." within the size cap)
        raise PublicError(400, "Send a JSON object.")
    if not isinstance(body, dict):
        raise PublicError(400, "Send a JSON object.")
    answers = body.get("answers")
    if not isinstance(answers, dict) or len(answers) > MAX_ANSWERS:
        raise PublicError(422, "The answers aren't in the right format.")
    for key, value in answers.items():
        if not isinstance(key, str) or len(key) > MAX_ANSWER_KEY:
            raise PublicError(422, "The answers aren't in the right format.")
        if not isinstance(value, str) or len(value) > MAX_ANSWER_VALUE:
            raise PublicError(422, "The answers aren't in the right format.")
    version = body.get("definition_version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise PublicError(422, "definition_version must be a whole number.")
    keys = body.get("media_keys") or {}
    if not isinstance(keys, dict) or len(keys) > MAX_MEDIA_KEYS:
        raise PublicError(422, "The photos aren't in the right format.")
    for key, value in keys.items():
        if not isinstance(key, str) or not isinstance(value, str) or len(key) > 20 or len(value) > 100:
            raise PublicError(422, "The photos aren't in the right format.")
    return {"name": body.get("name"), "answers": answers, "definition_version": version, "media_keys": keys}


def submissions_last_hour(db: Session, link: ChecklistPublicLink) -> int:
    since = utcnow() - dt.timedelta(hours=1)
    return db.execute(
        select(func.count(ChecklistSubmission.id)).where(
            ChecklistSubmission.checklist_type == link.checklist_type,
            ChecklistSubmission.via_public_link.is_(True),
            ChecklistSubmission.created_at >= since,
        )
    ).scalar_one()


def check_link_hourly(db: Session, link: ChecklistPublicLink) -> None:
    if submissions_last_hour(db, link) >= max(1, link.max_per_hour or DEFAULT_MAX_PER_HOUR):
        raise PublicError(429, LINK_LIMITED)


def check_media_rate(link: ChecklistPublicLink) -> None:
    if not limiter.hit(f"link-media:{link.id}", MEDIA_PER_LINK_HOUR, 3600):
        raise PublicError(429, LINK_LIMITED)


def mark_used(db: Session, link: ChecklistPublicLink) -> None:
    link.last_used_at = utcnow()
    db.commit()


# --- Admin ----------------------------------------------------------------------------


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _slug_words(definition: ChecklistDefinition) -> List[str]:
    text = (definition.short_name or definition.title or "").lower()
    return [w[:SLUG_MAX] for w in re.findall(r"[a-z0-9]+", text) if len(w) >= SLUG_MIN]


def _suggested(definition: ChecklistDefinition) -> dict:
    """One short word: the preset, else the title's first word ("Store
    Closing" -> "store")."""
    preset = SUGGESTED.get(definition.checklist_type, {})
    words = _slug_words(definition)
    slug = preset.get("slug") or (words[0] if words else "checklist")
    return {"slug": slug, "allowed_names": list(preset.get("allowed_names", []))}


def _free_slug(db: Session, definition: ChecklistDefinition, first: str) -> str:
    """`first` if no link uses it, else another word of the title ("Kitchen
    Closing - Chef" -> "chef"), else the word plus a number."""
    taken = set(db.execute(select(ChecklistPublicLink.slug)).scalars().all())
    words = _slug_words(definition)
    for slug in [first] + words[::-1] + ["checklist"]:
        if slug not in taken:
            return slug
    for n in range(2, 1000):
        slug = f"{first[: SLUG_MAX - 4]}{n}"
        if slug not in taken:
            return slug
    return f"{first[: SLUG_MAX - 5]}-{secrets.randbelow(9000) + 1000}"


def _get_link(db: Session, checklist_type: str) -> Optional[ChecklistPublicLink]:
    return db.execute(
        select(ChecklistPublicLink).where(ChecklistPublicLink.checklist_type == checklist_type)
    ).scalar_one_or_none()


def admin_out(db: Session, definition: ChecklistDefinition, link: Optional[ChecklistPublicLink]) -> dict:
    if link is None:
        suggested = _suggested(definition)
        return {
            "checklist_type": definition.checklist_type,
            "exists": False,
            "enabled": False,
            "slug": suggested["slug"],
            "name_mode": "list",
            "allowed_names": suggested["allowed_names"],
            "max_per_hour": DEFAULT_MAX_PER_HOUR,
            "url": None,
            "path": None,
            "code": None,
            "short": False,
            "last_used_at": None,
            "submissions_last_hour": 0,
            "created_at": None,
            "created_by": None,
            "updated_at": None,
            "updated_by": None,
            "rotated_at": None,
            "rotated_by": None,
        }
    return {
        "checklist_type": link.checklist_type,
        "exists": True,
        "enabled": bool(link.enabled),
        "slug": link.slug,
        "name_mode": link.name_mode,
        "allowed_names": allowed_names(link),
        "max_per_hour": link.max_per_hour,
        "url": link_url(link),
        "path": link_path(link),
        # The 4 digits (None for an old long link - rotate to get a short one).
        "code": link.token if is_short_code(link.token) else None,
        "short": is_short_code(link.token),
        "last_used_at": _iso(link.last_used_at),
        "submissions_last_hour": submissions_last_hour(db, link),
        "created_at": _iso(link.created_at),
        "created_by": link.created_by,
        "updated_at": _iso(link.updated_at),
        "updated_by": link.updated_by,
        "rotated_at": _iso(link.rotated_at),
        "rotated_by": link.rotated_by,
    }


def _definition(db: Session, checklist_type: str) -> ChecklistDefinition:
    from app.services.checklist_admin import _get

    return _get(db, checklist_type)


def get_link_admin(db: Session, checklist_type: str) -> dict:
    definition = _definition(db, checklist_type)
    return admin_out(db, definition, _get_link(db, checklist_type))


def _validate(db: Session, payload: dict, current: dict, link_id: Optional[int]) -> Tuple[dict, List[str]]:
    problems: List[str] = []
    known = {"enabled", "slug", "name_mode", "allowed_names", "max_per_hour"}
    unknown = sorted(k for k in payload if k not in known)
    if unknown:
        problems.append(f"Unknown field(s): {', '.join(unknown)}")
    out = dict(current)
    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            problems.append("enabled must be true or false")
        else:
            out["enabled"] = payload["enabled"]
    if "slug" in payload:
        slug = payload["slug"]
        if not isinstance(slug, str):
            problems.append("The link name must be text")
        else:
            slug = slug.strip().lower()
            if not (SLUG_MIN <= len(slug) <= SLUG_MAX) or not _SLUG_RE.match(slug) or "--" in slug:
                problems.append(
                    f"The link word must be {SLUG_MIN}-{SLUG_MAX} lowercase letters, numbers or single hyphens"
                )
            else:
                taken = db.execute(
                    select(ChecklistPublicLink.id).where(ChecklistPublicLink.slug == slug)
                ).scalar_one_or_none()
                if taken is not None and taken != link_id:
                    problems.append("Another checklist's link already uses that name")
                out["slug"] = slug
    if "name_mode" in payload:
        if payload["name_mode"] not in NAME_MODES:
            problems.append('name_mode must be "list" or "free_text"')
        else:
            out["name_mode"] = payload["name_mode"]
    if "allowed_names" in payload:
        names = payload["allowed_names"]
        if not isinstance(names, list):
            problems.append("allowed_names must be a list")
        else:
            cleaned: List[str] = []
            seen = set()
            for raw in names:
                try:
                    name = clean_name(raw)
                except ValueError:
                    problems.append(f"Not a valid name: {str(raw)[:NAME_MAX]!r} (letters, spaces, . ' - only, 2-60)")
                    continue
                if name.casefold() not in seen:
                    seen.add(name.casefold())
                    cleaned.append(name)
            if len(cleaned) > MAX_NAMES:
                problems.append(f"At most {MAX_NAMES} names")
            out["allowed_names"] = cleaned
    if "max_per_hour" in payload:
        value = payload["max_per_hour"]
        if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= MAX_PER_HOUR_LIMIT):
            problems.append(f"The hourly limit must be a whole number from 1 to {MAX_PER_HOUR_LIMIT}")
        else:
            out["max_per_hour"] = value
    if out["name_mode"] == "list" and out["enabled"] and not out["allowed_names"]:
        problems.append("Add at least one name, or let people type their name")
    return out, problems


def put_link(db: Session, user: User, checklist_type: str, payload: dict) -> dict:
    from app.services.checklist_admin import AdminError

    definition = _definition(db, checklist_type)
    link = _get_link(db, checklist_type)
    if link is None:
        suggested = _suggested(definition)
        current = {
            "enabled": False,
            "slug": suggested["slug"],
            "name_mode": "list",
            "allowed_names": suggested["allowed_names"],
            "max_per_hour": DEFAULT_MAX_PER_HOUR,
        }
        if "slug" not in payload:
            current["slug"] = _free_slug(db, definition, current["slug"])
    else:
        current = {
            "enabled": bool(link.enabled),
            "slug": link.slug,
            "name_mode": link.name_mode,
            "allowed_names": allowed_names(link),
            "max_per_hour": link.max_per_hour,
        }
    values, problems = _validate(db, payload, current, link.id if link is not None else None)
    if problems:
        raise AdminError(422, problems[0], problems)
    now = utcnow()
    if link is None:
        link = ChecklistPublicLink(
            checklist_type=checklist_type,
            token=new_code(db),
            created_at=now,
            created_by=user.username,
        )
        db.add(link)
    link.enabled = values["enabled"]
    link.slug = values["slug"]
    link.name_mode = values["name_mode"]
    link.allowed_names_json = json.dumps(values["allowed_names"])
    link.max_per_hour = values["max_per_hour"]
    link.updated_at = now
    link.updated_by = user.username
    db.commit()
    db.refresh(link)
    return admin_out(db, definition, link)


def rotate_link(db: Session, user: User, checklist_type: str) -> dict:
    from app.services.checklist_admin import AdminError

    definition = _definition(db, checklist_type)
    link = _get_link(db, checklist_type)
    if link is None:
        raise AdminError(404, "This checklist has no public link")
    link.token = new_code(db, avoid=link.token)
    now = utcnow()
    link.rotated_at = now
    link.rotated_by = user.username
    link.updated_at = now
    link.updated_by = user.username
    db.commit()
    db.refresh(link)
    return admin_out(db, definition, link)


def delete_link(db: Session, user: User, checklist_type: str) -> dict:
    definition = _definition(db, checklist_type)
    link = _get_link(db, checklist_type)
    if link is not None:
        # Unused photos uploaded through it go with it (the nightly prune
        # would remove them anyway).
        db.query(TeamMedia).filter(
            TeamMedia.owner_type == PUBLIC_PENDING_OWNER_TYPE, TeamMedia.owner_id == link.id
        ).update({TeamMedia.owner_id: None}, synchronize_session=False)
        db.delete(link)
        db.commit()
    return admin_out(db, definition, None)


def list_links(db: Session) -> List[dict]:
    """Every link (for an overview of which checklists have one)."""
    from app.services.checklist_admin import ensure_seeded

    ensure_seeded(db)
    links = {l.checklist_type: l for l in db.execute(select(ChecklistPublicLink)).scalars().all()}
    out = []
    for definition in db.execute(select(ChecklistDefinition).order_by(ChecklistDefinition.sort_order)).scalars():
        row = admin_out(db, definition, links.get(definition.checklist_type))
        row["title"] = definition.title
        row["active"] = bool(definition.active)
        out.append(row)
    return out
