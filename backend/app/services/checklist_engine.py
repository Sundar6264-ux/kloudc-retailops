"""Checklist builder engine (Sep 30): the generic, Google-Forms-like checklist
format admins edit on the website, and everything computed from it - answer
validation, section branching, issue flagging, the "Chef: JL"-style details,
and the LEGACY blocks the released app (build 5) renders.

Pure functions over plain dicts, no database access (checklist_schemas.py is
the database-backed layer; docs/CHECKLIST_BUILDER_CONTRACT.md is the shared
contract with the website editor and the Flutter app).

Two stored formats:
  - legacy: the three original checklists exactly as served before the
    builder (sections with `grid` / `grids` / `questions` / `checkbox_groups`,
    top-level `issues_field` / `confirmation`). Seeded as their early versions.
  - items:  {"format": "items", "sections": [{"id", "title", "description",
    "items": [...]}], "legacy_order": bool}. Every version published from the
    editor. `legacy_to_items` converts a legacy schema losslessly, so one engine
    validates and flags both.

Answers stay a flat {key: string} map (build 5 and old rows assume strings).
Answer keys per item (see answer_keys):
  - single-value types: the item id
  - grids: "{item_id}.{row_id}" - or the bare row id for a grid converted from
    a legacy checklist ("answer_format": "legacy"), which is what build 5 sends
  - checkboxes: the item id, value a JSON array string of the ticked option
    values - or, for a legacy checkbox group ("answer_format": "legacy"), one
    key per ticked option id with the value "Done"
  - checkbox_grid rows / file_upload: JSON array strings

Python 3.9 compatible.
"""

import copy
import datetime as dt
import json
import re
import secrets
from typing import Dict, List, Optional, Sequence, Tuple

# --- Constants ---------------------------------------------------------------------------

ITEM_TYPES = [
    "short_text",
    "paragraph",
    "multiple_choice",
    "checkboxes",
    "dropdown",
    "linear_scale",
    "rating",
    "multiple_choice_grid",
    "checkbox_grid",
    "date",
    "time",
    "file_upload",
    "title_text",
    "image",
]
DISPLAY_ONLY_TYPES = {"title_text", "image"}
CHOICE_TYPES = {"multiple_choice", "checkboxes", "dropdown"}
GRID_TYPES = {"multiple_choice_grid", "checkbox_grid"}
TEXT_TYPES = {"short_text", "paragraph"}
BRANCHING_TYPES = {"multiple_choice", "dropdown"}
OTHER_OPTION_TYPES = {"multiple_choice", "checkboxes"}

# The legacy checkbox-group answer for a ticked box.
CHECKBOX_DONE_VALUE = "Done"
# "Other" answers are stored as "Other: <text>".
OTHER_PREFIX = "Other: "

# Human-readable label per issue_tag, used to build the plain-language
# summary shown in the app and sent to WhatsApp - one place, reused by both.
# The editor offers exactly these tags (or none) for an option.
ISSUE_TAG_LABELS = {
    "issue": "Issue",
    "morning_prep": "Morning Prep Required",
    "low_stock": "Low Stock",
    "critical": "Critical Issues",
    "cleanup": "Cleanup Not Completed",
    "not_turned_off": "Not Turned Off",
    "not_done": "Not Done",
}
# Display order for the summary/WhatsApp message - a heat source left on
# first (a safety problem), then critical, since it's the most actionable,
# cleanup last.
ISSUE_TAG_ORDER = ["not_turned_off", "critical", "morning_prep", "low_stock", "cleanup", "not_done", "issue"]

# Answers to an "anything unusual?" field that mean "nothing to report"
# (compared after lowercasing and dropping punctuation). Only honoured when the
# item says so (`nothing_to_report_ok`, set for a REQUIRED such field); anything
# else is reported as an issue - over-flagging is the safe side.
NOTHING_TO_REPORT = {
    "no", "nope", "none", "nothing", "nil", "na", "n a", "no issue", "no issues",
    "nothing unusual", "no nothing", "nothing to report", "no problem", "no problems",
    "all good", "all ok", "all okay", "all fine", "ok", "okay", "good", "fine",
    "everything is fine", "everything fine", "everything ok", "everything good",
    "everything is good", "everything is ok", "not really", "no unusual",
    "none all good", "no all good", "no everything is fine", "no everything fine",
}

# settings: closing = counted in the single 9:30 PM missing-checklist alert and
# the nightly (silent) auto-task; whatsapp_group = which staff WhatsApp group
# gets the submission summary (None = no WhatsApp message); all_as_usual = the
# app shows "All as usual" on grid sections.
DEFAULT_SETTINGS = {"closing": False, "whatsapp_group": None, "all_as_usual": True}
# The built-in groups. Since migration 0044 any key in the whatsapp_groups
# table is allowed (services/whatsapp_groups.py); the admin API passes the
# active keys to validate_settings.
WHATSAPP_GROUPS = ("store", "kitchen")
_GROUP_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")

# Google Forms-style response validation, per item type.
VALIDATION_RULES = {
    "number": ["gt", "gte", "lt", "lte", "eq", "neq", "between", "not_between", "is_number", "whole_number"],
    "text": ["contains", "not_contains", "email", "url"],
    "length": ["max_chars", "min_chars"],
    "regex": ["contains", "not_contains", "matches", "not_matches"],
    "checkbox_count": ["at_least", "at_most", "exactly"],
}
VALIDATION_KINDS_BY_TYPE = {
    "short_text": ["number", "text", "length", "regex"],
    "paragraph": ["length", "regex"],
    "checkboxes": ["checkbox_count"],
}
FILE_TYPES = ["image"]  # uploads go through the team media upload (images only)
MAX_FILES = 10
SCALE_MAX = 10
BRANCH_SUBMIT = "submit"
BRANCH_NEXT = "next"

# The app build that first renders `items` (build 5 renders legacy blocks only).
ITEMS_MIN_APP_BUILD = 6

_ID_RE = re.compile(r"^[^.\s][^.]{0,99}$")  # no "." (grid answer keys use it), <= 100 chars
# A quantified group that contains a quantifier - (a+)+, (\w*\s?)*, (x{2,}){3} -
# the shape behind catastrophic backtracking.
_NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()\\]|\\.)*(?:[+*]|\{\d*,?\d*\})(?:[^()\\]|\\.)*\)(?:[+*]|\{\d*,\d*\})")
MAX_PATTERN_LENGTH = 300
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(https?://)?[^\s/$.?#]+\.[^\s]+$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A picture item's address: an uploaded picture (/api/...) or a web address.
# Never javascript:, data: and the like - since Oct 22 a non-admin with
# "Build checklists" can set it, and admins open these pictures.
_IMAGE_URL_RE = re.compile(r"^(https?://\S+|/(?!/)\S*)$", re.IGNORECASE)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SchemaError(ValueError):
    """A checklist definition the editor sent isn't valid. `errors` lists every
    problem found (human-readable), not just the first."""

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def is_nothing_to_report(text: str) -> bool:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.lower())
    return " ".join(cleaned.split()) in NOTHING_TO_REPORT


