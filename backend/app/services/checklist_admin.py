"""Checklist builder - admin side (owner, Sep 30: "give access to admins to
create / modify existing checklists questionnaires (give all the options that
google form gives)"). Routes: routers/checklist_admin.py
(/api/checklists/admin/..., admins and "Build checklists").

Model: a checklist (ChecklistDefinition) has immutable, numbered versions
(ChecklistDefinitionVersion). The editor saves a draft (PUT), publishing
turns the draft into the next version and makes it current; revert publishes
an old version again as a new one. Title, short name, description, settings,
active and order are not versioned - PATCH applies them at once.

Drafts are saved even with problems (so work in progress is never lost) and
come back with `problems`; publish refuses a schema with problems.

Every write clears the checklist_schemas cache, so the app and the website
see a published change on their next fetch.
"""

import copy
import datetime as dt
import json
import re
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dbcompat import group_order
from app.demo import hide_demo_clause
from app.features import user_has_feature
from app.models import ChecklistDefinition, ChecklistDefinitionVersion, ChecklistSubmission, User
from app.services import checklist_engine as engine
from app.services import checklist_schemas
from app.services import whatsapp_groups
from app.team import media as media_svc
from app.team.models import TeamMedia
from app.timeutil import utcnow

MAX_TITLE = 120
MAX_SHORT_NAME = 40

# Pictures shown by "image" items: uploaded through POST /api/checklists/media
# (or the team upload), then attached to the checklist definition when a draft
# is saved or published - so they aren't pruned as unattached uploads and
# everyone who can fill checklists can load them.
MEDIA_OWNER_TYPE = "checklist_definition"
_MEDIA_URL_RE = re.compile(r"/api/team/media/([A-Za-z0-9_-]+)")


def _can_see_definition_media(db: Session, user: User, definition_id: int) -> bool:
    row = db.get(ChecklistDefinition, definition_id)
    if row is None:
        return False
    # Builders see every checklist's pictures (also turned-off ones) in the editor.
    if user_has_feature(user, "checklist_builder"):
        return True
    return bool(row.active) and user_has_feature(user, "daily_checklists")


media_svc.register_media_access(MEDIA_OWNER_TYPE, _can_see_definition_media)


def _attach_images(db: Session, row: ChecklistDefinition, schema: Optional[dict]) -> None:
    """Attaches every still-unattached upload an image item points at to this
    checklist (caller commits). Already-attached media is left alone (e.g. a
    duplicated checklist reusing the original's picture - still readable
    through the original's definition)."""
    if not schema:
        return
    for _, item in engine.iter_items(schema):
        if item.get("type") != "image" or not isinstance(item.get("image_url"), str):
            continue
        match = _MEDIA_URL_RE.search(item["image_url"])
        if not match:
            continue
        media = db.query(TeamMedia).filter(TeamMedia.token == match.group(1)).first()
        if media is not None and media.owner_type is None:
            media.owner_type = MEDIA_OWNER_TYPE
            media.owner_id = row.id


class AdminError(Exception):
    def __init__(self, status_code: int, detail, problems: Optional[List[str]] = None):
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status_code = status_code
        self.detail = detail
        self.problems = problems or []


# --- Seeding ------------------------------------------------------------------------------

STORE_SEEDED_BY = "store pack"


def seed_rows(now, created_by: str = STORE_SEEDED_BY) -> list:
    """The store's built-in checklists (checklist_schemas.store_seed_definitions)
    as table rows: [{"definition": {...}, "versions": [{...}]}] - the same
    shape as checklist_builtin.seed_rows (migration 0039)."""
    return rows_for_seeds(checklist_schemas.store_seed_definitions(), now, created_by)


