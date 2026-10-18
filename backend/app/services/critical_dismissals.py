"""Critical vendors: Close / Skip this week / Remind me later
(docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md, migration 0047).

Owner (Oct 6): the red Critical tile on the app's Today card "looks annoying
every day". An admin can hide a vendor from that tile - and from the 9 AM
critical-vendors push - for everyone, three ways:
  - close ("Handled", with a reason): until something NEW turns critical for
    the vendor (an item that wasn't critical when it was closed), and
    CLOSE_MAX_DAYS at most, so nothing hides forever;
  - skip_week: until next Monday 00:00 store time (tomorrow on a Sunday);
  - snooze ("Remind me later"): until a chosen time (SNOOZE_PRESETS).
How urgency is worked out does not change (vendor minimum orders still
apply): the Reorder Candidates report keeps every vendor and its tier, and
each vendor group only gains `critical_dismissed` / `critical_dismissal`.

A dismissal is active while cleared_at is NULL and `until` is still ahead.
Rows are never deleted. How one ended is written down lazily, by whoever
looks next:
  - expired: `until` passed (settled on every read; cleared_at = until);
  - new_critical_item: a close whose vendor has a critical item it didn't
    have when it was closed. Judged on the Today report only - the app tile's
    settings (TODAY_LOOKBACK_DAYS of sales, TODAY_LEAD_TIME_DAYS lead time, as
    of today): the reorder-candidates endpoint at those settings, the 9 AM
    push, and every new dismissal. Once ended it stays ended, even if that
    item stops being critical again;
  - superseded: a newer action on the same vendor;
  - show_again: an admin's Show again (also the apps' Undo).
"""

import datetime as dt
import json
import logging
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CriticalVendorDismissal, User
from app.services import report_cache
from app.services.reorder import cached_report, compute_reorder_candidates, group_candidates_by_vendor, report_key
from app.services.supplier_projection import list_suppliers
from app.timeutil import restaurant_timezone, restaurant_today, utcnow
from app.tenancy import advisory_lock, tenant_lock

logger = logging.getLogger(__name__)

# The Today tile's report: the app's todayCriticalVendorsProvider and the
# critical-vendors push (notification_triggers.REORDER_*) use these.
TODAY_LOOKBACK_DAYS = 30
TODAY_LEAD_TIME_DAYS = 7

# Owner's rules (contract, Oct 6).
CLOSE_MAX_DAYS = 14  # a close comes back after this many days at most
LATER_TODAY_HOURS = 4  # "Later today" = this many hours from now...
LATER_TODAY_LATEST = dt.time(21, 0)  # ...but never past 9 PM store time
LATER_TODAY_CUTOFF = dt.time(17, 0)  # after 5 PM, "Later today" is tomorrow 9 AM
SNOOZE_TIME = dt.time(9, 0)  # the presets with a day come back at 9 AM
SNOOZE_IN_DAYS = 3  # "In 3 days (9 AM)"
SNOOZE_MAX_DAYS_AHEAD = 30  # "Pick a date": at most this many days ahead
WEEK_STARTS = dt.time(0, 0)  # a store week starts Monday 00:00
HISTORY_DAYS = 60  # GET ?include_history=1
NOTE_MAX_CHARS = 500

KIND_LABELS = {"close": "Closed", "skip_week": "Skipped this week", "snooze": "Remind me later"}
CLOSE_REASONS = [
    {"key": "ordered", "label": "Ordered / will order", "needs_note": False},
    {"key": "not_ordering", "label": "Not ordering from this vendor right now", "needs_note": False},
    {"key": "counts_wrong", "label": "Stock counts are wrong", "needs_note": False},
    {"key": "other", "label": "Other", "needs_note": True},
]
_REASON_LABELS = {r["key"]: r["label"] for r in CLOSE_REASONS}
SNOOZE_PRESETS = [
    {"key": "later_today", "label": "Later today"},
    {"key": "tomorrow", "label": "Tomorrow 9 AM"},
    {"key": "in_3_days", "label": "In 3 days (9 AM)"},
    {"key": "date", "label": "Pick a date"},
]
_PRESET_KEYS = {p["key"] for p in SNOOZE_PRESETS}
CLEARED_REASONS = ("show_again", "new_critical_item", "expired", "superseded")

