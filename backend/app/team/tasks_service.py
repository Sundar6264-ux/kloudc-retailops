"""Team hub tasks (design §3-§8, Phase 1): who may see and act on a task,
every write path (each records its change-feed row in the same transaction
and appends to the task's event history), the recurring-template admin, and
the push reminders.

Who is who, for a task:
- assignees: the assigned user, or every current member of the assigned team
- readers: assignees, the creator, admins
- actors (complete / can't do / items): assignees and admins
- editors (edit, reassign, cancel): the creator and admins
- reopen: the creator, admins, and whoever completed it (a cancelled task:
  creator and admins only)
Everything also needs the `team_tasks` feature (admins always have it).

Status changes are one conditional UPDATE (`... WHERE status = 'open'`), so
when two people finish the same team task at once the first one wins and
the other gets a 409 - never a silent overwrite.

Automatic tasks (auto_tasks.py) are created by SYSTEM_USER_ID (0, never a
real user) - it's left out of feed rows and pushes; their "can't do" goes
to the admins and the assignee team's assigners instead.

Oct 15 (docs/BATCH_OCT15_CONTRACT.md, "As built - TEAM TASKS (server)"):
every write is conditional on the task still being open (and, when the
client sends `expected_updated_at`, on nobody having changed it since - 409
with the current task otherwise); steps can be added / renamed / removed
and their ticks are in the history; a source-linked task says where its
real action is (`action`); lists filter by person / source / text; admins
can close several tasks at once; and the pushes a request causes are sent
after the response (BackgroundTasks), never inside it.
"""

import datetime as dt
import json
import logging
import threading
import uuid
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import Integer, and_, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.demo import is_demo_user
from app.features import user_has_feature
from app.models import NotificationLog, User
from app.services import notifications as notif
from app.services import push as push_svc
from app.services.notifications import Alert, dispatch_alert, in_quiet_hours
from app.team import content_filter
from app.team import media as media_svc
from app.team import recurrence
from app.team.changes import (
    record_change,
    register_badge_provider,
    team_member_user_ids,
    teammate_user_ids,
    user_team_ids,
)
from app.team.jobs import register_team_job, with_session
from app.team.models import Team, TeamMedia
from app.team.tasks_models import Task, TaskEvent, TaskItem, TaskTemplate
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

REQUIRES = ("none", "photo", "note")
PRIORITIES = ("normal", "urgent")
STATUSES = ("open", "done", "cant_do", "cancelled")
VIEWS = ("mine", "team", "created", "all")

MAX_TITLE = 200
MAX_TEXT = 2000
MAX_ITEMS = 30
MAX_BATCH = 50
# Open lists are a to-do list, not paged; past this they say `truncated`.
OPEN_LIST_LIMIT = 500

SYSTEM_USER_ID = 0
# The actor name on automatic tasks is the store itself: system_name()
# (STORE_NAME). SYSTEM_NAME still reads it (module __getattr__ below).


def system_name() -> str:
    from app.store.settings_view import store_display_name

    return store_display_name()

SOON_BEFORE = dt.timedelta(minutes=30)
OVERDUE_AFTER = dt.timedelta(hours=1)
# How far back the reminder/assignment sweeps look - bounded so a long-open
# task never keeps getting scanned.
SWEEP_WINDOW = dt.timedelta(hours=12)
OVERDUE_WINDOW = dt.timedelta(days=2)

# task_assigned is sent both from the request that created the task and by
# the 5-minute sweep (for quiet hours / generated tasks). dispatch_alert's
# check-send-log isn't atomic, so the two could both send the same push;
# one process-wide lock around the send makes the second see the log row.
# (The app runs as a single uvicorn process.)
_ASSIGN_PUSH_LOCK = threading.Lock()


def store_zone():
    return recurrence.get_zone(recurrence.default_timezone())


# --- Access -------------------------------------------------------------------


def require_tasks(user: User) -> None:
    if not get_settings().team_tasks_enabled:
        raise HTTPException(status_code=503, detail="Tasks are turned off right now")
    if not user_has_feature(user, "team_tasks"):
        raise HTTPException(status_code=403, detail="You don't have access to Tasks")


def is_assigner(user: User) -> bool:
    return bool(user.is_admin or user.can_assign)


def assignable_people(db: Session, user: User) -> List[Dict[str, Any]]:
    # A demo login (app/demo.py) may only assign people work to itself
    # (app/demo_guard.create_in_sandbox), so - even with can_assign, which it
    # needs for its own team's tasks - it isn't shown the real staff list.
    if not is_assigner(user) or is_demo_user(user):
        return [{"id": user.id, "name": user.username}]
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    return [{"id": u.id, "name": u.username} for u in users if user_has_feature(u, "team_tasks")]


def is_assignee(db: Session, user: User, task: Task, teams: Optional[Set[int]] = None) -> bool:
    if task.assignee_user_id is not None:
        return task.assignee_user_id == user.id
    teams = user_team_ids(db, user) if teams is None else teams
    return task.assignee_team_id in teams


def can_read(db: Session, user: User, task: Optional[Task], teams: Optional[Set[int]] = None) -> bool:
    if task is None:
        return False
    if user.is_admin or task.created_by_user_id == user.id:
        return True
    return is_assignee(db, user, task, teams)


def can_act(db: Session, user: User, task: Task, teams: Optional[Set[int]] = None) -> bool:
    return user.is_admin or is_assignee(db, user, task, teams)


def can_edit(user: User, task: Task) -> bool:
    return user.is_admin or task.created_by_user_id == user.id


def can_reopen(user: User, task: Task) -> bool:
    if can_edit(user, task):
        return True
    # Whoever finished it may undo that - but a cancellation is the
    # creator's (or an admin's) decision.
    return (
        task.status != "cancelled"
        and task.completed_by_user_id is not None
        and task.completed_by_user_id == user.id
    )


def get_task_for(db: Session, user: User, task_id: int) -> Task:
    """The task, or 404 - also for one the user may not see, so ids reveal
    nothing."""
    task = db.get(Task, task_id)
    if not can_read(db, user, task):
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# --- Change feed + history ------------------------------------------------------


def record_task_change(db: Session, task: Task, action: str, extra_user_ids: Iterable[int] = ()) -> None:
    """Feed row(s) for everyone who can see the task (admins see all)."""
    users = {task.created_by_user_id}
    if task.assignee_user_id is not None:
        users.add(task.assignee_user_id)
    users.update(u for u in extra_user_ids if u is not None)
    users.discard(SYSTEM_USER_ID)
    record_change(db, "task", task.id, action, team_id=task.assignee_team_id, user_ids=users)


def add_event(
    db: Session,
    task: Task,
    user: Optional[User],
    kind: str,
    detail: Optional[dict] = None,
    system_name: str = "Schedule",
) -> None:
    """`system_name` names the actor when there's no user: "Schedule" for
    recurring occurrences, system_name() for automatic tasks."""
    db.add(
        TaskEvent(
            task_id=task.id,
            user_id=user.id if user else None,
            user_name=user.username if user else system_name,
            kind=kind,
            detail_json=json.dumps(detail) if detail else None,
            created_at=utcnow(),
        )
    )


# --- Serialization ------------------------------------------------------------------


class _Names:
    """Cached user/team name lookups for one response."""

    def __init__(self, db: Session):
        self.db = db
        self._users: Dict[int, str] = {}
        self._teams: Dict[int, Team] = {}

    def user(self, user_id: Optional[int]) -> Optional[str]:
        if user_id is None:
            return None
        if user_id not in self._users:
            u = self.db.get(User, user_id)
            self._users[user_id] = u.username if u else "(deleted user)"
        return self._users[user_id]

    def team(self, team_id: Optional[int]) -> Optional[Team]:
        if team_id is None:
            return None
        if team_id not in self._teams:
            self._teams[team_id] = self.db.get(Team, team_id)
        return self._teams[team_id]


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _assignee_out(task, names: _Names) -> dict:
    if task.assignee_user_id is not None:
        return {"type": "user", "id": task.assignee_user_id, "name": names.user(task.assignee_user_id)}
    team = names.team(task.assignee_team_id)
    return {
        "type": "team",
        "id": task.assignee_team_id,
        "name": team.name if team else "(deleted team)",
        "color": team.color if team else None,
    }


class _ListContext:
    """Everything a list page needs per task, loaded once for the page
    instead of a few queries per row."""

    def __init__(self, db: Session, user: User, tasks: List[Task]):
        self.teams = user_team_ids(db, user)
        ids = [t.id for t in tasks]
        self.item_stats: Dict[int, Tuple[int, int]] = {}
        self.media: Dict[int, TeamMedia] = {}
        if ids:
            rows = db.execute(
                select(TaskItem.task_id, func.count(TaskItem.id), func.sum(cast(TaskItem.done, Integer)))
                .where(TaskItem.task_id.in_(ids))
                .group_by(TaskItem.task_id)
            ).all()
            self.item_stats = {tid: (int(n), int(done or 0)) for tid, n, done in rows}
        media_ids = [t.completion_media_id for t in tasks if t.completion_media_id]
        if media_ids:
            self.media = {
                m.id: m for m in db.execute(select(TeamMedia).where(TeamMedia.id.in_(media_ids))).scalars()
            }


def _completed_by_out(task: Task) -> Optional[dict]:
    # Closed by the system (an automatic task's signal cleared): no user id,
    # but still a name, so "Done by <store>" reads right.
    if task.completed_by_user_id is None and not task.completed_by_name:
        return None
    return {"id": task.completed_by_user_id, "name": task.completed_by_name}


