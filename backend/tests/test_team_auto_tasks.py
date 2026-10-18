"""Phase 3 automatic tasks (app/team/auto_tasks.py): each rule creates its
task once, for the configured team, closes it when the signal clears, and
does nothing when switched off or when its team is missing. The WhatsApp
fallback is mocked - it never reaches Whapi from a test."""

import datetime as dt
import zoneinfo

import pytest

from app.config import get_settings
from app.models import ChecklistSubmission, DeviceToken, InvoiceSubmission, PlacedOrder
from app.services import push, whapi_client
from app.team import auto_tasks
from app.team import tasks_service as svc
from app.team.models import Team
from app.team.tasks_models import Task

from tests.conftest import add_member, client_for, make_user

# Two WhatsApp roles (store, kitchen) like the checklists these tests use:
# tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

NY = zoneinfo.ZoneInfo("America/New_York")


def ny(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=NY)


NIGHT = ny(2026, 9, 28, 21, 35)
EVENING = ny(2026, 9, 28, 20, 0)
MORNING = ny(2026, 9, 28, 9, 10)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "team_autotask_checklist_enabled", True)
    monkeypatch.setattr(s, "team_autotask_invoice_enabled", False)
    monkeypatch.setattr(s, "team_autotask_order_enabled", False)
    monkeypatch.setattr(s, "team_whatsapp_fallback", False)
    monkeypatch.setitem(auto_tasks._order_scan, "day", None)  # per store (platform B)
    monkeypatch.setattr(svc, "_quiet", lambda now: False)


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "data": data, "thread_id": thread_id})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


@pytest.fixture
def whatsapp(monkeypatch):
    sent = []
    monkeypatch.setattr(whapi_client, "send_whatsapp_message", lambda text, to=None: sent.append((to, text)))
    return sent


@pytest.fixture
def managers(db):
    return db.query(Team).filter(Team.name == "Managers").one()


@pytest.fixture
def manager(db, managers):
    user = make_user(db, "meena", features=["team_tasks"])
    add_member(db, managers, user)
    return user


def auto(db, source_type=None):
    q = db.query(Task).filter(Task.created_by_user_id == auto_tasks.SYSTEM_USER_ID)
    if source_type:
        q = q.filter(Task.source_type == source_type)
    return q.order_by(Task.id).all()


def submit_checklist(db, ctype, when):
    db.add(
        ChecklistSubmission(
            checklist_type=ctype,
            submitted_by_user_id=1,
            submitted_by_name="ravi",
            answers_json="{}",
            all_clear=True,
            answers_hash=f"h-{ctype}-{when.isoformat()}",
            created_at=when.astimezone(dt.timezone.utc),
        )
    )
    db.commit()


# --- Checklist ---------------------------------------------------------------------------


def test_checklist_task_not_before_930(db, manager):
    auto_tasks.auto_tasks_tick(db, EVENING)
    assert auto(db) == []


def test_checklist_tasks_created_once_for_the_team(db, manager, managers, pushes):
    db.add(DeviceToken(user_id=manager.id, token="tok-meena", platform="ios"))
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    auto_tasks.auto_tasks_tick(db, NIGHT + dt.timedelta(minutes=5))
    tasks = auto(db, "checklist")
    assert [t.title for t in tasks] == [
        "Submit the Spice Town closing checklist",
        "Submit the Street Kitchen closing checklist",
        "Submit the Street Kitchen FSA closing checklist",
    ]
    assert {t.source_id for t in tasks} == {
        "spice_town_closing:2026-09-28",
        "street_kitchen_closing:2026-09-28",
        "street_kitchen_fsa:2026-09-28",
    }
    for t in tasks:
        assert t.assignee_team_id == managers.id
        # Normal priority, and due no later than quiet hours start (10 PM):
        # nothing about it pushes at night.
        assert t.priority == "normal"
        assert t.created_by_name == "StoreOps"  # the store name (STORE_NAME unset: the product)
        assert t.due_at == ny(2026, 9, 28, 22, 0).astimezone(dt.timezone.utc)
    # Silent: the single 9:30 PM closing-checklist alert covers them.
    assert pushes == []


def test_only_the_missing_checklist_gets_a_task(db, manager):
    submit_checklist(db, "spice_town_closing", ny(2026, 9, 28, 21, 0))
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert [t.source_id for t in auto(db)] == [
        "street_kitchen_closing:2026-09-28",
        "street_kitchen_fsa:2026-09-28",
    ]


