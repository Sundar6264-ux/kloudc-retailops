"""add users.allowed_features (per-user dashboard feature access, admin-granted)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-02

Backfills every EXISTING user (admin or not) to the full feature list, so
nobody who currently has full access silently loses it the moment this
migration runs - an admin can then dial individual users down from there.
New users created after this migration default to allowed_features='[]'
(the column's own server_default) and an admin must explicitly grant access.
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# Keep this in sync with app/features.py's FEATURES list - duplicated here
# (not imported) since migrations must stay self-contained and correct even
# if the application's feature list changes again later.
_ALL_FEATURE_KEYS = [
    "overview",
    "items_sold",
    "reorder_candidates",
    "supplier_projection",
    "purchase_order_cart",
    "delivery_review",
    "transfer_review",
    "inventory_reports",
    "reconciliation",
    "ask_bot",
    "help",
]


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("allowed_features", sa.String, nullable=False, server_default="[]"),
    )
    conn = op.get_bind()
    all_features_json = json.dumps(_ALL_FEATURE_KEYS)
    conn.execute(
        sa.text("UPDATE users SET allowed_features = :features"),
        {"features": all_features_json},
    )


def downgrade() -> None:
    op.drop_column("users", "allowed_features")
