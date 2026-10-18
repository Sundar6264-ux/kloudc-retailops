"""Item vendors: one place that says who supplies an item (multi-store M1,
MT5; plan section 4.9; docs/MULTISTORE_M1_CONTRACT.md "As built - MT5").

Before MT5 the vendor list was a ";"-joined string from the Toast Retail CSV
(`inventory_snapshots.supplier`, gotcha #6), split by 8 hand-written parsers
(reorder, supplier projection, PO section rules, Fix Issues leads, placed
orders, similar products, category overrides, product codes). They all now
call this module, so the rule lives in one place:

- `split_field(raw)`: any ";"-separated Toast field (vendors, vendor item
  codes, barcodes) - each part trimmed, empty parts dropped, order, case,
  repeats and junk values kept exactly as the source has them.
  `keep_empty=True` keeps the empty slots, so position i of `supplier`
  still lines up with position i of `supplier_item_id`.
- `vendors_of(item)`: an item's vendors in the order the source lists them
  (rank = position). `item` is a snapshot row, a dict with "supplier", or
  the raw string. For Toast this reads the item's own CSV field - the CSV
  wins - so every page shows what it showed before.

The `item_vendors` table (app/item_vendor_models.py, migration 0061) holds
the same lists as app data, one row per vendor:

- `ranked_vendors(supplier, supplier_item_id)` turns a CSV row into those
  rows: one per vendor (a name listed twice, "Chetak;Chetak", is kept once at
  its first position; repeats are matched ignoring case and the first
  spelling is kept), rank 1 = listed first, plus that vendor's own item code
  (same rule as similar_products.vendor_refs). Junk values such as
  "ACT_SUP_UPDT" are kept, as the CSV has them - they are filtered where
  they are shown (similar_products.is_junk_vendor), as before.
- `refresh_from_csv(db, item_ids)` makes the table match each item's newest
  CSV row (the row the Toast API snapshot carries supplier fields from). The
  CSV upload calls it for the items it loaded; the 0061 migration did the
  first fill. A CSV list replaces the item's whole list, manual rows too
  (the CSV wins); an empty CSV list only clears the CSV's own rows.
- `render_vendor_fields(vendors)` goes the other way (table -> the
  ";"-joined `supplier` / `supplier_item_id` pair). A store whose POS has no
  vendor field (M3, Clover) fills its catalog rows' fields from the table
  with it, so the same pages work there.

Nothing here talks to Toast or any other outside service.
"""

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.item_vendor_models import ItemVendor
from app.models import InventorySnapshot
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

FIELD_SEP = ";"

SOURCE_TOAST_CSV = "toast_csv"
SOURCE_MANUAL = "manual"
SOURCES = (SOURCE_TOAST_CSV, SOURCE_MANUAL)

# Snapshot rows built from the Toast API (services/inventory_api.py
# API_SOURCE_PREFIX; a test keeps the two equal). Every other row is a CSV row.
API_SOURCE_PREFIX = "api:"

# SQLite's bound-parameter limit is 999 on older builds.
_CHUNK = 500


# ---------------------------------------------------------------------------
# Parsing (no database)
# ---------------------------------------------------------------------------


def split_field(raw: Any, keep_empty: bool = False) -> List[str]:
    """A ";"-separated Toast field -> its parts, trimmed, in order.

    Empty parts are dropped unless `keep_empty` (then every slot keeps its
    place: ";2524;856977005124" -> ["", "2524", "856977005124"]). Nothing
    else is changed: repeats, case and junk values stay. None / "" -> [].
    """
    if not raw:
        return []
    parts = [s.strip() for s in str(raw).split(FIELD_SEP)]
    return parts if keep_empty else [s for s in parts if s]


def _raw_supplier(item: Any) -> Any:
    if item is None or isinstance(item, (str, bytes, int, float)):
        return item
    if isinstance(item, Mapping):
        return item.get("supplier")
    return getattr(item, "supplier", None)


def vendors_of(item: Any, keep_empty: bool = False) -> List[str]:
    """An item's vendors, in the order its source lists them (first = rank 1).

    `item`: an inventory snapshot row (or anything with a `supplier`
    attribute), a dict with a "supplier" key, or the raw field itself. The
    list is exactly the item's own field split on ";" (split_field rules), so
    repeats are kept: callers that need one entry per vendor use
    ranked_vendors().
    """
    return split_field(_raw_supplier(item), keep_empty=keep_empty)


def aligned_item_codes(supplier: Any, supplier_item_id: Any) -> Optional[List[Optional[str]]]:
    """The vendor item code for each slot of `supplier` (None where empty), or
    None when the two fields don't line up. A trailing empty code slot is
    ignored ("A;B" with "1;2;")."""
    vendors = split_field(supplier, keep_empty=True)
    codes = split_field(supplier_item_id, keep_empty=True)
    while len(codes) > len(vendors) and codes and codes[-1] == "":
        codes.pop()
    if len(codes) != len(vendors):
        return None
    return [c or None for c in codes]


