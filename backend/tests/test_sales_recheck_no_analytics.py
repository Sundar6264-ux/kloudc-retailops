"""The sales re-check without Toast Analytics keys (pre-production 2026-10-21,
docs/PARITY_INVESTIGATION.md).

Reproduces the pilot's parity gap on a synthetic store: Toast changed closed
business days after the nightly sync (lines added to a closed check, a whole
order comped to 0 later, lines re-priced, a late payment) and a store with no
Analytics keys never looked at those days again. The re-check must pick every
one of them up, once a day, and say plainly that the keys are missing. No real
Toast: the Orders API fetch is monkeypatched.
"""

import datetime as dt

import pytest

from app.config import get_settings
from app.models import JobRun, Order
from app.services import sales_accuracy, sales_sync

TODAY = dt.date(2026, 10, 20)  # the closed business "today" of the run
D15 = dt.date(2026, 10, 15)
D18 = dt.date(2026, 10, 18)
D19 = dt.date(2026, 10, 19)
PAID = "2026-10-18T18:00:00.000+0000"


def _order(guid, sels, paid=PAID):
    return {
        "guid": guid,
        "openedDate": PAID,
        "createdDate": PAID,
        "paidDate": paid,
        "checks": [{"guid": "c-" + guid, "paidDate": paid, "selections": sels}],
    }


def _sel(guid, price, qty=1.0, tax=0.0):
    return {"guid": guid, "item": {"guid": "i-" + guid}, "displayName": guid, "price": price, "quantity": qty,
            "tax": tax, "voided": False, "modifiers": []}


@pytest.fixture
def toast(monkeypatch):
    """No Analytics keys; Toast orders per business date from a dict."""
    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "")
    monkeypatch.setattr(s, "toast_analytics_client_secret", "")
    state = {"orders": {}, "fetches": []}

    def fake_fetch(d):
        state["fetches"].append(d)
        return state["orders"].get(d, [])

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", fake_fetch)
    monkeypatch.setattr(sales_accuracy, "_sleep", lambda s: None)
    return state


def _net(db, day):
    return round(sum(o.net_price or 0 for o in db.query(Order).filter_by(business_date=day, voided=False)), 2)


def _rechecks(db):
    return db.query(JobRun).filter_by(job_name="sales_recheck").order_by(JobRun.id).all()


def test_late_toast_edits_on_closed_days_are_picked_up_without_analytics(db, toast):
    # The nightly sync, as it ran on each night.
    toast["orders"][D15] = [_order("c", [_sel("c1", 20.0)])]
    toast["orders"][D18] = [
        _order("a", [_sel(f"a{i}", 17.0) for i in range(9)]),
        _order("b", [_sel("b1", 0.99), _sel("b2", 0.99), _sel("b3", 5.0)]),
    ]
    for d in (D15, D18):
        sales_sync.sync_sales_for_date(db, d)
    assert _net(db, D15) == 20.0 and _net(db, D18) == 9 * 17.0 + 0.99 * 2 + 5.0

    # Days later Toast changes them: 3 lines added to the closed check C;
    # order A fully comped (net 0, not voided); order B gets 2 new lines (one
    # taxed) and 2 lines re-priced 0.99 -> 0.94.
    toast["orders"][D15] = [_order("c", [_sel("c1", 20.0), _sel("c2", 4.0), _sel("c3", 4.0), _sel("c4", 4.4)])]
    toast["orders"][D18] = [
        _order("a", [_sel(f"a{i}", 0.0) for i in range(9)]),
        _order("b", [_sel("b1", 0.94), _sel("b2", 0.94), _sel("b3", 5.0), _sel("b4", 4.0, tax=0.48), _sel("b5", 6.44)]),
    ]
    toast["fetches"].clear()

    job = sales_accuracy.run_sales_repull(db, today=TODAY)

    assert job.job_name == "sales_recheck" and job.status == "success"
    assert job.detail.startswith("Daily re-check without Toast Analytics keys")
    # Only days synced before are re-synced (never-synced days are the nightly's).
    assert sorted(toast["fetches"]) == [D15, D18]
    db.expire_all()
    assert _net(db, D15) == 32.4  # +12.40, the 10-15 gap
    assert _net(db, D18) == round(0.94 * 2 + 5.0 + 4.0 + 6.44, 2)
    assert "2026-10-15 +3 line(s), net +12.40" in job.detail
    assert "2026-10-18 +2 line(s), net -" in job.detail
    assert "2 changed" in job.detail
    tax = sum(o.tax_amount or 0 for o in db.query(Order).filter_by(business_date=D18))
    assert round(tax, 2) == 0.48


