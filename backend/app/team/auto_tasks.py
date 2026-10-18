"""Automatic tasks (design Phase 3): the signals the push alerts already watch
also become Team tasks, so the work is assigned, tracked and closed in one
place. They come IN ADDITION to the alerts (build plan decision 5) - nothing
here changes an existing alert.

Rules, each behind its own switch in settings (TEAM_AUTOTASK_*):
- checklist (OFF since Oct 15 - owner: the 9:30 PM closing-checklist push
  and Home's checklist card already cover it; 38 of these piled up unseen):
  a closing checklist not submitted by 9:30 PM -> a "Submit the ...
  closing checklist" task for the configured team, due in 30 minutes but
  never after quiet hours start (10 PM). Done automatically once that
  checklist is submitted; one still open after its store day (or any at
  all while the rule is off) is closed as "Missed" (cancelled) - never a
  task of an App Review demo team (expire_checklist_tasks).
- invoice (default off): an invoice that's sent but not yet closed in Toast
  (the same can_mark_toast_closed the invoice list uses) -> "Close ... in
  Toast". Done when it's marked closed; cancelled if the invoice is deleted.
- order (default off): a vendor order due today on the Vendor Calendar (the
  same events the order-due alert uses, skipped cycles excluded) with at
  least TEAM_AUTOTASK_ORDER_MIN_ITEMS items to order (vendors have minimum
  orders - 1-2 items is never an order) -> "Place ... order", due 5 PM.
  Done once a purchase order for that vendor is placed since its previous
  order day; cancelled if that delivery is skipped on the calendar (or the
  vendor is skipped this week on Critical), or as "Expired" once it's been
  open more than ORDER_CHECK_DAYS days.

Oct 15: the sources close their tasks right away too (source_sync.py, called
from the checklist / order / invoice / Fix Issues write paths) - the
5-minute tick stays as the safety net. Closing is one conditional UPDATE
(only while still open), so it never overwrites a person's done / can't do
written a moment before.

An automatic task is recognisable by its creator: created_by_user_id is
SYSTEM_USER_ID (0, never a real user - the column is NOT NULL and the task
code treats the creator as a user id) and created_by_name "Spice Town".
(source_type, source_id) is unique among automatic tasks - checked before
creating, and the rules run inside tasks_tick (one job, never concurrent) -
so every run is idempotent, and a task someone completed or cancelled is
never re-created. One an admin reopened is never auto-closed again.

WhatsApp fallback (TEAM_WHATSAPP_FALLBACK, default off): when a closing
checklist task is created and some member of its team has no phone
registered for pushes, one message goes to that location's checklist
WhatsApp group.
"""

import datetime as dt
import logging
from typing import Dict, List, Optional, Set

import json

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.demo import demo_user_ids, hide_demo_clause
from app.models import (
    ChecklistSubmission,
    CriticalVendorDismissal,
    DeviceToken,
    InvoiceSubmission,
    PlacedOrder,
    User,
    VendorOrderSkip,
    VendorSchedule,
)
from app.services import whapi_client
from app.services.checklist_schemas import short_name as checklist_short_name
from app.services.invoice_submission import can_mark_toast_closed
from app.services.checklist_schemas import closing_checklist_types
from app.services.checklist_schemas import whatsapp_group as checklist_whatsapp_group
from app.services.whatsapp_groups import resolve_chat_id as resolve_whatsapp_chat_id
from app.services.notifications import QUIET_HOURS_START
from app.services.vendor_calendar import get_vendor_calendar
from app.team.changes import team_member_user_ids
from app.team.models import Team, TeamMember
from app.team.tasks_models import Task, TaskEvent
from app.team.tasks_service import (
    SYSTEM_NAME,
    SYSTEM_USER_ID,
    add_event,
    notify_assigned,
    record_task_change,
)
from app.timeutil import restaurant_timezone, utcnow

logger = logging.getLogger(__name__)

