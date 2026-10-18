"""Chat (design §3-§9, Phase 2): team channels and DMs, messages, read
markers, mute/hide, DM blocks, chat pushes, badges and retention.

Who is in a channel:
- team channel: everyone in that team right now (every user for the
  Everyone team) who has the `team_chat` feature. Membership is derived
  live from team_members, so adding/removing someone from Kitchen changes
  their Kitchen chat access immediately; channel_members rows there only
  hold per-person state (last read, muted).
- DM: the two people with channel_members rows, while both still have chat.
Admins may read any channel (moderation) but only get pushes/feed rows for
channels they're actually in.
"""

import datetime as dt
import json
import logging
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.demo import is_demo_user
from app.features import user_has_feature
from app.models import DeviceToken, NotificationLog, NotificationPref, User
from app.services import push
from app.services import notifications as notif
from app.team import content_filter
from app.team import media as media_svc
from app.team.changes import (
    is_everyone_member,
    record_change,
    register_badge_provider,
    register_channel_resolver,
    team_member_user_ids,
    teammate_user_ids,
    user_team_ids,
)
from app.team.chat_models import Channel, ChannelMember, DmBlock, Message, MessageReport
from app.team.models import Team, TeamMedia, TeamMember
from app.timeutil import restaurant_timezone, utcnow

logger = logging.getLogger(__name__)

NEUTRAL_BLOCKED = "You can't message this person."
OUTSIDE_TEAM = "You can only message people in your team."
MAX_BODY = 4000
PAGE_DEFAULT = 50
PAGE_MAX = 100
PUSH_THROTTLE = dt.timedelta(seconds=60)
PREVIEW_CHARS = 80
HIDE_PREVIEWS_KEY = "chat_hide_previews"


# --- Access -------------------------------------------------------------------------


def has_chat(user: User) -> bool:
    return user_has_feature(user, "team_chat")


def require_chat_enabled() -> None:
    if not get_settings().team_chat_enabled:
        raise HTTPException(status_code=403, detail="Chat is turned off right now.")


def _users_with_chat(db: Session, ids: Iterable[int]) -> Set[int]:
    ids = set(ids)
    if not ids:
        return set()
    users = db.execute(select(User).where(User.id.in_(ids))).scalars().all()
    return {u.id for u in users if has_chat(u)}


def team_channel_member_ids(db: Session, channel: Channel) -> Set[int]:
    team = db.get(Team, channel.team_id)
    if team is None:
        return set()
    # The one shared team-membership helper, so Everyone's exclusions (e.g.
    # the App Review demo login) apply to chat exactly as everywhere else.
    return _users_with_chat(db, team_member_user_ids(db, team))


def channel_archived(db: Session, channel: Channel) -> bool:
    """A team channel follows its team's archived state directly - the copy
    on the channel row is only synced when the channel list loads."""
    if channel.kind == "team":
        team = db.get(Team, channel.team_id)
        return team is None or team.archived_at is not None
    return channel.archived_at is not None


def channel_member_ids(db: Session, channel: Channel) -> Set[int]:
    if channel.kind == "team":
        return team_channel_member_ids(db, channel)
    ids = db.execute(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel.id)).scalars().all()
    return _users_with_chat(db, ids)


# --- Chat scoping (App Review build 7, contract §1) ---------------------------------
# A demo login or anyone left out of Everyone only chats with people who share
# a real team with them (teammate_user_ids): they don't see each other in
# People, can't open or send in a DM, and an older DM between them is hidden
# from both. Two ordinary staff members are unaffected.


def is_scoped(user: User) -> bool:
    return is_demo_user(user) or not is_everyone_member(user)


def may_chat(db: Session, a: User, b: User) -> bool:
    if not (is_scoped(a) or is_scoped(b)):
        return True
    return b.id in teammate_user_ids(db, a.id)


def _dm_in_scope(db: Session, user: User, channel: Channel) -> bool:
    other_id = _dm_other_user_id(channel, user.id)
    other = db.get(User, other_id) if other_id else None
    # A deleted account's DM keeps its history readable as before.
    return other is None or may_chat(db, user, other)


def is_member(db: Session, user: User, channel: Channel) -> bool:
    if not has_chat(user):
        return False
    if channel.kind == "team":
        return channel.team_id in user_team_ids(db, user)
    return (
        db.execute(
            select(ChannelMember.user_id).where(
                ChannelMember.channel_id == channel.id, ChannelMember.user_id == user.id
            )
        ).first()
        is not None
        and _dm_in_scope(db, user, channel)
    )


def ensure_team_channels(db: Session) -> None:
    """One channel per team (created lazily, archived with its team)."""
    teams = db.execute(select(Team)).scalars().all()
    existing = {
        c.team_id: c for c in db.execute(select(Channel).where(Channel.kind == "team")).scalars().all()
    }
    changed = False
    for team in teams:
        channel = existing.get(team.id)
        if channel is None:
            channel = Channel(kind="team", team_id=team.id, created_at=utcnow(), archived_at=team.archived_at)
            db.add(channel)
            try:
                db.flush()
            except IntegrityError:
                # Two first-time channel lists racing - the other one created it.
                db.rollback()
                return ensure_team_channels(db)
            record_change(db, "channel", channel.id, "created", channel_id=channel.id)
            changed = True
        elif (channel.archived_at is None) != (team.archived_at is None):
            channel.archived_at = team.archived_at
            record_change(db, "channel", channel.id, "updated", channel_id=channel.id)
            changed = True
    if changed:
        try:
            db.commit()
        except IntegrityError:  # another request created it first
            db.rollback()


