"""Notifications: store defaults and per-user push log (docs/BATCH_OCT15_CONTRACT.md N).

Adds push_deliveries (app/notification_models.py): one row per user a push
reached - who got what, which notification_log (one row per alert, only a
recipient count) can't say. Chat pushes are logged here too.

The new role-aware defaults need no table: they live in the alert catalog
(services/notifications.py ALERTS), and an admin's store-wide changes to
them are setting_changes rows (key "notification_defaults", migration 0052).
A user's own choice stays a notification_prefs row and always wins; prod has
none today, so no data moves.

Additive; the downgrade drops the table (sends keep working without it - the
log write is best-effort).

Revision ID: 0058
Revises: 0057
"""

from alembic import op
import sqlalchemy as sa

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_push_deliveries_user_id", "push_deliveries", ["user_id"])
    op.create_index("ix_push_deliveries_key", "push_deliveries", ["key"])
    op.create_index("ix_push_deliveries_sent_at", "push_deliveries", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_push_deliveries_sent_at", table_name="push_deliveries")
    op.drop_index("ix_push_deliveries_key", table_name="push_deliveries")
    op.drop_index("ix_push_deliveries_user_id", table_name="push_deliveries")
    op.drop_table("push_deliveries")
