"""Shared helpers for moving a store database from SQLite to PostgreSQL
(copy_sqlite_to_pg.py, verify_copy.py, the parity harness's PostgreSQL mode).

Value rules (SQLite as the app writes it -> PostgreSQL column type):

    timestamptz       naive-UTC text "YYYY-MM-DD HH:MM:SS[.ffffff]" (UTCDateTime;
                      ISO 'T' and explicit offsets are accepted too) -> aware UTC
    date              "YYYY-MM-DD" -> date
    boolean           0 / 1 -> false / true
    double precision  int / float -> float
    integer           int (or an integral float) -> int
    varchar / text    str (a number stored in a text column -> its text)

Anything else (text in a number column, a timestamp that does not parse, a
NUL character, a BLOB) is a CopyValueError naming table, column and row:
the copy never guesses.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

UTC = dt.timezone.utc

# information_schema.columns.data_type -> our kind
PG_KINDS = {
    "integer": "int",
    "bigint": "int",
    "smallint": "int",
    "character varying": "text",
    "text": "text",
    "character": "text",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "date": "date",
    "boolean": "bool",
    "double precision": "float",
    "real": "float",
    "numeric": "float",
}


class CopyValueError(ValueError):
    pass


def libpq_conninfo(url: str) -> str:
    """psycopg conninfo from a SQLAlchemy URL (postgresql+psycopg://...) or a
    libpq URL (postgresql://...)."""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    if not u.drivername.startswith("postgresql"):
        raise SystemExit(f"not a PostgreSQL URL: {u.render_as_string(hide_password=True)}")
    parts = {
        "host": u.host or "127.0.0.1",
        "port": str(u.port or 5432),
        "user": u.username or "",
        "password": u.password or "",
        "dbname": u.database or "",
    }
    for key, value in u.query.items():
        parts[key] = value if isinstance(value, str) else value[0]
    return " ".join(f"{k}='{str(v)}'" for k, v in parts.items() if v)


def pg_column_kinds(cur, table: str) -> Dict[str, str]:
    rows = cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    out = {}
    for name, data_type in rows:
        if data_type not in PG_KINDS:
            raise SystemExit(f"{table}.{name}: no copy rule for PostgreSQL type {data_type!r}")
        out[name] = PG_KINDS[data_type]
    return out


_DB_TIME = re.compile(r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d(?::\d\d(?:\.\d{1,6})?)?)(Z|[+-]\d\d:?\d\d)?$")


def parse_db_time(value: str) -> dt.datetime:
    m = _DB_TIME.match(value.strip())
    if not m:
        raise ValueError(f"not a timestamp: {value!r}")
    text = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_pg(value: Any, kind: str, where: str) -> Any:
    """One SQLite value -> the Python value written to PostgreSQL."""
    if value is None:
        return None
    try:
        if kind == "text":
            if isinstance(value, bytes):
                raise ValueError("BLOB in a text column")
            if isinstance(value, float):
                value = repr(value)
            text = str(value)
            if "\x00" in text:
                raise ValueError("NUL character (PostgreSQL text cannot hold it)")
            return text
        if kind == "int":
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            raise ValueError(f"not an integer: {value!r}")
        if kind == "float":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            raise ValueError(f"not a number: {value!r}")
        if kind == "bool":
            if value in (0, 1):
                return bool(value)
            raise ValueError(f"not a boolean 0/1: {value!r}")
        if kind == "date":
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", value):
                return dt.date.fromisoformat(value)
            raise ValueError(f"not a date: {value!r}")
        if kind == "timestamptz":
            if isinstance(value, str):
                return parse_db_time(value)
            raise ValueError(f"not a timestamp: {value!r}")
        if kind == "timestamp":
            if isinstance(value, str):
                return parse_db_time(value).replace(tzinfo=None)
            raise ValueError(f"not a timestamp: {value!r}")
    except ValueError as exc:
        raise CopyValueError(f"{where}: {exc}") from None
    raise CopyValueError(f"{where}: unknown column kind {kind!r}")


# --- canonical forms (checksums) ------------------------------------------------


def canonical_from_sqlite(value: Any, kind: str) -> bytes:
    """The checksum form of a SQLite value, as it should read on PostgreSQL."""
    if value is None:
        return b"N;"
    return canonical_from_pg(to_pg(value, kind, "checksum"), kind)


def canonical_from_pg(value: Any, kind: str) -> bytes:
    if value is None:
        return b"N;"
    if kind in ("timestamptz", "timestamp"):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        text = value.strftime("%Y-%m-%d %H:%M:%S.%f")
    elif kind == "date":
        text = value.isoformat()
    elif kind == "bool":
        text = "1" if value else "0"
    elif kind == "float":
        text = repr(float(value))
    elif kind == "int":
        text = str(int(value))
    else:
        text = str(value)
    raw = text.encode("utf-8", "surrogatepass")
    return kind[:1].encode() + str(len(raw)).encode() + b":" + raw + b";"


def sqlite_style(value: Any, kind: str) -> Any:
    """A PostgreSQL value as the SQLite driver would return the same stored
    value (for the parity fingerprint): timestamps as naive-UTC text with
    microseconds, dates as ISO text, booleans as 0/1."""
    if value is None:
        return None
    if kind in ("timestamptz", "timestamp"):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    if kind == "date":
        return value.isoformat()
    if kind == "bool":
        return 1 if value else 0
    if kind == "float":
        return float(value)
    return value


# --- table facts ----------------------------------------------------------------


def sqlite_tables(con) -> List[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def sqlite_columns(con, table: str) -> List[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]


def sqlite_pk(con, table: str) -> List[str]:
    cols = sorted((r[5], r[1]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall() if r[5])
    return [c for _, c in cols]


def fk_order(con, tables: Sequence[str]) -> List[str]:
    """Tables ordered so that a referenced table comes before the ones
    referencing it (SQLite's declared FKs; cycles keep name order)."""
    deps = {t: set() for t in tables}
    for t in tables:
        for row in con.execute(f'PRAGMA foreign_key_list("{t}")').fetchall():
            ref = row[2]
            if ref in deps and ref != t:
                deps[t].add(ref)
    out: List[str] = []
    done = set()
    visiting = set()

    def visit(t: str) -> None:
        if t in done or t in visiting:
            return
        visiting.add(t)
        for d in sorted(deps[t]):
            visit(d)
        visiting.discard(t)
        done.add(t)
        out.append(t)

    for t in sorted(tables):
        visit(t)
    return out


def table_checksum_sqlite(con, table: str, kinds: Dict[str, str], order_by: Sequence[str]) -> Tuple[int, Dict[str, str]]:
    cols = list(kinds)
    hashes = [hashlib.sha256() for _ in cols]
    col_list = ", ".join(f'"{c}"' for c in cols)
    order = ", ".join(f'"{c}"' for c in order_by)
    count = 0
    cur = con.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order}')
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        for row in batch:
            count += 1
            for h, c, v in zip(hashes, cols, row):
                h.update(canonical_from_sqlite(v, kinds[c]))
    return count, {c: h.hexdigest() for c, h in zip(cols, hashes)}


def table_checksum_pg(pgcon, table: str, kinds: Dict[str, str], order_by: Sequence[str]) -> Tuple[int, Dict[str, str]]:
    cols = list(kinds)
    hashes = [hashlib.sha256() for _ in cols]
    col_list = ", ".join(f'"{c}"' for c in cols)
    # Bytewise text order, like SQLite's BINARY collation, whatever the
    # database's own collation.
    order = ", ".join(f'"{c}" COLLATE "C"' if kinds[c] == "text" else f'"{c}"' for c in order_by)
    count = 0
    with pgcon.cursor(name=f"cs_{table}") as cur:
        cur.itersize = 5000
        cur.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order}')
        for row in cur:
            count += 1
            for h, c, v in zip(hashes, cols, row):
                h.update(canonical_from_pg(v, kinds[c]))
    return count, {c: h.hexdigest() for c, h in zip(cols, hashes)}


def compare_databases(con, pgcon, tables: Optional[Sequence[str]] = None) -> List[str]:
    """Row counts + per-column checksums of every table (both ordered by the
    primary key). Returns the differences (empty = identical)."""
    problems: List[str] = []
    names = list(tables) if tables is not None else [t for t in sqlite_tables(con) if t != "alembic_version"]
    with pgcon.cursor() as cur:
        for table in names:
            kinds = pg_column_kinds(cur, table)
            if not kinds:
                problems.append(f"{table}: missing on PostgreSQL")
                continue
            src_cols = sqlite_columns(con, table)
            if set(src_cols) != set(kinds):
                problems.append(f"{table}: columns differ: sqlite-only {sorted(set(src_cols) - set(kinds))}, "
                                f"pg-only {sorted(set(kinds) - set(src_cols))}")
                continue
            kinds = {c: kinds[c] for c in src_cols}
            pk = sqlite_pk(con, table) or src_cols
            n_s, sums_s = table_checksum_sqlite(con, table, kinds, pk)
            n_p, sums_p = table_checksum_pg(pgcon, table, kinds, pk)
            if n_s != n_p:
                problems.append(f"{table}: {n_s} rows on SQLite, {n_p} on PostgreSQL")
            for c in src_cols:
                if sums_s[c] != sums_p[c]:
                    problems.append(f"{table}.{c}: checksum differs")
    return problems
