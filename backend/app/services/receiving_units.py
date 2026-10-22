"""Case size from the Toast "retail items" export's receiving-unit columns.

The owner: "Apply receiving units as a case quantity" - if an item comes in
a case of 24 from the vendor, one case ordered is 24 units on the shelf.

Two columns, matched by header like every other column (inventory_parser):
  `receiving units`            - unit names, e.g. "Each;Case"
  `receiving unit quantities`  - units in each, same order, e.g. "1;24"

The export's multi-value fields are ";"-separated in parallel order (the
`supplier` / `supplier item id` quirk), so that is the main shape handled.
Anything else is parsed tolerantly rather than rejected - a cell this can't
read gives "no case size" (the item stays in units), never an error:
  - other separators: "," "|" "/" (between names) and new lines;
  - a number written into the name: "Case of 24", "Case (24)", "CS/24",
    "24 ct", "24pk", "Case = 24 Each";
  - one quantity for several names ("Each;Case" + "24"): it belongs to the
    one name that isn't Each;
  - blanks, "Each" alone (= 1, no case), 0 / negative / junk numbers.

The case size is the units per case: the quantity of the unit named like a
case (case / cs / carton / ctn), else the largest quantity of a unit that
isn't "Each"; only a size above 1 counts (a "case of 1" says nothing).

Pure - no database; tested directly (tests/test_case_sizes.py).

POS sources without receiving units (Clover, a plain CSV) leave the case
size unknown; ordering then falls back to the vendor's cart line, then the
last placed order (services/order_suggestion.known_case_sizes).
"""

import math
import re
from typing import List, NamedTuple, Optional, Tuple

# Biggest believable units-per-case; above this a number is junk (a pasted
# barcode or item code), not a pack size.
MAX_CASE_SIZE = 10_000

