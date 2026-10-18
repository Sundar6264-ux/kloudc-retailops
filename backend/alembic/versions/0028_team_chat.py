"""Team hub Phase 2: announcements (with the recipient snapshot) and chat -
channels (team + DM), channel_members, messages, message_reports, dm_blocks
(see app/team/chat_models.py, docs/TEAM_HUB_DESIGN.md §5)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("team_media.id"), nullable=True),
        sa.Column("require_ack", sa.Boolean(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_push", sa.String(), nullable=True),
    )
    op.create_index("ix_announcements_created_by_user_id", "announcements", ["created_by_user_id"])
    op.create_index("ix_announcements_created_at", "announcements", ["created_at"])

    op.create_table(
        "announcement_audience",
        sa.Column("announcement_id", sa.Integer(), sa.ForeignKey("announcements.id"), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), primary_key=True),
    )

    op.create_table(
        "announcement_recipients",
        sa.Column("announcement_id", sa.Integer(), sa.ForeignKey("announcements.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("user_name", sa.String(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_announcement_recipients_user_id", "announcement_recipients", ["user_id"])

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("dm_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("team_id", name="uq_channels_team_id"),
        sa.UniqueConstraint("dm_key", name="uq_channels_dm_key"),
    )

    op.create_table(
        "channel_members",
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("last_read_message_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pushed_message_id", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_channel_members_user_id", "channel_members", ["user_id"])

    # AUTOINCREMENT so a message id is never reused (read markers compare ids).
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), nullable=False),
        sa.Column("sender_name", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("team_media.id"), nullable=True),
        sa.Column("client_uuid", sa.String(), nullable=False),
        sa.Column("mentions_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("delete_reason", sa.String(), nullable=True),
        sa.UniqueConstraint("sender_user_id", "client_uuid", name="uq_messages_sender_client_uuid"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_messages_channel_id_id", "messages", ["channel_id", "id"])
    op.create_index("ix_messages_sender_user_id", "messages", ["sender_user_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    op.create_table(
        "message_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("reporter_user_id", sa.Integer(), nullable=False),
        sa.Column("reporter_name", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolved_by_name", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(), nullable=True),
        sa.UniqueConstraint("message_id", "reporter_user_id", name="uq_message_reports_message_reporter"),
    )
    op.create_index("ix_message_reports_message_id", "message_reports", ["message_id"])
    op.create_index("ix_message_reports_reporter_user_id", "message_reports", ["reporter_user_id"])
    op.create_index("ix_message_reports_status", "message_reports", ["status"])

    op.create_table(
        "dm_blocks",
        sa.Column("blocker_user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("blocked_user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dm_blocks_blocked_user_id", "dm_blocks", ["blocked_user_id"])


def downgrade() -> None:
    op.drop_index("ix_dm_blocks_blocked_user_id", table_name="dm_blocks")
    op.drop_table("dm_blocks")
    op.drop_index("ix_message_reports_status", table_name="message_reports")
    op.drop_index("ix_message_reports_reporter_user_id", table_name="message_reports")
    op.drop_index("ix_message_reports_message_id", table_name="message_reports")
    op.drop_table("message_reports")
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_sender_user_id", table_name="messages")
    op.drop_index("ix_messages_channel_id_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_channel_members_user_id", table_name="channel_members")
    op.drop_table("channel_members")
    op.drop_table("channels")
    op.drop_index("ix_announcement_recipients_user_id", table_name="announcement_recipients")
    op.drop_table("announcement_recipients")
    op.drop_table("announcement_audience")
    op.drop_index("ix_announcements_created_at", table_name="announcements")
    op.drop_index("ix_announcements_created_by_user_id", table_name="announcements")
    op.drop_table("announcements")
