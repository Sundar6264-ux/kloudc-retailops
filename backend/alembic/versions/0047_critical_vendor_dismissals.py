"""Critical vendors: Close / Skip this week / Remind me later
(docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md).

critical_vendor_dismissals: one row per admin action that hides a vendor
from the Today "Critical" tile and the critical-vendors push, store-wide.
- vendor: the vendor name exactly as the Reorder Candidates report groups it.
- kind: close | skip_week | snooze; reason (close only), note.
- until (UTC): when it ends at the latest (close: created_at + 14 days).
- critical_item_ids_json (close): the vendor's critical item ids at the
  moment of closing - it comes back once an item NOT in this list turns
  critical.
- created_by_* / created_at, and cleared_* when it ended (cleared_reason:
  show_again, new_critical_item, expired, superseded). Rows are never
  deleted, so who/when/why stays in the history.

AUTOINCREMENT: "Show again" takes an id, so an id must never be handed out
twice. Additive; downgrade drops the table.

Revision ID: 0047
Revises: 0046
"""

from alembic import op
import sqlalchemy as sa

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "critical_vendor_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("critical_item_ids_json", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by_user_id", sa.Integer(), nullable=True),
        sa.Column("cleared_by_name", sa.String(), nullable=True),
        sa.Column("cleared_reason", sa.String(), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_critical_vendor_dismissals_vendor", "critical_vendor_dismissals", ["vendor"])
    op.create_index("ix_critical_vendor_dismissals_created_at", "critical_vendor_dismissals", ["created_at"])
    op.create_index("ix_critical_vendor_dismissals_cleared_at", "critical_vendor_dismissals", ["cleared_at"])


def downgrade() -> None:
    op.drop_index("ix_critical_vendor_dismissals_cleared_at", table_name="critical_vendor_dismissals")
    op.drop_index("ix_critical_vendor_dismissals_created_at", table_name="critical_vendor_dismissals")
    op.drop_index("ix_critical_vendor_dismissals_vendor", table_name="critical_vendor_dismissals")
    op.drop_table("critical_vendor_dismissals")
