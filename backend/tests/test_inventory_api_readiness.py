"""The "Use Toast for inventory" switch (services/inventory_api_switch.py) and
the nightly readiness score + "ready" alert (services/inventory_api_readiness.py).

Toast is never called: the snapshot tests use the canned stock / menus of
test_inventory_api.py through a fake `toast_get`, the overlay gets a fake
fetch, and push / WhatsApp are replaced by recorders. A guard fails the test if
anything reaches httpx."""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import uuid
import zoneinfo

import httpx
import pytest

from app.config import get_settings
from app.models import DeviceToken, InventorySnapshot, JobRun, NotificationLog, Order
from app.pos.adapters.toast import client as toast_http
from app.services import inventory_api as ia
from app.services import inventory_api_readiness as rd
from app.services import inventory_api_switch as sw
from app.services import push, whapi_client
from app.services.notification_triggers import TRIGGERS, run_alert_job
from app.services.notifications import ALERTS_BY_KEY, set_pref
from app.setting_models import SettingChange
from tests.conftest import client_for, make_user
from tests.test_inventory_api import RICE, TEA, TODAY, FakeToast, csv_row, sale

NY = zoneinfo.ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    def _no_network(*a, **k):
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    ia.clear_live_stock_cache()
    ia._compare_cache.clear()
    s = get_settings()
    monkeypatch.setattr(s, "inventory_api_snapshots", False)
    monkeypatch.setattr(s, "inventory_api_ready_whatsapp", False)
    monkeypatch.setattr(s, "public_base_url", "https://ops.example.test")
    for name, value in (
        ("inventory_api_ready_nights", 7),
        ("inventory_api_ready_qty_pct", 99.0),
        ("inventory_api_ready_price_pct", 99.0),
        ("inventory_api_ready_barcode_pct", 99.0),
        ("inventory_api_ready_category_pct", 100.0),
        ("inventory_api_ready_max_unexplained_qty", 10),
    ):
        monkeypatch.setattr(s, name, value)
    yield
    ia.clear_live_stock_cache()


@pytest.fixture
def boss(db):
    return make_user(db, "boss", is_admin=True)


@pytest.fixture
def toast(monkeypatch):
    fake = FakeToast()
    monkeypatch.setattr(toast_http, "toast_get", fake)
    monkeypatch.setattr(ia, "_after_new_snapshot", lambda: None)
    return fake


@pytest.fixture
def small_store(db):
    """Yesterday's CSV for the canned catalog (only what the job needs)."""
    csv_row(db, RICE, "Rice 20lb", 30, price=17.99, category="Grains", sub="Rice")
    csv_row(db, TEA, "Tea 1kg", 14, price=9.99)
    sale(db, TEA, TODAY - dt.timedelta(days=1), qty=1.0, net=9.99)
    db.commit()
    return db


def _api_rows(db, day):
    return db.query(InventorySnapshot).filter(
        InventorySnapshot.snapshot_date == day, InventorySnapshot.source_filename.like("api:%")
    ).count()


# ---------------------------------------------------------------------------
# Precedence: .env is the starting value, the page's setting wins once set
# ---------------------------------------------------------------------------


def test_env_value_is_used_until_the_page_sets_it(db, boss, monkeypatch):
    s = get_settings()
    assert sw.is_on(db) is False and sw.switch_state(db)["source"] == "env"
    monkeypatch.setattr(s, "inventory_api_snapshots", True)
    assert sw.is_on(db) is True and sw.is_on() is True  # without a session too
    st = sw.switch_state(db)
    assert st == {"on": True, "source": "env", "env_default": True, "changed_at": None, "changed_by": None, "note": None}

    # The page turns it off: that wins over .env = true...
    out = sw.set_switch(db, boss, False, "  side-by-side week first  ")
    assert out["changed"] is True
    assert sw.is_on(db) is False and sw.is_on() is False
    st = sw.switch_state(db)
    assert st["source"] == "page" and st["changed_by"] == "boss" and st["note"] == "side-by-side week first"
    assert st["env_default"] is True and st["changed_at"]
    # ...and editing .env afterwards changes nothing.
    monkeypatch.setattr(s, "inventory_api_snapshots", False)
    assert sw.is_on() is False
    sw.set_switch(db, boss, True)
    monkeypatch.setattr(s, "inventory_api_snapshots", False)
    assert sw.is_on() is True


