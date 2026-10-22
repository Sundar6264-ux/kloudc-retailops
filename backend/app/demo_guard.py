"""Demo read-only guard (docs/APP_REVIEW_BUILD7_CONTRACT.md, "Demo read-only guard").

The App Review demo login (app/demo.py) may LOOK at everything its features
allow, but may only WRITE to things that are its own or sandboxed. This is
enforced in one place: `demo_write_guard` is an app-wide dependency
(main.py passes it to FastAPI(dependencies=...)), so every route - including
ones added later - runs it. For a demo session, every POST/PUT/PATCH/DELETE is
refused with 403 DEMO_READ_ONLY unless (method, route path) is in ALLOWLIST,
and an allowlisted route with an ownership check must also pass it.

New routes are therefore blocked for the demo login by default;
tests/test_demo_guard.py enumerates app.routes to keep it that way.

Task create / edit / reopen are allowed only inside the demo's sandbox: its
own tasks and those of the named teams it belongs to (never Everyone, never
another person). Create and edit look at the JSON body (who it's assigned
to), so those checks are marked `needs_body`; the guard reads the body (already
cached by FastAPI for these routes) only for a demo session.
"""

import json
from typing import Any, Callable, Dict, Optional, Set, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import SESSION_COOKIE_NAME, extract_bearer_token, lookup_session
from app.db import get_optional_db
from app.demo import is_demo_user
from app.models import InvoiceSubmission, User

DEMO_READ_ONLY = "The demo account can look around but can't change the store's real data."
DEMO_READ_ONLY_CODE = "demo_read_only"


def demo_read_only_error(detail: str = DEMO_READ_ONLY) -> HTTPException:
    """The 403 every demo refusal answers: the old ``detail`` plus
    ``{"error": "demo_read_only", "message"}`` (app/main.py renders it), so
    a client can tell a demo refusal from a missing permission without
    matching words (pre-production gaps G15 / G21)."""
    exc = HTTPException(status_code=403, detail=detail)
    exc.error_code = DEMO_READ_ONLY_CODE  # type: ignore[attr-defined]
    return exc

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# check(request, db, user) -> bool; a check with `needs_body = True` also
# gets the parsed JSON body: check(request, db, user, body).
Check = Callable[..., bool]


def _int_param(request: Request, name: str) -> Optional[int]:
    try:
        return int(request.path_params.get(name))
    except (TypeError, ValueError):
        return None


def own_invoice(request: Request, db: Session, user: User) -> bool:
    """Only an invoice this demo user submitted itself (always sandboxed)."""
    sid = _int_param(request, "submission_id")
    entry = db.get(InvoiceSubmission, sid) if sid is not None else None
    return entry is not None and entry.submitted_by_user_id == user.id


def own_message(request: Request, db: Session, user: User) -> bool:
    from app.team.chat_models import Message

    mid = _int_param(request, "message_id")
    msg = db.get(Message, mid) if mid is not None else None
    return msg is not None and msg.sender_user_id == user.id


def assigned_task(request: Request, db: Session, user: User) -> bool:
    """A task assigned to the demo user, or to a team it belongs to (never
    the admin "can act on anything" bypass)."""
    from app.team import tasks_service
    from app.team.tasks_models import Task

    tid = _int_param(request, "task_id")
    task = db.get(Task, tid) if tid is not None else None
    return task is not None and tasks_service.is_assignee(db, user, task)


def _own_team_ids(db: Session, user: User) -> Set[int]:
    """The named, live teams the demo user is a member of (e.g. Review) -
    never the Everyone team, never an archived one."""
    from app.team.models import Team, TeamMember

    return set(
        db.execute(
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == user.id, Team.is_everyone.is_(False), Team.archived_at.is_(None))
        ).scalars()
    )


def _is_id(value: Any) -> bool:
    # Exact ints only: a "5" or a true would be coerced by the route, so
    # anything but a plain id is refused rather than guessed at.
    return type(value) is int


def _inside_sandbox(db: Session, user: User, user_id: Any, team_id: Any) -> bool:
    """An assignee (one person or one team) the demo may give work to: itself
    or one of its own teams."""
    if user_id is not None:
        return _is_id(user_id) and user_id == user.id
    return _is_id(team_id) and team_id in _own_team_ids(db, user)


