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
            ids = list(item_ids)
            stats = refresh_from_csv(db, ids)
            if editor_editable():
                # A store without Toast keeps vendors typed in the Item
                # vendors editor for items its file lists none for: the
                # snapshot rows get the table's list (the file's own list
                # when it has one - refresh_from_csv just stored it).
                stats["snapshot_rows_filled"] = apply_to_snapshots(db, ids)
            return stats
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


# ---------------------------------------------------------------------------
# The Item vendors editor (website-ready track D4: Setup > Item vendors)
# ---------------------------------------------------------------------------
#
# Who may edit: a store whose POS gives no vendor field in a catalog file -
# every POS except Toast (Clover, CSV-only). A Toast store's list follows its
# inventory CSV (the CSV wins), so the editor is read-only there.
#
# The editor writes "manual" rows (rank = the order given) and then puts the
# same list into the item's rows of the newest inventory snapshot date
# (`supplier` / `supplier_item_id`, render_vendor_fields), so every page that
# reads vendors_of(row) - reorder, supplier projection, PO sections, Fix
# Issues - sees the change at once. The nightly Clover snapshot fills those
# fields from this table too (pos/sync/catalog.py, pos_is_catalog_source).

import csv as _csv
import io as _io
import re as _re

MAX_VENDORS_PER_ITEM = 10
MAX_NAME_LEN = 80
MAX_CODE_LEN = 64
MAX_IMPORT_ROWS = 20000
EXPORT_HEADERS = ("Item ID", "Item name", "Barcode", "Vendor", "Vendor item code", "Rank")
_ID_HEADERS = frozenset({"item id", "item_id", "itemid", "item guid", "clover item id", "id"})
_BARCODE_HEADERS = frozenset({"barcode", "upc", "code", "sku"})
_NAME_HEADERS = frozenset({"item name", "item", "name", "product", "product name"})
_VENDOR_HEADERS = frozenset({"vendor", "vendor name", "supplier", "supplier name"})
_CODE_HEADERS = frozenset({"vendor item code", "vendor code", "supplier item id", "supplier item code", "item code"})
_RANK_HEADERS = frozenset({"rank", "order", "priority"})


class VendorEditError(ValueError):
    """A bad value from the editor (HTTP 400)."""


def editor_editable() -> bool:
    """False for a Toast store (its inventory CSV holds the vendors)."""
    from app.pos.registry import provider_name

    return provider_name() != "toast"


def _latest_date(db: Session) -> Optional[dt.date]:
    return db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar()


def catalog_items(db: Session) -> Dict[str, InventorySnapshot]:
    """item_id -> the item's row on the newest snapshot date (the editor's
    item list: what the store stocks now)."""
    day = _latest_date(db)
    if day is None:
        return {}
    rows = db.execute(select(InventorySnapshot).where(InventorySnapshot.snapshot_date == day)).scalars()
    out: Dict[str, InventorySnapshot] = {}
    for r in rows:
        out.setdefault(r.item_id, r)
    return out


def _clean_name(raw: Any) -> str:
    v = " ".join(str(raw or "").split())
    if not v:
        raise VendorEditError("Enter a vendor name.")
    if len(v) > MAX_NAME_LEN:
        raise VendorEditError(f"A vendor name can be at most {MAX_NAME_LEN} characters.")
    if FIELD_SEP in v or any(ord(c) < 32 for c in v):
        raise VendorEditError('A vendor name can\'t contain ";".')
    return v


def _clean_code(raw: Any) -> Optional[str]:
    v = str(raw or "").strip()
    if not v:
        return None
    if len(v) > MAX_CODE_LEN:
        raise VendorEditError(f"A vendor item code can be at most {MAX_CODE_LEN} characters.")
    if FIELD_SEP in v or any(ord(c) < 32 for c in v):
        raise VendorEditError('A vendor item code can\'t contain ";".')
    return v


def clean_vendor_list(vendors: Iterable[Mapping[str, Any]]) -> List[RankedVendor]:
    """The editor's list -> ranked vendors (order given = rank). Refuses
    blanks, repeats (ignoring case) and more than 10."""
    out: List[RankedVendor] = []
    seen = set()
    for v in vendors or []:
        name = _clean_name((v or {}).get("name"))
        key = name.lower()
        if key in seen:
            raise VendorEditError(f"{name} is listed twice.")
        seen.add(key)
        out.append(RankedVendor(len(out) + 1, name, _clean_code((v or {}).get("item_code"))))
    if len(out) > MAX_VENDORS_PER_ITEM:
        raise VendorEditError(f"An item can have at most {MAX_VENDORS_PER_ITEM} vendors.")
    return out


