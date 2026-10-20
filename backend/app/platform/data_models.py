"""Platform tables for store data ownership (docs/WEBSITE_READY_CONTRACT.md,
track B; migration ``alembic_platform/versions/p0002_data_ownership.py``).

- ``store_usage_daily``  one number per store, UTC day and metric (I3)
- ``store_plans``        the plan of a store (I4); no row = the platform's
                         default plan (p0002 gives every store that existed
                         before it the ``custom`` plan)
- ``store_exports``      "Export all data" ZIP files and their download links
- ``store_offboarding``  archive -> final export -> grace period -> purge

Rows are keyed by the store code (not a foreign key) so they outlive a purged
store: the tombstone keeps the usage history, the exports list and the
off-boarding timeline.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import BigInteger, Date, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import TS, BigId, PlatformBase, utcnow


class StoreUsageDaily(PlatformBase):
    __tablename__ = "store_usage_daily"

    store_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)


class StorePlan(PlatformBase):
    __tablename__ = "store_plans"

    store_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    plan: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class StoreExport(PlatformBase):
    __tablename__ = "store_exports"
    __table_args__ = (
        Index("ix_store_exports_store_created", "store_code", "created_at"),
        # One export at a time per store.
        Index(
            "uq_store_exports_running",
            "store_code",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    store_code: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # queued|running|ready|failed|expired
    reason: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")  # manual|offboarding
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[Optional[str]] = mapped_column(Text)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    tables: Mapped[Optional[int]] = mapped_column(Integer)
    rows: Mapped[Optional[int]] = mapped_column(BigInteger)
    files: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    link_token_hash: Mapped[Optional[str]] = mapped_column(String(64))
    link_expires_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    downloads: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    expires_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class StoreOffboarding(PlatformBase):
    __tablename__ = "store_offboarding"

    store_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # scheduled|cancelled|deleted
    purge_after: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    final_export_id: Mapped[Optional[int]] = mapped_column(BigId)
    started_by: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(128))
    cancelled_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    purged_by: Mapped[Optional[str]] = mapped_column(String(128))
    purged_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    db_name: Mapped[Optional[str]] = mapped_column(String(63))
    note: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
