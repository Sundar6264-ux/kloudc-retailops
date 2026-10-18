"""The Alembic chain builds a fresh database, and the Team tables it creates
match the ORM models (a missed column would only show up in production)."""

import os
import pathlib
import sqlite3
import subprocess
import sys

from app.db import Base

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_upgrade_head_matches_models(tmp_path):
    db_file = tmp_path / "migrated.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)

    con = sqlite3.connect(db_file)
    try:
        for table in (
            "teams", "team_members", "team_changes", "team_media", "users",
            "task_templates", "tasks", "task_items", "task_events",
            "announcements", "announcement_audience", "announcement_recipients",
            "channels", "channel_members", "messages", "message_reports", "dm_blocks",
        ):
            migrated = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            modeled = {c.name for c in Base.metadata.tables[table].columns}
            assert migrated == modeled, f"{table}: migration {sorted(migrated)} vs model {sorted(modeled)}"
        teams = [r[0] for r in con.execute("SELECT name FROM teams ORDER BY id")]
        assert teams == ["Everyone", "Grocery", "Kitchen", "Managers"]
        for table in ("team_changes", "users", "team_media"):
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            assert "AUTOINCREMENT" in sql, f"{table} must never reuse ids"
        media_cols = {row[1]: row for row in con.execute("PRAGMA table_info(team_media)")}
        assert media_cols["uploaded_by_user_id"][3] == 0, "uploaded_by_user_id is nullable"
        # A retried "Post" must never create a second announcement.
        unique = [
            r[1] for r in con.execute("PRAGMA index_list(announcements)") if r[2] == 1
        ]
        cols = {
            name: [c[2] for c in con.execute(f"PRAGMA index_info({name})")] for name in unique
        }
        assert ["created_by_user_id", "client_uuid"] in cols.values(), cols
    finally:
        con.close()


def test_0029_keeps_users_and_never_reuses_ids(tmp_path):
    """On an existing database: every user row survives the rebuild, and the
    next id is above every user id still referenced anywhere - including one
    from an account deleted before the migration."""
    db_file = tmp_path / "existing.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *args: subprocess.run(  # noqa: E731
        [str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True
    )
    run("upgrade", "0028")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at, allowed_features, can_assign) "
        "VALUES (1, 'boss', 'h', 1, '2026-01-01', '[]', 0), (7, 'ravi', 'h', 0, '2026-01-01', '[\"team_chat\"]', 1)"
    )
    # A message from user 12, whose account was deleted earlier.
    con.execute(
        "INSERT INTO channels (id, kind, team_id, created_at) VALUES (1, 'dm', NULL, '2026-01-01')"
    )
    con.execute(
        "INSERT INTO messages (channel_id, sender_user_id, sender_name, body, client_uuid, created_at) "
        "VALUES (1, 12, 'leaver', 'hi', 'u1', '2026-01-01')"
    )
    con.commit()
    con.close()

    run("upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        rows = con.execute("SELECT id, username, is_admin, allowed_features, can_assign FROM users ORDER BY id").fetchall()
        assert rows == [(1, "boss", 1, "[]", 0), (7, "ravi", 0, '["team_chat"]', 1)]
        con.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES ('newhire', 'h', 0, '2026-02-01')"
        )
        new_id = con.execute("SELECT id FROM users WHERE username = 'newhire'").fetchone()[0]
        assert new_id == 13, "above the deleted user 12 still referenced by a message"
        con.commit()
    finally:
        con.close()

    run("downgrade", "0028")
    run("upgrade", "head")
