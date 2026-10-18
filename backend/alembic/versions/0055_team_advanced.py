"""Team tab advanced (docs/BATCH_OCT14_CONTRACT.md, W5):

- task_templates: steps (items_json, copied onto every task made from it),
  per-weekday assignees (day_assignees_json, e.g. {"MO": {"user_id": 3}})
  and `repeats` (false = a saved template only used by "Create task now";
  its rrule is then "").
- tasks: started_at / started_by_* - the board's "In progress" column. The
  status stays "open" (builds 5-9 know nothing else).
- messages: pinned_at / pinned_by_* (pinned messages in a channel).
- team_media: kind ("image" | "audio" | "document"), display_name (a PDF's
  name, for display only - never a path), duration_ms (voice notes),
  page_count (PDFs).

All additive. Chained W1 0053 -> W4 0054 -> W5 0055 -> W2 0056.

Revision ID: 0055
Revises: 0054
"""

from alembic import op
import sqlalchemy as sa

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_templates", sa.Column("items_json", sa.Text(), nullable=True))
    op.add_column("task_templates", sa.Column("day_assignees_json", sa.Text(), nullable=True))
    op.add_column("task_templates", sa.Column("repeats", sa.Boolean(), nullable=False, server_default="1"))

    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("started_by_user_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("started_by_name", sa.String(), nullable=True))

    op.add_column("messages", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("pinned_by_user_id", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("pinned_by_name", sa.String(), nullable=True))

    op.add_column("team_media", sa.Column("kind", sa.String(), nullable=False, server_default="image"))
    op.add_column("team_media", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column("team_media", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("team_media", sa.Column("page_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN (SQLite 3.35+), never a batch copy: messages and
    # team_media are AUTOINCREMENT tables and a rebuild would lose that.
    for table, cols in (
        ("team_media", ("page_count", "duration_ms", "display_name", "kind")),
        ("messages", ("pinned_by_name", "pinned_by_user_id", "pinned_at")),
        ("tasks", ("started_by_name", "started_by_user_id", "started_at")),
        ("task_templates", ("repeats", "day_assignees_json", "items_json")),
    ):
        for col in cols:
            op.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