def my_channel_ids(db: Session, user: User) -> Set[int]:
    """Channel ids the user is a member of (the change feed's resolver)."""
    if not has_chat(user):
        return set()
    teams = user_team_ids(db, user)
    ids: Set[int] = set()
    if teams:
        ids.update(
            db.execute(select(Channel.id).where(Channel.kind == "team", Channel.team_id.in_(teams))).scalars().all()
        )
    dms = db.execute(
        select(Channel)
        .join(ChannelMember, Channel.id == ChannelMember.channel_id)
        .where(Channel.kind == "dm", ChannelMember.user_id == user.id)
    ).scalars().all()
    ids.update(c.id for c in dms if _dm_in_scope(db, user, c))
    return ids


def get_channel_for(db: Session, user: User, channel_id: int, *, allow_admin: bool = False) -> Channel:
    """The channel if the user is in it (or is an admin, for reading) - 404
    otherwise, so channel ids can't be probed."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Not found")
    if is_member(db, user, channel):
        return channel
    if allow_admin and user.is_admin:
        # Moderation access to a conversation the admin isn't part of - kept
        # in the server log so it can be accounted for.
        logger.info("Chat audit: admin %s (id %s) opened channel %s (%s)", user.username, user.id, channel.id, channel.kind)
        return channel
    raise HTTPException(status_code=404, detail="Not found")


def _member_row(db: Session, channel_id: int, user_id: int, create: bool = True) -> Optional[ChannelMember]:
    row = db.get(ChannelMember, (channel_id, user_id))
    if row is None and create:
        row = ChannelMember(channel_id=channel_id, user_id=user_id)
        db.add(row)
        db.flush()
    return row


# --- Blocks ---------------------------------------------------------------------------


def blocked_between(db: Session, a: int, b: int) -> bool:
    return (
        db.execute(
            select(DmBlock.blocker_user_id).where(
                or_(
                    and_(DmBlock.blocker_user_id == a, DmBlock.blocked_user_id == b),
                    and_(DmBlock.blocker_user_id == b, DmBlock.blocked_user_id == a),
                )
            )
        ).first()
        is not None
    )


def _dm_other_user_id(channel: Channel, me: int) -> Optional[int]:
    if channel.kind != "dm" or not channel.dm_key:
        return None
    a, b = (int(x) for x in channel.dm_key.split(":"))
    return b if a == me else a


def _dm_key(a: int, b: int) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}:{hi}"


def list_blocks(db: Session, user: User) -> List[dict]:
    rows = db.execute(
        select(DmBlock, User.username)
        .join(User, User.id == DmBlock.blocked_user_id, isouter=True)
        .where(DmBlock.blocker_user_id == user.id)
        .order_by(DmBlock.created_at.desc())
    ).all()
    return [
        {"user_id": b.blocked_user_id, "name": name or "Deleted user", "created_at": b.created_at.isoformat()}
        for b, name in rows
    ]


def list_all_blocks(db: Session) -> List[dict]:
    """Admin view: that a block exists, never why."""
    rows = db.execute(select(DmBlock).order_by(DmBlock.created_at.desc())).scalars().all()
    names = dict(db.execute(select(User.id, User.username)).all())
    return [
        {
            "blocker_user_id": b.blocker_user_id,
            "blocker_name": names.get(b.blocker_user_id, "Deleted user"),
            "blocked_user_id": b.blocked_user_id,
            "blocked_name": names.get(b.blocked_user_id, "Deleted user"),
            "created_at": b.created_at.isoformat(),
        }
        for b in rows
    ]


def _dm_channel_between(db: Session, a: int, b: int) -> Optional[Channel]:
    return db.execute(select(Channel).where(Channel.dm_key == _dm_key(a, b))).scalars().first()


def block_user(db: Session, user: User, other_id: int) -> dict:
    if other_id == user.id:
        raise HTTPException(status_code=400, detail="You can't block yourself.")
    other = db.get(User, other_id)
    if other is None:
        raise HTTPException(status_code=404, detail="Not found")
    if db.get(DmBlock, (user.id, other_id)) is None:
        db.add(DmBlock(blocker_user_id=user.id, blocked_user_id=other_id, created_at=utcnow()))
        channel = _dm_channel_between(db, user.id, other_id)
        if channel is not None:
            row = _member_row(db, channel.id, user.id)
            row.hidden = True
            # Only the blocker's own devices hear about it - nothing tells
            # the blocked person (design §4.4).
            record_change(db, "channel", channel.id, "updated", user_ids=[user.id])
        record_change(db, "dm_block", other_id, "created", user_ids=[user.id])
        db.commit()
    return {"user_id": other_id, "name": other.username}


def unblock_user(db: Session, user: User, other_id: int) -> None:
    row = db.get(DmBlock, (user.id, other_id))
    if row is None:
        return
    db.delete(row)
    channel = _dm_channel_between(db, user.id, other_id)
    if channel is not None:
        member = _member_row(db, channel.id, user.id)
        member.hidden = False
        record_change(db, "channel", channel.id, "updated", user_ids=[user.id])
    record_change(db, "dm_block", other_id, "deleted", user_ids=[user.id])
    db.commit()


# --- Channels ---------------------------------------------------------------------------


def people(db: Session, user: User) -> List[dict]:
    """Who the user can start a DM with: others with chat access, minus
    anyone blocked either way, minus anyone outside the chat scope (a demo
    or Everyone-excluded login on either side must share a team)."""
    users = db.execute(select(User).where(User.id != user.id).order_by(User.username)).scalars().all()
    blocked = set(
        db.execute(select(DmBlock.blocked_user_id).where(DmBlock.blocker_user_id == user.id)).scalars().all()
    ) | set(db.execute(select(DmBlock.blocker_user_id).where(DmBlock.blocked_user_id == user.id)).scalars().all())
    me_scoped = is_scoped(user)
    mates = teammate_user_ids(db, user.id)

    def in_scope(u: User) -> bool:
        return (not me_scoped and not is_scoped(u)) or u.id in mates

    return [
        {"id": u.id, "name": u.username}
        for u in users
        if has_chat(u) and u.id not in blocked and in_scope(u)
    ]


def channel_members_out(db: Session, channel: Channel) -> List[dict]:
    ids = channel_member_ids(db, channel)
    if not ids:
        return []
    users = db.execute(select(User).where(User.id.in_(ids)).order_by(User.username)).scalars().all()
    return [{"id": u.id, "name": u.username} for u in users]


def get_or_create_dm(db: Session, user: User, other_id: int) -> Channel:
    other = db.get(User, other_id)
    # Same neutral answer whether the person doesn't exist, has no chat, or
    # a block exists - so nothing about the other side is revealed.
    if other is None or other.id == user.id or not has_chat(other) or blocked_between(db, user.id, other_id):
        raise HTTPException(status_code=403, detail=NEUTRAL_BLOCKED)
    if not may_chat(db, user, other):
        raise HTTPException(status_code=403, detail=OUTSIDE_TEAM)
    channel = _dm_channel_between(db, user.id, other_id)
    if channel is None:
        channel = Channel(kind="dm", dm_key=_dm_key(user.id, other_id), created_at=utcnow())
        db.add(channel)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            channel = _dm_channel_between(db, user.id, other_id)
            return channel
        for uid in (user.id, other_id):
            db.add(ChannelMember(channel_id=channel.id, user_id=uid))
        db.flush()
        record_change(db, "channel", channel.id, "created", channel_id=channel.id)
        db.commit()
    else:
        row = _member_row(db, channel.id, user.id)
        if row.hidden:
            row.hidden = False
            record_change(db, "channel", channel.id, "updated", channel_id=channel.id)
        db.commit()
    return channel


def access_start(db: Session, channel: Channel, user_id: int) -> Optional[dt.datetime]:
    """When this user got into a team channel: when they joined the team
    (when their account was created, for Everyone). Messages from before
    that are history, not unread - so a new hire or someone just added to
    Kitchen doesn't start with months of "unread" messages."""
    if channel.kind != "team":
        return None
    team = db.get(Team, channel.team_id)
    if team is None:
        return None
    if team.is_everyone:
        user = db.get(User, user_id)
        return user.created_at if user else None
    row = db.get(TeamMember, (team.id, user_id))
    return row.created_at if row else None


