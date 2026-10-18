"""Editable PO sections: the heading each product prints under on the vendor
PO PDF (services/po_sections.py picks the AUTOMATIC section from the item's
category - Toast's, with the app-side corrections in category_overrides
applied - and its name).

The owner edits sections here, never Toast categories. Rules live in
`po_section_rules` (models.PoSectionRule):

- "item": one item -> a section.
- "vendor": every item from a vendor (any of its ";"-separated suppliers)
  -> a section. On a PO the PO's own supplier's rule is used first.
- "rename": a section -> a new name (a merge when the new name is an
  existing section). Applied last, to whatever section an item ended up in,
  so it covers automatic sections and item / vendor rules alike.

Resolution: item rule > vendor rule > automatic section, then renames.
OTHER (unmatched / hand-added lines) always prints last.

Rules are never deleted: a change deactivates the old row (updated_at /
updated_by) and adds a new one, so the table is the history. The rows one
change touches share a batch id (created_batch / updated_batch), which is
what "Undo last change" reverses (by adding new rows, never by editing old
ones back).

The same data is served through the existing `/api/categories/*` endpoints
(routers/po_sections.py) in the shapes the released app build 5 parses: the
"category" of an item there IS its PO section. See `item_row`.
"""

import logging
import uuid
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import CartItem, PoSectionRule
from app.services.item_vendors import vendors_of
from app.services.product_match import snapshot_code_match
from app.services.po_sections import (
    OTHER,
    builtin_sections,
    group_po_items,
    po_line_resolver,
    section_for,
    section_sort_key,
)
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

# What the app (build 5) shows as the "category group" of every item.
GROUP_LABEL = "PO section"
RULE_TYPES = ("item", "vendor", "rename")
# Build 5 knows match_type item_id / supplier (and category); renames are
# web-only.
MATCH_TYPE_OUT = {"item": "item_id", "vendor": "supplier", "rename": "rename"}
_MATCH_TYPE_IN = {"item_id": "item", "supplier": "vendor", "item": "item", "vendor": "vendor", "rename": "rename"}
# item_row "source": "toast" is build 5's word for "no rule" (automatic).
SOURCES = ("toast", "item", "vendor")
_CACHE_KEY = "po_section_rules"

DEFAULT_LIMIT = 200
MAX_LIMIT = 2000
MAX_SECTION_LEN = 80
MAX_VENDOR_LEN = 200
MAX_NOTE_LEN = 500
MAX_ITEM_ID_LEN = 100
MAX_BULK_ITEMS = MAX_LIMIT
MAX_MOVE_ITEMS = 10000
_MAX_RENAME_HOPS = 20
# Rows changes() reads (newest additions + newest deactivations).
_CHANGES_WINDOW = 5000


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


class UnknownItems(ValueError):
    def __init__(self, item_ids):
        super().__init__("Not in the latest inventory upload: " + ", ".join(item_ids))
        self.item_ids = item_ids


class NotLatestChange(ValueError):
    """Undo asked for a change that is no longer the latest one."""


def clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _checked(label: str, value: Optional[str], max_len: int) -> Optional[str]:
    """clean() plus a length limit and no control characters - these values
    print on PDFs and show in dropdowns."""
    value = clean(value)
    if value is None:
        return None
    if len(value) > max_len:
        raise ValueError("%s is too long (max %d characters)." % (label, max_len))
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("%s can't contain line breaks or control characters." % label)
    return value


def _note(note: Optional[str]) -> Optional[str]:
    note = clean(note)
    if note is not None and len(note) > MAX_NOTE_LEN:
        raise ValueError("Note is too long (max %d characters)." % MAX_NOTE_LEN)
    return note


def _item_ids(item_ids, max_items: int = MAX_BULK_ITEMS) -> List[str]:
    ids = list(dict.fromkeys(i.strip() for i in (item_ids or []) if isinstance(i, str) and i.strip()))
    if not ids:
        raise ValueError("No items given.")
    if len(ids) > max_items:
        raise ValueError("Too many items at once (max %d)." % max_items)
    if any(len(i) > MAX_ITEM_ID_LEN for i in ids):
        raise ValueError("Item id is too long.")
    return ids


def _split(raw: Optional[str]) -> List[str]:
    return vendors_of(raw)  # services/item_vendors.py (M1 MT5)


def _new_batch() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------
# Rules + resolution
# --------------------------------------------------------------------------


class _Rule:
    """A plain, session-free copy of one active rule."""

    __slots__ = ("id", "rule_type", "match_value", "section", "note", "created_at", "created_by")

    def __init__(self, row):
        for name in self.__slots__:
            setattr(self, name, getattr(row, name))


class Resolution:
    __slots__ = ("section", "source", "rule", "vendor_rule", "automatic", "base_automatic")

    def __init__(self, section, source, rule, vendor_rule, automatic, base_automatic):
        self.section = section
        self.source = source
        self.rule = rule
        self.vendor_rule = vendor_rule
        self.automatic = automatic
        self.base_automatic = base_automatic


