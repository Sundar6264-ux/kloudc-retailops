"""add paid_at/promised_at to open_order_cache (paid-but-open vs future orders)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("open_order_cache", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("open_order_cache", sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("open_order_cache", "promised_at")
    op.drop_column("open_order_cache", "paid_at")