@dataclass(frozen=True)
class RankedVendor:
    rank: int  # 1 = listed first
    name: str
    item_code: Optional[str] = None


def ranked_vendors(supplier: Any, supplier_item_id: Any = None) -> List[RankedVendor]:
    """One entry per vendor, in listed order, with the vendor's item code.

    - empty slots are skipped and don't use up a rank;
    - a vendor listed again (same name ignoring case and spaces at the ends)
      keeps its first position and first spelling; its code is the first
      code found across its slots;
    - codes come from the slot-aligned `supplier_item_id`; when the two
      fields don't line up, a single code is kept only for a single vendor
      slot (same rule as similar_products.vendor_refs);
    - junk values ("ACT_SUP_UPDT", "2234") are kept as listed.
    """
    slots = split_field(supplier, keep_empty=True)
    codes = aligned_item_codes(supplier, supplier_item_id)
    named = [v for v in slots if v]
    real_codes = split_field(supplier_item_id)
    out: List[RankedVendor] = []
    index_by_key: Dict[str, int] = {}
    for i, name in enumerate(slots):
        if not name:
            continue
        code = None
        if codes is not None:
            code = codes[i]
        elif len(named) == 1 and len(real_codes) == 1:
            code = real_codes[0]
        key = name.lower()
        seen = index_by_key.get(key)
        if seen is not None:
            if out[seen].item_code is None and code:
                out[seen] = RankedVendor(out[seen].rank, out[seen].name, code)
            continue
        index_by_key[key] = len(out)
        out.append(RankedVendor(len(out) + 1, name, code))
    return out


def render_vendor_fields(vendors: Iterable[Any]) -> Tuple[Optional[str], Optional[str]]:
    """Table rows (ItemVendor or RankedVendor, any order) -> the ";"-joined
    (`supplier`, `supplier_item_id`) pair a catalog row carries, in rank
    order. (None, None) for no vendors; the codes field is None when no
    vendor has a code. ranked_vendors() of the result gives the same list."""
    rows = sorted(vendors, key=lambda v: v.rank)
    if not rows:
        return None, None
    names = [_name_of(v) for v in rows]
    codes = [_code_of(v) or "" for v in rows]
    return FIELD_SEP.join(names), (FIELD_SEP.join(codes) if any(codes) else None)


def _name_of(v: Any) -> str:
    return v.vendor_name if isinstance(v, ItemVendor) else v.name


def _code_of(v: Any) -> Optional[str]:
    return v.vendor_item_code if isinstance(v, ItemVendor) else v.item_code


# ---------------------------------------------------------------------------
# The item_vendors table
# ---------------------------------------------------------------------------


def _chunks(values: Sequence[str]) -> Iterable[Sequence[str]]:
    for i in range(0, len(values), _CHUNK):
        yield values[i : i + _CHUNK]


def _csv_only():
    return or_(
        InventorySnapshot.source_filename.is_(None),
        ~InventorySnapshot.source_filename.like(API_SOURCE_PREFIX + "%"),
    )


def latest_csv_rows(db: Session, item_ids: Optional[Iterable[str]] = None) -> Dict[str, tuple]:
    """item_id -> (snapshot_date, supplier, supplier_item_id) of the item's
    newest CSV (non-API) snapshot row - the row Toast API snapshots carry
    their supplier fields from (inventory_api.latest_csv_row_per_item)."""

    def query(ids: Optional[Sequence[str]]):
        newest = select(InventorySnapshot.item_id, func.max(InventorySnapshot.snapshot_date).label("d")).where(_csv_only())
        if ids is not None:
            newest = newest.where(InventorySnapshot.item_id.in_(ids))
        newest = newest.group_by(InventorySnapshot.item_id).subquery()
        return select(
            InventorySnapshot.item_id,
            InventorySnapshot.snapshot_date,
            InventorySnapshot.supplier,
            InventorySnapshot.supplier_item_id,
        ).join(
            newest,
            and_(InventorySnapshot.item_id == newest.c.item_id, InventorySnapshot.snapshot_date == newest.c.d),
        )

    out: Dict[str, tuple] = {}
    if item_ids is None:
        batches: Iterable[Optional[Sequence[str]]] = [None]
    else:
        batches = list(_chunks(sorted(set(item_ids))))
    for ids in batches:
        for item_id, day, supplier, codes in db.execute(query(ids)):
            out[item_id] = (day, supplier, codes)
    return out


def load_vendor_table(db: Session, item_ids: Optional[Iterable[str]] = None) -> Dict[str, List[ItemVendor]]:
    """item_id -> its item_vendors rows, rank order (all sources)."""
    out: Dict[str, List[ItemVendor]] = defaultdict(list)
    if item_ids is None:
        batches: Iterable[Optional[Sequence[str]]] = [None]
    else:
        batches = list(_chunks(sorted(set(item_ids))))
    for ids in batches:
        stmt = select(ItemVendor)
        if ids is not None:
            stmt = stmt.where(ItemVendor.item_id.in_(ids))
        for row in db.execute(stmt.order_by(ItemVendor.item_id, ItemVendor.rank, ItemVendor.id)).scalars():
            out[row.item_id].append(row)
    return dict(out)


