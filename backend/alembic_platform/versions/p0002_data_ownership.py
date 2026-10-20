"""Data ownership: usage per store and day, plans, exports, off-boarding (docs/WEBSITE_READY_CONTRACT.md, track B).

PLACEHOLDER landed with the website-ready contract so the platform chain is
fixed before the tracks build in parallel (p0002 B, p0003 A, p0004 C). Track
B fills upgrade()/downgrade() with additive changes only.

Revision ID: p0002
Revises: p0001
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "p0002"
down_revision = "p0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
