"""Label printing on the shared service (docs/LABEL_PRINTING_FEATURE.md):
the request flow, who may change what, the label site's sign-in check with
X-Store, the website hand-off and the CLI."""

import datetime as dt
import hashlib
import json

import pytest
from sqlalchemy import select, text, update

from app.platform.models import AuditLog, FeatureRequest, LabelHandoff, StoreSetting
from tests.platform.conftest import provision, store_login

LABELS = "https://labels-alpha.retailops.test"


def _as(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def alpha(client):
    """ALPHA with its owner admin (ann) and a staff login (sam)."""
    provision("ALPHA")
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    admin = client.__class__(client.app)
    admin.headers.update(_as(token))
    admin.token = token
    r = admin.post("/api/auth/users", json={"username": "sam", "password": "sam-staff-pass-1", "is_admin": False})
    assert r.status_code in (200, 201), r.text
    staff_token = store_login(client, "ALPHA", "sam", "sam-staff-pass-1").json()["token"]
    staff = client.__class__(client.app)
    staff.headers.update(_as(staff_token))
    staff.token = staff_token
    return admin, staff


def _actions(pdb, prefix):
    pdb.expire_all()
    return list(pdb.execute(select(AuditLog.action).where(AuditLog.action.like(prefix + "%")).order_by(AuditLog.id)).scalars())


def _settings(pdb):
    pdb.expire_all()
    return {r.key: json.loads(r.value) for r in pdb.execute(select(StoreSetting)).scalars()}


# --- request flow -------------------------------------------------------------------------


def test_request_decline_ask_again_approve(alpha, master_client, pdb):
    admin, staff = alpha
    card = admin.get("/api/label-printing").json()
    assert card == {"enabled": False, "state": "off", "url": None, "can_request": True, "request": None}
    assert staff.get("/api/label-printing").json()["can_request"] is False  # not an admin

    r = admin.post("/api/label-printing/request", json={"note": "We print shelf tags daily."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "requested"
    again = admin.post("/api/label-printing/request", json={})
    assert again.status_code == 409 and again.json()["error"] == "already_requested"
    assert again.json()["request"]["status"] == "requested"
    assert staff.post("/api/label-printing/request", json={}).status_code == 403
    assert admin.get("/api/label-printing").json()["request"]["status"] == "requested"

    listing = master_client.get("/api/platform/feature-requests?status=requested").json()
    assert listing["open_count"] == 1
    (row,) = listing["requests"]
    assert row["store_code"] == "ALPHA" and row["feature"] == "label_printing" and row["feature_label"] == "Label printing"
    assert row["requested_by"] == "store:ALPHA:ann" and row["note"] == "We print shelf tags daily."
    assert master_client.get("/api/platform/stores/ALPHA").json()["label_printing"] == {
        "state": "off", "url": None, "options": {}, "open_request_id": row["id"]}

    # Decline needs a note; the store sees it.
    r = master_client.post(f"/api/platform/feature-requests/{row['id']}/decline", json={})
    assert r.status_code == 400
    r = master_client.post(f"/api/platform/feature-requests/{row['id']}/decline", json={"note": "Printer not supported yet."})
    assert r.status_code == 200 and r.json()["status"] == "declined"
    card = admin.get("/api/label-printing").json()
    assert card["request"]["status"] == "declined" and card["request"]["review_note"] == "Printer not supported yet."
    assert master_client.post(f"/api/platform/feature-requests/{row['id']}/approve", json={"labels_url": LABELS}).status_code == 409

    # Ask again: a new row.
    rid = admin.post("/api/label-printing/request", json={}).json()["id"]
    assert rid != row["id"]
    # Approve with no address stored and none given: 400.
    r = master_client.post(f"/api/platform/feature-requests/{rid}/approve", json={})
    assert r.status_code == 400 and r.json()["message"] == "Add the label site address first."
    # A bad address rolls the whole approval back.
    r = master_client.post(f"/api/platform/feature-requests/{rid}/approve", json={"labels_url": "https://labels.example.com"})
    assert r.status_code == 400 and r.json()["field"] == "labels_url"
    pdb.expire_all()
    assert pdb.get(FeatureRequest, rid).status == "requested"
    assert "label_printing_enabled" not in _settings(pdb)
    # Approve: settings and status together.
    r = master_client.post(f"/api/platform/feature-requests/{rid}/approve", json={"labels_url": LABELS, "note": "On now"})
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    stored = _settings(pdb)
    assert stored["label_printing_enabled"] is True and stored["labels_url"] == LABELS
    card = admin.get("/api/label-printing").json()
    assert card["enabled"] is True and card["state"] == "on" and card["url"] == LABELS and card["can_request"] is False
    me = admin.get("/api/auth/me").json()
    assert "labels" in me["store_features"]
    brand = admin.get("/api/app/branding").json()
    assert brand["label_printing"] == {"enabled": True, "url": LABELS, "options": {}} and brand["labels_url"] == LABELS
    r = admin.post("/api/label-printing/request", json={})
    assert r.status_code == 409 and r.json()["error"] == "already_on"
    assert master_client.get("/api/platform/feature-requests?status=requested").json()["open_count"] == 0
    assert _actions(pdb, "feature_request.") == [
        "feature_request.requested", "feature_request.decline", "feature_request.requested", "feature_request.approve"]
    assert "store.settings_changed" in _actions(pdb, "store.settings")


def test_cancel_and_manual_on_closes_the_open_request(alpha, master_client, pdb):
    admin, _ = alpha
    assert admin.post("/api/label-printing/request/cancel").status_code == 409  # nothing open
    admin.post("/api/label-printing/request", json={})
    r = admin.post("/api/label-printing/request/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    rid = admin.post("/api/label-printing/request", json={}).json()["id"]
    r = master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": True, "labels_url": LABELS})
    assert r.status_code == 200, r.text
    pdb.expire_all()
    row = pdb.get(FeatureRequest, rid)
    assert row.status == "approved" and row.review_note.startswith("Turned on by ")
    assert _actions(pdb, "feature_request.")[-1] == "feature_request.approve"


def test_request_note_limit_and_rate_limit(alpha):
    admin, _ = alpha
    assert admin.post("/api/label-printing/request", json={"note": "x" * 1001}).status_code == 400
    codes = []
    for _ in range(6):
        codes.append(admin.post("/api/label-printing/request", json={}).status_code)
        admin.post("/api/label-printing/request/cancel")
    assert codes[-1] == 429 and codes[0] == 200


# --- who can change what --------------------------------------------------------------------


def test_store_admin_address_only_while_on(alpha, master_client):
    admin, _ = alpha
    r = admin.patch("/api/settings/store", json={"labels_url": LABELS})
    assert r.status_code == 409 and r.json()["error"] == "label_printing_off"
    for key, value in (("label_printing_enabled", True), ("label_printing_options", {})):
        r = admin.patch("/api/settings/store", json={key: value})
        assert r.status_code == 400, key
    assert master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": True}).status_code == 200
    assert master_client.get("/api/platform/stores/ALPHA").json()["label_printing"]["state"] == "on_no_address"
    assert "labels" not in admin.get("/api/auth/me").json()["store_features"]
    r = admin.patch("/api/settings/store", json={"labels_url": LABELS})
    assert r.status_code == 200 and r.json()["settings"]["labels_url"] == LABELS
    # Off keeps the address, so on again needs no retyping.
    master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": False})
    lp = master_client.get("/api/platform/stores/ALPHA").json()["label_printing"]
    assert lp["state"] == "off" and lp["url"] == LABELS
    assert "labels" not in admin.get("/api/auth/me").json()["store_features"]


def test_master_options_checked_and_kept(alpha, master_client):
    r = master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_options": {"Bad": 1}})
    assert r.status_code == 400
    r = master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": "yes"})
    assert r.status_code == 400
    opts = {"label_size": "62x33.5", "future_choice": [1, 2]}
    r = master_client.put("/api/platform/stores/ALPHA/settings",
                          json={"label_printing_options": opts, "label_printing_enabled": True, "labels_url": LABELS})
    assert r.status_code == 200, r.text
    assert master_client.get("/api/platform/stores/ALPHA").json()["label_printing"]["options"] == opts


def test_neutral_branding_has_null_label_printing(client):
    provision("ALPHA")
    body = client.get("/api/app/branding?store=ALPHA").json()
    assert body["label_printing"] is None and body["labels_url"] is None


# --- the label site's check --------------------------------------------------------------------


def _turn_on(master_client):
    r = master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": True, "labels_url": LABELS})
    assert r.status_code == 200, r.text


def test_labels_session_with_x_store(alpha, master_client, client):
    admin, staff = alpha
    r = client.get("/api/labels/session", headers={**_as(staff.token), "X-Store": "ALPHA"})
    assert r.status_code == 403 and r.json()["error"] == "label_printing_off"
    _turn_on(master_client)
    r = client.get("/api/labels/session", headers={**_as(staff.token), "X-Store": "ALPHA", "Accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["store_code"] == "ALPHA" and body["user"]["username"] == "sam" and body["can_print"] is True
    assert body["label_printing"]["enabled"] is True and r.headers["cache-control"] == "no-store"
    assert staff.token not in r.text
    r = client.get("/api/labels/session", headers={**_as(staff.token), "X-Store": "BRAVO"})
    assert r.status_code == 409 and r.json()["error"] == "store_mismatch"
    # Signed out: 401 at once.
    staff.post("/api/auth/logout")
    r = client.get("/api/labels/session", headers={**_as(staff.token), "X-Store": "ALPHA"})
    assert r.status_code == 401
    # A made-up token for the store: 401.
    r = client.get("/api/labels/session", headers={**_as("ALPHA." + "x" * 40), "X-Store": "ALPHA"})
    assert r.status_code == 401


def test_demo_store_cannot_print(client, master_client):
    provision("DEMO", owner="demo_owner", password="demo-owner-pass")
    master_client.put("/api/platform/stores/DEMO/settings", json={"demo_mode": True, "label_printing_enabled": True,
                                                                   "labels_url": "https://labels-demo.retailops.test"})
    token = store_login(client, "DEMO", "demo_owner", "demo-owner-pass").json()["token"]
    body = client.get("/api/labels/session", headers={**_as(token), "X-Store": "DEMO"}).json()
    assert body["demo"] is True and body["can_print"] is False
    card = client.get("/api/label-printing", headers=_as(token)).json()
    assert card["can_request"] is False
    assert client.post("/api/label-printing/request", headers=_as(token), json={}).status_code == 403


# --- website hand-off ---------------------------------------------------------------------------


def test_handoff_single_use_and_store_bound(alpha, master_client, client, pdb):
    admin, staff = alpha
    r = staff.post("/api/labels/handoff")
    assert r.status_code == 403 and r.json()["error"] == "label_printing_off"
    _turn_on(master_client)
    r = staff.post("/api/labels/handoff")
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith(LABELS + "/kloudc/enter?code=") and staff.token not in url
    code = url.split("code=", 1)[1]
    pdb.expire_all()
    (row,) = pdb.execute(select(LabelHandoff)).scalars()
    assert row.code_hash == hashlib.sha256(code.encode()).hexdigest() and code not in row.identity
    assert staff.token not in row.identity and row.store_code == "ALPHA"

    anon = client.__class__(client.app)
    assert anon.post("/api/labels/handoff/redeem", json={"code": code}, headers={"X-Store": "BRAVO"}).status_code == 404
    r = anon.post("/api/labels/handoff/redeem", json={"code": code}, headers={"X-Store": "ALPHA"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "sam" and r.json()["store_code"] == "ALPHA" and r.json()["can_print"] is True
    r = anon.post("/api/labels/handoff/redeem", json={"code": code}, headers={"X-Store": "ALPHA"})
    assert r.status_code == 410 and r.json()["error"] == "code_used"
    assert anon.post("/api/labels/handoff/redeem", json={"code": "nope"}, headers={"X-Store": "ALPHA"}).status_code == 404

    # Expired after 60 s.
    code2 = staff.post("/api/labels/handoff").json()["url"].split("code=", 1)[1]
    pdb.execute(update(LabelHandoff).where(LabelHandoff.used_at.is_(None))
                .values(expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)))
    pdb.commit()
    r = anon.post("/api/labels/handoff/redeem", json={"code": code2}, headers={"X-Store": "ALPHA"})
    assert r.status_code == 410

    # Turned off between hand-off and redeem: refused.
    code3 = staff.post("/api/labels/handoff").json()["url"].split("code=", 1)[1]
    master_client.put("/api/platform/stores/ALPHA/settings", json={"label_printing_enabled": False})
    r = anon.post("/api/labels/handoff/redeem", json={"code": code3}, headers={"X-Store": "ALPHA"})
    assert r.status_code == 403 and r.json()["error"] == "label_printing_off"


def test_handoff_rate_limit_and_pruning(alpha, master_client, pdb):
    _, staff = alpha
    _turn_on(master_client)
    codes = [staff.post("/api/labels/handoff").status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    pdb.execute(text("UPDATE label_handoffs SET created_at = created_at - interval '2 days'"))
    pdb.commit()
    from app.platform.throttle import prune_login_attempts

    prune_login_attempts(pdb, 7)
    assert pdb.execute(select(LabelHandoff)).first() is None


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_label_printing(alpha, capsys, pdb):
    from app.platform.__main__ import main

    assert main(["label-printing", "ALPHA", "status"]) == 0
    assert "ALPHA: label printing off" in capsys.readouterr().out
    assert main(["label-printing", "ALPHA", "on"]) == 1  # no address stored
    assert "Add the label site address first." in capsys.readouterr().err
    assert main(["label-printing", "ALPHA", "on", "--url", "https://labels.example.com"]) == 1
    capsys.readouterr()
    assert main(["label-printing", "ALPHA", "on", "--url", LABELS]) == 0
    assert f"ALPHA: label printing on {LABELS}" in capsys.readouterr().out
    assert main(["label-printing", "ALPHA", "off"]) == 0
    assert "label printing off" in capsys.readouterr().out
    assert main(["label-printing", "ALPHA", "on"]) == 0  # the stored address is kept
    assert _settings(pdb)["labels_url"] == LABELS
    pdb.expire_all()
    actors = set(pdb.execute(select(AuditLog.actor_name).where(AuditLog.action == "store.settings_changed")).scalars())
    assert actors and all(a.startswith("cli:") for a in actors)


# --- migration ----------------------------------------------------------------------------------


def test_p0005_down_up_and_partial_unique_index(platform_db_url, pdb):
    from app.platform.db import downgrade_platform_db, upgrade_platform_db

    pdb.close()
    from sqlalchemy import create_engine

    from tests.platform.conftest import _admin, _url_for, PREFIX, PID

    name = f"{PREFIX}_p5_{PID}"
    admin = _admin()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = _url_for(name)
    try:
        assert upgrade_platform_db(url) == "p0005"
        assert downgrade_platform_db(url, "p0004") == "p0004"
        assert upgrade_platform_db(url) == "p0005"
        eng = create_engine(url)
        with eng.begin() as c:
            c.execute(text("INSERT INTO stores (code, name, status, db_name, timezone, pos_provider, pack_id, created_at, "
                           "updated_at, created_by) VALUES ('ALPHA', 'A', 'active', 'x_alpha', 'UTC', 'toast', 'p', now(), "
                           "now(), 't')"))
            sid = c.execute(text("SELECT id FROM stores")).scalar()
            ins = text("INSERT INTO feature_requests (store_id, feature, status, requested_by, created_at) "
                       "VALUES (:s, 'label_printing', :st, 'x', now())")
            c.execute(ins, {"s": sid, "st": "declined"})
            c.execute(ins, {"s": sid, "st": "requested"})
        with pytest.raises(Exception):
            with eng.begin() as c:
                c.execute(ins, {"s": sid, "st": "requested"})
        eng.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


# --- security check (2026-10-22): made-up tokens and codes can't lock a store out ---------------


def test_made_up_tokens_do_not_use_up_the_store_limit(alpha, master_client, client, monkeypatch):
    """Anyone can send GET /api/labels/session with made-up `ALPHA.<junk>`
    tokens. Those must not count against the store's per-minute limit, or a
    stranger could stop every label site sign-in check for the store."""
    import app.label_printing as lp

    _, staff = alpha
    _turn_on(master_client)
    monkeypatch.setattr(lp, "SESSION_PER_STORE_PER_MINUTE", 5)
    for i in range(10):
        r = client.get("/api/labels/session", headers={**_as(f"ALPHA.made{i:03d}" + "x" * 40), "X-Store": "ALPHA"})
        assert r.status_code == 401
    r = client.get("/api/labels/session", headers={**_as(staff.token), "X-Store": "ALPHA"})
    assert r.status_code == 200, r.text
    # Real sessions still count: the store limit holds.
    codes = [client.get("/api/labels/session", headers={**_as(staff.token), "X-Store": "ALPHA"}).status_code
             for _ in range(5)]
    assert codes[-1] == 429 and codes.count(200) == 4


def test_made_up_codes_from_one_address_do_not_block_redeem(alpha, master_client, client, monkeypatch):
    """The redeem limit is per caller address: junk codes sent from the
    internet (through the tunnel, CF-Connecting-IP) don't stop the label site
    (calling from this machine) from redeeming a real code."""
    import app.label_printing as lp

    _, staff = alpha
    _turn_on(master_client)
    monkeypatch.setattr(lp, "REDEEM_PER_MINUTE", 5)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")  # the test client stands in for cloudflared
    anon = client.__class__(client.app)
    for i in range(10):
        anon.post("/api/labels/handoff/redeem", json={"code": f"junk{i:03d}" + "y" * 40},
                  headers={"X-Store": "ALPHA", "CF-Connecting-IP": "203.0.113.9"})
    r = anon.post("/api/labels/handoff/redeem", json={"code": "junk" + "z" * 40},
                  headers={"X-Store": "ALPHA", "CF-Connecting-IP": "203.0.113.9"})
    assert r.status_code == 429
    code = staff.post("/api/labels/handoff").json()["url"].split("code=", 1)[1]
    r = anon.post("/api/labels/handoff/redeem", json={"code": code}, headers={"X-Store": "ALPHA"})
    assert r.status_code == 200, r.text
