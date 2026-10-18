"""Inventory snapshot from the Toast API + hourly barcode re-check
(docs/AUTOMATION_BATCH_CONTRACT.md items 1 and 6; services/inventory_api.py).

inventory_snapshots gains two nullable columns, both NULL on every CSV row:
- api_out_of_stock: 1 on an API-built row whose item Toast reports as
  OUT_OF_STOCK. The API gives no count for those, so inventory_quantity is
  stored as 0 and this flag says "0 or below, unknown".
- csv_quantity: on those flagged rows, the item's count in the latest CSV
  upload at build time, so Negative inventory keeps the CSV's negative count
  until the next CSV.

toast_menu_items: the latest pull of /menus/v2/menus, one row per menu item
(name, sku = barcode, plu, price, pricing strategy) plus when it was fetched
and the menu's own lastUpdated. Replaced as a whole on each changed pull.

Additive; downgrade drops the table and the two columns.

Revision ID: 0048
Revises: 0047
"""

from alembic import op
import sqlalchemy as sa

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN (no batch copy): inventory_snapshots has ~600k rows.
    op.add_column("inventory_snapshots", sa.Column("api_out_of_stock", sa.Boolean(), nullable=True))
    op.add_column("inventory_snapshots", sa.Column("csv_quantity", sa.Float(), nullable=True))
    op.create_table(
        "toast_menu_items",
        sa.Column("item_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("sku", sa.String(), nullable=True),
        sa.Column("plu", sa.String(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("pricing_strategy", sa.String(), nullable=True),
        sa.Column("menu_last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("toast_menu_items")
    op.drop_column("inventory_snapshots", "csv_quantity")
    op.drop_column("inventory_snapshots", "api_out_of_stock")
