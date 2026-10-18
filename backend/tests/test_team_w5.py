"""W5 (docs/BATCH_OCT14_CONTRACT.md): Team tab advanced - template steps,
weekday assignees and "Create task now"; the board's In progress; the
overdue view and admin completion stats; pinned messages, seen-by, @mention
pushes, message search; voice notes and PDFs in chat (and what older apps
see); moderation and the demo guard over the new content."""

import datetime as dt
import io
import json
import struct
import uuid
import zoneinfo

import pytest

from app.models import DeviceToken
from app.team import chat_service as chat
from app.team import media as media_svc
from app.team import recurrence
from app.team import tasks_service as svc
from app.team.models import TeamMedia
from app.team.tasks_models import Task, TaskItem, TaskTemplate
from app.timeutil import utcnow
from tests.conftest import add_member, client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")


def ny(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=NY).astimezone(dt.timezone.utc)


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    monkeypatch.setattr(svc, "_quiet", lambda now: False)
    monkeypatch.setattr(chat, "in_quiet_hours", lambda: False)


@pytest.fixture
def pushes(monkeypatch):
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


def device(db, user):
    db.add(DeviceToken(user_id=user.id, token=f"tok-{user.username}", platform="ios"))
    db.commit()


# --- media helpers -------------------------------------------------------------


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def m4a_bytes(seconds: float = 3.0, with_duration: bool = True) -> bytes:
    """A minimal MP4 with an AAC ('mp4a') sample entry and an mvhd."""
    timescale = 1000
    duration = int(seconds * timescale) if with_duration else 0
    mvhd = _box(b"mvhd", bytes([0, 0, 0, 0]) + b"\0" * 8 + struct.pack(">II", timescale, duration) + b"\0" * 80)
    stsd = _box(b"stsd", b"\0" * 8 + _box(b"mp4a", b"\0" * 28))
    trak = _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", stsd))))
    return _box(b"ftyp", b"M4A \0\0\0\0M4A isom") + _box(b"moov", mvhd + trak) + _box(b"mdat", b"\0" * 64)


