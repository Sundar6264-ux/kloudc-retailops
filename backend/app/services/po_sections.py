"""Generic picking sections for the vendor-facing purchase order PDF.

Suppliers complained that POs listed items in cart order (masala in row 1,
masala again in row 15, unrelated items in between), which is painful for
whoever picks the order. Each PO line is now grouped under one of a small,
generic set of warehouse-style sections, printed alphabetically with a
heading, and sorted by name within a section (name starts with the brand, so
the same brand/product in different sizes lands on adjacent rows).

Sections are derived from Toast's own category/subcategory (a reasonable
signal even though it's our internal catalog) via the fixed map below, not
used verbatim. Frozen is the exception: Toast files ~230 frozen items under
a bare "Frozen Foods" bucket, and frozen versions exist of nearly every dry
category (vegetables, fruits, snacks, sweets...), so frozen items get their
own "Frozen - <kind>" sections - kept together alphabetically and never mixed
into a dry section - with the kind picked from the item name.
"""

import re
from typing import Optional

OTHER = "Other"  # always printed last, not alphabetically

# (category) -> section, with optional per-subcategory overrides.
_CATEGORY_SECTIONS = {
    "Baby & Toddler Foods": "Baby Food",
    "Baking & Cooking Essentials": "Baking & Cooking Essentials",
    "Beverages (Non-Alcoholic)": "Beverages",
    "Canned & Jarred Goods": "Canned & Jarred Goods",
    "Condiments & Sauces": "Sauces & Condiments",
    "Cookware & Kitchenware": "Kitchenware",
    "Cosmetics": "Personal Care",
    "Dairy & Dairy Alternatives": "Dairy",
    "Festival & Cultural": "Pooja & Festival",
    "First Aid & Hygiene": "Health & Wellness",
    "Fresh Rotis": "Rotis, Naan & Wraps",
    "Grains, Flours & Cereals": "Flours, Atta & Rava",
    "Hair Care": "Personal Care",
    "Health & Wellness": "Health & Wellness",
    "Household & Cleaning": "Household & Cleaning",
    "Jewelry": "Pooja & Festival",
    "Lentils, Pulses & Beans": "Dals, Lentils & Beans",
    "Men's Grooming": "Personal Care",
    "Oils & Fats": "Oils & Ghee",
    "Oral Care": "Personal Care",
    "Pantry Staples": "Pantry Staples",
    "Personal Care": "Personal Care",
    "Plants & Seeds": "Plants & Seeds",
    "Pooja Essentials": "Pooja & Festival",
    "Powders & Rituals": "Pooja & Festival",
    "Ready-to-Eat & Instant Foods": "Ready-to-Eat & Instant",
    "Skin Care": "Personal Care",
    "Snacks & Savories": "Snacks, Chips & Namkeen",
    "Specialty & Organic Foods": "Organic & Specialty",
    "Spices & Seasonings": "Spices - Ground",
    "Spiritual & Cultural Products": "Pooja & Festival",
    "Sweeteners & Sugars": "Sugar, Jaggery & Honey",
    "Tea, Coffee & Hot Beverages": "Tea & Coffee",
}

_SUBCATEGORY_SECTIONS = {
    ("Condiments & Sauces", "Pickles & Chutneys"): "Pickles & Chutneys",
    ("Grains, Flours & Cereals", "Rice & Rice Products"): "Rice",
    ("Grains, Flours & Cereals", "Millets & Quinoa"): "Millets, Cereals & Oats",
    ("Grains, Flours & Cereals", "Breakfast Cereals & Oats"): "Millets, Cereals & Oats",
    ("Lentils, Pulses & Beans", "Canned Beans & Legumes"): "Canned & Jarred Goods",
    ("Pantry Staples", "Cooking Essences & Flavors"): "Baking & Cooking Essentials",
    ("Pantry Staples", "Dry Fruits & Nuts"): "Dry Fruits & Nuts",
    ("Pantry Staples", "Pasta & Macaroni"): "Noodles & Pasta",
    ("Pantry Staples", "Roti, Naan, Wraps"): "Rotis, Naan & Wraps",
    ("Ready-to-Eat & Instant Foods", "Instant Noodles & Pasta"): "Noodles & Pasta",
    # One picking section for biscuits, cookies and rusk / tea toast: Toast's
    # "Biscuits & Cookies" and the app-side "Biscuits, Cookies & Rusk"
    # subcategory (services/category_overrides.py - every Shaz International
    # item, owner's call Sep 2026) land together.
    ("Snacks & Savories", "Biscuits & Cookies"): "Biscuits, Cookies & Rusk",
    ("Snacks & Savories", "Biscuits, Cookies & Rusk"): "Biscuits, Cookies & Rusk",
    ("Snacks & Savories", "Sweets & Mithai"): "Sweets & Mithai",
    ("Snacks & Savories", "Mouth Fresheners"): "Mouth Fresheners",
    ("Snacks & Savories", "Papad"): "Papad",
    ("Spices & Seasonings", "Whole Spices"): "Spices - Whole",
    ("Spices & Seasonings", "Spice Mixes"): "Spices - Masala Mixes",
    ("Spices & Seasonings", "Seasoning Powders & Pastes"): "Spices - Masala Mixes",
    ("Spices & Seasonings", "Salt & Pepper"): "Spices - Salt & Pepper",
    ("Produce", "Fruits"): "Produce - Fruits",
    # Subcategories added by the Sep 30 category merge plan (category mapping
    # rules / corrections) - listed explicitly so each has a deliberate home.
    ("Snacks & Savories", "Chocolates & Candy"): "Chocolates & Candy",
    ("Ready-to-Eat & Instant Foods", "Soups"): "Ready-to-Eat & Instant",
    ("Tea, Coffee & Hot Beverages", "Health & Malt Drinks"): "Tea & Coffee",
    ("Tea, Coffee & Hot Beverages", "Filter & Ground Coffee"): "Tea & Coffee",
    ("Dairy & Dairy Alternatives", "Eggs"): "Dairy",
    ("Household & Cleaning", "Laundry"): "Household & Cleaning",
}

