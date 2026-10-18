"""category_overrides: who made / changed each rule (Product categories page,
ops fixes R). created_by = username that added the rule (created_at already
exists); updated_at / updated_by = the last change to it, which for a rule
from the page is its deactivation (a new category for an item adds a new rule
and deactivates the old one, so every rule row is a history entry).

Revision ID: 0036
Revises: 0034
"""

from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("category_overrides", sa.Column("created_by", sa.String(), nullable=True))
    op.add_column("category_overrides", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("category_overrides", sa.Column("updated_by", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("category_overrides") as batch:
        batch.drop_column("updated_by")
        batch.drop_column("updated_at")
        batch.drop_column("created_by")
