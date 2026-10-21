"""App Review build 7 (docs/APP_REVIEW_BUILD7_CONTRACT.md), server side:

1. demo accounts - invoices/checklists saved but never sent, no store-wide
   alerts, Ask bot canned, pushes stay inside the demo user's teams, chat
   scoping (demo / Everyone-excluded logins only chat with teammates);
2. the objectionable-content filter (masking, leetspeak, no Scunthorpe);
3. Team rules acceptance;
4. reporting tasks and task comments into the one moderation queue;
5. self-service account deletion;
plus migration 0043.

Nothing here reaches Whapi, Gmail, Anthropic or Firebase: every send function
is replaced with a recorder (or one that fails the test if called).
"""

import datetime as dt
import os
import pathlib
import sqlite3
import subprocess
import sys
import uuid
import zoneinfo

import pytest

from app.auth import hash_password
from app.config import get_settings
from app.demo import DEMO_ASK_ANSWER, DEMO_NOT_SENT, demo_user_ids, is_demo_user
from app.models import ChecklistSubmission, DeviceToken, InvoiceSubmission, User, UserSession
from app.routers import ask as ask_router
from app.routers import invoices as invoices_router
from app.services import checklist_submission
from app.services import invoice_submission as inv_svc
from app.services import notification_triggers as nt
from app.services import notifications as notif
from app.team import chat_service as chat
from app.team import content_filter
from app.team import moderation as mod
from app.team import tasks_service
from app.team.chat_models import Channel, Message, MessageReport
from app.team.models import Team, TeamMember, TeamRulesAcceptance
from app.team.tasks_models import TaskEvent
from app.timeutil import utcnow
from tests.conftest import add_member, client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")
BACKEND = pathlib.Path(__file__).resolve().parents[1]
FSA = "kitchen_fsa"
ALL_FEATURES = ["invoice_submission", "daily_checklists", "ask_bot", "team_tasks", "team_chat"]


# --- Fixtures ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    monkeypatch.setattr(chat, "in_quiet_hours", lambda: False)
    monkeypatch.setattr(tasks_service, "_quiet", lambda now: False)


@pytest.fixture(autouse=True)
def no_real_sends(monkeypatch):
    """Every outside-world send records instead - and the tests assert the
    demo paths never reach any of them."""
    calls = []
    monkeypatch.setattr(inv_svc, "send_whatsapp_message", lambda text, **kw: calls.append(("inv_whatsapp", text)))
    monkeypatch.setattr(
        inv_svc, "send_toast_invoice_email", lambda **kw: calls.append(("toast_email", kw.get("vendor")))
    )
    monkeypatch.setattr(inv_svc, "convert_to_pdf", lambda src, dst: dst.write_bytes(b"%PDF"))
    monkeypatch.setattr(
        checklist_submission, "send_whatsapp_message", lambda text, to=None: calls.append(("checklist_whatsapp", to))
    )
    monkeypatch.setattr(get_settings(), "whapi_kitchen_checklist_group_id", "sk-group@g.us")
    monkeypatch.setattr(get_settings(), "whapi_store_checklist_group_id", "st-group@g.us")
    return calls


@pytest.fixture
def pushes(monkeypatch):
    """Firebase 'configured', every token delivered; records each send."""
    sent = []

    class Result:
        def __init__(self, tokens):
            self.delivered_tokens = list(tokens)
            self.invalid_tokens = []
            self.success_count = len(tokens)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})
        return Result(tokens)

    monkeypatch.setattr(chat.push, "is_configured", lambda: True)
    monkeypatch.setattr(chat.push, "send_to_tokens", fake_send)
    return sent


@pytest.fixture
def demo(db):
    return make_user(db, "app_store_review", features=ALL_FEATURES)


@pytest.fixture
def demo_client(db, demo):
    return client_for(db, demo)


@pytest.fixture
def review_team(db):
    team = Team(name="Review", color="#123456", is_everyone=False, created_at=utcnow())
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def device(db, user, token=None):
    db.add(DeviceToken(user_id=user.id, token=token or f"tok-{user.username}", platform="ios"))
    db.commit()


def send(client, channel_id, body="hi"):
    return client.post(
        f"/api/team/channels/{channel_id}/messages", json={"body": body, "client_uuid": str(uuid.uuid4())}
    )


def team_channel(client, team):
    channels = client.get("/api/team/channels").json()["channels"]
    return next(c for c in channels if c["team_id"] == team.id)


def people_ids(client):
    return {p["id"] for p in client.get("/api/team/people").json()["people"]}


# --- 1. Demo accounts: identity ----------------------------------------------------------


def test_is_demo_user_reads_the_setting(db, monkeypatch):
    a = make_user(db, "App_Store_Review")
    b = make_user(db, "ravi")
    assert is_demo_user(a) and not is_demo_user(b) and not is_demo_user(None)
    assert demo_user_ids(db) == {a.id}
    monkeypatch.setattr(get_settings(), "demo_usernames", " Ravi , other ")
    assert is_demo_user(b) and not is_demo_user(a)


