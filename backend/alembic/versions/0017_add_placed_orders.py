"""add placed_orders table (a real record of each PO PDF export)

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "placed_orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("supplier", sa.String, nullable=False),
        sa.Column("item_count", sa.Integer, nullable=False),
        sa.Column("items_json", sa.Text, nullable=False),
        sa.Column("placed_by_user_id", sa.Integer, nullable=True),
        sa.Column("placed_by_name", sa.String, nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_placed_orders_supplier", "placed_orders", ["supplier"])
    op.create_index("ix_placed_orders_placed_at", "placed_orders", ["placed_at"])


def downgrade() -> None:
    op.drop_index("ix_placed_orders_placed_at", table_name="placed_orders")
    op.drop_index("ix_placed_orders_supplier", table_name="placed_orders")
    op.drop_table("placed_orders")