def test_the_daily_recheck_runs_once_per_day_then_only_unpaid_days(db, toast):
    toast["orders"][D18] = [_order("p", [_sel("p1", 10.0)])]
    toast["orders"][D19] = [_order("u", [_sel("u1", 40.0)], paid=None)]
    sales_sync.sync_sales_for_date(db, D18)
    sales_sync.sync_sales_for_date(db, D19)
    toast["fetches"].clear()

    sales_accuracy.run_sales_repull(db, today=TODAY)  # 06:30: the daily pass
    assert sorted(toast["fetches"]) == [D18, D19]
    toast["fetches"].clear()

    sales_accuracy.run_sales_repull(db, today=TODAY)  # 12:30: unpaid synced moments ago -> nothing
    assert toast["fetches"] == []
    assert _rechecks(db)[-1].detail.startswith("Re-check without Toast Analytics keys")

    # 6+ hours later the payment has landed in Toast.
    for job in db.query(JobRun).filter_by(job_name=sales_sync.JOB_NAME, business_date=D19):
        job.finished_at = job.finished_at - dt.timedelta(hours=7)
    db.commit()
    toast["orders"][D19] = [_order("u", [_sel("u1", 40.0)])]
    last = sales_accuracy.run_sales_repull(db, today=TODAY)
    assert toast["fetches"] == [D19]
    assert "1 line(s) now paid" in last.detail
    db.expire_all()
    assert db.query(Order).filter_by(toast_selection_guid="u1").one().paid_at is not None

    # The next closed business day: the daily pass again.
    toast["fetches"].clear()
    sales_accuracy.run_sales_repull(db, today=TODAY + dt.timedelta(days=1))
    assert sorted(toast["fetches"]) == [D18, D19]


def test_a_failed_resync_is_recorded_not_raised(db, toast, monkeypatch):
    toast["orders"][D18] = [_order("p", [_sel("p1", 10.0)])]
    sales_sync.sync_sales_for_date(db, D18)

    def down(d):
        raise RuntimeError("Toast is down")

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", down)
    job = sales_accuracy.run_sales_repull(db, today=TODAY)
    assert job.status == "failed" and "1 failed" in job.detail


def test_the_accuracy_report_and_status_say_the_keys_are_missing(db, toast):
    sales_accuracy.run_sales_repull(db, today=TODAY)
    status = sales_accuracy.recheck_status(db)
    assert status["analytics"] is False
    assert status["warning"] == "Sales double-check is limited: Toast Analytics keys are missing"
    assert status["last_recheck"]["detail"].startswith("Daily re-check")
    report = sales_accuracy.accuracy_report(db)
    assert report["configured"] is False and report["warning"] == status["warning"]


def test_with_analytics_keys_the_recheck_never_runs(db, toast, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "client-id")
    monkeypatch.setattr(s, "toast_analytics_client_secret", "x")
    monkeypatch.setattr(s, "toast_restaurant_guid", "rest-guid")
    from app.pos.adapters.toast import analytics as toast_analytics

    monkeypatch.setattr(toast_analytics, "menu_day_totals", lambda a, b: {})
    monkeypatch.setattr(toast_analytics, "day_metrics", lambda a, b: {})
    job = sales_accuracy.run_sales_repull(db, days=2, today=TODAY)
    assert job.job_name == "sales_repull"
    assert _rechecks(db) == []
    assert sales_accuracy.recheck_status(db)["warning"] is None
