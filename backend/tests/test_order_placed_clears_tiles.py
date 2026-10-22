"""Owner (Oct 22): "Home screen tiles still show orders due, critical orders
... even after the order is placed through new order / supplier projection
screens".

One shared rule (services/order_cycle.py) decides which delivery a placed
order is for, and every count that shows orders due or critical vendors goes
by it: the Vendor Calendar order-dues (app Home Orders due tile, website
Overview Vendor schedule, 9 AM push, morning summary), the Reorder "Ordered"
status (app Home Critical tile, critical-vendors push, website Need
reordering) and the Team "Place the order" task.

Every way of placing an order is tested: New order -> cart -> Send PO, the
cart's Mark placed, "Order already placed" on an order-due (Today tiles /
Vendor Calendar "Placed"), and "Order already placed" without a date
(Reorder).
"""

import datetime as dt
import json

import pytest

from app.models import CartItem, PlacedOrder, PurchaseLogEntry, VendorSchedule
from app.services import notification_triggers as nt
from app.services import order_actions
from app.services import vendor_calendar
from app.services.order_cycle import order_delivery
from app.services.purchase_order_log import mark_supplier_placed, place_sent_order
from app.services.reorder import group_candidates_by_vendor, ordered_vendors
from app.timeutil import utcnow
from tests.conftest import make_user

ET = dt.timezone(dt.timedelta(hours=-4))  # EDT
MON = dt.date(2026, 10, 19)  # a Monday: today in these tests
TUE = dt.date(2026, 10, 20)
THU = dt.date(2026, 10, 22)
VENDOR = "Raja Foods"


def _at(day, hour=10):
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(dt.timezone.utc)


NOW = dt.datetime(2026, 10, 19, 11, 0, tzinfo=ET)  # Monday 11 AM, after the order


@pytest.fixture
def setup(db, monkeypatch):
    # Monday + Thursday deliveries, 2 days' lead: Thursday's order is due
    # Tuesday - Home shows it as "Order tomorrow" on Monday.
    db.add(
        VendorSchedule(
            vendor=VENDOR, delivery_weekdays=json.dumps([0, 3]), lead_days=2, active=True, created_at=utcnow()
        )
    )
    # Last Thursday's delivery: an active vendor (not one the store stopped using).
    db.add(
        PurchaseLogEntry(
            item_id="r0",
            item_name="r0",
            supplier=VENDOR,
            quantity_received=1,
            received_date=dt.date(2026, 10, 15),
            created_at=_at(dt.date(2026, 10, 15), 12),
        )
    )
    db.commit()
    need = [{"item_id": f"a{i}", "projections": {"3": {"need_to_order": 4}, "4": {"need_to_order": 4}}} for i in range(6)]
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: need)
    critical = [
        {
            "item_id": f"r{i}",
            "name": f"r{i}",
            "supplier": VENDOR,
            "estimated_order_value": 5.0,
            "shortfall": 10,
            "on_hand_qty": 0,
            "avg_daily_demand": 2.0,
            "urgency": {"tier": "critical", "label": "Critical"},
        }
        for i in range(5)
    ]
    monkeypatch.setattr(nt, "compute_reorder_candidates", lambda *a, **k: [dict(c) for c in critical])
    return {"user": make_user(db, "boss", is_admin=True), "critical": critical}


def _thursday_order_due(db):
    events = vendor_calendar.get_vendor_calendar(db, MON, TUE)["events"]
    (event,) = [e for e in events if e["type"] == "order_due" and e["for_delivery_date"] == THU.isoformat()]
    return event


def _counts(db, critical):
    """What every tile / push shows for the vendor on Monday."""
    groups = group_candidates_by_vendor(db, [dict(c) for c in critical], MON, 7)
    (group,) = [g for g in groups if g["vendor"] == VENDOR]
    return {
        "order_due_tile": not _thursday_order_due(db)["ordered"],
        "order_due_push": [a.title for a in nt.order_due_alerts(db, NOW)],
        "critical_tile": group["urgency"]["tier"] == "critical",
        "critical_push": [a.title for a in nt.critical_vendors_alerts(db, NOW)],
    }


def _cart(db, user):
    row = CartItem(supplier=VENDOR, name="Toor Dal", qty=3, item_id="a0", supplier_item_id="TD", added_by_user_id=user.id)
    db.add(row)
    db.commit()
    return row


