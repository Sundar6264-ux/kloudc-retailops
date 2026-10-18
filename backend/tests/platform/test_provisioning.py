"""Sign-up -> approve -> provisioning end to end on PostgreSQL (contract 10,
13.1, 13.3, 13.4)."""

import json

import pytest
from sqlalchemy import create_engine, select, text

from app.platform import provisioning
from app.platform.models import AuditLog, SignupRequest, Store, StoreSetting
from tests.platform.conftest import STORE_PREFIX, _url_for, provision, signup_body, store_login


def _actions(pdb, code=None):
    q = select(AuditLog.action).order_by(AuditLog.id)
    if code:
        q = q.where(AuditLog.store_code == code)
    return list(pdb.execute(q).scalars())


def test_signup_approve_provisions_a_working_store(client, master_client, pdb, uploads):
    r = client.post("/api/signup", json=signup_body())
    assert r.status_code == 202, r.text
    req_id, status_token = r.json()["request_id"], r.json()["status_token"]
    assert client.get("/api/signup/status", params={"request_id": req_id, "token": status_token}).json()["status"] == "pending"
    assert client.get("/api/signup/status", params={"request_id": req_id, "token": "wrong"}).status_code == 404

    listed = master_client.get("/api/platform/signups").json()
    assert [x["requested_code"] for x in listed] == ["ALPHA"]
    assert "owner_password_hash" not in json.dumps(listed) and "status_token" not in json.dumps(listed)

    r = master_client.post(f"/api/platform/signups/{req_id}/approve", json={"pack_id": "test_toast"})
    assert r.status_code == 202, r.text
    assert r.json() == {"store_code": "ALPHA", "status": "provisioning", "login_url": "https://app.storeops.test/#/login?store=ALPHA"}
    result = provisioning.wait_for("ALPHA", 120)
    assert result and result["status"] == "active", result

    store = pdb.execute(select(Store).where(Store.code == "ALPHA")).scalar_one()
    assert store.status == "active" and store.provisioning_step == "activate"
    assert store.db_name == STORE_PREFIX + "alpha" and store.instance_id and store.schema_revision
    assert store.approved_by == "platform:owner"
    settings = {s.key: json.loads(s.value) for s in pdb.execute(select(StoreSetting).where(StoreSetting.store_id == store.id)).scalars()}
    assert settings == {"store_name": "Alpha Grocery", "store_timezone": "America/Chicago", "pos_provider": "toast",
                        "store_address": "1 Main St", "store_phone": "555-0100", "store_email": "ann@example.com"}
    # The sign-up's password hash is gone from the platform once used.
    assert pdb.get(SignupRequest, req_id).owner_password_hash == "!"

    # The store database: C collation, stamped, the pack, the owner admin.
    eng = create_engine(_url_for(store.db_name))
    with eng.connect() as c:
        assert c.execute(text("SELECT datcollate FROM pg_database WHERE datname = current_database()")).scalar() == "C"
        assert c.execute(text("SELECT store_code FROM store_identity")).scalar() == "ALPHA"
        assert c.execute(text("SELECT username, is_admin FROM users")).all() == [("ann", True)]
        assert c.execute(text("SELECT count(*) FROM setting_changes WHERE key = 'store_pack'")).scalar() == 1
    eng.dispose()
    root = uploads / "ALPHA"
    assert (root / ".store_identity").is_file() and (root / "team").is_dir() and (root / ".keys").is_dir()

    status = client.get("/api/signup/status", params={"request_id": req_id, "token": status_token}).json()
    assert status["status"] == "approved" and status["store_code"] == "ALPHA" and status["store_status"] == "active"

    # The owner signs in with Store ID + username + password.
    login = store_login(client, "alpha", "ANN", "alpha-owner-pass")
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["scope"] == "store" and body["store_code"] == "ALPHA" and body["is_admin"] is True
    assert body["api_base_url"] is None and body["token"]
    assert store_login(client, "ALPHA", "ann", "wrong-password-x").status_code == 401

    acts = _actions(pdb, "ALPHA")
    for a in ("signup.created", "signup.approved", "store.provisioning_started", "store.provisioned", "login.ok", "login.failed"):
        assert a in acts, a


