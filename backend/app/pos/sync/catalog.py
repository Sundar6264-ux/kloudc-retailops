"""Catalog + stock sync: inventory rows built from the POS (POS-neutral).

Moved from ``services/inventory_api.py`` in multi-store M1 batch MT3 (plan
4.6: ``build_api_rows``, ``compare_rows``, ``overlay_live_stock`` -> here).
The connector hands over ``Item`` / ``StockLevel`` models; this module builds
the same `inventory_snapshots` field dicts, `toast_menu_items` cache rows,
comparison and live-stock views as before M1. The old names still work:
``inventory_api.build_api_rows(db, stock_json, menus_json, ...)`` and the
other functions that take Toast JSON (or a Toast fetch function) map it
through the Toast adapter and call in here; the rest forward here.

The `inventory_snapshots` inputs from our own DB (latest CSV rows, sales by
item, ...) stay in ``services/inventory_api.py`` and are looked up there at
call time (``_inv().name``), so a test patching ``inventory_api.has_csv_for``
or ``inventory_api._after_new_snapshot`` still reaches this code.

Rules (each checked against the real Oct 11 2026 CSV - see
docs/AUTOMATION_BATCH_CONTRACT.md "As built - B2"):
- quantity: a count -> the count (3 decimals); out of stock (Toast: 0 AND
  negative, no count) -> 0 with `api_out_of_stock` set and `csv_quantity` =
  the item's count in the latest CSV, so Negative inventory keeps the CSV's
  negative count (`effective_quantity`); not tracked -> NULL ("in stock").
- name: the POS name, but the CSV's own spelling while it still matches one
  of the item's names (``Item.other_names``).
- price: the POS's fixed price (None for open / size / variable / $0).
- barcode: ``merged_barcode`` (the POS's one code vs the CSV's ";"-list).
- placement: the CSV's own group / category / subcategory while it is still
  one of the item's catalog locations, else the item's default placement.
- cost, supplier, supplier_item_id, last_received_from,
  inventory_last_received: carried forward from the item's latest CSV row.
- last 7/30/90 day orders / sales: from our `orders` table.
- A CSV for a date always wins: a date with any CSV row is never written.
"""

import datetime as dt
import logging
import threading
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import dialect_insert
from app.models import InventorySnapshot, JobRun, ToastMenuItem
from app.pos import registry
from app.pos.barcodes import _codes, merged_barcode
from app.pos.models import CatalogSnapshot, Item, StockLevel, StockStatus, to_float
from app.timeutil import restaurant_today

# Same logger name as before the move (log lines and filters unchanged).
logger = logging.getLogger("app.services.inventory_api")


def _inv():
    """services/inventory_api, imported at call time (it imports this module)."""
    from app.services import inventory_api

    return inventory_api


def _norm(v) -> str:
    return (v or "").strip().lower()


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _r(v: Optional[float], digits: int = 2) -> Optional[float]:
    return None if v is None else round(v, digits)


# ---------------------------------------------------------------------------
# Building the rows
# ---------------------------------------------------------------------------


def placement(item: Item, carried) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """The CSV row's own placement while it is still one of the item's catalog
    locations (compared trimmed, case-insensitive), else the item's default."""
    if carried is not None:
        mine = (_norm(carried.category_group), _norm(carried.category), _norm(carried.subcategory))
        for loc in item.locations:
            if (_norm(loc.group), _norm(loc.category), _norm(loc.subcategory)) == mine:
                return carried.category_group, carried.category, carried.subcategory
    return item.category.as_tuple() if item.category else (None, None, None)


def status_text(status: Optional[StockStatus], qty: Optional[float]) -> str:
    """The CSV's own wording: "932 in stock", "1,210 in stock",
    "0.485 in stock", "out of stock", or "in stock" (count not tracked)."""
    if status is StockStatus.OUT:
        return "out of stock"
    if qty is None:
        return "in stock"
    if float(qty).is_integer():
        text = f"{int(qty):,}"
    else:
        text = f"{qty:,.3f}".rstrip("0").rstrip(".")
    return f"{text} in stock"


