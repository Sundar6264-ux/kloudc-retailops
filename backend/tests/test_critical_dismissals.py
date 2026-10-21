"""Critical vendors: Close / Skip this week / Remind me later
(docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md; services/critical_dismissals.py,
routers/critical_dismissals.py, migration 0047).

1. Time rules at their boundaries in America/New_York, the Nov 1 2026 DST
   change included.
2. Close: back when a NEW item turns critical - not when the same items stay
   critical or some resolve - and after 14 days at most. Supersede, Show
   again (Undo), expiry, a vendor that stops being critical.
3. The API: shapes, validation, permissions (admin-only writes, reads gated
   on reorder_candidates, the demo login refused by the demo guard).
4. The reorder-candidates vendor groups only GAIN critical_dismissed /
   critical_dismissal; the critical-vendors push leaves hidden vendors out.
5. Glossary texts quote the real rules; migration 0047 up / down / up.

The Today report's candidates are synthetic (the `report` fixture) and go
through the real vendor grouping, as in tests/test_ops_ordered_status.py.
"""

import datetime as dt
import os
import pathlib
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest

from app.timeutil import restaurant_today
from app import glossary as g
from app.db import Base
from app.demo_guard import ALLOWLIST, DEMO_READ_ONLY
from app.models import CriticalVendorDismissal, PurchaseLogEntry
from app.routers import ordering as ordering_router
from app.services import critical_dismissals as svc
from app.services import notification_triggers as nt
from tests.conftest import client_for, make_user

BACKEND = pathlib.Path(__file__).resolve().parents[1]
NY = zoneinfo.ZoneInfo("America/New_York")
UTC = dt.timezone.utc
API = "/api/reorder/critical-dismissals"
REORDER = "/api/ordering/reorder-candidates"
TUESDAY = dt.date(2026, 10, 6)


def ny(y, m, d, h=0, mi=0, s=0, fold=0):
    return dt.datetime(y, m, d, h, mi, s, tzinfo=NY, fold=fold)


def in_ny(value):
    return value.astimezone(NY)


# --- 1. Time rules ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected",
    [
        (ny(2026, 10, 6, 10), ny(2026, 10, 12)),  # Tuesday -> next Monday 00:00
        (ny(2026, 10, 11, 0, 0), ny(2026, 10, 12)),  # Sunday -> tomorrow
        (ny(2026, 10, 11, 23, 59, 59), ny(2026, 10, 12)),
        (ny(2026, 10, 12, 0, 0), ny(2026, 10, 19)),  # Monday 00:00 itself -> a week away
        (ny(2026, 10, 12, 0, 1), ny(2026, 10, 19)),
        (ny(2026, 10, 31, 12), ny(2026, 11, 2)),  # the Saturday before DST ends
        (ny(2026, 11, 1, 1, 30, fold=0), ny(2026, 11, 2)),  # 1:30 AM EDT (the hour that repeats)
        (ny(2026, 11, 1, 1, 30, fold=1), ny(2026, 11, 2)),  # 1:30 AM EST
    ],
)
def test_skip_week_runs_to_next_monday_midnight_store_time(now, expected):
    until = svc.skip_week_until(now)
    assert until == expected
    assert until.utcoffset() == dt.timedelta(0)
    assert (in_ny(until).weekday(), in_ny(until).time()) == (0, dt.time(0, 0))


def test_skip_week_across_dst_is_midnight_est():
    # Monday Nov 2 00:00 is EST (UTC-5) = 05:00 UTC, not 04:00.
    assert svc.skip_week_until(ny(2026, 10, 31, 12)) == dt.datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
    assert svc.skip_week_until(ny(2026, 10, 6, 10)) == dt.datetime(2026, 10, 12, 4, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "now,expected",
    [
        (ny(2026, 10, 6, 10, 0), ny(2026, 10, 6, 14, 0)),
        (ny(2026, 10, 6, 1, 0), ny(2026, 10, 6, 5, 0)),
        (ny(2026, 10, 6, 16, 59), ny(2026, 10, 6, 20, 59)),  # 4:59 PM -> 8:59 PM
        (ny(2026, 10, 6, 17, 0), ny(2026, 10, 6, 21, 0)),  # 5:00 PM is not "after 5": 9 PM
        (ny(2026, 10, 6, 17, 0, 1), ny(2026, 10, 7, 9, 0)),  # after 5 PM -> tomorrow 9 AM
        (ny(2026, 10, 6, 17, 1), ny(2026, 10, 7, 9, 0)),  # 5:01 PM
        (ny(2026, 10, 6, 20, 59), ny(2026, 10, 7, 9, 0)),  # 8:59 PM
        (ny(2026, 10, 6, 21, 1), ny(2026, 10, 7, 9, 0)),  # 9:01 PM
        (ny(2026, 10, 6, 23, 59), ny(2026, 10, 7, 9, 0)),
        (ny(2026, 10, 31, 17, 1), ny(2026, 11, 1, 9, 0)),  # tomorrow 9 AM is already EST
        (ny(2026, 11, 1, 0, 30), ny(2026, 11, 1, 3, 30)),  # 4 real hours across the repeated hour
    ],
)
def test_later_today(now, expected):
    assert svc.snooze_until(now, "later_today") == expected


