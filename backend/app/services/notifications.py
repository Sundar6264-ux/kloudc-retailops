"""Push notification plumbing: the alert catalog, per-user preferences,
device registration, and `dispatch_alert` - the one path every alert goes
through to reach phones. What each alert actually checks lives in
services/notification_triggers.py; how it's delivered lives in
services/push.py (Firebase).

Rules enforced here, once, so no trigger can get them wrong:
- Recipients are users who have access to the alert's feature(s) (the same
  app.features gating the pages use - an alert never points at a page the
  user can't open), haven't switched it off, and have a registered device.
- Each dedupe key is sent at most once, tracked in notification_log - and
  only written after a real Firebase send, so a dry run (Firebase not set
  up yet) never "uses up" an alert.
- Quiet hours (in_quiet_hours) are checked by the job runner, not here, so
  the test endpoint still works at night.
- Whether a user gets an alert (effective_enabled, Oct 15): their own
  on/off choice, else the admin's store default for their role, else the
  catalog's role default (ALERTS[...]["defaults"]).
"""

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.features import user_has_feature
from app.models import DeviceToken, NotificationLog, NotificationPref, User
from app.notification_models import PushDelivery
from app.services import push
from app.setting_models import SettingChange
from app.timeutil import utcnow
from app.tenancy import tenant_lock

logger = logging.getLogger(__name__)

# Groups the Notifications screens (app More > Notifications, website
# Notifications page) show the switches under, in this order.
GROUPS = [
    {"key": "needs_action", "label": "Needs action", "description": "Something for you to do now."},
    {"key": "team", "label": "Team", "description": "People talking to you: chat, @mentions, announcements."},
    {
        "key": "daily_summary",
        "label": "Daily summary",
        "description": "The 9 AM Morning summary, or any of its parts as a separate push.",
    },
    {"key": "admin", "label": "Admin", "description": "Store-level alerts for admins."},
]
GROUP_KEYS = [g["key"] for g in GROUPS]

# Defaults, owner "Recommended" (Oct 15): staff get the closing checklist,
# their own tasks, chat and announcements; admins get those plus ONE 9 AM
# Morning summary instead of ~6 separate 9 AM pushes, whose keys stay as
# opt-ins. What a user gets: their own choice (a notification_prefs row),
# else the admin's store default for their role (store_defaults), else
# `defaults` here. `essential`: the screens confirm before switching it off.
ROLES = ("staff", "admin")


def _on(staff: bool, admin: bool) -> Dict[str, bool]:
    return {"staff": staff, "admin": admin}


