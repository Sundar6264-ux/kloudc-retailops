"""Verify 1 of the Oct 14 batch (docs/BATCH_OCT14_CONTRACT.md, "As built - Verify 1"):
the backend / data bugs found adversarially across W1-W6, each pinned by a
test that fails on the code as the builders committed it."""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from app.config import get_settings
from app.models import PlacedOrder, VendorSchedule
from app.services import notification_triggers as nt
from app.services import order_actions, vendor_calendar
from app.services.reorder import ordered_vendors
from app.team import auto_tasks
from app.team.models import Team
from app.team.tasks_models import Task
from app.timeutil import utcnow
from tests.conftest import add_member, make_user

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ET = dt.timezone(dt.timedelta(hours=-4))  # EDT


def _at(day, hour=10):
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(dt.timezone.utc)


# --- Migrations ---------------------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True)


def _create_sql(db_file, table):
    con = sqlite3.connect(db_file)
    try:
        return con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    finally:
        con.close()


def test_0056_downgrade_keeps_autoincrement(tmp_path):
    """vendor_contacts and po_sends are AUTOINCREMENT (0050: their ids are shown
    in the UI and logs and must never be reused). A batch-mode drop rebuilds
    the table without it - the downgrade must drop the columns in place."""
    db_file = tmp_path / "m.db"
    _alembic(db_file, "upgrade", "0056")
    _alembic(db_file, "downgrade", "0055")
    for table in ("vendor_contacts", "po_sends"):
        sql = _create_sql(db_file, table)
        assert "AUTOINCREMENT" in sql.upper(), f"{table} lost AUTOINCREMENT on downgrade: {sql}"
    _alembic(db_file, "upgrade", "0056")
    assert "cc_json" in _create_sql(db_file, "vendor_contacts")


# --- W1: an "Order already placed" tied to one delivery counts for that delivery only ------------

WED = dt.date(2026, 9, 30)  # order due (Thursday deliveries, 1-day lead)
THU = dt.date(2026, 10, 1)
LAST_THU = dt.date(2026, 9, 24)
NEXT_THU = dt.date(2026, 10, 8)


@pytest.fixture
def babco(db, monkeypatch):
    db.add(
        VendorSchedule(
            vendor="Babco", delivery_weekdays=json.dumps([3]), lead_days=1, active=True, created_at=utcnow()
        )
    )
    db.commit()
    rows = [{"item_id": f"i{n}", "projections": {str(h): {"need_to_order": 4} for h in range(1, 15)}} for n in range(6)]
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: rows)
    return rows


@pytest.fixture
def order_rule(db, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "team_autotask_order_enabled", True)
    monkeypatch.setattr(s, "team_autotask_order_min_items", 1)
    monkeypatch.setitem(auto_tasks._order_scan, "day", None)  # per store (platform B)
    team = db.query(Team).filter(Team.name == s.team_autotask_order_team).one()
    add_member(db, team, make_user(db, "meena", features=["team_tasks"]))


def _order_tasks(db):
    return (
        db.query(Task)
        .filter(Task.created_by_user_id == auto_tasks.SYSTEM_USER_ID, Task.source_type == "order_due")
        .order_by(Task.id)
        .all()
    )


@pytest.mark.parametrize("other_delivery", [LAST_THU, NEXT_THU], ids=["last week's", "next week's"])
def test_order_marked_for_another_delivery_doesnt_handle_this_one(db, admin, babco, order_rule, other_delivery):
    """An admin marks a DIFFERENT cycle's order-due chip "Order already placed"
    (the website calendar offers it on every chip) on the day this week's order
    is due. The calendar and the 9 AM push still say this week's order is due -
    the Team vendor-order task must agree, not count that order for this week."""
    morning = dt.datetime(2026, 9, 30, 9, 5, tzinfo=ET)
    order_actions.place_outside(db, admin, "Babco", other_delivery, now=_at(WED, 8))

    due = [e for e in vendor_calendar.get_vendor_calendar(db, WED, WED)["events"] if e["type"] == "order_due"]
    assert [(e["for_delivery_date"], e["ordered"]) for e in due] == [(THU.isoformat(), False)]
    assert [a.title for a in nt.order_due_alerts(db, morning)] == ["Order today: Babco"]

    auto_tasks.run_order_rule(db, morning)
    tasks = _order_tasks(db)
    assert [(t.source_id, t.status) for t in tasks] == [(f"Babco:{WED}:{THU}", "open")]

    # ...and an open task for this cycle isn't closed by it either.
    order_actions.place_outside(db, admin, "Babco", other_delivery, now=_at(WED, 11))
    auto_tasks.run_order_rule(db, morning + dt.timedelta(hours=2))
    db.expire_all()
    assert [t.status for t in _order_tasks(db)] == ["open"]

    # Marking THIS cycle closes it, like Mark placed.
    order_actions.place_outside(db, admin, "Babco", THU, now=_at(WED, 12))
    auto_tasks.run_order_rule(db, morning + dt.timedelta(hours=3))
    db.expire_all()
    assert [(t.status, t.completion_note) for t in _order_tasks(db)] == [("done", "Purchase order placed")]


def test_this_cycles_outside_order_placed_early_still_counts(db, admin, babco, order_rule):
    """Tied to this delivery but marked before the usual window (e.g. two weeks
    ahead): it counts for this cycle, as on the calendar."""
    morning = dt.datetime(2026, 9, 30, 9, 5, tzinfo=ET)
    order_actions.place_outside(db, admin, "Babco", THU, now=_at(dt.date(2026, 9, 17), 8))
    due = [e for e in vendor_calendar.get_vendor_calendar(db, WED, WED)["events"] if e["type"] == "order_due"]
    assert due[0]["ordered"] is True
    auto_tasks.run_order_rule(db, morning)
    assert _order_tasks(db) == []


