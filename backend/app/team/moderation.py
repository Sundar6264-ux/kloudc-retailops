"""Reporting and moderation (design §4.5, §10.1): anyone in a channel can
report a message, and anyone who can see a task can report it or one of its
comments (build 7); admins see one queue and resolve (optionally removing
the content) or dismiss each report. Admins see the reported text even after
the message was deleted, until the retention job blanks it (30 days).
"""

import json
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.demo import is_demo_user
from app.models import User
from app.team import media as media_svc
from app.team import tasks_service as tasks_svc
from app.team.changes import teammate_user_ids
from app.team.chat_models import Channel, Message, MessageReport
from app.team.chat_service import attachment_label, get_message_for, push_to_users, soft_delete_message
from app.team.models import Team, TeamMedia
from app.team.tasks_models import Task, TaskEvent
from app.timeutil import utcnow

REPORT_STATUSES = ("open", "resolved", "dismissed")
MAX_REASON = 300
MAX_NOTE = 1000
PAGE_DEFAULT = 50
PAGE_MAX = 100


TARGET_TYPES = ("message", "task", "task_comment")
OWN_CONTENT = {
    "message": "You can't report your own message.",
    "task": "You can't report your own task.",
    "task_comment": "You can't report your own comment.",
}
REMOVED_TASK_TITLE = "Removed by a manager"
REMOVED_COMMENT_BODY = "Comment removed by a manager"


def _find(db: Session, target_type: str, target_id: int, reporter_id: int) -> Optional[MessageReport]:
    return db.execute(
        select(MessageReport).where(
            MessageReport.target_type == target_type,
            MessageReport.target_id == target_id,
            MessageReport.reporter_user_id == reporter_id,
        )
    ).scalars().first()


