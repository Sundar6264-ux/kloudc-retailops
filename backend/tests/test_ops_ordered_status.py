"""Reorder "Ordered" status: a vendor with a placed order (Mark placed) that
hasn't been delivered yet shows "Ordered" instead of its stock tier, so it
stops counting as Critical / Order now (Home tile, push alerts, Overview).
See reorder.ordered_vendors / group_candidates_by_vendor."""

import datetime as dt
import json

from app.models import InvoiceSubmission, PlacedOrder, PurchaseLogEntry, VendorSchedule
from app.services import notification_triggers as nt
from app.services import vendor_calendar
from app.services.reorder import group_candidates_by_vendor, ordered_vendors
from app.timeutil import utcnow

ET = dt.timezone(dt.timedelta(hours=-4))  # EDT, the store's zone in Sep/Oct


def _at(day, hour=10):
    """A UTC timestamp for `hour` o'clock store time on `day`."""
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(dt.timezone.utc)


def _order(db, supplier, placed_at, status="placed"):
    row = PlacedOrder(
        supplier=supplier,
        item_count=1,
        items_json=json.dumps([{"name": "Toor Dal", "supplier_item_id": "TD", "qty": 1}]),
        placed_by_name="boss",
        placed_at=placed_at,
        status=status,
    )
    db.add(row)
    db.commit()
    return row


def _purchase(db, supplier, created_at, received=None):
    db.add(
        PurchaseLogEntry(
            item_id="x",
            item_name="x",
            supplier=supplier,
            quantity_received=1,
            received_date=received or created_at.date(),
            created_at=created_at,
        )
    )
    db.commit()


def _invoice(db, vendor, created_at, submission_type="invoice"):
    n = db.query(InvoiceSubmission).count()
    db.add(
        InvoiceSubmission(
            vendor=vendor,
            submission_type=submission_type,
            original_filename="a.pdf",
            stored_filename=f"a{n}.pdf",
            file_token=f"tok{n}",
            created_at=created_at,
        )
    )
    db.commit()


def _schedule(db, vendor, weekdays, lead_days):
    db.add(
        VendorSchedule(
            vendor=vendor, delivery_weekdays=json.dumps(weekdays), lead_days=lead_days, active=True, created_at=utcnow()
        )
    )
    db.commit()


def _item(item_id, supplier, tier="critical", on_hand=0, daily=2.0):
    labels = {"critical": "Critical", "order_now": "Order now", "can_wait": "Can wait"}
    return {
        "item_id": item_id,
        "name": item_id,
        "supplier": supplier,
        "estimated_order_value": 5.0,
        "shortfall": 10,
        "on_hand_qty": on_hand,
        "avg_daily_demand": daily,
        "urgency": {"tier": tier, "label": labels[tier]},
    }


def _critical_items(prefix, supplier, n=5):
    return [_item(f"{prefix}{i}", supplier) for i in range(n)]


def _by_vendor(groups):
    return {g["vendor"]: g for g in groups}


TODAY = dt.date(2026, 9, 30)  # a Wednesday


def test_placed_order_makes_the_vendor_ordered(db):
    order = _order(db, "Raja Foods", _at(TODAY, 1))
    # A recent delivery for Chetak keeps it active (not "inactive").
    _purchase(db, "Chetak", _at(TODAY - dt.timedelta(days=3)))
    raja = _critical_items("r", "Raja Foods")
    chetak = _critical_items("c", "Chetak")
    candidates = raja + chetak

    groups = group_candidates_by_vendor(db, candidates, TODAY, 7)
    by = _by_vendor(groups)

    g = by["Raja Foods"]
    assert g["urgency"] == {"tier": "ordered", "label": "Ordered"}
    assert g["ordered_order_id"] == order.id
    assert g["ordered_at"] == order.placed_at.isoformat()
    assert g["ordered_until"] == (TODAY + dt.timedelta(days=10)).isoformat()  # lead 7 + 3
    assert g["tier_counts"]["ordered"] == 5 and g["tier_counts"]["critical"] == 0
    for item in g["items"]:
        assert item["urgency"]["tier"] == "ordered" and item["urgency"]["label"] == "Ordered"
        assert item["ordered_at"] == order.placed_at.isoformat() and item["ordered_order_id"] == order.id
        # Stock numbers stay as they were (informational).
        assert item["on_hand_qty"] == 0 and item["shortfall"] == 10

    # The flat candidates list keeps the real status (copies, not mutated).
    assert all(c["urgency"]["tier"] == "critical" and "ordered_at" not in c for c in raja)

    assert by["Chetak"]["urgency"]["tier"] == "critical"
    assert by["Chetak"]["ordered_at"] is None and by["Chetak"]["ordered_order_id"] is None
    # Ordered vendors group after the urgent ones.
    assert [x["vendor"] for x in groups] == ["Chetak", "Raja Foods"]


