"""add push notification tables: device_tokens, notification_prefs,
notification_log (see models.DeviceToken / NotificationPref / NotificationLog)

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unique constraints inline on create_table - SQLite can't ALTER one in later.
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_device_tokens_token"),
    )
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])

    op.create_table(
        "notification_prefs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_notification_pref_user_key"),
    )
    op.create_index("ix_notification_prefs_user_id", "notification_prefs", ["user_id"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_log_dedupe_key"),
    )
    op.create_index("ix_notification_log_key", "notification_log", ["key"])
    op.create_index("ix_notification_log_sent_at", "notification_log", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_log_sent_at", table_name="notification_log")
    op.drop_index("ix_notification_log_key", table_name="notification_log")
    op.drop_table("notification_log")
    op.drop_index("ix_notification_prefs_user_id", table_name="notification_prefs")
    op.drop_table("notification_prefs")
    op.drop_index("ix_device_tokens_user_id", table_name="device_tokens")
    op.drop_table("device_tokens")
