"""Platform commands (contract 10, 11.3, 8.4).

    python -m app.platform migrate-platform
    python -m app.platform create-master --username U [--display-name N] [--email E]
    python -m app.platform list-stores [--status S]
    python -m app.platform provision --code C --name N --pack P --tz Z --pos toast --owner-username U [--no-password]
    python -m app.platform approve REQUEST_ID [--code C] [--pack P] [--tz Z]
    python -m app.platform retry CODE
    python -m app.platform suspend CODE --reason R
    python -m app.platform resume CODE
    python -m app.platform migrate-all [--store CODE] [--dry-run] [--keep-going]
    python -m app.platform gen-key [--id K]
    python -m app.platform rotate-keys [--dry-run]
    python -m app.platform purge-store CODE --confirm CODE
    python -m app.platform prune-logins [--days 7]

Onboarding a store that already runs on another system (app/ops,
docs/PILOT_RUNBOOK.md):

    python -m app.platform pilot-mode CODE on|off
    python -m app.platform label-printing CODE on|off|status [--url URL]
    python -m app.platform import-sqlite CODE --sqlite PATH [--workdir DIR] [--uploads DIR]
                                         [--replace --confirm CODE] [--no-keep-owner] [--keep-workdir]
    python -m app.platform load-pack CODE --file PATH_OUTSIDE_GIT [--no-rows]
    python -m app.platform parity CODE --reference PATH [--days 14] [--log-dir DIR]

Passwords come from a prompt, or RETAILOPS_MASTER_PASSWORD /
RETAILOPS_OWNER_PASSWORD (never a command-line argument). Every command
uses PLATFORM_DATABASE_URL (and the store URL template) from the settings.
"""

import argparse
import getpass
import json
import os
import sys
from typing import List, Optional

ACTOR = "cli:" + (os.environ.get("USER") or "operator")


def _print(value) -> None:
    print(json.dumps(value, indent=1, ensure_ascii=False, default=str))


def _password(env: str, prompt: str, minimum: int) -> str:
    pw = os.environ.get(env)
    if pw is None:
        pw = getpass.getpass(prompt)
        again = getpass.getpass("Again: ")
        if pw != again:
            raise SystemExit("The passwords do not match.")
    if len(pw) < minimum:
        raise SystemExit(f"The password needs at least {minimum} characters.")
    return pw


def cmd_migrate_platform(args) -> int:
    from app.platform.db import upgrade_platform_db

    print(f"platform database at {upgrade_platform_db()}")
    return 0


def cmd_create_master(args) -> int:
    from app.platform.db import platform_session
    from app.platform.masters import PASSWORD_MIN, create_master

    pw = _password("RETAILOPS_MASTER_PASSWORD", f"Password for {args.username}: ", PASSWORD_MIN)
    with platform_session() as db:
        user = create_master(db, args.username, pw, args.display_name or "", args.email, actor=ACTOR, actor_type="system")
        print(f"master {user.username} created")
    return 0


def cmd_list_stores(args) -> int:
    from app.platform.db import platform_session
    from app.platform.stores import list_stores

    with platform_session() as db:
        _print(list_stores(db, args.status))
    return 0


def cmd_provision(args) -> int:
    from app.auth import hash_password
    from app.platform.provisioning import create_store_manually

    pw_hash = None
    if not args.no_password:
        pw_hash = hash_password(_password("RETAILOPS_OWNER_PASSWORD", f"Password for {args.owner_username}: ", 10))
    out = create_store_manually(code=args.code, name=args.name, pack_id=args.pack, timezone=args.tz,
                                pos_provider=args.pos, owner_username=args.owner_username,
                                owner_password_hash=pw_hash, actor=ACTOR, run="inline")
    _print(out)
    return 0 if out.get("status") == "active" else 1


def cmd_approve(args) -> int:
    from app.platform.provisioning import approve_signup

    overrides = {k: v for k, v in {"code": args.code, "pack_id": args.pack, "timezone": args.tz}.items() if v}
    out = approve_signup(args.request_id, actor=ACTOR, overrides=overrides, run="inline")
    _print(out)
    return 0 if out.get("status") == "active" else 1


def cmd_retry(args) -> int:
    from app.platform.provisioning import retry_provisioning

    out = retry_provisioning(args.code.upper(), actor=ACTOR, run="inline")
    _print(out)
    return 0 if out.get("status") == "active" else 1


