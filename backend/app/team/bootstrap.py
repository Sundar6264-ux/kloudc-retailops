"""GET /api/team/bootstrap and GET /api/app/config payloads, and the teams
service (list/create/update/members) the admin endpoints use."""

import re
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.features import user_has_feature
from app.models import User
from app.pos.gating import capability_values, pos_name
from app.team.changes import badges, current_cursor, everyone_user_ids, record_change, user_team_ids
from app.team.models import Team, TeamMedia, TeamMember, TeamRulesAcceptance
from app.timeutil import utcnow

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def app_config() -> dict:
    s = get_settings()
    return {
        "min_supported_build": {"ios": s.min_supported_build_ios, "android": s.min_supported_build_android},
        "store_url": {"ios": s.app_store_url, "android": s.play_store_url},
        # Multi-store MT6 (additive): which POS this store runs and what it
        # can do (app/pos/gating.py). Spice Town: "Toast" and its full set.
        "pos_name": pos_name(),
        "capabilities": capability_values(),
    }


def can_assign(user: User) -> bool:
    return bool(user.is_admin or user.can_assign)


def team_out(team: Team, member_ids: Optional[List[int]] = None, member_count: Optional[int] = None) -> dict:
    out = {
        "id": team.id,
        "name": team.name,
        "color": team.color,
        "is_everyone": team.is_everyone,
    }
    if member_ids is not None:
        out["archived"] = team.archived_at is not None
        out["member_ids"] = member_ids
        member_count = len(member_ids)
    if member_count is not None:
        # Additive (Oct 15): 0 = a task for this team would reach nobody
        # (assigning one is refused - tasks_service._assignee_team).
        out["member_count"] = member_count
    return out


def member_counts(db: Session, teams: List[Team]) -> Dict[int, int]:
    """team id -> how many people are in it, for a list of teams (two
    queries, not one per team)."""
    rows = dict(
        db.execute(select(TeamMember.team_id, func.count()).group_by(TeamMember.team_id)).all()
    )
    everyone = len(everyone_user_ids(db)) if any(t.is_everyone for t in teams) else 0
    return {t.id: (everyone if t.is_everyone else int(rows.get(t.id, 0))) for t in teams}


def bootstrap(db: Session, user: User) -> dict:
    s = get_settings()
    teams = db.execute(
        select(Team).where(Team.archived_at.is_(None)).order_by(Team.is_everyone.desc(), Team.name)
    ).scalars().all()
    mine = user_team_ids(db, user)
    counts = member_counts(db, teams)
    return {
        "teams": [team_out(t, member_count=counts.get(t.id)) for t in teams],
        "my_team_ids": sorted(t.id for t in teams if t.id in mine),
        "can_assign": can_assign(user),
        "features": {
            "team_tasks": user_has_feature(user, "team_tasks"),
            "team_chat": user_has_feature(user, "team_chat"),
        },
        "flags": {
            "tasks_enabled": s.team_tasks_enabled,
            "announcements_enabled": s.team_announcements_enabled,
            "chat_enabled": s.team_chat_enabled,
            "poll_interval_s": s.team_poll_interval_s,
            "chat_poll_interval_s": s.team_chat_poll_interval_s,
            "badge_poll_interval_s": s.team_badge_poll_interval_s,
        },
        "min_supported_build": app_config()["min_supported_build"],
        "cursor": current_cursor(db),
        "badges": badges(db, user),
    }


# --- Teams admin ------------------------------------------------------------------


def _member_ids(db: Session, team: Team) -> List[int]:
    if team.is_everyone:
        return sorted(everyone_user_ids(db))
    return sorted(
        db.execute(select(TeamMember.user_id).where(TeamMember.team_id == team.id)).scalars().all()
    )


def list_teams(db: Session) -> List[dict]:
    teams = db.execute(select(Team).order_by(Team.is_everyone.desc(), Team.name)).scalars().all()
    return [team_out(t, _member_ids(db, t)) for t in teams]


