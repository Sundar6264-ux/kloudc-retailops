"""A "blank store" leaves no legacy store names in what the server sends out
(KloudC RetailOps M2, MT7/MT8 grep test; docs/M2_CONTRACT.md 9.2).

Default settings (nothing store-specific set) and the default store pack:
every read endpoint the parity harness records, the public pages, and the
text the server writes into PDFs, emails, WhatsApp messages, pushes and AI
prompts must not mention the store the code was first written for.

Not covered here (owned by MT9, the website batch): /api/glossary, whose
help text still describes that store's menus.
"""

import datetime as dt
import io
import re

import pytest

from app.timeutil import restaurant_today
from tests.conftest import client_for, make_user
from tests.origin_names import find_origin_names



def _assert_clean(label, text):
    hits = find_origin_names(text or "")
    assert not hits, f"{label}: origin-store name on line {hits[0][0]}: {hits[0][1][:200]!r}"


@pytest.mark.pack_checklists
def test_every_recorded_read_endpoint(db):
    import sys

    from tests.store_fixtures import BACKEND

    sys.path.insert(0, str(BACKEND / "scripts" / "parity"))
    from parity_endpoints import ISSUE_TYPES, build_calls

    admin = make_user(db, "owner", is_admin=True)
    client = client_for(db, admin)
    today = restaurant_today()
    checked = 0
    for call in build_calls(today, ["Acme Foods"], ISSUE_TYPES):
        if call.path == "/api/glossary":
            continue  # MT9 (see the module docstring)
        r = client.get(call.path, params=call.params or {})
        assert r.status_code < 500, (call.path, r.status_code, r.text[:300])
        _assert_clean(call.path, r.text)
        _assert_clean(call.path + " headers", str(dict(r.headers)))
        checked += 1
    assert checked >= 100


@pytest.mark.pack_checklists
def test_more_endpoints_and_public_pages(db, anon_client):
    admin = make_user(db, "owner", is_admin=True)
    client = client_for(db, admin)
    for path in (
        "/api/app/branding", "/api/app/config", "/api/team/rules", "/api/team/bootstrap", "/api/flyers/meta",
        "/api/checklists/schemas", "/api/checklists/admin/meta", "/api/checklists/admin/definitions",
        "/api/checklists/admin/whatsapp-groups", "/api/issue-leads",
    ):
        r = client.get(path)
        assert r.status_code < 400, (path, r.status_code, r.text[:200])
        _assert_clean(path, r.text)
    page = anon_client.get("/c/not-a-real-token")
    _assert_clean("public checklist page", page.text)
    _assert_clean("margin message", client.get("/api/reports/margin").text)


def test_po_pdf_email_and_whatsapp_text():
    from pypdf import PdfReader

    from app.services import po_export, po_send

    pdf = po_export.export_simple_po_pdf("Acme Foods", [{"name": "Rice 10 lb", "supplier_item_id": "R1", "qty": 2}])
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "KloudC RetailOps" in text  # the effective store name (no STORE_NAME set)
    _assert_clean("PO PDF", text)
    subject, body = po_send.compose("Acme Foods", [{"name": "x", "qty": 1}])
    _assert_clean("PO email", subject + body)
    assert body.endswith("Thank you,\nKloudC RetailOps" + "\n\n--\nSent with KloudC RetailOps by KloudC LLC")  # no blank address / phone lines
    _assert_clean("PO WhatsApp", po_send.whatsapp_caption("Acme Foods", [{"qty": 1}]))


def test_prompts_rules_and_names():
    from app.routers import reports
    from app.services import ask_bot, invoice_submission, weekly_digest
    from app.team import recurrence, rules, tasks_service

    _assert_clean("ask bot prompt", ask_bot.system_prompt())
    assert "a retail store" in ask_bot.system_prompt()
    _assert_clean("digest prompt", weekly_digest.system_prompt())
    _assert_clean("team rules", rules.RULES_TEXT)
    assert rules.RULES_TEXT.endswith("ask your manager.")
    assert tasks_service.SYSTEM_NAME == "KloudC RetailOps" == invoice_submission.TRANSFER_DEFAULT_VENDOR
    assert "KloudC RetailOps app" in reports.MARGIN_REMOVED_DETAIL
    assert recurrence.DEFAULT_TIMEZONE == "America/New_York"  # the TOAST_TIMEZONE default


def test_toast_pdf_producer_and_sales_export_name(admin_client):
    r = admin_client.get("/api/sales/export", params={"start": "2026-01-01", "end": "2026-01-02", "format": "csv"})
    assert 'filename="kloudcretailops-sales-2026-01-01_to_2026-01-02.csv"' in r.headers["content-disposition"]


def test_invoice_whatsapp_and_email_text(db):
    from app.models import InvoiceSubmission
    from app.services import invoice_submission

    entry = InvoiceSubmission(
        submission_type="transfer", vendor=invoice_submission.TRANSFER_DEFAULT_VENDOR, transfer_direction="store_to_container",
        total_amount=None, notes=None, submitted_by_name="Sam",
    )
    _assert_clean("invoice WhatsApp", invoice_submission._whatsapp_text(entry))


def test_flyer_categories_and_folders_are_the_packs():
    from app.services import flyers

    assert [c[0] for c in flyers.categories()] == ["groceries", "offers", "others"]
    assert str(flyers.base_dir()).endswith("flyers")
    _assert_clean("flyer categories", repr(flyers.categories()))


def test_whatsapp_roles_are_the_packs(db):
    from app.services import whatsapp_groups

    whatsapp_groups.ensure_seeded(db)
    labels = [g["label"] for g in whatsapp_groups.meta_groups(db)]
    assert labels == ["Store staff group"]


@pytest.mark.pack_checklists
def test_checklist_seeding_and_fallback_are_neutral(db):
    from app.models import ChecklistDefinition, ChecklistDefinitionVersion
    from app.services import checklist_schemas
    from app.services.checklist_admin import ensure_seeded

    _assert_clean("checklist fallback", repr(checklist_schemas.list_schemas()))
    ensure_seeded(db)
    for row in db.query(ChecklistDefinition).all():
        _assert_clean("checklist row", f"{row.title} {row.short_name} {row.description} {row.created_by}")
    for v in db.query(ChecklistDefinitionVersion).all():
        _assert_clean("checklist version", v.schema_json + (v.note or ""))
