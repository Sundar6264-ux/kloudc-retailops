"""Background job queue (SKIP LOCKED) and worker heartbeats (docs/WEBSITE_READY_CONTRACT.md, track C).

Additive only: two new tables in the platform database. Nothing reads them
until ``JOB_QUEUE_MODE=queue`` (the default stays ``inline``).

- ``jobs``: one row per enqueued job. Workers claim with
  ``SELECT ... FOR UPDATE SKIP LOCKED``; a partial unique index makes a
  ``dedupe_key`` unique among the jobs still waiting or running.
- ``job_workers``: one row per worker process (heartbeat every 15 s).

Revision ID: p0004
Revises: p0003
"""

from alembic import op
import sqlalchemy as sa

revision = "p0004"
down_revision = "p0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
OPEN = "dedupe_key IS NOT NULL AND status IN ('queued', 'running', 'failed')"


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("store_code", sa.String(24)),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("run_after", TS, nullable=False),
        sa.Column("locked_by", sa.String(128)),
        sa.Column("locked_at", TS),
        sa.Column("heartbeat_at", TS),
        sa.Column("result", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after", "priority"])
    op.create_index("ix_jobs_store", "jobs", ["store_code", "created_at"])
    op.create_index(
        "uq_jobs_open_dedupe",
        "jobs",
        ["kind", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(OPEN),
        sqlite_where=sa.text(OPEN),
    )
    op.create_table(
        "job_workers",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("hostname", sa.String(128), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("kinds", sa.Text()),
        sa.Column("concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("heartbeat_at", TS, nullable=False),
        sa.Column("stopped_at", TS),
        sa.Column("jobs_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_failed", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("job_workers")
    op.drop_index("uq_jobs_open_dedupe", table_name="jobs")
    op.drop_index("ix_jobs_store", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
