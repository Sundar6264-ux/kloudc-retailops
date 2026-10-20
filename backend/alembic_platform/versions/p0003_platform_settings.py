"""Platform settings a master edits in the console (docs/WEBSITE_READY_CONTRACT.md, track A).

PLACEHOLDER landed with the website-ready contract so the platform chain is
fixed before the tracks build in parallel (p0002 B, p0003 A, p0004 C). Track
A fills upgrade()/downgrade() with additive changes only.

Revision ID: p0003
Revises: p0002
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "p0003"
down_revision = "p0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
