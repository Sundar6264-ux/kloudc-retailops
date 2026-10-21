"""One login page: masters with Store ID PLATFORM, lockout, TOTP, scopes
(contract 4.5, 13.1, 13.2; attacks 5, 11)."""

import pytest
from sqlalchemy import select

from app.platform import totp
from app.platform.models import AuditLog, LoginAttempt, PlatformSession
from tests.platform.conftest import MASTER_PASSWORD, master_login, provision, store_login


def test_master_login_cookie_me_logout(client, master, pdb):
    r = client.post("/api/auth/login", json={"store_code": "platform", "username": "OWNER", "password": MASTER_PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "platform" and body["user"]["username"] == "owner" and "token" not in body
    cookie = r.cookies.get("session_token")
    assert cookie and cookie.startswith("PLATFORM.")
    assert "httponly" in r.headers["set-cookie"].lower()
    client.cookies.set("session_token", cookie)  # Secure cookie: http://testserver would not send it back
    row = pdb.execute(select(PlatformSession)).scalar_one()
    assert row.token_hash != cookie and len(row.token_hash) == 64
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json() == {"scope": "platform", "user": body["user"]}
    assert client.get("/api/platform/stores").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.clear()
    client.cookies.set("session_token", cookie)
    assert client.get("/api/platform/stores").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_one_message_for_every_bad_login(client, master):
    provision("ALPHA")
    msgs = set()
    for code, user, pw in [
        ("ALPHA", "ann", "wrong-pass-123"),
        ("ALPHA", "nobody", "alpha-owner-pass"),
        ("NOPE_X", "ann", "alpha-owner-pass"),
        ("PLATFORM", "owner", "wrong-pass-123"),
        ("PLATFORM", "ann", "alpha-owner-pass"),
        ("!!", "ann", "x"),
        ("", "ann", "alpha-owner-pass"),
    ]:
        r = client.post("/api/auth/login", json={"store_code": code, "username": user, "password": pw})
        assert r.status_code == 401, (code, user, r.text)
        msgs.add(r.json()["detail"])
    assert msgs == {"Incorrect store ID, username or password"}


def test_store_admin_cannot_use_platform_routes_and_master_cannot_use_store_routes(client, master):
    provision("ALPHA")
    store_token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    r = client.get("/api/platform/stores", headers={"Authorization": f"Bearer {store_token}"})
    assert r.status_code == 403 and r.json()["error"] == "master_only"
    assert client.get("/api/platform/stores").status_code == 401
    mt = master_login(client)
    for url in ("/api/inventory/items/search?q=x", "/api/auth/users", "/api/settings/integrations"):
        assert client.get(url, headers={"Authorization": f"Bearer {mt}"}).status_code in (401, 403), url


def test_user_lockout_and_ip_lockout(client, master, platform_env, monkeypatch, pdb):
    provision("ALPHA")
    for _ in range(5):
        assert store_login(client, "ALPHA", "ann", "wrong-pass-123").status_code == 401
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 429 and r.json()["error"] == "locked" and r.json()["retry_after"] > 0
    assert int(r.headers["Retry-After"]) > 0
    # Another user of the same store is not locked.
    assert store_login(client, "ALPHA", "zed", "whatever-123").status_code == 401
    assert "login.locked" in list(pdb.execute(select(AuditLog.action)).scalars())
    # Masters too (same table, store_code PLATFORM).
    for _ in range(5):
        client.post("/api/auth/login", json={"store_code": "PLATFORM", "username": "owner", "password": "bad-pass-123"})
    r = client.post("/api/auth/login", json={"store_code": "PLATFORM", "username": "owner", "password": MASTER_PASSWORD})
    assert r.status_code == 429
    # IP lockout across usernames.
    monkeypatch.setattr(platform_env, "login_ip_lockout_failures", 3)
    monkeypatch.setattr(platform_env, "directory_client_ip_header", "X-Test-IP")
    # The header counts only from a trusted proxy (app/platform/client_ip.py);
    # the TestClient's peer stands in for cloudflared here.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    for i in range(3):
        store_login_ip = client.post("/api/auth/login", json={"store_code": "ALPHA", "username": f"u{i}", "password": "bad-pass-1"},
                                     headers={"X-Test-IP": "10.9.9.9"})
        assert store_login_ip.status_code == 401
    r = client.post("/api/auth/login", json={"store_code": "ALPHA", "username": "u9", "password": "bad-pass-1"},
                    headers={"X-Test-IP": "10.9.9.9"})
    assert r.status_code == 429
    assert pdb.execute(select(LoginAttempt).where(LoginAttempt.client_ip == "10.9.9.9")).scalars().all()


def test_totp_enrolment_and_login(client, master_client, platform_env, monkeypatch):
    setup = master_client.post("/api/platform/users/me/totp/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    # A scannable QR code of that same URI, ready for an <img>.
    assert setup["qr_svg"].startswith("data:image/svg+xml")
    bad = master_client.post("/api/platform/users/me/totp/enable", json={"pending": setup["pending"], "code": "000000"})
    assert bad.status_code == 400
    ok = master_client.post("/api/platform/users/me/totp/enable",
                            json={"pending": setup["pending"], "code": totp.now_code(setup["secret"])})
    assert ok.status_code == 200
    master_client.headers.pop("Authorization")
    r = client.post("/api/auth/login", json={"store_code": "PLATFORM", "username": "owner", "password": MASTER_PASSWORD})
    assert r.status_code == 401 and r.json()["error"] == "totp_required"
    r = client.post("/api/auth/login", json={"store_code": "PLATFORM", "username": "owner", "password": MASTER_PASSWORD,
                                             "totp": "123456"})
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect store ID, username or password"
    r = client.post("/api/auth/login", json={"store_code": "PLATFORM", "username": "owner", "password": MASTER_PASSWORD,
                                             "totp": totp.now_code(setup["secret"])})
    assert r.status_code == 200


def test_require_totp_blocks_console_until_enrolled(master_client, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "platform_require_totp", True)
    r = master_client.get("/api/platform/stores")
    assert r.status_code == 403 and r.json()["error"] == "totp_setup_required"
    assert master_client.get("/api/platform/config").status_code == 200
    assert master_client.post("/api/platform/users/me/totp/setup").status_code == 200


def test_masters_users_and_password_change(master_client, client, pdb):
    r = master_client.post("/api/platform/users", json={"username": "helper", "password": "helper-pass-1234",
                                                        "display_name": "Helper"})
    assert r.status_code == 201 and r.json()["role"] == "master"
    assert master_client.post("/api/platform/users", json={"username": "helper", "password": "x" * 12}).status_code == 409
    assert master_client.post("/api/platform/users", json={"username": "h2", "password": "short"}).status_code == 400
    assert [u["username"] for u in master_client.get("/api/platform/users").json()] == ["helper", "owner"]
    other = master_login(client, "helper", "helper-pass-1234")
    r = master_client.post("/api/platform/users/me/password", json={"current": "nope", "new": "new-pass-123456"})
    assert r.status_code == 400
    r = master_client.post("/api/platform/users/me/password", json={"current": MASTER_PASSWORD, "new": "new-pass-123456"})
    assert r.status_code == 200
    assert master_client.get("/api/platform/stores").status_code == 200  # this session stays
    assert client.get("/api/platform/stores", headers={"Authorization": f"Bearer {other}"}).status_code == 200
    acts = list(pdb.execute(select(AuditLog.action)).scalars())
    assert "platform_user.created" in acts and "platform_user.password_changed" in acts


def test_single_mode_login_keeps_m2_behaviour(client, platform_env, monkeypatch):
    """TENANCY_MODE=single: no store code -> today's login; a wrong code -> 401."""
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models import User

    monkeypatch.setattr(platform_env, "tenancy_mode", "single")
    monkeypatch.setattr(platform_env, "store_code", "SOLO")
    db = SessionLocal()
    db.add(User(username="solo", password_hash=hash_password("solo-pass-1"), is_admin=True, allowed_features="[]"))
    db.commit()
    db.close()
    r = client.post("/api/auth/mobile-login", json={"username": "solo", "password": "solo-pass-1"})
    assert r.status_code == 200 and r.json()["scope"] == "store"
    assert client.post("/api/auth/mobile-login", json={"store_code": "solo", "username": "solo",
                                                       "password": "solo-pass-1"}).status_code == 200
    r = client.post("/api/auth/mobile-login", json={"store_code": "OTHER", "username": "solo", "password": "solo-pass-1"})
    assert r.status_code == 401
    r = client.post("/api/auth/mobile-login", json={"username": "solo", "password": "bad"})
    assert r.json()["detail"] == "Incorrect username or password"
    assert client.post("/api/auth/login", json={"store_code": "PLATFORM", "username": "x", "password": "y"}).status_code == 401
