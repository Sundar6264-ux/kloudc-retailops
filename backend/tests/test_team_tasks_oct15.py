"""Team tasks, advanced and in sync (docs/BATCH_OCT15_CONTRACT.md, "As built -
TEAM TASKS (server)"): empty-team guard, checklist tasks stopped + leftovers
closed as Missed, conditional writes / expected_updated_at, the push sweep's
recipient filter, steps CRUD + history, the /updates long-poll, source hooks,
lead-task live progress, filters, bulk, "My team today", schedules for the
app, background pushes. Pushes are faked; nothing reaches Firebase."""

import datetime as dt
import json
import threading
import time
import zoneinfo

import pytest

from app.config import get_settings
from app.models import ChecklistSubmission, DeviceToken, NotificationLog, PlacedOrder, VendorSchedule
from app.services import push
from app.team import auto_tasks
from app.team import tasks_service as svc
from app.team.changes import current_cursor, get_updates
from app.team.models import Team
from app.team.source_sync import task_sources_changed
from app.team.tasks_models import Task, TaskEvent
from app.timeutil import utcnow
from tests.conftest import add_member, client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    monkeypatch.setattr(svc, "_quiet", lambda now: False)


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


def device(db, user):
    db.add(DeviceToken(user_id=user.id, token=f"tok-{user.username}", platform="ios"))
    db.commit()


def reached(sent):
    return sorted({t.replace("tok-", "") for s in sent for t in s["tokens"]})


def set_pref(db, user, key, on):
    from app.models import NotificationPref

    db.add(NotificationPref(user_id=user.id, key=key, enabled=on))
    db.commit()


def create(client, **body):
    body.setdefault("title", "Clean the cooler")
    r = client.post("/api/team/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["tasks"][0]


def auto_task(db, source_type, source_id, team=None, user=None, status="open", created=None, **kw):
    stamp = created or utcnow()
    task = Task(
        title=f"{source_type} task", requires="none", priority="normal", status=status,
        assignee_team_id=team.id if team is not None else None,
        assignee_user_id=user.id if user is not None else None,
        source_type=source_type, source_id=source_id,
        created_by_user_id=svc.SYSTEM_USER_ID, created_by_name=svc.SYSTEM_NAME,
        created_at=stamp, updated_at=stamp, **kw,
    )
    db.add(task)
    db.commit()
    return task


def kinds(db, task_id):
    return [e.kind for e in db.query(TaskEvent).filter(TaskEvent.task_id == task_id).order_by(TaskEvent.id)]


# --- 1. Empty teams ------------------------------------------------------------------------


def test_assigning_to_an_empty_team_is_refused(db, admin_client, staff, team):
    r = admin_client.post("/api/team/tasks", json={"title": "Mop", "assignee_team_id": team.id})
    assert r.status_code == 400 and "no members yet" in r.json()["detail"]
    tid = create(admin_client, assignee_user_ids=[staff.id])["id"]
    r = admin_client.patch(f"/api/team/tasks/{tid}", json={"assignee_team_id": team.id})
    assert r.status_code == 400
    r = admin_client.post(
        "/api/team/templates", json={"title": "Mop", "rrule": "FREQ=DAILY;BYHOUR=9", "assignee_team_id": team.id}
    )
    assert r.status_code == 400
    add_member(db, team, staff)
    assert create(admin_client, assignee_team_id=team.id)["assignee"]["id"] == team.id


def test_member_count_in_teams_list_and_bootstrap(db, admin_client, staff, team):
    add_member(db, team, staff)
    teams = {t["name"]: t for t in admin_client.get("/api/team/teams").json()}
    assert teams["Kitchen"]["member_count"] == 1 and teams["Grocery"]["member_count"] == 0
    assert teams["Everyone"]["member_count"] >= 2
    boot = {t["name"]: t for t in admin_client.get("/api/team/bootstrap").json()["teams"]}
    assert boot["Kitchen"]["member_count"] == 1 and boot["Managers"]["member_count"] == 0


def test_membership_change_puts_the_teams_open_tasks_in_the_new_members_feed(db, admin_client, staff, staff2, team):
    add_member(db, team, staff)
    tid = create(admin_client, assignee_team_id=team.id)["id"]
    cursor = current_cursor(db)
    admin_client.put(f"/api/team/teams/{team.id}/members", json={"user_ids": [staff.id, staff2.id]})
    changes = get_updates(db, staff2, cursor)["changes"]
    assert {"id": tid, "action": "updated"} in changes.get("task", [])


# --- 2. Checklist tasks stopped; leftovers Missed; order tasks expire ----------------------


def test_checklist_tasks_are_no_longer_made_by_default(db):
    assert get_settings().team_autotask_checklist_enabled is False
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, make_user(db, "meena", features=["team_tasks"]))
    auto_tasks.auto_tasks_tick(db, dt.datetime(2026, 9, 28, 21, 35, tzinfo=NY))
    assert db.query(Task).filter(Task.source_type == "checklist").count() == 0


