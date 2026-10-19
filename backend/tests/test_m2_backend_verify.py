"""Adversarial checks of the M2 backend batches (MT7 settings, MT8 pack and
identity) against docs/M2_CONTRACT.md. Each test pins a defect the verify
pass found; see "As built - M2 backend verify" in the contract."""

import pathlib
import re

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import REFUSED_DOMAIN, get_settings
from app.models import InvoiceSubmission
from app.pos import gating
from app.timeutil import utcnow
from tests.conftest import client_for, make_user
from tests.store_fixtures import BACKEND, use_store

REPO = BACKEND.parent


# --- A POS with a capability set but no adapter (Clover in M2) ------------------------------


@pytest.fixture
def scheduled_job_ids(monkeypatch):
    """The job ids the real start_* functions register (never started)."""
    import app.scheduler as sched

    def _collect():
        s = get_settings()
        monkeypatch.setattr(BackgroundScheduler, "start", lambda self, *a, **k: None)
        for flag in ("scheduler_enabled", "notifications_enabled", "sales_repull_enabled", "insights_nightly_enabled"):
            monkeypatch.setattr(s, flag, True)
        monkeypatch.setattr(s, "menu_barcode_recheck_minutes", 60)
        out = {}
        for start in (sched.start_scheduler, sched.start_notification_scheduler):
            monkeypatch.setattr(sched, "_scheduler", None)
            monkeypatch.setattr(sched, "_notification_scheduler", None)
            scheduler = start()
            out.update({j.id: gating.job_func_name(j) for j in scheduler.get_jobs()})
        monkeypatch.setattr(sched, "_scheduler", None)
        monkeypatch.setattr(sched, "_notification_scheduler", None)
        return out

    return _collect


def test_clover_store_registers_no_job_that_calls_the_pos(monkeypatch, scheduled_job_ids):
    """Contract 7: a Clover store (no adapter in M2) must not run POS jobs -
    each one would fail with "Unknown POS_PROVIDER" on every run."""
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    funcs = set(scheduled_job_ids().values())
    assert funcs <= gating.POS_NEUTRAL_JOB_FUNCS, funcs - gating.POS_NEUTRAL_JOB_FUNCS


@pytest.mark.parametrize("path", ["/api/orders/open", "/api/orders/employees"])
def test_clover_store_pos_routes_answer_409_not_500(db, monkeypatch, path):
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    client = client_for(db, make_user(db, "owner", is_admin=True))
    r = client.get(path)
    assert r.status_code == 409, (r.status_code, r.text[:200])
    assert "Clover" in r.json()["detail"]


def test_clover_store_still_advertises_its_capabilities(monkeypatch):
    """Branding / /me keep the contract's Clover set (section 7) - only the
    server's own gates need a connected POS."""
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    assert "open_orders" in gating.capability_values()
    assert not gating.has_capability(gating.Capability.OPEN_ORDERS)


def test_csv_upload_needs_no_pos_connection(db, monkeypatch):
    """CSV_CATALOG_IMPORT is a file upload, not a POS call: a CSV-only store
    keeps it even before the csv-only adapter is registered."""
    use_store(monkeypatch, "DEMO")
    assert gating.has_capability(gating.Capability.CSV_CATALOG_IMPORT)


# --- store-init on an existing store (contract 3.5 step 2) -----------------------------------


def test_init_again_on_an_adopted_store_with_data_is_refused(db, tmp_path, monkeypatch):
    """A store adopted with its data keeps its migration-seeded rows (the
    WhatsApp roles carry its chat ids). Running init again without --adopt
    must refuse (store data present), not delete those rows."""
    import datetime as dt

    from app.models import Order, WhatsAppGroup
    from app.store.__main__ import InitError, store_init

    monkeypatch.setattr("app.store.__main__._db_revision", lambda db: "0062")
    monkeypatch.setattr("app.store.__main__._alembic_heads", lambda: ["0062"])
    settings = get_settings().model_copy(update={"uploads_root": str(tmp_path / "uploads")})
    db.add(
        Order(
            toast_order_guid="o1", toast_check_guid="c1", toast_selection_guid="s1",
            business_date=dt.date(2026, 1, 2), item_guid="i1", item_name="x", quantity=1,
        )
    )
    db.add(WhatsAppGroup(key="kitchen", label="Kitchen", chat_id="1203@g.us", builtin=True, active=True,
                         created_at=utcnow(), created_by="migration 0044"))
    db.commit()
    pack = str(BACKEND / "packs" / "generic.json")
    store_init("ABC", pack, adopt=True, db=db, settings=settings)
    with pytest.raises(InitError, match="already has store data"):
        store_init("ABC", pack, db=db, settings=settings)
    assert db.query(WhatsAppGroup).filter_by(key="kitchen").one().chat_id == "1203@g.us"


