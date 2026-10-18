"""Picking sections for the vendor-facing purchase order PDF.

Suppliers complained that POs listed items in cart order (masala in row 1,
masala again in row 15, unrelated items in between), which is painful for
whoever picks the order. Each PO line is grouped under one of a small set of
warehouse-style sections, printed alphabetically with a heading, and sorted
by name within a section (name starts with the brand, so the same brand /
product in different sizes lands on adjacent rows).

Which section an item gets (its "automatic" section) is the store pack's
po_sections rules (app/store/pack.py, docs/M2_CONTRACT.md 3.2): an ordered,
first-match list over the item's category group, category, subcategory and
name. A new store's default (packs/generic.json) uses the item's own
category. The owner's edits on the PO sections page (po_section_rules) win
over the automatic section.
"""

from typing import Optional

from app.store import pack as store_pack

OTHER = store_pack.OTHER_SECTION  # always printed last, not alphabetically


def section_for(
    category_group: Optional[str], category: Optional[str], subcategory: Optional[str], name: Optional[str]
) -> str:
    """The picking section for one inventory item (the store pack's rules).
    Never raises - anything unrecognised falls back to OTHER."""
    return store_pack.section_for(category_group, category, subcategory, name)


def section_sort_key(section: str) -> tuple:
    """Alphabetical, with OTHER always last."""
    return store_pack.section_sort_key(section)


def builtin_sections() -> tuple:
    """Every section the pack's rules can return (except OTHER), alphabetical
    - the automatic sections the PO sections page lists even when empty."""
    return store_pack.builtin_sections()


def __getattr__(name):
    # The old constant, now the active store pack's (read at call time).
    if name == "BUILTIN_SECTIONS":
        return builtin_sections()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def group_po_items(
    items: list,
    latest_inventory: dict,
    supplier: str,
    section_rules=None,
    line_sections: Optional[dict] = None,
) -> list:
    """Groups PO lines (dicts with name / supplier_item_id / qty, exactly what
    the web and mobile clients already send) into [(section, lines)], sections
    alphabetical with OTHER last, lines sorted by name within a section.

    A line is matched to its inventory item by this supplier's own code first,
    then by exact name (only when unambiguous); anything unmatched - e.g. a
    hand-added cart item - goes to OTHER. Every input line appears exactly
    once in the output.

    `section_rules` (po_section_rules.SectionRules): the owner's PO section
    edits - item rule > vendor rule (this PO's supplier first) > automatic
    section, then renames. `line_sections`: {lower-cased name: section} for
    this supplier's hand-added cart lines that were given a section (they
    would otherwise print under OTHER).
    """
    resolve = po_line_resolver(latest_inventory, supplier, section_rules, line_sections)
    groups: dict = {}
    for item in items:
        section, _item_id, _source = resolve(item)
        groups.setdefault(section, []).append(item)

    return [
        (section, sorted(groups[section], key=lambda i: (i.get("name") or "").strip().lower()))
        for section in sorted(groups, key=section_sort_key)
    ]


def po_line_resolver(
    latest_inventory: dict,
    supplier: str,
    section_rules=None,
    line_sections: Optional[dict] = None,
):
    """Returns resolve(line) -> (section, matched item_id or None, source) for
    one PO line (a dict with name / supplier_item_id), exactly as the PO PDF
    groups it. Shared by group_po_items (the PDF) and the cart headings
    (po_section_rules.cart_line_sections) so the two can never disagree.

    A line is matched to inventory by this supplier's code, then by exact
    name (only when unambiguous). source: "toast" (automatic) / "item" /
    "vendor" for a matched line; "line" (a hand-added line's own section) or
    "other" for an unmatched one.
    """
    # Imported here, not at module level, so this module stays importable on
    # its own for pure section_for() use.
    from app.services.supplier_projection import _split_suppliers, _supplier_item_id_for

    by_code: dict = {}
    by_name_supplier: dict = {}
    by_name_any: dict = {}
    item_ids: dict = {}
    for item_id, snap in latest_inventory.items():
        item_ids[id(snap)] = item_id
        lname = (snap.name or "").strip().lower()
        if lname:
            by_name_any.setdefault(lname, []).append(snap)
        if supplier in _split_suppliers(snap.supplier):
            code = _supplier_item_id_for(snap, supplier)
            if code:
                by_code.setdefault(code.strip().lower(), []).append(snap)
            if lname:
                by_name_supplier.setdefault(lname, []).append(snap)

    def lookup(item: dict):
        for key, index in (
            ((item.get("supplier_item_id") or "").strip().lower(), by_code),
            ((item.get("name") or "").strip().lower(), by_name_supplier),
            ((item.get("name") or "").strip().lower(), by_name_any),
        ):
            matches = index.get(key) if key else None
            if matches and len(matches) == 1:
                return matches[0]
        return None

    line_sections = line_sections or {}

    def resolve(item: dict) -> tuple:
        snap = lookup(item)
        if snap is not None:
            item_id = item_ids.get(id(snap))
            if section_rules is not None:
                r = section_rules.resolve(item_id, snap, supplier)
                return r.section, item_id, r.source
            return section_for(snap.category_group, snap.category, snap.subcategory, snap.name), item_id, "toast"
        own = line_sections.get((item.get("name") or "").strip().lower())
        section = own or OTHER
        if section_rules is not None:
            section = section_rules.rename(section)
        return section, None, ("line" if own else "other")

    return resolve
