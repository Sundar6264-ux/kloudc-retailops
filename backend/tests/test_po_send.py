"""Send PO from the cart + vendor contacts (docs/AUTOMATION_BATCH_CONTRACT.md
item 8). Every email goes to a fake SMTP class and every WhatsApp call to a
fake - nothing real is ever sent from here (the module-wide fixture fails a
test that reaches the real smtplib / Whapi)."""

import email
import json
import smtplib
import threading

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import CartItem, PlacedOrder
from app.po_send_models import PoSend, VendorContact
from app.services import po_send as svc
from app.services import whapi_client
from tests.conftest import client_for, make_user

CONTACTS = "/api/ordering/vendor-contacts"
PREVIEW = "/api/ordering/po-send/preview"
PREVIEW_PDF = "/api/ordering/po-send/preview.pdf"
SEND = "/api/ordering/po-send"
SENDS = "/api/ordering/po-sends"
MARK = "/api/ordering/cart/mark-placed"
PDF = "/api/ordering/purchase-order/export-pdf"
VENDOR = "Raja Foods"


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL. Class-level knobs per test."""

    instances: list = []
    sent: list = []
    fail_at = None  # "connect" | "login" | "send" | "send_refused"
    during_send = None  # callable run inside sendmail (e.g. a slow send)

    def __init__(self, host, port, timeout=None):
        FakeSMTP.instances.append((host, port, timeout))
        if FakeSMTP.fail_at == "connect":
            raise OSError("no route")
        self.closed = False

    def login(self, user, password):
        if FakeSMTP.fail_at == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def sendmail(self, sender, to_addrs, message):
        if FakeSMTP.during_send:
            FakeSMTP.during_send()
        if FakeSMTP.fail_at == "send":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        if FakeSMTP.fail_at == "send_refused":
            raise smtplib.SMTPDataError(552, b"content rejected")
        FakeSMTP.sent.append({"from": sender, "to": list(to_addrs), "message": message})
        return {}

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_real_sends(monkeypatch):
    """Nothing in this module may reach Gmail or Whapi for real."""
    FakeSMTP.instances, FakeSMTP.sent, FakeSMTP.fail_at, FakeSMTP.during_send = [], [], None, None

    def refuse(*_a, **_k):
        raise AssertionError("a real network send was attempted")

    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)
    monkeypatch.setattr(svc.smtplib, "SMTP_SSL", refuse)
    monkeypatch.setattr(whapi_client.httpx, "post", refuse)
    monkeypatch.setattr(whapi_client.httpx, "get", refuse)
    yield


@pytest.fixture
def gmail(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gmail_address", "orders@spicetown.test")
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")
    monkeypatch.setattr(svc.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def buyer(db):
    return client_for(db, make_user(db, "buyer", features=["purchase_order_cart"]))


@pytest.fixture
def projection_only(db):
    return client_for(db, make_user(db, "projector", features=["supplier_projection"]))


@pytest.fixture
def demo_client(db):
    return client_for(db, make_user(db, "app_store_review", features=["purchase_order_cart"]))


def _cart(db, supplier=VENDOR, lines=(("Toor Dal 4lb", "TD4", 2), ("Rice 20lb", "R20", 5))):
    for name, code, qty in lines:
        db.add(CartItem(supplier=supplier, item_id=f"id-{name}", name=name, supplier_item_id=code, qty=qty, case_of=1.0))
    db.commit()


def _contact(db, vendor=VENDOR, email="sales@raja.test", name="Ravi", whatsapp=None, send_po=True):
    row = VendorContact(
        vendor=vendor, vendor_key=svc.vendor_key(vendor), contact_name=name, email=email, whatsapp=whatsapp,
        send_po=send_po, created_at=svc.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _send(client, key="key-00000001", supplier=VENDOR, recipients=("sales@raja.test",), **extra):
    body = {"supplier": supplier, "idempotency_key": key, "recipients": list(recipients)}
    body.update(extra)
    return client.post(SEND, json=body)


def _cart_names(db, supplier=VENDOR):
    db.expire_all()
    return sorted(r.name for r in db.query(CartItem).filter(CartItem.supplier == supplier))


def _placed(db):
    db.expire_all()
    return db.query(PlacedOrder).order_by(PlacedOrder.id).all()


# --- Vendor contacts ---------------------------------------------------------


def test_admin_manages_contacts_and_buyers_can_read(db, admin_client, buyer):
    r = admin_client.post(
        CONTACTS,
        json={"vendor": " Raja Foods ", "contact_name": "Ravi\r\nBcc: x@evil.test", "email": "sales@raja.test",
              "whatsapp": "(860) 555-0101", "notes": "Calls back\nafter 3pm"},
    )
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["vendor"] == "Raja Foods"
    assert c["contact_name"] == "Ravi Bcc: x@evil.test"  # one line - no header break
    assert c["whatsapp"] == "+18605550101"
    assert c["notes"] == "Calls back\nafter 3pm"
    assert c["send_po"] is True and c["created_by_name"] == "boss"

    listed = buyer.get(CONTACTS).json()
    assert [i["email"] for i in listed["items"]] == ["sales@raja.test"]
    assert listed["can_edit"] is False
    assert "Raja Foods" in listed["vendors"]
    assert listed["whatsapp_enabled"] is False and "5 chats" in listed["whatsapp_off_reason"]
    assert buyer.get(CONTACTS, params={"vendor": "raja foods"}).json()["items"][0]["id"] == c["id"]

    assert buyer.post(CONTACTS, json={"vendor": VENDOR, "email": "a@b.test"}).status_code == 403
    assert buyer.patch(f"{CONTACTS}/{c['id']}", json={"email": "a@b.test"}).status_code == 403
    assert buyer.delete(f"{CONTACTS}/{c['id']}").status_code == 403

    r = admin_client.patch(f"{CONTACTS}/{c['id']}", json={"email": "office@raja.test", "send_po": False})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "office@raja.test" and r.json()["send_po"] is False
    assert r.json()["whatsapp"] == "+18605550101"  # untouched fields stay
    assert r.json()["updated_by_name"] == "boss"

    assert admin_client.delete(f"{CONTACTS}/{c['id']}").json() == {"deleted": True}
    assert admin_client.delete(f"{CONTACTS}/{c['id']}").status_code == 404
    assert buyer.get(CONTACTS).json()["items"] == []


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"vendor": VENDOR, "email": "not-an-email"}, "isn't an email"),
        ({"vendor": VENDOR, "email": "a@b.test\r\nBcc: c@d.test"}, "isn't an email"),
        ({"vendor": VENDOR}, "email or a WhatsApp"),
        ({"vendor": "  ", "email": "a@b.test"}, "Pick the vendor"),
        ({"vendor": VENDOR, "whatsapp": "123"}, "WhatsApp number"),
        ({"vendor": VENDOR, "whatsapp": "abc@g.us"}, "chat id"),
    ],
)
def test_contact_validation(db, admin_client, body, fragment):
    r = admin_client.post(CONTACTS, json=body)
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]
    assert db.query(VendorContact).count() == 0


def test_patch_that_removes_the_last_channel_is_refused(db, admin_client):
    c = _contact(db)
    r = admin_client.patch(f"{CONTACTS}/{c.id}", json={"email": ""})
    assert r.status_code == 422
    db.expire_all()
    assert db.get(VendorContact, c.id).email == "sales@raja.test"


def test_whatsapp_group_id_is_kept(db, admin_client):
    r = admin_client.post(CONTACTS, json={"vendor": VENDOR, "whatsapp": "120363012345678901@g.us"})
    assert r.status_code == 200, r.text
    assert r.json()["whatsapp"] == "120363012345678901@g.us"


def test_gates(db, projection_only, demo_client, buyer):
    _cart(db)
    _contact(db)
    for client in (projection_only, demo_client):
        assert client.get(CONTACTS).status_code == 403
        assert client.get(PREVIEW, params={"supplier": VENDOR}).status_code == 403
        assert _send(client).status_code == 403
        assert client.get(SENDS).status_code == 403
    assert "demo" in demo_client.get(CONTACTS).json()["detail"]
    assert _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"]
    assert db.query(PoSend).count() == 0


# --- Preview -------------------------------------------------------------------


def test_preview_shows_recipients_subject_body_and_pdf(db, buyer, gmail):
    _cart(db, lines=(("Toor Dal 4lb", "TD4", 2), ("Rice 20lb", "R20", 5), ("Old Line", None, 0)))
    _cart(db, supplier="Chetak", lines=(("Ghee", "G1", 1),))
    _contact(db, email="sales@raja.test")
    _contact(db, email="office@raja.test", name=None)
    _contact(db, email="cc-never@raja.test", send_po=False)
    _contact(db, email=None, whatsapp="+18605550101")
    _contact(db, vendor="Chetak", email="chetak@x.test")

    p = buyer.get(PREVIEW, params={"supplier": VENDOR}).json()
    assert p["recipients"] == ["sales@raja.test", "office@raja.test"]
    assert len(p["contacts"]) == 4
    assert p["item_count"] == 2 and p["zero_qty_lines"] == 1 and p["total_qty"] == 7
    assert [i["name"] for i in p["items"]] == ["Toor Dal 4lb", "Rice 20lb"]
    assert p["subject"].startswith("Purchase Order - StoreOps - Raja Foods - ")  # STORE_NAME unset: the product
    assert "Hello Raja Foods team," in p["body_template"]
    assert "2 items, 7 cases in total" in p["body_template"]
    assert p["note_marker"] in p["body_template"]
    assert p["pdf_filename"].startswith("purchase-order-raja-foods-") and p["pdf_filename"].endswith(".pdf")
    assert p["email_configured"] is True and p["from_address"] == "orders@spicetown.test"
    assert p["whatsapp"]["enabled"] is False and "402" in p["whatsapp"]["reason"]
    assert p["whatsapp"]["contacts"][0]["label"] == "Ravi - +18605550101"
    assert p["can_manage_contacts"] is False
    assert p["recent_sends"] == [] and p["uncertain_previous"] is None
    for text in (p["subject"], p["body_template"]):
        assert "—" not in text and "–" not in text

    pdf = buyer.get(PREVIEW_PDF, params={"supplier": VENDOR})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    # Looking isn't sending: nothing logged, nothing emailed.
    assert _placed(db) == [] and db.query(PoSend).count() == 0 and gmail.sent == []

    assert buyer.get(PREVIEW, params={"supplier": "Nobody"}).status_code == 400


def test_render_body_note_rules():
    _, template = svc.compose("Raja Foods", [{"qty": 1}])
    without = svc.render_body(template, "  ")
    assert svc.NOTE_MARKER not in without and "\n\n\n" not in without
    withnote = svc.render_body(template, "Please deliver Tuesday.\r\nThanks")
    assert "Please deliver Tuesday.\nThanks" in withnote
    assert "1 item, 1 case in total" in template


# --- Send ------------------------------------------------------------------------


def _email_parts(raw):
    msg = email.message_from_string(raw)
    parts = [p for p in msg.walk() if not p.is_multipart()]
    text = next(p for p in parts if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    pdfs = [p for p in parts if p.get_content_type() == "application/pdf"]
    return msg, text, pdfs


def test_send_emails_the_pdf_marks_placed_and_clears_only_that_vendor(db, admin_client, buyer, gmail):
    _cart(db, lines=(("Toor Dal 4lb", "TD4", 2), ("Rice 20lb", "R20", 5), ("Old Line", None, 0)))
    _cart(db, supplier="Chetak", lines=(("Ghee", "G1", 1),))
    _contact(db)
    _contact(db, email="office@raja.test")
    # A PDF downloaded earlier becomes this order (one row, not two).
    assert admin_client.post(PDF, json={"supplier": VENDOR, "items": [{"name": "Toor Dal 4lb", "qty": 2}]}).status_code == 200
    assert [o.status for o in _placed(db)] == ["pdf"]

    r = _send(buyer, recipients=["SALES@raja.test", "office@raja.test"], note="Deliver Tuesday please.")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sent"] is True and out["duplicate"] is False
    s = out["send"]
    assert s["status"] == "sent" and s["email_status"] == "sent"
    assert s["email_to"] == ["sales@raja.test", "office@raja.test"]
    assert s["email_message_id"].startswith("<") and s["sent_by_name"] == "buyer"

    assert len(gmail.sent) == 1
    sent = gmail.sent[0]
    assert sent["from"] == "orders@spicetown.test"
    assert sent["to"] == ["sales@raja.test", "office@raja.test"]
    msg, text, pdfs = _email_parts(sent["message"])
    assert msg["Subject"] == s["subject"]
    assert msg["Message-ID"] == s["email_message_id"]
    assert "Deliver Tuesday please." in text and svc.NOTE_MARKER not in text
    assert len(pdfs) == 1
    assert pdfs[0].get_filename() == s["pdf_filename"]
    assert pdfs[0].get_payload(decode=True).startswith(b"%PDF")

    orders = _placed(db)
    assert len(orders) == 1 and orders[0].status == "placed" and orders[0].id == s["placed_order_id"]
    assert orders[0].placed_by_name == "buyer"
    assert [(i["name"], i["qty"]) for i in json.loads(orders[0].items_json)] == [("Toor Dal 4lb", 2), ("Rice 20lb", 5)]
    assert _cart_names(db) == []  # the 0-qty line goes too, as with Mark placed
    assert _cart_names(db, "Chetak") == ["Ghee"]

    log = buyer.get(SENDS, params={"supplier": "raja foods"}).json()["items"]
    assert [x["id"] for x in log] == [s["id"]]
    recent = buyer.get("/api/ordering/orders/recent").json()["items"]
    assert recent[0]["supplier"] == VENDOR


def test_double_submit_sends_once_and_places_once(db, buyer, gmail):
    _cart(db)
    _contact(db)
    first = _send(buyer, key="dup-key-0001")
    second = _send(buyer, key="dup-key-0001")
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["send"]["id"] == first.json()["send"]["id"]
    assert len(gmail.sent) == 1
    assert len(_placed(db)) == 1
    # A fresh key after it went out: the cart is empty, nothing to send.
    third = _send(buyer, key="dup-key-0002")
    assert third.status_code == 400 and "Nothing to send" in third.json()["detail"]
    assert len(gmail.sent) == 1


def test_concurrent_same_key_sends_once(db, gmail):
    _cart(db)
    _contact(db)
    user = make_user(db, "racer", features=["purchase_order_cart"])
    a, b = client_for(db, user), client_for(db, user)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)

    gmail.during_send = slow
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("a", _send(a, key="race-key-01")))
    t.start()
    assert started.wait(5)
    same_key = _send(b, key="race-key-01")
    other_key = _send(b, key="race-key-02")
    release.set()
    t.join(5)
    assert results["a"].status_code == 200, results["a"].text
    assert same_key.status_code == 409 and same_key.json()["code"] == "in_progress"
    assert other_key.status_code == 409 and other_key.json()["code"] == "in_progress"
    assert len(gmail.sent) == 1 and len(_placed(db)) == 1
    assert db.query(PoSend).count() == 1


def test_failed_send_leaves_the_cart_and_orders_alone_then_retry_works(db, buyer, gmail):
    _cart(db)
    _contact(db)
    gmail.fail_at = "login"
    r = _send(buyer, key="fail-key-01")
    assert r.status_code == 502
    body = r.json()
    assert body["code"] == "failed" and "not sent" in body["detail"] and "GMAIL_APP_PASSWORD" in body["detail"]
    assert body["send"]["status"] == "failed" and body["send"]["placed_order_id"] is None
    assert _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"]
    assert _placed(db) == [] and gmail.sent == []

    # The same key again doesn't re-send a failed attempt; a retry uses a new key.
    again = _send(buyer, key="fail-key-01")
    assert again.status_code == 502 and again.json()["duplicate"] is True

    gmail.fail_at = None
    ok = _send(buyer, key="fail-key-02")
    assert ok.status_code == 200, ok.text
    assert len(gmail.sent) == 1 and len(_placed(db)) == 1 and _cart_names(db) == []
    statuses = [x["status"] for x in buyer.get(SENDS).json()["items"]]
    assert statuses == ["sent", "failed"]


@pytest.mark.parametrize("stage", ["connect", "send_refused"])
def test_other_definite_failures(db, buyer, gmail, stage):
    _cart(db)
    _contact(db)
    gmail.fail_at = stage
    r = _send(buyer)
    assert r.status_code == 502 and r.json()["code"] == "failed"
    assert _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"] and _placed(db) == []


def test_uncertain_send_asks_before_sending_again(db, buyer, gmail):
    _cart(db)
    _contact(db)
    gmail.fail_at = "send"
    r = _send(buyer, key="unc-key-01")
    assert r.status_code == 502
    assert r.json()["code"] == "uncertain" and "may have gone out" in r.json()["detail"]
    assert _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"] and _placed(db) == []

    p = buyer.get(PREVIEW, params={"supplier": VENDOR}).json()
    assert p["uncertain_previous"]["id"] == r.json()["send"]["id"]

    gmail.fail_at = None
    blocked = _send(buyer, key="unc-key-02")
    assert blocked.status_code == 409 and blocked.json()["code"] == "check_previous"
    assert gmail.sent == []
    ok = _send(buyer, key="unc-key-03", confirm_resend=True)
    assert ok.status_code == 200, ok.text
    assert len(gmail.sent) == 1 and len(_placed(db)) == 1


def test_mark_placed_instead_after_an_uncertain_send(db, buyer, gmail):
    """It did go out: Order placed records it, and the next send isn't blocked."""
    _cart(db)
    _contact(db)
    gmail.fail_at = "send"
    assert _send(buyer, key="unc-mark-01").status_code == 502
    assert buyer.post(MARK, params={"supplier": VENDOR}).status_code == 200
    assert len(_placed(db)) == 1 and _cart_names(db) == []
    assert buyer.get(SENDS).json()["items"][0]["status"] == "uncertain"
    # The next order for this vendor isn't held up by that answered question.
    gmail.fail_at = None
    _cart(db, lines=(("Chana Dal", "CD", 1),))
    assert buyer.get(PREVIEW, params={"supplier": VENDOR}).json()["uncertain_previous"] is None
    r = _send(buyer, key="unc-mark-02")
    assert r.status_code == 200, r.text
    assert len(gmail.sent) == 1 and len(_placed(db)) == 2


