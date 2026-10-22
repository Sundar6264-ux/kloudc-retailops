"""Tenancy core on PostgreSQL (docs/PLATFORM_CONTRACT.md 18.2, track B):
requests, sessions, caches, locks and files of two stores never mix."""

import concurrent.futures
import random
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.tenancy.conftest import seed_users


def _client(token):
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_each_token_reads_its_own_store(multi):
    a = seed_users("ALPHA", "alice")
    b = seed_users("BETA", "bob")
    ra = _client(a["alice"][1]).get("/api/auth/me")
    rb = _client(b["bob"][1]).get("/api/auth/me")
    assert ra.status_code == 200 and ra.json()["username"] == "alice"
    assert rb.status_code == 200 and rb.json()["username"] == "bob"
    # Same row id in both databases: still each store's own user.
    assert a["alice"][0] == b["bob"][0] == 1


def test_forged_prefix_is_refused(multi):
    a = seed_users("ALPHA", "alice")
    seed_users("BETA", "bob")
    token = a["alice"][1]
    secret = token.split(".", 1)[1]
    # ALPHA's secret under BETA's code: no such session in BETA's database.
    assert _client(f"BETA.{secret}").get("/api/auth/me").status_code == 401
    # BETA's code with a random secret.
    assert _client("BETA.not-a-real-secret").get("/api/auth/me").status_code == 401
    # Unknown store / no prefix.
    assert _client(f"NOSUCH.{secret}").get("/api/auth/me").status_code == 401
    assert _client(secret).get("/api/auth/me").status_code == 401


def test_header_or_query_naming_another_store_is_409(multi):
    a = seed_users("ALPHA", "alice")
    c = _client(a["alice"][1])
    r = c.get("/api/auth/me", headers={"X-Store": "BETA"})
    assert r.status_code == 409 and r.json()["error"] == "store_mismatch"
    r = c.get("/api/auth/me", params={"store": "BETA"})
    assert r.status_code == 409 and r.json()["error"] == "store_mismatch"
    # Naming its own store is fine.
    assert c.get("/api/auth/me", headers={"X-Store": "alpha"}).json()["username"] == "alice"


def test_no_store_means_401_and_platform_scope_403(multi):
    assert TestClient(app).get("/api/auth/me").status_code == 401
    # A master's token on a store route (/api/auth/me itself answers masters
    # with the platform body - track C, contract 4.5).
    r = _client("PLATFORM.whatever").get("/api/auth/users")
    assert r.status_code == 403 and r.json()["error"] == "platform_scope"
    # Logged-out routes without a store still work (health, static).
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}


def test_inactive_store_tokens_are_refused_with_403(multi):
    from app.tenancy.testing import update_test_store

    a = seed_users("ALPHA", "alice")
    update_test_store("ALPHA", status="suspended")
    r = _client(a["alice"][1]).get("/api/auth/me")
    assert r.status_code == 403 and r.json()["error"] == "store_suspended" and "suspended" in r.json()["detail"]
    update_test_store("ALPHA", status="provisioning")
    r = _client(a["alice"][1]).get("/api/auth/me")
    assert r.status_code == 403 and r.json()["error"] == "store_not_ready"
    update_test_store("ALPHA", status="archived")
    # Pre-production G2: an archived store says so (403 store_archived), so
    # the app can tell "gone" from a normal sign-out.
    r = _client(a["alice"][1]).get("/api/auth/me")
    assert r.status_code == 403 and r.json()["error"] == "store_archived"
    assert r.json()["message"] == "This store is no longer available. Contact the store owner."


def test_public_store_route_takes_store_query(multi):
    # Store ID privacy (2026-10): before sign-in every code, real or unknown,
    # gets the product's neutral branding (only the code is echoed).
    unknown = TestClient(app).get("/api/app/branding", params={"store": "NOSUCH"})
    real = TestClient(app).get("/api/app/branding", params={"store": "beta"})
    assert unknown.status_code == real.status_code == 200
    ju, jr = unknown.json(), real.json()
    assert (ju.pop("store_code"), jr.pop("store_code")) == ("NOSUCH", "BETA")
    assert ju == jr and jr["store_specific"] is False and "Beta Market" not in real.text
    # A store session gets its own look.
    b = seed_users("BETA", "bob")
    body = _client(b["bob"][1]).get("/api/app/branding", params={"store": "beta"}).json()
    assert (body["store_code"], body["name"], body["public_host"]) == ("BETA", "Beta Market", "https://app.retailops.test")
    # No store: product branding (store fields null).
    assert TestClient(app).get("/api/app/branding").json()["store_code"] is None
    # ?store= on a non-public route is ignored without a token (still 401).
    assert TestClient(app).get("/api/auth/me", params={"store": "BETA"}).status_code == 401