def rows_for_seeds(seeds: list, now, created_by: str = STORE_SEEDED_BY) -> list:
    """Seed definitions (store_seed_definitions shape) -> table rows."""
    rows = []
    for seed in seeds:
        latest = seed["versions"][-1]
        by = seed.get("created_by") or created_by
        rows.append(
            {
                "definition": {
                    "checklist_type": seed["checklist_type"],
                    "title": seed.get("title") or latest.get("title") or seed["checklist_type"],
                    "short_name": seed.get("short_name"),
                    "description": seed["description"] if "description" in seed else latest.get("description"),
                    "active": True,
                    "sort_order": seed.get("sort_order") or 0,
                    "settings_json": json.dumps(seed.get("settings") or {}),
                    "current_version": len(seed["versions"]),
                    "draft_json": None,
                    "created_at": now,
                    "created_by": by,
                    "updated_at": now,
                    "updated_by": by,
                },
                "versions": [
                    {
                        "version": index + 1,
                        "schema_json": json.dumps(schema),
                        "note": seed.get("notes", {}).get(index + 1) if isinstance(seed.get("notes"), dict) else None,
                        "created_at": now,
                        "created_by": by,
                    }
                    for index, schema in enumerate(seed["versions"])
                ],
            }
        )
    return rows


def ensure_seeded(db: Session) -> bool:
    """Inserts the store's built-in checklists (the store pack's) when the
    table is empty (a new store's database, tests). Returns True if it did."""
    if db.execute(select(func.count(ChecklistDefinition.id))).scalar_one():
        return False
    for row in seed_rows(utcnow()):
        definition = ChecklistDefinition(**row["definition"])
        db.add(definition)
        db.flush()
        for version in row["versions"]:
            db.add(ChecklistDefinitionVersion(definition_id=definition.id, **version))
    db.commit()
    checklist_schemas.clear_cache()
    return True


# --- Helpers ------------------------------------------------------------------------------


def _get(db: Session, checklist_type: str) -> ChecklistDefinition:
    ensure_seeded(db)
    row = db.execute(
        select(ChecklistDefinition).where(ChecklistDefinition.checklist_type == checklist_type)
    ).scalar_one_or_none()
    if row is None:
        raise AdminError(404, "Checklist not found")
    return row


def _version_row(db: Session, definition: ChecklistDefinition, version: int) -> ChecklistDefinitionVersion:
    row = db.execute(
        select(ChecklistDefinitionVersion).where(
            ChecklistDefinitionVersion.definition_id == definition.id,
            ChecklistDefinitionVersion.version == version,
        )
    ).scalar_one_or_none()
    if row is None:
        raise AdminError(404, f"Version {version} not found")
    return row


def _settings(row: ChecklistDefinition) -> dict:
    try:
        return engine.normalize_settings(json.loads(row.settings_json or "{}"))
    except ValueError:
        return engine.normalize_settings({})


def _meta(row: ChecklistDefinition) -> dict:
    return {
        "checklist_type": row.checklist_type,
        "title": row.title,
        "short_name": row.short_name,
        "description": row.description,
        "settings": _settings(row),
    }


def _stored(version_row: ChecklistDefinitionVersion) -> dict:
    return json.loads(version_row.schema_json)


def _current_items(db: Session, row: ChecklistDefinition) -> Optional[dict]:
    if row.current_version is None:
        return None
    return engine.items_view(_stored(_version_row(db, row, row.current_version)))


def _draft(row: ChecklistDefinition) -> Optional[dict]:
    if not row.draft_json:
        return None
    try:
        return json.loads(row.draft_json)
    except ValueError:
        return None


def _touch(row: ChecklistDefinition, user: User) -> None:
    row.updated_at = utcnow()
    row.updated_by = user.username


def _submission_counts(db: Session) -> Dict[tuple, int]:
    version = func.coalesce(ChecklistSubmission.definition_version, 1)
    rows = db.execute(
        select(ChecklistSubmission.checklist_type, version, func.count(ChecklistSubmission.id))
        .where(hide_demo_clause(db, ChecklistSubmission.submitted_by_user_id, ChecklistSubmission.submitted_by_name))  # demo submissions don't count
        .group_by(ChecklistSubmission.checklist_type, version)
        .order_by(*group_order(ChecklistSubmission.checklist_type, version))
    ).all()
    return {(t, v): n for t, v, n in rows}


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


