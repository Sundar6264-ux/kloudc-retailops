"""Final regression fixes (Oct 14).

(a) Vendor minimum orders on the Vendor Calendar: an order-due is `urgent` only with at least
    VENDOR_MIN_ORDER_ITEMS (5) items needing ordering - the same threshold as Reorder's vendor rule and the
    "Place the order" Team task. 1-4 items is `below_minimum` (the count still shows), and the 9 AM push says
    "N items low" instead of "N items to order".
(b) Schedule suggestions carry how often the vendor delivers (`cadence`, `irregular`, `cadence_text`) and
    `add_all_default`: Add all starts with High / Medium ticked, Low unticked, and irregular vendors (every
    2-4 weeks or rarer) unticked with the text that weekly isn't right.
"""

import datetime as dt
import json

import pytest

from app import glossary
from app.models import PlacedOrder, VendorSchedule
from app.services import notification_triggers as nt
from app.services import schedule_suggestions as ss
from app.services import vendor_calendar
from app.services.reorder import VENDOR_MIN_ORDER_ITEMS
from app.timeutil import utcnow
from tests.test_x2_schedule_suggestions import TODAY as X2_TODAY
from tests.test_x2_schedule_suggestions import invoice, run, weekly

ET = dt.timezone(dt.timedelta(hours=-4))
WED = dt.date(2026, 9, 30)  # order due for a Thursday delivery, 1 day lead


def _schedule(db, vendor):
    db.add(VendorSchedule(vendor=vendor, delivery_weekdays=json.dumps([3]), lead_days=1, active=True, created_at=utcnow()))
    db.commit()


def _need(n):
    return [{"item_id": f"i{k}", "projections": {"7": {"need_to_order": 3}}} for k in range(n)]


def _event(db, monkeypatch, n, vendor="Raja Foods"):
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: _need(n))
    events = vendor_calendar.get_vendor_calendar(db, WED, WED)["events"]
    return next(e for e in events if e["type"] == "order_due" and e["vendor"] == vendor)


def test_threshold_is_the_reorder_vendor_minimum():
    assert vendor_calendar.ORDER_MIN_ITEMS == VENDOR_MIN_ORDER_ITEMS == 5


@pytest.mark.parametrize("n,urgent,below", [(0, False, False), (1, False, True), (2, False, True), (4, False, True), (5, True, False), (12, True, False)])
def test_one_or_two_items_never_make_an_order_due_urgent(db, monkeypatch, n, urgent, below):
    _schedule(db, "Raja Foods")
    e = _event(db, monkeypatch, n)
    assert e["items_needing_order"] == n
    assert e["urgent"] is urgent and e["below_minimum"] is below
    assert e["min_order_items"] == 5


def test_skipped_or_placed_is_neither_urgent_nor_below_minimum(db, monkeypatch):
    _schedule(db, "Raja Foods")
    vendor_calendar.skip_order(db, "Raja Foods", dt.date(2026, 10, 1), "boss")
    e = _event(db, monkeypatch, 2)
    assert e["skipped"] is True and e["urgent"] is False and e["below_minimum"] is False

    _schedule(db, "Chetak")
    db.add(PlacedOrder(supplier="Chetak", item_count=1, items_json="[]", placed_by_name="boss", status="placed",
                       placed_at=dt.datetime(2026, 9, 30, 9, tzinfo=ET).astimezone(dt.timezone.utc)))
    db.commit()
    e = _event(db, monkeypatch, 7, vendor="Chetak")
    assert e["ordered"] is True and e["urgent"] is False and e["below_minimum"] is False


def test_push_says_low_not_to_order_below_the_minimum(db, monkeypatch):
    _schedule(db, "Raja Foods")
    now = dt.datetime(2026, 9, 30, 9, 0, tzinfo=ET)
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: _need(2))
    [alert] = nt.order_due_alerts(db, now)
    assert alert.title == "Order today: Raja Foods"
    assert alert.body == "2 items low - fewer than a usual order, review"
    assert "to order" not in alert.body
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: _need(6))
    [alert] = nt.order_due_alerts(db, now)
    assert alert.body == "6 items to order"
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: [])
    [alert] = nt.order_due_alerts(db, now)
    assert alert.body == "Review the order"


def test_glossary_states_the_minimum():
    term = next(t for t in glossary.TERMS if t["key"] == "cal_items_needed")
    assert "5 items" in term["definition"] and "minimum" in term["definition"] and "5 or more" in term["rule"]


