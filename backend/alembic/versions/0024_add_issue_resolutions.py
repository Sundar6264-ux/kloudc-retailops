"""add issue_resolutions table (closed Fix Issues items with a reason - see
models.IssueResolution)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unique constraint inline on create_table - SQLite can't ALTER one in later.
    op.create_table(
        "issue_resolutions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("issue_type", sa.String(), nullable=False),
        sa.Column("issue_key", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=True),
        sa.Column("item_name", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("closed_by_name", sa.String(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issue_type", "issue_key", name="uq_issue_resolution"),
    )
    op.create_index("ix_issue_resolutions_issue_type", "issue_resolutions", ["issue_type"])
    op.create_index("ix_issue_resolutions_closed_at", "issue_resolutions", ["closed_at"])


def downgrade() -> None:
    op.drop_index("ix_issue_resolutions_closed_at", table_name="issue_resolutions")
    op.drop_index("ix_issue_resolutions_issue_type", table_name="issue_resolutions")
    op.drop_table("issue_resolutions")