# Order matters: GET /api/notifications/prefs lists them grouped (GROUPS),
# in this order within a group.
# `features` are ALL required (e.g. an order-due alert opens Supplier
# Projection, so it needs that page as well as the calendar it's derived from).
ALERTS = [
    # Oct 15: one 9 AM push per person with a line for each 9 AM alert below
    # they can see and haven't turned on separately
    # (notification_triggers.run_morning_job). Old builds open Home for it.
    {
        "key": "morning_summary",
        "label": "Morning summary",
        "description": "One push at 9 AM with what needs attention today: critical vendors, orders due, loss watch, "
        "Fix Issues items back, inventory upload, yesterday's sales and open checks - only the parts you can open.",
        "features": [],
        "route": "home",
        "group": "daily_summary",
        "defaults": _on(False, True),
    },
    {
        "key": "order_due",
        "label": "Order due",
        "description": "A vendor order is due today or tomorrow (from the Vendor Calendar). Also a line in the "
        "Morning summary; turn this on for a separate push per vendor.",
        "features": ["vendor_calendar", "supplier_projection"],
        "route": "supplier_projection",
        "group": "daily_summary",
        "defaults": _on(False, False),
    },
    {
        "key": "critical_vendors",
        "label": "Critical vendors",
        "description": "Vendors with items about to run out, at 9 AM. Also a line in the Morning summary.",
        "features": ["reorder_candidates"],
        "route": "reorder",
        "group": "daily_summary",
        "defaults": _on(False, False),
    },
    {
        "key": "closing_checklist",
        "label": "Closing checklist",
        "description": "A closing checklist hasn't been submitted by 9:30 PM.",
        "features": ["daily_checklists"],
        "route": "checklists",
        "group": "needs_action",
        "defaults": _on(True, True),
        "essential": True,
    },
    # Oct 15: only to whoever submitted it, plus the admins.
    {
        "key": "invoice_failed",
        "label": "Invoice failed to send",
        "description": "An invoice you submitted couldn't be sent by WhatsApp or to Toast (admins: anyone's).",
        "features": ["invoice_submission"],
        "route": "invoice",
        "group": "needs_action",
        "defaults": _on(True, True),
        "essential": True,
    },
    # Owner (2026-09-28): open orders come as three summaries a day -
    # opening (9 AM), today so far (4 PM), day end (9:30 PM) - instead of a
    # push for every new order.
    # The key stays "open_orders_morning" so everyone's on/off choice carries
    # over; the per-order "open_orders_new" alert is retired.
    {
        "key": "open_orders_morning",
        "label": "Open checks (opening, 4 PM, day end)",
        "description": "Checks still open at 9 AM, new checks still open at 4 PM, and a day-end summary at 9:30 PM. "
        "The 9 AM count is also a line in the Morning summary.",
        "features": ["open_orders"],
        "route": "open_orders",
        "group": "daily_summary",
        "defaults": _on(False, False),
    },
    # Team hub tasks (app/team/tasks_service.py) - each sent only to the
    # people the task concerns (Alert.user_ids), never to the one who acted.
    {
        "key": "task_assigned",
        "label": "Task assigned",
        "description": "A task was assigned to you or your team.",
        "features": ["team_tasks"],
        "route": "task",
        "group": "needs_action",
        "defaults": _on(True, True),
        "essential": True,
    },
    # The key is "task_due" for both reminders (old builds switch it); since
    # Oct 15 the 30-minute one is governed by task_due_soon and a Fix Issues
    # lead task's by fix_issues_task_due (alert_pref_key), so this switch
    # is the "Overdue" one.
    {
        "key": "task_due",
        "label": "Task overdue",
        "description": "A task of yours is overdue (an hour past its due time).",
        "features": ["team_tasks"],
        "route": "task",
        "group": "needs_action",
        "defaults": _on(True, True),
    },
    {
        "key": "task_due_soon",
        "label": "Task due in 30 minutes",
        "description": "A reminder 30 minutes before a task of yours is due.",
        "features": ["team_tasks"],
        "route": "task",
        "group": "needs_action",
        "defaults": _on(False, False),
    },
    {
        "key": "fix_issues_task_due",
        "label": "Fix Issues task reminders",
        "description": "Due-soon and overdue reminders for your daily Fix Issues task (the task itself still "
        "arrives as Task assigned).",
        "features": ["team_tasks"],
        "route": "task",
        "group": "needs_action",
        "defaults": _on(False, False),
    },
    {
        "key": "task_cant_do",
        "label": "Task can't be done",
        "description": "Someone marked a task you assigned as \"can't do\".",
        "features": ["team_tasks"],
        "route": "task",
        "group": "needs_action",
        "defaults": _on(True, True),
        "essential": True,
    },
    # Oct 15 (tasks agent, team/tasks_service.push_done / push_comment): off
    # unless switched on - the task lists update live anyway.
    {
        "key": "task_done",
        "label": "Task done",
        "description": "A task you gave someone was finished.",
        "features": ["team_tasks"],
        "route": "task",
        "group": "team",
        "defaults": _on(False, False),
    },
    {
        "key": "task_comment",
        "label": "Task comments",
        "description": "Someone commented on a task you made or are assigned.",
        "features": ["team_tasks"],
        "route": "task",
        "group": "team",
        "defaults": _on(False, False),
    },
    {
        "key": "announcement",
        "label": "Announcements",
        "description": "A manager posts an announcement to your team, or reminds you to read one.",
        "features": [],
        "route": "announcement",
        "group": "team",
        "defaults": _on(True, True),
        "essential": True,
    },
    # Fix Issues (Sep 30): items closed as "New item - giving it more time"
    # that are back on the open list after the week - one summary a day.
    # Off by default since the leads' daily Fix Issues task has the same items.
    {
        "key": "fix_issues_reopened",
        "label": "Fix Issues items back",
        "description": "Items closed for more time (one week) that still show the problem and are open again. One "
        "push a day; also a line in the Morning summary.",
        "features": ["inventory_reports"],
        "route": "fix_issues",
        "group": "daily_summary",
        "defaults": _on(False, False),
    },
    # Cart (Sep 30): only admins remove cart lines; staff ask.
    {
        "key": "cart_removal_request",
        "label": "Cart removal requests",
        "description": "Someone asked to remove an item from the purchase order cart (admins only).",
        "features": ["purchase_order_cart"],
        "admin_only": True,
        "route": "cart",
        "group": "admin",
        "defaults": _on(False, True),
        "essential": True,
    },
    {
        "key": "chat_message",
        "label": "Chat messages",
        "description": "New messages in your team chats and direct messages (muted chats only for @mentions).",
        "features": ["team_chat"],
        "route": "chat",
        "group": "team",
        "defaults": _on(True, True),
    },
    # Marketing flyers (services/flyer_sender.py): one push to whoever sent a
    # flyer once every group in that send is done - sent to them only
    # (Alert.user_ids), never during quiet hours. Off by default: the Flyers
    # screen shows the same status.
    {
        "key": "flyer_sent",
        "label": "Flyer sent",
        "description": "A flyer you sent has gone out to its WhatsApp groups (or some groups failed).",
        "features": ["marketing_flyers"],
        "route": "flyers",
        "group": "needs_action",
        "defaults": _on(False, False),
    },
]
# Inventory from the Toast API (services/inventory_api.py): costs, vendor
# codes and last-received dates still only come from the CSV upload.
# Admins only since Oct 15 (staff can't act on it).
ALERTS.append(
    {
        "key": "inventory_upload_due",
        "label": "Inventory upload due",
        "description": "The last inventory file is too old (7 days by default) - upload one to refresh costs and "
        "vendor codes. 9 AM; also a line in the Morning summary (admins only).",
        "features": ["overview"],
        "admin_only": True,
        "route": "home",
        "group": "admin",
        "defaults": _on(False, False),
    }
)
# Insights from Toast Analytics (services/insights.py, docs/INSIGHTS_CONTRACT.md).
ALERTS.append(
    {
        "key": "loss_watch",
        "label": "Loss watch",
        "description": "Checks to review from yesterday: a full comp, a large discount, refund or void, something "
        "unusual for that employee, or a check open too long or changed after payment. 9 AM; also a line in the "
        "Morning summary (admins only).",
        "features": ["sales_insights"],
        "admin_only": True,
        "route": "loss_watch",
        "group": "admin",
        "defaults": _on(False, False),
    }
)
ALERTS.append(
    {
        "key": "daily_sales_summary",
        "label": "Yesterday's sales",
        "description": "Yesterday's net sales from Toast, compared with the same day last week and the 4-week "
        "average. 9 AM; also a line in the Morning summary (not sent to accounts with sales numbers hidden).",
        "features": ["sales_insights"],
        "route": "insights_compare",
        "group": "daily_summary",
        "defaults": _on(False, False),
    }
)
# Inventory from Toast readiness (services/inventory_api_readiness.py): once
# per streak, only while the switch is off. Builds without the route open Home.
ALERTS.append(
    {
        "key": "inventory_api_ready",
        "label": "Inventory from Toast ready",
        "description": "Toast's inventory matched the daily file enough nights in a row (7 by default) - time to "
        "turn on Inventory from Toast. Once, at 9 AM; also a line in the Morning summary (admins only).",
        "features": [],
        "admin_only": True,
        "route": "inventory_api",
        "group": "admin",
        "defaults": _on(False, False),
    }
)
# Team chat @mentions (app/team/chat_service.py, W5): the person mentioned
# gets "X mentioned you in Kitchen", even in a muted chat.
ALERTS.append(
    {
        "key": "chat_mention",
        "label": "Chat mentions",
        "description": "Someone @mentions you in a team chat or a direct message - even in a chat you muted.",
        "features": ["team_chat"],
        "route": "chat",
        "group": "team",
        "defaults": _on(True, True),
        "essential": True,
    }
)
ALERTS_BY_KEY = {a["key"]: a for a in ALERTS}