_FROZEN_SUBCATEGORY_SECTIONS = {
    "Frozen Fruits": "Frozen - Fruits",
    "Frozen Vegetables": "Frozen - Vegetables",
    "Frozen Parathas, Naan, Snacks": "Frozen - Breads & Snacks",
    "Frozen Ready Meals": "Frozen - Ready Meals",
    "Frozen Sweets & Mithai": "Frozen - Sweets & Desserts",
    "Ice Creams": "Frozen - Ice Cream",
}

# Items Toast files under the bare "Frozen Foods" bucket that are really shelf
# items (checked against the real catalog) - routed to their dry section.
_NOT_REALLY_FROZEN = [
    (r"\bdried\b|\bdates\b|\bfigs?\b", "Dry Fruits & Nuts"),
    (r"\bdry\b", "Pantry Staples"),
    (r"coconut milk|coconut cream|\(can\)", "Canned & Jarred Goods"),
    (r"desiccated", "Baking & Cooking Essentials"),
    (r"^everest\b", "Spices - Masala Mixes"),
]

# Name keywords for frozen items, checked in this order (first match wins;
# order resolves overlaps such as "puff pastry" dough vs. a dessert pastry, or
# "sambar mix" vegetables vs. ready-made sambar). All but the fruit rule are
# checked before Toast's frozen subcategory; see section_for.
_FROZEN_KEYWORDS = [
    (r"ice ?cream|kulfi|\bcone\b|\d\s*(ml|ltr)\b", "Frozen - Ice Cream"),
    (r"\bpuffs?\b|pastry sheet|puff pastry|sambosa|samosa|dough|wrapper|spring ?roll|halwa puri|cutlet|pakora",
     "Frozen - Breads & Snacks"),
    (r"pastry|halwa|gulab jamun|ras ?malai|rasgulla|burf|kalakand|laddu|ladoo|chum chum|milk cake|kaju katli"
     r"|khoya|khoa|mawa|shahi mix|brown sweet", "Frozen - Sweets & Desserts"),
    (r"samb(h)?ar mix", "Frozen - Vegetables"),
    # Frozen grated/shredded coconut and coconut slices are picked with the
    # frozen vegetables (owner's call, Sep 2026). Coconut ice creams / kulfi
    # and coconut burfee are caught by the earlier rules first.
    (r"coconut", "Frozen - Vegetables"),
    (r"samb(h)?ar|chol(e|ey)|chholey|rajma|kofta|dum aloo|pav bhaji|butter chicken|upma|kesari|kadhi"
     r"|ulavacharu|tiffin|mut(t)?on", "Frozen - Ready Meals"),
    (r"anchovy|mackerel|grouper|rohu|keski|fish|kabab|kebab|seekh|seeikh|patties|patty|burger|hot ?dogs?"
     r"|wings|nuggets|shami", "Frozen - Meat & Fish"),
    (r"par(o|a)t(t)?a|porotta|paratha|parantha|puri|bhatura|puttu|idiyappam|idl(i|y)|ut(h)?(t)?apam|uthappam"
     r"|thepla|roll|tikki|bonda|pakod|bajji|dhokla|muthia|mathia|pizza|sandwich|neyyappam|momo|murukku|borek",
     "Frozen - Breads & Snacks"),
    (r"mango|jamun|chikoo|amla|pom(e|o)granate|falsa|custard apple|seethaphal|pineapple|sugarcane"
     r"|munjalu", "Frozen - Fruits"),
]
_FROZEN_FRUIT_RULE = _FROZEN_KEYWORDS[-1:]