def _task(request: Request, db: Session):
    from app.team.tasks_models import Task

    tid = _int_param(request, "task_id")
    return db.get(Task, tid) if tid is not None else None


def reopenable_task(request: Request, db: Session, user: User) -> bool:
    """Undo after completing: a task assigned to the demo user that it closed
    itself, or any task assigned to one of its own teams."""
    task = _task(request, db)
    if task is None:
        return False
    if task.assignee_user_id is not None:
        return task.assignee_user_id == user.id and task.completed_by_user_id == user.id
    return _inside_sandbox(db, user, None, task.assignee_team_id)


def _body_dict(body: Any) -> Optional[dict]:
    return body if isinstance(body, dict) else None


def create_in_sandbox(request: Request, db: Session, user: User, body: Any) -> bool:
    """New tasks only for itself or its own teams (no assignee = a personal
    to-do, which is itself)."""
    data = _body_dict(body)
    if data is None:
        return False
    user_ids = data.get("assignee_user_ids") or []
    team_id = data.get("assignee_team_id")
    if not isinstance(user_ids, list):
        return False
    if any(not _inside_sandbox(db, user, uid, None) for uid in user_ids):
        return False
    return team_id is None or _inside_sandbox(db, user, None, team_id)


create_in_sandbox.needs_body = True  # type: ignore[attr-defined]


def edit_own_task(request: Request, db: Session, user: User, body: Any) -> bool:
    """Edit / cancel a task the demo user created itself, still assigned
    inside its sandbox, and only reassigned within it."""
    task = _task(request, db)
    data = _body_dict(body)
    if task is None or data is None or task.created_by_user_id != user.id:
        return False
    if not _inside_sandbox(db, user, task.assignee_user_id, task.assignee_team_id):
        return False
    new_user, new_team = data.get("assignee_user_id"), data.get("assignee_team_id")
    if new_user is not None and not _inside_sandbox(db, user, new_user, None):
        return False
    return new_team is None or _inside_sandbox(db, user, None, new_team)


edit_own_task.needs_body = True  # type: ignore[attr-defined]


