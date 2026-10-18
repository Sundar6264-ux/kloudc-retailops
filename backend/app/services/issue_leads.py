"""Category & vendor leads for Fix Issues, and their daily Team task
(docs/BATCH_OCT14_CONTRACT.md, W4).

Owner's decision: "category" = PO section (the store aisles the PO PDF
groups by - po_section_rules), because that's how the leads are split in the
owner's lead document (the store pack's issue_leads).

Who an item belongs to (owner's rule): the lead of its PO SECTION; if that
section has no lead, the lead of one of its VENDORS (Toast's ";"-list, in
the order Toast lists them); if none of those has one, the "everything else"
lead (an admin, kind "fallback"). No lead anywhere = unassigned.

Only SAVED leads count (issue_leads rows). Until an admin saves the leads
page nothing is assigned and no task is created: the page shows a proposed
mapping read from the owner's document (the store pack's issue_leads, typed
in from the document - the app never reads the document itself) for review.

Daily task: once a day, at an admin-set time (default 10:00 store time),
each lead gets ONE Team task listing how many open Fix Issues items they
have, with a link to Fix Issues filtered to them. Due after an admin-set
number of hours (per lead, else per issue type - the shortest among the
types in the task - else 6). Run again the same day (Run now / a settings
change), today's task is updated, never duplicated. The next day's task
replaces an earlier one still open (cancelled "Replaced by ..."), so fixed
or closed items drop off; a lead left with nothing gets the old one closed
as done.

Live progress (Oct 15, owner): the task reads "Fix Issues: N left of M" -
M = the morning's items (issue_lead_task_runs.items_json), N = those not
closed on Fix Issues since. Recomputed right after every Fix Issues close /
reopen (team/source_sync.py) and by the 5-minute task job - from the
morning list and the issue_resolutions table only, never the ~6 s full
recompute. At 0 left the task closes itself. Its only push is the morning
"New task" (no due-soon / overdue reminders - tasks_service.sends_reminders).
"""

import datetime as dt
import json
import logging
import re
import threading
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.demo import demo_user_ids
from app.features import user_has_feature
from app.issue_lead_models import IssueLead, IssueLeadTaskRun
from app.models import JobRun, User
from app.services.item_vendors import vendors_of
from app.setting_models import SettingChange
from app.timeutil import restaurant_timezone, utcnow
from app.store.settings_view import effective_public_base_url

logger = logging.getLogger(__name__)

JOB_NAME = "issue_lead_tasks"
SOURCE_TYPE = "issue_leads"
SETTINGS_KEY = "issue_lead_tasks"

# The legacy "Dead inventory" list is only kept for old app builds - the
# website and new builds show "Dead with stock" instead (fix_issues.py).
EXCLUDED_TASK_TYPES = {"dead_stock"}

DEFAULT_SETTINGS = {
    "enabled": True,
    "time": "10:00",
    "default_hours": 6,
    "hours_by_user": {},
    "hours_by_type": {},
    # Issue types left out of the daily task (still shown in Fix Issues).
    "types_off": [],
}
EARLIEST_TIME = dt.time(6, 0)
LATEST_TIME = dt.time(20, 0)
MIN_HOURS = 1
MAX_HOURS = 72
MAX_SECTIONS_IN_TASK = 6

_settings_lock = threading.Lock()
_run_lock = threading.Lock()

# ---------------------------------------------------------------------------
# The proposed leads: the store pack's issue_leads section (the owner's lead
# document, typed in - names exactly as printed there).
# ---------------------------------------------------------------------------

DOCUMENT = "document"  # the department is named in the document
GUESS = "guess"  # not named - a suggestion from what the section holds


def _doc_leads() -> List[dict]:
    from app.store import pack as store_pack

    return [lead.model_dump() for lead in store_pack.issue_leads().leads]


def _doc_fallback() -> dict:
    """"Then the admin" (owner's rule): the pack's fallback lead."""
    from app.store import pack as store_pack

    return store_pack.issue_leads().fallback.model_dump()


def _section_proposals() -> Dict[str, Tuple[str, str, str]]:
    """PO section (lower case) -> (lead key, basis, why). Sections not listed
    get no proposal (the vendor lead, then the admin, covers them until set)."""
    from app.store import pack as store_pack

    return {k: (p.lead, p.basis, p.why) for k, p in store_pack.issue_leads().section_proposals.items()}


def _section_notes() -> Dict[str, str]:
    """Sections the document leaves open on purpose (mixed aisles): explained
    on the page instead of guessed."""
    from app.store import pack as store_pack

    return dict(store_pack.issue_leads().section_notes)


def _document_title() -> str:
    from app.store import pack as store_pack

    return store_pack.issue_leads().document_title


_LEGACY_NAMES = {
    "DOC_LEADS": _doc_leads,
    "DOC_FALLBACK": _doc_fallback,
    "SECTION_PROPOSALS": _section_proposals,
    "SECTION_NOTES": _section_notes,
}


