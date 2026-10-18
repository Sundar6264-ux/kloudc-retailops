"""add paid_at to orders (actual payment time, distinct from business_date)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_orders_paid_at", "orders", ["paid_at"])


def downgrade() -> None:
    op.drop_index("ix_orders_paid_at", table_name="orders")
    op.drop_column("orders", "paid_at")
