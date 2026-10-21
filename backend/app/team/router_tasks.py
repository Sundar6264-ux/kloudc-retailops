"""Team hub task endpoints (design §6, Phase 1). Access rules live in
tasks_service; every endpoint first requires the `team_tasks` feature (and
the tasks kill switch)."""

import datetime as dt
from typing import Any, List, Optional

from multipart.exceptions import MultipartParseError
from starlette.datastructures import FormData
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
# request.form() hands back Starlette's UploadFile (FastAPI's is a subclass).
from starlette.datastructures import UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import User
from app.team import media as media_svc
from app.team import moderation as mod
from app.team import tasks_service as svc
from app.team.tasks_models import Task

router = APIRouter(prefix="/api/team", tags=["team-tasks"])


def tasks_user(user: User = Depends(get_current_user)) -> User:
    svc.require_tasks(user)
    return user


def tasks_admin(user: User = Depends(tasks_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    return user


class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    assignee_user_ids: Optional[List[int]] = None
    assignee_team_id: Optional[int] = None
    # With an offset/Z = that instant; without = store-local wall time.
    due_at: Optional[dt.datetime] = None
    requires: str = "none"
    priority: str = "normal"
    items: Optional[List[str]] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[dt.datetime] = None
    priority: Optional[str] = None
    requires: Optional[str] = None
    assignee_user_id: Optional[int] = None
    assignee_team_id: Optional[int] = None
    cancelled: Optional[bool] = None
    # Oct 15: the task's `updated_at` as the client last saw it; if someone
    # changed it since, 409 {"detail", "task"} instead of overwriting.
    expected_updated_at: Optional[dt.datetime] = None


class CantDoRequest(BaseModel):
    reason: str


class CommentRequest(BaseModel):
    body: str


class ItemRequest(BaseModel):
    # Builds 5-9 send {"done": bool}. Oct 15: or {"text"} to rename a step
    # (whoever can edit the task), or both.
    done: Optional[bool] = None
    text: Optional[str] = None


class ItemCreateRequest(BaseModel):
    text: str


class ItemsReplaceRequest(BaseModel):
    """The whole step list, in order: [{"id"?: int, "text": str}]."""

    items: List[Any]
    expected_updated_at: Optional[dt.datetime] = None


class BulkRequest(BaseModel):
    task_ids: List[Any]
    action: str  # done | cancel
    note: Optional[str] = None


class ReportRequest(BaseModel):
    """Same shape (and reason values) as the chat message report."""

    reason: str = ""
    note: Optional[str] = None


class TemplateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    requires: Optional[str] = None
    priority: Optional[str] = None
    assignee_user_id: Optional[int] = None
    assignee_team_id: Optional[int] = None
    rrule: Optional[str] = None
    timezone: Optional[str] = None
    generate_ahead_minutes: Optional[int] = None
    active: Optional[bool] = None
    # W5: steps, weekday assignees ({"MO": {"user_id": 3}} or a list of
    # {"day", "user_id"|"team_id"}; {} clears), and repeats=false for a
    # template only used by "Create task now".
    items: Optional[List[str]] = None
    day_assignees: Optional[Any] = None
    repeats: Optional[bool] = None


class TemplateUseRequest(BaseModel):
    assignee_user_ids: Optional[List[int]] = None
    assignee_team_id: Optional[int] = None
    due_at: Optional[dt.datetime] = None
    priority: Optional[str] = None


class StartRequest(BaseModel):
    started: bool = True


class PreviewRequest(BaseModel):
    rrule: str
    timezone: Optional[str] = None
    generate_ahead_minutes: int = 420
    # Editing a saved schedule: preview it anchored like its generation.
    template_id: Optional[int] = None


# --- Tasks -----------------------------------------------------------------------


def _conflict_aware(db: Session, user: User, fn, *args, **kwargs):
    """Runs a task write; a 409 from a conditional write (svc.TaskConflict)
    also carries the task as it is now: {"detail": ..., "task": {...}} -
    "detail" stays the same string older clients show."""
    try:
        return fn(db, user, *args, **kwargs)
    except svc.TaskConflict as e:
        body = {"detail": e.detail}
        try:
            db.rollback()
            task = db.get(Task, e.task_id)
            if task is not None and svc.can_read(db, user, task):
                body["task"] = svc.task_out(db, task, user, detail=True)
        except Exception:  # noqa: BLE001 - the 409 itself must still go out
            db.rollback()
        return JSONResponse(status_code=409, content=jsonable_encoder(body))


@router.get("/tasks")
def list_tasks(
    view: str = Query("mine"),
    status: str = Query("open"),
    before_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=200),
    overdue: bool = Query(False, description="W5: only open tasks past their due time"),
    assignee_user_id: Optional[int] = Query(None, description="Oct 15: one person's own tasks"),
    source: Optional[str] = Query(None, description="Oct 15: manual | auto | a source_type"),
    q: Optional[str] = Query(None, max_length=200, description="Oct 15: words in the title"),
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    return svc.list_tasks(
        db, user, view, status, before_id, limit, overdue=overdue,
        assignee_user_id=assignee_user_id, source=source, q=q,
    )


@router.get("/task-summary")
def task_summary(user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    """Oct 15 "My team today": per person / team open, overdue, due today
    and done today (admins: everyone; assigners: their own teams). Real
    staff's work, so never for the App Review demo login."""
    from app.demo import is_demo_user

    if is_demo_user(user):
        from app.demo_guard import demo_read_only_error

        raise demo_read_only_error("Not available for the demo account.")
    return svc.team_today(db, user)


@router.post("/tasks/bulk")
def bulk(body: BulkRequest, user: User = Depends(tasks_admin), db: Session = Depends(get_db)):
    """Oct 15, admins: {"task_ids": [...], "action": "done" | "cancel",
    "note"?}. Each task is closed only if it's still open."""
    return svc.bulk_action(db, user, body.task_ids, body.action, body.note)


@router.get("/task-stats")
def task_stats(days: int = Query(30), user: User = Depends(tasks_admin), db: Session = Depends(get_db)):
    """W5: per-person / per-team completion (admins). Real staff's
    performance, so never for the App Review demo login (admin or not)."""
    from app.demo import is_demo_user

    if is_demo_user(user):
        from app.demo_guard import demo_read_only_error

        raise demo_read_only_error("Not available for the demo account.")
    return svc.task_stats(db, days)


@router.get("/assignees")
def assignees(user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    """People the caller may assign a task to: everyone with Tasks access
    for admins and can_assign users, just themselves otherwise (they can
    only make personal to-dos). Separate from chat's /people, which needs
    team_chat and hides blocked users - blocking a DM must never stop a
    manager assigning work."""
    return {"people": svc.assignable_people(db, user)}


@router.post("/tasks")
def create_task(
    body: TaskCreateRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    # The "New task" pushes go out after the response (Oct 15).
    return svc.create_tasks(db, user, **body.model_dump(), background=background)


@router.get("/tasks/{task_id}")
def get_task(task_id: int, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    return svc.task_out(db, svc.get_task_for(db, user, task_id), user, detail=True)


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: int,
    body: TaskUpdateRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    # Only the fields actually sent - so {"due_at": null} clears the due date.
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    return _conflict_aware(db, user, svc.update_task, task_id, changes, background=background)


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    """Multipart: note?, photo? (file), photo_token? - or no body at all.
    No File(...)/Form(...) parameters on purpose: FastAPI would parse (and
    spool to disk) the whole multipart body before any check runs, so an
    oversized photo is refused from its Content-Length first (same as POST
    /api/team/media), then the form is read."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/"):
        media_svc.check_request_size(request)
    try:
        form = await request.form(max_files=1, max_fields=5)
    except MultipartParseError:
        # Real bug (Oct 1): the app sends an EMPTY multipart form for a task
        # that needs no note/photo, and Dio writes it without the trailing
        # CRLF python-multipart insists on -> 500 "request failed". An
        # unreadable form here can only be an empty one (a real note/photo
        # parses fine), so treat it as "no body".
        form = FormData()
    try:
        note = form.get("note")
        photo = form.get("photo")
        photo_token = form.get("photo_token")
        return await run_in_threadpool(
            _conflict_aware,
            db,
            user,
            svc.complete_task,
            task_id,
            note=note if isinstance(note, str) else None,
            photo=photo if isinstance(photo, UploadFile) else None,
            photo_token=photo_token if isinstance(photo_token, str) and photo_token else None,
            background=background,
        )
    finally:
        await form.close()


@router.post("/tasks/{task_id}/cant-do")
def cant_do(
    task_id: int,
    body: CantDoRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    return _conflict_aware(db, user, svc.cant_do_task, task_id, body.reason, background=background)


@router.post("/tasks/{task_id}/start")
def start(task_id: int, body: Optional[StartRequest] = None, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    """W5 board: {"started": true} = In progress, false = back to To do."""
    return _conflict_aware(db, user, svc.start_task, task_id, True if body is None else body.started)


@router.post("/tasks/{task_id}/reopen")
def reopen(task_id: int, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    return _conflict_aware(db, user, svc.reopen_task, task_id)


@router.post("/tasks/{task_id}/comments")
def comment(
    task_id: int,
    body: CommentRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    return svc.comment_task(db, user, task_id, body.body, background=background)


@router.post("/tasks/{task_id}/report")
def report_task(
    task_id: int,
    body: ReportRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    """Report a task's content (title, description, proof photo) to the
    admins' moderation queue - the same queue as chat reports."""
    out = mod.report_task(db, user, task_id, body.reason, body.note)
    background.add_task(_notify_admins, out["id"])
    return out


@router.post("/tasks/{task_id}/comments/{comment_id}/report")
def report_task_comment(
    task_id: int,
    comment_id: int,
    body: ReportRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    """`comment_id` is the comment's id in the task's events."""
    out = mod.report_task_comment(db, user, task_id, comment_id, body.reason, body.note)
    background.add_task(_notify_admins, out["id"])
    return out


def _notify_admins(report_id: int) -> None:
    # Own session, after the response - a push problem never surfaces.
    from app.team.router_chat import _in_own_session

    _in_own_session(mod.notify_admins, report_id)


@router.patch("/tasks/{task_id}/items/{item_id}")
def set_item(
    task_id: int,
    item_id: int,
    body: ItemRequest,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    return _conflict_aware(db, user, svc.set_item, task_id, item_id, done=body.done, text=body.text)


@router.post("/tasks/{task_id}/items")
def add_item(
    task_id: int, body: ItemCreateRequest, user: User = Depends(tasks_user), db: Session = Depends(get_db)
):
    """Oct 15: add a step to an open task (its creator or an admin)."""
    return _conflict_aware(db, user, svc.add_item, task_id, body.text)


@router.put("/tasks/{task_id}/items")
def replace_items(
    task_id: int, body: ItemsReplaceRequest, user: User = Depends(tasks_user), db: Session = Depends(get_db)
):
    """Oct 15: the whole step list at once - add, rename, reorder, remove."""
    return _conflict_aware(db, user, svc.replace_items, task_id, body.items, body.expected_updated_at)


@router.delete("/tasks/{task_id}/items/{item_id}")
def delete_item(task_id: int, item_id: int, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    """Oct 15: remove a step from an open task (its creator or an admin)."""
    return _conflict_aware(db, user, svc.delete_item, task_id, item_id)


# --- Batches ---------------------------------------------------------------------


@router.get("/task-batches/{batch_id}")
def get_batch(batch_id: str, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    return svc.get_batch(db, user, batch_id)


@router.post("/task-batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    return svc.cancel_batch(db, user, batch_id)


# --- Recurring templates -------------------------------------------------------------


@router.get("/templates")
def list_templates(user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    return svc.list_templates(db, user)


@router.get("/templates/{template_id}")
def get_template(template_id: int, user: User = Depends(tasks_user), db: Session = Depends(get_db)):
    """Oct 15: one schedule (same shape as a list entry)."""
    return svc.get_template(db, user, template_id)


@router.post("/templates/preview")
def preview_template(body: PreviewRequest, _: User = Depends(tasks_admin), db: Session = Depends(get_db)):
    return svc.preview_template(db, body.rrule, body.timezone, body.generate_ahead_minutes, body.template_id)


@router.post("/templates")
def create_template(body: TemplateRequest, user: User = Depends(tasks_admin), db: Session = Depends(get_db)):
    return svc.create_template(db, user, body.model_dump(exclude_unset=True))


@router.post("/templates/{template_id}/create-task")
def create_from_template(
    template_id: int,
    body: TemplateUseRequest,
    background: BackgroundTasks,
    user: User = Depends(tasks_user),
    db: Session = Depends(get_db),
):
    """W5: "Create task now" from a template (assigners; see list_templates)."""
    if not svc.is_assigner(user):
        raise HTTPException(status_code=403, detail="Only people who can assign tasks can use templates")
    return svc.create_task_from_template(db, user, template_id, body.model_dump(), background=background)


@router.patch("/templates/{template_id}")
def update_template(
    template_id: int, body: TemplateRequest, user: User = Depends(tasks_admin), db: Session = Depends(get_db)
):
    return svc.update_template(db, user, template_id, body.model_dump(exclude_unset=True))


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, user: User = Depends(tasks_admin), db: Session = Depends(get_db)):
    return svc.deactivate_template(db, user, template_id)
