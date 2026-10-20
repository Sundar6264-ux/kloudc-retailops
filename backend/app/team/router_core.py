"""Team hub core endpoints (design §6): bootstrap, the change feed, teams
admin and media. Plus GET /api/app/config, which needs no login - the
"Update required" gate must work before anyone signs in.
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import SessionLocal, get_db
from app.features import user_has_feature
from app.models import User
from app.team import bootstrap as svc
from app.team import media as media_svc
from app.team import rules as rules_svc
from app.team import changes as changes_svc
from app.team.changes import get_updates

router = APIRouter(prefix="/api/team", tags=["team"])
app_router = APIRouter(prefix="/api/app", tags=["app"])


class TeamCreateRequest(BaseModel):
    name: str
    color: Optional[str] = None


class TeamUpdateRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    archived: Optional[bool] = None


class TeamMembersRequest(BaseModel):
    user_ids: List[int]


class RulesAcceptRequest(BaseModel):
    version: int


@app_router.get("/config")
def app_config():
    return svc.app_config()


@router.get("/bootstrap")
def bootstrap(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.bootstrap(db, user)


@router.get("/updates")
async def updates(
    since: int = Query(0, ge=0),
    wait: int = Query(0, ge=0, description="Oct 15: long-poll up to this many seconds (capped at 20)"),
    include: Optional[str] = Query(None, description="Oct 15: 'tasks' adds each changed task the caller can see"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Without `wait` (every app build 5-9, the website): answers at once,
    exactly as before. With `wait`: answers as soon as something the caller
    can see changes, else after `wait` seconds with no changes - the same
    response shape either way. While waiting it holds no thread and no
    database connection (each check opens its own short session)."""
    with_tasks = (include or "").strip().lower() == "tasks"
    wait = min(wait, changes_svc.MAX_WAIT_S)
    if wait <= 0:
        return await run_in_threadpool(_updates_payload, db, user, since, with_tasks)
    user_id = user.id
    # Done with the request's session: nothing may sit open while waiting.
    await run_in_threadpool(db.close)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while True:
        version = changes_svc.change_version()
        out = await run_in_threadpool(_updates_fresh, user_id, since, with_tasks)
        remaining = deadline - loop.time()
        if out is None:
            raise HTTPException(status_code=401, detail="Session expired or invalid")
        if out.get("resync_required") or out.get("changes") or remaining <= 0:
            return out
        await changes_svc.wait_for_change(version, remaining)


def _updates_payload(db: Session, user: User, since: int, with_tasks: bool) -> dict:
    out = get_updates(db, user, since)
    if with_tasks:
        from app.team.tasks_service import tasks_for_changes

        out["tasks"] = tasks_for_changes(db, user, out.get("changes", {}).get("task", []))
    return out


def _updates_fresh(user_id: int, since: int, with_tasks: bool) -> Optional[dict]:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            return None
        return _updates_payload(db, user, since, with_tasks)
    finally:
        db.close()


# --- Team rules (the in-app EULA; contract §3) ---------------------------------------
# Any logged-in user. Never enforced server-side: the build 7 app gates the
# Team tab on it, while build 5/6 apps (which don't know about it) keep working.


@router.get("/rules")
def get_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return rules_svc.rules_out(db, user)


@router.post("/rules/accept")
def accept_rules(body: RulesAcceptRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return rules_svc.accept(db, user, body.version)


@router.get("/teams")
def list_teams(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.list_teams(db)


@router.post("/teams")
def create_team(body: TeamCreateRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.create_team(db, body.name, body.color)


@router.patch("/teams/{team_id}")
def update_team(
    team_id: int, body: TeamUpdateRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)
):
    return svc.update_team(db, team_id, body.name, body.color, body.archived)


@router.put("/teams/{team_id}/members")
def set_members(
    team_id: int, body: TeamMembersRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)
):
    return svc.set_members(db, team_id, body.user_ids)


def _uploader(user: User = Depends(get_current_user)) -> User:
    """Photos are for tasks, chat and announcements - so only people who can
    use one of those (admins always) may upload."""
    if not (user.is_admin or user_has_feature(user, "team_tasks") or user_has_feature(user, "team_chat")):
        raise HTTPException(status_code=403, detail="You don't have access to Team")
    return user


@router.post("/media")
async def upload_media(
    request: Request,
    user: User = Depends(_uploader),
    db: Session = Depends(get_db),
):
    # No File(...) parameter on purpose: FastAPI would parse (and spool to
    # disk) the whole multipart body before this function runs. The size is
    # checked from Content-Length first, then the form is read.
    media_svc.check_request_size(request)
    form = await request.form(max_files=1, max_fields=5)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="Attach the photo as 'file'.")
        # W5: voice notes and PDFs are for chat only - anyone else's upload
        # of one is checked (and refused) as an image, as before.
        kinds = (media_svc.KIND_IMAGE,)
        if user_has_feature(user, "team_chat"):
            kinds = media_svc.KINDS
        try:
            duration_ms = int(form.get("duration_ms") or 0) or None
        except (TypeError, ValueError):
            duration_ms = None
        # Decoding/re-encoding is CPU-bound - keep it off the event loop.
        from starlette.concurrency import run_in_threadpool

        media = await run_in_threadpool(
            lambda: media_svc.save_upload(db, user, upload, allowed_kinds=kinds, duration_ms=duration_ms)
        )
    finally:
        await form.close()
    return media_svc.media_out(media)


@router.get("/media/{token}")
def get_media(token: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    media = media_svc.media_for_token(db, token)
    # 404 - not 403 - for anything the caller can't see, so a guessed token
    # reveals nothing.
    if media is None or not media_svc.can_access(db, user, media):
        raise HTTPException(status_code=404, detail="Not found")
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if (media.kind or "image") == media_svc.KIND_DOCUMENT:
        from urllib.parse import quote

        name = media.display_name or "document.pdf"
        headers["Content-Disposition"] = f"inline; filename=\"document.pdf\"; filename*=UTF-8''{quote(name)}"
    # The local file or the store's object (STORAGE_BACKEND=s3), same answer.
    response = media_svc.media_response(media, headers)
    if response is None:
        raise HTTPException(status_code=404, detail="Not found")
    return response
