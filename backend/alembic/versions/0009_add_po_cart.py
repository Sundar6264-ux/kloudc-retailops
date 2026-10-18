"""add po_cart_items table (persistent, multi-supplier purchase order cart)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "po_cart_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("supplier", sa.String, nullable=False),
        sa.Column("item_id", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("supplier_item_id", sa.String, nullable=True),
        sa.Column("qty", sa.Float, nullable=False),
        sa.Column("added_by_user_id", sa.Integer, nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_po_cart_items_supplier", "po_cart_items", ["supplier"])
    op.create_index("ix_po_cart_items_item_id", "po_cart_items", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_po_cart_items_item_id", table_name="po_cart_items")
    op.drop_index("ix_po_cart_items_supplier", table_name="po_cart_items")
    op.drop_table("po_cart_items")
