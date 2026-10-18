"""Adversarial checks of the Oct 15 Notifications batch (docs/BATCH_OCT15_CONTRACT.md
"As built - NOTIFICATIONS"): the effective defaults of all 18 keys builds 5-9
know, per role, for prod-shaped logins; a user's explicit choice beating
store defaults on every path (alerts, morning summary, chat, tasks); the
Morning summary going out once per admin per day (DST, re-runs, alerts
already sent on an earlier day); quiet hours; old-build prefs shape; demo."""

import datetime as dt
import zoneinfo

import pytest

from app.demo_guard import DEMO_READ_ONLY
from app.models import DeviceToken, NotificationLog, NotificationPref
from app.notification_models import PushDelivery
from app.services import notification_triggers as trig
from app.services import notifications as notif
from app.services import push
from tests.conftest import client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")
NINE = dt.datetime(2026, 10, 15, 9, 0, tzinfo=NY)

# Prod staff (Harshitha / Sai Durga, read-only copy of users.allowed_features).
PROD_STAFF_FEATURES = [
    "open_orders", "reorder_candidates", "supplier_projection", "vendor_calendar", "purchase_order_cart",
    "delivery_review", "transfer_review", "inventory_reports", "reconciliation", "invoice_submission",
    "daily_checklists", "ask_bot", "team_tasks", "team_chat", "help",
]
OLD_KEYS = {
    "order_due", "critical_vendors", "closing_checklist", "invoice_failed", "open_orders_morning",
    "task_assigned", "task_due", "task_cant_do", "announcement", "fix_issues_reopened",
    "cart_removal_request", "chat_message", "flyer_sent", "inventory_upload_due", "loss_watch",
    "daily_sales_summary", "inventory_api_ready", "chat_mention",
}
# Owner table: staff ON = closing checklist, own tasks, chat + mentions, announcements.
# invoice_failed (only your own invoice since Oct 15) is the notif agent's documented
# "keep on" decision; cart_removal_request is admin-only.
STAFF_ON = {"closing_checklist", "task_assigned", "task_due", "task_cant_do", "chat_message", "chat_mention",
            "announcement", "invoice_failed"}
ADMIN_ON = STAFF_ON | {"morning_summary", "cart_removal_request"}


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


def _sources(monkeypatch, alerts):
    full = {k: [] for k in trig.MORNING_SOURCES}
    full.update(alerts)
    for key in trig.MORNING_SOURCES:
        monkeypatch.setitem(trig.TRIGGERS, key, lambda db, now, key=key: list(full[key]))
    return full


# ------------------------------------------------------------ effective defaults


def test_all_18_old_keys_effective_defaults_for_prod_shaped_logins(db, admin):
    staff = make_user(db, "harshitha", features=PROD_STAFF_FEATURES)
    for user, expected_on in ((staff, STAFF_ON), (admin, ADMIN_ON)):
        items = {i["key"]: i for i in client_for(db, user).get("/api/notifications/prefs").json()["items"]}
        on = {k for k, i in items.items() if i["enabled"]}
        assert on == expected_on & set(items), user.username
        # every old key the user can open is still listed (old builds keep their switches)
        visible_old = {k for k in OLD_KEYS if notif.user_can_access_alert(user, k)}
        assert visible_old <= set(items)
    # the owner's explicit OFF list
    for key in ("open_orders_morning", "task_due_soon", "fix_issues_reopened", "flyer_sent",
                "fix_issues_task_due", "order_due", "critical_vendors", "loss_watch", "daily_sales_summary",
                "inventory_upload_due", "inventory_api_ready"):
        assert notif.catalog_default(key, "staff") is False and notif.catalog_default(key, "admin") is False, key


