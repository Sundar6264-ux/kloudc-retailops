"""Platform schema: stores, sign-ups, masters, settings, credentials, audit,
dedicated hosting, login attempts, support sessions, owner setup links
(docs/PLATFORM_CONTRACT.md section 3).

Revision ID: p0001
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "p0001"
down_revision = None
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def _id():
    return sa.Column("id", BigId, primary_key=True, autoincrement=True)


AUDIT_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append only';
END;
$$ LANGUAGE plpgsql;
"""
AUDIT_TRIGGER = """
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
"""


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "signup_requests",
        _id(),
        sa.Column("requested_code", sa.String(24), nullable=False),
        sa.Column("store_name", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.Text(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=False),
        sa.Column("contact_phone", sa.Text()),
        sa.Column("address", sa.Text()),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("pos_provider", sa.String(16), nullable=False),
        sa.Column("owner_username", sa.String(64), nullable=False),
        sa.Column("owner_password_hash", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_token_hash", sa.String(64), nullable=False),
        sa.Column("reviewed_by", sa.String(128)),
        sa.Column("reviewed_at", TS),
        sa.Column("review_note", sa.Text()),
        sa.Column("store_id", BigId),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index(
        "uq_signup_pending_code",
        "signup_requests",
        ["requested_code"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_signup_status_created", "signup_requests", ["status", "created_at"])

    op.create_table(
        "stores",
        _id(),
        sa.Column("code", sa.String(24), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text()),
        sa.Column("hosting", sa.String(16), nullable=False, server_default="shared"),
        sa.Column("api_base_url", sa.Text()),
        sa.Column("db_name", sa.String(63), nullable=False, unique=True),
        sa.Column("db_host_ref", sa.String(64)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("pos_provider", sa.String(16), nullable=False),
        sa.Column("pack_id", sa.String(64), nullable=False),
        sa.Column("instance_id", sa.String(32)),
        sa.Column("schema_revision", sa.String(32)),
        sa.Column("settings_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provisioning_step", sa.String(32)),
        sa.Column("maintenance_message", sa.Text()),
        sa.Column("min_build_ios", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_build_android", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signup_request_id", BigId, sa.ForeignKey("signup_requests.id", name="fk_stores_signup_request")),
        sa.Column("owner_username", sa.String(64)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", TS),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','provisioning','active','suspended','failed','archived')", name="ck_stores_status"
        ),
        sa.CheckConstraint("hosting IN ('shared','dedicated')", name="ck_stores_hosting"),
    )
    op.create_index("ix_stores_status", "stores", ["status"])
    if pg:
        op.create_foreign_key("fk_signup_requests_store", "signup_requests", "stores", ["store_id"], ["id"])

    op.create_table(
        "platform_users",
        _id(),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        sa.Column("role", sa.String(16), nullable=False, server_default="master"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("totp_secret_enc", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_login_at", TS),
    )
    op.create_table(
        "platform_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "platform_user_id", BigId, sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("client_ip", sa.String(64)),
    )
    op.create_table(
        "store_settings",
        sa.Column("store_id", BigId, sa.ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
    )
    op.create_table(
        "store_credentials",
        _id(),
        sa.Column("store_id", BigId, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("public_fields", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(32), nullable=False),
        sa.Column("key_id", sa.String(32), nullable=False),
        sa.Column("last4", sa.String(8)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_tested_at", TS),
        sa.Column("last_test_message", sa.Text()),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("store_id", "kind", name="uq_store_credentials_store_kind"),
    )
    op.create_table(
        "audit_log",
        _id(),
        sa.Column("at", TS, nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64)),
        sa.Column("actor_name", sa.String(128)),
        sa.Column("store_code", sa.String(24)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128)),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("request_id", sa.String(64)),
    )
    op.create_index("ix_audit_store_at", "audit_log", ["store_code", "at"])
    op.create_index("ix_audit_action_at", "audit_log", ["action", "at"])
    if pg:
        op.execute(AUDIT_TRIGGER_FN)
        op.execute(AUDIT_TRIGGER)

    op.create_table(
        "dedicated_hosting_requests",
        _id(),
        sa.Column("store_id", BigId, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("target_api_base_url", sa.Text()),
        sa.Column("target_db_host_ref", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reviewed_by", sa.String(128)),
        sa.Column("reviewed_at", TS),
        sa.Column("review_note", sa.Text()),
        sa.Column("previous_api_base_url", sa.Text()),
        sa.Column("previous_db_host_ref", sa.String(64)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("completed_at", TS),
    )
    op.create_index(
        "uq_hosting_open_per_store",
        "dedicated_hosting_requests",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'approved', 'migrating')"),
        sqlite_where=sa.text("status IN ('requested', 'approved', 'migrating')"),
    )

    op.create_table(
        "login_attempts",
        _id(),
        sa.Column("at", TS, nullable=False),
        sa.Column("store_code", sa.String(24), nullable=False),
        sa.Column("username_lower", sa.String(64), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_login_attempts_user_at", "login_attempts", ["store_code", "username_lower", "at"])
    op.create_index("ix_login_attempts_ip_at", "login_attempts", ["client_ip", "at"])

    op.create_table(
        "support_sessions",
        _id(),
        sa.Column("platform_user_id", BigId, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("store_code", sa.String(24), nullable=False),
        sa.Column("store_username", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("ended_at", TS),
    )
    op.create_index("ix_support_sessions_store", "support_sessions", ["store_code", "started_at"])

    op.create_table(
        "owner_setup_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("store_id", BigId, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
    )


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.drop_table("owner_setup_tokens")
    op.drop_index("ix_support_sessions_store", table_name="support_sessions")
    op.drop_table("support_sessions")
    op.drop_index("ix_login_attempts_ip_at", table_name="login_attempts")
    op.drop_index("ix_login_attempts_user_at", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("uq_hosting_open_per_store", table_name="dedicated_hosting_requests")
    op.drop_table("dedicated_hosting_requests")
    if pg:
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.drop_index("ix_audit_action_at", table_name="audit_log")
    op.drop_index("ix_audit_store_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("store_credentials")
    op.drop_table("store_settings")
    op.drop_table("platform_sessions")
    op.drop_table("platform_users")
    if pg:
        op.drop_constraint("fk_signup_requests_store", "signup_requests", type_="foreignkey")
    op.drop_index("ix_stores_status", table_name="stores")
    op.drop_table("stores")
    op.drop_index("ix_signup_status_created", table_name="signup_requests")
    op.drop_index("uq_signup_pending_code", table_name="signup_requests")
    op.drop_table("signup_requests")