def test_leftover_checklist_tasks_close_as_missed_but_never_a_demo_teams(db, staff):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    review = Team(name="Review", color="#123456", created_at=utcnow())
    db.add(review)
    db.commit()
    add_member(db, review, make_user(db, "app_store_review", features=["team_tasks"]))
    add_member(db, managers, staff)
    old = auto_task(db, "checklist", "store_closing:2026-10-01", team=managers)
    submitted = auto_task(db, "checklist", "kitchen_closing:2026-10-02", team=managers)
    demo_team = auto_task(db, "checklist", "store_closing:2026-10-03", team=review)
    db.add(ChecklistSubmission(
        checklist_type="kitchen_closing", submitted_by_user_id=staff.id, submitted_by_name="ravi",
        answers_json="{}", all_clear=True, answers_hash="h1",
        created_at=dt.datetime(2026, 10, 2, 21, 0, tzinfo=NY).astimezone(dt.timezone.utc),
    ))
    db.commit()
    auto_tasks.auto_tasks_tick(db, dt.datetime(2026, 10, 15, 9, 0, tzinfo=NY))
    for t in (old, submitted, demo_team):
        db.refresh(t)
    assert (old.status, old.completion_note) == ("cancelled", "Missed - not submitted")
    assert submitted.status == "done" and submitted.completion_note == "Checklist submitted by ravi"
    assert demo_team.status == "open"
    # Idempotent: a second run changes nothing and adds no history.
    before = kinds(db, old.id)
    auto_tasks.auto_tasks_tick(db, dt.datetime(2026, 10, 15, 9, 5, tzinfo=NY))
    assert kinds(db, old.id) == before


def test_rule_on_keeps_tonights_task_until_its_day_is_over(db, monkeypatch, staff):
    monkeypatch.setattr(get_settings(), "team_autotask_checklist_enabled", True)
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    tonight = auto_task(db, "checklist", "store_closing:2026-10-15", team=managers)
    auto_tasks.expire_checklist_tasks(db, dt.datetime(2026, 10, 15, 21, 45, tzinfo=NY))
    db.refresh(tonight)
    assert tonight.status == "open"
    auto_tasks.expire_checklist_tasks(db, dt.datetime(2026, 10, 16, 0, 30, tzinfo=NY))
    db.refresh(tonight)
    assert tonight.status == "cancelled"


def test_protected_app_review_ids_are_never_closed(db, staff, monkeypatch):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    task = auto_task(db, "checklist", "store_closing:2026-10-01", team=managers)
    monkeypatch.setattr(auto_tasks, "PROTECTED_TASK_IDS", frozenset({task.id}))
    auto_tasks.expire_checklist_tasks(db, utcnow())
    db.refresh(task)
    assert task.status == "open"


def test_order_task_expires_after_seven_days(db, staff):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    task = auto_task(db, "order_due", "Chetak:2026-10-01:2026-10-03", team=managers)
    fresh = auto_task(db, "order_due", "Chetak:2026-10-14:2026-10-16", team=managers)
    auto_tasks.sync_order_tasks(db, dt.datetime(2026, 10, 15, 9, 0, tzinfo=NY))
    db.refresh(task)
    db.refresh(fresh)
    assert task.status == "cancelled" and task.completion_note.startswith("Expired")
    assert fresh.status == "open"


# --- 3. Conditional writes ----------------------------------------------------------------


def test_auto_close_never_overwrites_a_persons_completion(db, staff):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    task = auto_task(db, "invoice", "999", team=managers)
    stale = db.get(Task, task.id)
    # Someone finishes it between the rule's SELECT and its write.
    other = type(db)(bind=db.get_bind())
    other.query(Task).filter(Task.id == task.id).update(
        {"status": "done", "completed_by_user_id": staff.id, "completed_by_name": "ravi"}
    )
    other.commit()
    other.close()
    assert auto_tasks._close(db, stale, "cancelled", "Invoice deleted") is False
    db.commit()
    db.expire_all()
    fresh = db.get(Task, task.id)
    assert (fresh.status, fresh.completed_by_name) == ("done", "ravi")


