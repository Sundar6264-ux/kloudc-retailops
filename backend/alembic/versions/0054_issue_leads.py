"""Category & vendor leads for Fix Issues + the daily lead task record
(docs/BATCH_OCT14_CONTRACT.md, W4; app/issue_lead_models.py).

- issue_leads: one row per PO section / vendor / "everything else" with the
  lead's user id (NULL = no lead). Empty until an admin saves the leads page
  (the page proposes a mapping from the owner's quick-reference document).
- issue_lead_task_runs: which Fix Issues items each lead's daily Team task
  listed, per store day.

The task settings (time, completion hours) are rows in setting_changes
(0052), key "issue_lead_tasks" - no table here.

Additive; the downgrade drops both tables.

Revision ID: 0054
Revises: 0053 (W1's Today order actions), per the batch contract's chain
0053 -> 0054 -> 0055 -> 0056.
"""

from alembic import op
import sqlalchemy as sa

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_leads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="admin"),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "match_key", name="uq_issue_leads_kind_key"),
    )
    op.create_table(
        "issue_lead_task_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("counts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("day", "user_id", name="uq_issue_lead_task_runs_day_user"),
    )
    op.create_index("ix_issue_lead_task_runs_day", "issue_lead_task_runs", ["day"])


def downgrade() -> None:
    op.drop_index("ix_issue_lead_task_runs_day", table_name="issue_lead_task_runs")
    op.drop_table("issue_lead_task_runs")
    op.drop_table("issue_leads")
