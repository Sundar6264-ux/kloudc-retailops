"""Fix Issues "more time" snooze + came-back flag, and cart removal by admins
only (docs/FIXISSUES_CART_CONTRACT.md, owner Sep 30):

- "New item - giving it more time" closes for 7 days; then, if the item is
  still on the list, it moves back to Open (daily job + lazily on read) with
  one combined push a day. Gone items stay closed.
- Open rows carry times_closed / reopened, the list and summary a
  came_back_count, ?came_back=1 filters; every close / reopen is kept.
- Only admins delete / clear / zero cart lines; others request removal, admins
  approve or reject, and admins get one combined push.
"""

import datetime as dt
import os
import pathlib
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest

from app.models import CartItem, DeviceToken, IssueResolution, IssueResolutionEvent, NotificationLog
from app.services import fix_issues, notifications, push
from app.services.fix_issues import SNOOZE_DAYS, get_issue_list, reason_outcome, reasons_for
from app.services.notification_triggers import TRIGGERS, run_alert_job
from app.timeutil import restaurant_today
from tests.conftest import client_for, make_user
from tests.test_ops_fix_issues import _fresh_forecast_cache, ago, sale, snap, store  # noqa: F401

NY = zoneinfo.ZoneInfo("America/New_York")
BACKEND = pathlib.Path(__file__).resolve().parents[1]
ADMIN_ONLY = "Only an admin can remove items from the cart. Use Request removal."
TYPE = "dead_new_arrival"


def today():
    return restaurant_today()


def at(day, hour=9, minute=0):
    return dt.datetime.combine(day, dt.time(hour, minute), NY)


