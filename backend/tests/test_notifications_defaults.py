"""Notifications, Oct 15 batch (docs/BATCH_OCT15_CONTRACT.md "As built -
NOTIFICATIONS"): role-aware "Recommended" defaults, the 9 AM Morning summary,
the additive prefs API + reset, admin store defaults (audited), the per-user
push log, invoice_failed / inventory_upload_due recipients and the quieter
logs."""

import datetime as dt
import json
import logging
import os
import pathlib
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest

from app.demo_guard import DEMO_READ_ONLY
from app.models import DeviceToken, InvoiceSubmission, NotificationLog, NotificationPref
from app.notification_models import PushDelivery
from app.services import notification_triggers as trig
from app.services import notifications as notif
from app.services import push
from app.setting_models import SettingChange
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")
NINE = dt.datetime(2026, 10, 15, 9, 0, tzinfo=NY)

# The owner's "Recommended" table (Oct 15), (staff, admin).
RECOMMENDED = {
    "morning_summary": (False, True),
    "order_due": (False, False),
    "critical_vendors": (False, False),
    "closing_checklist": (True, True),
    "invoice_failed": (True, True),
    "open_orders_morning": (False, False),
    "task_assigned": (True, True),
    "task_due": (True, True),
    "task_due_soon": (False, False),
    "fix_issues_task_due": (False, False),
    "task_cant_do": (True, True),
    "announcement": (True, True),
    "fix_issues_reopened": (False, False),
    "cart_removal_request": (False, True),
    "chat_message": (True, True),
    "flyer_sent": (False, False),
    "inventory_upload_due": (False, False),
    "loss_watch": (False, False),
    "daily_sales_summary": (False, False),
    "inventory_api_ready": (False, False),
    "chat_mention": (True, True),
    # added by the tasks build (team/tasks_service.py), off for everyone
    "task_done": (False, False),
    "task_comment": (False, False),
}
OLD_KEYS = {  # the 18 keys builds 5-9 know - none may disappear
    "order_due", "critical_vendors", "closing_checklist", "invoice_failed", "open_orders_morning",
    "task_assigned", "task_due", "task_cant_do", "announcement", "fix_issues_reopened",
    "cart_removal_request", "chat_message", "flyer_sent", "inventory_upload_due", "loss_watch",
    "daily_sales_summary", "inventory_api_ready", "chat_mention",
}


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


def device(db, *users):
    for u in users:
        db.add(DeviceToken(user_id=u.id, token=f"tok-{u.username}", platform="ios"))
    db.commit()


def reached(sent):
    return sorted({t.replace("tok-", "") for s in sent for t in s["tokens"]})


# --------------------------------------------------------------------- catalog


def test_catalog_matches_the_owners_recommended_table():
    keys = [a["key"] for a in notif.ALERTS]
    assert len(keys) == len(set(keys))
    assert OLD_KEYS <= set(keys), "a key builds 5-9 know was removed"
    assert {a["key"]: (a["defaults"]["staff"], a["defaults"]["admin"]) for a in notif.ALERTS} == RECOMMENDED
    for a in notif.ALERTS:
        assert a["group"] in notif.GROUP_KEYS
        assert set(a["defaults"]) == {"staff", "admin"}
        assert "new customer orders" not in a["description"].lower()
    assert notif.ALERTS_BY_KEY["morning_summary"]["route"] == "home"
    assert notif.ALERTS_BY_KEY["inventory_upload_due"]["admin_only"] is True
    essential = {a["key"] for a in notif.ALERTS if a.get("essential")}
    assert {"closing_checklist", "task_assigned", "task_cant_do", "announcement", "chat_mention"} <= essential
    assert "open_orders_morning" not in essential and "morning_summary" not in essential


def test_quiet_hours_stay_10pm_to_7am():
    assert notif.in_quiet_hours(dt.datetime(2026, 10, 15, 22, 0, tzinfo=NY))
    assert notif.in_quiet_hours(dt.datetime(2026, 10, 15, 6, 59, tzinfo=NY))
    assert not notif.in_quiet_hours(dt.datetime(2026, 10, 15, 7, 0, tzinfo=NY))


