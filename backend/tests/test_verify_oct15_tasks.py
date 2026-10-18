"""Adversarial verification of the Oct 15 team-tasks batch (docs/
BATCH_OCT15_CONTRACT.md "As built - TEAM TASKS"): each test pins a defect
found by the verifier, fixed in the smallest way."""

import datetime as dt

import pytest

from app.models import IssueResolution
from app.team import tasks_service as svc
from app.team.tasks_models import Task
from tests.conftest import client_for, make_user


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    monkeypatch.setattr(svc, "_quiet", lambda now: False)


# --- Lead progress: an item that came back is not "done" ------------------------------------


def _lead_setup(db, admin, items):
    from app.services import issue_leads
    from tests.test_issue_leads import only_missing, save, snap

    sai = make_user(db, "Sai Durga", features=["team_tasks", "inventory_reports"])
    for item_id, name in items:
        snap(db, item_id, name, category="Oils & Fats", supplier="Chetak")
    db.commit()
    only_missing(db, admin)
    save(db, admin, sections=[("Oils & Ghee", sai.id)])
    return sai, issue_leads


def _came_back(db, item_id):
    """Closed as fixed two days ago, but today's upload still shows it -
    Fix Issues lists it as open again ("came back"), and its resolution row
    stays in issue_resolutions."""
    db.add(
        IssueResolution(
            issue_type="missing_barcode", issue_key=item_id, item_id=item_id, reason="fixed",
            closed_by_name="Sai Durga", closed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2),
        )
    )
    db.commit()


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_lead_progress_does_not_count_an_item_that_came_back_as_done(db, admin):
    from tests.test_issue_leads import at

    sai, issue_leads = _lead_setup(db, admin, [("rice1", "Sona Masoori 20lb"), ("rice2", "Basmati 10lb")])
    _came_back(db, "rice1")
    issue_leads.run_daily_tasks(db, at(10, 5))
    task = db.query(Task).filter(Task.source_type == "issue_leads").one()
    assert task.progress_total == 2  # rice1 is on today's list again
    issue_leads.refresh_lead_progress(db)
    db.refresh(task)
    assert (task.title, task.progress_done, task.status) == ("Fix Issues: 2 left of 2", 0, "open")
    # Closing it again today does count.
    body = {"issue_type": "missing_barcode", "issue_keys": ["rice1"], "reason": "fixed"}
    assert client_for(db, sai).post("/api/issues/close", json=body).status_code == 200
    db.refresh(task)
    assert (task.title, task.progress_done) == ("Fix Issues: 1 left of 2", 1)


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_a_lead_list_of_only_came_back_items_does_not_close_itself_at_once(db, admin):
    from tests.test_issue_leads import at

    sai, issue_leads = _lead_setup(db, admin, [("rice1", "Sona Masoori 20lb")])
    _came_back(db, "rice1")
    issue_leads.run_daily_tasks(db, at(10, 5))
    task = db.query(Task).filter(Task.source_type == "issue_leads").one()
    issue_leads.refresh_lead_progress(db)
    db.refresh(task)
    assert (task.status, task.title) == ("open", "Fix Issues: 1 left of 1")


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_a_reopened_lead_task_still_shows_its_live_count(db, admin):
    """Someone reopened it (so the system won't close it again) - its "N left
    of M" must still move, not freeze at the last number."""
    from app.team import auto_tasks
    from app.team.tasks_models import TaskEvent
    from tests.test_issue_leads import at

    sai, issue_leads = _lead_setup(db, admin, [("rice1", "Sona Masoori 20lb")])
    issue_leads.run_daily_tasks(db, at(10, 5))
    task = db.query(Task).filter(Task.source_type == "issue_leads").one()
    db.add(TaskEvent(task_id=task.id, kind="reopened", user_name="Sai Durga", created_at=task.created_at))
    db.commit()
    body = {"issue_type": "missing_barcode", "issue_keys": ["rice1"], "reason": "fixed"}
    assert client_for(db, sai).post("/api/issues/close", json=body).status_code == 200
    db.refresh(task)
    assert task.status == "open"  # reopened by a person: stays open
    assert (task.title, task.progress_done) == ("Fix Issues: 0 left of 1", 1)


# --- Every task 409 carries the task -------------------------------------------------------------


def test_reopen_of_an_already_open_task_409_carries_the_task(db, admin_client, staff):
    r = admin_client.post("/api/team/tasks", json={"title": "Mop", "assignee_user_ids": [staff.id]})
    tid = r.json()["tasks"][0]["id"]
    r = admin_client.post(f"/api/team/tasks/{tid}/reopen")
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "This task is already open"  # builds 5-9 show this string
    assert body["task"]["id"] == tid and body["task"]["status"] == "open"


# --- Probes that should already hold (kept as regression guards) ---------------------------------