def cmd_suspend(args) -> int:
    from app.platform.db import platform_session
    from app.platform.stores import suspend

    with platform_session() as db:
        suspend(db, args.code.upper(), actor=ACTOR, reason=args.reason)
    print(f"{args.code.upper()} suspended")
    return 0


def cmd_resume(args) -> int:
    from app.platform.db import platform_session
    from app.platform.stores import resume

    with platform_session() as db:
        resume(db, args.code.upper(), actor=ACTOR)
    print(f"{args.code.upper()} active")
    return 0


def migrate_all(store: Optional[str] = None, dry_run: bool = False, keep_going: bool = False,
                migrator=None) -> dict:
    """Contract 11.3: platform DB, then every shared store on this cluster."""
    from sqlalchemy import select

    from app.platform import bridge
    from app.platform.audit import audit
    from app.platform.db import platform_session, upgrade_platform_db
    from app.platform.models import Store
    from app.platform.provisioning import MIGRATOR

    migrator = migrator or MIGRATOR
    results = {"platform": None, "stores": {}, "dedicated": [], "ok": True}
    results["platform"] = "dry-run" if dry_run else upgrade_platform_db()
    with platform_session() as db:
        q = select(Store).where(Store.status.in_(("active", "suspended", "provisioning", "failed"))).order_by(Store.code)
        if store:
            q = q.where(Store.code == store.upper())
        stores = list(db.execute(q).scalars())
        for s in stores:
            if s.hosting != "shared" or s.db_host_ref:
                results["dedicated"].append(s.code)  # migrate on their own server
                continue
            if s.status in ("provisioning", "failed") and s.provisioning_step in (None, "reserve"):
                results["stores"][s.code] = "no database yet"
                continue
            if dry_run:
                results["stores"][s.code] = "would migrate"
                continue
            try:
                rev = migrator(bridge.store_url(s.db_name, s.db_host_ref))
                s.schema_revision = (rev or "")[:32] or None
                db.commit()
                results["stores"][s.code] = rev
            except Exception as exc:  # noqa: BLE001 - reported per store
                db.rollback()
                results["stores"][s.code] = f"FAILED: {type(exc).__name__}: {str(exc)[:200]}"
                results["ok"] = False
                if not keep_going:
                    break
        if not dry_run:
            audit("migrate_all.run", actor_type="system", actor_name=ACTOR, details=results, db=db)
            db.commit()
    return results


def cmd_migrate_all(args) -> int:
    out = migrate_all(args.store, args.dry_run, args.keep_going)
    _print(out)
    return 0 if out["ok"] else 1


def cmd_gen_key(args) -> int:
    from app.platform.crypto import gen_key

    print(gen_key(args.id))
    print("Put this FIRST in RETAILOPS_SECRET_KEYS (keep the old keys after it), restart, then run rotate-keys.",
          file=sys.stderr)
    return 0


def cmd_rotate_keys(args) -> int:
    from app.platform.credentials import rotate_all

    counts = rotate_all(actor=ACTOR, dry_run=args.dry_run)
    _print(counts)
    return 0 if not counts["credentials_failed"] and not counts["totp_failed"] else 1


def cmd_purge_store(args) -> int:
    from app.platform.provisioning import purge_store

    code = args.code.upper()
    if (args.confirm or "").upper() != code:
        print(f"Refused: repeat the code with --confirm {code}", file=sys.stderr)
        return 1
    _print(purge_store(code, actor=ACTOR))
    return 0


def cmd_prune_logins(args) -> int:
    from app.platform.db import platform_session
    from app.platform.throttle import prune_login_attempts

    with platform_session() as db:
        print(f"{prune_login_attempts(db, args.days)} old login attempts removed")
    return 0


def cmd_pilot_mode(args) -> int:
    from app.platform.db import platform_session
    from app.platform.stores import put_settings

    on = args.state == "on"
    with platform_session() as db:
        values = put_settings(db, args.code.upper(), {"pilot_mode": on}, actor=ACTOR, actor_type="system")
    state = "ON - nothing is sent from this store" if values.get("pilot_mode") else "off - the store sends as usual"
    print(f"{args.code.upper()}: pilot mode {state}")
    return 0


