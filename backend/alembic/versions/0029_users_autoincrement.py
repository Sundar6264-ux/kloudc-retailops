"""Never reuse user (or team media) ids: rebuild `users` and `team_media`
with SQLite AUTOINCREMENT, and let team_media.uploaded_by_user_id be NULL.

Without AUTOINCREMENT, SQLite gives a new row max(id)+1 - so deleting the
newest account and creating another handed the new person the old id, and
with it everything still keyed by that id (DMs, tasks, uploads). The
sequence is seeded above every user id still referenced anywhere in the
database, not just max(users.id), so ids of accounts deleted before this
migration are never handed out again either.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _highest_user_id_referenced(bind) -> int:
    """max over users.id and every *user_id column in every table."""
    highest = bind.execute(sa.text("SELECT COALESCE(MAX(id), 0) FROM users")).scalar() or 0
    tables = [
        r[0]
        for r in bind.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
    ]
    for table in tables:
        for col in bind.execute(sa.text(f'PRAGMA table_info("{table}")')):
            name = col[1]
            if name == "user_id" or name.endswith("_user_id"):
                value = bind.execute(
                    sa.text(f'SELECT MAX(CAST("{name}" AS INTEGER)) FROM "{table}"')
                ).scalar()
                if value is not None and int(value) > highest:
                    highest = int(value)
    return int(highest)


def _seed_sequence(bind, table: str, value: int) -> None:
    current = bind.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = :t"), {"t": table}).scalar()
    if current is None:
        bind.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES (:t, :v)"), {"t": table, "v": value})
    elif int(current) < value:
        bind.execute(sa.text("UPDATE sqlite_sequence SET seq = :v WHERE name = :t"), {"t": table, "v": value})


def upgrade() -> None:
    bind = op.get_bind()
    highest = _highest_user_id_referenced(bind)

    with op.batch_alter_table("users", recreate="always", table_kwargs={"sqlite_autoincrement": True}):
        pass
    with op.batch_alter_table(
        "team_media", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.alter_column("uploaded_by_user_id", existing_type=sa.Integer(), nullable=True)

    _seed_sequence(bind, "users", highest)
    media_max = bind.execute(sa.text("SELECT COALESCE(MAX(id), 0) FROM team_media")).scalar() or 0
    _seed_sequence(bind, "team_media", int(media_max))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE team_media SET uploaded_by_user_id = 0 WHERE uploaded_by_user_id IS NULL"))
    with op.batch_alter_table(
        "team_media", recreate="always", table_kwargs={"sqlite_autoincrement": False}
    ) as batch:
        batch.alter_column("uploaded_by_user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("users", recreate="always", table_kwargs={"sqlite_autoincrement": False}):
        pass
