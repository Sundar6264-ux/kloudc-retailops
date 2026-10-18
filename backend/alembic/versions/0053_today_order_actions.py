"""Today-tab order actions (docs/BATCH_OCT14_CONTRACT.md W1).

- placed_orders.source / note / for_delivery_date: "Order already placed"
  is recorded as a placed order with source "outside" (no lines), so the
  Vendor Calendar, Reorder Candidates ("Ordered"), the 9 AM pushes and the
  Team vendor-order task all treat it like Mark placed.
- vendor_order_skips.reason / note / skipped_by_user_id: "Other (with
  note)" is a skip of that order cycle with reason "other"; NULL = Skip
  this week.

Additive only (plain nullable columns); existing rows keep NULL = before.

Revision ID: 0053
Revises: 0052
"""

from alembic import op
import sqlalchemy as sa

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("placed_orders", sa.Column("source", sa.String(), nullable=True))
    op.add_column("placed_orders", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("placed_orders", sa.Column("for_delivery_date", sa.Date(), nullable=True))
    op.add_column("vendor_order_skips", sa.Column("reason", sa.String(), nullable=True))
    op.add_column("vendor_order_skips", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("vendor_order_skips", sa.Column("skipped_by_user_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vendor_order_skips") as batch:
        batch.drop_column("skipped_by_user_id")
        batch.drop_column("note")
        batch.drop_column("reason")
    with op.batch_alter_table("placed_orders") as batch:
        batch.drop_column("for_delivery_date")
        batch.drop_column("note")
        batch.drop_column("source")
