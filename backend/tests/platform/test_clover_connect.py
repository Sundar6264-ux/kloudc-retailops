"""Connect with Clover (website-ready track D3): status, the disabled-by-
default OAuth flow with a fake token endpoint, state tampering / expiry /
replay, the token pair refresh (single flight), manual token mode, Test
connection, disconnect, audit rows, and no secret in any response."""

import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.platform import connector_tests, credentials as creds
from app.pos.adapters.clover import auth as cauth
from tests.platform.conftest import needs_b, provision, store_login

ACCESS, REFRESH = "clover-access-AAAA1111", "clover-refresh-BBBB2222"
NEW_ACCESS, NEW_REFRESH = "clover-access-CCCC3333", "clover-refresh-DDDD4444"
MANUAL = "clover-manual-EEEE5555"
SECRETS = (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, MANUAL, "app-secret-FFFF6666")


def _never_leaks(text: str) -> None:
    for value in SECRETS:
        assert value not in text


@pytest.fixture
def store_admin(client):
    provision("ALPHA")
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


class TokenEndpoint:
    """Clover's /oauth/v2/token and /oauth/v2/refresh, faked."""

    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay

    def __call__(self, request):
        import json

        body = json.loads(request.content or b"{}")
        self.calls.append((request.url.path, body))
        if self.delay:
            time.sleep(self.delay)
        exp = int(time.time()) + 1800
        if request.url.path == "/oauth/v2/token":
            if body.get("code") != "good-code" or body.get("client_secret") != "app-secret-FFFF6666":
                return httpx.Response(400, json={"message": "bad code"})
            return httpx.Response(200, json={"access_token": ACCESS, "refresh_token": REFRESH,
                                             "access_token_expiration": exp, "refresh_token_expiration": exp + 86400})
        if request.url.path == "/oauth/v2/refresh":
            if body.get("refresh_token") != REFRESH:
                return httpx.Response(401)
            return httpx.Response(200, json={"access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH,
                                             "access_token_expiration": exp})
        return httpx.Response(404)


@pytest.fixture
def clover_app(monkeypatch):
    monkeypatch.setenv("CLOVER_APP_ID", "APPID123")
    monkeypatch.setenv("CLOVER_APP_SECRET", "app-secret-FFFF6666")
    monkeypatch.setenv("CLOVER_ENVIRONMENT", "sandbox")
    endpoint = TokenEndpoint()
    monkeypatch.setattr(cauth, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(endpoint)))
    return endpoint


def _audit_actions(pdb, code="ALPHA"):
    from sqlalchemy import select

    from app.platform.models import AuditLog

    pdb.expire_all()
    return [r.action for r in pdb.execute(select(AuditLog).where(AuditLog.store_code == code).order_by(AuditLog.id)).scalars()]


@needs_b
def test_off_by_default(store_admin, monkeypatch):
    monkeypatch.delenv("CLOVER_APP_ID", raising=False)
    monkeypatch.delenv("CLOVER_APP_SECRET", raising=False)
    s = store_admin.get("/api/settings/integrations/clover/status")
    assert s.status_code == 200
    assert s.json()["oauth_enabled"] is False and s.json()["connected"] is False and s.json()["environment"] == "sandbox"
    r = store_admin.get("/api/settings/integrations/clover/authorize-url")
    assert r.status_code == 409 and r.json()["error"] == "clover_app_not_configured"
    assert "API token" in r.json()["message"]


