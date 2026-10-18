"""Daily Checklists - where a checklist's questions come from.

Since the checklist builder (Sep 30, migration 0039) checklists live in the
database (checklist_definitions + checklist_definition_versions), created and
edited by admins on the website (services/checklist_admin.py). This module is
the thin, cached read layer every consumer goes through: the public schema
endpoints, submission validation / flagging / WhatsApp text, the 9:30 PM
closing alert, the nightly auto-tasks and the Overview.

- The cache is rebuilt after any admin change in this process
  (clear_cache) and, for other processes, when the definitions table's
  change stamp moves (checked at most every STAMP_CHECK_SECONDS).
- If the tables are missing (a database not yet migrated) or empty (a new
  store before `python -m app.store init`, tests), the store pack's
  checklists are served instead (store_seed_definitions) - what store-init
  and checklist_admin.ensure_seeded write. (services/checklist_builtin.py is
  only the frozen data migration 0039 imports.)
- A submission renders with the version it was submitted against
  (checklist_submissions.definition_version; NULL = version 1).

The generic logic (item types, validation, branching, flags, legacy blocks
for app build 5) is services/checklist_engine.py; the JSON contract is
docs/CHECKLIST_BUILDER_CONTRACT.md.
"""

import json
import logging
import threading
import time
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.db import SessionLocal
from app.models import ChecklistDefinition, ChecklistDefinitionVersion
from app.services import checklist_engine as engine
from app.services.checklist_engine import (  # noqa: F401 - re-exported
    CHECKBOX_DONE_VALUE,
    ISSUE_TAG_LABELS,
    ISSUE_TAG_ORDER,
    NOTHING_TO_REPORT,
)
from app.tenancy import tenant_local, tenant_lock

logger = logging.getLogger(__name__)

STAMP_CHECK_SECONDS = 3.0


class Definition:
    """One checklist as the registry knows it (read-only snapshot)."""

    def __init__(
        self,
        checklist_type: str,
        title: str,
        short_name: Optional[str],
        description: Optional[str],
        active: bool,
        sort_order: int,
        settings: dict,
        current_version: Optional[int],
        definition_id: Optional[int] = None,
        versions: Optional[Dict[int, dict]] = None,
    ):
        self.checklist_type = checklist_type
        self.title = title
        self.short_name = short_name
        self.description = description
        self.active = active
        self.sort_order = sort_order
        self.settings = engine.normalize_settings(settings)
        self.current_version = current_version
        self.definition_id = definition_id
        # version -> stored schema; built-ins hold all, DB definitions fill lazily.
        self.versions: Dict[int, dict] = dict(versions or {})

    @property
    def published(self) -> bool:
        return self.current_version is not None

    def meta(self) -> dict:
        return {
            "checklist_type": self.checklist_type,
            "title": self.title,
            "short_name": self.short_name,
            "description": self.description,
            "settings": self.settings,
        }


class Registry:
    def __init__(self, source: str, definitions: List[Definition], stamp=None):
        self.source = source  # "db" | "builtin"
        self.stamp = stamp
        ordered = sorted(definitions, key=lambda d: (d.sort_order, d.title.lower(), d.checklist_type))
        self.definitions: Dict[str, Definition] = {d.checklist_type: d for d in ordered}
        self._served: Dict[tuple, dict] = {}
        self._items: Dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def stored(self, definition: Definition, version: int) -> dict:
        if version not in definition.versions:
            if definition.definition_id is None:
                raise KeyError(version)
            db = SessionLocal()
            try:
                row = db.execute(
                    select(ChecklistDefinitionVersion.schema_json).where(
                        ChecklistDefinitionVersion.definition_id == definition.definition_id,
                        ChecklistDefinitionVersion.version == version,
                    )
                ).scalar_one_or_none()
            finally:
                db.close()
            if row is None:
                raise KeyError(version)
            definition.versions[version] = json.loads(row)
        return definition.versions[version]

    def served(self, definition: Definition, version: int) -> dict:
        key = (definition.checklist_type, version)
        with self._lock:
            cached = self._served.get(key)
        if cached is None:
            cached = engine.build_served(definition.meta(), version, self.stored(definition, version))
            with self._lock:
                self._served[key] = cached
        return cached

    def items(self, definition: Definition, version: int) -> dict:
        key = (definition.checklist_type, version)
        with self._lock:
            cached = self._items.get(key)
        if cached is None:
            cached = engine.items_view(self.stored(definition, version))
            with self._lock:
                self._items[key] = cached
        return cached


def store_seed_definitions() -> List[dict]:
    """The store's built-in checklists (the active store pack's), in seed
    shape: checklist_type, title, short_name, description, sort_order,
    settings, versions (stored schemas, oldest first). One place, so the
    fallback registry, checklist_admin.ensure_seeded and store-init agree;
    tests replace it to serve other fixtures."""
    from app.store import pack as store_pack

    return store_pack.checklist_seed_definitions()