def snapshot_quantity(level: Optional[StockLevel]) -> Optional[float]:
    """The on-hand figure a snapshot row stores for a stock level."""
    if level is not None and level.status is StockStatus.COUNT and level.quantity is not None:
        return round(float(level.quantity), 3)  # Toast sends float noise (85.39999999999989)
    if level is not None and level.status is StockStatus.OUT:
        return 0.0
    return None


def build_inventory_rows(
    db: Session,
    items: Sequence[Item],
    stock: Sequence[StockLevel],
    catalog_changed_at: Optional[dt.datetime] = None,
    snapshot_date: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    sales_end_offset_days: int = 1,
) -> Tuple[Dict[str, dict], dict]:
    """item_id -> InventorySnapshot field dict (not written), plus build
    stats. Pure apart from reading our DB."""
    inv = _inv()
    sales_windows = inv.SALES_WINDOWS
    snapshot_date = snapshot_date or restaurant_today()
    now = now or dt.datetime.now(dt.timezone.utc)
    source = inv.API_SOURCE_PREFIX + now.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    stock_by_id = {s.item_external_id: s for s in stock if s.item_external_id}
    carried = inv.latest_csv_row_per_item(db)
    latest_day = inv.latest_csv_date(db)
    groups = {
        _norm(g)
        for (g,) in db.execute(
            select(InventorySnapshot.category_group).where(InventorySnapshot.snapshot_date == latest_day, inv._not_api()).distinct()
        )
    } if latest_day else set()
    sales = inv.sales_by_item(db, snapshot_date, sales_end_offset_days)

    rows: Dict[str, dict] = {}
    menu_ids = set()
    stats = defaultdict(int)
    for item in items:
        guid = item.external_id
        menu_ids.add(guid)
        prev = carried.get(guid)
        group, category, sub = placement(item, prev)
        if prev is None and _norm(group) not in groups:
            stats["skipped_not_inventory"] += 1
            continue
        level = stock_by_id.get(guid)
        out_of_stock = level is not None and level.status is StockStatus.OUT
        qty = snapshot_quantity(level)
        price = to_float(item.price)
        cost = prev.cost if prev is not None else None
        sold = sales.get(guid) or {"units": {n: 0.0 for n in sales_windows}, "dollars": {n: 0.0 for n in sales_windows}}
        units90 = sold["units"][90]
        if qty is None:
            doh = None
        elif qty <= 0:
            doh = 0.0
        elif units90 > 0:
            doh = round(qty / (units90 / 90.0), 1)
        else:
            doh = None
        name = item.name
        if prev is not None and prev.name and _norm(prev.name) in tuple(_norm(n) for n in item.other_names):
            name = prev.name  # the CSV's spelling, while the POS still agrees with it
        row = {
            "snapshot_date": snapshot_date,
            "item_id": guid,
            "name": name,
            "category_group": group,
            "category": category,
            "subcategory": sub,
            "inventory_status": status_text(level.status if level is not None else None, qty),
            "inventory_quantity": qty,
            "inventory_cost": _r(qty * cost) if qty is not None and cost is not None else None,
            "inventory_value": _r(qty * price) if qty is not None and price is not None else None,
            "inventory_days_on_hand": doh,
            "price": price,
            "gross_margin": _r((price - cost) / price * 100) if price and cost is not None else None,
            "gross_profit": _r(price - cost) if price is not None and cost is not None else None,
            "barcode": merged_barcode(item.barcode, prev.barcode if prev is not None else None),
            "source_filename": source,
            "uploaded_at": now,
            "api_out_of_stock": True if out_of_stock else None,
            "csv_quantity": (prev.inventory_quantity if prev is not None else None) if out_of_stock else None,
        }
        for n in sales_windows:
            row[f"last_{n}_day_orders"] = sold["units"][n]
            row[f"last_{n}_day_sales"] = sold["dollars"][n]
        for f in inv.CARRIED_FIELDS:
            row[f] = getattr(prev, f) if prev is not None else None
        rows[guid] = row
        stats["rows"] += 1
        stats["out_of_stock" if out_of_stock else ("counted" if qty is not None else "not_tracked")] += 1
        if prev is None:
            stats["new_items_no_csv"] += 1
    stats["stock_rows"] = len(stock_by_id)
    stats["stock_not_in_menu"] = sum(1 for g in stock_by_id if g not in menu_ids)
    meta = {
        "source_filename": source,
        "snapshot_date": snapshot_date.isoformat(),
        "built_at": now.isoformat(),
        "carried_from_csv_date": latest_day.isoformat() if latest_day else None,
        "sales_through": (snapshot_date - dt.timedelta(days=sales_end_offset_days)).isoformat(),
        "sales_synced_through": _iso(inv.sales_synced_through(db)),
        "menu_last_updated": _iso(catalog_changed_at),
        "stats": dict(stats),
    }
    return rows, meta


