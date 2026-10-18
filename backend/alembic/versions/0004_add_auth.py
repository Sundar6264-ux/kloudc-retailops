"""add users and user_sessions tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String, nullable=False),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "user_sessions",
        sa.Column("token", sa.String, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_table("users")