def _clean_text(value, field: str, max_len: int, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise AdminError(400, f"{field} is required")
        return None
    if not isinstance(value, str):
        raise AdminError(400, f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise AdminError(400, f"{field} is required")
    if len(value) > max_len:
        raise AdminError(400, f"{field} is too long (max {max_len} characters)")
    return value or None


def _unique_slug(db: Session, title: str) -> str:
    base = engine.slugify(title)
    taken = set(db.execute(select(ChecklistDefinition.checklist_type)).scalars())
    slug, n = base, 2
    while slug in taken or slug in ("admin", "schemas", "schema", "overview", "media"):
        slug = f"{base}_{n}"
        n += 1
    return slug


def _carry_legacy_keys(new: dict, old: Optional[dict]) -> dict:
    """Keeps the keys that make an edited original checklist answer-compatible
    with app build 5 (answer_format, legacy_role, box ids, legacy_block,
    legacy_order) even if the editor didn't send them back - dropping them
    would change the answer keys build 5 sends."""
    if not old:
        return new
    if "legacy_order" not in new and old.get("legacy_order"):
        new["legacy_order"] = True
    old_sections = {s.get("id"): s for s in old.get("sections") or []}
    old_items = {}
    for section in old.get("sections") or []:
        for item in section.get("items") or []:
            old_items[item.get("id")] = item
    for section in new.get("sections") or []:
        previous = old_sections.get(section.get("id"))
        if previous and previous.get("legacy_block") and "legacy_block" not in section:
            section["legacy_block"] = previous["legacy_block"]
        for item in section.get("items") or []:
            before = old_items.get(item.get("id"))
            if not before or before.get("type") != item.get("type"):
                continue
            for key in ("answer_format", "legacy_role", "nothing_to_report_ok"):
                if key in before and key not in item:
                    item[key] = before[key]
            if item.get("type") == "checkboxes" and item.get("answer_format") == "legacy":
                ids_by_value = {o.get("value"): o.get("id") for o in before.get("options") or []}
                taken = {o.get("id") for o in item.get("options") or [] if o.get("id")}
                for option in item.get("options") or []:
                    if not option.get("id"):
                        guess = ids_by_value.get(option.get("value"))
                        if guess and guess not in taken:
                            option["id"] = guess
                        else:
                            option["id"] = engine._new_id(f"{item['id']}_", taken)
                        taken.add(option["id"])
    return new


def _prepare(payload_sections, base: Optional[dict], legacy_order=None) -> dict:
    schema = engine.normalize_schema({"sections": payload_sections, "legacy_order": legacy_order})
    return _carry_legacy_keys(schema, base)


def _sections_equal(a: Optional[dict], b: Optional[dict]) -> bool:
    if a is None or b is None:
        return False
    return json.dumps(a.get("sections"), sort_keys=True) == json.dumps(b.get("sections"), sort_keys=True) and bool(
        a.get("legacy_order")
    ) == bool(b.get("legacy_order"))


def _publish_schema(db: Session, row: ChecklistDefinition, stored: dict, user: User, note: Optional[str]) -> int:
    latest = db.execute(
        select(func.max(ChecklistDefinitionVersion.version)).where(ChecklistDefinitionVersion.definition_id == row.id)
    ).scalar_one()
    version = (latest or 0) + 1
    db.add(
        ChecklistDefinitionVersion(
            definition_id=row.id,
            version=version,
            schema_json=json.dumps(stored),
            note=note,
            created_at=utcnow(),
            created_by=user.username,
        )
    )
    row.current_version = version
    return version


# --- Output -------------------------------------------------------------------------------


def _summary(db: Session, row: ChecklistDefinition, counts: Dict[tuple, int], version_count: int, last_at) -> dict:
    min_build = None
    if row.current_version is not None:
        served = engine.build_served(_meta(row), row.current_version, _stored(_version_row(db, row, row.current_version)))
        min_build = served.get("min_app_build")
    return {
        "checklist_type": row.checklist_type,
        "title": row.title,
        "short_name": row.short_name,
        "description": row.description,
        "active": bool(row.active),
        "sort_order": row.sort_order,
        "settings": _settings(row),
        "current_version": row.current_version,
        "published": row.current_version is not None,
        "has_draft": bool(row.draft_json),
        "draft_updated_at": _iso(row.draft_updated_at),
        "draft_updated_by": row.draft_updated_by,
        "version_count": version_count,
        "submission_count": sum(n for (t, _), n in counts.items() if t == row.checklist_type),
        "last_submission_at": _iso(last_at),
        "min_app_build": min_build,
        "created_at": _iso(row.created_at),
        "created_by": row.created_by,
        "updated_at": _iso(row.updated_at),
        "updated_by": row.updated_by,
    }


def list_definitions(db: Session) -> List[dict]:
    ensure_seeded(db)
    rows = db.execute(
        select(ChecklistDefinition).order_by(
            ChecklistDefinition.sort_order, ChecklistDefinition.title, ChecklistDefinition.id
        )
    ).scalars().all()
    counts = _submission_counts(db)
    version_counts = dict(
        db.execute(
            select(ChecklistDefinitionVersion.definition_id, func.count(ChecklistDefinitionVersion.id))
            .group_by(ChecklistDefinitionVersion.definition_id)
            .order_by(*group_order(ChecklistDefinitionVersion.definition_id))
        ).all()
    )
    last = dict(
        db.execute(
            select(ChecklistSubmission.checklist_type, func.max(ChecklistSubmission.created_at))
            .where(hide_demo_clause(db, ChecklistSubmission.submitted_by_user_id, ChecklistSubmission.submitted_by_name))
            .group_by(ChecklistSubmission.checklist_type)
            .order_by(*group_order(ChecklistSubmission.checklist_type))
        ).all()
    )
    return [_summary(db, r, counts, version_counts.get(r.id, 0), last.get(r.checklist_type)) for r in rows]


def _versions(db: Session, row: ChecklistDefinition, counts: Dict[tuple, int]) -> List[dict]:
    rows = db.execute(
        select(ChecklistDefinitionVersion)
        .where(ChecklistDefinitionVersion.definition_id == row.id)
        .order_by(ChecklistDefinitionVersion.version.desc())
    ).scalars().all()
    out = []
    for v in rows:
        stored = _stored(v)
        served = engine.build_served(_meta(row), v.version, stored)
        out.append(
            {
                "version": v.version,
                "current": v.version == row.current_version,
                "format": "items" if engine.is_items_form(stored) else "legacy",
                "min_app_build": served.get("min_app_build"),
                "note": v.note,
                "created_at": _iso(v.created_at),
                "created_by": v.created_by,
                "submission_count": counts.get((row.checklist_type, v.version), 0),
            }
        )
    return out


def definition_detail(db: Session, checklist_type: str) -> dict:
    row = _get(db, checklist_type)
    counts = _submission_counts(db)
    versions = _versions(db, row, counts)
    last = db.execute(
        select(func.max(ChecklistSubmission.created_at)).where(
            ChecklistSubmission.checklist_type == row.checklist_type, hide_demo_clause(db, ChecklistSubmission.submitted_by_user_id, ChecklistSubmission.submitted_by_name)
        )
    ).scalar_one()
    summary = _summary(db, row, counts, len(versions), last)
    schema = None
    current_items = None
    if row.current_version is not None:
        stored = _stored(_version_row(db, row, row.current_version))
        schema = engine.build_served(_meta(row), row.current_version, stored)
        current_items = engine.items_view(stored)
    draft = _draft(row)
    editable = draft if draft is not None else _editable_from(row, current_items)
    summary.update(
        {
            "schema": schema,
            "draft": draft,
            "editable": editable,
            "problems": engine.validate_schema(editable) if editable else [],
            "versions": versions,
        }
    )
    return summary


def _editable_from(row: ChecklistDefinition, items: Optional[dict]) -> dict:
    base = copy.deepcopy(items) if items else {"format": "items", "sections": [_blank_section()]}
    base["title"] = row.title
    base["short_name"] = row.short_name
    base["description"] = row.description
    return base


def _blank_section() -> dict:
    return {"id": "s1", "title": None, "description": None, "items": []}


def version_detail(db: Session, checklist_type: str, version: int) -> dict:
    row = _get(db, checklist_type)
    v = _version_row(db, row, version)
    stored = _stored(v)
    return {
        "checklist_type": row.checklist_type,
        "version": v.version,
        "current": v.version == row.current_version,
        "format": "items" if engine.is_items_form(stored) else "legacy",
        "note": v.note,
        "created_at": _iso(v.created_at),
        "created_by": v.created_by,
        "schema": engine.build_served(_meta(row), v.version, stored),
        "editable": engine.items_view(stored),
    }


# --- Writes -------------------------------------------------------------------------------


def _apply_meta(row: ChecklistDefinition, payload: dict) -> bool:
    changed = False
    if "title" in payload:
        title = _clean_text(payload["title"], "Title", MAX_TITLE, required=True)
        changed |= title != row.title
        row.title = title
    if "short_name" in payload:
        short = _clean_text(payload["short_name"], "Short name", MAX_SHORT_NAME)
        changed |= short != row.short_name
        row.short_name = short
    if "description" in payload:
        description = _clean_text(payload["description"], "Description", 4000)
        changed |= description != row.description
        row.description = description
    return changed


def create_definition(db: Session, user: User, payload: dict) -> dict:
    ensure_seeded(db)
    title = _clean_text(payload.get("title"), "Title", MAX_TITLE, required=True)
    settings = payload.get("settings") or {}
    problems = engine.validate_settings(settings, whatsapp_groups.active_keys(db))
    if problems:
        raise AdminError(400, problems[0], problems)
    sections = payload.get("sections")
    try:
        draft = _prepare(sections if sections is not None else [_blank_section()], None)
    except engine.SchemaError as exc:
        raise AdminError(400, exc.errors[0], exc.errors)
    max_order = db.execute(select(func.max(ChecklistDefinition.sort_order))).scalar_one() or 0
    now = utcnow()
    short = _clean_text(payload.get("short_name"), "Short name", MAX_SHORT_NAME)
    description = _clean_text(payload.get("description"), "Description", 4000)
    # Drafts are self-contained: they carry the title / short name / description too.
    draft.update({"title": title, "short_name": short, "description": description})
    row = ChecklistDefinition(
        checklist_type=_unique_slug(db, title),
        title=title,
        short_name=short,
        description=description,
        active=True,
        sort_order=max_order + 1,
        settings_json=json.dumps(engine.normalize_settings(settings)),
        current_version=None,
        draft_json=json.dumps(draft),
        draft_updated_at=now,
        draft_updated_by=user.username,
        created_at=now,
        created_by=user.username,
        updated_at=now,
        updated_by=user.username,
    )
    db.add(row)
    db.flush()
    _attach_images(db, row, draft)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, row.checklist_type)


STALE_DRAFT_MESSAGE = (
    "Someone else saved this checklist since you opened it - reload to see their changes, then make yours again"
)


def _parse_iso(value: str) -> Optional[dt.datetime]:
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _check_draft_base(row: ChecklistDefinition, payload: dict) -> None:
    """Optimistic check for draft saves (and publish): a client that sends
    `base_draft_updated_at` - the draft_updated_at of the detail it is
    editing, null when it started from no draft - gets a 409 when the draft
    changed since (another admin saved, discarded, published or reverted).
    Without the key the save goes through as before (last write wins), so
    older apps and pages keep working."""
    if "base_draft_updated_at" not in payload:
        return
    base = payload["base_draft_updated_at"]
    if base is not None and not isinstance(base, str):
        raise AdminError(400, "base_draft_updated_at must be a date-time or null")
    current = row.draft_updated_at
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    if base is None or not base.strip():
        stale = current is not None
    else:
        parsed = _parse_iso(base)
        if parsed is None:
            raise AdminError(400, "base_draft_updated_at must be a date-time or null")
        stale = current is None or parsed != current
    if stale:
        raise AdminError(409, STALE_DRAFT_MESSAGE)


def save_draft(db: Session, user: User, checklist_type: str, payload: dict) -> dict:
    row = _get(db, checklist_type)
    _check_draft_base(row, payload)
    if "sections" not in payload:
        raise AdminError(400, "sections is required")
    try:
        draft = _prepare(payload["sections"], _current_items(db, row), payload.get("legacy_order"))
    except engine.SchemaError as exc:
        raise AdminError(400, exc.errors[0], exc.errors)
    limits = {"title": MAX_TITLE, "short_name": MAX_SHORT_NAME, "description": 4000}
    for key, limit in limits.items():
        if key in payload:
            draft[key] = _clean_text(payload[key], key.replace("_", " ").capitalize(), limit, required=(key == "title"))
    for key in ("title", "short_name", "description"):
        draft.setdefault(key, getattr(row, key))
    now = utcnow()
    row.draft_json = json.dumps(draft)
    row.draft_updated_at = now
    row.draft_updated_by = user.username
    _attach_images(db, row, draft)
    _touch(row, user)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, checklist_type)