class SectionRules:
    """Active PO section rules, indexed for per-item lookups."""

    def __init__(self, rules: Iterable):
        self.by_item: dict = {}
        self.by_vendor: dict = {}
        self.renames: dict = {}
        self._auto_memo: dict = {}
        # Ascending id: for duplicate keys the newest rule wins.
        for rule in sorted(rules, key=lambda r: r.id):
            value = (rule.match_value or "").strip()
            if not value or not (rule.section or "").strip():
                continue
            if rule.rule_type == "item":
                self.by_item[value] = rule
            elif rule.rule_type == "vendor":
                self.by_vendor[value.lower()] = rule
            elif rule.rule_type == "rename":
                self.renames[value.lower()] = rule

    def __bool__(self) -> bool:
        return bool(self.by_item or self.by_vendor or self.renames)

    def rename(self, section: Optional[str]) -> str:
        """The name `section` prints under after renames (follows chains;
        a cycle, which the API never creates, stops where it repeats)."""
        section = (section or "").strip() or OTHER
        if not self.renames:
            return section
        seen = set()
        for _ in range(_MAX_RENAME_HOPS):
            key = section.lower()
            rule = self.renames.get(key)
            if rule is None or key in seen:
                break
            seen.add(key)
            section = rule.section.strip()
        return section

    def item_rule(self, item_id: Optional[str]):
        if not item_id or not self.by_item:
            return None
        return self.by_item.get(item_id.strip())

    def vendor_rule(self, suppliers: Optional[str], prefer: Optional[str] = None):
        """The vendor rule for an item's ";"-separated suppliers: `prefer`
        (the PO's supplier) first when it's one of them, else the newest."""
        if not suppliers or not self.by_vendor:
            return None
        parts = [p.lower() for p in _split(suppliers)]
        if prefer and prefer.strip().lower() in parts:
            rule = self.by_vendor.get(prefer.strip().lower())
            if rule is not None:
                return rule
        best = None
        for part in parts:
            rule = self.by_vendor.get(part)
            if rule is not None and (best is None or rule.id > best.id):
                best = rule
        return best

    def base_automatic(self, snap) -> str:
        key = (snap.category_group, snap.category, snap.subcategory, snap.name)
        memo = self._auto_memo
        if key not in memo:
            memo[key] = section_for(*key)
        return memo[key]

    def resolve(self, item_id: Optional[str], snap, supplier: Optional[str] = None) -> Resolution:
        base = self.base_automatic(snap)
        automatic = self.rename(base)
        vendor_rule = self.vendor_rule(snap.supplier, supplier)
        rule = self.item_rule(item_id)
        if rule is not None:
            return Resolution(self.rename(rule.section), "item", rule, vendor_rule, automatic, base)
        if vendor_rule is not None:
            return Resolution(self.rename(vendor_rule.section), "vendor", vendor_rule, vendor_rule, automatic, base)
        return Resolution(automatic, "toast", None, None, automatic, base)

    def section_for_snap(self, item_id: Optional[str], snap, supplier: Optional[str] = None) -> str:
        return self.resolve(item_id, snap, supplier).section


def load_section_rules(db: Session) -> SectionRules:
    """Active rules, cached for the life of this session (one request)."""
    cached = db.info.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        rows = db.execute(select(PoSectionRule).where(PoSectionRule.active.is_(True))).scalars().all()
    except (OperationalError, ProgrammingError):  # SQLite / PostgreSQL "no such table"
        # Code deployed before `alembic upgrade`: automatic sections only.
        logger.warning("po_section_rules table missing - run alembic upgrade; automatic PO sections only")
        db.rollback()
        rows = []
    rules = SectionRules([_Rule(r) for r in rows])
    db.info[_CACHE_KEY] = rules
    return rules


def invalidate(db: Session) -> None:
    db.info.pop(_CACHE_KEY, None)


def _latest(db: Session) -> dict:
    # Corrected categories (category_overrides applied) - the automatic
    # section is picked from those.
    from app.services.reorder import latest_inventory_by_item

    return latest_inventory_by_item(db)


def line_sections_for(db: Session, supplier: str) -> dict:
    """{lower-cased name: section} of `supplier`'s hand-added cart lines that
    were given a section (the PO PDF groups those lines by it)."""
    try:
        rows = db.execute(
            select(CartItem).where(
                CartItem.supplier == supplier, CartItem.item_id.is_(None), CartItem.section.isnot(None)
            )
        ).scalars().all()
    except (OperationalError, ProgrammingError):  # SQLite / PostgreSQL "no such table"
        db.rollback()
        return {}
    return {(r.name or "").strip().lower(): r.section for r in rows if (r.name or "").strip()}


def group_for_pdf(db: Session, items: list, latest: dict, supplier: str) -> list:
    """PO lines grouped exactly as the PDF prints them."""
    return group_po_items(items, latest, supplier, load_section_rules(db), line_sections_for(db, supplier))