def is_items_form(stored: dict) -> bool:
    return isinstance(stored, dict) and stored.get("format") == "items"


def normalize_settings(settings: Optional[dict]) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if isinstance(settings, dict) and key in settings:
            out[key] = settings[key]
    out["closing"] = bool(out["closing"])
    out["all_as_usual"] = bool(out["all_as_usual"])
    group = out["whatsapp_group"]
    if not isinstance(group, str) or not _GROUP_KEY_RE.match(group):
        # Any stored group key is kept (an unknown / turned-off one simply
        # sends nothing - see whatsapp_groups.resolve_chat_id).
        out["whatsapp_group"] = None
    return out


def validate_settings(settings: dict, groups: Optional[Sequence[str]] = None) -> List[str]:
    """`groups`: the WhatsApp group keys allowed (default: the built-ins)."""
    errors = []
    if not isinstance(settings, dict):
        return ["settings must be an object"]
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        errors.append(f"Unknown settings: {', '.join(sorted(unknown))}")
    for key in ("closing", "all_as_usual"):
        if key in settings and not isinstance(settings[key], bool):
            errors.append(f"settings.{key} must be true or false")
    allowed = list(WHATSAPP_GROUPS if groups is None else groups)
    if "whatsapp_group" in settings and settings["whatsapp_group"] is not None and settings["whatsapp_group"] not in allowed:
        errors.append(
            f"settings.whatsapp_group {json.dumps(settings['whatsapp_group'])} isn't an active WhatsApp group. "
            f"Choose one of: {', '.join(allowed) or '(none set up)'} - or null for no WhatsApp message"
        )
    return errors


# --- Legacy -> items ---------------------------------------------------------------------


def legacy_to_items(stored: dict) -> dict:
    """A legacy (pre-builder) schema in items form, answer-compatible: grid rows
    and checkbox boxes keep their original answer keys ("answer_format":
    "legacy"), the top-level issues field / confirmation become the last items
    of the last section ("legacy_role"). `legacy_order` keeps the old
    validation-error and issue-summary ordering (all grids, then questions,
    then checkbox groups, then the issues text)."""
    sections = []
    for index, legacy in enumerate(stored.get("sections") or []):
        sid = f"s{index + 1}"
        items = []
        grids = []
        if legacy.get("grid") is not None:
            grids.append(legacy["grid"])
        grids.extend(legacy.get("grids") or [])
        for g_index, grid in enumerate(grids):
            items.append(
                {
                    "id": grid.get("id") or f"{sid}_grid{g_index}",
                    "type": "multiple_choice_grid",
                    "title": grid.get("title"),
                    "description": None,
                    "required": True,
                    "options": [{"value": o["value"], "issue_tag": o.get("issue_tag")} for o in grid["options"]],
                    "rows": [{"id": r["id"], "title": r["label"]} for r in grid["rows"]],
                    "answer_format": "legacy",
                }
            )
        for question in legacy.get("questions") or []:
            options = question.get("options") or []
            item = {
                "id": question["id"],
                "type": ("dropdown" if question.get("input") == "dropdown" else "multiple_choice") if options else "short_text",
                "title": question["label"],
                "description": None,
                "required": bool(question.get("required")),
            }
            if options:
                item["options"] = [{"value": o["value"], "issue_tag": o.get("issue_tag")} for o in options]
            if question.get("summary_label"):
                item["summary_label"] = question["summary_label"]
            items.append(item)
        for group in legacy.get("checkbox_groups") or []:
            items.append(
                {
                    "id": group["id"],
                    "type": "checkboxes",
                    "title": group["title"],
                    "description": None,
                    "required": bool(group.get("required")),
                    "options": [{"id": i["id"], "value": i["label"], "issue_tag": None} for i in group["items"]],
                    "unticked_tag": group.get("unticked_tag"),
                    "answer_format": "legacy",
                }
            )
        section = {"id": sid, "title": legacy.get("title"), "description": legacy.get("description"), "items": items}
        if legacy.get("grid") is not None:
            section["legacy_block"] = "grid"
        sections.append(section)

    trailing = []
    issues_field = stored.get("issues_field")
    if issues_field:
        item = {
            "id": issues_field["id"],
            "type": "paragraph",
            "title": issues_field["label"],
            "description": None,
            "required": bool(issues_field.get("required")),
            "flag_if_answered": True,
            # Only a REQUIRED "anything unusual?" box accepts "None" as
            # nothing to report; an optional one flags anything typed.
            "nothing_to_report_ok": bool(issues_field.get("required")),
            "legacy_role": "issues_field",
        }
        if issues_field.get("hint"):
            item["hint"] = issues_field["hint"]
        trailing.append(item)
    confirmation = stored.get("confirmation")
    if confirmation:
        trailing.append(
            {
                "id": confirmation["id"],
                "type": "multiple_choice",
                "title": confirmation["label"],
                "description": None,
                "required": bool(confirmation.get("required")),
                "options": [{"value": confirmation["label"], "issue_tag": None}],
                "legacy_role": "confirmation",
            }
        )
    if trailing:
        if not sections:
            sections.append({"id": "s1", "title": None, "description": None, "items": []})
        sections[-1]["items"].extend(trailing)
    return {"format": "items", "legacy_order": True, "sections": sections}


# --- Answer keys -------------------------------------------------------------------------


def _legacy_format(item: dict) -> bool:
    return item.get("answer_format") == "legacy"


def row_answer_key(item: dict, row: dict) -> str:
    return row["id"] if _legacy_format(item) else f"{item['id']}.{row['id']}"


def answer_keys(item: dict) -> List[str]:
    """Every key an item's answer uses in the flat answers map."""
    itype = item.get("type")
    if itype in DISPLAY_ONLY_TYPES:
        return []
    if itype in GRID_TYPES:
        return [row_answer_key(item, r) for r in item.get("rows") or []]
    if itype == "checkboxes" and _legacy_format(item):
        return [o["id"] for o in item.get("options") or []]
    return [item["id"]]


def iter_items(schema: dict):
    """(section_index, item) for every item, in form order."""
    for index, section in enumerate(schema.get("sections") or []):
        for item in section.get("items") or []:
            yield index, item


# --- Legacy emission (what build 5 renders) ----------------------------------------------


def _legacy_grid(item: dict) -> Optional[dict]:
    if item.get("type") != "multiple_choice_grid":
        return None
    # Build 5 treats every grid row as required and has no per-column limit.
    if not item.get("required") or item.get("one_per_column") or not item.get("title"):
        return None
    return {
        "id": item["id"],
        "title": item["title"],
        "options": [{"value": o["value"], "issue_tag": o.get("issue_tag")} for o in item["options"]],
        "rows": [{"id": row_answer_key(item, r), "label": r["title"]} for r in item["rows"]],
    }


