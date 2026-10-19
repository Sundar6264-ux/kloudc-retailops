"""category_overrides - app-side corrections of Toast categories (see
models.CategoryOverride / services/category_overrides.py). Seeds one example
rule: every item of the sample supplier "Sample Biscuit Supplier" -> RETAIL /
Snacks & Savories / Biscuits, Cookies & Rusk. `python -m app.store init`
removes it for every new store (store/__main__.py LEGACY_CATEGORY_SUPPLIER).

Revision ID: 0034
Revises: 0033
"""

import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No table-level constraints, but anything added later goes inline here
    # (SQLite can't ALTER a constraint in afterwards).
    table = op.create_table(
        "category_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_type", sa.String(), nullable=False),
        sa.Column("match_value", sa.String(), nullable=False),
        sa.Column("category_group", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("subcategory", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.bulk_insert(
        table,
        [
            {
                "match_type": "supplier",
                "match_value": "Sample Biscuit Supplier",
                "category_group": "RETAIL",
                "category": "Snacks & Savories",
                "subcategory": "Biscuits, Cookies & Rusk",
                "note": "Example rule: every Sample Biscuit Supplier item is Biscuits, Cookies & Rusk "
                "(removed by store init).",
                # Naive UTC, as UTCDateTime stores it.
                "created_at": dt.datetime.utcnow().replace(microsecond=0),
                "active": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("category_overrides")
