"""add purchase_log table (manual receiving records for Phase 3 reconciliation)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String, nullable=False),
        sa.Column("item_name", sa.String, nullable=True),
        sa.Column("supplier", sa.String, nullable=True),
        sa.Column("quantity_received", sa.Float, nullable=False),
        sa.Column("unit_cost", sa.Float, nullable=True),
        sa.Column("received_date", sa.Date, nullable=False),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("logged_by_user_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_purchase_log_item_id", "purchase_log", ["item_id"])
    op.create_index("ix_purchase_log_received_date", "purchase_log", ["received_date"])


def downgrade() -> None:
    op.drop_index("ix_purchase_log_received_date", table_name="purchase_log")
    op.drop_index("ix_purchase_log_item_id", table_name="purchase_log")
    op.drop_table("purchase_log")
