"""Multi-store M1, batch MT4: POS-neutral ids (migration 0060).

- Order.top_level() is exactly the old `parent_selection_guid IS NULL` filter,
  and no report still writes the old filter by hand.
- Every way a row reaches `orders` / `open_order_cache` fills the neutral
  columns: the sales sync upsert (also on a re-sync that moves a line), an ORM
  insert, and rows written without them (old code) are healed after a sync.
- pos_item_map and sync_cursors are kept up to date by the sales sync, and a
  failure in that bookkeeping never changes the sync's result.
- The migration backfills an existing database without touching any old
  column, and up/down/up gives back the original schema.
"""

import datetime as dt
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import select, text

from app.db import Base
from app.models import OpenOrderCache, Order, PosItemMap, SyncCursor
from app.pos import neutral_ids
from app.services import sales_sync

BACKEND = pathlib.Path(__file__).resolve().parents[1]
D1 = dt.date(2026, 10, 9)
D2 = dt.date(2026, 10, 10)
PAID = "2026-10-10T18:00:00.000+0000"


# --- Order.top_level() ------------------------------------------------------


def test_top_level_is_the_old_filter():
    assert str(Order.top_level()) == str(Order.parent_selection_guid.is_(None))
    assert str(Order.top_level()) == "orders.parent_selection_guid IS NULL"


def test_no_report_spells_the_old_filter_by_hand():
    offenders = []
    for path in (BACKEND / "app").rglob("*.py"):
        if path.name == "models.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"parent_selection_guid\s*\.\s*is_\(\s*None\s*\)|parent_selection_guid\s+IS\s+NULL", line, re.I):
                offenders.append(f"{path.relative_to(BACKEND)}:{n}")
    assert offenders == [], "use Order.top_level(): " + ", ".join(offenders)


def test_the_thirteen_report_queries_use_the_helper():
    # MT3 moved the accuracy query to app/pos/sync/, so count both folders.
    uses = 0
    for folder in (BACKEND / "app" / "services", BACKEND / "app" / "pos" / "sync"):
        for path in folder.glob("*.py"):
            uses += path.read_text().count("Order.top_level()")
    assert uses == 13


# --- writers ----------------------------------------------------------------


def test_orm_insert_copies_the_toast_ids(db):
    db.add_all(
        [
            Order(toast_order_guid="o1", toast_check_guid="c1", toast_selection_guid="s1",
                  business_date=D1, item_guid="i1", dining_option="dine-1"),
            Order(toast_order_guid="o1", toast_check_guid="c1", toast_selection_guid="s2",
                  parent_selection_guid="s1", business_date=D1, item_guid="i2"),
        ]
    )
    db.commit()
    rows = {r.toast_selection_guid: r for r in db.execute(select(Order)).scalars()}
    top, mod = rows["s1"], rows["s2"]
    assert (top.source, top.external_order_id, top.external_check_id, top.external_line_id) == ("toast", "o1", "c1", "s1")
    assert (top.parent_external_line_id, top.is_modifier, top.dining_option_id, top.channel) == (None, False, "dine-1", None)
    assert (mod.external_line_id, mod.parent_external_line_id, mod.is_modifier) == ("s2", "s1", True)


def test_open_order_cache_insert_copies_the_order_guid(db):
    db.add(OpenOrderCache(toast_order_guid="oo1", business_date=D1))
    db.commit()
    assert db.execute(select(OpenOrderCache.external_order_id)).scalar_one() == "oo1"


def test_multi_row_inserts_still_work_and_copy_per_row(db):
    """A multi-row `insert(Order).values([...])` worked before 0060; the
    copy-the-Toast-id defaults must not break it (SQLAlchemy 2.0 cannot
    isolate one row of it for a context-aware default) and must copy each
    row's own values."""
    from sqlalchemy import insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    def row(i, parent=None):
        return dict(toast_order_guid=f"o{i}", toast_check_guid=f"c{i}", toast_selection_guid=f"s{i}",
                    parent_selection_guid=parent, business_date=D1, item_guid=f"i{i}", dining_option=f"d{i}")

    db.execute(insert(Order).values([row(1), row(2, "s1"), row(3)]))
    db.execute(sqlite_insert(Order).values([row(4, "s3"), row(5)]).on_conflict_do_nothing())
    db.execute(insert(OpenOrderCache).values([dict(toast_order_guid=g, business_date=D1) for g in ("oa", "ob")]))
    db.commit()
    n = _neutral(db)
    assert n == {
        f"s{i}": ("toast", f"o{i}", f"c{i}", f"s{i}", parent, int(parent is not None), f"d{i}")
        for i, parent in ((1, None), (2, "s1"), (3, None), (4, "s3"), (5, None))
    }
    assert sorted(db.execute(select(OpenOrderCache.toast_order_guid, OpenOrderCache.external_order_id))) == [
        ("oa", "oa"), ("ob", "ob")
    ]