def _unread(db: Session, channel_id: int, user_id: int, last_read: int, since: Optional[dt.datetime] = None) -> int:
    stmt = select(func.count(Message.id)).where(
        Message.channel_id == channel_id,
        Message.id > last_read,
        Message.sender_user_id != user_id,
        Message.deleted_at.is_(None),
    )
    if since is not None:
        stmt = stmt.where(Message.created_at >= since)
    return int(db.execute(stmt).scalar() or 0)


def _last_message(db: Session, channel_id: int) -> Optional[Message]:
    return (
        db.execute(select(Message).where(Message.channel_id == channel_id).order_by(Message.id.desc()).limit(1))
        .scalars()
        .first()
    )


def i_blocked(db: Session, me: int, other_id: Optional[int]) -> bool:
    return bool(other_id) and db.get(DmBlock, (me, other_id)) is not None


def channel_out(db: Session, user: User, channel: Channel) -> dict:
    row = db.get(ChannelMember, (channel.id, user.id))
    last_read = row.last_read_message_id if row else 0
    last = _last_message(db, channel.id)
    archived = channel_archived(db, channel)
    member = is_member(db, user, channel)
    out = {
        "id": channel.id,
        "kind": channel.kind,
        "team_id": channel.team_id,
        "other_user_id": None,
        "name": "",
        "color": None,
        "archived": archived,
        "muted": bool(row.muted) if row else False,
        "hidden": bool(row.hidden) if row else False,
        # True only for the person who blocked (design §4.4: nothing tells
        # the blocked person) - they just get can_send=false.
        "blocked": False,
        "can_send": member and not archived,
        "last_read_message_id": last_read,
        "unread": _unread(db, channel.id, user.id, last_read, access_start(db, channel, user.id)),
        "last_message": message_out(db, last) if last else None,
        "seen_by": 0,
    }
    if channel.kind == "team":
        team = db.get(Team, channel.team_id)
        out["name"] = team.name if team else "Team"
        out["color"] = team.color if team else None
    else:
        ids = [int(x) for x in (channel.dm_key or "").split(":") if x]
        if user.id in ids:
            other_id = _dm_other_user_id(channel, user.id)
            other = db.get(User, other_id) if other_id else None
            out["other_user_id"] = other_id
            out["name"] = other.username if other else "Deleted user"
            out["blocked"] = i_blocked(db, user.id, other_id)
            out["can_send"] = bool(
                member and other is not None and has_chat(other) and not blocked_between(db, user.id, other_id)
            )
        else:
            # An admin looking in for moderation: name both people.
            names = []
            for uid in ids:
                u = db.get(User, uid)
                names.append(u.username if u else "Deleted user")
            out["name"] = " & ".join(names)
            out["can_send"] = False
    if last is not None:
        # "Seen by N": current members other than the sender whose read
        # marker has reached the last message (people who have since left
        # don't count).
        current = channel_member_ids(db, channel) - {last.sender_user_id}
        if current:
            out["seen_by"] = int(
                db.execute(
                    select(func.count(ChannelMember.user_id)).where(
                        ChannelMember.channel_id == channel.id,
                        ChannelMember.user_id.in_(current),
                        ChannelMember.last_read_message_id >= last.id,
                    )
                ).scalar()
                or 0
            )
    return out


def list_channels(db: Session, user: User) -> List[dict]:
    ensure_team_channels(db)
    ids = my_channel_ids(db, user)
    if not ids:
        return []
    channels = db.execute(select(Channel).where(Channel.id.in_(ids))).scalars().all()
    out = [channel_out(db, user, c) for c in channels]
    out = [c for c in out if not c["hidden"] and not (c["kind"] == "team" and c["archived"])]

    def sort_key(c: dict):
        last = c["last_message"]
        return (0 if c["kind"] == "team" else 1, -(last["id"] if last else 0), c["name"].lower())

    return sorted(out, key=sort_key)


