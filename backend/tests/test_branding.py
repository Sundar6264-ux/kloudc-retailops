"""GET /api/app/branding and the additive store keys (StoreOps M2, MT7;
docs/M2_CONTRACT.md section 2)."""

import json
import pathlib
import re

import pytest

from tests.conftest import make_user, client_for
from tests.store_fixtures import STORE_CODES, STORES_DIR, fake_store, use_store  # noqa: F401

LEGACY = re.compile(r"spice ?town|street kitchen|vernon|spicetown\.shop|tailcc", re.I)
KEYS = [
    "schema", "store_code", "name", "short_name", "product", "logo_url", "icon_url", "primary_color",
    "secondary_color", "timezone", "currency", "locale", "address", "phone", "pos_name", "capabilities",
    "store_features", "labels_url", "public_host", "legal", "min_build", "store_url", "demo",
]


def _expected(code: str) -> dict:
    return json.loads((STORES_DIR / f"{code}.branding.json").read_text())


def test_branding_matches_the_committed_body_for_each_fake_store(anon_client, fake_store):
    r = anon_client.get("/api/app/branding")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == KEYS
    assert body == _expected(fake_store)
    assert not LEGACY.search(r.text)
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_branding_types_and_null_rules(anon_client, monkeypatch):
    use_store(monkeypatch, "STOREOPS_TEST_CLOVER")
    body = anon_client.get("/api/app/branding").json()
    assert body["schema"] == 1 and isinstance(body["capabilities"], list)
    assert body["labels_url"] is None and body["legal"]["terms_url"] is None and body["store_url"]["ios"] is None
    assert body["logo_url"].startswith("https://") and body["icon_url"].startswith("https://")
    assert body["store_features"] == [] and body["demo"] is False
    use_store(monkeypatch, "DEMO")
    body = anon_client.get("/api/app/branding").json()
    assert body["demo"] is True and body["pos_name"] == "CSV upload" and body["capabilities"] == ["csv_catalog_import"]


def test_branding_without_a_store_code_is_the_product(anon_client):
    body = anon_client.get("/api/app/branding").json()
    assert body["store_code"] is None and body["name"] == "StoreOps" and body["short_name"] == "StoreOps"
    # Dev: no PUBLIC_HOST and no code -> the request's own address.
    assert body["public_host"] == "http://testserver"
    assert body["logo_url"] == "http://testserver/branding/logo.png"
    assert not LEGACY.search(json.dumps(body))


def test_branding_needs_no_login_and_reads_no_cookie(anon_client, admin_client):
    a = anon_client.get("/api/app/branding")
    b = admin_client.get("/api/app/branding", cookies={"session": "garbage"})
    assert a.status_code == b.status_code == 200 and a.json() == b.json()


def test_branding_etag_and_304(anon_client, monkeypatch):
    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    r = anon_client.get("/api/app/branding")
    etag = r.headers["etag"]
    assert re.fullmatch(r'"[0-9a-f]{32}"', etag)
    assert r.headers["cache-control"] == "public, max-age=300, stale-while-revalidate=86400"
    assert r.headers["vary"] == "Accept-Encoding"
    again = anon_client.get("/api/app/branding", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == etag
    other = anon_client.get("/api/app/branding", headers={"If-None-Match": '"0000"'})
    assert other.status_code == 200
    # A setting change changes the ETag.
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "store_name", "Renamed Market")
    assert anon_client.get("/api/app/branding").headers["etag"] != etag


def test_branding_never_carries_secrets(anon_client, monkeypatch):
    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    from app.config import get_settings

    s = get_settings()
    for name, value in (
        ("anthropic_api_key", "sk-fake-anthropic-key"),
        ("smtp_password", "fake-smtp-password"),
        ("gmail_app_password", "fake-gmail-password"),
        ("toast_client_secret", "fake-toast-secret"),
        ("insights_hmac_secret", "fake-hmac-secret"),
    ):
        monkeypatch.setattr(s, name, value)
    text = anon_client.get("/api/app/branding").text
    for secret in ("test-not-a-real-token", "sk-fake-anthropic-key", "fake-smtp-password", "fake-gmail-password",
                   "fake-toast-secret", "fake-hmac-secret"):
        assert secret not in text
    assert json.loads(text)["store_features"] == ["ap_inbox", "labels", "whatsapp", "ai"]