# An API build smaller than this share of the latest CSV (rows, or items
# with a count) isn't written - see implausible_build.
MIN_BUILD_SHARE = 0.5


def implausible_build(db: Session, stock: list, meta: dict) -> Optional[str]:
    """Why an API build looks cut short (and must not be stored), or None.
    Compared with the latest CSV upload: the store's catalog and its counted
    items don't halve overnight, but a POS answer that came back empty or
    partial would make every item "not tracked" or drop it."""
    inv = _inv()
    stats = meta.get("stats") or {}
    if not stock:
        return "Toast's stock list came back empty"
    latest_day = inv.latest_csv_date(db)
    if latest_day is None:
        return None
    csv_rows, csv_counted = db.execute(
        select(func.count(InventorySnapshot.id), func.count(InventorySnapshot.inventory_quantity)).where(
            InventorySnapshot.snapshot_date == latest_day, inv._not_api()
        )
    ).one()
    rows = int(stats.get("rows") or 0)
    counted = int(stats.get("counted") or 0) + int(stats.get("out_of_stock") or 0)
    if csv_rows and rows < MIN_BUILD_SHARE * csv_rows:
        return f"only {rows} items built vs {csv_rows} in the {latest_day} CSV"
    if csv_counted and counted < MIN_BUILD_SHARE * csv_counted:
        return f"only {counted} items with a count vs {csv_counted} in the {latest_day} CSV"
    return None


def store_api_snapshot(db: Session, rows: Dict[str, dict], snapshot_date: dt.date) -> dict:
    """Replace this date's API rows with `rows`. Never touches a CSV row: a
    date with any CSV row is skipped entirely (CSV wins), and a CSV row that
    lands meanwhile is kept (insert-or-ignore)."""
    inv = _inv()
    if inv.has_csv_for(db, snapshot_date):
        return {"written": 0, "skipped": "csv_exists"}
    db.execute(
        delete(InventorySnapshot).where(
            InventorySnapshot.snapshot_date == snapshot_date,
            InventorySnapshot.source_filename.like(inv.API_SOURCE_PREFIX + "%"),
        )
    )
    # Asked again now that this transaction holds SQLite's write lock: a CSV
    # upload that committed between the first check and here would otherwise
    # get API rows written next to it for the items it doesn't list.
    if inv.has_csv_for(db, snapshot_date):
        db.rollback()
        return {"written": 0, "skipped": "csv_exists"}
    written = 0
    batch = []
    for row in rows.values():
        batch.append(row)
        if len(batch) >= 500:
            written += _insert(db, batch)
            batch = []
    if batch:
        written += _insert(db, batch)
    db.commit()
    return {"written": written, "skipped": None}


def _insert(db: Session, batch: List[dict]) -> int:
    stmt = dialect_insert(InventorySnapshot, db).values(batch).on_conflict_do_nothing(
        index_elements=[InventorySnapshot.snapshot_date, InventorySnapshot.item_id]
    )
    # preserve_rowcount: SQLAlchemy only keeps an INSERT's rowcount on request
    # (PostgreSQL would answer -1 without it).
    return db.execute(stmt, execution_options={"preserve_rowcount": True}).rowcount or 0


# ---------------------------------------------------------------------------
# Side-by-side comparison (API vs CSV)
# ---------------------------------------------------------------------------