def test_old_build_prefs_shape_and_toggles(db, staff_client):
    """Builds 5-9 parse {"items": [{key,label,description,enabled}]} and PUT {key, enabled}."""
    body = staff_client.get("/api/notifications/prefs").json()
    assert isinstance(body["items"], list)
    for i in body["items"]:
        assert isinstance(i["key"], str) and isinstance(i["label"], str)
        assert isinstance(i["description"], str) and isinstance(i["enabled"], bool)
    # an old build can switch on a default-off key it has never heard of
    r = staff_client.put("/api/notifications/prefs", json={"key": "morning_summary", "enabled": True})
    assert r.status_code == 200
    assert {i["key"]: i["enabled"] for i in r.json()["items"]}["morning_summary"] is True
    # unknown key: same 400 as before
    assert staff_client.put("/api/notifications/prefs", json={"key": "nope", "enabled": True}).status_code == 400


# --------------------------------------------------------- explicit choice wins


def test_explicit_choice_beats_store_default_in_the_morning_summary(db, admin, pushes, monkeypatch):
    admin2 = make_user(db, "rk", is_admin=True)
    staff = make_user(db, "harshitha", features=PROD_STAFF_FEATURES)
    device(db, admin, admin2, staff)
    # Store: summary off for admins, on for staff. Choices: admin2 on, staff off.
    notif.set_store_default(db, admin, "morning_summary", "admin", False)
    notif.set_store_default(db, admin, "morning_summary", "staff", True)
    notif.set_pref(db, admin2, "morning_summary", True)
    notif.set_pref(db, staff, "morning_summary", False)
    _sources(monkeypatch, {"critical_vendors": [notif.Alert(key="critical_vendors", dedupe_keys=["cv:x"],
                                                            title="1 vendor critical", body="Babco")]})
    trig.run_morning_job(now=NINE, db=db)
    assert [p["tokens"] for p in pushes] == [["tok-rk"]]


def test_explicit_off_beats_store_on_for_a_task_push(db, admin, staff, pushes):
    from app.team.tasks_models import Task
    from app.timeutil import utcnow

    device(db, staff)
    notif.set_store_default(db, admin, "task_due_soon", "staff", True)
    notif.set_pref(db, staff, "task_due_soon", False)
    now = utcnow()
    task = Task(title="t", assignee_user_id=staff.id, status="open", created_by_user_id=admin.id,
                created_by_name="boss", created_at=now, updated_at=now)
    db.add(task)
    db.commit()
    soon = notif.Alert(key="task_due", dedupe_keys=[f"task_due:{task.id}:soon:1"], title="Due in 30 minutes",
                       body="t", data={"task_id": task.id}, user_ids=[staff.id])
    assert notif.dispatch_alert(db, soon) == 0
    notif.set_pref(db, staff, "task_due_soon", True)
    assert notif.dispatch_alert(db, soon) == 1


# ------------------------------------------------------------- morning summary


def test_morning_summary_once_per_admin_per_day_across_reruns_and_days(db, admin, pushes, monkeypatch):
    device(db, admin)

    def critical(db, now):
        return [notif.Alert(key="critical_vendors", dedupe_keys=[f"critical_vendors:{now.date()}"],
                            title="2 vendors critical", body="Babco, KP")]

    _sources(monkeypatch, {})
    monkeypatch.setitem(trig.TRIGGERS, "critical_vendors", critical)
    for now in (NINE, NINE + dt.timedelta(minutes=40), NINE + dt.timedelta(days=1),
                NINE + dt.timedelta(days=1, hours=1)):
        trig.run_morning_job(now=now, db=db)
    assert len(pushes) == 2, "one per day, re-runs the same day send nothing"
    keys = sorted(r.dedupe_key for r in db.query(NotificationLog).filter_by(key="morning_summary"))
    assert keys == [f"morning_summary:2026-10-15:{admin.id}", f"morning_summary:2026-10-16:{admin.id}"]


@pytest.mark.parametrize("when", [
    dt.datetime(2026, 11, 1, 9, 0, tzinfo=NY),   # DST ends (EDT -> EST)
    dt.datetime(2027, 3, 14, 9, 0, tzinfo=NY),   # DST starts
])
def test_morning_summary_dedupe_uses_the_store_day_on_dst_days(db, admin, pushes, monkeypatch, when):
    device(db, admin)
    _sources(monkeypatch, {"open_orders_morning": [notif.Alert(key="open_orders_morning", dedupe_keys=["oo"],
                                                                title="Opening: 1 open check", body="")]})
    assert trig.run_morning_job(now=when, db=db) == 1
    assert db.query(NotificationLog).filter_by(
        dedupe_key=f"morning_summary:{when.date().isoformat()}:{admin.id}").count() == 1


