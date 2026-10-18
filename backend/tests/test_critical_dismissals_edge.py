"""Critical vendors hidden from Today: edge cases found by the verifier pass
(Oct 6) - see tests/test_critical_dismissals.py for the main rules.

1. A vendor Toast spells two ways ("Vdyas" / "VDYAS") is two groups in the
   report but one vendor for a dismissal: a Close stores and compares the
   critical items of both.
2. Closing a vendor that isn't critical: the warning doesn't promise it
   stays hidden (a Close comes back once something new turns critical).
3. Two admins hiding the same vendor at once leave ONE active dismissal.
4. GET's can_edit is false for the App Review demo login, even as an admin.
5. A report worked out for the server's date, when that isn't the store's
   date, doesn't end a Close.
"""

import datetime as dt
import threading
import time

import pytest

from app.db import SessionLocal
from app.models import CriticalVendorDismissal, PurchaseLogEntry, User
from app.routers import ordering as ordering_router
from app.services import critical_dismissals as svc
from app.services import notification_triggers as nt
from tests.conftest import client_for, make_user
from tests.test_critical_dismissals import (  # noqa: F401  (clock, report: fixtures)
    API,
    REORDER,
    TUESDAY,
    UTC,
    _item,
    _row,
    _today,
    clock,
    ny,
    report,
)


def _deliveries(db, *vendors):
    """Recent deliveries keep each spelling's group active (not inactive)."""
    for vendor in vendors:
        for day in {TUESDAY, dt.date.today()}:
            db.add(
                PurchaseLogEntry(
                    item_id="x", item_name="x", supplier=vendor, quantity_received=1, received_date=day,
                    created_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
    db.commit()


@pytest.fixture
def two_spellings(db, monkeypatch):
    """The Today report's candidates, settable: two_spellings.items = [...]."""

    class Candidates:
        items = []

        def __call__(self, *a, **k):
            return [dict(i, urgency=dict(i["urgency"])) for i in self.items]

    c = Candidates()
    for module in (svc, ordering_router, nt):
        monkeypatch.setattr(module, "compute_reorder_candidates", c)
    _deliveries(db, "Vdyas", "VDYAS")
    return c


def _spelled_two_ways(vdyas=("v1", "v2", "v3"), caps=("w1",)):
    """The critical items under "Vdyas" and under "VDYAS"; each spelling also
    has 5 order-now items, so both groups are big enough to be Critical (every
    item sells 14 a week: one critical item is a critical fast seller)."""
    return (
        [_item(i, "Vdyas", "critical") for i in vdyas]
        + [_item(f"vo{n}", "Vdyas", "order_now") for n in range(5)]
        + [_item(i, "VDYAS", "critical") for i in caps]
        + [_item(f"wo{n}", "VDYAS", "order_now") for n in range(5)]
    )


# --- 1. One vendor, two spellings -----------------------------------------------------------------


def test_a_close_covers_every_spelling_of_the_vendor(db, admin, two_spellings):
    two_spellings.items = _spelled_two_ways()
    t0 = ny(2026, 10, 6, 10)
    groups = _today(db, t0)
    assert groups["Vdyas"]["urgency"]["tier"] == groups["VDYAS"]["urgency"]["tier"] == "critical"

    row, warning = svc.create_dismissal(db, admin, "vdyas", "close", reason="ordered", now=t0)
    assert warning is None
    assert row.vendor == "Vdyas"  # the most urgent spelling
    # Was {"w1"}: only the critical items of the group listed last.
    assert svc._stored_ids(row) == {"v1", "v2", "v3", "w1"}
    groups = _today(db, t0 + dt.timedelta(minutes=5))
    assert groups["Vdyas"]["critical_dismissed"] and groups["VDYAS"]["critical_dismissed"]
    assert _row(db, row.id).cleared_at is None


def test_the_spellings_trading_places_doesnt_end_a_close(db, admin, two_spellings):
    # "VDYAS" (4 critical) is listed before "Vdyas" (3) when it's closed...
    two_spellings.items = _spelled_two_ways(caps=("w1", "w2", "w3", "w4"))
    t0 = ny(2026, 10, 6, 10)
    row, _ = svc.create_dismissal(db, admin, "Vdyas", "close", reason="ordered", now=t0)
    assert row.vendor == "VDYAS"
    # ...then w2-w4 are restocked and "Vdyas" is listed first. Nothing new is
    # critical, so it stays hidden (it used to come back here).
    two_spellings.items = _spelled_two_ways(caps=("w1",))
    groups = _today(db, t0 + dt.timedelta(hours=1))
    assert groups["Vdyas"]["critical_dismissed"] and groups["VDYAS"]["critical_dismissed"]
    assert _row(db, row.id).cleared_at is None


@pytest.mark.parametrize("new", [{"vdyas": ("v1", "v2", "v3", "v4")}, {"caps": ("w1", "w9")}])
def test_a_new_critical_item_under_either_spelling_brings_a_close_back(db, admin, two_spellings, new):
    two_spellings.items = _spelled_two_ways()
    t0 = ny(2026, 10, 6, 10)
    row, _ = svc.create_dismissal(db, admin, "Vdyas", "close", reason="ordered", now=t0)
    two_spellings.items = _spelled_two_ways(**new)
    groups = _today(db, t0 + dt.timedelta(hours=1))
    assert not groups["Vdyas"]["critical_dismissed"] and not groups["VDYAS"]["critical_dismissed"]
    assert _row(db, row.id).cleared_reason == "new_critical_item"


# --- 2. Closing a vendor that isn't critical ---------------------------------------------------


def test_closing_a_vendor_that_isnt_critical_doesnt_promise_it_stays_hidden(admin_client, report, clock):
    # Vdyas: order-now items only, nothing critical.
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), vdyas=([], ["v1", "v2"]))
    closed = admin_client.post(API, json={"vendor": "Vdyas", "kind": "close", "reason": "ordered"}).json()
    assert closed["warning"] == svc.NOT_CRITICAL_CLOSE_WARNING
    assert "stays hidden" not in closed["warning"]
    assert "something new turns critical" in closed["warning"]
    # Skip this week / Remind me later do stay hidden through a turn to critical.
    skip = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "skip_week"}).json()
    assert skip["warning"] is None  # Raja is critical
    snooze = admin_client.post(API, json={"vendor": "Vdyas", "kind": "snooze", "preset": "tomorrow"}).json()
    assert snooze["warning"] == svc.NOT_CRITICAL_WARNING

    # What the close warning says is what happens.
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), vdyas=([], ["v1", "v2"]))
    reclose = admin_client.post(API, json={"vendor": "Vdyas", "kind": "close", "reason": "ordered"}).json()
    report.set(raja=(["r1", "r2", "r3"], ["r4", "r5"]), vdyas=(["v1", "v2", "v3"], ["v4", "v5"]))
    clock.set(ny(2026, 10, 6, 11))
    vendors = {g["vendor"]: g for g in admin_client.get(REORDER).json()["vendors"]}
    assert vendors["Vdyas"]["urgency"]["tier"] == "critical"
    assert vendors["Vdyas"]["critical_dismissed"] is False
    assert reclose["critical_item_count"] == 0