def test_retroactive_order_already_placed_doesnt_make_the_vendor_ordered(db, admin, babco):
    """Marking LAST week's missed order-due as placed (bookkeeping for a past
    cycle) is no order on the way: Reorder / the Critical tile / the 9 AM
    critical push must not show the vendor as Ordered because of it."""
    order_actions.place_outside(db, admin, "Babco", LAST_THU, now=_at(WED, 8))
    assert "babco" not in ordered_vendors(db, WED, 7)
    # An order for this or a later delivery is on the way.
    row, _ = order_actions.place_outside(db, admin, "Babco", THU, now=_at(WED, 9))
    assert ordered_vendors(db, WED, 7)["babco"]["order_id"] == row.id


def test_retroactive_mark_doesnt_hide_an_earlier_real_order(db, admin, babco):
    """The newest row wins in ordered_vendors; a retroactive mark made after a
    real Mark placed must not shadow that real order either."""
    real = PlacedOrder(
        supplier="Babco", item_count=2, items_json="[]", placed_by_name="boss", placed_at=_at(WED, 7), status="placed"
    )
    db.add(real)
    db.commit()
    order_actions.place_outside(db, admin, "Babco", LAST_THU, now=_at(WED, 8))
    assert ordered_vendors(db, WED, 7)["babco"]["order_id"] == real.id


# --- W4: a lead who can't see Team Tasks gets no (invisible) daily task ------------------------


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_lead_without_team_tasks_gets_no_task(db, admin, monkeypatch):
    """Every other way of giving a person a task refuses someone without
    Tasks (tasks_service._assignee_user, recurrence.problem_with): the daily
    lead task mustn't create one they can never see - it says so instead."""
    import zoneinfo

    from app.services import issue_leads as il
    from app.services import push
    from app.team import tasks_service
    from tests.test_issue_leads import only_missing, save, snap

    monkeypatch.setattr(tasks_service, "_quiet", lambda now: False)
    monkeypatch.setattr(push, "is_configured", lambda: False)
    ny = zoneinfo.ZoneInfo("America/New_York")
    snap(db, "rice1", "Sona Masoori 20lb", category="Oils & Fats", supplier="Chetak")
    snap(db, "frz1", "Veg Puff", category="Beverages (Non-Alcoholic)", supplier="Shata Traders")
    db.commit()
    sasha = make_user(db, "Sasha Durga", features=["team_tasks", "inventory_reports"])
    nobody = make_user(db, "Kiran", features=["inventory_reports"])  # no team_tasks
    only_missing(db, admin)
    save(db, admin, sections=[("Oils & Ghee", sasha.id)], vendors=[("Shata Traders", nobody.id)])

    result = il.run_daily_tasks(db, dt.datetime.combine(dt.datetime.now(ny).date(), dt.time(10, 5), ny))
    assignees = {
        t.assignee_user_id
        for t in db.query(Task).filter(Task.source_type == il.SOURCE_TYPE, Task.status == "open")
    }
    assert assignees == {sasha.id}
    assert result["no_tasks_access"] == [{"user_id": nobody.id, "name": "Kiran", "count": 1}]
    assert "Kiran can't see Team Tasks" in il.last_run(db)["detail"]


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
@pytest.mark.parametrize("day", [dt.date(2026, 11, 1), dt.date(2027, 3, 14)], ids=["fall back", "spring forward"])
def test_lead_tasks_once_a_store_day_across_dst(db, admin, monkeypatch, day):
    """The daily lead task on a DST change day: one run that store day (the
    5-minute tick keeps calling), due exactly the set hours later in real
    time, replaced the next store day."""
    import zoneinfo

    from app.services import issue_leads as il
    from app.services import push
    from app.team import tasks_service
    from tests.test_issue_leads import only_missing, save, snap

    monkeypatch.setattr(tasks_service, "_quiet", lambda now: False)
    monkeypatch.setattr(push, "is_configured", lambda: False)
    ny = zoneinfo.ZoneInfo("America/New_York")
    snap(db, "rice1", "Sona Masoori 20lb", category="Oils & Fats", supplier="Chetak")
    db.commit()
    sasha = make_user(db, "Sasha Durga", features=["team_tasks", "inventory_reports"])
    only_missing(db, admin)
    save(db, admin, sections=[("Oils & Ghee", sasha.id)])

    def local(d, h, m=0):
        return dt.datetime.combine(d, dt.time(h, m), ny)

    for hour, minute in ((1, 30), (2, 30), (9, 55)):
        assert il.run_daily_tasks(db, local(day, hour, minute)) is None  # before 10:00 store time
    first = il.run_daily_tasks(db, local(day, 10, 0))
    assert first["created"] == 1 and first["day"] == day.isoformat()
    for hour in (11, 15, 23):
        assert il.run_daily_tasks(db, local(day, hour, 59)) is None
    (task,) = db.query(Task).filter(Task.source_type == il.SOURCE_TYPE).all()
    assert task.source_id == f"{sasha.id}:{day.isoformat()}"
    assert task.due_at - task.created_at == dt.timedelta(hours=6)

    nxt = il.run_daily_tasks(db, local(day + dt.timedelta(days=1), 10, 5))
    assert nxt["created"] == 1 and nxt["closed"] == 1
    statuses = [t.status for t in db.query(Task).filter(Task.source_type == il.SOURCE_TYPE).order_by(Task.id)]
    assert statuses == ["cancelled", "open"]
