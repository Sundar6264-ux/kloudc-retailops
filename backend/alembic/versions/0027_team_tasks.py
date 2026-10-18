"""Team hub tasks: task_templates, tasks, task_items, task_events
(see app/team/tasks_models.py, docs/TEAM_HUB_DESIGN.md §5)

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_ONE_ASSIGNEE = "(assignee_user_id IS NULL) != (assignee_team_id IS NULL)"


def upgrade() -> None:
    op.create_table(
        "task_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requires", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("assignee_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assignee_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("rrule", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("generate_ahead_minutes", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_ASSIGNEE, name="ck_task_templates_one_assignee"),
        sa.CheckConstraint("requires IN ('none','photo','note')", name="ck_task_templates_requires"),
        sa.CheckConstraint("priority IN ('normal','urgent')", name="ck_task_templates_priority"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("task_templates.id"), nullable=True),
        sa.Column("assignment_batch_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requires", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("assignee_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assignee_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("completed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("completed_by_name", sa.String(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("completion_media_id", sa.Integer(), sa.ForeignKey("team_media.id"), nullable=True),
        sa.Column("cant_do_reason", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_ASSIGNEE, name="ck_tasks_one_assignee"),
        sa.CheckConstraint("requires IN ('none','photo','note')", name="ck_tasks_requires"),
        sa.CheckConstraint("priority IN ('normal','urgent')", name="ck_tasks_priority"),
        sa.CheckConstraint("status IN ('open','done','cant_do','cancelled')", name="ck_tasks_status"),
        sa.UniqueConstraint("template_id", "due_at", name="uq_tasks_template_due"),
    )
    op.create_index("ix_tasks_assignee_user", "tasks", ["assignee_user_id", "status", "due_at"])
    op.create_index("ix_tasks_assignee_team", "tasks", ["assignee_team_id", "status", "due_at"])
    op.create_index("ix_tasks_batch", "tasks", ["assignment_batch_id"])

    op.create_table(
        "task_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("done_by_user_id", sa.Integer(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_items_task_id", "task_items", ["task_id"])

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_events_task_id", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_items_task_id", table_name="task_items")
    op.drop_table("task_items")
    op.drop_index("ix_tasks_batch", table_name="tasks")
    op.drop_index("ix_tasks_assignee_team", table_name="tasks")
    op.drop_index("ix_tasks_assignee_user", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("task_templates")
