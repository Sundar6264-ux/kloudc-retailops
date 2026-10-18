"""Copy a SQLite store database into a PostgreSQL store database
(platform contract 11.4).

    cd backend
    .venv/bin/python scripts/pg/copy_sqlite_to_pg.py --sqlite /path/to/store.db \\
        --pg-url postgresql+psycopg://storeops@127.0.0.1:5433/so_store_x

- The SQLite file is opened READ-ONLY (never written; a live WAL database is
  read consistently inside one read transaction).
- The source must be at revision 0062 or later (PostgreSQL store databases
  start from the 0062 baseline). Upgrade a COPY of an older file first:
  `DATABASE_URL=sqlite:////path/copy.db alembic upgrade head`.
- The target must be empty: either a database with no tables (it is migrated
  to the source's revision here: baseline + chain), or one already at the
  source's revision whose tables hold no rows except the baseline's own seed
  rows (those are removed: the source's rows replace them). Anything else is
  refused - the tool never merges into or overwrites store data.
- Tables are copied in foreign-key order with COPY, in one transaction (all
  or nothing), in SQLite rowid order. Values are converted strictly
  (sqlite_pg.py); a value that does not fit its PostgreSQL column stops the
  copy with the table, column and row named.
- Identity sequences are set to max(id), so new rows continue after the
  copied ones.
- Afterwards every table's row count and per-column checksums are compared
  (verify_copy.py does the same on its own). Exit 0 only when identical.
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
import time
from typing import List, Optional

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))

import sqlite_pg  # noqa: E402

BASELINE = "0062"


def open_sqlite_ro(path: str) -> sqlite3.Connection:
    p = pathlib.Path(path).resolve()
    if not p.is_file():
        raise SystemExit(f"SQLite database not found: {p}")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    # One read transaction for the whole copy: a consistent snapshot even if
    # the file is being written (WAL).
    con.isolation_level = None
    con.execute("BEGIN")
    return con


def sqlite_revision(con: sqlite3.Connection) -> Optional[str]:
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _rev_at_least_baseline(rev: str) -> bool:
    from app.migrate import _pre_baseline_revisions

    pre = _pre_baseline_revisions()
    return rev == BASELINE or rev not in pre


def _pg_tables(cur) -> List[str]:
    return [r[0] for r in cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()").fetchall()]


def prepare_target(pg_url: str, revision: str, log=print) -> None:
    """Empty target -> migrated to `revision`; migrated target -> must hold
    only the baseline seed rows (removed); else refused."""
    import psycopg

    from app.migrate import baseline_seed, current_store_revision, upgrade_store_db

    with psycopg.connect(sqlite_pg.libpq_conninfo(pg_url)) as pg:
        tables = _pg_tables(pg.cursor())
    if not tables:
        log(f"Target is empty: migrating it to {revision} (baseline + chain)")
        upgrade_store_db(pg_url, revision)
    else:
        current = current_store_revision(pg_url)
        if current != revision:
            raise SystemExit(f"Target is at revision {current!r}, the source at {revision!r}: refusing.")
    seed = {t["table"]: t for t in baseline_seed()["tables"]}
    with psycopg.connect(sqlite_pg.libpq_conninfo(pg_url), autocommit=True) as pg:
        cur = pg.cursor()
        busy = []
        for t in _pg_tables(cur):
            if t == "alembic_version":
                continue
            n = cur.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            if n and not _only_seed_rows(cur, seed.get(t)):
                busy.append(f"{t} ({n} rows)")
        if busy:
            raise SystemExit("Target is not empty, refusing to copy into it: " + ", ".join(sorted(busy)))


def _only_seed_rows(cur, seed_table) -> bool:
    """True when the table holds exactly the baseline's seed rows (same
    values; the seed's "now" timestamps are not compared)."""
    if seed_table is None:
        return False
    from app.migrate import NOW_MARK, NOW_SECONDS_MARK

    cols = [c for c in seed_table["columns"] if not any(
        r.get(c) in (NOW_MARK, NOW_SECONDS_MARK) for r in seed_table["rows"]
    )]
    kinds = sqlite_pg.pg_column_kinds(cur, seed_table["table"])
    col_list = ", ".join(f'"{c}"' for c in cols)
    rows = cur.execute(f'SELECT {col_list} FROM "{seed_table["table"]}" ORDER BY 1').fetchall()
    expected = sorted(
        (tuple(sqlite_pg.to_pg(r.get(c), kinds[c], "seed") for c in cols) for r in seed_table["rows"]),
        key=lambda row: row[0],
    )
    return [tuple(r) for r in rows] == expected