def discard_draft(db: Session, user: User, checklist_type: str) -> dict:
    row = _get(db, checklist_type)
    row.draft_json = None
    row.draft_updated_at = None
    row.draft_updated_by = None
    _touch(row, user)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, checklist_type)


def publish(db: Session, user: User, checklist_type: str, payload: dict) -> dict:
    row = _get(db, checklist_type)
    base_version = payload.get("base_version")
    if base_version is not None and base_version != row.current_version:
        raise AdminError(
            409, f"Version {row.current_version} was published since you started editing - reload and try again"
        )
    _check_draft_base(row, payload)
    current = _current_items(db, row)
    if payload.get("sections") is not None:
        try:
            schema = _prepare(payload["sections"], current, payload.get("legacy_order"))
        except engine.SchemaError as exc:
            raise AdminError(400, exc.errors[0], exc.errors)
        meta = {k: payload[k] for k in ("title", "short_name", "description") if k in payload}
    else:
        draft = _draft(row)
        if draft is None:
            raise AdminError(400, "Nothing to publish - save a draft first")
        meta = {k: draft[k] for k in ("title", "short_name", "description") if k in draft}
        meta.update({k: payload[k] for k in ("title", "short_name", "description") if k in payload})
        schema = {k: v for k, v in draft.items() if k not in ("title", "short_name", "description")}
        schema["format"] = "items"
    problems = engine.validate_schema(schema)
    if problems:
        raise AdminError(400, problems[0], problems)
    meta_changed = _apply_meta(row, meta)
    if _sections_equal(schema, current):
        if not meta_changed:
            raise AdminError(400, f"Nothing changed since version {row.current_version}")
    else:
        note = _clean_text(payload.get("note"), "Note", 500)
        _publish_schema(db, row, schema, user, note)
        _attach_images(db, row, schema)
    row.draft_json = None
    row.draft_updated_at = None
    row.draft_updated_by = None
    _touch(row, user)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, checklist_type)


