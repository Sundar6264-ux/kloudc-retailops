"""Dialect helpers: the few places where SQLite and PostgreSQL SQL differ.

StoreOps runs on PostgreSQL in production (platform contract P2); SQLite is
kept for quick unit runs and for the M2 single-instance stores. App code
stays dialect-neutral by going through these helpers instead of importing
`sqlalchemy.dialects.sqlite` / `.postgresql` directly.

    dialect_insert(table, db)      INSERT with on_conflict_do_nothing /
                                   on_conflict_do_update(index_elements=...)
                                   on either dialect (same API on both)
    is_postgres(db)                True when the session / engine / connection
                                   talks to PostgreSQL
    sequence_last_value(db, table) the last id handed out by the table's
                                   auto-increment (sqlite_sequence on SQLite,
                                   the column's sequence on PostgreSQL), or
                                   None if none was handed out yet
    group_order(*keys)             ORDER BY that gives a GROUP BY's rows in the
                                   order SQLite returns them (keys ascending,
                                   bytewise; the NULL group first) - PostgreSQL
                                   may hash-aggregate and return any order
    fsum(column)                   SUM of a float column that adds up the same
                                   on both: SQLite's sum() is compensated
                                   (exact to the last bit for these values),
                                   PostgreSQL's float8 sum is not (2285.87 ->
                                   2285.8700000000013), so on PostgreSQL the
                                   values are summed as numeric and the total
                                   turned back into a float
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import Float, nulls_first, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Insert


def _dialect_name(bind_or_session: Any) -> str:
    """'sqlite', 'postgresql', ... for a Session, Engine, Connection or a
    dialect name given as a string."""
    if isinstance(bind_or_session, str):
        return bind_or_session
    if isinstance(bind_or_session, Session):
        bind = bind_or_session.get_bind()
    else:
        bind = bind_or_session
    if isinstance(bind, (Engine, Connection)):
        return bind.dialect.name
    dialect = getattr(bind, "dialect", None)
    if dialect is not None:
        return dialect.name
    raise TypeError(f"not a Session, Engine or Connection: {bind_or_session!r}")


def is_postgres(bind_or_session: Any) -> bool:
    return _dialect_name(bind_or_session) == "postgresql"


def is_sqlite(bind_or_session: Any) -> bool:
    return _dialect_name(bind_or_session) == "sqlite"


def dialect_insert(table: Any, bind_or_session: Any) -> Insert:
    """`insert(table)` for the dialect in use. Both returned constructs
    support `.on_conflict_do_nothing(index_elements=...)`,
    `.on_conflict_do_update(index_elements=..., set_=...)` and `.excluded`."""
    name = _dialect_name(bind_or_session)
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(table)
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(table)
    raise NotImplementedError(f"dialect_insert: unsupported database {name!r}")


def sequence_last_value(db: Session, table: str, column: str = "id") -> Optional[int]:
    """The last value the auto-increment of `table.column` handed out (even
    if every row has been deleted since), or None if it never handed one out.

    SQLite: `sqlite_sequence` (only for AUTOINCREMENT tables). PostgreSQL:
    the column's identity / serial sequence. Never raises for a missing
    table or sequence and never leaves a PostgreSQL transaction aborted
    (every lookup is a catalog query that cannot fail)."""
    name = _dialect_name(db)
    if name == "sqlite":
        has_seq = db.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'")
        ).first()
        if not has_seq:
            return None
        value = db.execute(text("SELECT seq FROM sqlite_sequence WHERE name = :t"), {"t": table}).scalar()
        return int(value) if value is not None else None
    if name == "postgresql":
        # to_regclass() is NULL (not an error) for a missing table, and
        # pg_get_serial_sequence() then NULL too; pg_sequences.last_value is
        # NULL until nextval() was first called.
        value = db.execute(
            text(
                "SELECT s.last_value FROM pg_sequences s "
                "WHERE to_regclass(:t) IS NOT NULL "
                "AND quote_ident(s.schemaname) || '.' || quote_ident(s.sequencename) = "
                "pg_get_serial_sequence(CAST(to_regclass(:t) AS text), :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
        return int(value) if value is not None else None
    raise NotImplementedError(f"sequence_last_value: unsupported database {name!r}")


class fsum(FunctionElement):
    """`fsum(col)`: SUM of a float column, dialect-exact (module docstring).
    NULL for no rows / only NULLs, like sum()."""

    type = Float()
    inherit_cache = True
    name = "fsum"


@compiles(fsum)
def _fsum_default(element, compiler, **kw):
    return "sum(%s)" % compiler.process(element.clauses, **kw)


@compiles(fsum, "postgresql")
def _fsum_postgresql(element, compiler, **kw):
    return "CAST(sum(CAST(%s AS NUMERIC)) AS DOUBLE PRECISION)" % compiler.process(element.clauses, **kw)


def group_order(*keys) -> list:
    """`.group_by(*keys).order_by(*group_order(*keys))`: SQLite returns a
    GROUP BY's rows sorted by the group keys (NULL first); code that builds
    a dict or a list from them in that order keeps working on PostgreSQL.
    (Store databases use the C collation, so text keys sort bytewise like
    SQLite's BINARY.)"""
    return [nulls_first(k) for k in keys]


def force_utc_sessions(engine: Engine) -> Engine:
    """Every new PostgreSQL connection of `engine` runs ``SET TIME ZONE 'UTC'``
    (contract 5.2). The ``options=-c timezone=UTC`` connect argument alone is
    not enough: libpq's PGTZ environment variable (CI sets one on purpose)
    overrides it. Timestamps are stored UTC either way (UTCDateTime); this
    keeps raw SQL date functions and timestamptz values read back in UTC."""
    from sqlalchemy import event

    if engine.dialect.name != "postgresql":
        return engine

    @event.listens_for(engine, "connect")
    def _utc(dbapi_connection, _record):  # pragma: no cover - exercised on PostgreSQL
        cur = dbapi_connection.cursor()
        try:
            cur.execute("SET TIME ZONE 'UTC'")
        finally:
            cur.close()
        dbapi_connection.commit()

    return engine
