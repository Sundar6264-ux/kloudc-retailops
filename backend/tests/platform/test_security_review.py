"""Security review fixes on the platform (docs/SECURITY_REVIEW.md, SR-01..SR-07).

Runs on the platform PostgreSQL harness (tests/platform/conftest.py)."""

import csv
import io
import logging
import zipfile

import pytest
from sqlalchemy import text

from app.platform import totp
from tests.platform.conftest import MASTER_PASSWORD, master_login, provision, store_login


def _bearer(client, token):
    c = client.__class__(client.app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.fixture
def alpha(platform_env):
    return provision("ALPHA")


@pytest.fixture
def alpha_admin(client, alpha):
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 200, r.text
    return _bearer(client, r.json()["token"])


def _enable_two_step(c):
    setup = c.post("/api/platform/users/me/totp/setup").json()
    r = c.post("/api/platform/users/me/totp/enable", json={"pending": setup["pending"], "code": totp.now_code(setup["secret"])})
    assert r.status_code == 200, r.text
    return setup["secret"]


# --- SR-01 CSV formula injection in Export all data ----------------------------------------------


def test_export_csv_cells_never_run_as_formulas(alpha, tmp_path):
    from app.platform import export
    from app.tenancy import tenant_session

    with tenant_session("ALPHA") as db:
        db.execute(text(
            "INSERT INTO setting_changes (key, value, previous_value, changed_at) VALUES "
            "('store_note', '=HYPERLINK(\"https://evil.test\",\"Click\")', '@SUM(A1:A9)', now()), "
            "('store_count', '-3', '+cmd|'' /C calc''!A0', now())"
        ))
        db.commit()
    dest = tmp_path / "x.zip"
    export.build_zip("ALPHA", str(dest))
    with zipfile.ZipFile(dest) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("tables/setting_changes.csv").decode())))
        manifest = zf.read("manifest.json").decode()
        readme = zf.read("README.txt").decode()
    by_key = {r["key"]: r for r in rows}
    assert by_key["store_note"]["value"] == "'=HYPERLINK(\"https://evil.test\",\"Click\")"
    assert by_key["store_note"]["previous_value"] == "'@SUM(A1:A9)"
    assert by_key["store_count"]["value"] == "-3"  # a plain number stays a number
    assert by_key["store_count"]["previous_value"].startswith("'+cmd")
    assert "formula_guard" in manifest and "leading '" in readme


def test_export_cell_unit_rules():
    from app.platform.export import _cell

    assert _cell("=1+2") == "'=1+2"
    assert _cell("\t=1") == "'\t=1"
    assert _cell("-2.5") == "-2.5" and _cell(-4) == -4 and _cell("Basmati") == "Basmati"
    assert _cell({"a": 1}) == '{"a": 1}'


# --- SR-03 a two-step code works once ------------------------------------------------------------


def test_totp_code_is_single_use_per_purpose():
    secret = totp.new_secret()
    code = totp.now_code(secret)
    assert totp.verify_once(secret, code, key="m1", purpose="login")
    assert not totp.verify_once(secret, code, key="m1", purpose="login")  # replay
    assert totp.verify_once(secret, code, key="m1", purpose="step_up")  # another purpose
    assert totp.verify_once(secret, code, key="m2", purpose="login")  # another master
    # An older code after a newer one was used is refused too.
    old = totp.now_code(secret, at=totp.time.time() - totp.STEP)
    assert not totp.verify_once(secret, old, key="m1", purpose="login")
    assert not totp.verify_once(secret, "12345", key="m3")


def test_master_sign_in_code_cannot_be_replayed(client, master_client):
    secret = _enable_two_step(master_client)
    totp.reset_used()
    code = totp.now_code(secret)
    body = {"store_code": "PLATFORM", "username": "owner", "password": MASTER_PASSWORD, "totp": code}
    first = client.__class__(client.app).post("/api/auth/mobile-login", json=body)
    assert first.status_code == 200, first.text
    again = client.__class__(client.app).post("/api/auth/mobile-login", json=body)
    assert again.status_code == 401


# --- SR-07 step-up for the most sensitive master actions -----------------------------------------


def test_sensitive_master_actions_need_a_fresh_code(client, master_client, alpha):
    secret = _enable_two_step(master_client)
    totp.reset_used()

    r = master_client.post("/api/platform/masters", json={"username": "helper.one"})
    assert r.status_code == 400 and r.json()["error"] == "totp_required"
    r = master_client.post("/api/platform/masters", json={"username": "helper.one", "totp_code": "000000"})
    assert r.status_code == 400 and r.json()["error"] == "totp_invalid"
    code = totp.now_code(secret)
    r = master_client.post("/api/platform/masters", json={"username": "helper.one", "totp_code": code})
    assert r.status_code == 201, r.text
    added = r.json()
    # The same code again is refused (single use).
    r = master_client.delete(f"/api/platform/masters/{added['id']}", headers={"X-Two-Step-Code": code})
    assert r.status_code == 400 and r.json()["error"] == "totp_invalid"
    assert master_client.post(f"/api/platform/masters/{added['id']}/reset-two-step").json()["error"] == "totp_required"
    r = master_client.post("/api/platform/stores/ALPHA/support-session", json={"reason": "checking a report"})
    assert r.status_code == 400 and r.json()["error"] == "totp_required"
    r = master_client.post("/api/platform/users", json={"username": "side.door", "password": "a long enough passphrase"})
    assert r.status_code == 400 and r.json()["error"] == "totp_required"

    later = totp.now_code(secret, at=totp.time.time() + totp.STEP)  # the next step (drift is allowed)
    r = master_client.delete(f"/api/platform/masters/{added['id']}", headers={"X-Two-Step-Code": later})
    assert r.status_code == 200, r.text


