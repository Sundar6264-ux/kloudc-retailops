"""End-to-end platform flows on PostgreSQL, through the HTTP API (verifier
"flows"): bootstrap a master with the CLI -> two sign-up requests -> the
master approves both at once -> both store databases are provisioned ->
each owner signs in on the one login (Store ID + username + password) ->
Settings > Integrations (Toast saved + Test connection, faked) -> the
store's data sync job runs for that store only, with that store's own
credentials -> the owner adds a staff login -> staff signs in -> the master
suspends the store -> logins and live sessions are refused, the other store
keeps working -> resume.

Nothing here calls Toast: the Toast HTTP client is a recording fake.
"""

import datetime as dt
import json
import threading
import types

import httpx
import pytest
from sqlalchemy import create_engine, select, text

from app.platform import connector_tests, provisioning
from app.platform.connector_tests import TestResult
from app.platform.models import Store
from tests.platform.conftest import _url_for, signup_body, store_login

PW_A = "alpha-owner-pass"
PW_B = "bravo-owner-pass"


def _store_rows(pdb, code):
    pdb.expire_all()
    return pdb.execute(select(Store).where(Store.code == code)).scalar_one()


def _db_scalar(pdb, code, sql):
    store = _store_rows(pdb, code)
    eng = create_engine(_url_for(store.db_name))
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def _signup_two(client):
    ids = {}
    for code, owner, pw, tz in (("alpha", "ann", PW_A, "America/Chicago"), ("bravo", "bob", PW_B, "America/New_York")):
        r = client.post("/api/signup", json=signup_body(store_name=f"{code.title()} Grocery", requested_code=code,
                                                         owner_username=owner, owner_password=pw, timezone=tz,
                                                         contact_email=f"{owner}@example.com"))
        assert r.status_code == 202, r.text
        ids[code.upper()] = r.json()["request_id"]
    return ids


def test_two_approvals_at_once_provision_both_databases(client, master_client, pdb):
    """The master approves two requests back to back: both provisioning
    threads migrate at the same time, and each store gets its own complete
    database."""
    ids = _signup_two(client)
    barrier = threading.Barrier(2)
    real = provisioning.MIGRATOR

    def together(url):
        # Line both migrations up so they really overlap (as two approvals
        # a second apart do on a slow server).
        try:
            barrier.wait(10)
        except threading.BrokenBarrierError:
            pass
        return real(url)

    provisioning.MIGRATOR = together
    try:
        for code, req in ids.items():
            r = master_client.post(f"/api/platform/signups/{req}/approve", json={"pack_id": "test_toast"})
            assert r.status_code == 202, r.text
        results = {code: provisioning.wait_for(code, 180) for code in ids}
    finally:
        provisioning.MIGRATOR = real
    for code, res in results.items():
        assert res and res["status"] == "active", (code, res)
    head = {code: _store_rows(pdb, code).schema_revision for code in ids}
    assert head["ALPHA"] and head["ALPHA"] == head["BRAVO"], head
    assert _db_scalar(pdb, "ALPHA", "SELECT username FROM users") == [("ann",)]
    assert _db_scalar(pdb, "BRAVO", "SELECT username FROM users") == [("bob",)]
    assert store_login(client, "ALPHA", "ann", PW_A).status_code == 200
    assert store_login(client, "BRAVO", "bob", PW_B).status_code == 200


def test_owner_trying_to_sign_in_before_approval_does_not_break_provisioning(client, master_client, pdb):
    """The owner tries the new Store ID while the request is still pending
    (the registry remembers "unknown" for a few seconds); the master then
    approves: the store must still be provisioned."""
    ids = _signup_two(client)
    assert store_login(client, "ALPHA", "ann", PW_A).status_code == 401
    r = master_client.post(f"/api/platform/signups/{ids['ALPHA']}/approve", json={"pack_id": "test_toast"})
    assert r.status_code == 202, r.text
    res = provisioning.wait_for("ALPHA", 180)
    assert res and res["status"] == "active", res
    assert store_login(client, "ALPHA", "ann", PW_A).status_code == 200


# --- the whole flow, two stores ---------------------------------------------------------------


