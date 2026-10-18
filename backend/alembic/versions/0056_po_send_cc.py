"""Send PO: email CCs (docs/BATCH_OCT14_CONTRACT.md W2).

- vendor_contacts.cc_json: a contact's default CC addresses (JSON list of
  emails, at most 10). Send PO's preview pre-fills its CC list from the
  default recipients' CCs.
- po_sends.email_cc_json: the CC addresses a send actually used (JSON list),
  shown in the send log. NULL = no CC (and every send before this).

Additive only (plain nullable columns); existing rows keep NULL.

Revision ID: 0056
Revises: 0055 (W5) - the contract's order is 0053 -> 0054 -> 0055 -> 0056;
the final stage fixes the chain if a lower number moved.
"""

from alembic import op
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendor_contacts", sa.Column("cc_json", sa.Text(), nullable=True))
    op.add_column("po_sends", sa.Column("email_cc_json", sa.Text(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN (SQLite 3.35+), never a batch copy: both tables are
    # AUTOINCREMENT (0050 - their ids are shown in the UI and logs and must
    # never be reused) and a batch rebuild silently drops that (Verify 1).
    op.execute("ALTER TABLE po_sends DROP COLUMN email_cc_json")
    op.execute("ALTER TABLE vendor_contacts DROP COLUMN cc_json")
