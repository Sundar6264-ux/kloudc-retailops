"""PostgreSQL port (platform contract 11.1, 11.4, 18.2 track A).

Dialect helpers and UTCDateTime run on whichever database the suite uses;
the baseline, schema-parity, migration-guard and copy-tool tests need
PostgreSQL and are skipped on the quick SQLite run.
"""

import datetime as dt
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, pool, select, text

from app.db import SessionLocal, engine
from app.dbcompat import dialect_insert, is_postgres, sequence_last_value
from app.models import InventorySnapshot, JobRun
from app.team.models import TeamChange
from app.timeutil import utcnow

from tests import pg_harness

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ON_PG = engine.dialect.name == "postgresql"
pg_only = pytest.mark.skipif(not ON_PG, reason="PostgreSQL only (RETAILOPS_TEST_DB=postgres)")


# --- dbcompat -----------------------------------------------------------------------------


def test_dialect_insert_upserts_on_both_dialects(db):
    day = dt.date(2026, 10, 1)
    for qty in (5.0, 7.0):
        stmt = dialect_insert(InventorySnapshot, db).values(snapshot_date=day, item_id="X1", name="Rice", inventory_quantity=qty)
        stmt = stmt.on_conflict_do_update(
            index_elements=[InventorySnapshot.snapshot_date, InventorySnapshot.item_id],
            set_={"inventory_quantity": stmt.excluded.inventory_quantity},
        )
        db.execute(stmt)
    db.commit()
    rows = db.execute(select(InventorySnapshot.inventory_quantity)).scalars().all()
    assert rows == [7.0]

    stmt = dialect_insert(InventorySnapshot, db).values(snapshot_date=day, item_id="X1", name="Other", inventory_quantity=1.0)
    db.execute(stmt.on_conflict_do_nothing(index_elements=["snapshot_date", "item_id"]))
    db.commit()
    assert db.execute(select(InventorySnapshot.name)).scalars().all() == ["Rice"]


def test_is_postgres_matches_the_engine(db):
    assert is_postgres(db) == ON_PG
    assert is_postgres(engine) == ON_PG


def test_sequence_last_value_survives_deleting_every_row(db):
    from app.team.changes import current_cursor

    assert current_cursor(db) == 0
    for _ in range(3):
        db.add(TeamChange(entity_type="task", entity_id=1, action="update", created_at=utcnow()))
    db.commit()
    assert sequence_last_value(db, "team_changes", "seq") == 3
    assert current_cursor(db) == 3
    db.query(TeamChange).delete()
    db.commit()
    assert sequence_last_value(db, "team_changes", "seq") == 3
    assert current_cursor(db) == 3, "the cursor never goes back after a prune"
    assert sequence_last_value(db, "no_such_table") is None


def _order_row(i, guid, qty):
    from app.models import Order

    return Order(
        toast_order_guid=f"o{i}", toast_check_guid=f"c{i}", toast_selection_guid=f"s{i}",
        business_date=dt.date(2026, 10, 1), item_guid=guid, quantity=qty,
    )


def test_fsum_adds_floats_like_sqlite(db):
    """SQLite's sum() (3.43+) is compensated; PostgreSQL's float8 sum is not
    (0.1 + 0.2 + 0.3 = 0.6000000000000001). fsum sums exactly on PostgreSQL."""
    import sqlite3 as _sqlite3

    from app.dbcompat import fsum
    from app.models import Order

    db.add_all([_order_row(i, "x", q) for i, q in enumerate((0.1, 0.2, 0.3))])
    db.commit()
    total = db.execute(select(fsum(Order.quantity))).scalar()
    if ON_PG or _sqlite3.sqlite_version_info >= (3, 43):
        assert total == 0.6
    assert db.execute(select(fsum(Order.quantity)).where(Order.item_guid == "none")).scalar() is None


def test_group_order_returns_groups_like_sqlite(db):
    from sqlalchemy import func

    from app.dbcompat import group_order
    from app.models import Order

    db.add_all([_order_row(1, "b", 1), _order_row(2, None, 1), _order_row(3, "a", 1), _order_row(4, "B", 1)])
    db.commit()
    rows = db.execute(
        select(Order.item_guid, func.count()).group_by(Order.item_guid).order_by(*group_order(Order.item_guid))
    ).all()
    # NULL group first, then bytewise ("B" < "a" < "b"), as SQLite returns them.
    assert [r[0] for r in rows] == [None, "B", "a", "b"]


