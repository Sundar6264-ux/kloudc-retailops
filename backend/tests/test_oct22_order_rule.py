"""Oct 22: the shared order rule (services/order_cycle.py), ported from the
other ops system's tests/test_oct22_a1_order_placed.py so the two agree.

An order placed by ANY path drops its vendor from Home's Orders due and
Critical tiles, the website's Vendor schedule card and the 9:00 pushes /
Morning summary. The owner's case: a Thu + Sun vendor (orders 2 days ahead)
sent its PO on Thursday (a delivery day) for Sunday's delivery.
"""

import datetime as dt
import json

import pytest

from app.models import CartItem, PurchaseLogEntry, VendorSchedule
from app.services import notification_triggers as nt
from app.services import order_actions, order_cycle, purchase_order_log, vendor_calendar
from app.services.order_cycle import order_delivery
from app.services.order_suggestion import cycle_order
from app.services.reorder import ordered_vendors
from app.timeutil import utcnow
from tests.conftest import make_user

ET = dt.timezone(dt.timedelta(hours=-4))  # EDT
THU = dt.date(2026, 10, 22)
FRI, SAT, SUN = THU + dt.timedelta(days=1), THU + dt.timedelta(days=2), THU + dt.timedelta(days=3)
TUE = THU - dt.timedelta(days=2)
THU_, SUN_ = 3, 6


def _at(day, hour=15, minute=0):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(dt.timezone.utc)


def _schedule(db, vendor, weekdays, lead_days):
    db.add(VendorSchedule(vendor=vendor, delivery_weekdays=json.dumps(weekdays), lead_days=lead_days, active=True,
                          created_at=utcnow()))
    db.commit()


@pytest.fixture
def need(monkeypatch):
    rows = [{"item_id": f"i{n}", "projections": {str(h): {"need_to_order": 3} for h in range(1, 15)}} for n in range(6)]
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: rows)
    return rows


@pytest.fixture
def clock(monkeypatch):
    """Every placement path stamps placed_at with utcnow(): pin it."""
    state = {"now": _at(THU)}

    def now():
        return state["now"]

    monkeypatch.setattr(purchase_order_log, "utcnow", now)
    monkeypatch.setattr(order_actions, "utcnow", now)
    return state


def _due(db, day):
    events = vendor_calendar.get_vendor_calendar(db, day, day)["events"]
    return {e["vendor"]: e for e in events if e["type"] == "order_due" and e["date"] == day.isoformat()}


def _morning(db):
    return sorted(a.title for a in nt.order_due_alerts(db, dt.datetime(THU.year, THU.month, THU.day, 9, 0, tzinfo=ET)))


def _cart(db, supplier, n=5):
    for i in range(n):
        db.add(CartItem(supplier=supplier, item_id=f"{supplier}-{i}", name=f"Item {i}", supplier_item_id=f"C{i}",
                        qty=2, case_of=1.0))
    db.commit()


# --- the shared rule ----------------------------------------------------------------------


def test_rule_order_on_a_delivery_day_is_for_the_next_delivery():
    # Thu + Sun, 2 days ahead: Thursday's order day is Tue, Sunday's is Fri.
    assert order_delivery(_at(THU), [THU_, SUN_], 2) == SUN
    assert order_delivery(_at(TUE), [THU_, SUN_], 2) == THU
    assert order_delivery(_at(FRI), [THU_, SUN_], 2) == SUN


def test_rule_late_order_on_delivery_day_is_that_days():
    # Weekly Thursday, ordered the day before: marked at 4am on Thursday with
    # nothing recorded yet = that Thursday's order (never next week's).
    assert order_delivery(_at(THU, 4), [THU_], 1) == THU
    # The real case: placed at 05:15 on delivery day for that day's delivery.
    assert order_delivery(_at(THU, 5, 15), [THU_], 1) == THU
    # Ordered early (Saturday) = the next Thursday.
    assert order_delivery(_at(THU - dt.timedelta(days=5)), [THU_], 1) == THU


def test_rule_a_delivery_already_recorded_that_day_is_done():
    recorded = [_at(THU, 11)]  # delivery logged at 11am, order at 3pm
    assert order_delivery(_at(THU, 15), [THU_], 1, recorded=recorded) == THU + dt.timedelta(days=7)
    # Recorded AFTER the order: the order is still that day's.
    assert order_delivery(_at(THU, 9), [THU_], 1, recorded=recorded) == THU


def test_rule_tied_delivery_and_no_schedule():
    assert order_delivery(_at(THU), [THU_, SUN_], 2, for_delivery_date=THU) == THU
    assert order_delivery(_at(THU), [], 2) is None


# --- every placement path, through the real endpoints ------------------------------------