def test_me_and_mobile_login_say_demo(db, demo_client, staff_client, anon_client):
    assert demo_client.get("/api/auth/me").json()["demo"] is True
    assert staff_client.get("/api/auth/me").json()["demo"] is False
    u = make_user(db, "appreview")
    u.password_hash = hash_password("review-pass-1")
    db.commit()
    r = anon_client.post("/api/auth/mobile-login", json={"username": "appreview", "password": "review-pass-1"})
    assert r.status_code == 200 and r.json()["demo"] is True and r.json()["token"]
    r = anon_client.post("/api/auth/login", json={"username": "appreview", "password": "review-pass-1"})
    assert r.json()["demo"] is True


def test_demo_is_never_in_everyone_even_if_not_excluded(db, monkeypatch, demo, staff):
    from app.team.changes import everyone_user_ids, is_everyone_member

    monkeypatch.setattr(get_settings(), "team_everyone_exclude", "")
    assert not is_everyone_member(demo)
    assert demo.id not in everyone_user_ids(db) and staff.id in everyone_user_ids(db)


# --- 1. Demo accounts: invoices ----------------------------------------------------------


def _submit(client, **fields):
    data = {"submission_type": "invoice", "vendor": "Raja Foods", "total_amount": "12.50"}
    data.update(fields)
    return client.post("/api/invoices", data=data, files={"file": ("bill.pdf", b"%PDF-1.4 test", "application/pdf")})


