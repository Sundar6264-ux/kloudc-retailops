"""Website ready A1: every "today" on the server is the STORE's date (its own
time zone), never the server clock's date.

The clock is frozen at 01:30 UTC on 2026-10-20 with the server process in
UTC: the server's own date is the 20th, but for an America/New_York store it
is still 21:30 on the 19th - every route below must answer for the 19th.
"""

import datetime as dt
import pathlib
import re
import time

import pytest
import time_machine

from app.config import get_settings
from app.timeutil import restaurant_today

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
UTC = dt.timezone.utc
FROZEN = dt.datetime(2026, 10, 20, 1, 30, tzinfo=UTC)
STORE_DAY = dt.date(2026, 10, 19)


@pytest.fixture
def utc_server_ny_store(monkeypatch):
    """The process clock in UTC (server date = the 20th) and the store in New
    York (store date = the 19th), clock frozen at 01:30 UTC."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    s = get_settings()
    monkeypatch.setattr(s, "store_timezone", "America/New_York")
    monkeypatch.setattr(s, "toast_timezone", "America/New_York")
    with time_machine.travel(FROZEN, tick=False):
        assert dt.date.today() == dt.date(2026, 10, 20)  # the server's day
        yield
    monkeypatch.undo()
    time.tzset()


def test_no_server_date_today_left_in_the_app():
    """Guard: a new `date.today()` in backend/app is the server's date, not the
    store's - use app.timeutil.restaurant_today()."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\bdate\.today\(\)|\bdatetime\.today\(\)", code):
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{no}")
    assert offenders == [], "use restaurant_today() (the store's date): " + ", ".join(offenders)


def test_restaurant_today_is_the_stores_date(utc_server_ny_store):
    assert restaurant_today() == STORE_DAY


def test_forecast_and_reorder_answer_for_the_stores_day(utc_server_ny_store, admin_client):
    r = admin_client.get("/api/ordering/forecast")
    assert r.status_code == 200 and r.json()["as_of"] == STORE_DAY.isoformat()
    r = admin_client.get("/api/ordering/reorder-candidates")
    assert r.status_code == 200 and r.json()["as_of"] == STORE_DAY.isoformat()


def test_supplier_projection_ends_on_the_stores_day(utc_server_ny_store, admin_client):
    r = admin_client.get("/api/ordering/supplier-projection", params={"supplier": "Acme Foods"})
    assert r.status_code == 200 and r.json()["as_of"] == STORE_DAY.isoformat()


def test_purchase_order_pdf_is_named_for_the_stores_day(utc_server_ny_store, admin_client):
    r = admin_client.post(
        "/api/ordering/purchase-order/export-pdf",
        json={"supplier": "Acme Foods", "items": [{"name": "Rice 10 lb", "qty": 2}]},
    )
    assert r.status_code == 200
    assert f"purchase-order-acme-foods-{STORE_DAY.isoformat()}.pdf" in r.headers["content-disposition"]


def test_manual_sales_sync_runs_for_the_stores_yesterday(utc_server_ny_store, admin_client, monkeypatch):
    from app.routers import jobs

    seen = {}

    def fake_sync(db, day):
        seen["day"] = day
        raise RuntimeError("stop here")

    monkeypatch.setattr(jobs, "sync_sales_for_date", fake_sync)
    with pytest.raises(RuntimeError):
        admin_client.post("/api/jobs/sales-sync/run")
    assert seen["day"] == STORE_DAY - dt.timedelta(days=1)


def test_weekly_digest_week_ends_on_the_stores_day(utc_server_ny_store, db):
    from app.services.weekly_digest import get_weekly_digest_data

    assert get_weekly_digest_data(db)["week_end"] == STORE_DAY.isoformat()


def test_stock_issue_zero_stock_window_uses_the_stores_day(utc_server_ny_store):
    from app.services import stock_issues

    # No uploads -> nothing to flag, but the default "today" is the store's.
    assert stock_issues.restaurant_today() == STORE_DAY


@pytest.mark.parametrize(
    "module",
    [
        "app.routers.ordering",
        "app.routers.jobs",
        "app.services.stock_issues",
        "app.services.similar_products",
        "app.services.supplier_projection",
        "app.services.po_export",
        "app.services.reorder",
        "app.services.insights",
        "app.services.weekly_digest",
    ],
)
def test_every_former_server_date_site_reads_the_store_date(module):
    import importlib

    mod = importlib.import_module(module)
    assert mod.restaurant_today is restaurant_today
