"""add supplier_item_id to inventory_snapshots (vendor's own SKU for PO exports)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_snapshots", sa.Column("supplier_item_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_snapshots", "supplier_item_id")