def test_history_records_who_when_and_before(db, boss):
    other = make_user(db, "manager", is_admin=True)
    sw.set_switch(db, boss, False)  # pins the .env value (false) on the page
    assert sw.set_switch(db, boss, False)["changed"] is False  # same again: no row
    sw.set_switch(db, other, True, "ready 7/7")
    sw.set_switch(db, boss, False)
    hist = sw.history(db)
    assert [(h["on"], h["previous_on"], h["previous_source"], h["changed_by"]) for h in hist] == [
        (False, True, "page", "boss"),
        (True, False, "page", "manager"),
        (False, False, "env", "boss"),
    ]
    assert hist[1]["note"] == "ready 7/7" and all(h["changed_at"] for h in hist)
    assert db.query(SettingChange).count() == 3


def test_unreadable_table_falls_back_to_env(db, monkeypatch):
    """Code deployed before migration 0052 ran: the .env value, no crash."""
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)
    SettingChange.__table__.drop(bind=db.get_bind())
    try:
        assert sw.is_on() is True and sw.is_on(db) is True
        assert ia.live_stock_enabled() is True
    finally:
        SettingChange.__table__.create(bind=db.get_bind())


# ---------------------------------------------------------------------------
# What the switch does - read at run time, no restart
# ---------------------------------------------------------------------------


def test_nightly_job_follows_the_page_switch_without_a_restart(small_store, toast, boss, monkeypatch):
    db = small_store
    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", True)  # .env says on
    sw.set_switch(db, boss, False)  # the page says off
    job = ia.run_api_snapshot(db)
    detail = json.loads(job.detail)
    assert detail["stored"] == {"written": 0, "skipped": "switch_off"} and detail["write_enabled"] is False
    assert _api_rows(db, TODAY) == 0

    monkeypatch.setattr(get_settings(), "inventory_api_snapshots", False)  # .env says off
    sw.set_switch(db, boss, True)  # the page says on - same process, no restart
    job = ia.run_api_snapshot(db)
    assert job.status == "success", job.detail
    assert json.loads(job.detail)["write_enabled"] is True and _api_rows(db, TODAY) > 0


def test_live_overlay_follows_the_page_switch(db, boss):
    snap = InventorySnapshot(snapshot_date=TODAY, item_id=TEA, name="Tea", inventory_quantity=14.0)
    latest = {TEA: snap}
    fetches = []

    def fetch():
        fetches.append(1)
        return [{"guid": TEA, "status": "QUANTITY", "quantity": 9.0}]

    assert ia.overlay_live_stock(latest, fetch) is latest and fetches == [] and ia.live_stock_meta() is None
    sw.set_switch(db, boss, True)
    out = ia.overlay_live_stock(latest, fetch)
    assert out[TEA].inventory_quantity == 9.0 and fetches == [1]
    assert ia.live_stock_meta()["live"] is True
    sw.set_switch(db, boss, False)  # immediate: no Toast call, snapshot counts, no "live" line
    assert ia.overlay_live_stock(latest, fetch) is latest and fetches == [1]
    assert ia.live_stock_meta() is None
    sw.set_switch(db, boss, True)  # back on: the cache was dropped, Toast asked afresh
    assert ia.overlay_live_stock(latest, fetch)[TEA].inventory_quantity == 9.0 and fetches == [1, 1]


# ---------------------------------------------------------------------------
# Sales since the CSV explain on-hand differences in the nightly comparison
# ---------------------------------------------------------------------------


def _order_at(db, item_id, when, qty, day=None, voided=False):
    db.add(
        Order(
            toast_order_guid=str(uuid.uuid4()),
            toast_check_guid=str(uuid.uuid4()),
            toast_selection_guid=str(uuid.uuid4()),
            business_date=day or (TODAY - dt.timedelta(days=1)),
            opened_at=when,
            item_guid=item_id,
            item_name=item_id,
            quantity=qty,
            net_price=qty,
            voided=voided,
        )
    )


