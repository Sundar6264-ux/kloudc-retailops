"""Data ownership: usage per store and day, plans, exports, off-boarding (docs/WEBSITE_READY_CONTRACT.md, track B).

Additive only: four new tables (models in ``app/platform/data_models.py``).
Every store that exists when this runs gets the ``custom`` plan (no limits);
stores created later get the platform's default plan.

Revision ID: p0002
Revises: p0001
"""

from alembic import op
import sqlalchemy as sa

revision = "p0002"
down_revision = "p0001"
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "store_usage_daily",
        sa.Column("store_code", sa.String(24), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("metric", sa.String(32), primary_key=True),
        sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "store_plans",
        sa.Column("store_code", sa.String(24), primary_key=True),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
    )
    op.create_table(
        "store_exports",
        sa.Column("id", BigId, primary_key=True, autoincrement=True),
        sa.Column("store_code", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("requested_by_type", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.Text()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("tables", sa.Integer()),
        sa.Column("rows", sa.BigInteger()),
        sa.Column("files", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("link_token_hash", sa.String(64)),
        sa.Column("link_expires_at", TS),
        sa.Column("downloads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("expires_at", TS),
    )
    op.create_index("ix_store_exports_store_created", "store_exports", ["store_code", "created_at"])
    op.create_index(
        "uq_store_exports_running",
        "store_exports",
        ["store_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "store_offboarding",
        sa.Column("store_code", sa.String(24), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("purge_after", TS),
        sa.Column("final_export_id", BigId),
        sa.Column("started_by", sa.String(128), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("cancelled_by", sa.String(128)),
        sa.Column("cancelled_at", TS),
        sa.Column("purged_by", sa.String(128)),
        sa.Column("purged_at", TS),
        sa.Column("db_name", sa.String(63)),
        sa.Column("note", sa.Text()),
        sa.Column("updated_at", TS, nullable=False),
    )
    # Stores that exist now keep working exactly as before: no limits.
    op.execute(
        sa.text(
            "INSERT INTO store_plans (store_code, plan, updated_at, updated_by) "
            "SELECT code, 'custom', CURRENT_TIMESTAMP, 'migration p0002' FROM stores"
        )
    )


def downgrade() -> None:
    op.drop_table("store_offboarding")
    op.drop_index("uq_store_exports_running", table_name="store_exports")
    op.drop_index("ix_store_exports_store_created", table_name="store_exports")
    op.drop_table("store_exports")
    op.drop_table("store_plans")
    op.drop_table("store_usage_daily")