class FakeToast:
    """Records every Toast call (login body, restaurant header) and answers
    with one order whose selection guid names the restaurant."""

    def __init__(self):
        self.logins = []
        self.gets = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authentication/v1/authentication/login"):
            body = json.loads(request.content)
            self.logins.append((body["clientId"], body["clientSecret"]))
            return httpx.Response(200, json={"token": {"accessToken": f"tok-{body['clientId']}", "expiresIn": 3600}})
        guid = request.headers.get("Toast-Restaurant-External-ID")
        self.gets.append((request.url.path, request.headers.get("Authorization"), guid))
        if request.url.path.endswith("/orders/v2/ordersBulk"):
            if request.url.params.get("page") != "1":
                return httpx.Response(200, json=[])
            day = request.url.params["businessDate"]
            paid = f"{day[:4]}-{day[4:6]}-{day[6:]}T18:00:00.000+0000"
            return httpx.Response(200, json=[{
                "guid": f"order-{guid}-{day}",
                "checks": [{"guid": f"check-{guid}-{day}", "paidDate": paid,
                            "selections": [{"guid": f"sel-{guid}-{day}", "item": {"guid": "item-1"},
                                            "quantity": 1, "price": 5.0}]}],
            }])
        return httpx.Response(200, json=[])


@pytest.fixture
def fake_toast(monkeypatch):
    from app.pos.adapters.toast import client as toast_client, orders as toast_orders

    fake = FakeToast()
    real_client = httpx.Client

    def client_factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(fake.handler)
        return real_client(*a, **kw)

    shim = types.SimpleNamespace(**{k: getattr(httpx, k) for k in dir(httpx) if not k.startswith("__")})
    shim.Client = client_factory
    monkeypatch.setattr(toast_client, "httpx", shim)
    monkeypatch.setattr(toast_orders, "httpx", shim)
    return fake


@pytest.fixture
def fake_testers(monkeypatch):
    calls = []

    def make(kind):
        def run(public, secrets):
            calls.append((kind, dict(public), dict(secrets)))
            return TestResult(True, f"{kind} fake ok")

        return run

    for kind in list(connector_tests.TESTERS):
        monkeypatch.setitem(connector_tests.TESTERS, kind, make(kind))
    return calls


@pytest.fixture
def paused_scheduler():
    """A real APScheduler (paused: nothing fires by itself) attached as the
    multi-mode 'main' scheduler, with the main per-store jobs declared."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.scheduler import declare_main_store_jobs
    from app.tenancy.jobs import attach_scheduler, detach_scheduler, unregister_store_jobs

    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    sched.start(paused=True)
    unregister_store_jobs()
    declare_main_store_jobs()
    attach_scheduler("main", sched)
    yield sched
    detach_scheduler("main")
    unregister_store_jobs()
    sched.shutdown(wait=False)


def _new_client(client):
    # https: the session cookie is Secure (SESSION_COOKIE_SECURE default).
    return client.__class__(client.app, base_url="https://testserver")


def _web_login(client, code, username, password):
    """The website's login: cookie session."""
    c = _new_client(client)
    r = c.post("/api/auth/login", json={"store_code": code, "username": username, "password": password})
    return c, r


def _jobs(sched, base):
    return sorted(j.id for j in sched.get_jobs() if j.id.startswith(base + ":"))