def test_public_store_lookup_matches_directory_shape(client, pdb):
    provision("ALPHA")
    r = client.get("/api/public/stores/alpha")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["schema", "code", "name", "api_base_url", "status", "message", "branding", "min_build", "updated_at"]
    assert body["code"] == "ALPHA" and body["status"] == "active" and body["api_base_url"] == "https://app.storeops.test"
    assert list(body["branding"]) == ["short_name", "logo_url", "icon_url", "primary_color", "secondary_color", "timezone", "pos_name"]
    assert body["branding"]["pos_name"] == "Toast" and body["branding"]["timezone"] == "America/Chicago"
    etag = r.headers["etag"]
    assert client.get("/api/public/stores/ALPHA", headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/api/public/stores/nope_x").json()["error"] == "unknown_store"
    assert client.get("/api/public/stores/!!").json()["error"] == "invalid_code"


def test_approve_with_overrides_and_reject(client, master_client, pdb):
    a = client.post("/api/signup", json=signup_body()).json()["request_id"]
    b = client.post("/api/signup", json=signup_body(requested_code="bravo", owner_username="bob")).json()["request_id"]
    r = master_client.post(f"/api/platform/signups/{b}/reject", json={"note": "Not a grocery"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert master_client.post(f"/api/platform/signups/{b}/approve", json={}).status_code == 409
    r = master_client.post(f"/api/platform/signups/{a}/approve", json={"code": "alpha-two", "timezone": "America/Denver",
                                                                      "pack_id": "nope"})
    assert r.status_code == 400  # unknown pack
    r = master_client.post(f"/api/platform/signups/{a}/approve", json={"code": "PLATFORM"})
    assert r.status_code == 409 and r.json()["reason"] == "reserved"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(provisioning, "start_provisioning", lambda code, actor: None)
        r = master_client.post(f"/api/platform/signups/{a}/approve", json={"code": "alpha-two", "timezone": "America/Denver"})
    assert r.status_code == 202 and r.json()["store_code"] == "ALPHA_TWO"
    store = pdb.execute(select(Store).where(Store.code == "ALPHA_TWO")).scalar_one()
    assert store.timezone == "America/Denver" and store.status == "pending" and store.pack_id == "generic"


@pytest.mark.parametrize("step", ["create_database", "migrate", "store_init", "owner_admin", "settings", "files", "activate"])
def test_failure_at_each_step_then_retry(step, pdb, master_client, monkeypatch):
    real = provisioning.STEP_FUNCS[step]

    def boom(*a, **k):
        raise provisioning.ProvisionError("simulated failure postgresql://u:secretpw@h/db")

    monkeypatch.setitem(provisioning.STEP_FUNCS, step, boom)
    from app.auth import hash_password

    out = provisioning.create_store_manually(code="CHARLIE", name="Charlie", pack_id="test_toast", timezone="UTC",
                                             pos_provider="toast", owner_username="cat",
                                             owner_password_hash=hash_password("charlie-pass-1"), actor="platform:t")
    assert out["status"] == "failed" and out["failed_step"] == step
    store = pdb.execute(select(Store).where(Store.code == "CHARLIE")).scalar_one()
    assert store.status == "failed" and step in store.status_reason and "secretpw" not in store.status_reason
    assert (store.provisioning_step or "reserve") == provisioning.STEPS[provisioning.STEPS.index(step) - 1]
    assert "store.provision_failed" in _actions(pdb, "CHARLIE")
    detail = master_client.get("/api/platform/stores/CHARLIE").json()
    assert detail["status"] == "failed" and detail["provisioning_step"] == store.provisioning_step

    monkeypatch.setitem(provisioning.STEP_FUNCS, step, real)
    r = provisioning.retry_provisioning("CHARLIE", actor="platform:t", run="inline")
    assert r["status"] == "active", r
    pdb.expire_all()
    assert pdb.execute(select(Store.status).where(Store.code == "CHARLIE")).scalar() == "active"


def test_retry_route_and_states(master_client, pdb):
    provision("ALPHA")
    assert master_client.post("/api/platform/stores/ALPHA/retry-provisioning").status_code == 409
    assert master_client.post("/api/platform/stores/NOPE_X/retry-provisioning").status_code == 404


def test_never_adopts_another_stores_database(pdb):
    """Attack 12: a store whose database name is already a stamped database
    of another store is refused at create_database."""
    provision("ALPHA")
    s = pdb.execute(select(Store).where(Store.code == "ALPHA")).scalar_one()
    # A second registry row pointing at ALPHA's database (as if the code
    # check had been bypassed): provisioning refuses it.
    s2 = Store(code="ALPHA2", name="x", status="pending", hosting="shared", db_name=STORE_PREFIX + "alpha2",
               timezone="UTC", pos_provider="toast", pack_id="test_toast", created_by="t", owner_username="z")
    from app.platform.models import utcnow

    s2.created_at = s2.updated_at = utcnow()
    pdb.add(s2)
    pdb.commit()
    eng = create_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{STORE_PREFIX}alpha2" TEMPLATE "{s.db_name}"'))
    eng.dispose()
    out = provisioning.provision_store("ALPHA2")
    assert out["status"] == "failed" and out["failed_step"] == "create_database"
    assert "belongs to store ALPHA" in out["reason"]


def test_manual_store_without_password_gets_one_time_setup_link(client, pdb):
    out = provisioning.create_store_manually(code="DELTA", name="Delta", pack_id="test_toast", timezone="UTC",
                                             pos_provider="toast", owner_username="dee", owner_password_hash=None,
                                             actor="platform:t")
    assert out["status"] == "active" and out["setup_url"].startswith("https://app.storeops.test/#/setup?store=DELTA&token=")
    token = out["setup_url"].split("token=")[1]
    assert store_login(client, "DELTA", "dee", "anything-long-1").status_code == 401  # no password yet
    r = client.post("/api/signup/owner-setup", json={"store_code": "delta", "token": token, "password": "delta-pass-123"})
    assert r.status_code == 200 and r.json()["username"] == "dee"
    assert client.post("/api/signup/owner-setup", json={"store_code": "DELTA", "token": token,
                                                        "password": "delta-pass-456"}).status_code == 404  # one time
    assert store_login(client, "DELTA", "dee", "delta-pass-123").status_code == 200
    assert "store.owner_password_set" in _actions(pdb, "DELTA")


def test_purge_only_failed_or_archived(pdb, master_client):
    provision("ALPHA")
    with pytest.raises(Exception):
        provisioning.purge_store("ALPHA", actor="platform:t")
    r = master_client.post("/api/platform/stores/ALPHA/archive", json={"confirm": "ALPHA"})
    assert r.status_code == 200 and r.json()["status"] == "archived"
    out = provisioning.purge_store("ALPHA", actor="platform:t")
    assert out["dropped_database"] == STORE_PREFIX + "alpha"
    assert pdb.execute(select(Store).where(Store.code == "ALPHA")).first() is None
    eng = create_engine(_url_for("postgres"))
    with eng.connect() as c:
        assert c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": STORE_PREFIX + "alpha"}).first() is None
    eng.dispose()
