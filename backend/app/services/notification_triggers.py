"""What each push alert checks for (see services/notifications.py ALERTS for
the catalog, and app/scheduler.py start_notification_scheduler for when each
one runs). Every trigger is a pure "what should be sent right now" function
of the DB and a store-local `now`, reusing the same service code the
matching page uses - so an alert can never disagree with the screen it opens
- and returns Alert objects; sending, recipients and dedupe are all
notifications.dispatch_alert's job.
"""

import datetime as dt
import logging
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.demo import demo_user_ids, hide_demo_clause
from app.models import (
    CartItem,
    ChecklistSubmission,
    InvoiceSubmission,
    IssueResolution,
    IssueResolutionEvent,
    NotificationLog,
    OpenOrderCache,
    User,
)
from app.services.checklist_schemas import checklist_title, closing_checklist_types
from app.services.critical_dismissals import annotate_vendor_groups
from app.services.inventory_api import inventory_upload_due_alerts
from app.services.inventory_api_readiness import inventory_api_ready_alerts
from app.services.invoice_submission import (
    TRANSFER_TYPE,
    is_channel_off,
    is_demo_not_sent,
    skips_toast,
    type_display_label,
)
from app.services.notifications import (
    Alert,
    dispatch_alert,
    enabled_user_ids,
    in_quiet_hours,
    prune_push_log,
    sent_dedupe_keys,
    user_can_access_alert,
)
from app.services.reorder import compute_reorder_candidates, group_candidates_by_vendor
from app.services.vendor_calendar import get_vendor_calendar
from app.timeutil import restaurant_timezone, utcnow

logger = logging.getLogger(__name__)

# Critical vendors: the Reorder Candidates page's own defaults.
REORDER_LOOKBACK_DAYS = 30
REORDER_LEAD_TIME_DAYS = 7
CRITICAL_NAMES_SHOWN = 3

# The nightly closing checklists are the active ones with settings.closing
# (checklist builder; admins set it per checklist) - see
# checklist_schemas.closing_checklist_types(). The 9:30 PM alert is ONE
# notification listing whichever of these are missing (owner: a single
# checklist alert). This constant is only the built-in default set, kept for
# reference; code must call closing_checklist_types().
CLOSING_CHECKLIST_TYPES = ["spice_town_closing", "street_kitchen_closing", "street_kitchen_fsa"]

# Invoice failed: how far back to look. Wide enough to cover a night of
# quiet hours, so failures from 10pm-7am still alert at 7am.
INVOICE_FAILED_WINDOW = dt.timedelta(hours=48)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _join_and(names: List[str]) -> str:
    """"A", "A and B", "A, B and C"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def order_due_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    """One alert per vendor order due today or tomorrow, from the Vendor
    Calendar's own order-due events (skipped orders excluded)."""
    today = now.date()
    tomorrow = today + dt.timedelta(days=1)
    events = get_vendor_calendar(db, today, tomorrow)["events"]
    alerts = []
    for e in events:
        # A skipped order, or one already placed (Mark placed), isn't due.
        if e["type"] != "order_due" or e.get("skipped") or e.get("ordered"):
            continue
        when = "today" if e["date"] == today.isoformat() else "tomorrow"
        count = e.get("items_needing_order") or 0
        if e.get("below_minimum"):
            # Vendors have minimum orders: 1-2 low items are not "N items to
            # order" (the calendar doesn't mark it urgent either).
            body = f"{_plural(count, 'item')} low - fewer than a usual order, review"
        elif count:
            body = f"{_plural(count, 'item')} to order"
        else:
            body = "Review the order"
        alerts.append(
            Alert(
                key="order_due",
                # Per day of the reminder: a heads-up the day before AND the
                # "Order today" on the day it's actually due.
                dedupe_keys=[f"order_due:{e['vendor']}:{e['for_delivery_date']}:{when}"],
                title=f"Order {when}: {e['vendor']}",
                body=body,
                data={"vendor": e["vendor"], "horizon_days": e["horizon_days"]},
            )
        )
    return alerts


