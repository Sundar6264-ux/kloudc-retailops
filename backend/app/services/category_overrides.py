"""App-side category corrections for items Toast has in the wrong category.

Our Toast API access is read-only (no menus:write), so when the owner says a
set of items is in the wrong category (for example every item of one
supplier belongs in RETAIL / Snacks & Savories / Biscuits, Cookies &
Rusk), the correction lives in the `category_overrides` table and is applied
whenever items are read - `reorder.latest_inventory_by_item` applies it, so
every report, list, export, PDF section and API built on it (Supplier
Projection, Reorder, Fix Issues, dead / negative stock, PO PDF sections, the
Ask bot, the app's item search...) shows the corrected category.

Rules:
- match_type "supplier" matches an item when ANY of its ";"-separated
  suppliers equals match_value (case-insensitive, trimmed) - so a new item
  from that vendor is covered with no extra step.
- match_type "item_id" matches one item and always wins over supplier rules.
- match_type "category" (a "category mapping" rule) matches an item by the
  category TOAST has it in - Toast's raw group / category / subcategory, never
  an already-corrected value - so every item Toast files under an old name
  (now or in a future upload) lands in the new one. match_value is a JSON
  array [group, category, subcategory] (see `encode_category_match`):
  subcategory null = any subcategory, "" = only items with no subcategory.
  Names compare case-insensitively and trimmed. An exact-subcategory rule
  wins over an any-subcategory rule for the same group / category.
- Precedence: item_id rule > supplier rule > category rule > Toast.
- Among several matching rules of the same type (and key), the newest
  (highest id) wins.
- A NULL subcategory on the rule keeps the item's own subcategory; "" on the
  rule means "no subcategory".
- Inactive rules are ignored.

SAFETY: `inventory_snapshots` holds Toast's real values and must never be
changed by this. Snapshot objects are ORM rows attached to a session, so
setting `.category` on one would be written back to the table by the next
autoflush/commit. Overridden items are therefore returned as a read-only
`OverriddenSnapshot` wrapper (a plain object, not mapped - it can't be added
to a session and has no settable attributes); the ORM row underneath is
never touched. The wrapper also carries Toast's own values as
`toast_category_group` / `toast_category` / `toast_subcategory`.

Rules are cached on the SQLAlchemy session (`Session.info`), i.e. once per
request, and dropped by `invalidate()` whenever a rule changes through the
admin API.
"""

import json
import logging
from operator import attrgetter, itemgetter
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.dbcompat import is_postgres
from app.models import CategoryOverride
from app.services.item_vendors import vendors_of

logger = logging.getLogger(__name__)

MATCH_TYPES = ("supplier", "item_id", "category")
_CACHE_KEY = "category_override_rules"
_TOAST_FIELDS = ("category_group", "category", "subcategory")
_from_dict = itemgetter(*_TOAST_FIELDS)
_from_attrs = attrgetter(*_TOAST_FIELDS)


def _toast_values(snap) -> tuple:
    """A snapshot's own (Toast's) category values. apply_overrides runs this
    for every item of every report: a loaded ORM row's values are read from
    its __dict__ (3x faster than the instrumented attributes); anything else
    (an expired row, a plain object) falls back to normal attribute reads."""
    try:
        return _from_dict(snap.__dict__)
    except (AttributeError, KeyError):
        return _from_attrs(snap)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def encode_category_match(group: str, category: str, subcategory: Optional[str]) -> str:
    """match_value of a "category" rule: a JSON array [group, category,
    subcategory] with subcategory None = any subcategory and "" = only items
    with no subcategory. Names are stored as given (trimmed)."""
    sub = None if subcategory is None else subcategory.strip()
    return json.dumps([group.strip(), category.strip(), sub], ensure_ascii=False)


def decode_category_match(value: Optional[str]) -> Optional[tuple]:
    """(group, category, subcategory-or-None) from a "category" rule's
    match_value, or None when it isn't a valid encoding (such a rule never
    matches anything)."""
    try:
        parsed = json.loads(value or "")
    except ValueError:
        return None
    if not isinstance(parsed, list) or len(parsed) != 3:
        return None
    group, category, sub = parsed
    if not isinstance(group, str) or not isinstance(category, str) or not (sub is None or isinstance(sub, str)):
        return None
    if not group.strip() or not category.strip():
        return None
    return (group.strip(), category.strip(), None if sub is None else sub.strip())