CLOSE_ENDS_LABEL = "Until something new turns critical"
NOT_CRITICAL_WARNING = (
    "This vendor isn't critical right now, so it isn't on the Today tile. If it turns critical before this ends, "
    "it stays hidden."
)
# A Close goes by the vendor's critical ITEMS, not its tier: something new
# turning critical for it brings it back - and with no critical item stored
# (none at closing), anything critical is new. So it must not promise to
# stay hidden.
NOT_CRITICAL_CLOSE_WARNING = (
    "This vendor isn't critical right now, so it isn't on the Today tile. If something new turns critical for it, "
    "it shows up there."
)

# Makes "settle, replace this vendor's active one, add the new one" one step
# within this process (FastAPI runs these sync routes on a thread pool, and
# two admins' POSTs can finish their ~2 s report at the same moment) - so a
# vendor never ends up with two active dismissals. Same as
# purchase_order_log._write_lock.
_write_lock = tenant_lock("critical_dismissals._write_lock")

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class DismissalError(Exception):
    """A request the routes turn into an HTTP error; `detail` is ready to show."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def vendor_key(name: Optional[str]) -> str:
    """Vendors match ignoring case and outer spaces (reorder._vendor_key)."""
    return (name or "").strip().lower()


# --- Time rules (store time, America/New_York; DST-safe) -----------------------------------


def _utc(value: dt.datetime) -> dt.datetime:
    """Aware UTC. A naive value is taken as store-local time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=restaurant_timezone())
    return value.astimezone(dt.timezone.utc)


def _local(value: dt.datetime) -> dt.datetime:
    return _utc(value).astimezone(restaurant_timezone())


def _store_time(day: dt.date, at: dt.time) -> dt.datetime:
    """`at` o'clock store time on `day`, as aware UTC (the offset of that day,
    so 9 AM stays 9 AM across a DST change)."""
    return dt.datetime.combine(day, at, tzinfo=restaurant_timezone()).astimezone(dt.timezone.utc)


def _now(now: Optional[dt.datetime]) -> dt.datetime:
    return _utc(now) if now is not None else utcnow()


def skip_week_until(now: dt.datetime) -> dt.datetime:
    """The start of next store week: Monday 00:00 store time. On a Sunday
    that's tomorrow; on a Monday (even at 00:00) it's a week away."""
    local = _local(now)
    return _store_time(local.date() + dt.timedelta(days=7 - local.weekday()), WEEK_STARTS)


def later_today_until(now: dt.datetime) -> dt.datetime:
    """LATER_TODAY_HOURS from now, never past 9 PM store time; after 5 PM
    (5:00 PM itself still gets 9 PM) it's tomorrow 9 AM instead."""
    local = _local(now)
    if local.time() > LATER_TODAY_CUTOFF:
        return _store_time(local.date() + dt.timedelta(days=1), SNOOZE_TIME)
    return min(_utc(now) + dt.timedelta(hours=LATER_TODAY_HOURS), _store_time(local.date(), LATER_TODAY_LATEST))


def snooze_until(now: dt.datetime, preset: str, day: Optional[dt.date] = None) -> dt.datetime:
    """When a "Remind me later" preset comes back. `day` is for "date"."""
    today = _local(now).date()
    if preset == "later_today":
        return later_today_until(now)
    if preset == "tomorrow":
        return _store_time(today + dt.timedelta(days=1), SNOOZE_TIME)
    if preset == "in_3_days":
        return _store_time(today + dt.timedelta(days=SNOOZE_IN_DAYS), SNOOZE_TIME)
    if preset == "date":
        if day is None:
            raise DismissalError(400, "Pick a date.")
        if day > today + dt.timedelta(days=SNOOZE_MAX_DAYS_AHEAD):
            raise DismissalError(400, f"Pick a date at most {SNOOZE_MAX_DAYS_AHEAD} days ahead.")
        until = _store_time(day, SNOOZE_TIME)
        if until <= _utc(now):
            raise DismissalError(400, "That date has passed. Pick a later one (today works only before 9 AM).")
        return until
    raise DismissalError(400, "Pick when to be reminded: later today, tomorrow 9 AM, in 3 days or a date.")


