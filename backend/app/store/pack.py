"""The store pack: one JSON file with the store's own rules (M2 contract,
section 3).

These used to be Python constants written for one store: which category
groups are kitchen-made, which menus count as grocery, how items are grouped
into PO sections, which supplier name is the store itself, how dining options
map to sales channels, who leads which aisle in Fix Issues, the built-in
checklists, the WhatsApp roles, the flyer categories and the category
corrections. A new store gets ``packs/generic.json`` (sane, empty-ish
defaults) until it loads its own.

Where the active pack comes from (``current_pack``), first match wins:

1. ``STORE_PACK_FILE`` - a file, read-only (parity runs and dev). Nothing is
   written to the database.
2. The newest ``setting_changes`` row with key ``store_pack`` (written by
   ``python -m app.store init`` / ``pack load``).
3. ``packs/generic.json``.

The pack is cached per process; a change needs a restart (``reload_pack``
exists for tests and for the commands).

Every section is optional in a pack file: a missing section is taken whole
from ``generic.json``. Unknown keys are an error, so a typo can't silently
switch a rule off.
"""

import copy
import json
import logging
import pathlib
import re
import threading
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

PACK_VERSION = 1
PACKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "packs"
GENERIC_PACK_FILE = PACKS_DIR / "generic.json"
SETTING_KEY = "store_pack"
SETTING_AUTHOR = "store-init"

# PO lines that match nothing print last under this heading. The PO code
# (po_sections / po_section_rules) treats it as a fixed name, so packs keep it.
OTHER_SECTION = "Other"

_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_PACK_ID_RE = re.compile(r"^[a-z0-9_]{1,60}$")
_CHECKLIST_TYPE_RE = re.compile(r"^[a-z0-9_]{1,60}$")