# --- UTCDateTime ------------------------------------------------------------------------------


def test_utc_datetime_round_trip_aware_and_naive(db):
    aware = dt.datetime(2026, 3, 8, 6, 30, 15, 123456, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    naive_utc = dt.datetime(2026, 3, 8, 11, 30, 15, 123456)
    for value in (aware, naive_utc):
        run = JobRun(job_name="t", started_at=value, status="ok")
        db.add(run)
        db.commit()
        db.expire_all()
        got = db.get(JobRun, run.id).started_at
        assert got == dt.datetime(2026, 3, 8, 11, 30, 15, 123456, tzinfo=dt.timezone.utc)
        assert got.tzinfo is dt.timezone.utc


@pg_only
def test_utc_datetime_ignores_a_non_utc_session_time_zone():
    """The stored instant never depends on the connection's TimeZone."""
    when = dt.datetime(2026, 7, 4, 2, 0, tzinfo=dt.timezone.utc)
    s1 = SessionLocal()
    try:
        s1.execute(text("SET TIME ZONE 'Asia/Kolkata'"))
        run = JobRun(job_name="tz", started_at=when, status="ok")
        s1.add(run)
        s1.flush()
        run_id = run.id
        s1.commit()
    finally:
        s1.close()
    s2 = SessionLocal()
    try:
        s2.execute(text("SET TIME ZONE 'America/Los_Angeles'"))
        assert s2.get(JobRun, run_id).started_at == when
        utc_text = s2.execute(
            text("SELECT to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') FROM job_runs WHERE id = :i"),
            {"i": run_id},
        ).scalar()
        assert utc_text == "2026-07-04 02:00"
        s2.rollback()
    finally:
        s2.close()
    engine.dispose()  # pooled connections must not keep the odd zones


# --- schema: PostgreSQL baseline + chain == SQLite chain --------------------------------------


def _sqlite_chain_db(tmp_path) -> pathlib.Path:
    db_file = tmp_path / "chain.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
    return db_file


def _type_family(t) -> str:
    name = type(t).__name__.upper()
    for family, names in (
        ("int", ("INTEGER", "BIGINT", "SMALLINT")),
        ("float", ("FLOAT", "DOUBLE_PRECISION", "REAL", "DOUBLE", "NUMERIC")),
        ("text", ("VARCHAR", "TEXT", "STRING", "CHAR")),
        ("timestamp", ("DATETIME", "TIMESTAMP")),
        ("date", ("DATE",)),
        ("bool", ("BOOLEAN",)),
    ):
        if name in names:
            return family
    return name


def _norm_default(default, family):
    if default is None:
        return None
    raw = str(default).strip()
    while raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    raw = raw.split("::")[0].strip("'")
    if family == "bool":
        return {"0": "false", "1": "true"}.get(raw, raw)
    if family in ("int", "float"):
        return str(float(raw))
    return raw


def _schema(bind):
    insp = inspect(bind)
    out = {}
    for table in sorted(insp.get_table_names()):
        if table == "alembic_version":
            continue
        cols = {}
        for c in insp.get_columns(table):
            family = _type_family(c["type"])
            cols[c["name"]] = (family, bool(c["nullable"]), _norm_default(c.get("default"), family))
        out[table] = {
            "columns": cols,
            "pk": tuple(insp.get_pk_constraint(table)["constrained_columns"]),
            "unique": sorted(tuple(u["column_names"]) for u in insp.get_unique_constraints(table)),
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                for i in insp.get_indexes(table)
                # PostgreSQL lists a unique constraint's own index too.
                if not i.get("duplicates_constraint")
            ),
            "checks": sorted((ck.get("name") or "") for ck in insp.get_check_constraints(table)),
            "fks": len(insp.get_foreign_keys(table)),
        }
    return out


