"""Team hub Phase 1: tasks, batches, recurring templates, reminders and the
change feed (docs/TEAM_HUB_DESIGN.md §5-§8, §12)."""

import datetime as dt
import io
import zoneinfo

import pytest
from PIL import Image

from app.models import DeviceToken
from app.services import push
from app.services.notifications import set_pref
from app.team import recurrence
from app.team import tasks_service as svc
from app.team.changes import get_updates
from app.team.tasks_models import Task, TaskTemplate
from app.timeutil import utcnow
from tests.conftest import add_member, client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")


def ny(*args) -> dt.datetime:
    """Store-local wall time -> aware UTC."""
    return dt.datetime(*args, tzinfo=NY).astimezone(dt.timezone.utc)


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    """API-level tests shouldn't depend on the wall clock hitting quiet
    hours; tests about quiet hours call the functions with an explicit now."""
    real = svc._quiet
    monkeypatch.setattr(svc, "_quiet", lambda now: False)
    return real


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data, "thread_id": thread_id})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


def device(db, user):
    db.add(DeviceToken(user_id=user.id, token=f"tok-{user.username}", platform="ios"))
    db.commit()


def reached(sent):
    return sorted({t.replace("tok-", "") for s in sent for t in s["tokens"]})


def create(client, **body):
    body.setdefault("title", "Clean the cooler")
    r = client.post("/api/team/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- Access ------------------------------------------------------------------------


def test_tasks_need_the_feature(db):
    nobody = make_user(db, "noaccess", features=["daily_checklists"])
    c = client_for(db, nobody)
    assert c.get("/api/team/tasks").status_code == 403
    assert c.post("/api/team/tasks", json={"title": "x"}).status_code == 403


def test_tasks_kill_switch(monkeypatch, staff_client):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "team_tasks_enabled", False)
    assert staff_client.get("/api/team/tasks").status_code == 503


def test_staff_can_only_create_for_themselves(staff_client, staff2, team):
    mine = create(staff_client)
    assert mine["tasks"][0]["assignee"] == {"type": "user", "id": staff_client.user.id, "name": "ravi"}
    assert mine["assignment_batch_id"] is None
    r = staff_client.post("/api/team/tasks", json={"title": "x", "assignee_user_ids": [staff2.id]})
    assert r.status_code == 403
    r = staff_client.post("/api/team/tasks", json={"title": "x", "assignee_team_id": team.id})
    assert r.status_code == 403


def test_can_assign_user_may_assign_to_others(db, staff2):
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    out = create(client_for(db, lead), assignee_user_ids=[staff2.id])
    assert out["tasks"][0]["assignee"]["id"] == staff2.id


def test_assignee_needs_tasks_access(db, admin_client):
    nobody = make_user(db, "noaccess", features=[])
    r = admin_client.post("/api/team/tasks", json={"title": "x", "assignee_user_ids": [nobody.id]})
    assert r.status_code == 400


def test_unrelated_user_cannot_see_or_touch_a_task(admin_client, staff_client, staff2_client):
    task = create(admin_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]
    tid = task["id"]
    assert [t["id"] for t in staff_client.get("/api/team/tasks").json()["items"]] == [tid]
    assert staff2_client.get("/api/team/tasks").json()["items"] == []
    # Direct-ID access: 404, never 403, so ids reveal nothing.
    assert staff2_client.get(f"/api/team/tasks/{tid}").status_code == 404
    assert staff2_client.post(f"/api/team/tasks/{tid}/complete").status_code == 404
    assert staff2_client.post(f"/api/team/tasks/{tid}/cant-do", json={"reason": "no"}).status_code == 404
    assert staff2_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 404
    assert staff2_client.post(f"/api/team/tasks/{tid}/comments", json={"body": "hi"}).status_code == 404
    assert staff2_client.patch(f"/api/team/tasks/{tid}", json={"title": "x"}).status_code == 404
    assert staff2_client.patch(f"/api/team/tasks/{tid}/items/1", json={"done": True}).status_code == 404
    assert staff2_client.get("/api/team/tasks/999999").status_code == 404
    # The assignee may act but not edit.
    assert staff_client.patch(f"/api/team/tasks/{tid}", json={"title": "x"}).status_code == 403


def test_all_view_is_admin_only(admin_client, staff_client):
    create(admin_client, assignee_user_ids=[staff_client.user.id])
    assert staff_client.get("/api/team/tasks?view=all").status_code == 403
    assert len(admin_client.get("/api/team/tasks?view=all").json()["items"]) == 1


def test_created_view(admin_client, staff_client):
    create(admin_client, assignee_user_ids=[staff_client.user.id])
    assert len(admin_client.get("/api/team/tasks?view=created").json()["items"]) == 1
    assert staff_client.get("/api/team/tasks?view=created").json()["items"] == []


# --- Team tasks ------------------------------------------------------------------------


def test_team_task_any_member_completes_once(db, admin_client, staff_client, staff2_client, team):
    add_member(db, team, staff_client.user)
    task = create(admin_client, assignee_team_id=team.id)["tasks"][0]
    assert task["assignee"]["type"] == "team" and task["assignee"]["name"] == "Kitchen"
    tid = task["id"]
    assert [t["id"] for t in staff_client.get("/api/team/tasks?view=team").json()["items"]] == [tid]
    assert [t["id"] for t in staff_client.get("/api/team/tasks").json()["items"]] == [tid]
    # Not a member: can't see it.
    assert staff2_client.get(f"/api/team/tasks/{tid}").status_code == 404
    assert staff2_client.get("/api/team/tasks?view=team").json()["items"] == []

    done = staff_client.post(f"/api/team/tasks/{tid}/complete").json()
    assert done["status"] == "done" and done["completed_by"]["name"] == "ravi"
    # Joining later, sam can see it but it's already done - first one wins.
    add_member(db, team, staff2_client.user)
    assert staff2_client.post(f"/api/team/tasks/{tid}/complete").status_code == 409


# --- Multi-person assignment (batch) ------------------------------------------------------