# Store-local; no alert job sends inside this window.
QUIET_HOURS_START = dt.time(22, 0)
QUIET_HOURS_END = dt.time(7, 0)


def in_quiet_hours(now_local: dt.datetime) -> bool:
    t = now_local.time()
    return t >= QUIET_HOURS_START or t < QUIET_HOURS_END


def user_can_access_alert(user: User, key: str) -> bool:
    alert = ALERTS_BY_KEY.get(key)
    if alert is None or (alert.get("admin_only") and not user.is_admin):
        return False
    return all(user_has_feature(user, f) for f in alert["features"])


# --- Devices -----------------------------------------------------------------


def register_device(db: Session, user: User, token: str, platform: str) -> None:
    """Upsert by token: the same phone re-registering refreshes last_seen_at;
    a token last registered by someone else (shared store phone, new login)
    moves to this user."""
    now = utcnow()
    row = db.execute(select(DeviceToken).where(DeviceToken.token == token)).scalars().first()
    if row is None:
        db.add(DeviceToken(user_id=user.id, token=token, platform=platform, created_at=now, last_seen_at=now))
    else:
        row.user_id = user.id
        row.platform = platform
        row.last_seen_at = now
    try:
        db.commit()
    except IntegrityError:
        # Two registrations of a brand-new token racing each other - the
        # other one inserted it first; just take it over.
        db.rollback()
        row = db.execute(select(DeviceToken).where(DeviceToken.token == token)).scalars().one()
        row.user_id, row.platform, row.last_seen_at = user.id, platform, now
        db.commit()


