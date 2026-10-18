"""Users: hide_sales - an admin-set option to keep sales numbers (today's /
yesterday's sales, revenue, Items Sold, the weekly digest's sales section...)
out of that person's web dashboard and app. Off for everyone by default, so
nothing changes until an admin ticks it.

Revision ID: 0033
Revises: 0032
"""

from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild) so users keeps its AUTOINCREMENT
    # (migration 0029).
    op.add_column("users", sa.Column("hide_sales", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("users", recreate="always", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_column("hide_sales")
