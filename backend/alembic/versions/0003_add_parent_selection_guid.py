"""add parent_selection_guid to orders (for modifier line items)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("parent_selection_guid", sa.String(), nullable=True))
    op.create_index("ix_orders_parent_selection_guid", "orders", ["parent_selection_guid"])


def downgrade() -> None:
    op.drop_index("ix_orders_parent_selection_guid", table_name="orders")
    op.drop_column("orders", "parent_selection_guid")
