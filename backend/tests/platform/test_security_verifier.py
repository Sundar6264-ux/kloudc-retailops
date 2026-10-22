"""Adversarial verifier probes (platform milestone, integrated main):
cross-store isolation and security gaps not covered by
test_isolation_attacks.py. Each test is a probe that once failed or pins a
dismissed attack."""

import datetime as dt
import re

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text

from app.platform import connector_tests
from app.platform.connector_tests import TestResult
from tests.platform.conftest import needs_b, provision, store_login
from tests.platform.test_isolation_attacks import CANARY, STORES, _seed_canaries

pytestmark = needs_b


def _as(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def three(client):
    for code, (user, pw) in STORES.items():
        provision(code, owner=user, password=pw)
    _seed_canaries()
    return {code: store_login(client, code, u, p).json()["token"] for code, (u, p) in STORES.items()}


# --- caches: BRAVO warms every cache first, then ALPHA crawls -------------------------


def test_warm_caches_do_not_leak_between_stores(client, three):
    from app.main import app

    c = client.__class__(client.app, raise_server_exceptions=False)
    urls = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if route.path.startswith(("/api/platform", "/c")):
            continue
        urls.append(re.sub(r"\{[^}]+\}", "1", route.path))
    for url in urls:
        c.get(url, headers=_as(three["BRAVO"]))
    leaks = []
    for url in urls:
        r = c.get(url, headers=_as(three["ALPHA"]))
        if CANARY.lower() in r.text.lower():
            leaks.append(url)
    assert leaks == []


# --- mixed credentials in one request ------------------------------------------------


def test_cookie_of_one_store_and_bearer_of_another_pick_one_store(client, three):
    c = client.__class__(client.app)
    c.cookies.set("session_token", three["ALPHA"])
    r = c.get("/api/auth/me", headers=_as(three["BRAVO"]))
    assert r.status_code == 200 and r.json()["store_code"] == "ALPHA"
    users = c.get("/api/auth/users", headers=_as(three["BRAVO"])).json()
    assert [u["username"] for u in users] == ["ann"]


def test_lower_case_and_padded_prefixes_do_not_cross(client, three):
    secret = three["BRAVO"].split(".", 1)[1]
    for forged in (f"alpha.{secret}", f" ALPHA.{secret}", f"ALPHA.{secret} ", f"ALPHA..{secret}"):
        assert client.get("/api/auth/me", headers=_as(forged)).status_code == 401, forged


# --- integrations: a stored secret never goes to a new address -------------------------


@pytest.fixture
def alpha_admin(client):
    provision("ALPHA")
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    c = client.__class__(client.app)
    c.headers.update(_as(token))
    return c


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake(kind):
        def run(public, secrets):
            calls.append((kind, dict(public), dict(secrets)))
            return TestResult(True, "ok")

        return run

    for kind in ("toast", "whatsapp", "smtp", "ai"):
        monkeypatch.setitem(connector_tests.TESTERS, kind, fake(kind))
    return calls


def test_test_connection_never_sends_the_stored_secret_to_a_new_address(alpha_admin, captured):
    r = alpha_admin.put("/api/settings/integrations/toast", json={
        "public": {"client_id": "cid", "restaurant_guid": "g"}, "secrets": {"client_secret": "stored-toast-SECRET"}})
    assert r.status_code == 200, r.text
    r = alpha_admin.put("/api/settings/integrations/smtp", json={
        "public": {"host": "smtp.example.test", "username": "u@example.test"}, "secrets": {"password": "stored-smtp-SECRET"}})
    assert r.status_code == 200, r.text
    # Unsaved test with another address and no secret typed: refused, the
    # stored secret is not sent there.
    r = alpha_admin.post("/api/settings/integrations/toast/test", json={"public": {"api_base": "https://evil.example.net"}})
    assert r.status_code == 400, r.text
    r = alpha_admin.post("/api/settings/integrations/smtp/test", json={"public": {"host": "mail.evil.example.net"}})
    assert r.status_code == 400, r.text
    assert all("stored-" not in str(s) for _, _, s in captured)
    # Same address (or a secret typed with the new address): fine.
    assert alpha_admin.post("/api/settings/integrations/toast/test",
                            json={"public": {"client_id": "cid2"}}).status_code == 200
    assert alpha_admin.post("/api/settings/integrations/toast/test",
                            json={"public": {"api_base": "https://ws-sandbox-api.eng.toasttab.com"},
                                  "secrets": {"client_secret": "typed-now"}}).status_code == 200


def test_saving_a_new_address_needs_the_secret_again(alpha_admin, captured):
    from app.platform.credentials import decrypt_secrets

    alpha_admin.put("/api/settings/integrations/whatsapp", json={"secrets": {"api_token": "stored-whapi-SECRET"}})
    r = alpha_admin.put("/api/settings/integrations/whatsapp", json={"public": {"base_url": "https://evil.example.net"}})
    assert r.status_code == 400, r.text
    assert decrypt_secrets("ALPHA", "whatsapp") == {"api_token": "stored-whapi-SECRET"}
    r = alpha_admin.put("/api/settings/integrations/whatsapp", json={"public": {"base_url": "https://evil.example.net"},
                                                                     "secrets": {"api_token": "new-token-1234"}})
    assert r.status_code == 200, r.text
    # Other public fields still keep the stored secret.
    r = alpha_admin.put("/api/settings/integrations/whatsapp", json={"public": {"group_chat_ids": {"store": "1@g.us"}}})
    assert r.status_code == 200 and decrypt_secrets("ALPHA", "whatsapp") == {"api_token": "new-token-1234"}


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.5", "169.254.169.254", "192.168.1.1", "[::1]",
                                  "db.internal.localhost", "0.0.0.0"])
