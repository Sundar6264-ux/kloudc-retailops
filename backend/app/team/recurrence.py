"""Recurring tasks (design §8.2): when each occurrence of a template is due,
and creating the ones whose time has come.

- `rrule` is an RFC 5545 subset describing when each occurrence is DUE,
  evaluated in the template's timezone (the store's by default) with
  python-dateutil. The task appears `generate_ahead_minutes` before it's due.
- DST: a local time that doesn't exist (2:30 AM on the spring-forward
  Sunday) moves forward to the next valid time (3:00 AM); an ambiguous one
  (1:30 AM on the fall-back Sunday) takes the first occurrence.
- Generation is idempotent: UNIQUE(template_id, due_at) - the job just tries
  every occurrence whose generate time has passed and skips existing ones.
- Catch-up: occurrences due within the last CATCH_UP window are still
  created (already overdue); older missed ones are skipped and logged.
  Nothing is created for a due time before the template itself existed.
- At most MAX_PER_DAY occurrences a day (BYHOUR x BYMINUTE), so one typo
  can't flood the task list.
- A template whose assignee is gone (deleted user, user without Tasks,
  archived team) generates nothing until it's fixed; that's logged.
"""

import datetime as dt
import json
import logging
import zoneinfo
from typing import Dict, List, Optional, Set, Tuple

from dateutil.rrule import rrulestr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.features import user_has_feature
from app.models import User
from app.team.models import Team
from app.team.tasks_models import Task, TaskEvent, TaskItem, TaskTemplate

logger = logging.getLogger(__name__)

# A template without a timezone uses the store's (STORE_TIMEZONE):
# default_timezone(). DEFAULT_TIMEZONE still reads it (module __getattr__).


def default_timezone() -> str:
    from app.store.settings_view import store_timezone_name

    return store_timezone_name()
CATCH_UP = dt.timedelta(hours=12)
MAX_GENERATE_AHEAD_MINUTES = 7 * 24 * 60
MAX_PER_DAY = 24
# The first tick after a (re)start looks this far back for occurrences that
# were missed entirely (only to log them).
MISSED_LOOKBACK = dt.timedelta(days=30)

_FREQS = {"DAILY", "WEEKLY", "MONTHLY"}
_DAYS = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}
_ALLOWED_KEYS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYHOUR", "BYMINUTE"}


class RRuleError(ValueError):
    pass


def _int_list(value: str, lo: int, hi: int, key: str, allow_negative: bool = False) -> List[int]:
    out = []
    for part in value.split(","):
        try:
            n = int(part)
        except ValueError:
            raise RRuleError(f"{key} must be numbers")
        ok = lo <= n <= hi or (allow_negative and -hi <= n <= -1)
        if not ok:
            raise RRuleError(f"{key} value {n} is out of range")
        out.append(n)
    return out


def normalize_rrule(text: str) -> str:
    """Validates the supported subset and returns it in a canonical form
    (upper case, fixed key order). Raises RRuleError with a readable reason.
    """
    raw = (text or "").strip()
    if raw.upper().startswith("RRULE:"):
        raw = raw[6:]
    if not raw:
        raise RRuleError("A repeat rule is required")
    parts: Dict[str, str] = {}
    for piece in raw.split(";"):
        if not piece.strip():
            continue
        if "=" not in piece:
            raise RRuleError(f"Bad rule part: {piece}")
        key, value = piece.split("=", 1)
        key = key.strip().upper()
        value = value.strip().upper()
        if key not in _ALLOWED_KEYS:
            raise RRuleError(f"{key} isn't supported (use FREQ, INTERVAL, BYDAY, BYMONTHDAY, BYHOUR, BYMINUTE)")
        if key in parts:
            raise RRuleError(f"{key} is given twice")
        parts[key] = value
    freq = parts.get("FREQ")
    if freq not in _FREQS:
        raise RRuleError("FREQ must be DAILY, WEEKLY or MONTHLY")
    if "BYHOUR" not in parts:
        raise RRuleError("BYHOUR (the hour it's due) is required")
    out = [f"FREQ={freq}"]
    if "INTERVAL" in parts:
        out.append(f"INTERVAL={_int_list(parts['INTERVAL'], 1, 52, 'INTERVAL')[0]}")
    if "BYDAY" in parts:
        days = [d.strip() for d in parts["BYDAY"].split(",")]
        if not days or any(d not in _DAYS for d in days):
            raise RRuleError("BYDAY must be days like MO,WE,FR")
        out.append("BYDAY=" + ",".join(days))
    if "BYMONTHDAY" in parts:
        if freq != "MONTHLY":
            raise RRuleError("BYMONTHDAY only works with FREQ=MONTHLY")
        days = _int_list(parts["BYMONTHDAY"], 1, 31, "BYMONTHDAY", allow_negative=True)
        out.append("BYMONTHDAY=" + ",".join(str(d) for d in days))
    hours = _int_list(parts["BYHOUR"], 0, 23, "BYHOUR")
    out.append("BYHOUR=" + ",".join(str(h) for h in hours))
    minutes = _int_list(parts.get("BYMINUTE", "0"), 0, 59, "BYMINUTE")
    out.append("BYMINUTE=" + ",".join(str(m) for m in minutes))
    if len(set(hours)) * len(set(minutes)) > MAX_PER_DAY:
        raise RRuleError(f"A schedule can repeat at most {MAX_PER_DAY} times a day")
    return ";".join(out)