def test_demo_invoice_submit_is_saved_never_sent(db, demo_client, no_real_sends):
    r = _submit(demo_client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["whatsapp_sent"] is False and body["whatsapp_error"] == DEMO_NOT_SENT
    assert body["toast_email_sent"] is False and body["toast_email_error"] == DEMO_NOT_SENT
    assert body["processing_status"] == "completed"
    assert no_real_sends == []
    # Saved like any other, and still "completed" when read back.
    assert demo_client.get(f"/api/invoices/{body['id']}").json()["processing_status"] == "completed"
    assert db.get(InvoiceSubmission, body["id"]).submitted_by_name == "app_store_review"


def test_demo_transfer_is_completed_too(demo_client, no_real_sends):
    body = _submit(demo_client, submission_type="transfer", transfer_direction="store_to_container", vendor="").json()
    assert body["whatsapp_error"] == DEMO_NOT_SENT and body["toast_email_error"] is None
    assert body["processing_status"] == "completed"
    assert no_real_sends == []


def test_demo_resend_never_sends(db, demo_client, no_real_sends):
    sid = _submit(demo_client).json()["id"]
    r = demo_client.post(f"/api/invoices/{sid}/resend", json={"channels": ["whatsapp", "toast_email"]})
    assert r.status_code == 200, r.text
    assert r.json()["processing_status"] == "completed"
    assert r.json()["whatsapp_error"] == DEMO_NOT_SENT
    assert no_real_sends == []


def test_demo_resend_of_a_real_invoice_changes_nothing_and_sends_nothing(db, demo_client, no_real_sends):
    clerk = client_for(db, make_user(db, "clerk", features=["invoice_submission"]))
    real = _submit(clerk).json()
    assert {c[0] for c in no_real_sends} == {"inv_whatsapp", "toast_email"}  # a real one does send
    no_real_sends.clear()
    before = demo_client.get(f"/api/invoices/{real['id']}").json()
    r = demo_client.post(f"/api/invoices/{real['id']}/resend", json={"channels": ["whatsapp"]})
    # Demo read-only guard (app/demo_guard.py): someone else's invoice is refused outright.
    assert r.status_code == 403
    demo_line = "The demo account can look around but can't change the store's real data."
    assert r.json() == {"detail": demo_line, "error": "demo_read_only", "message": demo_line}
    assert demo_client.get(f"/api/invoices/{real['id']}").json() == before
    assert no_real_sends == []


def test_someone_else_resending_a_demo_invoice_still_never_sends(db, demo_client, admin_client, no_real_sends):
    sid = _submit(demo_client).json()["id"]
    r = admin_client.post(f"/api/invoices/{sid}/resend", json={"channels": ["whatsapp", "toast_email"]})
    assert r.status_code == 200
    # The background attempt (run by TestClient after the response) re-records it.
    after = admin_client.get(f"/api/invoices/{sid}").json()
    assert after["whatsapp_error"] == DEMO_NOT_SENT and after["toast_email_error"] == DEMO_NOT_SENT
    assert after["processing_status"] == "completed"
    assert no_real_sends == []


def test_demo_invoice_never_raises_a_failed_send_alert(db, demo_client):
    _submit(demo_client)
    real = InvoiceSubmission(
        vendor="Bad Co", submission_type="invoice", submitted_by_user_id=99, submitted_by_name="clerk",
        original_filename="x.pdf", stored_filename="x.pdf", file_token="t1", whatsapp_error="Send failed: boom",
        created_at=utcnow(),
    )
    db.add(real)
    db.commit()
    alerts = nt.invoice_failed_alerts(db, utcnow())
    assert [a.data["invoice_id"] for a in alerts] == [real.id]


def test_not_configured_shape_is_unchanged_for_real_users():
    entry = InvoiceSubmission(submission_type="invoice", whatsapp_sent=False, whatsapp_error="WHAPI not configured",
                              toast_email_sent=False, toast_email_error="Gmail not configured")
    assert inv_svc._processing_status(entry) == "failed"


# --- 1. Demo accounts: checklists --------------------------------------------------------


def _good_answers():
    from tests.test_public_checklist_link import good_answers

    return good_answers(FSA)


@pytest.mark.suite_pack  # two WhatsApp roles (store, kitchen)
def test_demo_checklist_is_saved_without_whatsapp(db, demo_client, no_real_sends):
    r = demo_client.post(f"/api/checklists/{FSA}/submit", json={"answers": _good_answers()})
    assert r.status_code == 200, r.text
    entry = db.get(ChecklistSubmission, r.json()["id"])
    assert entry is not None and entry.submitted_by_name == "app_store_review"
    assert checklist_submission.should_send_whatsapp(entry) is False
    assert entry.whatsapp_sent is False and entry.whatsapp_error is None
    assert no_real_sends == []
    # The same submission from a real person does go to the group.
    real = client_for(db, make_user(db, "kitchenlead", features=["daily_checklists"]))
    assert real.post(f"/api/checklists/{FSA}/submit", json={"answers": _good_answers()}).status_code == 200
    assert no_real_sends == [("checklist_whatsapp", "sk-group@g.us")]


def test_public_submitter_named_like_the_demo_still_sends(db):
    entry = ChecklistSubmission(checklist_type=FSA, submitted_by_user_id=0, submitted_by_name="app_store_review")
    assert checklist_submission.should_send_whatsapp(entry) is True


def _row(db, ctype, user, when):
    db.add(ChecklistSubmission(checklist_type=ctype, submitted_by_user_id=user.id, submitted_by_name=user.username,
                               answers_json="{}", all_clear=True, answers_hash=f"h-{ctype}-{user.id}-{when}",
                               created_at=when.astimezone(dt.timezone.utc)))
    db.commit()


def test_demo_checklists_dont_count_in_the_930_alert(db, demo):
    from app.services.checklist_schemas import closing_checklist_types

    night = dt.datetime(2026, 9, 28, 21, 30, tzinfo=NY)
    closing = closing_checklist_types()
    for ctype in closing:
        _row(db, ctype, demo, dt.datetime(2026, 9, 28, 20, 0, tzinfo=NY))
    alerts = nt.closing_checklist_alerts(db, night)
    assert len(alerts) == 1 and alerts[0].body.count(",") + alerts[0].body.count(" and ") == len(closing) - 1
    real = make_user(db, "asha")
    for ctype in closing:
        _row(db, ctype, real, dt.datetime(2026, 9, 28, 20, 0, tzinfo=NY))
    assert nt.closing_checklist_alerts(db, night) == []


def test_demo_checklist_never_closes_the_real_auto_task(db, demo):
    from app.team import auto_tasks

    day = dt.date(2026, 9, 28)
    _row(db, FSA, demo, dt.datetime(2026, 9, 28, 20, 0, tzinfo=NY))
    assert auto_tasks._submitted_checklists(db, day) == {}


def test_store_wide_alerts_never_reach_a_demo_login(db, demo, pushes, monkeypatch):
    staff_user = make_user(db, "asha", features=["daily_checklists"])
    device(db, demo)
    device(db, staff_user)
    monkeypatch.setattr(notif.push, "is_configured", lambda: True)
    monkeypatch.setattr(notif.push, "send_to_tokens", lambda tokens, *a, **kw: pushes.append(sorted(tokens)) or
                        type("R", (), {"delivered_tokens": list(tokens), "invalid_tokens": [], "success_count": len(tokens)})())
    alert = notif.Alert(key="closing_checklist", dedupe_keys=["closing_checklist:test"], title="t", body="b")
    assert notif.dispatch_alert(db, alert) == 1
    assert pushes[-1] == ["tok-asha"]
    # An alert about the demo user's own thing still reaches it.
    own = notif.Alert(key="task_assigned", dedupe_keys=["task_assigned:x"], title="t", body="b", user_ids=[demo.id])
    assert notif.dispatch_alert(db, own) == 1
    assert pushes[-1] == ["tok-app_store_review"]


# --- 1. Demo accounts: Ask bot -----------------------------------------------------------


def test_demo_ask_bot_is_canned_with_no_anthropic_call(db, demo_client, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("Anthropic must not be called for the demo account")

    monkeypatch.setattr(ask_router, "ask", boom)
    r = demo_client.post("/api/ask", json={"question": "What should I reorder?", "history": []})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == DEMO_ASK_ANSWER
    assert r.json()["tool_calls"] == [] and r.json()["escalated"] is False
    # A real user still goes to the bot.
    real = client_for(db, make_user(db, "asha", features=["ask_bot"]))
    monkeypatch.setattr(ask_router, "ask", lambda db, q, h, include_sales=True: {"answer": "real"})
    assert real.post("/api/ask", json={"question": "hi", "history": []}).json()["answer"] == "real"


# --- 1. Demo accounts: pushes stay inside the demo user's teams ---------------------------


def test_demo_report_only_notifies_admins_in_its_teams(db, demo, demo_client, review_team, pushes):
    sutton = make_user(db, "sutton", is_admin=True)
    other_admin = make_user(db, "boss2", is_admin=True)
    for u in (demo, sutton):
        add_member(db, review_team, u)
    device(db, sutton)
    device(db, other_admin)
    sutton_c = client_for(db, sutton)
    channel = team_channel(sutton_c, review_team)
    msg = send(sutton_c, channel["id"], "welcome to the review team").json()
    pushes.clear()
    r = demo_client.post(f"/api/team/messages/{msg['id']}/report", json={"reason": "spam"})
    assert r.status_code == 200
    assert [p["tokens"] for p in pushes] == [["tok-sutton"]]  # never boss2


def test_real_report_notifies_every_admin(db, staff, staff_client, team, pushes):
    a1 = make_user(db, "boss1", is_admin=True)
    a2 = make_user(db, "boss2", is_admin=True)
    device(db, a1)
    device(db, a2)
    john = make_user(db, "john", features=["team_chat"])
    for u in (staff, john):
        add_member(db, team, u)
    john_c = client_for(db, john)
    msg = send(john_c, team_channel(john_c, team)["id"], "hello").json()
    pushes.clear()
    staff_client.post(f"/api/team/messages/{msg['id']}/report", json={"reason": "rude"})
    assert pushes[-1]["tokens"] == ["tok-boss1", "tok-boss2"]


def test_demo_message_pushes_only_teammates(db, demo, demo_client, review_team, pushes):
    sutton = make_user(db, "sutton", is_admin=True)
    for u in (demo, sutton):
        add_member(db, review_team, u)
    device(db, sutton)
    channel = team_channel(demo_client, review_team)
    assert send(demo_client, channel["id"], "hello").status_code == 200
    assert pushes and all(p["tokens"] == ["tok-sutton"] for p in pushes)
    assert chat.push_new_message(db, db.query(Message).order_by(Message.id.desc()).first().id) <= {sutton.id}


def test_demo_cant_do_only_notifies_teammates(db, demo, demo_client, review_team, monkeypatch):
    outsider = make_user(db, "outsider_admin", is_admin=True)
    sent = []
    monkeypatch.setattr(tasks_service, "_send", lambda db, key, dedupe, ids, *a: sent.append(sorted(ids)) or 0)
    task = client_for(db, outsider).post(
        "/api/team/tasks", json={"title": "Stock shelf", "assignee_user_ids": [demo.id]}
    ).json()["tasks"][0]
    sent.clear()
    r = demo_client.post(f"/api/team/tasks/{task['id']}/cant-do", json={"reason": "no stock"})
    assert r.status_code == 200
    assert sent == [[]]  # the creator isn't in the demo user's team: nobody


# --- 1. Chat scoping -----------------------------------------------------------------------


def _outsider(db, monkeypatch, name="night_guard"):
    """A non-demo login left out of Everyone."""
    monkeypatch.setattr(get_settings(), "team_everyone_exclude", f"app_store_review,appreview,{name}")
    return make_user(db, name, features=["team_chat"])


@pytest.mark.parametrize("kind", ["demo", "excluded"])
def test_chat_scoping_both_directions(db, monkeypatch, staff, staff_client, demo, review_team, kind):
    restricted = demo if kind == "demo" else _outsider(db, monkeypatch)
    rc = client_for(db, restricted)
    # No shared team: invisible to each other, no DM either way.
    assert staff.id not in people_ids(rc)
    assert restricted.id not in people_ids(staff_client)
    for client, other in ((rc, staff), (staff_client, restricted)):
        r = client.post("/api/team/channels/dm", json={"user_id": other.id})
        assert r.status_code == 403
        assert r.json()["detail"] == "You can only message people in your team."
    # The Everyone team doesn't count as shared.
    everyone = db.query(Team).filter(Team.is_everyone.is_(True)).one()
    assert all(c["team_id"] != everyone.id for c in rc.get("/api/team/channels").json()["channels"])
    # Sharing a real team opens it up both ways.
    add_member(db, review_team, restricted)
    add_member(db, review_team, staff)
    assert staff.id in people_ids(rc) and restricted.id in people_ids(staff_client)
    dm = rc.post("/api/team/channels/dm", json={"user_id": staff.id})
    assert dm.status_code == 200
    assert send(rc, dm.json()["id"], "hi from review").status_code == 200
    assert staff_client.get(f"/api/team/channels/{dm.json()['id']}/messages").status_code == 200


def test_two_ordinary_staff_are_unaffected(db, staff, staff_client):
    john = make_user(db, "john", features=["team_chat"])
    assert john.id in people_ids(staff_client)
    assert staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).status_code == 200


def test_a_demo_dm_disappears_when_they_stop_sharing_a_team(db, staff, staff_client, demo, demo_client, review_team):
    add_member(db, review_team, demo)
    add_member(db, review_team, staff)
    dm = staff_client.post("/api/team/channels/dm", json={"user_id": demo.id}).json()
    msg = send(staff_client, dm["id"], "secret").json()
    assert dm["id"] in {c["id"] for c in demo_client.get("/api/team/channels").json()["channels"]}
    db.query(TeamMember).filter(TeamMember.user_id == demo.id).delete()
    db.commit()
    for client in (demo_client, staff_client):
        assert dm["id"] not in {c["id"] for c in client.get("/api/team/channels").json()["channels"]}
        assert client.get(f"/api/team/channels/{dm['id']}/messages").status_code == 404
        assert send(client, dm["id"], "still there?").status_code == 404
    assert demo_client.post(f"/api/team/messages/{msg['id']}/report", json={"reason": "x"}).status_code == 404
    # An archived shared team doesn't count either.
    add_member(db, review_team, demo)
    assert send(demo_client, dm["id"], "back").status_code == 200
    review_team.archived_at = utcnow()
    db.commit()
    assert demo_client.get(f"/api/team/channels/{dm['id']}/messages").status_code == 404


def test_channel_messages_carry_the_author_id(db, staff, staff_client, team):
    add_member(db, team, staff)
    channel = team_channel(staff_client, team)
    send(staff_client, channel["id"], "hello")
    m = staff_client.get(f"/api/team/channels/{channel['id']}/messages").json()["messages"][-1]
    assert m["sender"] == {"id": staff.id, "name": staff.username}


# --- 2. Content filter ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("what the fuck", "what the f***"),
        ("FUCKING hell", "F****** hell"),
        ("sh!t happens!", "s*** happens!"),
        ("shit!", "s***!"),
        ("b!tch", "b****"),
        ("$hit", "$***"),
        ("a55hole", "a******"),
        ("bullsh1t.", "b*******."),
        ("you @ss", "you @**"),
        ("Fuck. Shit", "F***. S***"),
        ("sh1t, b1tch and a$$", "s***, b**** and a**"),
    ],
)
def test_filter_masks_whole_words_and_leetspeak(text, expected):
    assert content_filter.filter_text(text) == (expected, True)