def test_delivery_confirmed_after_the_order_clears_it(db):
    placed = _at(TODAY - dt.timedelta(days=2))
    _order(db, "Raja Foods", placed)
    # A confirmation from BEFORE the order doesn't count.
    _purchase(db, "Raja Foods", placed - dt.timedelta(days=1))
    assert "raja foods" in ordered_vendors(db, TODAY, 7)

    _purchase(db, "raja foods ", placed + dt.timedelta(hours=20))  # name matched ignoring case/spacing
    assert ordered_vendors(db, TODAY, 7) == {}
    (g,) = group_candidates_by_vendor(db, _critical_items("r", "Raja Foods"), TODAY, 7)
    assert g["urgency"]["tier"] == "critical" and g["ordered_at"] is None


def test_uploaded_invoice_or_toast_receipt_after_the_order_clears_it(db):
    placed = _at(TODAY - dt.timedelta(days=2))
    _order(db, "Chetak", placed)
    _invoice(db, "Chetak", placed + dt.timedelta(hours=5), submission_type="return")  # not a delivery
    assert "chetak" in ordered_vendors(db, TODAY, 7)
    _invoice(db, "chetak", placed + dt.timedelta(hours=6))
    assert ordered_vendors(db, TODAY, 7) == {}

    db.query(InvoiceSubmission).delete()
    db.commit()
    placed_day = TODAY - dt.timedelta(days=2)
    assert "chetak" in ordered_vendors(db, TODAY, 7, {"Chetak": placed_day})  # same day: the earlier delivery
    assert ordered_vendors(db, TODAY, 7, {"Chetak": placed_day + dt.timedelta(days=1)}) == {}


def test_window_expiry_unscheduled_uses_page_lead_time(db):
    placed_day = TODAY - dt.timedelta(days=10)
    _order(db, "Babco", _at(placed_day))
    assert "babco" in ordered_vendors(db, TODAY, 7)  # day 10 = lead 7 + 3: still ordered
    assert ordered_vendors(db, TODAY + dt.timedelta(days=1), 7) == {}  # day 11: back to stock status
    assert "babco" in ordered_vendors(db, TODAY + dt.timedelta(days=1), 8)  # longer lead on the page


def test_window_expiry_scheduled_uses_next_delivery(db):
    # Thursday deliveries, order 1 day ahead. Placed Wednesday -> Thursday's
    # delivery -> ordered through Sunday (1 + 3 days).
    _schedule(db, "Raja Foods", [3], 1)
    _order(db, "Raja Foods", _at(TODAY))
    got = ordered_vendors(db, TODAY, 7)
    assert got["raja foods"]["ordered_until"] == "2026-10-04"
    assert "raja foods" in ordered_vendors(db, dt.date(2026, 10, 4), 7)
    assert ordered_vendors(db, dt.date(2026, 10, 5), 7) == {}


def test_order_placed_after_the_cutoff_waits_for_the_next_delivery(db):
    # Oct 22 (one shared rule - services/order_cycle.py): placed on the
    # Thursday delivery day with that delivery not recorded yet, the order
    # is that Thursday's (a day late - the Vendor Calendar's Sep 17 case),
    # so it holds through Sunday and next week's order is still asked for.
    _schedule(db, "Raja Foods", [3], 1)
    _order(db, "Raja Foods", _at(dt.date(2026, 10, 1)))
    assert ordered_vendors(db, dt.date(2026, 10, 1), 7)["raja foods"]["ordered_until"] == "2026-10-04"


def test_order_placed_after_that_days_delivery_waits_for_the_next_one(db):
    # Once that Thursday's delivery is recorded (here at 9am, order at 10am),
    # the order is for next Thursday: ordered through next Thursday + 3.
    _schedule(db, "Raja Foods", [3], 1)
    db.add(PurchaseLogEntry(item_id="x", item_name="x", supplier="Raja Foods", quantity_received=1,
                            received_date=dt.date(2026, 10, 1), created_at=_at(dt.date(2026, 10, 1), 9)))
    db.commit()
    _order(db, "Raja Foods", _at(dt.date(2026, 10, 1)))
    assert ordered_vendors(db, dt.date(2026, 10, 1), 7)["raja foods"]["ordered_until"] == "2026-10-11"


