"""Demo read-only guard (docs/APP_REVIEW_BUILD7_CONTRACT.md, "Demo read-only guard").

1. The App Review demo login may look at everything, but every mutating route
   is refused (403 DEMO_READ_ONLY) unless it's in app/demo_guard.ALLOWLIST -
   enumerated from app.routes, so a NEW route is blocked by default.
2. Demo-made invoices / checklist submissions are hidden from real staff,
   admins (unless include_demo=true), reports, store-wide logic and jobs.
3. The 9 AM Fix Issues reopen push ignores a close made by a demo login.
"""

import datetime as dt
import re

import pytest
from fastapi.routing import APIRoute

from app.demo import hide_demo_clause, hidden_from
from app.demo_guard import ALLOWLIST, DEMO_READ_ONLY, SAFE_METHODS, demo_write_guard
from app.main import app
from app.models import ChecklistSubmission, IssueResolution, IssueResolutionEvent, InvoiceSubmission, User
from app.services import checklist_admin
from app.services import invoice_submission as inv_svc
from app.services.reconciliation import get_invoice_gaps
from app.team.chat_models import Message
from app.team.models import Team
from app.timeutil import restaurant_today, utcnow
from tests.conftest import add_member, client_for, make_user

ALL_FEATURES = [
    "invoice_submission", "daily_checklists", "ask_bot", "team_tasks", "team_chat", "overview",
    "purchase_order_cart", "supplier_projection", "inventory_reports", "reconciliation", "category_edits",
    "fix_issues", "vendor_calendar", "transfers",
]
FSA = "kitchen_fsa"


@pytest.fixture(autouse=True)
def no_real_sends(monkeypatch):
    calls = []
    monkeypatch.setattr(inv_svc, "send_whatsapp_message", lambda text, **kw: calls.append(("whatsapp", text)))
    monkeypatch.setattr(inv_svc, "send_toast_invoice_email", lambda **kw: calls.append(("toast", kw.get("vendor"))))
    monkeypatch.setattr(inv_svc, "convert_to_pdf", lambda src, dst: dst.write_bytes(b"%PDF"))
    return calls


@pytest.fixture
def demo(db):
    return make_user(db, "app_store_review", features=ALL_FEATURES)


@pytest.fixture
def demo_client(db, demo):
    return client_for(db, demo)


def mutating_routes():
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in sorted(r.methods - SAFE_METHODS):
                yield m, r.path


def concrete(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def _submit(client, vendor="Raja Foods", content=b"%PDF-1.4 test"):
    return client.post(
        "/api/invoices",
        data={"submission_type": "invoice", "vendor": vendor, "total_amount": "12.50"},
        files={"file": ("bill.pdf", content, "application/pdf")},
    )


# --- 1. The guard ------------------------------------------------------------------------


def test_every_route_runs_the_guard():
    missing = [
        r.path for r in app.routes
        if isinstance(r, APIRoute) and not any(d.call is demo_write_guard for d in r.dependant.dependencies)
    ]
    assert missing == []


def test_allowlist_only_names_real_routes():
    assert set(ALLOWLIST) <= set(mutating_routes())


def test_every_mutating_route_not_allowlisted_is_refused_for_demo(demo_client):
    """New routes default to blocked: anything not in ALLOWLIST must 403."""
    checked = 0
    for method, path in mutating_routes():
        if (method, path) in ALLOWLIST:
            continue
        r = demo_client.request(method, concrete(path), json={})
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert r.json() == {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY}, (method, path)
        checked += 1
    assert checked > 50


def test_guard_never_touches_a_real_user(db):
    plain = client_for(db, make_user(db, "plain"))
    for method, path in mutating_routes():
        if path == "/api/auth/logout":
            continue
        r = plain.request(method, concrete(path), json={})
        assert DEMO_READ_ONLY not in r.text, (method, path)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/ordering/cart/items"),
        ("PATCH", "/api/ordering/cart/items/1"),
        ("DELETE", "/api/ordering/cart/items/1"),
        ("POST", "/api/ordering/cart/mark-placed"),
        ("POST", "/api/ordering/purchase-order/export-pdf"),
        ("POST", "/api/ordering/orders/1/order-again"),
        ("POST", "/api/vendor-calendar/schedules"),
        ("POST", "/api/vendor-calendar/skips"),
        ("POST", "/api/issues/close"),
        ("POST", "/api/issues/reopen"),
        ("POST", "/api/reconciliation/delivery-confirm"),
        ("POST", "/api/reconciliation/purchases"),
        ("DELETE", "/api/reconciliation/purchases/1"),
        ("POST", "/api/transfers/confirm"),
        ("POST", "/api/inventory/upload"),
        ("PUT", "/api/categories/items/1"),
        ("POST", "/api/admin/category-overrides"),
        ("POST", "/api/auth/users"),
        ("POST", "/api/auth/change-password"),
        ("POST", "/api/checklists/admin/definitions"),
        ("PUT", "/api/checklists/admin/definitions/x/public-link"),
        ("POST", "/api/public/checklists/x/submit"),
        ("POST", "/api/team/tasks/1/reopen"),  # no such task
        ("PATCH", "/api/team/tasks/1"),  # no such task
        ("POST", "/api/team/announcements"),
        ("POST", "/api/team/announcements/1/remind"),
        ("POST", "/api/team/reports/1/resolve"),
    ],
)
def test_named_store_writes_are_refused(demo_client, method, path):
    r = demo_client.request(method, path, json={})
    assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY})


