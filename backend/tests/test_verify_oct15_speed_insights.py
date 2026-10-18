"""Verifier (Oct 15 batch, SPEED + INSIGHTS): adversarial checks that the
builders' own tests didn't cover. Toast is always the FakeToast of
test_insights.py; nothing is sent."""

import datetime as dt

from app.services import insights_data
from tests.test_insights import YESTERDAY, admin_c, fake, filled  # noqa: F401 - fixtures
from tests.test_insights_backfill import _day, _drop, _use_budget


def test_an_older_day_held_for_the_reserve_is_not_called_used_up(db, filled, admin_c):
    # 3 check calls are left today - kept for the newest days (reserve 4).
    old = YESTERDAY - dt.timedelta(days=10)
    _drop(db, "check", old)
    _use_budget(db, "check", 30 - 3)
    assert insights_data.daily_left(db, "check") == 3
    body = admin_c.get("/api/insights/loss-watch?days=30").json()
    assert body["coverage"]["pending"] == "waiting_budget"
    note = body["note"]
    assert _day(old) in note
    assert "used up" not in note, note
    assert "kept for the newest days" in note
    # The hourly catch-up never reads an older day: no "by 2:20 PM" promise.
    assert note.endswith("added automatically, at the latest with the 4:30 AM update."), note


def test_yesterday_waiting_for_budget_still_says_used_up_and_when(db, filled, admin_c):
    _drop(db, "check", YESTERDAY)
    _use_budget(db, "check")
    note = admin_c.get("/api/insights/loss-watch?days=30").json()["note"]
    assert "today's are used up" in note and "added automatically by" in note


def test_a_complete_page_never_says_filling(db, filled, admin_c):
    # Another report's fill (the hourly catch-up, the nightly) holds the
    # process-wide fill lock. App build 9 shows "Some days are still coming
    # in from Toast" whenever `filling` is true - on a complete page too.
    with insights_data._fill_lock:
        body = admin_c.get("/api/insights/loss-watch?days=7").json()
    assert body["stale"] is False and body["note"] is None
    assert body["filling"] is False


# --------------------------------------------------------------------------- speed: cache never stale

from app.models import VendorSchedule  # noqa: E402
from app.services import report_cache  # noqa: E402
from tests.test_speed_oct15 import REORDER, _vendors, computed, store  # noqa: E402,F401


def test_undo_of_order_already_placed_is_seen_at_once(db, admin_client, store, computed):
    assert _vendors(admin_client.get(REORDER).json())["Raja Foods"]["urgency"]["tier"] == "critical"
    placed = admin_client.post("/api/order-actions", json={"vendor": "Raja Foods", "action": "placed"}).json()
    assert _vendors(admin_client.get(REORDER).json())["Raja Foods"]["urgency"]["tier"] == "ordered"
    order_id = placed.get("order_id") or placed.get("id") or (placed.get("placed") or {}).get("order_id")
    assert order_id, placed
    r = admin_client.delete(f"/api/order-actions/placed/{order_id}")
    assert r.status_code == 200, r.text
    body = admin_client.get(REORDER).json()
    assert _vendors(body)["Raja Foods"]["urgency"]["tier"] == "critical"
    tile = admin_client.get(REORDER, params={"critical_only": 1}).json()
    assert [g["vendor"] for g in tile["vendors"]] == ["Raja Foods"] and tile["candidates"] == []


def test_a_vendor_schedule_change_ends_the_cache(db, admin_client, store, computed):
    admin_client.get(REORDER)
    gen = report_cache.generation()
    db.add(VendorSchedule(vendor="Raja Foods", delivery_weekdays="0", lead_days=1, active=True,
                          created_at=__import__("app.timeutil", fromlist=["utcnow"]).utcnow()))
    db.commit()
    assert report_cache.generation() > gen
    admin_client.get(REORDER)
    assert len(computed) == 2
    # A dismissal write deliberately doesn't (annotation is per request).
    gen = report_cache.generation()
    r = admin_client.post("/api/reorder/critical-dismissals", json={"vendor": "Raja Foods", "kind": "skip_week"})
    assert r.status_code == 200, r.text
    assert report_cache.generation() == gen
