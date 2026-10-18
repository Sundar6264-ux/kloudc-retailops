"""Announcements (design §3, §4.3, Phase 2): an admin posts to one or more
teams; the people in those teams *at that moment* become its recipients
(announcement_recipients), and every count ("7 of 9 acknowledged") comes
only from those rows - joining or leaving a team later changes nothing.

Everyone can receive announcements (no feature needed); only admins post,
edit, remind and see who has read what.
"""

import datetime as dt
import logging
from typing import List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DeviceToken, User
from app.services import push
from app.team import content_filter
from app.team import media as media_svc
from app.team.changes import record_change, register_badge_provider, team_member_user_ids
from app.team.chat_models import Announcement, AnnouncementAudience, AnnouncementRecipient
from app.team.chat_service import in_quiet_hours, push_to_users
from app.team.models import Team, TeamMedia
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

MAX_TITLE = 120
MAX_BODY = 4000
PAGE_DEFAULT = 30
PAGE_MAX = 100
PUSH_PREVIEW = 120


def require_enabled() -> None:
    if not get_settings().team_announcements_enabled:
        raise HTTPException(status_code=403, detail="Announcements are turned off right now.")


def _recipient(db: Session, announcement_id: int, user_id: int) -> Optional[AnnouncementRecipient]:
    return db.get(AnnouncementRecipient, (announcement_id, user_id))


