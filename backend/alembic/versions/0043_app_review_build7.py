"""App Review build 7 (docs/APP_REVIEW_BUILD7_CONTRACT.md):

- team_rules_acceptances: who agreed to which version of the Team rules
  (the in-app EULA), one row per user and version.
- messages.filtered: the content filter masked a word in the message
  (shown to admins in the moderation queue).
- message_reports becomes the one report queue for chat messages, tasks and
  task comments: target_type ("message" | "task" | "task_comment"),
  target_id, an optional note, a snapshot of a reported task/comment's
  text (snapshot_json), and message_id nullable (set only for a
  message report). Existing rows are all chat reports: target_type
  "message", target_id = message_id.

All additive for build 5/6 apps: existing columns keep their meaning.

Revision ID: 0043
Revises: 0042
"""

from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_rules_acceptances",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column("messages", sa.Column("filtered", sa.Boolean(), nullable=False, server_default="0"))

    with op.batch_alter_table("message_reports", recreate="always") as batch:
        batch.add_column(sa.Column("target_type", sa.String(), nullable=False, server_default="message"))
        batch.add_column(sa.Column("target_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("snapshot_json", sa.Text(), nullable=True))
        batch.alter_column("message_id", existing_type=sa.Integer(), nullable=True)
    op.execute("UPDATE message_reports SET target_type = 'message', target_id = message_id WHERE target_id IS NULL")
    op.create_index(
        "uq_message_reports_target_reporter",
        "message_reports",
        ["target_type", "target_id", "reporter_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_message_reports_target_reporter", table_name="message_reports")
    # Task / comment reports have no message to point at - they can't survive
    # going back to message_id NOT NULL.
    op.execute("DELETE FROM message_reports WHERE message_id IS NULL")
    with op.batch_alter_table("message_reports", recreate="always") as batch:
        batch.drop_column("snapshot_json")
        batch.drop_column("note")
        batch.drop_column("target_id")
        batch.drop_column("target_type")
        batch.alter_column("message_id", existing_type=sa.Integer(), nullable=False)
    # A plain DROP COLUMN (SQLite 3.35+), not a batch copy: rebuilding
    # `messages` would lose its AUTOINCREMENT (the sqlite_sequence row), so
    # ids of deleted messages could be handed out again.
    op.execute("ALTER TABLE messages DROP COLUMN filtered")
    op.drop_table("team_rules_acceptances")