def update_my_channel(db: Session, user: User, channel: Channel, muted: Optional[bool], hidden: Optional[bool]) -> dict:
    row = _member_row(db, channel.id, user.id)
    if muted is not None:
        row.muted = muted
    if hidden is not None:
        if channel.kind != "dm":
            raise HTTPException(status_code=400, detail="Only a direct message can be hidden.")
        row.hidden = hidden
    record_change(db, "channel", channel.id, "updated", user_ids=[user.id])
    db.commit()
    return channel_out(db, user, channel)


def mark_read(db: Session, user: User, channel: Channel, last_read_message_id: int) -> dict:
    row = _member_row(db, channel.id, user.id)
    newest = db.execute(select(func.max(Message.id)).where(Message.channel_id == channel.id)).scalar() or 0
    target = min(max(0, last_read_message_id), newest)
    if target > row.last_read_message_id:  # never moves backwards
        row.last_read_message_id = target
        record_change(db, "channel", channel.id, "updated", user_ids=[user.id])
    db.commit()
    return {
        "last_read_message_id": row.last_read_message_id,
        "unread": _unread(db, channel.id, user.id, row.last_read_message_id, access_start(db, channel, user.id)),
    }


# --- Messages ------------------------------------------------------------------------------


def find_mentions(body: str, users: Iterable[Tuple[int, str]]) -> List[int]:
    """User ids mentioned as "@<username>" in `body`. Matched against the
    actual usernames (they can have spaces - "@Sai Durga"), case-
    insensitively, longest name first so "@Sai Durga" isn't read as
    "@Sai", and only when the name ends at a non-word character or the end
    ("thanks @ravi." counts; "@ravikumar" doesn't mention ravi)."""
    text = (body or "").casefold()
    if "@" not in text:
        return []
    names = sorted(((name.casefold(), uid) for uid, name in users if name), key=lambda x: -len(x[0]))
    found: Set[int] = set()
    i = text.find("@")
    while i != -1:
        before = text[i - 1] if i > 0 else " "
        next_at = i + 1
        if not (before.isalnum() or before in "_@"):
            for name, uid in names:
                end = i + 1 + len(name)
                if text.startswith(name, i + 1) and (end == len(text) or not (text[end].isalnum() or text[end] == "_")):
                    found.add(uid)
                    next_at = end
                    break
        i = text.find("@", next_at)
    return sorted(found)


def _mentions(db: Session, channel: Channel, body: str) -> List[int]:
    if "@" not in (body or ""):
        return []
    members = channel_member_ids(db, channel)
    if not members:
        return []
    users = db.execute(select(User.id, User.username).where(User.id.in_(members))).all()
    return find_mentions(body, [(uid, name) for uid, name in users])


