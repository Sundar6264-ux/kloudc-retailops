"""Every case size an item lists, from the inventory file's receiving units.

Additive only: one nullable column on inventory_snapshots, e.g.
"24;30;35;60" in file order (services/receiving_units.case_info). The item's
case_size (0063) is the first of them; the screens show the others as
"Other case sizes: 30, 35, 60". NULL when the item lists no case size.

Runs on SQLite and PostgreSQL (env.py: 0063+ must run on both).

Revision ID: 0064
Revises: 0063
"""

from alembic import op
import sqlalchemy as sa

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_snapshots", sa.Column("case_sizes", sa.String(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN (PostgreSQL; SQLite 3.35+), never a batch copy of this big table.
    op.execute("ALTER TABLE inventory_snapshots DROP COLUMN case_sizes")