_SPLIT_RE = re.compile(r"[;\n|]+")
# A name list may also use commas; quantities too ("1, 24") - but a comma in
# a quantity could be a thousands separator ("1,000"), handled in _number.
_NAME_SPLIT_RE = re.compile(r"[;\n|,]+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_EACH_RE = re.compile(r"^(each|ea|unit|units|single|singles|piece|pieces|pc|pcs|item|items|1)$", re.I)
_CASE_RE = re.compile(r"\b(case|cases|cs|carton|cartons|ctn|ctns)\b", re.I)
# Numbers that are a weight/volume, not a count ("12 x 500g" -> 12 per case).
_MEASURE_AFTER_RE = re.compile(r"^\s*(g|gm|gms|gram|grams|kg|kgs|lb|lbs|oz|ml|l|ltr|litre|liter|fl)\b", re.I)
_COUNT_AFTER_RE = re.compile(r"^\s*(ct|count|pk|pack|packs|pcs|pc|units?|each|ea|x\b|/)", re.I)
_COUNT_BEFORE_RE = re.compile(r"(of|=|:|\(|/|x)\s*$", re.I)


class ReceivingUnit(NamedTuple):
    name: Optional[str]
    qty: Optional[float]


class CaseInfo(NamedTuple):
    # Units per case (> 1), or None when the item has no case size.
    case_size: Optional[float]
    # The receiving unit's name for the case ("Case"), or the only unit
    # named ("Each") when there is no case; None when the cells are blank.
    unit_name: Optional[str]
    # Both cells as found, "units | quantities" (blank side left empty) -
    # stored so a format nobody has seen yet can be read back later.
    raw: Optional[str]


def _clean(text) -> str:
    if text is None:
        return ""
    if isinstance(text, float) and math.isnan(text):
        return ""
    s = str(text).strip()
    return "" if s.lower() in ("nan", "none", "null", "-", "n/a") else s


def _number(text: str) -> Optional[float]:
    """The cell's number: "24" / "24.0" / "24 units" / "1,000" -> float;
    None for blank, junk, 0 or below, or absurdly big."""
    s = text.replace(",", "") if re.fullmatch(r"\s*\d{1,3}(,\d{3})+(\.\d+)?\s*", text or "") else text
    m = _NUMBER_RE.search(s or "")
    if not m:
        return None
    if (s or "")[: m.start()].strip().endswith("-"):
        return None  # "-3": a negative number is junk, not 3
    try:
        v = float(m.group(0))
    except ValueError:
        return None
    if not math.isfinite(v) or v <= 0 or v > MAX_CASE_SIZE:
        return None
    return v


def _number_in_name(name: str) -> Optional[float]:
    """A count written into a unit name: "Case of 24", "Case (24)", "CS/24",
    "24 ct", "24pk", "12 x 500g" (-> 12). None when there's none or it's
    ambiguous."""
    counts = []
    for m in _NUMBER_RE.finditer(name):
        before, after = name[: m.start()], name[m.end():]
        if _MEASURE_AFTER_RE.match(after):
            continue
        strong = bool(_COUNT_AFTER_RE.match(after) or _COUNT_BEFORE_RE.search(before))
        counts.append((strong, float(m.group(0))))
    strong = [v for s, v in counts if s]
    picked = strong[0] if strong else (counts[0][1] if len(counts) == 1 else None)
    if picked is None or not math.isfinite(picked) or picked <= 0 or picked > MAX_CASE_SIZE:
        return None
    return picked


def _name_only(name: str) -> str:
    """"Case of 24" -> "Case"; "Each" -> "Each"; "24 ct" -> "24 ct"."""
    m = _CASE_RE.search(name)
    if m:
        return m.group(1).capitalize() if m.group(1).lower() in ("case", "cases") else m.group(1).upper()
    return name.strip()


def is_each(name: Optional[str]) -> bool:
    return bool(name) and bool(_EACH_RE.match(name.strip()))


def split_units(units_raw, qtys_raw) -> List[ReceivingUnit]:
    """Pair the two cells up into (name, qty) units (see the module notes)."""
    units_text, qtys_text = _clean(units_raw), _clean(qtys_raw)
    names = [n.strip() for n in _NAME_SPLIT_RE.split(units_text) if n.strip()] if units_text else []
    if len(names) == 1 and "/" in names[0] and not _NUMBER_RE.search(names[0]):
        names = [n.strip() for n in names[0].split("/") if n.strip()]  # "Each/Case"
    qty_parts = [q.strip() for q in _SPLIT_RE.split(qtys_text) if q.strip()] if qtys_text else []
    if len(qty_parts) == 1 and len(names) > 1 and "," in qty_parts[0]:
        commas = [q.strip() for q in qty_parts[0].split(",") if q.strip()]
        if len(commas) == len(names):  # "1, 24" next to "Each, Case"
            qty_parts = commas
    if not qty_parts and len(names) <= 1 and "," in qtys_text:
        qty_parts = [qtys_text]
    qtys = [_number(q) for q in qty_parts]

    out: List[ReceivingUnit] = []
    if names and len(qtys) == len(names):
        for n, q in zip(names, qtys):
            out.append(ReceivingUnit(n, q if q is not None else _number_in_name(n)))
    elif names and len(qtys) == 1 and len(names) > 1:
        # One quantity for "Each;Case": the non-Each unit's.
        others = [n for n in names if not is_each(n)]
        for n in names:
            if is_each(n):
                out.append(ReceivingUnit(n, 1.0))
            elif len(others) == 1:
                out.append(ReceivingUnit(n, qtys[0] if qtys[0] is not None else _number_in_name(n)))
            else:
                out.append(ReceivingUnit(n, _number_in_name(n)))
    elif names:
        for i, n in enumerate(names):
            q = qtys[i] if i < len(qtys) else None
            out.append(ReceivingUnit(n, q if q is not None else _number_in_name(n)))
    else:
        for q in qtys:
            out.append(ReceivingUnit(None, q))
    # An "Each" with no number is 1.
    return [ReceivingUnit(u.name, 1.0) if u.qty is None and is_each(u.name) else u for u in out]


def case_info(units_raw, qtys_raw) -> CaseInfo:
    """CaseInfo for one item's two cells."""
    units_text, qtys_text = _clean(units_raw), _clean(qtys_raw)
    raw = f"{units_text} | {qtys_text}".strip(" |") if (units_text or qtys_text) else None
    if raw is not None and len(raw) > 500:
        raw = raw[:500]
    units = split_units(units_raw, qtys_raw)
    if not units:
        return CaseInfo(None, None, raw)

    def size(u: ReceivingUnit) -> float:
        return u.qty if u.qty is not None else 0.0

    marked = [u for u in units if u.name and _CASE_RE.search(u.name) and size(u) > 1]
    if marked:
        best = max(marked, key=size)
    else:
        not_each = [u for u in units if not is_each(u.name) and size(u) > 1]
        best = max(not_each, key=size) if not_each else None
    if best is None:
        first_named = next((u.name for u in units if u.name), None)
        return CaseInfo(None, _name_only(first_named) if first_named else None, raw)
    return CaseInfo(best.qty, _name_only(best.name) if best.name else "Case", raw)


def case_size_value(value) -> Optional[float]:
    """A stored / sent case size, or None unless it's a real one (> 1)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 1 or v > MAX_CASE_SIZE:
        return None
    return v


def cases_for_units(units: float, case_size: Optional[float]) -> Tuple[float, Optional[float]]:
    """(order qty, case size used): whole cases covering `units` when the case
    size is known - ceil(max(units, 0) / case size) - else the units as they
    are. Owner: "Order qty has to be in cases ALWAYS"."""
    size = case_size_value(case_size)
    if size is None:
        return units, None
    if units <= 0:
        return 0, size
    return int(math.ceil(units / size - 1e-9)), size


def fmt_num(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "0"
    r = round(f, 2)
    return str(int(r)) if r == int(r) else f"{r:g}"


def qty_text(qty, case_of) -> str:
    """How a cart / PO line's quantity reads: "2 cases (48 units)",
    "1 case (24 units)", or just "5" for an item not ordered by the case."""
    size = case_size_value(case_of)
    try:
        q = float(qty or 0)
    except (TypeError, ValueError):
        q = 0.0
    if size is None:
        return fmt_num(q)
    word = "case" if q == 1 else "cases"
    return f"{fmt_num(q)} {word} ({fmt_num(q * size)} units)"


def po_line_case_fields(qty, case_of) -> dict:
    """The case fields every PO line carries (additive; the PO downloads,
    services/po_download.py, read `case_size` / `cases`): case_size (units per case, null
    when not ordered by the case), cases (= qty when by the case, else
    null), units (qty x case size) and qty_text ("2 cases (48 units)")."""
    size = case_size_value(case_of)
    try:
        q = float(qty or 0)
    except (TypeError, ValueError):
        q = 0.0
    return {
        "case_size": size,
        "cases": q if size is not None else None,
        "units": round(q * (size or 1.0), 3),
        "qty_text": qty_text(q, size),
    }