def get_for(db: Session, user: User, announcement_id: int) -> Announcement:
    """The announcement if the user received it (or is an admin) - 404
    otherwise, deleted ones included."""
    a = db.get(Announcement, announcement_id)
    if a is None or a.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Not found")
    if not user.is_admin and _recipient(db, a.id, user.id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    return a


def _stats(db: Session, announcement_id: int) -> dict:
    total, seen, acked = db.execute(
        select(
            func.count(AnnouncementRecipient.user_id),
            func.count(AnnouncementRecipient.seen_at),
            func.count(AnnouncementRecipient.acked_at),
        ).where(AnnouncementRecipient.announcement_id == announcement_id)
    ).one()
    return {"recipients": int(total or 0), "seen": int(seen or 0), "acked": int(acked or 0)}


def announcement_out(db: Session, user: User, a: Announcement) -> dict:
    teams = db.execute(
        select(Team)
        .join(AnnouncementAudience, AnnouncementAudience.team_id == Team.id)
        .where(AnnouncementAudience.announcement_id == a.id)
        .order_by(Team.name)
    ).scalars().all()
    media = db.get(TeamMedia, a.media_id) if a.media_id else None
    mine = _recipient(db, a.id, user.id)
    return {
        "id": a.id,
        "title": a.title,
        "body": a.body,
        "media": media_svc.media_out(media) if media else None,
        "require_ack": a.require_ack,
        "pinned": a.pinned,
        "created_by": {"id": a.created_by_user_id, "name": a.created_by_name},
        "created_at": a.created_at.isoformat(),
        "edited_at": a.edited_at.isoformat() if a.edited_at else None,
        "teams": [{"id": t.id, "name": t.name, "color": t.color} for t in teams],
        "me": (
            {
                "seen_at": mine.seen_at.isoformat() if mine.seen_at else None,
                "acked_at": mine.acked_at.isoformat() if mine.acked_at else None,
            }
            if mine
            else None
        ),
        # Only admins see who has read it.
        "stats": _stats(db, a.id) if user.is_admin else None,
    }


def list_for(db: Session, user: User, before_id: Optional[int], limit: int) -> dict:
    limit = max(1, min(limit or PAGE_DEFAULT, PAGE_MAX))
    stmt = select(Announcement).where(Announcement.deleted_at.is_(None))
    if not user.is_admin:
        stmt = stmt.join(
            AnnouncementRecipient,
            (AnnouncementRecipient.announcement_id == Announcement.id) & (AnnouncementRecipient.user_id == user.id),
        )
    if before_id is not None:
        stmt = stmt.where(Announcement.id < before_id)
    rows = db.execute(stmt.order_by(Announcement.id.desc()).limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    return {"announcements": [announcement_out(db, user, a) for a in rows[:limit]], "has_more": has_more}


def _recipient_ids(db: Session, announcement_id: int) -> List[int]:
    return list(
        db.execute(
            select(AnnouncementRecipient.user_id).where(AnnouncementRecipient.announcement_id == announcement_id)
        ).scalars().all()
    )


def create(
    db: Session,
    admin: User,
    title: str,
    body: str,
    team_ids: List[int],
    require_ack: bool,
    pinned: bool,
    media_token: Optional[str],
    client_uuid: Optional[str] = None,
) -> Announcement:
    client_uuid = (client_uuid or "").strip() or None
    if client_uuid is not None:
        if len(client_uuid) > 64:
            raise HTTPException(status_code=400, detail="client_uuid is too long")
        # A retried Post (the first one timed out after the server saved
        # it) gets the original back - no second announcement, no second push.
        existing = db.execute(
            select(Announcement).where(
                Announcement.created_by_user_id == admin.id, Announcement.client_uuid == client_uuid
            )
        ).scalars().first()
        if existing is not None:
            existing.duplicate_of_retry = True  # the router skips a second push
            return existing
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise HTTPException(status_code=400, detail=f"Title must be 1-{MAX_TITLE} characters.")
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=400, detail=f"The message is limited to {MAX_BODY} characters.")
    # Objectionable words are masked, never rejected (content_filter.py).
    title, body = content_filter.clean(title), content_filter.clean(body)
    wanted = sorted(set(team_ids or []))
    if not wanted:
        raise HTTPException(status_code=400, detail="Pick at least one team.")
    teams = db.execute(select(Team).where(Team.id.in_(wanted))).scalars().all()
    if len(teams) != len(wanted) or any(t.archived_at is not None for t in teams):
        raise HTTPException(status_code=400, detail="Unknown or archived team.")

    # The snapshot: everyone in those teams right now, except the author.
    recipients: Set[int] = set()
    for team in teams:
        recipients |= team_member_user_ids(db, team)
    recipients.discard(admin.id)
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_(recipients))).all()) if recipients else {}

    now = utcnow()
    a = Announcement(
        title=title,
        body=body,
        require_ack=bool(require_ack),
        pinned=bool(pinned),
        created_by_user_id=admin.id,
        created_by_name=admin.username,
        created_at=now,
        client_uuid=client_uuid,
    )
    db.add(a)
    try:
        db.flush()
    except IntegrityError:
        # The same Post racing itself - the other request won.
        db.rollback()
        existing = db.execute(
            select(Announcement).where(
                Announcement.created_by_user_id == admin.id, Announcement.client_uuid == client_uuid
            )
        ).scalars().one()
        existing.duplicate_of_retry = True
        return existing
    if media_token:
        a.media_id = media_svc.attach_media(db, admin, media_token, "announcement", a.id).id
    for team in teams:
        db.add(AnnouncementAudience(announcement_id=a.id, team_id=team.id))
    for uid in sorted(recipients):
        db.add(AnnouncementRecipient(announcement_id=a.id, user_id=uid, user_name=names.get(uid, "")))
    # Quiet hours: the push waits for 7 AM (flush_pending_pushes).
    if in_quiet_hours():
        a.pending_push = "post"
    record_change(db, "announcement", a.id, "created", user_ids=recipients)
    db.commit()
    db.refresh(a)
    return a


