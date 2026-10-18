"""add invoice submission delete request/trash columns

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-07

"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_submissions", sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoice_submissions", sa.Column("delete_requested_by_name", sa.String, nullable=True))
    op.add_column("invoice_submissions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoice_submissions", sa.Column("deleted_by_name", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_submissions", "deleted_by_name")
    op.drop_column("invoice_submissions", "deleted_at")
    op.drop_column("invoice_submissions", "delete_requested_by_name")
    op.drop_column("invoice_submissions", "delete_requested_at")
