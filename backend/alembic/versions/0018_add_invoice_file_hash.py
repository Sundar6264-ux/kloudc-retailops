"""add invoice_submissions.file_hash (exact-file duplicate detection)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_submissions", sa.Column("file_hash", sa.String(), nullable=True))
    op.create_index("ix_invoice_submissions_file_hash", "invoice_submissions", ["file_hash"])


def downgrade() -> None:
    op.drop_index("ix_invoice_submissions_file_hash", table_name="invoice_submissions")
    op.drop_column("invoice_submissions", "file_hash")