def test_edit_and_step_tick_on_a_finished_task_409_with_the_task(db, admin_client, staff, staff_client):
    t = create(admin_client, assignee_user_ids=[staff.id], items=["One"])
    detail = admin_client.get(f"/api/team/tasks/{t['id']}").json()
    assert staff_client.post(f"/api/team/tasks/{t['id']}/complete").status_code == 200
    r = admin_client.patch(f"/api/team/tasks/{t['id']}", json={"title": "Late edit"})
    assert r.status_code == 409 and r.json()["task"]["status"] == "done"
    r = staff_client.patch(f"/api/team/tasks/{t['id']}/items/{detail['items'][0]['id']}", json={"done": True})
    assert r.status_code == 409


def test_expected_updated_at(db, admin_client, staff):
    t = create(admin_client, assignee_user_ids=[staff.id])
    seen = t["updated_at"]
    r = admin_client.patch(f"/api/team/tasks/{t['id']}", json={"title": "A", "expected_updated_at": seen})
    assert r.status_code == 200, r.text
    # A second editor still holding the old version: 409 + the current task.
    r = admin_client.patch(f"/api/team/tasks/{t['id']}", json={"title": "B", "expected_updated_at": seen})
    assert r.status_code == 409
    body = r.json()
    assert body["detail"].startswith("This task was changed") and body["task"]["title"] == "A"
    # Old clients (no expected_updated_at): last write wins, as before.
    assert admin_client.patch(f"/api/team/tasks/{t['id']}", json={"title": "C"}).json()["title"] == "C"


def test_due_change_detail_in_history(db, admin_client, staff):
    t = create(admin_client, assignee_user_ids=[staff.id], due_at="2026-10-20T15:00:00")
    admin_client.patch(f"/api/team/tasks/{t['id']}", json={"due_at": "2026-10-21T15:00:00"})
    ev = [e for e in admin_client.get(f"/api/team/tasks/{t['id']}").json()["events"] if e["kind"] == "edited"][-1]
    assert ev["detail"]["fields"] == ["due_at"] and ev["detail"]["due"]["to"].startswith("2026-10-21T19:00")


# --- 6. Sweep: nobody reachable = handled once --------------------------------------------


def test_sweep_logs_a_push_nobody_can_get_and_stops_retrying(db, pushes, admin, staff, monkeypatch):
    calls = []
    real = svc.notif.deliverable_user_ids
    monkeypatch.setattr(svc.notif, "deliverable_user_ids", lambda *a: calls.append(a) or real(*a))
    task = Task(
        title="Restock", requires="none", priority="normal", assignee_user_id=staff.id, status="open",
        created_by_user_id=admin.id, created_by_name="boss", created_at=utcnow(), updated_at=utcnow(),
    )
    db.add(task)
    db.commit()
    svc.sweep_assigned(db, utcnow())  # staff has no phone
    assert pushes == []
    row = db.query(NotificationLog).filter(NotificationLog.dedupe_key == f"task_assigned:{task.id}:{staff.id}").one()
    assert row.recipient_count == 0
    calls.clear()
    svc.sweep_assigned(db, utcnow())
    assert calls == []  # not even looked at again


def test_switched_off_assignee_gets_nothing(db, pushes, admin_client, staff):
    device(db, staff)
    set_pref(db, staff, "task_assigned", False)
    create(admin_client, assignee_user_ids=[staff.id])
    assert pushes == []


# --- 9. Lead tasks: only "assigned" by default ----------------------------------------------


def test_lead_task_has_no_due_reminders_by_default(db, pushes, staff, staff2):
    device(db, staff)
    device(db, staff2)
    set_pref(db, staff2, "fix_issues_task_due", True)  # opted back in
    now = utcnow()
    for who in (staff, staff2):
        auto_task(db, "issue_leads", f"{who.id}:2026-10-15", user=who,
                  due_at=now - dt.timedelta(hours=1, minutes=5), created=now - dt.timedelta(hours=8))
        auto_task(db, "issue_leads", f"{who.id}:2026-10-16", user=who,
                  due_at=now + dt.timedelta(minutes=20), created=now - dt.timedelta(hours=8))
    svc.send_reminders(db, now)
    assert reached(pushes) == ["sam"]
    assert sorted(p["title"] for p in pushes) == ["Due in 30 minutes", "Overdue task"]