def unregister_device(db: Session, token: str) -> None:
    """Called on logout. Deliberately not scoped to the current user and
    silent about unknown tokens - the goal is only "this phone stops getting
    alerts", and a stale token must never make logout fail."""
    db.execute(delete(DeviceToken).where(DeviceToken.token == token))
    db.commit()


def delete_user_notification_data(db: Session, user_id: int) -> None:
    """Removes a deleted user's devices, prefs and push log (caller commits)."""
    db.execute(delete(DeviceToken).where(DeviceToken.user_id == user_id))
    db.execute(delete(NotificationPref).where(NotificationPref.user_id == user_id))
    # In a savepoint: a missing table (code ahead of migration 0058) must
    # never make deleting an account fail.
    try:
        with db.begin_nested():
            db.execute(delete(PushDelivery).where(PushDelivery.user_id == user_id))
    except Exception:  # noqa: BLE001
        logger.warning("Notifications: couldn't remove push log rows of user %s", user_id, exc_info=True)


def _delete_tokens(db: Session, tokens: List[str]) -> None:
    if not tokens:
        return
    db.execute(delete(DeviceToken).where(DeviceToken.token.in_(tokens)))
    db.commit()
    logger.info("Notifications: removed %d device token(s) FCM reported as invalid", len(tokens))


# --- Defaults: catalog, store (admin), effective --------------------------------


def role_of(user: User) -> str:
    return "admin" if user.is_admin else "staff"


def catalog_default(key: str, role: str) -> bool:
    """The catalog's default for a role. Not in the catalog (chat_report,
    test, ...): on, as before."""
    alert = ALERTS_BY_KEY.get(key)
    if alert is None:
        return True
    return bool(alert["defaults"][role])


# Admin store defaults (P4): the newest setting_changes row for this key wins;
# its value is {alert key: {"staff": bool, "admin": bool}} with only the
# overridden roles present. No row = the catalog's defaults.
STORE_DEFAULTS_KEY = "notification_defaults"
_store_defaults_lock = tenant_lock("notifications._store_defaults_lock")


