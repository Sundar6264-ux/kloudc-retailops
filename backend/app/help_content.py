"""Help content (docs/DESIGN_SYSTEM.md 4 and 5.2): one source, three readers.

``content/help/*.json`` at the repository root is read by this server (GET
/api/help, GET /api/public/help, and the concept terms merged into
/api/glossary), by the website (build-time import) and by the app (a bundled
copy, refreshed from /api/help when the server's version is newer).

The files are loaded once, checked against ``schema.json`` (a small subset of
JSON Schema 2020-12 is enforced here, so no extra package is needed), and
served with this store's words filled in: ``{pos}``, ``{store}``,
``{store_city}`` and ``{support_email}``.

In the Docker image the folder is ``/app/content/help`` (the Dockerfile copies
``content/``); ``HELP_CONTENT_DIR`` points somewhere else when needed. A
missing folder never stops the server: help is then empty and a warning is
logged.
"""

import copy
import gzip
import hashlib
import json
import logging
import os
import pathlib
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_HELP_DIR = pathlib.Path(__file__).resolve().parents[2] / "content" / "help"
ALLOWED_PLACEHOLDERS = ("{pos}", "{store}", "{store_city}", "{support_email}")
SUPPORT_EMAIL = "support@kloudc.com"

FILES = {
    "manifest": "manifest.json",
    "pages": "pages.json",
    "getting_started": "getting-started.json",
    "faq": "faq.json",
    "troubleshooting": "troubleshooting.json",
    "glossary": "glossary.json",
    "glossary_categories": "glossary-categories.json",
    "contact": "contact.json",
}


def help_dir() -> pathlib.Path:
    override = os.environ.get("HELP_CONTENT_DIR", "").strip()
    return pathlib.Path(override) if override else DEFAULT_HELP_DIR


class HelpContentError(ValueError):
    pass


# ----------------------------------------------------------------- loading


