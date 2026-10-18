from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.demo import hidden_from, is_demo_user
from app.features import require_feature
from app.models import InvoiceSubmission, User
from app.schemas import InvoiceResendRequest, InvoiceStatusOverrideRequest
from app.services.invoice_submission import (
    InvoiceDeleteStateError,
    InvoiceStatusOverrideError,
    ToastClosureStateError,
    cancel_delete_request,
    create_invoice_submission,
    find_duplicate_by_hash,
    get_invoice_submission,
    is_demo_submission,
    list_invoice_submissions,
    list_trashed_submissions,
    mark_pending,
    mark_toast_closed,
    override_channel_status,
    permanently_delete_submission,
    record_demo_not_sent,
    request_delete,
    resolve_file_path,
    restore_submission,
    send_invoice_notifications,
    skips_toast,
    soft_delete,
    submission_out,
    unmark_toast_closed,
)
from app.services.invoice_close_suggestions import close_suggestion_key, get_close_suggestions
from app.services.suggestion_dismissals import dismiss, undismiss
from app.team.source_sync import task_sources_changed

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(require_feature("invoice_submission"))])


@router.get("")
def list_invoices(
    include_demo: bool = Query(False, description="Admins only: also list App Review demo submissions."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entries = list_invoice_submissions(db, viewer=user, include_demo=include_demo)
    # Additive (automation batch item 5): a suggested "Closed in Toast" per
    # open invoice with evidence, else null. Never stops the list loading.
    suggestions = get_close_suggestions(db)
    items = []
    for e in entries:
        out = submission_out(e)
        out["close_suggestion"] = suggestions.get(e.id) if out.get("can_mark_toast_closed") else None
        items.append(out)
    return {
        "count": len(entries),
        "items": items,
        "close_suggested_count": sum(1 for i in items if i["close_suggestion"]),
    }


# Registered before /{submission_id}. The open invoices that look closed in
# Toast already (a Toast receiving of the vendor after the upload, or a
# delivery-like stock rise) - for the Overview tile and the app's Home.
@router.get("/close-suggestions")
def list_close_suggestions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    suggestions = get_close_suggestions(db)
    items = []
    for submission_id, suggestion in sorted(suggestions.items()):
        entry = get_invoice_submission(db, submission_id)
        if entry is None or hidden_from(db, user, entry.submitted_by_user_id, entry.submitted_by_name):
            continue
        items.append(
            {
                "submission_id": entry.id,
                "vendor": entry.vendor,
                "total_amount": entry.total_amount,
                "created_at": submission_out(entry)["created_at"],
                "suggestion": suggestion,
            }
        )
    return {"count": len(items), "items": items}


# Registered before /{submission_id} - otherwise "trash" would be parsed as
# an attempted (invalid) submission_id by that route instead of reaching
# this one.
@router.get("/trash", dependencies=[Depends(require_admin)])
def list_trash(
    include_demo: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entries = list_trashed_submissions(db, viewer=user, include_demo=include_demo)
    return {"count": len(entries), "items": [submission_out(e) for e in entries]}


# Also registered before /{submission_id} for the same reason. The client
# computes the SHA-256 itself (over the exact bytes it's about to upload) and
# asks here first - lets the UI warn before spending the time/bandwidth on a
# real submission, without needing to upload the file just to check.
@router.get("/check-duplicate")
def check_duplicate(
    file_hash: str = Query(..., min_length=64, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = find_duplicate_by_hash(db, file_hash, viewer=user)
    return {"duplicate": existing is not None, "existing": submission_out(existing) if existing else None}


@router.get("/{submission_id}")
def get_invoice(submission_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    # A demo submission is invisible to real staff (admins can still open it by id).
    if entry is None or hidden_from(db, user, entry.submitted_by_user_id, entry.submitted_by_name):
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission_out(entry)


@router.post("")
async def submit_invoice(
    background_tasks: BackgroundTasks,
    submission_type: str = Form(...),
    file: UploadFile = File(...),
    # Optional at the form level only so a "transfer" can omit it (it then
    # defaults to TRANSFER_DEFAULT_VENDOR); every other type still gets the
    # same "Vendor is required" 400 as before, from create_invoice_submission.
    vendor: Optional[str] = Form(None),
    total_amount: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    transfer_direction: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vendor = (vendor or "").strip()

    parsed_total = None
    if total_amount is not None and total_amount.strip() != "":
        try:
            parsed_total = float(total_amount)
        except ValueError:
            raise HTTPException(status_code=400, detail="Total must be a number")

    file_bytes = await file.read()
    try:
        entry = create_invoice_submission(
            db,
            vendor=vendor,
            submission_type=submission_type,
            total_amount=parsed_total,
            notes=notes,
            user=user,
            original_filename=file.filename or "upload",
            content_type=file.content_type,
            file_bytes=file_bytes,
            transfer_direction=transfer_direction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # WhatsApp/Toast email happen after the response is sent - see
    # send_invoice_notifications's own docstring for why. The frontend polls
    # GET /api/invoices/{id} to pick up the real outcome once it lands.
    # NO_TOAST_TYPES (untracked, transfer) never get a Toast email attempt at
    # all - see those constants' comments in invoice_submission.py.
    channels = ["whatsapp"] if skips_toast(entry.submission_type) else ["whatsapp", "toast_email"]
    if is_demo_user(user):
        # App Review demo login: saved, never sent (app/demo.py).
        return submission_out(record_demo_not_sent(db, entry, channels))
    background_tasks.add_task(send_invoice_notifications, entry.id, channels)
    return submission_out(entry)


@router.post("/{submission_id}/resend")
def resend_invoice(
    submission_id: int,
    payload: InvoiceResendRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    # Belt and suspenders: a NO_TOAST_TYPES entry (untracked/transfer) never gets a Toast email
    # attempt regardless of what's requested here - the UI never offers it
    # as an option for this type, but this keeps the rule enforced
    # server-side too, not just by omission in the client.
    channels = [c for c in payload.channels if not (c == "toast_email" and skips_toast(entry.submission_type))]
    # Reset to "pending" synchronously (see mark_pending's docstring for why), then schedule the
    # actual retry in the background - same pattern as a fresh submission, so a resend doesn't
    # hang the request either. The frontend polls for the real outcome once it lands.
    if is_demo_user(user):
        # App Review demo login: nothing is ever sent. Its own submissions are
        # re-recorded as "not sent"; anyone else's real record is left exactly
        # as it was (a reviewer must never change a real invoice's status).
        if is_demo_submission(entry):
            entry = record_demo_not_sent(db, entry, channels)
        return submission_out(entry)
    entry = mark_pending(db, entry, channels)
    background_tasks.add_task(send_invoice_notifications, entry.id, channels)
    return submission_out(entry)


# --- Admin-only manual status override --------------------------------------
# Safety valve for when the real automated WhatsApp/Toast-email attempt is
# stuck or unreliable (e.g. Whapi timing out for reasons outside this app's
# control - see services/invoice_submission.py's own docstring on this) and
# staff need the record to reflect reality now. Admin-only since this
# overwrites what's otherwise a factual record of what actually happened.


@router.post("/{submission_id}/override-status", dependencies=[Depends(require_admin)])
def post_override_status(
    submission_id: int,
    payload: InvoiceStatusOverrideRequest,
    admin: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        entry = override_channel_status(db, entry, payload.channel, payload.status, admin)
    except InvoiceStatusOverrideError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return submission_out(entry)


# --- Manual "closed in Toast" confirmation ---------------------------------
# There is no Toast PO/Purchasing API access and no webhook, so Toast
# actually finishing its own processing into a real closed bill is not an
# event this app can detect on its own - a human checks Toast directly and
# confirms it here. Available to anyone with Invoice Submission access
# (a factual confirmation, not a destructive action), not admin-only.


@router.post("/{submission_id}/mark-toast-closed")
@router.post("/{submission_id}/mark-ap-closed")  # multi-store MT6: neutral alias, same handler
def post_mark_toast_closed(submission_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        entry = mark_toast_closed(db, entry, user)
    except ToastClosureStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task_sources_changed("invoice")  # its Team task closes now (Oct 15)
    return submission_out(entry)


@router.post("/{submission_id}/close-suggestion/dismiss")
def post_dismiss_close_suggestion(
    submission_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """"Not closed yet": hides the suggested close for this invoice for good
    (the invoice itself is unchanged and can still be marked closed)."""
    entry = get_invoice_submission(db, submission_id)
    if entry is None or hidden_from(db, user, entry.submitted_by_user_id, entry.submitted_by_name):
        raise HTTPException(status_code=404, detail="Submission not found")
    row = dismiss(db, "invoice_close", close_suggestion_key(entry.id), user)
    return {"submission_id": entry.id, "dismissed": True, "dismissed_by": row.dismissed_by_name}


@router.delete("/{submission_id}/close-suggestion/dismiss")
def delete_dismiss_close_suggestion(submission_id: int, db: Session = Depends(get_db)):
    if not undismiss(db, "invoice_close", close_suggestion_key(submission_id)):
        raise HTTPException(status_code=404, detail="That suggestion isn't dismissed.")
    return {"submission_id": submission_id, "dismissed": False}


@router.post("/{submission_id}/unmark-toast-closed")
@router.post("/{submission_id}/unmark-ap-closed")  # multi-store MT6: neutral alias, same handler
def post_unmark_toast_closed(submission_id: int, db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    if entry.toast_closed_at is None:
        # Nothing to undo (e.g. a stale screen, or someone else already undid it).
        raise HTTPException(
            status_code=409,
            detail="This invoice isn't marked closed in Toast, so there's nothing to undo. Refresh the list.",
        )
    return submission_out(unmark_toast_closed(db, entry))


# --- Delete request / approval / trash ------------------------------------
# Any user with Invoice Submission access can request a delete; only an
# admin can actually move something into trash, restore it, or permanently
# delete it (see services/invoice_submission.py's delete-workflow functions
# for the full state machine). Admin's own "Delete" button skips straight to
# soft_delete without a prior request - full delete access, per spec - but
# even that still lands in trash first, never an instant permanent delete.


@router.post("/{submission_id}/request-delete")
def post_request_delete(submission_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        entry = request_delete(db, entry, user)
    except InvoiceDeleteStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return submission_out(entry)


@router.post("/{submission_id}/cancel-delete-request", dependencies=[Depends(require_admin)])
def post_cancel_delete_request(submission_id: int, db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission_out(cancel_delete_request(db, entry))


@router.post("/{submission_id}/approve-delete", dependencies=[Depends(require_admin)])
def post_approve_delete(submission_id: int, admin: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        entry = soft_delete(db, entry, admin)
    except InvoiceDeleteStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task_sources_changed("invoice")  # its Team task is cancelled now (Oct 15)
    return submission_out(entry)


@router.post("/{submission_id}/delete", dependencies=[Depends(require_admin)])
def post_delete(submission_id: int, admin: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        entry = soft_delete(db, entry, admin)
    except InvoiceDeleteStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task_sources_changed("invoice")  # its Team task is cancelled now (Oct 15)
    return submission_out(entry)


@router.post("/{submission_id}/restore", dependencies=[Depends(require_admin)])
def post_restore(submission_id: int, db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        entry = restore_submission(db, entry)
    except InvoiceDeleteStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return submission_out(entry)


@router.delete("/{submission_id}/permanent", dependencies=[Depends(require_admin)])
def delete_permanent(submission_id: int, db: Session = Depends(get_db)):
    entry = get_invoice_submission(db, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        permanently_delete_submission(db, entry)
    except InvoiceDeleteStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": submission_id}


# Was a SEPARATE router with no auth dependency at all, reachable only by
# knowing the real, unguessable file_token - built that way specifically so
# WhatsApp group members without dashboard logins could open the link sent
# there. The user later decided that "unguessable but public" isn't an
# acceptable bar for what are real financial documents - every invoice file
# now requires an actual dashboard login (same invoice_submission feature
# gate as the rest of this router), full stop. This does mean a WhatsApp
# recipient with no dashboard account can no longer open that link directly -
# an accepted, deliberate tradeoff, not an oversight. The path itself
# (/public/invoices/{token}) is unchanged so links already sent out still
# resolve to the same entry once the viewer logs in, rather than 404ing.
public_router = APIRouter(
    prefix="/public/invoices",
    tags=["invoices-public"],
    dependencies=[Depends(require_feature("invoice_submission"))],
)


@public_router.get("/{identifier}")
def get_public_invoice_file(
    identifier: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    # Real reports since include a link like /public/invoices/17 (build_
    # invoice_file_url now uses the plain id, shorter/more readable in a
    # WhatsApp message - see that function's own docstring for why a token
    # is no longer needed for security here) - but a link sent before that
    # change still carries the old long file_token, so both are accepted.
    entry = None
    if identifier.isdigit():
        entry = db.get(InvoiceSubmission, int(identifier))
    if entry is None:
        entry = db.execute(
            select(InvoiceSubmission).where(InvoiceSubmission.file_token == identifier)
        ).scalar_one_or_none()
    # Same rule as GET /api/invoices/{id}: a demo submission's file is
    # invisible to real staff (admins can still open it).
    if entry is None or hidden_from(db, user, entry.submitted_by_user_id, entry.submitted_by_name):
        raise HTTPException(status_code=404, detail="Not found")
    path = resolve_file_path(entry)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on server")
    return FileResponse(path, media_type=entry.content_type or "application/octet-stream")