def test_a_line_added_while_sending_stays_in_the_cart(db, buyer, gmail):
    _cart(db)
    _contact(db)

    def phone_adds_a_line():
        other = SessionLocal()
        other.add(CartItem(supplier=VENDOR, item_id="id-new", name="Late Item", qty=3, case_of=1.0))
        other.commit()
        other.close()

    gmail.during_send = phone_adds_a_line
    assert _send(buyer).status_code == 200
    assert _cart_names(db) == ["Late Item"]
    items = json.loads(_placed(db)[0].items_json)
    assert "Late Item" not in [i["name"] for i in items]


def test_recipient_checks(db, buyer, gmail, monkeypatch):
    _cart(db)
    _contact(db)
    r = _send(buyer, recipients=["someone@else.test"])
    assert r.status_code == 422 and "isn't one of this vendor's contacts" in r.json()["detail"]
    r = _send(buyer, recipients=[])
    assert r.status_code == 422 and "Pick who to email" in r.json()["detail"]
    r = _send(buyer, supplier="Chetak", recipients=["sales@raja.test"])  # another vendor's contact
    assert r.status_code == 422
    r = _send(buyer, key="short")
    assert r.status_code == 422
    r = _send(buyer, note="x" * (svc.MAX_NOTE + 1))
    assert r.status_code == 422 and "too long" in r.json()["detail"]
    monkeypatch.setattr(get_settings(), "gmail_app_password", "")
    r = _send(buyer)
    assert r.status_code == 503 and "GMAIL" in r.json()["detail"]
    assert gmail.sent == [] and db.query(PoSend).count() == 0
    assert _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"]


