"""Phase 2 endpoints (design §6): announcements, chat (team channels + DMs),
reports/moderation and DM blocks. Pushes are sent after the response in a
background task with its own session, so a slow Firebase call never delays
a send.

Access: announcements - any logged-in user for what they received, admins
for the rest. Chat, reports and blocks - the `team_chat` feature (admins
always have it). Moderation - admins only.
"""

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import SessionLocal, get_db
from app.features import require_feature
from app.models import User
from app.team import announcements_service as ann
from app.team import chat_service as chat
from app.team import moderation as mod
from app.team.jobs import register_team_job, with_session

router = APIRouter(prefix="/api/team", tags=["team-chat"])


def _chat_user(user: User = Depends(require_feature("team_chat"))) -> User:
    chat.require_chat_enabled()
    return user


def _ann_user(user: User = Depends(get_current_user)) -> User:
    ann.require_enabled()
    return user


def _ann_admin(user: User = Depends(require_admin)) -> User:
    ann.require_enabled()
    return user


def _in_own_session(fn, *args) -> None:
    db = SessionLocal()
    try:
        fn(db, *args)
    except Exception:  # noqa: BLE001 - a push failure must never surface
        import logging

        logging.getLogger(__name__).exception("Team background push failed")
    finally:
        db.close()


# --- Request bodies ----------------------------------------------------------------


class AnnouncementCreate(BaseModel):
    title: str
    body: str = ""
    team_ids: List[int]
    require_ack: bool = False
    pinned: bool = False
    media_token: Optional[str] = None
    # Optional: the same value on a retry returns the first announcement.
    client_uuid: Optional[str] = None


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    pinned: Optional[bool] = None
    deleted: Optional[bool] = None


class DmRequest(BaseModel):
    user_id: int


class MessageCreate(BaseModel):
    body: str = ""
    client_uuid: str
    media_token: Optional[str] = None


class ReadRequest(BaseModel):
    last_read_message_id: int


class ChannelMeUpdate(BaseModel):
    muted: Optional[bool] = None
    hidden: Optional[bool] = None


class DeleteMessageRequest(BaseModel):
    reason: Optional[str] = None


class ReportRequest(BaseModel):
    reason: str = ""
    # Additive (build 7): optional extra detail from the reporter.
    note: Optional[str] = None


class ResolveRequest(BaseModel):
    note: Optional[str] = None
    delete_message: bool = False


class DismissRequest(BaseModel):
    note: Optional[str] = None


class BlockRequest(BaseModel):
    user_id: int


class ChatSettings(BaseModel):
    hide_previews: bool


# --- Announcements -------------------------------------------------------------------


@router.get("/announcements")
def list_announcements(
    before_id: Optional[int] = Query(None),
    limit: int = Query(ann.PAGE_DEFAULT, ge=1, le=ann.PAGE_MAX),
    user: User = Depends(_ann_user),
    db: Session = Depends(get_db),
):
    return ann.list_for(db, user, before_id, limit)


@router.post("/announcements")
def create_announcement(
    body: AnnouncementCreate,
    background: BackgroundTasks,
    admin: User = Depends(_ann_admin),
    db: Session = Depends(get_db),
):
    a = ann.create(
        db, admin, body.title, body.body, body.team_ids, body.require_ack, body.pinned, body.media_token, body.client_uuid
    )
    if not getattr(a, "duplicate_of_retry", False):
        background.add_task(_in_own_session, ann.push_after_post, a.id)
    return ann.announcement_out(db, admin, a)


@router.get("/announcements/{announcement_id}")
def get_announcement(announcement_id: int, user: User = Depends(_ann_user), db: Session = Depends(get_db)):
    return ann.announcement_out(db, user, ann.get_for(db, user, announcement_id))


