"""Match a product search to an item's codes (docs/BATCH_OCT14_CONTRACT.md W2).

Every product search (cart add, Reorder, Supplier Projection, Fix Issues,
Items Sold, PO sections, the app's product search...) matches the item's
NAME as before, and now also its codes:

- the Toast item id (`item_id`, a GUID),
- the vendor's own item id / SKU (`supplier_item_id`, ';'-separated across
  an item's vendors - gotcha #6),
- the barcode (';'-separated too).

The rule is the same on the website (src/productMatch.js) and in the app
(lib/core/utils/product_match.dart); the three are tested with the same
cases:

- A code equal to the search (ignoring case and spaces) is an EXACT match.
  Barcodes made only of digits also compare without leading zeros (a UPC-A
  "018625167909" scanned as EAN-13 "0018625167909").
- Part of a code matches only when the search is long enough not to hit
  everything: 3+ characters for a vendor SKU, 4+ for a barcode, and for the
  Toast id 6+ characters with a digit or a dash in them (a GUID is hex, so
  a plain word like "masala" never matches one by accident).

Name matching itself is unchanged (case-insensitive substring).
"""

import re
from typing import Iterable, List, Optional, Tuple

from app.services.item_vendors import split_field

ITEM_ID = "item_id"
SUPPLIER_ITEM_ID = "supplier_item_id"
BARCODE = "barcode"

MIN_SUPPLIER_PART = 3
MIN_BARCODE_PART = 4
MIN_ITEM_ID_PART = 6

_ID_HINT = re.compile(r"[0-9-]")


def normalize(text: Optional[str]) -> str:
    """Lowercase, no spaces at all (codes are compared without spaces)."""
    return "".join((text or "").split()).lower()


def split_codes(value: Optional[str]) -> List[str]:
    """A ';'-separated Toast field -> its non-empty codes, as typed."""
    return split_field(value)  # services/item_vendors.py (M1 MT5)


def _digits_equal(a: str, b: str) -> bool:
    return a.isdigit() and b.isdigit() and a.lstrip("0") != "" and a.lstrip("0") == b.lstrip("0")


def match_code(
    query: Optional[str],
    item_id: Optional[str] = None,
    supplier_item_ids: Optional[str] = None,
    barcodes: Optional[str] = None,
) -> Optional[Tuple[str, str, bool]]:
    """(kind, matched code as stored, exact) for the best code match, or
    None. Exact beats partial; on a tie the Toast id, then the vendor SKU,
    then the barcode."""
    needle = normalize(query)
    if not needle:
        return None
    partial: Optional[Tuple[str, str, bool]] = None

    if item_id:
        key = normalize(item_id)
        if key == needle:
            return (ITEM_ID, item_id, True)
        if len(needle) >= MIN_ITEM_ID_PART and _ID_HINT.search(needle) and needle in key:
            partial = partial or (ITEM_ID, item_id, False)

    for code in split_codes(supplier_item_ids):
        key = normalize(code)
        if key == needle:
            return (SUPPLIER_ITEM_ID, code, True)
        if len(needle) >= MIN_SUPPLIER_PART and needle in key:
            partial = partial or (SUPPLIER_ITEM_ID, code, False)

    for code in split_codes(barcodes):
        key = normalize(code)
        if key == needle or _digits_equal(key, needle):
            return (BARCODE, code, True)
        if len(needle) >= MIN_BARCODE_PART and needle in key:
            partial = partial or (BARCODE, code, False)

    return partial


def name_matches(query: Optional[str], *names: Optional[str]) -> bool:
    needle = (query or "").strip().lower()
    return bool(needle) and any(needle in (n or "").lower() for n in names)


def snapshot_code_match(query: Optional[str], item_id: Optional[str], snap) -> Optional[Tuple[str, str, bool]]:
    """match_code on an inventory snapshot (or any object with the same
    attribute names)."""
    return match_code(
        query,
        item_id=item_id,
        supplier_item_ids=getattr(snap, "supplier_item_id", None) if snap is not None else None,
        barcodes=getattr(snap, "barcode", None) if snap is not None else None,
    )


def code_fields(snap, existing: Optional[dict] = None) -> dict:
    """Additive search fields for a list row built from `snap`: every vendor
    SKU (`all_supplier_item_ids`, Toast's ';' string) and the `barcode`, so a
    page that filters on the device can match codes too. A key the row
    already has is left alone."""
    out = {}
    existing = existing or {}
    if "all_supplier_item_ids" not in existing:
        out["all_supplier_item_ids"] = getattr(snap, "supplier_item_id", None) if snap is not None else None
    if "barcode" not in existing:
        out["barcode"] = getattr(snap, "barcode", None) if snap is not None else None
    return out


def rank(name_hit: bool, code_hit: Optional[Tuple[str, str, bool]]) -> int:
    """Sort order for a mixed result list: an exact code first, then name
    matches (the old order), then partial code matches."""
    if code_hit is not None and code_hit[2]:
        return 0
    if name_hit:
        return 1
    return 2


def any_code_match(query: Optional[str], rows: Iterable[dict]) -> bool:
    """True when any dict row (item_id / supplier_item_id / barcode keys)
    matches `query` by code - for searches over stored order lines."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if match_code(query, row.get("item_id"), row.get("supplier_item_id"), row.get("barcode")):
            return True
    return False