@needs_b
def test_oauth_connect_flow(store_admin, clover_app, pdb, client):
    r = store_admin.get("/api/settings/integrations/clover/authorize-url")
    assert r.status_code == 200, r.text
    url = urlsplit(r.json()["url"])
    assert url.netloc == "sandbox.dev.clover.com" and url.path == "/oauth/v2/authorize"
    q = parse_qs(url.query)
    assert q["client_id"] == ["APPID123"]
    assert q["redirect_uri"] == ["https://app.retailops.test/api/settings/integrations/clover/callback"]
    state = q["state"][0]
    # Clover sends the merchant back; the browser may not carry the session.
    anon = client.__class__(client.app)
    cb = anon.get("/api/settings/integrations/clover/callback",
                  params={"code": "good-code", "merchant_id": "TESTMERCH0001", "state": state, "client_id": "APPID123"},
                  follow_redirects=False)
    assert cb.status_code == 303 and cb.headers["location"] == "/#/settings/integrations?clover=connected"
    assert clover_app.calls[0][0] == "/oauth/v2/token"
    public, secrets = creds.stored_values("ALPHA", "clover")
    assert public["mode"] == "oauth" and public["merchant_id"] == "TESTMERCH0001" and public["region"] == "sandbox"
    assert secrets == {"access_token": ACCESS, "refresh_token": REFRESH}
    status = store_admin.get("/api/settings/integrations/clover/status")
    assert status.json()["connected"] is True and status.json()["mode"] == "oauth"
    assert status.json()["last4"] == ACCESS[-4:]
    _never_leaks(status.text + r.text + store_admin.get("/api/settings/integrations").text)
    assert "clover.connected" in _audit_actions(pdb) and "credential.saved" in _audit_actions(pdb)
    # The same state can't be used twice.
    again = anon.get("/api/settings/integrations/clover/callback",
                     params={"code": "good-code", "merchant_id": "TESTMERCH0001", "state": state}, follow_redirects=False)
    assert again.headers["location"] == "/#/settings/integrations?clover=error&reason=invalid"


@needs_b
def test_state_tampered_expired_or_refused(store_admin, clover_app, client):
    anon = client.__class__(client.app)

    def cb(state, code="good-code"):
        return anon.get("/api/settings/integrations/clover/callback",
                        params={"code": code, "merchant_id": "TESTMERCH0001", "state": state},
                        follow_redirects=False).headers["location"]

    good = cauth.make_state("ALPHA", "ann")
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    assert cb(tampered) == "/#/settings/integrations?clover=error&reason=invalid"
    assert cb("") == "/#/settings/integrations?clover=error&reason=invalid"
    old = cauth.make_state("ALPHA", "ann", now=time.time() - cauth.STATE_TTL_SECONDS - 5)
    assert cb(old) == "/#/settings/integrations?clover=error&reason=expired"
    assert cb(cauth.make_state("ALPHA", "ann"), code="bad-code") == "/#/settings/integrations?clover=error&reason=refused"
    assert creds.stored_values("ALPHA", "clover")[1] == {}
    # The merchant said no (Clover sends no code).
    assert cb(cauth.make_state("ALPHA", "ann"), code="") == "/#/settings/integrations?clover=error&reason=denied"


@needs_b
def test_state_is_bound_to_its_store(store_admin, clover_app, client):
    """A state made for ALPHA connects ALPHA, whoever's browser follows it."""
    provision("BETA", owner="bob", password="beta-owner-pass")
    beta = client.__class__(client.app)
    beta.headers.update({"Authorization": "Bearer " + store_login(beta, "BETA", "bob", "beta-owner-pass").json()["token"]})
    state = cauth.make_state("ALPHA", "ann")
    beta.get("/api/settings/integrations/clover/callback",
             params={"code": "good-code", "merchant_id": "TESTMERCH0001", "state": state}, follow_redirects=False)
    assert creds.stored_values("ALPHA", "clover")[1]["access_token"] == ACCESS
    assert creds.stored_values("BETA", "clover")[1] == {}


