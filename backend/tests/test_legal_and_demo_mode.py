"""GET /api/app/legal (KloudC links + AI consent notice) and DEMO_MODE
(every login is a read-only demo login) - M2 batch MT13."""

import re
import types

import pytest

from app import demo, legal
from app.models import PurchaseLogEntry
from tests.conftest import client_for, make_user


def _settings(**values):
    return types.SimpleNamespace(**values)


def test_legal_is_public_with_product_defaults(anon_client, monkeypatch):
    monkeypatch.setattr(legal, "get_settings", lambda: _settings())
    r = anon_client.get("/api/app/legal")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=300"
    body = r.json()
    assert body == {
        "schema": 1,
        "product": "StoreOps",
        "company": "KloudC LLC",
        "privacy_url": "https://kloudc.com/kloudc-privacy-policy/",
        "terms_url": None,
        "support_url": "https://kloudc.com/kloudc-support/",
        "ai": {
            "enabled": False,
            "provider": "Anthropic",
            "service": "Claude",
            "notice": legal.DEFAULT_AI_CONSENT_NOTICE,
            "notice_version": legal.ai_notice_version(legal.DEFAULT_AI_CONSENT_NOTICE),
        },
        "demo": False,
    }
    assert re.fullmatch(r"[0-9a-f]{12}", body["ai"]["notice_version"])
    assert "set-cookie" not in r.headers


def test_legal_follows_the_store_settings(anon_client, monkeypatch):
    monkeypatch.setattr(
        legal,
        "get_settings",
        lambda: _settings(
            company_name="Example Co",
            product_name="StoreOps",
            privacy_url="https://example.invalid/privacy",
            terms_url="  https://example.invalid/terms ",
            support_url="",
            ai_enabled=True,
            ai_consent_notice="Custom notice for this store.",
            demo_mode=True,
            anthropic_api_key="sk-not-a-real-key",
        ),
    )
    body = anon_client.get("/api/app/legal").json()
    assert body["company"] == "Example Co"
    assert body["terms_url"] == "https://example.invalid/terms"
    assert body["support_url"] is None
    assert body["ai"]["enabled"] is True
    assert body["ai"]["notice"] == "Custom notice for this store."
    assert body["ai"]["notice_version"] != legal.ai_notice_version(legal.DEFAULT_AI_CONSENT_NOTICE)
    assert body["demo"] is True
    assert "sk-not-a-real-key" not in str(body)


def test_default_notice_names_the_provider_and_the_data():
    text = legal.DEFAULT_AI_CONSENT_NOTICE
    for words in ("Anthropic", "Claude", "question", "sales", "Not now", "Privacy Policy"):
        assert words in text


# --- DEMO_MODE --------------------------------------------------------------------------


def test_demo_mode_off_keeps_todays_rule(db, monkeypatch):
    user = make_user(db, "cashier_one", features=["overview"])
    monkeypatch.setattr(demo, "demo_mode", lambda: False)
    assert demo.is_demo_user(user) is False


def test_demo_mode_makes_every_login_a_demo_login(db, monkeypatch):
    staff = make_user(db, "cashier_one", features=["purchase_order_cart", "reconciliation"])
    boss = make_user(db, "store_manager", is_admin=True)
    monkeypatch.setattr(demo, "demo_mode", lambda: True)
    assert demo.is_demo_user(staff) and demo.is_demo_user(boss)
    # Which rows count as demo-made still follows DEMO_USERNAMES: the store's
    # own (made-up) data stays visible.
    assert demo.demo_user_ids(db) == set()
    # The read-only guard applies to everyone, admins too.
    for user in (staff, boss):
        client = client_for(db, user)
        r = client.post("/api/ordering/cart/items", json={"supplier": "x", "name": "x", "qty": 1})
        assert r.status_code == 403, user.username
        assert client.get("/api/auth/me").json()["demo"] is True
    assert db.query(PurchaseLogEntry).count() == 0


def test_demo_mode_reads_the_setting(monkeypatch):
    monkeypatch.setattr(demo, "get_settings", lambda: _settings(demo_mode=True, demo_usernames=""))
    assert demo.demo_mode() is True
    monkeypatch.setattr(demo, "get_settings", lambda: _settings(demo_usernames=""))
    assert demo.demo_mode() is False  # before MT7 adds the field


def test_ai_consent_notice_is_a_real_setting(anon_client, monkeypatch):
    """AI_CONSENT_NOTICE (contract 1.1) must reach /api/app/legal from the
    store's .env / environment, not only through getattr defaults."""
    from app.config import Settings

    monkeypatch.setenv("AI_CONSENT_NOTICE", "This store's own AI notice.")
    s = Settings(_env_file=None)
    monkeypatch.setattr(legal, "get_settings", lambda: s)
    body = anon_client.get("/api/app/legal").json()
    assert body["ai"]["notice"] == "This store's own AI notice."
    assert body["ai"]["notice_version"] == legal.ai_notice_version("This store's own AI notice.")


@pytest.mark.parametrize("company", ["", "  ", "Example Co"])
def test_legal_and_branding_agree_on_the_company(company, monkeypatch):
    """/api/app/legal and branding's legal block are the same links for the
    same settings (a blank COMPANY_NAME is the product default in both)."""
    from app.config import Settings
    from app.store.branding import branding_payload

    monkeypatch.setenv("COMPANY_NAME", company)
    s = Settings(_env_file=None)
    monkeypatch.setattr(legal, "get_settings", lambda: s)
    body = legal.legal_payload()
    brand = branding_payload("http://testserver", s=s)
    assert brand["legal"] == {k: body[k] for k in ("company", "privacy_url", "terms_url", "support_url")}
    assert brand["product"]["company"] == body["company"] == (company.strip() or "KloudC LLC")
