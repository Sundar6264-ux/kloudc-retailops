"""Website-ready track B: usage metering, plans, data export, off-boarding
(docs/WEBSITE_READY_CONTRACT.md B1-B5) on scratch PostgreSQL databases."""

import datetime as dt
import hashlib
import io
import json
import time
import zipfile

import pytest
from sqlalchemy import create_engine, select, text

from tests.platform.conftest import (
    PID,
    PREFIX,
    _admin,
    _url_for,
    provision,
    store_login,
)

B_TABLES = "store_usage_daily, store_plans, store_exports, store_offboarding"


@pytest.fixture(autouse=True)
def clean_b_tables(platform_env):
    from app.platform import usage
    from app.platform.db import platform_engine

    with platform_engine().begin() as c:
        c.execute(text(f"TRUNCATE {B_TABLES} RESTART IDENTITY"))
    usage._reset_for_tests()
    yield
    usage._reset_for_tests()


def _bearer(client, token):
    c = client.__class__(client.app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.fixture
def alpha(platform_env):
    return provision("ALPHA")


@pytest.fixture
def alpha_admin(client, alpha):
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 200, r.text
    return _bearer(client, r.json()["token"])


def _add_user(code, username, password, *, admin=False):
    from app.auth import hash_password
    from app.models import User
    from app.tenancy import tenant_session

    with tenant_session(code) as db:
        db.add(User(username=username, password_hash=hash_password(password), is_admin=admin, allowed_features="[]"))
        db.commit()


def _actions(pdb, code):
    from app.platform.models import AuditLog

    pdb.expire_all()
    return [r.action for r in pdb.execute(select(AuditLog).where(AuditLog.store_code == code).order_by(AuditLog.id)).scalars()]


def _wait_export(code, export_id, timeout=60.0):
    from app.platform.export import list_exports

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rows = {e["id"]: e for e in list_exports(code)}
        if rows.get(export_id, {}).get("status") in ("ready", "failed"):
            return rows[export_id]
        time.sleep(0.1)
    raise AssertionError(f"export {export_id} did not finish")


# --- B1 usage ---------------------------------------------------------------------------------


def test_record_never_raises_and_flush_adds_up_per_day(pdb, monkeypatch):
    from app.platform import usage
    from app.platform.data_models import StoreUsageDaily

    usage.record(usage.ORDERS_SYNCED, 25, code="alpha")
    usage.record(usage.ORDERS_SYNCED, 5, code="ALPHA")
    usage.record(usage.POS_API_CALLS, code="ALPHA")
    usage.record("not_a_metric", 3, code="ALPHA")  # ignored
    usage.record(usage.AI_CALLS, -2, code="ALPHA")  # ignored
    usage.record(usage.AI_CALLS, "x", code="ALPHA")  # ignored, no raise
    usage.record(usage.AI_CALLS)  # no store: nothing
    assert usage.flush() == 2
    usage.record(usage.ORDERS_SYNCED, 10, code="ALPHA")
    assert usage.flush() == 1
    rows = {r.metric: r.value for r in pdb.execute(select(StoreUsageDaily)).scalars()}
    assert rows == {"orders_synced": 40, "pos_api_calls": 1}

    # A platform database that is down: kept for the next flush, never raised.
    import app.platform.db as pdbmod

    usage.record(usage.AI_CALLS, 7, code="ALPHA")
    real = pdbmod.platform_session
    monkeypatch.setattr(pdbmod, "platform_session", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert usage.flush() == 0
    monkeypatch.setattr(pdbmod, "platform_session", real)
    assert usage.flush() == 1
    pdb.expire_all()
    assert pdb.get(StoreUsageDaily, ("ALPHA", usage._today(), "ai_calls")).value == 7


def test_record_is_fast_and_thread_safe():
    import threading

    from app.platform import usage

    def worker():
        for _ in range(2000):
            usage.record(usage.POS_API_CALLS, code="ALPHA")

    started = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.perf_counter() - started < 5
    usage.flush()
    assert usage.snapshot("ALPHA")["metrics"]["pos_api_calls"] == 8000


def test_touch_activity_is_rate_limited_and_kept_as_newest(pdb):
    from app.platform import usage

    usage.touch_activity(code="ALPHA")
    first = usage._activity["ALPHA"]
    usage.touch_activity(code="ALPHA")  # within the minute: nothing new
    assert usage._activity["ALPHA"] == first
    usage.flush()
    snap = usage.snapshot("ALPHA")
    assert snap["metrics"]["last_activity_at"] is not None
    assert snap["days"][0]["last_activity_at"] == snap["metrics"]["last_activity_at"]


def test_gauges_snapshot_and_signed_in_activity(alpha, alpha_admin, pdb):
    from app.platform import usage
    from app.storage import get_storage

    get_storage().put_bytes("invoices/one.pdf", b"x" * 1234, code="ALPHA")
    usage._reset_for_tests()
    assert alpha_admin.get("/api/auth/me").status_code == 200  # a signed-in request notes activity
    assert "ALPHA" in usage._activity
    usage.flush()
    gauges = usage.measure_gauges("ALPHA")
    assert gauges["users"] == 1 and gauges["storage_bytes"] >= 1234 and gauges["database_bytes"] > 0
    assert gauges["items"] == 0
    snap = usage.snapshot("ALPHA")
    assert snap["metrics"]["users"] == 1 and snap["metrics"]["last_activity_at"]
    assert snap["days"] and snap["updated_at"]


def test_usage_routes_master_vs_store(client, alpha, alpha_admin, master_client):
    from app.platform import usage

    usage.record(usage.ORDERS_SYNCED, 12, code="ALPHA")
    usage.record(usage.ORDERS_SYNCED, 99, code="OTHER")
    usage.flush()
    r = master_client.get("/api/platform/stores/ALPHA/usage")
    assert r.status_code == 200 and r.json()["metrics"]["orders_synced"] == 12
    assert {p["id"] for p in r.json()["plan"]["plans"]} == {"custom", "trial", "starter", "pro"}
    assert master_client.get("/api/platform/stores/NOPE/usage").status_code == 404
    r = master_client.post("/api/platform/stores/ALPHA/usage/measure")
    assert r.status_code == 200 and r.json()["metrics"]["users"] == 1

    own = alpha_admin.get("/api/settings/usage").json()
    assert own["available"] and own["store_code"] == "ALPHA" and own["metrics"]["orders_synced"] == 12
    assert "plans" not in own["plan"]
    # The store comes from the session: ?store= / other codes are never honoured.
    assert alpha_admin.get("/api/settings/usage?store=OTHER").status_code == 409
    # A store session is not a master.
    assert alpha_admin.get("/api/platform/stores/ALPHA/usage").status_code in (401, 403)
    _add_user("ALPHA", "staff1", "staff-pass-1")
    staff = _bearer(client, store_login(client, "ALPHA", "staff1", "staff-pass-1").json()["token"])
    assert staff.get("/api/settings/usage").status_code == 403
    assert client.__class__(client.app).get("/api/settings/usage").status_code == 401
    summary = {s["store_code"]: s for s in master_client.get("/api/platform/usage/summary").json()}
    assert summary["ALPHA"]["plan"] == "custom" or summary["ALPHA"]["plan"] in ("trial", "starter", "pro")


def test_single_mode_shows_the_shared_service_message(platform_env, monkeypatch):
    from app.db import SessionLocal
    from tests.conftest import client_for, make_user

    monkeypatch.setattr(platform_env, "tenancy_mode", "single")
    db = SessionLocal()
    try:
        admin_client = client_for(db, make_user(db, "boss1", is_admin=True))
    finally:
        db.close()
    assert admin_client.get("/api/settings/usage").json() == {
        "available": False, "message": "Usage is measured on the shared service."}
    assert admin_client.get("/api/settings/exports").json() == {"available": False, "exports": []}
    assert admin_client.post("/api/settings/exports").status_code == 409


# --- B2 plans -----------------------------------------------------------------------------------


@pytest.mark.parametrize("used,limit,state", [
    (None, 10, "ok"), (5, None, "ok"), (0, 10, "ok"), (7, 10, "ok"), (8, 10, "near"),
    (10, 10, "near"), (11, 10, "over"),
])
def test_limit_state_edges(used, limit, state):
    from app.platform.plans import state_of

    assert state_of(used, limit) == state


def test_plans_default_change_audit_and_states(alpha, master_client, pdb, monkeypatch):
    from app.platform import plans, usage
    from app.platform.data_models import StorePlan

    # A store made after p0002 without a plan row: the platform's default plan.
    pdb.query(StorePlan).delete()
    pdb.commit()
    assert plans.plan_of("ALPHA") == "custom"
    monkeypatch.setenv("DEFAULT_PLAN", "trial")
    assert plans.plan_of("ALPHA") == "trial"
    monkeypatch.setenv("DEFAULT_PLAN", "nonsense")
    assert plans.plan_of("ALPHA") == "custom"
    monkeypatch.delenv("DEFAULT_PLAN")

    r = master_client.put("/api/platform/stores/ALPHA/plan", json={"plan": "gold"})
    assert r.status_code == 400
    r = master_client.put("/api/platform/stores/ALPHA/plan", json={"plan": "trial"})
    assert r.status_code == 200 and r.json()["plan"]["plan"] == "trial"
    assert "store.plan_changed" in _actions(pdb, "ALPHA")

    usage.write_gauges("ALPHA", {"users": 6, "items": 1700, "storage_bytes": 10})
    states = {s["key"]: s["state"] for s in plans.limit_states("ALPHA")}
    assert states == {"users": "over", "items": "near", "storage_bytes": "ok"}
    # Over a limit never refuses work: the store's own pages still answer.
    _add_user("ALPHA", "u2", "user-pass-22", admin=True)
    c = master_client.__class__(master_client.app)
    tok = store_login(c, "ALPHA", "u2", "user-pass-22").json()["token"]
    own = _bearer(c, tok).get("/api/settings/usage").json()
    assert own["plan"]["state"] == "over"
    assert _bearer(c, tok).get("/api/auth/me").status_code == 200
    summary = {s["store_code"]: s for s in master_client.get("/api/platform/usage/summary").json()}
    assert summary["ALPHA"]["state"] == "over" and summary["ALPHA"]["plan_label"] == "Trial"


def test_p0002_gives_existing_stores_the_custom_plan():
    from app.platform.db import upgrade_platform_db

    name = f"{PREFIX}_p2_{PID}"
    admin = _admin()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = _url_for(name)
    try:
        upgrade_platform_db(url, "p0001")
        eng = create_engine(url)
        with eng.begin() as c:
            c.execute(text(
                "INSERT INTO stores (code, name, status, hosting, db_name, timezone, pos_provider, pack_id, "
                "created_at, created_by, updated_at) VALUES ('OLD1', 'Old', 'active', 'shared', 'x_old1', "
                "'UTC', 'toast', 'p', now(), 't', now())"))
        upgrade_platform_db(url, "p0002")
        with eng.connect() as c:
            assert c.execute(text("SELECT store_code, plan FROM store_plans")).all() == [("OLD1", "custom")]
        eng.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


# --- B3 billing -------------------------------------------------------------------------------------


def test_billing_none_is_a_noop_and_stripe_needs_keys(monkeypatch):
    from app.platform import billing

    assert billing.get_billing().plan_changed("ALPHA", "pro") == {"provider": "none", "sent": False}
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    with pytest.raises(billing.BillingConfigError, match="STRIPE_SECRET_KEY"):
        billing.startup_check()
    monkeypatch.setenv("BILLING_PROVIDER", "paypal")
    with pytest.raises(billing.BillingConfigError):
        billing.startup_check()


def test_stripe_stub_builds_payloads_through_a_fake_transport(monkeypatch):
    import hmac

    from app.platform import billing

    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_1")
    sent = []

    def transport(method, url, headers, form):
        sent.append((method, url, headers, form))
        return 200, {"id": "sub_1"}

    b = billing.get_billing(transport=transport)
    assert b.name == "stripe"
    assert b.plan_changed("ALPHA", "starter")["sent"] is False  # no price: not billed
    assert b.plan_changed("ALPHA", "pro") == {"provider": "stripe", "sent": True, "id": "sub_1"}
    method, url, headers, form = sent[-1]
    assert method == "POST" and url == "https://api.stripe.com/v1/subscriptions"
    assert headers["Authorization"] == "Bearer sk_test_fake" and headers["Idempotency-Key"] == "plan-ALPHA-pro"
    assert form == {"metadata[store_id]": "ALPHA", "metadata[plan]": "pro", "items[0][price]": "price_pro_1"}
    assert b.ensure_customer("ALPHA", name="Alpha", email="a@example.com") == "sub_1"

    payload = b'{"type": "invoice.paid"}'
    ts = int(time.time())
    sig = hmac.new(b"whsec_fake", f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    assert b.handle_webhook(payload, f"t={ts},v1={sig}")["type"] == "invoice.paid"
    with pytest.raises(billing.StripeError):
        b.handle_webhook(payload, f"t={ts},v1=deadbeef")
    with pytest.raises(billing.StripeError):
        billing.verify_webhook(payload, f"t={ts - 3600},v1={sig}", "whsec_fake")

    def failing(method, url, headers, form):
        return 402, {"error": {"message": "card declined"}}

    with pytest.raises(billing.StripeError, match="card declined"):
        billing.get_billing(transport=failing).plan_changed("ALPHA", "pro")


# --- B4 export -------------------------------------------------------------------------------------


def test_export_zip_contents_exclusions_link_and_audit(client, alpha, alpha_admin, pdb):
    from app.storage import get_storage
    from app.tenancy import tenant_session

    get_storage().put_bytes("invoices/2026/inv-1.pdf", b"%PDF-1.4 test", code="ALPHA")
    get_storage().put_bytes("team/2026/photo.jpg", b"\xff\xd8jpeg", code="ALPHA")
    with tenant_session("ALPHA") as db:
        db.execute(text("INSERT INTO setting_changes (key, value, previous_value, changed_at) "
                        "VALUES ('toast_client_secret', 'SUPERSECRET', 'OLDSECRET', now()), "
                        "('store_phone', '555-0100', NULL, now())"))
        db.commit()

    r = alpha_admin.post("/api/settings/exports")
    assert r.status_code == 202, r.text
    export_id = r.json()["id"]
    done = _wait_export("ALPHA", export_id)
    assert done["status"] == "ready", done
    assert done["files"] == 2 and done["tables"] > 10 and done["size_bytes"] > 0

    link = alpha_admin.post(f"/api/settings/exports/{export_id}/link")
    assert link.status_code == 200, link.text
    url = link.json()["url"]
    assert url.startswith(f"/api/settings/exports/{export_id}/download?token=")
    anon = client.__class__(client.app)
    got = anon.get(url)
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    assert "attachment" in got.headers["content-disposition"]
    assert hashlib.sha256(got.content).hexdigest() == done["sha256"]

    zf = zipfile.ZipFile(io.BytesIO(got.content))
    names = set(zf.namelist())
    assert {"manifest.json", "store.json", "README.txt", "files/invoices/2026/inv-1.pdf", "files/team/2026/photo.jpg"} <= names
    assert "tables/users.csv" in names and "tables/user_sessions.csv" not in names
    assert "tables/device_tokens.csv" not in names and "tables/alembic_version.csv" not in names
    assert not any(n.startswith("files/exports/") for n in names)
    users_header = zf.read("tables/users.csv").decode().splitlines()[0].split(",")
    assert "username" in users_header and "password_hash" not in users_header
    blob = b"".join(zf.read(n) for n in names)
    assert b"SUPERSECRET" not in blob and b"OLDSECRET" not in blob and b"$2b$" not in blob
    assert b"555-0100" in zf.read("tables/setting_changes.csv")
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["store_id"] == "ALPHA" and manifest["schema_revision"]
    for t in manifest["tables"]:
        assert hashlib.sha256(zf.read(t["file"])).hexdigest() == t["sha256"]
    for f in manifest["files"]:
        assert hashlib.sha256(zf.read(f["path"])).hexdigest() == f["sha256"]
    users = next(t for t in manifest["tables"] if t["name"] == "users")
    assert users["rows"] == 1
    assert "user_sessions" in manifest["excluded"]["tables"] and "password_hash" in manifest["excluded"]["columns"]["users"]
    store_json = json.loads(zf.read("store.json"))
    assert store_json["store"]["store_id"] == "ALPHA" and store_json["schema_revision"] == manifest["schema_revision"]

    actions = _actions(pdb, "ALPHA")
    for a in ("store.export_requested", "store.export_ready", "store.export_link_created", "store.export_downloaded"):
        assert a in actions, actions
    listed = alpha_admin.get("/api/settings/exports").json()
    assert listed["available"] and listed["exports"][0]["downloads"] == 1


def test_export_links_expire_and_other_stores_and_staff_are_refused(client, alpha, alpha_admin, master_client, pdb):
    from app.platform.data_models import StoreExport

    export_id = alpha_admin.post("/api/settings/exports").json()["id"]
    assert _wait_export("ALPHA", export_id)["status"] == "ready"
    url = alpha_admin.post(f"/api/settings/exports/{export_id}/link").json()["url"]
    anon = client.__class__(client.app)
    assert anon.get(url.replace("token=", "token=x")).status_code == 404
    assert anon.get(f"/api/settings/exports/{export_id}/download").status_code == 404
    # A new link ends the old one.
    url2 = master_client.post(f"/api/platform/stores/ALPHA/exports/{export_id}/link").json()["url"]
    assert anon.get(url).status_code == 404 and anon.get(url2).status_code == 200
    # Expired link.
    row = pdb.get(StoreExport, export_id)
    row.link_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    pdb.commit()
    r = anon.get(url2)
    assert r.status_code == 404 and r.json()["error"] == "link_expired"
    # An expired export cannot get a link.
    row = pdb.get(StoreExport, export_id)
    row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    pdb.commit()
    assert alpha_admin.post(f"/api/settings/exports/{export_id}/link").status_code == 409

    provision("BETA", owner="bob", password="beta-owner-pass")
    beta = _bearer(client, store_login(client, "BETA", "bob", "beta-owner-pass").json()["token"])
    assert beta.post(f"/api/settings/exports/{export_id}/link").status_code == 404  # ALPHA's export
    assert beta.get("/api/settings/exports").json()["exports"] == []
    assert master_client.post(f"/api/platform/stores/BETA/exports/{export_id}/link").status_code == 404
    _add_user("ALPHA", "staff1", "staff-pass-1")
    staff = _bearer(client, store_login(client, "ALPHA", "staff1", "staff-pass-1").json()["token"])
    assert staff.post("/api/settings/exports").status_code == 403
    assert staff.get("/api/settings/exports").status_code == 403


def test_one_export_at_a_time_and_a_failed_export_can_be_retried(alpha, alpha_admin, master_client, monkeypatch, pdb):
    from app.platform import export

    first = export.request_export("ALPHA", actor="t", actor_type="system", run=False)
    r = alpha_admin.post("/api/settings/exports")
    assert r.status_code == 409 and r.json()["error"] == "export_running"
    export.run_export(first["id"])
    assert export.list_exports("ALPHA")[0]["status"] == "ready"

    real_build = export.build_zip
    monkeypatch.setattr(export, "build_zip", lambda code, dest: (_ for _ in ()).throw(RuntimeError("disk full")))
    r = master_client.post("/api/platform/stores/ALPHA/exports")
    assert r.status_code == 202
    failed = _wait_export("ALPHA", r.json()["id"])
    assert failed["status"] == "failed" and "disk full" in failed["error"]
    assert "store.export_failed" in _actions(pdb, "ALPHA")
    monkeypatch.setattr(export, "build_zip", real_build)
    retry = master_client.post("/api/platform/stores/ALPHA/exports")
    assert retry.status_code == 202
    assert _wait_export("ALPHA", retry.json()["id"])["status"] == "ready"


def test_expired_exports_lose_their_files(alpha, pdb):
    from app.platform import export
    from app.platform.data_models import StoreExport
    from app.storage import get_storage

    e = export.request_export("ALPHA", actor="t", actor_type="system", run=False)
    export.run_export(e["id"])
    row = pdb.get(StoreExport, e["id"])
    assert get_storage().exists(row.storage_key, code="ALPHA")
    assert export.expire_old_exports(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=export.KEEP_DAYS + 1)) == 1
    pdb.expire_all()
    assert pdb.get(StoreExport, e["id"]).status == "expired"
    assert not get_storage().exists(row.storage_key, code="ALPHA")


# --- B5 off-boarding ---------------------------------------------------------------------------------


def _db_exists(name):
    eng = _admin()
    try:
        with eng.connect() as c:
            return c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first() is not None
    finally:
        eng.dispose()


def _wait_final(code):
    from app.platform import offboarding

    end = time.monotonic() + 60
    while time.monotonic() < end:
        v = offboarding.view(code)
        if v["final_export"] and v["final_export"]["status"] in ("ready", "failed"):
            return v
        time.sleep(0.1)
    raise AssertionError("final export did not finish")


def test_offboarding_full_cycle_cancel_and_idempotent_purge(client, alpha, master_client, pdb, uploads):
    from app.platform import credentials as creds
    from app.platform.data_models import StoreOffboarding
    from app.platform.models import Store, StoreCredential
    from app.storage import get_storage

    db_name = pdb.execute(select(Store.db_name).where(Store.code == "ALPHA")).scalar_one()
    creds.save_credential("ALPHA", "toast", {"client_id": "cid", "restaurant_guid": "g"}, {"client_secret": "s" * 20},
                          actor="t")
    get_storage().put_bytes("flyers/a.png", b"png", code="ALPHA")

    r = master_client.post("/api/platform/stores/ALPHA/offboarding", json={"confirm": "WRONG"})
    assert r.status_code == 400
    r = master_client.post("/api/platform/stores/ALPHA/offboarding", json={"confirm": "alpha", "reason": "closing"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "scheduled" and r.json()["status"] == "archived"
    v = _wait_final("ALPHA")
    assert v["final_export"]["status"] == "ready" and v["final_export"]["reason"] == "offboarding"
    assert v["final_export"]["expires_at"] == v["purge_after"]  # kept for the whole grace period
    assert master_client.post("/api/platform/stores/ALPHA/offboarding", json={"confirm": "ALPHA"}).status_code == 409

    # Cancel (and restore to suspended so a master can resume), then start again.
    r = master_client.post("/api/platform/stores/ALPHA/offboarding/cancel", json={"restore": True})
    assert r.status_code == 200 and r.json()["state"] == "cancelled" and r.json()["status"] == "suspended"
    assert master_client.post("/api/platform/stores/ALPHA/offboarding/purge", json={"confirm": "ALPHA"}).status_code == 409
    r = master_client.post("/api/platform/stores/ALPHA/offboarding", json={"confirm": "ALPHA"})
    assert r.status_code == 200 and r.json()["state"] == "scheduled"
    _wait_final("ALPHA")

    assert master_client.post("/api/platform/stores/ALPHA/offboarding/purge", json={"confirm": "BETA"}).status_code == 400
    r = master_client.post("/api/platform/stores/ALPHA/offboarding/purge", json={"confirm": "ALPHA"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "deleted" and body["status"] == "archived" and body["purged_at"]
    assert not _db_exists(db_name)
    assert not (uploads / "ALPHA").exists()
    pdb.expire_all()
    assert pdb.execute(select(StoreCredential)).scalars().all() == []
    assert pdb.execute(select(Store).where(Store.code == "ALPHA")).scalar_one().status == "archived"  # tombstone
    assert pdb.get(StoreOffboarding, "ALPHA").state == "deleted"
    # Idempotent.
    again = master_client.post("/api/platform/stores/ALPHA/offboarding/purge", json={"confirm": "ALPHA"})
    assert again.status_code == 200 and again.json()["state"] == "deleted"
    actions = _actions(pdb, "ALPHA")
    for a in ("store.archived", "store.offboarding_started", "store.offboarding_cancelled", "store.purge_started",
              "credential.deleted", "store.data_deleted"):
        assert a in actions, actions
    timeline = [t["action"] for t in again.json()["timeline"]]
    assert timeline[0] == "store.archived" and timeline[-1] == "store.data_deleted"
    # Nothing more can be exported from a deleted store; it stays out of sign-in.
    assert master_client.post("/api/platform/stores/ALPHA/exports").status_code == 409
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code != 200


def test_purge_needs_the_two_step_code_when_the_master_has_one(alpha, master_client, pdb):
    from app.platform import crypto, totp
    from app.platform.models import PlatformUser

    secret = totp.new_secret()
    m = pdb.execute(select(PlatformUser)).scalar_one()
    m.totp_secret_enc = crypto.seal_text(secret, crypto.totp_aad(m.username))
    pdb.commit()
    master_client.post("/api/platform/stores/ALPHA/offboarding", json={"confirm": "ALPHA"})
    _wait_final("ALPHA")
    r = master_client.post("/api/platform/stores/ALPHA/offboarding/purge", json={"confirm": "ALPHA"})
    assert r.status_code == 400 and r.json()["error"] == "totp_required"
    r = master_client.post("/api/platform/stores/ALPHA/offboarding/purge", json={"confirm": "ALPHA", "totp_code": "000000"})
    assert r.status_code == 400 and r.json()["error"] == "totp_invalid"
    r = master_client.post("/api/platform/stores/ALPHA/offboarding/purge",
                           json={"confirm": "ALPHA", "totp_code": totp.now_code(secret)})
    assert r.status_code == 200 and r.json()["state"] == "deleted"


def test_daily_job_deletes_only_due_non_pilot_stores_with_a_final_export(alpha, master_client, pdb):
    from app.platform import offboarding
    from app.platform.data_models import StoreExport, StoreOffboarding
    from app.platform.stores import put_settings

    provision("BETA", owner="bob", password="beta-owner-pass")
    provision("GAMMA", owner="gus", password="gamma-owner-pass")
    put_settings(pdb, "BETA", {"pilot_mode": True}, actor="t", actor_type="system")
    for code in ("ALPHA", "BETA", "GAMMA"):
        assert master_client.post(f"/api/platform/stores/{code}/offboarding", json={"confirm": code}).status_code == 200
        _wait_final(code)
    # GAMMA's final export failed: never deleted automatically.
    pdb.expire_all()
    gamma = pdb.get(StoreOffboarding, "GAMMA")
    pdb.get(StoreExport, gamma.final_export_id).status = "failed"
    pdb.commit()

    assert offboarding.purge_due()["deleted"] == []  # nothing is due yet
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=offboarding.grace_days() + 1)
    out = offboarding.purge_due(later)
    assert out["deleted"] == ["ALPHA"]
    assert out["skipped"] == {"BETA": "pilot_mode", "GAMMA": "no_final_export"}
    assert offboarding.view("BETA")["pilot_mode"] is True and offboarding.view("BETA")["state"] == "scheduled"
    # A master can still delete the pilot store by hand.
    r = master_client.post("/api/platform/stores/BETA/offboarding/purge", json={"confirm": "BETA"})
    assert r.status_code == 200 and r.json()["state"] == "deleted"


def test_grace_days_follow_the_platform_setting(alpha, master_client, monkeypatch):
    monkeypatch.setenv("OFFBOARD_GRACE_DAYS", "3")
    body = master_client.post("/api/platform/stores/ALPHA/offboarding", json={"confirm": "ALPHA"}).json()
    started = dt.datetime.fromisoformat(body["started_at"])
    purge_after = dt.datetime.fromisoformat(body["purge_after"])
    assert body["grace_days"] == 3 and abs((purge_after - started).total_seconds() - 3 * 86400) < 5
    _wait_final("ALPHA")


def test_jobs_are_registered():
    from app import jobqueue
    from app.platform import export, offboarding, usage  # noqa: F401 - registering is an import side effect

    kinds = jobqueue.periodic_jobs()
    assert kinds["usage_gauges"].per_store is True
    assert kinds["offboarding_daily"].per_store is False
    assert jobqueue.handler_for("store_export").max_attempts == 2


def test_responses_have_the_fields_the_website_reads(alpha, alpha_admin, master_client):
    """frontend/src/settings/dataModel.js and the panels read these keys."""
    from app.platform import usage

    usage.record(usage.ORDERS_SYNCED, 3, code="ALPHA")
    usage.flush()
    own = alpha_admin.get("/api/settings/usage").json()
    assert {"available", "store_code", "metrics", "days", "updated_at", "window_days", "plan"} <= set(own)
    assert {"plan", "label", "limits", "limit_states", "state"} <= set(own["plan"])
    assert set(usage.METRICS) <= set(own["metrics"])
    assert {"day", "orders_synced"} <= set(own["days"][0])
    master_client.put("/api/platform/stores/ALPHA/plan", json={"plan": "trial"})
    states = master_client.get("/api/platform/stores/ALPHA/usage").json()["plan"]["limit_states"]
    assert states and {"key", "label", "used", "limit", "unit", "state"} <= set(states[0])

    export_id = alpha_admin.post("/api/settings/exports").json()["id"]
    _wait_export("ALPHA", export_id)
    listed = alpha_admin.get("/api/settings/exports").json()
    assert {"available", "exports", "link_hours", "keep_days"} <= set(listed)
    row = listed["exports"][0]
    assert {"id", "status", "reason", "size_bytes", "tables", "files", "error", "created_at", "expires_at",
            "filename", "downloads"} <= set(row)
    link = alpha_admin.post(f"/api/settings/exports/{export_id}/link").json()
    assert {"url", "expires_at", "filename"} <= set(link)
    off = master_client.get("/api/platform/stores/ALPHA/offboarding").json()
    assert {"status", "state", "purge_after", "purged_at", "final_export", "pilot_mode", "grace_days", "hosting",
            "timeline"} <= set(off)
