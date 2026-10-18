"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
# Runs on SQLite AND PostgreSQL (store databases on the platform start from
# the PostgreSQL baseline at 0062; platform contract 11.1). Use portable
# operations: op.batch_alter_table() is fine (a no-op wrapper on PostgreSQL),
# recreate="always", PRAGMA, sqlite_master / sqlite_sequence, INSERT OR
# IGNORE and other SQLite-only SQL are not. Booleans: sa.true()/sa.false().
# Avoid new FOREIGN KEY constraints: SQLite never enforced them and the app
# relies on that (see scripts/pg/make_baseline.py).
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
