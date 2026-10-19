"""KloudC RetailOps store commands (docs/M2_CONTRACT.md 3.4).

    python -m app.store init --code CODE --pack PATH [--adopt]
    python -m app.store pack validate PATH
    python -m app.store pack load PATH
    python -m app.store pack show
    python -m app.store pack schema
    python -m app.store identity show
    python -m app.store branding [--env FILE]

Every command uses DATABASE_URL / UPLOADS_ROOT from the settings (.env).
Docker: docker compose run --rm api python -m app.store init --code XYZ --pack /packs/xyz.json
"""

import argparse
import datetime as dt
import json
import os
import sys
from typing import Dict, List, Optional


def _print(value) -> None:
    print(json.dumps(value, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------------------
# store-init (contract 3.5)
# ---------------------------------------------------------------------------

LEGACY_CATEGORY_SUPPLIER = "Sample Biscuit Supplier"  # migration 0034's one supplier rule
LEGACY_CATEGORY_CREATED_BY = "category merge plan (migration 0037)"
LEGACY_WHATSAPP_KEYS = ("store", "kitchen")  # migration 0044's built-in roles
LEGACY_WHATSAPP_CREATED_BY = "migration 0044"
DATA_TABLES = ("orders", "invoice_submissions", "checklist_submissions")


class InitError(RuntimeError):
    pass


def _alembic_heads() -> List[str]:
    import pathlib

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = pathlib.Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    return list(ScriptDirectory.from_config(cfg).get_heads())


def _db_revision(db) -> Optional[str]:
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except (OperationalError, ProgrammingError):
        db.rollback()
        return None


def _count(db, table: str) -> int:
    from sqlalchemy import text

    return int(db.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0)


def _legacy_checklist_types() -> List[str]:
    from app.services import checklist_builtin

    return [seed["checklist_type"] for seed in checklist_builtin.SEED_DEFINITIONS]


def remove_legacy_seed_rows(db) -> Dict[str, int]:
    """Delete the rows migrations 0034, 0037, 0039 and 0044 insert into every
    new database (they describe the store the code was first written for).
    Safe to run again: a second run finds nothing."""
    from sqlalchemy import and_, delete, select

    from app.models import CategoryOverride, ChecklistDefinition, ChecklistDefinitionVersion, WhatsAppGroup

    removed = {}
    removed["category_overrides"] = db.execute(
        delete(CategoryOverride).where(
            (
                (CategoryOverride.match_type == "supplier")
                & (CategoryOverride.match_value == LEGACY_CATEGORY_SUPPLIER)
                & CategoryOverride.created_by.is_(None)  # 0034 wrote no author
            )
            | (CategoryOverride.created_by == LEGACY_CATEGORY_CREATED_BY)
        )
    ).rowcount
    ids = list(
        db.execute(
            select(ChecklistDefinition.id).where(ChecklistDefinition.checklist_type.in_(_legacy_checklist_types()))
        ).scalars()
    )
    removed["checklist_definition_versions"] = (
        db.execute(delete(ChecklistDefinitionVersion).where(ChecklistDefinitionVersion.definition_id.in_(ids))).rowcount
        if ids
        else 0
    )
    removed["checklist_definitions"] = (
        db.execute(delete(ChecklistDefinition).where(ChecklistDefinition.id.in_(ids))).rowcount if ids else 0
    )
    removed["whatsapp_groups"] = db.execute(
        delete(WhatsAppGroup).where(
            and_(
                WhatsAppGroup.key.in_(LEGACY_WHATSAPP_KEYS),
                WhatsAppGroup.builtin.is_(True),
                WhatsAppGroup.created_by == LEGACY_WHATSAPP_CREATED_BY,
            )
        )
    ).rowcount
    return {k: int(v or 0) for k, v in removed.items()}


def write_pack_row(db, pack, author: str = "store-init") -> bool:
    """Store the pack as the newest setting_changes row "store_pack" unless
    the newest one already holds the same pack. Returns True if written."""
    from sqlalchemy import select

    from app.setting_models import SettingChange
    from app.store import pack as store_pack

    value = json.dumps(pack.to_json_dict(), sort_keys=True, ensure_ascii=False)
    newest = db.execute(
        select(SettingChange)
        .where(SettingChange.key == store_pack.SETTING_KEY)
        .order_by(SettingChange.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if newest is not None and newest.value == value:
        return False
    db.add(
        SettingChange(
            key=store_pack.SETTING_KEY,
            value=value,
            previous_value=newest.value if newest is not None else None,
            previous_source="db" if newest is not None else None,
            changed_by_user_id=None,
            changed_by_name=store_pack.SETTING_AUTHOR,
            changed_at=dt.datetime.now(dt.timezone.utc),
            note=f"store pack {pack.pack_id} ({author})",
        )
    )
    db.flush()
    return True


def insert_pack_rows(db, pack, author: str) -> Dict[str, int]:
    """Checklists, WhatsApp roles and category rules from the pack that are
    not in the database yet (by checklist_type / key / match)."""
    from sqlalchemy import select

    from app.models import CategoryOverride, ChecklistDefinition, ChecklistDefinitionVersion, WhatsAppGroup
    from app.services.checklist_admin import rows_for_seeds
    from app.store import pack as store_pack

    now = dt.datetime.now(dt.timezone.utc)
    inserted = {"checklist_definitions": 0, "whatsapp_groups": 0, "category_overrides": 0}

    have_roles = set(db.execute(select(WhatsAppGroup.key)).scalars())
    for role in pack.whatsapp_roles:
        if role.key in have_roles:
            continue
        db.add(WhatsAppGroup(key=role.key, label=role.label, chat_id=None, builtin=True, active=True,
                             created_at=now, created_by=author))
        inserted["whatsapp_groups"] += 1

    have_types = set(db.execute(select(ChecklistDefinition.checklist_type)).scalars())
    for row in rows_for_seeds(store_pack.checklist_seed_definitions(pack), now, author):
        if row["definition"]["checklist_type"] in have_types:
            continue
        definition = ChecklistDefinition(**row["definition"])
        db.add(definition)
        db.flush()
        for version in row["versions"]:
            db.add(ChecklistDefinitionVersion(definition_id=definition.id, **version))
        inserted["checklist_definitions"] += 1

    have_rules = set(db.execute(select(CategoryOverride.match_type, CategoryOverride.match_value)).all())
    for rule in pack.category_rules:
        key = (rule.match_type, rule.stored_match_value())
        if key in have_rules:
            continue
        db.add(
            CategoryOverride(
                match_type=rule.match_type,
                match_value=key[1],
                category_group=rule.category_group,
                category=rule.category,
                subcategory=rule.subcategory,
                note=rule.note,
                created_at=now,
                active=True,
                created_by=author,
            )
        )
        have_rules.add(key)
        inserted["category_overrides"] += 1
    db.flush()
    return inserted


def init_store(
    db,
    code: str,
    pack_path: str,
    *,
    adopt: bool = False,
    uploads_root=None,
    settings=None,
    reload_caches: bool = True,
) -> dict:
    """The six store-init steps on an open session, in one transaction
    (contract 3.5; platform contract 10 step 4 calls this for a new store).

    ``uploads_root`` is the store's own upload folder (it gets the identity
    marker); default: UPLOADS_ROOT from ``settings`` (single mode). Raises
    InitError / StoreIdentityError / PackError; on any error the session is
    rolled back and nothing is written."""
    from app.config import STORE_CODE_RE, get_settings
    from app.store import identity
    from app.store import pack as store_pack

    code = (code or "").strip().upper()
    if not STORE_CODE_RE.match(code):
        raise InitError(f"{code!r} is not a valid store code (3-24 letters, digits and _).")
    pack = store_pack.load_pack_file(pack_path)
    author = f"store-init ({pack.pack_id})"
    if uploads_root is not None:
        import pathlib

        root = pathlib.Path(uploads_root).expanduser().resolve()
    else:
        root = identity.uploads_root(settings if settings is not None else get_settings())

    try:
        # 1. At Alembic head.
        current = _db_revision(db)
        heads = _alembic_heads()
        if current not in heads:
            raise InitError(f"The database is at {current or 'no revision'}, not {heads[0]}: run `alembic upgrade head` first.")
        # 2. Not another store's; not a store with data unless adopting.
        stamp = identity.read_stamp(db)
        if stamp is not None and stamp["store_code"] != code:
            raise InitError(f"This database belongs to {stamp['store_code']}, not {code}.")
        marker = identity.read_marker(root)
        if marker is not None and marker.get("store_code") != code:
            raise InitError(f"The upload folder {root} belongs to {marker.get('store_code')}, not {code}.")
        # Contract 3.5 step 2: store data means an existing store, stamped or
        # not (an adopted store keeps its seeded rows - its WhatsApp roles hold
        # its chat ids); only --adopt goes on, and it removes nothing.
        if not adopt:
            busy = {t: _count(db, t) for t in DATA_TABLES}
            busy = {t: n for t, n in busy.items() if n}
            if busy:
                raise InitError(
                    "This database already has store data ("
                    + ", ".join(f"{t}: {n}" for t, n in busy.items())
                    + "). Use --adopt to keep it and only stamp it."
                )
        # 3. Remove the legacy seeded rows (not for an adopted store).
        removed = {} if adopt else remove_legacy_seed_rows(db)
        # 4. The pack.
        pack_written = write_pack_row(db, pack, author)
        inserted = insert_pack_rows(db, pack, author)
        # 5. The stamp (same transaction), then the upload-folder marker.
        instance_id = identity.stamp_database(db, code, "store-init", commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    identity.write_uploads_marker(root, code, instance_id)
    if reload_caches:
        store_pack.reload_pack()
        try:
            from app.services import checklist_schemas

            checklist_schemas.clear_cache()
        except Exception:  # noqa: BLE001 - only a cache
            pass
    # 6. Summary.
    return {
        "store_code": code,
        "pack_id": pack.pack_id,
        "pack_written": pack_written,
        "removed": removed,
        "inserted": inserted,
        "instance_id": instance_id,
    }


def store_init(code: str, pack_path: str, adopt: bool = False, db=None, settings=None) -> dict:
    """The six store-init steps in one transaction (contract 3.5): the CLI
    form, on this server's database (SessionLocal) unless ``db`` is given."""
    own = db is None
    if own:
        from app.db import SessionLocal

        db = SessionLocal()
    try:
        return init_store(db, code, pack_path, adopt=adopt, settings=settings)
    finally:
        if own:
            db.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    from app.store.identity import StoreIdentityError
    from app.store.pack import PackError

    try:
        _print(store_init(args.code, args.pack, adopt=args.adopt))
    except (InitError, StoreIdentityError, PackError) as exc:
        print(f"store-init refused: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_pack(args) -> int:
    from app.store import pack as store_pack

    if args.pack_cmd == "schema":
        _print(store_pack.json_schema())
        return 0
    if args.pack_cmd == "show":
        pack = store_pack.current_pack()
        _print({"source": store_pack.current_source(), "pack": pack.to_json_dict()})
        return 0
    try:
        pack = store_pack.load_pack_file(args.path)
    except store_pack.PackError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.pack_cmd == "validate":
        print(f"OK: {pack.pack_id} ({args.path})")
        return 0
    # load
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        written = write_pack_row(db, pack, "pack load")
        db.commit()
    finally:
        db.close()
    store_pack.reload_pack()
    print(f"{'Loaded' if written else 'Already loaded'}: {pack.pack_id} (restart the server to use it)")
    return 0


def cmd_identity(args) -> int:
    from app.db import SessionLocal
    from app.store import identity

    db = SessionLocal()
    try:
        stamp = identity.read_stamp(db)
    finally:
        db.close()
    root = identity.uploads_root()
    _print({"database": stamp, "uploads_root": str(root), "uploads_marker": identity.read_marker(root)})
    return 0


def cmd_branding(args) -> int:
    """Print the branding body; with --env FILE, for the settings in that file
    only (nothing from the process environment or .env)."""
    from app.config import Settings
    from app.store.branding import branding_payload

    if args.env:
        values = {}
        for raw in open(args.env, encoding="utf-8").read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip().lower()] = value
        fields = Settings.model_fields
        unknown = [k for k in values if k not in fields]
        if unknown:
            print(f"Unknown settings in {args.env}: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        settings = Settings(_env_file=None, **values)
        from unittest import mock

        from app.pos import gating, registry

        with mock.patch.object(gating, "get_settings", lambda: settings), mock.patch.object(
            registry, "get_settings", lambda: settings
        ):
            registry.reset_cache()
            body = branding_payload(s=settings)
        registry.reset_cache()
    else:
        body = branding_payload()
    print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.store", description="KloudC RetailOps store commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="set up this database as a store (store-init)")
    p.add_argument("--code", required=True)
    p.add_argument("--pack", required=True)
    p.add_argument("--adopt", action="store_true", help="an existing store: keep its rows, only stamp it")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("pack", help="store pack commands")
    psub = p.add_subparsers(dest="pack_cmd", required=True)
    for name in ("validate", "load"):
        q = psub.add_parser(name)
        q.add_argument("path")
    psub.add_parser("show")
    psub.add_parser("schema")
    p.set_defaults(func=cmd_pack)
    p = sub.add_parser("identity", help="store identity")
    isub = p.add_subparsers(dest="identity_cmd", required=True)
    isub.add_parser("show")
    p.set_defaults(func=cmd_identity)
    p = sub.add_parser("branding", help="print the branding body")
    p.add_argument("--env", default="", help="a .env-style file with the store's settings")
    p.set_defaults(func=cmd_branding)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    os.environ.setdefault("SCHEDULER_ENABLED", "false")
    sys.exit(main())