def _create(
    db: Session,
    user: User,
    target_type: str,
    target_id: int,
    reason: str,
    note: Optional[str],
    *,
    message_id: Optional[int] = None,
    snapshot: Optional[dict] = None,
) -> dict:
    """Reporting the same thing twice returns the first report."""
    existing = _find(db, target_type, target_id, user.id)
    if existing is None and message_id is not None:
        # A report made before migration 0043 backfilled target_id.
        existing = db.execute(
            select(MessageReport).where(
                MessageReport.message_id == message_id, MessageReport.reporter_user_id == user.id
            )
        ).scalars().first()
    if existing is not None:
        return {"id": existing.id, "status": existing.status}
    report = MessageReport(
        message_id=message_id,
        target_type=target_type,
        target_id=target_id,
        reporter_user_id=user.id,
        reporter_name=user.username,
        reason=(reason or "").strip()[:MAX_REASON],
        note=(note or "").strip()[:MAX_NOTE] or None,
        snapshot_json=json.dumps(snapshot) if snapshot is not None else None,
        created_at=utcnow(),
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        # A double-tapped Report racing itself - return the one that won.
        db.rollback()
        report = _find(db, target_type, target_id, user.id)
        return {"id": report.id, "status": report.status}
    db.refresh(report)
    return {"id": report.id, "status": report.status}


def report_message(db: Session, user: User, message_id: int, reason: str, note: Optional[str] = None) -> dict:
    msg, _ = get_message_for(db, user, message_id, allow_admin=False)
    if msg.sender_user_id == user.id:
        raise HTTPException(status_code=400, detail=OWN_CONTENT["message"])
    return _create(db, user, "message", msg.id, reason, note, message_id=msg.id)


def _task_snapshot(task: Task) -> dict:
    return {
        "title": task.title,
        "description": task.description,
        "completion_note": task.completion_note,
        "cant_do_reason": task.cant_do_reason,
        "completion_media_id": task.completion_media_id,
    }


def report_task(db: Session, user: User, task_id: int, reason: str, note: Optional[str] = None) -> dict:
    """A task's content: its title, description and proof photo. 404 for a
    task the reporter can't see (same as GET), 400 for their own."""
    task = tasks_svc.get_task_for(db, user, task_id)
    if task.created_by_user_id == user.id:
        raise HTTPException(status_code=400, detail=OWN_CONTENT["task"])
    return _create(db, user, "task", task.id, reason, note, snapshot=_task_snapshot(task))


def _comment_for(db: Session, task: Task, comment_id: int) -> TaskEvent:
    event = db.get(TaskEvent, comment_id)
    if event is None or event.task_id != task.id or event.kind != "commented":
        raise HTTPException(status_code=404, detail="Comment not found")
    return event


def _comment_detail(event: TaskEvent) -> dict:
    return json.loads(event.detail_json) if event.detail_json else {}


def report_task_comment(
    db: Session, user: User, task_id: int, comment_id: int, reason: str, note: Optional[str] = None
) -> dict:
    task = tasks_svc.get_task_for(db, user, task_id)
    event = _comment_for(db, task, comment_id)
    if event.user_id == user.id:
        raise HTTPException(status_code=400, detail=OWN_CONTENT["task_comment"])
    snapshot = {"task_id": task.id, "task_title": task.title, "body": _comment_detail(event).get("body", "")}
    return _create(db, user, "task_comment", event.id, reason, note, snapshot=snapshot)


_PUSH_TEXT = {
    "message": ("Message reported", "A chat message was reported. Open Reports to review it."),
    "task": ("Task reported", "A task was reported. Open Reports to review it."),
    "task_comment": ("Comment reported", "A task comment was reported. Open Reports to review it."),
}


def notify_admins(db: Session, report_id: int) -> None:
    """Background task after a report: a push to every admin (moderation
    must be timely - App Review 1.2). Never names the reporter. A demo
    login's report only reaches admins in its own teams."""
    report = db.get(MessageReport, report_id)
    if report is None:
        return
    admins = {
        u.id
        for u in db.execute(select(User).where(User.is_admin.is_(True))).scalars().all()
        if u.id != report.reporter_user_id
    }
    reporter = db.get(User, report.reporter_user_id)
    if is_demo_user(reporter):
        admins &= teammate_user_ids(db, reporter.id)
    title, body = _PUSH_TEXT.get(report.target_type or "message", _PUSH_TEXT["message"])
    push_to_users(
        db,
        "chat_report",
        admins,
        title,
        body,
        {"route": "moderation", "report_id": report.id},
        dedupe_key=f"chat_report:{report.id}",
        respect_prefs=False,
    )


def _channel_name(db: Session, channel: Optional[Channel]) -> str:
    if channel is None:
        return ""
    if channel.kind == "team":
        team = db.get(Team, channel.team_id)
        return team.name if team else "Team"
    names = []
    for uid in (int(x) for x in (channel.dm_key or "").split(":") if x):
        u = db.get(User, uid)
        names.append(u.username if u else "Deleted user")
    return "DM: " + " & ".join(names)


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _person(user_id: Optional[int], name: Optional[str]) -> dict:
    return {"id": user_id, "name": name or "Unknown"}


def _message_out(db: Session, msg: Message) -> dict:
    channel = db.get(Channel, msg.channel_id)
    media = db.get(TeamMedia, msg.media_id) if msg.media_id else None
    is_image = media is not None and (media.kind or media_svc.KIND_IMAGE) == media_svc.KIND_IMAGE
    body = msg.body
    body_is_label = False
    if media is not None and not is_image and not (body or "").strip():
        # Older admin apps only draw photos: say what was sent instead.
        body, body_is_label = attachment_label(media), True
    return {
        "id": msg.id,
        "channel_id": msg.channel_id,
        "channel_name": _channel_name(db, channel),
        "sender": {"id": msg.sender_user_id, "name": msg.sender_name},
        # The original text, even if deleted (until retention blanks it).
        "body": body,
        "media": media_svc.media_out(media) if is_image else None,
        # Additive (W5): a reported voice note or PDF, for admins to play/open.
        "attachment": media_svc.media_out(media) if media else None,
        "body_is_label": body_is_label,
        "created_at": msg.created_at.isoformat(),
        "deleted": msg.deleted_at is not None,
        "delete_reason": msg.delete_reason,
        # Additive (build 7): the content filter masked a word in it.
        "filtered": bool(msg.filtered),
    }


def _task_out(db: Session, task: Task) -> dict:
    photo = db.get(TeamMedia, task.completion_media_id) if task.completion_media_id else None
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "created_by": _person(task.created_by_user_id, task.created_by_name),
        "completed_by": (
            _person(task.completed_by_user_id, task.completed_by_name) if task.completed_by_name else None
        ),
        "completion_note": task.completion_note,
        "completion_photo": media_svc.media_out(photo) if photo else None,
        "cant_do_reason": task.cant_do_reason,
        "created_at": _iso(task.created_at),
    }


