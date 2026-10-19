"""Pilot mode (app/ops/pilot.py): a store being onboarded syncs and works
as usual but sends NOTHING out - no WhatsApp, no email, no push - whoever
asks (a page, a job, a retry). Off: every transport behaves as before."""

import pathlib

import httpx
import pytest

from app.config import get_settings
from app.ops import pilot
from app.services import po_send, push, toast_email, whapi_client


@pytest.fixture(autouse=True)
def _counts():
    pilot.reset_counts()
    yield
    pilot.reset_counts()


@pytest.fixture
def pilot_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "pilot_mode", True)


@pytest.fixture
def outbound(monkeypatch):
    """Every outbound call the transports could make, recorded instead."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append(("httpx.post", url))
        return httpx.Response(200, json={"media": [{"id": "m1"}], "sent": True}, request=httpx.Request("POST", url))

    class FakeSMTP:
        def __init__(self, *a, **k):
            calls.append(("smtp", a))

        def login(self, *a):
            calls.append(("smtp.login", a))

        def sendmail(self, *a):
            calls.append(("smtp.sendmail", a[1]))
            return {}

        def starttls(self):
            pass

        def quit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(whapi_client.httpx, "post", fake_post)
    monkeypatch.setattr(toast_email.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(toast_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(po_send.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(po_send.smtplib, "SMTP", FakeSMTP)
    s = get_settings()
    for key, value in {
        "whapi_api_token": "tok", "whapi_chat_id": "1203@g.us", "gmail_address": "store@example.com",
        "gmail_app_password": "pw", "smtp_username": "store@example.com", "smtp_password": "pw",
        "toast_invoice_email": "ap@example.com", "ap_inbox_email": "ap@example.com",
    }.items():
        monkeypatch.setattr(s, key, value)
    return calls


def _pdf(tmp_path) -> pathlib.Path:
    p = tmp_path / "inv.pdf"
    p.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return p


def test_pilot_mode_is_a_store_setting_and_off_by_default():
    from app.tenancy.settings import STORE_PRIVATE_KEYS, STORE_SETTING_KEYS

    assert get_settings().pilot_mode is False
    assert "pilot_mode" in STORE_SETTING_KEYS, "a master sets it per store"
    assert "pilot_mode" in STORE_PRIVATE_KEYS, "never inherited by every store from the platform's .env"
    assert pilot.blocked("whatsapp") is None


def test_pilot_blocks_whatsapp_text_and_media(pilot_on, outbound):
    with pytest.raises(whapi_client.WhapiPilotBlocked, match="Pilot mode"):
        whapi_client.send_whatsapp_message("hello")
    with pytest.raises(whapi_client.WhapiNotConfigured):  # every caller already handles this
        whapi_client.upload_media(b"x", "image/png")
    with pytest.raises(whapi_client.WhapiPilotBlocked):
        whapi_client.send_media_message("document", "1203@g.us", "media-id", filename="a.pdf")
    assert outbound == []
    assert sum(v for k, v in pilot.blocked_counts().items() if k.endswith(":whatsapp")) == 3


def test_pilot_blocks_both_email_transports(pilot_on, outbound, tmp_path):
    with pytest.raises(toast_email.EmailPilotBlocked):
        toast_email.send_toast_invoice_email("Vendor", "invoice", 12.5, "ann", _pdf(tmp_path), "inv.pdf")
    with pytest.raises(toast_email.EmailNotConfigured, match="Pilot mode"):
        po_send.send_email(["vendor@example.com"], "PO", "body", b"%PDF", "po.pdf")
    assert outbound == []


def test_pilot_blocks_push(pilot_on, monkeypatch):
    monkeypatch.setattr(push, "_get_app", lambda: object())  # Firebase "set up"
    assert push.is_configured() is False, "callers see a dry run"
    with pytest.raises(push.PushNotConfigured):
        push.send_to_tokens(["t1", "t2"], "Title", "Body", {})
    assert any(k.endswith(":push") for k in pilot.blocked_counts())


def test_without_pilot_mode_the_transports_send(outbound, tmp_path, monkeypatch):
    whapi_client.send_whatsapp_message("hello")
    po_send.send_email(["vendor@example.com"], "PO", "body", b"%PDF", "po.pdf")
    monkeypatch.setattr(push, "_get_app", lambda: object())
    assert push.is_configured() is True
    kinds = [c[0] for c in outbound]
    assert "httpx.post" in kinds and "smtp.sendmail" in kinds
    assert pilot.blocked_counts() == {}


def test_a_pilot_invoice_submission_is_saved_and_marked_not_sent(pilot_on, outbound, admin_client, tmp_path,
                                                                  monkeypatch):
    """The page still works; the WhatsApp and email steps record why
    nothing was sent (a "not set up" reason, never a failure to retry) and
    nothing goes out."""
    monkeypatch.setattr(get_settings(), "invoice_uploads_dir", str(tmp_path / "inv"))
    (tmp_path / "inv").mkdir()
    r = admin_client.post(
        "/api/invoices",
        data={"vendor": "Acme", "submission_type": "invoice", "total_amount": "10.00"},
        files={"file": ("inv.pdf", _pdf(tmp_path).read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    # TestClient runs the background send before returning.
    entry = admin_client.get(f"/api/invoices/{r.json()['id']}").json()
    assert entry["whatsapp_sent"] is False and entry["toast_email_sent"] is False
    assert entry["whatsapp_error"] and entry["toast_email_error"]
    assert [c for c in outbound if c[0] in ("httpx.post", "smtp.sendmail")] == []
    assert pilot.blocked_counts(), "the blocks were counted"
