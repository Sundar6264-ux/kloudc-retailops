"""add open_order_cache table (background-refreshed all-time open orders)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "open_order_cache",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("toast_order_guid", sa.String, nullable=False),
        sa.Column("business_date", sa.Date, nullable=False),
        sa.Column("display_number", sa.String, nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("server_guid", sa.String, nullable=True),
        sa.Column("server_name", sa.String, nullable=True),
        sa.Column("num_guests", sa.Integer, nullable=True),
        sa.Column("approval_status", sa.String, nullable=True),
        sa.Column("total_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("num_checks", sa.Integer, nullable=False, server_default="0"),
        # JSON-encoded [{name, quantity}, ...] of top-level (non-modifier,
        # non-voided) line items - built from the same _extract_line_items
        # flattening sales_sync.py already uses, so modifier/voided handling
        # can't drift between the two.
        sa.Column("line_items_json", sa.Text, nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_open_order_cache_business_date", "open_order_cache", ["business_date"])
    op.create_index("ix_open_order_cache_toast_order_guid", "open_order_cache", ["toast_order_guid"])


def downgrade() -> None:
    op.drop_index("ix_open_order_cache_toast_order_guid", table_name="open_order_cache")
    op.drop_index("ix_open_order_cache_business_date", table_name="open_order_cache")
    op.drop_table("open_order_cache")
