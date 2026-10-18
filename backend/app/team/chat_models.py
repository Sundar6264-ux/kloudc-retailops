"""Phase 2 tables: announcements (with a recipient snapshot) and chat (team
channels + DMs, messages, reports, DM blocks). Design §5.

Every "by" column keeps a name snapshot next to the user id, like the rest of
the schema, so history still reads correctly after an account is deleted.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

# --- Announcements --------------------------------------------------------------


class Announcement(Base):
    """A one-way post from an admin to one or more teams. Who it went to is
    fixed at post time in announcement_recipients - never recomputed from
    current team membership - so "7 of 9 acknowledged" never changes later.

    `pending_push` holds a push that couldn't go out yet ("post" or
    "remind:<n>") because it was posted during quiet hours; the Team job
    loop sends it at 7 AM.
    """

    __tablename__ = "announcements"
    __table_args__ = (
        Index("uq_announcements_author_client_uuid", "created_by_user_id", "client_uuid", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, default="")
    media_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_media.id"), nullable=True)
    require_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_by_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    edited_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    remind_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_push: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Set by the client so a retried "Post" (e.g. after a timeout) returns
    # the first announcement instead of posting and pushing it twice.
    client_uuid: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class AnnouncementAudience(Base):
    """Which teams an announcement was addressed to - for display only
    ("Kitchen, Grocery"). Who actually got it is announcement_recipients."""

    __tablename__ = "announcement_audience"

    announcement_id: Mapped[int] = mapped_column(Integer, ForeignKey("announcements.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), primary_key=True)


class AnnouncementRecipient(Base):
    """One row per person the announcement was sent to, written at post time
    from the audience teams' members at that moment (design §5)."""

    __tablename__ = "announcement_recipients"

    announcement_id: Mapped[int] = mapped_column(Integer, ForeignKey("announcements.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    user_name: Mapped[str] = mapped_column(String)
    seen_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    acked_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    last_reminded_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)


# --- Chat -------------------------------------------------------------------------


class Channel(Base):
    """A chat: one per team (kind "team", members = the team's members who
    have chat access, derived live) or one per pair of people (kind "dm",
    members are channel_members rows). v1 has no ad-hoc group channels."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "team" | "dm"
    kind: Mapped[str] = mapped_column(String)
    team_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("teams.id"), nullable=True, unique=True)
    # "<lower user id>:<higher user id>" - so a pair has exactly one DM.
    dm_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    archived_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)


class ChannelMember(Base):
    """Per-person state in a channel. For a DM these rows *are* the
    membership; for a team channel membership comes from the team and a row
    only exists once the person has read/muted something there.

    `hidden` is the blocker's view of a blocked DM. `last_pushed_*` drive
    the one-push-per-minute chat throttle (design §8.1).
    """

    __tablename__ = "channel_members"

    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("channels.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    last_pushed_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    last_pushed_message_id: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    """A chat message. `client_uuid` (generated by the phone per send) makes
    a retried send return the same message instead of posting twice.
    Deleting is soft: the API shows "Message deleted", while the original
    stays readable to admins through a report for 30 days (retention job).
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("sender_user_id", "client_uuid", name="uq_messages_sender_client_uuid"),
        Index("ix_messages_channel_id_id", "channel_id", "id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("channels.id"))
    sender_user_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_name: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, default="")
    media_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_media.id"), nullable=True)
    client_uuid: Mapped[str] = mapped_column(String)
    # JSON list of mentioned user ids, parsed server-side from "@username".
    mentions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    edited_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    deleted_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delete_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Migration 0043: the content filter masked a word in it
    # (content_filter.py) - shown to admins in the moderation queue.
    filtered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Migration 0055 (W5): pinned in its channel.
    pinned_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    pinned_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pinned_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class MessageReport(Base):
    """Someone flagged content. Admins resolve (optionally removing it) or
    dismiss it, with a note (design §4.5).

    Migration 0043: one queue for chat messages, tasks and task comments -
    `target_type` "message" | "task" | "task_comment" and `target_id` (the
    message / task / task_events id). `message_id` is set only for a message
    report (the existing column, kept for build 5/6 apps and retention).
    """

    __tablename__ = "message_reports"
    __table_args__ = (
        UniqueConstraint("message_id", "reporter_user_id", name="uq_message_reports_message_reporter"),
        Index("uq_message_reports_target_reporter", "target_type", "target_id", "reporter_user_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("messages.id"), index=True, nullable=True)
    target_type: Mapped[str] = mapped_column(String, default="message", server_default="message")
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reporter_user_id: Mapped[int] = mapped_column(Integer, index=True)
    reporter_name: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String, default="")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON copy of a task / task comment's reported text at report time, so
    # the evidence survives a later edit or removal (a message keeps its own
    # text for 30 days after deletion instead).
    snapshot_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # "open" | "resolved" | "dismissed"
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    resolved_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolved_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolved_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class DmBlock(Base):
    """One row per direction. While a block exists either way, the two can't
    start a DM or send in their existing one (design §3)."""

    __tablename__ = "dm_blocks"

    blocker_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    blocked_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