def category_match_label(value: Optional[str]) -> str:
    """"RETAIL / Produce / any subcategory" style text for a category rule."""
    match = decode_category_match(value)
    if match is None:
        return value or ""
    group, category, sub = match
    if sub is None:
        sub_text = "any subcategory"
    elif sub == "":
        sub_text = "no subcategory"
    else:
        sub_text = sub
    return "%s / %s / %s" % (group, category, sub_text)


class Rules:
    """Active rules, indexed for a fast per-item lookup."""

    __slots__ = ("by_item_id", "by_supplier", "by_category", "by_category_any", "_category_memo")

    def __init__(self, rules: list):
        self.by_item_id: dict = {}
        self.by_supplier: dict = {}
        # (group, category, subcategory) -> rule and (group, category) -> rule
        # for any-subcategory rules; keys lower-cased and trimmed, a missing
        # subcategory is "".
        self.by_category: dict = {}
        self.by_category_any: dict = {}
        # Toast's raw (group, category, subcategory) -> category rule or None:
        # a few hundred distinct combos across ~5k items, so the normalising
        # is done once per combo per request.
        self._category_memo: dict = {}
        # Ascending id, so for duplicate keys the newest rule is kept.
        for rule in sorted(rules, key=lambda r: r.id):
            value = (rule.match_value or "").strip()
            if not value:
                continue
            if rule.match_type == "item_id":
                self.by_item_id[value] = rule
            elif rule.match_type == "supplier":
                self.by_supplier[value.lower()] = rule
            elif rule.match_type == "category":
                match = decode_category_match(value)
                if match is None:
                    continue
                group, category, sub = match
                if sub is None:
                    self.by_category_any[(group.lower(), category.lower())] = rule
                else:
                    self.by_category[(group.lower(), category.lower(), sub.lower())] = rule

    @property
    def has_category_rules(self) -> bool:
        return bool(self.by_category or self.by_category_any)

    def __bool__(self) -> bool:
        return bool(self.by_item_id or self.by_supplier or self.by_category or self.by_category_any)

    def category_rule_for(self, categories) -> Optional[object]:
        """The category rule for Toast's raw (group, category, subcategory),
        or None. An exact-subcategory rule wins over an any-subcategory one."""
        if not categories or not (self.by_category or self.by_category_any):
            return None
        key = tuple(categories)
        memo = self._category_memo
        if key in memo:
            return memo[key]
        group, category, sub = _norm(key[0]), _norm(key[1]), _norm(key[2])
        rule = self.by_category.get((group, category, sub))
        if rule is None:
            rule = self.by_category_any.get((group, category))
        memo[key] = rule
        return rule

    def rule_for(self, item_id: Optional[str], supplier: Optional[str], categories=None):
        """The rule that applies to an item, or None. `categories` is the
        item's TOAST (raw) (group, category, subcategory) - needed for
        category rules; without it only item / supplier rules match."""
        if item_id and self.by_item_id:
            rule = self.by_item_id.get(item_id.strip())
            if rule is not None:
                return rule
        if supplier and self.by_supplier:
            best = None
            for part in vendors_of(supplier):
                rule = self.by_supplier.get(part.lower())
                if rule is not None and (best is None or rule.id > best.id):
                    best = rule
            if best is not None:
                return best
        return self.category_rule_for(categories)


class _RuleValues:
    """A plain, session-free copy of one rule (safe to cache and to keep
    after the session closes)."""

    __slots__ = (
        "id",
        "match_type",
        "match_value",
        "category_group",
        "category",
        "subcategory",
        "created_at",
        "created_by",
    )

    def __init__(self, row):
        for name in self.__slots__:
            setattr(self, name, getattr(row, name))


def load_rules(db: Session) -> Rules:
    """Active rules, cached for the life of this session (one request)."""
    cached = db.info.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        rows = db.execute(select(CategoryOverride).where(CategoryOverride.active.is_(True))).scalars().all()
    except (OperationalError, ProgrammingError):  # SQLite / PostgreSQL "no such table"
        # The table doesn't exist yet (code deployed before `alembic upgrade`)
        # - behave as if there were no rules rather than break every report.
        logger.warning("category_overrides table missing - run alembic upgrade; no overrides applied")
        if is_postgres(db):
            db.rollback()  # the failed statement aborted the transaction
        rows = []
    rules = Rules([_RuleValues(r) for r in rows])
    db.info[_CACHE_KEY] = rules
    return rules


def invalidate(db: Session) -> None:
    """Drop this session's cached rules (call after adding/changing a rule)."""
    db.info.pop(_CACHE_KEY, None)


