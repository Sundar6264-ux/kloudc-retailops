"""Invoice Submission - replaces the old Google Form -> Sheet -> Apps Script
workflow (see that script's real source for the exact behavior this mirrors).
Submitting one saves the uploaded file to disk and the row to the DB first
(so nothing is ever lost to a downstream outage), then attempts two
independent notifications: a WhatsApp message via Whapi with a link back to
the file, and an email to Toast's invoicing inbox with the file attached as
a PDF (converting it first if it wasn't already one) - sent for every
submission type, including "other", matching the original script's
unconditional email step. Toast turns that email into an automatic draft
bill. Each attempt's outcome is recorded on the row (`whatsapp_sent`/
`toast_email_sent` + their `_error` text) rather than raising - a failed
send is visible and individually retryable (`resend_invoice_submission`),
not a failed submission.
"""

import contextvars
import datetime as dt
import hashlib
import logging
import pathlib
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.demo import DEMO_NOT_SENT, hide_demo_clause, is_demo_username
from app.models import InvoiceSubmission, PurchaseLogEntry, User
from app.services.file_convert import UnsupportedFileType, convert_to_pdf
from app.services.toast_email import EmailNotConfigured, send_toast_invoice_email
from app.services.whapi_client import (
    WhapiAmbiguousTimeout,
    WhapiNotConfigured,
    find_sent_message,
    send_whatsapp_message,
)
from app.timeutil import restaurant_today, to_restaurant_date, utcnow
from app.store.settings_view import effective_public_base_url

logger = logging.getLogger(__name__)

VALID_TYPES = {"invoice", "return", "other", "untracked", "transfer"}
TYPE_LABELS = {
    "invoice": "Invoice",
    "return": "Return",
    "other": "Something else",
    "untracked": "Untracked",
    "transfer": "Transfer",
}
# Real ask: some invoices never become a Toast bill at all (not something
# Toast needs to know about) - the team should still hear about them over
# WhatsApp, but the Toast email step should be skipped outright, not just
# attempted-and-ignored. Any submission of this type never gets a Toast
# email attempt - see submit_invoice/resend_invoice in routers/invoices.py,
# which both strip "toast_email" from the channel list for this type before
# scheduling send_invoice_notifications, and _processing_status/
# can_mark_toast_closed below, which treat Toast as not applicable rather
# than "still pending" for it.
UNTRACKED_TYPE = "untracked"
# Owner, Oct 2026: "add a type for container to store / store to container
# movement". A transfer is an internal stock movement between the store and
# the storage container - not a vendor document, so (exactly like
# UNTRACKED_TYPE) it goes to WhatsApp only, never to Toast, and is never
# counted as a vendor delivery (reorder.py, reconciliation). It carries a
# required direction (TRANSFER_DIRECTIONS); the vendor is optional and
# defaults to TRANSFER_DEFAULT_VENDOR.
TRANSFER_TYPE = "transfer"
TRANSFER_DIRECTIONS = {
    "store_to_container": "Store \u2192 Container",
    "container_to_store": "Container \u2192 Store",
}
# TRANSFER_DEFAULT_VENDOR is the store's own name (STORE_NAME): see
# transfer_default_vendor() and the module __getattr__ at the end.
# Types that never get a Toast email attempt. "untracked" is no longer
# offered for new submissions in the app/website pickers, but the backend
# still accepts it (build 5 of the app sends it) and old rows keep it.
NO_TOAST_TYPES = frozenset({UNTRACKED_TYPE, TRANSFER_TYPE})


def skips_toast(submission_type: Optional[str]) -> bool:
    return submission_type in NO_TOAST_TYPES


def type_display_label(submission_type: Optional[str], transfer_direction: Optional[str] = None) -> str:
    """'Transfer: Store -> Container' for a transfer, else the plain type label."""
    label = TYPE_LABELS.get(submission_type or "", "Something else")
    if submission_type == TRANSFER_TYPE and transfer_direction in TRANSFER_DIRECTIONS:
        return f"{label}: {TRANSFER_DIRECTIONS[transfer_direction]}"
    return label