def cmd_label_printing(args) -> int:
    """Label printing on / off / status (docs/LABEL_PRINTING_FEATURE.md 5).
    Through put_settings, so validated and audited as cli:<user>."""
    from app.platform import feature_requests
    from app.platform.db import platform_session
    from app.platform.errors import PlatformError
    from app.platform.stores import put_settings, settings_values, store_by_code

    code = args.code.upper()
    with platform_session() as db:
        store = store_by_code(db, code)
        values = settings_values(db, store)
        if args.state == "on":
            updates = {"label_printing_enabled": True}
            if args.url:
                updates["labels_url"] = args.url
            elif not values.get("labels_url"):
                raise PlatformError(400, "invalid", feature_requests.MSG_NEED_ADDRESS, field="labels_url")
            values = put_settings(db, code, updates, actor=ACTOR, actor_type="system")
        elif args.state == "off":
            if args.url:
                raise PlatformError(400, "invalid", "--url only goes with on.")
            values = put_settings(db, code, {"label_printing_enabled": False}, actor=ACTOR, actor_type="system")
    state = feature_requests.label_printing_state_of(values)
    url = values.get("labels_url") or "(no address)"
    print(f"{code}: label printing {state} {url}")
    return 0


def cmd_import_sqlite(args) -> int:
    from app.ops.store_import import StoreImportError, import_sqlite

    code = args.code.upper()
    if args.replace and (args.confirm or "").upper() != code:
        print(f"Refused: --replace needs --confirm {code}", file=sys.stderr)
        return 1
    try:
        report = import_sqlite(code, args.sqlite, workdir=args.workdir, replace=args.replace,
                               keep_owner=not args.no_keep_owner, uploads_from=args.uploads,
                               keep_workdir=args.keep_workdir, actor=ACTOR,
                               log=lambda line: print(line, file=sys.stderr))
    except StoreImportError as exc:
        print(f"Import refused: {exc}", file=sys.stderr)
        return 1
    report.pop("table_rows", None)
    _print(report)
    print("Restart the server (or resume the store) so it opens the imported database.", file=sys.stderr)
    return 0


def cmd_load_pack(args) -> int:
    from app.ops.private_pack import PrivatePackError, load_private_pack

    try:
        out = load_private_pack(args.code, args.file, rows=not args.no_rows, actor=ACTOR)
    except PrivatePackError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    _print(out)
    return 0


def _demo(args, fn) -> int:
    import datetime as _dt

    from app.ops.demo_store import DemoStoreError
    from app.ops.store_import import StoreImportError

    try:
        out = fn(workdir=args.workdir, password_file=args.review_password_file, seed=args.seed, days=args.days,
                 items=args.items, end_date=_dt.date.fromisoformat(args.end_date) if args.end_date else None,
                 log=lambda line: print(line, file=sys.stderr))
    except (DemoStoreError, StoreImportError) as exc:
        print(f"DEMO: refused: {exc}", file=sys.stderr)
        return 1
    _print(out)
    return 0


def cmd_demo_provision(args) -> int:
    from app.ops.demo_store import provision

    return _demo(args, provision)


def cmd_demo_reset(args) -> int:
    from app.ops.demo_store import _store, reset

    if args.skip_if_missing and _store() is None:
        print("DEMO: no DEMO store on this server (demo-provision makes one) - nothing to do", file=sys.stderr)
        return 0
    return _demo(args, reset)


def cmd_parity(args) -> int:
    from app.ops.parity import ParityError, run_parity

    try:
        report = run_parity(args.code, args.reference, days=args.days, log_dir=args.log_dir)
    except ParityError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    _print(report)
    return 0 if report["ok"] else 1