def test_nightly_comparison_counts_differences_explained_by_sales(db, toast):
    """Toast says rice 31 / tea 12 at the pull; the CSV made at 01:00 said
    rice 33 / tea 14 / dal 1. Two rice and two tea sold after the file (one
    voided line doesn't count) - both explained; dal is not."""
    made = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    csv_row(db, RICE, "Rice 20lb", 33, day=TODAY, price=17.99, category="Grains", sub="Rice", uploaded_at=made)
    csv_row(db, TEA, "Tea 1kg", 14, day=TODAY, price=9.99, barcode="036000291452;012345678905", uploaded_at=made)
    csv_row(db, "dal-each", "Dal 4lb", 3, day=TODAY, price=6.99, category="Grains", sub="Rice", uploaded_at=made)
    _order_at(db, RICE, made + dt.timedelta(minutes=30), 2)
    _order_at(db, TEA, made + dt.timedelta(minutes=40), 2)
    _order_at(db, TEA, made + dt.timedelta(minutes=50), 5, voided=True)
    _order_at(db, "dal-each", made - dt.timedelta(minutes=10), 2)  # before the file: already in its count
    db.commit()
    job = ia.run_api_snapshot(db)
    cmp_ = json.loads(job.detail)["comparison"]
    qty = next(f for f in cmp_["fields"] if f["field"] == "inventory_quantity")
    assert (qty["tracked"], qty["tracked_same"], qty["explained_by_sales"], qty["unexplained"]) == (3, 0, 2, 1)
    assert qty["matched_or_explained_pct"] == round(200 / 3, 2)
    (ex,) = qty["unexplained_examples"]
    assert ex["item_id"] == "dal-each" and ex["csv"] == 3 and ex["api"] == 1.0 and ex["sold_since_csv"] == 0
    assert cmp_["sales_window"]["from"].startswith(made.replace(tzinfo=None).isoformat()[:16])
    assert cmp_["placement"]["same"] == 3 and cmp_["placement"]["same_pct"] == 100.0
    # Existing keys unchanged (additive).
    for key in ("field", "label", "compared", "same", "differ", "same_pct", "examples"):
        assert key in qty
    m = rd.night_metrics(cmp_)
    assert (m["qty_pct"], m["qty_unexplained"], m["qty_explained_by_sales"]) == (round(200 / 3, 2), 1, 2)


def test_out_of_stock_explained_when_sales_took_it_to_zero(db):
    rows = {"x": {"name": "X", "inventory_quantity": 0.0, "api_out_of_stock": True}}
    csv = {"x": InventorySnapshot(item_id="x", name="X", inventory_quantity=3.0)}
    out = ia.compare_rows(rows, csv, TODAY, sold_since={"x": 3.0})
    qty = out["fields"][0]
    assert (qty["explained_by_sales"], qty["unexplained"]) == (1, 0)
    out = ia.compare_rows(rows, csv, TODAY, sold_since={"x": 2.0})
    assert out["fields"][0]["unexplained"] == 1
    # Untracked on both sides: not part of the on-hand match.
    rows["k"] = {"name": "Dish", "inventory_quantity": None}
    csv["k"] = InventorySnapshot(item_id="k", name="Dish", inventory_quantity=None)
    q = ia.compare_rows(rows, csv, TODAY)["fields"][0]
    assert q["tracked"] == 1 and q["same"] == 1 and q["tracked_same"] == 0


# ---------------------------------------------------------------------------
# Readiness math
# ---------------------------------------------------------------------------


def comparison(qty_pct=100.0, unexplained=0, price_pct=100.0, barcode_pct=100.0, category_pct=100.0, tracked=10000):
    """A stored comparison with the given scores (10,000 tracked items so
    percentages land exactly on two decimals)."""
    matched = round(tracked * qty_pct / 100)
    explained = min(matched, 50)

    def f(name, pct):
        same = round(tracked * pct / 100)
        return {"field": name, "label": name, "compared": tracked, "same": same, "differ": tracked - same, "same_pct": pct, "examples": []}

    qty = f("inventory_quantity", qty_pct)
    qty.update(
        tracked=tracked,
        tracked_same=matched - explained,
        explained_by_sales=explained,
        unexplained=unexplained,
        matched_or_explained_pct=qty_pct,
    )
    same_cat = round(tracked * category_pct / 100)
    return {
        "csv_date": TODAY.isoformat(),
        "items_in_both": tracked,
        "fields": [qty, f("price", price_pct), f("barcode", barcode_pct), f("category_group", 100.0), f("category", 100.0), f("subcategory", 100.0)],
        "placement": {"compared": tracked, "same": same_cat, "differ": tracked - same_cat, "same_pct": category_pct, "examples": []},
    }