# (method, route path template) -> None (always allowed) or an ownership check.
# Everything not listed here is refused for a demo session.
ALLOWLIST: Dict[Tuple[str, str], Optional[Check]] = {
    # --- Auth / own account / own devices
    ("POST", "/api/auth/login"): None,
    ("POST", "/api/auth/mobile-login"): None,
    ("POST", "/api/auth/logout"): None,
    # Already refused by the route with its own message ("The demo account can't be deleted.").
    ("POST", "/api/auth/delete-account"): None,
    ("POST", "/api/notifications/devices"): None,
    ("DELETE", "/api/notifications/devices"): None,
    ("POST", "/api/notifications/unregister"): None,
    ("PUT", "/api/notifications/prefs"): None,
    ("POST", "/api/notifications/test"): None,  # a test push to the caller's own phones
    # --- Label printing: opening the label site is a look; it refuses a demo's prints itself
    # (docs/LABEL_PRINTING_FEATURE.md D5, can_print false). The request routes stay refused.
    ("POST", "/api/labels/handoff"): None,
    # --- Invoices: submitting is sandboxed (never sent); actions only on its own submissions
    ("POST", "/api/invoices"): None,
    ("POST", "/api/invoices/{submission_id}/resend"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/mark-toast-closed"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/unmark-toast-closed"): own_invoice,
    # Multi-store MT6: POS-neutral aliases of the two routes above, same check.
    ("POST", "/api/invoices/{submission_id}/mark-ap-closed"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/unmark-ap-closed"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/request-delete"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/cancel-delete-request"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/override-status"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/approve-delete"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/delete"): own_invoice,
    ("POST", "/api/invoices/{submission_id}/restore"): own_invoice,
    ("DELETE", "/api/invoices/{submission_id}/permanent"): own_invoice,
    # --- Checklists: submissions are sandboxed (no WhatsApp, hidden from staff)
    ("POST", "/api/checklists/{checklist_type}/submit"): None,
    ("POST", "/api/checklists/media"): None,
    # --- Ask bot / weekly digest: canned answers, no Anthropic call
    ("POST", "/api/ask"): None,
    ("POST", "/api/weekly-digest/generate"): None,
    # --- Team: rules, chat (already scoped to its teams), reports, blocks
    ("POST", "/api/team/rules/accept"): None,
    ("POST", "/api/team/media"): None,  # a pending upload; only attached by an allowed action
    ("POST", "/api/team/announcements/{announcement_id}/seen"): None,
    ("POST", "/api/team/announcements/{announcement_id}/ack"): None,
    ("POST", "/api/team/channels/dm"): None,
    ("POST", "/api/team/channels/{channel_id}/messages"): None,
    ("POST", "/api/team/channels/{channel_id}/read"): None,
    ("PATCH", "/api/team/channels/{channel_id}/me"): None,  # own mute / hide
    ("PUT", "/api/team/chat/settings"): None,  # own "hide previews"
    ("DELETE", "/api/team/messages/{message_id}"): own_message,
    ("POST", "/api/team/messages/{message_id}/report"): None,
    ("POST", "/api/team/blocks"): None,
    ("DELETE", "/api/team/blocks/{user_id}"): None,
    ("POST", "/api/team/tasks/{task_id}/report"): None,
    ("POST", "/api/team/tasks/{task_id}/comments/{comment_id}/report"): None,
    # Task actions only on tasks assigned to it or its team.
    ("POST", "/api/team/tasks/{task_id}/complete"): assigned_task,
    ("POST", "/api/team/tasks/{task_id}/cant-do"): assigned_task,
    ("POST", "/api/team/tasks/{task_id}/comments"): assigned_task,
    ("PATCH", "/api/team/tasks/{task_id}/items/{item_id}"): assigned_task,
    # Sandboxed task writes: create for itself / its own teams only, edit or
    # cancel its own, Undo (reopen) on its own or its team's tasks.
    ("POST", "/api/team/tasks"): create_in_sandbox,
    ("PATCH", "/api/team/tasks/{task_id}"): edit_own_task,
    ("POST", "/api/team/tasks/{task_id}/reopen"): reopenable_task,
    # W5: the board's To do / In progress move, on tasks assigned to it or
    # its team; pins in its own (already team-scoped) chats.
    ("POST", "/api/team/tasks/{task_id}/start"): assigned_task,
    ("POST", "/api/team/messages/{message_id}/pin"): None,
    ("DELETE", "/api/team/messages/{message_id}/pin"): None,
    # Read-only despite being a POST (computes the next dates of a schedule).
    ("POST", "/api/team/templates/preview"): None,
}


def _session_user(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE_NAME) or extract_bearer_token(request.headers.get("authorization"))
    if not token:
        return None
    session = lookup_session(db, token)  # store-bound, hashed tokens (PLATFORM_CONTRACT 4.2; B)
    if session is None:
        return None
    return db.get(User, session.user_id)


_PASS = object()
_REFUSE = object()


def _decide(request: Request, db: Session):
    """Sync part (DB work, run in the threadpool): _PASS, _REFUSE, or
    (check, user) for a check that needs the request body."""
    user = _session_user(request, db)
    if not is_demo_user(user):
        return _PASS
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    key = (request.method.upper(), path)
    if key not in ALLOWLIST:
        return _REFUSE
    check = ALLOWLIST[key]
    if check is None:
        return _PASS
    if getattr(check, "needs_body", False):
        return check, user
    return _PASS if check(request, db, user) else _REFUSE


async def demo_write_guard(request: Request, db: Optional[Session] = Depends(get_optional_db)) -> None:
    """App-wide dependency. A no-op for reads and for every non-demo caller
    (including logged-out ones - the route's own auth still applies). The
    DB work runs in the threadpool, as it did when this was a sync
    dependency; the body is only read for a demo session on a needs_body
    route."""
    if request.method.upper() in SAFE_METHODS or db is None:  # db None: no store (multi mode; B)
        return
    outcome = await run_in_threadpool(_decide, request, db)
    if outcome is _PASS:
        return
    if outcome is not _REFUSE:
        check, user = outcome
        try:
            raw = await request.body()
            body = json.loads(raw) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = None
        if await run_in_threadpool(check, request, db, user, body):
            return
    raise demo_read_only_error()
