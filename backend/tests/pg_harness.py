"""PostgreSQL test harness (platform contract 18.1).

Used by tests/conftest.py before anything from `app` is imported, so this
module must never import `app`.

- One throwaway database per test run: `<prefix>_test_<pid>` (prefix from
  RETAILOPS_TEST_DB_PREFIX, default `so_a`), created from template0 with C
  collation (like every store database, contract 1.4 / 11.3) on the server
  in RETAILOPS_TEST_PG_URL (default: the local dev cluster). Dropped at the
  end of the run; databases of earlier runs whose process is gone (a killed
  run) are dropped at the start of the next one. Only names with this run's
  prefix are ever touched.
- The schema is built once per run by the store migrations (the PostgreSQL
  baseline + the chain), exactly like a new store database.
- Between tests every non-empty table is truncated and every used sequence
  restarted (`so_test_reset()`, a server-side function created here), which
  is much cheaper than dropping and recreating 60+ tables.
"""

from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_ADMIN_URL = "postgresql+psycopg://storeops@127.0.0.1:5433/postgres"
TEST_DB_NAME = re.compile(r"^[a-z][a-z0-9_]*_test_(\d+)$")

# Truncates the non-empty tables (one TRUNCATE, CASCADE for FKs) and restarts
# every sequence that handed out a value. alembic_version is kept.
RESET_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION so_test_reset() RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    r record;
    has_rows boolean;
    dirty text := '';
BEGIN
    FOR r IN
        SELECT quote_ident(schemaname) || '.' || quote_ident(tablename) AS t
        FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    LOOP
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM ' || r.t || ')' INTO has_rows;
        IF has_rows THEN
            dirty := dirty || CASE WHEN dirty = '' THEN '' ELSE ', ' END || r.t;
        END IF;
    END LOOP;
    IF dirty <> '' THEN
        EXECUTE 'TRUNCATE ' || dirty || ' CASCADE';
    END IF;
    FOR r IN
        SELECT quote_ident(schemaname) || '.' || quote_ident(sequencename) AS s
        FROM pg_sequences
        WHERE schemaname = 'public' AND last_value IS NOT NULL
    LOOP
        PERFORM setval(r.s, 1, false);
    END LOOP;
END
$fn$;
"""


def admin_url() -> str:
    return os.environ.get("RETAILOPS_TEST_PG_URL") or DEFAULT_ADMIN_URL


def db_prefix() -> str:
    prefix = os.environ.get("RETAILOPS_TEST_DB_PREFIX") or "so_a"
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", prefix):
        raise SystemExit(f"RETAILOPS_TEST_DB_PREFIX={prefix!r}: lower-case letters, digits and _ only")
    return prefix


def run_db_name(pid: Optional[int] = None) -> str:
    return f"{db_prefix()}_test_{pid if pid is not None else os.getpid()}"


def _libpq_conninfo(url: str, dbname: Optional[str] = None) -> str:
    """psycopg conninfo from a SQLAlchemy URL (postgresql+psycopg://...)."""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    parts = {
        "host": u.host or "127.0.0.1",
        "port": str(u.port or 5432),
        "user": u.username or "",
        "dbname": dbname or u.database or "postgres",
    }
    if u.password:
        parts["password"] = u.password
    return " ".join(f"{k}={v}" for k, v in parts.items() if v)


def app_url(dbname: str) -> str:
    """The SQLAlchemy URL the app uses for the run database."""
    from sqlalchemy.engine import make_url

    u = make_url(admin_url()).set(drivername="postgresql+psycopg", database=dbname)
    return u.render_as_string(hide_password=False)


def _admin_connect():
    import psycopg

    return psycopg.connect(_libpq_conninfo(admin_url()), autocommit=True, connect_timeout=5)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def drop_stale_databases(conn) -> list:
    """Drops this prefix's run databases whose test process is gone."""
    prefix = db_prefix() + "_test_"
    dropped = []
    rows = conn.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (prefix.replace("_", r"\_") + "%",))
    for (name,) in rows.fetchall():
        if not name.startswith(prefix):
            continue
        m = TEST_DB_NAME.match(name)
        if not m or int(m.group(1)) == os.getpid() or _pid_alive(int(m.group(1))):
            continue
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        dropped.append(name)
    return dropped


def create_run_database() -> str:
    """Creates this run's empty database and returns the app URL for it."""
    import psycopg

    name = run_db_name()
    try:
        conn = _admin_connect()
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"PostgreSQL test server not reachable at {admin_url()!r} ({exc}). Start it, set "
            "RETAILOPS_TEST_PG_URL, or run the quick SQLite harness with RETAILOPS_TEST_DB=sqlite."
        ) from None
    try:
        drop_stale_databases(conn)
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(
            f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
        )
    finally:
        conn.close()
    return app_url(name)


def drop_run_database(name: Optional[str] = None) -> None:
    name = name or run_db_name()
    if not name.startswith(db_prefix() + "_test_"):
        raise RuntimeError(f"refusing to drop {name!r}: not this run's database")
    conn = _admin_connect()
    try:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        conn.close()


def install_reset_function(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(RESET_FUNCTION_SQL)


# Every relation of the schema by oid: a test that drops and recreates a
# table (to imitate an older database) changes it, and the next test then gets
# the schema rebuilt from the migrations.
SCHEMA_SIGNATURE_SQL = """
SELECT md5(string_agg(c.oid::text || ':' || c.relname || ':' || c.relkind::text, ',' ORDER BY c.oid))
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
"""


def schema_signature(engine) -> str:
    with engine.connect() as conn:
        return conn.exec_driver_sql(SCHEMA_SIGNATURE_SQL).scalar() or ""


def drop_schema(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")
    engine.dispose()


def reset(engine) -> None:
    """Empties every table (keeps alembic_version) and restarts sequences.
    A lock wait means a session from an earlier test is still open; it is
    reported instead of hanging the run."""
    with engine.begin() as conn:
        conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        conn.exec_driver_sql("SELECT so_test_reset()")