def _legacy_question(item: dict) -> Optional[dict]:
    if item.get("type") not in ("multiple_choice", "dropdown"):
        return None
    if item.get("other_option") or item.get("go_to_section") or item.get("legacy_role"):
        return None
    question = {
        "id": item["id"],
        "label": item["title"],
        "options": [{"value": o["value"], "issue_tag": o.get("issue_tag")} for o in item["options"]],
        "required": bool(item.get("required")),
    }
    if item["type"] == "dropdown":
        question["input"] = "dropdown"
    if item.get("summary_label"):
        question["summary_label"] = item["summary_label"]
    return question


def _legacy_checkbox_group(item: dict) -> Optional[dict]:
    if item.get("type") != "checkboxes" or not _legacy_format(item):
        return None
    if item.get("other_option") or item.get("validation"):
        return None
    if any(o.get("issue_tag") for o in item["options"]):
        return None  # build 5 has no per-box flags
    return {
        "id": item["id"],
        "title": item["title"],
        "required": bool(item.get("required")),
        "unticked_tag": item.get("unticked_tag"),
        "items": [{"id": o["id"], "label": o["value"]} for o in item["options"]],
    }


def _legacy_issues_field(item: dict) -> Optional[dict]:
    if item.get("type") != "paragraph" or item.get("validation"):
        return None
    field = {"id": item["id"], "label": item["title"], "required": bool(item.get("required"))}
    if item.get("hint"):
        field["hint"] = item["hint"]
    return field


def emit_legacy(schema: dict) -> Tuple[List[Optional[dict]], Optional[dict], Optional[dict]]:
    """For an items-form schema: the legacy blocks per section (None = that
    section can't be shown by build 5), plus the top-level issues_field and
    confirmation. The last section's trailing confirmation (legacy_role) and a
    trailing paragraph become the top-level fields build 5 shows at the end of
    the form. Display-only items (title_text, image) are left out of the legacy
    blocks - build 5 simply doesn't show them."""
    sections = schema.get("sections") or []
    trailing_confirmation = None
    trailing_issues = None
    last_items = list(sections[-1].get("items") or []) if sections else []
    if last_items and last_items[-1].get("legacy_role") == "confirmation":
        candidate = last_items[-1]
        if candidate.get("type") == "multiple_choice":
            trailing_confirmation = {
                "id": candidate["id"],
                "label": candidate["title"],
                "required": bool(candidate.get("required")),
            }
            last_items = last_items[:-1]
    if last_items:
        field = _legacy_issues_field(last_items[-1])
        if field is not None:
            trailing_issues = field
            last_items = last_items[:-1]

    blocks: List[Optional[dict]] = []
    for index, section in enumerate(sections):
        items = last_items if index == len(sections) - 1 else (section.get("items") or [])
        grids, questions, groups = [], [], []
        ok = not section.get("next_section")
        for item in items:
            if not ok:
                break
            itype = item.get("type")
            if itype in DISPLAY_ONLY_TYPES:
                continue
            converted = _legacy_grid(item)
            if converted is not None:
                grids.append(converted)
                continue
            converted = _legacy_question(item)
            if converted is not None:
                questions.append(converted)
                continue
            converted = _legacy_checkbox_group(item)
            if converted is not None:
                groups.append(converted)
                continue
            ok = False
        if not ok:
            blocks.append(None)
        elif section.get("legacy_block") == "grid" and len(grids) == 1 and not questions and not groups:
            blocks.append({"grid": grids[0]})
        else:
            blocks.append({"grids": grids, "questions": questions, "checkbox_groups": groups})
    return blocks, trailing_issues, trailing_confirmation


def newer_app_keys(served: dict) -> set:
    """Answer keys of the questions only a newer app can show: in a section
    served without legacy blocks, every item build 5 couldn't render on its
    own (a legacy-compatible grid there is still answered by a build 5 that
    fetched the schema before the edit, so it doesn't count)."""
    keys = set()
    for section in served.get("sections") or []:
        if any(block in section for block in ("grid", "grids", "questions", "checkbox_groups")):
            continue
        for item in section.get("items") or []:
            if item.get("type") in DISPLAY_ONLY_TYPES:
                continue
            if any(fn(item) is not None for fn in (_legacy_grid, _legacy_question, _legacy_checkbox_group)):
                continue
            keys.update(answer_keys(item))
    # The last section's trailing "anything unusual?" text / confirmation are
    # served top-level (issues_field / confirmation) and build 5 does send
    # them, even when the rest of that section is items-only - they must not
    # count, or a build 5 submission gets "Missing answer for ..." instead of
    # the "Update the app" message.
    for field in ("issues_field", "confirmation"):
        if isinstance(served.get(field), dict):
            keys.discard(served[field].get("id"))
    return keys


# --- Serving ------------------------------------------------------------------------------


def build_served(meta: dict, version: int, stored: dict) -> dict:
    """The JSON GET /api/checklists/schema/{type} serves for one version.

    meta: checklist_type, title, short_name, description, settings.
    Legacy-stored versions are served exactly as stored (build 5 and build 4
    parse that), with `id` + `items` added to every section; items-form
    versions get the legacy blocks for every section build 5 can show, and
    `min_app_build` when some section (or answer) needs a newer app."""
    settings = normalize_settings(meta.get("settings"))
    if is_items_form(stored):
        items_schema = stored
        served_sections = []
        blocks, issues_field, confirmation = emit_legacy(items_schema)
        needs_new_app = False
        for section, block in zip(items_schema.get("sections") or [], blocks):
            out = {"id": section["id"], "title": section.get("title"), "description": section.get("description")}
            if section.get("next_section"):
                out["next_section"] = section["next_section"]
            if block is None:
                needs_new_app = True
            else:
                out.update(copy.deepcopy(block))
            out["items"] = copy.deepcopy(section.get("items") or [])
            served_sections.append(out)
        served = {
            "checklist_type": meta["checklist_type"],
            "title": meta["title"],
            "short_name": meta.get("short_name"),
            "description": meta.get("description"),
            "version": version,
            "settings": settings,
            "sections": served_sections,
        }
        if issues_field is not None:
            served["issues_field"] = issues_field
        if confirmation is not None:
            served["confirmation"] = confirmation
        if needs_new_app:
            served["min_app_build"] = ITEMS_MIN_APP_BUILD
        return served

    served = copy.deepcopy(stored)
    items_schema = legacy_to_items(stored)
    for section, converted in zip(served.get("sections") or [], items_schema["sections"]):
        section["id"] = converted["id"]
        section["items"] = copy.deepcopy(converted["items"])
    # (The issues field / confirmation are the last section's trailing items.)
    served["checklist_type"] = meta["checklist_type"]
    served["title"] = meta["title"]
    served["short_name"] = meta.get("short_name")
    served["description"] = meta.get("description")
    served["version"] = version
    served["settings"] = settings
    return served


def items_view(stored: dict) -> dict:
    """Any stored version in items form (what the editor loads and the engine runs on)."""
    return copy.deepcopy(stored) if is_items_form(stored) else legacy_to_items(stored)


# --- Schema normalisation + validation (editor saves) ------------------------------------


def _new_id(prefix: str, taken: set) -> str:
    while True:
        candidate = f"{prefix}{secrets.token_hex(3)}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate


