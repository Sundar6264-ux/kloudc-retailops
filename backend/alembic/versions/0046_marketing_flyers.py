"""Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md, section 1).

- whatsapp_groups.marketing: an admin ticks the groups flyers may go to. NOT
  NULL DEFAULT 0, so every existing group (the two built-in staff groups
  included) starts as not-marketing.
- flyers: one row per uploaded flyer. The file itself lives on disk under
  FLYERS_DIR (default ~/Spice Town Flyers/<Category>/), at file_rel_path.
  Soft delete only (deleted_at).
- flyer_sends: one row per (send batch, target). `channel` is "whatsapp" for
  now; target_ref is the chat id snapshot (never returned to clients).
  queued_at / attempted_at / attempts / ambiguous are the background worker's
  own bookkeeping (stale-row recovery, and checking the group before a retry
  of a send that may have gone through).

Both new tables use AUTOINCREMENT: a flyer's id is part of its file name, so
an id must never be handed out twice.

Additive; downgrade drops the two tables and the column.

Revision ID: 0046
Revises: 0045
"""

from alembic import op
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_groups", sa.Column("marketing", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "flyers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("file_rel_path", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_flyers_category", "flyers", ["category"])
    op.create_index("ix_flyers_sha256", "flyers", ["sha256"])
    op.create_index("ix_flyers_deleted_at", "flyers", ["deleted_at"])
    op.create_table(
        "flyer_sends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flyer_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False, server_default="whatsapp"),
        sa.Column("target_key", sa.String(), nullable=False),
        sa.Column("target_label", sa.String(), nullable=False),
        sa.Column("target_ref", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("requested_by_name", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("batch_id", "target_key", name="uq_flyer_sends_batch_target"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_flyer_sends_flyer_id", "flyer_sends", ["flyer_id"])
    op.create_index("ix_flyer_sends_batch_id", "flyer_sends", ["batch_id"])
    op.create_index("ix_flyer_sends_status", "flyer_sends", ["status"])
    op.create_index("ix_flyer_sends_idempotency_key", "flyer_sends", ["idempotency_key"])


def downgrade() -> None:
    op.drop_table("flyer_sends")
    op.drop_table("flyers")
    with op.batch_alter_table("whatsapp_groups") as batch:
        batch.drop_column("marketing")