def update(
    db: Session,
    admin: User,
    announcement_id: int,
    title: Optional[str],
    body: Optional[str],
    pinned: Optional[bool],
    deleted: Optional[bool],
) -> Optional[Announcement]:
    a = get_for(db, admin, announcement_id)
    recipients = _recipient_ids(db, a.id)
    if deleted:
        a.deleted_at = utcnow()
        a.pending_push = None
        record_change(db, "announcement", a.id, "deleted", user_ids=recipients)
        db.commit()
        return None
    if title is not None:
        title = title.strip()
        if not title or len(title) > MAX_TITLE:
            raise HTTPException(status_code=400, detail=f"Title must be 1-{MAX_TITLE} characters.")
        a.title = content_filter.clean(title)
    if body is not None:
        if len(body.strip()) > MAX_BODY:
            raise HTTPException(status_code=400, detail=f"The message is limited to {MAX_BODY} characters.")
        a.body = content_filter.clean(body.strip())
    if pinned is not None:
        a.pinned = pinned
    if title is not None or body is not None:
        a.edited_at = utcnow()
    record_change(db, "announcement", a.id, "updated", user_ids=recipients)
    db.commit()
    return a


def mark_seen(db: Session, user: User, announcement_id: int, ack: bool) -> dict:
    a = get_for(db, user, announcement_id)
    row = _recipient(db, a.id, user.id)
    if row is None:  # an admin who wasn't a recipient
        raise HTTPException(status_code=400, detail="You didn't receive this announcement.")
    now = utcnow()
    changed = False
    if row.seen_at is None:
        row.seen_at = now
        changed = True
    if ack and row.acked_at is None:
        row.acked_at = now
        changed = True
    if changed:
        # The author/admins' counts change too; admins see every
        # non-channel row, so one row for this user covers both.
        record_change(db, "announcement", a.id, "updated", user_ids=[user.id])
        db.commit()
    return announcement_out(db, user, a)


def recipients_out(db: Session, announcement_id: int) -> dict:
    a = db.get(Announcement, announcement_id)
    if a is None or a.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Not found")
    rows = db.execute(
        select(AnnouncementRecipient)
        .where(AnnouncementRecipient.announcement_id == a.id)
        .order_by(AnnouncementRecipient.user_name)
    ).scalars().all()
    stats = _stats(db, a.id)
    return {
        "announcement_id": a.id,
        "require_ack": a.require_ack,
        # The count is "total" here: "recipients" is the list below.
        "total": stats["recipients"],
        "seen": stats["seen"],
        "acked": stats["acked"],
        "summary": (
            f"{stats['acked']} of {stats['recipients']} acknowledged"
            if a.require_ack
            else f"{stats['seen']} of {stats['recipients']} seen"
        ),
        "recipients": [
            {
                "user_id": r.user_id,
                "name": r.user_name,
                "seen_at": r.seen_at.isoformat() if r.seen_at else None,
                "acked_at": r.acked_at.isoformat() if r.acked_at else None,
                "last_reminded_at": r.last_reminded_at.isoformat() if r.last_reminded_at else None,
            }
            for r in rows
        ],
    }


def remind(db: Session, admin: User, announcement_id: int) -> dict:
    """Pushes again, only to recipients who haven't acknowledged (or, for an
    announcement without acknowledgment, haven't seen it)."""
    a = get_for(db, admin, announcement_id)
    col = AnnouncementRecipient.acked_at if a.require_ack else AnnouncementRecipient.seen_at
    pending = db.execute(
        select(AnnouncementRecipient).where(AnnouncementRecipient.announcement_id == a.id, col.is_(None))
    ).scalars().all()
    if not pending:
        return {"reminded": 0, "pending_push": False}
    a.remind_count += 1
    now = utcnow()
    for r in pending:
        r.last_reminded_at = now
    queued = in_quiet_hours()
    # A post still waiting for 7 AM already reaches everyone - keep it.
    if queued and a.pending_push != "post":
        a.pending_push = f"remind:{a.remind_count}"
    elif not queued and (a.pending_push or "").startswith("remind:"):
        # This reminder goes out now and supersedes one still queued from
        # the night - otherwise the 7 AM flush would send that one too.
        a.pending_push = None
    db.commit()
    if not queued:
        send_push(db, a, f"remind:{a.remind_count}")
    return {"reminded": len(pending), "pending_push": queued}


# --- Push -------------------------------------------------------------------------------


