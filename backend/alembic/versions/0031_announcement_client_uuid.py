"""Announcements: optional client_uuid so a retried "Post" is idempotent.

Revision ID: 0031
Revises: 0029
"""

from alembic import op
import sqlalchemy as sa

revision = "0031"
# NOTE: chains after 0029 (users autoincrement). If an 0030 migration exists
# when this branch is merged, point this at "0030" instead.
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("announcements") as batch:
        batch.add_column(sa.Column("client_uuid", sa.String(), nullable=True))
    op.create_index(
        "uq_announcements_author_client_uuid",
        "announcements",
        ["created_by_user_id", "client_uuid"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_announcements_author_client_uuid", table_name="announcements")
    with op.batch_alter_table("announcements") as batch:
        batch.drop_column("client_uuid")
