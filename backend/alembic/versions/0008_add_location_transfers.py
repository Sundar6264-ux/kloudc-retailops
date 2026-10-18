"""add location_transfers table (Container <-> Each stock movement log)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-30

"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_transfers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String, nullable=False),
        sa.Column("item_name", sa.String, nullable=True),
        sa.Column("direction", sa.String, nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("transfer_date", sa.Date, nullable=False),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("logged_by_user_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_location_transfers_item_id", "location_transfers", ["item_id"])
    op.create_index("ix_location_transfers_transfer_date", "location_transfers", ["transfer_date"])


def downgrade() -> None:
    op.drop_index("ix_location_transfers_transfer_date", table_name="location_transfers")
    op.drop_index("ix_location_transfers_item_id", table_name="location_transfers")
    op.drop_table("location_transfers")