def test_reads_are_unaffected(demo_client, db):
    for path in ("/api/auth/me", "/api/invoices", "/api/checklists", "/api/team/tasks"):
        assert demo_client.get(path).status_code == 200, path


def test_login_logout_and_delete_account_keep_their_own_behaviour(db, demo, demo_client):
    from app.auth import hash_password

    demo.password_hash = hash_password("pw-123456")
    db.commit()
    from fastapi.testclient import TestClient

    r = TestClient(app).post("/api/auth/mobile-login", json={"username": "app_store_review", "password": "pw-123456"})
    assert r.status_code == 200 and r.json()["demo"] is True
    r = demo_client.post("/api/auth/delete-account", json={"password": "pw-123456"})
    assert (r.status_code, r.json()["detail"]) == (403, "The demo account can't be deleted.")
    assert demo_client.post("/api/auth/logout").status_code == 200


def test_invoice_actions_only_on_its_own(db, demo_client, no_real_sends):
    clerk = client_for(db, make_user(db, "clerk", features=["invoice_submission"]))
    real_id = _submit(clerk, content=b"%PDF real").json()["id"]
    own_id = _submit(demo_client).json()["id"]
    for action in ("resend", "mark-toast-closed", "request-delete"):
        body = {"channels": ["whatsapp"]} if action == "resend" else {}
        r = demo_client.post(f"/api/invoices/{real_id}/{action}", json=body)
        assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY}), action
    no_real_sends.clear()
    assert demo_client.post(f"/api/invoices/{own_id}/resend", json={"channels": ["whatsapp"]}).status_code == 200
    assert demo_client.post(f"/api/invoices/{own_id}/request-delete").status_code == 200
    assert no_real_sends == []
    real = db.get(InvoiceSubmission, real_id)
    db.refresh(real)
    assert real.delete_requested_at is None and real.toast_closed_at is None


def test_task_actions_only_on_tasks_assigned_to_it_or_its_team(db, demo, demo_client, admin_client):
    kitchen = db.query(Team).filter(Team.name == "Kitchen").one()
    grocery = db.query(Team).filter(Team.name == "Grocery").one()
    add_member(db, kitchen, demo)
    add_member(db, grocery, make_user(db, "grocer", features=["team_tasks"]))  # Oct 15: no empty teams

    def task(**assignee):
        r = admin_client.post("/api/team/tasks", json={"title": "Wipe the shelf", **assignee})
        assert r.status_code == 200, r.text
        body = r.json()
        return (body.get("tasks") or [body])[0]["id"] if isinstance(body, dict) else body[0]["id"]

    mine = task(assignee_user_ids=[demo.id])
    team = task(assignee_team_id=kitchen.id)
    other = task(assignee_team_id=grocery.id)
    assert demo_client.post(f"/api/team/tasks/{mine}/comments", json={"body": "on it"}).status_code == 200
    assert demo_client.post(f"/api/team/tasks/{team}/complete").status_code == 200
    r = demo_client.post(f"/api/team/tasks/{other}/complete")
    assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY})
    assert demo_client.post(f"/api/team/tasks/{other}/cant-do", json={"reason": "x"}).status_code == 403


# --- Task create / edit / reopen inside the demo's sandbox ---------------------------------


