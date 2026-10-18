"""Barcode lists (POS-neutral; multi-store M1 batch MT3).

An imported catalog file can list several barcodes for one item, ";"-joined
(gotcha #6), while a POS API gives one (Toast's menu ``sku``, Clover's item
``code``). ``merged_barcode`` keeps the imported list while the POS's code is
one of its codes. Moved from the Toast adapter (``catalog.py``), which still
exposes both names (``inventory_api.merged_barcode`` / ``_codes`` too).
"""

from typing import List, Optional


def _codes(raw: Optional[str]) -> List[str]:
    return [c.strip() for c in (raw or "").split(";") if c.strip()]


def merged_barcode(sku: Optional[str], csv_barcode: Optional[str]) -> Optional[str]:
    """The menu's sku holds one barcode (the first of the CSV's ";"-list for
    233 of 233 multi-barcode items on Oct 11). Keep the CSV's full list while
    the sku is one of its codes; a different sku replaces it (a barcode added
    or corrected in Toast). An EMPTY sku never wipes the CSV's barcode: on
    Oct 11, 12 items had a barcode in that night's CSV and none in the menu,
    so the menu alone can't prove a barcode is missing - only the next CSV
    can."""
    sku = (sku or "").strip() or None
    codes = _codes(csv_barcode)
    if sku is None:
        return csv_barcode.strip() if codes else None
    if sku in codes:
        return csv_barcode.strip()
    return sku