@pg_only
def test_postgres_schema_matches_the_sqlite_chain(tmp_path):
    sqlite_engine = create_engine(f"sqlite:///{_sqlite_chain_db(tmp_path)}")
    try:
        lite = _schema(sqlite_engine)
    finally:
        sqlite_engine.dispose()
    pg = _schema(engine)
    assert sorted(lite) == sorted(pg)
    for table in lite:
        a, b = lite[table], pg[table]
        assert a["columns"] == b["columns"], table
        assert a["pk"] == b["pk"], table
        assert a["unique"] == b["unique"], table
        assert a["indexes"] == b["indexes"], table
        assert [n for n in a["checks"] if n] == [n for n in b["checks"] if n], table
        assert len(a["checks"]) == len(b["checks"]), table
        # The one difference on purpose: no FK constraints on PostgreSQL
        # (SQLite never enforced them; scripts/pg/make_baseline.py).
        assert b["fks"] == 0, table


def _model_drift(bind):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.insights_models  # noqa: F401
    import app.issue_lead_models  # noqa: F401
    import app.item_vendor_models  # noqa: F401
    import app.notification_models  # noqa: F401
    import app.po_send_models  # noqa: F401
    import app.setting_models  # noqa: F401
    import app.store.identity  # noqa: F401
    import app.team.chat_models  # noqa: F401
    import app.team.tasks_models  # noqa: F401,F811
    from app.db import Base

    with bind.connect() as conn:
        mc = MigrationContext.configure(conn, opts={"compare_type": False})
        diff = compare_metadata(mc, Base.metadata)
    out = set()
    for d in diff:
        if isinstance(d, list):
            continue  # column-level modify_* (not compared here)
        kind, obj = d[0], d[1]
        if kind in ("add_fk", "remove_fk"):
            continue  # the models declare FKs; neither database enforces them
        name = getattr(obj, "name", None) or tuple(c.name for c in getattr(obj, "columns", []))
        out.add((kind, getattr(getattr(obj, "table", None), "name", None), name))
    return out


@pg_only
def test_models_drift_from_the_migrations_is_the_same_on_both_dialects(tmp_path):
    """compare_metadata(models, PostgreSQL) finds exactly what it finds on
    the SQLite chain (a few indexes the migrations made that the models do
    not declare, and users.username's unique constraint vs unique index)."""
    sqlite_engine = create_engine(f"sqlite:///{_sqlite_chain_db(tmp_path)}")
    try:
        lite = _model_drift(sqlite_engine)
    finally:
        sqlite_engine.dispose()
    assert _model_drift(engine) == lite
    assert len(lite) <= 5, lite


# --- baseline, guard, revisions on PostgreSQL ------------------------------------------------