@pytest.fixture
def review(db, demo):
    """The demo's own team (prod: Review), with one real staff member in it."""
    team = Team(name="Review", color="#123456")
    db.add(team)
    db.commit()
    add_member(db, team, demo)
    return team


@pytest.fixture
def pushes(monkeypatch):
    from app.team import tasks_service

    sent = []

    def fake_dispatch(db, alert):
        sent.append((alert.key, set(alert.user_ids or [])))
        return len(alert.user_ids or [])

    monkeypatch.setattr(tasks_service, "dispatch_alert", fake_dispatch)
    # Oct 15: task pushes skip people without a phone first - these tests
    # are about who may hear, so everyone counts as reachable here.
    monkeypatch.setattr(tasks_service.notif, "deliverable_user_ids", lambda db, key, ids: set(ids))
    return sent


def _refused(r):
    return (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY})


def _created(r):
    assert r.status_code == 200, r.text
    return r.json()["tasks"][0]["id"]


def test_demo_can_undo_a_task_it_completed(db, demo, demo_client, admin_client):
    tid = _created(admin_client.post("/api/team/tasks", json={"title": "Fold boxes", "assignee_user_ids": [demo.id]}))
    assert demo_client.post(f"/api/team/tasks/{tid}/complete").status_code == 200
    r = demo_client.post(f"/api/team/tasks/{tid}/reopen")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "open"


def test_demo_can_undo_a_task_in_its_own_team(db, demo, demo_client, admin_client, review):
    tid = _created(admin_client.post("/api/team/tasks", json={"title": "Sweep", "assignee_team_id": review.id}))
    assert demo_client.post(f"/api/team/tasks/{tid}/complete").status_code == 200
    assert demo_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 200


def test_demo_cant_reopen_outside_its_sandbox(db, demo, demo_client, admin_client, staff, review):
    grocery = db.query(Team).filter(Team.name == "Grocery").one()
    add_member(db, grocery, staff)  # Oct 15: no tasks for empty teams
    staff_client = client_for(db, staff)
    staff_task = _created(admin_client.post("/api/team/tasks", json={"title": "Mop", "assignee_user_ids": [staff.id]}))
    assert staff_client.post(f"/api/team/tasks/{staff_task}/complete").status_code == 200
    other_team = _created(admin_client.post("/api/team/tasks", json={"title": "Stack", "assignee_team_id": grocery.id}))
    assert admin_client.post(f"/api/team/tasks/{other_team}/complete").status_code == 200
    # Assigned to the demo but closed by an admin: not its own completion.
    mine = _created(admin_client.post("/api/team/tasks", json={"title": "Label", "assignee_user_ids": [demo.id]}))
    assert admin_client.post(f"/api/team/tasks/{mine}/complete").status_code == 200
    for tid in (staff_task, other_team, mine):
        assert _refused(demo_client.post(f"/api/team/tasks/{tid}/reopen")), tid
    from app.team.tasks_models import Task

    assert {db.get(Task, t).status for t in (staff_task, other_team, mine)} == {"done"}


def test_demo_can_create_a_personal_task_and_one_for_its_team(db, demo, demo_client, review, staff, pushes):
    add_member(db, review, staff)
    demo.can_assign = True  # team tasks also need the assigner flag (service rule)
    db.commit()
    assert _created(demo_client.post("/api/team/tasks", json={"title": "Try the app"}))
    assert _created(demo_client.post("/api/team/tasks", json={"title": "Mine", "assignee_user_ids": [demo.id]}))
    pushes.clear()
    assert _created(demo_client.post(
        "/api/team/tasks", json={"title": "Team job", "assignee_team_id": review.id, "priority": "urgent"}
    ))
    # Only its teammate hears about it.
    assert pushes == [("task_assigned", {staff.id})]