# Vadilal's sweets are frozen, but Toast files them under Snacks / Sweets &
# Mithai / even Lentils (Kaju Katli, Motichoor Laddu, Burfee, Moong Dal Halwa)
# - so a Vadilal sweet always goes with the frozen sweets. Shelf sweet brands
# (Haldiram's, Deep) are unaffected.
_FROZEN_SWEET_BRAND = re.compile(
    r"^vadilal\b.*(kaju katli|lad(d)?u|ladoo|burf(ee|i)|barfi|gulab jamun|ras ?malai|rasgulla|halwa"
    r"|kalakand|peda|mysore pak|soan|chum chum|mithai)"
)
_FROZEN_KIND_RULES = _FROZEN_KEYWORDS[:-1]


def _match(rules, name: str) -> Optional[str]:
    for pattern, section in rules:
        if re.search(pattern, name):
            return section
    return None


def section_for(
    category_group: Optional[str], category: Optional[str], subcategory: Optional[str], name: Optional[str]
) -> str:
    """The picking section for one inventory item. Never raises - anything
    unrecognised falls back to OTHER."""
    group = (category_group or "").strip().upper()
    category = (category or "").strip()
    subcategory = (subcategory or "").strip()
    lname = (name or "").strip().lower()

    if group == "FISH MARKET":
        return "Frozen - Meat & Fish" if category == "Frozen Fish" else "Fish - Fresh"
    if group == "HALAL MEAT":
        return "Meat - Fresh"
    if group == "FRESH FLOWERS":
        return "Flowers & Leaves"
    if group in ("CATERING MENU", "STREET KITCHEN"):
        return OTHER
    if _FROZEN_SWEET_BRAND.search(lname) and not re.search(r"ice ?cream|kulfi|\bmix\b", lname):
        return "Frozen - Sweets & Desserts"
    # Vadilal's grated/shredded coconut and slices are frozen too, whatever
    # Toast files them under (one is under Produce).
    if re.search(r"^vadilal\b.*(shred|grated|coconut slice)", lname):
        return "Frozen - Vegetables"

    if category == "Frozen Foods" or re.search(r"\bfrozen\b", lname):
        # The name is checked before Toast's frozen subcategory on purpose:
        # Toast files mango ice creams under "Frozen Fruits" and veg puffs /
        # spring rolls under "Frozen Vegetables".
        # Frozen Sweets & Mithai only exists as our own corrected category
        # (Sep 30 review), so it's trusted over name keywords - "Kaju Roll" or
        # "Mango Sandwich" would otherwise land with the frozen snacks.
        if category == "Frozen Foods" and subcategory == "Frozen Sweets & Mithai":
            return _match(_FROZEN_KEYWORDS[:1], lname) or "Frozen - Sweets & Desserts"
        if category == "Frozen Foods" and subcategory in ("", "Frozen Foods"):
            not_frozen = _match(_NOT_REALLY_FROZEN, lname)
            if not_frozen:
                return not_frozen
        return (
            _match(_FROZEN_KIND_RULES, lname)
            or (_FROZEN_SUBCATEGORY_SECTIONS.get(subcategory) if category == "Frozen Foods" else None)
            or _match(_FROZEN_FRUIT_RULE, lname)
            or "Frozen - Vegetables"
        )

    if group == "PRODUCE" or category == "Produce":
        if subcategory == "Fruits" or (group == "PRODUCE" and category == "Fruits"):
            return "Produce - Fruits"
        if category == "Plants":
            return "Plants & Seeds"
        # Vegetables, Leafy Vegetables, Roots, Fresh Herbs...
        return "Produce - Vegetables"

    return _SUBCATEGORY_SECTIONS.get((category, subcategory)) or _CATEGORY_SECTIONS.get(category) or OTHER


def section_sort_key(section: str) -> tuple:
    """Alphabetical, with OTHER always last."""
    return (section == OTHER, section.lower())


def _builtin_sections() -> tuple:
    names = set(_CATEGORY_SECTIONS.values())
    names.update(_SUBCATEGORY_SECTIONS.values())
    names.update(_FROZEN_SUBCATEGORY_SECTIONS.values())
    names.update(section for _, section in _NOT_REALLY_FROZEN)
    names.update(section for _, section in _FROZEN_KEYWORDS)
    names.update(
        [
            "Fish - Fresh",
            "Frozen - Meat & Fish",
            "Meat - Fresh",
            "Flowers & Leaves",
            "Produce - Fruits",
            "Produce - Vegetables",
            "Plants & Seeds",
            "Frozen - Sweets & Desserts",
            "Frozen - Vegetables",
        ]
    )
    return tuple(sorted(names, key=section_sort_key))


# Every section section_for() can return (except OTHER), alphabetical - the
# automatic sections the PO sections page lists even when empty.
BUILTIN_SECTIONS = _builtin_sections()


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
