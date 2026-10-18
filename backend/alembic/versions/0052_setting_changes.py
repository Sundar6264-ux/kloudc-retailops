"""Admin-set settings with history: setting_changes (app/setting_models.py).

First use: the "Use Toast for inventory" switch on the Inventory from Toast
page (services/inventory_api_switch.py). The newest row for a key wins over
the .env default (INVENTORY_API_SNAPSHOTS); no row = the .env value, as
before. Rows are never updated or deleted (history).

Additive; the downgrade drops the table (the switch then falls back to the
.env value).

Revision ID: 0052
Revises: 0051
"""

from alembic import op
import sqlalchemy as sa

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setting_changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("previous_source", sa.String(), nullable=True),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("changed_by_name", sa.String(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_setting_changes_key", "setting_changes", ["key"])
    op.create_index("ix_setting_changes_changed_at", "setting_changes", ["changed_at"])


def downgrade() -> None:
    op.drop_index("ix_setting_changes_changed_at", table_name="setting_changes")
    op.drop_index("ix_setting_changes_key", table_name="setting_changes")
    op.drop_table("setting_changes")