def copy(sqlite_path: str, pg_url: str, *, verify: bool = True, log=print) -> List[str]:
    import psycopg

    con = open_sqlite_ro(sqlite_path)
    try:
        rev = sqlite_revision(con)
        if rev is None:
            raise SystemExit("The SQLite file has no Alembic revision: not a store database.")
        if not _rev_at_least_baseline(rev):
            raise SystemExit(
                f"The SQLite file is at {rev}, before the PostgreSQL baseline {BASELINE}. Upgrade a COPY of it "
                "first (DATABASE_URL=sqlite:////path/copy.db alembic upgrade head)."
            )
        prepare_target(pg_url, rev, log=log)
        tables = [t for t in sqlite_pg.sqlite_tables(con) if t != "alembic_version"]
        order = sqlite_pg.fk_order(con, tables)
        started = time.time()
        total = 0
        with psycopg.connect(sqlite_pg.libpq_conninfo(pg_url)) as pg:
            with pg.transaction():
                cur = pg.cursor()
                cur.execute("SET LOCAL timezone = 'UTC'")
                pg_tables = set(_pg_tables(cur)) - {"alembic_version"}
                missing = sorted(set(order) - pg_tables)
                extra = sorted(pg_tables - set(order))
                if missing or extra:
                    raise SystemExit(f"Table sets differ: only in SQLite {missing}, only in PostgreSQL {extra}")
                # Baseline seed rows go; the source's rows replace them.
                cur.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in order))
                for table in order:
                    kinds = sqlite_pg.pg_column_kinds(cur, table)
                    cols = sqlite_pg.sqlite_columns(con, table)
                    if set(cols) != set(kinds):
                        raise SystemExit(
                            f"{table}: columns differ (SQLite {sorted(set(cols) - set(kinds))} vs "
                            f"PostgreSQL {sorted(set(kinds) - set(cols))})"
                        )
                    n = _copy_table(con, cur, table, cols, kinds)
                    total += n
                    log(f"  {table}: {n} rows")
                _reset_sequences(cur, order)
        log(f"Copied {total} rows in {len(order)} tables ({time.time() - started:.1f} s)")
        if not verify:
            return []
        log("Verifying (row counts + per-column checksums)")
        with psycopg.connect(sqlite_pg.libpq_conninfo(pg_url)) as pg:
            problems = sqlite_pg.compare_databases(con, pg, order)
        return problems
    finally:
        con.close()


def _copy_table(con, cur, table: str, cols: List[str], kinds) -> int:
    col_list = ", ".join(f'"{c}"' for c in cols)
    pk = sqlite_pg.sqlite_pk(con, table)
    has_rowid = True
    try:
        con.execute(f'SELECT rowid FROM "{table}" LIMIT 1').fetchall()
    except sqlite3.OperationalError:
        has_rowid = False
    order = "rowid" if has_rowid else ", ".join(f'"{c}"' for c in (pk or cols))
    src = con.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order}')
    n = 0
    with cur.copy(f'COPY "{table}" ({col_list}) FROM STDIN') as cp:
        while True:
            batch = src.fetchmany(5000)
            if not batch:
                break
            for row in batch:
                n += 1
                where = f"{table} row {n}"
                cp.write_row([sqlite_pg.to_pg(v, kinds[c], f"{where}, {c}") for c, v in zip(cols, row)])
    return n


def _reset_sequences(cur, tables: List[str]) -> None:
    rows = cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND (is_identity = 'YES' OR column_default LIKE 'nextval(%%')"
    ).fetchall()
    for table, column in rows:
        if table not in tables:
            continue
        top = cur.execute(f'SELECT max("{column}") FROM "{table}"').fetchone()[0]
        seq = cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f'"{table}"', column)).fetchone()[0]
        if seq is None:
            continue
        if top is None:
            cur.execute("SELECT setval(%s, 1, false)", (seq,))
        else:
            cur.execute("SELECT setval(%s, %s, true)", (seq, top))


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sqlite", required=True, help="Source SQLite store database (opened read-only)")
    p.add_argument("--pg-url", required=True, help="Target PostgreSQL database URL (empty)")
    p.add_argument("--no-verify", action="store_true", help="Skip the checksum comparison")
    args = p.parse_args(argv)
    problems = copy(args.sqlite, args.pg_url, verify=not args.no_verify)
    if problems:
        print(f"DIFFERENCES: {len(problems)}")
        for line in problems:
            print("  " + line)
        return 1
    if not args.no_verify:
        print("DIFFERENCES: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
