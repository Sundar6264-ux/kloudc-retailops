"""Send the PO to the vendor from the app (docs/AUTOMATION_BATCH_CONTRACT.md
item 8).

vendor_contacts: who receives a vendor's purchase orders (email, WhatsApp
number or group, contact name, notes, send_po), managed by admins.
vendor_key = vendor trimmed + lowercased, how a cart vendor finds them.

po_sends: the log of every "Send PO" attempt - who, when, to whom, status
per channel, our email Message-ID / Whapi message id, the exact lines on the
PDF and the placed_orders row it was recorded as. idempotency_key is unique
(inline, per the SQLite constraint gotcha) so a repeated POST can never
email twice. Rows are never deleted.

AUTOINCREMENT on both: ids are shown in the UI / logs and must never be
reused. Additive; downgrade drops both tables.

Revision ID: 0050
Revises: 0049
"""

from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("vendor_key", sa.String(), nullable=False),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("whatsapp", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("send_po", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_name", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_vendor_contacts_vendor_key", "vendor_contacts", ["vendor_key"])

    op.create_table(
        "po_sends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("supplier", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("pdf_filename", sa.String(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        sa.Column("cart_item_ids_json", sa.Text(), nullable=False),
        sa.Column("email_to_json", sa.Text(), nullable=True),
        sa.Column("email_status", sa.String(), nullable=True),
        sa.Column("email_error", sa.Text(), nullable=True),
        sa.Column("email_message_id", sa.String(), nullable=True),
        sa.Column("whatsapp_to", sa.String(), nullable=True),
        sa.Column("whatsapp_status", sa.String(), nullable=True),
        sa.Column("whatsapp_error", sa.Text(), nullable=True),
        sa.Column("whatsapp_message_id", sa.String(), nullable=True),
        sa.Column("placed_order_id", sa.Integer(), nullable=True),
        sa.Column("sent_by_user_id", sa.Integer(), nullable=True),
        sa.Column("sent_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_po_sends_idempotency_key"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_po_sends_supplier", "po_sends", ["supplier"])
    op.create_index("ix_po_sends_created_at", "po_sends", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_po_sends_created_at", table_name="po_sends")
    op.drop_index("ix_po_sends_supplier", table_name="po_sends")
    op.drop_table("po_sends")
    op.drop_index("ix_vendor_contacts_vendor_key", table_name="vendor_contacts")
    op.drop_table("vendor_contacts")
