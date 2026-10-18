"""POS-neutral ids (multi-store M1, batch MT4; plan sections 4.4 and 8.1).

Expand + backfill only - every existing column, index and row value stays as
it is, and every report still reads the old Toast columns:

- orders: source ('toast'), external_order_id, external_check_id,
  external_line_id, parent_external_line_id, is_modifier, channel,
  dining_option_id; unique index (source, external_line_id) - the future
  upsert key, answered today by the same Toast selection GUID.
- open_order_cache: external_order_id.
- new table sync_cursors (source, stream) -> cursor.
- new table pos_item_map (source, external_id) -> item_id; for Spice Town every
  Toast item GUID maps to itself.

Backfill: copies of the Toast columns (channel stays NULL: Toast only gives a
dining-option GUID). Batched by id range and idempotent (only rows still
missing external_line_id), so a re-run or a half-done run is safe. Plain SQL,
so orders.updated_at is not touched. Measured on the Oct 15 prod copy (125,330
order rows): see docs/MULTISTORE_M1_CONTRACT.md "As built - MT4".

Downgrade drops only what this adds (SQLite >= 3.35 native DROP COLUMN, so the
orders table is not rebuilt and the original CREATE TABLE text comes back).

Revision ID: 0060
Revises: 0059
"""

import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

BATCH = 20000

ORDER_COLUMNS = [
    # (name, DDL)
    ("source", "VARCHAR NOT NULL DEFAULT 'toast'"),
    ("external_order_id", "VARCHAR"),
    ("external_check_id", "VARCHAR"),
    ("external_line_id", "VARCHAR"),
    ("parent_external_line_id", "VARCHAR"),
    ("is_modifier", "BOOLEAN NOT NULL DEFAULT 0"),
    ("channel", "VARCHAR"),
    ("dining_option_id", "VARCHAR"),
]
LINE_INDEX = "uq_orders_source_external_line_id"


def _columns(conn, table):
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def upgrade() -> None:
    conn = op.get_bind()

    have = _columns(conn, "orders")
    for name, ddl in ORDER_COLUMNS:
        if name not in have:
            op.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
    if "external_order_id" not in _columns(conn, "open_order_cache"):
        op.execute("ALTER TABLE open_order_cache ADD COLUMN external_order_id VARCHAR")

    # Backfill orders in id batches (bounded statement size; idempotent).
    lo, hi = conn.exec_driver_sql("SELECT MIN(id), MAX(id) FROM orders").one()
    if lo is not None:
        start = lo
        while start <= hi:
            conn.exec_driver_sql(
                "UPDATE orders SET "
                "external_order_id = toast_order_guid, "
                "external_check_id = toast_check_guid, "
                "external_line_id = toast_selection_guid, "
                "parent_external_line_id = parent_selection_guid, "
                "is_modifier = (parent_selection_guid IS NOT NULL), "
                "dining_option_id = dining_option "
                "WHERE id >= ? AND id < ? AND external_line_id IS NULL",
                (start, start + BATCH),
            )
            start += BATCH
    op.execute(
        "UPDATE open_order_cache SET external_order_id = toast_order_guid WHERE external_order_id IS NULL"
    )
    # After the backfill: building the index once is cheaper than updating it
    # row by row. Unique holds because toast_selection_guid already is.
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {LINE_INDEX} ON orders (source, external_line_id)")

    op.execute(
        "CREATE TABLE IF NOT EXISTS sync_cursors ("
        "source VARCHAR NOT NULL, "
        "stream VARCHAR NOT NULL, "
        "cursor VARCHAR, "
        "updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (source, stream))"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS pos_item_map ("
        "source VARCHAR NOT NULL, "
        "external_id VARCHAR NOT NULL, "
        "item_id VARCHAR NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "PRIMARY KEY (source, external_id))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_pos_item_map_item_id ON pos_item_map (item_id)")

    # UTCDateTime's storage format (naive UTC text).
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")

    # Every item id we hold, mapped to itself: sold items, every inventory
    # snapshot item, the Toast menu cache.
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO pos_item_map (source, external_id, item_id, created_at) "
        "SELECT 'toast', item_id, item_id, ? FROM ("
        "  SELECT DISTINCT item_guid AS item_id FROM orders WHERE item_guid IS NOT NULL"
        "  UNION SELECT DISTINCT item_id FROM inventory_snapshots"
        "  UNION SELECT item_id FROM toast_menu_items"
        ") WHERE item_id IS NOT NULL AND item_id != ''",
        (now,),
    )

    # Sales cursor: the newest business date the Toast sales sync finished.
    newest = conn.exec_driver_sql(
        "SELECT MAX(business_date) FROM job_runs WHERE job_name = 'toast_sales_sync' AND status = 'success'"
    ).scalar()
    if newest is not None:
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO sync_cursors (source, stream, cursor, updated_at) "
            "VALUES ('toast', 'orders_business_date', ?, ?)",
            (str(newest)[:10], now),
        )


def downgrade() -> None:
    conn = op.get_bind()
    op.execute("DROP TABLE IF EXISTS pos_item_map")
    op.execute("DROP TABLE IF EXISTS sync_cursors")
    op.execute(f"DROP INDEX IF EXISTS {LINE_INDEX}")
    if "external_order_id" in _columns(conn, "open_order_cache"):
        op.execute("ALTER TABLE open_order_cache DROP COLUMN external_order_id")
    have = _columns(conn, "orders")
    for name, _ in reversed(ORDER_COLUMNS):
        if name in have:
            op.execute(f"ALTER TABLE orders DROP COLUMN {name}")
