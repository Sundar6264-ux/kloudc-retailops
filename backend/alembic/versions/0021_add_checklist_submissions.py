"""add checklist_submissions table (Daily Closing Checklists - native
replacement for the Main Store / Market Kitchen Google Forms)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checklist_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("checklist_type", sa.String(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by_name", sa.String(), nullable=False),
        sa.Column("answers_json", sa.Text(), nullable=False),
        sa.Column("all_clear", sa.Boolean(), nullable=False),
        sa.Column("issue_summary", sa.Text(), nullable=True),
        sa.Column("answers_hash", sa.String(), nullable=False),
        sa.Column("whatsapp_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("whatsapp_error", sa.Text(), nullable=True),
        sa.Column("whatsapp_ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_checklist_submissions_checklist_type", "checklist_submissions", ["checklist_type"]
    )
    op.create_index(
        "ix_checklist_submissions_submitted_by_user_id", "checklist_submissions", ["submitted_by_user_id"]
    )
    op.create_index(
        "ix_checklist_submissions_answers_hash", "checklist_submissions", ["answers_hash"]
    )
    op.create_index(
        "ix_checklist_submissions_created_at", "checklist_submissions", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_checklist_submissions_created_at", table_name="checklist_submissions")
    op.drop_index("ix_checklist_submissions_answers_hash", table_name="checklist_submissions")
    op.drop_index("ix_checklist_submissions_submitted_by_user_id", table_name="checklist_submissions")
    op.drop_index("ix_checklist_submissions_checklist_type", table_name="checklist_submissions")
    op.drop_table("checklist_submissions")