def _sel(guid, price, modifiers=()):
    return {"guid": guid, "item": {"guid": "i-" + guid}, "displayName": guid, "price": price,
            "quantity": 1, "tax": 0, "voided": False, "modifiers": list(modifiers)}


def _order(guid, checks, dining="dine-guid"):
    return {
        "guid": guid,
        "openedDate": PAID,
        "createdDate": PAID,
        "paidDate": PAID,
        "diningOption": {"guid": dining},
        "checks": [{"guid": cg, "paidDate": PAID, "selections": sels} for cg, sels in checks],
    }


@pytest.fixture
def toast(monkeypatch):
    answers = {}
    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", lambda d: answers.get(d, []))
    return answers


def _neutral(db):
    cols = ("toast_selection_guid", "source", "external_order_id", "external_check_id", "external_line_id",
            "parent_external_line_id", "is_modifier", "dining_option_id")
    return {r[0]: tuple(r[1:]) for r in db.execute(text(f"SELECT {', '.join(cols)} FROM orders"))}


def test_sales_sync_writes_the_neutral_ids_item_map_and_cursor(db, toast):
    toast[D2] = [_order("o1", [("c1", [_sel("s1", 5.0, modifiers=[_sel("m1", 0.5)])])])]
    job = sales_sync.sync_sales_for_date(db, D2)
    assert job.status == "success"
    assert job.detail == "2 line items synced from 1 orders"
    assert _neutral(db) == {
        "s1": ("toast", "o1", "c1", "s1", None, 0, "dine-guid"),
        "m1": ("toast", "o1", "c1", "m1", "s1", 1, "dine-guid"),
    }
    mapped = {(m.source, m.external_id, m.item_id) for m in db.execute(select(PosItemMap)).scalars()}
    assert mapped == {("toast", "i-s1", "i-s1"), ("toast", "i-m1", "i-m1")}
    assert neutral_ids.get_cursor(db, "toast", neutral_ids.SALES_STREAM) == "2026-10-10"

    # A re-sync where Toast moved the line to another check updates the copy too.
    toast[D2] = [_order("o1", [("c2", [_sel("s1", 5.0, modifiers=[_sel("m1", 0.5)])])])]
    sales_sync.sync_sales_for_date(db, D2)
    assert _neutral(db)["s1"][2] == "c2" and _neutral(db)["m1"][2] == "c2"

    # Backfilling an older day never moves the cursor back.
    toast[D1] = [_order("o0", [("c0", [_sel("s0", 1.0)])])]
    sales_sync.sync_sales_for_date(db, D1)
    assert neutral_ids.get_cursor(db, "toast", neutral_ids.SALES_STREAM) == "2026-10-10"
    assert db.execute(select(SyncCursor)).scalars().one().cursor == "2026-10-10"


def test_a_failed_sync_records_nothing(db, toast, monkeypatch):
    def boom(d):
        raise RuntimeError("toast down")

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", boom)
    job = sales_sync.sync_sales_for_date(db, D2)
    assert job.status == "failed"
    assert neutral_ids.get_cursor(db, "toast", neutral_ids.SALES_STREAM) is None
    assert db.execute(select(PosItemMap)).first() is None