# --------------------------------------------------------------------------
# Section names
# --------------------------------------------------------------------------


def _section_counts(latest: dict, rules: SectionRules) -> dict:
    counts: dict = {}
    for item_id, snap in latest.items():
        section = rules.resolve(item_id, snap).section
        counts[section] = counts.get(section, 0) + 1
    return counts


def _all_sections(latest: dict, rules: SectionRules, counts: Optional[dict] = None) -> dict:
    """{section: items now} for every section in use or available: current
    ones, the automatic list (after renames) and those on active rules."""
    counts = dict(counts if counts is not None else _section_counts(latest, rules))
    names = [rules.rename(b) for b in builtin_sections()]
    names += [rules.rename(r.section) for r in rules.by_item.values()]
    names += [rules.rename(r.section) for r in rules.by_vendor.values()]
    for name in names:
        counts.setdefault(name, 0)
    return counts


def _spellings(latest: dict, rules: SectionRules) -> dict:
    """lower-cased name -> spelling in use: current sections first, then the
    automatic list and names on rules (incl. renamed-away ones)."""
    out: dict = {}
    for name in _all_sections(latest, rules):
        out.setdefault(name.lower(), name)
    for name in builtin_sections():
        out.setdefault(name.lower(), name)
    for rule in list(rules.renames.values()):
        out.setdefault(rule.match_value.strip().lower(), rule.match_value.strip())
    out[OTHER.lower()] = OTHER
    return out


def _section_name(label: str, value: Optional[str]) -> Optional[str]:
    """_checked() for a section name, with inner runs of spaces collapsed
    ("Rice  Flour" and "Rice Flour" are one section)."""
    value = _checked(label, value, MAX_SECTION_LEN)
    return " ".join(value.split()) if value is not None else None


def _section_value(label: str, value: Optional[str], spellings: dict) -> str:
    section = _section_name(label, value)
    if section is None:
        raise ValueError("Pick a section or type a new one.")
    return spellings.get(section.lower(), section)


# --------------------------------------------------------------------------
# Serializing (build-5 shapes + additive fields)
# --------------------------------------------------------------------------


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def rule_info(rule, rules: SectionRules) -> Optional[dict]:
    """Build 5's CategoryRule shape for the rule deciding an item."""
    if rule is None:
        return None
    return {
        "id": rule.id,
        "match_type": MATCH_TYPE_OUT.get(rule.rule_type, rule.rule_type),
        "match_value": rule.match_value,
        "category_group": GROUP_LABEL,
        "category": rules.rename(rule.section),
        "subcategory": None,
        "created_at": _iso(rule.created_at),
        "created_by": rule.created_by,
        # Additive.
        "rule_type": rule.rule_type,
        "section": rule.section,
    }


def serialize_rule(row: PoSectionRule, rules: Optional[SectionRules] = None) -> dict:
    """A full rule row (vendor rules list, history), build 5's CategoryRule
    shape. `category` = the section it prints under now (renames applied)
    for an active item / vendor rule; `section` = the name stored on it."""
    shown = row.section
    if rules is not None and row.active and row.rule_type in ("item", "vendor"):
        shown = rules.rename(row.section)
    return {
        "id": row.id,
        "match_type": MATCH_TYPE_OUT.get(row.rule_type, row.rule_type),
        "match_value": row.match_value,
        "category_group": GROUP_LABEL,
        "category": shown,
        "subcategory": None,
        "note": row.note,
        "created_at": _iso(row.created_at),
        "active": bool(row.active),
        "created_by": row.created_by,
        "updated_at": _iso(row.updated_at),
        "updated_by": row.updated_by,
        # Additive.
        "rule_type": row.rule_type,
        "section": row.section,
        "created_batch": row.created_batch,
        "updated_batch": row.updated_batch,
    }


def item_row(item_id: str, snap, rules: SectionRules) -> dict:
    """One item for the page / app. In build 5's shape the "category" is the
    PO section: category = the section it prints under now, toast_category =
    its automatic section (what it would print under with no item or vendor
    rule), source = item / vendor / toast (automatic)."""
    r = rules.resolve(item_id, snap)
    return {
        "item_id": item_id,
        "name": snap.name,
        "supplier": snap.supplier,
        "barcode": snap.barcode,
        "category_group": GROUP_LABEL,
        "category": r.section,
        "subcategory": None,
        "toast_category_group": GROUP_LABEL,
        "toast_category": r.automatic,
        "toast_subcategory": None,
        "source": r.source,
        "rule": rule_info(r.rule, rules),
        # The vendor rule covering the item even when an item rule wins
        # ("Use the <vendor> rule" drops the item rule).
        "vendor_rule": rule_info(r.vendor_rule, rules),
        "fallback_rule": rule_info(r.vendor_rule, rules),
        # Additive (web): plain names, and the item's category (Toast's with
        # the app's corrections), read-only.
        "section": r.section,
        "automatic_section": r.automatic,
        "item_category_group": snap.category_group,
        "item_category": snap.category,
        "item_subcategory": snap.subcategory,
    }