def test_due_soon_is_off_by_default_but_overdue_stays(db, pushes, admin, staff):
    device(db, staff)
    now = utcnow()
    for due in (now + dt.timedelta(minutes=20), now - dt.timedelta(hours=1, minutes=5)):
        db.add(Task(
            title="Mop", requires="none", priority="normal", assignee_user_id=staff.id, status="open", due_at=due,
            created_by_user_id=admin.id, created_by_name="boss", created_at=now - dt.timedelta(hours=5),
            updated_at=now - dt.timedelta(hours=5),
        ))
    db.commit()
    svc.send_reminders(db, now)
    assert [p["title"] for p in pushes] == ["Overdue task"]


# --- 10. Steps ------------------------------------------------------------------------------


def test_steps_add_rename_tick_remove_with_history(db, admin_client, staff, staff_client):
    t = create(admin_client, assignee_user_ids=[staff.id], items=["Wipe"])
    out = admin_client.post(f"/api/team/tasks/{t['id']}/items", json={"text": "Sweep"}).json()
    assert [i["text"] for i in out["items"]] == ["Wipe", "Sweep"] and out["item_count"] == 2
    wipe, sweep = out["items"]
    # The assignee ticks; only the creator / an admin renames or removes.
    out = staff_client.patch(f"/api/team/tasks/{t['id']}/items/{wipe['id']}", json={"done": True}).json()
    assert out["items_done"] == 1
    assert staff_client.patch(f"/api/team/tasks/{t['id']}/items/{wipe['id']}", json={"text": "x"}).status_code == 403
    assert staff_client.post(f"/api/team/tasks/{t['id']}/items", json={"text": "x"}).status_code == 403
    assert staff_client.delete(f"/api/team/tasks/{t['id']}/items/{sweep['id']}").status_code == 403
    out = admin_client.patch(f"/api/team/tasks/{t['id']}/items/{sweep['id']}", json={"text": "Sweep well"}).json()
    assert out["items"][1]["text"] == "Sweep well"
    out = admin_client.delete(f"/api/team/tasks/{t['id']}/items/{sweep['id']}").json()
    assert [i["text"] for i in out["items"]] == ["Wipe"]
    staff_client.patch(f"/api/team/tasks/{t['id']}/items/{wipe['id']}", json={"done": False})
    events = admin_client.get(f"/api/team/tasks/{t['id']}").json()["events"]
    assert [e["kind"] for e in events] == [
        "created", "items_edited", "item_done", "items_edited", "items_edited", "item_undone",
    ]
    assert events[2]["detail"] == {"item_id": wipe["id"], "text": "Wipe"}
    assert events[4]["detail"] == {"removed": [{"id": sweep["id"], "text": "Sweep well"}]}


def test_replace_items_keeps_ticks_and_reorders(db, admin_client, staff, staff_client):
    t = create(admin_client, assignee_user_ids=[staff.id], items=["A", "B", "C"])
    a, b, c = admin_client.get(f"/api/team/tasks/{t['id']}").json()["items"]
    staff_client.patch(f"/api/team/tasks/{t['id']}/items/{b['id']}", json={"done": True})
    r = admin_client.put(
        f"/api/team/tasks/{t['id']}/items",
        json={"items": [{"id": b["id"], "text": "B!"}, {"text": "D"}, {"id": a["id"], "text": "A"}, {"text": "  "}]},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(i["text"], i["done"]) for i in items] == [("B!", True), ("D", False), ("A", False)]
    detail = r.json()["events"][-1]["detail"]
    assert detail["removed"] == [{"id": c["id"], "text": "C"}] and detail["added"][0]["text"] == "D"
    assert detail["renamed"] == [{"id": b["id"], "from": "B", "to": "B!"}]
    bad = admin_client.put(f"/api/team/tasks/{t['id']}/items", json={"items": [{"id": 99999, "text": "x"}]})
    assert bad.status_code == 400
    stale = admin_client.put(
        f"/api/team/tasks/{t['id']}/items", json={"items": [], "expected_updated_at": t["updated_at"]}
    )
    assert stale.status_code == 409 and len(stale.json()["task"]["items"]) == 3


