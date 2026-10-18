"""Speed: indexes for the hot reports (docs/BATCH_OCT15_CONTRACT.md S, #6).

inventory_snapshots(item_id, snapshot_date, inventory_quantity): the demand
forecast reads "these items' counts over the last 30-90 days"
(demand_forecast._load_opening_counts) on every Reorder / Supplier
Projection / Vendor Calendar / cart projection request. With only the
single-column item_id index SQLite walked every day of every item (135
days on prod) and filtered the dates afterwards; this composite index (the
quantity included, so the table itself is never read) answers it straight.
Measured on a prod copy (Oct 15): opening counts for all items, 30 days
1,173 -> 333 ms; the Today report 1,778 -> 1,115 ms. ~38 MB, ~1 s to build.

Additive only. IF NOT EXISTS / IF EXISTS: safe to re-run, and harmless if
someone already created it by hand.

Revision ID: 0057
Revises: 0056
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

INDEX = "ix_inventory_snapshots_item_date_qty"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON inventory_snapshots (item_id, snapshot_date, inventory_quantity)"
    )
    # No ANALYZE: the planner already picks it (a covering index on the
    # IN + range query; checked with EXPLAIN QUERY PLAN), and new statistics
    # could change other plans on the live database.


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
