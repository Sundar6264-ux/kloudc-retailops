"""Compare a SQLite store database with its PostgreSQL copy (platform
contract 11.4): every table's row count and a checksum per column, both
sides ordered by the primary key, values in one canonical form
(sqlite_pg.py). Both databases are only read.

    cd backend
    .venv/bin/python scripts/pg/verify_copy.py --sqlite /path/to/store.db \\
        --pg-url postgresql+psycopg://storeops@127.0.0.1:5433/so_store_x

Prints DIFFERENCES: <n> and exits 0 only when there are none.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import List, Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sqlite_pg  # noqa: E402
from copy_sqlite_to_pg import open_sqlite_ro, sqlite_revision  # noqa: E402


def verify(sqlite_path: str, pg_url: str) -> List[str]:
    import psycopg

    con = open_sqlite_ro(sqlite_path)
    try:
        with psycopg.connect(sqlite_pg.libpq_conninfo(pg_url)) as pg:
            problems = sqlite_pg.compare_databases(con, pg)
            src_rev = sqlite_revision(con)
            try:
                row = pg.execute("SELECT version_num FROM alembic_version").fetchone()
            except Exception:  # noqa: BLE001 - reported below
                row = None
            pg_rev = row[0] if row else None
            if src_rev != pg_rev:
                problems.insert(0, f"alembic revision: SQLite {src_rev!r}, PostgreSQL {pg_rev!r}")
        return problems
    finally:
        con.close()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sqlite", required=True)
    p.add_argument("--pg-url", required=True)
    args = p.parse_args(argv)
    problems = verify(args.sqlite, args.pg_url)
    print(f"DIFFERENCES: {len(problems)}")
    for line in problems:
        print("  " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