def test_dedicated_store_answers_store_moved_only_with_the_flag(multi, monkeypatch):
    from app.config import process_settings
    from app.tenancy.testing import update_test_store

    a = seed_users("ALPHA", "alice")
    update_test_store("ALPHA", hosting="dedicated", api_base_url="https://alpha.retailops.test")
    # Flag off: a dedicated row is treated as shared.
    assert _client(a["alice"][1]).get("/api/auth/me").status_code == 200
    monkeypatch.setattr(process_settings(), "dedicated_hosting_enabled", True)
    r = _client(a["alice"][1]).get("/api/auth/me")
    assert r.status_code == 409
    assert r.json() == {
        "error": "store_moved",
        "message": r.json()["message"],
        "store_code": "ALPHA",
        "api_base_url": "https://alpha.retailops.test",
    }


def test_dedicated_node_serves_only_its_own_store(multi, monkeypatch):
    from app.config import process_settings

    a = seed_users("ALPHA", "alice")
    b = seed_users("BETA", "bob")
    monkeypatch.setattr(process_settings(), "dedicated_store_codes", "BETA")
    r = _client(a["alice"][1]).get("/api/auth/me")
    assert r.status_code == 409 and r.json()["api_base_url"] == "https://app.retailops.test"
    assert _client(b["bob"][1]).get("/api/auth/me").json()["username"] == "bob"


def test_admin_lists_only_its_own_users(multi):
    a = seed_users("ALPHA", "alice", "alfred")
    seed_users("BETA", "bob")
    r = _client(a["alice"][1]).get("/api/auth/users")
    assert r.status_code == 200, r.text
    names = {u["username"] for u in r.json()}
    assert names == {"alice", "alfred"}


def test_pool_bleed_interleaved_requests(multi):
    """300 requests for 3 stores from 16 threads: every answer comes from the
    store its token names (no session, connection or context bleed)."""
    tokens = {}
    for code in ("ALPHA", "BETA", "GAMMA"):
        users = seed_users(code, f"{code.lower()}_admin")
        tokens[code] = users[f"{code.lower()}_admin"][1]
    plan = [random.choice(list(tokens)) for _ in range(300)]
    local = threading.local()

    def call(code):
        clients = getattr(local, "clients", None)
        if clients is None:
            clients = local.clients = {c: _client(t) for c, t in tokens.items()}
        r = clients[code].get("/api/auth/me")
        return code, r.status_code, r.json().get("username")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, plan))
    bad = [r for r in results if r[1] != 200 or r[2] != f"{r[0].lower()}_admin"]
    assert not bad, bad[:5]


def test_identity_mismatch_is_refused(multi):
    from sqlalchemy import create_engine, text

    from app.tenancy import StoreIdentityError, events, tenant_engine
    from app.tenancy.engines import dispose_engine

    seen = []
    listener = lambda action, code, details: seen.append((action, code))  # noqa: E731
    events.add_listener(listener)
    try:
        a = seed_users("ALPHA", "alice")
        dispose_engine("ALPHA")
        # Someone pointed ALPHA's registry row at BETA's database.
        eng = create_engine(multi["ALPHA"])
        with eng.begin() as conn:
            conn.execute(text("UPDATE store_identity SET store_code = 'BETA'"))
        eng.dispose()
        with pytest.raises(StoreIdentityError):
            tenant_engine("ALPHA")
        assert ("store.identity_mismatch", "ALPHA") in seen
        r = _client(a["alice"][1]).get("/api/auth/me")
        assert r.status_code == 503 and r.json()["error"] == "store_unavailable"
    finally:
        events.remove_listener(listener)


def test_instance_id_mismatch_is_refused(multi):
    from app.tenancy import StoreIdentityError, tenant_engine
    from app.tenancy.testing import update_test_store

    update_test_store("BETA", instance_id="0" * 32)
    with pytest.raises(StoreIdentityError):
        tenant_engine("BETA")


def test_uploads_marker_of_another_store_is_refused(multi, tmp_path):
    from app.store.identity import write_uploads_marker
    from app.tenancy import StoreIdentityError, tenant_engine

    write_uploads_marker(tmp_path / "uploads" / "GAMMA", "ALPHA", "x" * 32)
    with pytest.raises(StoreIdentityError):
        tenant_engine("GAMMA")


def test_logout_in_one_store_does_not_touch_another(multi):
    from app.auth import delete_session, lookup_session
    from app.tenancy import tenant_session

    a = seed_users("ALPHA", "alice")
    b = seed_users("BETA", "bob")
    with tenant_session("ALPHA") as db:
        delete_session(db, a["alice"][1])
        assert lookup_session(db, a["alice"][1]) is None
    assert _client(b["bob"][1]).get("/api/auth/me").status_code == 200


def test_sessions_are_stored_hashed(multi):
    from app.models import UserSession
    from app.tenancy import tenant_session, token_hash

    a = seed_users("ALPHA", "alice")
    token = a["alice"][1]
    assert token.startswith("ALPHA.")
    with tenant_session("ALPHA") as db:
        rows = db.query(UserSession).all()
        assert [r.token for r in rows] == [token_hash(token)]