@pytest.mark.parametrize(
    "text",
    [
        "class", "assess", "Scunthorpe", "classic assistant passed", "cocktail", "Main Store spices",
        "shiitake", "Dickens", "grape", "therapist", "button", "hello @ravi!", "Hi!", "price $455 and 5 boxes",
        "455", "Mississippi", "assassin", "bass", "glasses", "Cockburn", "", "rapeseed oil", "black negro beans",
    ],
)
def test_filter_leaves_ordinary_words_alone(text):
    assert content_filter.filter_text(text) == (text, False)


def test_filter_passes_none_through():
    assert content_filter.filter_text(None) == (None, False)
    assert content_filter.clean(None) is None


def test_chat_message_is_masked_and_flagged(db, staff, staff_client, team, admin_client):
    john = make_user(db, "john", features=["team_chat"])
    for u in (staff, john):
        add_member(db, team, u)
    channel = team_channel(staff_client, team)
    r = send(staff_client, channel["id"], "this is bullsh!t man")
    assert r.status_code == 200
    body = r.json()["body"]
    assert body == "this is b******* man" and r.json()["filtered"] is True
    clean = send(staff_client, channel["id"], "the class is great").json()
    assert clean["body"] == "the class is great" and clean["filtered"] is False
    stored = db.get(Message, r.json()["id"])
    assert stored.body == "this is b******* man" and stored.filtered is True
    # The admin's queue shows it was filtered.
    john_c = client_for(db, john)
    john_c.post(f"/api/team/messages/{r.json()['id']}/report", json={"reason": "rude"})
    report = admin_client.get("/api/team/reports").json()["reports"][0]
    assert report["message"]["filtered"] is True and report["target_type"] == "message"


