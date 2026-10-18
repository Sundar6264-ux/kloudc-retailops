"""Team hub core tables (design §5): teams and their members, the change
feed every Team write records into, and validated uploaded images.

Tasks (tasks_models.py) and announcements/chat (chat_models.py) live in
their own modules so each phase owns its own file.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Team(Base):
    """A group of users that tasks, announcements and a chat channel can be
    sent to - Grocery, Kitchen, Managers. The one `is_everyone` team has no
    member rows at all: every user is implicitly in it, so a new account
    never has to be added by hand. Archived teams are kept (old tasks and
    announcements still name them) but can't be picked for anything new.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    # "#RRGGBB" - how the team is tinted in the app and on the website.
    color: Mapped[str] = mapped_column(String, default="#5B8DEF")
    is_everyone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    archived_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class TeamChange(Base):
    """One row per Team write - the change feed behind GET /api/team/updates
    (design §7). A row is a *pointer* (what kind of thing, which id, what
    happened) plus who may see it; the client refetches the entity itself.
    Written by changes.record_change in the same transaction as the change,
    so a committed change always has its row and a rolled-back one never
    does.

    Audience: `channel_id` set -> members of that channel only (even admins
    must be members); `team_id` and/or `user_id` set -> those team members /
    that user, plus admins; none set -> every logged-in user.

    `sqlite_autoincrement` makes SQLite never reuse a seq, even after the
    newest rows are pruned, so a cursor always means the same point in time.
    """

    __tablename__ = "team_changes"
    __table_args__ = (
        Index("ix_team_changes_created_at", "created_at"),
        {"sqlite_autoincrement": True},
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[int] = mapped_column(Integer)
    # "created" | "updated" | "deleted"
    action: Mapped[str] = mapped_column(String)
    team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class TeamMedia(Base):
    """An image uploaded for a task proof, a chat message or an
    announcement - always re-encoded by the server (media.py), stored under
    a random name, and only ever served through the authenticated
    GET /api/team/media/{token}. Uploaded unattached; the feature that uses
    it sets owner_type/owner_id (media.attach_media), which is what decides
    who else may see it.
    """

    __tablename__ = "team_media"
    # AUTOINCREMENT (migration 0029): tasks/messages/announcements point at
    # media by id, so a deleted row's id must never come back.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, unique=True)
    # Relative to team_uploads_dir, e.g. "2026/09/<random>.jpg".
    path: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String, default="image/jpeg")
    bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    # NULL once the uploader's account is deleted (no one inherits uploader
    # access).
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    # e.g. "task" / "message" / "announcement" - see media.register_media_access.
    owner_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Migration 0055 (W5): "image" (re-encoded JPEG), "audio" (a chat voice
    # note, AAC in MP4) or "document" (a PDF). display_name is a document's
    # name for showing only - never part of the stored path.
    kind: Mapped[str] = mapped_column(String, default="image", server_default="image")
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class TeamRulesAcceptance(Base):
    """Migration 0043: a user agreed to the Team rules (the in-app EULA,
    App Review guideline 1.2) - one row per user and rules version, so a new
    version asks again. See app/team/rules.py. Not a foreign key to users:
    account deletion removes the rows itself (bootstrap.delete_user_team_data).
    """

    __tablename__ = "team_rules_acceptances"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    accepted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