def night(db, day, status="success", comp=None, stored=None, error=None, started=None):
    detail = {"meta": {}, "write_enabled": False}
    if comp is not None:
        detail["comparison"] = comp
        detail["stored"] = {"written": 0, "skipped": "csv_exists"}
    if stored is not None:
        detail["stored"] = stored
    if error is not None:
        detail = {"error": error}
    started = started or dt.datetime.combine(day, dt.time(8, 5), tzinfo=dt.timezone.utc)
    db.add(JobRun(job_name=ia.JOB_SNAPSHOT, status=status, business_date=day, started_at=started, finished_at=started, detail=json.dumps(detail)))
    db.commit()


def day(n):
    return TODAY - dt.timedelta(days=n)


@pytest.mark.parametrize(
    "kwargs, ok",
    [
        ({}, True),
        ({"qty_pct": 99.0}, True),
        ({"qty_pct": 98.99}, False),
        ({"price_pct": 99.0}, True),
        ({"price_pct": 98.99}, False),
        ({"barcode_pct": 99.0}, True),
        ({"barcode_pct": 98.99}, False),
        ({"category_pct": 100.0}, True),
        ({"category_pct": 99.99}, False),
        ({"unexplained": 10}, True),
        ({"unexplained": 11}, False),
    ],
)
def test_a_night_passes_exactly_at_the_thresholds(kwargs, ok):
    m = rd.night_metrics(comparison(**kwargs))
    reasons = rd.judge(m, rd.thresholds())
    assert (not reasons) is ok, reasons


def test_reasons_say_what_missed():
    m = rd.night_metrics(comparison(qty_pct=97.5, unexplained=40, category_pct=99.99))
    reasons = rd.judge(m, rd.thresholds())
    assert reasons == [
        "On hand matched or explained by sales 97.5% (needs 99%)",
        "Category matched 99.99% (needs 100%, 1 differ)",
        "40 on-hand differences not explained by sales (at most 10)",
    ]


def test_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_ready_qty_pct", 97.0)
    monkeypatch.setattr(get_settings(), "inventory_api_ready_max_unexplained_qty", 50)
    assert rd.judge(rd.night_metrics(comparison(qty_pct=97.5, unexplained=40)), rd.thresholds()) == []


def test_an_old_comparison_without_the_sales_explanation_scores_on_exact_matches():
    comp = comparison()
    qty = comp["fields"][0]
    for k in ("tracked", "tracked_same", "explained_by_sales", "unexplained", "matched_or_explained_pct"):
        del qty[k]
    qty.update(compared=1000, same=995, differ=5, same_pct=99.5)
    del comp["placement"]
    m = rd.night_metrics(comp)
    assert (m["qty_pct"], m["qty_unexplained"], m["qty_explained_by_sales"], m["category_pct"]) == (99.5, 5, None, 100.0)


def test_streak_reaches_ready_after_seven_good_nights(db):
    for n in range(7, 0, -1):
        night(db, day(n), comp=comparison())
    r = rd.readiness(db)
    assert (r["streak"], r["needed"], r["ready"], r["reset"]) == (7, 7, True, None)
    assert r["streak_started"] == day(7).isoformat() and r["notify"] is True
    assert r["last_comparison_at"].startswith(day(1).isoformat())
    assert [n["result"] for n in r["nights"]] == ["good"] * 7


def test_six_good_nights_are_not_ready(db):
    for n in range(6, 0, -1):
        night(db, day(n), comp=comparison())
    r = rd.readiness(db)
    assert (r["streak"], r["ready"], r["notify"]) == (6, False, False)


def test_a_bad_night_resets_the_streak_and_says_why(db):
    for n in range(10, 4, -1):
        night(db, day(n), comp=comparison())  # 6 good
    night(db, day(4), comp=comparison(price_pct=98.0))  # bad
    for n in range(3, 0, -1):
        night(db, day(n), comp=comparison())  # 3 good since
    r = rd.readiness(db)
    assert (r["streak"], r["ready"]) == (3, False)
    assert r["reset"] == {"date": day(4).isoformat(), "reasons": ["Price matched 98% (needs 99%, 200 differ)"]}
    assert r["streak_started"] == day(3).isoformat()


def test_failed_and_cut_short_runs_reset_but_nights_without_a_csv_do_not(db):
    night(db, day(6), comp=comparison())
    night(db, day(5), status="failed", error="Toast /stock/v1/inventory returned HTTP 500")
    night(db, day(4), comp=comparison())
    night(db, day(3), stored={"written": 0, "skipped": "switch_off"})  # no CSV by 04:05
    night(db, day(2), comp=comparison())
    r = rd.readiness(db)
    assert r["streak"] == 2
    assert r["reset"] == {"date": day(5).isoformat(), "reasons": ["The nightly run failed: Toast /stock/v1/inventory returned HTTP 500"]}
    by_date = {n["date"]: n for n in r["nights"]}
    assert by_date[day(3).isoformat()]["result"] == "skipped"

    night(db, day(1), status="failed", stored={"written": 0, "skipped": "implausible", "reason": "120 stock rows"})
    r = rd.readiness(db)
    assert r["streak"] == 0 and r["reset"]["reasons"] == ["Toast's answer looked cut short (120 stock rows)"]


