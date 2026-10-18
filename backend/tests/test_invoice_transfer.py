"""Invoice upload "Transfer" type (owner, Oct 2026: "add a type for container
to store / store to container movement, and remove untracked").

A transfer is an internal stock movement: WhatsApp only, never emailed to
Toast, needs a direction, vendor optional, and never counts as a vendor
delivery. "untracked" is no longer offered by the pickers but the backend
still accepts it (build 5 of the app sends it).

Nothing here reaches Whapi or Gmail: the background send is replaced with a
recorder, or the two send functions are replaced with fakes.
"""

import datetime as dt

import pytest

from app.models import InvoiceSubmission
from app.routers import invoices as invoices_router
from app.services import invoice_submission as svc
from app.services import notification_triggers as nt
from app.services.reconciliation import get_invoice_gaps
from app.services.reorder import group_candidates_by_vendor, ordered_vendors
from app.timeutil import utcnow

from tests.conftest import client_for, make_user


@pytest.fixture
def scheduled(monkeypatch):
    """Records (submission_id, channels) instead of sending anything."""
    calls = []
    monkeypatch.setattr(invoices_router, "send_invoice_notifications", lambda sid, ch: calls.append((sid, list(ch))))
    return calls


@pytest.fixture
def inv_client(db):
    return client_for(db, make_user(db, "clerk", features=["invoice_submission"]))


def _post(client, **fields):
    data = {k: v for k, v in fields.items() if v is not None}
    return client.post("/api/invoices", data=data, files={"file": ("move.pdf", b"%PDF-1.4 test", "application/pdf")})


# --- submit ------------------------------------------------------------------


def test_transfer_submits_whatsapp_only(inv_client, scheduled):
    r = _post(inv_client, submission_type="transfer", transfer_direction="store_to_container")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["submission_type"] == "transfer"
    assert body["transfer_direction"] == "store_to_container"
    assert body["transfer_direction_label"] == "Store → Container"
    assert body["type_label"] == "Transfer: Store → Container"
    assert body["toast_applicable"] is False
    assert body["vendor"] == svc.TRANSFER_DEFAULT_VENDOR  # vendor optional for a transfer
    assert body["can_mark_toast_closed"] is False
    assert scheduled == [(body["id"], ["whatsapp"])]  # no Toast email scheduled


def test_transfer_keeps_a_typed_vendor(inv_client, scheduled):
    r = _post(inv_client, submission_type="transfer", transfer_direction="container_to_store", vendor=" Raja Foods ")
    assert r.status_code == 200
    assert r.json()["vendor"] == "Raja Foods"
    assert r.json()["type_label"] == "Transfer: Container → Store"


@pytest.mark.parametrize("direction", [None, "", "sideways"])
def test_transfer_requires_a_valid_direction(inv_client, scheduled, direction):
    r = _post(inv_client, submission_type="transfer", transfer_direction=direction)
    assert r.status_code == 400
    assert "transfer_direction" in r.json()["detail"]
    assert scheduled == []


def test_direction_rejected_on_other_types(inv_client, scheduled):
    r = _post(inv_client, submission_type="invoice", vendor="Raja Foods", transfer_direction="store_to_container")
    assert r.status_code == 400
    assert scheduled == []


def test_vendor_still_required_for_non_transfers(inv_client, scheduled):
    for t in ("invoice", "return", "other", "untracked"):
        r = _post(inv_client, submission_type=t)
        assert r.status_code == 400 and r.json()["detail"] == "Vendor is required", t
        r = _post(inv_client, submission_type=t, vendor="   ")
        assert r.status_code == 400, t
    assert scheduled == []


def test_untracked_still_accepted_whatsapp_only(inv_client, scheduled):
    r = _post(inv_client, submission_type="untracked", vendor="Raja Foods")
    assert r.status_code == 200
    body = r.json()
    assert body["transfer_direction"] is None and body["type_label"] == "Untracked"
    assert body["toast_applicable"] is False
    assert scheduled == [(body["id"], ["whatsapp"])]


def test_invoice_still_gets_both_channels(inv_client, scheduled):
    r = _post(inv_client, submission_type="invoice", vendor="Raja Foods")
    assert r.status_code == 200
    body = r.json()
    assert body["transfer_direction"] is None and body["transfer_direction_label"] is None
    assert body["type_label"] == "Invoice" and body["toast_applicable"] is True
    assert scheduled == [(body["id"], ["whatsapp", "toast_email"])]


def test_resend_never_emails_toast_for_a_transfer(inv_client, scheduled):
    sid = _post(inv_client, submission_type="transfer", transfer_direction="store_to_container").json()["id"]
    r = inv_client.post(f"/api/invoices/{sid}/resend", json={"channels": ["whatsapp", "toast_email"]})
    assert r.status_code == 200
    assert scheduled[-1] == (sid, ["whatsapp"])


# --- background send + status ---------------------------------------------------