def _same_manual(current: List[ItemVendor], wanted: List[RankedVendor]) -> bool:
    return [(r.rank, r.vendor_name, r.vendor_item_code) for r in current] == [
        (v.rank, v.name, v.item_code) for v in wanted
    ]


def apply_to_snapshots(db: Session, item_ids: Iterable[str]) -> int:
    """Put the table's lists into these items' rows of the newest snapshot
    date (what the pages read). Returns the rows updated."""
    ids = sorted({i for i in item_ids if i})
    day = _latest_date(db)
    if not ids or day is None:
        return 0
    table = load_vendor_table(db, ids)
    changed = 0
    for chunk in _chunks(ids):
        for row in db.execute(
            select(InventorySnapshot).where(InventorySnapshot.snapshot_date == day, InventorySnapshot.item_id.in_(chunk))
        ).scalars():
            supplier, codes = render_vendor_fields(table.get(row.item_id, []))
            if (row.supplier, row.supplier_item_id) != (supplier, codes):
                row.supplier, row.supplier_item_id = supplier, codes
                changed += 1
    db.flush()
    return changed


def set_item_vendors(db: Session, item_id: str, vendors: Iterable[Mapping[str, Any]]) -> Tuple[List[ItemVendor], bool]:
    """Replace one item's vendor list with `vendors` (manual rows). Returns
    (the rows now stored, whether anything changed). Does not commit."""
    wanted = clean_vendor_list(vendors)
    have = load_vendor_table(db, [item_id]).get(item_id, [])
    if have and all(r.source == SOURCE_MANUAL for r in have) and _same_manual(have, wanted):
        return have, False
    if not have and not wanted:
        return [], False
    now = utcnow()
    if have:
        db.execute(delete(ItemVendor).where(ItemVendor.item_id == item_id))
    for v in wanted:
        db.add(ItemVendor(item_id=item_id, vendor_name=v.name, vendor_item_code=v.item_code, rank=v.rank,
                          source=SOURCE_MANUAL, source_date=None, created_at=now, updated_at=now))
    db.flush()
    apply_to_snapshots(db, [item_id])
    return load_vendor_table(db, [item_id]).get(item_id, []), True


# The columns the editor shows (item_view): a page reads only these, not
# whole snapshot rows (pre-production gap G14).
_EDITOR_COLUMNS = (
    InventorySnapshot.item_id,
    InventorySnapshot.name,
    InventorySnapshot.barcode,
    InventorySnapshot.category_group,
    InventorySnapshot.category,
)


def catalog_rows(db: Session) -> Dict[str, Any]:
    """item_id -> (item_id, name, barcode, category_group, category) on the
    newest snapshot date: catalog_items without loading whole rows."""
    day = _latest_date(db)
    if day is None:
        return {}
    out: Dict[str, Any] = {}
    for r in db.execute(select(*_EDITOR_COLUMNS).where(InventorySnapshot.snapshot_date == day)):
        out.setdefault(r.item_id, r)
    return out


def catalog_item(db: Session, item_id: str) -> Optional[InventorySnapshot]:
    """One item's row on the newest snapshot date, or None."""
    day = _latest_date(db)
    if day is None:
        return None
    return db.execute(
        select(InventorySnapshot)
        .where(InventorySnapshot.snapshot_date == day, InventorySnapshot.item_id == item_id)
        .order_by(InventorySnapshot.id)
        .limit(1)
    ).scalar_one_or_none()


def vendor_view(rows: Iterable[ItemVendor]) -> List[dict]:
    return [{"name": r.vendor_name, "item_code": r.vendor_item_code, "rank": r.rank, "source": r.source} for r in rows]


def item_view(row: InventorySnapshot, vendors: Iterable[ItemVendor]) -> dict:
    return {
        "item_id": row.item_id,
        "name": row.name,
        "barcode": (split_field(row.barcode) or [None])[0],
        "category": " / ".join(p for p in (row.category_group, row.category) if p) or None,
        "vendors": vendor_view(vendors),
    }