def test_whatsapp_is_off_by_default(db, buyer, gmail):
    _cart(db)
    c = _contact(db, whatsapp="+18605550101")
    r = _send(buyer, channels=["email", "whatsapp"], whatsapp_contact_id=c.id)
    assert r.status_code == 422 and r.json()["code"] == "whatsapp_off" and "5 chats" in r.json()["detail"]
    assert gmail.sent == [] and _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"]


def test_whatsapp_when_enabled_sends_the_pdf_document(db, buyer, gmail, monkeypatch):
    monkeypatch.setattr(get_settings(), "po_whatsapp_enabled", True)
    calls = []
    monkeypatch.setattr(whapi_client, "upload_media", lambda data, mime: calls.append(("upload", mime, data[:4])) or "MID1")

    def fake_send(kind, to, media, caption=None, filename=None):
        calls.append(("send", kind, to, media, filename))
        return {"sent": True, "message": {"id": "wamid.1"}}

    monkeypatch.setattr(whapi_client, "send_media_message", fake_send)
    _cart(db)
    c = _contact(db, whatsapp="+18605550101")
    r = _send(buyer, channels=["email", "whatsapp"], whatsapp_contact_id=c.id)
    assert r.status_code == 200, r.text
    s = r.json()["send"]
    assert s["status"] == "sent" and s["whatsapp_status"] == "sent" and s["whatsapp_message_id"] == "wamid.1"
    assert calls[0] == ("upload", "application/pdf", b"%PDF")
    assert calls[1][:4] == ("send", "document", "18605550101@s.whatsapp.net", "MID1")
    assert len(_placed(db)) == 1