@router.patch("/announcements/{announcement_id}")
def update_announcement(
    announcement_id: int, body: AnnouncementUpdate, admin: User = Depends(_ann_admin), db: Session = Depends(get_db)
):
    a = ann.update(db, admin, announcement_id, body.title, body.body, body.pinned, body.deleted)
    return {"deleted": True} if a is None else ann.announcement_out(db, admin, a)


@router.post("/announcements/{announcement_id}/seen")
def seen_announcement(announcement_id: int, user: User = Depends(_ann_user), db: Session = Depends(get_db)):
    return ann.mark_seen(db, user, announcement_id, ack=False)


@router.post("/announcements/{announcement_id}/ack")
def ack_announcement(announcement_id: int, user: User = Depends(_ann_user), db: Session = Depends(get_db)):
    return ann.mark_seen(db, user, announcement_id, ack=True)


@router.get("/announcements/{announcement_id}/recipients")
def announcement_recipients(announcement_id: int, _: User = Depends(_ann_admin), db: Session = Depends(get_db)):
    return ann.recipients_out(db, announcement_id)


@router.post("/announcements/{announcement_id}/remind")
def remind_announcement(announcement_id: int, admin: User = Depends(_ann_admin), db: Session = Depends(get_db)):
    return ann.remind(db, admin, announcement_id)


# --- Chat -----------------------------------------------------------------------------