def list_editor_items(db: Session, q: str = "", show: str = "all", limit: int = 50, offset: int = 0) -> dict:
    """The editor's page: items (newest snapshot) matching `q` (name, id,
    barcode or vendor), `show` = all | missing (no vendor) | with."""
    items = catalog_rows(db)
    # One query for every vendor row (no IN batches over the whole catalog);
    # rows of items no longer in the catalog are simply not looked at.
    table = load_vendor_table(db) if items else {}
    needle = (q or "").strip().lower()
    out = []
    missing = 0
    for item_id, row in items.items():
        vendors = table.get(item_id, [])
        if not vendors:
            missing += 1
        if show == "missing" and vendors:
            continue
        if show == "with" and not vendors:
            continue
        if needle:
            hay = " ".join(
                [row.name or "", item_id, row.barcode or ""] + [v.vendor_name for v in vendors] + [v.vendor_item_code or "" for v in vendors]
            ).lower()
            if needle not in hay:
                continue
        out.append((row, vendors))
    out.sort(key=lambda p: ((p[0].name or "").lower(), p[0].item_id))
    names = sorted({r.vendor_name for item_id, rows in table.items() if item_id in items for r in rows}, key=str.lower)
    page = out[max(0, offset): max(0, offset) + max(1, min(limit, 200))]
    return {
        "items": [item_view(r, v) for r, v in page],
        "total": len(out),
        "catalog_total": len(items),
        "missing_count": missing,
        "vendor_names": names[:2000],
        "offset": offset,
        "limit": limit,
    }


# --- CSV import / export -------------------------------------------------------------------------


def _norm_header(h: Any) -> str:
    return _re.sub(r"\s+", " ", str(h or "").strip().lower().replace("_", " "))


def _find(headers: List[str], names: set) -> Optional[int]:
    for i, h in enumerate(headers):
        if h in names:
            return i
    return None


def parse_import(db: Session, filename: str, content: bytes) -> dict:
    """Read an Item vendors CSV. One row per (item, vendor); an item's rows
    in the file are its whole new list (by Rank, else file order). Items are
    found by Item ID, else barcode/SKU, else an exact, unique item name.

    Returns {"rows": [{line, item_id, item_name, vendor, item_code, error}],
    "items": {item_id: [RankedVendor]}, "errors": n, "headers_ok": bool}.
    Nothing is written."""
    if not (filename or "").lower().endswith(".csv"):
        raise VendorEditError("Upload a .csv file (save a spreadsheet as CSV first).")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = list(_csv.reader(_io.StringIO(text)))
    reader = [r for r in reader if any((c or "").strip() for c in r)]
    if not reader:
        raise VendorEditError("The file is empty.")
    if len(reader) - 1 > MAX_IMPORT_ROWS:
        raise VendorEditError(f"The file has more than {MAX_IMPORT_ROWS:,} rows. Split it and import each part.")
    headers = [_norm_header(h) for h in reader[0]]
    i_id, i_bar, i_name = _find(headers, _ID_HEADERS), _find(headers, _BARCODE_HEADERS), _find(headers, _NAME_HEADERS)
    i_vendor, i_code, i_rank = _find(headers, _VENDOR_HEADERS), _find(headers, _CODE_HEADERS), _find(headers, _RANK_HEADERS)
    if i_vendor is None or (i_id is None and i_bar is None and i_name is None):
        raise VendorEditError("The file needs a Vendor column and an Item ID (or Barcode, or Item name) column. "
                              "Download the template to see the columns.")
    items = catalog_items(db)
    by_barcode: Dict[str, List[str]] = defaultdict(list)
    by_name: Dict[str, List[str]] = defaultdict(list)
    for item_id, row in items.items():
        for code in split_field(row.barcode):
            by_barcode[code.lower()].append(item_id)
        if row.name:
            by_name[" ".join(row.name.split()).lower()].append(item_id)

    def cell(r: List[str], i: Optional[int]) -> str:
        return (r[i] if i is not None and i < len(r) else "").strip()

    rows_out: List[dict] = []
    grouped: Dict[str, List[Tuple[float, int, str, Optional[str]]]] = defaultdict(list)
    for n, r in enumerate(reader[1:], start=2):
        item_id, error = None, None
        raw_id, raw_bar, raw_name = cell(r, i_id), cell(r, i_bar), cell(r, i_name)
        if raw_id and raw_id in items:
            item_id = raw_id
        elif raw_bar and len(set(by_barcode.get(raw_bar.lower(), []))) == 1:
            item_id = by_barcode[raw_bar.lower()][0]
        elif raw_name and len(by_name.get(" ".join(raw_name.split()).lower(), [])) == 1:
            item_id = by_name[" ".join(raw_name.split()).lower()][0]
        elif raw_bar and len(set(by_barcode.get(raw_bar.lower(), []))) > 1:
            error = f"Barcode {raw_bar} belongs to more than one item: use the Item ID."
        elif raw_name and len(by_name.get(" ".join(raw_name.split()).lower(), [])) > 1:
            error = f"More than one item is called {raw_name}: use the Item ID."
        else:
            error = "No item with this Item ID, barcode or name."
        vendor, code, note = None, None, None
        if not cell(r, i_vendor):
            # An item without a vendor (as the export writes it): nothing to do.
            rows_out.append({"line": n, "item_id": item_id, "item_name": items[item_id].name if item_id in items else (raw_name or None),
                             "vendor": None, "item_code": None, "error": None, "note": "No vendor on this row: skipped."})
            continue
        try:
            vendor = _clean_name(cell(r, i_vendor))
            code = _clean_code(cell(r, i_code))
        except VendorEditError as exc:
            error = error or str(exc)
        rank_raw = cell(r, i_rank)
        rank = float(n)
        if rank_raw:
            try:
                rank = float(rank_raw)
            except ValueError:
                error = error or "Rank must be a number (1 = first choice)."
        if item_id and vendor and not error:
            if any(v[2].lower() == vendor.lower() for v in grouped[item_id]):
                error = f"{vendor} is listed twice for this item."
        if item_id and vendor and not error:
            grouped[item_id].append((rank, n, vendor, code))
        rows_out.append({
            "line": n,
            "item_id": item_id,
            "item_name": items[item_id].name if item_id in items else (raw_name or None),
            "vendor": vendor or cell(r, i_vendor) or None,
            "item_code": code,
            "error": error,
            "note": note,
        })
    lists: Dict[str, List[RankedVendor]] = {}
    for item_id, entries in grouped.items():
        entries.sort(key=lambda e: (e[0], e[1]))
        if len(entries) > MAX_VENDORS_PER_ITEM:
            for row in rows_out:
                if row["item_id"] == item_id and not row["error"]:
                    row["error"] = f"More than {MAX_VENDORS_PER_ITEM} vendors for this item."
            continue
        lists[item_id] = [RankedVendor(i + 1, e[2], e[3]) for i, e in enumerate(entries)]
    return {"rows": rows_out, "items": lists, "errors": sum(1 for r in rows_out if r["error"])}