def test_old_clients_tick_body_still_works(db, admin_client, staff, staff_client):
    t = create(admin_client, assignee_user_ids=[staff.id], items=["One"])
    item = admin_client.get(f"/api/team/tasks/{t['id']}").json()["items"][0]
    r = staff_client.patch(f"/api/team/tasks/{t['id']}/items/{item['id']}", json={"done": True})
    assert r.status_code == 200 and r.json()["items"][0]["done"] is True
    assert staff_client.patch(f"/api/team/tasks/{t['id']}/items/{item['id']}", json={}).status_code == 400


# --- 12. Long-poll -------------------------------------------------------------------------------


def test_updates_without_wait_is_unchanged(db, admin_client, staff_client, staff):
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]
    t = create(admin_client, assignee_user_ids=[staff.id])
    body = staff_client.get(f"/api/team/updates?since={cursor}").json()
    assert set(body) == {"cursor", "resync_required", "changes", "badges"}
    assert {"id": t["id"], "action": "created"} in body["changes"]["task"]


def test_updates_include_tasks(db, admin_client, staff_client, staff, staff2):
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]
    t = create(admin_client, assignee_user_ids=[staff.id])
    create(admin_client, assignee_user_ids=[staff2.id])  # not staff's: not in its feed at all
    body = staff_client.get(f"/api/team/updates?since={cursor}&include=tasks").json()
    assert [x["id"] for x in body["tasks"]] == [t["id"]] and body["tasks"][0]["title"] == "Clean the cooler"


def test_long_poll_answers_at_once_when_something_is_already_new(db, admin_client, staff_client, staff):
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]
    create(admin_client, assignee_user_ids=[staff.id])
    started = time.monotonic()
    body = staff_client.get(f"/api/team/updates?since={cursor}&wait=10").json()
    assert body["changes"]["task"] and time.monotonic() - started < 3


def test_long_poll_wakes_on_a_commit_from_another_thread(db, admin, staff_client, staff):
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]

    def later():
        time.sleep(0.6)
        from app.db import SessionLocal

        s = SessionLocal()
        try:
            svc.create_tasks(s, s.get(type(admin), admin.id), title="From the tick", assignee_user_ids=[staff.id])
        finally:
            s.close()

    threading.Thread(target=later).start()
    started = time.monotonic()
    body = staff_client.get(f"/api/team/updates?since={cursor}&wait=15").json()
    took = time.monotonic() - started
    assert body["changes"]["task"] and 0.4 < took < 8


def test_long_poll_times_out_empty_and_ignores_others_changes(db, admin_client, staff_client, staff2):
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]

    def later():
        time.sleep(0.3)
        create(admin_client, assignee_user_ids=[staff2.id])  # not visible to staff

    threading.Thread(target=later).start()
    started = time.monotonic()
    body = staff_client.get(f"/api/team/updates?since={cursor}&wait=1").json()
    assert body["changes"] == {} and body["resync_required"] is False
    assert 0.9 < time.monotonic() - started < 5


# --- 14. Source hooks + action deep link ------------------------------------------------------


def test_checklist_submission_closes_its_task_now(db, staff):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    today = utcnow().astimezone(NY).date()
    task = auto_task(db, "checklist", f"store_closing:{today.isoformat()}", team=managers)
    db.add(ChecklistSubmission(
        checklist_type="store_closing", submitted_by_user_id=staff.id, submitted_by_name="ravi",
        answers_json="{}", all_clear=True, answers_hash="h2", created_at=utcnow(),
    ))
    db.commit()
    assert task_sources_changed("checklist") == 1
    db.refresh(task)
    assert task.status == "done"


def test_order_placed_endpoint_closes_the_order_task_now(db, admin_client, staff):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    db.add(VendorSchedule(vendor="Chetak", delivery_weekdays="[0,1,2,3,4,5,6]", lead_days=1, active=True))
    db.commit()
    today = utcnow().astimezone(NY).date()
    task = auto_task(
        db, "order_due", f"Chetak:{today.isoformat()}:{(today + dt.timedelta(days=1)).isoformat()}", team=managers
    )
    r = admin_client.post("/api/order-actions", json={"vendor": "Chetak", "action": "placed"})
    assert r.status_code == 200, r.text
    db.refresh(task)
    assert (task.status, task.completion_note) == ("done", "Purchase order placed")