def _legacy_message(
    *, channel_name: str, sender: dict, body: str, media: Optional[dict], created_at, deleted: bool
) -> dict:
    """A task / comment report shaped like the chat-message block, so build
    5/6 admin apps (which only read `message`) still show what was reported."""
    return {
        "id": None,
        "channel_id": None,
        "channel_name": channel_name,
        "sender": sender,
        "body": body,
        "media": media,
        "created_at": _iso(created_at),
        "deleted": deleted,
        "delete_reason": None,
        "filtered": False,
    }


def report_out(db: Session, r: MessageReport) -> dict:
    target_type = r.target_type or "message"
    snapshot = json.loads(r.snapshot_json) if r.snapshot_json else {}
    out = {
        "id": r.id,
        "status": r.status,
        "reason": r.reason,
        # Additive (build 7).
        "note": r.note,
        "target_type": target_type,
        "target_id": r.target_id if r.target_id is not None else r.message_id,
        "created_at": r.created_at.isoformat(),
        "reporter": {"id": r.reporter_user_id, "name": r.reporter_name},
        "resolved_by": (
            {"id": r.resolved_by_user_id, "name": r.resolved_by_name} if r.resolved_by_user_id else None
        ),
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "resolution_note": r.resolution_note,
        "message": None,
        "task": None,
        "comment": None,
    }
    if target_type == "message":
        msg = db.get(Message, r.message_id) if r.message_id is not None else None
        out["message"] = _message_out(db, msg) if msg else None
        return out

    if target_type == "task":
        task = db.get(Task, r.target_id)
        if task is None:
            return out
        task_block = _task_out(db, task)
        task_block["reported"] = snapshot or None
        out["task"] = task_block
        body = "\n\n".join(x for x in (snapshot.get("title") or task.title, snapshot.get("description") or task.description) if x)
        out["message"] = _legacy_message(
            channel_name=f"Task: {task.title}",
            sender=_person(task.created_by_user_id, task.created_by_name),
            body=body,
            media=task_block["completion_photo"],
            created_at=task.created_at,
            deleted=task.title == REMOVED_TASK_TITLE,
        )
        return out

    # task_comment
    event = db.get(TaskEvent, r.target_id)
    if event is None:
        return out
    detail = _comment_detail(event)
    removed = bool(detail.get("removed"))
    body = snapshot.get("body") if removed or "body" not in detail else detail.get("body")
    task = db.get(Task, event.task_id)
    out["comment"] = {
        "id": event.id,
        "task_id": event.task_id,
        "task_title": task.title if task else snapshot.get("task_title"),
        "author": _person(event.user_id, event.user_name),
        # What was reported, even after a manager removed it.
        "body": body,
        "removed": removed,
        "created_at": _iso(event.created_at),
    }
    out["task"] = _task_out(db, task) if task else None
    out["message"] = _legacy_message(
        channel_name=f"Comment on task: {out['comment']['task_title'] or ''}",
        sender=out["comment"]["author"],
        body=body or "",
        media=None,
        created_at=event.created_at,
        deleted=removed,
    )
    return out