_SHAPES = {
    "options": list,
    "rows": list,
    "scale": dict,
    "file": dict,
    "validation": dict,
    "go_to_section": dict,
}


def _shape_errors(item: dict, prefix: str) -> List[str]:
    """Wrong JSON types the later checks would trip over (a crash is a 500
    for the admin instead of a message). Content is checked by
    validate_schema; this only makes sure it can look."""
    errors = []
    for key in ("id", "type", "title", "next_section"):
        value = item.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{prefix}: {key} must be text")
    for key, kind in _SHAPES.items():
        value = item.get(key)
        if value is not None and not isinstance(value, kind):
            errors.append(f"{prefix}: {key} has the wrong format")
    for key in ("options", "rows"):
        for entry in item.get(key) or [] if isinstance(item.get(key), list) else []:
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: every entry in {key} must be an object")
                break
            for field in ("id", "value", "title", "issue_tag"):
                if entry.get(field) is not None and not isinstance(entry.get(field), str):
                    errors.append(f"{prefix}: {key} {field} must be text")
                    break
    if isinstance(item.get("go_to_section"), dict):
        if any(not isinstance(t, str) for t in item["go_to_section"].values()):
            errors.append(f"{prefix}: go_to_section targets must be section ids")
    return errors


def normalize_schema(raw: dict) -> dict:
    """The editor's sections -> a stored items-form schema. Fills in missing
    ids (sections "s_xxxxxx", items "q_xxxxxx", rows "r_xxxxxx") and default
    flags; never changes an id that is already there (answers are keyed by
    them). Unknown keys on items are kept (forward compatible)."""
    if not isinstance(raw, dict):
        raise SchemaError(["The checklist must be an object with sections"])
    sections = raw.get("sections")
    if not isinstance(sections, list):
        raise SchemaError(["sections must be a list"])
    taken = set()
    for section in sections:
        if isinstance(section, dict):
            if isinstance(section.get("id"), str):
                taken.add(section["id"])
            items = section.get("items")
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    if isinstance(item.get("id"), str):
                        taken.add(item["id"])
    out_sections = []
    for section in sections:
        if not isinstance(section, dict):
            raise SchemaError(["Every section must be an object"])
        section = copy.deepcopy(section)
        for key in ("id", "next_section"):
            if section.get(key) is not None and not isinstance(section.get(key), str):
                raise SchemaError([f"Section {len(out_sections) + 1}: {key} must be text"])
        if not section.get("id"):
            section["id"] = _new_id("s_", taken)
        section.setdefault("title", None)
        section.setdefault("description", None)
        items = section.get("items")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise SchemaError([f"Section {section['id']}: items must be a list"])
        out_items = []
        for item in items:
            if not isinstance(item, dict):
                raise SchemaError([f"Section {section['id']}: every item must be an object"])
            item = copy.deepcopy(item)
            shape = _shape_errors(item, f"Section {section['id']}, question {item.get('id') or len(out_items) + 1}")
            if shape:
                raise SchemaError(shape)
            if not item.get("id"):
                item["id"] = _new_id("q_", taken)
            item.setdefault("description", None)
            item["required"] = bool(item.get("required", False)) and item.get("type") not in DISPLAY_ONLY_TYPES
            if item.get("type") in GRID_TYPES:
                row_taken = {r.get("id") for r in item.get("rows") or [] if isinstance(r, dict)}
                for row in item.get("rows") or []:
                    if isinstance(row, dict) and not row.get("id"):
                        row["id"] = _new_id("r_", row_taken)
            for option in item.get("options") or []:
                if isinstance(option, dict):
                    option.setdefault("issue_tag", None)
                    # "Yes " and "Yes" look the same to whoever fills it in;
                    # a stray space made app answers fail as "Invalid answer".
                    if isinstance(option.get("value"), str):
                        option["value"] = option["value"].strip()
            if isinstance(item.get("go_to_section"), dict):
                item["go_to_section"] = {
                    (k.strip() if isinstance(k, str) else k): v for k, v in item["go_to_section"].items()
                }
            out_items.append(item)
        section["items"] = out_items
        out_sections.append(section)
    out = {"format": "items", "sections": out_sections}
    if raw.get("legacy_order"):
        out["legacy_order"] = True
    return out


def _check_validation_rule(item: dict, prefix: str, errors: List[str]) -> None:
    rule = item.get("validation")
    if rule is None:
        return
    if not isinstance(rule, dict):
        errors.append(f"{prefix}: validation must be an object or null")
        return
    kind = rule.get("kind")
    allowed = VALIDATION_KINDS_BY_TYPE.get(item.get("type"), [])
    if kind not in allowed:
        errors.append(f"{prefix}: validation kind {kind!r} isn't available for {item.get('type')}")
        return
    name = rule.get("rule")
    if name not in VALIDATION_RULES[kind]:
        errors.append(f"{prefix}: unknown {kind} rule {name!r}")
        return
    value = rule.get("value")
    if kind == "number" and name not in ("is_number", "whole_number"):
        if name in ("between", "not_between"):
            if not (isinstance(value, list) and len(value) == 2 and all(_is_num(v) for v in value)):
                errors.append(f"{prefix}: {name} needs two numbers")
        elif not _is_num(value):
            errors.append(f"{prefix}: {name} needs a number")
    elif kind in ("length", "checkbox_count"):
        if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
            errors.append(f"{prefix}: {name} needs a whole number")
    elif kind == "regex" or (kind == "text" and name in ("contains", "not_contains")):
        if not isinstance(value, str) or not value:
            errors.append(f"{prefix}: {name} needs text")
        elif kind == "regex":
            try:
                re.compile(value)
            except re.error:
                errors.append(f"{prefix}: the pattern isn't a valid regular expression")
            else:
                if len(value) > MAX_PATTERN_LENGTH or _NESTED_QUANTIFIER_RE.search(value):
                    # Python's re has no timeout: a pattern like (a+)+$ can
                    # take minutes on a 40-character answer and tie up the
                    # server. Refused when the admin saves it.
                    errors.append(f"{prefix}: the pattern is too complex (a repeated group that itself repeats)")
    message = rule.get("message")
    if message is not None and not isinstance(message, str):
        errors.append(f"{prefix}: the validation message must be text")


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_schema(schema: dict, issue_tags: Optional[Dict[str, str]] = None) -> List[str]:
    """Every problem with an items-form schema (empty list = valid): unique ids
    and answer keys, required fields per type, option tags, scales, validation
    rules, and branching targets that exist (only forward, so a form can't
    loop)."""
    tags = ISSUE_TAG_LABELS if issue_tags is None else issue_tags
    errors: List[str] = []
    sections = schema.get("sections") or []
    if not sections:
        return ["Add at least one section"]
    section_ids = [s.get("id") for s in sections]
    section_index = {}
    for index, sid in enumerate(section_ids):
        if not isinstance(sid, str) or not _ID_RE.match(sid):
            errors.append(f"Section {index + 1}: invalid id {sid!r}")
        elif sid in section_index:
            errors.append(f"Duplicate section id {sid!r}")
        else:
            section_index[sid] = index
    item_ids = set()
    keys = set()
    answerable = 0
    for s_index, section in enumerate(sections):
        s_label = f"Section {s_index + 1}"
        for field in ("title", "description"):
            if section.get(field) is not None and not isinstance(section.get(field), str):
                errors.append(f"{s_label}: {field} must be text")
        _check_target(section.get("next_section"), s_index, section_index, f"{s_label}: after-section target", errors)
        branching = 0
        for i_index, item in enumerate(section.get("items") or []):
            iid = item.get("id")
            prefix = f"{s_label}, question {i_index + 1}"
            if item.get("title"):
                prefix += f" ({item['title']!r})"
            if not isinstance(iid, str) or not _ID_RE.match(iid):
                errors.append(f"{prefix}: invalid id {iid!r}")
                continue
            if iid in item_ids:
                errors.append(f"{prefix}: duplicate id {iid!r}")
            item_ids.add(iid)
            itype = item.get("type")
            if itype not in ITEM_TYPES:
                errors.append(f"{prefix}: unknown type {itype!r}")
                continue
            title = item.get("title")
            if itype != "image" and (not isinstance(title, str) or not title.strip()):
                errors.append(f"{prefix}: a title is required")
            if item.get("description") is not None and not isinstance(item.get("description"), str):
                errors.append(f"{prefix}: description must be text")
            if itype not in DISPLAY_ONLY_TYPES:
                answerable += 1
            _check_item(item, itype, prefix, tags, errors)
            _check_validation_rule(item, prefix, errors)
            if item.get("go_to_section"):
                branching += 1
                if itype not in BRANCHING_TYPES:
                    errors.append(f"{prefix}: only multiple choice and dropdown questions can go to a section")
                elif not isinstance(item["go_to_section"], dict):
                    errors.append(f"{prefix}: go_to_section must map answers to sections")
                else:
                    values = {o.get("value") for o in item.get("options") or []}
                    for answer, target in item["go_to_section"].items():
                        if answer not in values:
                            errors.append(f"{prefix}: go-to for {answer!r}, which isn't one of the options")
                        _check_target(target, s_index, section_index, f"{prefix}: go-to for {answer!r}", errors)
            try:
                item_keys = answer_keys(item)
            except (KeyError, TypeError):
                item_keys = []
            for key in item_keys:
                if not isinstance(key, str) or not key:
                    continue
                if key in keys:
                    errors.append(f"{prefix}: answer key {key!r} is used twice")
                keys.add(key)
        if branching > 1:
            errors.append(f"{s_label}: only one question per section can choose the next section")
    if answerable == 0:
        errors.append("Add at least one question")
    return errors


