"""Website ready, track A: the console's Masters tab (A2), Platform settings
(A3, incl. the live sign-up switch) and store health for the store detail
drawer (A4). Runs on the platform PostgreSQL harness (tests/platform/conftest.py)."""

import pytest
from sqlalchemy import select, text

from app.platform import totp
from app.platform.models import AuditLog, PlatformSession, PlatformUser
from tests.platform.conftest import MASTER_PASSWORD, master_login, provision, signup_body


@pytest.fixture(autouse=True)
def _clean_platform_settings(platform_env):
    from app.platform import platform_settings as ps
    from app.platform.db import platform_engine

    def wipe():
        with platform_engine().begin() as c:
            c.execute(text("DELETE FROM platform_settings"))
        ps.invalidate_cache()

    wipe()
    yield
    wipe()


def _audit(pdb, action):
    pdb.expire_all()
    return pdb.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).scalars().all()


def _signed_in(client, username, password):
    c = client.__class__(client.app)
    c.headers.update({"Authorization": f"Bearer {master_login(c, username, password)}"})
    return c


# --- A2 Masters -------------------------------------------------------------------------


def test_add_master_gets_a_temporary_password_that_must_be_changed(client, master_client, pdb):
    r = master_client.post("/api/platform/masters", json={"username": "Second.Master", "display_name": "Second"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "second.master" and body["must_change_password"] is True
    temp = body["temporary_password"]
    assert len(temp) >= 12 and r.headers["cache-control"] == "no-store"
    assert [a.target for a in _audit(pdb, "platform_user.created")][-1] == "second.master"
    assert master_client.post("/api/platform/masters", json={"username": "second.master"}).status_code == 409

    second = _signed_in(client, "second.master", temp)
    # Everything but the password / config routes waits for their own password.
    r = second.get("/api/platform/stores")
    assert r.status_code == 403 and r.json()["error"] == "password_change_required"
    assert second.get("/api/platform/config").json()["user"]["must_change_password"] is True
    ok = second.post("/api/platform/users/me/password", json={"current": temp, "new": "a brand new passphrase"})
    assert ok.status_code == 200, ok.text
    assert second.get("/api/platform/stores").status_code == 200
    listed = {m["username"]: m for m in master_client.get("/api/platform/masters").json()["masters"]}
    assert listed["second.master"]["must_change_password"] is False
    assert listed["owner"]["is_me"] is True and listed["second.master"]["is_me"] is False
    assert listed["owner"]["two_step"] is False


def test_remove_master_rules_and_audit(client, master_client, master, pdb):
    me = master_client.get("/api/platform/masters").json()["masters"][0]
    r = master_client.delete(f"/api/platform/masters/{me['id']}")
    assert r.status_code == 400 and r.json()["error"] == "self"

    added = master_client.post("/api/platform/masters", json={"username": "temp.helper"}).json()
    helper = _signed_in(client, "temp.helper", added["temporary_password"])
    r = master_client.delete(f"/api/platform/masters/{added['id']}")
    assert r.status_code == 200 and r.json()["username"] == "temp.helper"
    assert helper.get("/api/platform/config").status_code == 401  # sessions ended
    assert _audit(pdb, "platform_user.removed")[-1].target == "temp.helper"
    assert [m["username"] for m in master_client.get("/api/platform/masters").json()["masters"]] == ["owner"]
    assert master_client.delete(f"/api/platform/masters/{added['id']}").status_code == 404
    # Re-adding the username brings the account back with a new temporary password.
    again = master_client.post("/api/platform/masters", json={"username": "temp.helper"})
    assert again.status_code == 201 and again.json()["must_change_password"] is True
    assert _audit(pdb, "platform_user.reactivated")


def test_never_the_last_master(pdb, master):
    from app.platform import masters as m
    from app.platform.errors import PlatformError

    outsider = PlatformUser(id=-1, username="ghost", password_hash="x", display_name="g", role="master")
    with pytest.raises(PlatformError) as e:
        m.remove_master(pdb, master.id, actor=outsider)
    assert e.value.code == "last_master"


def _enable_two_step(c):
    setup = c.post("/api/platform/users/me/totp/setup").json()
    r = c.post("/api/platform/users/me/totp/enable", json={"pending": setup["pending"], "code": totp.now_code(setup["secret"])})
    assert r.status_code == 200, r.text
    return setup["secret"]


def test_reset_another_masters_two_step_is_audited(client, master_client, pdb):
    added = master_client.post("/api/platform/masters", json={"username": "phone.lost"}).json()
    other = _signed_in(client, "phone.lost", added["temporary_password"])
    other.post("/api/platform/users/me/password", json={"current": added["temporary_password"], "new": "another long passphrase"})
    _enable_two_step(other)
    listed = {m["username"]: m for m in master_client.get("/api/platform/masters").json()["masters"]}
    assert listed["phone.lost"]["two_step"] is True

    me = listed["owner"]["id"]
    assert master_client.post(f"/api/platform/masters/{me}/reset-two-step").json()["error"] == "self"
    r = master_client.post(f"/api/platform/masters/{added['id']}/reset-two-step")
    assert r.status_code == 200 and r.json()["totp_enrolled"] is False
    pdb.expire_all()
    assert pdb.execute(select(PlatformSession).where(PlatformSession.platform_user_id == added["id"])).first() is None
    row = _audit(pdb, "platform_user.two_step_reset")[-1]
    assert row.target == "phone.lost" and row.actor_name == "platform:owner"


def test_moving_two_step_needs_the_current_code(master_client, pdb):
    secret = _enable_two_step(master_client)
    # The first-time set-up route no longer replaces an active second step.
    r = master_client.post("/api/platform/users/me/totp/setup")
    assert r.status_code == 409 and r.json()["error"] == "two_step_on"
    assert master_client.post("/api/platform/masters/me/two-step/move", json={"current_code": "000000"}).status_code == 400
    from app.platform import throttle

    throttle.reset_limiters()  # 5 two-step attempts a minute
    moved = master_client.post("/api/platform/masters/me/two-step/move", json={"current_code": totp.now_code(secret)})
    assert moved.status_code == 200, moved.text
    new = moved.json()
    ok = master_client.post("/api/platform/users/me/totp/enable", json={"pending": new["pending"], "code": totp.now_code(new["secret"])})
    assert ok.status_code == 200
    assert master_client.post("/api/platform/masters/me/two-step/move", json={"current_code": totp.now_code(secret)}).status_code == 400


def test_console_routes_are_masters_only(client):
    store_token = {"Authorization": "Bearer ALPHA.not-a-master-token"}
    for method, path in [
        ("get", "/api/platform/masters"), ("post", "/api/platform/masters"), ("get", "/api/platform/settings"),
        ("patch", "/api/platform/settings"), ("get", "/api/platform/stores/ALPHA/health"),
        ("post", "/api/platform/masters/1/reset-two-step"), ("delete", "/api/platform/masters/1"),
    ]:
        r = getattr(client, method)(path, headers=store_token, **({"json": {}} if method in ("post", "patch") else {}))
        assert r.status_code == 403, (path, r.status_code)
        assert getattr(client, method)(path, **({"json": {}} if method in ("post", "patch") else {})).status_code == 401


# --- A3 Platform settings ------------------------------------------------------------------


def test_settings_precedence_console_over_environment_over_default(master_client, platform_env, monkeypatch):
    from app.platform import platform_settings as ps

    rows = {s["key"]: s for s in master_client.get("/api/platform/settings").json()["settings"]}
    assert list(rows) == list(ps.EDITABLE_KEYS)
    assert rows["offboard_grace_days"]["value"] == 30 and rows["offboard_grace_days"]["source"] == "default"
    assert rows["default_plan"]["choices"][0] == "custom"

    monkeypatch.setenv("OFFBOARD_GRACE_DAYS", "45")
    assert ps.get_platform_setting("offboard_grace_days") == 45  # environment, typed
    r = master_client.patch("/api/platform/settings", json={"offboard_grace_days": 60, "terms_url": "https://kloudc.com/terms"})
    assert r.status_code == 200, r.text
    assert {c["key"] for c in r.json()["changed"]} == {"offboard_grace_days", "terms_url"}
    assert ps.get_platform_setting("offboard_grace_days") == 60  # console wins
    rows = {s["key"]: s for s in r.json()["settings"]}
    assert rows["offboard_grace_days"]["source"] == "console"
    # Reset: back to the environment value.
    r = master_client.delete("/api/platform/settings/offboard_grace_days")
    assert r.status_code == 200 and ps.get_platform_setting("offboard_grace_days") == 45


@pytest.mark.parametrize(
    "body",
    [
        {"support_email": "not-an-email"},
        {"terms_url": "http://plain-http.example/terms"},
        {"offboard_grace_days": 3},
        {"export_link_hours": "twelve"},
        {"signup_enabled": "yes"},
        {"default_plan": "gold"},
        {"platform_require_totp": False},  # security switches stay environment-only
        {},
    ],
)
def test_bad_values_are_refused_and_nothing_is_saved(master_client, body):
    from app.platform import platform_settings as ps

    r = master_client.patch("/api/platform/settings", json={"support_email": "help@kloudc.com", **body} if body else {})
    assert r.status_code == 400, r.text
    assert r.json()["message"]
    assert ps.saved_values() == {}


def test_setting_change_is_audited_with_old_and_new(master_client, pdb):
    master_client.patch("/api/platform/settings", json={"export_link_hours": 48})
    row = _audit(pdb, "platform.setting_changed")[-1]
    assert row.target == "export_link_hours" and row.actor_name == "platform:owner"
    assert '"old": 24' in row.details and '"new": 48' in row.details
    # Saving the same value again changes nothing and writes no audit row.
    master_client.patch("/api/platform/settings", json={"export_link_hours": 48})
    assert len(_audit(pdb, "platform.setting_changed")) == 1


def test_signup_switch_works_without_a_restart(client, master_client):
    r = master_client.patch("/api/platform/settings", json={"signup_enabled": False})
    assert r.status_code == 200
    closed = client.post("/api/signup", json=signup_body())
    assert closed.status_code == 404 and closed.json()["error"] == "signup_disabled"
    assert master_client.get("/api/platform/config").json()["signup_enabled"] is False
    master_client.patch("/api/platform/settings", json={"signup_enabled": True})
    opened = client.post("/api/signup", json=signup_body())
    assert opened.status_code == 202, opened.text


def test_interface_reads_console_value(master_client):
    from app.platform.platform_settings import SUPPORT_EMAIL, get_platform_setting

    master_client.patch("/api/platform/settings", json={"support_email": "Help@KloudC.com"})
    assert get_platform_setting(SUPPORT_EMAIL) == "help@kloudc.com"


# --- A4 store health ------------------------------------------------------------------------


def test_store_health_overview(master_client):
    provision("ALPHA")
    r = master_client.get("/api/platform/stores/alpha/health")
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["code"] == "ALPHA" and h["status"] == "active"
    db = h["database"]
    assert db["reachable"] is True and db["revision"] and isinstance(db["jobs"], list)
    assert isinstance(h["integrations"], list)
    assert all("value" not in i and "secret" not in i for i in h["integrations"])
    assert master_client.get("/api/platform/stores/NOPE_X/health").status_code == 404


def test_store_health_reports_an_unreachable_database(master_client, monkeypatch):
    provision("ALPHA")
    from app.platform import bridge

    def boom(code):
        raise ConnectionError("down")

    monkeypatch.setattr(bridge, "tenant_session", boom)
    h = master_client.get("/api/platform/stores/ALPHA/health").json()
    assert h["database"]["reachable"] is False and h["database"]["error"] == "ConnectionError"


def test_master_password_constant_is_still_valid():
    assert len(MASTER_PASSWORD) >= 12