COMPARE_FIELDS = [
    # (field, label, kind)
    ("inventory_quantity", "On hand", "qty"),
    ("name", "Name", "text"),
    ("price", "Price", "num"),
    ("barcode", "Barcode", "codes"),
    ("category_group", "Category group", "text"),
    ("category", "Category", "text"),
    ("subcategory", "Subcategory", "text"),
    ("last_7_day_orders", "Units sold, 7 days", "sales"),
    ("last_30_day_orders", "Units sold, 30 days", "sales"),
    ("last_90_day_orders", "Units sold, 90 days", "sales"),
    ("last_7_day_sales", "Sales $, 7 days", "sales"),
    ("last_30_day_sales", "Sales $, 30 days", "sales"),
    ("last_90_day_sales", "Sales $, 90 days", "sales"),
    ("inventory_days_on_hand", "Days on hand", "sales"),
]
EXAMPLES_PER_FIELD = 10


def _same(kind: str, api, csv, api_row: dict) -> bool:
    if kind == "qty":
        if api_row.get("api_out_of_stock"):
            return csv is not None and csv <= 0.0001
        if api is None or csv is None:
            return api is None and csv is None
        return abs(api - csv) <= 0.011
    if kind in ("num", "sales"):
        if api is None or csv is None:
            if kind == "sales":  # no sales on record = 0
                return (api or 0) == 0 and (csv or 0) == 0
            return api is None and csv is None
        return abs(float(api) - float(csv)) <= 0.011
    if kind == "codes":
        return _codes(api) == _codes(csv)
    return _norm(api) == _norm(csv)


def _near(api, csv, pct: float = 0.05) -> bool:
    if api is None or csv is None:
        return (api in (None, 0)) and (csv in (None, 0))
    return abs(float(api) - float(csv)) <= max(0.011, pct * abs(float(csv)))


def _tracked(api_row: dict, csv_qty) -> bool:
    """Count-tracked on either side (an item neither side counts - a kitchen
    dish - isn't part of the on-hand match)."""
    return api_row.get("inventory_quantity") is not None or bool(api_row.get("api_out_of_stock")) or csv_qty is not None


def _explained_by_sales(api_row: dict, csv_qty, sold: float) -> bool:
    """The CSV count minus what sold after the file was made gives the POS's
    count (or the POS's "out of stock" when that is 0 or below)."""
    if not sold or csv_qty is None:
        return False
    if api_row.get("api_out_of_stock"):
        return csv_qty - sold <= 0.011
    api = api_row.get("inventory_quantity")
    return api is not None and abs((csv_qty - sold) - api) <= 0.011


PLACEMENT_FIELDS = ("category_group", "category", "subcategory")