# ------------------------------------------------------------- effective value


def test_explicit_choice_beats_store_default_beats_catalog(db, admin, staff):
    assert notif.effective_enabled(db, staff, "chat_message") is True
    assert notif.effective_enabled(db, staff, "morning_summary") is False
    assert notif.effective_enabled(db, admin, "morning_summary") is True
    notif.set_store_default(db, admin, "chat_message", "staff", False)
    assert notif.effective_enabled(db, staff, "chat_message") is False
    assert notif.effective_enabled(db, admin, "chat_message") is True, "only the staff role changed"
    notif.set_pref(db, staff, "chat_message", True)
    assert notif.effective_enabled(db, staff, "chat_message") is True, "the user's choice wins"
    notif.set_pref(db, admin, "morning_summary", False)
    assert notif.effective_enabled(db, admin, "morning_summary") is False


def test_bad_store_defaults_row_falls_back_to_the_catalog(db, admin, staff):
    db.add(SettingChange(key=notif.STORE_DEFAULTS_KEY, value="not json", changed_at=utcnow()))
    db.commit()
    assert notif.store_defaults(db) == {}
    db.add(SettingChange(key=notif.STORE_DEFAULTS_KEY, value=json.dumps({"nope": {"staff": True},
                         "chat_message": {"staff": "yes", "admin": False}}), changed_at=utcnow()))
    db.commit()
    assert notif.store_defaults(db) == {"chat_message": {"admin": False}}


def test_deliverable_skips_no_device_no_access_and_off(db, admin, staff, staff2):
    nobody = make_user(db, "nofeat")
    device(db, admin, staff, nobody)
    notif.set_pref(db, staff, "task_assigned", False)
    ids = {admin.id, staff.id, staff2.id, nobody.id}
    assert notif.deliverable_user_ids(db, "task_assigned", ids) == {admin.id}
    assert notif.deliverable_user_ids(db, "task_assigned", []) == set()


def test_unknown_keys_keep_the_old_rule(db, staff):
    """chat_report / test aren't in the catalog: off only by an explicit row."""
    assert notif.disabled_user_ids(db, "chat_report", [staff.id]) == set()
    db.add(NotificationPref(user_id=staff.id, key="chat_report", enabled=False))
    db.commit()
    assert notif.disabled_user_ids(db, "chat_report", [staff.id]) == {staff.id}


# ------------------------------------------------------------------- prefs API


def test_prefs_api_is_additive_and_grouped(db, staff_client, admin_client):
    body = staff_client.get("/api/notifications/prefs").json()
    items = body["items"]
    for item in items:
        assert {"key", "label", "description", "enabled"} <= set(item)  # what builds 5-9 read
        assert {"group", "default", "essential", "admin_only", "explicit"} <= set(item)
        assert item["explicit"] is False and item["enabled"] == item["default"]
    keys = [i["key"] for i in items]
    # staff ravi has team_tasks + team_chat only
    assert set(keys) == {
        "morning_summary", "task_assigned", "task_due", "task_due_soon", "fix_issues_task_due", "task_cant_do",
        "announcement", "chat_message", "chat_mention", "task_done", "task_comment",
    }
    ranks = [notif.GROUP_KEYS.index(i["group"]) for i in items]
    assert ranks == sorted(ranks), "listed group by group"
    assert [g["key"] for g in body["groups"]] == ["needs_action", "team", "daily_summary"]
    on = {i["key"] for i in items if i["enabled"]}
    assert on == {"task_assigned", "task_due", "task_cant_do", "announcement", "chat_message", "chat_mention"}

    admin_items = {i["key"]: i for i in admin_client.get("/api/notifications/prefs").json()["items"]}
    assert set(admin_items) == set(RECOMMENDED)
    assert admin_items["morning_summary"]["enabled"] is True and admin_items["cart_removal_request"]["enabled"]
    assert admin_items["inventory_upload_due"]["admin_only"] is True