def test_bundled_branding_images(anon_client):
    for name in ("logo.png", "icon.png"):
        r = anon_client.get(f"/branding/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert r.headers["cache-control"] == "public, max-age=86400"
    assert anon_client.get("/branding/../config.py").status_code == 404
    assert anon_client.get("/branding/nope.png").status_code == 404


def test_me_gains_store_code_and_store_features(db, monkeypatch):
    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    admin = make_user(db, "boss2", is_admin=True)
    me = client_for(db, admin).get("/api/auth/me").json()
    assert me["store_code"] == "STOREOPS_TEST_TOAST"
    assert me["store_features"] == ["ap_inbox", "labels", "whatsapp", "ai"]
    assert me["pos_name"] == "Toast" and "analytics_reports" in me["capabilities"]


def test_me_without_ai_drops_the_ask_bot(db, monkeypatch):
    use_store(monkeypatch, "STOREOPS_TEST_CLOVER")
    user = make_user(db, "clerk", features=["ask_bot", "open_orders", "sales_insights", "invoice_submission"])
    client = client_for(db, user)
    me = client.get("/api/auth/me").json()
    assert me["store_code"] == "STOREOPS_TEST_CLOVER" and me["store_features"] == []
    # ask_bot needs the "ai" store feature; sales_insights needs analytics
    # (Clover has none); open_orders is a Clover capability.
    assert me["allowed_features"] == ["open_orders", "invoice_submission"] or set(me["allowed_features"]) == {
        "open_orders", "invoice_submission"
    }
    r = client.post("/api/ask", json={"question": "hello"})
    assert r.status_code == 409


def test_app_config_shape_is_unchanged(anon_client):
    body = anon_client.get("/api/app/config").json()
    assert list(body) == ["min_supported_build", "store_url", "pos_name", "capabilities"]
    assert body["store_url"] == {"ios": None, "android": "https://play.google.com/store/apps/details?id=com.kloudc.storeops"}


def test_push_payload_gains_the_store_code(monkeypatch):
    from app.services import push

    assert push.with_store({"type": "x", "route": "reorder"}) == {"type": "x", "route": "reorder"}
    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    assert push.with_store({"type": "x", "route": "reorder"}) == {
        "type": "x", "route": "reorder", "store": "STOREOPS_TEST_TOAST"
    }


def test_push_send_adds_store_through_the_one_choke_point(monkeypatch):
    """send_to_tokens (every alert and chat push) puts `store` in the FCM data."""
    import sys
    import types

    from app.services import push

    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    sent = []

    class _Msg:
        def __init__(self, **kw):
            self.kw = kw

    class _Resp:
        success = True
        exception = None

    class _Batch:
        def __init__(self, n):
            self.responses = [_Resp() for _ in range(n)]
            self.success_count = n
            self.failure_count = 0

    fake = types.SimpleNamespace(
        Message=_Msg,
        Notification=lambda **kw: kw,
        APNSConfig=lambda **kw: kw,
        APNSPayload=lambda **kw: kw,
        Aps=lambda **kw: kw,
        AndroidConfig=lambda **kw: kw,
        AndroidNotification=lambda **kw: kw,
        send_each=lambda messages, app=None: (sent.extend(messages), _Batch(len(messages)))[1],
    )
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", fake)
    monkeypatch.setitem(sys.modules, "firebase_admin", types.SimpleNamespace(messaging=fake))
    monkeypatch.setattr(push, "_get_app", lambda: object())
    push.send_to_tokens(["tok"], "Title", "Body", {"type": "critical_vendor", "route": "reorder", "vendor": "Acme"})
    assert sent and sent[0].kw["data"] == {
        "type": "critical_vendor", "route": "reorder", "vendor": "Acme", "store": "STOREOPS_TEST_TOAST"
    }


def test_committed_branding_fixtures_cover_every_fake_store():
    for code in STORE_CODES:
        body = _expected(code)
        assert list(body) == KEYS and body["store_code"] == code
        assert not LEGACY.search(json.dumps(body))
    assert pathlib.Path(STORES_DIR / "STOREOPS_TEST_TOAST.branding.json").is_file()