def _connect_oauth(expires_in=-60):
    import datetime as dt

    creds.save_credential("ALPHA", "clover", {
        "merchant_id": "TESTMERCH0001", "region": "sandbox", "mode": "oauth",
        "token_expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in)).isoformat()},
        {"access_token": ACCESS, "refresh_token": REFRESH}, actor="test", actor_type="system")


def test_refresh_replaces_the_pair_once_under_a_race(clover_app):
    provision("ALPHA")
    clover_app.delay = 0.2
    _connect_oauth()
    results = []
    threads = [threading.Thread(target=lambda: results.append(cauth.refresh_now("ALPHA").access_token)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [NEW_ACCESS] * 4
    assert [c[0] for c in clover_app.calls] == ["/oauth/v2/refresh"]  # single flight
    public, secrets = creds.stored_values("ALPHA", "clover")
    assert secrets == {"access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH}
    assert public["token_expires_at"] > time.strftime("%Y")
    # Not due: no call.
    cauth.refresh_now("ALPHA")
    assert len(clover_app.calls) == 1


def test_force_refresh_after_401_and_token_provider(clover_app):
    provision("ALPHA")
    _connect_oauth(expires_in=1200)
    auth = cauth._from_vault("ALPHA")
    provide, on_401 = cauth.token_provider(auth)
    assert provide() == ACCESS and clover_app.calls == []
    on_401()
    assert provide() == NEW_ACCESS and [c[0] for c in clover_app.calls] == ["/oauth/v2/refresh"]


@needs_b
def test_manual_token_test_connection_and_disconnect(store_admin, pdb, monkeypatch):
    bad = store_admin.post("/api/settings/integrations/clover/manual", json={"merchant_id": "x!", "api_token": MANUAL})
    assert bad.status_code == 400 and bad.json()["field"] == "merchant_id"
    assert store_admin.post("/api/settings/integrations/clover/manual",
                            json={"merchant_id": "TESTMERCH0001", "api_token": "", "region": "us"}).status_code == 400
    assert store_admin.post("/api/settings/integrations/clover/manual",
                            json={"merchant_id": "TESTMERCH0001", "api_token": MANUAL, "region": "mars"}).status_code == 400
    r = store_admin.post("/api/settings/integrations/clover/manual",
                         json={"merchant_id": "testmerch0001", "api_token": MANUAL, "region": "eu"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] and body["mode"] == "token" and body["merchant_id"] == "TESTMERCH0001"
    assert body["region"] == "eu" and body["last4"] == MANUAL[-4:]
    # Same merchant + region, blank token: keeps the saved one.
    keep = store_admin.post("/api/settings/integrations/clover/manual",
                            json={"merchant_id": "TESTMERCH0001", "api_token": "", "region": "eu"})
    assert keep.status_code == 200 and creds.stored_values("ALPHA", "clover")[1]["access_token"] == MANUAL

    seen = []

    def fake(request):
        seen.append(request)
        if request.url.path.endswith("/properties"):
            return httpx.Response(200, json={"timezone": "Europe/Dublin"})
        return httpx.Response(200, json={"name": "Sample Corner Market"})

    monkeypatch.setattr(connector_tests, "_clover_transport", lambda: httpx.MockTransport(fake))
    t = store_admin.post("/api/settings/integrations/clover/test")
    assert t.status_code == 200 and t.json()["ok"] is True
    assert t.json()["message"] == "Connected to Clover: Sample Corner Market (time zone Europe/Dublin)."
    assert seen[0].url.host == "api.eu.clover.com" and seen[0].headers["Authorization"] == f"Bearer {MANUAL}"
    status = store_admin.get("/api/settings/integrations/clover/status").json()
    assert status["status"] == "ok"
    d = store_admin.post("/api/settings/integrations/clover/disconnect")
    assert d.status_code == 200 and d.json()["disconnected"] is True and d.json()["connected"] is False
    assert creds.stored_values("ALPHA", "clover")[1] == {}
    actions = _audit_actions(pdb)
    assert "clover.connected" in actions and "clover.disconnected" in actions and "credential.tested" in actions
    for resp in (r, keep, t, d):
        _never_leaks(resp.text)


@needs_b
def test_staff_and_anonymous_are_refused(client, store_admin):
    from app.auth import hash_password
    from app.models import User
    from app.tenancy import tenant_session

    with tenant_session("ALPHA") as db:
        db.add(User(username="staff1", password_hash=hash_password("staff-pass-1"), is_admin=False, allowed_features="[]"))
        db.commit()
    staff = client.__class__(client.app)
    staff.headers.update({"Authorization": "Bearer " + store_login(staff, "ALPHA", "staff1", "staff-pass-1").json()["token"]})
    anon = client.__class__(client.app)
    for c, code in ((staff, 403), (anon, 401)):
        assert c.get("/api/settings/integrations/clover/status").status_code == code
        assert c.get("/api/settings/integrations/clover/authorize-url").status_code == code
        assert c.post("/api/settings/integrations/clover/manual", json={}).status_code == code
        assert c.post("/api/settings/integrations/clover/disconnect").status_code == code
