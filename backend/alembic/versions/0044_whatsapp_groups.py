"""WhatsApp groups a checklist can post to
(docs/CHECKLIST_BUILDER_APP_AND_GROUPS_CONTRACT.md, section 1).

- whatsapp_groups: key (unique slug, the value stored in a checklist's
  settings.whatsapp_group), label, chat_id (Whapi chat id; NULL = the env
  setting, for the built-ins), builtin, active, audit columns.
- Seeds the two built-ins, "store" and "kitchen", with chat_id NULL, so every
  existing checklist keeps sending exactly where it did.

Additive: no existing table changes.

Revision ID: 0044
Revises: 0043
"""

import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "whatsapp_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=True),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.UniqueConstraint("key", name="uq_whatsapp_groups_key"),
    )
    now = dt.datetime.now(dt.timezone.utc)
    op.bulk_insert(
        table,
        [
            {"key": "store", "label": "Spice Town staff group", "chat_id": None, "builtin": True,
             "active": True, "created_at": now, "created_by": "migration 0044"},
            {"key": "kitchen", "label": "Street Kitchen staff group", "chat_id": None, "builtin": True,
             "active": True, "created_at": now, "created_by": "migration 0044"},
        ],
    )


def downgrade() -> None:
    op.drop_table("whatsapp_groups")