def test_whatsapp_402_with_email_ok_is_partly_sent_and_placed(db, buyer, gmail, monkeypatch):
    monkeypatch.setattr(get_settings(), "po_whatsapp_enabled", True)
    monkeypatch.setattr(whapi_client, "upload_media", lambda data, mime: "MID1")

    def refuse(*_a, **_k):
        raise whapi_client.WhapiMediaError("limit", status_code=402)

    monkeypatch.setattr(whapi_client, "send_media_message", refuse)
    _cart(db)
    c = _contact(db, whatsapp="+18605550101")
    r = _send(buyer, channels=["email", "whatsapp"], whatsapp_contact_id=c.id)
    assert r.status_code == 200, r.text
    s = r.json()["send"]
    assert s["status"] == "partly_sent" and s["whatsapp_status"] == "failed" and "5 chats" in s["whatsapp_error"]
    assert len(_placed(db)) == 1 and _cart_names(db) == []


def test_unexpected_error_never_leaves_a_send_stuck(db, buyer, gmail, monkeypatch):
    _cart(db)
    _contact(db)

    def boom(*_a, **_k):
        raise RuntimeError("pdf broke")

    monkeypatch.setattr(svc, "build_po_pdf", boom)
    r = _send(buyer)
    assert r.status_code == 502
    assert r.json()["send"]["status"] == "failed" and r.json()["send"]["email_status"] == "failed"
    assert _cart_names(db) == ["Rice 20lb", "Toor Dal 4lb"] and gmail.sent == []


