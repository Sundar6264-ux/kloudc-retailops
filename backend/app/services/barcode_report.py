"""Missing/invalid barcode reports against the latest inventory snapshot.

Validation checks *structural* correctness against the GS1 standard (right
length for a UPC-A/EAN-8/EAN-13/GTIN-14, and a correct check digit) - it can't
confirm a code is actually registered to this product, since that requires
paid access to GS1's real database. "Invalid" here means "malformed", not
"not really yours".
"""

from sqlalchemy.orm import Session

from app.services.inventory_api import barcode_overlay
from app.services.reorder import latest_inventory_by_item

_GS1_FORMATS_BY_LENGTH = {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}


def _gs1_check_digit(digits_without_check: str) -> int:
    """Standard GS1 check digit algorithm: from the rightmost digit leftward,
    alternate weights 3 and 1 (starting with 3), sum, then check digit is
    whatever makes the total a multiple of 10.
    """
    total = 0
    weight = 3
    for ch in reversed(digits_without_check):
        total += int(ch) * weight
        weight = 1 if weight == 3 else 3
    return (10 - (total % 10)) % 10


def validate_barcode(raw: str) -> dict:
    code = raw.strip()

    if not code.isdigit():
        return {"valid": False, "format": None, "reason": "contains non-digit characters"}

    length = len(code)
    expected_format = _GS1_FORMATS_BY_LENGTH.get(length)
    if expected_format is None:
        return {
            "valid": False,
            "format": None,
            "reason": f"unusual length ({length} digits) - not a standard UPC-A/EAN-8/EAN-13/GTIN-14 length",
        }

    body, check_digit = code[:-1], int(code[-1])
    expected_check_digit = _gs1_check_digit(body)
    if check_digit != expected_check_digit:
        return {
            "valid": False,
            "format": expected_format,
            "reason": f"check digit is {check_digit}, expected {expected_check_digit} (fails {expected_format} checksum)",
        }

    return {"valid": True, "format": expected_format, "reason": None}


def get_missing_barcodes(db: Session) -> list[dict]:
    # Barcodes from the hourly Toast menu re-check when it is newer than the
    # latest snapshot (inventory_api.barcode_overlay, contract item 6).
    latest = barcode_overlay(db, latest_inventory_by_item(db))
    items = [
        {
            "item_id": snap.item_id,
            "name": snap.name,
            "category": snap.category,
            "supplier": snap.supplier,
            "inventory_snapshot_date": snap.snapshot_date.isoformat(),
        }
        for snap in latest.values()
        if not snap.barcode or not snap.barcode.strip()
    ]
    items.sort(key=lambda i: i["name"] or "")
    return items


def get_invalid_barcodes(db: Session) -> list[dict]:
    """An item can legitimately list several barcodes in one semicolon-separated
    field (different pack sizes/regions mapped to one Toast item) - that's not a
    defect on its own. Only flag an item here if at least one of its listed
    codes fails validation; the reason names exactly which code(s) and why.
    """
    # Barcodes from the hourly Toast menu re-check when it is newer than the
    # latest snapshot (inventory_api.barcode_overlay, contract item 6).
    latest = barcode_overlay(db, latest_inventory_by_item(db))
    items = []
    for snap in latest.values():
        if not snap.barcode or not snap.barcode.strip():
            continue  # missing is its own report, not "invalid"

        codes = [c.strip() for c in snap.barcode.split(";") if c.strip()]
        bad = [(code, validate_barcode(code)) for code in codes]
        bad = [(code, r) for code, r in bad if not r["valid"]]
        if not bad:
            continue

        reason = "; ".join(f"{code}: {r['reason']}" for code, r in bad)
        items.append(
            {
                "item_id": snap.item_id,
                "name": snap.name,
                "category": snap.category,
                "supplier": snap.supplier,
                "barcode": snap.barcode,
                "reason": reason,
                "inventory_snapshot_date": snap.snapshot_date.isoformat(),
            }
        )
    items.sort(key=lambda i: i["name"] or "")
    return items
