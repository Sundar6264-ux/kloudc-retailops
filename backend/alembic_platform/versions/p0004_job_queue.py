"""Background job queue (SKIP LOCKED) and worker heartbeats (docs/WEBSITE_READY_CONTRACT.md, track C).

PLACEHOLDER landed with the website-ready contract so the platform chain is
fixed before the tracks build in parallel (p0002 B, p0003 A, p0004 C). Track
C fills upgrade()/downgrade() with additive changes only.

Revision ID: p0004
Revises: p0003
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "p0004"
down_revision = "p0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
