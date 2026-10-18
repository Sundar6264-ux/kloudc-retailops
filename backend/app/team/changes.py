"""The Team change feed (design §7): record_change, the per-user
GET /api/team/updates query, and the nightly prune.

A cursor is a `team_changes.seq`. `/updates?since=X` returns every row after
X the caller may see - grouped by entity type, one entry per entity (its
latest action) - and the new cursor. Rows older than the retention window are
pruned; a cursor that points into the pruned range can no longer be answered
exactly, so the response says `resync_required` and the client reloads.

Long-poll (Oct 15): `/updates?wait=N` waits up to N seconds for something to
change. Every commit of a session that recorded a change bumps an in-process
version (record_change marks the session; the after_commit hook below) and
wakes the waiting requests, which then re-run the query. The app runs as ONE
uvicorn process (the launchd plist has no --workers), so the in-process
signal sees every write - the request handlers' and the scheduler thread's.
"""

import asyncio
import datetime as dt
import logging
import threading
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import delete, event, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import is_postgres, sequence_last_value
from app.demo import demo_usernames
from app.models import User
from app.team.models import Team, TeamChange, TeamMember
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

ENTITY_TYPES = (
    "task",
    "task_template",
    "announcement",
    "channel",
    "message",
    "team",
    "dm_block",
)
ACTIONS = ("created", "updated", "deleted")

# --- Hooks later phases register into ---------------------------------------

# Channel ids the user is a member of (Phase 2 registers the real one).
_channel_resolvers: List[Callable[[Session, User], Set[int]]] = []

# Badge counts for /bootstrap and /updates, by name (tasks_due, unread,
# announcements_unacked). Each phase registers its own.
_badge_providers: Dict[str, Callable[[Session, User], int]] = {}

BADGE_NAMES = ("tasks_due", "unread", "announcements_unacked")


def register_channel_resolver(fn: Callable[[Session, User], Set[int]]) -> None:
    _channel_resolvers.append(fn)


def register_badge_provider(name: str, fn: Callable[[Session, User], int]) -> None:
    _badge_providers[name] = fn


# --- Membership helpers -----------------------------------------------------


def _everyone_excluded_names() -> Set[str]:
    raw = get_settings().team_everyone_exclude or ""
    # A demo login (DEMO_USERNAMES, app/demo.py) is never in Everyone either,
    # even if someone forgets to list it in TEAM_EVERYONE_EXCLUDE.
    return {n.strip().lower() for n in raw.split(",") if n.strip()} | demo_usernames()


def is_everyone_member(user: User) -> bool:
    """Is this user in the implicit Everyone team? Every user except the
    logins listed in TEAM_EVERYONE_EXCLUDE or DEMO_USERNAMES (e.g. the App
    Store demo account).
    The ONE place that rule lives - use this or everyone_user_ids, never
    "all users"."""
    return (user.username or "").strip().lower() not in _everyone_excluded_names()


def everyone_user_ids(db: Session) -> Set[int]:
    excluded = _everyone_excluded_names()
    rows = db.execute(select(User.id, User.username)).all()
    return {uid for uid, name in rows if (name or "").strip().lower() not in excluded}


def user_team_ids(db: Session, user: User) -> Set[int]:
    """Teams this user is in: their member rows plus the Everyone team
    (implicit, see is_everyone_member). Archived teams included - old items
    still belong to them."""
    ids = set(
        db.execute(select(TeamMember.team_id).where(TeamMember.user_id == user.id)).scalars().all()
    )
    if is_everyone_member(user):
        ids.update(db.execute(select(Team.id).where(Team.is_everyone.is_(True))).scalars().all())
    return ids


def team_member_user_ids(db: Session, team: Team) -> Set[int]:
    """Everyone in a team right now (for the Everyone team: every user but
    the excluded logins)."""
    if team.is_everyone:
        return everyone_user_ids(db)
    return set(db.execute(select(TeamMember.user_id).where(TeamMember.team_id == team.id)).scalars().all())


def teammate_user_ids(db: Session, user_id: int) -> Set[int]:
    """Everyone who shares a real team with this user - a named, live team
    (not Everyone, not archived) - the user included. Used to keep demo and
    Everyone-excluded logins inside their own teams (chat scoping, pushes)."""
    teams = select(TeamMember.team_id).join(Team, Team.id == TeamMember.team_id).where(
        TeamMember.user_id == user_id, Team.is_everyone.is_(False), Team.archived_at.is_(None)
    )
    ids = set(db.execute(select(TeamMember.user_id).where(TeamMember.team_id.in_(teams))).scalars().all())
    ids.add(user_id)
    return ids


def user_channel_ids(db: Session, user: User) -> Set[int]:
    ids: Set[int] = set()
    for resolver in _channel_resolvers:
        ids.update(resolver(db, user))
    return ids


# --- Writing ------------------------------------------------------------------


