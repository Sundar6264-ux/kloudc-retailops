"""add invoice_submissions.whatsapp_status_overridden_by/toast_email_status_overridden_by
(admin-only manual override of a send channel's status, for when the real
automated Whapi/Toast-email attempt is stuck/unreliable - see
services/invoice_submission.py::override_channel_status)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_submissions", sa.Column("whatsapp_status_overridden_by", sa.String(), nullable=True))
    op.add_column("invoice_submissions", sa.Column("toast_email_status_overridden_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_submissions", "toast_email_status_overridden_by")
    op.drop_column("invoice_submissions", "whatsapp_status_overridden_by")
