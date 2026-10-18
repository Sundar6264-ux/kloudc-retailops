"""Store database migrations from Python (platform contract 11.1).

    upgrade_store_db(url, revision="head") -> str   # the revision now applied
    current_store_revision(url) -> Optional[str]
    store_head_revision() -> str

Used by provisioning, `migrate-all` and the test harness. Runs the same
Alembic environment as the CLI (`backend/alembic/env.py`), in process, without
touching the logging configuration.

PostgreSQL store databases start from a frozen baseline at 0062
(`backend/alembic/pg_baseline/`): the helpers `pg_*` below are what env.py
calls for them - the per-database advisory lock, the baseline + seed on an
empty database (stamped 0062), and the guard that refuses to run any
revision <= 0062 (those were written for SQLite).
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import List, Optional

from sqlalchemy import MetaData, Table, create_engine, inspect, pool, text
from sqlalchemy.engine import Connection

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
ALEMBIC_DIR = BACKEND_DIR / "alembic"
PG_BASELINE_DIR = ALEMBIC_DIR / "pg_baseline"
PG_BASELINE_REVISION = "0062"
# pg_advisory_xact_lock(key1, key2): key1 marks "store schema migration",
# key2 is hashtext(current_database()).
_PG_MIGRATE_LOCK_KEY = 0x50_4F_4D_31  # "SOM1"

NOW_MARK = "__now__"
NOW_SECONDS_MARK = "__now_seconds__"


class BaselineError(RuntimeError):
    pass


def _alembic_config(url: Optional[str] = None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    if url is not None:
        cfg.attributes["db_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


def store_head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def current_store_revision(url: str) -> Optional[str]:
    from alembic.migration import MigrationContext

    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def upgrade_store_db(url: str, revision: str = "head") -> str:
    """Upgrades one store database to `revision` (an empty PostgreSQL
    database gets the baseline first). Returns the revision now applied."""
    from alembic import command

    command.upgrade(_alembic_config(url), revision)
    return current_store_revision(url) or ""


def downgrade_store_db(url: str, revision: str) -> str:
    """Downgrades one store database (on PostgreSQL never below the
    baseline). Returns the revision now applied."""
    from alembic import command

    command.downgrade(_alembic_config(url), revision)
    return current_store_revision(url) or ""


# --- PostgreSQL baseline (called from alembic/env.py) ---------------------------


def baseline_statements() -> List[str]:
    """The baseline DDL, one statement per item (the file separates
    statements with a blank line; comments are dropped)."""
    sql = (PG_BASELINE_DIR / f"{PG_BASELINE_REVISION}.sql").read_text()
    out = []
    for chunk in sql.split("\n\n"):
        lines = [ln for ln in chunk.splitlines() if ln.strip() and not ln.lstrip().startswith("--")]
        if lines:
            stmt = "\n".join(lines).strip()
            if not stmt.endswith(";"):
                raise BaselineError(f"baseline statement without ';': {stmt[:80]!r}")
            out.append(stmt[:-1])
    return out


def baseline_seed() -> dict:
    return json.loads((PG_BASELINE_DIR / f"{PG_BASELINE_REVISION}_seed.json").read_text())


def pg_lock_store_migrations(connection: Connection) -> None:
    """Transaction-scoped advisory lock: one store-schema migration per
    database at a time (released at commit / rollback)."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:k, hashtext(current_database()))"), {"k": _PG_MIGRATE_LOCK_KEY}
    )


def _user_tables(connection: Connection) -> List[str]:
    return [
        r[0]
        for r in connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename")
        )
    ]


def _seed_value(value, column_type, now: dt.datetime):
    if value == NOW_MARK:
        return now
    if value == NOW_SECONDS_MARK:
        return now.replace(microsecond=0)
    if value is not None and type(column_type).__name__ in ("TIMESTAMP", "DateTime"):
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=dt.timezone.utc)
    return value


def apply_baseline(connection: Connection, *, now: Optional[dt.datetime] = None) -> None:
    """Creates the 0062 schema and its seed rows in an EMPTY PostgreSQL
    database (inside the caller's transaction)."""
    for stmt in baseline_statements():
        connection.exec_driver_sql(stmt)
    now = now or dt.datetime.now(dt.timezone.utc)
    seed = baseline_seed()
    for item in seed["tables"]:
        table = Table(item["table"], MetaData(), autoload_with=connection)
        rows = [
            {col: _seed_value(row.get(col), table.c[col].type, now) for col in item["columns"]}
            for row in item["rows"]
        ]
        if rows:
            connection.execute(table.insert(), rows)
        _reset_identity(connection, item["table"])


def _reset_identity(connection: Connection, table: str) -> None:
    """After explicit ids: the next generated id follows the highest one."""
    for col in inspect(connection).get_columns(table):
        if not col.get("identity"):
            continue
        name = col["name"]
        top = connection.execute(text(f'SELECT max("{name}") FROM "{table}"')).scalar()
        if top is not None:
            connection.execute(
                text("SELECT setval(pg_get_serial_sequence(:t, :c), :v, true)"), {"t": table, "c": name, "v": top}
            )


def pg_apply_baseline_if_empty(connection: Connection) -> bool:
    """An empty database gets the baseline + seed and is stamped 0062.
    Returns True when it did. A database with tables but no alembic_version
    is not a store database: refused."""
    tables = _user_tables(connection)
    if "alembic_version" in tables:
        has_version = connection.execute(text("SELECT count(*) FROM alembic_version")).scalar()
        if has_version:
            return False
        tables = [t for t in tables if t != "alembic_version"]
    if tables:
        raise BaselineError(
            f"PostgreSQL database has {len(tables)} table(s) but no Alembic revision - not a store database "
            "(or a half-finished one); refusing to migrate it."
        )
    from alembic.migration import MigrationContext

    apply_baseline(connection)
    MigrationContext.configure(connection).stamp(_script_directory(), PG_BASELINE_REVISION)
    return True


def _script_directory():
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config())


def _pre_baseline_revisions() -> set:
    script = _script_directory()
    return {s.revision for s in script.iterate_revisions(PG_BASELINE_REVISION, "base")}


def pg_guard_revisions(migration_context) -> None:
    """Refuses, before anything runs, an upgrade step through a revision
    <= 0062 or a downgrade step of one (i.e. below the baseline)."""
    forbidden = _pre_baseline_revisions()
    original = migration_context._migrations_fn
    if original is None or getattr(original, "_so_guarded", False):
        return

    def guarded(heads, ctx):
        steps = list(original(heads, ctx) or [])
        for step in steps:
            script = getattr(step, "revision", None)
            rev = getattr(script, "revision", None)
            if rev is None or rev not in forbidden:
                continue
            if step.is_upgrade:
                raise BaselineError(
                    f"PostgreSQL store databases start from the {PG_BASELINE_REVISION} baseline; "
                    f"revision {rev} (written for SQLite) is never run on PostgreSQL."
                )
            raise BaselineError(
                f"Refusing to downgrade a PostgreSQL store database below the {PG_BASELINE_REVISION} baseline "
                f"(step {rev})."
            )
        return steps

    guarded._so_guarded = True
    migration_context._migrations_fn = guarded