def test_critical_skip_week_cancels_the_order_task(db, staff):
    from app.models import CriticalVendorDismissal

    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    today = utcnow().astimezone(NY).date()
    task = auto_task(db, "order_due", f"Chetak:{today.isoformat()}:{(today + dt.timedelta(days=2)).isoformat()}",
                     team=managers)
    db.add(CriticalVendorDismissal(vendor="Chetak", kind="skip_week", until=utcnow() + dt.timedelta(days=3),
                                   created_at=utcnow()))
    db.commit()
    task_sources_changed("order")
    db.refresh(task)
    assert (task.status, task.completion_note) == ("cancelled", "Skipped this week (Critical)")


def test_source_sync_never_raises(monkeypatch):
    monkeypatch.setattr(auto_tasks, "sync_invoice_tasks", lambda db, now: 1 / 0)
    assert task_sources_changed("invoice") == 0


def test_action_field(db, admin_client, staff):
    managers = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, managers, staff)
    lead = auto_task(db, "issue_leads", f"{staff.id}:2026-10-15", user=staff, progress_total=10, progress_done=4)
    order = auto_task(db, "order_due", "Raja Foods:2026-10-15:2026-10-17", team=managers)
    out = admin_client.get(f"/api/team/tasks/{lead.id}").json()
    assert out["action"]["route"] == "fix_issues" and out["action"]["web_path"] == f"/fix-issues?lead={staff.id}"
    assert out["action_route"] == "fix_issues" and out["is_auto"] is True
    assert out["progress"] == {"done": 4, "total": 10, "left": 6}
    out = admin_client.get(f"/api/team/tasks/{order.id}").json()
    assert out["action"]["params"] == {"vendor": "Raja Foods", "delivery_date": "2026-10-17"}
    plain = create(admin_client, assignee_user_ids=[staff.id])
    assert (plain["action"], plain["action_route"], plain["progress"], plain["is_auto"]) == (None, None, None, False)


# --- 15. Lead live progress ---------------------------------------------------------------------


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_lead_task_counts_down_and_closes_itself(db, admin, admin_client, pushes):
    from app.services import issue_leads
    from tests.test_issue_leads import at, only_missing, save, snap

    sasha = make_user(db, "Sasha Durga", features=["team_tasks", "inventory_reports"])
    snap(db, "rice1", "Sona Masoori 20lb", category="Oils & Fats", supplier="Chetak")
    snap(db, "rice2", "Basmati 10lb", category="Oils & Fats", supplier="Chetak")
    db.commit()
    only_missing(db, admin)
    save(db, admin, sections=[("Oils & Ghee", sasha.id)])
    issue_leads.run_daily_tasks(db, at(10, 5))
    task = db.query(Task).filter(Task.source_type == "issue_leads").one()
    assert task.title == "Fix Issues: 2 left of 2"
    sasha_client = client_for(db, sasha)
    body = {"issue_type": "missing_barcode", "issue_keys": ["rice1"], "reason": "fixed"}
    assert sasha_client.post("/api/issues/close", json=body).status_code == 200
    db.refresh(task)
    assert (task.title, task.progress_done, task.progress_total, task.status) == ("Fix Issues: 1 left of 2", 1, 2, "open")
    # A reopen counts it again.
    sasha_client.post("/api/issues/reopen", json={"issue_type": "missing_barcode", "issue_keys": ["rice1"]})
    db.refresh(task)
    assert task.title == "Fix Issues: 2 left of 2"
    sasha_client.post("/api/issues/close", json={**body, "issue_keys": ["rice1", "rice2"]})
    db.refresh(task)
    assert (task.status, task.completion_note) == ("done", "All 2 items fixed or closed")


# --- 16. Filters, bulk, My team today ------------------------------------------------------------


def test_list_filters(db, admin_client, staff, staff2):
    create(admin_client, assignee_user_ids=[staff.id], title="Clean the cooler")
    create(admin_client, assignee_user_ids=[staff2.id], title="Count the till")
    auto_task(db, "issue_leads", f"{staff.id}:2026-10-15", user=staff)

    def titles(**params):
        r = admin_client.get("/api/team/tasks", params={"view": "all", **params})
        assert r.status_code == 200, r.text
        return sorted(t["title"] for t in r.json()["items"])

    assert titles(assignee_user_id=staff.id) == ["Clean the cooler", "issue_leads task"]
    assert titles(source="auto") == ["issue_leads task"]
    assert titles(source="manual") == ["Clean the cooler", "Count the till"]
    assert titles(source="issue_leads") == ["issue_leads task"]
    assert titles(q="cooler") == ["Clean the cooler"]
    assert titles(q="the TILL") == ["Count the till"]
    assert titles(q="100%_") == []


