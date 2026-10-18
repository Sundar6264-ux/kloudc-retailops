"""Today-tab order actions (docs/BATCH_OCT14_CONTRACT.md W1, migration 0053):
Order already placed / Skip this week / Other + Undo, and that the Vendor
Calendar, Reorder Candidates, the 9 AM pushes and the Team vendor-order task
all agree with them."""

import datetime as dt
import json

import pytest

from app.demo_guard import ALLOWLIST, DEMO_READ_ONLY
from app.models import PlacedOrder, VendorOrderSkip, VendorSchedule
from app.services import notification_triggers as nt
from app.services import order_actions
from app.services import vendor_calendar
from app.services.purchase_order_log import order_out
from app.services.reorder import group_candidates_by_vendor, ordered_vendors
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

API = "/api/order-actions"
ET = dt.timezone(dt.timedelta(hours=-4))  # EDT
TODAY = dt.date(2026, 9, 30)  # a Wednesday
THU = dt.date(2026, 10, 1)


def _at(day, hour=10):
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(dt.timezone.utc)


def _schedule(db, vendor, weekdays, lead_days):
    db.add(
        VendorSchedule(
            vendor=vendor, delivery_weekdays=json.dumps(weekdays), lead_days=lead_days, active=True, created_at=utcnow()
        )
    )
    db.commit()


def _order(db, supplier, placed_at):
    row = PlacedOrder(
        supplier=supplier,
        item_count=1,
        items_json=json.dumps([{"name": "Toor Dal", "supplier_item_id": "TD", "qty": 1}]),
        placed_by_name="boss",
        placed_at=placed_at,
        status="placed",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def need(monkeypatch):
    rows = [{"item_id": "a", "projections": {"7": {"need_to_order": 4}}}]
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: rows)
    return rows


@pytest.fixture
def scheduled(db):
    # Thursday deliveries, 1-day lead: the orders are due Wednesday Sep 30.
    _schedule(db, "Raja Foods", [3], 1)
    _schedule(db, "Chetak", [3], 1)


def _order_dues(db, start=TODAY, end=TODAY):
    events = vendor_calendar.get_vendor_calendar(db, start, end)["events"]
    return {e["vendor"]: e for e in events if e["type"] == "order_due"}


def _alerts(db):
    return sorted(a.title for a in nt.order_due_alerts(db, dt.datetime(2026, 9, 30, 8, 0, tzinfo=ET)))


# --- Orders due: each action, its effect and its Undo ---------------------------------------