def test_multi_person_assignment_is_one_batch(db, admin_client, staff_client, staff2_client):
    out = create(admin_client, title="Clean freezer", assignee_user_ids=[staff_client.user.id, staff2_client.user.id])
    batch = out["assignment_batch_id"]
    assert batch and len(out["tasks"]) == 2
    assert {t["assignment_batch_id"] for t in out["tasks"]} == {batch}

    summary = admin_client.get(f"/api/team/task-batches/{batch}").json()
    assert (summary["title"], summary["total"], summary["done"]) == ("Clean freezer", 2, 0)
    ravi_task = next(t for t in out["tasks"] if t["assignee"]["id"] == staff_client.user.id)
    staff_client.post(f"/api/team/tasks/{ravi_task['id']}/complete")
    assert admin_client.get(f"/api/team/task-batches/{batch}").json()["done"] == 1
    detail = admin_client.get(f"/api/team/tasks/{ravi_task['id']}").json()
    assert detail["batch"]["done"] == 1
    # Assignees don't manage the batch.
    assert staff_client.get(f"/api/team/task-batches/{batch}").status_code == 404
    assert staff_client.get(f"/api/team/tasks/{ravi_task['id']}").json()["batch"] is None

    cancelled = admin_client.post(f"/api/team/task-batches/{batch}/cancel").json()
    statuses = {a["name"]: a["status"] for a in cancelled["assignees"]}
    assert statuses == {"ravi": "done", "sam": "cancelled"}
    assert cancelled["total"] == 1 and cancelled["done"] == 1


# --- Completing ---------------------------------------------------------------------------


def test_photo_required(db, admin_client, staff_client, staff2_client):
    tid = create(admin_client, assignee_user_ids=[staff_client.user.id], requires="photo")["tasks"][0]["id"]
    r = staff_client.post(f"/api/team/tasks/{tid}/complete", data={"note": "done"})
    assert r.status_code == 400
    r = staff_client.post(
        f"/api/team/tasks/{tid}/complete", files={"photo": ("x.png", png_bytes(), "image/png")}
    )
    assert r.status_code == 200, r.text
    photo = r.json()["completion_photo"]
    assert photo["url"].startswith("/api/team/media/")
    assert staff_client.get(photo["url"]).status_code == 200
    assert admin_client.get(photo["url"]).status_code == 200  # creator
    assert staff2_client.get(photo["url"]).status_code == 404  # unrelated


def test_photo_by_token_and_bad_image(admin_client, staff_client):
    tid = create(admin_client, assignee_user_ids=[staff_client.user.id], requires="photo")["tasks"][0]["id"]
    r = staff_client.post(
        f"/api/team/tasks/{tid}/complete", files={"photo": ("x.png", b"not an image", "image/png")}
    )
    assert r.status_code == 415
    token = staff_client.post("/api/team/media", files={"file": ("x.png", png_bytes(), "image/png")}).json()["token"]
    r = staff_client.post(f"/api/team/tasks/{tid}/complete", data={"photo_token": token})
    assert r.status_code == 200 and r.json()["completion_photo"]["token"] == token


def test_someone_elses_upload_cant_be_attached(admin_client, staff_client, staff2_client):
    tid = create(admin_client, assignee_user_ids=[staff_client.user.id], requires="photo")["tasks"][0]["id"]
    token = staff2_client.post("/api/team/media", files={"file": ("x.png", png_bytes(), "image/png")}).json()["token"]
    r = staff_client.post(f"/api/team/tasks/{tid}/complete", data={"photo_token": token})
    assert r.status_code == 400


def test_note_required(admin_client, staff_client):
    tid = create(admin_client, assignee_user_ids=[staff_client.user.id], requires="note")["tasks"][0]["id"]
    assert staff_client.post(f"/api/team/tasks/{tid}/complete", data={"note": "  "}).status_code == 400
    r = staff_client.post(f"/api/team/tasks/{tid}/complete", data={"note": "Cooler at 38F"})
    assert r.json()["completion_note"] == "Cooler at 38F"
    # Already done.
    assert staff_client.post(f"/api/team/tasks/{tid}/complete", data={"note": "x"}).status_code == 409


def test_cant_do_and_reopen(db, admin_client, staff_client):
    tid = create(admin_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]["id"]
    assert staff_client.post(f"/api/team/tasks/{tid}/cant-do", json={"reason": " "}).status_code == 400
    out = staff_client.post(f"/api/team/tasks/{tid}/cant-do", json={"reason": "No cleaner left"}).json()
    assert out["status"] == "cant_do" and out["cant_do_reason"] == "No cleaner left"
    assert out["can"]["reopen"] is True  # whoever marked it
    reopened = admin_client.post(f"/api/team/tasks/{tid}/reopen").json()
    assert reopened["status"] == "open" and reopened["cant_do_reason"] is None
    kinds = [e["kind"] for e in reopened["events"]]
    assert kinds == ["created", "cant_do", "reopened"]
    assert admin_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 409


def test_reopen_needs_creator_admin_or_completer(db, admin_client, staff_client, staff2_client, team):
    add_member(db, team, staff_client.user)
    add_member(db, team, staff2_client.user)
    tid = create(admin_client, assignee_team_id=team.id)["tasks"][0]["id"]
    staff_client.post(f"/api/team/tasks/{tid}/complete")
    assert staff2_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 403
    assert staff_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 200


