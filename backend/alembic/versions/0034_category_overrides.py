"""category_overrides - app-side corrections of Toast categories (see
models.CategoryOverride / services/category_overrides.py). Seeds the owner's
first rule: every Shaz International item -> RETAIL / Snacks & Savories /
Biscuits, Cookies & Rusk (Toast files them under Baking Essentials, Roti/Naan,
Biscuits & Cookies and Sweets & Mithai).

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
                "match_value": "Shaz International",
                "category_group": "RETAIL",
                "category": "Snacks & Savories",
                "subcategory": "Biscuits, Cookies & Rusk",
                "note": "Owner, Sep 2026: every Shaz International item is Biscuits, Cookies & Rusk "
                "(wrong in Toast; fix list in docs/SHAZ_CATEGORY_FIX.md).",
                # Naive UTC, as UTCDateTime stores it.
                "created_at": dt.datetime.utcnow().replace(microsecond=0),
                "active": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("category_overrides")