def test_task_text_is_masked_everywhere(db, admin_client, staff, staff_client):
    t = admin_client.post(
        "/api/team/tasks",
        json={"title": "Clean the shit", "description": "f*ck no, fuck yes", "assignee_user_ids": [staff.id],
              "items": ["wipe sh1t"]},
    ).json()["tasks"][0]
    assert t["title"] == "Clean the s***" and t["description"] == "f*ck no, f*** yes"
    detail = admin_client.get(f"/api/team/tasks/{t['id']}").json()
    assert detail["items"][0]["text"] == "wipe s***"
    t = admin_client.patch(f"/api/team/tasks/{t['id']}", json={"title": "bitch please", "description": "asshole"}).json()
    assert t["title"] == "b**** please" and t["description"] == "a******"
    t = staff_client.post(f"/api/team/tasks/{t['id']}/comments", json={"body": "what the fuck"}).json()
    assert t["events"][-1]["detail"]["body"] == "what the f***"
    r = staff_client.post(f"/api/team/tasks/{t['id']}/cant-do", json={"reason": "no shit"})
    assert r.json()["cant_do_reason"] == "no s***"
    t2 = admin_client.post("/api/team/tasks", json={"title": "x", "assignee_user_ids": [staff.id]}).json()["tasks"][0]
    done = staff_client.post(f"/api/team/tasks/{t2['id']}/complete", data={"note": "done, dickhead"})
    assert done.status_code == 200 and done.json()["completion_note"] == "done, d*******"