def compare_rows(
    api_rows: Dict[str, dict],
    csv_rows: Dict[str, InventorySnapshot],
    csv_date: Optional[dt.date] = None,
    sold_since: Optional[Dict[str, float]] = None,
    sales_window: Optional[Tuple[dt.datetime, dt.datetime]] = None,
) -> dict:
    """Per field: how many items agree between the API-built set and a CSV
    upload, with examples of the ones that don't.

    On hand also gets `tracked` (counted on either side), `explained_by_sales`
    (the difference is exactly the units sold between the CSV and the stock
    pull - `sold_since`, item_id -> units, over `sales_window`) and
    `unexplained` with its own examples; `placement` is group + category +
    subcategory all equal. All additive (readiness reads them)."""
    both = sorted(set(api_rows) & set(csv_rows))
    only_api = sorted(set(api_rows) - set(csv_rows))
    only_csv = sorted(set(csv_rows) - set(api_rows))
    sold_since = sold_since or {}
    fields = []
    for field, label, kind in COMPARE_FIELDS:
        same = near = 0
        examples = []
        compared = 0
        tracked = explained = 0
        unexplained_examples = []
        for item_id in both:
            a = api_rows[item_id].get(field)
            c = getattr(csv_rows[item_id], field)
            compared += 1
            is_tracked = kind == "qty" and _tracked(api_rows[item_id], c)
            if is_tracked:
                tracked += 1
            if _same(kind, a, c, api_rows[item_id]):
                same += 1
                near += 1
                continue
            if kind == "sales" and _near(a, c):
                near += 1
            if is_tracked:
                sold = float(sold_since.get(item_id) or 0)
                if _explained_by_sales(api_rows[item_id], c, sold):
                    explained += 1
                elif len(unexplained_examples) < EXAMPLES_PER_FIELD:
                    unexplained_examples.append(
                        {
                            "item_id": item_id,
                            "name": api_rows[item_id].get("name") or csv_rows[item_id].name,
                            "api": a,
                            "csv": c,
                            "sold_since_csv": sold,
                            "api_out_of_stock": bool(api_rows[item_id].get("api_out_of_stock")),
                        }
                    )
            if len(examples) < EXAMPLES_PER_FIELD:
                examples.append(
                    {
                        "item_id": item_id,
                        "name": api_rows[item_id].get("name") or csv_rows[item_id].name,
                        "api": a,
                        "csv": c,
                        "api_out_of_stock": bool(api_rows[item_id].get("api_out_of_stock")),
                    }
                )
        entry = {
            "field": field,
            "label": label,
            "compared": compared,
            "same": same,
            "differ": compared - same,
            "same_pct": round(100.0 * same / compared, 2) if compared else None,
            "examples": examples,
        }
        if kind == "sales":
            entry["within_5pct"] = near
            entry["within_5pct_pct"] = round(100.0 * near / compared, 2) if compared else None
        if kind == "qty":
            # Untracked on both sides count as "same" above; the on-hand
            # match is over tracked items only.
            tracked_same = same - (compared - tracked)
            entry["tracked"] = tracked
            entry["tracked_same"] = tracked_same
            entry["explained_by_sales"] = explained
            entry["unexplained"] = tracked - tracked_same - explained
            entry["matched_or_explained_pct"] = (
                round(100.0 * (tracked_same + explained) / tracked, 2) if tracked else None
            )
            entry["unexplained_examples"] = unexplained_examples
        fields.append(entry)

    placement_same = 0
    placement_examples = []
    for item_id in both:
        api_row, csv_row = api_rows[item_id], csv_rows[item_id]
        if all(_same("text", api_row.get(f), getattr(csv_row, f), api_row) for f in PLACEMENT_FIELDS):
            placement_same += 1
        elif len(placement_examples) < EXAMPLES_PER_FIELD:
            placement_examples.append(
                {
                    "item_id": item_id,
                    "name": api_row.get("name") or csv_row.name,
                    "api": " / ".join(str(api_row.get(f) or "") for f in PLACEMENT_FIELDS),
                    "csv": " / ".join(str(getattr(csv_row, f) or "") for f in PLACEMENT_FIELDS),
                }
            )

    oos_negative = sum(
        1
        for i in both
        if api_rows[i].get("api_out_of_stock") and (csv_rows[i].inventory_quantity or 0) < 0
    )

    def _named(ids, source):
        return [{"item_id": i, "name": (source[i]["name"] if isinstance(source[i], dict) else source[i].name)} for i in ids[:EXAMPLES_PER_FIELD]]

    return {
        "csv_date": csv_date.isoformat() if csv_date else None,
        "items_in_both": len(both),
        "only_in_api": len(only_api),
        "only_in_csv": len(only_csv),
        "only_in_api_examples": _named(only_api, api_rows),
        "only_in_csv_examples": _named(only_csv, csv_rows),
        "out_of_stock_with_negative_csv": oos_negative,
        "carried_from_csv": list(_inv().CARRIED_FIELDS),
        "fields": fields,
        "placement": {
            "compared": len(both),
            "same": placement_same,
            "differ": len(both) - placement_same,
            "same_pct": round(100.0 * placement_same / len(both), 2) if both else None,
            "examples": placement_examples,
        },
        "sales_window": (
            {"from": sales_window[0].isoformat(), "to": sales_window[1].isoformat()} if sales_window else None
        ),
    }


# ---------------------------------------------------------------------------
# Jobs: the nightly snapshot and the hourly catalog (barcode) re-check
# ---------------------------------------------------------------------------