def import_vendors(db: Session, filename: str, content: bytes, apply: bool = False) -> dict:
    """Preview (default) or apply an import. Apply writes only the items
    whose rows are all valid; an item with any bad row is left as it is.
    Idempotent: the same file again changes nothing. Does not commit."""
    parsed = parse_import(db, filename, content)
    bad_items = {r["item_id"] for r in parsed["rows"] if r["error"] and r["item_id"]}
    table = load_vendor_table(db, list(parsed["items"]))
    changes = []
    for item_id, wanted in parsed["items"].items():
        if item_id in bad_items:
            continue
        have = table.get(item_id, [])
        if have and all(r.source == SOURCE_MANUAL for r in have) and _same_manual(have, wanted):
            continue
        changes.append((item_id, wanted))
    if apply:
        for item_id, wanted in changes:
            set_item_vendors(db, item_id, [{"name": v.name, "item_code": v.item_code} for v in wanted])
    return {
        "rows": parsed["rows"][:500],
        "row_count": len(parsed["rows"]),
        "errors": parsed["errors"],
        "items_in_file": len(parsed["items"]),
        "items_to_change": len(changes),
        "items_skipped": len(bad_items),
        "applied": bool(apply),
    }


def _safe(value: Any) -> str:
    """A cell a spreadsheet won't run as a formula."""
    v = "" if value is None else str(value)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def export_csv(db: Session, template: bool = False) -> bytes:
    """Every item with its vendors, one row per vendor (an item without a
    vendor has one row with an empty Vendor). ``template``: the headers and
    one example row."""
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(EXPORT_HEADERS)
    if template:
        w.writerow(["(item id from the export)", "Basmati Rice 10 lb", "012345678905", "Main Street Wholesale", "BR-10", "1"])
        return buf.getvalue().encode("utf-8")
    items = catalog_items(db)
    table = load_vendor_table(db, list(items))
    for item_id, row in sorted(items.items(), key=lambda p: ((p[1].name or "").lower(), p[0])):
        barcode = (split_field(row.barcode) or [""])[0]
        vendors = table.get(item_id, [])
        if not vendors:
            w.writerow([_safe(item_id), _safe(row.name), _safe(barcode), "", "", ""])
        for v in vendors:
            w.writerow([_safe(item_id), _safe(row.name), _safe(barcode), _safe(v.vendor_name),
                        _safe(v.vendor_item_code), v.rank])
    return buf.getvalue().encode("utf-8")
