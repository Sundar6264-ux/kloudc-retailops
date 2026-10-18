"""Multi-store M1 / MT6: neutral contracts and POS capabilities.

Spice Town (Toast) must see no change at all: every gate passes, every old
key / route / setting keeps working, the scheduled job list is identical.
A store whose POS lacks a capability gets 409 on that POS-only route, loses
the matching feature in /me, and never registers the matching job.
"""

import types

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings, get_settings
from app.models import InvoiceSubmission
from app.pos import gating
from app.pos.adapters.toast.connector import ToastConnector
from app.pos.capabilities import TOAST_CAPABILITIES, Capability
from app.services import invoice_submission as inv_svc
from app.services import toast_email
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

# --- helpers -----------------------------------------------------------------------------


@pytest.fixture
def store_without(monkeypatch):
    """Make this process a store whose POS lacks `caps` (Spice Town's Toast
    set minus them), named "TestPOS"."""

    def _apply(*caps):
        # The store's name and capabilities come from its connector (MT2 registry).
        monkeypatch.setattr(ToastConnector, "display_name", "TestPOS")
        monkeypatch.setattr(ToastConnector, "capabilities", TOAST_CAPABILITIES - frozenset(caps))

    return _apply


def _invoice(db, user=None, sent=True, **kw):
    n = db.query(InvoiceSubmission).count() + 1
    entry = InvoiceSubmission(
        vendor="Raja Foods",
        submission_type=kw.pop("submission_type", "invoice"),
        original_filename="bill.pdf",
        stored_filename=f"tok{n}.pdf",
        file_token=f"tok{n}",
        submitted_by_user_id=user.id if user else None,
        submitted_by_name=user.username if user else "clerk",
        whatsapp_sent=sent,
        toast_email_sent=sent,
        created_at=utcnow(),
        **kw,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# Every key submission_out answered before MT6, in its order (app builds 5-9).
OLD_INVOICE_KEYS = [
    "id", "vendor", "submission_type", "transfer_direction", "transfer_direction_label", "type_label",
    "toast_applicable", "total_amount", "notes", "submitted_by_name", "original_filename", "file_url",
    "whatsapp_sent", "whatsapp_error", "whatsapp_ambiguous", "whatsapp_status_overridden_by",
    "toast_email_sent", "toast_email_error", "toast_email_status_overridden_by", "toast_closed_at",
    "toast_closed_by_name", "can_mark_toast_closed", "processing_status", "created_at",
    "delete_requested_at", "delete_requested_by_name", "deleted_at", "deleted_by_name",
]
AP_TWINS = {
    "ap_applicable": "toast_applicable",
    "ap_email_sent": "toast_email_sent",
    "ap_email_error": "toast_email_error",
    "ap_email_status_overridden_by": "toast_email_status_overridden_by",
    "ap_closed_at": "toast_closed_at",
    "ap_closed_by_name": "toast_closed_by_name",
    "can_mark_ap_closed": "can_mark_toast_closed",
}


# --- capabilities ------------------------------------------------------------------------


def test_spice_town_is_toast_with_its_full_capability_set():
    assert gating.pos_provider() == "toast"
    assert gating.pos_name() == "Toast"
    assert gating.store_capabilities() == TOAST_CAPABILITIES
    assert gating.capability_values() == [c.value for c in Capability if c in TOAST_CAPABILITIES]
    # Every capability a gate below asks for is one Spice Town has.
    used = set().union(*gating.FEATURE_CAPABILITIES.values(), *gating.JOB_CAPABILITIES.values())
    assert used <= TOAST_CAPABILITIES
    assert set(gating.CAPABILITY_LABELS) == set(Capability)


def test_unknown_provider_has_no_capabilities(monkeypatch):
    fake = types.SimpleNamespace(pos_provider="Clover")
    monkeypatch.setattr(gating, "get_settings", lambda: fake)
    assert gating.pos_provider() == "clover"
    assert gating.pos_name() == "Clover"
    assert gating.store_capabilities() == frozenset()
    assert gating.features_for_store(["overview", "open_orders", "sales_insights"]) == ["overview"]


# --- /api/app/config and /api/auth/me ------------------------------------------------------


def test_app_config_adds_pos_name_and_capabilities_after_the_old_keys(anon_client):
    body = anon_client.get("/api/app/config").json()
    assert list(body)[:2] == ["min_supported_build", "store_url"]
    assert body["pos_name"] == "Toast"
    assert body["capabilities"] == gating.capability_values()
    assert "analytics_reports" in body["capabilities"] and "open_orders" in body["capabilities"]


def test_me_adds_pos_fields_and_keeps_allowed_features(db, admin_client):
    from app.features import FEATURES

    staff = make_user(db, "clerk", features=["open_orders", "sales_insights", "overview"])
    me = client_for(db, staff).get("/api/auth/me").json()
    assert me["allowed_features"] == ["open_orders", "sales_insights", "overview"]
    assert list(me)[-2:] == ["pos_name", "capabilities"]
    assert me["pos_name"] == "Toast" and me["capabilities"] == gating.capability_values()
    admin_me = admin_client.get("/api/auth/me").json()
    assert admin_me["allowed_features"] == [f["key"] for f in FEATURES]
    # The admin Users list (stored grants) is unchanged: no POS fields there.
    users = admin_client.get("/api/auth/users").json()
    assert all("capabilities" not in u for u in users)


def test_me_drops_features_the_pos_cannot_support(db, admin_client, store_without):
    store_without(Capability.ANALYTICS_REPORTS, Capability.OPEN_ORDERS)
    staff = make_user(db, "clerk", features=["open_orders", "sales_insights", "overview"])
    me = client_for(db, staff).get("/api/auth/me").json()
    assert me["allowed_features"] == ["overview"]
    assert me["pos_name"] == "TestPOS"
    assert "analytics_reports" not in me["capabilities"]
    admin_keys = admin_client.get("/api/auth/me").json()["allowed_features"]
    assert "sales_insights" not in admin_keys and "open_orders" not in admin_keys and "overview" in admin_keys


# --- require_capability ------------------------------------------------------------------

GATED_ROUTES = [
    # (method, path, capability the store lacks)
    ("GET", "/api/insights/channels", Capability.ANALYTICS_REPORTS),
    ("GET", "/api/insights/returning-customers", Capability.CARD_FINGERPRINTS),
    ("GET", "/api/orders/open/all-time", Capability.OPEN_ORDERS),
    ("GET", "/api/orders/employees", Capability.EMPLOYEES),
    ("GET", "/api/inventory-api/status", Capability.STOCK_LEVELS),
    ("POST", "/api/inventory-api/menu-recheck", Capability.CATALOG_CHANGE_STAMP),
    ("GET", "/api/sales/accuracy", Capability.DAY_TOTALS),
    ("POST", "/api/sales/accuracy/run", Capability.DAY_TOTALS),
    ("POST", "/api/inventory/upload", Capability.CSV_CATALOG_IMPORT),
]


@pytest.mark.parametrize("method,path,cap", GATED_ROUTES)
def test_pos_only_routes_answer_409_without_the_capability(admin_client, store_without, method, path, cap):
    store_without(cap)
    r = admin_client.request(method, path)
    assert r.status_code == 409, (path, r.status_code, r.text)
    detail = r.json()["detail"]
    assert detail.startswith("Not available for this store: its point of sale (TestPOS) doesn't provide")
    assert gating.CAPABILITY_LABELS[cap] in detail


@pytest.mark.parametrize("method,path,cap", GATED_ROUTES)
def test_pos_only_routes_are_not_gated_for_spice_town(admin_client, monkeypatch, method, path, cap):
    """With Toast's set the gate never fires (the route answers as before:
    whatever it answered, never the 409 'Not available for this store')."""
    import app.routers.orders as orders_router
    import app.routers.sales as sales_router
    from app.services import inventory_api, toast_analytics

    monkeypatch.setattr(orders_router, "get_employees", lambda: [])
    monkeypatch.setattr(orders_router, "ensure_today_fresh", lambda db: False, raising=False)
    monkeypatch.setattr(inventory_api, "refresh_menu_cache", lambda db: types.SimpleNamespace(status="failed"))
    monkeypatch.setattr(toast_analytics, "is_configured", lambda: False)
    monkeypatch.setattr(sales_router.toast_analytics, "is_configured", lambda: False)
    r = admin_client.request(method, path)
    assert "Not available for this store" not in r.text, (path, r.status_code, r.text)


def test_require_capability_needs_every_capability(store_without):
    from fastapi import HTTPException

    dep = gating.require_capability(Capability.CATALOG, Capability.STOCK_LEVELS)
    assert dep() is None
    store_without(Capability.STOCK_LEVELS)
    with pytest.raises(HTTPException) as exc:
        dep()
    assert exc.value.status_code == 409 and "live stock levels" in exc.value.detail


def test_gate_runs_after_login_and_feature_checks(anon_client, db, store_without):
    store_without(Capability.ANALYTICS_REPORTS)
    assert anon_client.get("/api/insights/channels").status_code == 401
    plain = client_for(db, make_user(db, "plain", features=["overview"]))
    assert plain.get("/api/insights/channels").status_code == 403


# --- settings: neutral names, old names still read ---------------------------------------


def _settings(monkeypatch, **env):
    for key in ("STORE_TIMEZONE", "TOAST_TIMEZONE", "AP_INBOX_EMAIL", "TOAST_INVOICE_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_store_timezone_falls_back_to_toast_timezone(monkeypatch):
    s = _settings(monkeypatch)
    assert (s.store_timezone, s.toast_timezone) == ("America/New_York", "America/New_York")
    s = _settings(monkeypatch, TOAST_TIMEZONE="America/Chicago")
    assert (s.store_timezone, s.toast_timezone) == ("America/Chicago", "America/Chicago")


def test_store_timezone_wins_and_both_names_agree(monkeypatch):
    s = _settings(monkeypatch, TOAST_TIMEZONE="America/Chicago", STORE_TIMEZONE="America/Denver")
    assert (s.store_timezone, s.toast_timezone) == ("America/Denver", "America/Denver")
    s = _settings(monkeypatch, STORE_TIMEZONE="  ")
    assert (s.store_timezone, s.toast_timezone) == ("America/New_York", "America/New_York")


def test_ap_inbox_email_falls_back_wins_or_turns_off(monkeypatch):
    s = _settings(monkeypatch)
    assert s.ap_inbox_email == s.toast_invoice_email == "spicetown@invoices.toasttab.com"
    s = _settings(monkeypatch, TOAST_INVOICE_EMAIL="old@inbox.test")
    assert s.ap_inbox_email == s.toast_invoice_email == "old@inbox.test"
    s = _settings(monkeypatch, TOAST_INVOICE_EMAIL="old@inbox.test", AP_INBOX_EMAIL="ap@inbox.test")
    assert s.ap_inbox_email == s.toast_invoice_email == "ap@inbox.test"
    s = _settings(monkeypatch, TOAST_INVOICE_EMAIL="old@inbox.test", AP_INBOX_EMAIL="")
    assert s.ap_inbox_email == s.toast_invoice_email == ""


def test_restaurant_timezone_reads_the_neutral_name(monkeypatch):
    import zoneinfo

    from app import timeutil

    monkeypatch.setattr(timeutil, "get_settings", lambda: types.SimpleNamespace(store_timezone="America/Denver"))
    assert timeutil.restaurant_timezone() == zoneinfo.ZoneInfo("America/Denver")


class _FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def sendmail(self, frm, to, raw):
        _FakeSMTP.sent.append(to)


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"toast_invoice_email": "old@inbox.test"}, ["old@inbox.test"]),  # a settings object with only the old name
        ({"toast_invoice_email": "old@inbox.test", "ap_inbox_email": "ap@inbox.test"}, ["ap@inbox.test"]),
        ({"toast_invoice_email": "old@inbox.test", "ap_inbox_email": ""}, None),  # blank = off
    ],
)
def test_invoice_email_goes_to_the_ap_inbox(tmp_path, monkeypatch, fields, expected):
    _FakeSMTP.sent = []
    monkeypatch.setattr(toast_email.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(
        toast_email, "get_settings", lambda: types.SimpleNamespace(gmail_address="g@x.test", gmail_app_password="p", **fields)
    )
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really")
    monkeypatch.setattr(toast_email, "toast_friendly_pdf", _passthrough)
    if expected is None:
        with pytest.raises(toast_email.EmailNotConfigured, match="AP_INBOX_EMAIL"):
            toast_email.send_toast_invoice_email("V", "invoice", 1.0, None, pdf, "a.pdf")
        assert _FakeSMTP.sent == []
    else:
        toast_email.send_toast_invoice_email("V", "invoice", 1.0, None, pdf, "a.pdf")
        assert _FakeSMTP.sent == [expected]


class _passthrough:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self.path

    def __exit__(self, *a):
        return False


# --- invoices: ap_* keys and the /mark-ap-closed alias ------------------------------------


def test_invoice_answers_keep_every_toast_key_in_order_and_add_ap_twins(db, admin_client):
    entry = _invoice(db, toast_closed_at=utcnow(), toast_closed_by_name="boss")
    _invoice(db, submission_type="transfer", sent=False, toast_email_error="x")
    for body in [admin_client.get(f"/api/invoices/{entry.id}").json()] + admin_client.get("/api/invoices").json()["items"]:
        keys = list(body)
        assert [k for k in keys if k in OLD_INVOICE_KEYS] == OLD_INVOICE_KEYS
        assert keys[: len(OLD_INVOICE_KEYS)] == OLD_INVOICE_KEYS  # new keys only after the old ones
        for ap_key, toast_key in AP_TWINS.items():
            assert body[ap_key] == body[toast_key], ap_key
            assert type(body[ap_key]) is type(body[toast_key]), ap_key
    out = inv_svc.submission_out(db.get(InvoiceSubmission, entry.id))
    assert out["ap_closed_at"] == out["toast_closed_at"] is not None


def test_mark_ap_closed_is_the_same_handler_as_mark_toast_closed(db, admin_client):
    a, b = _invoice(db), _invoice(db)
    ra = admin_client.post(f"/api/invoices/{a.id}/mark-toast-closed")
    rb = admin_client.post(f"/api/invoices/{b.id}/mark-ap-closed")
    assert ra.status_code == rb.status_code == 200
    for r in (ra, rb):
        body = r.json()
        assert body["toast_closed_by_name"] == body["ap_closed_by_name"] == "boss"
        assert body["can_mark_toast_closed"] is body["can_mark_ap_closed"] is False
    # Same refusal for an invoice that isn't ready, same wording.
    c = _invoice(db, sent=False)
    r1 = admin_client.post(f"/api/invoices/{c.id}/mark-toast-closed")
    r2 = admin_client.post(f"/api/invoices/{c.id}/mark-ap-closed")
    assert (r1.status_code, r1.json()) == (r2.status_code, r2.json()) and r1.status_code == 400
    # Undo, through either name.
    assert admin_client.post(f"/api/invoices/{a.id}/unmark-ap-closed").json()["ap_closed_at"] is None
    assert admin_client.post(f"/api/invoices/{b.id}/unmark-toast-closed").json()["toast_closed_at"] is None
    r1 = admin_client.post(f"/api/invoices/{a.id}/unmark-toast-closed")
    r2 = admin_client.post(f"/api/invoices/{a.id}/unmark-ap-closed")
    assert (r1.status_code, r1.json()) == (r2.status_code, r2.json()) and r1.status_code == 409
    assert admin_client.post("/api/invoices/999/mark-ap-closed").status_code == 404


def test_mark_ap_closed_needs_the_invoice_feature(db):
    entry = _invoice(db)
    nobody = client_for(db, make_user(db, "nobody", features=["overview"]))
    for path in ("mark-toast-closed", "mark-ap-closed", "unmark-ap-closed"):
        assert nobody.post(f"/api/invoices/{entry.id}/{path}").status_code == 403


def test_demo_guard_treats_the_alias_like_the_old_route(db):
    from app.demo_guard import ALLOWLIST, DEMO_READ_ONLY, own_invoice

    for verb in ("mark", "unmark"):
        old = ALLOWLIST[("POST", f"/api/invoices/{{submission_id}}/{verb}-toast-closed")]
        new = ALLOWLIST[("POST", f"/api/invoices/{{submission_id}}/{verb}-ap-closed")]
        assert old is new is own_invoice
    demo = make_user(db, "app_store_review", features=["invoice_submission"])
    demo_client = client_for(db, demo)
    real = _invoice(db)
    own = _invoice(db, user=demo)
    for path in ("mark-ap-closed", "unmark-ap-closed", "mark-toast-closed"):
        r = demo_client.post(f"/api/invoices/{real.id}/{path}")
        assert (r.status_code, r.json()) == (403, {"detail": DEMO_READ_ONLY}), path
    assert demo_client.post(f"/api/invoices/{own.id}/mark-ap-closed").status_code == 200
    db.refresh(real)
    assert real.toast_closed_at is None


def test_ap_email_is_accepted_as_the_toast_email_channel(db, admin_client, monkeypatch):
    entry = _invoice(db)
    r = admin_client.post(
        f"/api/invoices/{entry.id}/override-status", json={"channel": "ap_email", "status": "failed"}
    )
    assert r.status_code == 200
    assert r.json()["toast_email_sent"] is False and r.json()["ap_email_status_overridden_by"] == "boss"
    sent = []
    monkeypatch.setattr(inv_svc, "send_invoice_notifications", lambda sid, channels: sent.append(channels))
    import app.routers.invoices as inv_router

    monkeypatch.setattr(inv_router, "send_invoice_notifications", lambda sid, channels: sent.append(channels))
    r = admin_client.post(f"/api/invoices/{entry.id}/resend", json={"channels": ["ap_email", "whatsapp"]})
    assert r.status_code == 200 and sent == [["toast_email", "whatsapp"]]
    # Anything else is refused exactly as before (the 422 doesn't name the alias).
    r = admin_client.post(f"/api/invoices/{entry.id}/override-status", json={"channel": "fax", "status": "sent"})
    assert r.status_code == 422 and "ap_email" not in r.text and "toast_email" in r.text


# --- scheduled jobs ----------------------------------------------------------------------

# Spice Town's job list on the Oct 15 production copy (parity baseline
# scheduler_jobs.json), main + notifications schedulers.
SPICE_TOWN_JOBS = {
    "main": {
        "daily_sales_sync": "run_daily_sales_sync",
        "insights_catchup": "run_insights_catchup",
        "insights_nightly": "run_insights_nightly",
        "inventory_api_snapshot": "run_inventory_api_snapshot",
        "menu_barcode_recheck": "run_menu_barcode_recheck",
        "open_orders_full_scan": "run_open_orders_full_scan",
        "open_orders_today_refresh": "run_open_orders_today_refresh",
        "sales_repull_0630": "run_sales_repull_job",
        "sales_repull_1230": "run_sales_repull_job",
        "sales_repull_1730": "run_sales_repull_job",
        "sales_repull_2230": "run_sales_repull_job",
        "startup_catchup_sync": "run_daily_sales_sync",
        "startup_fix_issues_warmup": "warm_fix_issues_cache",
        "startup_open_orders_full_scan": "run_open_orders_full_scan",
        "startup_open_orders_today": "run_open_orders_today_refresh",
    },
    "notifications": {
        "notify_cart_removal_request": "run_alert_job",
        "notify_closing_checklist": "run_alert_job",
        "notify_invoice_failed": "run_alert_job",
        "notify_morning_summary": "run_morning_summary",
        "notify_open_orders_1600": "run_open_orders_summary",
        "notify_open_orders_2130": "run_open_orders_summary",
    },
}


@pytest.fixture
def registered_jobs(monkeypatch):
    """Run the real start_* functions with a real (never started)
    BackgroundScheduler and return {scheduler: {job id: function name}}."""
    import app.scheduler as sched

    s = get_settings()
    monkeypatch.setattr(BackgroundScheduler, "start", lambda self, *a, **k: None)
    for flag in ("scheduler_enabled", "notifications_enabled", "sales_repull_enabled", "insights_nightly_enabled"):
        monkeypatch.setattr(s, flag, True)
    monkeypatch.setattr(s, "menu_barcode_recheck_minutes", 60)
    monkeypatch.setattr(s, "sales_repull_times", "06:30,12:30,17:30,22:30")

    def _collect():
        out = {}
        for label, start in (("main", sched.start_scheduler), ("notifications", sched.start_notification_scheduler)):
            monkeypatch.setattr(sched, "_scheduler", None)
            monkeypatch.setattr(sched, "_notification_scheduler", None)
            scheduler = start()
            out[label] = {j.id: gating.job_func_name(j) for j in scheduler.get_jobs()}
        monkeypatch.setattr(sched, "_scheduler", None)
        monkeypatch.setattr(sched, "_notification_scheduler", None)
        return out

    return _collect


def test_spice_town_job_list_is_identical(registered_jobs):
    assert registered_jobs() == SPICE_TOWN_JOBS


def test_every_job_is_classified(registered_jobs):
    """A new scheduled job must say whether it needs the POS (JOB_CAPABILITIES)
    or not (POS_NEUTRAL_JOB_FUNCS) - nothing slips in ungated by accident."""
    known = set(gating.JOB_CAPABILITIES) | gating.POS_NEUTRAL_JOB_FUNCS
    for jobs in registered_jobs().values():
        assert set(jobs.values()) <= known
    assert not set(gating.JOB_CAPABILITIES) & gating.POS_NEUTRAL_JOB_FUNCS


@pytest.mark.parametrize(
    "missing,gone",
    [
        (Capability.ANALYTICS_REPORTS, {"insights_nightly", "insights_catchup"}),
        (Capability.DAY_TOTALS, {"sales_repull_0630", "sales_repull_1230", "sales_repull_1730", "sales_repull_2230"}),
        (
            Capability.OPEN_ORDERS,
            {
                "open_orders_full_scan", "open_orders_today_refresh", "startup_open_orders_full_scan",
                "startup_open_orders_today", "notify_open_orders_1600", "notify_open_orders_2130",
            },
        ),
        (Capability.CATALOG_CHANGE_STAMP, {"menu_barcode_recheck"}),
        (Capability.STOCK_LEVELS, {"inventory_api_snapshot"}),
        (Capability.ORDERS_BY_BUSINESS_DATE, {"daily_sales_sync", "startup_catchup_sync"}),
    ],
)
def test_jobs_need_their_capability(registered_jobs, store_without, missing, gone):
    store_without(missing)
    jobs = registered_jobs()
    ids = set(jobs["main"]) | set(jobs["notifications"])
    all_ids = set(SPICE_TOWN_JOBS["main"]) | set(SPICE_TOWN_JOBS["notifications"])
    assert ids == all_ids - gone


def test_morning_summary_skips_the_open_checks_refresh_without_open_orders(monkeypatch, store_without):
    import app.scheduler as sched
    from app.services import notification_triggers

    calls = []
    monkeypatch.setattr(sched, "run_open_orders_today_refresh", lambda: calls.append("refresh"))
    monkeypatch.setattr(notification_triggers, "run_morning_job", lambda include_fix_issues: calls.append("summary"))
    sched.run_morning_summary()
    assert calls == ["refresh", "summary"]
    calls.clear()
    store_without(Capability.OPEN_ORDERS)
    sched.run_morning_summary()
    assert calls == ["summary"]


def test_drop_unsupported_jobs_ignores_a_recording_stand_in():
    class Recorder:
        def add_job(self, *a, **k):
            pass

    assert gating.drop_unsupported_jobs(Recorder()) == []


def test_scheduler_uses_the_store_time_zone(monkeypatch, registered_jobs):
    import app.scheduler as sched

    s = get_settings()
    monkeypatch.setattr(s, "store_timezone", "America/Denver")
    monkeypatch.setattr(BackgroundScheduler, "start", lambda self, *a, **k: None)
    monkeypatch.setattr(sched, "_scheduler", None)
    scheduler = sched.start_scheduler()
    assert str(scheduler.timezone) == "America/Denver"
    monkeypatch.setattr(sched, "_scheduler", None)