def test_put_writes_an_explicit_choice_and_reset_forgets_it(db, staff, staff_client):
    from app.team.chat_service import HIDE_PREVIEWS_KEY

    r = staff_client.put("/api/notifications/prefs", json={"key": "task_due_soon", "enabled": True})
    assert r.status_code == 200
    item = {i["key"]: i for i in r.json()["items"]}["task_due_soon"]
    assert item == {**item, "enabled": True, "explicit": True, "default": False}
    staff_client.put("/api/notifications/prefs", json={"key": "chat_message", "enabled": False})
    staff_client.put("/api/team/chat/settings", json={"hide_previews": True})
    assert staff_client.put("/api/notifications/prefs", json={"key": "loss_watch", "enabled": True}).status_code == 400

    r = staff_client.post("/api/notifications/prefs/reset")
    assert r.status_code == 200
    items = {i["key"]: i for i in r.json()["items"]}
    assert items["task_due_soon"]["enabled"] is False and items["chat_message"]["enabled"] is True
    assert not any(i["explicit"] for i in items.values())
    rows = db.query(NotificationPref).filter_by(user_id=staff.id).all()
    assert [(r.key, r.enabled) for r in rows] == [(HIDE_PREVIEWS_KEY, True)], "hide-previews is kept"


def test_demo_can_switch_its_own_alerts_but_not_reset_or_set_store_defaults(db):
    demo = make_user(db, "app_store_review", is_admin=True)
    c = client_for(db, demo)
    assert c.get("/api/notifications/prefs").status_code == 200
    assert c.put("/api/notifications/prefs", json={"key": "chat_message", "enabled": False}).status_code == 200
    for method, path, body in [
        ("POST", "/api/notifications/prefs/reset", None),
        ("PUT", "/api/notifications/defaults", {"key": "chat_message", "role": "staff", "enabled": False}),
        ("POST", "/api/notifications/defaults/reset", None),
    ]:
        r = c.request(method, path, json=body)
        assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY}), path
    assert db.query(SettingChange).count() == 0


# --------------------------------------------------------- store defaults (P4)


def test_store_defaults_are_admin_only_and_audited(db, admin, admin_client, staff_client):
    assert staff_client.get("/api/notifications/defaults").status_code == 403
    assert staff_client.put(
        "/api/notifications/defaults", json={"key": "chat_message", "role": "staff", "enabled": False}
    ).status_code == 403
    assert staff_client.get("/api/notifications/volume").status_code == 403

    state = admin_client.get("/api/notifications/defaults").json()
    by_key = {i["key"]: i for i in state["items"]}
    assert by_key["morning_summary"]["catalog"] == {"staff": False, "admin": True}
    assert by_key["morning_summary"]["store"] == {"staff": None, "admin": None}
    assert by_key["loss_watch"]["roles"] == ["admin"]
    assert state["changed_by"] is None and state["history"] == []

    r = admin_client.put("/api/notifications/defaults", json={"key": "morning_summary", "role": "staff", "enabled": True})
    assert r.status_code == 200
    item = {i["key"]: i for i in r.json()["items"]}["morning_summary"]
    assert item["store"]["staff"] is True and item["effective"] == {"staff": True, "admin": True}
    assert r.json()["changed_by"] == "boss"
    row = db.query(SettingChange).filter_by(key=notif.STORE_DEFAULTS_KEY).one()
    assert json.loads(row.value) == {"morning_summary": {"staff": True}}
    assert json.loads(row.previous_value) == {} and row.previous_source == "default"
    assert row.changed_by_user_id == admin.id and "Morning summary for staff: on" in row.note
    # Same again: no new history row.
    admin_client.put("/api/notifications/defaults", json={"key": "morning_summary", "role": "staff", "enabled": True})
    assert db.query(SettingChange).count() == 1
    # Staff now get the summary by default.
    staff_items = {i["key"]: i for i in staff_client.get("/api/notifications/prefs").json()["items"]}
    assert staff_items["morning_summary"]["enabled"] is True and staff_items["morning_summary"]["default"] is True

    assert admin_client.put(
        "/api/notifications/defaults", json={"key": "loss_watch", "role": "staff", "enabled": True}
    ).status_code == 400
    assert admin_client.put(
        "/api/notifications/defaults", json={"key": "nope", "role": "staff", "enabled": True}
    ).status_code == 400
    # null = back to recommended
    r = admin_client.put("/api/notifications/defaults", json={"key": "morning_summary", "role": "staff", "enabled": None})
    assert {i["key"]: i for i in r.json()["items"]}["morning_summary"]["store"]["staff"] is None
    admin_client.put("/api/notifications/defaults", json={"key": "chat_message", "role": "admin", "enabled": False})
    r = admin_client.post("/api/notifications/defaults/reset")
    assert r.status_code == 200 and notif.store_defaults(db) == {}
    assert len(r.json()["history"]) == 4