def revert(db: Session, user: User, checklist_type: str, version: int, payload: Optional[dict] = None) -> dict:
    row = _get(db, checklist_type)
    old = _version_row(db, row, version)
    if version == row.current_version:
        raise AdminError(400, f"Version {version} is already the current version")
    note = _clean_text((payload or {}).get("note"), "Note", 500) or f"Reverted to version {version}"
    _publish_schema(db, row, _stored(old), user, note)
    row.draft_json = None
    row.draft_updated_at = None
    row.draft_updated_by = None
    _touch(row, user)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, checklist_type)


def duplicate(db: Session, user: User, checklist_type: str, payload: dict) -> dict:
    source = _get(db, checklist_type)
    title = _clean_text(payload.get("title"), "Title", MAX_TITLE) or f"{source.title} (copy)"[:MAX_TITLE]
    items = _current_items(db, source) or _draft(source) or {"format": "items", "sections": [_blank_section()]}
    draft = {k: v for k, v in copy.deepcopy(items).items() if k not in ("title", "short_name", "description")}
    draft["format"] = "items"
    draft.update({"title": title, "short_name": None, "description": source.description})
    max_order = db.execute(select(func.max(ChecklistDefinition.sort_order))).scalar_one() or 0
    now = utcnow()
    row = ChecklistDefinition(
        checklist_type=_unique_slug(db, title),
        title=title,
        short_name=None,
        description=source.description,
        active=True,
        sort_order=max_order + 1,
        settings_json=json.dumps(_settings(source)),
        current_version=None,
        draft_json=json.dumps(draft),
        draft_updated_at=now,
        draft_updated_by=user.username,
        created_at=now,
        created_by=user.username,
        updated_at=now,
        updated_by=user.username,
    )
    db.add(row)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, row.checklist_type)