def test_bulk_done_and_cancel(db, admin_client, staff, staff_client):
    a = create(admin_client, assignee_user_ids=[staff.id])
    b = create(admin_client, assignee_user_ids=[staff.id], requires="photo")
    c = create(admin_client, assignee_user_ids=[staff.id])
    staff_client.post(f"/api/team/tasks/{c['id']}/complete")
    assert staff_client.post("/api/team/tasks/bulk", json={"task_ids": [a["id"]], "action": "done"}).status_code == 403
    r = admin_client.post("/api/team/tasks/bulk", json={"task_ids": [a["id"], b["id"], c["id"], 9999], "action": "done"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["done"] == [a["id"]]
    assert {s["id"]: s["reason"] for s in out["skipped"]} == {
        b["id"]: "needs a photo", c["id"]: "not open", 9999: "not found",
    }
    r = admin_client.post("/api/team/tasks/bulk", json={"task_ids": [b["id"]], "action": "cancel"})
    assert r.json()["tasks"][0]["status"] == "cancelled"
    assert admin_client.post("/api/team/tasks/bulk", json={"task_ids": [], "action": "done"}).status_code == 400
    assert admin_client.post("/api/team/tasks/bulk", json={"task_ids": [1], "action": "nuke"}).status_code == 400


def test_team_today(db, admin_client, staff, staff_client, staff2):
    now = utcnow()
    create(admin_client, assignee_user_ids=[staff.id], due_at=(now - dt.timedelta(hours=1)).isoformat())
    create(admin_client, assignee_user_ids=[staff.id])
    done = create(admin_client, assignee_user_ids=[staff2.id])
    client_for(db, staff2).post(f"/api/team/tasks/{done['id']}/complete")
    out = admin_client.get("/api/team/task-summary").json()
    rows = {r["name"]: r for r in out["people"]}
    assert (rows["ravi"]["open"], rows["ravi"]["overdue"]) == (2, 1)
    assert rows["sam"]["done_today"] == 1
    assert out["totals"]["open"] == 2 and out["totals"]["done_today"] == 1
    assert staff_client.get("/api/team/task-summary").status_code == 403


# --- 17. Schedules for the app ---------------------------------------------------------------------


def test_get_one_schedule_and_its_text(db, admin_client, staff, staff_client):
    tpl = admin_client.post(
        "/api/team/templates",
        json={"title": "Mop", "rrule": "FREQ=WEEKLY;BYDAY=MO,WE;BYHOUR=15", "assignee_user_id": staff.id},
    ).json()
    assert tpl["schedule_text"] == "Every Mon, Wed at 3:00 PM"
    one = admin_client.get(f"/api/team/templates/{tpl['id']}").json()
    assert one["id"] == tpl["id"] and one["title"] == "Mop"
    assert admin_client.get("/api/team/templates/9999").status_code == 404
    assert staff_client.get(f"/api/team/templates/{tpl['id']}").status_code == 403


@pytest.mark.parametrize(
    "rule,text",
    [
        ("FREQ=DAILY;BYHOUR=9", "Every day at 9:00 AM"),
        ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=7;BYMINUTE=30", "Weekdays at 7:30 AM"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=SA;BYHOUR=10", "Every 2 weeks on Sat at 10:00 AM"),
        ("FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=8", "Every month on the 1st at 8:00 AM"),
        ("FREQ=DAILY;BYHOUR=9,17", "Every day at 9:00 AM, 5:00 PM"),
        ("", "Doesn't repeat"),
    ],
)
def test_schedule_text(rule, text):
    assert svc.schedule_text(rule) == text


# --- Pushes after the response; new opt-in task pushes ---------------------------------------------


def test_create_push_runs_in_the_background(db, pushes, admin_client, staff, monkeypatch):
    device(db, staff)
    order = []
    real = svc.push_assigned
    monkeypatch.setattr(svc, "push_assigned", lambda d, *a: order.append("push") or real(d, *a))
    create(admin_client, assignee_user_ids=[staff.id])
    assert order == ["push"] and reached(pushes) == ["ravi"]


def test_done_and_comment_pushes_are_opt_in(db, pushes, admin, admin_client, staff, staff_client):
    device(db, admin)
    device(db, staff)
    t = create(admin_client, assignee_user_ids=[staff.id])
    pushes.clear()
    staff_client.post(f"/api/team/tasks/{t['id']}/comments", json={"body": "On it"})
    staff_client.post(f"/api/team/tasks/{t['id']}/complete")
    assert pushes == []
    set_pref(db, admin, "task_done", True)
    set_pref(db, admin, "task_comment", True)
    t2 = create(admin_client, assignee_user_ids=[staff.id])
    pushes.clear()
    staff_client.post(f"/api/team/tasks/{t2['id']}/comments", json={"body": "On it"})
    staff_client.post(f"/api/team/tasks/{t2['id']}/complete")
    assert [(p["title"], reached([p])) for p in pushes] == [
        ("Comment: Clean the cooler", ["boss"]), ("Task done", ["boss"]),
    ]
    assert pushes[0]["body"] == "ravi: On it" and pushes[0]["data"]["route"] == "task"


# --- Demo: every new write route is refused -----------------------------------------------------------


def test_demo_is_refused_on_the_new_write_routes(db, admin_client):
    from app.demo_guard import DEMO_READ_ONLY

    demo = make_user(db, "app_store_review", features=["team_tasks"], can_assign=True)
    demo_client = client_for(db, demo)
    t = create(demo_client, title="Mine", items=["one"])
    item = t and demo_client.get(f"/api/team/tasks/{t['id']}").json()["items"][0]
    for method, path, body in (
        ("post", f"/api/team/tasks/{t['id']}/items", {"text": "x"}),
        ("put", f"/api/team/tasks/{t['id']}/items", {"items": []}),
        ("delete", f"/api/team/tasks/{t['id']}/items/{item['id']}", None),
        ("post", "/api/team/tasks/bulk", {"task_ids": [t["id"]], "action": "done"}),
    ):
        kw = {"json": body} if body is not None else {}
        r = getattr(demo_client, method)(path, **kw)
        assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY}), path
    # Reads it may use: its own task (and the summary is refused politely).
    assert demo_client.get(f"/api/team/updates?since=0&include=tasks").status_code == 200
    assert demo_client.get("/api/team/task-summary").status_code == 403