def test_later_today_is_never_past_9pm_minute_by_minute():
    t = ny(2026, 10, 6, 0, 0)
    while in_ny(t).date() == TUESDAY:
        until = in_ny(svc.later_today_until(t))
        if in_ny(t).time() <= dt.time(17, 0):
            assert until.date() == TUESDAY and until.time() <= dt.time(21, 0), t
            assert dt.timedelta(0) < until - t <= dt.timedelta(hours=4), t
        else:
            assert (until.date(), until.time()) == (dt.date(2026, 10, 7), dt.time(9, 0)), t
        t += dt.timedelta(minutes=1)


def test_tomorrow_and_in_3_days_are_9am_store_time():
    assert svc.snooze_until(ny(2026, 10, 6, 10), "tomorrow") == ny(2026, 10, 7, 9)
    assert svc.snooze_until(ny(2026, 10, 6, 0, 1), "tomorrow") == ny(2026, 10, 7, 9)
    assert svc.snooze_until(ny(2026, 10, 6, 23, 59), "tomorrow") == ny(2026, 10, 7, 9)
    assert svc.snooze_until(ny(2026, 10, 6, 10), "in_3_days") == ny(2026, 10, 9, 9)
    # 9 AM stays 9 AM across the DST change: 13:00 UTC in EDT, 14:00 UTC in EST.
    assert svc.snooze_until(ny(2026, 10, 29, 12), "tomorrow") == dt.datetime(2026, 10, 30, 13, tzinfo=UTC)
    assert svc.snooze_until(ny(2026, 10, 31, 20), "tomorrow") == dt.datetime(2026, 11, 1, 14, tzinfo=UTC)
    assert svc.snooze_until(ny(2026, 10, 30, 12), "in_3_days") == dt.datetime(2026, 11, 2, 14, tzinfo=UTC)


def test_pick_a_date():
    early = ny(2026, 10, 6, 8, 59)
    assert svc.snooze_until(early, "date", dt.date(2026, 10, 6)) == ny(2026, 10, 6, 9)  # today, before 9 AM
    with pytest.raises(svc.DismissalError) as err:
        svc.snooze_until(ny(2026, 10, 6, 9, 0), "date", dt.date(2026, 10, 6))  # 9 AM today has come
    assert err.value.status_code == 400
    with pytest.raises(svc.DismissalError):
        svc.snooze_until(early, "date", dt.date(2026, 10, 5))
    # At most 30 days ahead (Nov 5 is in EST: 9 AM = 14:00 UTC).
    assert svc.snooze_until(early, "date", dt.date(2026, 11, 5)) == dt.datetime(2026, 11, 5, 14, tzinfo=UTC)
    with pytest.raises(svc.DismissalError) as err:
        svc.snooze_until(early, "date", dt.date(2026, 11, 6))
    assert "30 days" in err.value.detail
    with pytest.raises(svc.DismissalError):
        svc.snooze_until(early, "date", None)
    with pytest.raises(svc.DismissalError):
        svc.snooze_until(early, "next_year")


def test_ends_label():
    now = ny(2026, 10, 6, 9, 30)
    assert svc.ends_label("snooze", ny(2026, 10, 6, 13, 30), now) == "Until 1:30 PM"
    assert svc.ends_label("snooze", ny(2026, 10, 6, 12, 5), now) == "Until 12:05 PM"
    assert svc.ends_label("snooze", ny(2026, 10, 7, 9), now) == "Until tomorrow 9:00 AM"
    assert svc.ends_label("snooze", ny(2026, 10, 7, 0, 15), now) == "Until tomorrow 12:15 AM"
    assert svc.ends_label("snooze", ny(2026, 10, 9, 9), now) == "Until Fri Oct 9, 9:00 AM"
    assert svc.ends_label("skip_week", ny(2026, 10, 12), now) == "Until Mon Oct 12"
    assert svc.ends_label("close", now + dt.timedelta(days=14), now) == "Until something new turns critical"
    # Store time, not UTC: 01:30 UTC on Oct 7 is 9:30 PM on Oct 6 in New York.
    assert svc.ends_label("snooze", dt.datetime(2026, 10, 7, 1, 30, tzinfo=UTC), now) == "Until 9:30 PM"


# --- The Today report (synthetic candidates, real grouping) -------------------------------------

LABELS = {"critical": "Critical", "order_now": "Order now", "can_wait": "Can wait"}
VENDORS = ("Raja Foods", "Chetak", "Vdyas")


def _item(item_id, supplier, tier):
    return {
        "item_id": item_id,
        "name": f"Item {item_id}",
        "supplier": supplier,
        "category": "Grocery",
        "avg_daily_demand": 2.0,  # 14 a week: a fast seller, so one critical item keeps a vendor critical
        "forecast_over_lead_time": 14,
        "on_hand_qty": 0 if tier == "critical" else 3,
        "shortfall": 10,
        "estimated_order_value": 5.0,
        "inventory_snapshot_date": TUESDAY.isoformat(),
        "urgency": {"tier": tier, "label": LABELS[tier]},
    }