def _uploads_dir() -> pathlib.Path:
    d = pathlib.Path(get_settings().invoice_uploads_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_invoice_file_url(entry: InvoiceSubmission) -> str:
    # The plain integer id, not the long unguessable file_token - a real
    # report asked for a shorter, more readable link in the WhatsApp message.
    # The token existed for security when this route had no login gate at
    # all; now that it requires a real dashboard login + the invoice_
    # submission feature (see routers/invoices.py's public_router docstring),
    # an unguessable path adds nothing a plain id doesn't already have
    # elsewhere in this API (e.g. GET /api/invoices/{id} is already a plain
    # int). get_public_invoice_file still accepts the old long token too, so
    # links already sent out over WhatsApp before this change keep working.
    return f"{effective_public_base_url()}/public/invoices/{entry.id}"


def hash_file_bytes(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def not_demo(db: Session, viewer: Optional[User] = None, include_demo: bool = False):
    """Hides App Review demo submissions (app/demo.py hide_demo_clause):
    viewer None = store-wide logic/jobs, which never count them."""
    return hide_demo_clause(
        db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name, viewer, include_demo
    )


def find_duplicate_by_hash(
    db: Session, file_hash: str, viewer: Optional[User] = None
) -> Optional[InvoiceSubmission]:
    """The most recent active (not-trashed) submission with this exact file
    hash, if any - an exact byte-for-byte match is strong evidence the same
    PDF/photo was already submitted, not just a coincidentally similar one.
    A demo submission never counts as a duplicate for real staff.
    """
    stmt = (
        select(InvoiceSubmission)
        .where(
            InvoiceSubmission.file_hash == file_hash,
            InvoiceSubmission.deleted_at.is_(None),
            not_demo(db, viewer),
        )
        .order_by(InvoiceSubmission.created_at.desc(), InvoiceSubmission.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _whatsapp_text(entry: InvoiceSubmission) -> str:
    # WhatsApp's own markdown: single asterisks render bold in the client -
    # matches the original Apps Script's message body exactly, not just its
    # visible text.
    type_label = TYPE_LABELS.get(entry.submission_type, "Something else")
    total_str = f"${entry.total_amount:,.2f}" if entry.total_amount is not None else "$Unknown"
    notes_line = f"*Notes:* {entry.notes}\n" if entry.notes else ""
    if entry.submission_type == TRANSFER_TYPE:
        # Renders as "Transfer: Store -> Container" in WhatsApp. The vendor
        # line is dropped unless staff typed a real one.
        direction = TRANSFER_DIRECTIONS.get(entry.transfer_direction or "", "Unknown direction")
        who_line = f"*Transfer:* {direction}\n"
        if entry.vendor and entry.vendor != transfer_default_vendor():
            who_line += f"*Vendor:* {entry.vendor}\n"
    else:
        who_line = f"*Vendor:* {entry.vendor}\n"
    return (
        f"🚀 *New {type_label} Submitted*\n\n"
        f"{who_line}"
        f"*Total:* {total_str}\n"
        f"*Staff:* {entry.submitted_by_name or 'Unknown'}\n"
        f"{notes_line}\n"
        f"🔗 *View Document:* {build_invoice_file_url(entry)}"
    )


# A timed-out send's confirmation can lag a few seconds behind - these
# retries are spent BEFORE giving up and falling back to "ambiguous", not on
# top of it, since this already runs off the HTTP request in a background
# thread (see send_invoice_notifications).
_RECONCILE_ATTEMPTS = 3
_RECONCILE_DELAY_SECONDS = 4


def _reconcile_ambiguous_send(text: str) -> bool:
    for attempt in range(_RECONCILE_ATTEMPTS):
        if find_sent_message(text):
            return True
        if attempt < _RECONCILE_ATTEMPTS - 1:
            time.sleep(_RECONCILE_DELAY_SECONDS)
    return False


# --- App Review demo accounts (app/demo.py) -------------------------------------
# A demo login's submission is saved like any other but never sent: each
# channel is recorded exactly like the "not configured" path (sent False +
# an error text), with DEMO_NOT_SENT as the text, and _processing_status /
# the invoice_failed alert treat that as done, never failed.


def is_demo_submission(entry: InvoiceSubmission) -> bool:
    return entry.submitted_by_user_id is not None and is_demo_username(entry.submitted_by_name)


def is_demo_not_sent(sent: bool, error: Optional[str]) -> bool:
    return not sent and error == DEMO_NOT_SENT


# --- Channels a store has switched off (KloudC RetailOps M2 store features, contract 1.3) ---
# A store without WhatsApp (no WHAPI_API_TOKEN) or without an AP inbox
# (AP_INBOX_EMAIL blank) records that channel like this - not sent, this text
# as the reason - and it never counts as failed: the invoice is done once the
# channels the store does have are done, and no "failed to send" alert goes
# out for a channel the store doesn't use. A misconfigured channel the store
# DOES use (token set but no chat id, inbox set but no SMTP login) still fails.
WHATSAPP_OFF = "WhatsApp is not set up for this store."
AP_EMAIL_OFF = "The AP inbox email is not set up for this store."


def is_channel_off(sent: bool, error: Optional[str]) -> bool:
    return not sent and error in (WHATSAPP_OFF, AP_EMAIL_OFF)


def _channel_feature_on(feature: str) -> bool:
    from app.store.settings_view import has_store_feature

    return has_store_feature(feature)


def record_demo_not_sent(db: Session, entry: InvoiceSubmission, channels: List[str]) -> InvoiceSubmission:
    if "whatsapp" in channels:
        entry.whatsapp_sent = False
        entry.whatsapp_error = DEMO_NOT_SENT
        entry.whatsapp_ambiguous = False
        entry.whatsapp_status_overridden_by = None
    if "toast_email" in channels:
        entry.toast_email_sent = False
        entry.toast_email_error = DEMO_NOT_SENT
        entry.toast_email_status_overridden_by = None
    db.commit()
    db.refresh(entry)
    return entry


def _attempt_whatsapp(db: Session, entry: InvoiceSubmission) -> None:
    if is_demo_submission(entry):
        # Belt and braces: whoever pressed Resend, a demo submission never
        # reaches the real WhatsApp group.
        record_demo_not_sent(db, entry, ["whatsapp"])
        return
    text = _whatsapp_text(entry)
    # A real automated attempt always supersedes a prior manual override -
    # once the real system has an opinion again, the "an admin said so" label
    # must not linger next to it.
    entry.whatsapp_status_overridden_by = None
    try:
        send_whatsapp_message(text)
        entry.whatsapp_sent = True
        entry.whatsapp_error = None
        entry.whatsapp_ambiguous = False
    except WhapiAmbiguousTimeout as exc:
        if _reconcile_ambiguous_send(text):
            # The timed-out POST actually went through - report the real
            # outcome (Sent), not a permanently "uncertain" status.
            entry.whatsapp_sent = True
            entry.whatsapp_error = None
            entry.whatsapp_ambiguous = False
        else:
            # Still can't confirm either way after retrying - genuinely
            # unknown, not a confirmed failure. See WhapiAmbiguousTimeout's
            # docstring and whatsapp_ambiguous's column comment for why a
            # human deciding whether to Resend needs to see this
            # differently from a real failure.
            entry.whatsapp_sent = False
            entry.whatsapp_error = str(exc)
            entry.whatsapp_ambiguous = True
    except WhapiNotConfigured as exc:
        entry.whatsapp_sent = False
        entry.whatsapp_error = str(exc) if _channel_feature_on("whatsapp") else WHATSAPP_OFF
        entry.whatsapp_ambiguous = False
    except Exception as exc:  # noqa: BLE001 - a send failure must never break the submission
        logger.exception("WhatsApp send failed for invoice submission %s", entry.id)
        entry.whatsapp_sent = False
        entry.whatsapp_error = f"Send failed: {exc}"
        entry.whatsapp_ambiguous = False
    db.commit()


def _attempt_toast_email(db: Session, entry: InvoiceSubmission, uploads_dir: pathlib.Path) -> None:
    # Matches the original Apps Script exactly: it emails Toast unconditionally
    # for every submission type, including "other" - there's no type check in
    # its onFormSubmit at all before the email step runs.
    # Same reasoning as _attempt_whatsapp: a real attempt always supersedes a
    # prior manual override.
    entry.toast_email_status_overridden_by = None
    if is_demo_submission(entry):
        record_demo_not_sent(db, entry, ["toast_email"])
        return
    try:
        if entry.pdf_filename is None:
            src = uploads_dir / entry.stored_filename
            pdf_name = f"{entry.file_token}.pdf"
            convert_to_pdf(src, uploads_dir / pdf_name)
            entry.pdf_filename = pdf_name
            db.commit()
        send_toast_invoice_email(
            vendor=entry.vendor,
            submission_type=entry.submission_type,
            total_amount=entry.total_amount,
            submitted_by_name=entry.submitted_by_name,
            pdf_path=uploads_dir / entry.pdf_filename,
            pdf_filename=entry.original_filename
            if entry.original_filename.lower().endswith(".pdf")
            else f"{pathlib.Path(entry.original_filename).stem}.pdf",
            # A photo upload: build the Toast attachment from the original
            # image rather than its stored PDF copy (sharper OCR).
            source_path=None
            if entry.stored_filename == entry.pdf_filename
            else uploads_dir / entry.stored_filename,
        )
        entry.toast_email_sent = True
        entry.toast_email_error = None
    except (UnsupportedFileType, EmailNotConfigured) as exc:
        entry.toast_email_sent = False
        off = isinstance(exc, EmailNotConfigured) and not _channel_feature_on("ap_inbox")
        entry.toast_email_error = AP_EMAIL_OFF if off else str(exc)
    except Exception as exc:  # noqa: BLE001 - a send failure must never break the submission
        logger.exception("Toast email send failed for invoice submission %s", entry.id)
        entry.toast_email_sent = False
        entry.toast_email_error = f"Send failed: {exc}"
    db.commit()


def create_invoice_submission(
    db: Session,
    vendor: str,
    submission_type: str,
    total_amount: Optional[float],
    notes: Optional[str],
    user: Optional[User],
    original_filename: str,
    content_type: Optional[str],
    file_bytes: bytes,
    transfer_direction: Optional[str] = None,
) -> InvoiceSubmission:
    if submission_type not in VALID_TYPES:
        raise ValueError(f"submission_type must be one of {sorted(VALID_TYPES)}, got '{submission_type}'")
    transfer_direction = (transfer_direction or "").strip() or None
    if submission_type == TRANSFER_TYPE:
        if transfer_direction not in TRANSFER_DIRECTIONS:
            raise ValueError(
                f"transfer_direction is required for a transfer: one of {sorted(TRANSFER_DIRECTIONS)}"
            )
        vendor = (vendor or "").strip() or transfer_default_vendor()
    elif transfer_direction is not None:
        raise ValueError("transfer_direction is only allowed when submission_type is 'transfer'")
    if not (vendor or "").strip():
        raise ValueError("Vendor is required")
    if not file_bytes:
        raise ValueError("Uploaded file is empty")

    uploads_dir = _uploads_dir()
    token = secrets.token_urlsafe(24)
    suffix = pathlib.Path(original_filename).suffix.lower()
    stored_filename = f"{token}{suffix}"
    (uploads_dir / stored_filename).write_bytes(file_bytes)

    is_pdf = suffix == ".pdf" or content_type == "application/pdf"

    entry = InvoiceSubmission(
        vendor=vendor.strip(),
        submission_type=submission_type,
        transfer_direction=transfer_direction,
        total_amount=total_amount,
        notes=notes.strip() if notes else None,
        submitted_by_user_id=user.id if user else None,
        submitted_by_name=user.username if user else None,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=content_type,
        file_token=token,
        file_hash=hash_file_bytes(file_bytes),
        pdf_filename=stored_filename if is_pdf else None,
        created_at=utcnow(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # WhatsApp/Toast email are NOT attempted here - both are slow external calls (Whapi alone can
    # take up to a minute - see whapi_client.py), and blocking the HTTP response on them made
    # "Submit" feel hung for that whole time. See send_invoice_notifications below, which the
    # router schedules via FastAPI BackgroundTasks instead - the response returns as soon as the
    # record + file are saved, and the frontend polls GET /api/invoices/{id} to pick up the result
    # once it's actually done.
    return entry


def _send_whatsapp_only(submission_id: int) -> None:
    db = SessionLocal()
    try:
        entry = db.get(InvoiceSubmission, submission_id)
        if entry is not None:
            _attempt_whatsapp(db, entry)
    finally:
        db.close()


def _send_toast_email_only(submission_id: int) -> None:
    db = SessionLocal()
    try:
        entry = db.get(InvoiceSubmission, submission_id)
        if entry is not None:
            _attempt_toast_email(db, entry, _uploads_dir())
    finally:
        db.close()


def send_invoice_notifications(submission_id: int, channels: List[str]) -> None:
    """Runs the WhatsApp/Toast-email attempts for one submission in the background, in parallel -
    each on its own thread with its own DB session (SQLAlchemy Sessions aren't safe to share
    across threads, and Whapi in particular can be slow enough - see whapi_client.py - that
    running these sequentially would make Toast email's status look just as stuck as WhatsApp's,
    even though the email itself typically resolves in a couple of seconds once it actually
    starts). Used for both a fresh submission and a Resend - same underlying attempts, just
    parameterized by which channel(s) to (re)try.
    """
    # Each worker runs in its own copy of this context: a pool thread does
    # not inherit context variables, and without the store (multi mode) the
    # sends could not open the store's database or read its settings.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        if "whatsapp" in channels:
            futures.append(executor.submit(contextvars.copy_context().run, _send_whatsapp_only, submission_id))
        if "toast_email" in channels:
            futures.append(executor.submit(contextvars.copy_context().run, _send_toast_email_only, submission_id))
        for f in futures:
            f.result()


def mark_pending(db: Session, entry: InvoiceSubmission, channels: List[str]) -> InvoiceSubmission:
    """Clears the given channel(s) back to "not yet attempted" (sent=False, error=None) -
    called synchronously right before scheduling a background retry (Resend), so the immediate
    HTTP response already reflects "pending" instead of the previous attempt's stale error text.
    Without this, the frontend's "is this resolved yet" check would see the old error and
    conclude a fresh retry it hasn't even run yet is already done - the same
    sent=False/error=None shape a brand new submission starts in, reused here on purpose so the
    frontend doesn't need separate logic for "just submitted" vs. "just resent".
    """
    if "whatsapp" in channels:
        entry.whatsapp_sent = False
        entry.whatsapp_error = None
        entry.whatsapp_ambiguous = False
        entry.whatsapp_status_overridden_by = None
    if "toast_email" in channels:
        entry.toast_email_sent = False
        entry.toast_email_error = None
        entry.toast_email_status_overridden_by = None
    db.commit()
    db.refresh(entry)
    return entry


class InvoiceStatusOverrideError(Exception):
    """Raised for an invalid channel/status combination - the router already
    validates these against Literal types via InvoiceStatusOverrideRequest,
    so this is a defense-in-depth check, not the primary validation path.
    """


def override_channel_status(
    db: Session, entry: InvoiceSubmission, channel: str, status: str, admin: User
) -> InvoiceSubmission:
    """Admin-only manual override of one send channel's status - the safety
    valve for when the real automated attempt is stuck or unreliable (e.g.
    Whapi timing out for reasons outside this app's control) and staff need
    the record to reflect reality now rather than wait on a flaky external
    API. Directly overwrites the same fields a real attempt would set, so the
    already-computed `_processing_status`/`can_mark_toast_closed` pick up the
    change automatically - there is no separate "combined status" to keep in
    sync. Records who did it (see the model's own column comments for why),
    cleared automatically the next time a real attempt touches this channel.
    """
    if channel not in ("whatsapp", "toast_email"):
        raise InvoiceStatusOverrideError(f"Unknown channel '{channel}'")
    if status not in ("sent", "failed", "pending"):
        raise InvoiceStatusOverrideError(f"Unknown status '{status}'")

    sent = status == "sent"
    error = f"Manually marked failed by {admin.username}" if status == "failed" else None

    if channel == "whatsapp":
        entry.whatsapp_sent = sent
        entry.whatsapp_error = error
        entry.whatsapp_ambiguous = False
        entry.whatsapp_status_overridden_by = admin.username
    else:
        entry.toast_email_sent = sent
        entry.toast_email_error = error
        entry.toast_email_status_overridden_by = admin.username

    db.commit()
    db.refresh(entry)
    return entry


def list_invoice_submissions(
    db: Session, viewer: Optional[User] = None, include_demo: bool = False
) -> List[InvoiceSubmission]:
    """Active (not soft-deleted) submissions only - see list_trashed_submissions for the rest.
    Demo submissions are hidden unless the viewer made them (or is an admin asking include_demo)."""
    stmt = (
        select(InvoiceSubmission)
        .where(InvoiceSubmission.deleted_at.is_(None), not_demo(db, viewer, include_demo))
        .order_by(InvoiceSubmission.created_at.desc(), InvoiceSubmission.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_trashed_submissions(
    db: Session, viewer: Optional[User] = None, include_demo: bool = False
) -> List[InvoiceSubmission]:
    stmt = (
        select(InvoiceSubmission)
        .where(InvoiceSubmission.deleted_at.is_not(None), not_demo(db, viewer, include_demo))
        .order_by(InvoiceSubmission.deleted_at.desc().nulls_last(), InvoiceSubmission.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_invoice_submission(db: Session, submission_id: int) -> Optional[InvoiceSubmission]:
    return db.get(InvoiceSubmission, submission_id)


class InvoiceDeleteStateError(Exception):
    """Raised for a delete-workflow action attempted from the wrong state -
    e.g. approving a request that doesn't exist, or permanently deleting a
    submission that was never actually moved to trash first. See the
    functions below for the exact state machine (active -> requested ->
    trashed -> permanently gone, with restore possible any time before the
    last step).
    """


def request_delete(db: Session, entry: InvoiceSubmission, user: User) -> InvoiceSubmission:
    """A non-admin (or anyone) flags a submission for removal - doesn't delete anything by
    itself, just marks it for an admin to review. See soft_delete for the actual removal step.
    """
    if entry.deleted_at is not None:
        raise InvoiceDeleteStateError("This submission is already in the trash.")
    entry.delete_requested_at = utcnow()
    entry.delete_requested_by_name = user.username
    db.commit()
    db.refresh(entry)
    return entry


def cancel_delete_request(db: Session, entry: InvoiceSubmission) -> InvoiceSubmission:
    """Admin rejects a pending delete request - the submission stays active, untouched."""
    entry.delete_requested_at = None
    entry.delete_requested_by_name = None
    db.commit()
    db.refresh(entry)
    return entry


def soft_delete(db: Session, entry: InvoiceSubmission, admin_user: User) -> InvoiceSubmission:
    """Moves a submission to trash - used both to approve an existing delete request and for an
    admin's own direct delete (admin has full delete access, but even that goes through trash
    first rather than an instant, unrecoverable delete - see permanently_delete_submission for the
    one action that's actually irreversible). Clears any pending request either way, since this
    resolves it regardless of whether one existed.
    """
    if entry.deleted_at is not None:
        raise InvoiceDeleteStateError("This submission is already in the trash.")
    entry.deleted_at = utcnow()
    entry.deleted_by_name = admin_user.username
    entry.delete_requested_at = None
    entry.delete_requested_by_name = None
    db.commit()
    db.refresh(entry)
    return entry


def restore_submission(db: Session, entry: InvoiceSubmission) -> InvoiceSubmission:
    """Pulls a submission back out of trash to active - undoes soft_delete completely, including
    any delete-request trace, so a restored submission looks exactly like it was never touched.
    """
    if entry.deleted_at is None:
        raise InvoiceDeleteStateError("This submission isn't in the trash.")
    entry.deleted_at = None
    entry.deleted_by_name = None
    entry.delete_requested_at = None
    entry.delete_requested_by_name = None
    db.commit()
    db.refresh(entry)
    return entry


def permanently_delete_submission(db: Session, entry: InvoiceSubmission) -> None:
    """The one genuinely irreversible step - only allowed on a submission already sitting in
    trash (deleted_at is not None), so nothing can go from active straight to permanently gone in
    one action. Removes the DB row and its uploaded file(s) from disk.
    """
    if entry.deleted_at is None:
        raise InvoiceDeleteStateError("Only a trashed submission can be permanently deleted - move it to trash first.")
    uploads_dir = _uploads_dir()
    stored_path = uploads_dir / entry.stored_filename
    if stored_path.exists():
        stored_path.unlink()
    if entry.pdf_filename and entry.pdf_filename != entry.stored_filename:
        pdf_path = uploads_dir / entry.pdf_filename
        if pdf_path.exists():
            pdf_path.unlink()
    db.delete(entry)
    db.commit()


def _processing_status(entry: InvoiceSubmission) -> str:
    """One coarse business status. The two real downstream send outcomes
    this app can observe automatically (WhatsApp/Toast email) only ever get
    a submission to "in_progress" - "completed" additionally requires
    `toast_closed_at`, a human's manual confirmation that Toast actually
    finished processing this into a real closed bill. There is no Toast PO/
    Purchasing API access and no webhook (see the KloudC RetailOps skill notes, gotcha
    #15), so that event is genuinely unobservable automatically; guessing
    "completed" the moment the two sends succeed would be a real, honest
    claim this app can't back up (a real user report: "let me update the
    status manually when submitted to Toast - closure on Toast"). Computed
    here, once, so web and mobile both just display the same backend-owned
    value rather than each re-deriving their own notion of "done".

    An UNTRACKED_TYPE or TRANSFER_TYPE submission (NO_TOAST_TYPES) never
    gets a Toast email attempt at all (see those constants' comments) - Toast readiness/closure simply
    doesn't apply to it, so completion depends on WhatsApp alone rather than
    leaving it permanently stuck at "in_progress" waiting on a Toast step
    that will never happen.
    """
    wa_demo = is_demo_not_sent(entry.whatsapp_sent, entry.whatsapp_error)
    if wa_demo and (skips_toast(entry.submission_type) or is_demo_not_sent(entry.toast_email_sent, entry.toast_email_error)):
        # An App Review demo submission: saved, deliberately not sent - done.
        return "completed"
    wa_off = is_channel_off(entry.whatsapp_sent, entry.whatsapp_error)
    if skips_toast(entry.submission_type) or is_channel_off(entry.toast_email_sent, entry.toast_email_error):
        if wa_off:
            return "completed"  # nothing this store sends: saved is done
        whatsapp_done = entry.whatsapp_sent or entry.whatsapp_error is not None
        if not whatsapp_done:
            return "pending"
        whatsapp_failed = (not entry.whatsapp_sent) and entry.whatsapp_error is not None and not entry.whatsapp_ambiguous
        if whatsapp_failed:
            return "failed"
        return "completed" if entry.whatsapp_sent else "in_progress"

    if wa_off:
        # The AP inbox email alone (this store has no WhatsApp).
        if not (entry.toast_email_sent or entry.toast_email_error is not None):
            return "pending"
        if not entry.toast_email_sent:
            return "failed"
        return "completed" if entry.toast_closed_at is not None else "in_progress"

    whatsapp_done = entry.whatsapp_sent or entry.whatsapp_error is not None
    toast_done = entry.toast_email_sent or entry.toast_email_error is not None
    whatsapp_failed = (not entry.whatsapp_sent) and entry.whatsapp_error is not None and not entry.whatsapp_ambiguous
    toast_failed = (not entry.toast_email_sent) and entry.toast_email_error is not None

    if not whatsapp_done and not toast_done:
        return "pending"
    if whatsapp_failed or toast_failed:
        return "failed"
    if entry.whatsapp_sent and entry.toast_email_sent and entry.toast_closed_at is not None:
        return "completed"
    return "in_progress"


class ToastClosureStateError(Exception):
    """Raised when marking a submission "closed in Toast" is attempted
    before it's actually eligible (both real sends must succeed first)."""


def can_mark_toast_closed(entry: InvoiceSubmission) -> bool:
    """The action is only meaningful once both real sends have actually
    succeeded - marking it "closed in Toast" before Toast even received the
    email would be a nonsensical, unearned confirmation. Always False for
    UNTRACKED_TYPE - nothing was ever sent to Toast to close, so the
    concept doesn't apply (entry.toast_email_sent is always False for this
    type anyway, so this already falls out naturally, spelled out here for
    clarity).
    """
    if skips_toast(entry.submission_type) or is_channel_off(entry.toast_email_sent, entry.toast_email_error):
        return False
    whatsapp_ok = entry.whatsapp_sent or is_channel_off(entry.whatsapp_sent, entry.whatsapp_error)
    return bool(whatsapp_ok and entry.toast_email_sent and entry.toast_closed_at is None)


def mark_toast_closed(db: Session, entry: InvoiceSubmission, user: User) -> InvoiceSubmission:
    """A human confirms (having checked Toast's own UI themselves) that this
    submission was actually processed into a closed bill there - the one
    event this app cannot detect on its own. Denormalizes the confirming
    user's name, same pattern as submitted_by_name/deleted_by_name.
    """
    whatsapp_ok = entry.whatsapp_sent or is_channel_off(entry.whatsapp_sent, entry.whatsapp_error)
    if not (whatsapp_ok and entry.toast_email_sent):
        raise ToastClosureStateError(
            "Both WhatsApp and Toast email must succeed before this can be marked closed in Toast."
        )
    entry.toast_closed_at = utcnow()
    entry.toast_closed_by_name = user.username
    db.commit()
    db.refresh(entry)
    return entry


def unmark_toast_closed(db: Session, entry: InvoiceSubmission) -> InvoiceSubmission:
    """Undoes a mistaken confirmation - back to "in_progress", not a new,
    separate state.
    """
    entry.toast_closed_at = None
    entry.toast_closed_by_name = None
    db.commit()
    db.refresh(entry)
    return entry


def submission_out(entry: InvoiceSubmission) -> dict:
    def _iso(value: Optional[dt.datetime]) -> Optional[str]:
        return value.isoformat() if isinstance(value, dt.datetime) else value

    return {
        "id": entry.id,
        "vendor": entry.vendor,
        "submission_type": entry.submission_type,
        # Additive (Oct 2026): null unless submission_type == "transfer".
        "transfer_direction": entry.transfer_direction,
        "transfer_direction_label": TRANSFER_DIRECTIONS.get(entry.transfer_direction or ""),
        "type_label": type_display_label(entry.submission_type, entry.transfer_direction),
        "toast_applicable": toast_applicable(entry),
        "total_amount": entry.total_amount,
        "notes": entry.notes,
        "submitted_by_name": entry.submitted_by_name,
        "original_filename": entry.original_filename,
        "file_url": build_invoice_file_url(entry),
        "whatsapp_sent": entry.whatsapp_sent,
        "whatsapp_error": entry.whatsapp_error,
        "whatsapp_ambiguous": entry.whatsapp_ambiguous,
        "whatsapp_status_overridden_by": entry.whatsapp_status_overridden_by,
        "toast_email_sent": entry.toast_email_sent,
        "toast_email_error": entry.toast_email_error,
        "toast_email_status_overridden_by": entry.toast_email_status_overridden_by,
        "toast_closed_at": _iso(entry.toast_closed_at),
        "toast_closed_by_name": entry.toast_closed_by_name,
        "can_mark_toast_closed": can_mark_toast_closed(entry),
        "processing_status": _processing_status(entry),
        "created_at": _iso(entry.created_at),
        "delete_requested_at": _iso(entry.delete_requested_at),
        "delete_requested_by_name": entry.delete_requested_by_name,
        "deleted_at": _iso(entry.deleted_at),
        "deleted_by_name": entry.deleted_by_name,
        # Multi-store MT6 (additive): POS-neutral names for the same values
        # ("ap" = the accounts-payable inbox the PDF is emailed to). Every
        # toast_* key above stays, unchanged, for app builds 5-9.
        **ap_aliases(entry),
    }


def toast_applicable(entry: InvoiceSubmission) -> bool:
    """False for a type that never emails the AP inbox, and for a store
    without an AP inbox (the channel was recorded as off)."""
    return not skips_toast(entry.submission_type) and not is_channel_off(
        entry.toast_email_sent, entry.toast_email_error
    )


def ap_aliases(entry: InvoiceSubmission) -> dict:
    """The ap_* keys: each equals its toast_* twin in submission_out."""
    closed_at = entry.toast_closed_at
    return {
        "ap_applicable": toast_applicable(entry),
        "ap_email_sent": entry.toast_email_sent,
        "ap_email_error": entry.toast_email_error,
        "ap_email_status_overridden_by": entry.toast_email_status_overridden_by,
        "ap_closed_at": closed_at.isoformat() if isinstance(closed_at, dt.datetime) else closed_at,
        "ap_closed_by_name": entry.toast_closed_by_name,
        "can_mark_ap_closed": can_mark_toast_closed(entry),
    }


def resolve_file_path(entry: InvoiceSubmission) -> pathlib.Path:
    return _uploads_dir() / entry.stored_filename


# Toast can take up to 24h to finish processing a submitted invoice into a
# real bill, so the matching physical delivery is often only counted the
# NEXT day, not the same day the invoice was submitted - this nudge needs to
# look back further than just today to actually catch that common case.
DELIVERY_NUDGE_LOOKBACK_DAYS = 2


def get_vendors_submitted_today(db: Session) -> List[str]:
    """Distinct vendor names from a recent (not-yet-confirmed) invoice/return
    submission - used to pre-suggest "We received a delivery today" vendors
    on the inventory upload flow, so submitting an invoice and confirming
    the matching delivery don't have to be remembered as two separate manual
    steps. "other" submissions are excluded, same reasoning as
    reconciliation.py::get_invoice_gaps - they aren't necessarily a real
    delivery.

    Looks back DELIVERY_NUDGE_LOOKBACK_DAYS, not just today - see that
    constant's comment. A vendor that already has a purchase_log entry on or
    after their invoice date is excluded, so this stops re-suggesting a
    vendor whose delivery has already been confirmed once.
    """
    today = restaurant_today()
    window_start = today - dt.timedelta(days=DELIVERY_NUDGE_LOOKBACK_DAYS)

    submissions = (
        db.execute(
            select(InvoiceSubmission).where(
                InvoiceSubmission.submission_type.in_(["invoice", "return"]), not_demo(db)
            )
        )
        .scalars()
        .all()
    )
    candidates = {
        s.vendor
        for s in submissions
        if window_start <= to_restaurant_date(s.created_at) <= today
    }
    if not candidates:
        return []

    confirmed_vendors = {
        (supplier or "").strip().lower()
        for supplier, received_date in db.execute(
            select(PurchaseLogEntry.supplier, PurchaseLogEntry.received_date).where(
                PurchaseLogEntry.supplier.is_not(None), PurchaseLogEntry.received_date >= window_start
            )
        )
    }
    return sorted(v for v in candidates if v.strip().lower() not in confirmed_vendors)


def transfer_default_vendor() -> str:
    """A transfer's vendor when staff leave it blank: the store itself."""
    from app.store.settings_view import store_display_name

    return store_display_name()


def __getattr__(name):
    # The old constant, now the store's own name (read at call time).
    if name == "TRANSFER_DEFAULT_VENDOR":
        return transfer_default_vendor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
