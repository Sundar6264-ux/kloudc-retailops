"""Settings -> Integrations for the store admin (contract 9, 13.5): masked
views, write-only secrets, Test connection through fakes, Clover placeholder,
demo guard, rate limits; secrets never in any response."""

import json

import pytest

from app.platform import connector_tests
from app.platform.connector_tests import TestResult
from tests.platform.conftest import needs_b, provision, store_login

SECRETS = {
    "toast": {"client_secret": "toast-secret-AAAA1111"},
    "whatsapp": {"api_token": "whapi-token-BBBB2222"},
    "smtp": {"password": "smtp-pass-CCCC3333"},
    "ai": {"api_key": "sk-ant-key-DDDD4444"},
}
PUBLIC = {
    "toast": {"client_id": "cid-1", "restaurant_guid": "guid-1"},
    "whatsapp": {"group_chat_ids": {"store": "1203@g.us"}},
    "smtp": {"host": "smtp.example.test", "port": 587, "ssl": False, "username": "po@example.test", "from_name": "Alpha"},
    "ai": {},
}


def _never_leaks(text: str) -> None:
    for kind_secrets in SECRETS.values():
        for value in kind_secrets.values():
            assert value not in text and value[:-4] not in text


@pytest.fixture
def store_admin(client):
    provision("ALPHA")
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def fake_testers(monkeypatch):
    calls = []

    def make(kind, ok):
        def fake(public, secrets):
            calls.append((kind, dict(public), dict(secrets)))
            return TestResult(ok, f"{kind} fake {'ok' if ok else 'refused'}")

        return fake

    for kind in ("toast", "whatsapp", "smtp", "ai"):
        monkeypatch.setitem(connector_tests.TESTERS, kind, make(kind, kind != "smtp"))
    return calls


@needs_b
def test_full_flow_masks_secrets(store_admin, fake_testers, pdb):
    r = store_admin.get("/api/settings/integrations")
    assert r.status_code == 200
    assert [(x["kind"], x["configured"], x["status"]) for x in r.json()] == [
        ("toast", False, "not_configured"), ("clover", False, "not_configured"), ("whatsapp", False, "not_configured"),
        ("smtp", False, "not_configured"), ("ai", False, "not_configured")]
    texts = []
    for kind in ("toast", "whatsapp", "smtp", "ai"):
        r = store_admin.put(f"/api/settings/integrations/{kind}", json={"public": PUBLIC[kind], "secrets": SECRETS[kind]})
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is True and r.json()["last4"] == list(SECRETS[kind].values())[0][-4:]
        texts.append(r.text)
        t = store_admin.post(f"/api/settings/integrations/{kind}/test")
        assert t.status_code == 200 and t.json()["tested"] == "saved"
        texts.append(t.text)
    listing = store_admin.get("/api/settings/integrations")
    texts.append(listing.text)
    by_kind = {x["kind"]: x for x in listing.json()}
    assert by_kind["toast"]["status"] == "ok" and by_kind["smtp"]["status"] == "failed"
    assert by_kind["smtp"]["last_test_message"] == "smtp fake refused"
    assert fake_testers[0] == ("toast", {**PUBLIC["toast"], "api_base": "https://ws-api.toasttab.com",
                                          "analytics_client_id": ""}, SECRETS["toast"])
    for t in texts:
        _never_leaks(t)
    # The store's own Settings now carry them (track B's overlay + C's source).
    from app.tenancy import store_settings_for

    s = store_settings_for("ALPHA")
    assert s.toast_client_secret == SECRETS["toast"]["client_secret"] and s.smtp_port == 587
    assert json.loads(s.whatsapp_group_chat_ids) == {"store": "1203@g.us"}


@needs_b
def test_unsaved_values_test_and_blank_keeps(store_admin, fake_testers):
    store_admin.put("/api/settings/integrations/ai", json={"secrets": SECRETS["ai"]})
    r = store_admin.post("/api/settings/integrations/ai/test", json={"secrets": {"api_key": "sk-ant-other-EEEE5555"}})
    assert r.json()["tested"] == "unsaved" and fake_testers[-1][2] == {"api_key": "sk-ant-other-EEEE5555"}
    assert store_admin.get("/api/settings/integrations/ai").json()["status"] == "untested"  # not recorded on the row
    store_admin.put("/api/settings/integrations/ai", json={"secrets": {"api_key": ""}})
    from app.platform.credentials import decrypt_secrets

    assert decrypt_secrets("ALPHA", "ai") == SECRETS["ai"]