def _builtin_registry() -> Registry:
    definitions = []
    for seed in store_seed_definitions():
        latest = seed["versions"][-1]
        definitions.append(
            Definition(
                checklist_type=seed["checklist_type"],
                title=seed.get("title") or latest.get("title") or seed["checklist_type"],
                short_name=seed.get("short_name"),
                description=seed["description"] if "description" in seed else latest.get("description"),
                active=True,
                sort_order=seed["sort_order"],
                settings=seed["settings"],
                current_version=len(seed["versions"]),
                versions={i + 1: v for i, v in enumerate(seed["versions"])},
            )
        )
    return Registry("builtin", definitions)


def _read_stamp(db) -> Optional[tuple]:
    """Changes whenever any definition or version changes (every admin write
    bumps updated_at). None when the tables don't exist."""
    try:
        defs = db.execute(
            select(func.count(ChecklistDefinition.id), func.max(ChecklistDefinition.updated_at))
        ).one()
        versions = db.execute(select(func.count(ChecklistDefinitionVersion.id))).scalar_one()
    except (OperationalError, ProgrammingError):
        db.rollback()
        return None
    return (defs[0], str(defs[1]), versions)


def _load(db, stamp: Optional[tuple]) -> Registry:
    if stamp is None or stamp[0] == 0:
        registry = _builtin_registry()
        registry.stamp = stamp
        return registry
    rows = db.execute(select(ChecklistDefinition)).scalars().all()
    current = {}
    wanted = [(r.id, r.current_version) for r in rows if r.current_version is not None]
    if wanted:
        version_rows = db.execute(
            select(
                ChecklistDefinitionVersion.definition_id,
                ChecklistDefinitionVersion.version,
                ChecklistDefinitionVersion.schema_json,
            ).where(ChecklistDefinitionVersion.definition_id.in_([w[0] for w in wanted]))
        ).all()
        wanted_set = set(wanted)
        for def_id, version, schema_json in version_rows:
            if (def_id, version) in wanted_set:
                current[def_id] = (version, json.loads(schema_json))
    definitions = []
    for row in rows:
        try:
            settings = json.loads(row.settings_json or "{}")
        except ValueError:
            settings = {}
        versions = {}
        if row.id in current:
            versions[current[row.id][0]] = current[row.id][1]
        definitions.append(
            Definition(
                checklist_type=row.checklist_type,
                title=row.title,
                short_name=row.short_name,
                description=row.description,
                active=bool(row.active),
                sort_order=row.sort_order or 0,
                settings=settings,
                current_version=row.current_version if row.id in current else None,
                definition_id=row.id,
                versions=versions,
            )
        )
    return Registry("db", definitions, stamp)


_state = tenant_local("checklist_schemas._state", lambda: {"registry": None, "checked_at": 0.0, "generation": 0})
_state_lock = tenant_lock("checklist_schemas._state_lock")


def clear_cache() -> None:
    """Called after every admin change (and by the test harness).

    Bumps the generation so a registry() load that was already running on
    another thread when the change committed (and so may have read the old
    rows) is never stored as the fresh cache - see registry()."""
    with _state_lock:
        _state["registry"] = None
        _state["checked_at"] = 0.0
        _state["generation"] += 1


def registry() -> Registry:
    """The cached registry. A load that raced an admin change (clear_cache
    ran while it was reading) is thrown away and redone: storing it would
    serve - and let submissions record - the previous version for up to
    STAMP_CHECK_SECONDS after a publish (the "published v4, staff submission
    stored as v3" bug)."""
    fresh = None
    for _ in range(3):
        now = time.monotonic()
        with _state_lock:
            current = _state["registry"]
            generation = _state["generation"]
            if current is not None and now - _state["checked_at"] < STAMP_CHECK_SECONDS:
                return current
        db = SessionLocal()
        try:
            stamp = _read_stamp(db)
            if current is not None and stamp == current.stamp:
                fresh = current
            else:
                fresh = _load(db, stamp)
        except Exception:  # noqa: BLE001 - never take the checklists down over the registry
            logger.exception("Loading checklist definitions failed - serving the built-in checklists")
            fresh = current or _builtin_registry()
        finally:
            db.close()
        with _state_lock:
            if _state["generation"] == generation:
                _state["registry"] = fresh
                _state["checked_at"] = time.monotonic()
                return fresh
        # A change committed while this load was reading - load again.
    return fresh


# --- Lookups --------------------------------------------------------------------------


def get_definition(checklist_type: str) -> Optional[Definition]:
    return registry().definitions.get(checklist_type)


def _live(checklist_type: str) -> Definition:
    definition = get_definition(checklist_type)
    if definition is None or not definition.active or not definition.published:
        raise ValueError(f"Unknown checklist_type: {checklist_type!r}")
    return definition


def get_schema(checklist_type: str) -> dict:
    """The current published schema of an active checklist (what the app and
    the website render). Raises ValueError for an unknown, inactive or never
    published checklist. The returned dict is shared - don't mutate it."""
    definition = _live(checklist_type)
    return registry().served(definition, definition.current_version)