def _newest_store_defaults(db: Session) -> Optional[SettingChange]:
    return (
        db.execute(
            select(SettingChange)
            .where(SettingChange.key == STORE_DEFAULTS_KEY)
            .order_by(SettingChange.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _clean_store_defaults(raw) -> Dict[str, Dict[str, bool]]:
    out: Dict[str, Dict[str, bool]] = {}
    if not isinstance(raw, dict):
        return out
    for key, roles in raw.items():
        if key not in ALERTS_BY_KEY or not isinstance(roles, dict):
            continue
        clean = {r: v for r, v in roles.items() if r in ROLES and isinstance(v, bool)}
        if clean:
            out[key] = clean
    return out


def store_defaults(db: Session) -> Dict[str, Dict[str, bool]]:
    """The admin's overrides in force. Never raises: an unreadable table
    (code deployed before 0052) or a bad row = no overrides, logged."""
    try:
        row = _newest_store_defaults(db)
    except Exception:  # noqa: BLE001 - fall back to the catalog, never break a send
        db.rollback()
        logger.exception("Notifications: couldn't read store defaults; using the catalog's")
        return {}
    if row is None:
        return {}
    try:
        return _clean_store_defaults(json.loads(row.value))
    except ValueError:
        return {}


def default_for(key: str, role: str, store: Dict[str, Dict[str, bool]]) -> bool:
    """What a user of this role gets with no choice of their own."""
    value = (store.get(key) or {}).get(role)
    return catalog_default(key, role) if value is None else value


def _explicit(db: Session, key: str, user_ids: Iterable[int]) -> Dict[int, bool]:
    ids = list(set(user_ids))
    if not ids:
        return {}
    return {
        uid: bool(enabled)
        for uid, enabled in db.execute(
            select(NotificationPref.user_id, NotificationPref.enabled).where(
                NotificationPref.key == key, NotificationPref.user_id.in_(ids)
            )
        )
    }


def effective_enabled(
    db: Session, user: User, key: str, store: Optional[Dict[str, Dict[str, bool]]] = None
) -> bool:
    """The user's own choice, else the store default for their role, else
    the catalog's. (Feature access is separate: user_can_access_alert.)"""
    explicit = _explicit(db, key, [user.id])
    if user.id in explicit:
        return explicit[user.id]
    return default_for(key, role_of(user), store_defaults(db) if store is None else store)


def enabled_user_ids(db: Session, key: str, users: Iterable[User]) -> Set[int]:
    """Of `users`, those for whom `key` is effectively on (one query for the
    choices, one for the store defaults)."""
    users = list(users)
    if not users:
        return set()
    explicit = _explicit(db, key, [u.id for u in users])
    store = store_defaults(db)
    return {
        u.id for u in users if explicit.get(u.id, default_for(key, role_of(u), store))
    }


def disabled_user_ids(db: Session, key: str, user_ids: Iterable[int]) -> Set[int]:
    """Of `user_ids`, those for whom `key` is effectively off. For a key not
    in the catalog, only an explicit "off" counts (the old rule)."""
    ids = set(user_ids)
    if not ids:
        return set()
    if key not in ALERTS_BY_KEY:
        return {uid for uid, on in _explicit(db, key, ids).items() if not on}
    users = db.execute(select(User).where(User.id.in_(list(ids)))).scalars().all()
    return ids - enabled_user_ids(db, key, users)


def deliverable_user_ids(db: Session, key: str, user_ids: Iterable[int]) -> Set[int]:
    """Of `user_ids`, those a push for `key` would actually reach: a
    registered device, access to the alert, and the alert effectively on.
    For callers that want to skip everyone else before building a push
    (e.g. the 5-minute task sweep, so a user with no phone isn't retried
    every run)."""
    ids = set(user_ids)
    if not ids:
        return set()
    with_device = set(
        db.execute(select(DeviceToken.user_id).where(DeviceToken.user_id.in_(list(ids)))).scalars()
    )
    if not with_device:
        return set()
    users = db.execute(select(User).where(User.id.in_(list(with_device)))).scalars().all()
    users = [u for u in users if user_can_access_alert(u, key)]
    return enabled_user_ids(db, key, users)


# --- Preferences -------------------------------------------------------------


def _group_rank(alert: dict) -> int:
    return GROUP_KEYS.index(alert["group"])


def get_prefs(db: Session, user: User) -> List[dict]:
    """The user's switches, grouped (GROUPS order). key/label/description/
    enabled are what builds 5-9 read; group/default/essential/admin_only/
    explicit are additive (Oct 15). `enabled` is what the user actually
    gets; `default` what they'd get without their own choice."""
    stored = {
        key: enabled
        for key, enabled in db.execute(
            select(NotificationPref.key, NotificationPref.enabled).where(NotificationPref.user_id == user.id)
        )
    }
    store = store_defaults(db)
    role = role_of(user)
    alerts = sorted(
        (a for a in ALERTS if user_can_access_alert(user, a["key"])),
        key=_group_rank,  # stable: catalog order within a group
    )
    out = []
    for a in alerts:
        default = default_for(a["key"], role, store)
        explicit = a["key"] in stored
        out.append(
            {
                "key": a["key"],
                "label": a["label"],
                "description": a["description"],
                "enabled": bool(stored[a["key"]]) if explicit else default,
                "group": a["group"],
                "default": default,
                "essential": bool(a.get("essential")),
                "admin_only": bool(a.get("admin_only")),
                "explicit": explicit,
            }
        )
    return out


def prefs_payload(db: Session, user: User) -> dict:
    """GET / PUT / reset answer: {"items": [...]} (what builds 5-9 read)
    plus the groups that have at least one of this user's items."""
    items = get_prefs(db, user)
    present = {i["group"] for i in items}
    return {"items": items, "groups": [dict(g) for g in GROUPS if g["key"] in present]}


def set_pref(db: Session, user: User, key: str, enabled: bool) -> None:
    """Raises ValueError for an unknown key or one this user can't access.
    Always writes an explicit row (on or off), so a later change of the
    defaults never overrides a choice."""
    if not user_can_access_alert(user, key):
        raise ValueError(key)
    row = db.execute(
        select(NotificationPref).where(NotificationPref.user_id == user.id, NotificationPref.key == key)
    ).scalars().first()
    if row is None:
        db.add(NotificationPref(user_id=user.id, key=key, enabled=enabled))
    else:
        row.enabled = enabled
    db.commit()


def reset_prefs(db: Session, user: User) -> int:
    """"Reset to recommended": forgets the user's own choices for every
    catalog alert, so the defaults apply again. Other rows in the table
    (chat's "hide message text") are not alert choices and stay. Returns
    how many choices were removed."""
    result = db.execute(
        delete(NotificationPref).where(
            NotificationPref.user_id == user.id, NotificationPref.key.in_(list(ALERTS_BY_KEY))
        )
    )
    db.commit()
    return result.rowcount or 0


# --- Store defaults (admin) ---------------------------------------------------

STORE_DEFAULTS_HISTORY_LIMIT = 50


class DefaultsError(ValueError):
    pass


def defaults_state(db: Session) -> dict:
    """GET /api/notifications/defaults: every alert with its catalog
    default, the store override (null = none) and what's in force, per
    role; plus who changed it last and the history."""
    store = store_defaults(db)
    items = []
    for a in sorted(ALERTS, key=_group_rank):
        roles = ("admin",) if a.get("admin_only") else ROLES
        items.append(
            {
                "key": a["key"],
                "label": a["label"],
                "description": a["description"],
                "group": a["group"],
                "essential": bool(a.get("essential")),
                "admin_only": bool(a.get("admin_only")),
                "roles": list(roles),
                "catalog": {r: catalog_default(a["key"], r) for r in roles},
                "store": {r: (store.get(a["key"]) or {}).get(r) for r in roles},
                "effective": {r: default_for(a["key"], r, store) for r in roles},
            }
        )
    newest = _newest_store_defaults(db)
    history = db.execute(
        select(SettingChange)
        .where(SettingChange.key == STORE_DEFAULTS_KEY)
        .order_by(SettingChange.id.desc())
        .limit(STORE_DEFAULTS_HISTORY_LIMIT)
    ).scalars()
    return {
        "items": items,
        "groups": [dict(g) for g in GROUPS],
        "changed_by": newest.changed_by_name if newest else None,
        "changed_at": newest.changed_at.isoformat() if newest and newest.changed_at else None,
        "history": [
            {
                "id": r.id,
                "note": r.note,
                "changed_by": r.changed_by_name,
                "changed_at": r.changed_at.isoformat() if r.changed_at else None,
            }
            for r in history
        ],
    }


def _record_store_defaults(
    db: Session, admin: User, new: Dict[str, Dict[str, bool]], current: Dict[str, Dict[str, bool]], note: str
) -> None:
    prev = _newest_store_defaults(db)
    db.add(
        SettingChange(
            key=STORE_DEFAULTS_KEY,
            value=json.dumps(new, sort_keys=True),
            previous_value=json.dumps(current, sort_keys=True),
            previous_source="db" if prev is not None else "default",
            changed_by_user_id=admin.id,
            changed_by_name=admin.username,
            changed_at=utcnow(),
            note=note,
        )
    )
    db.commit()


def set_store_default(db: Session, admin: User, key: str, role: str, enabled: Optional[bool]) -> dict:
    """An admin's store default for one alert and role (None = back to the
    catalog's). Users' own choices still win. Each change is a
    setting_changes row (who / when / before); a no-op writes nothing."""
    alert = ALERTS_BY_KEY.get(key)
    if alert is None:
        raise DefaultsError("Unknown notification")
    if role not in ROLES:
        raise DefaultsError("Role must be staff or admin")
    if role == "staff" and alert.get("admin_only"):
        raise DefaultsError("Only admins get this notification")
    with _store_defaults_lock:
        db.commit()  # see the change another request just committed
        current = store_defaults(db)
        new = json.loads(json.dumps(current))
        roles = new.setdefault(key, {})
        if enabled is None or enabled == catalog_default(key, role):
            # The catalog's own value: no override needed.
            roles.pop(role, None)
        else:
            roles[role] = bool(enabled)
        if not roles:
            new.pop(key)
        if new != current:
            word = "default" if enabled is None else ("on" if enabled else "off")
            _record_store_defaults(db, admin, new, current, f"{alert['label']} for {role}: {word}")
    return defaults_state(db)


def reset_store_defaults(db: Session, admin: User) -> dict:
    """Back to the catalog's defaults for every alert (one history row)."""
    with _store_defaults_lock:
        db.commit()
        current = store_defaults(db)
        if current:
            _record_store_defaults(db, admin, {}, current, "Reset to recommended")
    return defaults_state(db)


# --- Per-user push log (P7) ---------------------------------------------------

PUSH_LOG_KEEP_DAYS = 180
RECENT_LIMIT = 50


def log_deliveries(db: Session, key: str, user_ids: Iterable[int], title: Optional[str]) -> None:
    """One push_deliveries row per user a push reached. Best-effort and in
    its own commit, after the send's notification_log row is committed - a
    failure here (table missing before 0058) never un-logs a send."""
    ids = sorted(set(user_ids))
    if not ids:
        return
    now = utcnow()
    try:
        for uid in ids:
            db.add(PushDelivery(user_id=uid, key=key, title=(title or "")[:200] or None, sent_at=now))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Notifications: couldn't log %s deliveries", key, exc_info=True)


def prune_push_log(db: Session, now: Optional[dt.datetime] = None) -> int:
    cutoff = (now or utcnow()) - dt.timedelta(days=PUSH_LOG_KEEP_DAYS)
    try:
        result = db.execute(delete(PushDelivery).where(PushDelivery.sent_at < cutoff))
        db.commit()
        return result.rowcount or 0
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Notifications: couldn't prune push_deliveries", exc_info=True)
        return 0


def recent_pushes(db: Session, user: User, limit: int = RECENT_LIMIT) -> List[dict]:
    rows = db.execute(
        select(PushDelivery)
        .where(PushDelivery.user_id == user.id)
        .order_by(PushDelivery.id.desc())
        .limit(max(1, min(limit, 200)))
    ).scalars()
    return [
        {
            "key": r.key,
            "label": ALERTS_BY_KEY[r.key]["label"] if r.key in ALERTS_BY_KEY else r.key,
            "title": r.title,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        }
        for r in rows
    ]


def push_volume(db: Session, days: int = 14) -> dict:
    """Admin: pushes per person per alert over the last `days`."""
    days = max(1, min(int(days), 90))
    since = utcnow() - dt.timedelta(days=days)
    counts: Dict[int, Dict[str, int]] = {}
    for uid, key, n in db.execute(
        select(PushDelivery.user_id, PushDelivery.key, func.count())
        .where(PushDelivery.sent_at >= since)
        .group_by(PushDelivery.user_id, PushDelivery.key)
    ):
        counts.setdefault(uid, {})[key] = n
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_(list(counts) or [-1]))).all())
    users = [
        {
            "user_id": uid,
            "username": names.get(uid),
            "total": sum(by_key.values()),
            "per_day": round(sum(by_key.values()) / days, 1),
            "by_key": by_key,
        }
        for uid, by_key in counts.items()
    ]
    users.sort(key=lambda u: (-u["total"], u["username"] or ""))
    return {"days": days, "users": users}