def _check_target(target, s_index: int, section_index: dict, label: str, errors: List[str]) -> None:
    if target in (None, BRANCH_NEXT, BRANCH_SUBMIT):
        return
    if target not in section_index:
        errors.append(f"{label}: no section {target!r}")
    elif section_index[target] <= s_index:
        errors.append(f"{label}: can only go to a later section")


def _check_options(item: dict, prefix: str, tags: dict, errors: List[str]) -> None:
    options = item.get("options")
    if not isinstance(options, list) or not options:
        errors.append(f"{prefix}: add at least one option")
        return
    seen = set()
    for option in options:
        value = option.get("value") if isinstance(option, dict) else None
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix}: every option needs text")
            continue
        if value in seen:
            errors.append(f"{prefix}: option {value!r} is listed twice")
        seen.add(value)
        if value.startswith(OTHER_PREFIX) and item.get("other_option"):
            errors.append(f"{prefix}: option {value!r} can't start with {OTHER_PREFIX!r}")
        tag = option.get("issue_tag")
        if tag is not None and tag not in tags:
            errors.append(f"{prefix}: unknown issue tag {tag!r}")
    if item.get("type") == "checkboxes" and _legacy_format(item):
        ids = [o.get("id") for o in options if isinstance(o, dict)]
        if any(not isinstance(i, str) or not _ID_RE.match(i) for i in ids):
            errors.append(f"{prefix}: every box needs an id")


def _check_item(item: dict, itype: str, prefix: str, tags: dict, errors: List[str]) -> None:
    if not isinstance(item.get("required", False), bool):
        errors.append(f"{prefix}: required must be true or false")
    if itype in CHOICE_TYPES or itype in GRID_TYPES:
        _check_options(item, prefix, tags, errors)
    if item.get("other_option") and itype not in OTHER_OPTION_TYPES:
        errors.append(f"{prefix}: only multiple choice and checkboxes can have an Other option")
    if item.get("unticked_tag") is not None:
        if itype != "checkboxes":
            errors.append(f"{prefix}: unticked_tag is only for checkboxes")
        elif item["unticked_tag"] not in tags:
            errors.append(f"{prefix}: unknown issue tag {item['unticked_tag']!r}")
    if itype in GRID_TYPES:
        rows = item.get("rows")
        if not isinstance(rows, list) or not rows:
            errors.append(f"{prefix}: add at least one row")
        else:
            seen = set()
            for row in rows:
                rid = row.get("id") if isinstance(row, dict) else None
                if not isinstance(rid, str) or not _ID_RE.match(rid):
                    errors.append(f"{prefix}: invalid row id {rid!r}")
                elif rid in seen:
                    errors.append(f"{prefix}: duplicate row id {rid!r}")
                seen.add(rid)
                if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not row["title"].strip():
                    errors.append(f"{prefix}: every row needs text")
    if itype in ("linear_scale", "rating"):
        scale = item.get("scale")
        if not isinstance(scale, dict):
            errors.append(f"{prefix}: a scale is required")
        else:
            low, high = scale.get("min", 1), scale.get("max")
            if itype == "linear_scale":
                if low not in (0, 1) or not isinstance(high, int) or isinstance(high, bool) or not 2 <= high <= SCALE_MAX:
                    errors.append(f"{prefix}: the scale goes from 0 or 1 up to 2-{SCALE_MAX}")
            else:
                if low != 1 or not isinstance(high, int) or isinstance(high, bool) or not 3 <= high <= SCALE_MAX:
                    errors.append(f"{prefix}: a rating goes from 1 up to 3-{SCALE_MAX}")
            for key in ("min_label", "max_label"):
                if scale.get(key) is not None and not isinstance(scale.get(key), str):
                    errors.append(f"{prefix}: {key} must be text")
    if itype == "file_upload":
        spec = item.get("file") or {}
        max_files = spec.get("max_files", 1)
        if not isinstance(max_files, int) or isinstance(max_files, bool) or not 1 <= max_files <= MAX_FILES:
            errors.append(f"{prefix}: max files must be 1-{MAX_FILES}")
        types = spec.get("types", FILE_TYPES)
        if not isinstance(types, list) or not types or any(t not in FILE_TYPES for t in types):
            errors.append(f"{prefix}: only photos can be uploaded")
    if itype == "image":
        if not isinstance(item.get("image_url"), str) or not item["image_url"].strip():
            errors.append(f"{prefix}: an image needs an image_url")
        elif not _IMAGE_URL_RE.match(item["image_url"].strip()):
            errors.append(f"{prefix}: the picture must be uploaded or a web address (https://...)")
    if item.get("flag_if_answered") and itype not in TEXT_TYPES:
        errors.append(f"{prefix}: only text questions can flag any answer as an issue")
    if item.get("summary_label") is not None:
        if itype in DISPLAY_ONLY_TYPES or itype in GRID_TYPES or itype in ("checkboxes", "file_upload"):
            errors.append(f"{prefix}: only single-answer questions can be shown next to Submitted by")
        elif not isinstance(item["summary_label"], str) or not item["summary_label"].strip():
            errors.append(f"{prefix}: summary_label must be text")
    if item.get("one_per_column") and itype not in GRID_TYPES:
        errors.append(f"{prefix}: one response per column is only for grids")