def test_email_sent_but_recording_failed_is_reported(db, buyer, gmail, monkeypatch):
    _cart(db)
    _contact(db)
    from app.services import purchase_order_log

    def boom(*_a, **_k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(purchase_order_log, "place_sent_order", boom)
    r = _send(buyer)
    assert r.status_code == 200
    s = r.json()["send"]
    assert s["status"] == "sent" and s["placed_order_id"] is None
    assert len(gmail.sent) == 1


def test_download_pdf_route_uses_the_shared_builder(db, admin_client, monkeypatch):
    seen = {}
    real = svc.build_po_pdf

    def spy(db_, supplier, items):
        seen["supplier"] = supplier
        return real(db_, supplier, items)

    monkeypatch.setattr("app.routers.ordering.build_po_pdf", spy)
    r = admin_client.post(PDF, json={"supplier": VENDOR, "items": [{"name": "Toor Dal", "qty": 2}]})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert seen["supplier"] == VENDOR


def test_migration_0050_matches_the_models_and_downgrades(tmp_path):
    import os
    import pathlib
    import sqlite3
    import subprocess
    import sys

    from app.db import Base

    backend = pathlib.Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), "upgrade", "0050"], cwd=backend, env=env, check=True, capture_output=True)
    con = sqlite3.connect(db_file)
    try:
        for table in ("vendor_contacts", "po_sends"):
            migrated = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            # Columns added later (0056: email CCs) aren't in 0050.
            later = {"vendor_contacts": {"cc_json"}, "po_sends": {"email_cc_json"}}[table]
            modeled = {c.name for c in Base.metadata.tables[table].columns} - later
            assert migrated == modeled, table
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            assert "AUTOINCREMENT" in sql
        unique = [r[1] for r in con.execute("PRAGMA index_list(po_sends)") if r[2] == 1]
        cols = [[c[2] for c in con.execute(f"PRAGMA index_info({name})")] for name in unique]
        assert ["idempotency_key"] in cols
    finally:
        con.close()
    subprocess.run([str(alembic), "downgrade", "0049"], cwd=backend, env=env, check=True, capture_output=True)
    con = sqlite3.connect(db_file)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "po_sends" not in names and "vendor_contacts" not in names
    finally:
        con.close()