def test_scheduler_9am_fires_at_9_local_across_dst(monkeypatch):
    from app import scheduler
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    sched = scheduler.start_notification_scheduler()
    try:
        trigger = sched.get_job("notify_morning_summary").trigger
        fire = trigger.get_next_fire_time(None, dt.datetime(2026, 10, 31, 10, 0, tzinfo=NY))
        fire2 = trigger.get_next_fire_time(None, fire + dt.timedelta(minutes=1))
        fire3 = trigger.get_next_fire_time(None, dt.datetime(2027, 3, 13, 10, 0, tzinfo=NY))
        fire4 = trigger.get_next_fire_time(None, fire3 + dt.timedelta(minutes=1))
    finally:
        scheduler.shutdown_notification_scheduler()
    for f in (fire, fire2, fire3, fire4):
        assert (f.astimezone(NY).hour, f.astimezone(NY).minute) == (9, 0)
    assert fire.astimezone(NY).date() == dt.date(2026, 11, 1) and fire2.astimezone(NY).date() == dt.date(2026, 11, 2)
    assert fire4.astimezone(NY).date() == dt.date(2027, 3, 15)


def test_alert_already_sent_on_an_earlier_day_is_not_repeated_in_the_summary(db, admin, pushes, monkeypatch):
    """inventory_api_ready keeps returning the same once-per-streak alert every
    morning (dispatch_alert's dedupe used to stop the repeat); the summary must
    not put it back in every day."""
    device(db, admin)
    ready = notif.Alert(key="inventory_api_ready", dedupe_keys=["inventory_api_ready:2026-10-01"],
                        title="Inventory from Toast", body="ready")
    _sources(monkeypatch, {"inventory_api_ready": [ready]})
    trig.run_morning_job(now=NINE, db=db)
    assert len(pushes) == 1 and "Inventory from Toast is ready" in pushes[0]["body"]
    pushes.clear()
    trig.run_morning_job(now=NINE + dt.timedelta(days=1), db=db)
    assert pushes == [], "the streak alert was already in yesterday's summary"


def test_alert_already_sent_separately_yesterday_not_in_todays_summary(db, admin, pushes, monkeypatch):
    """Someone who has inventory_api_ready on got it yesterday; today another admin's
    summary must not announce it as new."""
    admin2 = make_user(db, "rk", is_admin=True)
    device(db, admin, admin2)
    db.add(NotificationLog(key="inventory_api_ready", dedupe_key="inventory_api_ready:2026-10-01",
                           sent_at=dt.datetime(2026, 10, 14, 13, 0, tzinfo=dt.timezone.utc), recipient_count=1))
    db.commit()
    ready = notif.Alert(key="inventory_api_ready", dedupe_keys=["inventory_api_ready:2026-10-01"],
                        title="Inventory from Toast", body="ready")
    _sources(monkeypatch, {"inventory_api_ready": [ready]})
    trig.run_morning_job(now=NINE, db=db)
    assert pushes == []


def test_quiet_hours_and_demo_never_get_the_summary(db, admin, pushes, monkeypatch):
    demo = make_user(db, "app_store_review", features=PROD_STAFF_FEATURES)
    device(db, admin, demo)
    notif.set_store_default(db, admin, "morning_summary", "staff", True)
    notif.set_pref(db, demo, "morning_summary", True)
    _sources(monkeypatch, {"critical_vendors": [notif.Alert(key="critical_vendors", dedupe_keys=["cv"],
                                                            title="1 vendor critical", body="Babco")]})
    assert trig.run_morning_job(now=NINE.replace(hour=22), db=db) == 0
    assert trig.run_morning_job(now=NINE.replace(hour=6, minute=59), db=db) == 0
    assert pushes == []
    trig.run_morning_job(now=NINE, db=db)
    assert [p["tokens"] for p in pushes] == [["tok-boss"]]