# --- Docker: the identity marker lives in the store's volume ----------------------------------


def _dockerfile_env() -> dict:
    text = (REPO / "Dockerfile").read_text()
    out = {}
    for block in re.findall(r"^ENV (.*?)(?<!\\)\n", text, re.M | re.S):
        for name, value in re.findall(r"([A-Z_]+)=(\S+)", block):
            out[name] = value
    return out


def _compose_env() -> dict:
    import yaml

    data = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    return {k: str(v) for k, v in data["services"]["store"]["environment"].items()}


def test_dockerfile_sets_every_container_path_compose_sets():
    """`docker run` (no compose) must keep the database, uploads and the
    identity marker in /data too: UPLOADS_ROOT unset puts the marker under
    /app/backend (not the volume, not writable by the retailops user)."""
    image, compose = _dockerfile_env(), _compose_env()
    assert image.get("UPLOADS_ROOT") == "/data/uploads"
    for name, value in compose.items():
        assert image.get(name) == value, name


def test_dockerignore_keeps_private_packs_out_of_the_build_context():
    lines = {l.strip() for l in (REPO / ".dockerignore").read_text().splitlines()}
    for pattern in ("backend/packs/private", "**/*.pack.json", "**/legacy_cases.json", "**/forbidden_tokens.txt"):
        assert pattern in lines, pattern


# --- Rule 0.3: never under the legacy domain ------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [f"//{REFUSED_DOMAIN}", f"x@{REFUSED_DOMAIN}", f"https://{REFUSED_DOMAIN}\\@example.com", f"https://{REFUSED_DOMAIN}%2e",
     f"HTTPS://Labels.{REFUSED_DOMAIN.title()}./"],
)
def test_refused_domain_spellings(value):
    from app.config import refused_host

    assert refused_host(value)


@pytest.mark.parametrize("value", ["https://retailops-test-toast.retailops.invalid", f"not{REFUSED_DOMAIN}", ""])
def test_other_hosts_are_not_refused(value):
    from app.config import refused_host

    assert not refused_host(value)


# --- Store features off: invoices are not "failed" (contract 1.3) ----------------------------


def _invoice(db, **kw):
    n = db.query(InvoiceSubmission).count() + 1
    entry = InvoiceSubmission(
        vendor="Acme Foods", submission_type=kw.pop("submission_type", "invoice"), original_filename="bill.pdf",
        stored_filename=f"tok{n}.pdf", file_token=f"tok{n}", submitted_by_name="clerk", created_at=utcnow(), **kw,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_store_without_ap_inbox_or_whatsapp_never_fails_an_invoice(db, monkeypatch):
    """RETAILOPS_TEST_CLOVER has neither store feature: both channels are off
    for the store, so an invoice is saved and done - not "failed", and no
    "Invoice failed to send" push to every admin for each one."""
    from app.services import invoice_submission as inv
    from app.services.notification_triggers import invoice_failed_alerts

    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    monkeypatch.setattr(inv, "convert_to_pdf", lambda src, dst: None)
    make_user(db, "owner", is_admin=True)
    entry = _invoice(db)
    inv._attempt_whatsapp(db, entry)
    inv._attempt_toast_email(db, entry, pathlib.Path("/nonexistent"))
    db.refresh(entry)
    out = inv.submission_out(entry)
    assert out["processing_status"] == "completed"
    assert out["toast_applicable"] is False and out["ap_applicable"] is False
    assert out["can_mark_toast_closed"] is False
    assert invoice_failed_alerts(db, utcnow()) == []


def test_store_with_ap_inbox_but_no_whatsapp_can_close_the_bill(db, monkeypatch):
    from app.services import invoice_submission as inv
    from app.services.notification_triggers import invoice_failed_alerts

    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    monkeypatch.setattr(get_settings(), "ap_inbox_email", "ap@x.retailops.invalid")
    monkeypatch.setattr(get_settings(), "toast_invoice_email", "ap@x.retailops.invalid")
    admin = make_user(db, "owner", is_admin=True)
    entry = _invoice(db, toast_email_sent=True)
    inv._attempt_whatsapp(db, entry)
    db.refresh(entry)
    assert inv._processing_status(entry) == "in_progress"
    assert inv.can_mark_toast_closed(entry)
    inv.mark_toast_closed(db, entry, admin)
    assert inv._processing_status(entry) == "completed"
    assert invoice_failed_alerts(db, utcnow()) == []
