"""Team tasks: live progress columns (docs/BATCH_OCT15_CONTRACT.md T).

As built: docs/BATCH_OCT15_CONTRACT.md, "As built - TEAM TASKS (server)".

Additive only: two nullable columns on tasks for a task's live progress (a
Fix Issues lead's daily list reads "N left of M" and closes itself at 0).
Older code never reads them.

The leftover closing-checklist tasks are closed as "Missed" by the task job
itself (auto_tasks.expire_checklist_tasks, idempotent, every 5 minutes) -
not here - so the same rule covers any database, now and later.

Revision ID: 0059
Revises: 0058
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("progress_total", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("progress_done", sa.Integer(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN (SQLite 3.35+), never a batch copy (as 0055).
    for col in ("progress_done", "progress_total"):
        op.execute(f"ALTER TABLE tasks DROP COLUMN {col}")
