"""Store identity stamp (StoreOps M2, MT8; docs/M2_CONTRACT.md 1.5, plan 9.1).

One row says which store this database belongs to. The server refuses to
start when it doesn't match STORE_CODE (app/store/identity.py), so a
copy-paste mistake can't point one store's server at another store's data.
The row is written by `python -m app.store init` (or once with
STORE_IDENTITY_ADOPT=true), never by this migration.

Revision ID: 0062
Revises: 0061
"""

from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_code", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_store_identity_one_row"),
    )


def downgrade() -> None:
    op.drop_table("store_identity")