def format_duration(ms: Optional[int]) -> str:
    seconds = max(0, int(round((ms or 0) / 1000)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def attachment_label(media: Optional[TeamMedia]) -> str:
    """How a voice note / document reads where only text can be shown: a
    push, a channel list's last message, and an older app's bubble."""
    if media is None:
        return ""
    kind = media.kind or media_svc.KIND_IMAGE
    if kind == media_svc.KIND_AUDIO:
        return f"Voice message ({format_duration(media.duration_ms)})" if media.duration_ms else "Voice message"
    if kind == media_svc.KIND_DOCUMENT:
        return f"Document: {media.display_name or 'document.pdf'}"
    return "Photo"


def message_out(db: Session, m: Message) -> dict:
    deleted = m.deleted_at is not None
    media = None
    attachment = None
    body = None if deleted else m.body
    body_is_label = False
    if m.media_id and not deleted:
        row = db.get(TeamMedia, m.media_id)
        if row is not None:
            attachment = media_svc.media_out(row)
            if (row.kind or media_svc.KIND_IMAGE) == media_svc.KIND_IMAGE:
                media = attachment
            elif not (body or "").strip():
                # Builds 5-9 only draw `media` photos: a voice note or PDF
                # with no text would be an empty bubble there, so its label
                # stands in for the text. Newer clients see body_is_label
                # and draw the attachment instead.
                body = attachment_label(row)
                body_is_label = True
    return {
        "id": m.id,
        "channel_id": m.channel_id,
        "sender": {"id": m.sender_user_id, "name": m.sender_name},
        "body": body,
        "deleted": deleted,
        "deleted_by_moderator": deleted and m.deleted_by_user_id is not None and m.deleted_by_user_id != m.sender_user_id,
        # Photos only (what builds 5-9 draw); every kind is in `attachment`.
        "media": media,
        "mentions": [] if deleted else (json.loads(m.mentions_json) if m.mentions_json else []),
        "client_uuid": None if deleted else m.client_uuid,
        "created_at": m.created_at.isoformat(),
        "edited_at": m.edited_at.isoformat() if m.edited_at else None,
        # Additive (build 7): the content filter masked a word in it.
        "filtered": bool(m.filtered),
        # Additive (W5): voice note / document / photo, pinning.
        "attachment": attachment,
        "body_is_label": body_is_label,
        "pinned": bool(m.pinned_at) and not deleted,
        "pinned_at": m.pinned_at.isoformat() if m.pinned_at and not deleted else None,
        "pinned_by": (
            {"id": m.pinned_by_user_id, "name": m.pinned_by_name} if m.pinned_at and not deleted else None
        ),
    }


def list_messages(
    db: Session, channel: Channel, before_id: Optional[int], after_id: Optional[int], limit: int
) -> dict:
    limit = max(1, min(limit or PAGE_DEFAULT, PAGE_MAX))
    stmt = select(Message).where(Message.channel_id == channel.id)
    if after_id is not None:
        rows = db.execute(stmt.where(Message.id > after_id).order_by(Message.id.asc()).limit(limit + 1)).scalars().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
    else:
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        rows = db.execute(stmt.order_by(Message.id.desc()).limit(limit + 1)).scalars().all()
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
    return {"messages": [message_out(db, m) for m in rows], "has_more": has_more}


def send_message(
    db: Session,
    user: User,
    channel: Channel,
    body: str,
    client_uuid: str,
    media_token: Optional[str] = None,
) -> Tuple[Message, bool]:
    """Returns (message, created). A repeated client_uuid from the same
    sender returns the original message (created=False), never a copy."""
    client_uuid = (client_uuid or "").strip()
    if not client_uuid or len(client_uuid) > 64:
        raise HTTPException(status_code=400, detail="client_uuid is required")
    existing = db.execute(
        select(Message).where(Message.sender_user_id == user.id, Message.client_uuid == client_uuid)
    ).scalars().first()
    if existing is not None:
        if existing.channel_id != channel.id:
            raise HTTPException(status_code=409, detail="client_uuid already used for another message")
        return existing, False

    body = (body or "").strip()
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=400, detail=f"Messages are limited to {MAX_BODY} characters.")
    if not body and not media_token:
        raise HTTPException(status_code=400, detail="A message needs text, a photo, a voice note or a document.")
    if channel_archived(db, channel):
        raise HTTPException(status_code=403, detail="This team's chat is archived.")
    if channel.kind == "dm":
        other_id = _dm_other_user_id(channel, user.id)
        other = db.get(User, other_id) if other_id else None
        if other is None or not has_chat(other) or blocked_between(db, user.id, other_id):
            raise HTTPException(status_code=403, detail=NEUTRAL_BLOCKED)
        if not may_chat(db, user, other):
            raise HTTPException(status_code=403, detail=OUTSIDE_TEAM)

    # Objectionable words are masked, never rejected (content_filter.py).
    body, filtered = content_filter.filter_text(body)
    msg = Message(
        channel_id=channel.id,
        sender_user_id=user.id,
        sender_name=user.username,
        body=body,
        client_uuid=client_uuid,
        created_at=utcnow(),
        filtered=filtered,
    )
    mentions = _mentions(db, channel, body)
    if mentions:
        msg.mentions_json = json.dumps(mentions)
    db.add(msg)
    try:
        db.flush()
    except IntegrityError:
        # The same send racing itself - the other request won.
        db.rollback()
        msg = db.execute(
            select(Message).where(Message.sender_user_id == user.id, Message.client_uuid == client_uuid)
        ).scalars().one()
        return msg, False
    if media_token:
        media = media_svc.attach_media(db, user, media_token, "message", msg.id, kinds=media_svc.KINDS)
        msg.media_id = media.id
    # The sender has obviously read up to their own message.
    row = _member_row(db, channel.id, user.id)
    row.last_read_message_id = max(row.last_read_message_id, msg.id)
    if channel.kind == "dm" and row.hidden:
        row.hidden = False
    other_row = None
    if channel.kind == "dm":
        # A new message un-hides the DM for the other person (unless they
        # blocked - but then sending was refused above).
        other_row = _member_row(db, channel.id, _dm_other_user_id(channel, user.id))
        other_row.hidden = False
    record_change(db, "message", msg.id, "created", channel_id=channel.id)
    record_change(db, "channel", channel.id, "updated", channel_id=channel.id)
    db.commit()
    db.refresh(msg)
    return msg, True


def get_message_for(db: Session, user: User, message_id: int, *, allow_admin: bool = True) -> Tuple[Message, Channel]:
    msg = db.get(Message, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Not found")
    channel = db.get(Channel, msg.channel_id)
    if channel is None or not (is_member(db, user, channel) or (allow_admin and user.is_admin)):
        raise HTTPException(status_code=404, detail="Not found")
    return msg, channel


def soft_delete_message(db: Session, actor: User, msg: Message, reason: Optional[str]) -> Message:
    """Own delete or admin moderation (caller has checked who may). Keeps
    the text for 30 days so a report can still show what was said."""
    if msg.deleted_at is None:
        msg.deleted_at = utcnow()
        msg.deleted_by_user_id = actor.id
        msg.delete_reason = (reason or "").strip()[:200] or None
        record_change(db, "message", msg.id, "deleted", channel_id=msg.channel_id)
        record_change(db, "channel", msg.channel_id, "updated", channel_id=msg.channel_id)
        db.commit()
    return msg


def delete_message(db: Session, user: User, message_id: int, reason: Optional[str]) -> dict:
    msg, _ = get_message_for(db, user, message_id)
    own = msg.sender_user_id == user.id
    if not own and not user.is_admin:
        raise HTTPException(status_code=403, detail="You can only delete your own messages.")
    # A reason is only recorded for moderation (an admin deleting someone
    # else's message).
    soft_delete_message(db, user, msg, None if own else (reason or "Removed by a manager"))
    return message_out(db, msg)


# --- Pins, seen-by, search (W5) ------------------------------------------------------------

MAX_PINS = 20
SEARCH_MIN_CHARS = 2
SEARCH_PAGE = 30
SEARCH_PAGE_MAX = 100


def can_pin(user: User, channel: Channel) -> bool:
    """In a DM either person; in a team channel admins and the people who
    can assign tasks (shift leads) - pins are for the important few."""
    return channel.kind == "dm" or bool(user.is_admin or user.can_assign)


def set_pinned(db: Session, user: User, message_id: int, pinned: bool) -> dict:
    msg, channel = get_message_for(db, user, message_id, allow_admin=False)
    if not can_pin(user, channel):
        raise HTTPException(status_code=403, detail="Only managers and shift leads can pin in a team chat.")
    if msg.deleted_at is not None:
        raise HTTPException(status_code=409, detail="That message was deleted.")
    if channel_archived(db, channel):
        raise HTTPException(status_code=403, detail="This team's chat is archived.")
    if pinned and msg.pinned_at is None:
        count = db.execute(
            select(func.count(Message.id)).where(
                Message.channel_id == channel.id, Message.pinned_at.is_not(None), Message.deleted_at.is_(None)
            )
        ).scalar() or 0
        if count >= MAX_PINS:
            raise HTTPException(status_code=409, detail=f"Up to {MAX_PINS} pinned messages - unpin one first.")
        msg.pinned_at = utcnow()
        msg.pinned_by_user_id = user.id
        msg.pinned_by_name = user.username
    elif not pinned and msg.pinned_at is not None:
        msg.pinned_at = None
        msg.pinned_by_user_id = None
        msg.pinned_by_name = None
    else:
        return message_out(db, msg)
    record_change(db, "message", msg.id, "updated", channel_id=channel.id)
    record_change(db, "channel", channel.id, "updated", channel_id=channel.id)
    db.commit()
    return message_out(db, msg)


def list_pins(db: Session, channel: Channel) -> List[dict]:
    rows = db.execute(
        select(Message)
        .where(Message.channel_id == channel.id, Message.pinned_at.is_not(None), Message.deleted_at.is_(None))
        .order_by(Message.pinned_at.desc(), Message.id.desc())
        .limit(MAX_PINS)
    ).scalars().all()
    return [message_out(db, m) for m in rows]


def seen_by(db: Session, user: User, message_id: int) -> dict:
    """Who has read a message: the channel's current members other than its
    sender, split by whether their read marker has reached it (design: "Seen
    by N" on the last message; W5: the names, for any message)."""
    msg, channel = get_message_for(db, user, message_id)
    members = channel_member_ids(db, channel) - {msg.sender_user_id}
    if not members:
        return {"message_id": msg.id, "total": 0, "seen": [], "not_seen": []}
    read = dict(
        db.execute(
            select(ChannelMember.user_id, ChannelMember.last_read_message_id).where(
                ChannelMember.channel_id == channel.id, ChannelMember.user_id.in_(members)
            )
        ).all()
    )
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_(members))).all())
    seen, not_seen = [], []
    for uid in sorted(members, key=lambda u: (names.get(u) or "").lower()):
        person = {"id": uid, "name": names.get(uid, "Deleted user")}
        (seen if read.get(uid, 0) >= msg.id else not_seen).append(person)
    return {"message_id": msg.id, "total": len(members), "seen": seen, "not_seen": not_seen}


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def searchable_channel_ids(db: Session, user: User) -> Set[int]:
    """The chats this person can see in their list: their team channels
    (not archived) and DMs they haven't hidden or blocked."""
    out: Set[int] = set()
    for cid in my_channel_ids(db, user):
        channel = db.get(Channel, cid)
        if channel is None or channel_archived(db, channel):
            continue
        row = db.get(ChannelMember, (cid, user.id))
        if row is not None and row.hidden:
            continue
        out.add(cid)
    return out


