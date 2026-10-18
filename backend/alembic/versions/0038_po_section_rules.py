"""po_section_rules - editable PO sections (the heading each product prints
under on the vendor PO PDF, services/po_sections.py), plus a section for
hand-added cart lines.

rule_type "item": match_value = item_id; "vendor": match_value = vendor name
(matches any of an item's ";"-separated suppliers); "rename": match_value =
the section being renamed / merged, section = its new name. Rules are never
deleted: a change deactivates the old row (updated_at / updated_by) and adds a
new one, so the table is its own history. created_batch / updated_batch tie
the rows one change touched together (used by "Undo last change").

po_cart_items.section: the section a hand-added cart line (no item_id)
prints under; NULL = Other. Catalog items use po_section_rules instead.

category_overrides (Toast category corrections) is not touched.

Revision ID: 0038
Revises: 0037
"""

from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "po_section_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rule_type", sa.String(), nullable=False),
        sa.Column("match_value", sa.String(), nullable=False),
        sa.Column("section", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_batch", sa.String(), nullable=True),
        sa.Column("updated_batch", sa.String(), nullable=True),
    )
    op.create_index("ix_po_section_rules_active_type", "po_section_rules", ["active", "rule_type"])
    op.add_column("po_cart_items", sa.Column("section", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("po_cart_items") as batch:
        batch.drop_column("section")
    op.drop_index("ix_po_section_rules_active_type", table_name="po_section_rules")
    op.drop_table("po_section_rules")