def test_items_comments_and_edit(admin_client, staff_client):
    task = create(
        admin_client, assignee_user_ids=[staff_client.user.id], items=["Shelves", "Floor", " "]
    )["tasks"][0]
    tid = task["id"]
    assert task["item_count"] == 2
    detail = staff_client.get(f"/api/team/tasks/{tid}").json()
    item = detail["items"][0]
    out = staff_client.patch(f"/api/team/tasks/{tid}/items/{item['id']}", json={"done": True}).json()
    assert out["items_done"] == 1 and out["items"][0]["done_by"] == "ravi"
    out = staff_client.post(f"/api/team/tasks/{tid}/comments", json={"body": "On it"}).json()
    assert out["events"][-1]["detail"] == {"body": "On it"}

    due = "2030-01-15T15:00:00"  # naive = store-local wall time
    out = admin_client.patch(f"/api/team/tasks/{tid}", json={"due_at": due, "priority": "urgent"}).json()
    assert out["due_at_local"].startswith("2030-01-15T15:00:00-05:00")
    assert out["priority"] == "urgent"
    out = admin_client.patch(f"/api/team/tasks/{tid}", json={"due_at": None}).json()
    assert out["due_at"] is None
    out = admin_client.patch(f"/api/team/tasks/{tid}", json={"cancelled": True}).json()
    assert out["status"] == "cancelled"


def test_reassign(db, admin_client, staff_client, staff2_client):
    tid = create(admin_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]["id"]
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]
    out = admin_client.patch(f"/api/team/tasks/{tid}", json={"assignee_user_id": staff2_client.user.id}).json()
    assert out["assignee"]["name"] == "sam"
    assert out["events"][-1]["kind"] == "reassigned"
    assert staff_client.get("/api/team/tasks").json()["items"] == []
    assert staff2_client.get(f"/api/team/tasks/{tid}").status_code == 200
    # The old assignee's feed hears about it, so the task leaves their list.
    changes = staff_client.get(f"/api/team/updates?since={cursor}").json()["changes"]
    assert changes["task"] == [{"id": tid, "action": "updated"}]


# --- Change feed + badge -----------------------------------------------------------------


def test_change_feed_visibility(db, admin_client, staff_client, staff2_client, team):
    add_member(db, team, staff_client.user)
    start = admin_client.get("/api/team/bootstrap").json()["cursor"]
    personal = create(admin_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]["id"]
    team_task = create(admin_client, assignee_team_id=team.id)["tasks"][0]["id"]
    ravi = staff_client.get(f"/api/team/updates?since={start}").json()["changes"].get("task", [])
    sam = staff2_client.get(f"/api/team/updates?since={start}").json()["changes"].get("task", [])
    assert {c["id"] for c in ravi} == {personal, team_task}
    assert sam == []


def test_tasks_due_badge(db, admin_client, staff_client):
    now = utcnow()
    create(admin_client, assignee_user_ids=[staff_client.user.id], due_at=(now - dt.timedelta(hours=2)).isoformat())
    create(admin_client, assignee_user_ids=[staff_client.user.id], due_at=(now + dt.timedelta(days=3)).isoformat())
    create(admin_client, assignee_user_ids=[staff_client.user.id])  # no due date
    assert staff_client.get("/api/team/bootstrap").json()["badges"]["tasks_due"] == 1


# --- Recurrence rules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    ["", "FREQ=YEARLY;BYHOUR=9", "FREQ=DAILY", "FREQ=DAILY;BYHOUR=9;COUNT=3", "FREQ=WEEKLY;BYDAY=XX;BYHOUR=9",
     "FREQ=DAILY;BYHOUR=25", "FREQ=WEEKLY;BYMONTHDAY=3;BYHOUR=9"],
)
def test_invalid_rules(rule):
    with pytest.raises(recurrence.RRuleError):
        recurrence.normalize_rrule(rule)


def test_rule_normalized():
    assert recurrence.normalize_rrule("rrule:freq=weekly;byday=mo,we;byhour=15") == (
        "FREQ=WEEKLY;BYDAY=MO,WE;BYHOUR=15;BYMINUTE=0"
    )


def test_dst_spring_forward_moves_to_next_valid_time():
    # 2026-03-08 02:30 doesn't exist in New York -> 03:00 EDT (07:00 UTC).
    assert recurrence.local_to_utc(dt.datetime(2026, 3, 8, 2, 30), NY) == dt.datetime(
        2026, 3, 8, 7, 0, tzinfo=dt.timezone.utc
    )
    dues = recurrence.due_times_between(
        "FREQ=DAILY;BYHOUR=2;BYMINUTE=30", None, ny(2026, 3, 1), ny(2026, 3, 7), ny(2026, 3, 9, 12)
    )
    assert [d.astimezone(NY).strftime("%m-%d %H:%M %Z") for d in dues] == [
        "03-07 02:30 EST", "03-08 03:00 EDT", "03-09 02:30 EDT",
    ]


def test_dst_fall_back_takes_first_occurrence():
    # 2026-11-01 01:30 happens twice; the first is EDT (05:30 UTC).
    assert recurrence.local_to_utc(dt.datetime(2026, 11, 1, 1, 30), NY) == dt.datetime(
        2026, 11, 1, 5, 30, tzinfo=dt.timezone.utc
    )


def test_daily_3pm_stays_3pm_local_across_dst():
    dues = recurrence.due_times_between(
        "FREQ=DAILY;BYHOUR=15", None, ny(2026, 10, 30), ny(2026, 10, 31), ny(2026, 11, 2, 23)
    )
    assert [d.astimezone(NY).hour for d in dues] == [15, 15, 15]
    assert [d.hour for d in dues] == [19, 20, 20]  # Oct 31 EDT, Nov 1-2 EST