def task_out(
    db: Session,
    task: Task,
    user: User,
    names: Optional[_Names] = None,
    detail: bool = False,
    ctx: Optional[_ListContext] = None,
) -> dict:
    names = names or _Names(db)
    now = utcnow()
    items: List[TaskItem] = []
    if ctx is not None and not detail:
        item_count, items_done = ctx.item_stats.get(task.id, (0, 0))
        photo = ctx.media.get(task.completion_media_id) if task.completion_media_id else None
        teams = ctx.teams
    else:
        items = db.execute(
            select(TaskItem).where(TaskItem.task_id == task.id).order_by(TaskItem.position, TaskItem.id)
        ).scalars().all()
        item_count, items_done = len(items), sum(1 for i in items if i.done)
        photo = db.get(TeamMedia, task.completion_media_id) if task.completion_media_id else None
        teams = ctx.teams if ctx is not None else None
    out = {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "requires": task.requires,
        "priority": task.priority,
        "status": task.status,
        "assignee": _assignee_out(task, names),
        "assignee_user_id": task.assignee_user_id,
        "assignee_team_id": task.assignee_team_id,
        "due_at": _iso(task.due_at),
        "due_at_local": _iso(task.due_at.astimezone(store_zone())) if task.due_at else None,
        "is_overdue": bool(task.status == "open" and task.due_at is not None and task.due_at < now),
        "template_id": task.template_id,
        "assignment_batch_id": task.assignment_batch_id,
        "completed_by": _completed_by_out(task),
        "completed_at": _iso(task.completed_at),
        "completion_note": task.completion_note,
        "completion_photo": media_svc.media_out(photo) if photo else None,
        "cant_do_reason": task.cant_do_reason,
        "source_type": task.source_type,
        "source_id": task.source_id,
        "created_by": {"id": task.created_by_user_id, "name": task.created_by_name},
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "item_count": item_count,
        "items_done": items_done,
        # Additive (W5): the board's "In progress".
        "started_at": _iso(task.started_at),
        "started_by": (
            {"id": task.started_by_user_id, "name": task.started_by_name} if task.started_at else None
        ),
        "board_column": board_column(task),
        "can": {
            "complete": task.status == "open" and can_act(db, user, task, teams),
            "edit": task.status == "open" and can_edit(user, task),
            "reopen": task.status != "open" and can_reopen(user, task),
            "comment": True,
            "start": task.status == "open" and task.started_at is None and can_act(db, user, task, teams),
            "unstart": task.status == "open" and task.started_at is not None and can_act(db, user, task, teams),
            # Additive (Oct 15): add / rename / remove steps (never the
            # App Review demo login - its guard refuses those writes).
            "edit_items": task.status == "open" and can_edit(user, task) and not is_demo_user(user),
        },
        # Additive (Oct 15): made by the system (an automatic task), live
        # progress ("N left of M"), and where the real action is.
        "is_auto": task.created_by_user_id == SYSTEM_USER_ID,
        "progress": progress_out(task),
    }
    action = source_action(task)
    out["action"] = action
    out["action_route"] = action["route"] if action else None
    if detail:
        out["items"] = [
            {
                "id": i.id,
                "text": i.text,
                "done": i.done,
                "done_by": names.user(i.done_by_user_id),
                "done_at": _iso(i.done_at),
                "position": i.position,
            }
            for i in items
        ]
        events = db.execute(
            select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.id)
        ).scalars().all()
        out["events"] = [
            {
                "id": e.id,
                "kind": e.kind,
                "user_id": e.user_id,
                "user_name": e.user_name,
                "detail": json.loads(e.detail_json) if e.detail_json else None,
                "created_at": _iso(e.created_at),
            }
            for e in events
        ]
        out["batch"] = (
            batch_summary(db, task.assignment_batch_id, names)
            if task.assignment_batch_id and can_edit(user, task)
            else None
        )
    return out


def progress_out(task: Task) -> Optional[dict]:
    """{"done", "total", "left"} for a task that tracks a count (a Fix
    Issues lead's daily list), else None."""
    if task.progress_total is None:
        return None
    total = max(0, int(task.progress_total))
    done = min(total, max(0, int(task.progress_done or 0)))
    return {"done": done, "total": total, "left": total - done}


def _source_parts(task: Task) -> List[str]:
    return (task.source_id or "").split(":")


def source_action(task: Task) -> Optional[dict]:
    """Where a source-linked task's real work is done, so its main button
    opens that instead of a bare "Mark done" (the source then closes the
    task itself - source_sync.py). `route` uses the push-route names every
    app build already knows (core/notifications/push_target.dart);
    `web_path` is the website's own route. None for a plain task."""
    if task.created_by_user_id != SYSTEM_USER_ID or not task.source_type:
        return None
    parts = _source_parts(task)
    if task.source_type == "checklist":
        return {
            "kind": "checklist",
            "label": "Open the checklist",
            "route": "checklists",
            "params": {"checklist_type": parts[0], "day": parts[-1] if len(parts) > 1 else None},
            "web_path": "/daily-checklists",
        }
    if task.source_type == "order_due":
        # Imported here: auto_tasks imports this module.
        from app.team.auto_tasks import _parse_order_source

        parsed = _parse_order_source(task.source_id or "")
        vendor, _, delivery = parsed if parsed else (None, None, None)
        return {
            "kind": "order",
            "label": "Open the order",
            "route": "supplier_projection",
            "params": {"vendor": vendor or None, "delivery_date": delivery.isoformat() if delivery else None},
            "web_path": "/supplier-projection",
        }
    if task.source_type == "invoice":
        return {
            "kind": "invoice",
            "label": "Open the invoice",
            "route": "invoice",
            "params": {"invoice_id": task.source_id},
            "web_path": "/invoices",
        }
    if task.source_type == "issue_leads":
        lead = parts[0] if parts and parts[0].isdigit() else None
        return {
            "kind": "fix_issues",
            "label": "Open the Fix Issues list",
            "route": "fix_issues",
            "params": {"lead": lead},
            "web_path": f"/fix-issues?lead={lead}" if lead else "/fix-issues",
        }
    return None


def board_column(task: Task) -> str:
    """Where a task sits on the board: todo | in_progress | done | cant_do |
    cancelled."""
    if task.status == "open":
        return "in_progress" if task.started_at is not None else "todo"
    return task.status


def batch_summary(db: Session, batch_id: str, names: Optional[_Names] = None) -> dict:
    names = names or _Names(db)
    tasks = db.execute(
        select(Task).where(Task.assignment_batch_id == batch_id).order_by(Task.id)
    ).scalars().all()
    live = [t for t in tasks if t.status != "cancelled"]
    return {
        "id": batch_id,
        "title": tasks[0].title if tasks else None,
        "total": len(live),
        "done": sum(1 for t in live if t.status == "done"),
        "assignees": [
            {
                "task_id": t.id,
                "user_id": t.assignee_user_id,
                "name": names.user(t.assignee_user_id),
                "status": t.status,
                "completed_at": _iso(t.completed_at),
            }
            for t in tasks
        ],
    }


# --- Validation helpers ------------------------------------------------------------


def _clean_text(value: Optional[str], field: str, limit: int, required: bool = False) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        if required:
            raise HTTPException(status_code=400, detail=f"{field} is required")
        return None
    if len(value) > limit:
        raise HTTPException(status_code=400, detail=f"{field} is too long ({limit} characters max)")
    return value


def _clean_user_text(value: Optional[str], field: str, limit: int, required: bool = False) -> Optional[str]:
    """_clean_text plus the objectionable-content filter (masks, never
    rejects - content_filter.py). For everything people type into a task."""
    return content_filter.clean(_clean_text(value, field, limit, required))


def _check_choice(value: str, allowed, field: str) -> str:
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"{field} must be one of: {', '.join(allowed)}")
    return value