@pytest.fixture
def scratch_pg_db():
    """An extra empty PostgreSQL database for this test (run prefix), dropped after."""
    import psycopg

    name = f"{pg_harness.run_db_name()}_x"
    with psycopg.connect(pg_harness._libpq_conninfo(pg_harness.admin_url()), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
    try:
        yield pg_harness.app_url(name)
    finally:
        with psycopg.connect(pg_harness._libpq_conninfo(pg_harness.admin_url()), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pg_only
def test_baseline_upgrade_is_idempotent_and_guarded(scratch_pg_db):
    from app.migrate import (
        BaselineError,
        current_store_revision,
        downgrade_store_db,
        store_head_revision,
        upgrade_store_db,
    )

    assert current_store_revision(scratch_pg_db) is None
    head = store_head_revision()
    assert upgrade_store_db(scratch_pg_db) == head
    assert upgrade_store_db(scratch_pg_db) == head, "a second upgrade is a no-op"
    assert downgrade_store_db(scratch_pg_db, "0062") == "0062"
    with pytest.raises(BaselineError, match="below the 0062 baseline"):
        downgrade_store_db(scratch_pg_db, "0061")
    assert current_store_revision(scratch_pg_db) == "0062"
    assert upgrade_store_db(scratch_pg_db) == head

    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        with eng.connect() as conn:
            teams = conn.execute(text("SELECT name FROM teams ORDER BY id")).scalars().all()
            assert teams == ["Everyone", "Grocery", "Kitchen", "Managers"]
            # The next id follows the seeded ones (identity reset after the seed).
            conn.execute(text("INSERT INTO teams (name, color, is_everyone, created_at) VALUES ('X', '#000', false, now())"))
            assert conn.execute(text("SELECT max(id) FROM teams")).scalar() == 5
            conn.rollback()
            assert conn.execute(text("SELECT datcollate FROM pg_database WHERE datname = current_database()")).scalar() == "C"
    finally:
        eng.dispose()


@pg_only
def test_a_database_with_tables_but_no_revision_is_refused(scratch_pg_db):
    from app.migrate import BaselineError, upgrade_store_db

    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE something_else (id int)"))
    finally:
        eng.dispose()
    with pytest.raises(BaselineError, match="not a store database"):
        upgrade_store_db(scratch_pg_db)


@pg_only
def test_the_sqlite_only_revisions_never_run_on_postgres(scratch_pg_db):
    from app.migrate import BaselineError, upgrade_store_db

    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0050')"))
    finally:
        eng.dispose()
    with pytest.raises(BaselineError, match="never run on PostgreSQL"):
        upgrade_store_db(scratch_pg_db)


# --- copy tool (scripts/pg) ------------------------------------------------------------------


def _fake_store_sqlite(tmp_path) -> pathlib.Path:
    """A SQLite store database at head with made-up rows in the awkward
    types: timestamps, dates, booleans, floats, NULLs, unicode, ids with
    gaps (deleted rows) and an AUTOINCREMENT table."""
    db_file = _sqlite_chain_db(tmp_path)
    con = sqlite3.connect(db_file)
    try:
        con.executescript(
            """
            INSERT INTO users (id, username, password_hash, is_admin, allowed_features, can_assign, hide_sales, created_at)
                VALUES (1, 'owner', 'x', 1, '[]', 1, 0, '2026-10-01 12:00:00.000000'),
                       (7, 'cafe-staff', 'y', 0, '["team_chat"]', 0, 1, '2026-10-02 01:02:03.456789');
            INSERT INTO orders (id, toast_order_guid, toast_check_guid, toast_selection_guid, business_date, opened_at,
                                item_guid, item_name, quantity, unit_price, net_price, tax_amount, voided,
                                created_at, updated_at, source, is_modifier)
                VALUES (3, 'o1', 'c1', 's1', '2026-10-01', '2026-10-01 14:00:00.000000', 'i1', 'Chai – masala', 2,
                        1.5, 3.0, 0.21, 0, '2026-10-01 14:05:00.000000', '2026-10-01 14:05:00.000000', 'toast', 0),
                       (9, 'o2', 'c2', 's2', '2026-10-02', NULL, NULL, NULL, 1.25, 0, 0, 0, 1,
                        '2026-10-02 10:00:00.000000', '2026-10-02 10:00:00.000000', 'csv', 1);
            INSERT INTO team_changes (seq, entity_type, entity_id, action, created_at)
                VALUES (40, 'task', 1, 'update', '2026-10-02 10:00:00.000000');
            """
        )
        con.commit()
    finally:
        con.close()
    return db_file


@pg_only
def test_copy_tool_copies_a_fake_store_exactly(tmp_path, scratch_pg_db):
    sys.path.insert(0, str(BACKEND / "scripts" / "pg"))
    import copy_sqlite_to_pg
    import verify_copy

    src = _fake_store_sqlite(tmp_path)
    before = src.read_bytes()
    problems = copy_sqlite_to_pg.copy(str(src), scratch_pg_db, log=lambda *a: None)
    assert problems == []
    assert src.read_bytes() == before, "the source is only read"
    assert verify_copy.verify(str(src), scratch_pg_db) == []

    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        with eng.connect() as conn:
            row = conn.execute(text("SELECT voided, quantity, opened_at, business_date FROM orders WHERE id = 3")).one()
            assert row.voided is False and row.quantity == 2.0
            assert row.opened_at == dt.datetime(2026, 10, 1, 14, 0, tzinfo=dt.timezone.utc)
            assert row.business_date == dt.date(2026, 10, 1)
            # Seed rows the chain inserted came along from the source, not twice.
            assert conn.execute(text("SELECT count(*) FROM teams")).scalar() == 4
            # New rows continue after the copied ids.
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, is_admin, allowed_features, can_assign, hide_sales, "
                    "created_at) VALUES ('new', 'z', false, '[]', false, false, now())"
                )
            )
            assert conn.execute(text("SELECT max(id) FROM users")).scalar() == 8
            conn.execute(
                text("INSERT INTO team_changes (entity_type, entity_id, action, created_at) VALUES ('task', 2, 'update', now())")
            )
            assert conn.execute(text("SELECT max(seq) FROM team_changes")).scalar() == 41
            conn.rollback()
            conn.execute(text("UPDATE orders SET quantity = 3 WHERE id = 3"))
            conn.commit()
    finally:
        eng.dispose()
    assert verify_copy.verify(str(src), scratch_pg_db) == ["orders.quantity: checksum differs"]
    with pytest.raises(SystemExit, match="not empty"):
        copy_sqlite_to_pg.copy(str(src), scratch_pg_db, log=lambda *a: None)