def main(argv: Optional[List[str]] = None) -> int:
    from app.platform.errors import PlatformError

    ap = argparse.ArgumentParser(prog="python -m app.platform", description="KloudC RetailOps platform commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate-platform").set_defaults(func=cmd_migrate_platform)
    p = sub.add_parser("create-master")
    p.add_argument("--username", required=True)
    p.add_argument("--display-name", default="")
    p.add_argument("--email", default=None)
    p.set_defaults(func=cmd_create_master)
    p = sub.add_parser("list-stores")
    p.add_argument("--status", default=None)
    p.set_defaults(func=cmd_list_stores)
    p = sub.add_parser("provision", help="create a store without a sign-up")
    p.add_argument("--code", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--pack", default="generic")
    p.add_argument("--tz", required=True)
    p.add_argument("--pos", default="toast", choices=("toast", "clover", "csv"))
    p.add_argument("--owner-username", required=True)
    p.add_argument("--no-password", action="store_true", help="print a one-time set-up link instead")
    p.set_defaults(func=cmd_provision)
    p = sub.add_parser("approve")
    p.add_argument("request_id", type=int)
    p.add_argument("--code")
    p.add_argument("--pack")
    p.add_argument("--tz")
    p.set_defaults(func=cmd_approve)
    p = sub.add_parser("retry")
    p.add_argument("code")
    p.set_defaults(func=cmd_retry)
    p = sub.add_parser("suspend")
    p.add_argument("code")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_suspend)
    p = sub.add_parser("resume")
    p.add_argument("code")
    p.set_defaults(func=cmd_resume)
    p = sub.add_parser("migrate-all")
    p.add_argument("--store")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep-going", action="store_true")
    p.set_defaults(func=cmd_migrate_all)
    p = sub.add_parser("gen-key")
    p.add_argument("--id", default=None)
    p.set_defaults(func=cmd_gen_key)
    p = sub.add_parser("rotate-keys")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_rotate_keys)
    p = sub.add_parser("purge-store")
    p.add_argument("code")
    p.add_argument("--confirm", required=True)
    p.set_defaults(func=cmd_purge_store)
    p = sub.add_parser("prune-logins")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_prune_logins)
    p = sub.add_parser("pilot-mode", help="pilot mode on/off: nothing is sent from the store while on")
    p.add_argument("code")
    p.add_argument("state", choices=("on", "off"))
    p.set_defaults(func=cmd_pilot_mode)
    p = sub.add_parser("label-printing", help="label printing on/off/status for one store")
    p.add_argument("code")
    p.add_argument("state", choices=("on", "off", "status"))
    p.add_argument("--url", default=None, help="the label site address (https://labels-<name>.<platform domain>)")
    p.set_defaults(func=cmd_label_printing)
    p = sub.add_parser("import-sqlite", help="copy a single-store SQLite database into a provisioned store")
    p.add_argument("code")
    p.add_argument("--sqlite", required=True, help="the source database (opened read-only)")
    p.add_argument("--workdir", default=None, help="folder for the snapshot (default: a temporary folder)")
    p.add_argument("--uploads", default=None, help="the old system's upload folder to copy in (optional)")
    p.add_argument("--replace", action="store_true", help="overwrite a store that already has data")
    p.add_argument("--confirm", default=None)
    p.add_argument("--no-keep-owner", action="store_true", help="don't add the provisioned owner login back")
    p.add_argument("--keep-workdir", action="store_true")
    p.set_defaults(func=cmd_import_sqlite)
    p = sub.add_parser("load-pack", help="load a private store pack from a file outside git")
    p.add_argument("code")
    p.add_argument("--file", required=True)
    p.add_argument("--no-rows", action="store_true", help="only the pack, no checklists / roles / rules")
    p.set_defaults(func=cmd_load_pack)
    for name, func, help_text in (
        ("demo-provision", cmd_demo_provision, "create the DEMO store for App Review (made-up data, view only)"),
        ("demo-reset", cmd_demo_reset, "rebuild the DEMO store's made-up data (nightly)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--workdir", default=None, help="folder for the build (default: a temporary folder)")
        p.add_argument("--review-password-file", default=None,
                       help="the appreview password file (default <install>/secrets/demo_review_password)")
        p.add_argument("--seed", type=int, default=None)
        p.add_argument("--days", type=int, default=90)
        p.add_argument("--items", type=int, default=400)
        p.add_argument("--end-date", default=None)
        if name == "demo-reset":
            p.add_argument("--skip-if-missing", action="store_true", help="exit quietly when there is no DEMO store")
        p.set_defaults(func=func)
    p = sub.add_parser("parity", help="compare the store's key numbers with a reference SQLite database")
    p.add_argument("code")
    p.add_argument("--reference", required=True, help="the reference database (opened read-only)")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--log-dir", default=None)
    p.set_defaults(func=cmd_parity)
    args = ap.parse_args(argv)
    # Multi mode: the platform registry / settings sources, as the server
    # installs them at start - without them a store context in this process
    # (provisioning's store-init step, pilot-mode, load-pack) finds no store.
    from app.platform import sources

    sources.configure()
    try:
        return args.func(args)
    except PlatformError as exc:
        print(f"Refused: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("SCHEDULER_ENABLED", "false")
    sys.exit(main())
