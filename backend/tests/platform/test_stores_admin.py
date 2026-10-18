"""Masters' store operations: suspend, resume, archive, settings, audit,
support sessions (contract 3.1, 3.4, 13.1; attack 11)."""

import json

from sqlalchemy import select

from app.platform.models import AuditLog, Store, SupportSession
from tests.platform.conftest import needs_b, provision, store_login


def test_suspend_resume_archive(master_client, client, pdb):
    provision("ALPHA")
    from app.tenancy import list_stores

    assert [s.code for s in list_stores()] == ["ALPHA"]
    r = master_client.post("/api/platform/stores/ALPHA/suspend", json={"reason": "unpaid", "message": "Back soon"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert [s.code for s in list_stores()] == []  # no jobs for it (B reconciles from this list)
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 403 and r.json()["error"] == "store_suspended" and r.json()["message"] == "Back soon"
    assert store_login(client, "ALPHA", "ann", "wrong-pass-123").status_code == 401  # still no hint without the password
    look = client.get("/api/public/stores/ALPHA").json()
    assert look["status"] == "maintenance" and look["message"] == "Back soon"
    assert master_client.post("/api/platform/stores/ALPHA/suspend", json={"reason": "again"}).status_code == 409

    assert master_client.post("/api/platform/stores/ALPHA/resume").json()["status"] == "active"
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 200
    assert client.get("/api/public/stores/ALPHA").json()["status"] == "active"

    assert master_client.post("/api/platform/stores/ALPHA/archive", json={"confirm": "nope"}).status_code == 400
    assert master_client.post("/api/platform/stores/ALPHA/archive", json={"confirm": "alpha"}).json()["status"] == "archived"
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 401
    assert client.get("/api/public/stores/ALPHA").status_code == 404
    acts = [a for a in pdb.execute(select(AuditLog.action).where(AuditLog.store_code == "ALPHA")).scalars()]
    for a in ("store.suspended", "store.resumed", "store.archived"):
        assert a in acts


def test_not_ready_store_tells_only_its_owner(client, master_client, pdb):
    from tests.platform.conftest import signup_body

    req = client.post("/api/signup", json=signup_body()).json()["request_id"]
    from app.platform import provisioning

    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(provisioning, "start_provisioning", lambda code, actor: None)
        master_client.post(f"/api/platform/signups/{req}/approve", json={})
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 403 and r.json()["error"] == "store_not_ready"
    assert store_login(client, "ALPHA", "ann", "bad-pass-1234").status_code == 401
    assert store_login(client, "ALPHA", "eve", "alpha-owner-pass").status_code == 401
    assert client.get("/api/public/stores/ALPHA").status_code == 404


def test_store_settings_whitelist_and_validation(master_client, pdb):
    provision("ALPHA")
    r = master_client.put("/api/platform/stores/ALPHA/settings",
                          json={"brand_color": "#00aa00", "store_name": "Alpha Two", "ai_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["brand_color"] == "#00AA00" and r.json()["store_name"] == "Alpha Two"
    assert pdb.execute(select(Store.name).where(Store.code == "ALPHA")).scalar() == "Alpha Two"
    for bad in ({"database_url": "x"}, {"toast_client_secret": "x"}, {"cors_origins": "*"}, {"brand_color": "red"},
                {"store_timezone": "Mars/X"}, {"store_name": None}):
        assert master_client.put("/api/platform/stores/ALPHA/settings", json=bad).status_code == 400, bad
    r = master_client.put("/api/platform/stores/ALPHA/settings", json={"brand_color": None})
    assert "brand_color" not in r.json()
    from app.tenancy import store_settings_for

    s = store_settings_for("ALPHA")
    assert s.store_name == "Alpha Two" and s.ai_enabled is True and s.store_code == "ALPHA"
    look = master_client.get("/api/public/stores/ALPHA").json()
    assert look["name"] == "Alpha Two"
    audit = pdb.execute(select(AuditLog).where(AuditLog.action == "store.settings_changed")).scalars().first()
    assert json.loads(audit.details)["changed"]["store_name"] == "Alpha Two"


def test_store_list_detail_audit_packs(master_client):
    provision("ALPHA")
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    assert [s["code"] for s in master_client.get("/api/platform/stores").json()] == ["ALPHA", "BRAVO"]
    assert [s["code"] for s in master_client.get("/api/platform/stores", params={"q": "brav"}).json()] == ["BRAVO"]
    d = master_client.get("/api/platform/stores/alpha").json()
    assert d["code"] == "ALPHA" and d["credentials_configured"] == [] and d["owner_username"] == "ann"
    assert master_client.get("/api/platform/stores/NOPE_X").status_code == 404
    page = master_client.get("/api/platform/audit", params={"store": "BRAVO", "limit": 2}).json()
    assert len(page) == 2 and all(a["store_code"] == "BRAVO" for a in page)
    older = master_client.get("/api/platform/audit", params={"store": "BRAVO", "before": page[-1]["id"]}).json()
    assert all(a["id"] < page[-1]["id"] for a in older)
    assert master_client.get("/api/platform/audit", params={"action": "store.*"}).json()
    packs = {p["pack_id"] for p in master_client.get("/api/platform/packs").json()}
    assert {"generic", "test_toast", "demo"} <= packs and "store_pack.v1.schema" not in packs


def test_platform_write_rate_limit(master_client, platform_env, monkeypatch):
    monkeypatch.setenv("PLATFORM_WRITE_RATE_PER_MINUTE", "2")
    provision("ALPHA")
    got = [master_client.post("/api/platform/stores/ALPHA/resume").status_code for _ in range(3)]
    assert got == [409, 409, 429]


def test_support_session_is_time_limited_and_audited(master_client, client, pdb):
    provision("ALPHA")
    assert master_client.post("/api/platform/stores/ALPHA/support-session", json={"reason": "x"}).status_code == 422
    r = master_client.post("/api/platform/stores/ALPHA/support-session",
                           json={"reason": "Owner asked for help with PO", "minutes": 15})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["username"] == "ann" and out["minutes"] == 15 and out["token"]
    row = pdb.execute(select(SupportSession)).scalar_one()
    assert row.store_code == "ALPHA" and row.token_hash != out["token"]
    assert (row.expires_at - row.started_at).total_seconds() <= 15 * 60 + 5
    acts = list(pdb.execute(select(AuditLog.action)).scalars())
    assert "store.support_session_started" in acts
    assert master_client.post(f"/api/platform/support-sessions/{row.id}/end").status_code == 200
    pdb.expire_all()
    assert pdb.get(SupportSession, row.id).ended_at is not None


@needs_b
def test_support_token_works_then_ends(master_client, client, pdb):
    provision("ALPHA")
    out = master_client.post("/api/platform/stores/ALPHA/support-session", json={"reason": "Owner asked for help"}).json()
    anon = client.__class__(client.app)
    me = anon.get("/api/auth/me", headers={"Authorization": f"Bearer {out['token']}"})
    assert me.status_code == 200 and me.json()["username"] == "ann"
    r = anon.post("/api/auth/support-login", json={"token": out["token"]})
    assert r.status_code == 200 and r.json()["support"] is True
    sid = pdb.execute(select(SupportSession.id)).scalar()
    master_client.post(f"/api/platform/support-sessions/{sid}/end")
    assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {out['token']}"}).status_code == 401