def _explicit_until(now: dt.datetime, until: dt.datetime) -> dt.datetime:
    """A snooze sent with its own `until` instead of a preset."""
    value = _utc(until)
    if value <= _utc(now):
        raise DismissalError(400, "That time has passed. Pick a later one.")
    if _local(value).date() > _local(now).date() + dt.timedelta(days=SNOOZE_MAX_DAYS_AHEAD):
        raise DismissalError(400, f"Pick a time at most {SNOOZE_MAX_DAYS_AHEAD} days ahead.")
    return value


def _day_text(day: dt.date) -> str:
    """"Mon Oct 12" (fixed English names, whatever the server's locale)."""
    return f"{_DAYS[day.weekday()]} {_MONTHS[day.month - 1]} {day.day}"


def _clock(local: dt.datetime) -> str:
    """"1:30 PM", "9:00 AM"."""
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def ends_label(kind: str, until: dt.datetime, now: dt.datetime) -> str:
    """When it comes back, as text that reads after "Hidden from Today: "."""
    if kind == "close":
        return CLOSE_ENDS_LABEL
    end = _local(until)
    if kind == "skip_week":
        return f"Until {_day_text(end.date())}"
    today = _local(now).date()
    if end.date() == today:
        return f"Until {_clock(end)}"
    if end.date() == today + dt.timedelta(days=1):
        return f"Until tomorrow {_clock(end)}"
    return f"Until {_day_text(end.date())}, {_clock(end)}"


# --- The Today report -----------------------------------------------------------------------


def is_today_report(
    lookback_days: int, lead_time_days: int, as_of: Optional[dt.date] = None, method: Optional[str] = None
) -> bool:
    """Is a reorder-candidates request the Today tile's own report? Only that
    one may end a close ("something new turned critical"): another lookback
    or lead time can show other items as critical."""
    return (
        lookback_days == TODAY_LOOKBACK_DAYS
        and lead_time_days == TODAY_LEAD_TIME_DAYS
        and method is None
        and (as_of is None or as_of == restaurant_today())
    )


def today_report_groups(db: Session, now: Optional[dt.datetime] = None) -> List[dict]:
    """The Today report's vendor groups (the heavy part: about 2 s on prod
    when it isn't cached - report_cache). Read-only: the list is shared."""
    today = _local(_now(now)).date()
    report = cached_report(
        db, TODAY_LOOKBACK_DAYS, TODAY_LEAD_TIME_DAYS, today,
        candidates_fn=compute_reorder_candidates, group_fn=group_candidates_by_vendor,
    )
    return report["vendors"]


def cached_today_groups(db: Session, now: Optional[dt.datetime] = None) -> Optional[List[dict]]:
    """The Today report's groups if they are cached right now, else None
    (never computes)."""
    today = _local(_now(now)).date()
    found, value = report_cache.peek(
        report_key(TODAY_LOOKBACK_DAYS, TODAY_LEAD_TIME_DAYS, today, None), report_cache.data_version(db)
    )
    return value["vendors"] if found else None


def groups_by_key(groups: Iterable[dict]) -> Dict[str, List[dict]]:
    """The report's vendor groups by vendor_key, in report order (most urgent
    first). Usually one group per key, but the report groups by the exact
    name Toast gives, so a vendor spelled two ways ("Vdyas" on some items,
    "VDYAS" on others) has a group per spelling - and one dismissal (matched
    ignoring case) covers them all."""
    out: Dict[str, List[dict]] = {}
    for group in groups:
        out.setdefault(vendor_key(group.get("vendor")), []).append(group)
    return out


def critical_item_ids(groups: Union[dict, List[dict], None]) -> List[str]:
    """The items whose own tier is critical in a vendor's group (or all its
    groups, see groups_by_key), sorted."""
    if not groups:
        return []
    if isinstance(groups, dict):
        groups = [groups]
    return sorted(
        {
            str(item["item_id"])
            for group in groups
            for item in group.get("items") or []
            if item.get("item_id") is not None and (item.get("urgency") or {}).get("tier") == "critical"
        }
    )


def _stored_ids(row: CriticalVendorDismissal) -> Set[str]:
    """The close's critical items; unreadable JSON = none (so anything
    critical counts as new and the vendor shows again)."""
    try:
        ids = json.loads(row.critical_item_ids_json or "[]")
    except ValueError:
        return set()
    return {str(i) for i in ids} if isinstance(ids, list) else set()


# --- Settling and resolving ----------------------------------------------------------------