StockFetch = Callable[[], List[StockLevel]]
CatalogFetch = Callable[[], CatalogSnapshot]


def _default_stock() -> List[StockLevel]:
    return registry.get_connector().fetch_stock()


def _default_catalog() -> CatalogSnapshot:
    return registry.get_connector().fetch_catalog_snapshot()


def _default_changed_at() -> Optional[dt.datetime]:
    return registry.get_connector().catalog_changed_at()


def save_catalog_cache(
    db: Session, items: Sequence[Item], last_updated: Optional[dt.datetime], now: Optional[dt.datetime] = None
) -> int:
    """Replace `toast_menu_items` (the latest catalog pull: one row per item,
    its barcode for the hourly re-check) with `items`."""
    now = now or dt.datetime.now(dt.timezone.utc)
    db.execute(delete(ToastMenuItem))
    rows = [
        {
            "item_id": item.external_id,
            "name": item.name,
            "sku": item.barcode,
            "plu": item.plu,
            "price": to_float(item.price),
            "pricing_strategy": item.price_rule,
            "menu_last_updated": last_updated,
            "fetched_at": now,
        }
        for item in items
    ]
    for i in range(0, len(rows), 500):
        db.execute(dialect_insert(ToastMenuItem, db).values(rows[i : i + 500]))
    db.commit()
    _inv()._menu_cache_state.clear()
    return len(rows)


def run_snapshot(
    db: Session,
    write: Optional[bool] = None,
    snapshot_date: Optional[dt.date] = None,
    fetch_stock: Optional[StockFetch] = None,
    fetch_catalog: Optional[CatalogFetch] = None,
    now: Optional[dt.datetime] = None,
    rerun: Optional[dict] = None,
) -> JobRun:
    """The 04:05 job. Builds the API set; writes it only when `write` (default:
    INVENTORY_API_SNAPSHOTS) and no CSV exists for the date; records a
    comparison with the same day's CSV when there is one. Returns the
    job_runs row (detail = JSON summary). `rerun` marks a later run of the
    same date (`rerun_for_late_csv`) - kept in the detail, also on failure.
    `fetch_stock` / `fetch_catalog` default to the store's connector."""
    inv = _inv()
    write_arg = write
    snapshot_date = snapshot_date or restaurant_today()
    job = inv._job(db, inv.JOB_SNAPSHOT, snapshot_date)
    marker = {"rerun": rerun} if rerun else {}
    try:
        stock = (fetch_stock or _default_stock)()
        pulled_at = now or dt.datetime.now(dt.timezone.utc)
        catalog = (fetch_catalog or _default_catalog)()
        rows, meta = build_inventory_rows(db, catalog.items, stock, catalog.changed_at, snapshot_date, now)
        save_catalog_cache(db, catalog.items, catalog.changed_at, now)
        # The switch (the page's setting, else .env) is read now, after
        # the POS answered - the two GETs can take minutes, and an admin who
        # flips it meanwhile gets what the page said, for this run too. Its
        # own session: never rolls back this one's work.
        write = inv.inventory_api_switch.is_on() if write_arg is None else write_arg
        detail = {"meta": meta, "write_enabled": bool(write), **marker}
        csv_today = inv.csv_rows_for(db, snapshot_date)
        if csv_today:
            # On-hand differences the sales since the file explain (the
            # readiness score counts them as matched).
            made_at = inv.csv_made_at(db, snapshot_date)
            window = (made_at, pulled_at) if made_at is not None and made_at < pulled_at else None
            sold = inv.units_sold_between(db, *window) if window else {}
            detail["comparison"] = compare_rows(rows, csv_today, snapshot_date, sold_since=sold, sales_window=window)
            detail["stored"] = {"written": 0, "skipped": "csv_exists"}
        elif write:
            problem = implausible_build(db, stock, meta)
            if problem:
                # Never let a POS hiccup (an empty or cut-short stock list,
                # a near-empty menu) become the newest snapshot: every
                # on-hand figure would read "not tracked" or vanish until the
                # next good one. Yesterday's snapshot stays the latest.
                detail["stored"] = {"written": 0, "skipped": "implausible", "reason": problem}
                return inv._finish(db, job, "failed", detail)
            detail["stored"] = store_api_snapshot(db, rows, snapshot_date)
            if detail["stored"].get("written"):
                inv._after_new_snapshot()
        else:
            detail["stored"] = {"written": 0, "skipped": "switch_off"}
        return inv._finish(db, job, "success", detail)
    except Exception as exc:  # noqa: BLE001 - recorded on the job row
        db.rollback()
        logger.exception("Inventory API snapshot failed")
        return inv._finish(db, job, "failed", {"error": str(exc)[:500], **marker})