def list_reports(db: Session, status: Optional[str], before_id: Optional[int], limit: int) -> dict:
    if status is not None and status not in REPORT_STATUSES:
        raise HTTPException(status_code=400, detail="status must be open, resolved or dismissed")
    limit = max(1, min(limit or PAGE_DEFAULT, PAGE_MAX))
    stmt = select(MessageReport)
    if status:
        stmt = stmt.where(MessageReport.status == status)
    if before_id is not None:
        stmt = stmt.where(MessageReport.id < before_id)
    rows = db.execute(stmt.order_by(MessageReport.id.desc()).limit(limit + 1)).scalars().all()
    return {"reports": [report_out(db, r) for r in rows[:limit]], "has_more": len(rows) > limit}


def _get(db: Session, report_id: int) -> MessageReport:
    r = db.get(MessageReport, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    return r


def _same_target_open(db: Session, r: MessageReport):
    return db.execute(
        select(MessageReport).where(
            MessageReport.target_type == r.target_type,
            MessageReport.target_id == r.target_id,
            MessageReport.status == "open",
            MessageReport.id != r.id,
        )
    ).scalars().all()


def _remove_task_content(db: Session, admin: User, task: Task, note: Optional[str]) -> None:
    """A reported task's content is taken down: the title is replaced, the
    description, notes and proof photo are removed, and an open task is
    cancelled. The original text stays in the report's snapshot for admins."""
    if task.completion_media_id:
        media = db.get(TeamMedia, task.completion_media_id)
        task.completion_media_id = None
        if media is not None:
            media_svc.delete_media_file(media)
            db.delete(media)
    task.title = REMOVED_TASK_TITLE
    task.description = None
    task.completion_note = None
    if task.cant_do_reason:
        task.cant_do_reason = REMOVED_TASK_TITLE
    if task.status == "open":
        task.status = "cancelled"
    task.updated_at = utcnow()
    tasks_svc.add_event(db, task, admin, "moderated", {"reason": (note or "").strip()[:MAX_REASON] or None})
    tasks_svc.record_task_change(db, task, "updated")


def _remove_comment(db: Session, admin: User, event: TaskEvent) -> None:
    detail = _comment_detail(event)
    if detail.get("removed"):
        return
    # The text older apps show in place of the comment; the original stays
    # in the report's snapshot for admins.
    event.detail_json = json.dumps({"body": REMOVED_COMMENT_BODY, "removed": True, "removed_by": admin.username})
    task = db.get(Task, event.task_id)
    if task is not None:
        task.updated_at = utcnow()
        tasks_svc.record_task_change(db, task, "updated")


def resolve(db: Session, admin: User, report_id: int, note: Optional[str], delete_message: bool) -> dict:
    """`delete_message` (the existing name, kept for build 5/6 apps) removes
    the reported content: the message, the task's content, or the comment."""
    r = _get(db, report_id)
    target_type = r.target_type or "message"
    if delete_message:
        if target_type == "message":
            msg = db.get(Message, r.message_id) if r.message_id is not None else None
            if msg is not None:
                soft_delete_message(db, admin, msg, (note or "").strip() or "Removed by a manager")
        elif target_type == "task":
            task = db.get(Task, r.target_id)
            if task is not None:
                _remove_task_content(db, admin, task, note)
        elif target_type == "task_comment":
            event = db.get(TaskEvent, r.target_id)
            if event is not None:
                _remove_comment(db, admin, event)
    _close(r, admin, "resolved", note)
    # Other open reports of the same content are settled by the same decision.
    if delete_message:
        for other in _same_target_open(db, r):
            _close(other, admin, "resolved", note)
    db.commit()
    return report_out(db, r)


def dismiss(db: Session, admin: User, report_id: int, note: Optional[str]) -> dict:
    r = _get(db, report_id)
    _close(r, admin, "dismissed", note)
    db.commit()
    return report_out(db, r)


def _close(r: MessageReport, admin: User, status: str, note: Optional[str]) -> None:
    r.status = status
    r.resolved_by_user_id = admin.id
    r.resolved_by_name = admin.username
    r.resolved_at = utcnow()
    r.resolution_note = (note or "").strip()[:MAX_REASON] or None