def record_change(
    db: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    *,
    team_id: Optional[int] = None,
    user_ids: Optional[Iterable[int]] = None,
    channel_id: Optional[int] = None,
) -> None:
    """Adds the feed row(s) for a change to the caller's session - no commit,
    so it lands in the same transaction as the change itself.

    Audience: `channel_id` -> that channel's members; `team_id` -> that
    team's members; `user_ids` -> each of those users (one row each, e.g. a
    task's assignee and its creator); several may be combined. Nothing ->
    everyone. Admins see every non-channel row.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unknown entity_type: {entity_type}")
    if action not in ACTIONS:
        raise ValueError(f"Unknown action: {action}")
    now = utcnow()
    users = sorted(set(user_ids or []))
    rows = []
    if channel_id is not None:
        rows.append(TeamChange(channel_id=channel_id))
    if team_id is not None:
        rows.append(TeamChange(team_id=team_id))
    rows.extend(TeamChange(user_id=u) for u in users)
    if not rows:
        rows.append(TeamChange())
    for row in rows:
        row.entity_type = entity_type
        row.entity_id = entity_id
        row.action = action
        row.created_at = now
        db.add(row)
    # Waiting long-polls wake once this session commits (_after_commit).
    db.info[_DIRTY] = True


# --- Long-poll signal -----------------------------------------------------------

_DIRTY = "team_changes_dirty"
# Long-poll waits are capped below the app's 30 s Dio receiveTimeout.
MAX_WAIT_S = 20

_signal_lock = threading.Lock()
_signal_version = 0
_waiters: List[Tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []


def change_version() -> int:
    """Bumped after every commit that added feed rows. Read it BEFORE
    querying, then wait_for_change(version): a commit landing between the
    query and the wait still wakes the wait."""
    return _signal_version


def notify_changed() -> None:
    """Wakes every waiting /updates long-poll (any thread)."""
    global _signal_version
    with _signal_lock:
        _signal_version += 1
        waiters = list(_waiters)
    for loop, ev in waiters:
        try:
            loop.call_soon_threadsafe(ev.set)
        except RuntimeError:  # that request's loop is gone
            pass


async def wait_for_change(version: int, timeout: float) -> bool:
    """Waits until change_version() moves past `version`, at most `timeout`
    seconds. True = something was committed. Holds no thread and no DB
    connection while waiting."""
    if timeout <= 0:
        return False
    ev = asyncio.Event()
    entry = (asyncio.get_running_loop(), ev)
    with _signal_lock:
        if _signal_version != version:
            return True
        _waiters.append(entry)
    try:
        await asyncio.wait_for(ev.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        with _signal_lock:
            if entry in _waiters:
                _waiters.remove(entry)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    if session.info.pop(_DIRTY, False):
        notify_changed()


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_DIRTY, None)


# --- Reading ------------------------------------------------------------------


def current_cursor(db: Session) -> int:
    """The highest seq ever handed out, right even after every row has been
    pruned.

    SQLite: from sqlite_sequence (writers are serialised, so every seq it
    holds is committed). PostgreSQL: the newest committed row's seq; the
    sequence's last value only when no row is left - a sequence also counts
    seqs of transactions still in flight (or rolled back), and handing such a
    seq out as a cursor would make a client skip a change that commits later
    with a lower seq."""
    if is_postgres(db):
        seq = db.execute(select(func.max(TeamChange.seq))).scalar()
        if seq is None:
            seq = sequence_last_value(db, "team_changes", "seq")
        return int(seq or 0)
    seq = sequence_last_value(db, "team_changes", "seq")
    if seq is None:
        seq = db.execute(select(func.max(TeamChange.seq))).scalar()
    return int(seq or 0)


def badges(db: Session, user: User) -> Dict[str, int]:
    out = {name: 0 for name in BADGE_NAMES}
    for name, fn in _badge_providers.items():
        try:
            out[name] = int(fn(db, user))
        except Exception:  # noqa: BLE001 - a badge must never break /updates
            logger.exception("Team badge provider %s failed", name)
    return out


def get_updates(db: Session, user: User, since: int) -> dict:
    """What changed after `since` that `user` may see (design §7.3)."""
    cursor = current_cursor(db)
    oldest = db.execute(select(func.min(TeamChange.seq))).scalar()
    oldest_retained = int(oldest) if oldest is not None else cursor + 1
    # Every seq in (since, cursor] must still exist to answer exactly. A
    # cursor from the future (e.g. a reset dev database) can't be answered
    # either.
    if since > cursor or (since < cursor and since + 1 < oldest_retained):
        return {"cursor": cursor, "resync_required": True, "changes": {}, "badges": badges(db, user)}

    conditions = [TeamChange.channel_id.is_(None)] if user.is_admin else []
    channels = user_channel_ids(db, user)
    if channels:
        conditions.append(TeamChange.channel_id.in_(channels))
    if not user.is_admin:
        teams = user_team_ids(db, user)
        everyone = (
            TeamChange.channel_id.is_(None)
            & TeamChange.team_id.is_(None)
            & TeamChange.user_id.is_(None)
        )
        conditions.append(everyone)
        conditions.append(TeamChange.user_id == user.id)
        if teams:
            conditions.append(TeamChange.team_id.in_(teams) & TeamChange.channel_id.is_(None))

    rows = db.execute(
        select(TeamChange)
        .where(TeamChange.seq > since, TeamChange.seq <= cursor, or_(*conditions))
        .order_by(TeamChange.seq)
    ).scalars().all()

    # One entry per entity, carrying its latest action (a task created then
    # deleted since the cursor just reads "deleted").
    latest: Dict[str, Dict[int, str]] = {}
    for row in rows:
        latest.setdefault(row.entity_type, {})[row.entity_id] = row.action
    changes = {
        etype: [{"id": eid, "action": action} for eid, action in by_id.items()]
        for etype, by_id in latest.items()
    }
    return {"cursor": cursor, "resync_required": False, "changes": changes, "badges": badges(db, user)}


# --- Pruning ------------------------------------------------------------------


def prune_team_changes(db: Session, now: Optional[dt.datetime] = None) -> int:
    """Deletes feed rows older than team_changes_retention_days. Returns how
    many were removed."""
    days = get_settings().team_changes_retention_days
    cutoff = (now or utcnow()) - dt.timedelta(days=days)
    result = db.execute(delete(TeamChange).where(TeamChange.created_at < cutoff))
    db.commit()
    removed = result.rowcount or 0
    if removed:
        logger.info("Team change feed: pruned %d row(s) older than %d days", removed, days)
    return removed
