"""add invoice_submissions.whatsapp_ambiguous (distinguish an unconfirmed
Whapi timeout from a genuine send failure)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_submissions",
        sa.Column("whatsapp_ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("invoice_submissions", "whatsapp_ambiguous")