def test_announcement_is_masked(db, admin_client, team):
    a = admin_client.post(
        "/api/team/announcements", json={"title": "No bullshit", "body": "Stop the crap, shit.", "team_ids": [team.id]}
    ).json()
    assert a["title"] == "No b*******" and a["body"] == "Stop the crap, s***."
    a = admin_client.patch(f"/api/team/announcements/{a['id']}", json={"title": "fuck", "body": "ok"}).json()
    assert a["title"] == "f***" and a["body"] == "ok"


# --- 3. Team rules ---------------------------------------------------------------------------


def test_rules_get_accept_and_version(db, staff, staff_client, anon_client, monkeypatch):
    from app.config import get_settings

    # The store's name and phone (KloudC RetailOps M2 store settings).
    monkeypatch.setattr(get_settings(), "store_name", "Test Store")
    monkeypatch.setattr(get_settings(), "store_phone", "555-010-0100")
    assert anon_client.get("/api/team/rules").status_code == 401
    r = staff_client.get("/api/team/rules").json()
    assert r["version"] == 1 and r["title"] == "Team rules"
    assert r["accepted"] is False and r["accepted_at"] is None
    text = r["text"]
    assert "\n\n" in text and "zero tolerance" in text.lower() and "24 hours" in text
    assert "report" in text.lower() and "block" in text.lower()
    assert text.startswith("Team (tasks, announcements and chat) is for Test Store staff to work together.")
    assert text.endswith("Questions or concerns: ask your manager or call 555-010-0100.")
    assert staff_client.post("/api/team/rules/accept", json={"version": 2}).status_code == 400
    assert staff_client.post("/api/team/rules/accept", json={}).status_code == 422
    a = staff_client.post("/api/team/rules/accept", json={"version": 1}).json()
    assert a["accepted"] is True and a["accepted_at"]
    again = staff_client.post("/api/team/rules/accept", json={"version": 1}).json()
    assert again["accepted_at"] == a["accepted_at"]
    r = staff_client.get("/api/team/rules").json()
    assert r["accepted"] is True and r["accepted_at"] == a["accepted_at"]
    assert a["accepted_at"].endswith("+00:00")
    # Per user.
    other = client_for(db, make_user(db, "john"))
    assert other.get("/api/team/rules").json()["accepted"] is False
    # A new version asks again.
    from app.team import rules

    rules_v2 = rules.RULES_VERSION + 1
    try:
        rules.RULES_VERSION = rules_v2
        assert staff_client.get("/api/team/rules").json()["accepted"] is False
    finally:
        rules.RULES_VERSION = 1


def test_team_endpoints_dont_require_the_rules(db, staff, staff_client, team):
    add_member(db, team, staff)
    assert staff_client.get("/api/team/channels").status_code == 200
    assert staff_client.get("/api/team/tasks").status_code == 200


# --- 4. Reporting tasks and task comments ----------------------------------------------------


@pytest.fixture
def task_setup(db, admin, admin_client, staff, staff_client):
    t = admin_client.post(
        "/api/team/tasks", json={"title": "Mop aisle 3", "description": "Before 5", "assignee_user_ids": [staff.id]}
    ).json()["tasks"][0]
    admin_client.post(f"/api/team/tasks/{t['id']}/comments", json={"body": "admin says hi"})
    detail = staff_client.post(f"/api/team/tasks/{t['id']}/comments", json={"body": "staff reply"}).json()
    comments = [e for e in detail["events"] if e["kind"] == "commented"]
    return t, comments[0], comments[1]


def test_report_a_task_into_the_queue(db, admin_client, staff_client, task_setup, pushes, admin):
    task, _, _ = task_setup
    device(db, admin)
    r = staff_client.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "offensive", "note": "the title"})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"id", "status"} and r.json()["status"] == "open"
    again = staff_client.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "offensive"})
    assert again.json()["id"] == r.json()["id"]
    assert pushes and pushes[-1]["title"] == "Task reported" and pushes[-1]["tokens"] == ["tok-boss"]
    report = admin_client.get("/api/team/reports?status=open").json()["reports"][0]
    assert report["target_type"] == "task" and report["target_id"] == task["id"]
    assert report["reason"] == "offensive" and report["note"] == "the title"
    assert report["task"]["title"] == "Mop aisle 3" and report["task"]["description"] == "Before 5"
    assert report["task"]["reported"]["title"] == "Mop aisle 3"
    # Build 5/6 admin apps read `message` - it names the task.
    assert report["message"]["channel_name"] == "Task: Mop aisle 3"
    assert report["message"]["body"] == "Mop aisle 3\n\nBefore 5"
    assert report["reporter"]["name"] == "ravi"


def test_cant_report_your_own_task_or_comment(db, admin_client, staff_client, task_setup):
    task, admin_comment, staff_comment = task_setup
    r = admin_client.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "x"})
    assert r.status_code == 400 and r.json()["detail"] == "You can't report your own task."
    r = staff_client.post(f"/api/team/tasks/{task['id']}/comments/{staff_comment['id']}/report", json={"reason": "x"})
    assert r.status_code == 400 and r.json()["detail"] == "You can't report your own comment."