def test_full_flow_two_stores(client, pdb, fake_testers, fake_toast, paused_scheduler, monkeypatch):
    from app.platform.__main__ import main as platform_cli

    # 1. Bootstrap the first master with the CLI (owner action), sign in on
    #    the one login page with Store ID PLATFORM.
    monkeypatch.setenv("STOREOPS_MASTER_PASSWORD", "master-pass-123")
    assert platform_cli(["create-master", "--username", "Owner", "--display-name", "The Owner"]) == 0
    master, r = _web_login(client, "platform", "owner", "master-pass-123")
    assert r.status_code == 200 and r.json()["scope"] == "platform", r.text
    assert master.get("/api/auth/me").json()["scope"] == "platform"

    # 2. Two stores ask for access; nothing is provisioned yet.
    ids = _signup_two(client)
    assert sorted(x["requested_code"] for x in master.get("/api/platform/signups").json()) == ["ALPHA", "BRAVO"]
    r = store_login(client, "ALPHA", "ann", PW_A)
    assert r.status_code == 401, r.text  # no store yet: the one message
    assert paused_scheduler.get_jobs() == []

    # 3. The master approves both; both databases are provisioned.
    for code, req in ids.items():
        r = master.post(f"/api/platform/signups/{req}/approve", json={"pack_id": "test_toast"})
        assert r.status_code == 202 and r.json()["store_code"] == code, r.text
    for code in ids:
        res = provisioning.wait_for(code, 180)
        assert res and res["status"] == "active", (code, res)
    # Each approval told the scheduler: one sync job per store now.
    assert _jobs(paused_scheduler, "daily_sales_sync") == ["daily_sales_sync:ALPHA", "daily_sales_sync:BRAVO"]
    # ... each in its own store's timezone (from the sign-up).
    zones = {code: str(paused_scheduler.get_job(f"daily_sales_sync:{code}").trigger.inner.timezone) for code in ids}
    assert zones == {"ALPHA": "America/Chicago", "BRAVO": "America/New_York"}, zones

    # 4. Each owner signs in (website cookie; Store ID typed in any case).
    ann, r = _web_login(client, " alpha ", "Ann", PW_A)
    assert r.status_code == 200 and r.json()["store_code"] == "ALPHA" and r.json()["is_admin"], r.text
    bob, r = _web_login(client, "Bravo", "bob", PW_B)
    assert r.status_code == 200 and r.json()["store_code"] == "BRAVO", r.text
    assert ann.get("/api/auth/me").json()["store_code"] == "ALPHA"
    assert bob.get("/api/auth/me").json()["store_code"] == "BRAVO"
    assert store_login(client, "BRAVO", "ann", PW_A).status_code == 401  # ann is not a BRAVO user

    # 5. Settings > Integrations: each owner saves its own Toast keys and tests them.
    for c, guid, cid, secret in ((ann, "guid-alpha", "cid-alpha", "secret-alpha-1111"),
                                 (bob, "guid-bravo", "cid-bravo", "secret-bravo-2222")):
        r = c.put("/api/settings/integrations/toast",
                  json={"public": {"client_id": cid, "restaurant_guid": guid}, "secrets": {"client_secret": secret}})
        assert r.status_code == 200 and r.json()["last4"] == secret[-4:], r.text
        assert secret not in r.text
        t = c.post("/api/settings/integrations/toast/test")
        assert t.status_code == 200 and t.json()["ok"] is True, t.text
    assert [(k, p["client_id"], s["client_secret"]) for k, p, s in fake_testers] == [
        ("toast", "cid-alpha", "secret-alpha-1111"), ("toast", "cid-bravo", "secret-bravo-2222")]
    assert fake_toast.logins == []  # Test connection never reached "Toast" (fake tester)

    # 6. ALPHA's data sync job runs: ALPHA's keys, ALPHA's restaurant, ALPHA's database only.
    job = paused_scheduler.get_job("daily_sales_sync:ALPHA")
    job.func()
    assert fake_toast.logins and set(fake_toast.logins) == {("cid-alpha", "secret-alpha-1111")}, fake_toast.logins
    assert {g for _, _, g in fake_toast.gets} == {"guid-alpha"}
    assert {a for _, a, _ in fake_toast.gets} == {"Bearer tok-cid-alpha"}
    alpha_runs = _db_scalar(pdb, "ALPHA", "SELECT status FROM job_runs WHERE job_name = 'toast_sales_sync'")
    assert alpha_runs and {s for (s,) in alpha_runs} == {"success"}, alpha_runs
    alpha_sel = {g for (g,) in _db_scalar(pdb, "ALPHA", "SELECT toast_selection_guid FROM orders")}
    assert alpha_sel and all(g.startswith("sel-guid-alpha-") for g in alpha_sel), alpha_sel
    assert _db_scalar(pdb, "BRAVO", "SELECT count(*) FROM job_runs WHERE job_name = 'toast_sales_sync'") == [(0,)]
    assert _db_scalar(pdb, "BRAVO", "SELECT count(*) FROM orders") == [(0,)]
    n_alpha = len(fake_toast.logins)
    paused_scheduler.get_job("daily_sales_sync:BRAVO").func()
    assert set(fake_toast.logins[n_alpha:]) == {("cid-bravo", "secret-bravo-2222")}
    bravo_sel = {g for (g,) in _db_scalar(pdb, "BRAVO", "SELECT toast_selection_guid FROM orders")}
    assert bravo_sel and all(g.startswith("sel-guid-bravo-") for g in bravo_sel), bravo_sel

    # 7. The owner adds a staff login; staff signs in (app login), only in ALPHA.
    r = ann.post("/api/auth/users", json={"username": "sam", "password": "sam-staff-pass", "allowed_features": []})
    assert r.status_code == 200, r.text
    r = store_login(client, "alpha", "sam", "sam-staff-pass")
    assert r.status_code == 200 and r.json()["store_code"] == "ALPHA" and not r.json()["is_admin"], r.text
    staff = _new_client(client)
    staff.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    assert staff.get("/api/auth/me").json()["username"] == "sam"
    assert staff.get("/api/settings/integrations").status_code == 403  # staff never sees credentials
    assert store_login(client, "BRAVO", "sam", "sam-staff-pass").status_code == 401

    # 8. The master suspends ALPHA: live sessions and new logins are refused,
    #    its jobs stop, BRAVO carries on.
    r = master.post("/api/platform/stores/ALPHA/suspend", json={"reason": "unpaid", "message": "Paused - call us"})
    assert r.status_code == 200 and r.json()["status"] == "suspended", r.text
    r = store_login(client, "ALPHA", "sam", "sam-staff-pass")
    assert r.status_code == 403 and r.json()["error"] == "store_suspended", r.text
    r = _web_login(client, "ALPHA", "ann", PW_A)[1]
    assert r.status_code == 403 and r.json()["error"] == "store_suspended", r.text
    for c in (staff, ann):
        r = c.get("/api/auth/me")
        assert r.status_code == 403 and "suspended" in r.text, r.text
        assert c.get("/api/settings/integrations").status_code == 403
    assert _jobs(paused_scheduler, "daily_sales_sync") == ["daily_sales_sync:BRAVO"]
    assert bob.get("/api/auth/me").status_code == 200
    assert store_login(client, "BRAVO", "bob", PW_B).status_code == 200

    # 9. Resume: everything comes back, sessions included.
    assert master.post("/api/platform/stores/ALPHA/resume").json()["status"] == "active"
    assert staff.get("/api/auth/me").status_code == 200
    assert store_login(client, "ALPHA", "sam", "sam-staff-pass").status_code == 200
    assert _jobs(paused_scheduler, "daily_sales_sync") == ["daily_sales_sync:ALPHA", "daily_sales_sync:BRAVO"]