def test_full_send_path_whatsapp_text_and_completion(db, inv_client, monkeypatch):
    sent_text = []
    monkeypatch.setattr(svc, "send_whatsapp_message", lambda text: sent_text.append(text))

    def no_email(**kw):
        raise AssertionError("a transfer must never be emailed to Toast")

    monkeypatch.setattr(svc, "send_toast_invoice_email", no_email)
    # Real background task (TestClient runs it after the response), fakes only.
    r = _post(inv_client, submission_type="transfer", transfer_direction="store_to_container", total_amount="12.5")
    sid = r.json()["id"]
    assert len(sent_text) == 1
    text = sent_text[0]
    assert "*New Transfer Submitted*" in text
    assert "*Transfer:* Store → Container" in text
    assert "*Vendor:*" not in text  # default vendor isn't shown
    assert "$12.50" in text

    after = inv_client.get(f"/api/invoices/{sid}").json()
    assert after["whatsapp_sent"] is True
    assert after["toast_email_sent"] is False and after["toast_email_error"] is None
    assert after["processing_status"] == "completed"
    # Can't be marked closed in Toast.
    assert inv_client.post(f"/api/invoices/{sid}/mark-toast-closed").status_code == 400


def _row(db, vendor="Raja Foods", submission_type="transfer", direction="store_to_container", created_at=None, **kw):
    n = db.query(InvoiceSubmission).count()
    entry = InvoiceSubmission(
        vendor=vendor,
        submission_type=submission_type,
        transfer_direction=direction if submission_type == "transfer" else None,
        original_filename="a.pdf",
        stored_filename=f"t{n}.pdf",
        file_token=f"ttok{n}",
        created_at=created_at or utcnow(),
        **kw,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_failed_alert_ignores_toast_for_transfer(db):
    entry = _row(db, toast_email_error="x", whatsapp_error="down")
    assert nt._failed_channels(entry) == ["whatsapp"]
    (alert,) = nt.invoice_failed_alerts(db, utcnow())
    assert alert.title == "Transfer failed to send: Transfer: Store → Container"


# --- never a vendor delivery ------------------------------------------------------

ET = dt.timezone(dt.timedelta(hours=-4))
TODAY = dt.date(2026, 9, 30)


def _at(day, hour=10):
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(dt.timezone.utc)


def test_transfer_does_not_clear_an_ordered_vendor(db):
    from app.models import PlacedOrder
    import json

    placed = _at(TODAY - dt.timedelta(days=2))
    db.add(
        PlacedOrder(
            supplier="Chetak",
            item_count=1,
            items_json=json.dumps([{"name": "x", "supplier_item_id": "x", "qty": 1}]),
            placed_by_name="boss",
            placed_at=placed,
            status="placed",
        )
    )
    db.commit()
    _row(db, vendor="Chetak", created_at=placed + dt.timedelta(hours=5))
    assert "chetak" in ordered_vendors(db, TODAY, 7)


def test_transfer_is_not_delivery_activity_for_a_vendor(db):
    items = [
        {
            "item_id": f"c{i}",
            "name": f"c{i}",
            "supplier": "Chetak",
            "estimated_order_value": 5.0,
            "shortfall": 10,
            "on_hand_qty": 0,
            "avg_daily_demand": 2.0,
            "urgency": {"tier": "critical", "label": "Critical"},
        }
        for i in range(5)
    ]
    _row(db, vendor="Chetak", created_at=_at(TODAY - dt.timedelta(days=1)))
    (g,) = group_candidates_by_vendor(db, items, TODAY, 7)
    assert g["last_delivery_date"] is None and g["inactive"] is True
    # Control: a real invoice upload does count.
    _row(db, vendor="Chetak", submission_type="invoice", created_at=_at(TODAY - dt.timedelta(days=1)))
    (g,) = group_candidates_by_vendor(db, items, TODAY, 7)
    assert g["last_delivery_date"] == (TODAY - dt.timedelta(days=1)).isoformat()


def test_transfer_not_in_delivery_nudge_or_invoice_gaps(db):
    _row(db, vendor="Chetak")
    _row(db, vendor="Chetak", created_at=_at(TODAY))
    assert svc.get_vendors_submitted_today(db) == []
    assert get_invoice_gaps(db, TODAY - dt.timedelta(days=1), TODAY + dt.timedelta(days=1)) == []
    # Control: the same upload as an invoice shows up in both.
    _row(db, vendor="Chetak", submission_type="invoice")
    _row(db, vendor="Chetak", submission_type="invoice", created_at=_at(TODAY))
    assert svc.get_vendors_submitted_today(db) == ["Chetak"]
    gaps = get_invoice_gaps(db, TODAY - dt.timedelta(days=1), TODAY + dt.timedelta(days=1))
    assert gaps and {g["submission_type"] for g in gaps} == {"invoice"}


# --- unmark closed in Toast (pre-App-Store audit) ------------------------------


def test_unmark_toast_closed_needs_a_closed_invoice(db, inv_client):
    entry = _row(db, submission_type="invoice", whatsapp_sent=True, toast_email_sent=True)
    r = inv_client.post(f"/api/invoices/{entry.id}/unmark-toast-closed")
    assert r.status_code == 409
    assert "isn't marked closed in Toast" in r.json()["detail"]

    assert inv_client.post(f"/api/invoices/{entry.id}/mark-toast-closed").status_code == 200
    r = inv_client.post(f"/api/invoices/{entry.id}/unmark-toast-closed")
    assert r.status_code == 200 and r.json()["toast_closed_at"] is None
    # Undoing twice: the second is a clear 409, not a silent 200.
    assert inv_client.post(f"/api/invoices/{entry.id}/unmark-toast-closed").status_code == 409
    assert inv_client.post("/api/invoices/999999/unmark-toast-closed").status_code == 404