@pg_only
def test_copy_tool_refuses_a_value_that_does_not_fit(tmp_path, scratch_pg_db):
    sys.path.insert(0, str(BACKEND / "scripts" / "pg"))
    import copy_sqlite_to_pg
    import sqlite_pg

    src = _fake_store_sqlite(tmp_path)
    con = sqlite3.connect(src)
    con.execute("UPDATE orders SET quantity = 'two' WHERE id = 9")
    con.commit()
    con.close()
    with pytest.raises(sqlite_pg.CopyValueError, match=r"orders row 2, quantity: not a number"):
        copy_sqlite_to_pg.copy(str(src), scratch_pg_db, log=lambda *a: None)
    # All or nothing: the failed copy left no rows behind.
    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        with eng.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM orders")).scalar() == 0
    finally:
        eng.dispose()


@pg_only
def test_copy_tool_refuses_a_source_before_the_baseline(tmp_path, scratch_pg_db):
    sys.path.insert(0, str(BACKEND / "scripts" / "pg"))
    import copy_sqlite_to_pg

    db_file = tmp_path / "old.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), "upgrade", "0059"], cwd=BACKEND, env=env, check=True, capture_output=True)
    with pytest.raises(SystemExit, match="before the PostgreSQL baseline"):
        copy_sqlite_to_pg.copy(str(db_file), scratch_pg_db, log=lambda *a: None)


# --- per-store pg_dump backup (scripts/backup/pg_backup.py) -----------------------------------


def _pg_bin():
    import shutil

    for candidate in (os.environ.get("PG_BIN"), "/opt/homebrew/opt/postgresql@16/bin", "/usr/lib/postgresql/16/bin"):
        if candidate and (pathlib.Path(candidate) / "pg_dump").exists():
            return candidate
    return str(pathlib.Path(shutil.which("pg_dump")).parent) if shutil.which("pg_dump") else None


@pg_only
def test_pg_backup_dumps_checks_prunes_and_restores(tmp_path, scratch_pg_db):
    pg_bin = _pg_bin()
    if pg_bin is None:
        pytest.skip("pg_dump not installed")
    sys.path.insert(0, str(BACKEND / "scripts" / "backup"))
    import pg_backup
    from app.migrate import upgrade_store_db

    upgrade_store_db(scratch_pg_db)
    eng = create_engine(scratch_pg_db, poolclass=pool.NullPool)
    try:
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO job_runs (job_name, started_at, status) VALUES ('x', now(), 'ok')"))
    finally:
        eng.dispose()
    dest = tmp_path / "dumps"
    dest.mkdir()
    old = dest / "STORE_T-20200101-000000.dump"
    old.write_bytes(b"old")
    status = pg_backup.backup(scratch_pg_db, str(dest), name="STORE_T", pg_bin=pg_bin,
                              now=dt.datetime(2026, 10, 18, 3, 30))
    assert status["ok"] and status["tables"] >= 60
    assert (dest / "STORE_T-20261018-033000.dump").exists()
    assert not old.exists(), "older than 30 days: pruned"
    assert json_load(dest / "last_backup.json")["ok"] is True

    result = pg_backup.drill(scratch_pg_db, str(dest / "STORE_T-20261018-033000.dump"), pg_bin=pg_bin)
    assert result["ok"], result["differences"]
    assert result["rows"] >= 1 + 4  # the job run + the seeded teams


def json_load(path):
    import json

    return json.loads(pathlib.Path(path).read_text())
