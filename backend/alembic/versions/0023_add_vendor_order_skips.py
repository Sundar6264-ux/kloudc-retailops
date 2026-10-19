"""add vendor_order_skips table (skip one order cycle on the Vendor Order
Calendar - see models.VendorOrderSkip)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unique constraint inline on create_table - SQLite can't ALTER one in
    # afterwards (see the origin store's operating notes' alembic/SQLite gotcha).
    op.create_table(
        "vendor_order_skips",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("skipped_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vendor", "delivery_date", name="uq_vendor_order_skip"),
    )
    op.create_index("ix_vendor_order_skips_vendor", "vendor_order_skips", ["vendor"])
    op.create_index("ix_vendor_order_skips_delivery_date", "vendor_order_skips", ["delivery_date"])


def downgrade() -> None:
    op.drop_index("ix_vendor_order_skips_delivery_date", table_name="vendor_order_skips")
    op.drop_index("ix_vendor_order_skips_vendor", table_name="vendor_order_skips")
    op.drop_table("vendor_order_skips")
