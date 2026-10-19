"""Dedicated hosting (contract 12): off by default; with the flag on, a
store moves only after a master approves, and the shared service then
answers store_moved for it."""

import pytest
from sqlalchemy import select

from app.platform.models import AuditLog, Store
from tests.platform.conftest import provision, store_login

NEW_URL = "https://alpha.retailops.test"


def _hosting_routes(client, req_id=1):
    return [
        client.post("/api/platform/stores/ALPHA/hosting-requests", json={"reason": "Big store, own server"}),
        client.post(f"/api/platform/hosting-requests/{req_id}/approve", json={}),
        client.post(f"/api/platform/hosting-requests/{req_id}/start", json={}),
        client.post(f"/api/platform/hosting-requests/{req_id}/complete", json={"api_base_url": NEW_URL}),
        client.post(f"/api/platform/hosting-requests/{req_id}/rollback", json={}),
        client.post(f"/api/platform/hosting-requests/{req_id}/reject", json={}),
    ]


def test_flag_off_answers_409_everywhere(master_client, pdb):
    provision("ALPHA")
    for r in _hosting_routes(master_client):
        assert r.status_code == 409 and r.json()["error"] == "dedicated_hosting_disabled", r.text
    assert master_client.get("/api/platform/hosting-requests").json() == {"enabled": False, "requests": []}
    assert pdb.execute(select(Store.hosting).where(Store.code == "ALPHA")).scalar() == "shared"


def test_flag_on_request_approve_complete_moves_the_store(master_client, client, pdb, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    provision("ALPHA")
    r = master_client.post("/api/platform/stores/ALPHA/hosting-requests",
                           json={"reason": "Big store, own server", "target_api_base_url": "https://evil.example.com"})
    assert r.status_code == 400  # not under the platform domain
    r = master_client.post("/api/platform/stores/ALPHA/hosting-requests", json={"reason": "Big store, own server",
                                                                              "target_api_base_url": NEW_URL})
    assert r.status_code == 200 and r.json()["status"] == "requested"
    rid = r.json()["id"]
    assert master_client.post("/api/platform/stores/ALPHA/hosting-requests",
                              json={"reason": "second one please"}).status_code == 409  # one open per store
    # Not approved yet: complete is refused and nothing moves.
    assert master_client.post(f"/api/platform/hosting-requests/{rid}/complete", json={}).status_code == 409
    assert pdb.execute(select(Store.hosting).where(Store.code == "ALPHA")).scalar() == "shared"
    assert master_client.post(f"/api/platform/hosting-requests/{rid}/approve", json={"note": "ok"}).json()["status"] == "approved"
    assert master_client.post(f"/api/platform/hosting-requests/{rid}/start").json()["status"] == "migrating"
    done = master_client.post(f"/api/platform/hosting-requests/{rid}/complete", json={})
    assert done.status_code == 200 and done.json()["status"] == "done" and done.json()["rollback_until"]
    pdb.expire_all()
    store = pdb.execute(select(Store).where(Store.code == "ALPHA")).scalar_one()
    assert store.hosting == "dedicated" and store.api_base_url == NEW_URL

    # The shared service: lookup points at the new server, login answers store_moved.
    assert client.get("/api/public/stores/ALPHA").json()["api_base_url"] == NEW_URL
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 409 and r.json()["error"] == "store_moved" and r.json()["api_base_url"] == NEW_URL

    # Rollback flips it back.
    rb = master_client.post(f"/api/platform/hosting-requests/{rid}/rollback", json={"note": "slow"})
    assert rb.json()["status"] == "rolled_back"
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 200
    assert client.get("/api/public/stores/ALPHA").json()["api_base_url"] == "https://app.retailops.test"
    acts = list(pdb.execute(select(AuditLog.action).where(AuditLog.action.like("hosting.%")).order_by(AuditLog.id)).scalars())
    assert acts == ["hosting.requested", "hosting.approved", "hosting.migrating", "hosting.completed", "hosting.rolled_back"]


def test_dedicated_node_serves_only_its_store(client, pdb, platform_env, monkeypatch):
    """The dedicated server (DEDICATED_STORE_CODES=ALPHA) refuses every other store."""
    provision("ALPHA")
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    monkeypatch.setattr(platform_env, "dedicated_store_codes", "ALPHA")
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 200
    r = store_login(client, "BRAVO", "bob", "bravo-owner-pass")
    assert r.status_code == 409 and r.json()["error"] == "store_moved"
    assert r.json()["api_base_url"] == "https://app.retailops.test"


def test_rejected_and_cancelled(master_client, platform_env, monkeypatch):
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    provision("ALPHA")
    rid = master_client.post("/api/platform/stores/ALPHA/hosting-requests", json={"reason": "please move"}).json()["id"]
    assert master_client.post(f"/api/platform/hosting-requests/{rid}/reject", json={"note": "no"}).json()["status"] == "rejected"
    assert master_client.post(f"/api/platform/hosting-requests/{rid}/approve").status_code == 409
    rid2 = master_client.post("/api/platform/stores/ALPHA/hosting-requests", json={"reason": "please move"}).json()["id"]
    assert master_client.post(f"/api/platform/hosting-requests/{rid2}/cancel").json()["status"] == "cancelled"
    assert master_client.post(f"/api/platform/hosting-requests/{rid2}/bogus").status_code == 404