def search_messages(
    db: Session,
    user: User,
    q: str,
    channel_id: Optional[int] = None,
    before_id: Optional[int] = None,
    limit: int = SEARCH_PAGE,
) -> dict:
    """Messages matching `q` (text, or a document's name) in the chats this
    person can see, newest first. Deleted messages are never found."""
    q = (q or "").strip()
    if len(q) < SEARCH_MIN_CHARS:
        raise HTTPException(status_code=400, detail=f"Type at least {SEARCH_MIN_CHARS} characters to search.")
    if len(q) > 100:
        q = q[:100]
    limit = max(1, min(limit or SEARCH_PAGE, SEARCH_PAGE_MAX))
    channels = searchable_channel_ids(db, user)
    if channel_id is not None:
        channels &= {channel_id}
    if not channels:
        return {"results": [], "has_more": False}
    pattern = _like(q)
    # People I blocked: their messages are folded away in team chats and the
    # chat list (App Review 1.2), so search never brings them back up.
    blocked_ids = select(DmBlock.blocked_user_id).where(DmBlock.blocker_user_id == user.id)
    doc_ids = select(TeamMedia.id).where(
        TeamMedia.kind == media_svc.KIND_DOCUMENT, TeamMedia.display_name.ilike(pattern, escape="\\")
    )
    stmt = (
        select(Message)
        .where(
            Message.channel_id.in_(channels),
            Message.deleted_at.is_(None),
            Message.sender_user_id.not_in(blocked_ids),
            or_(Message.body.ilike(pattern, escape="\\"), Message.media_id.in_(doc_ids)),
        )
        .order_by(Message.id.desc())
    )
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    rows = db.execute(stmt.limit(limit + 1)).scalars().all()
    by_channel = {c.id: c for c in db.execute(select(Channel).where(Channel.id.in_({m.channel_id for m in rows}))).scalars()} if rows else {}
    results = []
    for m in rows[:limit]:
        channel = by_channel.get(m.channel_id)
        out = message_out(db, m)
        out["channel"] = _channel_brief(db, user, channel) if channel else None
        results.append(out)
    return {"results": results, "has_more": len(rows) > limit}


def _channel_brief(db: Session, user: User, channel: Channel) -> dict:
    if channel.kind == "team":
        team = db.get(Team, channel.team_id)
        return {"id": channel.id, "kind": "team", "name": team.name if team else "Team", "color": team.color if team else None}
    other_id = _dm_other_user_id(channel, user.id)
    other = db.get(User, other_id) if other_id else None
    return {"id": channel.id, "kind": "dm", "name": other.username if other else "Deleted user", "color": None}


# --- Push -------------------------------------------------------------------------------------


def _now_local() -> dt.datetime:
    return dt.datetime.now(restaurant_timezone())


def in_quiet_hours() -> bool:
    return notif.in_quiet_hours(_now_local())


def pref_disabled_user_ids(db: Session, key: str, user_ids: Iterable[int]) -> Set[int]:
    """Who has `key` effectively off: their own choice, else the store / role
    default (services/notifications.py, Oct 15)."""
    return notif.disabled_user_ids(db, key, user_ids)


