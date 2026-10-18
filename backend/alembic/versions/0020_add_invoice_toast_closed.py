"""add invoice_submissions.toast_closed_at/toast_closed_by_name (manual
confirmation that Toast actually finished processing this into a closed
bill - unobservable automatically, no Purchasing API access)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_submissions", sa.Column("toast_closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoice_submissions", sa.Column("toast_closed_by_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_submissions", "toast_closed_by_name")
    op.drop_column("invoice_submissions", "toast_closed_at")