def test_pdf_only_rows_are_ignored(db):
    _order(db, "Raja Foods", _at(TODAY), status="pdf")
    assert ordered_vendors(db, TODAY, 7) == {}
    (g,) = group_candidates_by_vendor(db, _critical_items("r", "Raja Foods"), TODAY, 7)
    assert g["urgency"]["tier"] != "ordered" and g["ordered_at"] is None


def test_latest_placed_order_counts_and_future_orders_are_ignored(db):
    _order(db, "Raja Foods", _at(TODAY - dt.timedelta(days=20)))
    assert ordered_vendors(db, TODAY, 7) == {}  # old order, window long gone
    newest = _order(db, "Raja Foods", _at(TODAY - dt.timedelta(days=1)))
    assert ordered_vendors(db, TODAY, 7)["raja foods"]["order_id"] == newest.id
    # As of a date before the order was placed, it isn't ordered yet.
    assert ordered_vendors(db, TODAY - dt.timedelta(days=2), 7) == {}


def test_multi_vendor_item_keeps_real_status_under_the_other_vendor(db):
    _order(db, "Raja Foods", _at(TODAY))
    _purchase(db, "Chetak", _at(TODAY - dt.timedelta(days=3)))
    # Listed under both; with no receiving history it goes to its first
    # listed vendor, Chetak, which has no order - so it stays Critical.
    shared = _item("shared", "Chetak;Raja Foods")
    only_raja = _item("raja-only", "Raja Foods")
    groups = _by_vendor(group_candidates_by_vendor(db, [shared, only_raja], TODAY, 7))
    assert [i["urgency"]["tier"] for i in groups["Chetak"]["items"]] == ["critical"]
    assert [i["urgency"]["tier"] for i in groups["Raja Foods"]["items"]] == ["ordered"]
    assert shared["urgency"]["tier"] == "critical" and only_raja["urgency"]["tier"] == "critical"
    # Put the shared item under Raja instead: ordered there, flat copy untouched.
    shared2 = _item("shared", "Raja Foods;Vadilal")
    groups = _by_vendor(group_candidates_by_vendor(db, [shared2], TODAY, 7))
    assert groups["Raja Foods"]["items"][0]["urgency"]["tier"] == "ordered"
    assert shared2["urgency"]["tier"] == "critical"


def test_critical_vendors_alert_skips_ordered_vendors(db, monkeypatch):
    now = dt.datetime(2026, 9, 30, 8, 0, tzinfo=ET)
    _order(db, "Raja Foods", _at(TODAY, 1))
    _purchase(db, "Chetak", _at(TODAY - dt.timedelta(days=3)))
    items = _critical_items("r", "Raja Foods") + _critical_items("c", "Chetak")
    monkeypatch.setattr(nt, "compute_reorder_candidates", lambda *a, **k: [dict(i) for i in items])
    (alert,) = nt.critical_vendors_alerts(db, now)
    assert alert.title == "1 vendor critical" and alert.body == "Chetak"

    _order(db, "Chetak", _at(TODAY, 2))
    assert nt.critical_vendors_alerts(db, now) == []


def test_order_due_alert_and_calendar_skip_an_ordered_vendor(db, monkeypatch):
    # Thursday deliveries, 1 day lead: order due Wednesday Sep 30.
    _schedule(db, "Raja Foods", [3], 1)
    _schedule(db, "Chetak", [3], 1)
    # 5 items: vendors have minimum orders, so fewer is never urgent.
    need = [{"item_id": f"a{i}", "projections": {"7": {"need_to_order": 4}}} for i in range(5)]
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: need)
    now = dt.datetime(2026, 9, 30, 8, 0, tzinfo=ET)
    assert sorted(a.title for a in nt.order_due_alerts(db, now)) == ["Order today: Chetak", "Order today: Raja Foods"]

    order = _order(db, "Raja Foods", _at(TODAY, 1))
    # A placed order for the PREVIOUS cycle (before last week's cutoff) doesn't count.
    _order(db, "Chetak", _at(dt.date(2026, 9, 23), 1))
    assert [a.title for a in nt.order_due_alerts(db, now)] == ["Order today: Chetak"]

    events = vendor_calendar.get_vendor_calendar(db, TODAY, TODAY)["events"]
    by = {e["vendor"]: e for e in events if e["type"] == "order_due"}
    assert by["Raja Foods"]["ordered"] is True and by["Raja Foods"]["urgent"] is False
    assert by["Raja Foods"]["ordered_at"] == order.placed_at.isoformat()
    assert by["Chetak"]["ordered"] is False and by["Chetak"]["urgent"] is True