def pdf_bytes(pages: int = 2) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(100, 700, f"Page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def upload(client, data, filename, content_type, **fields):
    return client.post(
        "/api/team/media",
        files={"file": (filename, data, content_type)},
        data={k: str(v) for k, v in fields.items()},
    )


def chatter(db, name, **kw):
    return make_user(db, name, features=["team_chat", "team_tasks"], **kw)


def kitchen(client, team):
    channels = client.get("/api/team/channels").json()["channels"]
    return next(c for c in channels if c["team_id"] == team.id)


def send(client, channel_id, body="hi", **extra):
    r = client.post(
        f"/api/team/channels/{channel_id}/messages",
        json={"body": body, "client_uuid": str(uuid.uuid4()), **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- Templates: steps, weekday assignees, repeats, create now --------------------


def make_template(db, admin, **kw):
    fields = dict(
        title="Open store",
        requires="none",
        priority="normal",
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone="America/New_York",
        generate_ahead_minutes=60,
        active=True,
        created_by_user_id=admin.id,
        created_by_name=admin.username,
        created_at=ny(2026, 9, 1),
        updated_at=ny(2026, 9, 1),
    )
    fields.update(kw)
    tpl = TaskTemplate(**fields)
    db.add(tpl)
    db.commit()
    return tpl


def test_template_api_steps_days_and_preview(db, admin_client, admin, staff, staff2, team):
    add_member(db, team, staff)
    body = {
        "title": "Open store",
        "rrule": "FREQ=DAILY;BYHOUR=9",
        "assignee_user_id": staff.id,
        "items": ["Lights on", "  ", "Count the till", "Unlock doors"],
        "day_assignees": {"SA": {"user_id": staff2.id}, "SU": {"team_id": team.id}},
    }
    r = admin_client.post("/api/team/templates", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["items"] == ["Lights on", "Count the till", "Unlock doors"]
    assert [(d["day"], d["type"], d["id"]) for d in out["day_assignees"]] == [
        ("SA", "user", staff2.id), ("SU", "team", team.id)
    ]
    assert out["repeats"] is True
    assert out["next"] is not None and "assignee" in out["next"]
    # The list form also works, and {} clears them.
    r = admin_client.patch(f"/api/team/templates/{out['id']}", json={"day_assignees": [{"day": "MO", "team_id": team.id}]})
    assert [d["day"] for d in r.json()["day_assignees"]] == ["MO"]
    r = admin_client.patch(f"/api/team/templates/{out['id']}", json={"day_assignees": {}})
    assert r.json()["day_assignees"] == []
    # Bad input.
    bad = admin_client.patch(f"/api/team/templates/{out['id']}", json={"day_assignees": {"XX": {"user_id": staff.id}}})
    assert bad.status_code == 400
    bad = admin_client.patch(
        f"/api/team/templates/{out['id']}", json={"day_assignees": {"MO": {"user_id": staff.id, "team_id": team.id}}}
    )
    assert bad.status_code == 400
    nobody = make_user(db, "nochat", features=["daily_checklists"])
    bad = admin_client.patch(f"/api/team/templates/{out['id']}", json={"day_assignees": {"MO": {"user_id": nobody.id}}})
    assert bad.status_code == 400
    # Steps are word-filtered like any task text.
    r = admin_client.patch(f"/api/team/templates/{out['id']}", json={"items": ["no shit"]})
    assert r.json()["items"] == ["no s***"]


def test_generation_copies_steps_and_uses_weekday_assignee(db, admin, staff, staff2, team):
    add_member(db, team, staff2)  # Oct 15: an empty team's occurrence is skipped
    tpl = make_template(
        db,
        admin,
        assignee_user_id=staff.id,
        items_json=json.dumps(["Lights on", "Count the till"]),
        day_assignees_json=json.dumps({"SA": {"user_id": staff2.id}, "SU": {"team_id": team.id}}),
    )
    # Fri Oct 2, Sat Oct 3, Sun Oct 4 2026 (9 AM due, appears 8 AM).
    recurrence.generate_occurrences(db, ny(2026, 10, 2, 8, 30))
    recurrence.generate_occurrences(db, ny(2026, 10, 3, 8, 30))
    recurrence.generate_occurrences(db, ny(2026, 10, 4, 8, 30))
    db.expire_all()
    tasks = db.query(Task).filter(Task.template_id == tpl.id).order_by(Task.due_at).all()
    assert [(t.assignee_user_id, t.assignee_team_id) for t in tasks] == [
        (staff.id, None), (staff2.id, None), (None, team.id)
    ]
    for t in tasks:
        items = db.query(TaskItem).filter(TaskItem.task_id == t.id).order_by(TaskItem.position).all()
        assert [i.text for i in items] == ["Lights on", "Count the till"]


def test_weekday_assignee_problem_skips_only_that_day(db, admin, staff, staff2):
    tpl = make_template(db, admin, assignee_user_id=staff.id, day_assignees_json=json.dumps({"SA": {"user_id": staff2.id}}))
    staff2.allowed_features = json.dumps([])
    db.commit()
    recurrence.generate_occurrences(db, ny(2026, 10, 2, 8, 30))
    recurrence.generate_occurrences(db, ny(2026, 10, 3, 8, 30))
    db.expire_all()
    tasks = db.query(Task).filter(Task.template_id == tpl.id).all()
    assert [t.assignee_user_id for t in tasks] == [staff.id]


def test_non_repeating_template_and_create_now(db, admin_client, admin, staff, staff_client):
    r = admin_client.post(
        "/api/team/templates",
        json={"title": "Deep clean fryer", "repeats": False, "assignee_user_id": staff.id, "items": ["Drain", "Scrub"],
              "requires": "photo"},
    )
    assert r.status_code == 200, r.text
    tpl = r.json()
    assert tpl["repeats"] is False and tpl["rrule"] == "" and tpl["next"] is None
    # Never generated.
    assert recurrence.generate_occurrences(db, utcnow() + dt.timedelta(days=3)) == []
    # Create now: the template's assignee, steps and requirement.
    r = admin_client.post(f"/api/team/templates/{tpl['id']}/create-task", json={})
    assert r.status_code == 200, r.text
    task = r.json()["tasks"][0]
    assert task["assignee_user_id"] == staff.id and task["requires"] == "photo"
    assert task["item_count"] == 2 and task["source_type"] == "template" and task["template_id"] is None
    # Someone else and a due time.
    r = admin_client.post(
        f"/api/team/templates/{tpl['id']}/create-task", json={"assignee_user_ids": [admin.id], "due_at": "2026-12-01T10:00:00"}
    )
    assert r.json()["tasks"][0]["due_at"].startswith("2026-12-01T15:00")
    # Staff without can_assign can't use templates.
    assert staff_client.post(f"/api/team/templates/{tpl['id']}/create-task", json={}).status_code == 403
    assert admin_client.post("/api/team/templates/9999/create-task", json={}).status_code == 404
    # Turning repeats on needs a rule.
    bad = admin_client.patch(f"/api/team/templates/{tpl['id']}", json={"repeats": True})
    assert bad.status_code == 400
    ok = admin_client.patch(f"/api/team/templates/{tpl['id']}", json={"repeats": True, "rrule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=7"})
    assert ok.status_code == 200 and ok.json()["repeats"] is True


def test_lead_can_create_from_template_for_others(db, admin, staff):
    lead = make_user(db, "lead", features=["team_tasks"], can_assign=True)
    tpl = make_template(db, admin, assignee_user_id=staff.id, repeats=False, rrule="")
    r = client_for(db, lead).post(f"/api/team/templates/{tpl.id}/create-task", json={})
    assert r.status_code == 200 and r.json()["tasks"][0]["assignee_user_id"] == staff.id


# --- Board: In progress ------------------------------------------------------------


def test_start_and_unstart_task(db, admin_client, staff_client, staff, staff2_client):
    task = admin_client.post("/api/team/tasks", json={"title": "Face shelves", "assignee_user_ids": [staff.id]}).json()["tasks"][0]
    assert task["board_column"] == "todo" and task["can"]["start"] is True  # admins can act on any task
    mine = staff_client.get(f"/api/team/tasks/{task['id']}").json()
    assert mine["can"]["start"] is True and mine["can"]["unstart"] is False
    r = staff_client.post(f"/api/team/tasks/{task['id']}/start", json={"started": True})
    assert r.status_code == 200
    out = r.json()
    assert out["board_column"] == "in_progress" and out["status"] == "open"
    assert out["started_by"]["id"] == staff.id and out["can"]["unstart"] is True
    assert [e["kind"] for e in out["events"]][-1] == "started"
    # Someone it isn't for: 404 (can't see it).
    assert staff2_client.post(f"/api/team/tasks/{task['id']}/start").status_code == 404
    r = staff_client.post(f"/api/team/tasks/{task['id']}/start", json={"started": False})
    assert r.json()["board_column"] == "todo" and r.json()["started_at"] is None
    # Done, then reopen: back to To do.
    staff_client.post(f"/api/team/tasks/{task['id']}/start")
    done = staff_client.post(f"/api/team/tasks/{task['id']}/complete").json()
    assert done["board_column"] == "done"
    assert staff_client.post(f"/api/team/tasks/{task['id']}/start").status_code == 409
    reopened = admin_client.post(f"/api/team/tasks/{task['id']}/reopen").json()
    assert reopened["board_column"] == "todo" and reopened["started_at"] is None


def test_reassign_moves_back_to_todo(db, admin_client, staff_client, staff, staff2):
    task = admin_client.post("/api/team/tasks", json={"title": "x", "assignee_user_ids": [staff.id]}).json()["tasks"][0]
    staff_client.post(f"/api/team/tasks/{task['id']}/start")
    out = admin_client.patch(f"/api/team/tasks/{task['id']}", json={"assignee_user_id": staff2.id}).json()
    assert out["board_column"] == "todo"


# --- Overdue view + stats ------------------------------------------------------------


def _task(db, admin, **kw):
    now = utcnow()
    fields = dict(title="t", status="open", created_by_user_id=admin.id, created_by_name=admin.username,
                  created_at=now - dt.timedelta(days=1), updated_at=now)
    fields.update(kw)
    t = Task(**fields)
    db.add(t)
    db.commit()
    return t


def test_overdue_view(db, admin, admin_client, staff, staff_client):
    now = utcnow()
    late = _task(db, admin, assignee_user_id=staff.id, due_at=now - dt.timedelta(hours=2))
    _task(db, admin, assignee_user_id=staff.id, due_at=now + dt.timedelta(hours=2))
    _task(db, admin, assignee_user_id=staff.id)
    ids = [t["id"] for t in staff_client.get("/api/team/tasks?view=mine&overdue=true").json()["items"]]
    assert ids == [late.id]
    assert len(staff_client.get("/api/team/tasks?view=mine").json()["items"]) == 3
    assert [t["id"] for t in admin_client.get("/api/team/tasks?view=all&overdue=true").json()["items"]] == [late.id]


def test_task_stats(db, admin, admin_client, staff, staff2, staff_client, team):
    now = utcnow()
    _task(db, admin, assignee_user_id=staff.id, status="done", completed_by_user_id=staff.id, completed_by_name="ravi",
          due_at=now - dt.timedelta(hours=1), completed_at=now - dt.timedelta(hours=2))  # on time
    _task(db, admin, assignee_user_id=staff.id, status="done", completed_by_user_id=staff.id, completed_by_name="ravi",
          due_at=now - dt.timedelta(hours=3), completed_at=now - dt.timedelta(hours=1))  # late
    _task(db, admin, assignee_user_id=staff.id, due_at=now - dt.timedelta(hours=1))  # overdue
    _task(db, admin, assignee_user_id=staff2.id, status="cant_do", completed_by_user_id=staff2.id,
          completed_by_name="sam", completed_at=now)
    _task(db, admin, assignee_team_id=team.id, status="done", completed_by_user_id=staff2.id, completed_by_name="sam",
          completed_at=now)
    _task(db, admin, assignee_user_id=staff.id, status="cancelled")
    _task(db, admin, assignee_user_id=staff.id, created_at=now - dt.timedelta(days=40))  # outside 30 days
    r = admin_client.get("/api/team/task-stats?days=30")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["totals"] == {"tasks": 5, "done": 3, "open": 1, "overdue": 1, "cant_do": 1}
    ravi = next(p for p in out["people"] if p["user_id"] == staff.id)
    assert (ravi["assigned"], ravi["assigned_done"], ravi["done"], ravi["on_time"], ravi["late"], ravi["overdue"]) == (3, 2, 2, 1, 1, 1)
    assert ravi["completion_rate"] == round(2 / 3, 3) and ravi["on_time_rate"] == 0.5
    sam = next(p for p in out["people"] if p["user_id"] == staff2.id)
    assert (sam["assigned"], sam["done"], sam["cant_do"]) == (1, 1, 1)
    kitchen_row = next(t for t in out["teams"] if t["team_id"] == team.id)
    assert (kitchen_row["total"], kitchen_row["done"]) == (1, 1)
    assert staff_client.get("/api/team/task-stats").status_code == 403
    assert admin_client.get("/api/team/task-stats?days=5").status_code == 400


# --- Pins, seen-by, search ------------------------------------------------------------


def test_pins(db, team, admin):
    lead = chatter(db, "lead", can_assign=True)
    ravi = chatter(db, "ravi2")
    for u in (lead, ravi):
        add_member(db, team, u)
    lc, rc = client_for(db, lead), client_for(db, ravi)
    ch = kitchen(lc, team)
    m = send(rc, ch["id"], "Freezer 2 is broken - use freezer 3")
    # Staff can't pin in a team chat; a lead can.
    assert rc.post(f"/api/team/messages/{m['id']}/pin").status_code == 403
    r = lc.post(f"/api/team/messages/{m['id']}/pin")
    assert r.status_code == 200 and r.json()["pinned"] is True and r.json()["pinned_by"]["name"] == "lead"
    pins = rc.get(f"/api/team/channels/{ch['id']}/pins").json()
    assert [p["id"] for p in pins["pins"]] == [m["id"]] and pins["can_pin"] is False
    assert lc.get(f"/api/team/channels/{ch['id']}/pins").json()["can_pin"] is True
    # A deleted message drops off.
    rc.delete(f"/api/team/messages/{m['id']}")
    assert rc.get(f"/api/team/channels/{ch['id']}/pins").json()["pins"] == []
    assert lc.post(f"/api/team/messages/{m['id']}/pin").status_code == 409
    # DMs: either person.
    dm = rc.post("/api/team/channels/dm", json={"user_id": lead.id}).json()
    m2 = send(rc, dm["id"], "keys are in the drawer")
    assert rc.post(f"/api/team/messages/{m2['id']}/pin").status_code == 200
    assert lc.delete(f"/api/team/messages/{m2['id']}/pin").json()["pinned"] is False
    # Non-members can't pin (404).
    other = chatter(db, "outsider")
    assert client_for(db, other).post(f"/api/team/messages/{m2['id']}/pin").status_code == 404


def test_pin_limit(db, team, monkeypatch):
    monkeypatch.setattr(chat, "MAX_PINS", 2)
    lead = chatter(db, "lead", can_assign=True)
    add_member(db, team, lead)
    lc = client_for(db, lead)
    ch = kitchen(lc, team)
    ids = [send(lc, ch["id"], f"m{i}")["id"] for i in range(3)]
    assert lc.post(f"/api/team/messages/{ids[0]}/pin").status_code == 200
    assert lc.post(f"/api/team/messages/{ids[1]}/pin").status_code == 200
    assert lc.post(f"/api/team/messages/{ids[2]}/pin").status_code == 409


def test_seen_by(db, team):
    a, b, c = chatter(db, "anna"), chatter(db, "bob"), chatter(db, "carl")
    for u in (a, b, c):
        add_member(db, team, u)
    ac, bc, cc = client_for(db, a), client_for(db, b), client_for(db, c)
    ch = kitchen(ac, team)
    m = send(ac, ch["id"], "stock count at 4")
    bc.post(f"/api/team/channels/{ch['id']}/read", json={"last_read_message_id": m["id"]})
    out = ac.get(f"/api/team/messages/{m['id']}/seen-by").json()
    assert [p["name"] for p in out["seen"]] == ["bob"] and [p["name"] for p in out["not_seen"]] == ["carl"]
    assert out["total"] == 2
    stranger = chatter(db, "zed")
    assert client_for(db, stranger).get(f"/api/team/messages/{m['id']}/seen-by").status_code == 404


def test_search(db, team):
    a, b = chatter(db, "anna"), chatter(db, "bob")
    add_member(db, team, a)
    ac, bc = client_for(db, a), client_for(db, b)
    ch = kitchen(ac, team)
    m1 = send(ac, ch["id"], "Rice delivery at 3")
    send(ac, ch["id"], "nothing here")
    dm = ac.post("/api/team/channels/dm", json={"user_id": b.id}).json()
    m3 = send(bc, dm["id"], "more RICE please")
    deleted = send(ac, ch["id"], "rice gone")
    ac.delete(f"/api/team/messages/{deleted['id']}")
    out = ac.get("/api/team/messages/search?q=rice").json()
    assert [r["id"] for r in out["results"]] == [m3["id"], m1["id"]]
    assert out["results"][0]["channel"]["name"] == "bob" and out["results"][1]["channel"]["kind"] == "team"
    # bob isn't in Kitchen: only the DM.
    assert [r["id"] for r in bc.get("/api/team/messages/search?q=rice").json()["results"]] == [m3["id"]]
    # Scoped to a channel; LIKE wildcards are literal.
    assert [r["id"] for r in ac.get(f"/api/team/messages/search?q=rice&channel_id={ch['id']}").json()["results"]] == [m1["id"]]
    assert ac.get("/api/team/messages/search?q=%25").status_code == 400
    assert ac.get("/api/team/messages/search?q=%25%25").json()["results"] == []
    # A hidden (blocked) DM isn't searched.
    ac.post("/api/team/blocks", json={"user_id": b.id})
    assert [r["id"] for r in ac.get("/api/team/messages/search?q=rice").json()["results"]] == [m1["id"]]
    # Paging.
    page = ac.get("/api/team/messages/search?q=er&limit=1").json()
    assert page["has_more"] is True and len(page["results"]) == 1


# --- Mentions ------------------------------------------------------------------------------


def test_mention_push(db, team, pushes):
    a, b, c = chatter(db, "anna"), chatter(db, "bob"), chatter(db, "carl")
    for u in (a, b, c):
        add_member(db, team, u)
        device(db, u)
    ac, bc = client_for(db, a), client_for(db, b)
    ch = kitchen(ac, team)
    bc.patch(f"/api/team/channels/{ch['id']}/me", json={"muted": True})
    m = send(ac, ch["id"], "@bob can you check the cooler?")
    by_title = {p["title"]: p["tokens"] for p in pushes}
    assert by_title["anna mentioned you in Kitchen"] == ["tok-bob"]
    assert by_title["anna in Kitchen"] == ["tok-carl"]
    # Sent once: the dedupe key.
    pushes.clear()
    chat.push_new_message(db, m["id"])
    assert all(p["title"] != "anna mentioned you in Kitchen" for p in pushes)
    # Mentions switched off: muted bob gets nothing, unmuted carl the plain push.
    from app.models import NotificationPref

    for u in (b, c):
        db.add(NotificationPref(user_id=u.id, key="chat_mention", enabled=False))
    db.commit()
    # carl has read (so the one-a-minute throttle doesn't hold his push).
    client_for(db, c).post(f"/api/team/channels/{ch['id']}/read", json={"last_read_message_id": m["id"]})
    pushes.clear()
    send(ac, ch["id"], "@bob @carl hello")
    assert [(p["title"], p["tokens"]) for p in pushes] == [("anna in Kitchen", ["tok-carl"])]


def test_mention_never_crosses_a_block(db, team, pushes):
    a, b = chatter(db, "anna"), chatter(db, "bob")
    for u in (a, b):
        add_member(db, team, u)
        device(db, u)
    ac, bc = client_for(db, a), client_for(db, b)
    bc.post("/api/team/blocks", json={"user_id": a.id})
    send(ac, kitchen(ac, team)["id"], "@bob hi")
    assert pushes == []


def test_chat_mention_alert_listed(db, staff_client):
    keys = [p["key"] for p in staff_client.get("/api/notifications/prefs").json()["items"]]
    assert "chat_mention" in keys


# --- Voice notes + documents ---------------------------------------------------------------


def test_voice_note_upload_send_and_old_app_label(db, team, staff_client, staff):
    add_member(db, team, staff)
    r = upload(staff_client, m4a_bytes(12.4), "note.m4a", "audio/mp4", duration_ms=99999)
    assert r.status_code == 200, r.text
    media = r.json()
    # The file's own duration wins over the client's.
    assert media["kind"] == "audio" and media["duration_ms"] == 12400 and media["content_type"] == "audio/mp4"
    ch = kitchen(staff_client, team)
    msg = send(staff_client, ch["id"], "", media_token=media["token"])
    assert msg["media"] is None  # older apps only draw photos from `media`
    assert msg["attachment"]["kind"] == "audio"
    assert msg["body"] == "Voice message (0:12)" and msg["body_is_label"] is True
    # Served with its type, to channel members.
    got = staff_client.get(media["url"])
    assert got.status_code == 200 and got.headers["content-type"] == "audio/mp4"
    assert got.headers["x-content-type-options"] == "nosniff"
    # The stored body stays empty - a search for the label finds nothing.
    assert staff_client.get("/api/team/messages/search?q=voice").json()["results"] == []
    # With text, the text is the body.
    media2 = upload(staff_client, m4a_bytes(2), "n.m4a", "audio/x-m4a").json()
    msg2 = send(staff_client, ch["id"], "listen", media_token=media2["token"])
    assert msg2["body"] == "listen" and msg2["body_is_label"] is False


def test_voice_note_fragmented_uses_client_duration_and_caps(db, staff_client):
    r = upload(staff_client, m4a_bytes(0, with_duration=False), "n.m4a", "audio/mp4", duration_ms=4500)
    assert r.json()["duration_ms"] == 4500
    too_long = upload(staff_client, m4a_bytes(400), "n.m4a", "audio/mp4")
    assert too_long.status_code == 413


@pytest.mark.parametrize(
    "data,ctype,status",
    [
        (b"not audio at all", "audio/mp4", 415),
        (b"\0\0\0\x18ftypM4A " + b"\0" * 20, "audio/mp4", 415),  # no moov
        (b"OggS" + b"\0" * 100, "audio/ogg", 415),  # not an accepted type
    ],
)
def test_bad_voice_notes_refused(db, staff_client, data, ctype, status):
    assert upload(staff_client, data, "x", ctype).status_code == status


def test_voice_without_aac_refused(db, staff_client):
    data = m4a_bytes(3).replace(b"mp4a", b"opus")
    assert upload(staff_client, data, "x.m4a", "audio/mp4").status_code == 415


def test_document_upload_and_send(db, team, staff_client, staff):
    add_member(db, team, staff)
    r = upload(staff_client, pdf_bytes(3), "../../etc/Invoice‮ March.PDF", "application/pdf")
    assert r.status_code == 200, r.text
    media = r.json()
    assert media["kind"] == "document" and media["page_count"] == 3
    assert media["name"] == "Invoice March.pdf"
    row = db.query(TeamMedia).filter(TeamMedia.token == media["token"]).one()
    assert row.path.endswith(".pdf") and "Invoice" not in row.path
    ch = kitchen(staff_client, team)
    msg = send(staff_client, ch["id"], "", media_token=media["token"])
    assert msg["body"] == "Document: Invoice March.pdf" and msg["attachment"]["page_count"] == 3
    got = staff_client.get(media["url"])
    assert got.headers["content-type"] == "application/pdf"
    assert "inline" in got.headers["content-disposition"]
    # Found by its name.
    assert [m["id"] for m in staff_client.get("/api/team/messages/search?q=march").json()["results"]] == [msg["id"]]


def test_bad_documents_refused(db, staff_client):
    assert upload(staff_client, b"hello", "a.pdf", "application/pdf").status_code == 415
    assert upload(staff_client, b"%PDF-1.4 garbage", "a.pdf", "application/pdf").status_code == 415


def test_voice_and_pdf_only_for_chat_and_messages(db, admin_client, team, staff, staff_client):
    tasks_only = make_user(db, "tasker", features=["team_tasks"])
    tc = client_for(db, tasks_only)
    # Without chat: checked (and refused) as an image.
    assert upload(tc, m4a_bytes(), "n.m4a", "audio/mp4").status_code == 415
    assert upload(tc, pdf_bytes(), "a.pdf", "application/pdf").status_code == 415
    # A voice note can't be a task's proof photo.
    voice = upload(staff_client, m4a_bytes(), "n.m4a", "audio/mp4").json()
    task = admin_client.post("/api/team/tasks", json={"title": "x", "assignee_user_ids": [staff.id], "requires": "photo"}).json()["tasks"][0]
    r = staff_client.post(f"/api/team/tasks/{task['id']}/complete", data={"photo_token": voice["token"]})
    assert r.status_code == 400
    # Nor an announcement's picture.
    doc = upload(admin_client, pdf_bytes(), "a.pdf", "application/pdf").json()
    r = admin_client.post("/api/team/announcements", json={"title": "t", "team_ids": [team.id], "media_token": doc["token"]})
    assert r.status_code == 400


def test_media_access_for_new_kinds(db, team, staff_client, staff):
    add_member(db, team, staff)
    media = upload(staff_client, pdf_bytes(), "a.pdf", "application/pdf").json()
    ch = kitchen(staff_client, team)
    send(staff_client, ch["id"], "", media_token=media["token"])
    outsider = chatter(db, "outsider")
    assert client_for(db, outsider).get(media["url"]).status_code == 404


def test_reported_voice_note_in_moderation(db, team, admin_client, staff_client, staff):
    other = chatter(db, "bob")
    for u in (staff, other):
        add_member(db, team, u)
    voice = upload(staff_client, m4a_bytes(5), "n.m4a", "audio/mp4").json()
    msg = send(staff_client, kitchen(staff_client, team)["id"], "", media_token=voice["token"])
    rep = client_for(db, other).post(f"/api/team/messages/{msg['id']}/report", json={"reason": "Harassment"}).json()
    reports = admin_client.get("/api/team/reports?status=open").json()["reports"]
    r = next(x for x in reports if x["id"] == rep["id"])
    assert r["message"]["attachment"]["kind"] == "audio" and r["message"]["media"] is None
    assert r["message"]["body"].startswith("Voice message") and r["message"]["body_is_label"] is True
    # Admin can still play it after removing the message.
    admin_client.post(f"/api/team/reports/{rep['id']}/resolve", json={"delete_message": True})
    assert admin_client.get(voice["url"]).status_code == 200
    assert staff_client.get(f"/api/team/channels/{kitchen(staff_client, team)['id']}/messages").json()["messages"][-1]["attachment"] is None


def test_retention_removes_voice_files(db, team, staff_client, staff):
    add_member(db, team, staff)
    voice = upload(staff_client, m4a_bytes(), "n.m4a", "audio/mp4").json()
    msg = send(staff_client, kitchen(staff_client, team)["id"], "", media_token=voice["token"])
    row = db.query(TeamMedia).filter(TeamMedia.token == voice["token"]).one()
    path = media_svc.media_file(row)
    assert path.exists()
    staff_client.delete(f"/api/team/messages/{msg['id']}")
    chat.run_chat_retention(db, now=utcnow() + dt.timedelta(days=40))
    assert not path.exists()


def test_push_preview_for_voice_note(db, team, pushes):
    a, b = chatter(db, "anna"), chatter(db, "bob")
    for u in (a, b):
        add_member(db, team, u)
        device(db, u)
    ac = client_for(db, a)
    voice = upload(ac, m4a_bytes(7), "n.m4a", "audio/mp4").json()
    send(ac, kitchen(ac, team)["id"], "", media_token=voice["token"])
    assert pushes[-1]["body"] == "Voice message (0:07)"


# --- Demo guard ------------------------------------------------------------------------------


def test_demo_guard_allows_only_sandboxed_new_writes(db, monkeypatch, admin_client, team):
    from app import demo

    monkeypatch.setattr(demo, "demo_usernames", lambda: {"appreview"})
    import app.team.changes as changes

    monkeypatch.setattr(changes, "demo_usernames", lambda: {"appreview"})
    review = chatter(db, "appreview", can_assign=True)
    add_member(db, team, review)
    rc = client_for(db, review)
    mine = rc.post("/api/team/tasks", json={"title": "mine"}).json()["tasks"][0]
    assert rc.post(f"/api/team/tasks/{mine['id']}/start").status_code == 200
    other = chatter(db, "realstaff")
    theirs = admin_client.post("/api/team/tasks", json={"title": "x", "assignee_user_ids": [other.id]}).json()["tasks"][0]
    r = rc.post(f"/api/team/tasks/{theirs['id']}/start")
    assert r.status_code == 403 and "demo" in r.json()["detail"].lower()
    tpl = admin_client.post("/api/team/templates", json={"title": "t", "repeats": False, "assignee_user_id": other.id}).json()
    assert rc.post(f"/api/team/templates/{tpl['id']}/create-task", json={}).status_code == 403
    # Staff completion stats are never shown to a demo login, even an admin one.
    review.is_admin = True
    db.commit()
    assert rc.get("/api/team/task-stats").status_code == 403