def get_zone(name: Optional[str]) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(name or default_timezone())
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise RRuleError(f"Unknown timezone: {name}")


def local_to_utc(local: dt.datetime, tz: zoneinfo.ZoneInfo) -> dt.datetime:
    """A naive local wall time -> aware UTC, with the DST rules above.

    fold=0 picks the first of two ambiguous times. A time in the
    spring-forward gap doesn't survive a round trip; it moves forward
    minute by minute to the first wall time that exists (the end of the gap).
    """
    candidate = local
    for _ in range(24 * 60):
        aware = candidate.replace(tzinfo=tz, fold=0)
        back = aware.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
        if back == candidate:
            return aware.astimezone(dt.timezone.utc)
        candidate = (candidate + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    raise RRuleError("Could not resolve a local time")  # pragma: no cover


def _rule_parts(rule: str) -> Dict[str, str]:
    return dict(p.split("=", 1) for p in rule.split(";"))


def _advance_start(rule: str, dtstart: dt.datetime, lo: dt.datetime) -> dt.datetime:
    """A later dtstart that gives exactly the same occurrences from `lo` on,
    so a years-old template isn't re-expanded from its creation day on every
    5-minute tick. The shift is a whole number of the rule's periods (days
    x INTERVAL, weeks x INTERVAL, months x INTERVAL), which keeps INTERVAL
    counting, the weekday of a WEEKLY rule and the week alignment dateutil
    uses. MONTHLY only moves when dtstart's day exists in every month (<= 28).
    """
    if lo <= dtstart:
        return dtstart
    parts = _rule_parts(rule)
    interval = int(parts.get("INTERVAL", "1"))
    freq = parts["FREQ"]
    margin = lo - dt.timedelta(days=1)  # stay safely before lo
    if freq in ("DAILY", "WEEKLY"):
        period = interval * (1 if freq == "DAILY" else 7)
        steps = (margin - dtstart).days // period
        return dtstart + dt.timedelta(days=max(0, steps) * period)
    if dtstart.day > 28:
        return dtstart
    months = (margin.year - dtstart.year) * 12 + (margin.month - dtstart.month) - 1
    steps = max(0, months // interval)
    total = dtstart.month - 1 + steps * interval
    return dtstart.replace(year=dtstart.year + total // 12, month=total % 12 + 1)


def due_times_between(
    rule: str,
    tz_name: Optional[str],
    anchor_utc: dt.datetime,
    start_utc: dt.datetime,
    end_utc: dt.datetime,
) -> List[dt.datetime]:
    """Every due time (aware UTC) of `rule` within [start_utc, end_utc].

    The rule is anchored at local midnight of `anchor_utc`'s date (the
    template's creation), so INTERVAL=2 counts from there.
    """
    tz = get_zone(tz_name)
    rule = normalize_rrule(rule)
    anchor_local = anchor_utc.astimezone(tz).replace(tzinfo=None)
    dtstart = anchor_local.replace(hour=0, minute=0, second=0, microsecond=0)
    # Local naive bounds with a margin for the UTC offset/DST, then filter
    # exactly after converting.
    lo = start_utc.astimezone(tz).replace(tzinfo=None) - dt.timedelta(hours=3)
    hi = end_utc.astimezone(tz).replace(tzinfo=None) + dt.timedelta(hours=3)
    dtstart = _advance_start(rule, dtstart, lo)
    rr = rrulestr(rule, dtstart=dtstart)
    out = []
    seen = set()
    for local in rr.between(max(lo, dtstart), hi, inc=True):
        due = local_to_utc(local, tz)
        if start_utc <= due <= end_utc and due not in seen:
            seen.add(due)
            out.append(due)
    return out


def preview(
    rule: str,
    tz_name: Optional[str],
    generate_ahead_minutes: int,
    now: dt.datetime,
    count: int = 5,
    anchor: Optional[dt.datetime] = None,
) -> List[dict]:
    """The next `count` occurrences after `now` - what the template editor
    shows so a DST or rule mistake is obvious before saving.

    `anchor` is the saved template's creation time: generation counts
    INTERVAL (and a WEEKLY rule's weekday) from it, so the preview must too.
    A rule not saved yet is anchored at `now`, where it would start."""
    tz = get_zone(tz_name)
    ahead = dt.timedelta(minutes=generate_ahead_minutes)
    anchor = anchor or now
    out: List[dict] = []
    window_end = now
    # Widen the search until enough occurrences turn up (a monthly rule on
    # the 31st needs more than a month).
    for days in (8, 40, 400):
        window_end = now + dt.timedelta(days=days)
        dues = due_times_between(rule, tz_name, anchor, max(now, anchor), window_end)
        if len(dues) >= count:
            break
    for due in dues[:count]:
        out.append(
            {
                "due_at_utc": due.isoformat(),
                "due_at_local": due.astimezone(tz).isoformat(),
                "generate_at_local": (due - ahead).astimezone(tz).isoformat(),
            }
        )
    return out


def validate_template_fields(rule: str, tz_name: Optional[str], generate_ahead_minutes: int) -> Tuple[str, str]:
    """Returns (normalized rule, timezone name) or raises RRuleError."""
    normalized = normalize_rrule(rule)
    tz = tz_name or default_timezone()
    get_zone(tz)
    if not (0 <= generate_ahead_minutes <= MAX_GENERATE_AHEAD_MINUTES):
        raise RRuleError("generate_ahead_minutes must be between 0 and 10080 (7 days)")
    return normalized, tz


# When the previous generation run happened (per process) - bounds the
# "missed entirely" check to what fell out of the catch-up window since.
_last_run: Optional[dt.datetime] = None
# Templates whose bad assignee was already logged this process.
_warned_assignee: Set[int] = set()
# (template, user, team) weekday assignees whose problem was logged.
_warned_day: Set[Tuple[int, Optional[int], Optional[int]]] = set()


WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def parse_day_assignees(raw: Optional[str]) -> Dict[str, Tuple[Optional[int], Optional[int]]]:
    """task_templates.day_assignees_json -> {"MO": (user_id, team_id)}.
    Junk is ignored (that day then uses the template's own assignee)."""
    out: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except ValueError:
        return out
    if not isinstance(data, dict):
        return out
    for day, who in data.items():
        if day not in WEEKDAY_CODES or not isinstance(who, dict):
            continue
        uid, tid = who.get("user_id"), who.get("team_id")
        if type(uid) is int and tid is None:
            out[day] = (uid, None)
        elif type(tid) is int and uid is None:
            out[day] = (None, tid)
    return out


def parse_items(raw: Optional[str]) -> List[str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(x) for x in data if isinstance(x, str) and x.strip()] if isinstance(data, list) else []


def occurrence_assignee(tpl: TaskTemplate, due_utc: dt.datetime) -> Tuple[Optional[int], Optional[int]]:
    """Who one occurrence is for: the template's assignee for that weekday
    (store-local weekday of its due time), else its own assignee."""
    by_day = parse_day_assignees(tpl.day_assignees_json)
    if by_day:
        day = WEEKDAY_CODES[due_utc.astimezone(get_zone(tpl.timezone)).weekday()]
        if day in by_day:
            return by_day[day]
    return tpl.assignee_user_id, tpl.assignee_team_id


def problem_with(db: Session, user_id: Optional[int], team_id: Optional[int]) -> Optional[str]:
    """Why work can't be generated for this person / team, or None."""
    if user_id is not None:
        user = db.get(User, user_id)
        if user is None:
            return "its assignee was deleted"
        if not user_has_feature(user, "team_tasks"):
            return f"{user.username} no longer has Tasks"
        return None
    team = db.get(Team, team_id) if team_id is not None else None
    if team is None:
        return "its team no longer exists"
    if team.archived_at is not None:
        return f"the {team.name} team is archived"
    # Oct 15: an occurrence nobody is in the team to see helps nobody.
    from app.team.changes import team_member_user_ids

    if not team_member_user_ids(db, team):
        return f"the {team.name} team has no members"
    return None


def assignee_problem(db: Session, tpl: TaskTemplate) -> Optional[str]:
    """Why a template can't generate for its (default) assignee, or None."""
    return problem_with(db, tpl.assignee_user_id, tpl.assignee_team_id)


def _log_missed(db: Session, tpl: TaskTemplate, now: dt.datetime, since: Optional[dt.datetime]) -> None:
    """Logs occurrences that fell out of the catch-up window without being
    created (the server was down longer than CATCH_UP)."""
    lo = max(tpl.created_at, (since or now - MISSED_LOOKBACK) - CATCH_UP)
    hi = now - CATCH_UP - dt.timedelta(microseconds=1)
    if lo >= hi:
        return
    dues = due_times_between(tpl.rrule, tpl.timezone, tpl.created_at, lo, hi)
    if not dues:
        return
    existing = set(
        db.execute(select(Task.due_at).where(Task.template_id == tpl.id, Task.due_at.in_(dues))).scalars()
    )
    missed = [d for d in dues if d not in existing]
    if missed:
        logger.warning(
            "Recurring task template %s (%r): skipped %d occurrence(s) missed by more than %s (%s .. %s)",
            tpl.id, tpl.title, len(missed), CATCH_UP, missed[0].isoformat(), missed[-1].isoformat(),
        )


def generate_occurrences(db: Session, now: dt.datetime) -> List[Task]:
    """Creates every template occurrence whose generate time has passed and
    that doesn't exist yet. Commits. Returns the new tasks."""
    global _last_run
    created: List[Task] = []
    since = _last_run
    templates = db.execute(
        select(TaskTemplate).where(TaskTemplate.active.is_(True), TaskTemplate.repeats.is_not(False))
    ).scalars().all()
    for tpl in templates:
        # Only a template with no weekday assignees stops outright; with
        # them, each occurrence's own assignee is checked below.
        problem = assignee_problem(db, tpl) if not parse_day_assignees(tpl.day_assignees_json) else None
        if problem is not None:
            if tpl.id not in _warned_assignee:
                _warned_assignee.add(tpl.id)
                logger.warning("Task template %s (%r) generates nothing: %s", tpl.id, tpl.title, problem)
            continue
        _warned_assignee.discard(tpl.id)
        steps = parse_items(tpl.items_json)
        ahead = dt.timedelta(minutes=tpl.generate_ahead_minutes)
        start = max(now - CATCH_UP, tpl.created_at)
        try:
            _log_missed(db, tpl, now, since)
            dues = due_times_between(tpl.rrule, tpl.timezone, tpl.created_at, start, now + ahead)
        except RRuleError:
            logger.exception("Task template %s has an invalid rule %r - skipped", tpl.id, tpl.rrule)
            continue
        if not dues:
            continue
        existing = set(
            db.execute(
                select(Task.due_at).where(Task.template_id == tpl.id, Task.due_at.in_(dues))
            ).scalars().all()
        )
        for due in dues:
            if due in existing:
                continue
            user_id, team_id = occurrence_assignee(tpl, due)
            problem = problem_with(db, user_id, team_id)
            if problem is not None:
                key = (tpl.id, user_id, team_id)
                if key not in _warned_day:
                    _warned_day.add(key)
                    logger.warning("Task template %s (%r): skipped %s - %s", tpl.id, tpl.title, due.isoformat(), problem)
                continue
            task = Task(
                template_id=tpl.id,
                title=tpl.title,
                description=tpl.description,
                requires=tpl.requires,
                priority=tpl.priority,
                assignee_user_id=user_id,
                assignee_team_id=team_id,
                due_at=due,
                status="open",
                created_by_user_id=tpl.created_by_user_id,
                created_by_name=tpl.created_by_name,
                created_at=now,
                updated_at=now,
            )
            # A savepoint per task: a concurrent run that inserted the same
            # occurrence first just makes this one a no-op.
            try:
                with db.begin_nested():
                    db.add(task)
                    db.flush()
                    for pos, text in enumerate(steps):
                        db.add(TaskItem(task_id=task.id, position=pos, text=text, done=False))
                    db.add(
                        TaskEvent(
                            task_id=task.id,
                            user_id=None,
                            user_name="Schedule",
                            kind="created",
                            detail_json=None,
                            created_at=now,
                        )
                    )
                    _on_generated(db, task)
                    db.flush()
            except IntegrityError:
                continue
            created.append(task)
    db.commit()
    _last_run = now
    if created:
        logger.info("Recurring tasks: created %d occurrence(s)", len(created))
    return created


def _on_generated(db: Session, task: Task) -> None:
    # Imported here: tasks_service imports this module.
    from app.team.tasks_service import record_task_change

    record_task_change(db, task, "created")


def __getattr__(name):
    if name == "DEFAULT_TIMEZONE":
        return default_timezone()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
