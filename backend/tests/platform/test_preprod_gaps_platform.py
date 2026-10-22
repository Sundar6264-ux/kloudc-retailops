"""Pre-production gaps, shared-service side (docs/PREPROD_PLAN.md section 1,
track S1): reason codes on 401/403 (G1-G3), Connect with Clover handing back
to the app (G4), export progress (G7), full Download links (G8), the generic
Clover PUT (G10), the store lookup limit for a shared store network (G13) and
demo refusals with their code (G15/G21). Scratch PostgreSQL databases."""

import types
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text

from app.platform import credentials as creds
from app.pos.adapters.clover import auth as cauth
from tests.platform.conftest import needs_b, provision, store_login


def _bearer(client, token):
    c = client.__class__(client.app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.fixture
def alpha_admin(client):
    provision("ALPHA")
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 200, r.text
    return _bearer(client, r.json()["token"])


@pytest.fixture(autouse=True)
def clean_exports(platform_env):
    from app.platform.db import platform_engine

    with platform_engine().begin() as c:
        c.execute(text("TRUNCATE store_exports RESTART IDENTITY"))
    yield


# --- G1 / G2 / G3: why a session stopped working ---------------------------------------------


@needs_b
def test_password_change_tells_the_other_devices_why(client, alpha_admin):
    second = _bearer(client, store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"])
    assert second.get("/api/auth/me").status_code == 200
    r = alpha_admin.post("/api/auth/change-password",
                         json={"current_password": "alpha-owner-pass", "new_password": "alpha-new-pass-123"})
    assert r.status_code == 200, r.text
    assert alpha_admin.get("/api/auth/me").status_code == 200  # this device stays signed in
    r = second.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"] == "password_changed" and "password was changed" in r.json()["message"]
    assert r.json()["detail"] == r.json()["message"]
    # A token that never existed: the plain expired answer, with its code.
    r = _bearer(client, "ALPHA.not-a-real-token").get("/api/auth/me")
    assert r.status_code == 401 and r.json()["error"] == "session_expired"
    assert r.json()["detail"] == "Session expired or invalid"


@needs_b
def test_suspended_store_in_session_carries_the_masters_message(client, alpha_admin, pdb):
    from app.platform import stores

    stores.suspend(pdb, "ALPHA", actor="platform:test", reason="test", message="Closed for stocktake until Monday.")
    pdb.commit()
    r = alpha_admin.get("/api/auth/me")
    assert r.status_code == 403 and r.json()["error"] == "store_suspended"
    assert r.json()["message"] == "Closed for stocktake until Monday." == r.json()["detail"]


@needs_b
def test_suspended_store_without_a_message_keeps_the_platform_default(client, alpha_admin, pdb):
    from app.platform import stores

    store = stores.suspend(pdb, "ALPHA", actor="platform:test", reason="test")
    pdb.commit()
    r = alpha_admin.get("/api/auth/me")
    assert r.status_code == 403 and r.json()["error"] == "store_suspended"
    # The same line the sign-in shows for this store.
    assert r.json()["message"] == (store.maintenance_message or "This store is suspended. Contact support.")
    assert "support" not in r.json()["message"] or not store.maintenance_message


# --- G4: Connect with Clover started in the app -----------------------------------------------


@pytest.fixture
def clover_app(monkeypatch):
    monkeypatch.setenv("CLOVER_APP_ID", "APPID123")
    monkeypatch.setenv("CLOVER_APP_SECRET", "app-secret-FFFF6666")
    monkeypatch.setenv("CLOVER_ENVIRONMENT", "sandbox")

    def endpoint(request):
        return httpx.Response(200, json={"access_token": "acc-1111", "refresh_token": "ref-2222",
                                         "access_token_expiration": 4102444800})

    monkeypatch.setattr(cauth, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(endpoint)))


def _state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


@needs_b
def test_clover_return_to_app_lands_on_the_hand_back_page(client, alpha_admin, clover_app):
    r = alpha_admin.get("/api/settings/integrations/clover/authorize-url", params={"return_to": "app"})
    assert r.status_code == 200, r.text
    assert r.json()["return_to"] == "app"
    anon = client.__class__(client.app)
    cb = anon.get("/api/settings/integrations/clover/callback",
                  params={"code": "good-code", "merchant_id": "TESTMERCH0001", "state": _state_of(r.json()["url"])},
                  follow_redirects=False)
    assert cb.status_code == 303
    assert cb.headers["location"] == "/api/settings/integrations/clover/app-return?clover=connected"
    page = anon.get(cb.headers["location"])
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "kloudcretailops://clover?clover=connected" in page.text and "Clover is connected" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert creds.stored_values("ALPHA", "clover")[0]["mode"] == "oauth"


@needs_b
def test_clover_return_defaults_to_the_website_and_refuses_unknown_targets(client, alpha_admin, clover_app):
    r = alpha_admin.get("/api/settings/integrations/clover/authorize-url")
    assert r.status_code == 200 and r.json()["return_to"] == "web"
    anon = client.__class__(client.app)
    cb = anon.get("/api/settings/integrations/clover/callback",
                  params={"code": "good-code", "merchant_id": "bad id!", "state": _state_of(r.json()["url"])},
                  follow_redirects=False)
    assert cb.headers["location"] == "/#/settings/integrations?clover=error&reason=denied"
    for bad in ("https://evil.example", "APP2", "javascript:"):
        r = alpha_admin.get("/api/settings/integrations/clover/authorize-url", params={"return_to": bad})
        assert r.status_code in (400, 422), (bad, r.text)


@needs_b
def test_clover_app_return_errors_from_the_app(client, alpha_admin, clover_app):
    r = alpha_admin.get("/api/settings/integrations/clover/authorize-url", params={"return_to": "app"})
    anon = client.__class__(client.app)
    cb = anon.get("/api/settings/integrations/clover/callback",
                  params={"code": "", "merchant_id": "TESTMERCH0001", "state": _state_of(r.json()["url"])},
                  follow_redirects=False)
    assert cb.headers["location"] == "/api/settings/integrations/clover/app-return?clover=error&reason=denied"


def test_clover_app_return_page_only_repeats_allowlisted_values(client, monkeypatch):
    anon = client.__class__(client.app)
    page = anon.get("/api/settings/integrations/clover/app-return",
                    params={"clover": "<script>", "reason": "x\"><img src=x>"})
    assert page.status_code == 200
    assert "<script>" not in page.text and "<img" not in page.text
    assert "kloudcretailops://clover?clover=error\"" in page.text and "was not connected" in page.text
    page = anon.get("/api/settings/integrations/clover/app-return", params={"clover": "error", "reason": "refused"})
    assert "clover=error&amp;reason=refused" in page.text or "clover=error&reason=refused" in page.text
    # A malformed scheme setting falls back to the app's own scheme.
    monkeypatch.setenv("APP_URL_SCHEME", "https://evil")
    page = anon.get("/api/settings/integrations/clover/app-return", params={"clover": "connected"})
    assert "kloudcretailops://clover?clover=connected" in page.text and "evil" not in page.text
    monkeypatch.setenv("APP_URL_SCHEME", "kloudcretailopsdev")
    page = anon.get("/api/settings/integrations/clover/app-return", params={"clover": "connected"})
    assert "kloudcretailopsdev://clover?clover=connected" in page.text


def test_clover_state_seals_the_return_target():
    from app.platform import crypto

    crypto.reset_keyring()
    state = cauth.make_state("ALPHA", "ann", return_to="app")
    assert cauth.read_state(state)["r"] == "app"
    assert "r" not in cauth.read_state(cauth.make_state("ALPHA", "ann"))
    assert "r" not in cauth.read_state(cauth.make_state("ALPHA", "ann", return_to="https://evil.example"))


# --- G10: the generic Clover PUT --------------------------------------------------------------


@needs_b
def test_generic_clover_put_saves_the_api_token(alpha_admin):
    r = alpha_admin.put("/api/settings/integrations/clover",
                        json={"public": {"merchant_id": "testmerch0001", "region": "us"},
                              "secrets": {"api_token": "clover-manual-EEEE5555"}})
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True and r.json()["mode"] == "token" and r.json()["merchant_id"] == "TESTMERCH0001"
    assert "clover-manual-EEEE5555" not in r.text
    public, secrets = creds.stored_values("ALPHA", "clover")
    assert public["mode"] == "token" and secrets["access_token"] == "clover-manual-EEEE5555"
    r = alpha_admin.put("/api/settings/integrations/clover", json={"public": {"merchant_id": "TESTMERCH0001",
                                                                             "region": "mars"}})
    assert r.status_code == 400 and r.json()["field"] == "region"


# --- G7 / G8: exports --------------------------------------------------------------------------


def _wait_ready(code, export_id, timeout=60.0):
    import time

    from app.platform.export import list_exports

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rows = {e["id"]: e for e in list_exports(code)}
        if rows.get(export_id, {}).get("status") in ("ready", "failed"):
            return rows[export_id]
        time.sleep(0.1)
    raise AssertionError("export did not finish")


@needs_b
def test_export_link_is_a_full_address_and_progress_reaches_100(client, alpha_admin, platform_env, monkeypatch):
    export_id = alpha_admin.post("/api/settings/exports").json()["id"]
    done = _wait_ready("ALPHA", export_id)
    assert done["status"] == "ready" and done["progress"] == 100
    assert alpha_admin.get("/api/settings/exports").json()["exports"][0]["progress"] == 100
    url = alpha_admin.post(f"/api/settings/exports/{export_id}/link").json()["url"]
    assert url.startswith(f"https://app.retailops.test/api/settings/exports/{export_id}/download?token=")
    assert client.__class__(client.app).get(url).status_code == 200
    # No public address set: the address the request came in on.
    monkeypatch.setattr(platform_env, "platform_public_url", "")
    url = alpha_admin.post(f"/api/settings/exports/{export_id}/link").json()["url"]
    assert url.startswith(f"http://testserver/api/settings/exports/{export_id}/download?token=")


@needs_b
def test_export_progress_steps_while_running(alpha_admin, pdb, tmp_path):
    from app.platform import export
    from app.platform.data_models import StoreExport

    seen = []
    summary = export.build_zip("ALPHA", str(tmp_path / "x.zip"), on_progress=lambda d, t: seen.append((d, t)))
    total = seen[-1][1]
    assert seen[0] == (0, total) and seen[-1] == (total - 1, total)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert summary["tables"] > 10

    row = StoreExport(store_code="ALPHA", status="running", reason="manual", requested_by="t", requested_by_type="system")
    pdb.add(row)
    pdb.commit()
    export._record_progress(row.id, 30, 60)
    pdb.expire_all()
    assert export.export_view(pdb.get(StoreExport, row.id))["progress"] == 50
    # A failed export drops the step counts (they were never totals).
    export._finish(row.id, error="boom")
    pdb.expire_all()
    view = export.export_view(pdb.get(StoreExport, row.id))
    assert view["progress"] is None and view["tables"] is None and view["files"] is None


def test_progress_of_each_status():
    from app.platform.export import progress_of

    row = types.SimpleNamespace(status="queued", tables=None, files=None)
    assert progress_of(row) == 0
    row.status = "running"
    assert progress_of(row) == 0
    row.tables, row.files = 59, 60
    assert progress_of(row) == 98
    row.tables = 60
    assert progress_of(row) == 99  # 100 only once it is ready
    row.status = "ready"
    assert progress_of(row) == 100
    assert progress_of(row, "expired") is None and progress_of(row, "failed") is None


# --- G13: the store lookup on a shared network -------------------------------------------------


def test_many_phones_on_one_network_can_look_up_their_store(client):
    provision("ALPHA")
    anon = client.__class__(client.app)
    for i in range(40):
        r = anon.get("/api/public/stores/alpha")
        assert r.status_code == 200, (i, r.status_code)


def test_trying_many_different_store_ids_is_still_limited(client):
    anon = client.__class__(client.app)
    codes = [f"NOSUCH{i:02d}" for i in range(12)]
    statuses = [anon.get(f"/api/public/stores/{c}").status_code for c in codes]
    assert statuses[:10] == [200] * 10  # an unknown code reads like a real one
    assert statuses[10] == 429
    r = anon.get("/api/public/stores/NOSUCH99")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    # A code asked for before still answers (its own, higher limit).
    assert anon.get(f"/api/public/stores/{codes[0]}").status_code == 200


def test_lookup_limits_per_store_and_address(monkeypatch):
    from app.platform import throttle

    throttle.reset_limiters()
    monkeypatch.setenv("DIRECTORY_STORE_RATE_PER_MINUTE", "5")
    for _ in range(5):
        assert throttle.check_lookup_rate("10.0.0.1", "alpha") is None
    assert throttle.check_lookup_rate("10.0.0.1", "ALPHA") is not None  # same store, same address
    assert throttle.check_lookup_rate("10.0.0.2", "ALPHA") is None  # another address
    assert throttle.check_lookup_rate("10.0.0.1", "BETA") is None  # another store
    throttle.reset_limiters()


# --- G15 / G21: demo refusals carry their code --------------------------------------------------


@needs_b
def test_demo_mode_store_refusals_have_the_code(client, alpha_admin, monkeypatch):
    from app.store import settings_view

    monkeypatch.setattr(settings_view, "demo_mode", lambda *a, **k: True)
    import app.demo as demo

    monkeypatch.setattr(demo, "demo_mode", lambda *a, **k: True)
    for method, path, body in (
        ("post", "/api/settings/exports", None),
        ("put", "/api/settings/integrations/smtp", {"public": {}}),
        ("put", "/api/item-vendors/items/X1", {"vendors": []}),
    ):
        r = getattr(alpha_admin, method)(path, json=body) if body is not None else getattr(alpha_admin, method)(path)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert r.json()["error"] == "demo_read_only", (path, r.text)
        assert r.json()["detail"] == r.json()["message"], (path, r.text)