def hide_previews(db: Session, user_id: int) -> bool:
    row = db.execute(
        select(NotificationPref).where(NotificationPref.user_id == user_id, NotificationPref.key == HIDE_PREVIEWS_KEY)
    ).scalars().first()
    return bool(row and row.enabled)


def set_hide_previews(db: Session, user: User, value: bool) -> dict:
    row = db.execute(
        select(NotificationPref).where(NotificationPref.user_id == user.id, NotificationPref.key == HIDE_PREVIEWS_KEY)
    ).scalars().first()
    if row is None:
        db.add(NotificationPref(user_id=user.id, key=HIDE_PREVIEWS_KEY, enabled=value))
    else:
        row.enabled = value
    db.commit()
    return {"hide_previews": value}


def push_to_users(
    db: Session,
    key: str,
    user_ids: Iterable[int],
    title: str,
    body: str,
    data: Dict[str, str],
    *,
    dedupe_key: Optional[str] = None,
    respect_prefs: bool = True,
    body_for: Optional[Dict[int, str]] = None,
) -> Set[int]:
    """Sends one push per user to that user's devices; returns the user ids
    reached. Targeted (unlike notifications.dispatch_alert, which broadcasts
    to everyone with access): the caller has already decided who. Honors the
    per-alert on/off prefs and, when `dedupe_key` is given, sends it at most
    once (notification_log, written only after a real send)."""
    ids = set(user_ids)
    if respect_prefs:
        ids -= pref_disabled_user_ids(db, key, ids)
    if not ids:
        return set()
    if dedupe_key and notif.sent_dedupe_keys(db, [dedupe_key]):
        return set()
    tokens_by_user: Dict[int, List[str]] = {}
    for uid, token in db.execute(select(DeviceToken.user_id, DeviceToken.token).where(DeviceToken.user_id.in_(ids))):
        tokens_by_user.setdefault(uid, []).append(token)
    payload = {"type": key}
    payload.update({k: str(v) for k, v in data.items() if v is not None})
    if not push.is_configured():
        logger.info("Team push (dry run): %s to %s: %r / %r %s", key, sorted(tokens_by_user), title, body, payload)
        return set()
    if not tokens_by_user:
        return set()
    reached: Set[int] = set()
    # Grouped by body so users with "Hide previews" get the generic text.
    groups: Dict[str, List[int]] = {}
    for uid in tokens_by_user:
        groups.setdefault((body_for or {}).get(uid, body), []).append(uid)
    for text, uids in groups.items():
        user_by_token = {t: uid for uid in uids for t in tokens_by_user[uid]}
        # One lock-screen group per conversation.
        thread = f"chat-{payload['channel_id']}" if payload.get("route") == "chat" and payload.get("channel_id") else None
        result = push.send_to_tokens(list(user_by_token), title, text, payload, thread_id=thread)
        notif._delete_tokens(db, result.invalid_tokens)
        reached.update(user_by_token[t] for t in result.delivered_tokens)
    if dedupe_key and reached:
        db.add(NotificationLog(key=key, dedupe_key=dedupe_key, sent_at=utcnow(), recipient_count=len(reached)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    notif.log_deliveries(db, key, reached, title)  # per-user log, chat included (0058)
    return reached


def chat_push_recipients(db: Session, msg: Message, now: Optional[dt.datetime] = None) -> Set[int]:
    """Who a new message pushes to (design §8.1): channel members except the
    sender; except muted members, unless mentioned; never across a DM
    block; at most one push per person per channel per minute while they
    still haven't read since the last one; and not those who switched the
    chat alert off."""
    channel = db.get(Channel, msg.channel_id)
    if channel is None:
        return set()
    now = now or utcnow()
    members = channel_member_ids(db, channel) - {msg.sender_user_id}
    mentions = set(json.loads(msg.mentions_json)) if msg.mentions_json else set()
    # W5: someone who switched Mentions off isn't pulled through a mute or
    # the throttle by being mentioned.
    mentions -= pref_disabled_user_ids(db, MENTION_KEY, mentions)
    rows = {
        r.user_id: r
        for r in db.execute(select(ChannelMember).where(ChannelMember.channel_id == channel.id)).scalars().all()
    }
    out: Set[int] = set()
    for uid in members:
        row = rows.get(uid)
        if blocked_between(db, uid, msg.sender_user_id):
            continue
        if row is not None and row.muted and uid not in mentions:
            continue
        if row is not None and row.hidden:
            continue
        if (
            row is not None
            and row.last_pushed_at is not None
            and now - row.last_pushed_at < PUSH_THROTTLE
            and row.last_read_message_id < row.last_pushed_message_id
            and uid not in mentions
        ):
            continue
        out.add(uid)
    return out - pref_disabled_user_ids(db, "chat_message", out)


MENTION_KEY = "chat_mention"


def mention_push_recipients(db: Session, msg: Message) -> Set[int]:
    """W5: the people @mentioned in a message get their own "X mentioned
    you" push - even in a muted chat and never throttled, but never across
    a DM block, not in a DM they hid, and not if they switched Mentions off
    (then the normal chat push rules apply to them)."""
    mentions = set(json.loads(msg.mentions_json)) if msg.mentions_json else set()
    if not mentions:
        return set()
    channel = db.get(Channel, msg.channel_id)
    if channel is None:
        return set()
    members = channel_member_ids(db, channel) - {msg.sender_user_id}
    out: Set[int] = set()
    for uid in mentions & members:
        if blocked_between(db, uid, msg.sender_user_id):
            continue
        row = db.get(ChannelMember, (channel.id, uid))
        if row is not None and row.hidden:
            continue
        out.add(uid)
    return out - pref_disabled_user_ids(db, MENTION_KEY, out)


def _message_preview(db: Session, msg: Message) -> str:
    preview = msg.body or ""
    if not preview.strip() and msg.media_id:
        preview = attachment_label(db.get(TeamMedia, msg.media_id))
    if len(preview) > PREVIEW_CHARS:
        preview = preview[: PREVIEW_CHARS - 1].rstrip() + "…"
    return preview


def push_new_message(db: Session, message_id: int) -> Set[int]:
    """Runs after the send is committed (a background task)."""
    msg = db.get(Message, message_id)
    if msg is None or msg.deleted_at is not None:
        return set()
    if in_quiet_hours():
        return set()  # nothing queued; unread counts show next time
    recipients = chat_push_recipients(db, msg)
    mentioned = mention_push_recipients(db, msg)
    sender = db.get(User, msg.sender_user_id)
    if is_demo_user(sender):
        # A demo login's message only ever notifies its own teammates.
        mates = teammate_user_ids(db, sender.id)
        recipients &= mates
        mentioned &= mates
    # A mentioned person gets the mention push instead of the plain one.
    recipients -= mentioned
    if not recipients and not mentioned:
        return set()
    channel = db.get(Channel, msg.channel_id)
    if channel.kind == "team":
        team = db.get(Team, channel.team_id)
        where = team.name if team else "Team"
        title = f"{msg.sender_name} in {where}"
        mention_title = f"{msg.sender_name} mentioned you in {where}"
        generic = f"New message in {where}"
    else:
        title = msg.sender_name
        mention_title = f"{msg.sender_name} mentioned you"
        generic = f"New message from {msg.sender_name}"
    preview = _message_preview(db, msg)
    data = {"route": "chat", "channel_id": channel.id, "message_id": msg.id}
    reached: Set[int] = set()
    if recipients:
        body_for = {uid: generic for uid in recipients if hide_previews(db, uid)}
        reached |= push_to_users(
            db,
            "chat_message",
            recipients,
            title,
            preview,
            data,
            respect_prefs=False,  # already applied in chat_push_recipients
            body_for=body_for,
        )
    if mentioned:
        body_for = {uid: generic for uid in mentioned if hide_previews(db, uid)}
        reached |= push_to_users(
            db,
            MENTION_KEY,
            mentioned,
            mention_title,
            preview,
            data,
            dedupe_key=f"{MENTION_KEY}:{msg.id}",
            respect_prefs=False,  # applied in mention_push_recipients
            body_for=body_for,
        )
    # The throttle counts a push as sent once it reached a device (or, with
    # Firebase not set up, as if it had - so the rule behaves the same).
    now = utcnow()
    for uid in reached if push.is_configured() else (recipients | mentioned):
        row = _member_row(db, channel.id, uid)
        row.last_pushed_at = now
        row.last_pushed_message_id = msg.id
    db.commit()
    return reached


# --- Hooks: change-feed resolver, badges, media access ------------------------------------------


def unread_total(db: Session, user: User) -> int:
    if not get_settings().team_chat_enabled or not has_chat(user):
        return 0
    total = 0
    for channel_id in my_channel_ids(db, user):
        channel = db.get(Channel, channel_id)
        if channel is None or channel_archived(db, channel):
            continue
        row = db.get(ChannelMember, (channel_id, user.id))
        if row is not None and row.hidden:
            continue
        total += _unread(
            db, channel_id, user.id, row.last_read_message_id if row else 0, access_start(db, channel, user.id)
        )
    return total


def _message_media_access(db: Session, user: User, message_id: int) -> bool:
    msg = db.get(Message, message_id)
    if msg is None or msg.deleted_at is not None:
        return False
    channel = db.get(Channel, msg.channel_id)
    return channel is not None and is_member(db, user, channel)


register_channel_resolver(my_channel_ids)
register_badge_provider("unread", unread_total)
media_svc.register_media_access("message", _message_media_access)


# --- Retention (design §9.4) ----------------------------------------------------------------


def run_chat_retention(db: Session, now: Optional[dt.datetime] = None) -> dict:
    """Nightly: hard-delete messages (and their images and reports) older
    than the chat retention period; blank the text/image of messages
    soft-deleted more than 30 days ago; drop reports resolved more than a
    retention period ago."""
    s = get_settings()
    now = now or utcnow()
    old = now - dt.timedelta(days=s.team_chat_retention_days)
    purge_deleted = now - dt.timedelta(days=s.team_deleted_message_days)

    expired = db.execute(select(Message).where(Message.created_at < old)).scalars().all()
    for msg in expired:
        _drop_message_media(db, msg)
    expired_ids = [m.id for m in expired]
    if expired_ids:
        db.execute(delete(MessageReport).where(MessageReport.message_id.in_(expired_ids)))
        db.execute(delete(Message).where(Message.id.in_(expired_ids)))

    # Evidence for a report still waiting on an admin is kept until it's
    # handled - otherwise deleting a reported message and waiting 30 days
    # would wipe it from the moderation queue.
    under_review = set(
        db.execute(select(MessageReport.message_id).where(MessageReport.status == "open")).scalars().all()
    )
    blanked = 0
    for msg in db.execute(
        select(Message).where(
            Message.deleted_at.is_not(None),
            Message.deleted_at < purge_deleted,
            or_(Message.body != "", Message.media_id.is_not(None), Message.mentions_json.is_not(None)),
        )
    ).scalars().all():
        if msg.id in under_review:
            continue
        if msg.body:
            blanked += 1
        msg.body = ""
        msg.mentions_json = None
        _drop_message_media(db, msg)

    reports = db.execute(
        delete(MessageReport).where(MessageReport.status != "open", MessageReport.resolved_at < old)
    ).rowcount or 0
    db.commit()
    result = {"deleted": len(expired_ids), "blanked": blanked, "reports": reports}
    if expired_ids or blanked or reports:
        logger.info("Chat retention: %s", result)
    return result


def _drop_message_media(db: Session, msg: Message) -> None:
    if msg.media_id is None:
        return
    media = db.get(TeamMedia, msg.media_id)
    msg.media_id = None
    if media is not None:
        media_svc.delete_media_file(media)
        db.delete(media)