def get_version_schema(checklist_type: str, version: int) -> dict:
    """A specific version (active or not) - for rendering old submissions."""
    definition = get_definition(checklist_type)
    if definition is None:
        raise ValueError(f"Unknown checklist_type: {checklist_type!r}")
    try:
        return registry().served(definition, version)
    except KeyError:
        raise ValueError(f"Unknown version {version} of {checklist_type!r}")


def current_version(checklist_type: str) -> int:
    return _live(checklist_type).current_version


def live_version(db, checklist_type: str) -> int:
    """The current published version of an active checklist, read from the
    database in the caller's session - what a submission is validated and
    stored against. Unlike current_version() it never trusts the per-process
    cache (another process may have published a moment ago; the cache only
    re-checks every STAMP_CHECK_SECONDS): when the cache disagrees it's
    dropped, so the schema lookups that follow see the new version too.
    Raises ValueError for an unknown, inactive or unpublished checklist."""
    try:
        row = db.execute(
            select(ChecklistDefinition.active, ChecklistDefinition.current_version).where(
                ChecklistDefinition.checklist_type == checklist_type
            )
        ).one_or_none()
    except (OperationalError, ProgrammingError):
        db.rollback()
        return current_version(checklist_type)  # not migrated: the built-ins
    if row is None:
        if registry().source == "builtin":
            return current_version(checklist_type)  # empty tables (tests): the built-ins
        raise ValueError(f"Unknown checklist_type: {checklist_type!r}")
    if not row.active or row.current_version is None:
        raise ValueError(f"Unknown checklist_type: {checklist_type!r}")
    cached = get_definition(checklist_type)
    if cached is None or cached.current_version != row.current_version or not cached.active:
        clear_cache()
    return row.current_version



def items_schema(checklist_type: str, version: Optional[int] = None) -> dict:
    """Items form of a version (default: the current one) for the engine."""
    reg = registry()
    definition = reg.definitions.get(checklist_type)
    if definition is None:
        raise ValueError(f"Unknown checklist_type: {checklist_type!r}")
    if version is None:
        definition = _live(checklist_type)
        version = definition.current_version
    try:
        return reg.items(definition, version)
    except KeyError:
        raise ValueError(f"Unknown version {version} of {checklist_type!r}")


def list_schemas() -> Dict[str, dict]:
    """Every active, published checklist's current schema, in sort order
    (GET /api/checklists/schemas)."""
    reg = registry()
    out = {}
    for t, d in reg.definitions.items():
        if not (d.active and d.published):
            continue
        # One broken entry must not take the whole list down: app build 5
        # parses every entry, so a 500 here would hide all its checklists.
        try:
            out[t] = reg.served(d, d.current_version)
        except Exception:  # noqa: BLE001
            logger.exception("Checklist %s v%s couldn't be served - left out of /schemas", t, d.current_version)
    return out


def checklist_title(checklist_type: str) -> str:
    definition = get_definition(checklist_type)
    return definition.title if definition is not None else checklist_type


def short_name(checklist_type: str) -> str:
    """A checklist's short name ("Main Store", "Kitchen FSA"): its own
    short_name if it has one, else the title up to its first " - " / " – "."""
    definition = get_definition(checklist_type)
    if definition is None:
        return checklist_type
    if definition.short_name:
        return definition.short_name
    title = definition.title
    for sep in (" - ", " – "):
        if sep in title:
            return title.split(sep)[0].strip()
    return title


def closing_checklist_types() -> List[str]:
    """Active, published checklists with settings.closing, in sort order -
    the ones the single 9:30 PM alert and the nightly auto-task cover."""
    return [
        t
        for t, d in registry().definitions.items()
        if d.active and d.published and d.settings.get("closing")
    ]


def whatsapp_group(checklist_type: str) -> Optional[str]:
    """"store" | "kitchen" | None (no WhatsApp message)."""
    definition = get_definition(checklist_type)
    return definition.settings.get("whatsapp_group") if definition is not None else None


def submission_version(entry) -> int:
    """The version a stored submission was made against (NULL = 1)."""
    return entry.definition_version or 1


# --- Engine shortcuts by type ---------------------------------------------------------


def validate_answers(checklist_type: str, answers: dict, version: Optional[int] = None) -> None:
    engine.validate_answers(items_schema(checklist_type, version), answers)


def compute_status(checklist_type: str, answers: dict, version: Optional[int] = None) -> tuple:
    return engine.compute_status(items_schema(checklist_type, version), answers)


def summary_details(checklist_type: str, answers: dict, version: Optional[int] = None) -> list:
    """[{"label": "Chef", "value": "RK"}] for a version (default: current).
    Unknown types / versions -> []."""
    try:
        schema = items_schema(checklist_type, version)
    except ValueError:
        return []
    return engine.summary_details(schema, answers)


def all_row_ids(checklist_type: str, version: Optional[int] = None) -> set:
    """Every answer key a checklist version uses."""
    ids = set()
    for _, item in engine.iter_items(items_schema(checklist_type, version)):
        ids.update(engine.answer_keys(item))
    return ids