# --------------------------------------------------------------- dispatching


def test_default_off_alert_reaches_only_who_turned_it_on(db, admin, staff, pushes):
    lead = make_user(db, "lead", features=["open_orders"])
    device(db, admin, lead)
    alert = notif.Alert(key="open_orders_morning", dedupe_keys=["oo:1"], title="Day end: 2 open checks", body="")
    assert notif.dispatch_alert(db, alert) == 0
    assert pushes == []
    notif.set_pref(db, lead, "open_orders_morning", True)
    assert notif.dispatch_alert(db, alert) == 1
    assert reached(pushes) == ["lead"]


def test_task_due_soon_and_lead_task_reminders_have_their_own_switches(db, admin, staff, pushes):
    from app.services.issue_leads import SOURCE_TYPE
    from app.team.tasks_models import Task

    device(db, staff)
    now = utcnow()
    task = Task(title="Fix Issues: 40 left", assignee_user_id=staff.id, status="open", created_by_user_id=0,
                created_by_name="System", created_at=now, updated_at=now, source_type=SOURCE_TYPE)
    db.add(task)
    db.commit()

    def due(kind, task_id, n):
        return notif.Alert(key="task_due", dedupe_keys=[f"task_due:{task_id}:{kind}:{n}"], title=kind, body="",
                           data={"task_id": task_id}, user_ids=[staff.id])

    assert notif.alert_pref_key(db, due("soon", 999, 1)) == "task_due_soon"
    assert notif.alert_pref_key(db, due("overdue", 999, 1)) == "task_due"
    assert notif.alert_pref_key(db, due("overdue", task.id, 1)) == "fix_issues_task_due"
    assert notif.dispatch_alert(db, due("soon", 999, 1)) == 0
    assert notif.dispatch_alert(db, due("overdue", task.id, 1)) == 0
    assert notif.dispatch_alert(db, due("overdue", 999, 1)) == 1
    assert pushes[0]["data"]["type"] == "task_due" and pushes[0]["data"]["route"] == "task"
    notif.set_pref(db, staff, "task_due_soon", True)
    notif.set_pref(db, staff, "fix_issues_task_due", True)
    assert notif.dispatch_alert(db, due("soon", 999, 2)) == 1
    assert notif.dispatch_alert(db, due("soon", task.id, 2)) == 1
    explicit = notif.Alert(key="task_due", dedupe_keys=["x:soon:3"], title="t", body="", user_ids=[staff.id],
                           pref_key="task_assigned")
    assert notif.alert_pref_key(db, explicit) == "task_assigned"


def test_sends_are_logged_per_user_and_listed(db, admin, staff, admin_client, staff_client, pushes):
    device(db, admin, staff)
    notif.dispatch_alert(db, notif.Alert(key="announcement", dedupe_keys=["a:1"], title="Staff meeting", body="Fri"))
    rows = db.query(PushDelivery).order_by(PushDelivery.user_id).all()
    assert [(r.user_id, r.key, r.title) for r in rows] == [(admin.id, "announcement", "Staff meeting"),
                                                           (staff.id, "announcement", "Staff meeting")]
    recent = staff_client.get("/api/notifications/recent").json()["items"]
    assert recent[0]["key"] == "announcement" and recent[0]["label"] == "Announcements"
    assert recent[0]["title"] == "Staff meeting" and recent[0]["sent_at"]
    vol = admin_client.get("/api/notifications/volume?days=7").json()
    assert vol["days"] == 7 and {u["username"]: u["by_key"] for u in vol["users"]} == {
        "boss": {"announcement": 1}, "ravi": {"announcement": 1}}


