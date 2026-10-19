"""Store settings (KloudC RetailOps M2, MT7; docs/M2_CONTRACT.md 1.1-1.3)."""

import pytest
from pydantic import ValidationError

from app.config import REFUSED_DOMAIN, Settings, get_settings, refused_host
from app.store import settings_view as sv
from tests.store_fixtures import use_store


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_defaults_are_product_values_or_blank():
    d = {name: f.default for name, f in Settings.model_fields.items()}
    assert d["database_url"] == "sqlite:///./retailops.db"
    assert d["flyers_dir"] == "uploads/flyers"
    assert d["toast_invoice_email"] == "" and d["ap_inbox_email"] is None
    assert d["public_base_url"] == "" and d["app_store_url"] == "" and d["play_store_url"] == ""
    assert d["product_name"] == "KloudC RetailOps" and d["company_name"] == "KloudC LLC"
    assert d["platform_domain"] == "retailops.invalid"
    assert d["app_bundle_id"] == "com.kloudc.retailops"
    assert d["store_code"] == "" and d["store_name"] == "" and d["ai_enabled"] is False
    assert d["business_day_cutoff_hour"] == 4 and d["first_sales_date"] == ""
    assert (d["smtp_host"], d["smtp_port"], d["smtp_ssl"]) == ("smtp.gmail.com", 465, True)
    # No default anywhere names the origin store or its hosts.
    from tests.origin_names import has_origin_name

    assert not [n for n, v in d.items() if isinstance(v, str) and has_origin_name(v)]