# --- Branching ----------------------------------------------------------------------------


def visited_sections(schema: dict, answers: dict) -> List[int]:
    """Indexes of the sections a submission goes through, following each
    section's go-to (a multiple choice / dropdown answer with a go_to_section,
    else the section's own next_section, else the next section). Skipped
    sections are neither required nor flagged."""
    sections = schema.get("sections") or []
    index_by_id = {s.get("id"): i for i, s in enumerate(sections)}
    path = []
    current: Optional[int] = 0
    while current is not None and 0 <= current < len(sections) and current not in path:
        path.append(current)
        section = sections[current]
        target = section.get("next_section")
        for item in section.get("items") or []:
            mapping = item.get("go_to_section")
            if mapping:
                value = answers.get(item["id"])
                if isinstance(value, str) and mapping.get(value):
                    target = mapping[value]
        if target in (None, BRANCH_NEXT):
            current = current + 1
        elif target == BRANCH_SUBMIT:
            current = None
        else:
            current = index_by_id.get(target, current + 1)
    return path


# --- Answers ------------------------------------------------------------------------------


def _phase(item: dict, legacy_order: bool, position: int) -> tuple:
    """Sort key for errors / flagged answers. With legacy_order (a checklist
    that started as one of the original three) the old order is kept: every
    grid, then single-choice questions, then checkbox groups, then the issues
    text, then the confirmation. Otherwise plain form order."""
    if not legacy_order:
        return (0, position)
    itype = item.get("type")
    if item.get("legacy_role") == "confirmation":
        phase = 4
    elif itype in GRID_TYPES:
        phase = 0
    elif itype in ("multiple_choice", "dropdown"):
        phase = 1
    elif itype == "checkboxes":
        phase = 2
    else:
        phase = 3
    return (phase, position)


def _json_list(value: str) -> Optional[list]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _label(item: dict) -> str:
    return item.get("title") or item.get("id")


def _missing(item: dict, label: Optional[str] = None) -> str:
    return f"Missing answer for required item: {(label or _label(item))!r}"


def _invalid(value, label: str) -> str:
    return f"Invalid answer {value!r} for {label!r}"


def _choice_ok(item: dict, value: str) -> bool:
    if value in {o["value"] for o in item.get("options") or []}:
        return True
    return bool(item.get("other_option")) and value.startswith(OTHER_PREFIX) and bool(value[len(OTHER_PREFIX):].strip())


def _rule_error(item: dict, value, count: Optional[int] = None) -> Optional[str]:
    rule = item.get("validation")
    if not rule:
        return None
    kind, name, target = rule.get("kind"), rule.get("rule"), rule.get("value")
    ok = True
    if kind == "number":
        try:
            number = float(str(value).strip())
            if number != number:  # NaN
                raise ValueError
        except ValueError:
            number = None
        if number is None:
            ok = False
        elif name == "whole_number":
            ok = number == int(number)
        elif name == "gt":
            ok = number > target
        elif name == "gte":
            ok = number >= target
        elif name == "lt":
            ok = number < target
        elif name == "lte":
            ok = number <= target
        elif name == "eq":
            ok = number == target
        elif name == "neq":
            ok = number != target
        elif name == "between":
            ok = min(target) <= number <= max(target)
        elif name == "not_between":
            ok = not (min(target) <= number <= max(target))
    elif kind == "text":
        text = str(value)
        if name == "contains":
            ok = target in text
        elif name == "not_contains":
            ok = target not in text
        elif name == "email":
            ok = bool(_EMAIL_RE.match(text.strip()))
        elif name == "url":
            ok = bool(_URL_RE.match(text.strip()))
    elif kind == "length":
        length = len(str(value))
        ok = length <= target if name == "max_chars" else length >= target
    elif kind == "regex":
        text = str(value)
        try:
            if name == "contains":
                ok = re.search(target, text) is not None
            elif name == "not_contains":
                ok = re.search(target, text) is None
            elif name == "matches":
                ok = re.fullmatch(target, text) is not None
            elif name == "not_matches":
                ok = re.fullmatch(target, text) is None
        except re.error:
            ok = True
    elif kind == "checkbox_count" and count is not None:
        if name == "at_least":
            ok = count >= target
        elif name == "at_most":
            ok = count <= target
        elif name == "exactly":
            ok = count == target
    if ok:
        return None
    message = rule.get("message") or _default_rule_message(kind, name, target)
    return f"{_label(item)}: {message}"


def _fmt(number) -> str:
    return str(int(number)) if _is_num(number) and number == int(number) else str(number)


def _default_rule_message(kind: str, name: str, target) -> str:
    if kind == "number":
        if name == "is_number":
            return "must be a number"
        if name == "whole_number":
            return "must be a whole number"
        if name in ("between", "not_between"):
            low, high = min(target), max(target)
            word = "between" if name == "between" else "not between"
            return f"must be a number {word} {_fmt(low)} and {_fmt(high)}"
        words = {
            "gt": "greater than",
            "gte": "greater than or equal to",
            "lt": "less than",
            "lte": "less than or equal to",
            "eq": "equal to",
            "neq": "not equal to",
        }
        return f"must be a number {words[name]} {_fmt(target)}"
    if kind == "text":
        return {
            "contains": f"must contain {target!r}",
            "not_contains": f"must not contain {target!r}",
            "email": "must be an email address",
            "url": "must be a web address",
        }[name]
    if kind == "length":
        return f"must be at most {target} characters" if name == "max_chars" else f"must be at least {target} characters"
    if kind == "checkbox_count":
        return {"at_least": f"tick at least {target}", "at_most": f"tick at most {target}", "exactly": f"tick exactly {target}"}[name]
    return "doesn't match the required format"