CHECKLIST_AFTER = dt.time(21, 30)  # same time as the closing_checklist alert
CHECKLIST_DUE_IN = dt.timedelta(minutes=30)
ORDER_AFTER = dt.time(9, 0)  # same time as the order_due alert
ORDER_DUE_AT = dt.time(17, 0)
# Invoices to close: the same 7-day window as Home's Invoices tile.
INVOICE_WINDOW = dt.timedelta(days=7)
# Open order tasks are re-checked for a placed PO / a skip this long, then
# cancelled as expired (an order placed by phone never produces a PO).
ORDER_CHECK_DAYS = 7
MISSED_NOTE = "Missed - not submitted"
EXPIRED_NOTE = f"Expired - no order recorded within {ORDER_CHECK_DAYS} days"
# Prod's App Review demo tasks (on team "Review") - never closed by a rule,
# whatever they look like. (demo_protected also covers any task of a team a
# demo login is in.)
PROTECTED_TASK_IDS = frozenset({11, 12, 47})

# The Vendor Calendar is expensive to compute, so the order rule scans it
# once per store day (per process - a restart just rescans, harmlessly).
_order_scan_day: Optional[dt.date] = None


def _local(now: dt.datetime) -> dt.datetime:
    return now.astimezone(restaurant_timezone())


def _day_bounds(day: dt.date):
    tz = restaurant_timezone()
    return (
        dt.datetime.combine(day, dt.time.min, tz),
        dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tz),
    )


def _team(db: Session, name: str) -> Optional[Team]:
    team = db.execute(
        select(Team).where(func.lower(Team.name) == name.strip().lower(), Team.archived_at.is_(None))
    ).scalars().first()
    if team is None:
        logger.warning("Auto-tasks: team %r not found - rule skipped", name)
        return None
    if not team_member_user_ids(db, team):
        # Oct 15: a task nobody is in the team to see helps nobody.
        logger.warning("Auto-tasks: team %r has no members - rule skipped", name)
        return None
    return team


def demo_protected(db: Session, task: Task) -> bool:
    """A task no rule may close: prod's App Review tasks, and anything
    assigned to (or made by) a demo login or one of a demo login's teams."""
    if task.id in PROTECTED_TASK_IDS:
        return True
    demo = demo_user_ids(db)
    if not demo:
        return False
    if task.assignee_user_id in demo or task.created_by_user_id in demo:
        return True
    if task.assignee_team_id is None:
        return False
    return (
        db.execute(
            select(TeamMember.user_id).where(
                TeamMember.team_id == task.assignee_team_id, TeamMember.user_id.in_(demo)
            ).limit(1)
        ).first()
        is not None
    )


def _existing(db: Session, source_type: str, source_id: str) -> Optional[Task]:
    return db.execute(
        select(Task).where(
            Task.created_by_user_id == SYSTEM_USER_ID,
            Task.source_type == source_type,
            Task.source_id == source_id,
        )
    ).scalars().first()


def _open_auto(db: Session, source_type: str) -> List[Task]:
    return list(
        db.execute(
            select(Task).where(
                Task.created_by_user_id == SYSTEM_USER_ID,
                Task.source_type == source_type,
                Task.status == "open",
            )
        ).scalars()
    )


