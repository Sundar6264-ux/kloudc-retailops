"""Fix Issues "more time" snooze + came-back history, and cart removal
requests (docs/FIXISSUES_CART_CONTRACT.md).

- issue_resolutions.snooze_until (Date, nullable): the store-local day a
  snoozed close (reason give_time, 7 days) comes back. NULL on older
  give_time rows = closed day + 7 (fix_issues.snooze_until_for).
- issue_resolution_events: every close and reopen, never deleted (the
  resolution row itself is still replaced / deleted as before). Seeded with
  one "closed" event per existing resolution, so times_closed starts right.
- po_cart_items.removal_requested_at / _by / _by_user_id / _reason: a
  non-admin's request to take a line out of the cart (only admins remove).

All additive; downgrade drops them.

Revision ID: 0040
Revises: 0039
"""

from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issue_resolutions", sa.Column("snooze_until", sa.Date(), nullable=True))
    op.create_table(
        "issue_resolution_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("issue_type", sa.String(), nullable=False),
        sa.Column("issue_key", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=True),
        sa.Column("item_name", sa.String(), nullable=True),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("how", sa.String(), nullable=True),
        sa.Column("by_name", sa.String(), nullable=True),
        sa.Column("snooze_until", sa.Date(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_issue_resolution_events_type_key", "issue_resolution_events", ["issue_type", "issue_key"]
    )
    op.create_index("ix_issue_resolution_events_at", "issue_resolution_events", ["at"])
    # History starts with what's closed today (same stored datetime text).
    op.execute(
        "INSERT INTO issue_resolution_events "
        "(issue_type, issue_key, item_id, item_name, event, reason, reason_detail, by_name, at) "
        "SELECT issue_type, issue_key, item_id, item_name, 'closed', reason, reason_detail, closed_by_name, "
        "closed_at FROM issue_resolutions"
    )

    op.add_column("po_cart_items", sa.Column("removal_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("po_cart_items", sa.Column("removal_requested_by", sa.String(), nullable=True))
    op.add_column("po_cart_items", sa.Column("removal_requested_by_user_id", sa.Integer(), nullable=True))
    op.add_column("po_cart_items", sa.Column("removal_requested_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("po_cart_items") as batch:
        batch.drop_column("removal_requested_reason")
        batch.drop_column("removal_requested_by_user_id")
        batch.drop_column("removal_requested_by")
        batch.drop_column("removal_requested_at")
    op.drop_index("ix_issue_resolution_events_at", table_name="issue_resolution_events")
    op.drop_index("ix_issue_resolution_events_type_key", table_name="issue_resolution_events")
    op.drop_table("issue_resolution_events")
    with op.batch_alter_table("issue_resolutions") as batch:
        batch.drop_column("snooze_until")