def refresh_catalog_cache(
    db: Session,
    force: bool = False,
    fetch_changed_at: Optional[Callable[[], Optional[dt.datetime]]] = None,
    fetch_catalog: Optional[CatalogFetch] = None,
    now: Optional[dt.datetime] = None,
) -> JobRun:
    """The hourly barcode re-check: one cheap "when did the catalog change"
    call; the full catalog only when that moved (or nothing is cached yet)."""
    inv = _inv()
    job = inv._job(db, inv.JOB_MENU_RECHECK)
    try:
        remote = (fetch_changed_at or _default_changed_at)()
        stored = inv.stored_menu_last_updated(db)
        if not force and stored is not None and remote is not None and remote <= stored:
            return inv._finish(db, job, "success", {"changed": False, "menu_last_updated": stored.isoformat()})
        catalog = (fetch_catalog or _default_catalog)()
        last_updated = catalog.changed_at or remote
        count = save_catalog_cache(db, catalog.items, last_updated, now)
        return inv._finish(
            db,
            job,
            "success",
            {"changed": True, "items": count, "menu_last_updated": last_updated.isoformat() if last_updated else None},
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("Menu barcode re-check failed")
        return inv._finish(db, job, "failed", {"error": str(exc)[:500]})


# ---------------------------------------------------------------------------
# Negative counts on API rows
# ---------------------------------------------------------------------------


def effective_quantity(snap) -> Optional[float]:
    """The on-hand figure Negative inventory should judge: an API row the POS
    reported out of stock (stored as 0) keeps the latest CSV's NEGATIVE count
    when there was one; everything else is its own inventory_quantity."""
    qty = getattr(snap, "inventory_quantity", None)
    # qty == 0: still the stored "out of stock" figure (a live count laid on
    # top - LiveStockSnapshot - wins when the POS now has a number).
    if getattr(snap, "api_out_of_stock", None) and qty is not None and qty == 0:
        carried = getattr(snap, "csv_quantity", None)
        if carried is not None and carried < 0:
            return carried
    return qty


# ---------------------------------------------------------------------------
# Live stock overlay
# ---------------------------------------------------------------------------

# _live_lock only guards the `_live` dict (never held while the POS is asked);
# _live_fetch_lock makes it one POS fetch at a time.
_live_lock = threading.Lock()
_live_fetch_lock = threading.Lock()
_live: dict = {}
LIVE_FAILURE_BACKOFF_SECONDS = 60
# A request waits this long for another request's fetch, then uses the
# snapshot counts.
LIVE_FETCH_WAIT_SECONDS = 5.0


def live_stock_enabled() -> bool:
    """The "Use Toast for inventory" switch (page setting, else
    INVENTORY_API_SNAPSHOTS) - read on every call, so it applies at once."""
    return _inv().inventory_api_switch.is_on()


def _cached_live_stock(ttl: int) -> Tuple[bool, Optional[dict]]:
    """(answered, data): answered = no fetch is wanted right now (a fresh
    cache, or a recent failure cooling down - then a not-too-old copy or
    None)."""
    with _live_lock:
        now = time.monotonic()
        if _live.get("data") is not None and now - _live["at"] < ttl:
            return True, _live["data"]
        if _live.get("failed_at") and now - _live["failed_at"] < LIVE_FAILURE_BACKOFF_SECONDS:
            return True, (_live.get("data") if _live.get("data") and now - _live["at"] < ttl * 3 else None)
    return False, None


def _default_live_stock() -> List[StockLevel]:
    return registry.get_connector().fetch_stock(live=True)


def get_live_stock(fetch_levels: Optional[StockFetch] = None) -> Optional[dict]:
    """{"by_guid": {item_id: (StockStatus, quantity)}, "fetched_at": aware UTC}
    or None (switched off, or the POS failed - then not retried for a
    minute). Cached LIVE_STOCK_CACHE_SECONDS; one fetch at a time, and a page
    never waits long for the POS (short timeout, no 429 sleeps, a short wait
    for another request's fetch) - the snapshot counts are used instead."""
    if not live_stock_enabled():
        return None
    ttl = max(int(get_settings().live_stock_cache_seconds or 0), 1)
    answered, data = _cached_live_stock(ttl)
    if answered:
        return data
    if not _live_fetch_lock.acquire(timeout=LIVE_FETCH_WAIT_SECONDS):
        return None  # another request is still asking the POS: snapshot counts this time
    try:
        answered, data = _cached_live_stock(ttl)
        if answered:
            return data
        try:
            levels = (fetch_levels or _default_live_stock)()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live stock fetch failed (%s); using the latest snapshot", exc)
            with _live_lock:
                _live["failed_at"] = time.monotonic()
            return None
        data = {
            "by_guid": {lvl.item_external_id: (lvl.status, lvl.quantity) for lvl in levels if lvl.item_external_id},
            "fetched_at": dt.datetime.now(dt.timezone.utc),
        }
        with _live_lock:
            _live.clear()
            _live.update({"data": data, "at": time.monotonic()})
        return data
    finally:
        _live_fetch_lock.release()


def clear_live_stock_cache() -> None:
    with _live_lock:
        _live.clear()


class LiveStockSnapshot:
    """Read-only view of a snapshot with the live POS count. Reads through
    to the wrapped snapshot (which may itself be a category-override view)."""

    __slots__ = ("_snap", "inventory_quantity", "inventory_status", "live_stock_at", "snapshot_quantity")

    def __init__(self, snap, qty, status_text, at):
        set_ = object.__setattr__
        set_(self, "_snap", snap)
        set_(self, "inventory_quantity", qty)
        set_(self, "inventory_status", status_text)
        set_(self, "live_stock_at", at)
        set_(self, "snapshot_quantity", getattr(snap, "inventory_quantity", None))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "_snap"), name)

    def __setattr__(self, name, value):
        raise AttributeError("LiveStockSnapshot is read-only")