def test_preview_endpoint(admin_client, staff_client):
    r = admin_client.post(
        "/api/team/templates/preview",
        json={"rrule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=15", "generate_ahead_minutes": 420},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 5
    first = dt.datetime.fromisoformat(rows[0]["due_at_local"])
    assert first.weekday() == 0 and first.hour == 15
    assert dt.datetime.fromisoformat(rows[0]["generate_at_local"]).hour == 8
    assert admin_client.post("/api/team/templates/preview", json={"rrule": "FREQ=DAILY"}).status_code == 400
    assert staff_client.post("/api/team/templates/preview", json={"rrule": "FREQ=DAILY;BYHOUR=9"}).status_code == 403


# --- Templates + generation ------------------------------------------------------------------


def make_template(db, admin, **kw):
    fields = dict(
        title="Clean walk-in cooler",
        requires="photo",
        priority="normal",
        assignee_user_id=kw.pop("assignee_user_id", None),
        assignee_team_id=kw.pop("assignee_team_id", None),
        rrule="FREQ=WEEKLY;BYDAY=MO;BYHOUR=15;BYMINUTE=0",
        timezone="America/New_York",
        generate_ahead_minutes=420,
        active=True,
        created_by_user_id=admin.id,
        created_by_name=admin.username,
        created_at=kw.pop("created_at", ny(2026, 9, 1)),
        updated_at=ny(2026, 9, 1),
    )
    fields.update(kw)
    tpl = TaskTemplate(**fields)
    db.add(tpl)
    db.commit()
    return tpl


def template_tasks(db, tpl):
    db.expire_all()
    return db.query(Task).filter(Task.template_id == tpl.id).order_by(Task.due_at).all()


def test_generation_appears_ahead_and_is_idempotent(db, admin, staff):
    tpl = make_template(db, admin, assignee_user_id=staff.id)
    # Monday 2026-09-28: due 3 PM, appears 8 AM.
    assert recurrence.generate_occurrences(db, ny(2026, 9, 28, 7, 59)) == []
    created = recurrence.generate_occurrences(db, ny(2026, 9, 28, 8, 0))
    assert len(created) == 1
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 8, 5))
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 14, 0))
    tasks = template_tasks(db, tpl)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.due_at == ny(2026, 9, 28, 15, 0)
    assert (task.title, task.requires, task.assignee_user_id) == ("Clean walk-in cooler", "photo", staff.id)


def test_catch_up_window(db, admin, staff):
    tpl = make_template(db, admin, assignee_user_id=staff.id)
    # Server down Monday; back 5 h after the 3 PM due time: still created.
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 20, 0))
    assert len(template_tasks(db, tpl)) == 1
    # A week later, 13 h late: skipped.
    recurrence.generate_occurrences(db, ny(2026, 10, 6, 4, 0))
    assert len(template_tasks(db, tpl)) == 1


def test_nothing_before_the_template_existed(db, admin, staff):
    tpl = make_template(db, admin, assignee_user_id=staff.id, created_at=ny(2026, 9, 28, 16, 0))
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 20, 0))
    assert template_tasks(db, tpl) == []


def test_template_edits_affect_future_only(db, admin_client, admin, staff):
    tpl = make_template(db, admin, assignee_user_id=staff.id)
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 9, 0))
    r = admin_client.patch(f"/api/team/templates/{tpl.id}", json={"title": "Deep clean cooler"})
    assert r.status_code == 200
    db.expire_all()  # the edit was committed by the API's own session
    recurrence.generate_occurrences(db, ny(2026, 10, 5, 9, 0))
    titles = [t.title for t in template_tasks(db, tpl)]
    assert titles == ["Clean walk-in cooler", "Deep clean cooler"]


def test_inactive_template_generates_nothing(db, admin_client, admin, staff):
    tpl = make_template(db, admin, assignee_user_id=staff.id)
    admin_client.delete(f"/api/team/templates/{tpl.id}")
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 9, 0))
    assert template_tasks(db, tpl) == []


def test_template_api_permissions(db, admin_client, staff_client, staff, team):
    add_member(db, team, staff)
    body = {"title": "Mop", "rrule": "FREQ=DAILY;BYHOUR=21", "assignee_team_id": team.id}
    assert staff_client.post("/api/team/templates", json=body).status_code == 403
    assert staff_client.get("/api/team/templates").status_code == 403
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    lead_client = client_for(db, lead)
    assert lead_client.post("/api/team/templates", json=body).status_code == 403  # admins only
    out = admin_client.post("/api/team/templates", json=body).json()
    assert out["rrule"] == "FREQ=DAILY;BYHOUR=21;BYMINUTE=0" and out["generate_ahead_minutes"] == 420
    assert out["next"]["due_at_local"].endswith(("-04:00", "-05:00"))
    assert [t["title"] for t in lead_client.get("/api/team/templates").json()] == ["Mop"]
    assert admin_client.post("/api/team/templates", json={**body, "rrule": "FREQ=DAILY"}).status_code == 400
    assert admin_client.post("/api/team/templates", json={"title": "x", "rrule": "FREQ=DAILY;BYHOUR=9"}).status_code == 400


# --- Push notifications -------------------------------------------------------------------------


def test_assigned_push_goes_to_assignee_not_actor(db, pushes, admin, admin_client, staff, staff2):
    for u in (admin, staff, staff2):
        device(db, u)
    task = create(admin_client, assignee_user_ids=[staff.id])["tasks"][0]
    assert reached(pushes) == ["ravi"]
    assert pushes[0]["data"]["route"] == "task" and pushes[0]["data"]["task_id"] == str(task["id"])
    # The sweep doesn't repeat it.
    svc.sweep_assigned(db, utcnow())
    assert len(pushes) == 1


def test_self_task_sends_nothing(db, pushes, staff, staff_client):
    device(db, staff)
    create(staff_client)
    svc.sweep_assigned(db, utcnow())
    assert pushes == []


def test_team_task_push_to_members_except_actor(db, pushes, admin, admin_client, staff, staff2, team):
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    for u in (admin, staff, staff2, lead):
        device(db, u)
    add_member(db, team, staff)
    add_member(db, team, lead)
    create(client_for(db, lead), assignee_team_id=team.id)
    assert reached(pushes) == ["ravi"]


def test_assigned_during_quiet_hours_waits_for_the_sweep(db, pushes, _daytime, monkeypatch, admin, staff):
    monkeypatch.setattr(svc, "_quiet", _daytime)  # real quiet hours
    device(db, staff)
    task = Task(
        title="Restock", requires="none", priority="normal", assignee_user_id=staff.id, status="open",
        created_by_user_id=admin.id, created_by_name="boss", created_at=ny(2026, 9, 28, 23, 0),
        updated_at=ny(2026, 9, 28, 23, 0),
    )
    db.add(task)
    db.commit()
    assert svc.notify_assigned(db, task, admin.id, now=ny(2026, 9, 28, 23, 0)) == 0
    assert pushes == []
    svc.sweep_assigned(db, ny(2026, 9, 29, 7, 5))
    assert reached(pushes) == ["ravi"]