# --- Recipients + dispatch ---------------------------------------------------


def recipient_tokens(db: Session, key: str) -> Dict[int, List[str]]:
    """{user_id: [device tokens]} for everyone who should get alert `key`:
    a device, access, and the alert effectively on for them."""
    tokens_by_user: Dict[int, List[str]] = {}
    for user_id, token in db.execute(select(DeviceToken.user_id, DeviceToken.token)):
        tokens_by_user.setdefault(user_id, []).append(token)
    if not tokens_by_user:
        return {}
    users = db.execute(select(User).where(User.id.in_(list(tokens_by_user)))).scalars().all()
    users = [u for u in users if user_can_access_alert(u, key)]
    enabled = enabled_user_ids(db, key, users)
    return {uid: tokens_by_user[uid] for uid in enabled}


def sent_dedupe_keys(db: Session, dedupe_keys: List[str]) -> set:
    if not dedupe_keys:
        return set()
    return set(
        db.execute(select(NotificationLog.dedupe_key).where(NotificationLog.dedupe_key.in_(dedupe_keys))).scalars()
    )


@dataclass
class Alert:
    """One notification a trigger wants sent. Usually one dedupe key; an
    invoice failing on both channels carries one per channel, so a later
    failure on the other channel still alerts on its own."""

    key: str
    dedupe_keys: List[str]
    title: str
    body: str
    data: Dict[str, str] = field(default_factory=dict)
    # Only these users (still subject to feature access and prefs) - for
    # alerts about one person's thing, e.g. a task assigned to them. None =
    # everyone with access, as for the store-wide alerts.
    user_ids: Optional[List[int]] = None
    # The switch that decides who gets it, when not its own key (Oct 15):
    # e.g. a "task_due" push 30 min before due is governed by task_due_soon.
    # None = alert_pref_key works it out (usually just `key`). The payload's
    # `type` and `route` always stay `key`'s, so old builds open it the same.
    pref_key: Optional[str] = None

    def payload_data(self) -> Dict[str, str]:
        base = {"type": self.key, "route": ALERTS_BY_KEY[self.key]["route"]}
        base.update({k: str(v) for k, v in self.data.items() if v is not None})
        return base