def critical_vendors_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    """A single morning digest of every active vendor the Reorder Candidates
    page currently marks Critical. A vendor with an order on the way shows
    "Ordered" there, not Critical, so it's never in this alert. Neither is a
    vendor an admin hid from Today (Close / Skip this week / Remind me later,
    services/critical_dismissals.py); with all of them hidden, no push."""
    today = now.date()
    candidates = compute_reorder_candidates(db, REORDER_LOOKBACK_DAYS, REORDER_LEAD_TIME_DAYS, today)
    groups = group_candidates_by_vendor(db, candidates, today, REORDER_LEAD_TIME_DAYS)
    # This is the Today report, so a Close whose vendor has something new
    # critical ends here (and the vendor is in the push again).
    annotate_vendor_groups(db, groups, now=now, today_report=True)
    critical = [
        g["vendor"]
        for g in groups
        if g["urgency"]["tier"] == "critical" and not g["inactive"] and not g["critical_dismissed"]
    ]
    if not critical:
        return []
    body = ", ".join(critical[:CRITICAL_NAMES_SHOWN])
    if len(critical) > CRITICAL_NAMES_SHOWN:
        body += f" +{len(critical) - CRITICAL_NAMES_SHOWN} more"
    return [
        Alert(
            key="critical_vendors",
            dedupe_keys=[f"critical_vendors:{today.isoformat()}"],
            title=f"{_plural(len(critical), 'vendor')} critical",
            body=body,
        )
    ]


def closing_checklist_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    """Any closing checklist with no submission yet today, all in one alert. "Today" is the
    store-local day, bounded the same way checklist_submission.list_submissions
    bounds its date filter (a 9 PM EDT submission is already tomorrow in UTC)."""
    today = now.date()
    tz = restaurant_timezone()
    day_start = dt.datetime.combine(today, dt.time.min, tz)
    day_end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, tz)
    closing = closing_checklist_types()
    if not closing:
        return []
    submitted = set(
        db.execute(
            select(ChecklistSubmission.checklist_type).where(
                ChecklistSubmission.checklist_type.in_(closing),
                ChecklistSubmission.created_at >= day_start,
                ChecklistSubmission.created_at < day_end,
                # An App Review demo login's submission doesn't count - the
                # real store still has to close.
                ChecklistSubmission.submitted_by_user_id.notin_(demo_user_ids(db)),
            )
        ).scalars()
    )
    missing = [checklist_title(t) for t in closing if t not in submitted]
    if not missing:
        return []
    return [
        Alert(
            key="closing_checklist",
            dedupe_keys=[f"closing_checklist:{today.isoformat()}"],
            title="Closing checklist not done",
            body="Not submitted yet: " + _join_and(missing),
        )
    ]


def _failed_channels(entry: InvoiceSubmission) -> List[str]:
    """Same notion of "failed" as invoice_submission._processing_status: an
    error recorded and not sent. A WhatsApp send Whapi couldn't confirm
    either way (whatsapp_ambiguous) isn't a failure, and an "untracked" or
    "transfer" submission never emails Toast at all. A channel mid-resend has its
    error cleared by mark_pending, so it never counts here."""
    channels = []
    if (
        not entry.whatsapp_sent
        and entry.whatsapp_error is not None
        and not entry.whatsapp_ambiguous
        and not is_demo_not_sent(entry.whatsapp_sent, entry.whatsapp_error)
        and not is_channel_off(entry.whatsapp_sent, entry.whatsapp_error)
    ):
        channels.append("whatsapp")
    if (
        not skips_toast(entry.submission_type)
        and not entry.toast_email_sent
        and entry.toast_email_error is not None
        and not is_demo_not_sent(entry.toast_email_sent, entry.toast_email_error)
        and not is_channel_off(entry.toast_email_sent, entry.toast_email_error)
    ):
        channels.append("toast_email")
    return channels


_CHANNEL_LABELS = {"whatsapp": "WhatsApp", "toast_email": "Toast email"}