def test_urgent_bypasses_quiet_hours(db, pushes, _daytime, monkeypatch, admin, staff):
    monkeypatch.setattr(svc, "_quiet", _daytime)
    device(db, staff)
    task = Task(
        title="Freezer alarm", requires="none", priority="urgent", assignee_user_id=staff.id, status="open",
        created_by_user_id=admin.id, created_by_name="boss", created_at=ny(2026, 9, 28, 23, 0),
        updated_at=ny(2026, 9, 28, 23, 0),
    )
    db.add(task)
    db.commit()
    svc.notify_assigned(db, task, admin.id, now=ny(2026, 9, 28, 23, 0))
    assert reached(pushes) == ["ravi"] and pushes[0]["title"] == "Urgent task"


def _due_task(db, admin, staff, due, priority="normal", created=None):
    task = Task(
        title="Mop floor", requires="none", priority=priority, assignee_user_id=staff.id, status="open",
        due_at=due, created_by_user_id=admin.id, created_by_name="boss",
        created_at=created or due - dt.timedelta(hours=5), updated_at=due - dt.timedelta(hours=5),
    )
    db.add(task)
    db.commit()
    return task


def test_soon_and_overdue_reminders(db, pushes, admin, staff, staff2):
    for u in (admin, staff, staff2):
        device(db, u)
    set_pref(db, staff, "task_due_soon", True)  # off by default since Oct 15
    due = ny(2026, 9, 28, 15, 0)
    _due_task(db, admin, staff, due)
    svc.send_reminders(db, ny(2026, 9, 28, 14, 20))
    assert pushes == []  # not yet inside 30 min
    svc.send_reminders(db, ny(2026, 9, 28, 14, 35))
    assert reached(pushes) == ["ravi"] and pushes[0]["title"] == "Due in 30 minutes"
    svc.send_reminders(db, ny(2026, 9, 28, 14, 40))
    assert len(pushes) == 1  # deduped
    pushes.clear()
    svc.send_reminders(db, ny(2026, 9, 28, 16, 5))
    assert reached(pushes) == ["boss", "ravi"] and pushes[0]["title"] == "Overdue task"
    svc.send_reminders(db, ny(2026, 9, 28, 16, 10))
    assert len(pushes) == 1


def test_moving_the_due_time_rearms_reminders(db, pushes, admin, staff):
    device(db, staff)
    set_pref(db, staff, "task_due_soon", True)  # off by default since Oct 15
    task = _due_task(db, admin, staff, ny(2026, 9, 28, 15, 0))
    svc.send_reminders(db, ny(2026, 9, 28, 14, 35))
    task.due_at = ny(2026, 9, 28, 17, 0)
    db.commit()
    svc.send_reminders(db, ny(2026, 9, 28, 16, 35))
    assert len(pushes) == 2


def test_quiet_hours_drop_soon_but_keep_overdue(db, pushes, _daytime, monkeypatch, admin, staff):
    monkeypatch.setattr(svc, "_quiet", _daytime)
    device(db, staff)
    _due_task(db, admin, staff, ny(2026, 9, 28, 23, 0))
    svc.send_reminders(db, ny(2026, 9, 28, 22, 40))
    assert pushes == []  # soon dropped
    svc.send_reminders(db, ny(2026, 9, 29, 0, 5))
    assert pushes == []  # overdue waits
    svc.send_reminders(db, ny(2026, 9, 29, 7, 0))
    assert [p["title"] for p in pushes] == ["Overdue task"]


def test_urgent_reminder_at_night(db, pushes, _daytime, monkeypatch, admin, staff):
    monkeypatch.setattr(svc, "_quiet", _daytime)
    device(db, staff)
    set_pref(db, staff, "task_due_soon", True)  # off by default since Oct 15
    _due_task(db, admin, staff, ny(2026, 9, 28, 23, 0), priority="urgent")
    svc.send_reminders(db, ny(2026, 9, 28, 22, 40))
    assert [p["title"] for p in pushes] == ["Due in 30 minutes"]


def test_cant_do_notifies_creator_only(db, pushes, admin, admin_client, staff, staff_client):
    device(db, admin)
    device(db, staff)
    tid = create(admin_client, assignee_user_ids=[staff.id])["tasks"][0]["id"]
    pushes.clear()
    staff_client.post(f"/api/team/tasks/{tid}/cant-do", json={"reason": "Out of bags"})
    assert reached(pushes) == ["boss"]
    assert pushes[0]["body"] == "ravi: Out of bags"


def test_push_needs_tasks_feature_and_pref(db, pushes, admin_client, staff, team):
    from app.models import NotificationPref

    nobody = make_user(db, "noaccess", features=[])
    add_member(db, team, staff)
    add_member(db, team, nobody)
    device(db, staff)
    device(db, nobody)
    db.add(NotificationPref(user_id=staff.id, key="task_assigned", enabled=False))
    db.commit()
    create(admin_client, assignee_team_id=team.id)
    assert pushes == []


def test_generated_occurrence_is_announced_by_the_tick(db, pushes, admin, staff):
    device(db, admin)
    device(db, staff)
    make_template(db, admin, assignee_user_id=staff.id)
    svc.tasks_tick(db, ny(2026, 9, 28, 8, 0))
    assert reached(pushes) == ["ravi"]
    svc.tasks_tick(db, ny(2026, 9, 28, 8, 5))
    assert len(pushes) == 1


def test_generated_task_is_in_the_feed(db, admin, staff):
    make_template(db, admin, assignee_user_id=staff.id)
    recurrence.generate_occurrences(db, ny(2026, 9, 28, 8, 0))
    changes = get_updates(db, staff, 0)["changes"]
    assert len(changes["task"]) == 1 and changes["task"][0]["action"] == "created"