def _read(path: pathlib.Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_raw(directory: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Every file as parsed JSON (articles as a list, sorted by order then
    title). Raises FileNotFoundError when the folder or a file is missing."""
    d = pathlib.Path(directory) if directory else help_dir()
    if not d.is_dir():
        raise FileNotFoundError(str(d))
    out: Dict[str, Any] = {name: _read(d / fname) for name, fname in FILES.items()}
    articles = [_read(p) for p in sorted((d / "articles").glob("*.json"))]
    articles.sort(key=lambda a: (a.get("order", 999), a.get("title", "")))
    out["articles"] = articles
    return out


@lru_cache(maxsize=1)
def _content() -> Dict[str, Any]:
    try:
        raw = load_raw()
    except (FileNotFoundError, json.JSONDecodeError) as exc:  # pragma: no cover - deploy problem
        log.warning("help content not loaded from %s: %s", help_dir(), exc)
        return _empty()
    errors = schema_errors(raw)
    if errors:  # pragma: no cover - the tests keep this from shipping
        log.warning("help content has %d schema problem(s), first: %s", len(errors), errors[0])
    return raw


def _empty() -> Dict[str, Any]:
    return {
        "manifest": {"version": 0, "updated": None, "locale": "en"},
        "pages": [],
        "articles": [],
        "getting_started": {},
        "faq": [],
        "troubleshooting": [],
        "glossary": [],
        "glossary_categories": [],
        "contact": {"email": SUPPORT_EMAIL, "terms_url": ""},
    }


def content() -> Dict[str, Any]:
    """The loaded content (cached; a copy is cheap enough for callers that fill words)."""
    return _content()


def reload() -> None:
    """Drop the cache (tests, or after replacing the files)."""
    _content.cache_clear()


def concept_terms() -> List[dict]:
    return content().get("glossary", [])


def concept_categories() -> List[dict]:
    return content().get("glossary_categories", [])


# ----------------------------------------------------------------- schema (subset of JSON Schema 2020-12)

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, t) -> bool:
    if isinstance(t, list):
        return any(_type_ok(value, x) for x in t)
    py = _TYPES[t]
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, py)


def _resolve(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    if not ref.startswith("#/$defs/"):
        raise HelpContentError(f"unsupported $ref {ref}")
    return root["$defs"][ref.split("/")[-1]]


def validate_schema(value, schema: dict, root: dict, path: str = "$") -> List[str]:
    """type, enum, const, required, properties, additionalProperties (false
    or a schema), items, minItems, maxItems, minLength, maxLength, pattern,
    $ref (to #/$defs), anyOf."""
    schema = _resolve(schema, root)
    errs: List[str] = []
    if "anyOf" in schema:
        options = [validate_schema(value, s, root, path) for s in schema["anyOf"]]
        if all(options):
            errs.append(f"{path}: matches none of anyOf ({options[0][0]})")
        return errs
    t = schema.get("type")
    if t is not None and not _type_ok(value, t):
        return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate_schema(v, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing {k}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errs += validate_schema(v, props[k], root, f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unexpected key {k}")
            elif isinstance(extra, dict):
                errs += validate_schema(v, extra, root, f"{path}.{k}")
    return errs


def load_schema(directory: Optional[pathlib.Path] = None) -> dict:
    d = pathlib.Path(directory) if directory else help_dir()
    return _read(d / "schema.json")


def schema_errors(raw: Dict[str, Any], schema: Optional[dict] = None) -> List[str]:
    """Every file against its definition in schema.json."""
    if schema is None:
        try:
            schema = load_schema()
        except FileNotFoundError:
            return ["schema.json missing"]
    files = schema.get("properties", {})
    errs: List[str] = []
    for name, value in raw.items():
        if name not in files:
            errs.append(f"{name}: no schema")
            continue
        errs += validate_schema(value, files[name], schema, name)
    return errs


# ----------------------------------------------------------------- store words


# POS providers whose "name" is not a POS people work in: a CSV-only store
# reads "your POS" (website ready A7), never "CSV upload".
NO_POS_NAME = frozenset({"csv"})
POS_LABELS = {"toast": "Toast", "clover": "Clover", "csv": "inventory file"}


def store_pos() -> Optional[str]:
    """This store's POS provider id ("toast", "clover", "csv"), or None."""
    try:
        from app.pos.gating import pos_provider

        return (pos_provider() or "").strip().lower() or None
    except Exception:  # noqa: BLE001 - no store context
        return None


def pos_word() -> str:
    """How help and the glossary name this store's POS: "Toast", "Clover", or
    "your POS" for a store without one (CSV-only)."""
    from app.pos.gating import pos_name

    if store_pos() in NO_POS_NAME:
        return "your POS"
    return pos_name() or "the POS"


def only_for_pos(value, pos: Optional[str]):
    """A copy of help JSON without the entries marked for another POS
    (``"pos": ["toast"]``, schema.json; website ready A8). ``pos=None`` (a
    master, or a signed-out reader): every entry stays, and a marked one gets
    ``pos_label`` ("Toast only") so the reader sees which POS it is about."""
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, dict) and isinstance(v.get("pos"), list) and pos is not None and pos not in v["pos"]:
                continue
            out.append(only_for_pos(v, pos))
        return out
    if isinstance(value, dict):
        # A map of entries (a page's ``sections``) is filtered like a list
        # (G11): a tagged entry for another POS is left out by its key.
        out = {
            k: only_for_pos(v, pos)
            for k, v in value.items()
            if not (isinstance(v, dict) and isinstance(v.get("pos"), list) and pos is not None and pos not in v["pos"])
        }
        if pos is None and isinstance(value.get("pos"), list) and value["pos"]:
            out["pos_label"] = " / ".join(POS_LABELS.get(p, p.title()) for p in value["pos"]) + " only"
        return out
    return value


def store_words() -> Dict[str, str]:
    """This store's words, the same ones the glossary uses."""
    from app.config import get_settings

    s = get_settings()
    tz = getattr(s, "store_timezone", "") or getattr(s, "toast_timezone", "") or "America/New_York"
    store = (getattr(s, "store_name", "") or "").strip()
    return {
        "{pos}": pos_word(),
        "{store}": store or "your store",
        "{store_city}": tz.rsplit("/", 1)[-1].replace("_", " "),
        "{support_email}": content().get("contact", {}).get("email") or SUPPORT_EMAIL,
    }


# A word placeholder that starts a sentence ("{pos} says 0 or less on
# hand...") is capitalised there: "your POS says" read as a typo on a store
# without a named POS (App Store screenshot review, Oct 22). An email
# address is never capitalised.
_SENTENCE_START = re.compile(r"(^|[.!?]\s+)(\{(?:pos|store|store_city)\})")


def fill_text(text: str, words: Dict[str, str]) -> str:
    """`text` with its placeholders filled, capitalised at a sentence start."""

    def cap(m: "re.Match[str]") -> str:
        word = words.get(m.group(2))
        if word is None:
            return m.group(0)
        return m.group(1) + word[:1].upper() + word[1:]

    text = _SENTENCE_START.sub(cap, text)
    for k, v in words.items():
        text = text.replace(k, v)
    return text


def fill(value, words: Dict[str, str]):
    """A deep copy of any JSON value with every string's placeholders filled."""
    if isinstance(value, str):
        return fill_text(value, words)
    if isinstance(value, list):
        return [fill(v, words) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, words) for k, v in value.items()}
    return value


# ----------------------------------------------------------------- responses


def _terms_url() -> str:
    """The server's TERMS_URL (blank until a Terms page exists on kloudc.com)."""
    try:
        from app.config import get_settings

        value = getattr(get_settings(), "terms_url", "")
    except Exception:  # noqa: BLE001 - logged out on a shared server: no store settings
        from app.config import process_settings

        value = getattr(process_settings(), "terms_url", "")
    value = (value or "").strip()
    return value if value.startswith("https://") else ""


def generic_words() -> Dict[str, str]:
    """Words for logged-out help, where no store is known."""
    return {
        "{pos}": "your POS",
        "{store}": "your store",
        "{store_city}": "your store's",
        "{support_email}": content().get("contact", {}).get("email") or SUPPORT_EMAIL,
    }


def _contact(words: Dict[str, str]) -> dict:
    c = copy.deepcopy(content().get("contact", {}))
    terms = _terms_url()
    if terms:
        c["terms_url"] = terms
    return fill(c, words)


_ALL_POS = object()


def help_body(words: Optional[Dict[str, str]] = None, pos=_ALL_POS) -> dict:
    """GET /api/help: everything, this store's words filled in and entries
    for another POS left out (``pos``: this store's POS by default)."""
    words = words or store_words()
    if pos is _ALL_POS:
        pos = store_pos()
    c = only_for_pos(content(), pos)
    return {
        "version": c["manifest"].get("version", 0),
        "updated": c["manifest"].get("updated"),
        "locale": c["manifest"].get("locale", "en"),
        "pages": fill(c["pages"], words),
        "articles": fill(c["articles"], words),
        "getting_started": fill(c["getting_started"], words),
        "faq": fill(c["faq"], words),
        "troubleshooting": fill(c["troubleshooting"], words),
        "contact": _contact(words),
    }


def public_help_body(words: Optional[Dict[str, str]] = None) -> dict:
    """GET /api/public/help: the logged-out pages (public: true) and contact
    only, for the sign-in and sign-up screens of older app builds. Logged
    out there is no store, so the words are generic."""
    words = words or generic_words()
    c = only_for_pos(content(), None)
    return {
        "version": c["manifest"].get("version", 0),
        "updated": c["manifest"].get("updated"),
        "locale": c["manifest"].get("locale", "en"),
        "pages": fill([p for p in c["pages"] if p.get("public")], words),
        "contact": _contact(words),
    }


def etag_for(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return '"help-%s-%s"' % (body.get("version", 0), hashlib.sha256(raw).hexdigest()[:16])


def encoded(body: dict, accept_encoding: str = ""):
    """(bytes, gzip?) for a response body."""
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if "gzip" in (accept_encoding or "").lower() and len(raw) > 1024:
        return gzip.compress(raw, compresslevel=6), True
    return raw, False
