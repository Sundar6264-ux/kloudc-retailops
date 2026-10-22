"""Case size per item from the inventory file's receiving units.

Additive only: three nullable columns on inventory_snapshots, filled by the
inventory upload from the `receiving units` / `receiving unit quantities`
columns (services/receiving_units.py). Older rows stay NULL (= no case size
known: the item is ordered in units, as before). Older code never reads
them, and the forecast doesn't either.

Runs on SQLite and PostgreSQL (env.py: 0063+ must run on both).

Revision ID: 0063
Revises: 0062
"""

from alembic import op
import sqlalchemy as sa

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

COLUMNS = ("case_size", "receiving_unit", "receiving_units_raw")


def upgrade() -> None:
    op.add_column("inventory_snapshots", sa.Column("case_size", sa.Float(), nullable=True))
    op.add_column("inventory_snapshots", sa.Column("receiving_unit", sa.String(), nullable=True))
    op.add_column("inventory_snapshots", sa.Column("receiving_units_raw", sa.String(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN (PostgreSQL; SQLite 3.35+), never a batch copy of this big table.
    for col in reversed(COLUMNS):
        op.execute(f"ALTER TABLE inventory_snapshots DROP COLUMN {col}")