def normalize_due(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """Aware -> UTC. Naive = store-local wall time (what a website
    datetime-local input sends), resolved with the same DST rules as
    recurring tasks."""
    if value is None:
        return None
    if value.tzinfo is None:
        return recurrence.local_to_utc(value, store_zone())
    return value.astimezone(dt.timezone.utc)


def _assignee_user(db: Session, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=400, detail=f"Unknown user id {user_id}")
    if not user_has_feature(target, "team_tasks"):
        raise HTTPException(status_code=400, detail=f"{target.username} doesn't have access to Tasks")
    return target


def _assignee_team(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None or team.archived_at is not None:
        raise HTTPException(status_code=400, detail="Unknown or archived team")
    # Oct 15: a task for a team with nobody in it reaches nobody (no list,
    # no push) - prod's checklist tasks piled up unseen that way. Refused,
    # with what to do instead.
    if not team_member_user_ids(db, team):
        raise HTTPException(
            status_code=400,
            detail=f"{team.name} has no members yet - add people to it on the Teams page, or assign it to a person.",
        )
    return team


# --- Lists ----------------------------------------------------------------------------


def list_tasks(
    db: Session,
    user: User,
    view: str = "mine",
    status: str = "open",
    before_id: Optional[int] = None,
    limit: int = 100,
    overdue: bool = False,
    assignee_user_id: Optional[int] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    """`overdue` (W5): only open tasks already past their due time.
    Oct 15 filters, on top of the view (never widening it):
    `assignee_user_id` (one person's own tasks), `source` ("manual" = made
    by a person, "auto" = by the system, or one source_type such as
    "issue_leads"), `q` (words in the title)."""
    _check_choice(view, VIEWS, "view")
    _check_choice(status, STATUSES, "status")
    limit = max(1, min(limit, 200))
    teams = user_team_ids(db, user)
    stmt = select(Task).where(Task.status == status)
    if overdue:
        stmt = stmt.where(Task.status == "open", Task.due_at.is_not(None), Task.due_at < utcnow())
    stmt = _filtered(stmt, assignee_user_id, source, q)
    if view == "mine":
        conds = [Task.assignee_user_id == user.id]
        if teams:
            conds.append(Task.assignee_team_id.in_(teams))
        stmt = stmt.where(or_(*conds))
    elif view == "team":
        if not teams:
            return {"items": [], "next_before_id": None, "truncated": False}
        stmt = stmt.where(Task.assignee_team_id.in_(teams))
    elif view == "created":
        stmt = stmt.where(Task.created_by_user_id == user.id)
    elif not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can see all tasks")

    truncated = False
    if status == "open":
        # Open work is a to-do list: soonest due first, no-due-date last.
        # Bounded rather than paged; `truncated` says when there's more (so
        # a client can say "showing the first 500" instead of silently
        # dropping today's task behind stale overdue ones).
        rows = db.execute(
            stmt.order_by(Task.due_at.is_(None), Task.due_at, Task.id).limit(OPEN_LIST_LIMIT + 1)
        ).scalars().all()
        truncated = len(rows) > OPEN_LIST_LIMIT
        rows = rows[:OPEN_LIST_LIMIT]
        next_before = None
    else:
        if before_id is not None:
            stmt = stmt.where(Task.id < before_id)
        rows = db.execute(stmt.order_by(Task.id.desc()).limit(limit + 1)).scalars().all()
        next_before = rows[limit - 1].id if len(rows) > limit else None
        rows = rows[:limit]
    names = _Names(db)
    ctx = _ListContext(db, user, rows)
    return {
        "items": [task_out(db, t, user, names, ctx=ctx) for t in rows],
        "next_before_id": next_before,
        "truncated": truncated,
    }


MAX_INCLUDED_TASKS = 100


def tasks_for_changes(db: Session, user: User, entries: List[dict]) -> List[dict]:
    """For GET /updates?include=tasks (Oct 15): each changed task the
    caller may see, in the list shape (task_out), so a client patches its
    lists without fetching them again. A deleted task, one it can't see, or
    no Tasks access at all: left out (the change entry still says what
    happened). At most 100 - past that, refetch the lists."""
    if not entries or not get_settings().team_tasks_enabled or not user_has_feature(user, "team_tasks"):
        return []
    ids = [e["id"] for e in entries if e.get("action") != "deleted"][:MAX_INCLUDED_TASKS]
    if not ids:
        return []
    rows = db.execute(select(Task).where(Task.id.in_(ids)).order_by(Task.id)).scalars().all()
    ctx = _ListContext(db, user, rows)
    names = _Names(db)
    return [task_out(db, t, user, names, ctx=ctx) for t in rows if can_read(db, user, t, ctx.teams)]


def _filtered(stmt, assignee_user_id: Optional[int], source: Optional[str], q: Optional[str]):
    if assignee_user_id is not None:
        stmt = stmt.where(Task.assignee_user_id == assignee_user_id)
    source = (source or "").strip().lower()
    if source == "manual":
        stmt = stmt.where(Task.created_by_user_id != SYSTEM_USER_ID)
    elif source == "auto":
        stmt = stmt.where(Task.created_by_user_id == SYSTEM_USER_ID)
    elif source:
        stmt = stmt.where(Task.source_type == source)
    for word in (q or "").split()[:5]:
        pattern = "%" + word[:50].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(Task.title.ilike(pattern, escape="\\"))
    return stmt


# --- Writes --------------------------------------------------------------------------


def create_tasks(
    db: Session,
    user: User,
    *,
    title: str,
    description: Optional[str] = None,
    assignee_user_ids: Optional[List[int]] = None,
    assignee_team_id: Optional[int] = None,
    due_at: Optional[dt.datetime] = None,
    requires: str = "none",
    priority: str = "normal",
    items: Optional[List[str]] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    background: Optional[BackgroundTasks] = None,
) -> dict:
    title = _clean_user_text(title, "Title", MAX_TITLE, required=True)
    description = _clean_user_text(description, "Description", MAX_TEXT)
    _check_choice(requires, REQUIRES, "requires")
    _check_choice(priority, PRIORITIES, "priority")
    items = [content_filter.clean(t) for t in ((i or "").strip() for i in (items or [])) if t]
    if len(items) > MAX_ITEMS or any(len(i) > MAX_TITLE for i in items):
        raise HTTPException(status_code=400, detail=f"Up to {MAX_ITEMS} sub-items, {MAX_TITLE} characters each")
    user_ids = list(dict.fromkeys(assignee_user_ids or []))
    if user_ids and assignee_team_id is not None:
        raise HTTPException(status_code=400, detail="Assign to people or to a team, not both")
    if not user_ids and assignee_team_id is None:
        user_ids = [user.id]  # no assignee = a personal to-do
    if len(user_ids) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"Up to {MAX_BATCH} people at once")
    for_others = assignee_team_id is not None or any(u != user.id for u in user_ids)
    if for_others and not is_assigner(user):
        raise HTTPException(status_code=403, detail="You can only create tasks for yourself")
    due = normalize_due(due_at)

    if assignee_team_id is not None:
        _assignee_team(db, assignee_team_id)
        targets = [(None, assignee_team_id)]
    else:
        for uid in user_ids:
            _assignee_user(db, uid)
        targets = [(uid, None) for uid in user_ids]
    batch_id = str(uuid.uuid4()) if len(targets) > 1 else None

    now = utcnow()
    created: List[Task] = []
    for uid, tid in targets:
        task = Task(
            assignment_batch_id=batch_id,
            title=title,
            description=description,
            requires=requires,
            priority=priority,
            assignee_user_id=uid,
            assignee_team_id=tid,
            due_at=due,
            status="open",
            source_type=_clean_text(source_type, "source_type", 40),
            source_id=_clean_text(source_id, "source_id", 100),
            created_by_user_id=user.id,
            created_by_name=user.username,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.flush()
        for pos, text in enumerate(items):
            db.add(TaskItem(task_id=task.id, position=pos, text=text, done=False))
        add_event(db, task, user, "created", {"batch": batch_id} if batch_id else None)
        record_task_change(db, task, "created")
        created.append(task)
    db.commit()
    for task in created:
        after_response(background, db, push_assigned, task.id, user.id)
    names = _Names(db)
    return {"assignment_batch_id": batch_id, "tasks": [task_out(db, t, user, names) for t in created]}


def after_response(background: Optional[BackgroundTasks], db: Session, fn, *args) -> None:
    """Runs `fn(db, *args)` (a push) after the response has gone out when
    the route passed its BackgroundTasks - FCM takes ~250 ms a recipient,
    which used to hold up task create / reassign / can't do - else right
    away (jobs, tests, other callers)."""
    if background is None:
        fn(db, *args)
    else:
        background.add_task(_in_own_session, fn, *args)


def _in_own_session(fn, *args) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        fn(db, *args)
    except Exception:  # noqa: BLE001 - a push problem never surfaces
        logger.exception("Task push %s failed", getattr(fn, "__name__", fn))
    finally:
        db.close()


def _require_open(task: Task) -> None:
    if task.status != "open":
        raise TaskConflict("This task isn't open any more", task.id)


class TaskConflict(HTTPException):
    """A 409 the task routes answer with the task as it is now as well
    ({"detail": ..., "task": {...}}), so the client can show the current
    state without another round trip. Elsewhere it's a plain 409."""

    def __init__(self, detail: str, task_id: int):
        super().__init__(status_code=409, detail=detail)
        self.task_id = task_id


CHANGED_BY_SOMEONE = "This task was changed by someone else - reload it"


def _aware(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def _check_expected(task: Task, expected: Optional[dt.datetime]) -> None:
    """Optional precondition (Oct 15): the client sends the `updated_at` it
    last saw; anything newer means someone else changed the task since."""
    if expected is not None and _aware(task.updated_at) != _aware(expected):
        raise TaskConflict(CHANGED_BY_SOMEONE, task.id)


def _transition(
    db: Session,
    task: Task,
    from_statuses: Iterable[str],
    values: dict,
    conflict: str,
    expected_updated_at: Optional[dt.datetime] = None,
) -> None:
    """Changes the task's status (and `values`) only if it's still in one of
    `from_statuses` - one conditional UPDATE, so of two people acting at the
    same moment exactly one succeeds; the other gets a 409. With
    `expected_updated_at`, also only if nobody changed it since. Leaves the
    change in the session's open transaction for the caller to commit."""
    conds = [Task.id == task.id, Task.status.in_(list(from_statuses))]
    if expected_updated_at is not None:
        conds.append(Task.updated_at == _aware(expected_updated_at))
    result = db.execute(
        update(Task).where(*conds).values(**values).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise TaskConflict(CHANGED_BY_SOMEONE if expected_updated_at is not None else conflict, task.id)
    for key, value in values.items():
        setattr(task, key, value)


def update_task(
    db: Session, user: User, task_id: int, changes: dict, background: Optional[BackgroundTasks] = None
) -> dict:
    """PATCH: title, description, due_at, priority, requires, assignee
    (assignee_user_id or assignee_team_id), or cancelled=True. Optional
    `expected_updated_at` (Oct 15). One conditional UPDATE: an edit never
    lands on a task someone finished a moment ago."""
    task = get_task_for(db, user, task_id)
    if not can_edit(user, task):
        raise HTTPException(status_code=403, detail="Only whoever created this task (or an admin) can change it")
    _require_open(task)
    changes = dict(changes)
    expected = changes.pop("expected_updated_at", None)
    _check_expected(task, expected)
    before_users = {task.assignee_user_id}
    before_team = task.assignee_team_id
    now = utcnow()

    if changes.get("cancelled"):
        _transition(
            db, task, ["open"], {"status": "cancelled", "updated_at": now},
            "This task isn't open any more", expected,
        )
        add_event(db, task, user, "cancelled")
        record_task_change(db, task, "updated")
        db.commit()
        return task_out(db, task, user, detail=True)

    values: Dict[str, Any] = {}
    edited: List[str] = []
    due_change = None
    if "title" in changes:
        values["title"] = _clean_user_text(changes["title"], "Title", MAX_TITLE, required=True)
        edited.append("title")
    if "description" in changes:
        values["description"] = _clean_user_text(changes["description"], "Description", MAX_TEXT)
        edited.append("description")
    if "due_at" in changes:
        values["due_at"] = normalize_due(changes["due_at"])
        edited.append("due_at")
        if _aware(values["due_at"]) != _aware(task.due_at):
            due_change = {"from": _iso(task.due_at), "to": _iso(values["due_at"])}
    if "priority" in changes and changes["priority"] is not None:
        values["priority"] = _check_choice(changes["priority"], PRIORITIES, "priority")
        edited.append("priority")
    if "requires" in changes and changes["requires"] is not None:
        values["requires"] = _check_choice(changes["requires"], REQUIRES, "requires")
        edited.append("requires")

    reassigned = None
    new_user = changes.get("assignee_user_id")
    new_team = changes.get("assignee_team_id")
    if new_user is not None and new_team is not None:
        raise HTTPException(status_code=400, detail="Assign to a person or a team, not both")
    if new_user is not None or new_team is not None:
        if (new_team is not None or new_user != user.id) and not is_assigner(user):
            raise HTTPException(status_code=403, detail="You can only assign tasks to yourself")
        if new_user is not None and new_user != task.assignee_user_id:
            _assignee_user(db, new_user)
            reassigned = {"from": _assignee_label(db, task), "to_user_id": new_user}
            values.update(assignee_user_id=new_user, assignee_team_id=None)
        elif new_team is not None and new_team != task.assignee_team_id:
            _assignee_team(db, new_team)
            reassigned = {"from": _assignee_label(db, task), "to_team_id": new_team}
            values.update(assignee_user_id=None, assignee_team_id=new_team)
    if reassigned:
        # Someone new: back to "To do" on the board.
        values.update(started_at=None, started_by_user_id=None, started_by_name=None)

    values["updated_at"] = now
    _transition(db, task, ["open"], values, "This task isn't open any more", expected)
    if edited:
        detail: Dict[str, Any] = {"fields": edited}
        if due_change:
            detail["due"] = due_change
        add_event(db, task, user, "edited", detail)
    if reassigned:
        reassigned["to"] = _assignee_label(db, task)
        add_event(db, task, user, "reassigned", reassigned)
        # The old assignee's list must drop it, too.
        if before_team is not None and before_team != task.assignee_team_id:
            record_change(db, "task", task.id, "updated", team_id=before_team)
    record_task_change(db, task, "updated", extra_user_ids=before_users)
    db.commit()
    if reassigned:
        after_response(background, db, push_assigned, task.id, user.id)
    return task_out(db, task, user, detail=True)


def _assignee_label(db: Session, task: Task) -> str:
    return _assignee_out(task, _Names(db))["name"]


def complete_task(
    db: Session,
    user: User,
    task_id: int,
    note: Optional[str] = None,
    photo: Optional[UploadFile] = None,
    photo_token: Optional[str] = None,
    background: Optional[BackgroundTasks] = None,
) -> dict:
    task = get_task_for(db, user, task_id)
    if not can_act(db, user, task):
        raise HTTPException(status_code=403, detail="This task isn't assigned to you")
    _require_open(task)
    note = _clean_user_text(note, "Note", MAX_TEXT)
    has_photo = photo is not None or bool(photo_token)
    if task.requires == "photo" and not has_photo:
        raise HTTPException(status_code=400, detail="This task needs a photo to be marked done")
    if task.requires == "note" and not note:
        raise HTTPException(status_code=400, detail="This task needs a note to be marked done")

    if photo is not None:
        uploaded = media_svc.save_upload(db, user, photo)
        photo_token = uploaded.token

    now = utcnow()
    # First mark it done (atomically - see _transition), then attach the
    # photo in the same transaction: a loser of the race never attaches its
    # photo to someone else's completion.
    _transition(
        db,
        task,
        ["open"],
        {
            "status": "done",
            "completed_by_user_id": user.id,
            "completed_by_name": user.username,
            "completed_at": now,
            "completion_note": note,
            "updated_at": now,
        },
        "Someone else already finished this task",
    )
    media = media_svc.attach_media(db, user, photo_token, "task", task.id) if photo_token else None
    task.completion_media_id = media.id if media else None
    add_event(db, task, user, "done", {"note": bool(note), "photo": media is not None})
    record_task_change(db, task, "updated", extra_user_ids=[user.id])
    db.commit()
    after_response(background, db, push_done, task.id, user.id)
    return task_out(db, task, user, detail=True)


def cant_do_task(
    db: Session, user: User, task_id: int, reason: str, background: Optional[BackgroundTasks] = None
) -> dict:
    task = get_task_for(db, user, task_id)
    if not can_act(db, user, task):
        raise HTTPException(status_code=403, detail="This task isn't assigned to you")
    _require_open(task)
    reason = _clean_user_text(reason, "Reason", 500, required=True)
    now = utcnow()
    _transition(
        db,
        task,
        ["open"],
        {
            "status": "cant_do",
            "cant_do_reason": reason,
            "completed_by_user_id": user.id,
            "completed_by_name": user.username,
            "completed_at": now,
            "updated_at": now,
        },
        "Someone else already closed this task",
    )
    add_event(db, task, user, "cant_do", {"reason": reason})
    record_task_change(db, task, "updated", extra_user_ids=[user.id])
    db.commit()
    after_response(background, db, push_cant_do, task.id, user.id)
    return task_out(db, task, user, detail=True)


def reopen_task(db: Session, user: User, task_id: int) -> dict:
    task = get_task_for(db, user, task_id)
    if task.status == "open":
        raise TaskConflict("This task is already open", task.id)
    if not can_reopen(user, task):
        raise HTTPException(status_code=403, detail="You can't reopen this task")
    previous = task.status
    # Only from the status the caller saw: two reopens (or a reopen racing a
    # second close) can't both apply. The photo stays in the history (event
    # + media row), just no longer the task's proof.
    _transition(
        db,
        task,
        [previous],
        {
            "status": "open",
            "completed_by_user_id": None,
            "completed_by_name": None,
            "completed_at": None,
            "completion_note": None,
            "completion_media_id": None,
            "cant_do_reason": None,
            "started_at": None,
            "started_by_user_id": None,
            "started_by_name": None,
            "updated_at": utcnow(),
        },
        "This task was changed by someone else - reload it",
    )
    add_event(db, task, user, "reopened", {"from": previous})
    record_task_change(db, task, "updated")
    db.commit()
    return task_out(db, task, user, detail=True)


def start_task(db: Session, user: User, task_id: int, started: bool) -> dict:
    """W5 board: move an open task to "In progress" (started=True) or back
    to "To do". Whoever can complete it can move it. Only while it's open -
    one conditional UPDATE, like every status change."""
    task = get_task_for(db, user, task_id)
    if not can_act(db, user, task):
        raise HTTPException(status_code=403, detail="This task isn't assigned to you")
    _require_open(task)
    if started == (task.started_at is not None):
        return task_out(db, task, user, detail=True)
    now = utcnow()
    values = (
        {"started_at": now, "started_by_user_id": user.id, "started_by_name": user.username, "updated_at": now}
        if started
        else {"started_at": None, "started_by_user_id": None, "started_by_name": None, "updated_at": now}
    )
    _transition(db, task, ["open"], values, "This task isn't open any more")
    add_event(db, task, user, "started" if started else "unstarted")
    record_task_change(db, task, "updated", extra_user_ids=[user.id])
    db.commit()
    return task_out(db, task, user, detail=True)


def comment_task(
    db: Session, user: User, task_id: int, body: str, background: Optional[BackgroundTasks] = None
) -> dict:
    task = get_task_for(db, user, task_id)
    body = _clean_user_text(body, "Comment", MAX_TEXT, required=True)
    task.updated_at = utcnow()
    add_event(db, task, user, "commented", {"body": body})
    record_task_change(db, task, "updated")
    db.commit()
    event_id = db.execute(
        select(func.max(TaskEvent.id)).where(TaskEvent.task_id == task.id, TaskEvent.kind == "commented")
    ).scalar()
    after_response(background, db, push_comment, task.id, user.id, event_id)
    return task_out(db, task, user, detail=True)


# --- Steps (task items) --------------------------------------------------------------


def _item_of(db: Session, task: Task, item_id: int) -> TaskItem:
    item = db.get(TaskItem, item_id)
    if item is None or item.task_id != task.id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


def _require_item_editor(db: Session, user: User, task_id: int) -> Task:
    task = get_task_for(db, user, task_id)
    if not can_edit(user, task):
        raise HTTPException(status_code=403, detail="Only whoever created this task (or an admin) can change its steps")
    _require_open(task)
    return task


def _touch_open(db: Session, task: Task, expected: Optional[dt.datetime] = None) -> None:
    """Bumps updated_at only while the task is still open (conditional
    UPDATE, which in SQLite also takes the write lock first) - so a step
    change can't land on a task someone just finished."""
    _transition(db, task, ["open"], {"updated_at": utcnow()}, "This task isn't open any more", expected)


def _item_text(value: Any) -> str:
    text = content_filter.clean(_clean_text(value if isinstance(value, str) else None, "Step", MAX_TITLE, required=True))
    return text


def set_item(
    db: Session,
    user: User,
    task_id: int,
    item_id: int,
    done: Optional[bool] = None,
    text: Optional[str] = None,
) -> dict:
    """Tick / untick a step (whoever can complete the task) and/or rename it
    (whoever can edit the task). Only while the task is open; a tick is in
    the history (item_done / item_undone, Oct 15)."""
    task = get_task_for(db, user, task_id)
    if done is None and text is None:
        raise HTTPException(status_code=400, detail="Send done and/or text")
    if done is not None and not can_act(db, user, task):
        raise HTTPException(status_code=403, detail="This task isn't assigned to you")
    if text is not None and not can_edit(user, task):
        raise HTTPException(status_code=403, detail="Only whoever created this task (or an admin) can change its steps")
    if text is not None and is_demo_user(user):
        # Renaming is new (Oct 15) and the demo stays view-only for it.
        from app.demo_guard import DEMO_READ_ONLY

        raise HTTPException(status_code=403, detail=DEMO_READ_ONLY)
    _require_open(task)
    item = _item_of(db, task, item_id)
    new_text = _item_text(text) if text is not None else None
    _touch_open(db, task)
    if done is not None and bool(done) != bool(item.done):
        item.done = bool(done)
        item.done_by_user_id = user.id if done else None
        item.done_at = utcnow() if done else None
        add_event(db, task, user, "item_done" if done else "item_undone", {"item_id": item.id, "text": item.text})
    if new_text is not None and new_text != item.text:
        add_event(db, task, user, "items_edited", {"renamed": [{"id": item.id, "from": item.text, "to": new_text}]})
        item.text = new_text
    record_task_change(db, task, "updated")
    db.commit()
    return task_out(db, task, user, detail=True)


def add_item(db: Session, user: User, task_id: int, text: str) -> dict:
    task = _require_item_editor(db, user, task_id)
    new_text = _item_text(text)
    count = db.execute(select(func.count(TaskItem.id)).where(TaskItem.task_id == task.id)).scalar() or 0
    if count >= MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"Up to {MAX_ITEMS} steps")
    last = db.execute(select(func.max(TaskItem.position)).where(TaskItem.task_id == task.id)).scalar()
    _touch_open(db, task)
    item = TaskItem(task_id=task.id, position=(last + 1) if last is not None else 0, text=new_text, done=False)
    db.add(item)
    db.flush()
    add_event(db, task, user, "items_edited", {"added": [{"id": item.id, "text": new_text}]})
    record_task_change(db, task, "updated")
    db.commit()
    return task_out(db, task, user, detail=True)


def delete_item(db: Session, user: User, task_id: int, item_id: int) -> dict:
    task = _require_item_editor(db, user, task_id)
    item = _item_of(db, task, item_id)
    _touch_open(db, task)
    add_event(db, task, user, "items_edited", {"removed": [{"id": item.id, "text": item.text}]})
    db.delete(item)
    record_task_change(db, task, "updated")
    db.commit()
    return task_out(db, task, user, detail=True)


def replace_items(
    db: Session, user: User, task_id: int, items: Any, expected: Optional[dt.datetime] = None
) -> dict:
    """The whole step list in one call (an edit sheet's Save): [{"id"?,
    "text"}] in order. Rows with an id keep their tick; new rows are added;
    steps left out are removed. One history entry with what changed."""
    task = _require_item_editor(db, user, task_id)
    _check_expected(task, expected)
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"Up to {MAX_ITEMS} steps")
    existing = {
        i.id: i for i in db.execute(select(TaskItem).where(TaskItem.task_id == task.id)).scalars()
    }
    wanted: List[Tuple[Optional[int], str]] = []
    for row in items:
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail="Each step is {id?, text}")
        iid = row.get("id")
        if iid is not None and (type(iid) is not int or iid not in existing):
            raise HTTPException(status_code=400, detail="Unknown step id")
        text = (row.get("text") or "").strip() if isinstance(row.get("text"), str) else ""
        if not text:
            continue  # a blank row is just dropped, like on create
        wanted.append((iid, _item_text(text)))
    _touch_open(db, task, expected)
    added, renamed = [], []
    kept: Set[int] = set()
    for pos, (iid, text) in enumerate(wanted):
        if iid is None or iid in kept:
            item = TaskItem(task_id=task.id, position=pos, text=text, done=False)
            db.add(item)
            db.flush()
            added.append({"id": item.id, "text": text})
            continue
        item = existing[iid]
        kept.add(iid)
        if item.text != text:
            renamed.append({"id": iid, "from": item.text, "to": text})
            item.text = text
        item.position = pos
    removed = [{"id": i.id, "text": i.text} for iid, i in existing.items() if iid not in kept]
    for iid, item in existing.items():
        if iid not in kept:
            db.delete(item)
    detail = {k: v for k, v in (("added", added), ("renamed", renamed), ("removed", removed)) if v}
    if detail:
        add_event(db, task, user, "items_edited", detail)
    record_task_change(db, task, "updated")
    db.commit()
    return task_out(db, task, user, detail=True)


# --- Batches ---------------------------------------------------------------------------


def _batch_tasks_for(db: Session, user: User, batch_id: str) -> List[Task]:
    tasks = db.execute(select(Task).where(Task.assignment_batch_id == batch_id)).scalars().all()
    if not tasks or not all(can_edit(user, t) for t in tasks):
        raise HTTPException(status_code=404, detail="Assignment not found")
    return tasks


def get_batch(db: Session, user: User, batch_id: str) -> dict:
    _batch_tasks_for(db, user, batch_id)
    return batch_summary(db, batch_id)


def cancel_batch(db: Session, user: User, batch_id: str) -> dict:
    tasks = _batch_tasks_for(db, user, batch_id)
    now = utcnow()
    for task in tasks:
        if task.status != "open":
            continue
        # Conditional per task: one finished a moment ago stays finished.
        result = db.execute(
            update(Task)
            .where(Task.id == task.id, Task.status == "open")
            .values(status="cancelled", updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        task.status, task.updated_at = "cancelled", now
        add_event(db, task, user, "cancelled", {"batch": batch_id})
        record_task_change(db, task, "updated")
    db.commit()
    db.expire_all()
    return batch_summary(db, batch_id)


# --- Bulk Done / Cancel (Oct 15, admins) --------------------------------------------------

BULK_ACTIONS = ("done", "cancel")
MAX_BULK = 200


def bulk_action(db: Session, user: User, task_ids: Any, action: str, note: Optional[str] = None) -> dict:
    """Admins close many open tasks at once (the owner closed 38 one by
    one). Each task changes with its own conditional UPDATE, so one someone
    else finished meanwhile is skipped, never overwritten. "done" skips a
    task that needs a photo (and one that needs a note when no note is
    sent). Returns {"done": [ids], "skipped": [{"id", "reason"}], "tasks":
    [task_out of each changed one]}."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    _check_choice(action, BULK_ACTIONS, "action")
    if not isinstance(task_ids, list) or not task_ids or any(type(i) is not int for i in task_ids):
        raise HTTPException(status_code=400, detail="Send task_ids: a list of task ids")
    if len(task_ids) > MAX_BULK:
        raise HTTPException(status_code=400, detail=f"Up to {MAX_BULK} tasks at once")
    note = _clean_user_text(note, "Note", MAX_TEXT)
    changed: List[Task] = []
    skipped: List[dict] = []
    for tid in dict.fromkeys(task_ids):
        task = db.get(Task, tid)
        if task is None:
            skipped.append({"id": tid, "reason": "not found"})
            continue
        if task.status != "open":
            skipped.append({"id": tid, "reason": "not open"})
            continue
        if action == "done" and task.requires == "photo":
            skipped.append({"id": tid, "reason": "needs a photo"})
            continue
        if action == "done" and task.requires == "note" and not note:
            skipped.append({"id": tid, "reason": "needs a note"})
            continue
        now = utcnow()
        if action == "done":
            values = {
                "status": "done", "completed_by_user_id": user.id, "completed_by_name": user.username,
                "completed_at": now, "completion_note": note, "updated_at": now,
            }
        else:
            values = {"status": "cancelled", "updated_at": now}
        result = db.execute(
            update(Task)
            .where(Task.id == task.id, Task.status == "open")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            skipped.append({"id": tid, "reason": "not open"})
            continue
        for key, value in values.items():
            setattr(task, key, value)
        if action == "done":
            add_event(db, task, user, "done", {"note": bool(note), "photo": False, "bulk": True})
        else:
            add_event(db, task, user, "cancelled", {"bulk": True})
        record_task_change(db, task, "updated", extra_user_ids=[user.id])
        changed.append(task)
    db.commit()
    names = _Names(db)
    ctx = _ListContext(db, user, changed)
    return {
        "action": action,
        "done": [t.id for t in changed],
        "skipped": skipped,
        "tasks": [task_out(db, t, user, names, ctx=ctx) for t in changed],
    }


# --- "My team today" (Oct 15, admins and assigners) ----------------------------------------


def _today_bounds(now: dt.datetime) -> Tuple[dt.datetime, dt.datetime]:
    zone = store_zone()
    day = now.astimezone(zone).date()
    start = dt.datetime.combine(day, dt.time(0), tzinfo=zone)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=zone)
    return start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)


def team_today(db: Session, user: User, now: Optional[dt.datetime] = None) -> dict:
    """One strip above the task list: per person (and per team) how many
    open tasks, how many overdue, due today, and done today. Admins see
    everyone; an assigner sees the people in their own named teams (and
    themselves). Cheap: one query over open tasks + today's closed ones."""
    if not is_assigner(user):
        raise HTTPException(status_code=403, detail="Only people who can assign tasks can see this")
    now = now or utcnow()
    start, end = _today_bounds(now)
    rows = db.execute(
        select(Task).where(
            or_(
                Task.status == "open",
                and_(Task.status.in_(("done", "cant_do")), Task.completed_at >= start, Task.completed_at < end),
            )
        )
    ).scalars().all()
    scope: Optional[Set[int]] = None
    my_teams: Set[int] = set()
    if not user.is_admin:
        scope = teammate_user_ids(db, user.id)
        my_teams = user_team_ids(db, user)
    names = _Names(db)
    people: Dict[int, dict] = {}
    teams: Dict[int, dict] = {}

    def person(uid: int) -> dict:
        row = people.get(uid)
        if row is None:
            row = people[uid] = {
                "user_id": uid, "name": names.user(uid), "open": 0, "overdue": 0, "due_today": 0,
                "done_today": 0, "cant_do_today": 0,
            }
        return row

    for t in rows:
        if t.status == "open":
            overdue = t.due_at is not None and t.due_at < now
            due_today = t.due_at is not None and start <= t.due_at < end
            if t.assignee_user_id is not None:
                if scope is not None and t.assignee_user_id not in scope:
                    continue
                row = person(t.assignee_user_id)
            else:
                if scope is not None and t.assignee_team_id not in my_teams:
                    continue
                team = names.team(t.assignee_team_id)
                row = teams.setdefault(
                    t.assignee_team_id,
                    {"team_id": t.assignee_team_id, "name": team.name if team else "(deleted team)",
                     "color": team.color if team else None, "open": 0, "overdue": 0, "due_today": 0},
                )
            row["open"] += 1
            row["overdue"] += 1 if overdue else 0
            row["due_today"] += 1 if due_today else 0
        elif t.completed_by_user_id and t.completed_by_user_id != SYSTEM_USER_ID:
            if scope is not None and t.completed_by_user_id not in scope:
                continue
            row = person(t.completed_by_user_id)
            row["done_today" if t.status == "done" else "cant_do_today"] += 1
    people_out = sorted(people.values(), key=lambda r: (-(r["overdue"]), -(r["open"]), (r["name"] or "").lower()))
    return {
        "day": start.astimezone(store_zone()).date().isoformat(),
        "totals": {
            "open": sum(r["open"] for r in people_out) + sum(r["open"] for r in teams.values()),
            "overdue": sum(r["overdue"] for r in people_out) + sum(r["overdue"] for r in teams.values()),
            "done_today": sum(r["done_today"] for r in people_out),
        },
        "people": people_out,
        "teams": sorted(teams.values(), key=lambda r: r["name"].lower()),
    }


# --- Completion stats (W5, admins) -------------------------------------------------------

STATS_DAYS = (7, 14, 30, 90)


def task_stats(db: Session, days: int = 30, now: Optional[dt.datetime] = None) -> dict:
    """Per-person and per-team completion for tasks CREATED in the last
    `days` days (cancelled ones left out). A person's row: tasks assigned to
    them (and how many are done, open, overdue), plus everything they
    finished - their own and their teams' tasks - split into on time / late
    (vs. its due time; no due time = on time), can't-do, and the median
    time from created to done."""
    if days not in STATS_DAYS:
        raise HTTPException(status_code=400, detail=f"days must be one of {', '.join(map(str, STATS_DAYS))}")
    now = now or utcnow()
    since = now - dt.timedelta(days=days)
    tasks = db.execute(
        select(Task).where(Task.created_at >= since, Task.status != "cancelled")
    ).scalars().all()
    people: Dict[int, dict] = {}
    teams: Dict[int, dict] = {}
    durations: Dict[int, List[float]] = {}

    def person(uid: int, name: Optional[str]) -> dict:
        row = people.get(uid)
        if row is None:
            row = people[uid] = {
                "user_id": uid, "name": name or "(deleted user)", "assigned": 0, "assigned_done": 0,
                "open": 0, "overdue": 0, "done": 0, "on_time": 0, "late": 0, "cant_do": 0,
                "median_minutes_to_done": None, "completion_rate": None, "on_time_rate": None,
            }
        return row

    names = _Names(db)
    for t in tasks:
        overdue = t.status == "open" and t.due_at is not None and t.due_at < now
        if t.assignee_user_id is not None:
            row = person(t.assignee_user_id, names.user(t.assignee_user_id))
            row["assigned"] += 1
            row["assigned_done"] += 1 if t.status == "done" else 0
            row["open"] += 1 if t.status == "open" else 0
            row["overdue"] += 1 if overdue else 0
        else:
            team = names.team(t.assignee_team_id)
            trow = teams.setdefault(
                t.assignee_team_id,
                {"team_id": t.assignee_team_id, "name": team.name if team else "(deleted team)",
                 "color": team.color if team else None, "total": 0, "done": 0, "open": 0, "overdue": 0, "cant_do": 0},
            )
            trow["total"] += 1
            trow["done"] += 1 if t.status == "done" else 0
            trow["open"] += 1 if t.status == "open" else 0
            trow["overdue"] += 1 if overdue else 0
            trow["cant_do"] += 1 if t.status == "cant_do" else 0
        if t.completed_by_user_id and t.completed_by_user_id != SYSTEM_USER_ID and t.status in ("done", "cant_do"):
            row = person(t.completed_by_user_id, t.completed_by_name)
            if t.status == "cant_do":
                row["cant_do"] += 1
                continue
            row["done"] += 1
            late = t.due_at is not None and t.completed_at is not None and t.completed_at > t.due_at
            row["late" if late else "on_time"] += 1
            if t.completed_at is not None and t.created_at is not None:
                durations.setdefault(t.completed_by_user_id, []).append(
                    max(0.0, (t.completed_at - t.created_at).total_seconds() / 60)
                )
    for uid, row in people.items():
        mins = sorted(durations.get(uid, []))
        if mins:
            mid = len(mins) // 2
            row["median_minutes_to_done"] = round(mins[mid] if len(mins) % 2 else (mins[mid - 1] + mins[mid]) / 2)
        if row["assigned"]:
            row["completion_rate"] = round(row["assigned_done"] / row["assigned"], 3)
        if row["done"]:
            row["on_time_rate"] = round(row["on_time"] / row["done"], 3)
    people_out = sorted(people.values(), key=lambda r: (-(r["done"] + r["assigned"]), r["name"].lower()))
    totals = {
        "tasks": len(tasks),
        "done": sum(1 for t in tasks if t.status == "done"),
        "open": sum(1 for t in tasks if t.status == "open"),
        "overdue": sum(1 for t in tasks if t.status == "open" and t.due_at is not None and t.due_at < now),
        "cant_do": sum(1 for t in tasks if t.status == "cant_do"),
    }
    return {
        "days": days,
        "since": _iso(since),
        "totals": totals,
        "people": people_out,
        "teams": sorted(teams.values(), key=lambda r: r["name"].lower()),
    }


# --- Templates ---------------------------------------------------------------------------


def _day_assignees_out(tpl: TaskTemplate, names: _Names) -> List[dict]:
    out = []
    by_day = recurrence.parse_day_assignees(tpl.day_assignees_json)
    for day in recurrence.WEEKDAY_CODES:
        if day not in by_day:
            continue
        uid, tid = by_day[day]
        if uid is not None:
            out.append({"day": day, "type": "user", "id": uid, "name": names.user(uid)})
        else:
            team = names.team(tid)
            out.append(
                {"day": day, "type": "team", "id": tid, "name": team.name if team else "(deleted team)",
                 "color": team.color if team else None}
            )
    return out


def template_out(db: Session, tpl: TaskTemplate, names: Optional[_Names] = None) -> dict:
    names = names or _Names(db)
    repeats = tpl.repeats is not False
    try:
        upcoming = (
            recurrence.preview(
                tpl.rrule, tpl.timezone, tpl.generate_ahead_minutes, utcnow(), count=1, anchor=tpl.created_at
            )
            if repeats
            else []
        )
    except recurrence.RRuleError:
        upcoming = []
    if upcoming and tpl.day_assignees_json:
        # Name who the next one is for (it may be a weekday's own assignee).
        due = dt.datetime.fromisoformat(upcoming[0]["due_at_utc"])
        uid, tid = recurrence.occurrence_assignee(tpl, due)
        upcoming[0]["assignee"] = _assignee_out(SimpleNamespace(assignee_user_id=uid, assignee_team_id=tid), names)
    return {
        "id": tpl.id,
        "title": tpl.title,
        "description": tpl.description,
        "requires": tpl.requires,
        "priority": tpl.priority,
        "assignee": _assignee_out(tpl, names),
        "assignee_user_id": tpl.assignee_user_id,
        "assignee_team_id": tpl.assignee_team_id,
        "rrule": tpl.rrule,
        "timezone": tpl.timezone,
        "generate_ahead_minutes": tpl.generate_ahead_minutes,
        "active": tpl.active,
        "next": upcoming[0] if upcoming else None,
        # Additive (W5): steps, weekday assignees, and whether it repeats.
        "items": recurrence.parse_items(tpl.items_json),
        "day_assignees": _day_assignees_out(tpl, names),
        "repeats": repeats,
        # Additive (Oct 15): the same words on the website and in the app.
        "schedule_text": schedule_text(tpl.rrule if repeats else ""),
        "created_by": {"id": tpl.created_by_user_id, "name": tpl.created_by_name},
        "created_at": _iso(tpl.created_at),
        "updated_at": _iso(tpl.updated_at),
    }


_DAY_NAMES = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


def _ordinal(n: int) -> str:
    if n < 0:
        return "last day" if n == -1 else f"{-n}th from last day"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def schedule_text(rule: Optional[str]) -> str:
    """"Every Mon, Wed at 3:00 PM" from a template's rule (the subset
    recurrence.normalize_rrule accepts); "Doesn't repeat" without one."""
    if not (rule or "").strip():
        return "Doesn't repeat"
    try:
        parts = dict(p.split("=", 1) for p in recurrence.normalize_rrule(rule).split(";"))
    except (recurrence.RRuleError, ValueError):
        return rule or ""
    times = []
    for h in sorted(int(x) for x in parts["BYHOUR"].split(",")):
        for m in sorted(int(x) for x in parts.get("BYMINUTE", "0").split(",")):
            times.append(dt.time(h, m).strftime("%-I:%M %p"))
    at = " at " + ", ".join(times)
    every = int(parts.get("INTERVAL", "1"))
    days = [d for d in parts.get("BYDAY", "").split(",") if d]
    freq = parts["FREQ"]
    if freq == "DAILY":
        return ("Every day" if every == 1 else f"Every {every} days") + at
    if freq == "WEEKLY":
        if every == 1 and days == ["MO", "TU", "WE", "TH", "FR"]:
            return "Weekdays" + at
        if every == 1 and len(days) == 7:
            return "Every day" + at
        names = ", ".join(_DAY_NAMES[d] for d in days) if days else "week"
        return (f"Every {names}" if every == 1 else f"Every {every} weeks on {names}") + at
    month_days = [int(x) for x in parts.get("BYMONTHDAY", "").split(",") if x]
    which = " on the " + ", ".join(_ordinal(d) for d in month_days) if month_days else ""
    return ("Every month" if every == 1 else f"Every {every} months") + which + at


def get_template(db: Session, user: User, template_id: int) -> dict:
    """One schedule (Oct 15: the app's schedule screen opens one directly)."""
    if not is_assigner(user):
        raise HTTPException(status_code=403, detail="Only people who can assign tasks can see schedules")
    tpl = db.get(TaskTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return template_out(db, tpl)


def list_templates(db: Session, user: User) -> List[dict]:
    if not is_assigner(user):
        raise HTTPException(status_code=403, detail="Only people who can assign tasks can see schedules")
    rows = db.execute(
        select(TaskTemplate).order_by(TaskTemplate.active.desc(), TaskTemplate.title, TaskTemplate.id)
    ).scalars().all()
    names = _Names(db)
    return [template_out(db, t, names) for t in rows]


def _template_fields(db: Session, data: dict, tpl: Optional[TaskTemplate] = None) -> dict:
    out = {}
    if tpl is None or "title" in data:
        # Every occurrence copies these (recurrence.py), so they are filtered
        # here like a task's own text.
        out["title"] = _clean_user_text(data.get("title"), "Title", MAX_TITLE, required=True)
    if "description" in data:
        out["description"] = _clean_user_text(data.get("description"), "Description", MAX_TEXT)
    for field, allowed in (("requires", REQUIRES), ("priority", PRIORITIES)):
        if data.get(field) is not None:
            out[field] = _check_choice(data[field], allowed, field)
    repeats = data.get("repeats")
    if repeats is None:
        repeats = tpl.repeats is not False if tpl else True
    out["repeats"] = bool(repeats)
    rule = data.get("rrule") if "rrule" in data else (tpl.rrule if tpl else None)
    tz = data.get("timezone") if data.get("timezone") else (tpl.timezone if tpl else None)
    ahead = data.get("generate_ahead_minutes")
    if ahead is None:
        ahead = tpl.generate_ahead_minutes if tpl else 420
    if not repeats and not (rule or "").strip():
        # A saved template only used by "Create task now": no schedule.
        try:
            recurrence.get_zone(tz)
        except recurrence.RRuleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        out.update(rrule="", timezone=tz or recurrence.default_timezone(), generate_ahead_minutes=int(ahead))
    else:
        try:
            rule, tz = recurrence.validate_template_fields(rule, tz, int(ahead))
        except recurrence.RRuleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        out.update(rrule=rule, timezone=tz, generate_ahead_minutes=int(ahead))
    if "items" in data and data["items"] is not None:
        out["items_json"] = json.dumps(_clean_items(data["items"])) or None
    if "day_assignees" in data and data["day_assignees"] is not None:
        out["day_assignees_json"] = _clean_day_assignees(db, data["day_assignees"])
    new_user = data.get("assignee_user_id")
    new_team = data.get("assignee_team_id")
    if new_user is not None and new_team is not None:
        raise HTTPException(status_code=400, detail="Assign to a person or a team, not both")
    if new_user is not None:
        _assignee_user(db, new_user)
        out.update(assignee_user_id=new_user, assignee_team_id=None)
    elif new_team is not None:
        _assignee_team(db, new_team)
        out.update(assignee_user_id=None, assignee_team_id=new_team)
    elif tpl is None:
        raise HTTPException(status_code=400, detail="Choose who the task is for")
    if "active" in data and data["active"] is not None:
        out["active"] = bool(data["active"])
    return out


def _clean_items(items: Any) -> List[str]:
    """A task's / template's steps: trimmed, word-filtered, blanks dropped."""
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Steps must be a list")
    out = [content_filter.clean(t) for t in ((i if isinstance(i, str) else "").strip() for i in items) if t]
    if len(out) > MAX_ITEMS or any(len(i) > MAX_TITLE for i in out):
        raise HTTPException(status_code=400, detail=f"Up to {MAX_ITEMS} steps, {MAX_TITLE} characters each")
    return out


def _clean_day_assignees(db: Session, value: Any) -> Optional[str]:
    """{"MO": {"user_id": 3}, "SA": {"team_id": 2}} (or a list of {"day",
    "user_id"|"team_id"}) -> the stored JSON, each assignee checked like
    the template's own. Days left out use the template's assignee; {} or []
    clears them all."""
    if isinstance(value, list):
        pairs = []
        for row in value:
            if not isinstance(row, dict):
                raise HTTPException(status_code=400, detail="Bad weekday assignee")
            pairs.append((row.get("day"), row))
    elif isinstance(value, dict):
        pairs = list(value.items())
    else:
        raise HTTPException(status_code=400, detail="Bad weekday assignees")
    out: Dict[str, dict] = {}
    for day, who in pairs:
        if day not in recurrence.WEEKDAY_CODES or not isinstance(who, dict):
            raise HTTPException(status_code=400, detail="Weekdays are MO, TU, WE, TH, FR, SA, SU")
        uid, tid = who.get("user_id"), who.get("team_id")
        if (uid is None) == (tid is None) or (uid is not None and type(uid) is not int) or (
            tid is not None and type(tid) is not int
        ):
            raise HTTPException(status_code=400, detail=f"{day}: choose one person or one team")
        if uid is not None:
            _assignee_user(db, uid)
            out[day] = {"user_id": uid}
        else:
            _assignee_team(db, tid)
            out[day] = {"team_id": tid}
    return json.dumps({d: out[d] for d in recurrence.WEEKDAY_CODES if d in out}) if out else None


def create_task_from_template(
    db: Session, user: User, template_id: int, data: dict, background: Optional[BackgroundTasks] = None
) -> dict:
    """W5 "Create task now": one task (or one per person) from a template's
    title, description, requirements and steps - whether or not it repeats.
    The assignee is whoever's sent, else the template's own (its weekday's,
    for today). Same rules as any new task (only assigners give work to
    others). Linked by source_type "template", not template_id (that stays
    the recurring occurrence's key)."""
    tpl = db.get(TaskTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    user_ids = data.get("assignee_user_ids")
    team_id = data.get("assignee_team_id")
    if not user_ids and team_id is None:
        uid, tid = recurrence.occurrence_assignee(tpl, utcnow())
        user_ids, team_id = ([uid] if uid is not None else None), tid
    return create_tasks(
        db,
        user,
        title=tpl.title,
        description=tpl.description,
        assignee_user_ids=user_ids,
        assignee_team_id=team_id,
        due_at=data.get("due_at"),
        requires=tpl.requires,
        priority=data.get("priority") or tpl.priority,
        items=recurrence.parse_items(tpl.items_json),
        source_type="template",
        source_id=str(tpl.id),
        background=background,
    )


def _template_audience(db: Session, tpl: TaskTemplate) -> Set[int]:
    """Who gets a template's change-feed rows: its creator and everyone who
    can list templates (admins see every row anyway)."""
    assigners = set(db.execute(select(User.id).where(User.can_assign.is_(True))).scalars())
    return ({tpl.created_by_user_id} | assigners) - {SYSTEM_USER_ID}


def _clear_future_occurrences(db: Session, tpl: TaskTemplate, user: User) -> int:
    """After a rule change or deactivation: deletes the template's already
    generated occurrences that are still in the future and untouched (open,
    nothing but their "created" event, no sub-item ticked) - they'd
    otherwise stay on people's lists at the old times, next to the new
    ones. A deleted occurrence can be generated again (the UNIQUE on
    template+due is freed), so turning a schedule back on works. Anything
    someone already worked on is left alone."""
    now = utcnow()
    candidates = db.execute(
        select(Task).where(Task.template_id == tpl.id, Task.status == "open", Task.due_at > now)
    ).scalars().all()
    removed = 0
    for task in candidates:
        worked = db.execute(
            select(TaskEvent.id).where(TaskEvent.task_id == task.id, TaskEvent.kind != "created").limit(1)
        ).first()
        ticked = db.execute(
            select(TaskItem.id).where(TaskItem.task_id == task.id, TaskItem.done.is_(True)).limit(1)
        ).first()
        if worked or ticked:
            continue
        record_task_change(db, task, "deleted")
        db.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
        db.execute(delete(TaskItem).where(TaskItem.task_id == task.id))
        db.delete(task)
        removed += 1
    if removed:
        logger.info("Template %s changed by %s: removed %d future occurrence(s)", tpl.id, user.username, removed)
    return removed


def create_template(db: Session, user: User, data: dict) -> dict:
    now = utcnow()
    fields = {"requires": "none", "priority": "normal", "active": True}
    fields.update(_template_fields(db, data))
    tpl = TaskTemplate(
        created_by_user_id=user.id,
        created_by_name=user.username,
        created_at=now,
        updated_at=now,
        **fields,
    )
    db.add(tpl)
    db.flush()
    record_change(db, "task_template", tpl.id, "created", user_ids=_template_audience(db, tpl))
    db.commit()
    return template_out(db, tpl)


def update_template(db: Session, user: User, template_id: int, data: dict) -> dict:
    tpl = db.get(TaskTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    before = (tpl.rrule, tpl.timezone, tpl.active, tpl.repeats is not False)
    for key, value in _template_fields(db, data, tpl).items():
        setattr(tpl, key, value)
    tpl.updated_at = utcnow()
    if (tpl.rrule, tpl.timezone, tpl.active, tpl.repeats is not False) != before:
        _clear_future_occurrences(db, tpl, user)
    record_change(db, "task_template", tpl.id, "updated", user_ids=_template_audience(db, tpl))
    db.commit()
    return template_out(db, tpl)


def deactivate_template(db: Session, user: User, template_id: int) -> dict:
    tpl = db.get(TaskTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    tpl.active = False
    tpl.updated_at = utcnow()
    _clear_future_occurrences(db, tpl, user)
    record_change(db, "task_template", tpl.id, "updated", user_ids=_template_audience(db, tpl))
    db.commit()
    return template_out(db, tpl)


def preview_template(
    db: Session, rule: str, tz: Optional[str], ahead: int, template_id: Optional[int] = None
) -> List[dict]:
    """The next 5 occurrences. For an existing template (`template_id`) the
    rule is anchored at its creation, exactly like generation - otherwise
    INTERVAL=2 or a WEEKLY rule without BYDAY would preview other dates."""
    anchor = None
    if template_id is not None:
        tpl = db.get(TaskTemplate, template_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        anchor = tpl.created_at
    try:
        rule, tz = recurrence.validate_template_fields(rule, tz, ahead)
        return recurrence.preview(rule, tz, ahead, utcnow(), anchor=anchor)
    except recurrence.RRuleError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Notifications -----------------------------------------------------------------------


def assignee_user_ids(db: Session, task: Task) -> Set[int]:
    if task.assignee_user_id is not None:
        return {task.assignee_user_id}
    team = db.get(Team, task.assignee_team_id)
    return team_member_user_ids(db, team) if team else set()


def _quiet(now: dt.datetime) -> bool:
    return in_quiet_hours(now.astimezone(store_zone()))


def _due_phrase(task: Task) -> str:
    if task.due_at is None:
        return ""
    local = task.due_at.astimezone(store_zone())
    return " - due " + local.strftime("%a %-I:%M %p")


# Task pushes added Oct 15 - OFF unless the person turns them on (owner:
# fewer notifications; their catalog entries in services/notifications.py).
TASK_DONE = "task_done"
TASK_COMMENT = "task_comment"


def _log_unreachable(db: Session, key: str, dedupe: str) -> None:
    """Marks a push nobody can get as handled (recipient_count 0) so the
    5-minute sweep stops re-trying it (prod: 846 "has no recipients" log
    lines in two weeks, mostly the demo login's tasks). Only when pushes are
    really on - a dev run without Firebase never uses anything up (the same
    rule as dispatch_alert)."""
    if not push_svc.is_configured():
        return
    if notif.sent_dedupe_keys(db, [dedupe]):
        return
    db.add(NotificationLog(key=key, dedupe_key=dedupe, sent_at=utcnow(), recipient_count=0))
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - logged concurrently
        db.rollback()


def _send(db: Session, key: str, dedupe: str, user_ids: Iterable[int], title: str, body: str, task: Task) -> int:
    """One task push. Only to the people it would actually reach - a phone,
    access, and its switch on (notifications.deliverable_user_ids, with the
    switch alert_pref_key picks: task_due_soon for "Due in 30 minutes",
    fix_issues_task_due for a lead task's reminders); nobody left = logged
    as handled instead of re-tried every 5 minutes."""
    wanted = set(user_ids) - {SYSTEM_USER_ID}
    if not wanted or key not in notif.ALERTS_BY_KEY:
        return 0
    if notif.sent_dedupe_keys(db, [dedupe]):
        return 0  # already sent (or logged as nobody to send to)
    alert = Alert(key=key, dedupe_keys=[dedupe], title=title, body=body, data={"task_id": task.id})
    try:
        ids = sorted(notif.deliverable_user_ids(db, notif.alert_pref_key(db, alert), wanted))
    except Exception:  # noqa: BLE001 - a push problem must never fail the request
        logger.exception("Task push %s: recipients failed", dedupe)
        db.rollback()
        return 0
    if not ids:
        _log_unreachable(db, key, dedupe)
        return 0
    alert.user_ids = ids
    try:
        return dispatch_alert(db, alert)
    except Exception:  # noqa: BLE001 - a push problem must never fail the request
        logger.exception("Task push %s failed", dedupe)
        db.rollback()
        return 0


def _demo_scoped(db: Session, user_id: Optional[int], recipients: Set[int]) -> Set[int]:
    """When `user_id` (whoever acted on / created the task) is an App Review
    demo login, only its own teammates may hear about it - never anyone
    outside its teams (app/demo.py). Everyone else: unchanged."""
    actor = db.get(User, user_id) if user_id else None
    if is_demo_user(actor):
        return recipients & teammate_user_ids(db, actor.id)
    return recipients


def is_silent(task: Task) -> bool:
    """Automatic closing-checklist tasks never push on their own: the single
    9:30 PM closing-checklist alert already covers them (owner: one
    checklist notification, not one per checklist plus task pushes)."""
    return task.created_by_user_id == SYSTEM_USER_ID and task.source_type == "checklist"


def sends_reminders(task: Task) -> bool:
    """Due-soon / overdue pushes: never for a silent task. A Fix Issues
    lead's daily list sends them only to someone who turned on "Fix Issues
    task reminders" (fix_issues_task_due, off by default - owner Oct 15: the
    lead task's one push is its morning "New task")."""
    return not is_silent(task)


def notify_assigned(db: Session, task: Task, actor_id: Optional[int], now: Optional[dt.datetime] = None) -> int:
    """task_assigned to each assignee except whoever assigned it. One
    dedupe key per person, so a reassignment to someone new still reaches
    them. During quiet hours (non-urgent) it's left for the sweep to send
    at 7 AM. Serialized with the sweep (_ASSIGN_PUSH_LOCK) so the same
    push is never sent twice."""
    now = now or utcnow()
    if task.status != "open" or is_silent(task) or (_quiet(now) and task.priority != "urgent"):
        return 0
    title = "Urgent task" if task.priority == "urgent" else "New task"
    body = task.title + _due_phrase(task)
    reached = 0
    with _ASSIGN_PUSH_LOCK:
        recipients = assignee_user_ids(db, task) - {actor_id, SYSTEM_USER_ID}
        recipients = _demo_scoped(db, task.created_by_user_id, _demo_scoped(db, actor_id, recipients))
        for uid in sorted(recipients):
            reached += _send(db, "task_assigned", f"task_assigned:{task.id}:{uid}", [uid], title, body, task)
    return reached


def push_assigned(db: Session, task_id: int, actor_id: Optional[int]) -> int:
    task = db.get(Task, task_id)
    return notify_assigned(db, task, actor_id) if task is not None else 0


def push_cant_do(db: Session, task_id: int, actor_id: Optional[int]) -> int:
    task = db.get(Task, task_id)
    return notify_cant_do(db, task, actor_id) if task is not None else 0


def push_done(db: Session, task_id: int, actor_id: Optional[int], now: Optional[dt.datetime] = None) -> int:
    """task_done (Oct 15, OFF unless switched on): a task someone gave you
    was finished - to its creator, unless they finished it themselves. Not
    for automatic tasks (no human creator). Dropped in quiet hours."""
    task = db.get(Task, task_id)
    now = now or utcnow()
    if task is None or task.status != "done" or task.created_by_user_id == SYSTEM_USER_ID:
        return 0
    if _quiet(now):
        return 0
    recipients = _demo_scoped(db, actor_id, {task.created_by_user_id} - {actor_id})
    stamp = int(task.completed_at.timestamp()) if task.completed_at else 0
    return _send(
        db, TASK_DONE, f"task_done:{task.id}:{stamp}", recipients,
        "Task done", f"{task.completed_by_name}: {task.title}", task,
    )


def push_comment(
    db: Session, task_id: int, actor_id: Optional[int], event_id: Optional[int], now: Optional[dt.datetime] = None
) -> int:
    """task_comment (Oct 15, OFF unless switched on): a comment on a task
    you made or are assigned - to the creator and assignees, never the
    commenter. Dropped in quiet hours."""
    task = db.get(Task, task_id)
    event = db.get(TaskEvent, event_id) if event_id else None
    now = now or utcnow()
    if task is None or event is None or event.kind != "commented" or _quiet(now):
        return 0
    recipients = ({task.created_by_user_id} | assignee_user_ids(db, task)) - {actor_id, SYSTEM_USER_ID}
    recipients = _demo_scoped(db, task.created_by_user_id, _demo_scoped(db, actor_id, recipients))
    try:
        text = (json.loads(event.detail_json or "{}").get("body") or "").strip()
    except ValueError:
        text = ""
    if len(text) > 120:
        text = text[:119] + "…"
    return _send(
        db, TASK_COMMENT, f"task_comment:{event.id}", recipients,
        f"Comment: {task.title}", f"{event.user_name}: {text}" if text else event.user_name, task,
    )


def cant_do_recipients(db: Session, task: Task) -> Set[int]:
    """Who hears that a task can't be done: its creator - or, for an
    automatic task (no human creator), every admin plus whoever can assign
    in its team."""
    if task.created_by_user_id != SYSTEM_USER_ID:
        return {task.created_by_user_id}
    admins = set(db.execute(select(User.id).where(User.is_admin.is_(True))).scalars())
    leads: Set[int] = set()
    if task.assignee_team_id is not None:
        members = assignee_user_ids(db, task)
        if members:
            leads = set(
                db.execute(select(User.id).where(User.id.in_(members), User.can_assign.is_(True))).scalars()
            )
    return admins | leads


def notify_cant_do(db: Session, task: Task, actor_id: Optional[int], now: Optional[dt.datetime] = None) -> int:
    """Sent right away, or - during quiet hours for a normal task - by the
    morning sweep. The dedupe key carries when it was marked, so a task
    that's reopened and marked "can't do" again is reported again."""
    now = now or utcnow()
    if task.status != "cant_do" or task.completed_at is None:
        return 0
    if _quiet(now) and task.priority != "urgent":
        return 0
    # A demo login's actions only ever notify its own teammates.
    recipients = _demo_scoped(db, actor_id, cant_do_recipients(db, task) - {actor_id, SYSTEM_USER_ID})
    return _send(
        db,
        "task_cant_do",
        f"task_cant_do:{task.id}:{int(task.completed_at.timestamp())}",
        recipients,
        f"Can't do: {task.title}",
        f"{task.completed_by_name}: {task.cant_do_reason}",
        task,
    )


def sweep_cant_do(db: Session, now: dt.datetime) -> int:
    """Sends any "can't do" push held back by quiet hours (dedupe makes
    repeats no-ops)."""
    rows = db.execute(
        select(Task).where(Task.status == "cant_do", Task.updated_at >= now - SWEEP_WINDOW)
    ).scalars().all()
    return sum(notify_cant_do(db, t, t.completed_by_user_id, now=now) for t in rows)


def _last_assign_actor(db: Session, task: Task) -> Optional[int]:
    event = db.execute(
        select(TaskEvent)
        .where(TaskEvent.task_id == task.id, TaskEvent.kind.in_(("created", "reassigned")))
        .order_by(TaskEvent.id.desc())
        .limit(1)
    ).scalars().first()
    return event.user_id if event else None


def sweep_assigned(db: Session, now: dt.datetime) -> int:
    """Sends any task_assigned push not sent yet (quiet hours, generated
    occurrences) for open tasks touched in the last 12 h. dispatch_alert's
    dedupe makes repeats no-ops."""
    rows = db.execute(
        select(Task).where(Task.status == "open", Task.updated_at >= now - SWEEP_WINDOW)
    ).scalars().all()
    reached = 0
    for task in rows:
        reached += notify_assigned(db, task, _last_assign_actor(db, task), now=now)
    return reached


def send_reminders(db: Session, now: dt.datetime) -> int:
    """task_due: `soon` 30 min before due (to assignees; dropped if quiet
    hours swallow its window), `overdue` 1 h after (assignees + creator;
    waits for 7 AM). Urgent tasks ignore quiet hours. The dedupe key
    carries the due time, so moving a task's due date re-arms both."""
    quiet = _quiet(now)
    reached = 0
    soon = db.execute(
        select(Task).where(Task.status == "open", Task.due_at > now, Task.due_at <= now + SOON_BEFORE)
    ).scalars().all()
    for task in soon:
        if not sends_reminders(task) or (quiet and task.priority != "urgent"):
            continue
        # A task that appeared inside the window (e.g. created 10 min before
        # it's due) gets its assigned push instead.
        if task.created_at >= task.due_at - SOON_BEFORE:
            continue
        stamp = int(task.due_at.timestamp())
        # Oct 15: governed by its own switch, task_due_soon - OFF unless
        # turned on (notifications.alert_pref_key tells it by ":soon:").
        reached += _send(
            db, "task_due", f"task_due:{task.id}:soon:{stamp}",
            _demo_scoped(db, task.created_by_user_id, assignee_user_ids(db, task)),
            "Due in 30 minutes", task.title, task,
        )
    overdue = db.execute(
        select(Task).where(
            Task.status == "open",
            Task.due_at <= now - OVERDUE_AFTER,
            Task.due_at >= now - OVERDUE_WINDOW,
        )
    ).scalars().all()
    for task in overdue:
        if not sends_reminders(task):
            continue
        # An automatic task never pushes at night, urgent or not - the
        # alerts it comes from don't either.
        if quiet and (task.priority != "urgent" or task.created_by_user_id == SYSTEM_USER_ID):
            continue
        stamp = int(task.due_at.timestamp())
        recipients = (assignee_user_ids(db, task) | {task.created_by_user_id}) - {SYSTEM_USER_ID}
        recipients = _demo_scoped(db, task.created_by_user_id, recipients)
        reached += _send(
            db, "task_due", f"task_due:{task.id}:overdue:{stamp}", recipients,
            "Overdue task", task.title + _due_phrase(task), task,
        )
    return reached


def tasks_tick(db: Session, now: Optional[dt.datetime] = None) -> None:
    """The 5-minute job, in order: generate recurring occurrences, run the
    automatic-task rules, then send pending assigned / can't-do pushes and
    reminders. One job (not one per feature) so nothing here runs at the
    same moment as anything else here."""
    if not get_settings().team_tasks_enabled:
        return
    now = now or utcnow()
    try:
        recurrence.generate_occurrences(db, now)
    except Exception:  # noqa: BLE001
        logger.exception("Recurring task generation failed")
        db.rollback()
    # Imported here: auto_tasks imports this module.
    from app.team import auto_tasks

    auto_tasks.auto_tasks_tick(db, now)
    for step in (sweep_assigned, sweep_cant_do, send_reminders):
        try:
            step(db, now)
        except Exception:  # noqa: BLE001
            logger.exception("Task job step %s failed", step.__name__)
            db.rollback()


# --- Hooks ---------------------------------------------------------------------------------


def _tasks_due_badge(db: Session, user: User) -> int:
    """Open tasks for me or my teams due by the end of today (store time),
    overdue included."""
    if not user_has_feature(user, "team_tasks"):
        return 0
    zone = store_zone()
    today_end = dt.datetime.combine(
        utcnow().astimezone(zone).date() + dt.timedelta(days=1), dt.time(0), tzinfo=zone
    )
    teams = user_team_ids(db, user)
    conds = [Task.assignee_user_id == user.id]
    if teams:
        conds.append(Task.assignee_team_id.in_(teams))
    rows = db.execute(
        select(Task.id).where(
            Task.status == "open", or_(*conds), and_(Task.due_at.is_not(None), Task.due_at < today_end)
        )
    ).all()
    return len(rows)


def _media_access(db: Session, user: User, task_id: int) -> bool:
    return can_read(db, user, db.get(Task, task_id))


register_badge_provider("tasks_due", _tasks_due_badge)
media_svc.register_media_access("task", _media_access)
register_team_job(
    "tasks_tick",
    with_session(tasks_tick),
    lambda tz: IntervalTrigger(minutes=5, timezone=tz),
    misfire_grace_time=240,
)


def __getattr__(name):
    if name == "SYSTEM_NAME":
        return system_name()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