def test_rows_written_without_neutral_ids_are_healed_and_keep_updated_at(db, toast):
    # What the old code (still serving between `alembic upgrade` and the restart) writes.
    db.execute(text(
        "INSERT INTO orders (toast_order_guid, toast_check_guid, toast_selection_guid, parent_selection_guid,"
        " business_date, item_guid, quantity, unit_price, net_price, tax_amount, voided, dining_option,"
        " created_at, updated_at) VALUES"
        " ('oldo', 'oldc', 'olds', NULL, '2026-10-08', 'i-old', 1, 1, 1, 0, 0, 'dine', '2026-10-08 10:00:00.000000', '2026-10-08 10:00:00.000000'),"
        " ('oldo', 'oldc', 'oldm', 'olds', '2026-10-08', 'i-oldm', 1, 0, 0, 0, 0, 'dine', '2026-10-08 10:00:00.000000', '2026-10-08 10:00:00.000000')"
    ))
    db.execute(text("INSERT INTO open_order_cache (toast_order_guid, business_date, total_amount, num_checks, scanned_at)"
                    " VALUES ('open1', '2026-10-08', 0, 0, '2026-10-08 10:00:00.000000')"))
    db.commit()
    assert _neutral(db)["olds"] == ("toast", None, None, None, None, 0, None)

    toast[D2] = [_order("o1", [("c1", [_sel("s1", 5.0)])])]
    sales_sync.sync_sales_for_date(db, D2)
    n = _neutral(db)
    assert n["olds"] == ("toast", "oldo", "oldc", "olds", None, 0, "dine")
    assert n["oldm"] == ("toast", "oldo", "oldc", "oldm", "olds", 1, "dine")
    assert db.execute(text("SELECT updated_at FROM orders WHERE toast_selection_guid = 'olds'")).scalar() == "2026-10-08 10:00:00.000000"
    assert db.execute(text("SELECT external_order_id FROM open_order_cache")).scalar() == "open1"


def test_catalog_items_are_mapped_after_a_sync(db, toast):
    db.execute(text("INSERT INTO inventory_snapshots (snapshot_date, item_id, uploaded_at) VALUES"
                    " ('2026-10-01', 'old-only', '2026-10-01 00:00:00'), ('2026-10-09', 'inv-1', '2026-10-09 00:00:00')"))
    db.execute(text("INSERT INTO toast_menu_items (item_id, fetched_at) VALUES ('menu-1', '2026-10-09 00:00:00')"))
    db.commit()
    toast[D2] = [_order("o1", [("c1", [_sel("s1", 5.0)])])]
    sales_sync.sync_sales_for_date(db, D2)
    ids = set(db.execute(text("SELECT external_id FROM pos_item_map WHERE item_id = external_id")).scalars())
    assert ids == {"i-s1", "inv-1", "menu-1"}


def test_bookkeeping_failure_never_changes_the_sync(db, toast, monkeypatch, caplog):
    def boom(_db):
        raise RuntimeError("disk full")

    monkeypatch.setattr(neutral_ids, "heal_orders", boom)
    toast[D2] = [_order("o1", [("c1", [_sel("s1", 5.0)])])]
    job = sales_sync.sync_sales_for_date(db, D2)
    assert job.status == "success"
    assert job.detail == "1 line items synced from 1 orders"
    # The line itself was written (with its neutral ids, in the upsert).
    assert _neutral(db)["s1"][3] == "s1"
    assert "bookkeeping failed" in caplog.text


def test_record_items_is_idempotent(db):
    assert neutral_ids.record_items(db, ["a", "b", None, "", "a"]) == 2
    neutral_ids.record_items(db, ["a"])
    db.commit()
    assert db.execute(text("SELECT COUNT(*) FROM pos_item_map")).scalar() == 2


# --- migration 0060 ---------------------------------------------------------


def _alembic(db_file):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True)

    return run


def _schema(con):
    return sorted(con.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall(),
                  key=lambda r: (r[0], r[1]))


OLD_ORDER_COLS = ("id, toast_order_guid, toast_check_guid, toast_selection_guid, parent_selection_guid, business_date,"
                  " opened_at, paid_at, item_guid, item_name, quantity, unit_price, net_price, tax_amount, voided,"
                  " dining_option, created_at, updated_at")