def push_targets(db: Session, a: Announcement, kind: str) -> List[int]:
    """Post -> every snapshot recipient; remind -> those still pending.
    Never current team membership, never the author."""
    stmt = select(AnnouncementRecipient.user_id).where(AnnouncementRecipient.announcement_id == a.id)
    if kind.startswith("remind"):
        col = AnnouncementRecipient.acked_at if a.require_ack else AnnouncementRecipient.seen_at
        stmt = stmt.where(col.is_(None))
    return [uid for uid in db.execute(stmt).scalars().all() if uid != a.created_by_user_id]


def send_push(db: Session, a: Announcement, kind: str) -> Set[int]:
    body = a.body if len(a.body) <= PUSH_PREVIEW else a.body[: PUSH_PREVIEW - 1].rstrip() + "…"
    title = a.title if kind == "post" else f"Reminder: {a.title}"
    return push_to_users(
        db,
        "announcement",
        push_targets(db, a, kind),
        title,
        body or "New announcement",
        {"route": "announcement", "announcement_id": a.id},
        dedupe_key=f"announcement:{a.id}:{kind}",
    )


def push_after_post(db: Session, announcement_id: int) -> Set[int]:
    """Background task after create: send now unless it was queued for the
    end of quiet hours."""
    a = db.get(Announcement, announcement_id)
    if a is None or a.deleted_at is not None or a.pending_push:
        return set()
    return send_push(db, a, "post")


def flush_pending_pushes(db: Session) -> int:
    """Team job: once quiet hours are over, send the pushes queued during
    them (design §8.1: "a post during quiet hours is delivered at 7 AM")."""
    if in_quiet_hours():
        return 0
    rows = db.execute(
        select(Announcement).where(Announcement.pending_push.is_not(None), Announcement.deleted_at.is_(None))
    ).scalars().all()
    sent = 0
    for a in rows:
        kind = a.pending_push
        reached = send_push(db, a, kind)
        if not reached and _could_have_reached(db, a, kind):
            # Firebase failed for everyone: keep it queued and try again at
            # the next run (the dedupe key stops a double send once it works).
            logger.warning("Announcement %s: queued %s push failed; will retry", a.id, kind)
            continue
        a.pending_push = None
        db.commit()
        sent += 1
    return sent


def _could_have_reached(db: Session, a: Announcement, kind: str) -> bool:
    """True when the push had someone to go to (Firebase set up, a target
    with a registered device, not already sent) - so reaching nobody means
    it failed rather than there being nobody to tell."""
    from app.services import notifications as notif

    if not push.is_configured():
        return False
    if notif.sent_dedupe_keys(db, [f"announcement:{a.id}:{kind}"]):
        return False
    targets = push_targets(db, a, kind)
    # Announcements switched off (own choice or store default) = nobody to tell.
    targets = sorted(set(targets) - notif.disabled_user_ids(db, "announcement", targets))
    if not targets:
        return False
    return db.execute(select(DeviceToken.id).where(DeviceToken.user_id.in_(targets)).limit(1)).first() is not None


# --- Badge + media access -----------------------------------------------------------------


def unacked_count(db: Session, user: User) -> int:
    if not get_settings().team_announcements_enabled:
        return 0
    return int(
        db.execute(
            select(func.count(AnnouncementRecipient.user_id))
            .join(Announcement, Announcement.id == AnnouncementRecipient.announcement_id)
            .where(
                AnnouncementRecipient.user_id == user.id,
                AnnouncementRecipient.acked_at.is_(None),
                Announcement.require_ack.is_(True),
                Announcement.deleted_at.is_(None),
            )
        ).scalar()
        or 0
    )


def _media_access(db: Session, user: User, announcement_id: int) -> bool:
    a = db.get(Announcement, announcement_id)
    return a is not None and a.deleted_at is None and _recipient(db, a.id, user.id) is not None


register_badge_provider("announcements_unacked", unacked_count)
media_svc.register_media_access("announcement", _media_access)