def test_smtp_host_may_not_be_a_private_address(alpha_admin, captured, host):
    r = alpha_admin.put("/api/settings/integrations/smtp", json={
        "public": {"host": host, "username": "u@example.test", "port": 5433}, "secrets": {"password": "p-12345678"}})
    assert r.status_code == 400, r.text


def test_smtp_tester_refuses_names_that_resolve_to_private_addresses(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 465))])
    connects = []
    import smtplib

    monkeypatch.setattr(smtplib.SMTP_SSL, "__init__", lambda self, *a, **k: connects.append(a) or None)
    out = connector_tests.run_test("smtp", {"host": "mail.example.com", "port": 465, "ssl": True, "username": "u"},
                                   {"password": "pw-123456"})
    assert out.ok is False and connects == []


# --- provisioning identifiers -----------------------------------------------------------


@pytest.mark.parametrize("code", ['x"; DROP DATABASE postgres; --', "ab\n", "ABC\nDEF", "ÄBC", "abc$1", "a" * 25])
def test_approve_override_code_cannot_inject(master_client, client, pdb, code):
    from tests.platform.conftest import signup_body

    r = client.post("/api/signup", json=signup_body())
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    r = master_client.post(f"/api/platform/signups/{rid}/approve", json={"code": code, "pack_id": "test_toast"})
    assert r.status_code == 400, r.text


def test_identifier_checks_refuse_a_trailing_newline():
    from app.platform import provisioning
    from app.platform.codes import db_name_for
    from app.tenancy.registry import is_valid_store_code

    with pytest.raises(provisioning.ProvisionError):
        provisioning._quote_ident("so_alpha\n")
    with pytest.raises(ValueError):
        db_name_for("ALPHA\n")
    assert not is_valid_store_code("ALPHA\n")


# --- support sessions -------------------------------------------------------------------


def test_support_session_expiry_is_enforced_and_cannot_be_extended(master_client, client, pdb):
    provision("ALPHA")
    r = master_client.post("/api/platform/stores/ALPHA/support-session", json={"reason": "check invoices", "minutes": 500})
    assert r.status_code == 422
    r = master_client.post("/api/platform/stores/ALPHA/support-session", json={"reason": "check invoices", "minutes": 5})
    token = r.json()["token"]
    assert client.get("/api/auth/me", headers=_as(token)).status_code == 200
    from app.models import UserSession
    from app.tenancy import tenant_session, token_hash

    with tenant_session("ALPHA") as db:
        row = db.get(UserSession, token_hash(token))
        assert row.expires_at <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=6)
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/me", headers=_as(token)).status_code == 401
    assert client.post("/api/auth/support-login", json={"token": token}).status_code == 401


# --- the DEMO store on the new routes ------------------------------------------------------


def test_demo_store_cannot_write_platform_settings(client, captured):
    provision("DEMO", owner="appreview", password="demo-owner-pass")
    token = store_login(client, "DEMO", "appreview", "demo-owner-pass").json()["token"]
    h = _as(token)
    for method, url, body in (
        ("put", "/api/settings/integrations/ai", {"secrets": {"api_key": "sk-x-12345678"}}),
        ("post", "/api/settings/integrations/ai/test", {"secrets": {"api_key": "sk-x-12345678"}}),
        ("delete", "/api/settings/integrations/ai", None),
        ("patch", "/api/settings/store", {"store_name": "Hacked"}),
        ("post", "/api/settings/hosting/request", {"reason": "please move us"}),
    ):
        kwargs = {"headers": h}
        if body is not None:
            kwargs["json"] = body
        r = getattr(client, method)(url, **kwargs)
        assert r.status_code == 403, (url, r.status_code, r.text)
    assert captured == []


# --- dedicated hosting: store admins never move a store ---------------------------------