def __getattr__(name):
    # The old constants, now the active store pack's (read at call time).
    if name in _LEGACY_NAMES:
        return _LEGACY_NAMES[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Matching names
# ---------------------------------------------------------------------------

_CORPORATE = {
    "llc", "inc", "co", "corp", "ltd", "usa", "distributors", "distributor", "enterprises", "traders",
    "international", "overseas", "imports", "foods", "food", "produce", "farm", "farms", "trade", "global",
}


def _words(name: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (name or "").lower().replace("'", ""))


def _compact(name: str) -> str:
    return "".join(_words(name))


def _core(name: str) -> str:
    words = _words(name)
    kept = [w for w in words if w not in _CORPORATE]
    return "".join(kept or words)


def vendor_candidates(doc_name: str, vendors: Iterable[str]) -> List[str]:
    """Inventory vendor names a document vendor name matches: the same
    letters ("Aldi's" = "Aldis", "Rajafoods" = "Raja Foods"), a name it
    starts ("Shata" -> "Shata Traders"), its initials ("HOS" -> "House of
    Spices"), or a near-identical core name ("Indigeneous" -> "Indigenous
    Foods"). Exact matches win over the looser rules."""
    vendors = list(dict.fromkeys(v for v in vendors if v and v.strip()))
    doc = _compact(doc_name)
    if not doc:
        return []
    exact = [v for v in vendors if _compact(v) == doc]
    if exact:
        return exact
    prefix = [v for v in vendors if _compact(v).startswith(doc) and len(doc) >= 3]
    if prefix:
        return prefix
    initials = [v for v in vendors if len(_words(v)) >= 2 and "".join(w[0] for w in _words(v)) == doc]
    if initials:
        return initials
    doc_core = _core(doc_name)
    fuzzy = [v for v in vendors if SequenceMatcher(None, doc_core, _core(v)).ratio() >= 0.85]
    return fuzzy


def match_user(users: List[User], doc_name: str) -> Optional[User]:
    """A user for a lead named in the document: the same username, else the
    one user whose first name it is ("Sam" -> "Sam Doe"). Ambiguous = None."""
    key = doc_name.strip().lower()
    same = [u for u in users if (u.username or "").strip().lower() == key]
    if len(same) == 1:
        return same[0]
    first = [u for u in users if (_words(u.username) or [""])[0] == key]
    return first[0] if len(first) == 1 else None


def _key(name: Optional[str]) -> str:
    return (name or "").strip().lower()


# ---------------------------------------------------------------------------
# Saved leads
# ---------------------------------------------------------------------------


class Leads:
    """Saved lead assignments, indexed for per-item lookups."""

    def __init__(self, rows: List[IssueLead], names: Dict[int, str]):
        self.names = names  # user id -> username (existing users only)
        self.sections: Dict[str, Optional[int]] = {}
        self.vendors: Dict[str, Optional[int]] = {}
        self.fallback: Optional[int] = None
        self.saved = bool(rows)
        self.rows = rows
        for r in rows:
            if r.kind == "section":
                self.sections[r.match_key] = r.user_id
            elif r.kind == "vendor":
                self.vendors[r.match_key] = r.user_id
            elif r.kind == "fallback":
                self.fallback = r.user_id

    def _person(self, uid: Optional[int]) -> Optional[str]:
        return self.names.get(uid) if uid is not None else None

    def lead_for(self, section: Optional[str], suppliers: Iterable[str]) -> Optional[dict]:
        uid = self.sections.get(_key(section))
        if self._person(uid):
            return {"user_id": uid, "name": self.names[uid], "via": "section", "via_name": section}
        for vendor in suppliers:
            uid = self.vendors.get(_key(vendor))
            if self._person(uid):
                return {"user_id": uid, "name": self.names[uid], "via": "vendor", "via_name": vendor.strip()}
        if self._person(self.fallback):
            return {"user_id": self.fallback, "name": self.names[self.fallback], "via": "fallback", "via_name": None}
        return None


def _eligible_users(db: Session) -> List[User]:
    demo = demo_user_ids(db)
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    return [u for u in users if u.id not in demo]


def load_leads(db: Session) -> Leads:
    try:
        rows = db.execute(select(IssueLead)).scalars().all()
    except Exception:  # noqa: BLE001 - code deployed before alembic upgrade
        logger.warning("issue_leads table missing - run alembic upgrade; no Fix Issues leads")
        db.rollback()
        rows = []
    names = {u.id: u.username for u in _eligible_users(db)} if rows else {}
    return Leads(list(rows), names)


def lead_people(db: Session) -> List[dict]:
    """Every person who leads something (saved), by name."""
    leads = load_leads(db)
    ids = {r.user_id for r in leads.rows if r.user_id is not None}
    return sorted(
        ({"user_id": uid, "name": leads.names[uid]} for uid in ids if uid in leads.names),
        key=lambda p: p["name"].lower(),
    )


def _split(raw: Optional[str]) -> List[str]:
    return vendors_of(raw)  # services/item_vendors.py (M1 MT5)


# ---------------------------------------------------------------------------
# Fix Issues rows: section, lead, task (additive fields)
# ---------------------------------------------------------------------------


class RowState:
    """Everything the row annotation needs, loaded once per request."""

    def __init__(self, db: Session, latest: dict):
        from app.services.po_section_rules import load_section_rules

        self.db = db
        self.latest = latest
        self.rules = load_section_rules(db)
        self.leads = load_leads(db)
        self._runs = None

    @property
    def runs(self) -> Dict[int, dict]:
        """user id -> the lead's latest daily task: items + task state."""
        if self._runs is None:
            self._runs = latest_runs(self.db) if self.leads.saved else {}
        return self._runs

    def section_of(self, item_id: Optional[str]) -> Optional[str]:
        snap = self.latest.get(item_id) if item_id else None
        if snap is None:
            return None
        return self.rules.section_for_snap(item_id, snap)


def latest_runs(db: Session) -> Dict[int, dict]:
    from app.team.tasks_models import Task

    rows = db.execute(select(IssueLeadTaskRun).order_by(IssueLeadTaskRun.day, IssueLeadTaskRun.id)).scalars().all()
    by_user: Dict[int, IssueLeadTaskRun] = {}
    for r in rows:
        by_user[r.user_id] = r  # newest day wins
    out = {}
    for uid, r in by_user.items():
        task = db.get(Task, r.task_id) if r.task_id else None
        try:
            items = set(json.loads(r.items_json or "[]"))
        except ValueError:
            items = set()
        out[uid] = {
            "items": items,
            "task": {
                "task_id": r.task_id,
                "day": r.day.isoformat(),
                "status": task.status if task else None,
                "due_at": task.due_at.isoformat() if task and task.due_at else None,
            },
        }
    return out


def _row_suppliers(row: dict) -> List[str]:
    product = row.get("product") or {}
    out = _split(product.get("supplier") or row.get("supplier"))
    for v in row.get("variants") or []:
        for s in _split((v.get("product") or {}).get("supplier") or v.get("supplier")):
            if s not in out:
                out.append(s)
    return out


def annotate_rows(state: RowState, issue_type: str, rows: List[dict]) -> None:
    """Adds `po_section`, `lead` and `lead_task` to each row (in place)."""
    for row in rows:
        section = state.section_of(row.get("item_id"))
        row["po_section"] = section
        lead = state.leads.lead_for(section, _row_suppliers(row)) if state.leads.saved else None
        row["lead"] = lead
        task = None
        if lead is not None and not row.get("closed_at"):
            run = state.runs.get(lead["user_id"])
            if run is not None and f"{issue_type}:{row.get('issue_key')}" in run["items"]:
                task = run["task"]
        row["lead_task"] = task


# ---------------------------------------------------------------------------
# Settings (admin-set, setting_changes rows)
# ---------------------------------------------------------------------------


def _newest_setting(db: Session) -> Optional[SettingChange]:
    return (
        db.execute(
            select(SettingChange).where(SettingChange.key == SETTINGS_KEY).order_by(SettingChange.id.desc()).limit(1)
        )
        .scalars()
        .first()
    )


def get_task_settings(db: Session) -> dict:
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        row = _newest_setting(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        row = None
    if row is not None:
        try:
            saved = json.loads(row.value)
        except ValueError:
            saved = {}
        if isinstance(saved, dict):
            for k in out:
                if k in saved:
                    out[k] = saved[k]
    return out


def settings_meta(db: Session) -> Optional[dict]:
    row = _newest_setting(db)
    if row is None:
        return None
    return {"changed_by": row.changed_by_name, "changed_at": row.changed_at.isoformat() if row.changed_at else None}


class SettingsError(ValueError):
    pass


def _parse_time(value) -> str:
    try:
        t = dt.datetime.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        raise SettingsError("Time must look like 10:00.")
    if t < EARLIEST_TIME or t > LATEST_TIME:
        raise SettingsError("Pick a time between 6:00 AM and 8:00 PM.")
    return t.strftime("%H:%M")


def _hours(value, label: str) -> int:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{label}: hours must be a number.")
    if n != n or not float(n).is_integer() or n < MIN_HOURS or n > MAX_HOURS:
        raise SettingsError(f"{label}: hours must be a whole number from {MIN_HOURS} to {MAX_HOURS}.")
    return int(n)


def save_task_settings(db: Session, body: dict, user: User) -> dict:
    from app.services.fix_issues import ISSUE_TYPES

    with _settings_lock:
        current = get_task_settings(db)
        new = dict(current)
        if "enabled" in body:
            new["enabled"] = bool(body["enabled"])
        if "time" in body:
            new["time"] = _parse_time(body["time"])
        if "default_hours" in body:
            new["default_hours"] = _hours(body["default_hours"], "Default")
        if "hours_by_user" in body:
            raw = body["hours_by_user"] or {}
            if not isinstance(raw, dict):
                raise SettingsError("hours_by_user must be an object.")
            known = {u.id: u.username for u in _eligible_users(db)}
            clean = {}
            for k, v in raw.items():
                try:
                    uid = int(k)
                except (TypeError, ValueError):
                    raise SettingsError("Unknown person.")
                if uid not in known:
                    raise SettingsError("Unknown person.")
                if v is None or v == "":
                    continue
                clean[str(uid)] = _hours(v, known[uid])
            new["hours_by_user"] = clean
        if "hours_by_type" in body:
            raw = body["hours_by_type"] or {}
            if not isinstance(raw, dict):
                raise SettingsError("hours_by_type must be an object.")
            clean = {}
            for k, v in raw.items():
                if k not in ISSUE_TYPES or k in EXCLUDED_TASK_TYPES:
                    raise SettingsError("Unknown issue type.")
                if v is None or v == "":
                    continue
                clean[k] = _hours(v, ISSUE_TYPES[k]["label"])
            new["hours_by_type"] = clean
        if "types_off" in body:
            raw = body["types_off"] or []
            if not isinstance(raw, list) or any(t not in ISSUE_TYPES or t in EXCLUDED_TASK_TYPES for t in raw):
                raise SettingsError("Unknown issue type.")
            new["types_off"] = sorted(set(raw))
        if new == current:
            return current
        prev = _newest_setting(db)
        db.add(
            SettingChange(
                key=SETTINGS_KEY,
                value=json.dumps(new, sort_keys=True),
                previous_value=json.dumps(current, sort_keys=True),
                previous_source="db" if prev is not None else "default",
                changed_by_user_id=user.id,
                changed_by_name=user.username,
                changed_at=utcnow(),
            )
        )
        db.commit()
        return new


def hours_for(settings: dict, user_id: int, counts: Dict[str, int]) -> int:
    """Lead override, else the shortest per-type hours among the types in
    the task, else the default."""
    by_user = settings.get("hours_by_user") or {}
    if str(user_id) in by_user:
        return int(by_user[str(user_id)])
    by_type = settings.get("hours_by_type") or {}
    typed = [int(by_type[t]) for t, n in counts.items() if n and t in by_type]
    if typed:
        return min(typed)
    return int(settings.get("default_hours") or DEFAULT_SETTINGS["default_hours"])


# ---------------------------------------------------------------------------
# Proposal from the document + the admin page
# ---------------------------------------------------------------------------


def _inventory_names(db: Session) -> Tuple[Dict[str, int], Dict[str, int], dict]:
    """(section -> item count, vendor -> item count, latest)."""
    from app.services.po_section_rules import _latest, load_section_rules

    latest = _latest(db)
    rules = load_section_rules(db)
    sections: Counter = Counter()
    vendors: Counter = Counter()
    for item_id, snap in latest.items():
        sections[rules.section_for_snap(item_id, snap)] += 1
        for v in _split(snap.supplier):
            vendors[v] += 1
    return dict(sections), dict(vendors), latest


def document_proposal(db: Session, vendor_names: Iterable[str]) -> dict:
    users = _eligible_users(db)
    leads = []
    by_key: Dict[str, dict] = {}
    vendor_map: Dict[str, dict] = {}
    unmatched_vendors = []
    fallback_doc = _doc_fallback()
    for doc in _doc_leads() + [fallback_doc]:
        user = match_user(users, doc["name"])
        entry = {
            "key": doc["key"],
            "name": doc["name"],
            "title": doc["title"],
            "departments": doc.get("departments"),
            "user": {"user_id": user.id, "name": user.username} if user else None,
            "vendors": [],
        }
        for vname in doc.get("vendors", []):
            matched = vendor_candidates(vname, vendor_names)
            entry["vendors"].append({"name": vname, "matched": matched})
            if not matched:
                unmatched_vendors.append({"name": vname, "lead": doc["name"]})
            for m in matched:
                vendor_map.setdefault(_key(m), {"lead_key": doc["key"], "doc_name": vname, "vendor": m})
        by_key[doc["key"]] = entry
        if doc is not fallback_doc:
            leads.append(entry)
    return {
        "leads": leads,
        "fallback": by_key[fallback_doc["key"]],
        "by_key": by_key,
        "vendor_map": vendor_map,
        "unmatched_vendors": unmatched_vendors,
    }


def _proposal_out(lead_key: Optional[str], doc: dict, basis: Optional[str], why: Optional[str]) -> Optional[dict]:
    if lead_key is None:
        return None
    entry = doc["by_key"][lead_key]
    user = entry["user"]
    return {
        "doc_lead": entry["name"],
        "user_id": user["user_id"] if user else None,
        "name": user["name"] if user else None,
        "basis": basis,
        "why": why,
    }


def _lead_out(leads: Leads, kind: str, key: str) -> Tuple[Optional[dict], bool]:
    """(lead, saved) for one key: saved = a row exists (even with no lead)."""
    table = {"section": leads.sections, "vendor": leads.vendors}.get(kind)
    if kind == "fallback":
        saved = any(r.kind == "fallback" for r in leads.rows)
        uid = leads.fallback
    else:
        saved = key in table
        uid = table.get(key)
    if uid is not None and uid in leads.names:
        return {"user_id": uid, "name": leads.names[uid]}, saved
    return ({"user_id": uid, "name": None, "missing": True} if uid is not None else None), saved


def leads_page(db: Session) -> dict:
    sections, vendors, _ = _inventory_names(db)
    leads = load_leads(db)
    if leads.saved and not leads.names:
        leads.names = {u.id: u.username for u in _eligible_users(db)}
    doc = document_proposal(db, vendors.keys())

    from app.services.po_sections import section_sort_key

    proposals = _section_proposals()
    notes = _section_notes()
    section_rows = []
    for name in sorted(sections, key=section_sort_key):
        key = _key(name)
        prop = proposals.get(key)
        lead, saved = _lead_out(leads, "section", key)
        section_rows.append(
            {
                "section": name,
                "items": sections[name],
                "lead": lead,
                "saved": saved,
                "proposal": _proposal_out(prop[0], doc, prop[1], prop[2]) if prop else None,
                "note": notes.get(key),
            }
        )
    vendor_rows = []
    for name in sorted(vendors, key=str.lower):
        key = _key(name)
        prop = doc["vendor_map"].get(key)
        lead, saved = _lead_out(leads, "vendor", key)
        vendor_rows.append(
            {
                "vendor": name,
                "items": vendors[name],
                "lead": lead,
                "saved": saved,
                "proposal": _proposal_out(prop["lead_key"], doc, DOCUMENT, f'"{prop["doc_name"]}" in the document')
                if prop
                else None,
            }
        )
    # Saved rows for sections / vendors no longer in the inventory stay visible.
    present_sections = {_key(s) for s in sections}
    present_vendors = {_key(v) for v in vendors}
    stale = [
        {"kind": r.kind, "name": r.display_name, "lead": _lead_out(leads, r.kind, r.match_key)[0]}
        for r in leads.rows
        if (r.kind == "section" and r.match_key not in present_sections)
        or (r.kind == "vendor" and r.match_key not in present_vendors)
    ]
    fallback_lead, fallback_saved = _lead_out(leads, "fallback", "")
    people = [
        {
            "user_id": u.id,
            "name": u.username,
            "is_admin": bool(u.is_admin),
            "has_tasks": bool(user_has_feature(u, "team_tasks")),
        }
        for u in _eligible_users(db)
    ]
    settings = get_task_settings(db)
    return {
        "saved": leads.saved,
        "sections": section_rows,
        "vendors": vendor_rows,
        "fallback": {
            "lead": fallback_lead,
            "saved": fallback_saved,
            "proposal": _proposal_out(
                doc["fallback"]["key"], doc, DOCUMENT, f"{doc['fallback']['title']} in the document"
            ),
        },
        "stale": stale,
        "people": people,
        "document": {
            "title": _document_title(),
            "leads": doc["leads"],
            "fallback": doc["fallback"],
            "unmatched_vendors": doc["unmatched_vendors"],
        },
        "settings": settings,
        "settings_changed": settings_meta(db),
        "last_run": last_run(db),
        "issue_types": _task_issue_types(),
    }


def _task_issue_types() -> List[dict]:
    from app.services.fix_issues import ISSUE_TYPES

    return [{"key": k, "label": v["label"]} for k, v in ISSUE_TYPES.items() if k not in EXCLUDED_TASK_TYPES]


class LeadsError(ValueError):
    pass


def _upsert(db: Session, kind: str, name: str, user_id: Optional[int], source: str, actor: User, now) -> None:
    key = "" if kind == "fallback" else _key(name)
    row = db.execute(select(IssueLead).where(IssueLead.kind == kind, IssueLead.match_key == key)).scalars().first()
    if row is None:
        row = IssueLead(kind=kind, match_key=key, created_at=now)
        db.add(row)
    elif row.user_id == user_id and row.display_name == (name or "Everything else"):
        return
    row.display_name = (name or "").strip() or "Everything else"
    row.user_id = user_id
    row.source = source
    row.updated_by_user_id = actor.id
    row.updated_by_name = actor.username
    row.updated_at = now


def save_leads(db: Session, body: dict, actor: User, source: str = "admin") -> int:
    """body: {sections: [{name, user_id}], vendors: [{name, user_id}],
    fallback: {user_id} | absent}. user_id None = no lead (saved as such)."""
    eligible = {u.id for u in _eligible_users(db)}
    now = utcnow()
    changed = 0

    def _uid(value) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            uid = int(value)
        except (TypeError, ValueError):
            raise LeadsError("Unknown person.")
        if uid not in eligible:
            raise LeadsError("That person can't be a lead.")
        return uid

    entries = []
    for kind, field in (("section", "sections"), ("vendor", "vendors")):
        for e in body.get(field) or []:
            name = (e.get("name") or "").strip()
            if not name or len(name) > 200:
                raise LeadsError(f"Each {kind} needs a name.")
            entries.append((kind, name, _uid(e.get("user_id"))))
    if body.get("fallback") is not None:
        entries.append(("fallback", "Everything else", _uid((body.get("fallback") or {}).get("user_id"))))
    if len(entries) > 2000:
        raise LeadsError("Too many rows at once.")
    for kind, name, uid in entries:
        _upsert(db, kind, name, uid, source, actor, now)
        changed += 1
    db.commit()
    return changed


def accept_proposal(db: Session, actor: User) -> int:
    """Saves the document's proposal for every section / vendor (and the
    everything-else lead) that has no saved row yet. Saved rows are kept."""
    page = leads_page(db)
    body = {"sections": [], "vendors": []}
    for row in page["sections"]:
        if not row["saved"] and row["proposal"] and row["proposal"]["user_id"]:
            body["sections"].append({"name": row["section"], "user_id": row["proposal"]["user_id"]})
    for row in page["vendors"]:
        if not row["saved"] and row["proposal"] and row["proposal"]["user_id"]:
            body["vendors"].append({"name": row["vendor"], "user_id": row["proposal"]["user_id"]})
    fb = page["fallback"]
    if not fb["saved"] and fb["proposal"] and fb["proposal"]["user_id"]:
        body["fallback"] = {"user_id": fb["proposal"]["user_id"]}
    return save_leads(db, body, actor, source="proposal")


# ---------------------------------------------------------------------------
# What each lead has open (preview + the daily task)
# ---------------------------------------------------------------------------


def open_by_lead(db: Session, types_off: Iterable[str] = ()) -> dict:
    """{"leads": {user_id: {...}}, "unassigned": n, "total": n} over every
    open item of the task's issue types (minus `types_off`)."""
    from app.services.fix_issues import ISSUE_TYPES, _Context, get_issue_list

    ctx = _Context(db)
    out: Dict[int, dict] = {}
    unassigned = 0
    unassigned_sections: Counter = Counter()
    total = 0
    for issue_type, cfg in ISSUE_TYPES.items():
        if issue_type in EXCLUDED_TASK_TYPES or issue_type in set(types_off):
            continue
        data = get_issue_list(db, issue_type, ctx)
        for row in data["open"]:
            total += 1
            lead = row.get("lead")
            if lead is None:
                unassigned += 1
                unassigned_sections[row.get("po_section") or "No section"] += 1
                continue
            entry = out.setdefault(
                lead["user_id"],
                {"user_id": lead["user_id"], "name": lead["name"], "items": [], "counts": Counter(),
                 "sections": Counter(), "via": Counter()},
            )
            entry["items"].append(f"{issue_type}:{row['issue_key']}")
            entry["counts"][issue_type] += 1
            entry["sections"][row.get("po_section") or "No section"] += 1
            entry["via"][lead["via"]] += 1
    return {
        "leads": out,
        "unassigned": unassigned,
        "unassigned_sections": dict(unassigned_sections.most_common()),
        "total": total,
    }


def fix_issues_link(user_id: int) -> str:
    base = effective_public_base_url()
    return f"{base}/#/fix-issues?lead={user_id}"


def _labels() -> Dict[str, str]:
    from app.services.fix_issues import ISSUE_TYPES

    return {k: v["label"] for k, v in ISSUE_TYPES.items()}


def progress_title(left: int, total: int) -> str:
    return f"Fix Issues: {left} left of {total}"


def task_text(entry: dict) -> Tuple[str, str]:
    n = len(entry["items"])
    title = progress_title(n, n)
    labels = _labels()
    lines = [f"Open Fix Issues items for {entry['name']} today: {n}."]
    for t, c in sorted(entry["counts"].items(), key=lambda kv: (-kv[1], labels.get(kv[0], kv[0]))):
        lines.append(f"- {labels.get(t, t)}: {c}")
    top = entry["sections"].most_common(MAX_SECTIONS_IN_TASK)
    if top:
        lines.append("Biggest sections: " + ", ".join(f"{s} ({c})" for s, c in top) + ".")
    lines.append("Each item you fix or close on Fix Issues counts right away; the task closes itself when none are left.")
    lines.append(f"Open the list: {fix_issues_link(entry['user_id'])}")
    return title, "\n".join(lines)


def preview(db: Session, now: Optional[dt.datetime] = None) -> dict:
    """What the next daily run would assign, per lead (counts only)."""
    settings = get_task_settings(db)
    data = open_by_lead(db, settings.get("types_off") or [])
    leads = []
    for uid, e in sorted(data["leads"].items(), key=lambda kv: kv[1]["name"].lower()):
        leads.append(
            {
                "user_id": uid,
                "name": e["name"],
                "count": len(e["items"]),
                "counts": dict(e["counts"]),
                "sections": dict(e["sections"].most_common()),
                "via": dict(e["via"]),
                "hours": hours_for(settings, uid, e["counts"]),
            }
        )
    return {
        "time": settings["time"],
        "enabled": settings["enabled"],
        "leads": leads,
        "unassigned": data["unassigned"],
        "unassigned_sections": data["unassigned_sections"],
        "total": data["total"],
    }


# ---------------------------------------------------------------------------
# The daily run
# ---------------------------------------------------------------------------


def last_run(db: Session) -> Optional[dict]:
    row = (
        db.execute(select(JobRun).where(JobRun.job_name == JOB_NAME).order_by(JobRun.id.desc()).limit(1))
        .scalars()
        .first()
    )
    if row is None:
        return None
    return {
        "status": row.status,
        "day": row.business_date.isoformat() if row.business_date else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "detail": row.detail,
    }


def _ran_today(db: Session, today: dt.date) -> bool:
    return (
        db.execute(
            select(JobRun.id).where(
                JobRun.job_name == JOB_NAME, JobRun.business_date == today, JobRun.status == "success"
            ).limit(1)
        ).first()
        is not None
    )


def _open_lead_tasks(db: Session, user_id: Optional[int] = None):
    from app.team.tasks_models import Task
    from app.team.tasks_service import SYSTEM_USER_ID

    q = select(Task).where(
        Task.created_by_user_id == SYSTEM_USER_ID, Task.source_type == SOURCE_TYPE, Task.status == "open"
    )
    if user_id is not None:
        q = q.where(Task.assignee_user_id == user_id)
    return list(db.execute(q).scalars())


def run_daily_tasks(db: Session, now: Optional[dt.datetime] = None, force: bool = False) -> Optional[dict]:
    """Creates / updates today's task per lead. Without `force` it runs once
    a day, from the set time on, while switched on and once leads are saved.
    Returns a summary, or None when it didn't run."""
    from app.team.auto_tasks import _close, _existing
    from app.team.tasks_models import Task
    from app.team.tasks_service import SYSTEM_USER_ID, add_event, notify_assigned, record_task_change, system_name

    now = now or dt.datetime.now(restaurant_timezone())
    local = now.astimezone(restaurant_timezone())
    today = local.date()
    settings = get_task_settings(db)
    if not force:
        if not settings["enabled"]:
            return None
        if local.time() < dt.datetime.strptime(settings["time"], "%H:%M").time():
            return None
        if not load_leads(db).saved or _ran_today(db, today):
            return None
    if not _run_lock.acquire(timeout=1):
        return None
    job = JobRun(job_name=JOB_NAME, started_at=utcnow(), status="running", business_date=today)
    try:
        db.add(job)
        db.commit()
        demo = demo_user_ids(db)
        data = open_by_lead(db, settings.get("types_off") or [])
        stamp = utcnow()
        created = updated = closed = 0
        leads_out = []
        no_access = []
        for uid, entry in data["leads"].items():
            if uid in demo:
                continue
            person = db.get(User, uid)
            if person is None or not user_has_feature(person, "team_tasks"):
                # Like every other way of giving someone a task
                # (tasks_service._assignee_user, recurrence.problem_with): a
                # task they can't see helps nobody - the run says so instead.
                no_access.append({"user_id": uid, "name": entry["name"], "count": len(entry["items"])})
                continue
            title, description = task_text(entry)
            hours = hours_for(settings, uid, entry["counts"])
            source_id = f"{uid}:{today.isoformat()}"
            task = _existing(db, SOURCE_TYPE, source_id)
            if task is None:
                task = Task(
                    title=title,
                    description=description,
                    requires="none",
                    priority="normal",
                    assignee_user_id=uid,
                    due_at=(stamp + dt.timedelta(hours=hours)),
                    status="open",
                    source_type=SOURCE_TYPE,
                    source_id=source_id,
                    created_by_user_id=SYSTEM_USER_ID,
                    created_by_name=system_name(),
                    created_at=stamp,
                    updated_at=stamp,
                    progress_total=len(entry["items"]),
                    progress_done=0,
                )
                db.add(task)
                db.flush()
                add_event(db, task, None, "created", {"auto": SOURCE_TYPE}, system_name=system_name())
                record_task_change(db, task, "created")
                created += 1
                is_new = True
            else:
                is_new = False
                if task.status == "open":
                    task.title = title
                    task.description = description
                    task.due_at = task.created_at + dt.timedelta(hours=hours)
                    task.updated_at = stamp
                    task.progress_total = len(entry["items"])
                    task.progress_done = 0
                    add_event(db, task, None, "edited", {"auto": SOURCE_TYPE}, system_name=system_name())
                    record_task_change(db, task, "updated")
                    updated += 1
            run = db.execute(
                select(IssueLeadTaskRun).where(IssueLeadTaskRun.day == today, IssueLeadTaskRun.user_id == uid)
            ).scalars().first()
            if run is None:
                run = IssueLeadTaskRun(day=today, user_id=uid, created_at=stamp)
                db.add(run)
            if is_new or task.status == "open":
                run.task_id = task.id
                run.item_count = len(entry["items"])
                run.items_json = json.dumps(sorted(entry["items"]))
                run.counts_json = json.dumps(dict(entry["counts"]), sort_keys=True)
                run.updated_at = stamp
            # An earlier day's list still open is replaced by today's.
            for old in _open_lead_tasks(db, uid):
                if old.id != task.id and (old.source_id or "") != source_id:
                    closed += _close(db, old, "cancelled", f"Replaced by the {today.strftime('%b %-d')} list")
            db.commit()
            if is_new:
                notify_assigned(db, task, actor_id=None, now=stamp)
            leads_out.append({"user_id": uid, "name": entry["name"], "count": len(entry["items"]), "hours": hours})
        # Leads with nothing open any more: their old list is done.
        for old in _open_lead_tasks(db):
            if old.assignee_user_id not in data["leads"] or old.assignee_user_id in demo:
                closed += _close(db, old, "done", "Nothing left on the Fix Issues list")
        summary = {
            "day": today.isoformat(),
            "created": created,
            "updated": updated,
            "closed": closed,
            "leads": sorted(leads_out, key=lambda e: e["name"].lower()),
            "unassigned": data["unassigned"],
            # Additive (Verify 1): leads left without a task - no Team Tasks.
            "no_tasks_access": sorted(no_access, key=lambda e: e["name"].lower()),
        }
        # Items already closed since the morning list was taken count at once.
        refresh_lead_progress(db)
        job.status = "success"
        job.finished_at = utcnow()
        job.detail = (
            f"{created} created, {updated} updated, {closed} closed; "
            + ", ".join(f"{e['name']} {e['count']}" for e in summary["leads"])
            + (f"; {data['unassigned']} unassigned" if data["unassigned"] else "")
            + "".join(
                f"; {e['name']} can't see Team Tasks - no task ({e['count']} items)"
                for e in summary["no_tasks_access"]
            )
        )
        db.commit()
        return summary
    except Exception as exc:
        db.rollback()
        logger.exception("Fix Issues lead tasks failed")
        try:
            job = db.merge(job)
            job.status = "failed"
            job.finished_at = utcnow()
            job.detail = str(exc)[:500]
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        raise
    finally:
        _run_lock.release()


def run_issue_lead_rule(db: Session, now: dt.datetime) -> int:
    """auto_tasks rule hook (every 5 minutes inside tasks_tick)."""
    result = run_daily_tasks(db, now)
    refresh_lead_progress(db)
    return 0 if result is None else result["created"]


def refresh_lead_progress(db: Session) -> int:
    """Brings every open lead task's "N left of M" up to date: M = its
    morning list, N = those items with no Fix Issues close on record now
    (a reopen counts the item again). Closes a task with nothing left.
    Cheap: the open lead tasks (one per lead), their runs, and the
    issue_resolutions keys. Commits. Returns how many tasks changed."""
    from sqlalchemy import update

    from app.models import IssueResolution
    from app.team.auto_tasks import _close
    from app.team.tasks_models import Task
    from app.team.tasks_service import record_task_change

    tasks = _open_lead_tasks(db)
    if not tasks:
        return 0
    runs = {
        r.task_id: r
        for r in db.execute(
            select(IssueLeadTaskRun)
            .where(IssueLeadTaskRun.task_id.in_([t.id for t in tasks]))
            .order_by(IssueLeadTaskRun.id)
        ).scalars()
    }
    # key -> when it was closed. A close older than the morning list doesn't
    # count: that item was on the list because it came back / recurred
    # (Fix Issues keeps those resolution rows while showing the item open).
    closed_at = {
        f"{t}:{k}": at
        for t, k, at in db.execute(
            select(IssueResolution.issue_type, IssueResolution.issue_key, IssueResolution.closed_at)
        )
    }
    changed = 0
    for task in tasks:
        run = runs.get(task.id)
        if run is None:
            continue
        try:
            items = set(json.loads(run.items_json or "[]"))
        except ValueError:
            continue
        total = len(items)
        since = run.updated_at
        done = sum(
            1 for i in items
            if i in closed_at and (since is None or closed_at[i] is None or closed_at[i] >= since)
        )
        if total and done >= total and _close(db, task, "done", f"All {total} items fixed or closed"):
            changed += 1
            continue
        # (Not closed = someone reopened it: it stays open, its count still moves.)
        if (task.progress_total, task.progress_done) == (total, done):
            continue
        values = {
            "progress_total": total,
            "progress_done": done,
            "title": progress_title(total - done, total),
            "updated_at": utcnow(),
        }
        result = db.execute(
            update(Task)
            .where(Task.id == task.id, Task.status == "open")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            for key, value in values.items():
                setattr(task, key, value)
            record_task_change(db, task, "updated")
            changed += 1
    db.commit()
    return changed


def summary_by_lead(rows: List[dict]) -> Tuple[Dict[str, int], int]:
    counts: Dict[str, int] = defaultdict(int)
    none = 0
    for r in rows:
        lead = r.get("lead")
        if lead is None:
            none += 1
        else:
            counts[str(lead["user_id"])] += 1
    return dict(counts), none
