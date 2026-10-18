"""W2 (docs/BATCH_OCT14_CONTRACT.md): Send PO email CCs.

- Default CCs per vendor contact (vendor_contacts.cc_json, migration 0056),
  set by admins with the contact; validated, at most 10, the contact's own
  address dropped.
- The preview pre-fills `default_cc` from the default recipients' CCs.
- A send takes `cc` (any valid address, at most 10, duplicates and To
  addresses dropped); the email carries a Cc header and goes to To + CC; the
  send log shows `email_cc`.
Every email goes to the fake SMTP from test_po_send - nothing real is sent.
"""

import email
import json

import pytest

from app.po_send_models import PoSend, VendorContact
from app.services import po_send as svc
from tests.test_po_send import (  # noqa: F401 - fixtures
    CONTACTS,
    PREVIEW,
    SENDS,
    VENDOR,
    FakeSMTP,
    _cart,
    _contact,
    _send,
    buyer,
    gmail,
    no_real_sends,
)


def test_contact_default_cc_is_validated_and_listed(db, admin_client, buyer):
    r = admin_client.post(
        CONTACTS,
        json={
            "vendor": VENDOR,
            "email": "sales@raja.test",
            "cc": ["Owner@SpiceTown.test", "owner@spicetown.test", "sales@raja.test", "ap@raja.test, books@raja.test"],
        },
    )
    assert r.status_code == 200, r.text
    c = r.json()
    # Duplicates (any case) and the contact's own address are dropped; a
    # pasted "a, b" counts as two.
    assert c["cc"] == ["Owner@SpiceTown.test", "ap@raja.test", "books@raja.test"]
    assert buyer.get(CONTACTS).json()["items"][0]["cc"] == c["cc"]

    r = admin_client.patch(f"{CONTACTS}/{c['id']}", json={"cc": []})
    assert r.status_code == 200 and r.json()["cc"] == []
    db.expire_all()
    assert db.get(VendorContact, c["id"]).cc_json is None

    # Changing the contact's email to one of its CCs drops that CC.
    admin_client.patch(f"{CONTACTS}/{c['id']}", json={"cc": ["ap@raja.test", "x@raja.test"]})
    r = admin_client.patch(f"{CONTACTS}/{c['id']}", json={"email": "AP@raja.test"})
    assert r.json()["cc"] == ["x@raja.test"]

    # A buyer can't set CCs (contacts are admin-only).
    assert buyer.patch(f"{CONTACTS}/{c['id']}", json={"cc": ["a@b.test"]}).status_code == 403


@pytest.mark.parametrize(
    "cc, fragment",
    [
        (["not-an-email"], "isn't an email"),
        (["a@b.test\r\nBcc: c@d.test"], "isn't an email"),
        ([f"p{i}@x.test" for i in range(11)], "At most 10"),
    ],
)
def test_contact_cc_validation(db, admin_client, cc, fragment):
    r = admin_client.post(CONTACTS, json={"vendor": VENDOR, "email": "sales@raja.test", "cc": cc})
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]
    assert db.query(VendorContact).count() == 0


def test_preview_prefills_default_cc_from_default_recipients(db, buyer):
    _cart(db)
    a = _contact(db)
    a.cc_json = json.dumps(["owner@spicetown.test", "ap@raja.test"])
    b = _contact(db, email="office@raja.test", name="Office")
    b.cc_json = json.dumps(["AP@raja.test", "sales@raja.test", "mgr@raja.test"])
    # Not a default recipient: its CCs aren't pre-filled.
    c = _contact(db, email="old@raja.test", name="Old", send_po=False)
    c.cc_json = json.dumps(["nobody@raja.test"])
    db.commit()
    p = buyer.get(PREVIEW, params={"supplier": VENDOR}).json()
    assert p["recipients"] == ["sales@raja.test", "office@raja.test"]
    assert p["default_cc"] == ["owner@spicetown.test", "ap@raja.test", "mgr@raja.test"]
    assert p["max_cc"] == 10
    assert {x["email"]: x["cc"] for x in p["contacts"]}["old@raja.test"] == ["nobody@raja.test"]


def test_send_with_cc_emails_to_and_cc_and_logs_it(db, buyer, gmail):
    _cart(db)
    _contact(db)
    r = _send(buyer, cc=["owner@spicetown.test", "OWNER@spicetown.test", "sales@raja.test", "ap@raja.test"])
    assert r.status_code == 200, r.text
    send = r.json()["send"]
    assert send["email_to"] == ["sales@raja.test"]
    assert send["email_cc"] == ["owner@spicetown.test", "ap@raja.test"]
    [mail] = gmail.sent
    assert mail["to"] == ["sales@raja.test", "owner@spicetown.test", "ap@raja.test"]
    msg = email.message_from_string(mail["message"])
    assert msg["To"] == "sales@raja.test"
    assert msg["Cc"] == "owner@spicetown.test, ap@raja.test"
    logged = buyer.get(SENDS, params={"supplier": VENDOR}).json()["items"][0]
    assert logged["email_cc"] == ["owner@spicetown.test", "ap@raja.test"]


def test_send_without_cc_is_unchanged(db, buyer, gmail):
    _cart(db)
    _contact(db)
    r = _send(buyer)
    assert r.status_code == 200, r.text
    assert r.json()["send"]["email_cc"] == []
    [mail] = gmail.sent
    assert mail["to"] == ["sales@raja.test"]
    assert email.message_from_string(mail["message"])["Cc"] is None
    db.expire_all()
    assert db.query(PoSend).one().email_cc_json is None


@pytest.mark.parametrize(
    "cc, fragment",
    [
        (["owner@spicetown"], "isn't an email"),
        (["a@b.test\nBcc: evil@x.test"], "isn't an email"),
        ([f"p{i}@x.test" for i in range(11)], "At most 10"),
    ],
)
def test_send_cc_validation_sends_nothing(db, buyer, gmail, cc, fragment):
    _cart(db)
    _contact(db)
    r = _send(buyer, cc=cc)
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]
    assert gmail.sent == []
    assert db.query(PoSend).count() == 0


def test_cc_ten_is_the_limit_after_dedupe(db, buyer, gmail):
    _cart(db)
    _contact(db)
    cc = [f"p{i}@x.test" for i in range(10)] + ["P0@x.test", "sales@raja.test"]
    r = _send(buyer, cc=cc)
    assert r.status_code == 200, r.text
    assert len(r.json()["send"]["email_cc"]) == 10


def test_normalize_cc_helper():
    assert svc.normalize_cc(None) == []
    assert svc.normalize_cc("a@b.test; c@d.test") == ["a@b.test", "c@d.test"]
    with pytest.raises(svc.PoSendError):
        svc.normalize_cc({"a": 1})
    with pytest.raises(svc.PoSendError):
        svc.normalize_cc([5])


def test_migration_0056_adds_and_drops_the_columns(tmp_path):
    import os
    import pathlib
    import sqlite3
    import subprocess
    import sys

    backend = pathlib.Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *args: subprocess.run(  # noqa: E731
        [str(alembic), *args], cwd=backend, env=env, check=True, capture_output=True
    )
    run("upgrade", "0056")

    def cols(table):
        con = sqlite3.connect(db_file)
        try:
            return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        finally:
            con.close()

    assert "cc_json" in cols("vendor_contacts") and "email_cc_json" in cols("po_sends")
    run("downgrade", "0055")
    assert "cc_json" not in cols("vendor_contacts") and "email_cc_json" not in cols("po_sends")
    assert "vendor" in cols("vendor_contacts")