def _send_po(db, user):
    row = _cart(db, user)
    snapshot = [{"name": "Toor Dal", "supplier_item_id": "TD", "qty": 3, "item_id": "a0"}]
    order, _ = place_sent_order(db, VENDOR, user, snapshot, [row.id])
    return order


def _mark_placed(db, user):
    _cart(db, user)
    order, _ = mark_supplier_placed(db, VENDOR, user)
    return order


def _already_placed_on_order_due(db, user):
    order, _ = order_actions.place_outside(db, user, VENDOR, THU, now=_at(MON))
    return order


def _already_placed_no_date(db, user):
    order, _ = order_actions.place_outside(db, user, VENDOR, None, now=_at(MON))
    return order


PATHS = {
    "new_order_send_po": _send_po,
    "cart_mark_placed": _mark_placed,
    "order_already_placed_on_order_due": _already_placed_on_order_due,
    "order_already_placed_no_date": _already_placed_no_date,
}


@pytest.mark.parametrize("path", sorted(PATHS))
def test_every_way_of_placing_clears_every_tile(db, setup, path):
    before = _counts(db, setup["critical"])
    assert before == {
        "order_due_tile": True,
        "order_due_push": ["Order tomorrow: Raja Foods"],
        "critical_tile": True,
        "critical_push": ["1 vendor critical"],
    }

    order = PATHS[path](db, setup["user"])
    # Placed on Monday at 10 AM - a delivery day (its delivery is last
    # cycle's order); the order is for Thursday.
    order.placed_at = _at(MON)
    db.commit()

    assert _counts(db, setup["critical"]) == {
        "order_due_tile": False,
        "order_due_push": [],
        "critical_tile": False,
        "critical_push": [],
    }
    event = _thursday_order_due(db)
    assert event["ordered_order_id"] == order.id and event["urgent"] is False
    assert ordered_vendors(db, MON, 7)["raja foods"]["ordered_until"] == "2026-10-25"  # Thu + 3 days


def test_order_placed_on_a_delivery_day_is_for_the_next_delivery_it_can_make(db, setup):
    # Before the shared rule the calendar gave a Monday order to Monday's own
    # delivery (the previous order), so Tuesday's "Order tomorrow" stayed.
    order = _send_po(db, setup["user"])
    order.placed_at = _at(MON)
    db.commit()
    events = vendor_calendar.get_vendor_calendar(db, dt.date(2026, 10, 17), TUE)["events"]
    by = {e["for_delivery_date"]: e for e in events if e["type"] == "order_due"}
    assert by["2026-10-19"]["ordered"] is False  # Monday's order was due Saturday
    assert by["2026-10-22"]["ordered"] is True


def test_the_shared_rule():
    mon_thu = [0, 3]
    # Placed Monday, 2 days' lead: Thursday's order day (Tue) is closer than
    # Monday's (Sat) -> Thursday.
    assert order_delivery(_at(MON), mon_thu, 2) == THU
    # Marked for a delivery: exactly that one.
    assert order_delivery(_at(MON), mon_thu, 2, for_delivery_date=dt.date(2026, 10, 26)) == dt.date(2026, 10, 26)
    # No schedule: no delivery to tie it to.
    assert order_delivery(_at(MON), [], 2) is None


def test_morning_summary_leaves_out_a_placed_vendor(db, setup):
    order = _send_po(db, setup["user"])
    order.placed_at = _at(MON)
    db.commit()
    # The two morning-summary lines come from these triggers (MORNING_SOURCES).
    computed = {key: nt.TRIGGERS[key](db, NOW) for key in ("order_due", "critical_vendors")}
    assert {"order_due", "critical_vendors"} <= set(nt.MORNING_SOURCES)
    assert computed == {"order_due": [], "critical_vendors": []}


def test_team_order_task_is_not_created_for_a_placed_order(db, setup):
    from app.team import auto_tasks

    order = _send_po(db, setup["user"])
    order.placed_at = _at(MON)
    db.commit()
    # The Tuesday cycle (Thursday delivery) counts as placed for the task too.
    assert auto_tasks._placed_since(db, VENDOR, TUE, THU) is True
    assert db.query(PlacedOrder).count() == 1