# --- 3. Two admins at once -------------------------------------------------------------------------


def test_two_admins_hiding_a_vendor_at_once_leave_one_active(db, report, monkeypatch):
    first = make_user(db, "boss1", is_admin=True)
    second = make_user(db, "boss2", is_admin=True)
    # Both ~3 s reports finish together, and reading the open dismissals is
    # slow: without the write lock both requests see none and both add one.
    both_ready = threading.Barrier(2)
    real_report, real_rows = svc.today_report_groups, svc._open_rows

    def report_groups(db_, now=None):
        out = real_report(db_, now)
        both_ready.wait(timeout=10)
        return out

    def open_rows(db_):
        rows = real_rows(db_)
        if threading.current_thread() is not threading.main_thread():
            time.sleep(0.3)
        return rows

    monkeypatch.setattr(svc, "today_report_groups", report_groups)
    monkeypatch.setattr(svc, "_open_rows", open_rows)
    errors = []

    def hide(user_id, kind):
        session = SessionLocal()
        try:
            extra = {"reason": "ordered"} if kind == "close" else {}
            svc.create_dismissal(session, session.get(User, user_id), "Raja Foods", kind, now=ny(2026, 10, 6, 10), **extra)
        except Exception as err:  # noqa: BLE001
            errors.append(repr(err))
        finally:
            session.close()

    threads = [threading.Thread(target=hide, args=(first.id, "close")), threading.Thread(target=hide, args=(second.id, "skip_week"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    monkeypatch.setattr(svc, "_open_rows", real_rows)

    db.expire_all()
    rows = db.query(CriticalVendorDismissal).order_by(CriticalVendorDismissal.id).all()
    assert len(rows) == 2
    older, newer = rows
    assert [d["id"] for d in svc.list_active(db, ny(2026, 10, 6, 10, 1))] == [newer.id]
    assert older.cleared_reason == "superseded" and older.cleared_by_name == newer.created_by_name


# --- 4. The demo login ---------------------------------------------------------------------------


def test_can_edit_is_false_for_the_demo_login_even_as_an_admin(db, admin_client, report, clock):
    demo = client_for(db, make_user(db, "app_store_review", is_admin=True))
    assert demo.get(API).json()["can_edit"] is False
    assert admin_client.get(API).json()["can_edit"] is True
    r = demo.post(API, json={"vendor": "Raja Foods", "kind": "skip_week"})
    assert r.status_code == 403  # what can_edit says: the demo guard refuses it


# --- 5. Server date vs store date --------------------------------------------------------------


def test_a_report_for_the_servers_date_not_the_stores_doesnt_end_a_close(admin_client, report, clock, db, monkeypatch):
    created = admin_client.post(API, json={"vendor": "Raja Foods", "kind": "close", "reason": "ordered"}).json()
    report.set(raja=(["r1", "r2", "r3", "r4"], ["r5"]), chetak=(["c1", "c2", "c3"], ["c4", "c5"]))
    # The endpoint's default as_of is the server's date (dt.date.today()); a
    # server clock in another zone can be on another day than the store.
    real_today = svc.restaurant_today
    monkeypatch.setattr(svc, "restaurant_today", lambda: dt.date.today() - dt.timedelta(days=1))
    raja = {g["vendor"]: g for g in admin_client.get(REORDER).json()["vendors"]}["Raja Foods"]
    assert raja["critical_dismissed"] is True
    assert _row(db, created["id"]).cleared_at is None
    # On the store's day it is the Today report again: r4 is new, it's back.
    monkeypatch.setattr(svc, "restaurant_today", real_today)
    raja = {g["vendor"]: g for g in admin_client.get(REORDER).json()["vendors"]}["Raja Foods"]
    assert raja["critical_dismissed"] is False
    assert _row(db, created["id"]).cleared_reason == "new_critical_item"
