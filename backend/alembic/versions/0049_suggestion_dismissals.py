"""Suggestions batch (docs/AUTOMATION_BATCH_CONTRACT.md items 4, 5 and 7):
suggested deliveries, suggested "Closed in Toast" and suggested vendor
schedules are all computed on request from data the app already has, so the
only thing stored is a person saying "not this one".

suggestion_dismissals: one row per dismissed suggestion.
- kind: delivery | invoice_close | vendor_schedule.
- suggestion_key: what was dismissed, built by the service that made it
  (delivery: "<received date>|<vendor, lowercased>"; invoice_close: the
  submission id; vendor_schedule: "<vendor, lowercased>|<weekdays json>",
  so a different pattern for the same vendor is suggested again).
- dismissed_by_user_id / dismissed_by_name / created_at.
Unique on (kind, suggestion_key), inline (SQLite can't ALTER a constraint
in afterwards). Undo deletes the row. Additive; downgrade drops the table.

Revision ID: 0049
Revises: 0048
"""

from alembic import op
import sqlalchemy as sa

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suggestion_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("suggestion_key", sa.String(), nullable=False),
        sa.Column("dismissed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("dismissed_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "suggestion_key", name="uq_suggestion_dismissal"),
    )
    op.create_index("ix_suggestion_dismissals_kind", "suggestion_dismissals", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_suggestion_dismissals_kind", table_name="suggestion_dismissals")
    op.drop_table("suggestion_dismissals")
