"""add vendor_schedules table

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-07

"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_schedules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vendor", sa.String, nullable=False),
        sa.Column("delivery_weekdays", sa.String, nullable=False),
        sa.Column("lead_days", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vendor", name="uq_vendor_schedule_vendor"),
    )
    op.create_index("ix_vendor_schedules_vendor", "vendor_schedules", ["vendor"])


def downgrade() -> None:
    op.drop_index("ix_vendor_schedules_vendor", table_name="vendor_schedules")
    op.drop_table("vendor_schedules")