def _create(
    db: Session,
    team: Team,
    *,
    title: str,
    source_type: str,
    source_id: str,
    due_at: Optional[dt.datetime],
    priority: str = "normal",
    description: Optional[str] = None,
    now: dt.datetime,
) -> Optional[Task]:
    """One automatic task, unless this source already has one (whatever its
    status). Commits, then sends the assigned push."""
    if _existing(db, source_type, source_id) is not None:
        return None
    stamp = utcnow()
    task = Task(
        title=title,
        description=description,
        requires="none",
        priority=priority,
        assignee_team_id=team.id,
        due_at=due_at.astimezone(dt.timezone.utc) if due_at else None,
        status="open",
        source_type=source_type,
        source_id=source_id,
        created_by_user_id=SYSTEM_USER_ID,
        created_by_name=SYSTEM_NAME,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(task)
    db.flush()
    add_event(db, task, None, "created", {"auto": source_type}, system_name=SYSTEM_NAME)
    record_task_change(db, task, "created")
    db.commit()
    logger.info("Auto-task %s:%s -> %s (task %s)", source_type, source_id, team.name, task.id)
    notify_assigned(db, task, actor_id=None, now=now.astimezone(dt.timezone.utc))
    if source_type == "checklist":
        whatsapp_fallback(db, task, team)
    return task


def _reopened(db: Session, task: Task) -> bool:
    """Someone deliberately reopened it - the system never closes it again."""
    return (
        db.execute(
            select(TaskEvent.id).where(TaskEvent.task_id == task.id, TaskEvent.kind == "reopened").limit(1)
        ).first()
        is not None
    )


def _close(db: Session, task: Task, status: str, note: str) -> bool:
    """Closes an automatic task on the system's behalf once its signal
    clears ("done") or its subject disappears ("cancelled") - unless someone
    reopened it, which means they want it open. One conditional UPDATE
    (Oct 15): only while it's still open, so a person's done / can't do
    committed a moment earlier is never overwritten. True = closed now."""
    if _reopened(db, task):
        return False
    stamp = utcnow()
    values = {
        "status": status,
        "completed_by_user_id": None,
        "completed_by_name": SYSTEM_NAME,
        "completed_at": stamp,
        "completion_note": note,
        "updated_at": stamp,
    }
    result = db.execute(
        update(Task)
        .where(Task.id == task.id, Task.status == "open")
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    for key, value in values.items():
        setattr(task, key, value)
    add_event(db, task, None, status, {"auto": note}, system_name=SYSTEM_NAME)
    record_task_change(db, task, "updated")
    return True


# --- Rule 1: closing checklist ---------------------------------------------------------


def _submitted_checklists(db: Session, day: dt.date) -> Dict[str, str]:
    """checklist_type -> submitter name, for the given store day - bounded
    exactly like notification_triggers.closing_checklist_alerts."""
    start, end = _day_bounds(day)
    rows = db.execute(
        # Every type (not just today's closing set), so an open task still
        # closes if an admin has since switched its checklist's closing off.
        select(ChecklistSubmission.checklist_type, ChecklistSubmission.submitted_by_name).where(
            ChecklistSubmission.created_at >= start,
            ChecklistSubmission.created_at < end,
            # A demo login's submission never closes the real store's task.
            ChecklistSubmission.submitted_by_user_id.notin_(demo_user_ids(db)),
        )
    ).all()
    return {t: name for t, name in rows}


def short_checklist_name(checklist_type: str) -> str:
    """"Spice Town - Daily Closing Checklist" -> "Spice Town" (same idea as
    the app's shortChecklistName); a schema's own short_name wins
    ("Street Kitchen FSA", so it can't read the same as the chef's)."""
    return checklist_short_name(checklist_type)


def checklist_task_title(checklist_type: str) -> str:
    """"Submit the Spice Town closing checklist"; a short name that already
    says "closing" ("Store Closing") isn't doubled up."""
    name = short_checklist_name(checklist_type)
    if "closing" in name.lower():
        return f"Submit the {name} checklist"
    return f"Submit the {name} closing checklist"


def sync_checklist_tasks(db: Session, now: dt.datetime) -> int:
    """Closes open checklist tasks whose checklist has since been submitted
    (any day) - then expires the rest (expire_checklist_tasks). Commits.
    Returns how many closed."""
    closed = 0
    for task in _open_auto(db, "checklist"):
        ctype, _, day_s = (task.source_id or "").partition(":")
        try:
            day = dt.date.fromisoformat(day_s)
        except ValueError:
            continue
        by = _submitted_checklists(db, day).get(ctype)
        if by is not None and not demo_protected(db, task):
            closed += _close(db, task, "done", f"Checklist submitted by {by}")
    db.commit()
    return closed + expire_checklist_tasks(db, now)


def expire_checklist_tasks(db: Session, now: dt.datetime) -> int:
    """Owner (Oct 15): checklist tasks are no longer made, and none may pile
    up. Every one still open is closed as "Missed - not submitted"
    (cancelled) once its store day is over - or right away while the rule
    is off (the leftovers from before). Idempotent; never a demo team's
    task (demo_protected). Commits. Returns how many closed."""
    enabled = get_settings().team_autotask_checklist_enabled
    today = _local(now).date()
    closed = 0
    for task in _open_auto(db, "checklist"):
        _, _, day_s = (task.source_id or "").partition(":")
        try:
            day = dt.date.fromisoformat(day_s)
        except ValueError:
            day = None
        if enabled and day is not None and day >= today:
            continue  # tonight's, while the rule is on: still time
        if demo_protected(db, task):
            continue
        if _close(db, task, "cancelled", MISSED_NOTE):
            closed += 1
    db.commit()
    if closed:
        logger.info("Auto-tasks: %d closing-checklist task(s) closed as missed", closed)
    return closed


def run_checklist_rule(db: Session, now: dt.datetime) -> int:
    settings = get_settings()
    local = _local(now)

    # Close open ones whose checklist has since been submitted (any day),
    # and expire the ones left over.
    sync_checklist_tasks(db, now)

    # 9:30 PM until quiet hours start: never created (or pushed) at night.
    if (
        not settings.team_autotask_checklist_enabled
        or local.time() < CHECKLIST_AFTER
        or local.time() >= QUIET_HOURS_START
    ):
        return 0
    team = _team(db, settings.team_autotask_checklist_team)
    if team is None:
        return 0
    today = local.date()
    submitted = _submitted_checklists(db, today)
    # Due in 30 minutes, but no later than quiet hours start, so its overdue
    # reminder can't fall in the night.
    quiet_start = dt.datetime.combine(today, QUIET_HOURS_START, local.tzinfo)
    due_at = min(local + CHECKLIST_DUE_IN, quiet_start)
    created = 0
    for ctype in closing_checklist_types():
        if ctype in submitted:
            continue
        task = _create(
            db,
            team,
            title=checklist_task_title(ctype),
            description="It hasn't been submitted yet tonight.",
            source_type="checklist",
            source_id=f"{ctype}:{today.isoformat()}",
            due_at=due_at,
            priority="normal",
            now=now,
        )
        created += task is not None
    return created


# --- Rule 2: invoices to close in Toast --------------------------------------------------


def sync_invoice_tasks(db: Session, now: Optional[dt.datetime] = None) -> int:
    """Closes open invoice tasks whose invoice was closed in Toast (done)
    or deleted (cancelled). Commits. Returns how many closed."""
    closed = 0
    for task in _open_auto(db, "invoice"):
        if demo_protected(db, task):
            continue
        try:
            entry = db.get(InvoiceSubmission, int(task.source_id or ""))
        except ValueError:
            entry = None
        if entry is None or entry.deleted_at is not None:
            closed += _close(db, task, "cancelled", "Invoice deleted")
        elif entry.toast_closed_at is not None:
            by = entry.toast_closed_by_name
            closed += _close(db, task, "done", f"Closed in Toast by {by}" if by else "Closed in Toast")
    db.commit()
    return closed


def run_invoice_rule(db: Session, now: dt.datetime) -> int:
    settings = get_settings()
    sync_invoice_tasks(db, now)

    if not settings.team_autotask_invoice_enabled:
        return 0
    team = _team(db, settings.team_autotask_invoice_team)
    if team is None:
        return 0
    since = now.astimezone(dt.timezone.utc) - INVOICE_WINDOW
    entries = db.execute(
        select(InvoiceSubmission).where(
            InvoiceSubmission.created_at >= since,
            InvoiceSubmission.deleted_at.is_(None),
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),  # no auto task for a demo upload
        )
    ).scalars().all()
    created = 0
    for entry in entries:
        if not can_mark_toast_closed(entry):
            continue
        task = _create(
            db,
            team,
            title=f"Close the {entry.vendor} invoice in Toast",
            description="It was sent to Toast; enter it there, then mark it closed in the app.",
            source_type="invoice",
            source_id=str(entry.id),
            due_at=None,
            now=now,
        )
        created += task is not None
    return created


# --- Rule 3: vendor orders due today ----------------------------------------------------


def _order_window_start(db: Session, vendor: str, order_day: dt.date, delivery_day: dt.date) -> dt.date:
    """The first day a purchase order counts toward this cycle: the day
    after the vendor's previous order-due day (so a PO placed the evening
    before still counts), from its Vendor Calendar schedule. Without a
    schedule: the day before the order day."""
    fallback = order_day - dt.timedelta(days=1)
    schedule = db.execute(
        select(VendorSchedule).where(func.lower(VendorSchedule.vendor) == vendor.strip().lower())
    ).scalars().first()
    if schedule is None:
        return fallback
    try:
        weekdays = set(json.loads(schedule.delivery_weekdays or "[]"))
    except ValueError:
        return fallback
    for back in range(1, 29):
        prev_delivery = delivery_day - dt.timedelta(days=back)
        if prev_delivery.weekday() in weekdays:
            return prev_delivery - dt.timedelta(days=schedule.lead_days) + dt.timedelta(days=1)
    return fallback


def _placed_since(
    db: Session, vendor: str, first_day: dt.date, delivery_day: Optional[dt.date] = None
) -> bool:
    """A confirmed ("Mark placed") order since `first_day`. A PO PDF preview
    ("pdf" row) isn't an order - it doesn't close or prevent the task.

    With `delivery_day` (the cycle's delivery), an order tied to one delivery
    ("Order already placed" from an order-due, `for_delivery_date`, W1)
    counts for exactly that delivery, whenever it was marked - as on the
    Vendor Calendar and in the 9 AM order-due push (Verify 1)."""
    start, _ = _day_bounds(first_day)
    when = PlacedOrder.placed_at >= start
    if delivery_day is not None:
        when = or_(
            and_(PlacedOrder.for_delivery_date.is_(None), when),
            PlacedOrder.for_delivery_date == delivery_day,
        )
    return (
        db.execute(
            select(PlacedOrder.id).where(
                func.lower(PlacedOrder.supplier) == vendor.strip().lower(),
                when,
                PlacedOrder.status == "placed",
            )
        ).first()
        is not None
    )


def _skipped(db: Session, vendor: str, delivery_day: dt.date) -> bool:
    return (
        db.execute(
            select(VendorOrderSkip.id).where(
                func.lower(VendorOrderSkip.vendor) == vendor.strip().lower(),
                VendorOrderSkip.delivery_date == delivery_day,
            )
        ).first()
        is not None
    )


def _parse_order_source(source_id: str):
    """"Vendor:2026-09-28:2026-09-30" -> (vendor, order day, delivery day).
    Older ids without the delivery day -> delivery day None."""
    head, _, last = source_id.rpartition(":")
    try:
        last_day = dt.date.fromisoformat(last)
    except ValueError:
        return None
    vendor, _, middle = head.rpartition(":")
    try:
        return vendor, dt.date.fromisoformat(middle), last_day
    except ValueError:
        return head, last_day, None


def _skipped_on_critical(db: Session, vendor: str, order_day: dt.date) -> bool:
    """"Skip this week" on the Critical tile for this vendor since its order
    day (and not taken back with Show again) - the same decision as a
    skipped delivery on the Vendor Calendar."""
    start, _ = _day_bounds(order_day)
    return (
        db.execute(
            select(CriticalVendorDismissal.id).where(
                func.lower(func.trim(CriticalVendorDismissal.vendor)) == vendor.strip().lower(),
                CriticalVendorDismissal.kind == "skip_week",
                CriticalVendorDismissal.created_at >= start,
                or_(
                    CriticalVendorDismissal.cleared_at.is_(None),
                    CriticalVendorDismissal.cleared_reason == "expired",
                ),
            ).limit(1)
        ).first()
        is not None
    )


def sync_order_tasks(db: Session, now: dt.datetime) -> int:
    """Closes open order tasks: done once a PO is placed for the cycle;
    cancelled when the delivery is skipped (Vendor Calendar, or Skip this
    week on Critical) or, after ORDER_CHECK_DAYS, as expired. Commits.
    Returns how many closed. Never needs the (slow) Vendor Calendar."""
    today = _local(now).date()
    closed = 0
    for task in _open_auto(db, "order_due"):
        parsed = _parse_order_source(task.source_id or "")
        if parsed is None or demo_protected(db, task):
            continue
        vendor, order_day, delivery_day = parsed
        if not vendor:
            continue
        if (today - order_day).days > ORDER_CHECK_DAYS:
            closed += _close(db, task, "cancelled", EXPIRED_NOTE)
            continue
        if delivery_day is not None and _skipped(db, vendor, delivery_day):
            closed += _close(db, task, "cancelled", "Order skipped on the Vendor Calendar")
            continue
        first_day = (
            _order_window_start(db, vendor, order_day, delivery_day) if delivery_day is not None else order_day
        )
        if _placed_since(db, vendor, first_day, delivery_day):
            closed += _close(db, task, "done", "Purchase order placed")
        elif _skipped_on_critical(db, vendor, order_day):
            closed += _close(db, task, "cancelled", "Skipped this week (Critical)")
    db.commit()
    return closed


def run_order_rule(db: Session, now: dt.datetime) -> int:
    global _order_scan_day
    settings = get_settings()
    local = _local(now)
    today = local.date()

    sync_order_tasks(db, now)

    if not settings.team_autotask_order_enabled or local.time() < ORDER_AFTER or _order_scan_day == today:
        return 0
    team = _team(db, settings.team_autotask_order_team)
    if team is None:
        return 0
    events = get_vendor_calendar(db, today, today)["events"]
    due_at = dt.datetime.combine(today, ORDER_DUE_AT, restaurant_timezone())
    min_items = settings.team_autotask_order_min_items
    created = 0
    for e in events:
        if e["type"] != "order_due" or e.get("skipped") or e["date"] != today.isoformat():
            continue
        count = e.get("items_needing_order") or 0
        if count < min_items:
            continue  # vendors have minimum orders - not a real order yet
        vendor = e["vendor"]
        delivery_day = dt.date.fromisoformat(e["for_delivery_date"])
        if _placed_since(db, vendor, _order_window_start(db, vendor, today, delivery_day), delivery_day):
            continue
        task = _create(
            db,
            team,
            title=f"Place the {vendor} order",
            description=f"{count} item(s) to order, per Supplier Projection.",
            source_type="order_due",
            source_id=f"{vendor}:{today.isoformat()}:{delivery_day.isoformat()}",
            due_at=due_at if due_at > local else None,
            now=now,
        )
        created += task is not None
    # Only once every vendor was handled: a failure above means the next
    # tick scans again (creation is idempotent).
    _order_scan_day = today
    return created


# --- WhatsApp fallback -------------------------------------------------------------------


def _group_for(task: Task) -> Optional[str]:
    """The location's checklist group, from the task's source."""
    if task.source_type == "checklist":
        # The checklist's own settings.whatsapp_group (checklist builder),
        # resolved through the whatsapp_groups table (env for the built-ins).
        ctype = (task.source_id or "").partition(":")[0]
        return resolve_whatsapp_chat_id(checklist_whatsapp_group(ctype)) or None
    return None


def whatsapp_fallback(db: Session, task: Task, team: Team) -> bool:
    """One WhatsApp message for an urgent automatic task when some team
    member can't get the push (no phone registered). Never raises."""
    settings = get_settings()
    if not settings.team_whatsapp_fallback:
        return False
    group = _group_for(task)
    if not group:
        return False
    # An App Review demo login in the team is never a reason to message the
    # real staff group, nor named in it (app/demo.py).
    members: Set[int] = team_member_user_ids(db, team) - demo_user_ids(db)
    if not members:
        return False
    with_device = set(
        db.execute(select(DeviceToken.user_id).where(DeviceToken.user_id.in_(members))).scalars()
    )
    missing = members - with_device
    if not missing:
        return False
    names = sorted(db.execute(select(User.username).where(User.id.in_(missing))).scalars())
    due = ""
    if task.due_at is not None:
        due = " - due " + _local(task.due_at).strftime("%-I:%M %p")
    text = (
        f"*Task for {team.name}:* {task.title}{due}\n"
        f"(Sent here because {', '.join(names)} can't get app notifications.)"
    )
    try:
        whapi_client.send_whatsapp_message(text, to=group)
    except Exception:  # noqa: BLE001 - a fallback must never break the job
        logger.exception("Auto-task WhatsApp fallback failed for task %s", task.id)
        return False
    return True


# --- Job ---------------------------------------------------------------------------------------


def auto_tasks_tick(db: Session, now: Optional[dt.datetime] = None) -> None:
    """Run from tasks_service.tasks_tick every 5 minutes (not a job of its
    own: sharing the one task job means its pushes can't race the assigned
    sweep). Each rule is isolated: one failing never stops the others."""
    if not get_settings().team_tasks_enabled:
        return
    now = now or dt.datetime.now(restaurant_timezone())
    # W4 (Oct 14): each Fix Issues lead's daily task (services/issue_leads.py).
    from app.services.issue_leads import run_issue_lead_rule

    for rule in (run_checklist_rule, run_invoice_rule, run_order_rule, run_issue_lead_rule):
        try:
            rule(db, now)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-task rule %s failed", rule.__name__)
            db.rollback()
