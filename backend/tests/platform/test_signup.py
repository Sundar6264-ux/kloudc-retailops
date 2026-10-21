"""Public sign-up (contract 13.3): validation, honeypot, codes, rate limits."""

import pytest
from sqlalchemy import select

from app.platform.models import AuditLog, SignupRequest
from tests.platform.conftest import provision, signup_body


def test_signup_creates_a_pending_request_with_only_a_hash(client, pdb):
    r = client.post("/api/signup", json=signup_body())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending" and len(body["status_token"]) > 20
    req = pdb.execute(select(SignupRequest)).scalar_one()
    assert req.requested_code == "ALPHA" and req.contact_email == "ann@example.com"
    assert req.owner_password_hash.startswith("$2") and "alpha-owner-pass" not in req.owner_password_hash
    assert req.status_token_hash != body["status_token"] and len(req.status_token_hash) == 64
    audit = pdb.execute(select(AuditLog)).scalar_one()
    assert audit.action == "signup.created" and "alpha-owner-pass" not in audit.details


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"website": "http://spam"}, "website"),
        ({"accept_terms": False}, "accept_terms"),
        ({"store_name": "A"}, "store_name"),
        ({"contact_email": "not-an-email"}, "contact_email"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"pos_provider": "square"}, "pos_provider"),
        ({"owner_username": "a b"}, "owner_username"),
        ({"owner_password": "short"}, "owner_password"),
        ({"owner_password": "ann"}, "owner_password"),
        ({"notes": "x" * 2001}, "notes"),
    ],
)
def test_signup_validation(client, changes, field):
    r = client.post("/api/signup", json=signup_body(**changes))
    assert r.status_code == 400, r.text
    assert r.json()["field"] == field
    assert "alpha-owner-pass" not in r.text


def test_codes_reserved_taken_requested_invalid(client, pdb, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "signup_rate_per_hour", 100)  # every request counts, refused ones too
    assert client.post("/api/signup", json=signup_body(requested_code="x")).json()["error"] == "invalid_code"
    for code in ("platform", "Admin", "kloudc", "TEST"):
        r = client.post("/api/signup", json=signup_body(requested_code=code))
        assert r.status_code == 409 and r.json()["reason"] == "reserved", code
    assert client.post("/api/signup", json=signup_body()).status_code == 202
    r = client.post("/api/signup", json=signup_body(owner_username="other"))
    assert r.status_code == 409 and r.json()["reason"] == "requested"
    provision("BRAVO")
    r = client.post("/api/signup", json=signup_body(requested_code="bravo"))
    assert r.status_code == 409 and r.json()["reason"] == "taken"

    def avail(code):
        return client.get("/api/signup/code-available", params={"code": code}).json()

    assert avail("charlie") == {"code": "CHARLIE", "available": True}
    assert avail("alpha")["reason"] == "requested"
    assert avail("bravo")["reason"] == "taken"
    assert avail("root")["reason"] == "reserved"
    assert avail("!")["reason"] == "invalid"


def test_signup_rate_limits(client, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "signup_rate_per_hour", 2)
    codes = ["aaa1", "aaa2", "aaa3"]
    statuses = [client.post("/api/signup", json=signup_body(requested_code=c)).status_code for c in codes]
    assert statuses == [202, 202, 429]
    r = client.post("/api/signup", json=signup_body(requested_code="aaa4"))
    assert r.json()["error"] == "rate_limited" and int(r.headers["Retry-After"]) > 0
    # Overall limit (other IPs) too.
    monkeypatch.setattr(platform_env, "signup_rate_per_hour", 100)
    monkeypatch.setenv("SIGNUP_RATE_OVERALL_PER_HOUR", "3")
    from app.platform import throttle

    throttle.reset_limiters()
    monkeypatch.setattr(platform_env, "directory_client_ip_header", "X-Test-IP")
    got = [client.post("/api/signup", json=signup_body(requested_code=f"bbb{i}"), headers={"X-Test-IP": f"10.0.0.{i}"}).status_code
           for i in range(4)]
    assert got == [202, 202, 202, 429]


def test_signup_disabled_and_single_mode(client, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "signup_enabled", False)
    assert client.post("/api/signup", json=signup_body()).status_code == 404
    assert client.get("/api/signup/code-available", params={"code": "abc"}).status_code == 404
    monkeypatch.setattr(platform_env, "signup_enabled", True)
    monkeypatch.setattr(platform_env, "tenancy_mode", "single")
    assert client.post("/api/signup", json=signup_body()).status_code == 404


def test_lookup_and_code_check_share_the_lookup_rate_limit(client, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "directory_rate_per_minute", 3)
    got = [client.get("/api/signup/code-available", params={"code": "abc"}).status_code for _ in range(2)]
    got.append(client.get("/api/public/stores/abc").status_code)
    # Pre-production G13: the same Store ID again from this address has its
    # own, higher limit (a store's shared Wi-Fi); a new one is refused.
    got.append(client.get("/api/public/stores/abc").status_code)
    got.append(client.get("/api/public/stores/xyz").status_code)
    assert got == [200, 200, 404, 404, 429]