def test_po_text_uses_the_store_settings(monkeypatch):
    """StoreOps M2: name, address and phone come from the store settings;
    blank lines are left out."""
    from app.services import po_send
    from tests.store_fixtures import use_store

    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    subject, body = po_send.compose("Acme Foods", [{"name": "x", "qty": 2}])
    assert subject.startswith("Purchase Order - Test Toast Market - Acme Foods - ")
    assert "Please find attached Test Toast Market's purchase order" in body
    assert body.endswith("Thank you,\nTest Toast Market\n100 Example Ave, Springfield, IL 62701\n(555) 010-0100")
    assert po_send.whatsapp_caption("Acme Foods", [{"qty": 2}]).endswith("Please confirm. (555) 010-0100")
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "store_phone", "")
    monkeypatch.setattr(get_settings(), "store_address", "")
    subject, body = po_send.compose("Acme Foods", [{"name": "x", "qty": 2}])
    assert body.endswith("Thank you,\nTest Toast Market")
    assert po_send.whatsapp_caption("Acme Foods", [{"qty": 2}]).endswith("Please confirm.")


def test_po_email_headers_use_the_smtp_settings(monkeypatch):
    """SMTP_HOST / SMTP_PORT / SMTP_SSL, EMAIL_FROM_NAME, STORE_EMAIL (Reply-To)
    and EMAIL_FALLBACK_DOMAIN; nothing leaves the process."""
    import email

    from app.config import get_settings
    from app.services import po_send

    s = get_settings()
    for name, value in (("smtp_username", "orders"), ("smtp_password", "pw"), ("smtp_host", "mail.example.invalid"),
                        ("smtp_port", 2525), ("smtp_ssl", False), ("email_from_name", "Main Store Orders"),
                        ("store_email", "buyer@example.invalid"), ("email_fallback_domain", "mail.example.invalid")):
        monkeypatch.setattr(s, name, value)
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["connect"] = (host, port)

        def starttls(self):
            seen["starttls"] = True

        def login(self, user, password):
            seen["login"] = (user, password)

        def sendmail(self, sender, to, raw):
            seen["msg"] = email.message_from_string(raw)
            return {}

        def quit(self):
            pass

    monkeypatch.setattr(po_send.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(po_send.smtplib, "SMTP_SSL", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SSL used")))
    message_id = po_send.send_email(["v@example.invalid"], "Subject", "Body", b"%PDF-1.4", "po.pdf")
    assert seen["connect"] == ("mail.example.invalid", 2525) and seen["starttls"] and seen["login"] == ("orders", "pw")
    msg = seen["msg"]
    assert msg["From"] == "Main Store Orders <orders>" and msg["Reply-To"] == "buyer@example.invalid"
    assert message_id.endswith("@mail.example.invalid>")
