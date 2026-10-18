"""POS-neutral ids on our own tables (multi-store plan 4.4 / 8.1, batch MT4).

Migration 0060 added POS-neutral columns next to the Toast ones:

    orders            source, external_order_id, external_check_id,
                      external_line_id, parent_external_line_id, is_modifier,
                      channel, dining_option_id
    open_order_cache  external_order_id
    sync_cursors      (source, stream) -> cursor
    pos_item_map      (source, external_id) -> item_id

M1 is "expand, backfill, dual-write": every report still reads the old Toast
columns (`toast_selection_guid`, `parent_selection_guid`, ...). This module
keeps the new columns filled:

- `neutral_line_values(line)` - the new columns for one line dict from the
  sales sync, written in the same upsert as the old ones.
- `after_sales_sync(db, business_date)` - run after a successful sync: fills
  any order row still missing its neutral ids (e.g. written by the old code
  between `alembic upgrade` and the restart), records the day's item ids in
  `pos_item_map` (plus the newest inventory snapshot's and the Toast menu
  cache's), and moves the sales cursor forward. It never raises: a
  failure here is logged and must not change the sync's own result.

The SQL is plain text on purpose: an ORM/Core UPDATE would also stamp
`orders.updated_at` (its onupdate), and the backfill must not change any old
column.
"""

import datetime as dt
import logging
from typing import Dict, Iterable, Optional

from sqlalchemy import Date, bindparam, text
from sqlalchemy.orm import Session

from app.db import UTCDateTime
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

TOAST = "toast"
# sync_cursors.stream of the per-business-date sales sync. Cursor: ISO date of
# the newest business date synced successfully.
SALES_STREAM = "orders_business_date"

# Fills the neutral ids of order rows that do not have them yet. Idempotent;
# `external_line_id IS NULL` is answered by the (source, external_line_id)
# unique index, so a sweep with nothing to do costs a lookup.
_HEAL_ORDERS_SQL = """
UPDATE orders SET
    external_order_id = toast_order_guid,
    external_check_id = toast_check_guid,
    external_line_id = toast_selection_guid,
    parent_external_line_id = parent_selection_guid,
    is_modifier = (parent_selection_guid IS NOT NULL),
    dining_option_id = dining_option
WHERE source = 'toast' AND external_line_id IS NULL
"""

_HEAL_OPEN_ORDERS_SQL = """
UPDATE open_order_cache SET external_order_id = toast_order_guid
WHERE external_order_id IS NULL
"""


def neutral_line_values(line: Dict, source: str = TOAST) -> Dict:
    """The POS-neutral columns for one sales-sync line dict (the keys
    `sales_sync._extract_line_items` produces). The neutral sync
    (`pos/sync/sales.py`) builds the same columns from the connector's models
    instead (`neutral_line_rows`); for Toast the two are equal."""
    parent = line.get("parent_selection_guid")
    return {
        "source": source,
        "external_order_id": line.get("toast_order_guid"),
        "external_check_id": line.get("toast_check_guid"),
        "external_line_id": line.get("toast_selection_guid"),
        "parent_external_line_id": parent,
        "is_modifier": parent is not None,
        "dining_option_id": line.get("dining_option"),
    }


# The raw SQL below binds timestamps and dates through the same column types
# the ORM uses, so a row written here looks exactly like one the ORM writes:
# naive-UTC text on SQLite, timestamptz / date on PostgreSQL.
_NOW = bindparam("now", type_=UTCDateTime())
_DAY = bindparam("d", type_=Date())


def heal_orders(db: Session) -> int:
    """Fills neutral ids on rows that miss them. Returns rows changed."""
    changed = db.execute(text(_HEAL_ORDERS_SQL)).rowcount or 0
    changed += db.execute(text(_HEAL_OPEN_ORDERS_SQL)).rowcount or 0
    return changed


def record_items(db: Session, item_ids: Iterable[Optional[str]], source: str = TOAST) -> int:
    """Adds `source` item ids to pos_item_map, each mapped to itself (our
    item ids are the Toast GUIDs). Ids already there are left alone."""
    now = utcnow()
    rows = [{"s": source, "e": i, "now": now} for i in sorted({i for i in item_ids if i})]
    if not rows:
        return 0
    db.execute(
        text(
            "INSERT INTO pos_item_map (source, external_id, item_id, created_at) "
            "VALUES (:s, :e, :e, :now) ON CONFLICT (source, external_id) DO NOTHING"
        ).bindparams(_NOW),
        rows,
    )
    return len(rows)


# Catalog ids we hold: the newest inventory snapshot and the Toast menu cache.
# Swept in one statement after each sales sync, so the inventory upload and
# the menu pull need no change of their own.
_RECORD_CATALOG_ITEMS_SQL = """
INSERT INTO pos_item_map (source, external_id, item_id, created_at)
SELECT :s, ids.item_id, ids.item_id, :now FROM (
    SELECT item_id FROM inventory_snapshots
    WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots)
    UNION
    SELECT item_id FROM toast_menu_items
) AS ids WHERE ids.item_id IS NOT NULL AND ids.item_id != ''
ON CONFLICT (source, external_id) DO NOTHING
"""


def record_catalog_items(db: Session, source: str = TOAST) -> int:
    return (
        db.execute(text(_RECORD_CATALOG_ITEMS_SQL).bindparams(_NOW), {"s": source, "now": utcnow()}).rowcount or 0
    )


def get_cursor(db: Session, source: str, stream: str) -> Optional[str]:
    row = db.execute(
        text("SELECT cursor FROM sync_cursors WHERE source = :s AND stream = :t"),
        {"s": source, "t": stream},
    ).first()
    return row[0] if row else None


def set_cursor(db: Session, source: str, stream: str, cursor: Optional[str]) -> None:
    db.execute(
        text(
            "INSERT INTO sync_cursors (source, stream, cursor, updated_at) VALUES (:s, :t, :c, :now) "
            "ON CONFLICT (source, stream) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at"
        ).bindparams(_NOW),
        {"s": source, "t": stream, "c": cursor, "now": utcnow()},
    )


def advance_sales_cursor(db: Session, business_date: dt.date, source: str = TOAST) -> None:
    """Moves the sales cursor to `business_date` if that is newer (a backfill
    of an older day leaves it where it is)."""
    current = get_cursor(db, source, SALES_STREAM)
    if current is None or business_date.isoformat() > current:
        set_cursor(db, source, SALES_STREAM, business_date.isoformat())


def after_sales_sync(db: Session, business_date: dt.date, source: str = TOAST) -> None:
    """Bookkeeping after a successful sales sync of `business_date` from the
    POS `source` (the connector's `source`; "toast" for a Toast store). Never
    raises (see the module docstring)."""
    try:
        heal_orders(db)
        item_ids = db.execute(
            text(
                "SELECT DISTINCT item_guid FROM orders WHERE business_date = :d AND item_guid IS NOT NULL"
            ).bindparams(_DAY),
            {"d": business_date},
        ).scalars()
        record_items(db, list(item_ids), source)
        record_catalog_items(db, source)
        advance_sales_cursor(db, business_date, source)
        db.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must never fail the sync
        db.rollback()
        logger.exception("POS-neutral id bookkeeping failed after the sales sync of %s", business_date)