def invoice_failed_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    """One alert per recent invoice with a failed send, covering only the
    channels not already alerted on. Sent to whoever submitted it plus the
    admins (Oct 15; it used to go to everyone with Invoices)."""
    since = now.astimezone(dt.timezone.utc) - INVOICE_FAILED_WINDOW
    entries = db.execute(
        select(InvoiceSubmission).where(
            InvoiceSubmission.created_at >= since,
            InvoiceSubmission.deleted_at.is_(None),
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),
        )
    ).scalars().all()
    alerts = []
    admins: Optional[List[int]] = None
    for entry in entries:
        channels = _failed_channels(entry)
        if not channels:
            continue
        if admins is None:
            demo = demo_user_ids(db)
            admins = [u for u in db.execute(select(User.id).where(User.is_admin.is_(True))).scalars() if u not in demo]
        keys = {c: f"invoice_failed:{entry.id}:{c}" for c in channels}
        already = sent_dedupe_keys(db, list(keys.values()))
        channels = [c for c in channels if keys[c] not in already]
        if not channels:
            continue
        alerts.append(
            Alert(
                key="invoice_failed",
                dedupe_keys=[keys[c] for c in channels],
                title=(
                    f"Transfer failed to send: {type_display_label(entry.submission_type, entry.transfer_direction)}"
                    if entry.submission_type == TRANSFER_TYPE
                    else f"Invoice failed to send: {entry.vendor}"
                ),
                body=" and ".join(_CHANNEL_LABELS[c] for c in channels) + " failed",
                data={"invoice_id": entry.id},
                user_ids=sorted(set(admins) | ({entry.submitted_by_user_id} if entry.submitted_by_user_id else set())),
            )
        )
    return alerts


# The three daily open-order summaries (owner, 2026-09-28), store time:
#   9:00 AM  Opening  - every order still open
#   4:00 PM  Today    - orders opened 9 AM-4 PM today that are still open
#   9:30 PM  Day end  - every order still open
OPEN_ORDER_SUMMARY_TIMES = ((9, 0), (16, 0), (21, 30))


def open_orders_summary_slot(now: dt.datetime) -> str:
    if now.hour < 12:
        return "Opening"
    if now.hour < 19:
        return "Today"
    return "Day end"


def open_orders_morning_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    """The opening / afternoon / day-end Open Checks summary ("Day end: 3
    open checks"). The 4 PM one only counts orders opened since 9 AM today.
    Nothing when there's nothing to report."""
    slot = open_orders_summary_slot(now)
    rows = db.execute(select(OpenOrderCache)).scalars().all()
    local = now.astimezone(restaurant_timezone())
    if slot == "Today":
        start = local.replace(hour=9, minute=0, second=0, microsecond=0)
        end = local.replace(hour=16, minute=0, second=0, microsecond=0)
        rows = [r for r in rows if r.opened_at and start <= r.opened_at.astimezone(start.tzinfo) < end]
    if not rows:
        return []
    utc_now = now.astimezone(dt.timezone.utc)
    scheduled = sum(1 for r in rows if r.promised_at and r.promised_at > utc_now)
    opened = [r.opened_at for r in rows if r.opened_at]
    parts = []
    if scheduled:
        parts.append(f"{scheduled} scheduled for later")
    if opened and slot != "Today":
        days = (utc_now - min(opened)).days
        if days >= 1:
            parts.append(f"oldest open {_plural(days, 'day')}")
    if slot == "Today":
        title = f"Today: {_plural(len(rows), 'open check')} since 9 AM"
    else:
        title = f"{slot}: {_plural(len(rows), 'open check')}"
    return [
        Alert(
            key="open_orders_morning",
            dedupe_keys=[f"open_orders_morning:{local.date().isoformat()}:{slot.lower().replace(' ', '_')}"],
            title=title,
            body=" \u00b7 ".join(parts) or "Tap to review open checks",
        )
    ]


# Fix Issues "more time" (owner, Sep 30): one combined push a day, at 9:00,
# for every item whose week ran out and is open again. Built from the reopen
# history, so items the list already reopened lazily (someone opened Fix
# Issues before 9:00) are still in it. Each reopen is in one push only.
FIX_ISSUES_REOPENED_LOOKBACK = dt.timedelta(days=7)
REOPENED_NAMES_SHOWN = 3