def test_dry_run_and_no_recipients_log_nothing(db, staff, caplog):
    caplog.set_level(logging.INFO, logger="app.services.notifications")
    alert = notif.Alert(key="task_assigned", dedupe_keys=["t:1"], title="t", body="", user_ids=[staff.id])
    notif.dispatch_alert(db, alert)  # Firebase not configured: dry run
    assert db.query(PushDelivery).count() == 0


def test_no_recipient_line_is_debug_only(db, staff, pushes, caplog):
    caplog.set_level(logging.INFO, logger="app.services.notifications")
    alert = notif.Alert(key="task_assigned", dedupe_keys=["t:2"], title="t", body="", user_ids=[staff.id])
    assert notif.dispatch_alert(db, alert) == 0
    assert "has no recipients" not in caplog.text


def test_job_runner_drops_the_found_0_line(db, caplog):
    caplog.set_level(logging.INFO, logger="app.services.notification_triggers")
    trig.run_alert_job("invoice_failed", now=dt.datetime(2026, 10, 15, 12, 0, tzinfo=NY), db=db)
    trig.run_alert_job("invoice_failed", now=dt.datetime(2026, 10, 15, 23, 0, tzinfo=NY), db=db)
    assert "found 0" not in caplog.text and "quiet hours" not in caplog.text


# ----------------------------------------------------------------------- P8


def test_invoice_failed_goes_to_the_submitter_and_admins(db, admin, pushes):
    submitter = make_user(db, "clerk", features=["invoice_submission"])
    other = make_user(db, "clerk2", features=["invoice_submission"])
    device(db, admin, submitter, other)
    db.add(InvoiceSubmission(
        vendor="Babco", submission_type="invoice", submitted_by_user_id=submitter.id, submitted_by_name="clerk",
        original_filename="a.pdf", stored_filename="t1.pdf", file_token="ttok1", created_at=utcnow(), whatsapp_sent=False, whatsapp_error="timeout", toast_email_sent=True,
    ))
    db.commit()
    (alert,) = trig.invoice_failed_alerts(db, dt.datetime.now(NY))
    assert sorted(alert.user_ids) == sorted([admin.id, submitter.id])
    assert notif.dispatch_alert(db, alert) == 2
    assert reached(pushes) == ["boss", "clerk"]


def test_inventory_upload_due_is_admin_only(db, admin):
    staff = make_user(db, "viewer", features=["overview"])
    assert notif.user_can_access_alert(admin, "inventory_upload_due")
    assert not notif.user_can_access_alert(staff, "inventory_upload_due")


# ------------------------------------------------------------- chat (pref filter + log)


def test_chat_push_follows_store_defaults_and_is_logged(db, admin, staff, pushes):
    from app.team import chat_service as chat

    device(db, staff)
    assert chat.pref_disabled_user_ids(db, "chat_message", [staff.id]) == set()
    notif.set_store_default(db, admin, "chat_message", "staff", False)
    assert chat.pref_disabled_user_ids(db, "chat_message", [staff.id]) == {staff.id}
    notif.set_pref(db, staff, "chat_message", True)
    assert chat.pref_disabled_user_ids(db, "chat_message", [staff.id]) == set()
    got = chat.push_to_users(db, "chat_message", [staff.id], "boss in Kitchen", "hi", {"route": "chat"})
    assert got == {staff.id}
    row = db.query(PushDelivery).one()
    assert (row.user_id, row.key, row.title) == (staff.id, "chat_message", "boss in Kitchen")


# ----------------------------------------------------------- Morning summary (P2)


