"""initial schema: orders, inventory_snapshots, vendors_reference, job_runs

Revision ID: 0001
Revises:
Create Date: 2026-08-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("toast_order_guid", sa.String, nullable=False),
        sa.Column("toast_check_guid", sa.String, nullable=False),
        sa.Column("toast_selection_guid", sa.String, nullable=False),
        sa.Column("business_date", sa.Date, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_guid", sa.String, nullable=True),
        sa.Column("item_name", sa.String, nullable=True),
        sa.Column("quantity", sa.Float, nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("net_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("voided", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dining_option", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("toast_selection_guid", name="uq_orders_selection_guid"),
    )
    op.create_index("ix_orders_toast_order_guid", "orders", ["toast_order_guid"])
    op.create_index("ix_orders_toast_check_guid", "orders", ["toast_check_guid"])
    op.create_index("ix_orders_toast_selection_guid", "orders", ["toast_selection_guid"])
    op.create_index("ix_orders_business_date", "orders", ["business_date"])
    op.create_index("ix_orders_item_guid", "orders", ["item_guid"])

    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("item_id", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=True),
        sa.Column("category_group", sa.String, nullable=True),
        sa.Column("category", sa.String, nullable=True),
        sa.Column("subcategory", sa.String, nullable=True),
        sa.Column("inventory_status", sa.String, nullable=True),
        sa.Column("inventory_quantity", sa.Float, nullable=True),
        sa.Column("inventory_cost", sa.Float, nullable=True),
        sa.Column("inventory_value", sa.Float, nullable=True),
        sa.Column("inventory_days_on_hand", sa.Float, nullable=True),
        sa.Column("cost", sa.Float, nullable=True),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("gross_margin", sa.Float, nullable=True),
        sa.Column("gross_profit", sa.Float, nullable=True),
        sa.Column("last_7_day_sales", sa.Float, nullable=True),
        sa.Column("last_30_day_sales", sa.Float, nullable=True),
        sa.Column("last_90_day_sales", sa.Float, nullable=True),
        sa.Column("last_7_day_orders", sa.Float, nullable=True),
        sa.Column("last_30_day_orders", sa.Float, nullable=True),
        sa.Column("last_90_day_orders", sa.Float, nullable=True),
        sa.Column("supplier", sa.String, nullable=True),
        sa.Column("last_received_from", sa.String, nullable=True),
        sa.Column("inventory_last_received", sa.String, nullable=True),
        sa.Column("source_filename", sa.String, nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_date", "item_id", name="uq_inventory_snapshot_date_item"),
    )
    op.create_index("ix_inventory_snapshots_snapshot_date", "inventory_snapshots", ["snapshot_date"])
    op.create_index("ix_inventory_snapshots_item_id", "inventory_snapshots", ["item_id"])

    op.create_table(
        "vendors_reference",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vendor_id", sa.String, nullable=False),
        sa.Column("vendor_name", sa.String, nullable=True),
        sa.Column("item_id", sa.String, nullable=True),
        sa.Column("cost", sa.Float, nullable=True),
        sa.Column("lead_time_days", sa.Integer, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vendor_id", "item_id", name="uq_vendor_item"),
    )
    op.create_index("ix_vendors_reference_vendor_id", "vendors_reference", ["vendor_id"])
    op.create_index("ix_vendors_reference_item_id", "vendors_reference", ["item_id"])

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_name", sa.String, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("business_date", sa.Date, nullable=True),
    )
    op.create_index("ix_job_runs_job_name", "job_runs", ["job_name"])


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("vendors_reference")
    op.drop_table("inventory_snapshots")
    op.drop_table("orders")