def test_the_newest_run_of_a_date_is_the_night(db):
    for n in range(7, 0, -1):
        night(db, day(n), comp=comparison())
    # A later re-run of night 1 that missed: that's the night now.
    night(db, day(1), comp=comparison(unexplained=30), started=dt.datetime.combine(day(1), dt.time(9, 0), tzinfo=dt.timezone.utc))
    r = rd.readiness(db)
    assert r["streak"] == 0 and r["reset"]["date"] == day(1).isoformat()
    assert len([n for n in r["nights"] if n["date"] == day(1).isoformat()]) == 1


# ---------------------------------------------------------------------------
# The "ready" alert: once, admins only, quiet hours, switch off only
# ---------------------------------------------------------------------------


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


@pytest.fixture
def whatsapp(monkeypatch):
    sent = []
    monkeypatch.setattr(whapi_client, "send_whatsapp_message", lambda text, to=None: sent.append((to, text)))
    return sent


def _devices(db, *users):
    for u in users:
        db.add(DeviceToken(user_id=u.id, token=f"tok-{u.username}", platform="ios"))
    db.commit()
    # The separate push is off by default since Oct 15 (a Morning summary line).
    for u in users:
        if u.is_admin:
            set_pref(db, u, "inventory_api_ready", True)


def _ready(db):
    for n in range(7, 0, -1):
        night(db, day(n), comp=comparison())


def test_ready_alert_goes_to_admins_once(db, boss, pushes, whatsapp):
    staff = make_user(db, "ravi", features=["overview", "inventory_reports"])
    demo = make_user(db, "app_store_review", is_admin=True)
    _devices(db, boss, staff, demo)
    _ready(db)
    alert = ALERTS_BY_KEY["inventory_api_ready"]
    assert alert["admin_only"] is True and alert["route"] == "inventory_api" and "inventory_api_ready" in TRIGGERS

    nine = dt.datetime.combine(TODAY, dt.time(9, 0), tzinfo=NY)
    assert run_alert_job("inventory_api_ready", now=nine.replace(hour=6), db=db) == 0  # quiet hours
    assert pushes == []
    assert run_alert_job("inventory_api_ready", now=nine, db=db) == 1
    (p,) = pushes
    assert p["tokens"] == ["tok-boss"]  # not staff, not the demo login
    assert p["body"] == "Inventory from Toast is ready - tap to turn on"
    assert p["data"]["route"] == "inventory_api" and p["data"]["url"] == "https://ops.example.test/#/inventory-api"
    # Deduped: the next mornings, while the streak goes on, nothing more.
    night(db, day(0), comp=comparison())
    assert run_alert_job("inventory_api_ready", now=nine + dt.timedelta(days=1), db=db) == 0
    assert len(pushes) == 1
    # Push only by default: WhatsApp never touched.
    assert whatsapp == []
    assert db.query(NotificationLog).filter(NotificationLog.key == "inventory_api_ready").count() == 1


def test_no_alert_while_not_ready_or_switched_on(db, boss, pushes):
    _devices(db, boss)
    nine = dt.datetime.combine(TODAY, dt.time(9, 0), tzinfo=NY)
    for n in range(6, 0, -1):
        night(db, day(n), comp=comparison())
    assert TRIGGERS["inventory_api_ready"](db, nine) == []
    night(db, day(0), comp=comparison())
    sw.set_switch(db, boss, True)
    assert TRIGGERS["inventory_api_ready"](db, nine) == []
    sw.set_switch(db, boss, False)
    assert len(TRIGGERS["inventory_api_ready"](db, nine)) == 1


def test_a_new_streak_after_a_reset_alerts_again(db, boss, pushes):
    _devices(db, boss)
    nine = dt.datetime.combine(TODAY, dt.time(9, 0), tzinfo=NY)
    for n in range(16, 9, -1):
        night(db, day(n), comp=comparison())
    assert run_alert_job("inventory_api_ready", now=nine - dt.timedelta(days=9), db=db) == 1
    night(db, day(9), comp=comparison(qty_pct=90.0))  # reset
    for n in range(8, 1, -1):
        night(db, day(n), comp=comparison())
    assert run_alert_job("inventory_api_ready", now=nine, db=db) == 1
    assert len(pushes) == 2