def _names_line(names: List[str], shown: int) -> str:
    line = ", ".join(names[:shown])
    if len(names) > shown:
        line += f" + {len(names) - shown} more"
    return line


def _closed_by_demo(db: Session, reopen: IssueResolutionEvent) -> bool:
    """Whether the close this reopen ended was made by a demo account."""
    from app.demo import is_demo_username

    by = db.execute(
        select(IssueResolutionEvent.by_name)
        .where(
            IssueResolutionEvent.issue_type == reopen.issue_type,
            IssueResolutionEvent.issue_key == reopen.issue_key,
            IssueResolutionEvent.event == "closed",
            IssueResolutionEvent.at <= reopen.at,
        )
        .order_by(IssueResolutionEvent.at.desc(), IssueResolutionEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return is_demo_username(by)


def fix_issues_reopened_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    from app.services.fix_issues import HOW_SNOOZE_ENDED, ISSUE_TYPES, reopen_all_ended_snoozes

    today = now.date()
    reopen_all_ended_snoozes(db, today)
    day_key = f"fix_issues_reopened:{today.isoformat()}"
    if sent_dedupe_keys(db, [day_key]):
        return []
    since = now.astimezone(dt.timezone.utc) - FIX_ISSUES_REOPENED_LOOKBACK
    events = db.execute(
        select(IssueResolutionEvent)
        .where(
            IssueResolutionEvent.event == "reopened",
            IssueResolutionEvent.how == HOW_SNOOZE_ENDED,
            IssueResolutionEvent.at >= since,
        )
        .order_by(IssueResolutionEvent.at, IssueResolutionEvent.id)
    ).scalars().all()
    # Closed again since (someone already dealt with it) - nothing to say.
    closed_now = {
        (t, k) for t, k in db.execute(select(IssueResolution.issue_type, IssueResolution.issue_key))
    }
    events = [e for e in events if (e.issue_type, e.issue_key) not in closed_now]
    # Defensive (the demo guard already refuses a demo close): a "more time"
    # close made by the App Review demo login never pushes when it ends.
    events = [e for e in events if not _closed_by_demo(db, e)]
    keys = {e.id: f"fix_issues_reopened:event:{e.id}" for e in events}
    already = sent_dedupe_keys(db, list(keys.values()))
    events = [e for e in events if keys[e.id] not in already]
    if not events:
        return []
    names = list(dict.fromkeys(e.item_name or e.issue_key for e in events))
    types = {e.issue_type for e in events}
    data = {"issue_type": next(iter(types))} if len(types) == 1 else {}
    n = len(events)
    return [
        Alert(
            key="fix_issues_reopened",
            dedupe_keys=[day_key] + [keys[e.id] for e in events],
            title=f"Fix Issues: {_plural(n, 'item')} back",
            body=_names_line(names, REOPENED_NAMES_SHOWN),
            data=data,
        )
    ]


# Cart removal requests (Sep 30): only admins remove cart lines; a request
# from anyone else is pushed to the admins. The job runs every
# CART_REMOVAL_CHECK_MINUTES, so requests made within that window go out
# as one push. Each request is in one push only (dedupe per line + request
# time, so a cancelled and re-made request alerts again).
CART_REMOVAL_CHECK_MINUTES = 10


def cart_removal_dedupe_key(row: CartItem) -> str:
    return f"cart_removal_request:{row.id}:{int(row.removal_requested_at.timestamp())}"


def cart_removal_request_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    rows = db.execute(
        select(CartItem).where(CartItem.removal_requested_at.is_not(None)).order_by(CartItem.removal_requested_at, CartItem.id)
    ).scalars().all()
    # An App Review demo login's request never pushes the real admins
    # (app/demo.py) - it stays visible in the cart for them to see.
    demo = demo_user_ids(db)
    rows = [r for r in rows if r.removal_requested_by_user_id not in demo]
    keys = {r.id: cart_removal_dedupe_key(r) for r in rows}
    already = sent_dedupe_keys(db, list(keys.values()))
    rows = [r for r in rows if keys[r.id] not in already]
    if not rows:
        return []
    admins = list(db.execute(select(User.id).where(User.is_admin.is_(True))).scalars())
    if len(rows) == 1:
        r = rows[0]
        title = "Cart: removal request"
        body = f"{r.removal_requested_by or 'Someone'}: {r.name} ({r.supplier})"
        if r.removal_requested_reason:
            body += f" - {r.removal_requested_reason}"
    else:
        title = f"Cart: {len(rows)} removal requests"
        body = _names_line([f"{r.name} ({r.supplier})" for r in rows], REOPENED_NAMES_SHOWN)
    return [
        Alert(
            key="cart_removal_request",
            dedupe_keys=[keys[r.id] for r in rows],
            title=title,
            body=body,
            data={"vendor": rows[0].supplier} if len({r.supplier for r in rows}) == 1 else {},
            user_ids=admins,
        )
    ]


# Insights (docs/INSIGHTS_CONTRACT.md): Loss watch / Odd checks flags from
# yesterday, one combined push at 9:00 (admins only). Flags come from the
# cached Toast check report (the 04:30 nightly job), recomputed here from the
# cache - no Toast call. Each flag is in one push only; an acknowledged flag
# never pushes.
def loss_watch_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    from app.services import insights

    today = now.date()
    try:
        insights.refresh_flags(db, today_bd=today)
    except Exception:
        db.rollback()
        logger.exception("loss_watch: recomputing flags failed - alerting on what's stored")
    flags = insights.pending_alert_flags(db, today)
    if not flags:
        return []
    title, body = insights.loss_watch_alert_text(flags)
    dates = sorted({f.business_date for f in flags})
    return [
        Alert(
            key="loss_watch",
            dedupe_keys=[insights.alert_dedupe_key(f.id) for f in flags],
            title=title,
            body=body,
            data={"date": dates[-1].isoformat()},
            # Admins only, never the App Review demo login (real store data).
            user_ids=[
                uid
                for uid in db.execute(select(User.id).where(User.is_admin.is_(True))).scalars()
                if uid not in demo_user_ids(db)
            ],
        )
    ]


# Yesterday's sales vs last week / the 4-week average (the compare insight),
# 9:00, to everyone with Sales insights who can see sales numbers. From the
# cache only; nothing when yesterday isn't cached yet.
def daily_sales_summary_alerts(db: Session, now: dt.datetime) -> List[Alert]:
    from app.sales_access import can_see_sales
    from app.services import insights, insights_data

    yesterday = now.date() - dt.timedelta(days=1)
    if not insights_data.cached(db, "metrics_day", [yesterday]):
        return []
    cmp = insights.compare(db, yesterday)
    if cmp.get("net_sales") is None:
        return []
    line = insights.compare_line(cmp)
    body = (line + " \u00b7 " if line else "") + f"{cmp.get('orders') or 0} orders"
    demo = demo_user_ids(db)
    users = [u.id for u in db.execute(select(User)).scalars() if can_see_sales(u) and u.id not in demo]
    return [
        Alert(
            key="daily_sales_summary",
            dedupe_keys=[f"daily_sales_summary:{yesterday.isoformat()}"],
            title=f"{cmp['weekday']}: {insights.money(cmp['net_sales'])} net sales",
            body=body,
            data={"date": yesterday.isoformat()},
            user_ids=users,
        )
    ]


TRIGGERS: Dict[str, Callable[[Session, dt.datetime], List[Alert]]] = {
    "order_due": order_due_alerts,
    "critical_vendors": critical_vendors_alerts,
    "closing_checklist": closing_checklist_alerts,
    "invoice_failed": invoice_failed_alerts,
    "open_orders_morning": open_orders_morning_alerts,
    "fix_issues_reopened": fix_issues_reopened_alerts,
    "cart_removal_request": cart_removal_request_alerts,
    "inventory_upload_due": inventory_upload_due_alerts,
    "inventory_api_ready": inventory_api_ready_alerts,
    "loss_watch": loss_watch_alerts,
    "daily_sales_summary": daily_sales_summary_alerts,
}


def run_alert_job(key: str, now: Optional[dt.datetime] = None, db: Optional[Session] = None) -> int:
    """Scheduler entry point for one alert: compute, then dispatch each.
    Never raises - a broken trigger or an FCM outage must not take down the
    scheduler thread or anything else. Returns users reached (for tests)."""
    now = now or dt.datetime.now(restaurant_timezone())
    if in_quiet_hours(now):
        logger.debug("Notifications: %s skipped - quiet hours (%s)", key, now.strftime("%H:%M"))
        return 0
    own_session = db is None
    db = db or SessionLocal()
    reached = 0
    try:
        alerts = TRIGGERS[key](db, now)
        if alerts:  # not "found 0" every 10 minutes
            logger.info("Notifications: %s found %d alert(s)", key, len(alerts))
        for alert in alerts:
            try:
                reached += dispatch_alert(db, alert)
            except Exception:
                db.rollback()
                logger.exception("Notifications: dispatching %s failed", alert.dedupe_keys)
    except Exception:
        db.rollback()
        logger.exception("Notifications: %s trigger failed", key)
    finally:
        if own_session:
            db.close()
    return reached


# --- Morning summary (Oct 15, owner) ---------------------------------------------
# ONE 9:00 push per person instead of ~6 at the same minute. The 9:00 alerts
# below are computed once; each still goes out on its own to whoever turned
# that alert on (all off by default), and everyone with Morning summary on
# gets one push with a line for each of the rest they can see. In this order:
MORNING_SOURCES = [
    "critical_vendors",
    "order_due",
    "loss_watch",
    "fix_issues_reopened",
    "inventory_upload_due",
    "inventory_api_ready",
    "daily_sales_summary",
    "open_orders_morning",
]
MORNING_TITLE = "Morning summary"


def _order_due_line(alerts: List[Alert]) -> str:
    """"Order today: Babco, KP · tomorrow: Raja Foods"."""
    by_when: Dict[str, List[str]] = {}
    for a in alerts:
        when, _, vendor = a.title.partition(": ")
        by_when.setdefault(when.replace("Order ", ""), []).append(vendor)
    parts = [f"{when}: {_names_line(v, CRITICAL_NAMES_SHOWN)}" for when, v in by_when.items()]
    return "Order " + " \u00b7 ".join(parts)


def _first_part(text: str) -> str:
    return text.split(" \u00b7 ")[0].split(". ")[0]


MORNING_LINES: Dict[str, Callable[[List[Alert]], str]] = {
    "critical_vendors": lambda a: f"{a[0].title}: {a[0].body}",
    "order_due": _order_due_line,
    "loss_watch": lambda a: a[0].title,
    "fix_issues_reopened": lambda a: a[0].title,
    "inventory_upload_due": lambda a: f"{a[0].title} - {_first_part(a[0].body)}",
    "inventory_api_ready": lambda a: "Inventory from Toast is ready to turn on",
    "daily_sales_summary": lambda a: (
        f"{a[0].title} ({_first_part(a[0].body)})" if " vs " in f" {a[0].body}" else a[0].title
    ),
    "open_orders_morning": lambda a: a[0].title,
}


def _summary_recipients(db: Session) -> List[User]:
    """Users with a phone, Morning summary effectively on, never a demo login."""
    from app.models import DeviceToken

    ids = set(db.execute(select(DeviceToken.user_id)).scalars()) - demo_user_ids(db)
    if not ids:
        return []
    users = [
        u
        for u in db.execute(select(User).where(User.id.in_(list(ids)))).scalars().all()
        if user_can_access_alert(u, "morning_summary")
    ]
    on = enabled_user_ids(db, "morning_summary", users)
    return [u for u in users if u.id in on]


def morning_summary_lines(
    db: Session, user: User, computed: Dict[str, List[Alert]], separate: Dict[str, set]
) -> List[Tuple[str, str]]:
    """[(source key, line)] for one user: each 9:00 alert they can see,
    aimed at them (Alert.user_ids), and not already their own push."""
    lines = []
    for key in MORNING_SOURCES:
        alerts = computed.get(key) or []
        if not alerts or user.id in separate.get(key, set()) or not user_can_access_alert(user, key):
            continue
        mine = [a for a in alerts if a.user_ids is None or user.id in a.user_ids]
        if mine:
            lines.append((key, MORNING_LINES[key](mine)))
    return lines


def send_morning_summary(db: Session, now: dt.datetime, computed: Dict[str, List[Alert]]) -> int:
    """One push per recipient (dedupe morning_summary:<date>:<user>), then
    marks every alert that made it into a summary as sent, so it isn't
    repeated tomorrow (Fix Issues items back, loss-watch flags and the
    Inventory-from-Toast streak are each in one push only, as before)."""
    users = _summary_recipients(db)
    if not users:
        return 0
    separate = {key: enabled_user_ids(db, key, users) for key in MORNING_SOURCES}
    today = now.date().isoformat()
    reached_by_source: Dict[str, int] = {}
    reached = 0
    for user in users:
        lines = morning_summary_lines(db, user, computed, separate)
        if not lines:
            continue
        n = dispatch_alert(
            db,
            Alert(
                key="morning_summary",
                dedupe_keys=[f"morning_summary:{today}:{user.id}"],
                title=MORNING_TITLE,
                body="\n".join(line for _, line in lines),
                user_ids=[user.id],
            ),
        )
        reached += n
        if n:
            for key, _ in lines:
                reached_by_source[key] = reached_by_source.get(key, 0) + n
    if reached_by_source:
        sent_at = utcnow()
        for key, count in reached_by_source.items():
            for alert in computed.get(key) or []:
                for dedupe in alert.dedupe_keys:
                    if not sent_dedupe_keys(db, [dedupe]):
                        db.add(NotificationLog(key=key, dedupe_key=dedupe, sent_at=sent_at, recipient_count=count))
        try:
            db.commit()
        except Exception:  # noqa: BLE001 - logged concurrently: fine
            db.rollback()
    return reached


def run_morning_job(
    now: Optional[dt.datetime] = None, db: Optional[Session] = None, include_fix_issues: bool = True
) -> int:
    """The 9:00 job (app/scheduler.py): every MORNING_SOURCES trigger once,
    their separate pushes (only to whoever turned them on), then the Morning
    summary. `include_fix_issues=False` (SCHEDULER_ENABLED off) leaves the
    Fix Issues reopen to the list's lazy reopen, as before. Never raises;
    returns users reached (all pushes)."""
    now = now or dt.datetime.now(restaurant_timezone())
    if in_quiet_hours(now):
        return 0
    own_session = db is None
    db = db or SessionLocal()
    reached = 0
    try:
        computed: Dict[str, List[Alert]] = {}
        for key in MORNING_SOURCES:
            if key == "fix_issues_reopened" and not include_fix_issues:
                continue
            try:
                # Only what dispatch_alert would still send: a trigger that
                # returns the same alert every morning (inventory_api_ready,
                # once per streak) must not be in every day's summary.
                computed[key] = [
                    a for a in TRIGGERS[key](db, now) if set(a.dedupe_keys) - sent_dedupe_keys(db, a.dedupe_keys)
                ]
            except Exception:
                db.rollback()
                logger.exception("Notifications: %s trigger failed", key)
        for key, alerts in computed.items():
            for alert in alerts:
                try:
                    reached += dispatch_alert(db, alert)
                except Exception:
                    db.rollback()
                    logger.exception("Notifications: dispatching %s failed", alert.dedupe_keys)
        try:
            reached += send_morning_summary(db, now, computed)
        except Exception:
            db.rollback()
            logger.exception("Notifications: morning summary failed")
        found = {k: len(v) for k, v in computed.items() if v}
        if found:
            logger.info("Notifications: morning job found %s; reached %d", found, reached)
        prune_push_log(db)
    finally:
        if own_session:
            db.close()
    return reached