def test_store_code_is_upper_cased_and_checked():
    assert _settings(store_code=" xyz_grocery ").store_code == "XYZ_GROCERY"
    for bad in ("ab", "-ABC", "A" * 25, "AB CD", "_ABC"):
        with pytest.raises(ValidationError):
            _settings(store_code=bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("brand_color", "teal"),
        ("brand_secondary_color", "#12345"),
        ("first_sales_date", "2026-13-01"),
        ("business_day_cutoff_hour", 24),
        ("store_timezone", "Mars/Olympus"),
    ],
)
def test_bad_values_refuse_to_start(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


@pytest.mark.parametrize(
    "field", ["platform_domain", "public_host", "public_base_url", "labels_url", "session_cookie_domain"]
)
@pytest.mark.parametrize(
    "value", [REFUSED_DOMAIN, f"https://ops.{REFUSED_DOMAIN}", f".{REFUSED_DOMAIN}", f"https://labels.{REFUSED_DOMAIN.title()}/x"]
)
def test_legacy_domain_is_refused_everywhere(field, value):
    with pytest.raises(ValidationError) as exc:
        _settings(**{field: value})
    assert "never serves" in str(exc.value)


def test_refused_host_matches_only_that_domain():
    assert refused_host(REFUSED_DOMAIN) and refused_host(f"x.{REFUSED_DOMAIN}") and refused_host(f".{REFUSED_DOMAIN}")
    assert not refused_host(f"not{REFUSED_DOMAIN}") and not refused_host(f"{REFUSED_DOMAIN}.example.com")
    assert not refused_host("https://retailops-x.retailops.invalid")


def test_derived_names_and_hosts():
    s = _settings()
    assert sv.store_display_name(s) == "KloudC RetailOps" and sv.store_short_name(s) == "KloudC RetailOps"
    assert sv.store_description(s) == "a retail store"
    assert sv.effective_public_host(s) == "" and sv.effective_public_host(s, "http://testserver/") == "http://testserver"
    s = _settings(store_code="RETAILOPS_TEST_TOAST", store_name="Test Toast Market")
    assert sv.store_host_label(s) == "retailops-test-toast"
    assert sv.effective_public_host(s) == "https://retailops-test-toast.retailops.invalid"
    assert sv.effective_public_base_url(s) == "https://retailops-test-toast.retailops.invalid"
    assert sv.absolute_url("/branding/logo.png", s) == "https://retailops-test-toast.retailops.invalid/branding/logo.png"
    assert sv.absolute_url("https://cdn.example.invalid/logo.png/", s) == "https://cdn.example.invalid/logo.png"
    assert sv.store_short_name(s) == "Test Toast Market" and sv.store_slug(s) == "testtoastmarket"
    s = _settings(store_code="ABC", public_host="https://abc.example.invalid/", public_base_url="https://files.example.invalid")
    assert sv.effective_public_host(s) == "https://abc.example.invalid"
    assert sv.effective_public_base_url(s) == "https://files.example.invalid"
    assert sv.play_store_url(_settings()) == "https://play.google.com/store/apps/details?id=com.kloudc.retailops"
    assert sv.app_store_url(_settings()) is None


def test_first_sales_date_and_cutoff():
    import datetime as dt

    assert sv.first_sales_date(_settings()) is None and sv.sales_floor(_settings()) == dt.date.min
    s = _settings(first_sales_date="2026-01-02", business_day_cutoff_hour=3)
    assert sv.first_sales_date(s) == dt.date(2026, 1, 2) and sv.business_day_cutoff_hour(s) == 3


def test_store_features_fixed_order():
    assert sv.store_features(_settings(ai_enabled=False)) == []
    s = _settings(ai_enabled=True, whapi_api_token="t", labels_url="https://labels-x.retailops.invalid", ap_inbox_email="ap@x.invalid")
    assert sv.store_features(s) == ["ap_inbox", "labels", "whatsapp", "ai"]
    # The old name still switches the AP inbox on.
    assert sv.store_features(_settings(toast_invoice_email="ap@x.invalid", ai_enabled=False)) == ["ap_inbox"]


def test_whatsapp_role_map_wins_over_legacy_settings():
    s = _settings(
        whapi_store_checklist_group_id="1@g.us",
        whapi_kitchen_checklist_group_id="2@g.us",
        whatsapp_group_chat_ids='{"store": "3@g.us", "bar": "4@g.us"}',
    )
    assert sv.whatsapp_role_chat_ids(s) == {"store": "3@g.us", "kitchen": "2@g.us", "bar": "4@g.us"}
    assert sv.whatsapp_role_chat_ids(_settings(whatsapp_group_chat_ids="not json")) == {}


def test_smtp_config_falls_back_to_the_gmail_names():
    cfg = sv.smtp_config(_settings(gmail_address="a@x.invalid", gmail_app_password="pw", store_name="Shop"))
    assert (cfg.username, cfg.password, cfg.from_name) == ("a@x.invalid", "pw", "Shop")
    cfg = sv.smtp_config(
        _settings(smtp_username="b@x.invalid", smtp_password="pw2", smtp_host="mail.x.invalid", smtp_port=587,
                  smtp_ssl=False, email_from_name="Orders")
    )
    assert cfg == ("mail.x.invalid", 587, False, "b@x.invalid", "pw2", "Orders")
    assert sv.email_fallback_domain(_settings()) == "retailops.invalid"


def test_use_store_switches_every_reader(monkeypatch):
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    s = get_settings()
    assert s.store_code == "RETAILOPS_TEST_CLOVER" and s.toast_timezone == "America/Los_Angeles"
    from app.pos import gating
    from app.timeutil import restaurant_timezone

    assert str(restaurant_timezone()) == "America/Los_Angeles"
    assert gating.pos_name() == "Clover"
    assert "analytics_reports" not in gating.capability_values()


def test_env_example_lists_every_setting():
    import pathlib
    import re

    text = (pathlib.Path(__file__).resolve().parents[1] / ".env.example").read_text()
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))
    missing = [name.upper() for name in Settings.model_fields if name.upper() not in keys]
    assert not missing, f"backend/.env.example is missing: {missing}"
    # Secrets are blank.
    for secret in ("TOAST_CLIENT_SECRET", "SMTP_PASSWORD", "WHAPI_API_TOKEN", "ANTHROPIC_API_KEY",
                   "FIREBASE_CREDENTIALS_FILE", "GMAIL_APP_PASSWORD", "TOAST_ANALYTICS_CLIENT_SECRET"):
        assert re.search(rf"^{secret}=$", text, re.M), secret