def test_whatsapp_only_when_allowed_and_only_once(db, boss, pushes, whatsapp, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_ready_whatsapp", True)
    monkeypatch.setattr(get_settings(), "whapi_chat_id", "120363000000000000@g.us")
    _devices(db, boss)
    _ready(db)
    nine = dt.datetime.combine(TODAY, dt.time(9, 0), tzinfo=NY)
    assert run_alert_job("inventory_api_ready", now=nine.replace(hour=23), db=db) == 0
    assert whatsapp == []  # quiet hours hold the WhatsApp text too
    run_alert_job("inventory_api_ready", now=nine, db=db)
    run_alert_job("inventory_api_ready", now=nine + dt.timedelta(hours=1), db=db)
    assert whatsapp == [(None, "Inventory from Toast is ready - tap to turn on: https://ops.example.test/#/inventory-api")]
    assert len(pushes) == 1


def test_whatsapp_timeout_is_never_posted_twice(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "inventory_api_ready_whatsapp", True)
    calls = []

    def timeout(text, to=None):
        calls.append(text)
        raise whapi_client.WhapiAmbiguousTimeout("maybe")

    monkeypatch.setattr(whapi_client, "send_whatsapp_message", timeout)
    assert rd.post_ready_whatsapp(db, day(7).isoformat()) == "uncertain"
    assert rd.post_ready_whatsapp(db, day(7).isoformat()) == "already_sent"
    assert len(calls) == 1

    def boom(text, to=None):
        raise httpx.HTTPStatusError("402", request=None, response=None)

    monkeypatch.setattr(whapi_client, "send_whatsapp_message", boom)
    assert rd.post_ready_whatsapp(db, day(1).isoformat()) == "failed"  # retried next run
    assert db.query(NotificationLog).count() == 1


# ---------------------------------------------------------------------------
# API: admin only, demo refused, additive responses
# ---------------------------------------------------------------------------


def test_switch_api_permissions(db, boss, anon_client):
    staff = make_user(db, "ravi", features=["overview", "inventory_reports"])
    demo = make_user(db, "app_store_review", is_admin=True)
    admin_client, staff_client, demo_client = client_for(db, boss), client_for(db, staff), client_for(db, demo)

    assert anon_client.get("/api/inventory-api/switch").status_code == 401
    assert anon_client.put("/api/inventory-api/switch", json={"on": True}).status_code == 401
    assert staff_client.get("/api/inventory-api/switch").status_code == 403
    assert staff_client.put("/api/inventory-api/switch", json={"on": True}).status_code == 403
    r = demo_client.put("/api/inventory-api/switch", json={"on": True})
    assert r.status_code == 403 and "demo" in r.json()["detail"].lower()
    assert db.query(SettingChange).count() == 0

    r = admin_client.get("/api/inventory-api/switch")
    assert r.status_code == 200
    body = r.json()
    assert body["on"] is False and body["source"] == "env" and body["history"] == []
    assert body["readiness"] == {"ready": False, "streak": 0, "needed": 7, "last_comparison_at": None}

    r = admin_client.put("/api/inventory-api/switch", json={"on": True, "note": "7/7 nights"})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] is True and body["on"] is True and body["source"] == "page" and body["changed_by"] == "boss"
    assert body["history"][0]["note"] == "7/7 nights"
    assert admin_client.put("/api/inventory-api/switch", json={"on": True}).json()["changed"] is False
    assert admin_client.put("/api/inventory-api/switch", json={"on": "maybe"}).status_code == 422
    assert admin_client.put("/api/inventory-api/switch", json={"on": False, "note": "x" * 501}).status_code == 422
    assert sw.is_on() is True