def _open_rows(db: Session) -> List[CriticalVendorDismissal]:
    """Not yet cleared, newest first."""
    return list(
        db.execute(
            select(CriticalVendorDismissal)
            .where(CriticalVendorDismissal.cleared_at.is_(None))
            .order_by(CriticalVendorDismissal.created_at.desc(), CriticalVendorDismissal.id.desc())
        ).scalars()
    )


def _came_back(row: CriticalVendorDismissal, by_key: Dict[str, List[dict]]) -> bool:
    """A close whose vendor now has a critical item it didn't have when it
    was closed. The same items still critical, or some resolved: no."""
    current = set(critical_item_ids(by_key.get(vendor_key(row.vendor))))
    return bool(current - _stored_ids(row))


def _settle(
    db: Session, now: dt.datetime, today_groups: Optional[Iterable[dict]] = None
) -> List[CriticalVendorDismissal]:
    """The active dismissals, newest first, after writing down the ones that
    ended: `until` passed -> expired (cleared_at = until); with the Today
    report's groups, a close that came back -> new_critical_item. Writes only
    when something changed."""
    by_key = groups_by_key(today_groups) if today_groups is not None else None
    active: List[CriticalVendorDismissal] = []
    changed = False
    for row in _open_rows(db):
        if row.until <= now:
            row.cleared_at, row.cleared_reason = row.until, "expired"
            changed = True
        elif by_key is not None and row.kind == "close" and _came_back(row, by_key):
            row.cleared_at, row.cleared_reason = now, "new_critical_item"
            changed = True
        else:
            active.append(row)
    if changed:
        db.commit()
    return active


def annotate_vendor_groups(
    db: Session, groups: List[dict], now: Optional[dt.datetime] = None, today_report: bool = False
) -> None:
    """Adds `critical_dismissed` (an active dismissal hides the vendor from
    Today, whatever its tier) and `critical_dismissal` to every vendor group,
    in place. `today_report`: these groups are the Today report, so a close
    whose vendor has something new critical ends here. Never raises: on any
    error every vendor shows (as before this feature)."""
    now = _now(now)
    try:
        active = _settle(db, now, groups if today_report else None)
    except Exception:  # noqa: BLE001
        logger.exception("Critical dismissals: lookup failed - showing every vendor")
        db.rollback()
        active = []
    hidden: dict = {}
    for row in active:  # newest first: the newest one wins
        hidden.setdefault(vendor_key(row.vendor), row)
    for group in groups:
        row = hidden.get(vendor_key(group.get("vendor")))
        group["critical_dismissed"] = row is not None
        group["critical_dismissal"] = group_dismissal_out(row, now) if row is not None else None


# --- Output -------------------------------------------------------------------------------------


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def cleared_reason_label(row: CriticalVendorDismissal) -> Optional[str]:
    reason = row.cleared_reason
    if reason == "show_again":
        return "Shown again"
    if reason == "new_critical_item":
        return "Back: something new turned critical"
    if reason == "superseded":
        return "Replaced by a newer action"
    if reason == "expired":
        if row.kind == "close":
            return f"Back after {CLOSE_MAX_DAYS} days"
        if row.kind == "skip_week":
            return "Back: the new week started"
        return "Back: reminder time"
    return None


def dismissal_out(row: CriticalVendorDismissal, now: Optional[dt.datetime] = None) -> dict:
    now = _now(now)
    ids = _stored_ids(row) if row.kind == "close" else None
    return {
        "id": row.id,
        "vendor": row.vendor,
        "kind": row.kind,
        "kind_label": KIND_LABELS.get(row.kind, row.kind),
        "reason": row.reason,
        "reason_label": _REASON_LABELS.get(row.reason) if row.reason else None,
        "note": row.note,
        "until": _iso(row.until),
        "created_by_name": row.created_by_name,
        "created_at": _iso(row.created_at),
        "ends_label": ends_label(row.kind, row.until, now),
        "critical_item_count": len(ids) if ids is not None else None,
    }


_GROUP_FIELDS = (
    "id", "kind", "kind_label", "ends_label", "created_by_name", "until", "reason", "reason_label", "note",
    "created_at",
)


def group_dismissal_out(row: CriticalVendorDismissal, now: Optional[dt.datetime] = None) -> dict:
    """`critical_dismissal` on a reorder vendor group."""
    full = dismissal_out(row, now)
    return {key: full[key] for key in _GROUP_FIELDS}