@needs_b
def test_test_connection_rate_limit_and_errors(store_admin, fake_testers):
    store_admin.put("/api/settings/integrations/ai", json={"secrets": SECRETS["ai"]})
    got = [store_admin.post("/api/settings/integrations/ai/test").status_code for _ in range(6)]
    assert got == [200] * 5 + [429]
    assert store_admin.put("/api/settings/integrations/nope", json={}).status_code == 404
    r = store_admin.put("/api/settings/integrations/smtp", json={"public": {"host": "h", "username": "u", "port": "x"},
                                                           "secrets": {"password": "p-12345678"}})
    assert r.status_code == 400 and r.json()["error"] == "invalid"
    assert store_admin.put("/api/settings/integrations/toast", json={"public": PUBLIC["toast"]}).status_code == 400  # no secret yet


@needs_b
def test_clover_placeholder_and_delete(store_admin):
    r = store_admin.post("/api/settings/integrations/clover/connect")
    assert r.status_code == 501 and r.json() == {"error": "not_available", "message": "Clover connection arrives with M3"}
    assert store_admin.put("/api/settings/integrations/clover", json={}).status_code == 501
    store_admin.put("/api/settings/integrations/ai", json={"secrets": SECRETS["ai"]})
    assert store_admin.delete("/api/settings/integrations/ai").json() == {"kind": "ai", "deleted": True}
    assert store_admin.get("/api/settings/integrations/ai").json()["configured"] is False


@needs_b
def test_staff_and_anonymous_and_demo_are_refused(client, store_admin):
    from app.auth import hash_password
    from app.models import User
    from app.tenancy import tenant_session

    with tenant_session("ALPHA") as db:
        db.add(User(username="staff1", password_hash=hash_password("staff-pass-1"), is_admin=False, allowed_features="[]"))
        db.add(User(username="appreview", password_hash=hash_password("demo-pass-12"), is_admin=True, allowed_features="[]"))
        db.commit()
    anon = client.__class__(client.app)
    assert anon.get("/api/settings/integrations").status_code == 401
    staff = store_login(anon, "ALPHA", "staff1", "staff-pass-1").json()["token"]
    assert anon.get("/api/settings/integrations", headers={"Authorization": f"Bearer {staff}"}).status_code == 403
    demo = store_login(anon, "ALPHA", "appreview", "demo-pass-12").json()["token"]
    r = anon.put("/api/settings/integrations/ai", json={"secrets": SECRETS["ai"]}, headers={"Authorization": f"Bearer {demo}"})
    assert r.status_code == 403


def test_single_mode_answers_platform_mode_required(platform_env, monkeypatch):
    from app.db import SessionLocal
    from tests.conftest import client_for, make_user

    monkeypatch.setattr(platform_env, "tenancy_mode", "single")
    db = SessionLocal()
    try:
        admin_client = client_for(db, make_user(db, "boss1", is_admin=True))
    finally:
        db.close()
    r = admin_client.get("/api/settings/integrations")
    assert r.status_code == 409 and r.json()["error"] == "platform_mode_required"


@needs_b
def test_store_settings_and_hosting_request_by_store_admin(store_admin, master_client, platform_env, monkeypatch):
    r = store_admin.patch("/api/settings/store", json={"store_phone": "555-0199", "brand_color": "#112233"})
    assert r.status_code == 200 and r.json()["settings"]["store_phone"] == "555-0199"
    assert store_admin.patch("/api/settings/store", json={"pos_provider": "clover"}).status_code == 400
    assert store_admin.post("/api/settings/hosting/request", json={"reason": "we grew a lot"}).status_code == 409
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    r = store_admin.post("/api/settings/hosting/request", json={"reason": "we grew a lot"})
    assert r.status_code == 200 and r.json()["requested_by"] == "store:ALPHA:ann"
    assert master_client.get("/api/platform/hosting-requests").json()["requests"][0]["store_code"] == "ALPHA"
