"""Placed orders: a status column ("pdf" or "placed").

Every PO PDF generation used to be logged as its own "placed" order, so a
preview (to send on WhatsApp) followed by "Mark placed" showed up as two
orders. Rows written before this migration are all treated as "placed".

Revision ID: 0032
Revises: 0031
"""

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("placed_orders") as batch:
        batch.add_column(sa.Column("status", sa.String(), nullable=False, server_default="placed"))


def downgrade() -> None:
    with op.batch_alter_table("placed_orders") as batch:
        batch.drop_column("status")