def test_demo_cant_create_tasks_for_others(db, demo, demo_client, admin, staff, review):
    demo.can_assign = True
    db.commit()
    grocery = db.query(Team).filter(Team.name == "Grocery").one()
    everyone = db.query(Team).filter(Team.is_everyone.is_(True)).first()
    archived = Team(name="Old crew", archived_at=utcnow())
    db.add(archived)
    db.commit()
    add_member(db, archived, demo)
    bodies = [
        {"title": "x", "assignee_user_ids": [staff.id]},  # a real staff member
        {"title": "x", "assignee_user_ids": [demo.id, admin.id]},  # itself plus someone else
        {"title": "x", "assignee_team_id": grocery.id},  # a team it isn't in
        {"title": "x", "assignee_team_id": archived.id},
        {"title": "x", "assignee_user_ids": [str(demo.id)]},  # not a plain id
        {"title": "x", "assignee_user_ids": [True]},
    ]
    if everyone is not None:
        bodies.append({"title": "x", "assignee_team_id": everyone.id})
    from app.team.tasks_models import Task

    before = db.query(Task).count()
    for body in bodies:
        assert _refused(demo_client.post("/api/team/tasks", json=body)), body
    r = demo_client.post("/api/team/tasks", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code in (403, 422)  # FastAPI rejects bad JSON before the guard runs
    assert db.query(Task).count() == before


def test_demo_assignee_list_is_only_itself_even_with_can_assign(db, demo, demo_client, admin, staff, review):
    # Prod gives the demo can_assign for its Review team's tasks; the app then
    # shows /assignees as the people picker. Every real person there would be
    # refused by the guard, so the list is just the demo itself.
    add_member(db, review, staff)
    demo.can_assign = True
    db.commit()
    people = demo_client.get("/api/team/assignees").json()["people"]
    assert people == [{"id": demo.id, "name": demo.username}]
    # A real assigner still sees everyone with Tasks access.
    names = {p["name"] for p in client_for(db, admin).get("/api/team/assignees").json()["people"]}
    assert {demo.username, staff.username} <= names


def test_demo_can_edit_and_cancel_its_own_task(db, demo, demo_client, review):
    demo.can_assign = True
    db.commit()
    tid = _created(demo_client.post("/api/team/tasks", json={"title": "Draft"}))
    r = demo_client.patch(f"/api/team/tasks/{tid}", json={"title": "Final", "priority": "urgent"})
    assert r.status_code == 200 and r.json()["title"] == "Final"
    r = demo_client.patch(f"/api/team/tasks/{tid}", json={"assignee_team_id": review.id})
    assert r.status_code == 200, r.text
    assert demo_client.patch(f"/api/team/tasks/{tid}", json={"cancelled": True}).json()["status"] == "cancelled"
    # Undo of its own cancel (its own team's task).
    assert demo_client.post(f"/api/team/tasks/{tid}/reopen").status_code == 200


def test_demo_cant_edit_someone_elses_task_or_reassign_out(db, demo, demo_client, admin_client, staff, review):
    demo.can_assign = True
    db.commit()
    from app.team.tasks_models import Task

    theirs = _created(admin_client.post("/api/team/tasks", json={"title": "Admin's", "assignee_user_ids": [demo.id]}))
    team_task = _created(admin_client.post("/api/team/tasks", json={"title": "Team's", "assignee_team_id": review.id}))
    for tid in (theirs, team_task):
        assert _refused(demo_client.patch(f"/api/team/tasks/{tid}", json={"title": "hijack"})), tid
        assert _refused(demo_client.patch(f"/api/team/tasks/{tid}", json={"cancelled": True})), tid
    own = _created(demo_client.post("/api/team/tasks", json={"title": "Mine"}))
    grocery = db.query(Team).filter(Team.name == "Grocery").one()
    assert _refused(demo_client.patch(f"/api/team/tasks/{own}", json={"assignee_user_id": staff.id}))
    assert _refused(demo_client.patch(f"/api/team/tasks/{own}", json={"assignee_team_id": grocery.id}))
    assert db.get(Task, theirs).title == "Admin's" and db.get(Task, theirs).status == "open"
    task = db.get(Task, own)
    db.refresh(task)
    assert (task.assignee_user_id, task.assignee_team_id) == (demo.id, None)


def test_demo_made_tasks_never_push_outside_its_teams_or_whatsapp(
    db, demo, demo_client, review, staff, staff2, pushes, monkeypatch
):
    """Defence in depth: even a task that somehow names an outsider (e.g.
    made before the guard) only ever pushes to the demo's teammates, and a
    demo task never goes through the WhatsApp fallback."""
    from app.team import auto_tasks
    from app.team import tasks_service as svc
    from app.team.tasks_models import Task

    whatsapp = []
    monkeypatch.setattr(auto_tasks.whapi_client, "send_whatsapp_message", lambda *a, **kw: whatsapp.append(a))
    monkeypatch.setattr(svc, "_quiet", lambda now: False)
    add_member(db, review, staff)
    demo.can_assign = True
    db.commit()
    tid = _created(demo_client.post("/api/team/tasks", json={"title": "Team job", "assignee_team_id": review.id}))
    leaked = Task(
        title="Old", assignee_user_id=staff2.id, status="open", created_by_user_id=demo.id,
        created_by_name=demo.username, created_at=utcnow(), updated_at=utcnow(),
        due_at=utcnow() - dt.timedelta(hours=2),
    )
    db.add(leaked)
    db.commit()
    pushes.clear()
    svc.notify_assigned(db, leaked, actor_id=demo.id)
    svc.send_reminders(db, utcnow())
    team_task = db.get(Task, tid)
    team_task.due_at = utcnow() + dt.timedelta(minutes=20)
    team_task.created_at = utcnow() - dt.timedelta(hours=1)
    db.commit()
    svc.send_reminders(db, utcnow())
    everyone_told = set().union(*(ids for _, ids in pushes)) if pushes else set()
    assert staff2.id not in everyone_told
    assert everyone_told <= {demo.id, staff.id}
    assert ("task_due", {demo.id}) in pushes  # the overdue push for "Old": staff2 dropped
    assert ("task_due", {staff.id, demo.id}) in pushes  # its own team still gets reminders
    assert whatsapp == []


def test_chat_delete_only_its_own_message(db, demo, demo_client, staff):
    from tests.test_app_review_build7 import send, team_channel

    kitchen = db.query(Team).filter(Team.name == "Kitchen").one()
    add_member(db, kitchen, demo)
    add_member(db, kitchen, staff)
    staff_client = client_for(db, staff)
    channel = team_channel(demo_client, kitchen)
    own = send(demo_client, channel["id"], "hello").json()["id"]
    theirs = send(staff_client, channel["id"], "hi back").json()["id"]
    r = demo_client.delete(f"/api/team/messages/{theirs}")
    assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY})
    assert db.get(Message, theirs).deleted_at is None
    assert demo_client.delete(f"/api/team/messages/{own}").status_code == 200


