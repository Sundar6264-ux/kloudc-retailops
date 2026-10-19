"""checklist builder - checklists move from code into the database so admins
can create and edit them on the website (docs/CHECKLIST_BUILDER_CONTRACT.md).

- checklist_definitions: one row per checklist (slug checklist_type, title,
  short_name, description, active, sort_order, settings JSON, current_version,
  an unpublished draft, created/updated at+by).
- checklist_definition_versions: immutable published versions (schema JSON).
- checklist_submissions.definition_version (nullable): the version a
  submission was made against. Existing rows stay NULL = version 1.

Seeds the three built-in checklists (app/services/checklist_builtin.py) as
their version 1, exactly as served before. The Market Kitchen FSA closing
also gets version 2 = the same form without the "Chef Name" question (owner:
"names with user only, no dropdown - like other checklists"); every existing
FSA submission stays on version 1 and still shows its chef.

Unique constraints are inline on create_table (SQLite can't ALTER them in).
Downgrade drops the tables and the column (the checklists then come from the
code again, as before).

Revision ID: 0039
Revises: 0038
"""

import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    definitions = op.create_table(
        "checklist_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("checklist_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("draft_json", sa.Text(), nullable=True),
        sa.Column("draft_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.UniqueConstraint("checklist_type", name="uq_checklist_definitions_type"),
    )
    versions = op.create_table(
        "checklist_definition_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("definition_id", sa.Integer(), sa.ForeignKey("checklist_definitions.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_json", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.UniqueConstraint("definition_id", "version", name="uq_checklist_definition_versions_def_version"),
    )
    op.create_index(
        "ix_checklist_definition_versions_definition_id", "checklist_definition_versions", ["definition_id"]
    )
    op.add_column("checklist_submissions", sa.Column("definition_version", sa.Integer(), nullable=True))

    # Seed (frozen data module - never edited, so this migration stays stable).
    from app.services.checklist_builtin import seed_rows

    conn = op.get_bind()
    now = dt.datetime.now(dt.timezone.utc)
    for row in seed_rows(now):
        result = conn.execute(definitions.insert().values(**row["definition"]))
        definition_id = result.inserted_primary_key[0]
        for version in row["versions"]:
            conn.execute(versions.insert().values(definition_id=definition_id, **version))


def downgrade() -> None:
    with op.batch_alter_table("checklist_submissions") as batch:
        batch.drop_column("definition_version")
    op.drop_index("ix_checklist_definition_versions_definition_id", table_name="checklist_definition_versions")
    op.drop_table("checklist_definition_versions")
    op.drop_table("checklist_definitions")