def _item_errors(item: dict, answers: dict) -> List[str]:
    """Problems with one item's answer, in the order the old code checked them."""
    itype = item.get("type")
    required = bool(item.get("required"))
    label = _label(item)
    if itype in DISPLAY_ONLY_TYPES:
        return []

    if item.get("legacy_role") == "confirmation":
        # Old rule: any non-empty value (build 5 sends the label itself).
        if required and not answers.get(item["id"]):
            return [_missing(item)]
        return []

    if itype in GRID_TYPES:
        errors = []
        seen_columns: Dict[str, str] = {}
        for row in item.get("rows") or []:
            key = row_answer_key(item, row)
            row_label = row["title"]
            value = answers.get(key)
            if value is None or (itype == "checkbox_grid" and value == ""):
                if required:
                    errors.append(_missing(item, row_label))
                continue
            if itype == "multiple_choice_grid":
                if value not in {o["value"] for o in item["options"]}:
                    errors.append(_invalid(value, row_label))
                    continue
                picked = [value]
            else:
                picked = _json_list(value)
                valid = {o["value"] for o in item["options"]}
                if picked is None or any(not isinstance(v, str) or v not in valid for v in picked) or len(set(picked)) != len(picked):
                    errors.append(_invalid(value, row_label))
                    continue
                if required and not picked:
                    errors.append(_missing(item, row_label))
                    continue
            if item.get("one_per_column"):
                for column in picked:
                    if column in seen_columns:
                        errors.append(f"{label}: {column!r} can only be picked for one row")
                    seen_columns[column] = row["id"]
        return errors

    if itype == "checkboxes" and _legacy_format(item):
        ticked = 0
        for option in item.get("options") or []:
            value = answers.get(option["id"])
            if value is None:
                continue
            if value != CHECKBOX_DONE_VALUE:
                return [_invalid(value, option["value"])]
            ticked += 1
        if required and ticked == 0:
            return [f"Tick at least one box for: {label!r}"]
        return []

    value = answers.get(item["id"])
    if itype in TEXT_TYPES:
        text = str(value or "").strip()
        if not text:
            return [_missing(item)] if required else []
        error = _rule_error(item, value)
        return [error] if error else []

    if value is None:
        return [_missing(item)] if required else []

    if itype in ("multiple_choice", "dropdown"):
        # The old rule: an empty string isn't "missing", it's not a choice.
        if value == "" and not item.get("options"):
            return [_missing(item)] if required else []
        if itype == "multiple_choice":
            ok = _choice_ok(item, value)
        else:
            ok = value in {o["value"] for o in item.get("options") or []}
        return [] if ok else [_invalid(value, label)]

    if itype == "checkboxes":
        picked = _json_list(value)
        if picked is None or any(not isinstance(v, str) or not _choice_ok(item, v) for v in picked) or len(set(picked)) != len(picked):
            return [_invalid(value, label)]
        if sum(1 for v in picked if v.startswith(OTHER_PREFIX) and v not in {o["value"] for o in item["options"]}) > 1:
            return [_invalid(value, label)]
        if required and not picked:
            return [_missing(item)]
        if picked or required:
            error = _rule_error(item, value, count=len(picked))
            if error:
                return [error]
        return []

    if value == "":
        return [_missing(item)] if required else []

    if itype in ("linear_scale", "rating"):
        scale = item.get("scale") or {}
        low = scale.get("min", 1) if itype == "linear_scale" else 1
        high = scale.get("max", 5)
        if not re.fullmatch(r"\d{1,2}", value) or not low <= int(value) <= high:
            return [_invalid(value, label)]
        return []
    if itype == "date":
        try:
            if not _DATE_RE.match(value):
                raise ValueError
            dt.date.fromisoformat(value)
        except ValueError:
            return [_invalid(value, label)]
        return []
    if itype == "time":
        return [] if _TIME_RE.match(value) else [_invalid(value, label)]
    if itype == "file_upload":
        ids = _json_list(value)
        spec = item.get("file") or {}
        if ids is None or any(not _media_id(v) for v in ids) or len(set(map(str, ids))) != len(ids):
            return [_invalid(value, label)]
        if required and not ids:
            return [_missing(item)]
        if len(ids) > spec.get("max_files", 1):
            return [f"{label}: at most {spec.get('max_files', 1)} photo(s)"]
        return []
    return []