@router.get("/channels")
def list_channels(user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return {"channels": chat.list_channels(db, user)}


@router.get("/people")
def people(user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    """Who the caller can start a DM with."""
    return {"people": chat.people(db, user)}


@router.post("/channels/dm")
def open_dm(body: DmRequest, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.channel_out(db, user, chat.get_or_create_dm(db, user, body.user_id))


@router.get("/channels/{channel_id}")
def get_channel(channel_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.channel_out(db, user, chat.get_channel_for(db, user, channel_id, allow_admin=True))


@router.get("/channels/{channel_id}/members")
def channel_members(channel_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    """For the @mention picker."""
    channel = chat.get_channel_for(db, user, channel_id, allow_admin=True)
    return {"members": chat.channel_members_out(db, channel)}


@router.get("/channels/{channel_id}/messages")
def list_messages(
    channel_id: int,
    before_id: Optional[int] = Query(None),
    after_id: Optional[int] = Query(None),
    limit: int = Query(chat.PAGE_DEFAULT, ge=1, le=chat.PAGE_MAX),
    user: User = Depends(_chat_user),
    db: Session = Depends(get_db),
):
    channel = chat.get_channel_for(db, user, channel_id, allow_admin=True)
    return chat.list_messages(db, channel, before_id, after_id, limit)


@router.post("/channels/{channel_id}/messages")
def send_message(
    channel_id: int,
    body: MessageCreate,
    background: BackgroundTasks,
    user: User = Depends(_chat_user),
    db: Session = Depends(get_db),
):
    # Only members send - an admin reading a channel for moderation can't
    # post into it.
    channel = chat.get_channel_for(db, user, channel_id)
    msg, created = chat.send_message(db, user, channel, body.body, body.client_uuid, body.media_token)
    if created:
        background.add_task(_in_own_session, chat.push_new_message, msg.id)
    return {**chat.message_out(db, msg), "duplicate": not created}


@router.post("/channels/{channel_id}/read")
def mark_read(channel_id: int, body: ReadRequest, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.mark_read(db, user, chat.get_channel_for(db, user, channel_id), body.last_read_message_id)


@router.patch("/channels/{channel_id}/me")
def update_my_channel(
    channel_id: int, body: ChannelMeUpdate, user: User = Depends(_chat_user), db: Session = Depends(get_db)
):
    channel = chat.get_channel_for(db, user, channel_id)
    return chat.update_my_channel(db, user, channel, body.muted, body.hidden)


@router.delete("/messages/{message_id}")
def delete_message(
    message_id: int,
    body: Optional[DeleteMessageRequest] = None,
    user: User = Depends(_chat_user),
    db: Session = Depends(get_db),
):
    return chat.delete_message(db, user, message_id, body.reason if body else None)


@router.post("/messages/{message_id}/report")
def report_message(
    message_id: int,
    body: ReportRequest,
    background: BackgroundTasks,
    user: User = Depends(_chat_user),
    db: Session = Depends(get_db),
):
    out = mod.report_message(db, user, message_id, body.reason, body.note)
    background.add_task(_in_own_session, mod.notify_admins, out["id"])
    return out


# --- Pins, seen-by, search (W5) -------------------------------------------------------


@router.get("/channels/{channel_id}/pins")
def channel_pins(channel_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    channel = chat.get_channel_for(db, user, channel_id, allow_admin=True)
    member = chat.is_member(db, user, channel)
    return {
        "pins": chat.list_pins(db, channel),
        "can_pin": bool(member and chat.can_pin(user, channel) and not chat.channel_archived(db, channel)),
        "max_pins": chat.MAX_PINS,
    }


@router.post("/messages/{message_id}/pin")
def pin_message(message_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.set_pinned(db, user, message_id, True)


@router.delete("/messages/{message_id}/pin")
def unpin_message(message_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.set_pinned(db, user, message_id, False)


@router.get("/messages/{message_id}/seen-by")
def message_seen_by(message_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.seen_by(db, user, message_id)


@router.get("/messages/search")
def search_messages(
    q: str = Query(..., max_length=200),
    channel_id: Optional[int] = Query(None),
    before_id: Optional[int] = Query(None),
    limit: int = Query(chat.SEARCH_PAGE, ge=1, le=chat.SEARCH_PAGE_MAX),
    user: User = Depends(_chat_user),
    db: Session = Depends(get_db),
):
    return chat.search_messages(db, user, q, channel_id, before_id, limit)


@router.get("/chat/settings")
def get_chat_settings(user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return {"hide_previews": chat.hide_previews(db, user.id)}


@router.put("/chat/settings")
def put_chat_settings(body: ChatSettings, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.set_hide_previews(db, user, body.hide_previews)


# --- Moderation (admin) ----------------------------------------------------------------


@router.get("/reports")
def list_reports(
    status: Optional[str] = Query(None),
    before_id: Optional[int] = Query(None),
    limit: int = Query(mod.PAGE_DEFAULT, ge=1, le=mod.PAGE_MAX),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return mod.list_reports(db, status, before_id, limit)


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int, body: ResolveRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return mod.resolve(db, admin, report_id, body.note, body.delete_message)


@router.post("/reports/{report_id}/dismiss")
def dismiss_report(report_id: int, body: DismissRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return mod.dismiss(db, admin, report_id, body.note)


# --- DM blocks -------------------------------------------------------------------------


@router.get("/blocks")
def list_blocks(
    all: bool = Query(False, description="Admins: every block that exists (never why)."),
    user: User = Depends(_chat_user),
    db: Session = Depends(get_db),
):
    if all and user.is_admin:
        return {"blocks": chat.list_all_blocks(db)}
    return {"blocks": chat.list_blocks(db, user)}


@router.post("/blocks")
def block(body: BlockRequest, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    return chat.block_user(db, user, body.user_id)


@router.delete("/blocks/{user_id}")
def unblock(user_id: int, user: User = Depends(_chat_user), db: Session = Depends(get_db)):
    chat.unblock_user(db, user, user_id)
    return {"ok": True}


# --- Jobs ----------------------------------------------------------------------------------


def _register_jobs() -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    register_team_job(
        "team_chat_retention",
        with_session(chat.run_chat_retention),
        lambda tz: CronTrigger(hour=3, minute=45, timezone=tz),
        misfire_grace_time=3600,
    )
    register_team_job(
        "team_announcement_pending_pushes",
        with_session(ann.flush_pending_pushes),
        lambda tz: IntervalTrigger(minutes=5, timezone=tz),
    )


_register_jobs()
