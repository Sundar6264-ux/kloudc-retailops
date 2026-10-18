"""add invoice_submissions table

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_submissions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vendor", sa.String, nullable=False),
        sa.Column("submission_type", sa.String, nullable=False),
        sa.Column("total_amount", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("submitted_by_user_id", sa.Integer, nullable=True),
        sa.Column("submitted_by_name", sa.String, nullable=True),
        sa.Column("original_filename", sa.String, nullable=False),
        sa.Column("stored_filename", sa.String, nullable=False),
        sa.Column("content_type", sa.String, nullable=True),
        sa.Column("file_token", sa.String, nullable=False),
        sa.Column("pdf_filename", sa.String, nullable=True),
        sa.Column("whatsapp_sent", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("whatsapp_error", sa.Text, nullable=True),
        sa.Column("toast_email_sent", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("toast_email_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invoice_submissions_vendor", "invoice_submissions", ["vendor"])
    op.create_index(
        "ix_invoice_submissions_file_token", "invoice_submissions", ["file_token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_submissions_file_token", table_name="invoice_submissions")
    op.drop_index("ix_invoice_submissions_vendor", table_name="invoice_submissions")
    op.drop_table("invoice_submissions")