def patch(db: Session, user: User, checklist_type: str, payload: dict) -> dict:
    row = _get(db, checklist_type)
    _apply_meta(row, payload)
    if "active" in payload:
        if not isinstance(payload["active"], bool):
            raise AdminError(400, "active must be true or false")
        row.active = payload["active"]
    if "sort_order" in payload:
        order = payload["sort_order"]
        if not isinstance(order, int) or isinstance(order, bool):
            raise AdminError(400, "sort_order must be a whole number")
        row.sort_order = order
    if "settings" in payload:
        incoming = payload["settings"]
        # Any active group, or the one this checklist already has (so other
        # settings can still be saved after its group was turned off).
        allowed = whatsapp_groups.active_keys(db)
        current_group = _settings(row).get("whatsapp_group")
        if current_group and current_group not in allowed:
            allowed.append(current_group)
        problems = engine.validate_settings(incoming, allowed)
        if problems:
            raise AdminError(400, problems[0], problems)
        merged = _settings(row)
        merged.update(incoming)
        row.settings_json = json.dumps(engine.normalize_settings(merged))
    _touch(row, user)
    db.commit()
    checklist_schemas.clear_cache()
    return definition_detail(db, checklist_type)


def reorder(db: Session, user: User, order: List[str]) -> List[dict]:
    """Sets sort_order 1..n in the given order (types not listed keep theirs, after)."""
    ensure_seeded(db)
    rows = {r.checklist_type: r for r in db.execute(select(ChecklistDefinition)).scalars()}
    unknown = [t for t in order if t not in rows]
    if unknown:
        raise AdminError(404, f"Checklist not found: {unknown[0]}")
    for index, checklist_type in enumerate(order):
        rows[checklist_type].sort_order = index + 1
        _touch(rows[checklist_type], user)
    rest = [r for t, r in rows.items() if t not in order]
    for offset, r in enumerate(sorted(rest, key=lambda r: r.sort_order)):
        r.sort_order = len(order) + offset + 1
    db.commit()
    checklist_schemas.clear_cache()
    return list_definitions(db)


def editor_meta(db: Session) -> dict:
    """What the editor offers: item types, issue tags, validation rules, groups."""
    return {
        "item_types": engine.ITEM_TYPES,
        "display_only_types": sorted(engine.DISPLAY_ONLY_TYPES),
        "issue_tags": [{"key": k, "label": engine.ISSUE_TAG_LABELS[k]} for k in engine.ISSUE_TAG_ORDER],
        "validation": {
            item_type: {kind: engine.VALIDATION_RULES[kind] for kind in kinds}
            for item_type, kinds in engine.VALIDATION_KINDS_BY_TYPE.items()
        },
        # Active groups from the whatsapp_groups table: [{key, label, builtin}].
        "whatsapp_groups": whatsapp_groups.meta_groups(db),
        "default_settings": dict(engine.DEFAULT_SETTINGS),
        "file_types": engine.FILE_TYPES,
        "max_files": engine.MAX_FILES,
        "scale_max": engine.SCALE_MAX,
        "items_min_app_build": engine.ITEMS_MIN_APP_BUILD,
    }