def history_out(row: CriticalVendorDismissal, now: Optional[dt.datetime] = None) -> dict:
    now = _now(now)
    out = dismissal_out(row, now)
    out.update(
        {
            "active": row.cleared_at is None and row.until > now,
            "cleared_at": _iso(row.cleared_at),
            "cleared_by_name": row.cleared_by_name,
            "cleared_reason": row.cleared_reason,
            "cleared_reason_label": cleared_reason_label(row),
        }
    )
    return out


def presets_out(now: Optional[dt.datetime] = None) -> List[dict]:
    """The "Remind me later" choices, with when each would end right now
    (and the date range "Pick a date" allows)."""
    now = _now(now)
    local = _local(now)
    out = []
    for preset in SNOOZE_PRESETS:
        item = dict(preset)
        if preset["key"] == "date":
            first = local.date() if local.time() < SNOOZE_TIME else local.date() + dt.timedelta(days=1)
            item.update(
                until=None,
                ends_label=None,
                min_date=first.isoformat(),
                max_date=(local.date() + dt.timedelta(days=SNOOZE_MAX_DAYS_AHEAD)).isoformat(),
            )
        else:
            until = snooze_until(now, preset["key"])
            item.update(until=until.isoformat(), ends_label=ends_label("snooze", until, now))
        out.append(item)
    return out


def options_out(now: Optional[dt.datetime] = None) -> dict:
    now = _now(now)
    week = skip_week_until(now)
    return {
        "reasons": CLOSE_REASONS,
        "presets": presets_out(now),
        "skip_week": {"until": week.isoformat(), "ends_label": ends_label("skip_week", week, now)},
        "close_max_days": CLOSE_MAX_DAYS,
    }


# --- Reads -------------------------------------------------------------------------------------


def list_active(db: Session, now: Optional[dt.datetime] = None) -> List[dict]:
    """Every active dismissal, newest first (stored state; a close that came
    back is ended the next time the Today report is computed)."""
    now = _now(now)
    return [dismissal_out(row, now) for row in _settle(db, now)]


def list_history(db: Session, now: Optional[dt.datetime] = None) -> List[dict]:
    """Dismissals made in the last HISTORY_DAYS, newest first, active ones
    included."""
    now = _now(now)
    _settle(db, now)
    rows = db.execute(
        select(CriticalVendorDismissal)
        .where(CriticalVendorDismissal.created_at >= now - dt.timedelta(days=HISTORY_DAYS))
        .order_by(CriticalVendorDismissal.created_at.desc(), CriticalVendorDismissal.id.desc())
    ).scalars()
    return [history_out(row, now) for row in rows]


# --- Writes ------------------------------------------------------------------------------------


