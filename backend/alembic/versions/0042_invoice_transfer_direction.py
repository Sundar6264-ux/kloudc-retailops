"""Invoice upload "Transfer" type: invoice_submissions.transfer_direction.

A new submission_type "transfer" (store <-> container stock movement, WhatsApp
only, never emailed to Toast) needs a direction: "store_to_container" or
"container_to_store". Nullable and additive - every existing row stays NULL
(= not a transfer). submission_type is a plain String, so no enum change.

Revision ID: 0042
Revises: 0041
"""

from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_submissions", sa.Column("transfer_direction", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invoice_submissions") as batch:
        batch.drop_column("transfer_direction")