def test_migration_0059_adds_and_drops_the_progress_columns(tmp_path):
    import os
    import pathlib
    import sqlite3
    import subprocess
    import sys

    backend = pathlib.Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=backend, env=env, check=True, capture_output=True)

    def cols():
        con = sqlite3.connect(db_file)
        try:
            return {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
        finally:
            con.close()

    run("upgrade", "0059")
    assert {"progress_total", "progress_done"} <= cols()
    run("downgrade", "0058")
    assert not {"progress_total", "progress_done"} & cols()
    run("upgrade", "0059")
    assert {"progress_total", "progress_done"} <= cols()


def test_schedule_for_an_empty_team_generates_nothing(db, admin, staff, team):
    from app.team import recurrence
    from app.team.tasks_models import TaskTemplate

    tpl = TaskTemplate(
        title="Mop", requires="none", priority="normal", assignee_team_id=team.id,
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0", timezone="America/New_York", generate_ahead_minutes=420,
        active=True, created_by_user_id=admin.id, created_by_name="boss",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), updated_at=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
    )
    db.add(tpl)
    db.commit()
    when = dt.datetime(2026, 9, 28, 8, 0, tzinfo=NY)
    recurrence.generate_occurrences(db, when)
    assert db.query(Task).filter(Task.template_id == tpl.id).count() == 0
    add_member(db, team, staff)
    recurrence.generate_occurrences(db, when + dt.timedelta(minutes=5))
    assert db.query(Task).filter(Task.template_id == tpl.id).count() == 1


def test_demo_cant_rename_steps_even_on_its_own_task(db):
    demo = make_user(db, "app_store_review", features=["team_tasks"])
    demo_client = client_for(db, demo)
    t = create(demo_client, title="Mine", items=["one"])
    detail = demo_client.get(f"/api/team/tasks/{t['id']}").json()
    assert detail["can"]["edit_items"] is False
    item = detail["items"][0]
    assert demo_client.patch(f"/api/team/tasks/{t['id']}/items/{item['id']}", json={"text": "x"}).status_code == 403
    # Ticking its own step stays allowed (as before).
    assert demo_client.patch(f"/api/team/tasks/{t['id']}/items/{item['id']}", json={"done": True}).status_code == 200