def close(client, keys, reason="give_time", issue_type=TYPE, detail=None):
    body = {"issue_type": issue_type, "issue_keys": keys, "reason": reason}
    if detail:
        body["reason_detail"] = detail
    res = client.post("/api/issues/close", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def backdate(db, key, days, issue_type=TYPE):
    """Pretend the close happened `days` ago (snooze day moves with it)."""
    row = db.query(IssueResolution).filter_by(issue_type=issue_type, issue_key=key).one()
    closed_day = today() - dt.timedelta(days=days)
    row.closed_at = at(closed_day, 15).astimezone(dt.timezone.utc)
    if row.snooze_until is not None:
        row.snooze_until = closed_day + dt.timedelta(days=SNOOZE_DAYS[row.reason])
    db.commit()
    db.expire_all()


def rows_by_key(data, part="open"):
    return {r["issue_key"]: r for r in data[part]}


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


# ------------------------------------------------------------ snooze rules


def test_snooze_mapping_and_reason_outcomes():
    assert SNOOZE_DAYS == {"give_time": 7}
    assert reason_outcome(TYPE, "give_time") == "snooze"
    assert reason_outcome(TYPE, "fixed") == "rechecked"
    assert reason_outcome(TYPE, "markdown") == "stays_closed"
    assert reason_outcome("negative_inventory", "sold_before_received") == "one_occurrence"
    assert reason_outcome("negative_inventory", "loose_weighed") == "stays_closed"
    assert reason_outcome("negative_inventory", "not_an_issue") == "stays_closed"


def test_reopens_on_day_7_not_before(store, admin_client):
    close(admin_client, ["new-rcv"])
    res = store.query(IssueResolution).filter_by(issue_key="new-rcv").one()
    assert res.snooze_until == today() + dt.timedelta(days=7)

    for day in range(0, 7):
        data = get_issue_list(store, TYPE, today=today() + dt.timedelta(days=day))
        assert "new-rcv" not in rows_by_key(data), f"back too early on day {day}"
    closed = rows_by_key(get_issue_list(store, TYPE, today=today() + dt.timedelta(days=6)), "closed")
    assert closed["new-rcv"]["snooze_until"] == (today() + dt.timedelta(days=7)).isoformat()

    data = get_issue_list(store, TYPE, today=today() + dt.timedelta(days=7))
    row = rows_by_key(data)["new-rcv"]
    assert row["reopened"]["how"] == "snooze_ended"
    assert row["reopened"]["reason"] == "give_time"
    assert row["reopened"]["reason_label"] == "New item - giving it more time"
    assert row["reopened"]["closed_by"] == "boss"
    assert row["times_closed"] == 1
    assert data["came_back_count"] == 1
    # The resolution is gone (it's open), the history has both steps.
    assert store.query(IssueResolution).filter_by(issue_key="new-rcv").count() == 0
    events = [e.event for e in store.query(IssueResolutionEvent).filter_by(issue_key="new-rcv").order_by("id")]
    assert events == ["closed", "reopened"]


def test_item_gone_stays_closed(store, admin_client):
    close(admin_client, ["new-rcv"])
    sale(store, "new-rcv", ago(1))  # it sold - no longer a dead new arrival
    store.commit()
    data = get_issue_list(store, TYPE, today=today() + dt.timedelta(days=8))
    assert "new-rcv" not in rows_by_key(data)
    assert store.query(IssueResolution).filter_by(issue_key="new-rcv").count() == 1
    assert store.query(IssueResolutionEvent).filter_by(issue_key="new-rcv", event="reopened").count() == 0
    # The daily job agrees.
    assert fix_issues.reopen_all_ended_snoozes(store, today() + dt.timedelta(days=8)) == []


def test_lazy_reopen_on_read_and_came_back_filter(store, admin_client):
    close(admin_client, ["new-rcv", "new-seen"])
    backdate(store, "new-rcv", 7)  # its week is up; new-seen's isn't
    data = admin_client.get(f"/api/issues/{TYPE}").json()
    open_rows = rows_by_key(data)
    assert open_rows["new-rcv"]["reopened"]["how"] == "snooze_ended"
    assert "new-seen" not in open_rows
    assert data["came_back_count"] == 1
    assert all(r["reopened"] is None for k, r in open_rows.items() if k != "new-rcv")
    closed = rows_by_key(data, "closed")
    assert closed["new-seen"]["snooze_until"] == (today() + dt.timedelta(days=7)).isoformat()

    only = admin_client.get(f"/api/issues/{TYPE}?came_back=1").json()
    assert [r["issue_key"] for r in only["open"]] == ["new-rcv"]
    assert only["open_count"] == 1 and only["came_back_count"] == 1
    assert only["closed_count"] == data["closed_count"]

    summary = {i["issue_type"]: i for i in admin_client.get("/api/issues/summary").json()["items"]}
    assert summary[TYPE]["came_back_count"] == 1
    assert summary["missing_barcode"]["came_back_count"] == 0


def test_rows_closed_before_0040_get_one_week(store, admin_client):
    """No snooze_until stored (closed before migration 0040): closed day + 7."""
    store.add(
        IssueResolution(
            issue_type=TYPE, issue_key="new-rcv", item_id="new-rcv", item_name="New Biscuits",
            reason="give_time", closed_by_name="old", closed_at=at(today() - dt.timedelta(days=6), 15),
        )
    )
    store.commit()
    assert "new-rcv" not in rows_by_key(get_issue_list(store, TYPE))
    data = get_issue_list(store, TYPE, today=today() + dt.timedelta(days=1))
    assert rows_by_key(data)["new-rcv"]["reopened"]["how"] == "snooze_ended"


def test_fixed_but_still_showing_and_detected_again(store, admin_client):
    close(admin_client, ["toor"], reason="fixed", issue_type="refill_from_container")
    backdate(store, "toor", 2, "refill_from_container")
    row = rows_by_key(get_issue_list(store, "refill_from_container"))["toor"]
    # Existing came_back object keeps its exact shape (build 5 reads it).
    assert set(row["came_back"]) == {"reason", "reason_label", "reason_detail", "closed_by", "closed_at", "closed_date"}
    assert row["reopened"]["how"] == "fixed_but_still_showing"
    assert row["reopened"]["at"]

    close(admin_client, ["neg"], reason="sold_before_received", issue_type="negative_inventory")
    backdate(store, "neg", 5, "negative_inventory")
    snap(store, "neg", "Negative Dal", 4, day=ago(3))
    store.commit()
    data = get_issue_list(store, "negative_inventory")
    neg = rows_by_key(data)["neg"]
    assert neg["reopened"]["how"] == "detected_again"
    assert "came_back" not in neg
    assert data["came_back_count"] == 1


def test_manual_reopen_is_history_not_came_back(store, admin_client):
    close(admin_client, ["new-rcv"], reason="markdown")
    admin_client.post("/api/issues/reopen", json={"issue_type": TYPE, "issue_keys": ["new-rcv"]})
    row = rows_by_key(get_issue_list(store, TYPE))["new-rcv"]
    assert row["reopened"] is None and row["times_closed"] == 1
    hist = admin_client.get(f"/api/issues/{TYPE}/history", params={"key": "new-rcv"}).json()
    assert [(e["event"], e["how"]) for e in hist["events"]] == [("reopened", "manual"), ("closed", None)]
    assert hist["events"][0]["by"] == "boss"


def test_history_keeps_every_close_and_reopen(store, admin_client):
    close(admin_client, ["new-rcv"])
    backdate(store, "new-rcv", 7)
    get_issue_list(store, TYPE)  # lazy reopen
    close(admin_client, ["new-rcv"], reason="markdown")  # closing a came-back item works as before
    assert "new-rcv" not in rows_by_key(get_issue_list(store, TYPE))
    hist = admin_client.get(f"/api/issues/{TYPE}/history?key=new-rcv").json()
    assert hist["times_closed"] == 2
    assert [e["event"] for e in hist["events"]] == ["closed", "reopened", "closed"]
    assert hist["events"][1]["how"] == "snooze_ended"
    assert hist["events"][2]["snooze_until"] is not None  # the first (give_time) close
    admin_client.post("/api/issues/reopen", json={"issue_type": TYPE, "issue_keys": ["new-rcv"]})
    assert store.query(IssueResolutionEvent).filter_by(issue_key="new-rcv").count() == 4
    row = rows_by_key(get_issue_list(store, TYPE))["new-rcv"]
    assert row["times_closed"] == 2


def test_history_is_gated_and_checks_type(store, db, admin_client):
    assert admin_client.get("/api/issues/nope/history?key=x").status_code == 404
    nobody = client_for(db, make_user(db, "no_reports", features=["purchase_order_cart"]))
    assert nobody.get(f"/api/issues/{TYPE}/history?key=x").status_code == 403


# ---------------------------------------------------------- reopened push


@pytest.fixture
def people(db, admin):
    reports = make_user(db, "lead", features=["inventory_reports"])
    other = make_user(db, "cashier", features=["purchase_order_cart"])
    for u in (admin, reports, other):
        device(db, u)
    # The separate push is off by default since Oct 15 (a Morning summary line).
    for u in (admin, reports):
        notifications.set_pref(db, u, "fix_issues_reopened", True)
    return admin, reports, other


def test_one_combined_push_a_day(store, admin_client, people, pushes):
    close(admin_client, ["new-rcv", "new-seen"])
    backdate(store, "new-rcv", 7)
    backdate(store, "new-seen", 8)
    now = at(today(), 9)
    assert run_alert_job("fix_issues_reopened", now=now, db=store) == 2  # admin + lead
    assert len(pushes) == 1
    p = pushes[0]
    assert p["title"] == "Fix Issues: 2 items back"
    assert set(p["body"].split(", ")) == {"New Biscuits", "New Papad"}
    assert p["tokens"] == ["tok-boss", "tok-lead"]
    assert p["data"]["route"] == "fix_issues" and p["data"]["type"] == "fix_issues_reopened"
    # Same day again: nothing.
    assert run_alert_job("fix_issues_reopened", now=at(today(), 11), db=store) == 0
    # Next day: those two were already in a push.
    assert run_alert_job("fix_issues_reopened", now=at(today() + dt.timedelta(days=1), 9), db=store) == 0
    assert len(pushes) == 1


def test_push_lists_three_names_then_more(store, admin_client, people, pushes):
    for item_id, name in [("x1", "Item One"), ("x2", "Item Two"), ("x3", "Item Three"), ("x4", "Item Four"), ("x5", "Item Five")]:
        for day in (ago(25), ago(0)):
            snap(store, item_id, name, 5, day=day, inventory_last_received=f"{ago(10).isoformat()}T15:00:00.000000Z")
    store.commit()
    close(admin_client, ["x1", "x2", "x3", "x4", "x5"])
    for k in ("x1", "x2", "x3", "x4", "x5"):
        backdate(store, k, 7)
    run_alert_job("fix_issues_reopened", now=at(today(), 9), db=store)
    assert pushes[0]["title"] == "Fix Issues: 5 items back"
    assert pushes[0]["body"].endswith(" + 2 more")
    assert pushes[0]["body"].count(",") == 2


def test_reopened_lazily_before_9_still_in_the_push(store, admin_client, people, pushes):
    close(admin_client, ["new-rcv"])
    backdate(store, "new-rcv", 7)
    admin_client.get(f"/api/issues/{TYPE}")  # someone opened Fix Issues first
    assert run_alert_job("fix_issues_reopened", now=at(today(), 9), db=store) == 2
    assert pushes[0]["title"] == "Fix Issues: 1 item back"
    assert pushes[0]["body"] == "New Biscuits"


def test_nothing_back_nothing_sent_and_quiet_hours(store, admin_client, people, pushes):
    assert run_alert_job("fix_issues_reopened", now=at(today(), 9), db=store) == 0
    close(admin_client, ["new-rcv"])
    backdate(store, "new-rcv", 7)
    assert run_alert_job("fix_issues_reopened", now=at(today(), 23), db=store) == 0
    assert run_alert_job("fix_issues_reopened", now=at(today(), 6, 30), db=store) == 0
    assert pushes == []
    # Re-closed before the push went out: nothing to say.
    get_issue_list(store, TYPE)
    close(admin_client, ["new-rcv"], reason="markdown")
    assert run_alert_job("fix_issues_reopened", now=at(today(), 9), db=store) == 0
    assert pushes == []


def test_alerts_registered_with_the_right_recipients(db, admin, people):
    keys = [a["key"] for a in notifications.ALERTS]
    assert "fix_issues_reopened" in keys and "cart_removal_request" in keys
    assert {"fix_issues_reopened", "cart_removal_request"} <= set(TRIGGERS)
    _, lead, cashier = people
    assert notifications.user_can_access_alert(lead, "fix_issues_reopened")
    assert not notifications.user_can_access_alert(cashier, "fix_issues_reopened")
    assert notifications.user_can_access_alert(admin, "cart_removal_request")
    assert not notifications.user_can_access_alert(cashier, "cart_removal_request")
    prefs = client_for(db, lead).get("/api/notifications/prefs").json()
    pref_keys = [p["key"] for p in prefs["items"]]
    assert "fix_issues_reopened" in pref_keys and "cart_removal_request" not in pref_keys


def test_scheduler_jobs(monkeypatch):
    from app import scheduler
    from app.config import get_settings

    called = []
    monkeypatch.setattr(scheduler, "run_alert_job", lambda key: called.append(key))
    monkeypatch.setattr(get_settings(), "scheduler_enabled", False)
    scheduler.run_fix_issues_reopened()
    assert called == [], "SCHEDULER_ENABLED=false skips the job (lists still reopen on read)"
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    scheduler.run_fix_issues_reopened()
    assert called == ["fix_issues_reopened"]

    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    sched = scheduler.start_notification_scheduler()
    try:
        jobs = {j.id: j for j in sched.get_jobs()}
        # Since Oct 15 the 9:00 Fix Issues push runs inside the morning job.
        job = jobs["notify_morning_summary"]
        assert str(job.trigger.fields[5]) == "9" and str(job.trigger.fields[6]) == "0"  # hour, minute
        assert "notify_fix_issues_reopened" not in jobs
        assert "notify_cart_removal_request" in jobs
    finally:
        scheduler.shutdown_notification_scheduler()
    monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    assert scheduler.start_notification_scheduler() is None


# ------------------------------------------------------------------- cart


@pytest.fixture
def buyer(db):
    return make_user(db, "buyer", features=["purchase_order_cart", "supplier_projection"])


@pytest.fixture
def buyer_client(db, buyer):
    return client_for(db, buyer)


@pytest.fixture
def cart(admin_client):
    items = [
        {"item_id": "a1", "name": "Basmati 10lb", "qty": 2},
        {"item_id": "a2", "name": "Toor Dal 4lb", "qty": 3},
        {"name": "Hand added thing", "qty": 1},
    ]
    rows = admin_client.post("/api/ordering/cart/items", json={"supplier": "Raja Foods", "items": items}).json()
    rows += admin_client.post(
        "/api/ordering/cart/items", json={"supplier": "KP Produce", "items": [{"name": "Okra", "qty": 1}]}
    ).json()
    return {r["name"]: r["id"] for r in rows}


def test_non_admin_cannot_remove_any_way(cart, buyer_client):
    line = cart["Basmati 10lb"]
    for res in (
        buyer_client.delete(f"/api/ordering/cart/items/{line}"),
        buyer_client.delete("/api/ordering/cart", params={"supplier": "Raja Foods"}),
        buyer_client.patch(f"/api/ordering/cart/items/{line}", json={"qty": 0}),
        buyer_client.patch(f"/api/ordering/cart/items/{line}", json={"qty": -1}),
        buyer_client.post(
            "/api/ordering/cart/items",
            json={"supplier": "Raja Foods", "items": [{"item_id": "a1", "name": "Basmati 10lb", "qty": 0}]},
        ),
    ):
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == ADMIN_ONLY
    body = buyer_client.get("/api/ordering/cart").json()
    assert body["total_items"] == 4 and body["can_remove"] is False
    # Adding and changing qty (>= 1) stay open.
    assert buyer_client.patch(f"/api/ordering/cart/items/{line}", json={"qty": 5}).json()["qty"] == 5
    added = buyer_client.post(
        "/api/ordering/cart/items", json={"supplier": "Raja Foods", "items": [{"item_id": "a9", "name": "Ghee", "qty": 1}]}
    )
    assert added.status_code == 200
    # Mark placed (clears that vendor's cart) stays allowed.
    placed = buyer_client.post("/api/ordering/cart/mark-placed", params={"supplier": "KP Produce"})
    assert placed.status_code == 200 and placed.json()["placed"] is True


def test_admin_still_removes(cart, admin_client):
    assert admin_client.get("/api/ordering/cart").json()["can_remove"] is True
    assert admin_client.delete(f"/api/ordering/cart/items/{cart['Okra']}").json() == {"deleted": True}
    assert admin_client.patch(f"/api/ordering/cart/items/{cart['Basmati 10lb']}", json={"qty": 0}).json()["qty"] == 0
    assert admin_client.delete("/api/ordering/cart", params={"supplier": "Raja Foods"}).json() == {"deleted": 3}


def test_request_cancel_approve_reject(db, cart, buyer, buyer_client, admin_client):
    line = cart["Basmati 10lb"]
    url = f"/api/ordering/cart/items/{line}/removal-request"
    res = buyer_client.post(url, json={"reason": "Vendor is out of stock"})
    assert res.status_code == 200, res.text
    req = res.json()["removal_requested"]
    assert req["by"] == "buyer" and req["by_user_id"] == buyer.id and req["reason"] == "Vendor is out of stock"
    assert req["at"]
    items = {i["id"]: i for s in admin_client.get("/api/ordering/cart").json()["suppliers"] for i in s["items"]}
    assert items[line]["removal_requested"]["reason"] == "Vendor is out of stock"
    assert items[cart["Toor Dal 4lb"]]["removal_requested"] is None

    # Another non-admin can't cancel it; the requester can.
    other = client_for(db, make_user(db, "other_buyer", features=["purchase_order_cart"]))
    assert other.delete(url).status_code == 403
    assert other.post(url, json={}).json()["removal_requested"]["by"] == "buyer"  # first request kept
    assert buyer_client.delete(url).json()["removal_requested"] is None

    # Non-admins can't approve / reject; nothing to approve -> 409.
    assert admin_client.post(url + "/approve").status_code == 409
    buyer_client.post(url)  # no body at all is fine
    assert buyer_client.post(url + "/approve").status_code == 403
    assert buyer_client.post(url + "/reject").status_code == 403
    rejected = admin_client.post(url + "/reject")
    assert rejected.status_code == 200 and rejected.json()["removal_requested"] is None
    assert admin_client.post(url + "/reject").status_code == 409

    buyer_client.post(url, json={"reason": "dup"})
    assert admin_client.delete(url).json()["removal_requested"] is None  # admin can cancel too
    buyer_client.post(url, json={"reason": "dup"})
    assert admin_client.post(url + "/approve").json() == {"deleted": True}
    assert db.get(CartItem, line) is None
    assert buyer_client.post(url).status_code == 404


def test_removal_push_to_admins_combined(db, admin, cart, buyer, buyer_client, pushes):
    second_admin = make_user(db, "boss2", is_admin=True)
    for u in (admin, second_admin, buyer):
        device(db, u)
    buyer_client.post(f"/api/ordering/cart/items/{cart['Basmati 10lb']}/removal-request", json={"reason": "expired"})
    buyer_client.post(f"/api/ordering/cart/items/{cart['Okra']}/removal-request")
    noon = at(today(), 12)
    assert run_alert_job("cart_removal_request", now=noon, db=db) == 2
    assert len(pushes) == 1
    assert pushes[0]["title"] == "Cart: 2 removal requests"
    assert pushes[0]["body"] == "Basmati 10lb (Raja Foods), Okra (KP Produce)"
    assert pushes[0]["tokens"] == ["tok-boss", "tok-boss2"]  # admins only
    assert pushes[0]["data"]["route"] == "cart"
    assert run_alert_job("cart_removal_request", now=noon, db=db) == 0  # already sent

    buyer_client.post(f"/api/ordering/cart/items/{cart['Toor Dal 4lb']}/removal-request", json={"reason": "too much"})
    assert run_alert_job("cart_removal_request", now=at(today(), 23), db=db) == 0  # quiet hours
    assert run_alert_job("cart_removal_request", now=noon, db=db) == 2
    assert pushes[1]["title"] == "Cart: removal request"
    assert pushes[1]["body"] == "buyer: Toor Dal 4lb (Raja Foods) - too much"
    assert pushes[1]["data"]["vendor"] == "Raja Foods"
    assert db.query(NotificationLog).filter_by(key="cart_removal_request").count() == 3


# --------------------------------------------------- build 5 compatibility


def test_build5_fields_unchanged(store, admin_client, cart):
    # Issue rows: every field build 5 reads is still there.
    row = admin_client.get(f"/api/issues/{TYPE}").json()["open"][0]
    for k in ("issue_key", "item_id", "name", "product", "arrived_date", "days_since_arrival", "on_hand_qty"):
        assert k in row, k
    data = admin_client.get(f"/api/issues/{TYPE}").json()
    for k in ("issue_type", "label", "inventory_snapshot_date", "open_count", "closed_count", "open", "closed"):
        assert k in data
    reasons = admin_client.get("/api/issues/reasons").json()
    for t, cfg in reasons.items():
        assert [(r["key"], r["label"]) for r in cfg["reasons"]] == reasons_for(t)
        assert all(r["glossary_key"] and r["does"] for r in cfg["reasons"]), t
    assert admin_client.post("/api/issues/close", json={"issue_type": TYPE, "issue_keys": ["new-rcv"], "reason": "give_time"}).json() == {"closed": 1}
    assert admin_client.post("/api/issues/reopen", json={"issue_type": TYPE, "issue_keys": ["new-rcv"]}).json() == {"reopened": 1}
    # Cart: old item fields and top-level fields are all still sent.
    body = admin_client.get("/api/ordering/cart").json()
    assert {"suppliers", "total_items", "grand_estimate_total"} <= set(body)
    item = body["suppliers"][0]["items"][0]
    for k in ("id", "supplier", "item_id", "name", "supplier_item_id", "qty", "case_of", "added_at", "unit_cost",
              "total_units", "line_cost", "on_hand_qty", "container_qty", "section", "section_source"):
        assert k in item, k


# --------------------------------------------------------------- glossary


def test_every_close_reason_has_a_definition_that_says_what_happens():
    from app import glossary as g

    want = {"stays_closed": g._STAYS, "rechecked": g._RECHECKED, "one_occurrence": g._ONE_TIME}
    for issue_type in fix_issues.ISSUE_TYPES:
        for reason, _label in reasons_for(issue_type):
            term = g.reason_term(issue_type, reason)
            assert term is not None, (issue_type, reason)
            text = f"{term['definition']} {term['rule']}"
            outcome = reason_outcome(issue_type, reason)
            if outcome == "snooze":
                assert f"{SNOOZE_DAYS[reason]} days" in text and "comes back" in text
            else:
                assert want[outcome] in text, (issue_type, reason, outcome)
    for key in ("came_back_snooze_ended", "came_back_fixed_but_still_showing", "came_back_detected_again",
                "issue_snoozed", "issue_times_closed", "cart_removal_requested", "cart_removal_admin_only"):
        assert key in g.TERMS_BY_KEY, key
    from app.services.notification_triggers import CART_REMOVAL_CHECK_MINUTES

    assert f"{CART_REMOVAL_CHECK_MINUTES} minutes" in g.TERMS_BY_KEY["cart_removal_requested"]["rule"]


# -------------------------------------------------------------- migration


def _copy(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Read-only backup of a live database (never written to)."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()


def _legacy_copy(var: str) -> pathlib.Path:
    """A `.backup` copy of an existing store's database, named explicitly by
    an environment variable (made with sqlite3 "file:...?mode=ro" ".backup").
    No default path: the suite never reaches into another store's folders."""
    raw = os.environ.get(var, "").strip()
    return pathlib.Path(raw) if raw else pathlib.Path(f"/nonexistent/{var}-not-set")


DB_COPIES = {
    "dev": _legacy_copy("MIGRATION_DEV_DB_COPY"),
    "prod": _legacy_copy("MIGRATION_PROD_DB_COPY"),
}


@pytest.mark.parametrize("which", ["fresh", "dev", "prod"])
def test_migration_0040_up_and_down(tmp_path, which):
    db_file = tmp_path / f"{which}.db"
    if which != "fresh":
        src = DB_COPIES[which]
        if not src.exists():
            pytest.skip(f"{src} not here")
        _copy(src, db_file)
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *a: subprocess.run([str(alembic), *a], cwd=BACKEND, env=env, check=True, capture_output=True)  # noqa: E731

    def cols(con, table):
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}

    def version(con):
        has = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'alembic_version'").fetchone()
        row = con.execute("SELECT version_num FROM alembic_version").fetchone() if has else None
        return int(row[0]) if row else None

    con = sqlite3.connect(db_file)
    start = version(con)
    con.close()
    # A copy already past 0040 (dev and prod are) doesn't run it again here:
    # "upgrade 0039" leaves it where it is.
    runs_0040 = start is None or start < 40

    run("upgrade", "0039")
    con = sqlite3.connect(db_file)
    before = con.execute("SELECT COUNT(*) FROM issue_resolutions").fetchone()[0]
    cart_before = con.execute("SELECT COUNT(*) FROM po_cart_items").fetchone()[0]
    con.close()

    run("upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        from app.db import Base

        for table in ("issue_resolution_events", "issue_resolutions", "po_cart_items"):
            assert cols(con, table) == {c.name for c in Base.metadata.tables[table].columns}, table
        events = con.execute("SELECT COUNT(*) FROM issue_resolution_events WHERE event = 'closed'").fetchone()[0]
        if runs_0040:
            assert events == before, "every existing close is seeded into the history"
            assert con.execute("SELECT COUNT(*) FROM po_cart_items WHERE removal_requested_at IS NOT NULL").fetchone()[0] == 0
        else:
            # Migrated long ago: a reopen deletes the close but never its history
            # event (on Oct 4 prod had 129 closes and 132 'closed' events).
            assert events >= before, "every close has its history event"
        assert con.execute("SELECT COUNT(*) FROM po_cart_items").fetchone()[0] == cart_before
    finally:
        con.close()

    run("downgrade", "0039")
    con = sqlite3.connect(db_file)
    try:
        assert "issue_resolution_events" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        assert "snooze_until" not in cols(con, "issue_resolutions")
        assert "removal_requested_at" not in cols(con, "po_cart_items")
        assert con.execute("SELECT COUNT(*) FROM issue_resolutions").fetchone()[0] == before
        assert con.execute("SELECT COUNT(*) FROM po_cart_items").fetchone()[0] == cart_before
    finally:
        con.close()
    run("upgrade", "head")  # and back up again cleanly


# ------------------------------------------------ DST + concurrent reopen


@pytest.mark.parametrize(
    "closed_local, back_on",
    [
        # Late evening the day before DST ends (EDT -> EST, Nov 1 2026): already
        # the next day in UTC, but the store day is what counts.
        (dt.datetime(2026, 10, 31, 23, 30, tzinfo=NY), dt.date(2026, 11, 7)),
        # Just after midnight on the fall-back day.
        (dt.datetime(2026, 11, 1, 0, 30, tzinfo=NY), dt.date(2026, 11, 8)),
        # Late evening the day before DST starts (EST -> EDT, Mar 14 2027).
        (dt.datetime(2027, 3, 13, 23, 45, tzinfo=NY), dt.date(2027, 3, 20)),
    ],
)
def test_snooze_day_is_store_local_across_dst(store, admin_client, monkeypatch, closed_local, back_on):
    monkeypatch.setattr(fix_issues, "_now", lambda: closed_local.astimezone(dt.timezone.utc))
    close(admin_client, ["new-rcv"])
    res = store.query(IssueResolution).filter_by(issue_key="new-rcv").one()
    store.refresh(res)
    assert res.snooze_until == back_on
    # A row with no stored day (closed before 0040) works out the same day.
    res.snooze_until = None
    store.commit()
    assert fix_issues.snooze_until_for(res) == back_on
    assert "new-rcv" not in rows_by_key(get_issue_list(store, TYPE, today=back_on - dt.timedelta(days=1)))
    assert rows_by_key(get_issue_list(store, TYPE, today=back_on))["new-rcv"]["reopened"]["how"] == "snooze_ended"


def test_lazy_read_and_daily_job_at_once_reopen_once(store, admin_client, monkeypatch):
    """The 9:00 job deletes the resolution between the list read's check
    and its delete: only one reopen is recorded."""
    from app.db import SessionLocal

    close(admin_client, ["new-rcv"])
    backdate(store, "new-rcv", 7)
    items, _ = fix_issues._detected(store, TYPE, fix_issues._Context(store))
    job = SessionLocal()
    real_execute = store.execute
    raced = []

    def racing_execute(stmt, *args, **kwargs):
        if not raced and getattr(stmt, "is_delete", False):
            raced.append(fix_issues.reopen_all_ended_snoozes(job))
        return real_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(store, "execute", racing_execute)
    try:
        assert fix_issues.reopen_ended_snoozes(store, TYPE, items) == []
    finally:
        job.close()
    assert [len(r) for r in raced] == [1]
    assert store.query(IssueResolutionEvent).filter_by(issue_key="new-rcv", event="reopened").count() == 1
    assert rows_by_key(get_issue_list(store, TYPE))["new-rcv"]["reopened"]["how"] == "snooze_ended"


def test_demo_removal_request_never_pushes_admins(db, admin, cart, buyer_client, pushes):
    """App Review build 7: a demo login can't request a removal at all (the
    demo read-only guard, app/demo_guard.py), so nothing ever pushes admins."""
    demo = make_user(db, "app_store_review", features=["purchase_order_cart"])
    device(db, admin)
    demo_client = client_for(db, demo)
    res = demo_client.post(f"/api/ordering/cart/items/{cart['Okra']}/removal-request", json={"reason": "test"})
    assert res.status_code == 403
    noon = at(today(), 12)
    assert run_alert_job("cart_removal_request", now=noon, db=db) == 0
    assert pushes == []
    # A real request still goes out, and only names the real line.
    buyer_client.post(f"/api/ordering/cart/items/{cart['Basmati 10lb']}/removal-request")
    assert run_alert_job("cart_removal_request", now=noon, db=db) == 1
    assert pushes[0]["body"] == "buyer: Basmati 10lb (Raja Foods)"