def _media_id(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and value.isdigit() and int(value) > 0


def answer_errors(schema: dict, answers: dict) -> List[str]:
    """Every problem with a submission, in reporting order."""
    legacy_order = bool(schema.get("legacy_order"))
    found = []
    position = 0
    sections = schema.get("sections") or []
    for s_index in visited_sections(schema, answers):
        for item in sections[s_index].get("items") or []:
            position += 1
            for message in _item_errors(item, answers):
                found.append((_phase(item, legacy_order, position), message))
    found.sort(key=lambda pair: pair[0])
    return [message for _, message in found]


def _canonical_choice(item: dict, value):
    """`value` as the definition spells it: an answer that matches an option
    only once surrounding spaces are ignored ("Yes" vs a stored "Yes ")
    becomes that option's stored value. Anything else is returned as is."""
    if not isinstance(value, str):
        return value
    values = [o.get("value") for o in item.get("options") or [] if isinstance(o, dict)]
    if value in values:
        return value
    stripped = value.strip()
    for option_value in values:
        if isinstance(option_value, str) and option_value.strip() == stripped:
            return option_value
    return value


def _canonical_list(item: dict, value):
    picked = _json_list(value) if isinstance(value, str) else None
    if picked is None:
        return value
    fixed = [_canonical_choice(item, v) for v in picked]
    return json.dumps(fixed) if fixed != picked else value


def canonical_answers(schema: dict, answers: dict) -> dict:
    """A copy of `answers` with choice answers matched to the definition's
    option values ignoring surrounding spaces, so a definition published with
    "Yes " (before option values were trimmed on save) and an app that sends
    "Yes" still agree - for validation, flags and branching alike. Answers
    that already match exactly are left untouched."""
    if not isinstance(answers, dict):
        return answers
    out = dict(answers)
    for section in schema.get("sections") or []:
        for item in section.get("items") or []:
            if not isinstance(item, dict) or item.get("legacy_role") == "confirmation":
                continue
            itype = item.get("type")
            if itype in GRID_TYPES:
                for row in item.get("rows") or []:
                    key = row_answer_key(item, row)
                    if key not in out:
                        continue
                    if itype == "multiple_choice_grid":
                        out[key] = _canonical_choice(item, out[key])
                    else:
                        out[key] = _canonical_list(item, out[key])
            elif itype == "checkboxes":
                if not _legacy_format(item) and item.get("id") in out:
                    out[item["id"]] = _canonical_list(item, out[item["id"]])
            elif itype in ("multiple_choice", "dropdown") and item.get("id") in out:
                out[item["id"]] = _canonical_choice(item, out[item["id"]])
    return out


def validate_answers(schema: dict, answers: dict) -> None:
    """Raises ValueError with the first problem (same messages the old code
    used for the original three checklists). Choice answers are compared
    ignoring surrounding spaces (canonical_answers)."""
    errors = answer_errors(schema, canonical_answers(schema, answers))
    if errors:
        raise ValueError(errors[0])


def media_ids(schema: dict, answers: dict) -> List[int]:
    """Media ids referenced by file_upload answers (visited sections only)."""
    ids: List[int] = []
    sections = schema.get("sections") or []
    for s_index in visited_sections(schema, answers):
        for item in sections[s_index].get("items") or []:
            if item.get("type") == "file_upload":
                for value in _json_list(answers.get(item["id"]) or "[]") or []:
                    if _media_id(value):
                        ids.append(int(value))
    return ids


def _flag_entries(item: dict, answers: dict) -> List[dict]:
    """Every flagged part of one item's answer, as dicts:
      tag    - the issue_tag
      text   - the issue_summary line (unchanged format: "Row (Item title)")
      label  - the thing itself, without the "(Item title)" suffix
      answer - the picked / typed answer when it's not implied by the label
               (grid / single-choice value, typed text), else None
      part   - True for a grid row or checkbox option (a sub-item of `item`)
      note   - True for the staff's free-text "anything unusual?" field
    """
    itype = item.get("type")
    title = item.get("title")
    out: List[dict] = []

    def add(tag, text, label, answer=None, part=False, note=False):
        out.append({"tag": tag, "text": text, "label": label, "answer": answer, "part": part, "note": note})

    if itype in DISPLAY_ONLY_TYPES or item.get("legacy_role") == "confirmation":
        return out
    tag_of = {o["value"]: o.get("issue_tag") for o in item.get("options") or []}
    if itype in GRID_TYPES:
        for row in item.get("rows") or []:
            value = answers.get(row_answer_key(item, row))
            if value is None:
                continue
            picked = [value] if itype == "multiple_choice_grid" else (_json_list(value) or [])
            for choice in picked:
                tag = tag_of.get(choice, "unrecognized")
                if tag:
                    label = f"{row['title']} ({title})" if title else row["title"]
                    if itype == "checkbox_grid":
                        label = f"{row['title']}: {choice} ({title})" if title else f"{row['title']}: {choice}"
                    add(tag, label, row["title"], str(choice), part=True)
        return out
    if itype == "checkboxes":
        if _legacy_format(item):
            picked = [o["value"] for o in item["options"] if answers.get(o["id"]) == CHECKBOX_DONE_VALUE]
        else:
            picked = _json_list(answers.get(item["id"]) or "[]") or []
        for option in item.get("options") or []:
            if option["value"] in picked and option.get("issue_tag"):
                add(option["issue_tag"], f"{option['value']} ({title})", option["value"], part=True)
        tag = item.get("unticked_tag")
        if tag:
            for option in item.get("options") or []:
                if option["value"] not in picked:
                    add(tag, f"{option['value']} ({title})", option["value"], part=True)
        return out
    value = answers.get(item["id"])
    if itype in ("multiple_choice", "dropdown"):
        if value is not None and item.get("options"):
            tag = tag_of.get(value, None if value.startswith(OTHER_PREFIX) and item.get("other_option") else "unrecognized")
            if tag:
                add(tag, title, _label(item), value)
        return out
    if itype in TEXT_TYPES and item.get("flag_if_answered"):
        text = (value or "").strip()
        if text and not (item.get("nothing_to_report_ok") and is_nothing_to_report(text)):
            tag = item.get("flag_tag") or "issue"
            note = item.get("legacy_role") == "issues_field"
            add(tag, text if note else f"{title}: {text}", _label(item), text, note=note)
    return out


def _flags(item: dict, answers: dict) -> List[Tuple[str, str]]:
    """(issue_tag, text) for every flagged part of one item's answer."""
    return [(entry["tag"], entry["text"]) for entry in _flag_entries(item, answers)]


def compute_status(schema: dict, answers: dict) -> Tuple[bool, Optional[str]]:
    """(all_clear, issue_summary) - decided server-side from the options'
    issue tags, never trusted from the client. The summary groups flagged
    answers under their tag's label, tags in ISSUE_TAG_ORDER."""
    legacy_order = bool(schema.get("legacy_order"))
    flagged = []
    position = 0
    sections = schema.get("sections") or []
    for s_index in visited_sections(schema, answers):
        for item in sections[s_index].get("items") or []:
            position += 1
            for tag, text in _flags(item, answers):
                flagged.append((_phase(item, legacy_order, position), tag, text))
    if not flagged:
        return True, None
    flagged.sort(key=lambda entry: entry[0])
    by_tag: Dict[str, List[str]] = {}
    for _, tag, text in flagged:
        by_tag.setdefault(tag, []).append(text)
    lines = []
    for tag in ISSUE_TAG_ORDER:
        items = by_tag.get(tag)
        if not items:
            continue
        lines.append(f"{ISSUE_TAG_LABELS.get(tag, tag)}:")
        lines.extend(f"  - {item}" for item in items)
    # Any tag not in the known order is never silently dropped.
    for tag, items in by_tag.items():
        if tag in ISSUE_TAG_ORDER:
            continue
        lines.append(f"{tag}:")
        lines.extend(f"  - {item}" for item in items)
    return False, "\n".join(lines)


def summary_details(schema: dict, answers: dict) -> List[dict]:
    """[{"label": "Chef", "value": "JL"}] - answers of items with a
    summary_label, shown next to "Submitted by" (history, detail, WhatsApp)."""
    details = []
    if not isinstance(answers, dict):
        return details
    for _, item in iter_items(schema):
        label = item.get("summary_label")
        value = answers.get(item.get("id"))
        if label and isinstance(value, str) and value.strip():
            details.append({"label": label, "value": value.strip()})
    return details


def flagged_groups(schema: dict, answers: dict) -> dict:
    """The flagged answers grouped for the WhatsApp summary, in form order:
    {"groups": [{"heading": str or None, "items": [entry, ...]}],
     "notes": [text, ...]} (entries as _flag_entries returns them; notes are
    the staff's free-text "anything unusual?" answers).

    Heading: the section title. A grid / checkbox group's own title is used
    instead when its section has no title, or when the section holds more
    than one titled grid / checkbox group (each is then its own sub-list,
    e.g. "Marinated?" vs "Pre-Cooked?"). A plain question in an untitled
    section gets no heading. Separate from compute_status on purpose: the
    stored issue_summary (shown by the apps) keeps its exact old format."""
    groups: List[dict] = []
    notes: List[str] = []
    sections = schema.get("sections") or []
    for s_index in visited_sections(schema, answers):
        section = sections[s_index]
        items = section.get("items") or []
        section_title = (section.get("title") or "").strip() or None
        titled_groups = sum(
            1 for i in items if i.get("type") in GRID_TYPES | {"checkboxes"} and (i.get("title") or "").strip()
        )
        for item in items:
            entries = _flag_entries(item, answers)
            if not entries:
                continue
            item_title = (item.get("title") or "").strip() or None
            heading = section_title
            if item.get("type") in GRID_TYPES | {"checkboxes"} and item_title:
                if section_title is None or titled_groups > 1:
                    heading = item_title
            for entry in entries:
                if entry["note"]:
                    notes.append(entry["answer"])
                    continue
                key = (s_index, heading)
                if not groups or groups[-1]["key"] != key:
                    groups.append({"key": key, "heading": heading, "items": []})
                groups[-1]["items"].append(entry)
    for group in groups:
        del group["key"]
    return {"groups": groups, "notes": notes}


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")
    return slug[:48].strip("_") or "checklist"