def table_vendors_of(db: Session, item_id: str) -> List[str]:
    """The vendor names the table holds for one item, rank order."""
    return [r.vendor_name for r in load_vendor_table(db, [item_id]).get(item_id, [])]


def _same(current: List[ItemVendor], wanted: List[RankedVendor]) -> bool:
    """Same list (source, rank, name, code); the source date is not compared."""
    return [(r.source, r.rank, r.vendor_name, r.vendor_item_code) for r in current] == [
        (SOURCE_TOAST_CSV, v.rank, v.name, v.item_code) for v in wanted
    ]


def refresh_from_csv(db: Session, item_ids: Optional[Iterable[str]] = None) -> dict:
    """Make the table match each item's newest CSV row (all items when
    `item_ids` is None). Only items whose list changed are rewritten. Does
    not commit. Items without any CSV row are left alone.

    A non-empty CSV list replaces every row of the item (manual ones too:
    the CSV wins); an empty one removes only the CSV's own rows.
    """
    ids = None if item_ids is None else sorted({i for i in item_ids if i})
    stats = {"items_checked": 0, "items_changed": 0, "rows_written": 0, "rows_deleted": 0, "rows_redated": 0}
    if ids is not None and not ids:
        return stats
    latest = latest_csv_rows(db, ids)
    current = load_vendor_table(db, list(latest.keys()) if ids is not None else None)
    now = utcnow()
    redate: Dict[dt.date, List[int]] = defaultdict(list)
    for item_id, (day, supplier, codes) in latest.items():
        stats["items_checked"] += 1
        wanted = ranked_vendors(supplier, codes)
        have = current.get(item_id, [])
        if wanted:
            if _same(have, wanted):
                # Same list from a newer CSV: only the source date moves.
                redate[day].extend(r.id for r in have if r.source_date != day)
                continue
            doomed = have
        else:
            doomed = [r for r in have if r.source == SOURCE_TOAST_CSV]
            if not doomed:
                continue
        stats["items_changed"] += 1
        if doomed:
            db.execute(delete(ItemVendor).where(ItemVendor.id.in_([r.id for r in doomed])))
            stats["rows_deleted"] += len(doomed)
        for v in wanted:
            db.add(
                ItemVendor(
                    item_id=item_id,
                    vendor_name=v.name,
                    vendor_item_code=v.item_code,
                    rank=v.rank,
                    source=SOURCE_TOAST_CSV,
                    source_date=day,
                    created_at=now,
                    updated_at=now,
                )
            )
            stats["rows_written"] += 1
    for day, row_ids in redate.items():
        for chunk in _chunks(row_ids):
            db.execute(
                update(ItemVendor.__table__).where(ItemVendor.__table__.c.id.in_(chunk)).values(source_date=day)
            )
        stats["rows_redated"] += len(row_ids)
    db.flush()
    return stats


def refresh_after_csv_upload(db: Session, item_ids: Iterable[str]) -> Optional[dict]:
    """The CSV upload's hook (inventory_parser.parse_and_store). Runs in a
    savepoint and never raises: a failure here must not fail the upload (it
    is logged, and the next upload or scripts/item_vendors_check.py --refresh
    puts the table right)."""
    try:
        with db.begin_nested():
            return refresh_from_csv(db, item_ids)
    except Exception:  # noqa: BLE001
        logger.exception("item_vendors: refresh after CSV upload failed")
        return None


def compare_with_snapshots(db: Session) -> dict:
    """How far the table is from each item's newest CSV row (0 mismatches
    expected). Also counts what the parsers see, for the MT5 parity report."""
    latest = latest_csv_rows(db)
    table = load_vendor_table(db)
    out = {
        "items_with_csv_row": len(latest),
        "items_with_vendors": 0,
        "table_items": len(table),
        "table_rows": sum(len(v) for v in table.values()),
        "multi_vendor_items": 0,
        "items_with_repeated_vendor": 0,
        "junk_vendor_rows": 0,
        "mismatched_items": 0,
        "mismatch_examples": [],
    }
    from app.services.similar_products import is_junk_vendor  # local: similar_products imports this module

    for item_id, (day, supplier, codes) in latest.items():
        wanted = ranked_vendors(supplier, codes)
        listed = vendors_of(supplier)
        if wanted:
            out["items_with_vendors"] += 1
        if len(wanted) > 1:
            out["multi_vendor_items"] += 1
        if len(listed) != len(wanted):
            out["items_with_repeated_vendor"] += 1
        out["junk_vendor_rows"] += sum(1 for v in wanted if is_junk_vendor(v.name))
        have = [r for r in table.get(item_id, []) if r.source == SOURCE_TOAST_CSV]
        if not _same(have, wanted) or any(r.source_date != day for r in have):
            out["mismatched_items"] += 1
            if len(out["mismatch_examples"]) < 10:
                out["mismatch_examples"].append(item_id)
    stray = set(table) - set(latest)
    out["table_items_without_csv_row"] = len(stray)
    return out