def _fake_morning(monkeypatch, extra=None):
    """Every morning source returns a fixed alert (no store data needed)."""
    alerts = {
        "critical_vendors": [notif.Alert(key="critical_vendors", dedupe_keys=["cv:d"], title="2 vendors critical",
                                         body="Babco, KP")],
        "order_due": [
            notif.Alert(key="order_due", dedupe_keys=["od:a"], title="Order today: Babco", body="3 items"),
            notif.Alert(key="order_due", dedupe_keys=["od:b"], title="Order tomorrow: Raja", body="1 item"),
        ],
        "loss_watch": [],
        "fix_issues_reopened": [notif.Alert(key="fix_issues_reopened", dedupe_keys=["fi:day", "fi:ev:1"],
                                            title="Fix Issues: 1 item back", body="New Papad")],
        "inventory_upload_due": [],
        "inventory_api_ready": [],
        "daily_sales_summary": [],
        "open_orders_morning": [notif.Alert(key="open_orders_morning", dedupe_keys=["oo:open"],
                                            title="Opening: 3 open checks", body="")],
    }
    alerts.update(extra or {})
    for key in trig.MORNING_SOURCES:
        monkeypatch.setitem(trig.TRIGGERS, key, lambda db, now, key=key: list(alerts[key]))
    return alerts


def test_one_morning_push_per_admin_instead_of_six(db, admin, pushes, monkeypatch):
    staff = make_user(db, "lead", features=["inventory_reports", "reorder_candidates"])
    demo = make_user(db, "app_store_review", is_admin=True)
    admin2 = make_user(db, "owner2", is_admin=True)
    device(db, admin, staff, demo, admin2)
    notif.set_pref(db, admin2, "critical_vendors", True)  # wants it as its own push
    _fake_morning(monkeypatch)
    assert trig.run_morning_job(now=NINE.replace(hour=6), db=db) == 0  # quiet hours
    assert trig.run_morning_job(now=NINE, db=db) == 3
    summaries = [p for p in pushes if p["data"]["type"] == "morning_summary"]
    separate = [p for p in pushes if p["data"]["type"] != "morning_summary"]
    assert [p["tokens"] for p in separate] == [["tok-owner2"]] and separate[0]["data"]["type"] == "critical_vendors"
    by_user = {p["tokens"][0]: p for p in summaries}
    assert set(by_user) == {"tok-boss", "tok-owner2"}, "staff off by default, never the demo login"
    boss = by_user["tok-boss"]
    assert boss["title"] == "Morning summary" and boss["data"]["route"] == "home"
    assert boss["body"].split("\n") == [
        "2 vendors critical: Babco, KP",
        "Order today: Babco · tomorrow: Raja",
        "Fix Issues: 1 item back",
        "Opening: 3 open checks",
    ]
    assert "critical" not in by_user["tok-owner2"]["body"], "already its own push"
    # Each summarized alert now counts as sent: not repeated tomorrow / again today.
    logged = {r.dedupe_key for r in db.query(NotificationLog).all()}
    assert {"cv:d", "od:a", "od:b", "fi:day", "fi:ev:1", "oo:open"} <= logged
    assert f"morning_summary:2026-10-15:{admin.id}" in logged
    pushes.clear()
    assert trig.run_morning_job(now=NINE.replace(minute=30), db=db) == 0
    assert pushes == []


def test_staff_who_opt_in_get_only_lines_they_can_open(db, admin, pushes, monkeypatch):
    lead = make_user(db, "lead", features=["inventory_reports"])
    device(db, lead)
    notif.set_pref(db, lead, "morning_summary", True)
    _fake_morning(monkeypatch, {
        "loss_watch": [notif.Alert(key="loss_watch", dedupe_keys=["lw:1"], title="Loss watch: 1 check to review",
                                   body="", user_ids=[admin.id])],
    })
    trig.run_morning_job(now=NINE, db=db)
    (p,) = pushes
    assert p["tokens"] == ["tok-lead"] and p["body"] == "Fix Issues: 1 item back"
    # Nobody saw loss watch: it stays unsent for tomorrow.
    assert not db.query(NotificationLog).filter_by(dedupe_key="lw:1").count()