def test_checklist_task_closes_when_submitted(db, manager):
    auto_tasks.auto_tasks_tick(db, NIGHT)
    submit_checklist(db, "street_kitchen_closing", ny(2026, 9, 28, 21, 50))
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 21, 55))
    by_source = {t.source_id: t for t in auto(db)}
    done = by_source["street_kitchen_closing:2026-09-28"]
    assert done.status == "done"
    assert done.completed_by_name == "StoreOps"  # the store name (STORE_NAME unset: the product)
    assert done.completion_note == "Checklist submitted by ravi"
    assert by_source["spice_town_closing:2026-09-28"].status == "open"
    # Closed stays closed - a later run doesn't recreate it.
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 22, 30))
    assert len(auto(db)) == 3


def test_team_members_see_auto_tasks_in_the_api(db, manager):
    auto_tasks.auto_tasks_tick(db, NIGHT)
    client = client_for(db, manager)
    r = client.get("/api/team/tasks", params={"view": "mine"})
    assert r.status_code == 200, r.text
    titles = [t["title"] for t in r.json()["items"]]
    assert "Submit the Spice Town closing checklist" in titles
    item = r.json()["items"][0]
    assert item["created_by"]["name"] == "StoreOps"  # the store name (STORE_NAME unset: the product)
    assert item["can"]["complete"] is True
    # A member can complete it by hand too.
    r = client.post(f"/api/team/tasks/{item['id']}/complete")
    assert r.status_code == 200, r.text


