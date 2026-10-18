"""Public checklist link (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md).

- checklist_public_links: one logged-out link per checklist (slug + random
  token, on/off, name list or free text, hourly limit, audit columns,
  last_used_at). Nothing is seeded; an admin creates links.
- checklist_submissions.via_public_link / public_name (nullable): a row filled
  on the public page. Old rows stay NULL (= not via a link).

All additive; downgrade drops them.

Revision ID: 0041
Revises: 0040
"""

from alembic import op
import sqlalchemy as sa

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checklist_public_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("checklist_type", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("name_mode", sa.String(), nullable=False, server_default="list"),
        sa.Column("allowed_names_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("max_per_hour", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_by", sa.String(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("checklist_type", name="uq_checklist_public_links_type"),
        sa.UniqueConstraint("slug", name="uq_checklist_public_links_slug"),
        sa.UniqueConstraint("token", name="uq_checklist_public_links_token"),
    )
    op.add_column("checklist_submissions", sa.Column("via_public_link", sa.Boolean(), nullable=True))
    op.add_column("checklist_submissions", sa.Column("public_name", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("checklist_submissions") as batch:
        batch.drop_column("public_name")
        batch.drop_column("via_public_link")
    op.drop_table("checklist_public_links")
