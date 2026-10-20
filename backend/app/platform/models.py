"""Platform database models (contract section 3; owner C).

Separate metadata (``PlatformBase``) from the store models: these tables only
ever live in the platform database (``so_platform``), never in a store
database, and store tables never live here. Schema changes go through
``backend/alembic_platform`` (``alembic -n platform ...``).
"""

import datetime as dt
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# bigint identity on PostgreSQL; INTEGER PRIMARY KEY (rowid) on SQLite, the
# only form SQLite auto-increments (the SQLite smoke test of the models).
BigId = BigInteger().with_variant(Integer(), "sqlite")
TS = DateTime(timezone=True)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class PlatformBase(DeclarativeBase):
    pass


class Store(PlatformBase):
    __tablename__ = "stores"
    __table_args__ = (Index("ix_stores_status", "status"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    hosting: Mapped[str] = mapped_column(String(16), nullable=False, server_default="shared")
    api_base_url: Mapped[Optional[str]] = mapped_column(Text)
    db_name: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    db_host_ref: Mapped[Optional[str]] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    pos_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(String(32))
    schema_revision: Mapped[Optional[str]] = mapped_column(String(32))
    settings_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    provisioning_step: Mapped[Optional[str]] = mapped_column(String(32))
    maintenance_message: Mapped[Optional[str]] = mapped_column(Text)
    min_build_ios: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    min_build_android: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    signup_request_id: Mapped[Optional[int]] = mapped_column(BigId, ForeignKey("signup_requests.id"))
    owner_username: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(128))
    approved_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow, onupdate=utcnow)


class SignupRequest(PlatformBase):
    __tablename__ = "signup_requests"
    __table_args__ = (
        Index(
            "uq_signup_pending_code",
            "requested_code",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_signup_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    requested_code: Mapped[str] = mapped_column(String(24), nullable=False)
    store_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_email: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[Optional[str]] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    pos_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_username: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128))
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    store_id: Mapped[Optional[int]] = mapped_column(BigId, ForeignKey("stores.id", use_alter=True))
    client_ip: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)


class PlatformUser(PlatformBase):
    __tablename__ = "platform_users"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # stored lower case
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="master")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    totp_secret_enc: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class PlatformSession(PlatformBase):
    __tablename__ = "platform_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform_user_id: Mapped[int] = mapped_column(BigId, ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64))


class StoreSetting(PlatformBase):
    __tablename__ = "store_settings"

    store_id: Mapped[int] = mapped_column(BigId, ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded scalar
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class StoreCredential(PlatformBase):
    __tablename__ = "store_credentials"
    __table_args__ = (UniqueConstraint("store_id", "kind", name="uq_store_credentials_store_kind"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigId, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    public_fields: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(String(32), nullable=False)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    last4: Mapped[Optional[str]] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="untested")
    last_tested_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    last_test_message: Mapped[Optional[str]] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)


class AuditLog(PlatformBase):
    """Append only: the PostgreSQL migration adds a trigger refusing UPDATE
    and DELETE."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_store_at", "store_code", "at"),
        Index("ix_audit_action_at", "action", "at"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(64))
    actor_name: Mapped[Optional[str]] = mapped_column(String(128))
    store_code: Mapped[Optional[str]] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[Optional[str]] = mapped_column(String(128))
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    client_ip: Mapped[Optional[str]] = mapped_column(String(64))
    request_id: Mapped[Optional[str]] = mapped_column(String(64))


class DedicatedHostingRequest(PlatformBase):
    __tablename__ = "dedicated_hosting_requests"
    __table_args__ = (
        Index(
            "uq_hosting_open_per_store",
            "store_id",
            unique=True,
            postgresql_where=text("status IN ('requested', 'approved', 'migrating')"),
            sqlite_where=text("status IN ('requested', 'approved', 'migrating')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigId, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    target_api_base_url: Mapped[Optional[str]] = mapped_column(Text)
    target_db_host_ref: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128))
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    # What the store pointed at before `complete` (for `rollback`).
    previous_api_base_url: Mapped[Optional[str]] = mapped_column(Text)
    previous_db_host_ref: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class LoginAttempt(PlatformBase):
    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_user_at", "store_code", "username_lower", "at"),
        Index("ix_login_attempts_ip_at", "client_ip", "at"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    store_code: Mapped[str] = mapped_column(String(24), nullable=False)
    username_lower: Mapped[str] = mapped_column(String(64), nullable=False)
    client_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SupportSession(PlatformBase):
    """A master's time-limited support session inside one store (impersonate
    for support). The store session row lives in the store's database; this
    row is the platform's record of it (who, which store, as whom, until
    when) so it can be listed and ended early."""

    __tablename__ = "support_sessions"
    __table_args__ = (Index("ix_support_sessions_store", "store_code", "started_at"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    platform_user_id: Mapped[int] = mapped_column(BigId, ForeignKey("platform_users.id"), nullable=False)
    store_code: Mapped[str] = mapped_column(String(24), nullable=False)
    store_username: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False)
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class OwnerSetupToken(PlatformBase):
    """A one-time link for a store's owner admin to set their password (a
    store created by a master without a sign-up, or a master-issued reset).
    Shown to the master once; only the hash is kept."""

    __tablename__ = "owner_setup_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigId, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False)
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


# Website-ready track C: the job queue tables (p0004) live with the platform
# metadata. Imported last: app/jobqueue/models.py builds on PlatformBase.
from app.jobqueue import models as _jobqueue_models  # noqa: E402,F401
# Website-ready track B: the data-ownership tables share this metadata (p0002).
from app.platform import data_models as _data_models  # noqa: E402,F401