def test_status_is_additive(db, boss):
    for n in range(3, 0, -1):
        night(db, day(n), comp=comparison())
    sw.set_switch(db, boss, True)
    body = client_for(db, boss).get("/api/inventory-api/status").json()
    for key in (
        "settings", "latest_snapshot_date", "latest_snapshot_source", "latest_api_snapshot_date", "latest_csv_date",
        "csv_age_days", "upload_due", "menu_check", "last_snapshot_run", "live_stock", "live_stock_screens",
    ):
        assert key in body, key
    assert set(body["settings"]) >= {
        "inventory_api_snapshots", "snapshot_time", "inventory_csv_max_age_days", "menu_barcode_recheck_minutes",
        "live_stock_cache_seconds", "scheduler_enabled",
    }
    assert body["settings"]["inventory_api_snapshots"] is True  # the switch in force, not just .env
    assert body["switch"]["source"] == "page"
    r = body["readiness"]
    assert (r["streak"], r["needed"], r["ready"], r["switch_on"], r["notify"]) == (3, 7, False, True, False)
    assert r["thresholds"] == {
        "nights": 7, "qty_pct": 99.0, "price_pct": 99.0, "barcode_pct": 99.0, "category_pct": 100.0, "max_unexplained_qty": 10,
    }
    assert len(r["nights"]) == 3 and r["nights"][0]["metrics"]["qty_pct"] == 100.0


# ---------------------------------------------------------------------------
# Migration 0052
# ---------------------------------------------------------------------------


def test_migration_0052_up_down_up(tmp_path):
    from app.db import Base

    backend = pathlib.Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=backend, env=env, check=True, capture_output=True)

    def tables():
        con = sqlite3.connect(db_file)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            con.close()

    run("upgrade", "0052")
    con = sqlite3.connect(db_file)
    try:
        migrated = {row[1] for row in con.execute("PRAGMA table_info(setting_changes)")}
        assert migrated == {c.name for c in Base.metadata.tables["setting_changes"].columns}
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'setting_changes'").fetchone()[0]
        assert "AUTOINCREMENT" in sql
    finally:
        con.close()
    run("downgrade", "0051")
    assert "setting_changes" not in tables()
    run("upgrade", "head")
    assert "setting_changes" in tables()


# ---------------------------------------------------------------------------
# Verify pass (Oct 13): races, a long streak, a flip during the nightly run, DST
# ---------------------------------------------------------------------------


def test_admins_flipping_at_once_keep_a_true_history(db, boss):
    """Two admins pressing at the same moment: every row's "before" is the
    value really in force before it, and no "turned on, was on" rows (seen
    on a throwaway server: 5 broken rows out of 10 from 20 parallel PUTs)."""
    import threading

    from app.db import SessionLocal

    other = make_user(db, "boss2", is_admin=True)
    users = {boss.id: boss.username, other.id: other.username}
    barrier = threading.Barrier(8, timeout=20)  # 8: well inside the connection pool
    errors = []

    def press(i):
        s = SessionLocal()
        try:
            u = s.get(type(boss), boss.id if i % 2 else other.id)
            barrier.wait()
            sw.set_switch(s, u, bool(i % 3))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=press, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    rows = db.query(SettingChange).order_by(SettingChange.id).all()
    assert rows and {r.changed_by_name for r in rows} <= set(users.values())
    before = json.dumps(False)  # .env (off) before the first change
    for r in rows:
        assert r.previous_value == before
        # Setting the .env value explicitly is recorded on purpose (from then
        # on the page decides - see set_switch), so a first "off, was off"
        # row from .env is right when an "off" press wins the race. After
        # that, a row that changes nothing is the race bug this test guards.
        if r.previous_source == "db":
            assert r.value != r.previous_value
        else:
            assert r is rows[0]
        before = r.value
    assert sw.is_on(db) is json.loads(rows[-1].value)