class PackError(ValueError):
    """A pack that can't be used. ``errors`` is a list of readable lines."""

    def __init__(self, errors: List[str], source: str = ""):
        self.errors = list(errors)
        self.source = source
        head = f"Store pack {source} is not valid" if source else "Store pack is not valid"
        super().__init__(head + ":\n" + "\n".join(f"  - {e}" for e in self.errors))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _check_key(cls, value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must match ^[a-z0-9_]{1,40}$")
    return value


def _check_regex(cls, value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression ({exc})") from None
    return value


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


class Menu(_Model):
    """Category groups / menus. Groups compare upper-cased and trimmed, menus
    lower-cased and trimmed."""

    excluded_category_groups: List[str] = Field(
        default_factory=list, description="Groups left out of Fix Issues and similar-product checks (kitchen-made)."
    )
    kitchen_groups: List[str] = Field(
        default_factory=list, description="Groups whose items are made in the kitchen (no delivery suggestions)."
    )
    grocery_menus: List[str] = Field(
        default_factory=list, description="POS menus that count as grocery in Insights. Empty = every menu."
    )
    kitchen_menu_prefixes: List[str] = Field(
        default_factory=list,
        description="POS menus whose name starts with one of these are the kitchen's (Insights kitchen items).",
    )
    kitchen_label: str = Field("Kitchen", description="What the kitchen is called in Insights notes.")


class When(_Model):
    """A condition on an item; every key given must hold."""

    group: Optional[List[str]] = None
    category: Optional[List[str]] = None
    subcategory: Optional[List[str]] = None
    name_regex: Optional[str] = None
    name_not_regex: Optional[str] = None
    any_: Optional[List["When"]] = Field(None, alias="any")

    _v_name = field_validator("name_regex", "name_not_regex")(_check_regex)


class NameSection(_Model):
    name_regex: str
    section: str

    _v_name = field_validator("name_regex")(_check_regex)


class SectionRule(_Model):
    """One PO section rule: an optional condition and exactly one outcome."""

    when: Optional[When] = None
    section: Optional[str] = None
    lookup: Optional[str] = Field(None, description="subcategory | category | group | category_subcategory")
    map: Optional[Dict[str, str]] = None
    first_match: Optional[List[NameSection]] = None
    section_from: Optional[str] = Field(None, description="category | subcategory")

    @model_validator(mode="after")
    def _one_outcome(self) -> "SectionRule":
        outcomes = [k for k in ("section", "lookup", "first_match", "section_from") if getattr(self, k) is not None]
        if len(outcomes) != 1:
            raise ValueError(
                "a rule needs exactly one of section, lookup (+ map), first_match, section_from"
                + (f" (has {', '.join(outcomes)})" if outcomes else "")
            )
        if self.lookup is not None:
            if self.lookup not in ("subcategory", "category", "group", "category_subcategory"):
                raise ValueError("lookup must be subcategory, category, group or category_subcategory")
            if self.map is None:
                raise ValueError("lookup needs a map")
        elif self.map is not None:
            raise ValueError("map is only used with lookup")
        if self.section_from is not None and self.section_from not in ("category", "subcategory"):
            raise ValueError("section_from must be category or subcategory")
        if self.section is not None and not self.section.strip():
            raise ValueError("section can't be blank")
        return self


class PoSections(_Model):
    other_section: str = OTHER_SECTION
    extra_sections: List[str] = Field(default_factory=list)
    rules: List[SectionRule] = Field(default_factory=list)

    @field_validator("other_section")
    @classmethod
    def _other(cls, value: str) -> str:
        if value != OTHER_SECTION:
            raise ValueError(f'must be "{OTHER_SECTION}" in pack version 1')
        return value


class HouseVendors(_Model):
    prefixes: List[str] = Field(default_factory=list)


class ChannelRule(_Model):
    equals: Optional[List[str]] = None
    starts_with: Optional[List[str]] = None
    channel: str

    @model_validator(mode="after")
    def _one(self) -> "ChannelRule":
        if (self.equals is None) == (self.starts_with is None):
            raise ValueError("a channel rule needs exactly one of equals, starts_with")
        return self


class Channels(_Model):
    empty: str = "Other"
    rules: List[ChannelRule] = Field(default_factory=list)
    split_on: str = " - "
    rename: Dict[str, str] = Field(default_factory=dict)
    default: str = "Other"


class Lead(_Model):
    key: str
    name: str
    title: str = ""
    departments: Optional[str] = None
    vendors: List[str] = Field(default_factory=list)

    _v_key = field_validator("key")(_check_key)


class FallbackLead(_Model):
    key: str = "admin"
    name: str = "Store admin"
    title: str = "Inventory & Purchase"

    _v_key = field_validator("key")(_check_key)


class Proposal(_Model):
    lead: str
    basis: str
    why: str

    @field_validator("basis")
    @classmethod
    def _basis(cls, value: str) -> str:
        if value not in ("document", "guess"):
            raise ValueError('must be "document" or "guess"')
        return value


class IssueLeads(_Model):
    document_title: str = Field("Suggested leads", description="Shown above the proposed leads on the Leads page.")
    leads: List[Lead] = Field(default_factory=list)
    fallback: FallbackLead = Field(default_factory=FallbackLead)
    section_proposals: Dict[str, Proposal] = Field(default_factory=dict)
    section_notes: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _refs(self) -> "IssueLeads":
        keys = [lead.key for lead in self.leads]
        if len(set(keys)) != len(keys):
            raise ValueError("lead keys must be unique")
        if self.fallback.key in keys:
            raise ValueError("the fallback key can't also be a lead key")
        allowed = set(keys) | {self.fallback.key}
        for section, prop in self.section_proposals.items():
            if section != section.strip().lower():
                raise ValueError(f"section_proposals key {section!r} must be lower case and trimmed")
            if prop.lead not in allowed:
                raise ValueError(f"section_proposals[{section!r}].lead {prop.lead!r} is not a lead key")
        for section in self.section_notes:
            if section != section.strip().lower():
                raise ValueError(f"section_notes key {section!r} must be lower case and trimmed")
        return self


class PackChecklist(_Model):
    checklist_type: str
    title: str
    short_name: Optional[str] = None
    description: Optional[str] = None
    sort_order: int = 0
    settings: Dict[str, Any] = Field(default_factory=dict)
    schema_: Dict[str, Any] = Field(alias="schema")

    @field_validator("checklist_type")
    @classmethod
    def _type(cls, value: str) -> str:
        if not _CHECKLIST_TYPE_RE.match(value):
            raise ValueError("must match ^[a-z0-9_]{1,60}$")
        return value


class WhatsAppRole(_Model):
    key: str
    label: str

    _v_key = field_validator("key")(_check_key)


class FlyerCategory(_Model):
    key: str
    label: str
    folder: str

    _v_key = field_validator("key")(_check_key)

    @field_validator("folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        v = value.strip()
        if not v or v != value or "/" in v or "\\" in v or v.startswith((".", "_")) or len(v) > 60:
            raise ValueError("must be a plain folder name (no slashes, not starting with . or _)")
        return value


class CategoryRule(_Model):
    match_type: str
    match_value: Union[str, List[Optional[str]]]
    category_group: str
    category: str
    subcategory: Optional[str] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _shape(self) -> "CategoryRule":
        if self.match_type == "supplier":
            if not isinstance(self.match_value, str) or not self.match_value.strip():
                raise ValueError("a supplier rule's match_value is the supplier name")
        elif self.match_type == "category":
            if isinstance(self.match_value, list):
                if len(self.match_value) != 3:
                    raise ValueError("a category rule's match_value is [group, category, subcategory]")
            else:
                try:
                    parsed = json.loads(self.match_value)
                except ValueError:
                    parsed = None
                if not isinstance(parsed, list) or len(parsed) != 3:
                    raise ValueError("a category rule's match_value is [group, category, subcategory]")
        else:
            raise ValueError('match_type must be "supplier" or "category"')
        return self

    def stored_match_value(self) -> str:
        if self.match_type == "category" and isinstance(self.match_value, list):
            from app.services.category_overrides import encode_category_match

            group, category, subcategory = self.match_value
            return encode_category_match(group or "", category or "", subcategory)
        return self.match_value if isinstance(self.match_value, str) else json.dumps(self.match_value)


# ---------------------------------------------------------------------------
# The pack
# ---------------------------------------------------------------------------


class StorePack(_Model):
    pack_version: int
    pack_id: str
    label: str = ""
    menu: Menu = Field(default_factory=Menu)
    po_sections: PoSections = Field(default_factory=PoSections)
    house_vendors: HouseVendors = Field(default_factory=HouseVendors)
    channels: Channels = Field(default_factory=Channels)
    issue_leads: IssueLeads = Field(default_factory=IssueLeads)
    checklists: List[PackChecklist] = Field(default_factory=list)
    whatsapp_roles: List[WhatsAppRole] = Field(default_factory=list)
    flyer_categories: List[FlyerCategory] = Field(default_factory=list)
    category_rules: List[CategoryRule] = Field(default_factory=list)

    _compiled: Optional["_Compiled"] = PrivateAttr(default=None)

    @field_validator("pack_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != PACK_VERSION:
            raise ValueError(f"only pack_version {PACK_VERSION} is supported")
        return value

    @field_validator("pack_id")
    @classmethod
    def _pack_id(cls, value: str) -> str:
        if not _PACK_ID_RE.match(value):
            raise ValueError("must match ^[a-z0-9_]{1,60}$")
        return value

    @model_validator(mode="after")
    def _cross(self) -> "StorePack":
        problems = []
        roles = [r.key for r in self.whatsapp_roles]
        if len(set(roles)) != len(roles):
            problems.append("whatsapp_roles keys must be unique")
        flyer_keys = [c.key for c in self.flyer_categories]
        if len(set(flyer_keys)) != len(flyer_keys):
            problems.append("flyer_categories keys must be unique")
        folders = [c.folder.lower() for c in self.flyer_categories]
        if len(set(folders)) != len(folders):
            problems.append("flyer_categories folders must be unique")
        types = [c.checklist_type for c in self.checklists]
        if len(set(types)) != len(types):
            problems.append("checklists checklist_type values must be unique")
        if problems:
            raise ValueError("; ".join(problems))
        _checklist_errors(self)
        return self

    # --- dumping ---------------------------------------------------------------

    def to_json_dict(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")

    def compiled(self) -> "_Compiled":
        if self._compiled is None:
            self._compiled = _Compiled(self)
        return self._compiled


When.model_rebuild()


def _checklist_errors(pack: "StorePack") -> None:
    """Each checklist must pass the checklist builder's own checks."""
    if not pack.checklists:
        return
    from app.services import checklist_engine as engine

    roles = [r.key for r in pack.whatsapp_roles]
    problems = []
    for c in pack.checklists:
        label = f"checklists[{c.checklist_type}]"
        for err in engine.validate_settings(c.settings, groups=roles):
            problems.append(f"{label}.settings: {err}")
        try:
            stored = engine.normalize_schema(c.schema_)
        except engine.SchemaError as exc:
            problems.extend(f"{label}.schema: {e}" for e in exc.problems)
            continue
        for err in engine.validate_schema(stored):
            problems.append(f"{label}.schema: {err}")
    if problems:
        raise ValueError("; ".join(problems))


# ---------------------------------------------------------------------------
# Compiled rules (what the hot paths call)
# ---------------------------------------------------------------------------

_Pred = Callable[[str, str, str, str], bool]
_Outcome = Callable[[str, str, str, str], Optional[str]]


def _norm_group(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _compile_when(w: Optional[When]) -> Optional[_Pred]:
    if w is None:
        return None
    checks: List[_Pred] = []
    if w.group is not None:
        groups = frozenset(_norm_group(g) for g in w.group)
        checks.append(lambda g, c, s, n: g in groups)
    if w.category is not None:
        cats = frozenset((x or "").strip() for x in w.category)
        checks.append(lambda g, c, s, n: c in cats)
    if w.subcategory is not None:
        subs = frozenset((x or "").strip() for x in w.subcategory)
        checks.append(lambda g, c, s, n: s in subs)
    if w.name_regex is not None:
        must = re.compile(w.name_regex)
        checks.append(lambda g, c, s, n: must.search(n) is not None)
    if w.name_not_regex is not None:
        must_not = re.compile(w.name_not_regex)
        checks.append(lambda g, c, s, n: must_not.search(n) is None)
    if w.any_ is not None:
        options = [p for p in (_compile_when(x) for x in w.any_) if p is not None]
        always = any(x is not None and _compile_when(x) is None for x in w.any_)
        if not always:
            checks.append(lambda g, c, s, n: any(p(g, c, s, n) for p in options))
    if not checks:
        return None
    if len(checks) == 1:
        return checks[0]
    return lambda g, c, s, n: all(check(g, c, s, n) for check in checks)


def _compile_outcome(rule: SectionRule) -> _Outcome:
    if rule.section is not None:
        section = rule.section
        return lambda g, c, s, n: section
    if rule.lookup is not None:
        kind = rule.lookup
        if kind == "group":
            table = {_norm_group(k): v for k, v in (rule.map or {}).items()}
            return lambda g, c, s, n: table.get(g)
        table = dict(rule.map or {})
        if kind == "subcategory":
            return lambda g, c, s, n: table.get(s)
        if kind == "category":
            return lambda g, c, s, n: table.get(c)
        return lambda g, c, s, n: table.get(f"{c}|{s}")
    if rule.first_match is not None:
        pairs = [(re.compile(x.name_regex), x.section) for x in rule.first_match]

        def first(g, c, s, n):
            for pattern, section in pairs:
                if pattern.search(n):
                    return section
            return None

        return first
    if rule.section_from == "subcategory":
        return lambda g, c, s, n: s or None
    return lambda g, c, s, n: c or None


def _produced(rule: SectionRule) -> List[str]:
    if rule.section is not None:
        return [rule.section]
    if rule.map is not None:
        return list(rule.map.values())
    if rule.first_match is not None:
        return [x.section for x in rule.first_match]
    return []


def section_sort_key(section: str) -> tuple:
    """Alphabetical, with the "Other" section always last."""
    return (section == OTHER_SECTION, section.lower())


def _house_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower().replace("&", "and"))


class _Compiled:
    def __init__(self, pack: StorePack):
        po = pack.po_sections
        self.rules: List[Tuple[Optional[_Pred], _Outcome]] = [
            (_compile_when(r.when), _compile_outcome(r)) for r in po.rules
        ]
        self.other = po.other_section
        produced = set(po.extra_sections)
        for r in po.rules:
            produced.update(_produced(r))
        produced.discard(self.other)
        self.builtin_sections = tuple(sorted(produced, key=section_sort_key))
        self.section_names = tuple(sorted(set(produced) | {self.other}, key=section_sort_key))

        menu = pack.menu
        self.excluded_groups = frozenset(_norm_group(g) for g in menu.excluded_category_groups)
        self.kitchen_groups = frozenset(_norm_group(g) for g in menu.kitchen_groups)
        self.grocery_menus = frozenset((m or "").strip().lower() for m in menu.grocery_menus)
        self.kitchen_prefixes = tuple((p or "").strip().lower() for p in menu.kitchen_menu_prefixes if (p or "").strip())

        self.house_prefixes = tuple(p for p in (_house_norm(x) for x in pack.house_vendors.prefixes) if p)

        ch = pack.channels
        self.channel_rules = [
            (
                "equals" if r.equals is not None else "starts_with",
                tuple((x or "").strip().lower() for x in (r.equals if r.equals is not None else r.starts_with)),
                r.channel,
            )
            for r in ch.rules
        ]
        self.channels = ch
        self.rename = {k.strip().lower(): v for k, v in ch.rename.items()}

    def section_for(self, group, category, subcategory, name) -> str:
        g = _norm_group(group)
        c = (category or "").strip()
        s = (subcategory or "").strip()
        n = (name or "").strip().lower()
        for pred, outcome in self.rules:
            if pred is not None and not pred(g, c, s, n):
                continue
            section = outcome(g, c, s, n)
            if section:
                return section
        return self.other

    def channel_of(self, dining_option) -> str:
        ch = self.channels
        s = (dining_option or "").strip()
        if not s:
            return ch.empty
        low = s.lower()
        for kind, values, channel in self.channel_rules:
            if kind == "equals" and low in values:
                return channel
            if kind == "starts_with" and low.startswith(values):
                return channel
        prefix = (s.split(ch.split_on, 1)[0] if ch.split_on else s).strip()
        renamed = self.rename.get(prefix.lower())
        if renamed is not None:
            return renamed
        return prefix or ch.default


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _format_errors(exc: ValidationError) -> List[str]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", ()) if p != "__root__")
        msg = e.get("msg", "invalid")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


_generic_raw: Optional[dict] = None


def generic_raw() -> dict:
    global _generic_raw
    if _generic_raw is None:
        _generic_raw = json.loads(GENERIC_PACK_FILE.read_text(encoding="utf-8"))
    return copy.deepcopy(_generic_raw)


SECTION_KEYS = (
    "menu",
    "po_sections",
    "house_vendors",
    "channels",
    "issue_leads",
    "checklists",
    "whatsapp_roles",
    "flyer_categories",
    "category_rules",
)


def parse_pack(raw: Any, source: str = "") -> StorePack:
    """Validate a pack (already parsed JSON). Missing sections come from
    generic.json. Raises PackError with readable lines."""
    if not isinstance(raw, dict):
        raise PackError(["a store pack is a JSON object"], source)
    merged = dict(raw)
    if any(k not in merged for k in SECTION_KEYS):
        generic = generic_raw()
        for key in SECTION_KEYS:
            if key not in merged:
                merged[key] = generic.get(key)
                if merged[key] is None:
                    merged.pop(key)
    try:
        return StorePack.model_validate(merged)
    except ValidationError as exc:
        raise PackError(_format_errors(exc), source) from None


def load_pack_file(path: Union[str, pathlib.Path]) -> StorePack:
    p = pathlib.Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackError([f"can't read the file ({exc.strerror or exc})"], str(p)) from None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise PackError([f"not valid JSON ({exc})"], str(p)) from None
    return parse_pack(raw, str(p))


def json_schema() -> dict:
    schema = StorePack.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "StoreOps store pack (version 1)"
    return schema


# ---------------------------------------------------------------------------
# The active pack
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_state: Dict[str, Any] = {"pack": None, "source": None, "override": None}


def _from_database(db=None) -> Optional[Tuple[StorePack, str]]:
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.setting_models import SettingChange

    own = db is None
    if own:
        from app.db import SessionLocal

        db = SessionLocal()
    try:
        try:
            row = db.execute(
                select(SettingChange).where(SettingChange.key == SETTING_KEY).order_by(SettingChange.id.desc()).limit(1)
            ).scalar_one_or_none()
        except (OperationalError, ProgrammingError):
            db.rollback()
            return None
        if row is None:
            return None
        try:
            raw = json.loads(row.value)
        except ValueError:
            raise PackError(["the stored pack is not valid JSON"], f"setting_changes #{row.id}") from None
        return parse_pack(raw, f"setting_changes #{row.id}"), f"database (setting_changes #{row.id})"
    finally:
        if own:
            db.close()


def _resolve(db=None) -> Tuple[StorePack, str]:
    from app.config import get_settings

    path = (getattr(get_settings(), "store_pack_file", "") or "").strip()
    if path:
        return load_pack_file(path), f"file {path} (STORE_PACK_FILE)"
    try:
        found = _from_database(db)
    except PackError:
        logger.exception("The store pack saved in the database is not valid - using generic.json")
        found = None
    if found is not None:
        return found
    return load_pack_file(GENERIC_PACK_FILE), "generic.json (built-in default)"


def current_pack(db=None) -> StorePack:
    """The active pack, cached per process."""
    override = _state["override"]
    if override is not None:
        return override
    pack = _state["pack"]
    if pack is not None:
        return pack
    with _lock:
        if _state["pack"] is None:
            pack, source = _resolve(db)
            _state["pack"], _state["source"] = pack, source
        return _state["pack"]


def current_source() -> str:
    if _state["override"] is not None:
        return "override (tests)"
    current_pack()
    return _state["source"] or ""


def reload_pack() -> None:
    with _lock:
        _state["pack"] = None
        _state["source"] = None


def set_override(pack: Optional[StorePack]) -> None:
    """Pin a pack for this process (tests and the fake-store fixtures); None
    clears it."""
    _state["override"] = pack


def _p(pack: Optional[StorePack]) -> StorePack:
    return pack if pack is not None else current_pack()


# ---------------------------------------------------------------------------
# What the rest of the app calls
# ---------------------------------------------------------------------------


def section_for(group, category, subcategory, name, pack: Optional[StorePack] = None) -> str:
    """The PO picking section for one item (po_sections.section_for)."""
    return _p(pack).compiled().section_for(group, category, subcategory, name)


def section_names(pack: Optional[StorePack] = None) -> Tuple[str, ...]:
    """Every section a rule can produce, the extra sections and "Other",
    alphabetical with "Other" last."""
    return _p(pack).compiled().section_names


def builtin_sections(pack: Optional[StorePack] = None) -> Tuple[str, ...]:
    """section_names without "Other" (po_sections.BUILTIN_SECTIONS)."""
    return _p(pack).compiled().builtin_sections


def channel_of(dining_option, pack: Optional[StorePack] = None) -> str:
    return _p(pack).compiled().channel_of(dining_option)


def excluded_category_groups(pack: Optional[StorePack] = None) -> FrozenSet[str]:
    return _p(pack).compiled().excluded_groups


def kitchen_groups(pack: Optional[StorePack] = None) -> FrozenSet[str]:
    return _p(pack).compiled().kitchen_groups


def grocery_menus(pack: Optional[StorePack] = None) -> FrozenSet[str]:
    return _p(pack).compiled().grocery_menus


def is_grocery_menu_set(menus, pack: Optional[StorePack] = None) -> bool:
    """True when any of `menus` (lower-cased names) is a grocery menu; an
    empty grocery_menus list counts every menu."""
    wanted = grocery_menus(pack)
    if not wanted:
        return True
    return bool(set(menus) & wanted)


def kitchen_menu_prefixes(pack: Optional[StorePack] = None) -> Tuple[str, ...]:
    return _p(pack).compiled().kitchen_prefixes


def kitchen_label(pack: Optional[StorePack] = None) -> str:
    return _p(pack).menu.kitchen_label


def is_house_vendor_key(key: str, pack: Optional[StorePack] = None) -> bool:
    """`key` is a schedule_suggestions.vendor_key(); True when it is the store
    itself (a house-made "supplier")."""
    prefixes = _p(pack).compiled().house_prefixes
    return bool(prefixes) and (key or "").replace(" ", "").startswith(prefixes)


def issue_leads(pack: Optional[StorePack] = None) -> IssueLeads:
    return _p(pack).issue_leads


def flyer_categories(pack: Optional[StorePack] = None) -> List[FlyerCategory]:
    return list(_p(pack).flyer_categories)


def whatsapp_roles(pack: Optional[StorePack] = None) -> List[WhatsAppRole]:
    return list(_p(pack).whatsapp_roles)


PACK_SEED_NOTE = "From the store pack"


def checklist_seed_definitions(pack: Optional[StorePack] = None) -> List[dict]:
    """The store's built-in checklists, in the shape the checklist registry
    and the seeding code use: checklist_type, title, short_name, description,
    sort_order, settings, versions (stored schemas, oldest first)."""
    from app.services import checklist_engine as engine

    active = _p(pack)
    note = f"{PACK_SEED_NOTE} ({active.pack_id})"
    out = []
    for c in active.checklists:
        out.append(
            {
                "checklist_type": c.checklist_type,
                "title": c.title,
                "short_name": c.short_name,
                "description": c.description,
                "sort_order": c.sort_order,
                "settings": engine.normalize_settings(c.settings),
                "versions": [engine.normalize_schema(c.schema_)],
                "notes": {1: note},
            }
        )
    return out