def get_team(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def _clean_name(db: Session, name: str, exclude_id: Optional[int] = None) -> str:
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise HTTPException(status_code=400, detail="Team name must be 1-40 characters")
    stmt = select(Team.id).where(func.lower(Team.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Team.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise HTTPException(status_code=409, detail="A team with that name already exists")
    return name


def _clean_color(color: Optional[str]) -> Optional[str]:
    if color is None:
        return None
    if not _COLOR.match(color):
        raise HTTPException(status_code=400, detail="Color must look like #RRGGBB")
    return color.upper()


def create_team(db: Session, name: str, color: Optional[str]) -> dict:
    team = Team(name=_clean_name(db, name), color=_clean_color(color) or "#5B8DEF", created_at=utcnow())
    db.add(team)
    db.flush()
    record_change(db, "team", team.id, "created")
    db.commit()
    return team_out(team, _member_ids(db, team))


def update_team(
    db: Session, team_id: int, name: Optional[str], color: Optional[str], archived: Optional[bool]
) -> dict:
    team = get_team(db, team_id)
    if name is not None:
        team.name = _clean_name(db, name, exclude_id=team.id)
    if color is not None:
        team.color = _clean_color(color)
    if archived is not None:
        if team.is_everyone and archived:
            raise HTTPException(status_code=400, detail="The Everyone team can't be archived")
        team.archived_at = utcnow() if archived else None
    db.add(team)
    record_change(db, "team", team.id, "updated")
    db.commit()
    return team_out(team, _member_ids(db, team))


def set_members(db: Session, team_id: int, user_ids: List[int]) -> dict:
    team = get_team(db, team_id)
    if team.is_everyone:
        raise HTTPException(status_code=400, detail="Everyone always includes every user")
    wanted = set(user_ids)
    existing_users = set(db.execute(select(User.id).where(User.id.in_(wanted))).scalars().all()) if wanted else set()
    unknown = wanted - existing_users
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown user id(s): {sorted(unknown)}")
    current = set(
        db.execute(select(TeamMember.user_id).where(TeamMember.team_id == team.id)).scalars().all()
    )
    removed = current - wanted
    if removed:
        db.execute(delete(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id.in_(removed)))
    now = utcnow()
    for uid in sorted(wanted - current):
        db.add(TeamMember(team_id=team.id, user_id=uid, created_at=now))
    # Everyone whose team list changed hears about it, plus the team itself.
    record_change(db, "team", team.id, "updated")
    # Oct 15: people who joined or left also get the team's open tasks in
    # their feed, so their task lists refresh without waiting for a change.
    moved = sorted((wanted - current) | removed)
    if moved:
        from app.team.tasks_models import Task

        for tid in db.execute(
            select(Task.id).where(Task.assignee_team_id == team.id, Task.status == "open")
        ).scalars():
            record_change(db, "task", tid, "updated", user_ids=moved)
    db.commit()
    return team_out(team, _member_ids(db, team))


def delete_user_team_data(db: Session, user_id: int) -> None:
    """For account deletion (caller commits). User ids are never reused
    (users is AUTOINCREMENT since migration 0029), so history can safely keep
    pointing at the old id with its name snapshot - messages, completed
    tasks, announcement recipients, reports. What must go is anything that
    would keep *acting* for the gone account:

    - team memberships, DM memberships/read state and DM blocks (either way);
    - open tasks assigned to them alone -> cancelled (with an event), so
      they don't sit in managers' lists forever or keep pushing reminders;
    - recurring templates assigned to them -> deactivated;
    - their uploads -> uploaded_by cleared, so no one inherits "uploader"
      access to them.
    Models from the later phases are imported lazily and skipped if absent.
    """
    import json

    db.execute(delete(TeamMember).where(TeamMember.user_id == user_id))
    db.execute(delete(TeamRulesAcceptance).where(TeamRulesAcceptance.user_id == user_id))
    db.execute(update(TeamMedia).where(TeamMedia.uploaded_by_user_id == user_id).values(uploaded_by_user_id=None))

    try:
        from app.team.chat_models import ChannelMember, DmBlock
    except ImportError:  # pragma: no cover - chat not installed
        ChannelMember = DmBlock = None
    if ChannelMember is not None:
        db.execute(delete(ChannelMember).where(ChannelMember.user_id == user_id))
        db.execute(
            delete(DmBlock).where((DmBlock.blocker_user_id == user_id) | (DmBlock.blocked_user_id == user_id))
        )

    try:
        from app.team.tasks_models import Task, TaskEvent, TaskTemplate
    except ImportError:  # pragma: no cover - tasks not installed
        return
    now = utcnow()
    open_tasks = db.execute(
        select(Task).where(Task.assignee_user_id == user_id, Task.status == "open")
    ).scalars().all()
    for task in open_tasks:
        task.status = "cancelled"
        task.updated_at = now
        db.add(
            TaskEvent(
                task_id=task.id,
                user_id=None,
                user_name="Spice Town",
                kind="cancelled",
                detail_json=json.dumps({"reason": "The assignee's account was removed"}),
                created_at=now,
            )
        )
        record_change(db, "task", task.id, "updated", user_ids=[task.created_by_user_id])
    for tpl in db.execute(
        select(TaskTemplate).where(TaskTemplate.assignee_user_id == user_id, TaskTemplate.active.is_(True))
    ).scalars().all():
        tpl.active = False
        tpl.updated_at = now
        record_change(db, "task_template", tpl.id, "updated", user_ids=[tpl.created_by_user_id])