def test_newest_change_wins_by_order_not_clock(db, boss):
    """A change recorded after a clock step back (changed_at earlier than
    the row before it) is still the one in force."""
    sw.set_switch(db, boss, True)
    db.add(
        SettingChange(
            key=sw.SWITCH_KEY, value="false", previous_value="true", previous_source="db",
            changed_by_name=boss.username, changed_at=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
    )
    db.commit()
    assert sw.is_on(db) is False
    assert sw.history(db)[0]["on"] is False


def test_a_streak_longer_than_the_page_list_alerts_only_once(db, boss, pushes):
    """The page lists 30 nights; a streak past that must keep its first
    night, or the "ready" push (deduped on it) would go out every morning."""
    _devices(db, boss)
    for n in range(40, 0, -1):
        night(db, day(n), comp=comparison())
    r = rd.readiness(db)
    assert r["streak"] == 40 and r["streak_started"] == day(40).isoformat()
    assert len(r["nights"]) == rd.NIGHTS_SHOWN
    nine = dt.datetime.combine(TODAY, dt.time(9, 0), tzinfo=NY)
    assert run_alert_job("inventory_api_ready", now=nine, db=db) == 1
    night(db, day(0), comp=comparison())  # night 41
    assert rd.readiness(db)["streak_started"] == day(40).isoformat()
    assert run_alert_job("inventory_api_ready", now=nine + dt.timedelta(days=1), db=db) == 0
    assert len(pushes) == 1


def test_a_reset_older_than_the_page_list_is_still_found(db):
    night(db, day(45), comp=comparison(price_pct=50.0))
    for n in range(44, 0, -1):
        night(db, day(n), comp=comparison())
    r = rd.readiness(db)
    assert r["streak"] == 44 and r["reset"]["date"] == day(45).isoformat()
    assert r["last_comparison_at"].startswith(day(1).isoformat())


def test_last_comparison_is_shown_even_when_the_newest_night_failed(db):
    night(db, day(2), comp=comparison())
    night(db, day(1), status="failed", error="Toast returned HTTP 503")
    r = rd.readiness(db)
    assert r["streak"] == 0 and r["last_comparison_at"].startswith(day(2).isoformat())


def test_switch_flipped_on_while_the_nightly_job_waits_for_toast_applies_to_that_run(db, boss, monkeypatch):
    """Toast's two GETs can take minutes; the switch is read after they
    answer, so turning it on (or off) meanwhile counts for this run."""
    fake = FakeToast()
    monkeypatch.setattr(ia, "_after_new_snapshot", lambda: None)
    csv_row(db, RICE, "Rice 20lb", 30, price=17.99, category="Grains", sub="Rice")
    csv_row(db, TEA, "Tea 1kg", 14, price=9.99)
    db.commit()

    def stock_then_flip(on):
        def fetch():
            sw.set_switch(db, boss, on)  # an admin presses while Toast answers
            return fake.stock
        return fetch

    job = ia.run_api_snapshot(db, fetch_stock_fn=stock_then_flip(True), fetch_menus_fn=lambda: fake.menus)
    assert job.status == "success", job.detail
    assert json.loads(job.detail)["write_enabled"] is True and _api_rows(db, TODAY) > 0

    job = ia.run_api_snapshot(
        db, snapshot_date=TODAY + dt.timedelta(days=1), fetch_stock_fn=stock_then_flip(False), fetch_menus_fn=lambda: fake.menus
    )
    assert json.loads(job.detail)["stored"] == {"written": 0, "skipped": "switch_off"}
    assert _api_rows(db, TODAY + dt.timedelta(days=1)) == 0


def test_sales_window_across_the_november_dst_change(db):
    """Nov 1 2026: clocks go back at 2 AM. CSV made 00:19 EDT, stock pulled
    04:05 EST - both hours of 1:30 AM count, nothing outside the window."""
    nov1 = dt.date(2026, 11, 1)
    made = dt.datetime(2026, 11, 1, 0, 19, tzinfo=NY)
    pulled = dt.datetime(2026, 11, 1, 4, 5, tzinfo=NY)
    # The extra hour is real time (subtract in UTC: same-zone datetimes subtract wall clocks).
    assert pulled.astimezone(dt.timezone.utc) - made.astimezone(dt.timezone.utc) == dt.timedelta(hours=4, minutes=46)

    def paid(at_utc, qty):
        sale(db, TEA, dt.date(2026, 10, 31), qty=qty)
        db.flush()
        o = db.query(Order).order_by(Order.id.desc()).first()
        o.paid_at = at_utc

    utc = dt.timezone.utc
    paid(dt.datetime(2026, 11, 1, 3, 0, tzinfo=utc), 100)  # Oct 31 23:00 EDT - before the CSV
    paid(dt.datetime(2026, 11, 1, 5, 30, tzinfo=utc), 1)  # 01:30 EDT
    paid(dt.datetime(2026, 11, 1, 6, 30, tzinfo=utc), 2)  # 01:30 EST (the repeated hour)
    paid(dt.datetime(2026, 11, 1, 9, 30, tzinfo=utc), 1000)  # 04:30 EST - after the pull
    db.commit()
    assert ia.units_sold_between(db, made.astimezone(utc), pulled.astimezone(utc)) == {TEA: 3.0}
    # And the readiness night is keyed on the business date, once.
    night(db, nov1, comp=comparison())
    assert [n["date"] for n in rd.readiness(db)["nights"]] == [nov1.isoformat()]