def test_masters_without_two_step_are_not_asked(master_client):
    # Development / tests (PLATFORM_REQUIRE_TOTP off): no code to give.
    r = master_client.post("/api/platform/masters", json={"username": "helper.two"})
    assert r.status_code == 201, r.text


# --- SR-05 the store never sees the raw export error ---------------------------------------------


def test_store_sees_a_plain_export_error_masters_see_the_detail(alpha, alpha_admin, master_client):
    from app.platform import export

    view = export.request_export("ALPHA", actor="store:ALPHA:ann", actor_type="store_user", run=False)
    export._finish(view["id"], error='connection to server at "10.1.2.3", port 5432 failed: FATAL: role "st_alpha"')
    own = alpha_admin.get("/api/settings/exports").json()["exports"][0]
    assert own["status"] == "failed" and "10.1.2.3" not in own["error"] and "st_alpha" not in own["error"]
    master = master_client.get("/api/platform/stores/ALPHA/exports").json()["exports"][0]
    assert "10.1.2.3" in master["error"]


# --- SR-06 Clover webhook: no forged log lines, unauthenticated calls slowed down ---------------


@pytest.fixture
def webhook_on(monkeypatch):
    from app.pos.adapters.clover import webhooks

    monkeypatch.setenv("CLOVER_WEBHOOKS_ENABLED", "true")
    monkeypatch.setenv("CLOVER_WEBHOOK_AUTH", "hook-auth-GGGG7777")
    webhooks.reset()
    monkeypatch.setattr(webhooks, "enqueue", lambda *a, **k: None)


def test_webhook_verification_code_cannot_forge_log_lines(client, webhook_on, caplog):
    url = "/api/settings/integrations/clover/webhook"
    with caplog.at_level(logging.WARNING):
        r = client.post(url, json={"verificationCode": "abc-123\nERROR forged admin login"})
    assert r.status_code == 200
    line = [m for m in caplog.messages if "verification code" in m][-1]
    assert "\n" not in line and line.endswith("abc-123ERRORforgedadminlogin")
    assert client.post(url, json={"verificationCode": "\n\r "}).status_code == 400


def test_webhook_wrong_auth_is_rate_limited(client, webhook_on):
    url = "/api/settings/integrations/clover/webhook"
    note = {"appId": "A", "merchants": {"M1": [{"objectId": "O:1", "type": "UPDATE", "ts": 1}]}}
    codes = [client.post(url, json=note, headers={"X-Clover-Auth": f"guess-{i}"}).status_code for i in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429


# --- SR-08 store-typed addresses are checked after DNS on every connection ----------------------


@pytest.fixture
def private_dns(monkeypatch):
    import socket

    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        if host in ("intranet.example.com", "smtp.example.org"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
        if host == "public.example.net":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return real(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_egress_refuses_private_targets_on_the_shared_service(platform_env, private_dns, monkeypatch):
    import httpx

    from app.platform import egress
    from app.services import po_send

    with pytest.raises(httpx.ConnectError):
        egress.check_url("https://intranet.example.com/v1/orders")
    egress.check_url("https://public.example.net/v1/orders")  # public: allowed
    egress.check_url("https://ws-api.toasttab.com/orders")  # the POS vendor's own host: not looked up

    class Cfg:
        host, port, ssl = "smtp.example.org", 25, False

    connected = []
    monkeypatch.setattr(po_send.smtplib, "SMTP", lambda *a, **k: connected.append(a))
    with pytest.raises(egress.EgressRefused):
        po_send._smtp_connect(Cfg)
    assert connected == []  # refused before any connection

    # Every request of a POS client goes through the check.
    sent = []
    transport = httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200, json={}))
    from app.pos import http as pos_http

    with httpx.Client(transport=transport, event_hooks=pos_http.metering_hooks()) as c:
        with pytest.raises(httpx.ConnectError):
            c.get("https://intranet.example.com/x")
        assert c.get("https://public.example.net/x").status_code == 200
    assert len(sent) == 1


def test_egress_is_off_on_a_single_store_server(private_dns, monkeypatch):
    from app.config import process_settings
    from app.platform import egress

    monkeypatch.setattr(process_settings(), "tenancy_mode", "single")
    egress.check_host("intranet.example.com", 443)  # the owner's own server: their call


def test_export_zip_never_holds_a_path_outside_files(alpha, tmp_path, monkeypatch):
    """A bucket object written by something else with "../" in its key is
    left out of the ZIP (zip-slip on the owner's computer)."""
    from app import storage as st
    from app.platform import export

    real = st.get_storage()

    class Sneaky:
        backend = "local"

        def list(self, prefix="", *, code=None):
            yield st.StoredObject("../../evil.sh", 3)
            yield from real.list(prefix, code=code)

        def open(self, key, *, code=None):
            return real.open(key, code=code)

    real.put_bytes("invoices/ok.pdf", b"%PDF", code="ALPHA")
    monkeypatch.setattr(st, "get_storage", lambda: Sneaky())
    dest = tmp_path / "x.zip"
    export.build_zip("ALPHA", str(dest))
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert "files/invoices/ok.pdf" in names
    assert not any(".." in n for n in names)


def test_export_blanks_more_secret_looking_setting_keys():
    from app.platform.export import _SECRET_KEY

    for key in ("smtp_pass", "gmail_app_password", "insights_hmac_key", "clover_webhook_auth_code", "sentry_dsn"):
        assert _SECRET_KEY.search(key), key
    for key in ("store_phone", "passport_scanner", "compass_mode"):
        assert not _SECRET_KEY.search(key), key
