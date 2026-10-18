"""Category & vendor leads for Fix Issues (docs/BATCH_OCT14_CONTRACT.md, W4;
migration 0054).

Kept out of models.py (a file every batch edits). Imported by
services/issue_leads.py, which the app always loads (routers/issue_leads.py
and services/fix_issues.py).

- IssueLead: who leads one PO section, one vendor, or "everything else"
  (kind "fallback", key ""). A row with user_id NULL = deliberately no lead.
  No row at all = not reviewed yet (the admin page shows the proposal from
  the owner's quick-reference document for it).
- IssueLeadTaskRun: the daily Team task one lead got for one store day and
  exactly which Fix Issues items it listed - what "a task has been assigned"
  means on the Fix Issues filters.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Date, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

KINDS = ("section", "vendor", "fallback")


class IssueLead(Base):
    __tablename__ = "issue_leads"
    __table_args__ = (UniqueConstraint("kind", "match_key", name="uq_issue_leads_kind_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "section" | "vendor" | "fallback"
    kind: Mapped[str] = mapped_column(String)
    # Lower-cased, trimmed section / vendor name ("" for the fallback).
    match_key: Mapped[str] = mapped_column(String)
    # The name as shown (the section / vendor spelling when it was saved).
    display_name: Mapped[str] = mapped_column(String)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "proposal" (accepted from the document's proposal) | "admin" (set by hand)
    source: Mapped[str] = mapped_column(String, default="admin")
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class IssueLeadTaskRun(Base):
    __tablename__ = "issue_lead_task_runs"
    __table_args__ = (UniqueConstraint("day", "user_id", name="uq_issue_lead_task_runs_day_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    # JSON list of "<issue_type>:<issue_key>"
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    # JSON {issue_type: count}
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