class OverriddenSnapshot:
    """Read-only stand-in for an InventorySnapshot whose category is
    overridden. Every attribute reads through to the real snapshot except
    the three category fields; nothing can be set on it, and the snapshot
    underneath is never modified."""

    __slots__ = (
        "_snap",
        "category_group",
        "category",
        "subcategory",
        "toast_category_group",
        "toast_category",
        "toast_subcategory",
        "category_override_id",
    )

    def __init__(self, snap, rule):
        set_ = object.__setattr__
        set_(self, "_snap", snap)
        set_(self, "toast_category_group", snap.category_group)
        set_(self, "toast_category", snap.category)
        set_(self, "toast_subcategory", snap.subcategory)
        set_(self, "category_group", rule.category_group)
        set_(self, "category", rule.category)
        set_(self, "subcategory", rule.subcategory if rule.subcategory is not None else snap.subcategory)
        set_(self, "category_override_id", rule.id)

    def __getattr__(self, name):
        # Only called for names that aren't one of the slots above. Private
        # names (SQLAlchemy's `_sa_instance_state` above all) are NOT passed
        # through, so the wrapper is an unmapped object: `session.add()` on it
        # raises instead of quietly acting on the real row.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "_snap"), name)

    def __setattr__(self, name, value):
        raise AttributeError("OverriddenSnapshot is read-only (it must never write into inventory_snapshots)")

    def __delattr__(self, name):
        raise AttributeError("OverriddenSnapshot is read-only")

    def __repr__(self) -> str:
        return "<OverriddenSnapshot item_id=%r %s / %s / %s (Toast: %s / %s / %s)>" % (
            self.item_id,
            self.category_group,
            self.category,
            self.subcategory,
            self.toast_category_group,
            self.toast_category,
            self.toast_subcategory,
        )


def apply_overrides(db: Session, snapshots: dict) -> dict:
    """{item_id: snapshot} -> the same mapping with every item a rule matches
    replaced by an OverriddenSnapshot. Returns the input dict itself when no
    rule is active (no copy, no per-item work). Never modifies a snapshot."""
    rules = load_rules(db)
    if not rules or not snapshots:
        return snapshots
    result = dict(snapshots)
    rule_for = rules.rule_for
    category_rule_for = rules.category_rule_for if rules.has_category_rules else None
    for item_id, snap in snapshots.items():
        if isinstance(snap, OverriddenSnapshot):
            continue
        rule = rule_for(item_id, snap.supplier)
        if rule is None and category_rule_for is not None:
            # Category rules match Toast's raw values - the snapshot's own.
            rule = category_rule_for(_toast_values(snap))
        if rule is not None:
            result[item_id] = OverriddenSnapshot(snap, rule)
    return result


def overridden_categories(db: Session, items: dict) -> dict:
    """{item_id: supplier} or {item_id: (supplier, toast_group,
    toast_category, toast_subcategory)} -> {item_id: (category_group,
    category, subcategory)} for the items a rule matches (subcategory None =
    keep the item's own). Category rules only match when Toast's categories
    are given. For code that reads plain rows instead of snapshots, e.g.
    price history's column-only query."""
    rules = load_rules(db)
    if not rules:
        return {}
    out = {}
    for item_id, value in items.items():
        if isinstance(value, tuple):
            rule = rules.rule_for(item_id, value[0], value[1:4])
        else:
            rule = rules.rule_for(item_id, value)
        if rule is not None:
            out[item_id] = (rule.category_group, rule.category, rule.subcategory)
    return out


def toast_categories(snap) -> tuple:
    """Toast's own (category_group, category, subcategory) for a snapshot,
    overridden or not."""
    if isinstance(snap, OverriddenSnapshot):
        return (snap.toast_category_group, snap.toast_category, snap.toast_subcategory)
    return (snap.category_group, snap.category, snap.subcategory)


def serialize_rule(row: CategoryOverride) -> dict:
    out = {
        "id": row.id,
        "match_type": row.match_type,
        "match_value": row.match_value,
        "category_group": row.category_group,
        "category": row.category,
        "subcategory": row.subcategory,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "active": bool(row.active),
        # Who / when (migration 0036); None on rules made before it.
        "created_by": row.created_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }
    if row.match_type == "category":
        match = decode_category_match(row.match_value)
        out["match"] = (
            {
                "category_group": match[0],
                "category": match[1],
                # null = any subcategory, "" = no subcategory.
                "subcategory": match[2],
                "any_subcategory": match[2] is None,
            }
            if match
            else None
        )
        out["match_label"] = category_match_label(row.match_value)
    return out
