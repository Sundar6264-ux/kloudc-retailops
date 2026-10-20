"""Platform settings a master edits in the console, and the "must change the
temporary password" flag of a newly added master (docs/WEBSITE_READY_CONTRACT.md,
track A: A2, A3). Additive only.

Revision ID: p0003
Revises: p0002
"""

from alembic import op
import sqlalchemy as sa

revision = "p0003"
down_revision = "p0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        # JSON text of the value ({"v": <value>}), so bools / ints / strings keep their type.
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "platform_users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("platform_users", "must_change_password")
    op.drop_table("platform_settings")