def test_scheduled_vendor_same_day_delivery_is_the_previous_cycle(db):
    # Orders are often marked placed at 1-3am; with Thursday deliveries and a
    # 1-day lead, a delivery recorded later that Wednesday is last cycle's
    # (it can't be this order), so the vendor stays ordered until Thursday.
    _schedule(db, "Raja Foods", [3], 1)
    placed = _at(TODAY, 1)
    _order(db, "Raja Foods", placed)
    _purchase(db, "Raja Foods", _at(TODAY, 14))
    _invoice(db, "Raja Foods", _at(TODAY, 15))
    assert "raja foods" in ordered_vendors(db, TODAY, 7)
    assert "raja foods" in ordered_vendors(db, TODAY, 7, {"Raja Foods": _at(TODAY, 16)})
    # Thursday's delivery (any source) clears it.
    assert ordered_vendors(db, TODAY + dt.timedelta(days=1), 7, {"Raja Foods": _at(TODAY + dt.timedelta(days=1), 11)}) == {}
    _purchase(db, "Raja Foods", _at(TODAY + dt.timedelta(days=1), 12))
    assert ordered_vendors(db, TODAY + dt.timedelta(days=1), 7) == {}


def test_toast_receipt_timestamp_compared_in_store_time(db):
    # Unscheduled vendor: a Toast receipt clears only if after the order.
    placed = _at(TODAY, 18)  # 6pm store time
    _order(db, "Chetak", placed)
    before = placed - dt.timedelta(hours=2)
    assert "chetak" in ordered_vendors(db, TODAY, 7, {"Chetak": before})
    assert ordered_vendors(db, TODAY, 7, {"Chetak": placed + dt.timedelta(hours=3)}) == {}


def test_toast_receipt_evening_utc_is_dated_in_store_time():
    from app.services.reorder import _as_date, _as_datetime

    # 11:20pm UTC Sep 18 = 7:20pm EDT Sep 18; 02:51 UTC Sep 12 = Sep 11 evening.
    assert _as_date("2026-09-18T23:20:41.037702Z") == dt.date(2026, 9, 18)
    assert _as_date("2026-09-12T02:51:32.1Z") == dt.date(2026, 9, 11)
    assert _as_date("2026-09-12") == dt.date(2026, 9, 12)
    assert _as_datetime("2026-09-12") is None
    assert _as_datetime("2026-09-12T02:51:32Z") == dt.datetime(2026, 9, 12, 2, 51, 32, tzinfo=dt.timezone.utc)


def test_calendar_counts_an_order_for_one_delivery_only(db, monkeypatch):
    # Thursday deliveries, 1-day lead. An order marked placed at 4am on
    # delivery day (Thu Sep 24) is that delivery's, not next week's: the
    # Oct 1 order (due Wed Sep 30) is still due and still alerts.
    _schedule(db, "Chetak", [3], 1)
    # 5 items: vendors have minimum orders, so fewer is never urgent.
    need = [{"item_id": f"a{i}", "projections": {"7": {"need_to_order": 4}}} for i in range(5)]
    monkeypatch.setattr(vendor_calendar, "compute_supplier_projection", lambda *a, **k: need)
    _order(db, "Chetak", _at(dt.date(2026, 9, 24), 4))
    events = vendor_calendar.get_vendor_calendar(db, dt.date(2026, 9, 23), TODAY)["events"]
    by = {e["for_delivery_date"]: e for e in events if e["type"] == "order_due"}
    assert by["2026-09-24"]["ordered"] is True
    assert by["2026-10-01"]["ordered"] is False and by["2026-10-01"]["urgent"] is True
    now = dt.datetime(2026, 9, 30, 8, 0, tzinfo=ET)
    assert [a.title for a in nt.order_due_alerts(db, now)] == ["Order today: Chetak"]


def test_calendar_hides_ordered_order_dues_unless_asked():
    from app.routers.vendor_calendar import _without_ordered_order_dues

    events = [
        {"type": "order_due", "vendor": "Raja Foods", "ordered": True},
        {"type": "order_due", "vendor": "Babco", "ordered": False},
        {"type": "delivery", "vendor": "Raja Foods", "ordered": True},
    ]
    kept = _without_ordered_order_dues({"events": events, "start": "2026-09-30"})
    assert [(e["type"], e["vendor"]) for e in kept["events"]] == [("order_due", "Babco"), ("delivery", "Raja Foods")]
    assert kept["start"] == "2026-09-30"