def test_migration_0060_backfills_and_round_trips(tmp_path):
    db_file = tmp_path / "m.db"
    run = _alembic(db_file)
    run("upgrade", "0059")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO orders (id, toast_order_guid, toast_check_guid, toast_selection_guid, parent_selection_guid,"
        " business_date, item_guid, quantity, unit_price, net_price, tax_amount, voided, dining_option, created_at, updated_at)"
        " VALUES (1, 'o1', 'c1', 's1', NULL, '2026-10-09', 'i1', 1, 2, 2, 0, 0, 'dine', '2026-10-09 10:00:00', '2026-10-09 11:00:00'),"
        " (2, 'o1', 'c1', 'm1', 's1', '2026-10-09', 'i2', 1, 0, 0, 0, 0, 'dine', '2026-10-09 10:00:00', '2026-10-09 11:00:00'),"
        " (50001, 'o2', 'c2', 's2', NULL, '2026-10-10', NULL, 1, 1, 1, 0, 1, NULL, '2026-10-10 10:00:00', '2026-10-10 11:00:00')"
    )
    con.execute("INSERT INTO open_order_cache (toast_order_guid, business_date, total_amount, num_checks, scanned_at)"
                " VALUES ('open1', '2026-10-10', 0, 0, '2026-10-10 10:00:00')")
    con.execute("INSERT INTO inventory_snapshots (snapshot_date, item_id, uploaded_at) VALUES ('2026-10-01', 'inv1', '2026-10-01 00:00:00')")
    con.execute("INSERT INTO toast_menu_items (item_id, fetched_at) VALUES ('menu1', '2026-10-01 00:00:00'), ('i1', '2026-10-01 00:00:00')")
    con.execute("INSERT INTO job_runs (job_name, status, business_date, started_at) VALUES"
                " ('toast_sales_sync', 'success', '2026-10-09', '2026-10-10 08:00:00'),"
                " ('toast_sales_sync', 'failed', '2026-10-10', '2026-10-11 08:00:00')")
    con.commit()
    before_schema = _schema(con)
    before_rows = con.execute(f"SELECT {OLD_ORDER_COLS} FROM orders ORDER BY id").fetchall()
    con.close()

    run("upgrade", "0060")
    con = sqlite3.connect(db_file)
    try:
        assert con.execute(f"SELECT {OLD_ORDER_COLS} FROM orders ORDER BY id").fetchall() == before_rows
        assert con.execute(
            "SELECT id, source, external_order_id, external_check_id, external_line_id, parent_external_line_id,"
            " is_modifier, channel, dining_option_id FROM orders ORDER BY id"
        ).fetchall() == [
            (1, "toast", "o1", "c1", "s1", None, 0, None, "dine"),
            (2, "toast", "o1", "c1", "m1", "s1", 1, None, "dine"),
            (50001, "toast", "o2", "c2", "s2", None, 0, None, None),
        ]
        assert con.execute("SELECT external_order_id FROM open_order_cache").fetchall() == [("open1",)]
        assert sorted(r[0] for r in con.execute("SELECT external_id FROM pos_item_map WHERE item_id = external_id AND source = 'toast'")) == [
            "i1", "i2", "inv1", "menu1"
        ]
        assert con.execute("SELECT source, stream, cursor FROM sync_cursors").fetchall() == [
            ("toast", "orders_business_date", "2026-10-09")
        ]
        unique = [r[1] for r in con.execute("PRAGMA index_list(orders)") if r[2] == 1]
        assert "uq_orders_source_external_line_id" in unique
        plan = " ".join(str(r[-1]) for r in con.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM orders WHERE source = 'toast' AND external_line_id IS NULL"))
        assert "uq_orders_source_external_line_id" in plan
        # The migrated tables match the models (a missed column would only show in production).
        for table in ("orders", "open_order_cache", "sync_cursors", "pos_item_map"):
            migrated = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            modeled = {c.name for c in Base.metadata.tables[table].columns}
            assert migrated == modeled, table
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        con.close()

    run("downgrade", "0059")
    con = sqlite3.connect(db_file)
    assert _schema(con) == before_schema
    assert con.execute(f"SELECT {OLD_ORDER_COLS} FROM orders ORDER BY id").fetchall() == before_rows
    con.close()

    run("upgrade", "head")
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT COUNT(*) FROM orders WHERE external_line_id = toast_selection_guid").fetchone() == (3,)
    con.close()


def test_migration_0060_reruns_on_a_half_done_database(tmp_path):
    """A column added by hand (or a crashed earlier attempt) does not stop it."""
    db_file = tmp_path / "m.db"
    run = _alembic(db_file)
    run("upgrade", "0059")
    con = sqlite3.connect(db_file)
    con.execute("ALTER TABLE orders ADD COLUMN source VARCHAR NOT NULL DEFAULT 'toast'")
    con.execute("CREATE TABLE sync_cursors (source VARCHAR NOT NULL, stream VARCHAR NOT NULL, cursor VARCHAR,"
                " updated_at DATETIME NOT NULL, PRIMARY KEY (source, stream))")
    con.commit()
    con.close()
    run("upgrade", "0060")
    con = sqlite3.connect(db_file)
    assert {r[1] for r in con.execute("PRAGMA table_info(orders)")} >= {"source", "external_line_id", "is_modifier"}
    con.close()