def _rows_for(db: Session, ids: List[str]) -> List[dict]:
    latest = _latest(db)
    rules = load_section_rules(db)
    return [item_row(i, latest[i], rules) for i in ids if i in latest]


# --------------------------------------------------------------------------
# Items: search / options
# --------------------------------------------------------------------------


def search_items(
    db: Session,
    q: Optional[str] = None,
    vendor: Optional[str] = None,
    section: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Items in the latest upload. `q` matches name, item id, barcode or a
    vendor SKU;
    `section` = the section it prints under now (case-insensitive; "Other"
    finds the unassigned ones); `source` = toast (automatic) / item / vendor,
    or "changed" (any rule)."""
    latest = _latest(db)
    rules = load_section_rules(db)
    needle = (q or "").strip().lower()
    vendor_key = (vendor or "").strip().lower()
    section_key = (section or "").strip().lower()
    rows = []
    for item_id, snap in latest.items():
        if needle:
            hay = " ".join([snap.name or "", item_id or "", snap.barcode or ""]).lower()
            # W2 (Oct 14): any vendor's SKU too (product_match rule).
            if needle not in hay and not snapshot_code_match(q, item_id, snap):
                continue
        if vendor_key and vendor_key not in [s.lower() for s in _split(snap.supplier)]:
            continue
        row = item_row(item_id, snap, rules)
        if section_key and row["section"].lower() != section_key:
            continue
        if source:
            if source == "changed":
                if row["source"] == "toast":
                    continue
            elif row["source"] != source:
                continue
        rows.append(row)
    rows.sort(key=lambda r: ((r["name"] or "").lower(), r["item_id"]))
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    return {"total": len(rows), "limit": limit, "items": rows[:limit]}


def _vendors(latest: dict) -> List[str]:
    vendors = set()
    for snap in latest.values():
        vendors.update(_split(snap.supplier))
    return sorted(vendors, key=str.lower)


def list_sections(db: Session, latest: Optional[dict] = None) -> List[dict]:
    """Every section with its item count, alphabetical, OTHER last.
    `automatic`: an automatic section (or one it was renamed into);
    `renamed_from`: active renames / merges into it."""
    latest = latest if latest is not None else _latest(db)
    rules = load_section_rules(db)
    counts = _section_counts(latest, rules)
    all_counts = _all_sections(latest, rules, counts)
    automatic = {rules.rename(b) for b in builtin_sections()}
    renamed_from: dict = {}
    for rule in rules.renames.values():
        target = rules.rename(rule.section)
        renamed_from.setdefault(target, []).append(
            {"rule_id": rule.id, "from": rule.match_value, "to": rule.section}
        )
    item_rules: dict = {}
    for rule in rules.by_item.values():
        key = rules.rename(rule.section)
        item_rules[key] = item_rules.get(key, 0) + 1
    vendor_rules: dict = {}
    for rule in rules.by_vendor.values():
        key = rules.rename(rule.section)
        vendor_rules[key] = vendor_rules.get(key, 0) + 1
    return [
        {
            "section": name,
            "items": all_counts[name],
            "automatic": name in automatic,
            "is_other": name == OTHER,
            "renamed_from": sorted(renamed_from.get(name, []), key=lambda r: r["rule_id"]),
            "item_rules": item_rules.get(name, 0),
            "vendor_rules": vendor_rules.get(name, 0),
        }
        for name in sorted(all_counts, key=section_sort_key)
    ]


def section_options(db: Session) -> dict:
    """Build 5's options shape: one "group" (PO section), a combo per
    section with its item count, the vendor list. `sections` is additive."""
    latest = _latest(db)
    sections = list_sections(db, latest)
    return {
        "groups": [GROUP_LABEL],
        "combos": [
            {"category_group": GROUP_LABEL, "category": s["section"], "subcategory": None, "items": s["items"]}
            for s in sections
        ],
        "vendors": _vendors(latest),
        "sections": sections,
    }


# --------------------------------------------------------------------------
# Item edits
# --------------------------------------------------------------------------


def _active_item_rules(db: Session, item_ids: Iterable[str]) -> dict:
    ids = list(item_ids)
    out: dict = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = db.execute(
            select(PoSectionRule).where(
                PoSectionRule.active.is_(True),
                PoSectionRule.rule_type == "item",
                PoSectionRule.match_value.in_(chunk),
            )
        ).scalars().all()
        for row in rows:
            out.setdefault(row.match_value, []).append(row)
    return out


def _deactivate(rows, username: str, now, batch: str) -> None:
    for row in rows:
        row.active = False
        row.updated_at = now
        row.updated_by = username
        row.updated_batch = batch


def _add(db: Session, rule_type: str, match_value: str, section: str, note, username: str, now, batch: str):
    row = PoSectionRule(
        rule_type=rule_type,
        match_value=match_value,
        section=section,
        note=note,
        active=True,
        created_at=now,
        created_by=username,
        created_batch=batch,
    )
    db.add(row)
    return row


def set_item_sections(
    db: Session,
    item_ids: List[str],
    section: Optional[str],
    username: str,
    max_items: int = MAX_BULK_ITEMS,
    supplier: Optional[str] = None,
) -> dict:
    """Put these items in this section. Setting an item to the section it
    would be in without its own rule (its vendor rule's, or the automatic
    one) just removes its item rule. `supplier`: judge that on this vendor's
    PO (a cart line) - for a multi-vendor item its vendor rule can differ."""
    ids = _item_ids(item_ids, max_items)
    latest = _latest(db)
    rules = load_section_rules(db)
    section = _section_value("Section", section, _spellings(latest, rules))
    unknown = [i for i in ids if i not in latest]
    if unknown:
        raise UnknownItems(unknown)
    target = rules.rename(section)
    existing = _active_item_rules(db, ids)
    now = utcnow()
    batch = _new_batch()
    changed = 0
    for item_id in ids:
        r = rules.resolve(item_id, latest[item_id], supplier)
        if r.section == target and (r.source != "item" or rules.rename(r.rule.section) == target):
            continue
        _deactivate(existing.get(item_id, []), username, now, batch)
        without = rules.rename(r.vendor_rule.section) if r.vendor_rule is not None else r.automatic
        if target == without:
            changed += 1  # its vendor rule / automatic section: no rule needed
            continue
        _add(db, "item", item_id, section, "PO sections page", username, now, batch)
        changed += 1
    db.commit()
    invalidate(db)
    return {"changed": changed, "items": _rows_for(db, ids)}


def reset_items(
    db: Session, item_ids: List[str], username: str, to_toast: bool = True, supplier: Optional[str] = None
) -> dict:
    """Remove these items' own section rules. `to_toast` ("Back to
    automatic", build 5's "Reset to Toast"): an item a vendor rule covers
    gets an item rule pinning its automatic section, so it really prints
    there; without it, the vendor rule applies again."""
    ids = _item_ids(item_ids)
    latest = _latest(db)
    rules = load_section_rules(db)
    existing = _active_item_rules(db, ids)
    now = utcnow()
    batch = _new_batch()
    changed = 0
    for item_id in ids:
        rows = existing.get(item_id, [])
        snap = latest.get(item_id)
        r = rules.resolve(item_id, snap, supplier) if snap is not None else None
        if (
            to_toast
            and r is not None
            and r.vendor_rule is not None
            and rules.rename(r.vendor_rule.section) != r.automatic
        ):
            if len(rows) == 1 and rules.rename(rows[0].section) == r.automatic:
                continue  # already pinned
            _deactivate(rows, username, now, batch)
            _add(
                db,
                "item",
                item_id,
                r.base_automatic,
                "Back to automatic (over vendor rule %s)" % r.vendor_rule.match_value,
                username,
                now,
                batch,
            )
            changed += 1
        elif rows:
            _deactivate(rows, username, now, batch)
            changed += 1
    db.commit()
    invalidate(db)
    return {"changed": changed, "items": _rows_for(db, [i for i in ids if i in latest])}


def move_items(
    db: Session, to_section: Optional[str], username: str, vendor: Optional[str] = None,
    from_section: Optional[str] = None,
) -> dict:
    """Every current item from a vendor, or every item now in a section,
    to another section (item rules, one change). For future items too, use a
    vendor rule or a rename / merge instead."""
    vendor = _checked("Vendor", vendor, MAX_VENDOR_LEN)
    from_section = _section_name("Section", from_section)
    if (vendor is None) == (from_section is None):
        raise ValueError("Pick a vendor or a section to move items from.")
    latest = _latest(db)
    rules = load_section_rules(db)
    ids = []
    for item_id, snap in latest.items():
        if vendor is not None:
            if vendor.lower() in [s.lower() for s in _split(snap.supplier)]:
                ids.append(item_id)
        elif rules.resolve(item_id, snap).section.lower() == from_section.lower():
            ids.append(item_id)
    if not ids:
        raise ValueError("No items to move.")
    result = set_item_sections(db, ids, to_section, username, max_items=MAX_MOVE_ITEMS)
    return {"changed": result["changed"], "matched": len(ids), "section": to_section}


# --------------------------------------------------------------------------
# Vendor rules
# --------------------------------------------------------------------------


def _vendor_counts(db: Session) -> dict:
    latest = _latest(db)
    rules = load_section_rules(db)
    counts: dict = {}
    for item_id, snap in latest.items():
        r = rules.resolve(item_id, snap)
        if r.source == "vendor":
            counts[r.rule.id] = counts.get(r.rule.id, 0) + 1
    return counts


def list_vendor_rules(db: Session, include_inactive: bool = False) -> List[dict]:
    stmt = select(PoSectionRule).where(PoSectionRule.rule_type == "vendor")
    if not include_inactive:
        stmt = stmt.where(PoSectionRule.active.is_(True))
    rows = db.execute(stmt.order_by(PoSectionRule.id.desc())).scalars().all()
    counts = _vendor_counts(db)
    rules = load_section_rules(db)
    out = []
    for row in rows:
        item = serialize_rule(row, rules)
        item["items"] = counts.get(row.id, 0) if row.active else 0
        out.append(item)
    return out


def create_vendor_rule(db: Session, vendor: Optional[str], section: Optional[str], note, username: str) -> dict:
    vendor = _checked("Vendor", vendor, MAX_VENDOR_LEN)
    note = _note(note)
    if vendor is None:
        raise ValueError("Vendor is required.")
    latest = _latest(db)
    rules = load_section_rules(db)
    section = _section_value("Section", section, _spellings(latest, rules))
    known = {v.lower(): v for v in _vendors(latest)}
    vendor = known.get(vendor.lower(), vendor)
    now = utcnow()
    batch = _new_batch()
    older = db.execute(
        select(PoSectionRule).where(PoSectionRule.active.is_(True), PoSectionRule.rule_type == "vendor")
    ).scalars().all()
    # One active rule per vendor: the new one replaces an older one.
    _deactivate([r for r in older if (r.match_value or "").strip().lower() == vendor.lower()], username, now, batch)
    row = _add(db, "vendor", vendor, section, note, username, now, batch)
    db.commit()
    db.refresh(row)
    invalidate(db)
    item = serialize_rule(row, load_section_rules(db))
    item["items"] = _vendor_counts(db).get(row.id, 0)
    return item


def deactivate_rule(db: Session, rule_id: int, username: str, rule_type: str) -> Optional[dict]:
    row = db.get(PoSectionRule, rule_id)
    if row is None or row.rule_type != rule_type:
        return None
    if row.active:
        _deactivate([row], username, utcnow(), _new_batch())
        db.commit()
        db.refresh(row)
        invalidate(db)
    item = serialize_rule(row)
    item["items"] = 0
    return item


# --------------------------------------------------------------------------
# Renames / merges
# --------------------------------------------------------------------------


def list_renames(db: Session, include_inactive: bool = False) -> List[dict]:
    stmt = select(PoSectionRule).where(PoSectionRule.rule_type == "rename")
    if not include_inactive:
        stmt = stmt.where(PoSectionRule.active.is_(True))
    rows = db.execute(stmt.order_by(PoSectionRule.id.desc())).scalars().all()
    out = []
    for row in rows:
        item = serialize_rule(row)
        item["from_section"] = row.match_value
        item["to_section"] = row.section
        out.append(item)
    return out


def create_rename(db: Session, from_section: Optional[str], to_section: Optional[str], note, username: str) -> dict:
    """Rename a section (or merge it into another when the new name already
    exists). Covers items in it now and later, whatever put them there."""
    note = _note(note)
    latest = _latest(db)
    rules = load_section_rules(db)
    current = _all_sections(latest, rules)
    by_lower = {name.lower(): name for name in current}
    source = _section_name("Section", from_section)
    if source is None:
        raise ValueError("Pick the section to rename.")
    if source.lower() not in by_lower:
        raise ValueError("There is no section called %s." % source)
    source = by_lower[source.lower()]
    if source == OTHER:
        raise ValueError("Other can't be renamed: it holds lines with no section.")
    target = _section_name("New name", to_section)
    if target is None:
        raise ValueError("Type the new name.")
    if target.lower() == OTHER.lower():
        raise ValueError("Pick another name: Other is for lines with no section.")
    if target.lower() != source.lower():
        target = _spellings(latest, rules).get(target.lower(), target)
    if target == source:
        raise ValueError("That is already the section's name.")
    now = utcnow()
    batch = _new_batch()
    to_deactivate = []
    old = rules.renames.get(source.lower())
    if old is not None:
        to_deactivate.append(old.id)
    onward = rules.renames.get(target.lower())
    if onward is not None and target.lower() != source.lower():
        if rules.rename(target).lower() == source.lower():
            # "B" was renamed to "A"; renaming "A" back to "B" restores B.
            to_deactivate.append(onward.id)
        else:
            raise ValueError(
                "%s was renamed to %s. Undo that rename first, or pick another name."
                % (target, rules.rename(target))
            )
    if to_deactivate:
        rows = db.execute(select(PoSectionRule).where(PoSectionRule.id.in_(to_deactivate))).scalars().all()
        _deactivate(rows, username, now, batch)
    merge = target.lower() in by_lower and target.lower() != source.lower()
    row = _add(db, "rename", source, target, note or ("Merged into %s" % target if merge else None), username, now, batch)
    db.commit()
    db.refresh(row)
    invalidate(db)
    item = serialize_rule(row)
    item["from_section"] = row.match_value
    item["to_section"] = row.section
    item["merge"] = merge
    new_rules = load_section_rules(db)
    item["items"] = _section_counts(latest, new_rules).get(new_rules.rename(target), 0)
    return item


# --------------------------------------------------------------------------
# History + undo
# --------------------------------------------------------------------------


def history(
    db: Session, item_id: Optional[str] = None, limit: int = 100, match_type: Optional[str] = None
) -> List[dict]:
    """Rules newest first - each is one change; deactivated ones carry
    updated_at / updated_by. `match_type`: item_id / supplier / rename (or
    item / vendor)."""
    stmt = select(PoSectionRule)
    if match_type:
        stmt = stmt.where(PoSectionRule.rule_type == _MATCH_TYPE_IN.get(match_type, match_type))
    if item_id:
        stmt = stmt.where(PoSectionRule.rule_type == "item", PoSectionRule.match_value == item_id)
    rows = db.execute(stmt.order_by(PoSectionRule.id.desc()).limit(max(1, min(limit, 1000)))).scalars().all()
    latest = _latest(db) if rows else {}
    out = []
    for row in rows:
        item = serialize_rule(row)
        if row.rule_type == "item":
            snap = latest.get(row.match_value)
            item["item_name"] = snap.name if snap is not None else None
        out.append(item)
    return out


def _batch_rows(db: Session, batch: str) -> tuple:
    added = db.execute(select(PoSectionRule).where(PoSectionRule.created_batch == batch)).scalars().all()
    removed = db.execute(select(PoSectionRule).where(PoSectionRule.updated_batch == batch)).scalars().all()
    return added, removed


def _summary(added, removed) -> str:
    parts = []
    item_adds: dict = {}
    for row in added:
        if row.rule_type == "item":
            item_adds[row.section] = item_adds.get(row.section, 0) + 1
        elif row.rule_type == "vendor":
            parts.append("Vendor rule: %s to %s" % (row.match_value, row.section))
        elif row.rule_type == "rename":
            parts.append("Renamed %s to %s" % (row.match_value, row.section))
    for section, n in sorted(item_adds.items()):
        parts.append("%d item%s to %s" % (n, "" if n == 1 else "s", section))
    added_keys = {(r.rule_type, r.match_value.lower()) for r in added}
    freed = [r for r in removed if (r.rule_type, r.match_value.lower()) not in added_keys]
    item_freed = sum(1 for r in freed if r.rule_type == "item")
    if item_freed:
        parts.append("%d item%s back to automatic or vendor rule" % (item_freed, "" if item_freed == 1 else "s"))
    for row in freed:
        if row.rule_type == "vendor":
            parts.append("Vendor rule off: %s" % row.match_value)
        elif row.rule_type == "rename":
            parts.append("Rename undone: %s to %s" % (row.match_value, row.section))
    return "; ".join(parts) or "No change"


def changes(db: Session, limit: int = 20) -> List[dict]:
    """Recent changes (one per page action), newest first. Only the newest
    can be undone."""
    # Newest rows by id (additions) AND newest deactivations by updated_at: a
    # change can deactivate a rule far older than the newest N ids (e.g.
    # "vendor rule off"), and missing it would make Undo reverse the wrong
    # change.
    newest = db.execute(select(PoSectionRule).order_by(PoSectionRule.id.desc()).limit(_CHANGES_WINDOW)).scalars().all()
    deactivated = db.execute(
        select(PoSectionRule)
        .where(PoSectionRule.updated_batch.isnot(None))
        .order_by(PoSectionRule.updated_at.desc().nulls_last(), PoSectionRule.id.desc())
        .limit(_CHANGES_WINDOW)
    ).scalars().all()
    rows = list({r.id: r for r in list(newest) + list(deactivated)}.values())
    events: dict = {}
    for row in rows:
        if row.created_batch:
            ev = events.setdefault(row.created_batch, {"at": None, "by": None, "added": [], "removed": []})
            ev["added"].append(row)
            if ev["at"] is None or (row.created_at and row.created_at > ev["at"]):
                ev["at"], ev["by"] = row.created_at, row.created_by
        if row.updated_batch:
            ev = events.setdefault(row.updated_batch, {"at": None, "by": None, "added": [], "removed": []})
            ev["removed"].append(row)
            if ev["at"] is None or (row.updated_at and row.updated_at > ev["at"]):
                ev["at"], ev["by"] = row.updated_at, row.updated_by
    def when(kv) -> tuple:
        ev = kv[1]
        at = ev["at"].timestamp() if ev["at"] is not None else 0.0
        return (at, max(r.id for r in ev["added"] + ev["removed"]))

    ordered = sorted(events.items(), key=when, reverse=True)
    out = []
    for i, (batch, ev) in enumerate(ordered[: max(1, min(limit, 200))]):
        out.append(
            {
                "batch": batch,
                "at": _iso(ev["at"]),
                "by": ev["by"],
                "summary": _summary(ev["added"], ev["removed"]),
                "added": len(ev["added"]),
                "removed": len(ev["removed"]),
                "can_undo": i == 0,
            }
        )
    return out


def undo_last(db: Session, username: str, batch: Optional[str] = None) -> dict:
    """Reverse the newest change: its new rules are turned off and the rules
    it turned off come back (as new rows - history is kept). Undoing an undo
    redoes. `batch` (optional): the change the user saw as newest; if
    another change came in since, nothing is undone."""
    recent = changes(db, limit=1)
    if not recent:
        raise ValueError("Nothing to undo.")
    latest_batch = recent[0]["batch"]
    if batch and batch != latest_batch:
        raise NotLatestChange("Someone made another change since. Reload and try again.")
    added, removed = _batch_rows(db, latest_batch)
    now = utcnow()
    new_batch = _new_batch()
    _deactivate([r for r in added if r.active], username, now, new_batch)
    for row in removed:
        if row.active:
            continue
        _add(db, row.rule_type, row.match_value, row.section, row.note, username, now, new_batch)
    db.commit()
    invalidate(db)
    return {"undone": latest_batch, "batch": new_batch, "summary": recent[0]["summary"]}


# --------------------------------------------------------------------------
# Cart lines + PDF preview
# --------------------------------------------------------------------------


def _cart_line_resolvers(db: Session, rows: List[CartItem], latest: dict) -> dict:
    rules = load_section_rules(db)
    out: dict = {}
    for supplier in {r.supplier for r in rows}:
        out[supplier] = po_line_resolver(latest, supplier, rules, line_sections_for(db, supplier))
    return out


def _as_line(row: CartItem) -> dict:
    # Exactly what the clients send to the PO PDF export for this line.
    return {"name": row.name, "supplier_item_id": row.supplier_item_id}


def cart_line_sections(db: Session, rows: List[CartItem], latest: dict) -> dict:
    """cart row id -> (section, source), resolved exactly as the PO PDF
    groups the line (po_sections.po_line_resolver: matched by this supplier's
    code, then unambiguous name): a catalog item's section for that row's
    supplier (source item / vendor / automatic); a hand-added line's own
    section ("line") or OTHER ("other")."""
    resolvers = _cart_line_resolvers(db, rows, latest)
    out: dict = {}
    for row in rows:
        section, _item_id, source = resolvers[row.supplier](_as_line(row))
        out[row.id] = (section, "automatic" if source == "toast" else source)
    return out


def set_cart_line_section(db: Session, cart_item_id: int, section: Optional[str], username: str) -> Optional[dict]:
    """Set the section of one cart line: a catalog item gets a normal item
    rule (blank = back to automatic); a hand-added line stores it on the
    line (blank = Other)."""
    row = db.get(CartItem, cart_item_id)
    if row is None:
        return None
    latest = _latest(db)
    # The inventory item the PDF prints this line as (normally row.item_id;
    # a hand-added line whose name matches one catalog item prints as it).
    _section, matched, _source = _cart_line_resolvers(db, [row], latest)[row.supplier](_as_line(row))
    if matched is not None:
        if clean(section) is None:
            reset_items(db, [matched], username, to_toast=True, supplier=row.supplier)
        else:
            set_item_sections(db, [matched], section, username, supplier=row.supplier)
    else:
        rules = load_section_rules(db)
        value = None
        if clean(section) is not None:
            value = _section_value("Section", section, _spellings(latest, rules))
            if value == OTHER:
                value = None
        row.section = value
        db.commit()
    db.refresh(row)
    section_now, source = cart_line_sections(db, [row], _latest(db))[row.id]
    return {"id": row.id, "item_id": row.item_id, "name": row.name, "supplier": row.supplier,
            "section": section_now, "section_source": source}


def cart_preview(db: Session, supplier: Optional[str] = None) -> dict:
    """The cart's vendors, and `supplier`'s cart grouped exactly as its PO
    PDF prints it (sections alphabetical, Other last)."""
    rows = db.execute(select(CartItem).order_by(CartItem.supplier, CartItem.added_at, CartItem.id)).scalars().all()
    suppliers: dict = {}
    for row in rows:
        suppliers[row.supplier] = suppliers.get(row.supplier, 0) + 1
    out = {
        "suppliers": [{"supplier": s, "lines": n} for s, n in sorted(suppliers.items(), key=lambda kv: kv[0].lower())],
        "supplier": supplier,
        "sections": [],
    }
    if not supplier:
        return out
    mine = [r for r in rows if r.supplier == supplier]
    latest = _latest(db)
    items = [
        {"name": r.name, "supplier_item_id": r.supplier_item_id, "qty": r.qty, "cart_item_id": r.id,
         "item_id": r.item_id}
        for r in mine
    ]
    groups = group_for_pdf(db, items, latest, supplier)
    out["sections"] = [{"section": section, "lines": lines} for section, lines in groups]
    return out