def test_task_reports_respect_task_visibility(db, staff, task_setup):
    task, admin_comment, _ = task_setup
    stranger = client_for(db, make_user(db, "stranger", features=["team_tasks"]))
    assert stranger.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "x"}).status_code == 404
    assert stranger.post(
        f"/api/team/tasks/{task['id']}/comments/{admin_comment['id']}/report", json={"reason": "x"}
    ).status_code == 404
    no_tasks = client_for(db, make_user(db, "chatonly", features=["team_chat"]))
    assert no_tasks.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "x"}).status_code == 403


def test_report_a_comment_and_remove_it(db, admin_client, staff_client, task_setup):
    task, admin_comment, staff_comment = task_setup
    # A comment id from another task, or an event that isn't a comment, is 404.
    other = admin_client.post("/api/team/tasks", json={"title": "Other"}).json()["tasks"][0]
    assert staff_client.post(
        f"/api/team/tasks/{other['id']}/comments/{admin_comment['id']}/report", json={"reason": "x"}
    ).status_code == 404
    created_event = db.query(TaskEvent).filter(TaskEvent.task_id == task["id"], TaskEvent.kind == "created").one()
    assert staff_client.post(
        f"/api/team/tasks/{task['id']}/comments/{created_event.id}/report", json={"reason": "x"}
    ).status_code == 404

    r = staff_client.post(f"/api/team/tasks/{task['id']}/comments/{admin_comment['id']}/report", json={"reason": "abuse"})
    assert r.status_code == 200
    report = admin_client.get("/api/team/reports").json()["reports"][0]
    assert report["target_type"] == "task_comment" and report["target_id"] == admin_comment["id"]
    assert report["comment"]["body"] == "admin says hi" and report["comment"]["task_id"] == task["id"]
    assert report["comment"]["author"]["name"] == "boss" and report["comment"]["removed"] is False
    assert report["task"]["id"] == task["id"]
    assert report["message"]["body"] == "admin says hi"

    out = admin_client.post(f"/api/team/reports/{r.json()['id']}/resolve", json={"note": "gone", "delete_message": True})
    assert out.status_code == 200
    out = out.json()
    assert out["status"] == "resolved" and out["resolution_note"] == "gone"
    assert out["comment"]["removed"] is True and out["comment"]["body"] == "admin says hi"  # evidence kept
    events = staff_client.get(f"/api/team/tasks/{task['id']}").json()["events"]
    shown = next(e for e in events if e["id"] == admin_comment["id"])
    assert shown["detail"]["body"] == mod.REMOVED_COMMENT_BODY and shown["detail"]["removed"] is True
    assert "admin says hi" not in str(events)


def test_resolve_a_task_report_removes_its_content(db, admin_client, staff_client, task_setup):
    task, _, _ = task_setup
    rid = staff_client.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "x"}).json()["id"]
    out = admin_client.post(f"/api/team/reports/{rid}/resolve", json={"delete_message": True}).json()
    assert out["status"] == "resolved"
    assert out["task"]["title"] == mod.REMOVED_TASK_TITLE and out["task"]["description"] is None
    assert out["task"]["status"] == "cancelled"
    assert out["task"]["reported"]["title"] == "Mop aisle 3"  # the evidence
    seen = staff_client.get(f"/api/team/tasks/{task['id']}").json()
    assert seen["title"] == mod.REMOVED_TASK_TITLE and seen["events"][-1]["kind"] == "moderated"


def test_dismiss_works_for_all_three(db, admin_client, staff_client, staff, team, task_setup):
    task, admin_comment, _ = task_setup
    john = make_user(db, "john", features=["team_chat"])
    for u in (staff, john):
        add_member(db, team, u)
    john_c = client_for(db, john)
    msg = send(john_c, team_channel(john_c, team)["id"], "hey").json()
    ids = [
        staff_client.post(f"/api/team/messages/{msg['id']}/report", json={"reason": "a"}).json()["id"],
        staff_client.post(f"/api/team/tasks/{task['id']}/report", json={"reason": "b"}).json()["id"],
        staff_client.post(
            f"/api/team/tasks/{task['id']}/comments/{admin_comment['id']}/report", json={"reason": "c"}
        ).json()["id"],
    ]
    queue = admin_client.get("/api/team/reports").json()["reports"]
    assert [r["target_type"] for r in queue] == ["task_comment", "task", "message"]
    for rid in ids:
        out = admin_client.post(f"/api/team/reports/{rid}/dismiss", json={"note": "fine"}).json()
        assert out["status"] == "dismissed"
    assert admin_client.get("/api/team/reports?status=open").json()["reports"] == []
    # Untouched content.
    assert staff_client.get(f"/api/team/tasks/{task['id']}").json()["title"] == "Mop aisle 3"


def test_old_chat_report_rows_read_as_messages(db, admin_client, staff, team):
    add_member(db, team, staff)
    ch = Channel(kind="team", team_id=team.id, created_at=utcnow())
    db.add(ch)
    db.flush()
    m = Message(channel_id=ch.id, sender_user_id=staff.id, sender_name="ravi", body="old", client_uuid="u1",
                created_at=utcnow())
    db.add(m)
    db.flush()
    # A pre-0043 row: no target_id.
    db.add(MessageReport(message_id=m.id, target_id=None, reporter_user_id=999, reporter_name="x", reason="r",
                         created_at=utcnow()))
    db.commit()
    report = admin_client.get("/api/team/reports").json()["reports"][0]
    assert report["target_type"] == "message" and report["target_id"] == m.id
    assert report["message"]["body"] == "old" and report["task"] is None and report["comment"] is None