def _task(client, **body):
    body.setdefault("title", "Clean the cooler")
    r = client.post("/api/team/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["tasks"][0]


def test_put_items_with_a_stale_version_409_with_the_task(db, admin_client, staff):
    t = _task(admin_client, assignee_user_ids=[staff.id], items=["One"])
    seen = t["updated_at"]
    admin_client.patch(f"/api/team/tasks/{t['id']}", json={"title": "Renamed"})
    r = admin_client.put(f"/api/team/tasks/{t['id']}/items", json={"items": [{"text": "Two"}], "expected_updated_at": seen})
    assert r.status_code == 409 and r.json()["task"]["title"] == "Renamed"
    detail = admin_client.get(f"/api/team/tasks/{t['id']}").json()
    assert [i["text"] for i in detail["items"]] == ["One"]  # nothing changed


def test_step_writes_on_a_finished_task_are_refused_and_change_nothing(db, admin_client, staff, staff_client):
    t = _task(admin_client, assignee_user_ids=[staff.id], items=["One"])
    item = admin_client.get(f"/api/team/tasks/{t['id']}").json()["items"][0]
    assert staff_client.post(f"/api/team/tasks/{t['id']}/complete").status_code == 200
    for method, path, body in (
        ("post", f"/api/team/tasks/{t['id']}/items", {"text": "x"}),
        ("put", f"/api/team/tasks/{t['id']}/items", {"items": []}),
        ("delete", f"/api/team/tasks/{t['id']}/items/{item['id']}", None),
        ("patch", f"/api/team/tasks/{t['id']}/items/{item['id']}", {"text": "y"}),
    ):
        kw = {"json": body} if body is not None else {}
        r = getattr(admin_client, method)(path, **kw)
        assert r.status_code == 409 and r.json()["task"]["status"] == "done", path
    detail = admin_client.get(f"/api/team/tasks/{t['id']}").json()
    assert [i["text"] for i in detail["items"]] == ["One"]


def test_an_assignee_may_tick_but_not_rename_or_add_steps(db, admin_client, staff, staff_client):
    t = _task(admin_client, assignee_user_ids=[staff.id], items=["One"])
    item = admin_client.get(f"/api/team/tasks/{t['id']}").json()["items"][0]
    assert staff_client.patch(f"/api/team/tasks/{t['id']}/items/{item['id']}", json={"text": "Mine"}).status_code == 403
    assert staff_client.post(f"/api/team/tasks/{t['id']}/items", json={"text": "x"}).status_code == 403
    r = staff_client.patch(f"/api/team/tasks/{t['id']}/items/{item['id']}", json={"done": True})
    assert r.status_code == 200 and r.json()["can"]["edit_items"] is False


def test_bulk_validates_its_input(db, admin_client, staff):
    for body in ({"task_ids": [], "action": "done"}, {"task_ids": ["1"], "action": "done"},
                 {"task_ids": [1], "action": "delete"}, {"task_ids": list(range(201)), "action": "cancel"}):
        assert admin_client.post("/api/team/tasks/bulk", json=body).status_code == 400, body


def test_summary_needs_an_assigner(db, staff_client):
    assert staff_client.get("/api/team/task-summary").status_code == 403


def test_long_poll_wait_is_capped_and_validated(db, staff_client, monkeypatch):
    from app.team import changes

    waited = []

    from app.team import router_core

    real_fresh = router_core._updates_fresh

    async def fake_wait(version, timeout):
        waited.append(timeout)
        return True

    def fresh(*a):
        out = real_fresh(*a)
        if waited:  # the second look: pretend something arrived
            out["changes"] = {"task": [{"id": 0, "action": "updated"}]}
        return out

    monkeypatch.setattr(changes, "wait_for_change", fake_wait)
    monkeypatch.setattr(router_core, "_updates_fresh", fresh)
    assert staff_client.get("/api/team/updates?since=0&wait=-1").status_code == 422
    cursor = staff_client.get("/api/team/updates?since=0").json()["cursor"]
    r = staff_client.get(f"/api/team/updates?since={cursor}&wait=999&include=tasks")
    assert r.status_code == 200 and r.json()["tasks"] == []
    assert len(waited) == 1 and 19 < waited[0] <= 20


def test_include_tasks_for_a_login_without_tasks_is_an_empty_list(db, admin_client, staff):
    chat_only = make_user(db, "chatty", features=["team_chat"])
    c = client_for(db, chat_only)
    cursor = c.get("/api/team/updates?since=0").json()["cursor"]
    _task(admin_client, assignee_user_ids=[staff.id])
    r = c.get(f"/api/team/updates?since={cursor}&include=tasks")
    assert r.status_code == 200 and r.json().get("tasks") == []


def test_someone_elses_task_is_not_leaked_through_include_tasks(db, admin_client, staff, staff2):
    c = client_for(db, staff2)
    cursor = c.get("/api/team/updates?since=0").json()["cursor"]
    t = _task(admin_client, assignee_user_ids=[staff.id], title="Private")
    r = c.get(f"/api/team/updates?since={cursor}&include=tasks").json()
    assert all(x["id"] != t["id"] for x in r.get("tasks", []))