def test_assignees_lists_tasks_users_for_assigners_only(db, admin, staff, staff2):
    """A can_assign lead without chat still gets the people picker; a
    regular user only sees themselves; users without Tasks aren't listed."""
    make_user(db, "nochat_notasks", features=["reorder_candidates"])
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)

    names = [p["name"] for p in client_for(db, lead).get("/api/team/assignees").json()["people"]]
    assert "ravi" in names and "sam" in names and "lead" in names and "boss" in names
    assert "nochat_notasks" not in names

    own = client_for(db, staff2).get("/api/team/assignees").json()["people"]
    assert own == [{"id": staff2.id, "name": "sam"}]

    assert client_for(db, make_user(db, "outsider")).get("/api/team/assignees").status_code == 403


# --- Review fixes -------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_generation_state(monkeypatch):
    # recurrence keeps per-process memory (last run, logged templates).
    monkeypatch.setitem(recurrence._run_state, "last_run", None)  # per store (platform B)
    monkeypatch.setattr(recurrence, "_warned_assignee", set())


def test_simultaneous_completion_first_one_wins(db, admin_client, staff_client, staff2_client, team):
    """Two members finish the same team task at once: the second gets a 409
    and the first completion is kept (it used to be silently overwritten)."""
    from fastapi import HTTPException

    from app.db import SessionLocal

    add_member(db, team, staff_client.user)
    add_member(db, team, staff2_client.user)
    tid = create(admin_client, assignee_team_id=team.id)["tasks"][0]["id"]
    # Sam's request read the task while it was still open...
    other = SessionLocal()
    stale = other.get(Task, tid)
    assert stale.status == "open"
    # ...then Ravi finished it first.
    assert staff_client.post(f"/api/team/tasks/{tid}/complete").status_code == 200
    import app.team.tasks_service as tasks_mod

    original = tasks_mod.get_task_for
    tasks_mod.get_task_for = lambda d, u, i: stale
    try:
        with pytest.raises(HTTPException) as e:
            svc.complete_task(other, staff2_client.user, tid, note="me too")
        assert e.value.status_code == 409
    finally:
        tasks_mod.get_task_for = original
        other.close()
    db.expire_all()
    task = db.get(Task, tid)
    assert task.completed_by_name == "ravi" and task.completion_note is None
    kinds = [e["kind"] for e in admin_client.get(f"/api/team/tasks/{tid}").json()["events"]]
    assert kinds.count("done") == 1


def test_cant_do_and_cancel_are_atomic_too(db, admin_client, staff_client):
    from fastapi import HTTPException

    from app.db import SessionLocal

    tid = create(admin_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]["id"]
    other = SessionLocal()
    stale = other.get(Task, tid)
    assert admin_client.patch(f"/api/team/tasks/{tid}", json={"cancelled": True}).status_code == 200
    import app.team.tasks_service as tasks_mod

    original = tasks_mod.get_task_for
    tasks_mod.get_task_for = lambda d, u, i: stale
    try:
        with pytest.raises(HTTPException) as e:
            svc.cant_do_task(other, staff_client.user, tid, "no bags")
        assert e.value.status_code == 409
    finally:
        tasks_mod.get_task_for = original
        other.close()
    db.expire_all()
    assert db.get(Task, tid).status == "cancelled"


def test_reopen_of_cancelled_is_creator_or_admin_only(db, admin_client, staff_client, staff2_client):
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    lead_client = client_for(db, lead)
    tid = create(lead_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]["id"]
    assert lead_client.patch(f"/api/team/tasks/{tid}", json={"cancelled": True}).status_code == 200
    assert staff_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 403
    assert lead_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 200
    # Reopening twice: the second is a conflict, not a second "reopened".
    assert lead_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 409


def test_open_list_says_when_it_is_truncated(db, admin, staff_client):
    now = ny(2026, 9, 28, 9, 0)
    for i in range(svc.OPEN_LIST_LIMIT + 3):
        db.add(
            Task(
                title=f"stale {i}", requires="none", priority="normal", assignee_user_id=staff_client.user.id,
                status="open", due_at=now - dt.timedelta(days=30, minutes=i), created_by_user_id=admin.id,
                created_by_name="boss", created_at=now, updated_at=now,
            )
        )
    db.commit()
    body = staff_client.get("/api/team/tasks", params={"view": "mine"}).json()
    assert len(body["items"]) == svc.OPEN_LIST_LIMIT and body["truncated"] is True
    done = staff_client.get("/api/team/tasks", params={"view": "mine", "status": "done"}).json()
    assert done["truncated"] is False


def test_task_list_query_count_does_not_grow_per_task(db, admin, staff, team):
    """The list loads teams, item counts and photos once per page."""
    from sqlalchemy import event

    from app.db import engine

    add_member(db, team, staff)
    now = ny(2026, 9, 28, 9, 0)

    def count_queries(n_tasks):
        for i in range(n_tasks):
            db.add(
                Task(
                    title=f"t{i}", requires="none", priority="normal", assignee_team_id=team.id, status="open",
                    created_by_user_id=admin.id, created_by_name="boss", created_at=now, updated_at=now,
                )
            )
        db.commit()
        statements = []
        listener = lambda *a, **k: statements.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            svc.list_tasks(db, staff, "mine", "open")
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        return len(statements)

    few = count_queries(3)
    many = count_queries(30)
    assert many <= few + 2, (few, many)


def test_cant_do_at_night_reaches_the_creator_in_the_morning(db, pushes, _daytime, monkeypatch, admin, staff):
    monkeypatch.setattr(svc, "_quiet", _daytime)
    device(db, admin)
    night = ny(2026, 9, 28, 22, 30)
    task = Task(
        title="Mop", requires="none", priority="normal", assignee_user_id=staff.id, status="cant_do",
        cant_do_reason="No mop", completed_by_user_id=staff.id, completed_by_name="ravi",
        completed_at=night, created_by_user_id=admin.id, created_by_name="boss",
        created_at=night, updated_at=night,
    )
    db.add(task)
    db.commit()
    assert svc.notify_cant_do(db, task, staff.id, now=night) == 0
    svc.sweep_cant_do(db, ny(2026, 9, 29, 2, 0))
    assert pushes == []
    svc.sweep_cant_do(db, ny(2026, 9, 29, 7, 5))
    assert reached(pushes) == ["boss"]
    svc.sweep_cant_do(db, ny(2026, 9, 29, 7, 10))
    assert len(pushes) == 1


