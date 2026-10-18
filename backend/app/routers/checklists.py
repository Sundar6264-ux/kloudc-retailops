import datetime as dt
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
# request.form() yields Starlette's UploadFile (FastAPI's is a subclass, so an
# isinstance check against it always failed).
from starlette.datastructures import UploadFile

from app.db import get_db
from app.features import require_feature
from app.models import User
from app.schemas import ChecklistSubmitRequest
from app.services.checklist_schemas import get_schema, get_version_schema, list_schemas, registry
from app.services.checklist_submission import (
    ChecklistStaleVersionError,
    ChecklistValidationError,
    create_submission,
    get_submission_for_user,
    list_submissions,
    queue_whatsapp,
    send_checklist_notification,
    should_send_whatsapp,  # noqa: F401
    submission_out,
)
from app.team import media as media_svc
from app.team.source_sync import task_sources_changed
from app.timeutil import restaurant_timezone

router = APIRouter(
    prefix="/api/checklists",
    tags=["checklists"],
    dependencies=[Depends(require_feature("daily_checklists"))],
)


# Registered before /{submission_id} - otherwise "schemas" would be parsed
# as an attempted (invalid) submission_id by that route instead of reaching
# this one (same real gotcha as invoices.py's /trash route).
@router.get("/schemas")
def get_schemas():
    """Every active, published checklist's full structure (current version),
    keyed by type in the admins' order - for the "choose a checklist" hub
    without a second round trip. App build 5 parses every entry, so each
    one keeps the legacy shape (see checklist_engine.build_served)."""
    return {"schemas": list_schemas()}


@router.get("/schema/{checklist_type}")
def get_one_schema(checklist_type: str, version: Optional[int] = Query(None, ge=1)):
    """The current version, or `?version=N` (any version, even of an inactive
    checklist) to render an old submission with the questions it answered."""
    try:
        if version is not None:
            return get_version_schema(checklist_type, version)
        return get_schema(checklist_type)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown checklist type")


@router.get("/overview")
def get_overview(
    date: Optional[dt.date] = Query(None),
    user: User = Depends(require_feature("daily_checklists")),
    db: Session = Depends(get_db),
):
    """Per active checklist (admins' order): its settings and the latest
    submission, plus whether one came in on `date` (store-local day, default
    today) - the website Overview tiles. Like the list endpoint, a non-admin
    only ever sees their own submissions."""
    day = date or dt.datetime.now(restaurant_timezone()).date()
    out = []
    for checklist_type, definition in registry().definitions.items():
        if not (definition.active and definition.published):
            continue
        latest = list_submissions(db, user, checklist_type=checklist_type, limit=1)
        today = list_submissions(db, user, checklist_type=checklist_type, start_date=day, end_date=day, limit=500)
        out.append(
            {
                "checklist_type": checklist_type,
                "title": definition.title,
                "short_name": definition.short_name,
                "settings": definition.settings,
                "submitted_on_date": bool(today),
                "count_on_date": len(today),
                "latest": submission_out(latest[0], db) if latest else None,
            }
        )
    return {"date": day.isoformat(), "checklists": out}


@router.post("/media")
async def upload_checklist_photo(
    request: Request,
    user: User = Depends(require_feature("daily_checklists")),
    db: Session = Depends(get_db),
):
    """A photo for a file_upload question: multipart field `file`. Same
    processing and limits as team photos; returns {id, token, url, width,
    height}. Send the id in the answer; it's attached to the submission."""
    media_svc.check_request_size(request)
    form = await request.form(max_files=1, max_fields=5)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="Attach the photo as 'file'.")
        from starlette.concurrency import run_in_threadpool

        media = await run_in_threadpool(media_svc.save_upload, db, user, upload)
    finally:
        await form.close()
    return media_svc.media_out(media)


@router.post("/{checklist_type}/submit")
def submit_checklist(
    checklist_type: str,
    payload: ChecklistSubmitRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_feature("daily_checklists")),
    db: Session = Depends(get_db),
):
    try:
        entry = create_submission(db, user, checklist_type, payload.answers, payload.definition_version)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown checklist type")
    except ChecklistStaleVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ChecklistValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Only actually send once, for a genuinely new row - a replayed/duplicate
    # submission returns the same existing row (see create_submission) and
    # must not fire a second WhatsApp message for something already sent.
    # The public link's submit (routers/checklist_public.py) uses the same step.
    queue_whatsapp(background_tasks, entry, send_checklist_notification)
    task_sources_changed("checklist")  # its Team task closes now (Oct 15)
    return submission_out(entry, db)


@router.get("")
def get_submissions(
    checklist_type: Optional[str] = Query(None),
    employee_user_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, pattern="^(all_clear|issues)$"),
    start_date: Optional[dt.date] = Query(None),
    end_date: Optional[dt.date] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    include_demo: bool = Query(False, description="Admins only: also list App Review demo submissions."),
    user: User = Depends(require_feature("daily_checklists")),
    db: Session = Depends(get_db),
):
    """Admin: every submission, with optional filters. Non-admin: ONLY
    their own, regardless of any `employee_user_id` passed - enforced in
    list_submissions() itself, not just by what the UI happens to show.
    """
    entries = list_submissions(
        db,
        user,
        checklist_type=checklist_type,
        employee_user_id=employee_user_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        include_demo=include_demo,
    )
    return {"count": len(entries), "items": [submission_out(e, db) for e in entries]}


@router.get("/{submission_id}")
def get_submission(
    submission_id: int,
    user: User = Depends(require_feature("daily_checklists")),
    db: Session = Depends(get_db),
):
    entry = get_submission_for_user(db, user, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission_out(entry, db)