class Report:
    """What compute_reorder_candidates returns, per vendor: the ids that are
    critical / order now. Patched into the service (new dismissals), the
    reorder-candidates route and the critical-vendors push."""

    def __init__(self):
        self.items = []

    def set(self, **vendors):
        """report.set(raja=(["r1", "r2"], ["r3"])) - (critical ids, order-now ids)."""
        names = {"raja": "Raja Foods", "chetak": "Chetak", "vdyas": "Vdyas"}
        self.items = [
            _item(item_id, names[key], tier)
            for key, (critical, order_now) in vendors.items()
            for tier, ids in (("critical", critical), ("order_now", order_now))
            for item_id in ids
        ]

    def candidates(self, *args, **kwargs):
        return [dict(i, urgency=dict(i["urgency"])) for i in self.items]


@pytest.fixture
def report(db, monkeypatch):
    rep = Report()
    for module in (svc, ordering_router, nt):
        monkeypatch.setattr(module, "compute_reorder_candidates", rep.candidates)
    # A delivery on both the test dates and the real today keeps every vendor
    # active (not "inactive") whichever date the report is computed for.
    for vendor in VENDORS:
        for day in {TUESDAY, restaurant_today()}:
            db.add(
                PurchaseLogEntry(
                    item_id="x", item_name="x", supplier=vendor, quantity_received=1, received_date=day,
                    created_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
    db.commit()
    # Raja: 3 critical + 2 order now; Chetak: 3 critical + 2 order now.
    rep.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    return rep


def _today(db, now):
    """The Today report's vendor groups at `now`, annotated (by vendor)."""
    groups = svc.today_report_groups(db, now)
    svc.annotate_vendor_groups(db, groups, now=now, today_report=True)
    return {grp["vendor"]: grp for grp in groups}


def _row(db, row_id):
    db.expire_all()
    return db.get(CriticalVendorDismissal, row_id)


# --- 2. Close, supersede, show again, expiry ----------------------------------------------------


def test_close_stores_the_critical_items_and_hides_the_vendor(db, admin, report):
    now = ny(2026, 10, 6, 10)
    groups = _today(db, now)
    assert groups["Raja Foods"]["urgency"]["tier"] == "critical"
    assert groups["Raja Foods"]["critical_dismissed"] is False

    row, warning = svc.create_dismissal(db, admin, "Raja Foods", "close", reason="ordered", now=now)
    assert warning is None
    assert sorted(svc._stored_ids(row)) == ["r1", "r2", "r3"]
    assert row.until == now + dt.timedelta(days=14)
    groups = _today(db, now + dt.timedelta(minutes=5))
    raja = groups["Raja Foods"]
    assert raja["critical_dismissed"] is True
    # Urgency is untouched: still Critical, every item still listed.
    assert raja["urgency"]["tier"] == "critical" and raja["item_count"] == 5
    assert raja["critical_dismissal"]["id"] == row.id and raja["critical_dismissal"]["kind"] == "close"
    assert groups["Chetak"]["critical_dismissed"] is False


def test_close_stays_hidden_while_the_same_items_stay_critical_or_some_resolve(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    row, _ = svc.create_dismissal(db, admin, "Raja Foods", "close", reason="ordered", now=t0)
    # Same items, next day.
    assert _today(db, t0 + dt.timedelta(days=1))["Raja Foods"]["critical_dismissed"] is True
    # Some resolve (r2 and r3 restocked to "order now").
    report.set(raja=(["r1"], ["r2", "r3", "r4", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    assert _today(db, t0 + dt.timedelta(days=2))["Raja Foods"]["critical_dismissed"] is True
    # All resolve: the vendor isn't critical at all; the close stays recorded.
    report.set(raja=([], ["r1", "r2", "r3", "r4", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    assert _today(db, t0 + dt.timedelta(days=3))["Raja Foods"]["urgency"]["tier"] != "critical"
    # ...and still applies when the same items turn critical again.
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    assert _today(db, t0 + dt.timedelta(days=4))["Raja Foods"]["critical_dismissed"] is True
    assert _row(db, row.id).cleared_at is None


def test_close_comes_back_when_a_new_item_turns_critical(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    row, _ = svc.create_dismissal(db, admin, "Raja Foods", "close", reason="counts_wrong", now=t0)
    report.set(raja=(["r1", "r2", "r3", "r4"], ["r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    t1 = t0 + dt.timedelta(hours=3)
    assert _today(db, t1)["Raja Foods"]["critical_dismissed"] is False
    ended = _row(db, row.id)
    assert ended.cleared_reason == "new_critical_item" and ended.cleared_at == t1
    assert ended.cleared_by_name is None
    # Once back it stays back, even when the new item is restocked again.
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    assert _today(db, t1 + dt.timedelta(hours=1))["Raja Foods"]["critical_dismissed"] is False
    assert svc.list_active(db, t1 + dt.timedelta(hours=1)) == []


def test_a_new_item_with_other_items_resolved_still_brings_it_back(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    row, _ = svc.create_dismissal(db, admin, "Raja Foods", "close", reason="ordered", now=t0)
    report.set(raja=(["r1", "r4"], ["r2", "r3", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    assert _today(db, t0 + dt.timedelta(hours=1))["Raja Foods"]["critical_dismissed"] is False
    assert _row(db, row.id).cleared_reason == "new_critical_item"


def test_only_the_today_report_ends_a_close(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    row, _ = svc.create_dismissal(db, admin, "Raja Foods", "close", reason="ordered", now=t0)
    report.set(raja=(["r1", "r2", "r3", "r4"], ["r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    # Another lookback / lead time shows the stored state, without ending it.
    groups = svc.today_report_groups(db, t0)
    svc.annotate_vendor_groups(db, groups, now=t0 + dt.timedelta(hours=1), today_report=False)
    assert {grp["vendor"]: grp for grp in groups}["Raja Foods"]["critical_dismissed"] is True
    assert _row(db, row.id).cleared_at is None
    assert svc.is_today_report(30, 7) and svc.is_today_report(30, 7, restaurant_today())
    assert not svc.is_today_report(60, 7)
    assert not svc.is_today_report(30, 14)
    assert not svc.is_today_report(30, 7, restaurant_today() - dt.timedelta(days=1))
    assert not svc.is_today_report(30, 7, None, "legacy")


def test_close_ends_after_14_days_at_most(db, admin, report):
    t0 = ny(2026, 10, 25, 12)  # EDT; 14 days later is EST
    row, _ = svc.create_dismissal(db, admin, "Raja Foods", "close", reason="not_ordering", now=t0)
    assert row.until - t0 == dt.timedelta(days=14)
    assert in_ny(row.until) == ny(2026, 11, 8, 11)  # 14 x 24 real hours: 11 AM EST
    assert _today(db, row.until - dt.timedelta(seconds=1))["Raja Foods"]["critical_dismissed"] is True
    assert _today(db, row.until)["Raja Foods"]["critical_dismissed"] is False
    ended = _row(db, row.id)
    assert ended.cleared_reason == "expired" and ended.cleared_at == row.until


def test_snooze_and_skip_week_end_on_time(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    snooze, _ = svc.create_dismissal(db, admin, "Raja Foods", "snooze", preset="later_today", now=t0)
    skip, _ = svc.create_dismissal(db, admin, "Chetak", "skip_week", now=t0)
    assert in_ny(snooze.until) == ny(2026, 10, 6, 14) and in_ny(skip.until) == ny(2026, 10, 12)
    groups = _today(db, ny(2026, 10, 6, 13, 59))
    assert groups["Raja Foods"]["critical_dismissed"] and groups["Chetak"]["critical_dismissed"]
    assert groups["Raja Foods"]["critical_dismissal"]["ends_label"] == "Until 2:00 PM"
    assert groups["Chetak"]["critical_dismissal"]["ends_label"] == "Until Mon Oct 12"
    groups = _today(db, ny(2026, 10, 6, 14, 0))
    assert not groups["Raja Foods"]["critical_dismissed"] and groups["Chetak"]["critical_dismissed"]
    assert _row(db, snooze.id).cleared_reason == "expired" and _row(db, snooze.id).cleared_at == snooze.until
    assert _today(db, ny(2026, 10, 11, 23, 59))["Chetak"]["critical_dismissed"] is True
    assert _today(db, ny(2026, 10, 12, 0, 0))["Chetak"]["critical_dismissed"] is False


def test_skip_stays_recorded_while_the_vendor_is_not_critical(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    skip, _ = svc.create_dismissal(db, admin, "Raja Foods", "skip_week", now=t0)
    report.set(chetak=(["c1", "c2", "c3"], ["c4", "c5"]))  # Raja off the report entirely
    assert "Raja Foods" not in _today(db, t0 + dt.timedelta(days=1))
    assert [d["id"] for d in svc.list_active(db, t0 + dt.timedelta(days=1))] == [skip.id]
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    assert _today(db, ny(2026, 10, 8, 10))["Raja Foods"]["critical_dismissed"] is True


def test_a_new_action_supersedes_the_active_one(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    snooze, _ = svc.create_dismissal(db, admin, "Raja Foods", "snooze", preset="tomorrow", now=t0)
    other = make_user(db, "manager", is_admin=True)
    skip, _ = svc.create_dismissal(db, other, "raja foods", "skip_week", now=t0 + dt.timedelta(minutes=1))
    old = _row(db, snooze.id)
    assert old.cleared_reason == "superseded" and old.cleared_at == t0 + dt.timedelta(minutes=1)
    assert old.cleared_by_name == "manager" and old.cleared_by_user_id == other.id
    assert [d["id"] for d in svc.list_active(db, t0 + dt.timedelta(minutes=2))] == [skip.id]
    assert _today(db, t0 + dt.timedelta(minutes=2))["Raja Foods"]["critical_dismissal"]["id"] == skip.id


def test_an_ended_dismissal_is_recorded_as_ended_not_superseded(db, admin, report):
    t0 = ny(2026, 10, 6, 10)
    snooze, _ = svc.create_dismissal(db, admin, "Raja Foods", "snooze", preset="later_today", now=t0)
    svc.create_dismissal(db, admin, "Raja Foods", "skip_week", now=t0 + dt.timedelta(hours=5))
    assert _row(db, snooze.id).cleared_reason == "expired"
    close, _ = svc.create_dismissal(db, admin, "Chetak", "close", reason="ordered", now=t0)
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), chetak=(["c1", "c2", "c3", "c9"], ["c4", "c5"]))
    svc.create_dismissal(db, admin, "Chetak", "snooze", preset="tomorrow", now=t0 + dt.timedelta(hours=1))
    assert _row(db, close.id).cleared_reason == "new_critical_item"


# --- 3. The API ---------------------------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    """Freeze the dismissals' clock: clock.set(ny(...))."""

    class Clock:
        now = ny(2026, 10, 6, 10)

        def set(self, value):
            self.now = value.astimezone(UTC)

    c = Clock()
    c.set(c.now)
    monkeypatch.setattr(svc, "utcnow", lambda: c.now)
    return c


@pytest.fixture
def staff_r(db):
    return client_for(db, make_user(db, "ravi_r", features=["reorder_candidates"]))


ITEM_KEYS = {
    "id", "vendor", "kind", "kind_label", "reason", "reason_label", "note", "until", "created_by_name", "created_at",
    "ends_label", "critical_item_count",
}


def test_post_close_and_get(admin_client, staff_r, report, clock):
    r = admin_client.post(API, json={"vendor": "  raja foods ", "kind": "close", "reason": "ordered", "note": " PO sent "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == ITEM_KEYS | {"warning"}
    assert body["vendor"] == "Raja Foods"  # the report's own name
    assert (body["kind"], body["kind_label"]) == ("close", "Closed")
    assert (body["reason"], body["reason_label"]) == ("ordered", "Ordered / will order")
    assert body["note"] == "PO sent" and body["warning"] is None
    assert body["critical_item_count"] == 3
    assert body["ends_label"] == "Until something new turns critical"
    assert body["created_by_name"] == "boss"
    assert dt.datetime.fromisoformat(body["until"]) == clock.now + dt.timedelta(days=14)

    # Store-wide: staff see it (read-only), with the choices the actions offer.
    listing = staff_r.get(API).json()
    assert [i["id"] for i in listing["items"]] == [body["id"]]
    assert set(listing["items"][0]) == ITEM_KEYS
    assert listing["can_edit"] is False and "history" not in listing
    assert [r["key"] for r in listing["reasons"]] == ["ordered", "not_ordering", "counts_wrong", "other"]
    assert [r["needs_note"] for r in listing["reasons"]] == [False, False, False, True]
    assert [p["key"] for p in listing["presets"]] == ["later_today", "tomorrow", "in_3_days", "date"]
    assert listing["skip_week"]["ends_label"] == "Until Mon Oct 12"
    assert listing["close_max_days"] == 14
    assert "history" not in staff_r.get(API + "?include_history=1").json()  # admins only


def test_get_presets_follow_the_clock(admin_client, clock):
    clock.set(ny(2026, 10, 6, 16, 59))
    presets = {p["key"]: p for p in admin_client.get(API).json()["presets"]}
    assert presets["later_today"]["ends_label"] == "Until 8:59 PM"
    assert presets["tomorrow"]["ends_label"] == "Until tomorrow 9:00 AM"
    assert presets["in_3_days"]["ends_label"] == "Until Fri Oct 9, 9:00 AM"
    assert (presets["date"]["min_date"], presets["date"]["max_date"]) == ("2026-10-07", "2026-11-05")
    clock.set(ny(2026, 10, 6, 17, 1))
    presets = {p["key"]: p for p in admin_client.get(API).json()["presets"]}
    assert presets["later_today"]["ends_label"] == "Until tomorrow 9:00 AM"
    clock.set(ny(2026, 10, 6, 8, 0))
    presets = {p["key"]: p for p in admin_client.get(API).json()["presets"]}
    assert presets["date"]["min_date"] == "2026-10-06"  # before 9 AM, today still works


@pytest.mark.parametrize(
    "body,status,detail",
    [
        ({"vendor": "Raja Foods", "kind": "nap"}, 400, "Pick Close, Skip this week or Remind me later."),
        ({"vendor": " ", "kind": "skip_week"}, 400, "Pick a vendor."),
        ({"vendor": "Raja Foods", "kind": "close"}, 400, "Pick a reason for closing."),
        ({"vendor": "Raja Foods", "kind": "close", "reason": "bored"}, 400, "Pick a reason for closing."),
        ({"vendor": "Raja Foods", "kind": "close", "reason": "other"}, 400, "Add a note for Other."),
        ({"vendor": "Raja Foods", "kind": "close", "reason": "other", "note": "   "}, 400, "Add a note for Other."),
        ({"vendor": "Raja Foods", "kind": "skip_week", "note": "x" * 501}, 400, None),
        ({"vendor": "Raja Foods", "kind": "snooze"}, 400, None),
        ({"vendor": "Raja Foods", "kind": "snooze", "preset": "someday"}, 400, None),
        ({"vendor": "Raja Foods", "kind": "snooze", "preset": "date"}, 400, "Pick a date."),
        ({"vendor": "Raja Foods", "kind": "snooze", "preset": "date", "date": "2026-11-06"}, 400, None),
        ({"vendor": "Raja Foods", "kind": "snooze", "preset": "date", "date": "2026-10-05"}, 400, None),
        ({"vendor": "Raja Foods", "kind": "snooze", "until": "2026-10-06T09:00:00-04:00"}, 400, None),
        ({"vendor": "Raja Foods", "kind": "snooze", "until": "2026-11-07T09:00:00-05:00"}, 400, None),
        ({"vendor": "Nobody Foods", "kind": "skip_week"}, 404, "Unknown vendor: Nobody Foods"),
    ],
)
def test_post_validation(admin_client, report, clock, db, body, status, detail):
    r = admin_client.post(API, json=body)
    assert r.status_code == status, r.text
    if detail:
        assert r.json()["detail"] == detail
    assert db.query(CriticalVendorDismissal).count() == 0


def test_post_variants(admin_client, report, clock, monkeypatch):
    # The reason's label (any case) is accepted too; "Other" with a note.
    r = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "close", "reason": "STOCK COUNTS ARE WRONG"})
    assert r.status_code == 200 and r.json()["reason"] == "counts_wrong"
    r = admin_client.post(API, json={"vendor": "Chetak", "kind": "close", "reason": "Other", "note": "Owner calls"})
    assert r.status_code == 200 and (r.json()["reason"], r.json()["note"]) == ("other", "Owner calls")
    # A snooze with its own time (naive = store time).
    r = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "snooze", "until": "2026-10-06T15:45:00"})
    assert r.status_code == 200 and r.json()["ends_label"] == "Until 3:45 PM"
    r = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "snooze", "preset": "date", "date": "2026-10-20"})
    assert r.status_code == 200 and r.json()["ends_label"] == "Until Tue Oct 20, 9:00 AM"
    assert r.json()["critical_item_count"] is None and r.json()["reason"] is None


def test_a_vendor_that_isnt_critical_can_still_be_hidden(admin_client, report, clock, monkeypatch):
    # Vdyas: 2 critical items, not enough for a vendor minimum order -> not critical.
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), vdyas=(["v1", "v2"], []))
    r = admin_client.post(API, json={"vendor": "Vdyas", "kind": "skip_week"})
    assert r.status_code == 200
    assert r.json()["warning"] == svc.NOT_CRITICAL_WARNING
    # A supplier from the latest upload with nothing on the report at all.
    monkeypatch.setattr(svc, "list_suppliers", lambda db: ["Mehta Foods"])
    r = admin_client.post(API, json={"vendor": "mehta foods", "kind": "close", "reason": "not_ordering"})
    assert r.status_code == 200 and r.json()["vendor"] == "Mehta Foods"
    # A Close doesn't promise to stay hidden: anything critical is new for it.
    assert r.json()["warning"] == svc.NOT_CRITICAL_CLOSE_WARNING and r.json()["critical_item_count"] == 0
    assert {i["vendor"] for i in admin_client.get(API).json()["items"]} == {"Vdyas", "Mehta Foods"}


def test_show_again(admin_client, report, clock, db):
    created = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "skip_week", "note": "Out of town"}).json()
    r = admin_client.delete(f"{API}/{created['id']}")
    assert r.status_code == 204 and r.content == b""
    row = _row(db, created["id"])
    assert row.cleared_reason == "show_again" and row.cleared_by_name == "boss" and row.cleared_at == clock.now
    assert admin_client.get(API).json()["items"] == []
    # Again: nothing to do (204); an unknown id: 404.
    assert admin_client.delete(f"{API}/{created['id']}").status_code == 204
    assert _row(db, created["id"]).cleared_at == clock.now
    assert admin_client.delete(f"{API}/99999").status_code == 404


def test_show_again_ends_every_active_dismissal_of_the_vendor(admin_client, report, clock, db):
    # Two admins hid the vendor at the same moment (a race): Show again on
    # either one brings the vendor back.
    for _ in range(2):
        db.add(
            CriticalVendorDismissal(
                vendor="Raja Foods", kind="snooze", until=clock.now + dt.timedelta(hours=3),
                created_by_name="boss", created_at=clock.now,
            )
        )
    db.add(CriticalVendorDismissal(vendor="Chetak", kind="skip_week", until=clock.now + dt.timedelta(days=2),
                                   created_at=clock.now))
    db.commit()
    first = db.query(CriticalVendorDismissal).filter_by(vendor="Raja Foods").first()
    assert admin_client.delete(f"{API}/{first.id}").status_code == 204
    assert [i["vendor"] for i in admin_client.get(API).json()["items"]] == ["Chetak"]


def test_undo_after_a_newer_action_leaves_it_in_place(admin_client, report, clock):
    first = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "snooze", "preset": "tomorrow"}).json()
    second = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "skip_week"}).json()
    assert admin_client.delete(f"{API}/{first['id']}").status_code == 204
    assert [i["id"] for i in admin_client.get(API).json()["items"]] == [second["id"]]


def test_history_for_admins(admin_client, report, clock, db):
    old = CriticalVendorDismissal(
        vendor="Chetak", kind="skip_week", until=clock.now - dt.timedelta(days=61), created_at=clock.now - dt.timedelta(days=65)
    )
    db.add(old)
    db.commit()
    snooze = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "snooze", "preset": "later_today"}).json()
    clock.set(ny(2026, 10, 6, 11))
    skip = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "skip_week"}).json()
    clock.set(ny(2026, 10, 6, 12))
    close = admin_client.post(API, json={"vendor": "Chetak", "kind": "close", "reason": "ordered"}).json()
    admin_client.delete(f"{API}/{close['id']}")
    body = admin_client.get(API + "?include_history=1").json()
    assert body["can_edit"] is True
    history = body["history"]
    assert [h["id"] for h in history] == [close["id"], skip["id"], snooze["id"]]  # newest first, 60 days only
    by = {h["id"]: h for h in history}
    assert by[skip["id"]]["active"] is True and by[skip["id"]]["cleared_reason"] is None
    assert by[snooze["id"]]["cleared_reason"] == "superseded"
    assert by[snooze["id"]]["cleared_reason_label"] == "Replaced by a newer action"
    assert by[close["id"]]["cleared_reason"] == "show_again" and by[close["id"]]["cleared_by_name"] == "boss"
    assert by[close["id"]]["active"] is False and by[close["id"]]["cleared_at"]
    assert [i["id"] for i in body["items"]] == [skip["id"]]
    # The 65-day-old one ended long ago (settled as expired), outside the 60 days.
    assert _row(db, old.id).cleared_reason == "expired"


def test_permissions(db, admin_client, anon_client, staff_r, report, clock):
    nobody = client_for(db, make_user(db, "nobody"))
    demo = client_for(db, make_user(db, "app_store_review", features=["reorder_candidates"]))
    body = {"vendor": "Raja Foods", "kind": "skip_week"}
    # Logged out.
    assert anon_client.get(API).status_code == 401
    assert anon_client.post(API, json=body).status_code == 401
    assert anon_client.delete(f"{API}/1").status_code == 401
    # Reads need Reorder Candidates.
    assert nobody.get(API).status_code == 403
    assert staff_r.get(API).status_code == 200
    # Writes are admin-only, even with the feature.
    r = staff_r.post(API, json=body)
    assert r.status_code == 403 and r.json()["detail"] == "Admin privileges required"
    created = admin_client.post(API, json=body).json()
    assert staff_r.delete(f"{API}/{created['id']}").status_code == 403
    # The demo login can look, never change (not in the demo guard's allowlist).
    assert demo.get(API).status_code == 200
    for r in (demo.post(API, json=body), demo.delete(f"{API}/{created['id']}")):
        assert r.status_code == 403 and r.json() == {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY}
    assert ("POST", API) not in ALLOWLIST
    assert ("DELETE", API + "/{dismissal_id}") not in ALLOWLIST
    assert [i["id"] for i in admin_client.get(API).json()["items"]] == [created["id"]]
    assert db.query(CriticalVendorDismissal).count() == 1


# --- 4. The reorder report and the push ---------------------------------------------------------


def _strip(body):
    return [{k: v for k, v in grp.items() if k not in ("critical_dismissed", "critical_dismissal")} for grp in body["vendors"]]


def test_reorder_candidates_only_gain_fields(admin_client, staff_r, report, clock):
    before = admin_client.get(REORDER).json()
    assert before["vendors"] and all(
        grp["critical_dismissed"] is False and grp["critical_dismissal"] is None for grp in before["vendors"]
    )
    created = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "skip_week"}).json()
    after = staff_r.get(REORDER).json()  # store-wide: everyone sees it hidden
    # Nothing removed, reordered or re-tiered; candidates unchanged.
    assert _strip(after) == _strip(before)
    assert after["candidates"] == before["candidates"]
    by = {grp["vendor"]: grp for grp in after["vendors"]}
    raja = by["Raja Foods"]
    assert raja["critical_dismissed"] is True and raja["urgency"]["tier"] == "critical"
    d = raja["critical_dismissal"]
    assert {"id", "kind", "ends_label", "created_by_name"} <= set(d)
    assert (d["id"], d["kind"], d["ends_label"], d["created_by_name"]) == (
        created["id"], "skip_week", "Until Mon Oct 12", "boss",
    )
    assert by["Chetak"]["critical_dismissed"] is False and by["Chetak"]["critical_dismissal"] is None


def test_reorder_candidates_end_a_close_only_at_the_today_settings(admin_client, report, clock, db):
    created = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "close", "reason": "ordered"}).json()
    report.set(raja=(["r1", "r2", "r3", "r4"], ["r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    other = admin_client.get(REORDER + "?lookback_days=60&lead_time_days=7").json()
    assert {grp["vendor"]: grp for grp in other["vendors"]}["Raja Foods"]["critical_dismissed"] is True
    assert _row(db, created["id"]).cleared_at is None
    today = admin_client.get(REORDER).json()
    assert {grp["vendor"]: grp for grp in today["vendors"]}["Raja Foods"]["critical_dismissed"] is False
    assert _row(db, created["id"]).cleared_reason == "new_critical_item"


def test_reorder_candidates_survive_a_broken_dismissal_lookup(admin_client, report, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "_settle", broken)
    r = admin_client.get(REORDER)
    assert r.status_code == 200
    assert all(grp["critical_dismissed"] is False for grp in r.json()["vendors"])


def test_push_leaves_hidden_vendors_out(db, admin, report):
    now = ny(2026, 10, 6, 9, 0)
    (alert,) = nt.critical_vendors_alerts(db, now)
    assert alert.title == "2 vendors critical" and set(alert.body.split(", ")) == {"Raja Foods", "Chetak"}
    # Raja snoozed until Thu Oct 8, 9 AM.
    svc.create_dismissal(db, admin, "Raja Foods", "snooze", preset="in_3_days", now=ny(2026, 10, 5, 18))
    (alert,) = nt.critical_vendors_alerts(db, now)
    assert alert.title == "1 vendor critical" and alert.body == "Chetak"
    close, _ = svc.create_dismissal(db, admin, "Chetak", "close", reason="ordered", now=ny(2026, 10, 5, 18))
    assert nt.critical_vendors_alerts(db, now) == []  # every critical vendor hidden: no push
    # Something new turns critical for Chetak: it's back in the push (Raja still snoozed).
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), chetak=(["c1", "c2", "c3", "c4"], ["c5"]))
    (alert,) = nt.critical_vendors_alerts(db, ny(2026, 10, 7, 9, 0))
    assert alert.title == "1 vendor critical" and alert.body == "Chetak"
    assert _row(db, close.id).cleared_reason == "new_critical_item"


def test_push_after_a_snooze_ends(db, admin, report):
    svc.create_dismissal(db, admin, "Raja Foods", "snooze", preset="tomorrow", now=ny(2026, 10, 5, 18))
    svc.create_dismissal(db, admin, "Chetak", "skip_week", now=ny(2026, 10, 5, 18))
    assert nt.critical_vendors_alerts(db, ny(2026, 10, 6, 8, 59)) == []
    (alert,) = nt.critical_vendors_alerts(db, ny(2026, 10, 6, 9, 0))  # tomorrow 9 AM has come
    assert alert.body == "Raja Foods"


# --- 5. Glossary and migration ------------------------------------------------------------------


def _text(key):
    term = g.TERMS_BY_KEY[key]
    return f"{term['definition']} {term['rule'] or ''}"


def test_glossary_terms_quote_the_rules():
    for key in ("critical_close", "critical_skip_week", "critical_snooze"):
        assert g.TERMS_BY_KEY[key]["category"] == "reorder"
    assert f"{svc.CLOSE_MAX_DAYS} days" in _text("critical_close")
    assert "Monday at midnight" in _text("critical_skip_week")
    snooze = _text("critical_snooze")
    for part in (
        f"{svc.LATER_TODAY_HOURS} hours", "never past 9 PM", "after 5 PM", "tomorrow 9 AM",
        f"In {svc.SNOOZE_IN_DAYS} days", f"up to {svc.SNOOZE_MAX_DAYS_AHEAD} days ahead",
    ):
        assert part in snooze, part
    # The texts are built from these; the rules above are tested against them.
    assert (svc.LATER_TODAY_LATEST, svc.LATER_TODAY_CUTOFF, svc.SNOOZE_TIME) == (
        dt.time(21, 0), dt.time(17, 0), dt.time(9, 0),
    )
    assert "left out" in _text("home_critical")
    # The Today report here is the push's (and the app tile's) report.
    assert (svc.TODAY_LOOKBACK_DAYS, svc.TODAY_LEAD_TIME_DAYS) == (nt.REORDER_LOOKBACK_DAYS, nt.REORDER_LEAD_TIME_DAYS)


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)


def _copy(src, dst):
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


@pytest.mark.parametrize("which", ["fresh", "dev"])
def test_migration_0047_up_down_up(tmp_path, which):
    db_file = tmp_path / f"{which}.db"
    if which == "dev":
        src = BACKEND / "origin-dev.db"
        if not src.exists():
            pytest.skip("no dev database here")
        _copy(src, db_file)
    _alembic(db_file, "upgrade", "0046")
    # A copy already at 0047 or later (dev once it's migrated, or synced from
    # a migrated prod) steps back to 0046 here; "upgrade 0046" leaves it.
    _alembic(db_file, "downgrade", "0046")
    con = sqlite3.connect(db_file)
    before = _tables(con)
    con.close()
    assert "critical_vendor_dismissals" not in before

    _alembic(db_file, "upgrade", "0047")
    con = sqlite3.connect(db_file)
    try:
        migrated = {r[1]: r for r in con.execute("PRAGMA table_info(critical_vendor_dismissals)")}
        assert set(migrated) == {c.name for c in Base.metadata.tables["critical_vendor_dismissals"].columns}
        for name in ("vendor", "kind", "until", "created_at"):
            assert migrated[name][3] == 1, f"{name} is NOT NULL"
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'critical_vendor_dismissals'").fetchone()[0]
        assert "AUTOINCREMENT" in sql, "Show again takes an id: ids must never be reused"
        indexes = {r[1] for r in con.execute("PRAGMA index_list(critical_vendor_dismissals)")}
        assert {
            "ix_critical_vendor_dismissals_vendor", "ix_critical_vendor_dismissals_created_at",
            "ix_critical_vendor_dismissals_cleared_at",
        } <= indexes
        con.execute(
            "INSERT INTO critical_vendor_dismissals (vendor, kind, until, created_at) "
            "VALUES ('Raja Foods', 'skip_week', '2026-10-12 04:00:00', '2026-10-06 14:00:00')"
        )
        con.commit()
    finally:
        con.close()

    _alembic(db_file, "downgrade", "0046")
    con = sqlite3.connect(db_file)
    try:
        assert _tables(con) == before
    finally:
        con.close()

    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        assert "critical_vendor_dismissals" in _tables(con)
        assert con.execute("SELECT count(*) FROM critical_vendor_dismissals").fetchone() == (0,)
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] >= "0047"
    finally:
        con.close()