def _reason_key(raw: Optional[str]) -> Optional[str]:
    """A close reason by key, or by its label (any case)."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    for reason in CLOSE_REASONS:
        if text in (reason["key"], reason["label"].lower()):
            return reason["key"]
    return None


def _clean_note(note: Optional[str]) -> Optional[str]:
    if note is None:
        return None
    text = str(note).strip()
    if len(text) > NOTE_MAX_CHARS:
        raise DismissalError(400, f"Keep the note to {NOTE_MAX_CHARS} characters or fewer.")
    return text or None


def _ends_for(
    kind: str,
    now: dt.datetime,
    reason: Optional[str],
    note: Optional[str],
    preset: Optional[str],
    day: Optional[dt.date],
    until: Optional[dt.datetime],
) -> Tuple[dt.datetime, Optional[str]]:
    """(until, reason key) for a new dismissal, or DismissalError(400)."""
    if kind == "close":
        key = _reason_key(reason)
        if key is None:
            raise DismissalError(400, "Pick a reason for closing.")
        if key == "other" and not note:
            raise DismissalError(400, "Add a note for Other.")
        return now + dt.timedelta(days=CLOSE_MAX_DAYS), key
    if kind == "skip_week":
        return skip_week_until(now), None
    if kind == "snooze":
        if preset:
            if preset not in _PRESET_KEYS:
                raise DismissalError(400, "Pick when to be reminded: later today, tomorrow 9 AM, in 3 days or a date.")
            return snooze_until(now, preset, day), None
        if until is not None:
            return _explicit_until(now, until), None
        raise DismissalError(400, "Pick when to be reminded: later today, tomorrow 9 AM, in 3 days or a date.")
    raise DismissalError(400, "Pick Close, Skip this week or Remind me later.")


def create_dismissal(
    db: Session,
    user: User,
    vendor: str,
    kind: str,
    reason: Optional[str] = None,
    note: Optional[str] = None,
    preset: Optional[str] = None,
    day: Optional[dt.date] = None,
    until: Optional[dt.datetime] = None,
    now: Optional[dt.datetime] = None,
) -> Tuple[CriticalVendorDismissal, Optional[str]]:
    """A new close / skip_week / snooze for `vendor` (admins; the route
    checks), superseding the vendor's active one. Returns (row, warning):
    warning is set when the vendor isn't critical right now (it's still
    saved). 404 for a vendor that's neither in the Today report nor a
    supplier in the latest inventory upload.

    Only a close needs the Today report (its critical items are stored). A
    skip_week / snooze uses it only when it's already cached (speed #2,
    Oct 15 - it took ~1.7 s per tap, 3.9 s next to Home's reports): without
    it the vendor's spelling comes from the supplier list, settling is
    expiry-only (the next Today report read ends a close that came back,
    annotate_vendor_groups) and `warning` is None (not known)."""
    now = _now(now)
    name = (vendor or "").strip()
    if not name:
        raise DismissalError(400, "Pick a vendor.")
    note = _clean_note(note)
    ends, reason_key = _ends_for(kind, now, reason, note, preset, day, until)

    groups = today_report_groups(db, now) if kind == "close" else cached_today_groups(db, now)
    # Every group of this vendor (one per spelling Toast uses, most urgent
    # first): the close stores the critical items of them all.
    same = (groups_by_key(groups).get(vendor_key(name)) or []) if groups is not None else []
    if same:
        name = same[0]["vendor"]
    else:
        suppliers = {vendor_key(s): s for s in list_suppliers(db)}
        if vendor_key(name) in suppliers:
            name = suppliers[vendor_key(name)]
        else:
            if groups is None:
                # Not a supplier: only the report can know it (its
                # "Unassigned" group) - rare, so worth the computation.
                groups = today_report_groups(db, now)
                same = groups_by_key(groups).get(vendor_key(name)) or []
            if not same:
                raise DismissalError(404, f"Unknown vendor: {name}")
            name = same[0]["vendor"]

    with _write_lock, advisory_lock(db, "critical_dismissals"):
        # Settle first (an ended one is recorded as expired / came back, not
        # as replaced), then this vendor's active ones are superseded.
        for row in _settle(db, now, groups):
            if vendor_key(row.vendor) == vendor_key(name):
                row.cleared_at, row.cleared_reason = now, "superseded"
                row.cleared_by_user_id, row.cleared_by_name = user.id, user.username
        row = CriticalVendorDismissal(
            vendor=name,
            kind=kind,
            reason=reason_key,
            note=note,
            until=ends,
            critical_item_ids_json=json.dumps(critical_item_ids(same)) if kind == "close" else None,
            created_by_user_id=user.id,
            created_by_name=user.username,
            created_at=now,
        )
        db.add(row)
        db.commit()
    db.refresh(row)
    if groups is None:
        return row, None  # skip / snooze with the report not cached: not known
    critical = any(g["urgency"]["tier"] == "critical" and not g.get("inactive") for g in same)
    if critical:
        return row, None
    return row, NOT_CRITICAL_CLOSE_WARNING if kind == "close" else NOT_CRITICAL_WARNING


def show_again(db: Session, user: User, dismissal_id: int, now: Optional[dt.datetime] = None) -> bool:
    """Show again / Undo: ends the dismissal - and any other active one for
    the same vendor (two admins acting at once), so the vendor really is back
    on Today. One that already ended is left as it is (an Undo after someone
    else's newer action doesn't undo theirs). False = no such id."""
    row = db.get(CriticalVendorDismissal, dismissal_id)
    if row is None:
        return False
    now = _now(now)
    with _write_lock, advisory_lock(db, "critical_dismissals"):
        active = _settle(db, now)
        if row not in active:
            return True
        for other in active:
            if vendor_key(other.vendor) == vendor_key(row.vendor):
                other.cleared_at, other.cleared_reason = now, "show_again"
                other.cleared_by_user_id, other.cleared_by_name = user.id, user.username
        db.commit()
    return True