def test_store_admin_cannot_drive_hosting_or_reach_master_routes(alpha_admin, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    r = alpha_admin.post("/api/settings/hosting/request", json={"reason": "we are big now",
                                                                "target_api_base_url": "https://alpha.retailops.test",
                                                                "target_db_host_ref": "anything"})
    assert r.status_code == 200, r.text
    assert r.json()["target_db_host_ref"] is None
    rid = r.json()["id"]
    for action in ("approve", "start", "complete"):
        assert alpha_admin.post(f"/api/platform/hosting-requests/{rid}/{action}",
                                json={"api_base_url": "https://alpha.retailops.test"}).status_code == 403
    assert alpha_admin.put("/api/platform/stores/ALPHA/settings", json={"store_name": "x"}).status_code == 403
    assert alpha_admin.patch("/api/settings/store", json={"hosting": "dedicated"}).status_code == 400


# --- public per-store answers and shared caches --------------------------------------------


def test_public_store_answers_are_not_shared_cacheable_unless_the_url_names_the_store(client, three):
    anon = client.__class__(client.app)
    for url in ("/api/app/branding", "/api/app/legal"):
        by_url = anon.get(url + "?store=BRAVO")
        assert by_url.headers["cache-control"].startswith("public"), url
        assert "X-Store" in by_url.headers["vary"] and "Authorization" in by_url.headers["vary"]
        for kwargs in ({"headers": {"X-Store": "BRAVO"}}, {"headers": _as(three["BRAVO"])}):
            r = anon.get(url, **kwargs)
            assert r.status_code == 200, (url, r.text)
            assert r.headers["cache-control"].startswith("private"), (url, kwargs, r.headers["cache-control"])
    # Store ID privacy (2026-10): without a session the X-Store code is only
    # echoed (neutral body); the store's own look needs its session, and
    # that answer is private even when the URL names the store.
    anon_body = anon.get("/api/app/branding", headers={"X-Store": "BRAVO"}).json()
    assert anon_body["store_code"] == "BRAVO" and anon_body["store_specific"] is False
    for url in ("/api/app/branding", "/api/app/legal"):
        r = anon.get(url + "?store=BRAVO", headers=_as(three["BRAVO"]))
        assert r.headers["cache-control"].startswith("private"), url
    assert anon.get("/api/app/branding", headers=_as(three["BRAVO"])).json()["store_specific"] is True


# --- one browser, two tabs, two stores ------------------------------------------------------


def test_a_tab_pinned_to_its_store_is_refused_after_another_tab_signs_in_elsewhere(client, three):
    """The cookie jar is shared by every tab: signing in to BRAVO replaces
    ALPHA's cookie. A tab still showing ALPHA must not write into BRAVO - the
    website names its store in X-Store (frontend/src/sessionStore.js)."""
    browser = client.__class__(client.app, base_url="https://testserver")  # the cookie is Secure
    assert browser.post("/api/auth/login", json={"store_code": "ALPHA", "username": "ann",
                                                 "password": "alpha-owner-pass"}).status_code == 200
    assert browser.post("/api/auth/login", headers={"X-Store": "ALPHA"},
                        json={"store_code": "BRAVO", "username": "bob", "password": "bravo-owner-pass"}).status_code == 200
    # Without the header the old tab silently reaches BRAVO ...
    assert browser.get("/api/auth/users").json()[0]["username"] != "ann"
    # ... with it, every read and write is refused until the tab reloads.
    r = browser.post("/api/auth/users", headers={"X-Store": "ALPHA"},
                     json={"username": "tab_one_user", "password": "x" * 12, "is_admin": False})
    assert r.status_code == 409 and r.json()["error"] == "store_mismatch"
    assert browser.get("/api/auth/me").json()["store_code"] == "BRAVO"


# --- a store admin's links run on the shared website origin -----------------------------------


@pytest.mark.parametrize("key", ["labels_url", "logo_url", "icon_url"])
@pytest.mark.parametrize("value", ["javascript:alert(document.domain)", "JaVaScRiPt:alert(1)", "data:text/html,<script>1</script>",
                                   "//evil.example.net/x", "http://plain.example.net/x", "https://user:pw@example.net/x",
                                   " javascript:alert(1)"])
def test_store_urls_must_be_https(alpha_admin, master_client, key, value):
    r = alpha_admin.patch("/api/settings/store", json={key: value})
    assert r.status_code == 400, (key, value, r.text)
    r = master_client.put("/api/platform/stores/ALPHA/settings", json={key: value})
    assert r.status_code == 400, (key, value, r.text)


def test_store_urls_https_still_fine(alpha_admin, master_client):
    r = alpha_admin.patch("/api/settings/store", json={"logo_url": "https://cdn.example.com/a/logo.png", "icon_url": ""})
    assert r.status_code == 200, r.text
    # The label site address: only while label printing is on, and only under
    # the platform domain (docs/LABEL_PRINTING_FEATURE.md 1.1, 1.4).
    r = alpha_admin.patch("/api/settings/store", json={"labels_url": "https://labels-alpha.retailops.test"})
    assert r.status_code == 409 and r.json()["error"] == "label_printing_off", r.text
    assert master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": True}).status_code == 200
    r = alpha_admin.patch("/api/settings/store", json={"labels_url": "https://labels.alpha.example.com"})
    assert r.status_code == 400, r.text
    r = alpha_admin.patch("/api/settings/store", json={"labels_url": "https://labels-alpha.retailops.test"})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["labels_url"] == "https://labels-alpha.retailops.test"