def test_order_already_placed_from_an_orders_due_row(db, admin_client, scheduled, need):
    assert _alerts(db) == ["Order today: Chetak", "Order today: Raja Foods"]
    r = admin_client.post(API, json={"vendor": "raja foods", "action": "placed", "delivery_date": THU.isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "placed" and body["vendor"] == "Raja Foods"  # the schedule's spelling
    assert body["delivery_date"] == "2026-10-01" and body["duplicate"] is False
    assert body["handled"]["kind"] == "placed_outside" and body["handled"]["can_undo"] is True
    row = db.get(PlacedOrder, body["order_id"])
    assert (row.status, row.source, row.item_count, row.items_json, row.for_delivery_date) == (
        "placed", "outside", 0, "[]", THU,
    )
    assert row.placed_by_name == "boss"

    by = _order_dues(db)
    raja = by["Raja Foods"]
    assert raja["ordered"] is True and raja["urgent"] is False and raja["skipped"] is False
    assert raja["ordered_source"] == "outside" and raja["ordered_order_id"] == row.id
    assert raja["handled"]["kind"] == "placed_outside" and raja["handled"]["label"] == "Order already placed"
    assert by["Chetak"]["handled"] is None and by["Chetak"]["ordered_source"] is None
    assert _alerts(db) == ["Order today: Chetak"]
    # Released app builds get the same answer as Mark placed: left out by default.
    vendors = [e["vendor"] for e in admin_client.get(
        "/api/vendor-calendar", params={"start": TODAY.isoformat(), "end": TODAY.isoformat()}
    ).json()["events"] if e["type"] == "order_due"]
    assert vendors == ["Chetak"]

    # Same cycle again (double tap / second phone): the same row.
    again = admin_client.post(API, json={"vendor": "Raja Foods", "action": "placed", "delivery_date": "2026-10-01"})
    assert again.json()["duplicate"] is True and again.json()["order_id"] == row.id
    assert db.query(PlacedOrder).count() == 1

    # Undo.
    u = admin_client.delete(f"{API}/placed/{row.id}")
    assert u.status_code == 200 and u.json()["vendor"] == "Raja Foods"
    assert db.query(PlacedOrder).count() == 0
    assert _order_dues(db)["Raja Foods"]["ordered"] is False
    assert _alerts(db) == ["Order today: Chetak", "Order today: Raja Foods"]
    assert admin_client.delete(f"{API}/placed/{row.id}").status_code == 404


def test_skip_this_week_and_other_with_undo(db, admin_client, scheduled, need):
    r = admin_client.post(API, json={"vendor": "Chetak", "action": "skip", "delivery_date": "2026-10-01"})
    assert r.status_code == 200 and r.json()["handled"]["kind"] == "skip"
    r = admin_client.post(
        API, json={"vendor": "Raja Foods", "action": "other", "delivery_date": "2026-10-01", "note": " Rep visits Fri "}
    )
    assert r.status_code == 200, r.text
    assert r.json()["handled"] == {
        "kind": "other", "label": "Other", "note": "Rep visits Fri", "by_name": "boss",
        "at": r.json()["handled"]["at"], "order_id": None, "can_undo": True,
    }
    skip = db.query(VendorOrderSkip).filter_by(vendor="Raja Foods").one()
    assert (skip.reason, skip.note, skip.skipped_by_name) == ("other", "Rep visits Fri", "boss")

    by = _order_dues(db)
    assert by["Chetak"]["skipped"] is True and by["Chetak"]["skip_reason"] == "skip" and by["Chetak"]["urgent"] is False
    assert by["Raja Foods"]["skipped"] is True and by["Raja Foods"]["skip_reason"] == "other"
    assert by["Raja Foods"]["handled"]["note"] == "Rep visits Fri"
    assert _alerts(db) == []
    # The delivery shows skipped (not missed), with why.
    events = vendor_calendar.get_vendor_calendar(db, THU, THU)["events"]
    deliveries = {e["vendor"]: e for e in events if e["type"] == "delivery"}
    assert deliveries["Raja Foods"]["status"] == "skipped"
    assert deliveries["Raja Foods"]["skip_reason"] == "other" and deliveries["Raja Foods"]["skip_note"] == "Rep visits Fri"
    assert deliveries["Chetak"]["skip_reason"] == "skip" and deliveries["Chetak"]["skip_note"] is None

    # Skip -> Other on the same cycle updates the one row.
    admin_client.post(API, json={"vendor": "Chetak", "action": "other", "delivery_date": "2026-10-01", "note": "closed"})
    assert db.query(VendorOrderSkip).filter_by(vendor="Chetak").count() == 1

    # Undo (also undoes a skip made on the website calendar).
    u = admin_client.delete(f"{API}/skip", params={"vendor": "raja foods", "delivery_date": "2026-10-01"})
    assert u.status_code == 200
    assert _order_dues(db)["Raja Foods"]["skipped"] is False
    assert _alerts(db) == ["Order today: Raja Foods"]
    assert admin_client.delete(f"{API}/skip", params={"vendor": "Raja Foods", "delivery_date": "2026-10-01"}).status_code == 404


def test_validation(db, admin_client, scheduled):
    def post(**body):
        return admin_client.post(API, json=body)

    r = post(vendor="Chetak", action="other", delivery_date="2026-10-01")
    assert r.status_code == 400 and r.json()["detail"] == "Add a note for Other."
    r = post(vendor="Chetak", action="skip")
    assert r.status_code == 400 and "delivery date" in r.json()["detail"]
    r = post(vendor="Chetak", action="nope", delivery_date="2026-10-01")
    assert r.status_code == 400
    r = post(vendor="Chetak", action="skip", delivery_date="2026-10-02")  # a Friday: not a Chetak delivery
    assert r.status_code == 400 and "doesn't deliver on Fri Oct 2" in r.json()["detail"]
    r = post(vendor="Babco", action="skip", delivery_date="2026-10-01")
    assert r.status_code == 404 and "no delivery schedule" in r.json()["detail"]
    r = post(vendor="Chetak", action="other", delivery_date="2026-10-01", note="x" * 501)
    assert r.status_code == 400 and "500 characters" in r.json()["detail"]
    r = post(vendor="  ", action="placed")
    assert r.status_code == 400
    assert db.query(VendorOrderSkip).count() == 0 and db.query(PlacedOrder).count() == 0


def test_undo_never_deletes_an_order_placed_from_the_cart(db, admin_client):
    row = _order(db, "Chetak", _at(TODAY, 9))
    r = admin_client.delete(f"{API}/placed/{row.id}")
    assert r.status_code == 400 and "Order already placed" in r.json()["detail"]
    assert db.get(PlacedOrder, row.id) is not None


def test_in_app_order_shows_as_placed_without_undo(db, scheduled, need):
    row = _order(db, "Chetak", _at(TODAY, 2))
    chetak = _order_dues(db)["Chetak"]
    assert chetak["ordered_source"] == "app"
    assert chetak["handled"]["kind"] == "placed" and chetak["handled"]["can_undo"] is False
    assert chetak["handled"]["order_id"] == row.id


def test_for_delivery_date_ties_the_order_to_that_delivery(db, admin, need):
    need[0]["projections"].update({str(h): {"need_to_order": 4} for h in range(1, 8)})
    # Mon + Thu deliveries, lead 0: on Thursday Oct 1 the order due today is
    # for today's delivery... marked from the NEXT cycle's row (Mon Oct 5,
    # due Mon) it counts for Oct 5, not Oct 1, though placed Oct 1.
    _schedule(db, "Chetak", [0, 3], 0)
    order_actions.place_outside(db, admin, "Chetak", dt.date(2026, 10, 5), now=_at(THU, 7))
    by = {
        e["for_delivery_date"]: e
        for e in vendor_calendar.get_vendor_calendar(db, THU, dt.date(2026, 10, 5))["events"]
        if e["type"] == "order_due"
    }
    assert by["2026-10-01"]["ordered"] is False  # the window rule would have said True
    assert by["2026-10-05"]["ordered"] is True and by["2026-10-05"]["ordered_source"] == "outside"


# --- Critical: Order already placed takes the vendor off Today --------------------------------


def _item(item_id, supplier):
    return {
        "item_id": item_id, "name": item_id, "supplier": supplier, "estimated_order_value": 5.0, "shortfall": 10,
        "on_hand_qty": 0, "avg_daily_demand": 2.0, "urgency": {"tier": "critical", "label": "Critical"},
    }


def test_order_already_placed_from_the_critical_tile(db, admin, admin_client, monkeypatch):
    from app.models import PurchaseLogEntry

    db.add(PurchaseLogEntry(item_id="x", item_name="x", supplier="Chetak", quantity_received=1,
                            received_date=TODAY - dt.timedelta(days=3), created_at=_at(TODAY - dt.timedelta(days=3))))
    db.commit()
    items = [_item(f"c{i}", "Chetak") for i in range(5)]
    monkeypatch.setattr(nt, "compute_reorder_candidates", lambda *a, **k: [dict(i) for i in items])
    monkeypatch.setattr(order_actions, "resolve_vendor", lambda db, v: "Chetak")
    now = dt.datetime(2026, 9, 30, 9, 0, tzinfo=ET)
    assert [a.title for a in nt.critical_vendors_alerts(db, now)] == ["1 vendor critical"]

    row, dup = order_actions.place_outside(db, admin, "chetak", now=_at(TODAY, 8))
    assert dup is False and row.for_delivery_date is None
    # A double tap within 2 minutes is the same order.
    again, dup = order_actions.place_outside(db, admin, "Chetak", now=_at(TODAY, 8) + dt.timedelta(seconds=30))
    assert dup is True and again.id == row.id

    groups = group_candidates_by_vendor(db, [dict(i) for i in items], TODAY, 7)
    g = groups[0]
    assert g["urgency"]["tier"] == "ordered" and g["ordered_source"] == "outside" and g["ordered_order_id"] == row.id
    assert ordered_vendors(db, TODAY, 7)["chetak"]["source"] == "outside"
    assert nt.critical_vendors_alerts(db, now) == []

    # The Critical vendors sheet's list (GET critical-dismissals).
    active = order_actions.outside_orders_active(db, TODAY, 7)
    assert [(a["order_id"], a["vendor"]) for a in active] == [(row.id, "Chetak")]

    out = order_out(row)
    assert out["source"] == "outside" and out["source_label"] == "Placed outside the app"
    assert out["items"] == [] and out["item_count"] == 0 and out["total_qty"] == 0

    order_actions.undo_outside(db, row.id)
    assert [a.title for a in nt.critical_vendors_alerts(db, now)] == ["1 vendor critical"]


def test_critical_dismissals_get_lists_outside_orders(db, admin, admin_client, monkeypatch):
    monkeypatch.setattr(order_actions, "resolve_vendor", lambda db, v: "Chetak")
    row, _ = order_actions.place_outside(db, admin, "Chetak", note="Called Raj")
    body = admin_client.get("/api/reorder/critical-dismissals").json()
    assert [(p["order_id"], p["vendor"], p["note"]) for p in body["placed_outside"]] == [(row.id, "Chetak", "Called Raj")]


def test_placed_orders_answer_is_additive(db, admin, admin_client, monkeypatch):
    monkeypatch.setattr(order_actions, "resolve_vendor", lambda db, v: "Chetak")
    order_actions.place_outside(db, admin, "Chetak")
    app_row = _order(db, "Babco", utcnow())
    items = admin_client.get("/api/ordering/orders/recent").json()["items"]
    by = {i["supplier"]: i for i in items}
    assert by["Chetak"]["source"] == "outside" and by["Chetak"]["items"] == []
    assert by["Babco"]["source"] == "app" and by["Babco"]["source_label"] is None
    # Order again on an outside order adds nothing (no lines).
    r = admin_client.post(f"/api/ordering/orders/{by['Chetak']['id']}/order-again")
    assert r.status_code == 200 and r.json()["added"] == 0
    assert app_row.id == by["Babco"]["id"]


def test_unknown_vendor_for_placed(db, admin_client, monkeypatch):
    from app.services import supplier_projection

    monkeypatch.setattr(supplier_projection, "list_suppliers", lambda db: ["Chetak"])
    r = admin_client.post(API, json={"vendor": "Nobody Foods", "action": "placed"})
    assert r.status_code == 404 and r.json()["detail"] == "Unknown vendor: Nobody Foods"
    r = admin_client.post(API, json={"vendor": "CHETAK", "action": "placed", "note": "phone"})
    assert r.status_code == 200 and r.json()["vendor"] == "Chetak"


# --- The Team vendor-order task follows them too -----------------------------------------------


def test_team_task_rules_see_outside_orders_and_other(db, admin, scheduled):
    from app.team import auto_tasks

    assert auto_tasks._placed_since(db, "Raja Foods", TODAY) is False
    order_actions.place_outside(db, admin, "Raja Foods", THU, now=_at(TODAY, 8))
    assert auto_tasks._placed_since(db, "raja foods", TODAY) is True
    order_actions.skip_cycle(db, admin, "Chetak", THU, "other", "closed")
    assert auto_tasks._skipped(db, "Chetak", THU) is True


# --- Permissions --------------------------------------------------------------------------------


def test_permissions(db, admin_client, anon_client, scheduled):
    staff = client_for(db, make_user(db, "kiran", features=["vendor_calendar", "reorder_candidates"]))
    demo = client_for(db, make_user(db, "app_store_review", is_admin=True))
    body = {"vendor": "Chetak", "action": "skip", "delivery_date": "2026-10-01"}
    assert anon_client.post(API, json=body).status_code == 401
    r = staff.post(API, json=body)
    assert r.status_code == 403 and r.json()["detail"] == "Admin privileges required"
    assert staff.delete(f"{API}/placed/1").status_code == 403
    assert staff.delete(f"{API}/skip", params={"vendor": "Chetak", "delivery_date": "2026-10-01"}).status_code == 403
    for r in (
        demo.post(API, json=body),
        demo.delete(f"{API}/placed/1"),
        demo.delete(f"{API}/skip", params={"vendor": "Chetak", "delivery_date": "2026-10-01"}),
    ):
        assert r.status_code == 403 and r.json() == {"detail": DEMO_READ_ONLY}
    assert ("POST", API) not in ALLOWLIST
    assert db.query(VendorOrderSkip).count() == 0
