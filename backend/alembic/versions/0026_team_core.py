"""Team hub core: teams, team_members, team_changes (change feed),
team_media, and users.can_assign; seeds the Everyone/Grocery/Kitchen/
Managers teams (see app/team/models.py, docs/TEAM_HUB_BUILD_PLAN.md)

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-27

"""
import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_SEED_TEAMS = [
    ("Everyone", "#5B8DEF", True),
    ("Grocery", "#3ED598", False),
    ("Kitchen", "#F5A524", False),
    ("Managers", "#A78BFA", False),
]


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("can_assign", sa.Boolean(), nullable=False, server_default="0"))

    teams = op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column("is_everyone", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_teams_name"),
    )

    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])

    # AUTOINCREMENT so a seq is never reused after pruning (see TeamChange).
    op.create_table(
        "team_changes",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_team_changes_created_at", "team_changes", ["created_at"])

    op.create_table(
        "team_media",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("owner_type", sa.String(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_team_media_token"),
    )
    op.create_index("ix_team_media_uploaded_by_user_id", "team_media", ["uploaded_by_user_id"])

    # Naive UTC, matching how UTCDateTime stores values.
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    op.bulk_insert(
        teams,
        [
            {"name": name, "color": color, "is_everyone": everyone, "created_at": now, "archived_at": None}
            for name, color, everyone in _SEED_TEAMS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_team_media_uploaded_by_user_id", table_name="team_media")
    op.drop_table("team_media")
    op.drop_index("ix_team_changes_created_at", table_name="team_changes")
    op.drop_table("team_changes")
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("can_assign")
