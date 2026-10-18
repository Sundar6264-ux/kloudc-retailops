"""Database fingerprint for the parity harness.

Per table: the row count and, per column, a SHA-256 of that column's values
in row order. Per column (not per row) so that an additive migration - a new
column, a new table - leaves every existing column's checksum unchanged and
the diff tool can tell "added" (allowed in M1) from "changed" (not allowed).

Row order is rowid order (stable for a copy of the same file); a table
without a rowid is ordered by every column.

Skipped, with reasons:
* sqlite_* internal tables (sqlite_sequence, sqlite_stat1): bookkeeping that
  any new table or ANALYZE changes; not business data.
* alembic_version: the migration head is expected to move with every
  migration batch; it is recorded separately in the recording's meta.

`pg_fingerprint(url)` is the same fingerprint of a PostgreSQL store
database, in SQLite's terms (platform track A, the parity run on
PostgreSQL): every value is turned into what the SQLite driver returns for
the same stored value (timestamps as naive-UTC text with microseconds,
dates as ISO text, booleans 0/1; scripts/pg/sqlite_pg.py), and rows are in
primary-key order for an integer key (= SQLite's rowid order) or physical
order otherwise (a fresh copy is written in rowid order).
"""

import hashlib
import sqlite3
from typing import Dict, List

SKIP_TABLES = {"alembic_version"}


def _table_names(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _has_rowid(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f'SELECT rowid FROM "{table}" LIMIT 1').fetchall()
        return True
    except sqlite3.OperationalError:
        return False


def _encode(value) -> bytes:
    # Type-tagged so 1, 1.0, "1" and b"1" never hash alike.
    if value is None:
        return b"N;"
    if isinstance(value, bytes):
        return b"B" + str(len(value)).encode() + b":" + value + b";"
    if isinstance(value, float):
        return b"F" + repr(value).encode() + b";"
    if isinstance(value, int):
        return b"I" + str(value).encode() + b";"
    text = str(value).encode("utf-8", "surrogatepass")
    return b"T" + str(len(text)).encode() + b":" + text + b";"


def fingerprint(db_path: str) -> Dict:
    """{"tables": {name: {"rows": n, "columns": {col: sha256}}}} for a
    database file, opened read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables: Dict[str, Dict] = {}
        for table in _table_names(conn):
            cols = _columns(conn, table)
            order = "rowid" if _has_rowid(conn, table) else ", ".join(f'"{c}"' for c in cols)
            col_list = ", ".join(f'"{c}"' for c in cols)
            hashes = [hashlib.sha256() for _ in cols]
            count = 0
            cur = conn.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order}')
            while True:
                batch = cur.fetchmany(5000)
                if not batch:
                    break
                for row in batch:
                    count += 1
                    for h, value in zip(hashes, row):
                        h.update(_encode(value))
            tables[table] = {
                "rows": count,
                "columns": {c: h.hexdigest() for c, h in zip(cols, hashes)},
            }
        return {"tables": tables}
    finally:
        conn.close()


def alembic_head(db_path: str):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def pg_fingerprint(url: str) -> Dict:
    """fingerprint() of a PostgreSQL database, in SQLite's terms (see the
    module docstring). Only reads."""
    import pathlib
    import sys

    pg_dir = pathlib.Path(__file__).resolve().parents[1] / "pg"
    if str(pg_dir) not in sys.path:
        sys.path.insert(0, str(pg_dir))
    import psycopg
    import sqlite_pg

    tables: Dict[str, Dict] = {}
    with psycopg.connect(sqlite_pg.libpq_conninfo(url)) as conn:
        conn.read_only = True
        cur = conn.cursor()
        names = [
            r[0]
            for r in cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename"
            ).fetchall()
            if r[0] not in SKIP_TABLES
        ]
        for table in names:
            kinds = sqlite_pg.pg_column_kinds(cur, table)
            cols = list(kinds)
            pk = [
                r[0]
                for r in cur.execute(
                    "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = %s::regclass AND i.indisprimary",
                    (f'"{table}"',),
                ).fetchall()
            ]
            order = f'"{pk[0]}"' if len(pk) == 1 and kinds.get(pk[0]) == "int" else "ctid"
            col_list = ", ".join(f'"{c}"' for c in cols)
            hashes = [hashlib.sha256() for _ in cols]
            count = 0
            with conn.cursor(name=f"fp_{table}") as named:
                named.itersize = 5000
                named.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order}')
                for row in named:
                    count += 1
                    for h, c, value in zip(hashes, cols, row):
                        h.update(_encode(sqlite_pg.sqlite_style(value, kinds[c])))
            tables[table] = {"rows": count, "columns": {c: h.hexdigest() for c, h in zip(cols, hashes)}}
    return {"tables": tables}


def pg_alembic_head(url: str):
    import pathlib
    import sys

    pg_dir = pathlib.Path(__file__).resolve().parents[1] / "pg"
    if str(pg_dir) not in sys.path:
        sys.path.insert(0, str(pg_dir))
    import psycopg
    import sqlite_pg

    with psycopg.connect(sqlite_pg.libpq_conninfo(url)) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None