def test_nothing_to_say_no_summary_and_fix_issues_skippable(db, admin, pushes, monkeypatch):
    device(db, admin)
    alerts = _fake_morning(monkeypatch)
    for k in alerts:
        alerts[k] = []
    assert trig.run_morning_job(now=NINE, db=db) == 0 and pushes == []
    alerts["fix_issues_reopened"] = [notif.Alert(key="fix_issues_reopened", dedupe_keys=["fi:x"], title="F", body="")]
    assert trig.run_morning_job(now=NINE, db=db, include_fix_issues=False) == 0


def test_a_broken_source_doesnt_stop_the_summary(db, admin, pushes, monkeypatch):
    device(db, admin)
    _fake_morning(monkeypatch)

    def boom(db, now):
        raise RuntimeError("toast down")

    monkeypatch.setitem(trig.TRIGGERS, "critical_vendors", boom)
    assert trig.run_morning_job(now=NINE, db=db) == 1
    assert "critical" not in pushes[0]["body"] and "Opening" in pushes[0]["body"]


def test_summary_line_formats():
    sales = notif.Alert(key="daily_sales_summary", dedupe_keys=["s"], title="Tuesday: $5,120 net sales",
                        body="vs last Tue ▲8% · vs 4-week avg ▼3% · 120 orders")
    assert trig.MORNING_LINES["daily_sales_summary"]([sales]) == "Tuesday: $5,120 net sales (vs last Tue ▲8%)"
    upload = notif.Alert(key="inventory_upload_due", dedupe_keys=["u"], title="Inventory upload due",
                         body="Last inventory file: 2026-10-01 (14 days ago). Upload one to refresh costs.")
    assert trig.MORNING_LINES["inventory_upload_due"]([upload]) == (
        "Inventory upload due - Last inventory file: 2026-10-01 (14 days ago)")


def test_scheduler_has_one_9am_job(monkeypatch):
    from app import scheduler
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    sched = scheduler.start_notification_scheduler()
    try:
        ids = {j.id for j in sched.get_jobs()}
    finally:
        scheduler.shutdown_notification_scheduler()
    assert "notify_morning_summary" in ids
    for gone in ("notify_order_due", "notify_critical_vendors", "notify_loss_watch", "notify_daily_sales_summary",
                 "notify_inventory_upload_due", "notify_inventory_api_ready", "notify_fix_issues_reopened",
                 "notify_open_orders_0900"):
        assert gone not in ids
    assert {"notify_open_orders_1600", "notify_open_orders_2130", "notify_closing_checklist"} <= ids

    called = []
    monkeypatch.setattr(scheduler, "run_open_orders_today_refresh", lambda: called.append("refresh"))
    monkeypatch.setattr(trig, "run_morning_job", lambda include_fix_issues: called.append(include_fix_issues))
    monkeypatch.setattr(get_settings(), "scheduler_enabled", False)
    scheduler.run_morning_summary()
    assert called == ["refresh", False]


# ------------------------------------------------------------------- migration


def test_0058_creates_push_deliveries_like_the_model(tmp_path):
    from app.db import Base

    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    backend = pathlib.Path(__file__).resolve().parents[1]
    subprocess.run([str(alembic), "upgrade", "0058"], cwd=backend, env=env, check=True, capture_output=True)
    con = sqlite3.connect(db_file)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(push_deliveries)")}
        assert cols == {c.name for c in Base.metadata.tables["push_deliveries"].columns}
    finally:
        con.close()
    subprocess.run([str(alembic), "downgrade", "0057"], cwd=backend, env=env, check=True, capture_output=True)
    con = sqlite3.connect(db_file)
    try:
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='push_deliveries'").fetchall()
    finally:
        con.close()


def test_deleting_an_account_removes_its_push_log(db, staff, staff_client):
    db.add(PushDelivery(user_id=staff.id, key="chat_message", title="x", sent_at=utcnow()))
    db.add(NotificationPref(user_id=staff.id, key="chat_message", enabled=False))
    db.commit()
    notif.delete_user_notification_data(db, staff.id)
    db.commit()
    assert db.query(PushDelivery).count() == 0 and db.query(NotificationPref).count() == 0
