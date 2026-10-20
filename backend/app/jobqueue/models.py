"""Job queue tables in the platform database (alembic_platform p0004).

They share ``PlatformBase`` so the platform metadata (and its migration
check) knows them; ``app/platform/models.py`` imports this module at its end.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import TS, BigId, PlatformBase, utcnow

OPEN_STATUSES = ("queued", "running", "failed")
_OPEN_SQL = "dedupe_key IS NOT NULL AND status IN ('queued', 'running', 'failed')"


class Job(PlatformBase):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after", "priority"),
        Index("ix_jobs_store", "store_code", "created_at"),
        Index(
            "uq_jobs_open_dedupe",
            "kind",
            "dedupe_key",
            unique=True,
            postgresql_where=text(_OPEN_SQL),
            sqlite_where=text(_OPEN_SQL),
        ),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    store_code: Mapped[Optional[str]] = mapped_column(String(24))
    payload: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="900")
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200))
    run_after: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    locked_by: Mapped[Optional[str]] = mapped_column(String(128))
    locked_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    heartbeat_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    result: Mapped[Optional[str]] = mapped_column(Text)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow, onupdate=utcnow)


class JobWorker(PlatformBase):
    __tablename__ = "job_workers"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    kinds: Mapped[Optional[str]] = mapped_column(Text)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    started_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    heartbeat_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    stopped_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    jobs_done: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    jobs_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