# --- 5. Delete your own account --------------------------------------------------------------


def _with_password(db, user, password="correct-horse"):
    user.password_hash = hash_password(password)
    db.commit()
    return user


def test_delete_account(db, admin, staff, team):
    _with_password(db, staff)
    add_member(db, team, staff)
    device(db, staff)
    db.add(TeamRulesAcceptance(user_id=staff.id, version=1, accepted_at=utcnow()))
    db.commit()
    c1 = client_for(db, staff)
    c2 = client_for(db, staff)  # a second device
    assert c1.post("/api/auth/delete-account", json={"password": "nope"}).status_code == 400
    assert c1.post("/api/auth/delete-account", json={"password": "nope"}).json()["detail"] == "Wrong password."
    sid = staff.id
    r = c1.post("/api/auth/delete-account", json={"password": "correct-horse"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "session_token" in r.headers.get("set-cookie", "")  # cookie cleared
    db.expire_all()
    assert db.get(User, sid) is None
    assert db.query(UserSession).filter(UserSession.user_id == sid).count() == 0
    assert db.query(DeviceToken).filter(DeviceToken.user_id == sid).count() == 0
    assert db.query(TeamMember).filter(TeamMember.user_id == sid).count() == 0
    assert db.query(TeamRulesAcceptance).filter(TeamRulesAcceptance.user_id == sid).count() == 0
    assert c1.get("/api/auth/me").status_code == 401
    assert c2.get("/api/auth/me").status_code == 401


def test_last_admin_cant_delete_themselves(db, admin):
    _with_password(db, admin)
    c = client_for(db, admin)
    r = c.post("/api/auth/delete-account", json={"password": "correct-horse"})
    assert r.status_code == 409
    assert r.json()["detail"] == "You are the last admin - make someone else an admin first."
    make_user(db, "boss2", is_admin=True)
    assert c.post("/api/auth/delete-account", json={"password": "correct-horse"}).status_code == 200


def test_demo_account_cant_be_deleted(db, demo):
    _with_password(db, demo)
    c = client_for(db, demo)
    r = c.post("/api/auth/delete-account", json={"password": "correct-horse"})
    assert r.status_code == 403 and r.json()["detail"] == "The demo account can't be deleted."
    assert db.get(User, demo.id) is not None
    assert c.get("/api/auth/me").status_code == 200


def test_delete_account_needs_login(anon_client):
    assert anon_client.post("/api/auth/delete-account", json={"password": "x"}).status_code == 401


# --- Migration 0043 ----------------------------------------------------------------------------


def test_migration_0043_up_and_down(tmp_path):
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *a: subprocess.run([str(alembic), *a], cwd=BACKEND, env=env, check=True, capture_output=True)  # noqa: E731
    run("upgrade", "0042")
    con = sqlite3.connect(db_file)
    con.execute("INSERT INTO channels (id, kind, team_id, created_at) VALUES (1, 'dm', NULL, '2026-01-01')")
    con.execute(
        "INSERT INTO messages (id, channel_id, sender_user_id, sender_name, body, client_uuid, created_at) "
        "VALUES (5, 1, 2, 'ravi', 'hi', 'u1', '2026-01-01')"
    )
    con.execute(
        "INSERT INTO message_reports (message_id, reporter_user_id, reporter_name, reason, created_at, status) "
        "VALUES (5, 3, 'sam', 'rude', '2026-01-01', 'open')"
    )
    con.commit()
    con.close()

    run("upgrade", "head")
    from app.db import Base

    con = sqlite3.connect(db_file)
    try:
        for table in ("team_rules_acceptances", "messages", "message_reports"):
            migrated = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            assert migrated == {c.name for c in Base.metadata.tables[table].columns}, table
        assert con.execute("SELECT target_type, target_id, message_id FROM message_reports").fetchall() == [
            ("message", 5, 5)
        ]
        assert con.execute("SELECT filtered FROM messages").fetchall() == [(0,)]
        con.execute(
            "INSERT INTO message_reports (message_id, target_type, target_id, reporter_user_id, reporter_name, "
            "reason, created_at, status) VALUES (NULL, 'task', 9, 3, 'sam', 'x', '2026-01-02', 'open')"
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO message_reports (message_id, target_type, target_id, reporter_user_id, reporter_name, "
                "reason, created_at, status) VALUES (NULL, 'task', 9, 3, 'sam', 'x', '2026-01-02', 'open')"
            )
    finally:
        con.close()

    run("downgrade", "0042")
    con = sqlite3.connect(db_file)
    try:
        assert "team_rules_acceptances" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        assert con.execute("SELECT message_id, reason FROM message_reports").fetchall() == [(5, "rude")]
        assert "filtered" not in {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    finally:
        con.close()
    run("upgrade", "head")