def test_checklist_switched_off(db, manager, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_checklist_enabled", False)
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert auto(db) == []


def test_missing_team_skips(db, manager, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_checklist_team", "Night crew")
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert auto(db) == []


def test_archived_team_skips(db, manager, managers):
    managers.archived_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert auto(db) == []


def test_tasks_kill_switch_stops_everything(db, manager, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_tasks_enabled", False)
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert auto(db) == []


# --- Invoices ------------------------------------------------------------------------------


def invoice(db, n, **kw):
    fields = dict(
        vendor=f"Vendor{n}",
        submission_type="invoice",
        original_filename="x.pdf",
        stored_filename=f"x{n}.pdf",
        file_token=f"t{n}",
        whatsapp_sent=True,
        toast_email_sent=True,
        created_at=(MORNING - dt.timedelta(days=1)).astimezone(dt.timezone.utc),
    )
    fields.update(kw)
    entry = InvoiceSubmission(**fields)
    db.add(entry)
    db.commit()
    return entry


def test_invoice_rule_off_by_default(db, manager):
    invoice(db, 1)
    auto_tasks.auto_tasks_tick(db, MORNING)
    assert auto(db, "invoice") == []


def test_invoice_to_close_lifecycle(db, manager, managers, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_invoice_enabled", True)
    a = invoice(db, 1)
    b = invoice(db, 2)
    invoice(db, 3, toast_email_sent=False)  # not sent to Toast yet - nothing to close
    invoice(db, 4, created_at=(MORNING - dt.timedelta(days=9)).astimezone(dt.timezone.utc))  # too old
    auto_tasks.auto_tasks_tick(db, MORNING)
    auto_tasks.auto_tasks_tick(db, MORNING + dt.timedelta(minutes=5))
    tasks = {t.source_id: t for t in auto(db, "invoice")}
    assert set(tasks) == {str(a.id), str(b.id)}
    assert tasks[str(a.id)].title == "Close the Vendor1 invoice in Toast"
    assert tasks[str(a.id)].assignee_team_id == managers.id
    assert tasks[str(a.id)].priority == "normal"

    a.toast_closed_at = dt.datetime.now(dt.timezone.utc)
    a.toast_closed_by_name = "meena"
    b.deleted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    auto_tasks.auto_tasks_tick(db, MORNING + dt.timedelta(minutes=10))
    db.expire_all()
    tasks = {t.source_id: t for t in auto(db, "invoice")}
    assert tasks[str(a.id)].status == "done"
    assert tasks[str(a.id)].completion_note == "Closed in Toast by meena"
    assert tasks[str(b.id)].status == "cancelled"


# --- Orders ------------------------------------------------------------------------------


@pytest.fixture
def calendar(monkeypatch):
    calls = []

    def fake(db, start, end):
        calls.append((start, end))
        return {
            "events": [
                {"type": "order_due", "date": "2026-09-28", "vendor": "Babco", "for_delivery_date": "2026-09-30",
                 "items_needing_order": 12, "skipped": False},
                {"type": "order_due", "date": "2026-09-28", "vendor": "Chetak", "for_delivery_date": "2026-09-30",
                 "items_needing_order": 8, "skipped": True},
                # Below the vendor minimum: not a real order.
                {"type": "order_due", "date": "2026-09-28", "vendor": "Deen", "for_delivery_date": "2026-09-29",
                 "items_needing_order": 2, "skipped": False},
                {"type": "order_due", "date": "2026-09-29", "vendor": "Vdyas", "for_delivery_date": "2026-10-01",
                 "items_needing_order": 9, "skipped": False},
                {"type": "delivery", "date": "2026-09-28", "vendor": "KP Produce"},
            ]
        }

    monkeypatch.setattr(auto_tasks, "get_vendor_calendar", fake)
    return calls


def test_order_rule_off_by_default(db, manager, calendar):
    auto_tasks.auto_tasks_tick(db, MORNING)
    assert auto(db, "order_due") == []
    assert calendar == [], "the expensive calendar isn't computed when off"


def test_order_due_today_creates_task_once_and_scans_once(db, manager, calendar, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 8, 30))
    assert calendar == [], "not before 9 AM"
    auto_tasks.auto_tasks_tick(db, MORNING)
    auto_tasks.auto_tasks_tick(db, MORNING + dt.timedelta(minutes=5))
    assert len(calendar) == 1, "one calendar scan per day"
    tasks = auto(db, "order_due")
    assert [(t.title, t.source_id) for t in tasks] == [("Place the Babco order", "Babco:2026-09-28:2026-09-30")]
    assert tasks[0].due_at == ny(2026, 9, 28, 17, 0).astimezone(dt.timezone.utc)
    assert tasks[0].description == "12 item(s) to order, per Supplier Projection."


def test_order_task_closes_when_po_placed(db, manager, calendar, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    auto_tasks.auto_tasks_tick(db, MORNING)
    db.add(
        PlacedOrder(
            supplier="babco",
            item_count=3,
            items_json="[]",
            placed_at=ny(2026, 9, 28, 11, 0).astimezone(dt.timezone.utc),
        )
    )
    db.commit()
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 11, 5))
    db.expire_all()
    (task,) = auto(db, "order_due")
    assert task.status == "done"
    assert task.completion_note == "Purchase order placed"


def test_order_already_placed_gets_no_task(db, manager, calendar, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    db.add(
        PlacedOrder(
            supplier="Babco", item_count=1, items_json="[]",
            placed_at=ny(2026, 9, 28, 8, 0).astimezone(dt.timezone.utc),
        )
    )
    db.commit()
    auto_tasks.auto_tasks_tick(db, MORNING)
    assert auto(db, "order_due") == []


# --- WhatsApp fallback -------------------------------------------------------------------


@pytest.fixture
def group(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whapi_spice_town_checklist_group_id", "st-group@g.us")
    monkeypatch.setattr(s, "whapi_street_kitchen_checklist_group_id", "sk-group@g.us")


def test_whatsapp_fallback_off_by_default(db, manager, group, whatsapp):
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert whatsapp == []


def test_whatsapp_fallback_when_a_member_has_no_phone(db, manager, managers, group, whatsapp, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_whatsapp_fallback", True)
    with_phone = make_user(db, "arun", features=["team_tasks"])
    add_member(db, managers, with_phone)
    db.add(DeviceToken(user_id=with_phone.id, token="tok-arun", platform="ios"))
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    auto_tasks.auto_tasks_tick(db, NIGHT + dt.timedelta(minutes=5))
    # One message per task (one per location), never repeated.
    # The FSA closing is a Street Kitchen checklist - same group.
    assert [to for to, _ in whatsapp] == ["st-group@g.us", "sk-group@g.us", "sk-group@g.us"]
    text = whatsapp[0][1]
    assert "Submit the Spice Town closing checklist" in text
    assert "meena" in text and "arun" not in text


def test_no_whatsapp_when_everyone_has_a_phone(db, manager, group, whatsapp, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_whatsapp_fallback", True)
    db.add(DeviceToken(user_id=manager.id, token="tok-meena", platform="ios"))
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert whatsapp == []


def test_no_whatsapp_without_a_group(db, manager, whatsapp, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_whatsapp_fallback", True)
    monkeypatch.setattr(get_settings(), "whapi_spice_town_checklist_group_id", "")
    monkeypatch.setattr(get_settings(), "whapi_street_kitchen_checklist_group_id", "")
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert whatsapp == []
    assert len(auto(db)) == 3, "tasks are still created"


def test_whatsapp_failure_never_breaks_the_job(db, manager, group, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_whatsapp_fallback", True)

    def boom(text, to=None):
        raise whapi_client.WhapiNotConfigured("no token")

    monkeypatch.setattr(whapi_client, "send_whatsapp_message", boom)
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert len(auto(db)) == 3


# --- Robustness --------------------------------------------------------------------------


def test_one_failing_rule_doesnt_stop_the_others(db, manager, monkeypatch):
    def broken(db, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(auto_tasks, "run_invoice_rule", broken)
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert len(auto(db, "checklist")) == 3


def test_rules_run_inside_the_single_task_job():
    """No separate auto_tasks job: the rules run in tasks_tick, so their
    pushes can't race the assigned sweep (which ran at the same instant)."""
    from app.team.jobs import TEAM_JOBS

    ids = [j.id for j in TEAM_JOBS]
    assert "tasks_tick" in ids and "auto_tasks" not in ids


def test_tasks_tick_creates_checklist_tasks_once_and_silently(db, manager, pushes):
    db.add(DeviceToken(user_id=manager.id, token="tok-meena", platform="ios"))
    db.commit()
    utc = NIGHT.astimezone(dt.timezone.utc)
    svc.tasks_tick(db, utc)
    svc.tasks_tick(db, utc + dt.timedelta(minutes=5))
    assert len(auto(db, "checklist")) == 3
    assert [p for p in pushes if p["data"]["type"] in ("task_assigned", "task_due")] == [], (
        "checklist tasks never push - the closing-checklist alert is the one notification"
    )


# --- Review fixes --------------------------------------------------------------------------


def test_no_checklist_task_once_quiet_hours_start(db, manager):
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 22, 5))
    assert auto(db) == []


def test_auto_task_never_pushes_overdue_at_night(db, manager, pushes, monkeypatch):
    from app.services.notifications import in_quiet_hours

    db.add(DeviceToken(user_id=manager.id, token="tok-meena", platform="ios"))
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    for t in auto(db):
        t.priority = "urgent"  # even an urgent automatic task stays quiet
    db.commit()
    pushes.clear()
    monkeypatch.setattr(svc, "_quiet", lambda now: in_quiet_hours(now.astimezone(NY)))
    svc.send_reminders(db, ny(2026, 9, 28, 23, 10).astimezone(dt.timezone.utc))
    assert pushes == []


def test_cant_do_on_auto_task_goes_to_admins_and_team_leads(db, manager, managers, pushes):
    boss = make_user(db, "boss", is_admin=True)
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    add_member(db, managers, lead)
    for u in (boss, lead, manager):
        db.add(DeviceToken(user_id=u.id, token=f"tok-{u.username}", platform="ios"))
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    task = auto(db, "checklist")[0]
    pushes.clear()
    r = client_for(db, manager).post(f"/api/team/tasks/{task.id}/cant-do", json={"reason": "Store closed early"})
    assert r.status_code == 200, r.text
    assert sorted(t for p in pushes for t in p["tokens"]) == ["tok-boss", "tok-lead"]


def test_admin_reopened_auto_task_is_not_closed_again(db, manager):
    boss = make_user(db, "boss", is_admin=True)
    auto_tasks.auto_tasks_tick(db, NIGHT)
    submit_checklist(db, "street_kitchen_closing", ny(2026, 9, 28, 21, 50))
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 21, 55))
    done = [t for t in auto(db) if t.status == "done"][0]
    assert client_for(db, boss).post(f"/api/team/tasks/{done.id}/reopen").status_code == 200
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 22, 0))
    db.expire_all()
    assert db.get(Task, done.id).status == "open"


def test_auto_closed_task_shows_spice_town_as_finisher(db, manager):
    auto_tasks.auto_tasks_tick(db, NIGHT)
    submit_checklist(db, "street_kitchen_closing", ny(2026, 9, 28, 21, 50))
    auto_tasks.auto_tasks_tick(db, ny(2026, 9, 28, 21, 55))
    done = [t for t in auto(db) if t.status == "done"][0]
    out = client_for(db, manager).get(f"/api/team/tasks/{done.id}").json()
    assert out["completed_by"] == {"id": None, "name": "StoreOps"}
    assert out["events"][-1]["user_name"] == "StoreOps"


def test_order_below_vendor_minimum_gets_no_task(db, manager, calendar, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    auto_tasks.auto_tasks_tick(db, MORNING)
    assert "Place the Deen order" not in [t.title for t in auto(db, "order_due")]


def test_order_task_cancelled_when_skipped_on_calendar(db, manager, calendar, monkeypatch):
    from app.models import VendorOrderSkip

    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    auto_tasks.auto_tasks_tick(db, MORNING)
    db.add(VendorOrderSkip(vendor="Babco", delivery_date=dt.date(2026, 9, 30), skipped_by_name="meena"))
    db.commit()
    auto_tasks.auto_tasks_tick(db, MORNING + dt.timedelta(minutes=5))
    db.expire_all()
    (task,) = auto(db, "order_due")
    assert task.status == "cancelled"
    assert task.completion_note == "Order skipped on the Vendor Calendar"


def test_order_placed_the_evening_before_counts(db, manager, calendar, monkeypatch):
    from app.models import VendorSchedule

    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    # Babco delivers Wed (2) and Sat (5), ordered 2 days ahead: the previous
    # cycle's order day was Thu 9/24, so a PO on Sun 9/27 evening counts.
    db.add(VendorSchedule(vendor="Babco", delivery_weekdays="[2, 5]", lead_days=2, active=True))
    db.add(
        PlacedOrder(
            supplier="Babco", item_count=12, items_json="[]",
            placed_at=ny(2026, 9, 27, 19, 0).astimezone(dt.timezone.utc),
        )
    )
    db.commit()
    auto_tasks.auto_tasks_tick(db, MORNING)
    assert "Place the Babco order" not in [t.title for t in auto(db, "order_due")]


def test_order_scan_retried_after_a_failure(db, manager, calendar, monkeypatch):
    monkeypatch.setattr(get_settings(), "team_autotask_order_enabled", True)
    real_create = auto_tasks._create
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real_create(*a, **k)

    monkeypatch.setattr(auto_tasks, "_create", flaky)
    auto_tasks.auto_tasks_tick(db, MORNING)
    assert auto(db, "order_due") == []
    auto_tasks.auto_tasks_tick(db, MORNING + dt.timedelta(minutes=5))
    assert [t.title for t in auto(db, "order_due")] == ["Place the Babco order"]


def test_checklist_auto_task_never_pushes_on_its_own(db):
    """Owner: one checklist notification at 9:30 PM - the automatic
    'Submit the checklist' task must not add assigned/due pushes."""
    from app.team import tasks_service as svc
    from app.team.tasks_models import Task

    task = Task(
        title="Submit the Spice Town closing checklist",
        requires="none",
        priority="normal",
        status="open",
        created_by_user_id=svc.SYSTEM_USER_ID,
        created_by_name="Spice Town",
        source_type="checklist",
        source_id="spice_town_closing:2026-09-28",
    )
    assert svc.is_silent(task)
    task.source_type = "invoice"
    assert not svc.is_silent(task), "other automatic tasks still notify"


def test_whatsapp_fallback_never_because_of_or_naming_a_demo_login(db, manager, managers, group, whatsapp, monkeypatch):
    """App Review build 7: a demo login (no phone registered) in the team is
    neither a reason to message the real staff group nor named in it."""
    monkeypatch.setattr(get_settings(), "team_whatsapp_fallback", True)
    db.add(DeviceToken(user_id=manager.id, token="tok-meena", platform="ios"))
    demo = make_user(db, "app_store_review", features=["team_tasks"])
    add_member(db, managers, demo)
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT)
    assert whatsapp == []
    # A real member without a phone still triggers it - without the demo name.
    add_member(db, managers, make_user(db, "arun", features=["team_tasks"]))
    for task in auto(db):
        db.delete(task)
    db.commit()
    auto_tasks.auto_tasks_tick(db, NIGHT + dt.timedelta(minutes=5))
    assert whatsapp and all("arun" in text and "app_store_review" not in text for _, text in whatsapp)