def _place(path, db, admin_client):
    if path == "mark_placed":
        _cart(db, "Chetak")
        r = admin_client.post("/api/ordering/cart/mark-placed", params={"supplier": "Chetak"})
    elif path == "send_po":  # what Send PO records once the email went out
        _cart(db, "Chetak")
        rows = db.query(CartItem).filter(CartItem.supplier == "Chetak").all()
        snapshot = [{"name": r.name, "supplier_item_id": r.supplier_item_id, "qty": r.qty, "item_id": r.item_id}
                    for r in rows]
        purchase_order_log.place_sent_order(db, "Chetak", make_user(db, "sender", is_admin=True), snapshot,
                                            [r.id for r in rows])
        r = None
    elif path == "already_placed_no_date":  # Critical sheet / Reorder
        r = admin_client.post("/api/order-actions", json={"vendor": "Chetak", "action": "placed"})
    elif path == "orders_due_row":  # Home's Orders due row, Vendor Calendar chip "Placed"
        r = admin_client.post("/api/order-actions",
                              json={"vendor": "Chetak", "action": "placed", "delivery_date": SUN.isoformat()})
    else:
        raise AssertionError(path)
    if r is not None:
        assert r.status_code == 200, r.text


def _assert_dropped_everywhere(db):
    db.expire_all()
    # Home Orders due (and the website's Vendor schedule card): Friday's
    # order for Sunday is placed; Tuesday's (Thursday's) is not this order.
    assert _due(db, FRI)["Chetak"]["ordered"] is True
    assert _due(db, FRI)["Chetak"]["urgent"] is False
    assert _due(db, TUE)["Chetak"]["ordered"] is False
    # 9:00 push / Morning summary (today + tomorrow).
    assert "Order tomorrow: Chetak" not in _morning(db)
    # Critical tile + 9:00 critical push: "Ordered" until Sunday's delivery (+ grace).
    o = ordered_vendors(db, THU, 7)["chetak"]
    assert o["ordered_until"] == (SUN + dt.timedelta(days=3)).isoformat()
    # The suggested-order basis line agrees.
    assert cycle_order(db, "Chetak", SUN, [THU_, SUN_]) is not None
    assert cycle_order(db, "Chetak", THU, [THU_, SUN_]) is None


@pytest.mark.parametrize("path", ["mark_placed", "send_po", "already_placed_no_date", "orders_due_row"])
def test_each_path_drops_the_order_due_and_critical(db, admin_client, need, clock, path):
    _schedule(db, "Chetak", [THU_, SUN_], 2)
    assert _due(db, FRI)["Chetak"]["urgent"] is True
    assert "Order tomorrow: Chetak" in _morning(db)
    assert "chetak" not in ordered_vendors(db, THU, 7)
    _place(path, db, admin_client)
    _assert_dropped_everywhere(db)


def test_order_at_5am_on_delivery_day_clears_that_days_reminder(db, admin_client, need, clock):
    # Weekly Thursday, ordered a day ahead; the order went in at 05:15 on
    # Thursday for Thursday's delivery (real data): Wednesday's order-due is
    # placed, and next week's is still asked for.
    _schedule(db, "Chetak", [THU_], 1)
    clock["now"] = _at(THU, 5, 15)
    _cart(db, "Chetak")
    assert admin_client.post("/api/ordering/cart/mark-placed", params={"supplier": "Chetak"}).status_code == 200
    db.expire_all()
    assert _due(db, THU - dt.timedelta(days=1))["Chetak"]["ordered"] is True
    assert _due(db, THU + dt.timedelta(days=6))["Chetak"]["ordered"] is False
    assert ordered_vendors(db, THU, 7)["chetak"]["ordered_until"] == (THU + dt.timedelta(days=3)).isoformat()


def test_weekly_vendor_ordered_right_after_its_delivery_is_next_weeks(db, admin_client, need, clock):
    # Weekly Thursday vendor (order by Wed). The delivery came at 11am and was
    # logged; the 3pm order is for next Thursday, so next Wednesday's order
    # due shows as placed and the vendor stays "Ordered" until then.
    _schedule(db, "Chetak", [THU_], 1)
    db.add(PurchaseLogEntry(item_id="x", item_name="x", supplier="Chetak", quantity_received=1, received_date=THU,
                            created_at=_at(THU, 11)))
    db.commit()
    _cart(db, "Chetak")
    assert admin_client.post("/api/ordering/cart/mark-placed", params={"supplier": "Chetak"}).status_code == 200
    db.expire_all()
    next_wed = THU + dt.timedelta(days=6)
    assert _due(db, next_wed)["Chetak"]["ordered"] is True
    assert "chetak" in ordered_vendors(db, THU, 7)


def test_order_cycle_module_is_the_one_rule():
    import inspect

    from app.services import order_suggestion, reorder
    from app.team import auto_tasks

    assert "OrderCycles" in inspect.getsource(vendor_calendar.get_vendor_calendar)
    assert "OrderCycles" in inspect.getsource(reorder.ordered_vendors)
    assert "OrderCycles" in inspect.getsource(order_suggestion.cycle_order)
    assert "OrderCycles" in inspect.getsource(auto_tasks._placed_since)
    assert order_cycle.order_delivery is order_delivery