def test_cant_do_again_after_reopen_is_reported_again(db, pushes, admin, admin_client, staff, staff_client):
    device(db, admin)
    tid = create(admin_client, assignee_user_ids=[staff.id])["tasks"][0]["id"]
    staff_client.post(f"/api/team/tasks/{tid}/cant-do", json={"reason": "No bags"})
    admin_client.post(f"/api/team/tasks/{tid}/reopen")
    import time

    time.sleep(1.1)  # the dedupe key carries the second it was marked
    staff_client.post(f"/api/team/tasks/{tid}/cant-do", json={"reason": "Still no bags"})
    assert [p["body"] for p in pushes if p["title"].startswith("Can't do")] == [
        "ravi: No bags",
        "ravi: Still no bags",
    ]


def test_assigned_push_is_sent_under_the_lock(db, monkeypatch, admin, staff):
    """The request's notify_assigned and the 5-minute sweep run in different
    threads; every task_assigned send happens while holding one process-wide
    lock, so whichever comes second sees the first one's log row and sends
    nothing (dispatch_alert's check-send-log isn't atomic on its own)."""
    device(db, staff)
    held = []
    real = svc.dispatch_alert

    def spy(db_, alert):
        held.append(svc._ASSIGN_PUSH_LOCK.locked())
        return real(db_, alert)

    monkeypatch.setattr(svc, "dispatch_alert", spy)
    monkeypatch.setattr(push, "is_configured", lambda: True)
    monkeypatch.setattr(
        push, "send_to_tokens",
        lambda tokens, *a, **k: push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens)),
    )
    now = ny(2026, 9, 28, 10, 0)
    task = Task(
        title="Restock", requires="none", priority="normal", assignee_user_id=staff.id, status="open",
        created_by_user_id=admin.id, created_by_name="boss", created_at=now, updated_at=now,
    )
    db.add(task)
    db.commit()
    assert svc.notify_assigned(db, task, admin.id, now=now) == 1
    assert svc.sweep_assigned(db, now) == 0  # already sent: logged under the lock
    assert held and all(held)


def test_preview_is_anchored_like_generation(db, admin_client, admin, staff):
    """Every other Monday, created on a Monday: "next" and the editor preview
    show the same Mondays the generator creates."""
    tpl = make_template(
        db, admin, assignee_user_id=staff.id, rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
        created_at=ny(2026, 9, 21, 6, 0), generate_ahead_minutes=60,
    )
    generated = []
    for day in (21, 28):
        generated += recurrence.generate_occurrences(db, ny(2026, 9, day, 8, 30))
    generated += recurrence.generate_occurrences(db, ny(2026, 10, 5, 8, 30))
    dues = sorted(t.due_at for t in template_tasks(db, tpl))
    assert dues == [ny(2026, 9, 21, 9, 0), ny(2026, 10, 5, 9, 0)]
    r = admin_client.post(
        "/api/team/templates/preview",
        json={"rrule": tpl.rrule, "generate_ahead_minutes": 60, "template_id": tpl.id},
    )
    assert r.status_code == 200, r.text
    previewed = [dt.datetime.fromisoformat(p["due_at_utc"]) for p in r.json()]
    for p in previewed:
        # Always a multiple of two weeks from the first generated Monday.
        assert (p - ny(2026, 9, 21, 9, 0)).days % 14 == 0
    out = svc.template_out(db, tpl)
    assert (dt.datetime.fromisoformat(out["next"]["due_at_utc"]) - ny(2026, 9, 21, 9, 0)).days % 14 == 0


def test_rule_change_replaces_untouched_future_occurrences(db, admin_client, admin, staff, staff_client):
    tpl = make_template(db, admin, assignee_user_id=staff.id, rrule="FREQ=DAILY;BYHOUR=15;BYMINUTE=0",
                        generate_ahead_minutes=36 * 60, created_at=ny(2026, 9, 1))
    import app.team.tasks_service as tasks_mod

    monkey_now = ny(2026, 9, 28, 9, 0)
    recurrence.generate_occurrences(db, monkey_now)
    dues = [t.due_at for t in template_tasks(db, tpl)]
    assert dues == [ny(2026, 9, 28, 15, 0), ny(2026, 9, 29, 15, 0)]
    # Someone commented on tomorrow's - it's been worked on, so it stays.
    worked = template_tasks(db, tpl)[1]
    staff_client.post(f"/api/team/tasks/{worked.id}/comments", json={"body": "will do"})
    original = tasks_mod.utcnow
    tasks_mod.utcnow = lambda: monkey_now
    try:
        r = admin_client.patch(f"/api/team/templates/{tpl.id}", json={"rrule": "FREQ=DAILY;BYHOUR=16"})
    finally:
        tasks_mod.utcnow = original
    assert r.status_code == 200
    db.expire_all()
    recurrence.generate_occurrences(db, monkey_now)
    dues = [t.due_at for t in template_tasks(db, tpl)]
    assert ny(2026, 9, 28, 15, 0) not in dues  # untouched 3 PM one replaced
    assert ny(2026, 9, 28, 16, 0) in dues
    assert ny(2026, 9, 29, 15, 0) in dues  # the worked one kept


def test_deactivating_clears_future_and_reactivating_regenerates(db, admin_client, admin, staff):
    tpl = make_template(db, admin, assignee_user_id=staff.id, rrule="FREQ=DAILY;BYHOUR=23;BYMINUTE=0",
                        generate_ahead_minutes=24 * 60, created_at=ny(2026, 9, 1))
    now = dt.datetime.now(dt.timezone.utc)
    recurrence.generate_occurrences(db, now)
    assert any(t.due_at > now for t in template_tasks(db, tpl))
    assert admin_client.delete(f"/api/team/templates/{tpl.id}").status_code == 200
    assert [t for t in template_tasks(db, tpl) if t.due_at > now and t.status == "open"] == []
    assert admin_client.patch(f"/api/team/templates/{tpl.id}", json={"active": True}).status_code == 200
    db.expire_all()
    recurrence.generate_occurrences(db, now)
    assert any(t.due_at > now for t in template_tasks(db, tpl))