def _is_lead_task(db: Session, task_id) -> bool:
    """A system Fix Issues lead task (services/issue_leads.py)."""
    try:
        from app.services.issue_leads import SOURCE_TYPE
        from app.team.tasks_models import Task
        from app.team.tasks_service import SYSTEM_USER_ID

        task = db.get(Task, int(task_id))
    except Exception:  # noqa: BLE001 - unknown id / module: an ordinary task
        return False
    return task is not None and task.created_by_user_id == SYSTEM_USER_ID and task.source_type == SOURCE_TYPE


def alert_pref_key(db: Session, alert: Alert) -> str:
    """Which switch governs `alert`. Owner (Oct 15): "Due in 30 minutes"
    reminders and every due / overdue reminder of a Fix Issues lead task are
    off by default, while "Overdue" for your own tasks stays on - all sent
    as task_due by team/tasks_service.py (dedupe "task_due:<id>:soon:..." /
    ":overdue:..."), so they're told apart here."""
    if alert.pref_key:
        return alert.pref_key
    if alert.key == "task_due":
        if _is_lead_task(db, alert.data.get("task_id")):
            return "fix_issues_task_due"
        if any(":soon:" in k for k in alert.dedupe_keys):
            return "task_due_soon"
    return alert.key


def dispatch_alert(db: Session, alert: Alert) -> int:
    """Sends `alert` to its recipients unless already sent. Returns how many
    users it reached (0 for a dry run, a duplicate, or nobody to send to).
    """
    unsent = [k for k in alert.dedupe_keys if k not in sent_dedupe_keys(db, alert.dedupe_keys)]
    if not unsent:
        return 0

    recipients = recipient_tokens(db, alert_pref_key(db, alert))
    if alert.user_ids is not None:
        wanted = set(alert.user_ids)
        recipients = {uid: tokens for uid, tokens in recipients.items() if uid in wanted}
    else:
        # Store-wide alerts (closing checklist, invoice failed, orders...)
        # are about real store operations - never sent to an App Review
        # demo login (app/demo.py). Alerts about its own things (a task
        # assigned to it) still reach it.
        from app.demo import demo_user_ids

        demo = demo_user_ids(db)
        recipients = {uid: tokens for uid, tokens in recipients.items() if uid not in demo}
    if not push.is_configured():
        logger.info(
            "Notifications (dry run - Firebase not configured): would send %s to %d user(s): %r / %r %s",
            unsent, len(recipients), alert.title, alert.body, alert.payload_data(),
        )
        return 0
    if not recipients:
        # Debug only: a task's push to someone with no phone (or with it
        # switched off) is retried by every 5-minute sweep - 846 of these
        # lines in two weeks of prod logs.
        logger.debug("Notifications: %s has no recipients (no enabled users with a device)", unsent)
        return 0

    user_by_token = {token: user_id for user_id, tokens in recipients.items() for token in tokens}
    result = push.send_to_tokens(list(user_by_token), alert.title, alert.body, alert.payload_data())
    _delete_tokens(db, result.invalid_tokens)

    reached = {user_by_token[t] for t in result.delivered_tokens}
    if not reached:
        # Nothing got through (FCM outage, every token dead) - leave it
        # unlogged so the next run retries.
        logger.warning("Notifications: %s reached no devices", unsent)
        return 0

    now = utcnow()
    for dedupe_key in unsent:
        db.add(NotificationLog(key=alert.key, dedupe_key=dedupe_key, sent_at=now, recipient_count=len(reached)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("Notifications: %s was logged concurrently", unsent)
    log_deliveries(db, alert.key, reached, alert.title)
    logger.info("Notifications: sent %s to %d user(s): %r", unsent, len(reached), alert.title)
    return len(reached)


def send_test(db: Session, user: User) -> int:
    """Sends a test push to the user's own devices; returns how many devices
    it reached. Raises push.PushNotConfigured when Firebase isn't set up."""
    tokens = list(db.execute(select(DeviceToken.token).where(DeviceToken.user_id == user.id)).scalars())
    if not push.is_configured():
        raise push.PushNotConfigured()
    if not tokens:
        return 0
    result = push.send_to_tokens(
        tokens,
        "Test notification",
        "Notifications are working on this device.",
        # No route: a test isn't about any page, so tapping it just opens the app.
        {"type": "test"},
    )
    _delete_tokens(db, result.invalid_tokens)
    return result.success_count

