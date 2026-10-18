"""Team hub tasks (design §5, Phase 1): tasks, their checklist items and
event history, and the recurring templates that generate them.

A task is assigned to exactly one person or exactly one team. A team task is
one row that any member can complete (first one wins); assigning to several
named people creates one row each, linked by `assignment_batch_id`.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_ONE_ASSIGNEE = "(assignee_user_id IS NULL) != (assignee_team_id IS NULL)"



def _store_timezone() -> str:
    """Column default: the store's time zone (STORE_TIMEZONE)."""
    from app.store.settings_view import store_timezone_name

    return store_timezone_name()


class TaskTemplate(Base):
    """A recurring task rule. `rrule` says when each occurrence is DUE, in
    `timezone` (e.g. 'FREQ=WEEKLY;BYDAY=MO;BYHOUR=15;BYMINUTE=0' = Mondays
    3 PM); the task appears `generate_ahead_minutes` earlier (see
    recurrence.py). Edits affect future occurrences only - each generated
    task keeps its own copy of title/assignee/due time. "Deleting" a
    template just sets active=False.
    """

    __tablename__ = "task_templates"
    __table_args__ = (
        CheckConstraint(_ONE_ASSIGNEE, name="ck_task_templates_one_assignee"),
        CheckConstraint("requires IN ('none','photo','note')", name="ck_task_templates_requires"),
        CheckConstraint("priority IN ('normal','urgent')", name="ck_task_templates_priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requires: Mapped[str] = mapped_column(String, default="none")
    priority: Mapped[str] = mapped_column(String, default="normal")
    assignee_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    assignee_team_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("teams.id"), nullable=True)
    rrule: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String, default=_store_timezone)
    generate_ahead_minutes: Mapped[int] = mapped_column(Integer, default=420)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Migration 0055 (W5): steps copied onto every task made from it (JSON
    # list of strings), assignees by weekday ({"MO": {"user_id": 3}, "SA":
    # {"team_id": 2}} - other days use the template's own assignee), and
    # whether it repeats at all (False = a saved template used only by
    # "Create task now"; its rrule is then "").
    items_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    day_assignees_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    repeats: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_ONE_ASSIGNEE, name="ck_tasks_one_assignee"),
        CheckConstraint("requires IN ('none','photo','note')", name="ck_tasks_requires"),
        CheckConstraint("priority IN ('normal','urgent')", name="ck_tasks_priority"),
        CheckConstraint("status IN ('open','done','cant_do','cancelled')", name="ck_tasks_status"),
        # Recurring generation is idempotent: one task per template occurrence.
        UniqueConstraint("template_id", "due_at", name="uq_tasks_template_due"),
        Index("ix_tasks_assignee_user", "assignee_user_id", "status", "due_at"),
        Index("ix_tasks_assignee_team", "assignee_team_id", "status", "due_at"),
        Index("ix_tasks_batch", "assignment_batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("task_templates.id"), nullable=True)
    # A uuid shared by the tasks of one multi-person assignment.
    assignment_batch_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requires: Mapped[str] = mapped_column(String, default="none")
    priority: Mapped[str] = mapped_column(String, default="normal")
    assignee_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    assignee_team_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("teams.id"), nullable=True)
    due_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    completed_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    completion_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_media_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("team_media.id"), nullable=True
    )
    cant_do_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Where it came from: 'chat_message' | 'checklist' | 'invoice' | 'alert'.
    source_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Migration 0055 (W5): the board's "In progress" - set while the task is
    # still `open` (older apps only know open/done/cant_do/cancelled).
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    started_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Migration 0059 (Oct 15): live progress of a task that tracks a count
    # (a Fix Issues lead's daily list: "N left of M"). NULL = no progress.
    progress_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_done: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class TaskItem(Base):
    """An optional checklist sub-item of a task."""

    __tablename__ = "task_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    done_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)


class TaskEvent(Base):
    """The task's audit trail - created / reassigned / done / reopened /
    cant_do / cancelled / commented / edited. Never updated or deleted.
    A comment is an event with kind='commented' and detail {"body": ...}.
    """

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), index=True)
    # Null for the recurring-task scheduler.
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
