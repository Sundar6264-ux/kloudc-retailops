"""The read-only SQLite snapshot used by the store import and the parity
check (app/ops/store_import.py)."""

import hashlib
import sqlite3

import pytest

from app.ops.store_import import connect_ro, snapshot_sqlite, sqlite_counts


def _wal_db(path, rows=5):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE alembic_version (version_num TEXT)")
    con.execute("INSERT INTO alembic_version VALUES ('0059')")
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, qty REAL)")
    con.executemany("INSERT INTO orders (qty) VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    return con


def test_an_idle_wal_copy_opens_read_only(tmp_path):
    db = tmp_path / "idle.db"
    _wal_db(db).close()  # closing the last connection removes -wal and -shm
    assert not (tmp_path / "idle.db-wal").exists()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    con = connect_ro(db)
    assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 5
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO orders (qty) VALUES (1)")
    con.close()
    out = tmp_path / "snap.db"
    info = snapshot_sqlite(db, out)
    assert info["revision"] == "0059" and info["bytes"] > 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before, "the source is never written"
    assert sqlite3.connect(out).execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert sqlite_counts(out) == {"alembic_version": 1, "orders": 5}


def test_a_live_wal_database_is_snapshotted_with_its_unsaved_writes(tmp_path):
    db = tmp_path / "live.db"
    writer = _wal_db(db, rows=3)  # stays open: the rows live in the -wal file
    try:
        assert (tmp_path / "live.db-wal").exists()
        snapshot_sqlite(db, tmp_path / "snap.db")
        assert sqlite_counts(tmp_path / "snap.db")["orders"] == 3
    finally:
        writer.close()