def test_generation_skips_a_template_whose_assignee_is_gone(db, admin, staff, team, caplog):
    gone = make_template(db, admin, assignee_user_id=staff.id)
    archived = make_template(db, admin, assignee_team_id=team.id)
    staff.allowed_features = "[]"
    team.archived_at = ny(2026, 9, 20)
    db.commit()
    with caplog.at_level("WARNING"):
        recurrence.generate_occurrences(db, ny(2026, 9, 28, 9, 0))
        recurrence.generate_occurrences(db, ny(2026, 9, 28, 9, 5))
    assert template_tasks(db, gone) == [] and template_tasks(db, archived) == []
    warnings = [r.message for r in caplog.records if "generates nothing" in r.message]
    assert len(warnings) == 2, "logged once per template, not every tick"


def test_at_most_24_occurrences_a_day(admin_client, team):
    body = {"title": "Spam", "assignee_team_id": team.id,
            "rrule": "FREQ=DAILY;BYHOUR=" + ",".join(str(h) for h in range(24)) + ";BYMINUTE=0,30"}
    r = admin_client.post("/api/team/templates", json=body)
    assert r.status_code == 400 and "24" in r.json()["detail"]


def test_occurrences_missed_by_downtime_are_logged(db, admin, staff, caplog):
    make_template(db, admin, assignee_user_id=staff.id)  # Mondays 3 PM
    recurrence.generate_occurrences(db, ny(2026, 9, 27, 12, 0))
    with caplog.at_level("WARNING"):
        # Down from Sunday noon to Tuesday 9 AM: Monday 3 PM is 18 h late.
        recurrence.generate_occurrences(db, ny(2026, 9, 29, 9, 0))
    assert any("skipped 1 occurrence" in r.message for r in caplog.records)


def test_template_changes_reach_assigners_feed(db, admin_client, admin, team):
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    add_member(db, team, lead)
    out = admin_client.post(
        "/api/team/templates", json={"title": "Mop", "rrule": "FREQ=DAILY;BYHOUR=21", "assignee_team_id": team.id}
    ).json()
    changes = get_updates(db, lead, 0)["changes"]
    assert [c["id"] for c in changes.get("task_template", [])] == [out["id"]]


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=DAILY;BYHOUR=9",
        "FREQ=DAILY;INTERVAL=3;BYHOUR=9,17;BYMINUTE=15",
        "FREQ=WEEKLY;BYHOUR=9",
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;BYHOUR=6",
        "FREQ=WEEKLY;INTERVAL=3;BYHOUR=23;BYMINUTE=45",
        "FREQ=MONTHLY;BYMONTHDAY=31;BYHOUR=9",
        "FREQ=MONTHLY;INTERVAL=2;BYHOUR=9",
        "FREQ=MONTHLY;BYDAY=MO;BYHOUR=9",
        "FREQ=MONTHLY;BYMONTHDAY=-1;BYHOUR=9",
    ],
)
@pytest.mark.parametrize("created_day", [1, 17, 30])
def test_bounded_expansion_gives_identical_occurrences(rule, created_day):
    """Starting the expansion late (not from the template's creation) must
    never change which occurrences exist."""
    from dateutil.rrule import rrulestr

    anchor = ny(2024, 1 if created_day <= 28 else 3, created_day, 10, 0)
    start, end = ny(2026, 9, 1), ny(2026, 12, 31)
    got = recurrence.due_times_between(rule, "America/New_York", anchor, start, end)
    # Reference: the full expansion from the anchor day, no shortcut.
    anchor_local = anchor.astimezone(NY).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    rr = rrulestr(recurrence.normalize_rrule(rule), dtstart=anchor_local)
    ref = sorted(
        {
            recurrence.local_to_utc(local, NY)
            for local in rr.between(
                start.astimezone(NY).replace(tzinfo=None) - dt.timedelta(hours=3),
                end.astimezone(NY).replace(tzinfo=None) + dt.timedelta(hours=3),
                inc=True,
            )
        }
    )
    ref = [d for d in ref if start <= d <= end]
    assert got == ref and len(got) > 0


def test_complete_refuses_an_oversized_photo_before_reading_it(admin_client, staff_client):
    from app.team import media as media_mod

    tid = create(admin_client, assignee_user_ids=[staff_client.user.id], requires="photo")["tasks"][0]["id"]
    big = b"x" * (media_mod.MAX_REQUEST_BYTES + 10)
    r = staff_client.post(f"/api/team/tasks/{tid}/complete", files={"photo": ("p.jpg", big, "image/jpeg")})
    assert r.status_code == 413
    # A plain completion with no body still works for tasks that need nothing.
    tid2 = create(admin_client, assignee_user_ids=[staff_client.user.id])["tasks"][0]["id"]
    assert staff_client.post(f"/api/team/tasks/{tid2}/complete").status_code == 200


def test_complete_with_an_empty_multipart_body_from_the_app(db, admin_client, staff_client, team):
    """Real bug (Oct 1): the app's empty Dio FormData (task needs no note or
    photo) came without the closing CRLF -> MultipartParseError -> 500."""
    add_member(db, team, staff_client.user)
    tid = create(admin_client, assignee_team_id=team.id)["tasks"][0]["id"]
    boundary = "--dio-boundary-1234567890"
    for body in (f"--{boundary}--".encode(), f"--{boundary}--\r\n".encode(), b""):
        r = staff_client.post(
            f"/api/team/tasks/{tid}/complete",
            content=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        assert r.status_code in (200, 409), (body, r.status_code, r.text)
    assert staff_client.get(f"/api/team/tasks/{tid}").json()["status"] == "done"