# --- 2. Demo-made records are hidden from real staff ----------------------------------------


def test_demo_invoices_hidden_from_staff_and_admins(db, demo_client, admin_client):
    clerk = client_for(db, make_user(db, "clerk", features=["invoice_submission"]))
    real_id = _submit(clerk, content=b"%PDF real").json()["id"]
    demo_id = _submit(demo_client).json()["id"]
    # A row with no submitter (old data) must stay visible - NULL-safe filter.
    legacy = InvoiceSubmission(
        vendor="Old", submission_type="invoice", original_filename="o.pdf", stored_filename="o.pdf",
        file_token="legacy", created_at=utcnow(),
    )
    db.add(legacy)
    db.commit()

    def ids(client, query=""):
        return {i["id"] for i in client.get(f"/api/invoices{query}").json()["items"]}

    assert ids(clerk) == {real_id, legacy.id}
    assert ids(admin_client) == {real_id, legacy.id}
    assert ids(admin_client, "?include_demo=true") == {real_id, legacy.id, demo_id}
    assert ids(clerk, "?include_demo=true") == {real_id, legacy.id}  # admins only
    assert ids(demo_client) == {real_id, legacy.id, demo_id}  # its own + everything real
    assert clerk.get(f"/api/invoices/{demo_id}").status_code == 404
    assert admin_client.get(f"/api/invoices/{demo_id}").status_code == 200
    # Duplicate check: the demo's file is not a duplicate for real staff.
    file_hash = inv_svc.hash_file_bytes(b"%PDF-1.4 test")
    assert clerk.get(f"/api/invoices/check-duplicate?file_hash={file_hash}").json()["duplicate"] is False
    assert demo_client.get(f"/api/invoices/check-duplicate?file_hash={file_hash}").json()["duplicate"] is True


def test_demo_invoice_file_hidden_from_staff(db, demo_client, admin_client):
    """The file link follows the same rule as the detail: staff can't open a
    demo submission's file by id (or by its old file_token)."""
    clerk = client_for(db, make_user(db, "clerk", features=["invoice_submission"]))
    real_id = _submit(clerk, content=b"%PDF real").json()["id"]
    demo_id = _submit(demo_client).json()["id"]
    token = db.get(InvoiceSubmission, demo_id).file_token
    assert clerk.get(f"/public/invoices/{demo_id}").status_code == 404
    assert clerk.get(f"/public/invoices/{token}").status_code == 404
    assert clerk.get(f"/public/invoices/{real_id}").status_code == 200
    assert admin_client.get(f"/public/invoices/{demo_id}").status_code == 200
    assert demo_client.get(f"/public/invoices/{demo_id}").status_code == 200  # its own