def test_saving_pos_keys_runs_the_store_catch_up_once(client, pdb, paused_scheduler, fake_testers):
    """A new store has no POS keys when it is approved, so the catch-up sync
    the scheduler runs when it first sees a store finds nothing to do. Saving
    the Toast keys in Settings > Integrations is the moment the store can
    sync (on a single-store server: the restart after editing .env), so the
    store's catch-up jobs run again - for that store only."""
    from tests.platform.conftest import provision

    provision("ALPHA")
    provision("BRAVO", owner="bob", password=PW_B)
    startup = lambda: sorted(j.id for j in paused_scheduler.get_jobs() if j.id.startswith("startup_"))  # noqa: E731
    assert "startup_catchup_sync:ALPHA" in startup() and "startup_catchup_sync:BRAVO" in startup()
    for jid in startup():  # they ran (no keys yet: nothing synced)
        paused_scheduler.remove_job(jid)

    token = store_login(client, "ALPHA", "ann", PW_A).json()["token"]
    ann = client.__class__(client.app)
    ann.headers.update({"Authorization": f"Bearer {token}"})
    r = ann.put("/api/settings/integrations/toast",
                json={"public": {"client_id": "cid-a", "restaurant_guid": "guid-a"}, "secrets": {"client_secret": "sec-a-1234"}})
    assert r.status_code == 200, r.text
    assert "startup_catchup_sync:ALPHA" in startup(), startup()
    assert not [j for j in startup() if j.endswith(":BRAVO")], startup()
    # Another integration (AI key) is not a reason to sync the POS again.
    for jid in startup():
        paused_scheduler.remove_job(jid)
    assert ann.put("/api/settings/integrations/ai", json={"secrets": {"api_key": "sk-ant-x-9999"}}).status_code == 200
    assert startup() == []


def test_staff_added_with_stray_spaces_can_sign_in(client):
    """Phones add a space after a word; the login strips what is typed, so a
    username saved with the space could never sign in."""
    from tests.platform.conftest import provision

    provision("ALPHA")
    token = store_login(client, "ALPHA", "ann", PW_A).json()["token"]
    ann = client.__class__(client.app)
    ann.headers.update({"Authorization": f"Bearer {token}"})
    r = ann.post("/api/auth/users", json={"username": " Sam ", "password": "sam-staff-pass"})
    assert r.status_code == 200 and r.json()["username"] == "Sam", r.text
    assert store_login(client, "ALPHA", "Sam ", "sam-staff-pass").status_code == 200
    assert ann.post("/api/auth/users", json={"username": "sam", "password": "sam-staff-pass"}).status_code == 409
    assert ann.post("/api/auth/users", json={"username": "   ", "password": "sam-staff-pass"}).status_code in (400, 422)