def test_hidden_sales_staff_who_opt_in_never_see_sales(db, admin, pushes, monkeypatch):
    seller = make_user(db, "seller", features=PROD_STAFF_FEATURES + ["sales_insights"])
    seller.hide_sales = True
    db.commit()
    device(db, seller)
    notif.set_pref(db, seller, "morning_summary", True)
    sales = notif.Alert(key="daily_sales_summary", dedupe_keys=["s"], title="Tuesday: $5,120 net sales",
                        body="120 orders", user_ids=[admin.id])
    _sources(monkeypatch, {"daily_sales_summary": [sales],
                           "open_orders_morning": [notif.Alert(key="open_orders_morning", dedupe_keys=["oo"],
                                                               title="Opening: 1 open check", body="")]})
    trig.run_morning_job(now=NINE, db=db)
    assert len(pushes) == 1 and "$" not in pushes[0]["body"]


# --------------------------------------------------------------- recipients / log


def test_recent_lists_only_your_own_pushes(db, admin, staff, staff_client, pushes):
    device(db, admin, staff)
    notif.dispatch_alert(db, notif.Alert(key="task_assigned", dedupe_keys=["ta:1"], title="New task", body="x",
                                         user_ids=[admin.id]))
    assert staff_client.get("/api/notifications/recent").json()["items"] == []
    assert db.query(PushDelivery).filter_by(user_id=admin.id).count() == 1


def test_push_log_never_stores_a_chat_body(db, staff, pushes):
    from app.team import chat_service as chat

    device(db, staff)
    chat.push_to_users(db, "chat_message", [staff.id], "boss in Kitchen", "secret text", {"route": "chat"})
    row = db.query(PushDelivery).one()
    assert "secret" not in (row.title or "")


def test_a_missing_push_log_table_never_breaks_a_send(db, staff, pushes):
    device(db, staff)
    db.execute(notif.PushDelivery.__table__.delete())
    db.commit()
    from sqlalchemy import text

    db.execute(text("DROP TABLE push_deliveries"))
    db.commit()
    alert = notif.Alert(key="task_assigned", dedupe_keys=["ta:9"], title="New task", body="x", user_ids=[staff.id])
    assert notif.dispatch_alert(db, alert) == 1
    assert db.query(NotificationLog).filter_by(dedupe_key="ta:9").count() == 1


# ------------------------------------------------------------------------- demo


def test_demo_cannot_reset_or_touch_store_defaults_even_with_explicit_rows(db):
    demo = make_user(db, "app_store_review", features=PROD_STAFF_FEATURES)
    c = client_for(db, demo)
    assert c.put("/api/notifications/prefs", json={"key": "chat_message", "enabled": False}).status_code == 200
    assert c.post("/api/notifications/prefs/reset").json() == {"detail": DEMO_READ_ONLY}
    assert db.query(NotificationPref).filter_by(user_id=demo.id, key="chat_message").one().enabled is False
    for path in ("/api/notifications/defaults", "/api/notifications/volume"):
        assert c.get(path).status_code == 403
    assert c.get("/api/notifications/recent").status_code == 200


# ------------------------------------------------------------- quiet hours queue


def test_queued_announcement_to_people_who_switched_it_off_is_not_retried_forever(
    db, admin, admin_client, staff, pushes, monkeypatch
):
    """A night post is queued for 7 AM. When everyone it targets has
    Announcements off (their own choice, or an admin's store default), the
    morning flush reaches nobody - that's "nobody to tell", not a Firebase
    failure, so it must not stay queued and retried every 5 minutes."""
    from app.team import announcements_service as ann
    from app.team.chat_models import Announcement
    from app.team.models import Team
    from tests.conftest import add_member

    team = db.query(Team).filter(Team.name == "Kitchen").one()
    add_member(db, team, staff)
    device(db, staff)
    notif.set_store_default(db, admin, "announcement", "staff", False)
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: True)
    a = admin_client.post("/api/team/announcements", json={"title": "Night post", "team_ids": [team.id]}).json()
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: False)
    ann.flush_pending_pushes(db)
    db.expire_all()
    assert pushes == []
    assert db.get(Announcement, a["id"]).pending_push is None
