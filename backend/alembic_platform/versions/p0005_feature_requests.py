"""Feature requests and label-site hand-off codes (docs/LABEL_PRINTING_FEATURE.md 3.4, 4).

Additive only: two new tables in the platform database.

- ``feature_requests``: a store asks for a store feature (``label_printing``
  in v1); a master approves or declines. A partial unique index keeps one
  open (``requested``) row per store and feature.
- ``label_handoffs``: one-time 60-second codes that open the label site from
  the website already signed in. Only the code's sha256 is stored.

Revision ID: p0005
Revises: p0004
"""

from alembic import op
import sqlalchemy as sa

revision = "p0005"
down_revision = "p0004"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
BIG_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
OPEN = "status = 'requested'"


def upgrade() -> None:
    op.create_table(
        "feature_requests",
        sa.Column("id", BIG_ID, primary_key=True, autoincrement=True),
        sa.Column("store_id", BIG_ID, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("reviewed_by", sa.String(128)),
        sa.Column("reviewed_at", TS),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index(
        "uq_feature_requests_open",
        "feature_requests",
        ["store_id", "feature"],
        unique=True,
        postgresql_where=sa.text(OPEN),
        sqlite_where=sa.text(OPEN),
    )
    op.create_index("ix_feature_requests_status", "feature_requests", ["status", "created_at"])
    op.create_table(
        "label_handoffs",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("store_code", sa.String(24), nullable=False),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
    )
    op.create_index("ix_label_handoffs_expires", "label_handoffs", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_label_handoffs_expires", table_name="label_handoffs")
    op.drop_table("label_handoffs")
    op.drop_index("ix_feature_requests_status", table_name="feature_requests")
    op.drop_index("uq_feature_requests_open", table_name="feature_requests")
    op.drop_table("feature_requests")
