"""add case_of column to po_cart_items (units per case, for cost estimate)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "po_cart_items",
        sa.Column("case_of", sa.Float, nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("po_cart_items", "case_of")