# --- (b) cadence + Add all defaults ------------------------------------------------------------


def _days(*offsets):
    return [X2_TODAY - dt.timedelta(days=o) for o in offsets]


def test_cadence_rules():
    assert ss.cadence([])["cadence"] == "unknown"
    assert ss.cadence(_days(0))["irregular"] is False
    weekly_ = ss.cadence(_days(0, 7, 14, 21, 28))
    assert weekly_["cadence"] == "weekly" and weekly_["irregular"] is False and weekly_["text"] == ""
    twice = ss.cadence(_days(0, 3, 7, 10, 14, 17))
    assert twice["cadence"] == "weekly" and twice["median_gap_days"] == 3.0
    biweekly = ss.cadence(_days(0, 14, 28, 42, 56))
    assert biweekly["cadence"] == "every_few_weeks" and biweekly["irregular"] is True
    assert biweekly["text"].startswith("Delivers about every 2 weeks, not every week")
    assert "Weekly isn't right" in biweekly["text"]
    # RBest on prod: gaps 14, 15, 7, 14, 7, 6, 14 -> every 1-2 weeks.
    rbest = ss.cadence(_days(0, 14, 20, 27, 41, 48, 63, 77))
    assert rbest["irregular"] is True and rbest["text"].startswith("Delivers every 1-2 weeks")
    assert rbest["label"] == "every 1-2 weeks" and biweekly["label"] == "about every 2 weeks"
    monthly = ss.cadence(_days(0, 42, 63))
    assert monthly["cadence"] == "rare" and monthly["text"].startswith("Delivers about once a month or less")
    assert monthly["label"] == "about once a month or less" and weekly_["label"] == ""
    # A weekly vendor with one missed week stays weekly.
    assert ss.cadence(_days(0, 7, 21, 28, 35))["cadence"] == "weekly"


def test_add_all_default_high_medium_ticked_low_and_irregular_not(db):
    for day in weekly(1, 8):  # Tuesdays, every week: High
        invoice(db, "Weekly High", day)
    for day in weekly(3, 4):  # Thursdays, every week, 4 of 4: Medium
        invoice(db, "Weekly Medium", day)
    for day in weekly(4, 12)[::2]:  # Fridays every 2 weeks: 6 of 6 -> High but irregular
        invoice(db, "Biweekly", day)
    for day in weekly(0, 8)[:2]:  # Mondays 2 + scattered: Low
        invoice(db, "Thin", day)
    for day in _days(5, 26):
        invoice(db, "Thin", day)
    db.commit()
    result = run(db)
    by = {s["vendor"]: s for s in result["suggestions"]}
    assert by["Weekly High"]["confidence"] == "high" and by["Weekly High"]["add_all_default"] is True
    assert by["Weekly Medium"]["confidence"] == "medium" and by["Weekly Medium"]["add_all_default"] is True
    assert by["Biweekly"]["irregular"] is True and by["Biweekly"]["add_all_default"] is False
    assert by["Biweekly"]["cadence_text"].startswith("Delivers about every 2 weeks")
    assert by["Biweekly"]["cadence_label"] == "about every 2 weeks" and by["Weekly High"]["cadence_label"] == ""
    assert by["Thin"]["confidence"] == "low" and by["Thin"]["add_all_default"] is False
    assert by["Weekly High"]["cadence"] == "weekly" and by["Weekly High"]["cadence_text"] == ""
    assert result["rules"]["add_all_default_confidence"] == ["high", "medium"]
    assert result["rules"]["irregular_min_gap_days"] == ss.IRREGULAR_MIN_GAP_DAYS
    # Every suggestion is still listed (the clients show unticked ones too).
    assert set(by) == {"Weekly High", "Weekly Medium", "Biweekly", "Thin"}


def test_add_all_still_accepts_an_unticked_by_default_key(db, admin_client):
    for day in weekly(4, 12)[::2]:
        invoice(db, "Biweekly", day)
    db.commit()
    [s] = admin_client.get("/api/vendor-calendar/schedule-suggestions").json()["suggestions"]
    assert s["add_all_default"] is False
    r = admin_client.post("/api/vendor-calendar/schedule-suggestions/accept-all", json={"keys": [s["key"]]})
    assert r.status_code == 200 and r.json()["created_count"] == 1