def test_demo_invoices_left_out_of_store_wide_logic(db, demo_client):
    _submit(demo_client, vendor="Demo Vendor")
    clerk = client_for(db, make_user(db, "clerk", features=["invoice_submission"]))
    _submit(clerk, vendor="Real Vendor", content=b"%PDF real")
    assert inv_svc.get_vendors_submitted_today(db) == ["Real Vendor"]
    day = restaurant_today()
    assert [g["vendor"] for g in get_invoice_gaps(db, day, day)] == ["Real Vendor"]
    from app.services.notification_triggers import invoice_failed_alerts

    for e in db.query(InvoiceSubmission).all():
        e.whatsapp_sent, e.whatsapp_error = False, "Send failed: boom"
    db.commit()
    alerts = invoice_failed_alerts(db, dt.datetime.now(dt.timezone.utc))
    assert len(alerts) == 1 and "Real Vendor" in (alerts[0].title + alerts[0].body)


def test_demo_rows_hidden_even_after_the_demo_account_is_gone(db, demo):
    row = InvoiceSubmission(
        vendor="V", submission_type="invoice", submitted_by_user_id=demo.id, submitted_by_name="App_Store_Review",
        original_filename="d.pdf", stored_filename="d.pdf", file_token="gone", created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.delete(db.get(User, demo.id))
    db.commit()
    clause = hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name)
    assert db.query(InvoiceSubmission).filter(clause).count() == 0
    assert hidden_from(db, None, row.submitted_by_user_id, row.submitted_by_name) is True


def test_demo_checklists_hidden_from_admin_history_and_counts(db, demo_client, admin_client):
    from tests.test_public_checklist_link import good_answers

    assert demo_client.post(f"/api/checklists/{FSA}/submit", json={"answers": good_answers(FSA)}).status_code == 200
    lead = client_for(db, make_user(db, "kitchenlead", features=["daily_checklists"]))
    real_id = lead.post(f"/api/checklists/{FSA}/submit", json={"answers": good_answers(FSA)}).json()["id"]
    demo_id = db.query(ChecklistSubmission).filter_by(submitted_by_name="app_store_review").one().id

    def ids(client, query=""):
        return {i["id"] for i in client.get(f"/api/checklists{query}").json()["items"]}

    assert ids(admin_client) == {real_id}
    assert ids(admin_client, "?include_demo=true") == {real_id, demo_id}
    assert ids(demo_client) == {demo_id}
    assert ids(lead) == {real_id}
    overview = admin_client.get("/api/checklists/overview").json()["checklists"]
    fsa = next(c for c in overview if c["checklist_type"] == FSA)
    assert fsa["count_on_date"] == 1 and fsa["latest"]["id"] == real_id
    counts = checklist_admin._submission_counts(db)
    assert sum(n for (t, _), n in counts.items() if t == FSA) == 1


# --- 3. Fix Issues 9 AM reopen push ignores a demo close -------------------------------------


def test_reopen_push_ignores_a_close_made_by_demo(db):
    from app.services import notification_triggers as nt

    closed_at = utcnow() - dt.timedelta(days=8)
    for key, by in (("real-item", "boss"), ("demo-item", "app_store_review")):
        db.add(IssueResolutionEvent(issue_type="dead_new_arrival", issue_key=key, item_name=key, event="closed",
                                    reason="give_time", by_name=by, at=closed_at))
        db.add(IssueResolutionEvent(issue_type="dead_new_arrival", issue_key=key, item_name=key, event="reopened",
                                    reason="give_time", how="snooze_ended", at=utcnow() - dt.timedelta(hours=1)))
    db.commit()
    import app.services.fix_issues as fi

    orig = fi.reopen_all_ended_snoozes
    fi.reopen_all_ended_snoozes = lambda db, today: []
    try:
        alerts = nt.fix_issues_reopened_alerts(db, dt.datetime.now(dt.timezone.utc))
    finally:
        fi.reopen_all_ended_snoozes = orig
    assert len(alerts) == 1
    assert alerts[0].body == "real-item" and alerts[0].title == "Fix Issues: 1 item back"
    assert db.query(IssueResolution).count() == 0