def live_level_quantity(snap, status: Optional[StockStatus], qty) -> Optional[float]:
    """The on-hand figure for a snapshot given the POS's live stock level."""
    if status is StockStatus.COUNT and qty is not None:
        return round(float(qty), 3)
    if status is StockStatus.OUT:
        current = effective_quantity(snap)
        # "0 or below": keep a known negative count, else 0.
        return current if current is not None and current < 0 else 0.0
    return getattr(snap, "inventory_quantity", None)


def overlay_live_stock(latest: dict, fetch_levels: Optional[StockFetch] = None) -> dict:
    """latest (item_id -> snapshot) with live counts when the switch is on and
    the POS answered; otherwise `latest` itself, unchanged. An item missing
    from the POS's stock list (count not tracked) keeps its snapshot figure."""
    if not latest:
        return latest
    live = get_live_stock(fetch_levels)
    if live is None:
        return latest
    by_guid = live["by_guid"]
    at = live["fetched_at"]
    out = dict(latest)
    for item_id, snap in latest.items():
        entry = by_guid.get(item_id)
        if entry is None:
            continue
        status, qty = entry
        new_qty = live_level_quantity(snap, status, qty)
        out[item_id] = LiveStockSnapshot(
            snap, new_qty, status_text(status, new_qty if status is StockStatus.COUNT else None), at
        )
    return out


def live_stock_meta() -> Optional[dict]:
    """Additive response field for the pages that use the overlay: when the
    counts came from the POS (None = snapshot counts)."""
    if not live_stock_enabled():
        return None
    with _live_lock:
        data = _live.get("data")
    if not data:
        return {"live": False, "as_of": None}
    return {"live": True, "as_of": data["fetched_at"].isoformat()}

