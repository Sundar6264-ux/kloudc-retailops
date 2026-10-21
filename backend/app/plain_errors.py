"""Plain-language error replies (pre-production gaps G16, G25 and G27).

Store users and App Review read these sentences, so they never carry
validator jargon ("Input should be a valid integer"), a bare HTTP phrase
("Not Found", "Internal Server Error") or the value that was typed.

Every change is additive (docs/PLATFORM_CONTRACT.md "As built - S2"):

- A form error (422) keeps FastAPI's ``detail`` list with ``loc`` and
  ``type``; each item's ``msg`` becomes a short sentence without the field
  name (older app builds show ``"<field>: <msg>"``), the typed ``input`` is
  no longer echoed, and the body gains ``error: "invalid_input"``,
  ``message`` (one sentence naming the field) and ``fields``
  (field -> sentence).
- A bare HTTP phrase gains ``error`` and ``message``. ``detail`` keeps
  "Not Found" / "Method Not Allowed" only when no route matched: older app
  builds read exactly that as "this server doesn't have the feature yet".
  A route's own 404 without a sentence, and every other bare phrase, get a
  sentence in ``detail`` too.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple

# Field names as the screens label them. Unknown names fall back to the
# name with spaces ("unit_cost" -> "Unit cost").
FIELD_LABELS: Dict[str, str] = {
    "store_name": "Store name",
    "store_short_name": "Short name",
    "store_address": "Address",
    "store_phone": "Phone",
    "store_email": "Email",
    "email_from_name": "Sender name",
    "logo_url": "Logo link",
    "icon_url": "Icon link",
    "brand_color": "Main colour",
    "brand_secondary_color": "Second colour",
    "labels_url": "Label Scanner link",
    "ai_enabled": "AI features",
    "store_timezone": "Time zone",
    "pos_provider": "POS",
    "current_password": "Current password",
    "new_password": "New password",
    "password": "Password",
    "username": "Username",
    "store_code": "Store ID",
    "qty": "Quantity",
    "quantity": "Quantity",
    "vendor_item_code": "Vendor code",
    "sku": "Vendor code",
    "upc": "Barcode",
    "barcode": "Barcode",
}

# Bare phrases Starlette/FastAPI send when nobody wrote a sentence.
BARE_PHRASES: Dict[int, Tuple[str, str, str]] = {
    # status: (phrase, error code, sentence)
    400: ("Bad Request", "bad_request", "That request couldn't be read. Try again."),
    401: ("Unauthorized", "not_logged_in", "Please sign in again."),
    403: ("Forbidden", "forbidden", "You don't have access to this."),
    404: ("Not Found", "not_found", "That page or item wasn't found. It may have been removed."),
    405: ("Method Not Allowed", "not_found", "That page or item wasn't found. It may have been removed."),
    413: ("Request Entity Too Large", "too_large", "That file or form is too large."),
    429: ("Too Many Requests", "rate_limited", "Too many tries. Wait a minute and try again."),
    500: ("Internal Server Error", "server_error", "Something went wrong on our side. Try again in a minute."),
    502: ("Bad Gateway", "unavailable", "RetailOps can't be reached right now. Try again in a minute."),
    503: ("Service Unavailable", "unavailable", "RetailOps can't be reached right now. Try again in a minute."),
}

def field_label(name: Any) -> str:
    if not isinstance(name, str) or not name or name == "body":
        return "The form"
    return FIELD_LABELS.get(name) or name.replace("_", " ").strip().capitalize()


def _field_of(loc: Iterable[Any]) -> Optional[str]:
    """The last named part of a pydantic location (list indexes skipped)."""
    names = [p for p in (loc or ()) if isinstance(p, str)]
    names = [p for p in names if p not in ("body", "query", "path", "header", "cookie")]
    return names[-1] if names else None


def _n(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def plain_message(err: Dict[str, Any]) -> str:
    """One pydantic error as a short sentence without the field name, e.g.
    "Must be a whole number." or "Use at most 120 characters."."""
    t = str(err.get("type") or "")
    ctx = err.get("ctx") or {}
    raw = str(err.get("msg") or "")
    if t == "missing":
        return "This is required."
    if t in ("string_too_short",):
        n = ctx.get("min_length")
        return "This can't be blank." if n in (None, 1) else f"Use at least {_n(n)} characters."
    if t in ("string_too_long",):
        return f"Use at most {_n(ctx.get('max_length'))} characters."
    if t in ("too_short",):
        n = ctx.get("min_length")
        return "Add at least one." if n in (None, 1) else f"Add at least {_n(n)}."
    if t in ("too_long",):
        return f"Use at most {_n(ctx.get('max_length'))}."
    if t.startswith("int_"):
        return "Must be a whole number."
    if t.startswith(("float_", "decimal_")) or t in ("finite_number",):
        return "Must be a number."
    if t.startswith("bool_"):
        return "Must be yes or no."
    if t == "greater_than_equal":
        return f"Must be {_n(ctx.get('ge'))} or more."
    if t == "greater_than":
        return f"Must be more than {_n(ctx.get('gt'))}."
    if t == "less_than_equal":
        return f"Must be {_n(ctx.get('le'))} or less."
    if t == "less_than":
        return f"Must be less than {_n(ctx.get('lt'))}."
    if t == "multiple_of":
        return f"Must be a multiple of {_n(ctx.get('multiple_of'))}."
    if t in ("literal_error", "enum"):
        expected = str(ctx.get("expected") or "").replace("'", "")
        return f"Choose one of: {expected}." if expected else "Choose one of the listed values."
    if t == "string_pattern_mismatch":
        return "That isn't in the right format."
    if t.startswith("date_") or t == "date_type":
        return "Must be a date, like 2026-10-21."
    if t.startswith("datetime_"):
        return "Must be a date and time."
    if t.startswith("time_"):
        return "Must be a time, like 09:30."
    if t.startswith("url_"):
        return "Must be a web address starting with https://."
    if t in ("json_invalid", "json_type"):
        return "That couldn't be read. Try again."
    if t == "extra_forbidden":
        return "This can't be set here."
    if t in ("string_type", "string_unicode"):
        return "Must be text."
    if t in ("list_type", "set_type", "tuple_type"):
        return "Must be a list."
    if t in ("dict_type", "model_type", "model_attributes_type", "dataclass_type"):
        return "Has the wrong kind of value."
    if t in ("value_error", "assertion_error"):
        # Our own validators already write sentences: keep them, without
        # pydantic's prefix.
        for prefix in ("Value error, ", "Assertion failed, "):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        raw = raw.strip()
        if raw:
            raw = raw[0].upper() + raw[1:]
            return raw if raw.endswith((".", "?", "!")) else raw + "."
    return "That isn't a valid value."


def _join(label: str, sentence: str) -> str:
    """"Quantity" + "Must be a whole number." -> "Quantity: must be a whole number."."""
    if label == "The form":
        return sentence
    first = sentence[:1] if sentence[1:2].isupper() else sentence[:1].lower()
    return f"{label}: {first}{sentence[1:]}"


def validation_body(errors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The 422 body for a list of pydantic errors (``exc.errors()``)."""
    detail: List[Dict[str, Any]] = []
    fields: Dict[str, str] = {}
    lines: List[str] = []
    for err in errors or []:
        sentence = plain_message(err)
        item = {k: v for k, v in err.items() if k not in ("input", "url", "ctx", "msg")}
        item["loc"] = list(err.get("loc") or ())
        item["msg"] = sentence
        detail.append(item)
        name = _field_of(err.get("loc") or ())
        key = name or "form"
        if key not in fields:
            fields[key] = sentence
            lines.append(_join(field_label(name), sentence))
    if not lines:
        lines = ["Some values are not valid."]
    message = lines[0] if len(lines) == 1 else f"{lines[0]} ({len(lines) - 1} more to fix)"
    return {"detail": detail, "error": "invalid_input", "message": message, "fields": fields}


def setting_error_message(key: str, errors: List[Dict[str, Any]]) -> str:
    """A store setting refused by its validator, as one sentence (G16)."""
    sentence = plain_message(errors[0]) if errors else "That isn't a valid value."
    return _join(field_label(key), sentence)


def bare_phrase_body(status: int, detail: Any, route_matched: bool) -> Optional[Dict[str, Any]]:
    """A reply body for an HTTPException whose detail is only the HTTP
    phrase (or empty). None when the detail is already a sentence."""
    known = BARE_PHRASES.get(status)
    if known is None:
        return None
    phrase, code, sentence = known
    if detail not in (None, "", phrase):
        return None
    # 405 always means "no route for this method" (the path matched another).
    keep_phrase = phrase == "Method Not Allowed" or (not route_matched and phrase == "Not Found")
    return {"detail": phrase if keep_phrase else sentence, "error": code, "message": sentence}
