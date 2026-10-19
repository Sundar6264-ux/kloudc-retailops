"""Import an existing single-store SQLite database into a provisioned store
of the shared service (onboarding the first real stores).

    python -m app.platform import-sqlite CODE --sqlite /path/store.db [--workdir DIR]
        [--replace --confirm CODE] [--uploads /path/uploads] [--no-keep-owner] [--keep-workdir]

1. Checks: the store exists, is active or suspended, lives on the shared
   cluster, and nothing else is connected to its database (suspend the
   store in the master console or stop the server first). A store whose
   database already holds business data (sales, inventory, invoices,
   checklists...) is refused unless --replace --confirm CODE.
2. Snapshot: the source is opened READ-ONLY and copied with SQLite's online
   backup API into the work folder (one consistent snapshot even while the
   source's own server keeps writing); ``quick_check`` must say ok. The
   source file is never written.
3. The snapshot (never the source) is upgraded to this release's schema
   head (``alembic upgrade head``).
4. A new staging database ``<db_name>__import`` is created and
   scripts/pg/copy_sqlite_to_pg.py copies every table into it (one
   transaction) and compares every table's row count and a checksum of
   every column with the snapshot. Any difference fails the import, the
   staging database is dropped and the store's own database is exactly as
   it was (``store.import_failed`` in the audit log).
5. After the verified copy (all reported, on the staging database): the store identity stamp is set
   to CODE and the registry's instance id; sign-in sessions and push
   device tokens of the old system are removed (people sign in again; their
   phones register again); the owner login made at provisioning is added
   back when the imported data has no user with that name (--no-keep-owner
   skips it). Then the staging database replaces the store's database
   (old renamed aside, staging renamed into place, old dropped). With
   --uploads, the old system's upload folders (invoices,
   team media, flyers) are copied in (existing files are never overwritten).
6. The registry is updated (schema revision, settings version) and
   ``store.imported`` is written to the audit log with the counts, the
   snapshot's SHA-256 and the timings. The work folder is deleted unless
   --keep-workdir.

Restart the server afterwards (or resume the store) so it opens the new
database. Load the store's private pack next (load-pack) and keep the store
in pilot mode until the parity check is clean.
"""

import datetime as dt
import hashlib
import logging
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
PG_SCRIPTS = BACKEND_DIR / "scripts" / "pg"
BUSINESS_TABLES = (
    "orders",
    "inventory_snapshots",
    "invoice_submissions",
    "checklist_submissions",
    "placed_orders",
    "purchase_log",
)
CLEARED_AFTER_IMPORT = ("user_sessions", "device_tokens")


class StoreImportError(RuntimeError):
    pass


def _copy_tool():
    if str(PG_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(PG_SCRIPTS))
    import copy_sqlite_to_pg  # noqa: PLC0415

    return copy_sqlite_to_pg


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def connect_ro(path) -> sqlite3.Connection:
    """A read-only connection. A WAL database that nothing has open (no
    -wal / -shm next to it, e.g. a copy) can't be opened with mode=ro alone -
    SQLite would need to create the -shm - so it is opened as immutable
    (safe: with no -wal file there is no writer)."""
    p = pathlib.Path(path)
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.OperationalError:
        if p.with_name(p.name + "-wal").exists():
            raise
        return sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)


def snapshot_sqlite(source: pathlib.Path, dest: pathlib.Path) -> Dict[str, Any]:
    """A consistent copy of `source` (opened read-only) at `dest` (in
    rollback-journal mode, so every later read-only open just works)."""
    src = connect_ro(source)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()
    con = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        check = con.execute("PRAGMA quick_check").fetchone()[0]
        try:
            revision = con.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            revision = None
    finally:
        con.close()
    if check != "ok":
        raise StoreImportError(f"the snapshot failed quick_check: {check}")
    return {"bytes": dest.stat().st_size, "revision": revision[0] if revision else None}


def sqlite_counts(path: pathlib.Path) -> Dict[str, int]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: int(con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]) for n in names}
    finally:
        con.close()


def _quote_ident(name: str) -> str:
    import re

    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise StoreImportError(f"{name!r} is not a safe database name")
    return '"' + name + '"'


def _table_exists(conn, table: str) -> bool:
    from sqlalchemy import text

    return conn.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = :t"),
        {"t": table},
    ).first() is not None


def _target_state(url: str, owner_username: Optional[str]) -> Dict[str, Any]:
    from sqlalchemy import text

    from app.platform.db import make_engine

    eng = make_engine(url, pool_size=1, max_overflow=0)
    try:
        with eng.connect() as conn:
            busy = {}
            for t in BUSINESS_TABLES:
                if _table_exists(conn, t):
                    n = int(conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() or 0)
                    if n:
                        busy[t] = n
            owner = None
            if owner_username and _table_exists(conn, "users"):
                row = conn.execute(
                    text("SELECT * FROM users WHERE lower(username) = lower(:u)"), {"u": owner_username}
                ).mappings().first()
                owner = dict(row) if row is not None else None
            return {"busy": busy, "owner": owner}
    finally:
        eng.dispose()


def _release_own_connections(code: str) -> None:
    """This process's own pooled connections to the store (its tenancy
    engine, if anything here opened one) are closed first: only other
    processes' connections should stop the import."""
    from app.platform import bridge

    t = bridge.tenancy()
    if t is None:
        return
    for name in ("dispose_engine", "clear_tenant_caches"):
        fn = getattr(t, name, None)
        if fn is not None:
            try:
                fn(code)
            except Exception:  # noqa: BLE001 - nothing open is fine
                pass


STAGING_SUFFIX = "__import"
RETIRED_SUFFIX = "__replaced"


def _side_name(db_name: str, suffix: str) -> str:
    """<db_name><suffix>, kept within PostgreSQL's 63-byte names."""
    return db_name[: 63 - len(suffix)] + suffix


def _admin_engine():
    from app.platform import bridge
    from app.platform.db import make_engine

    return make_engine(bridge.admin_url(), isolation_level="AUTOCOMMIT", pool_size=1, max_overflow=0)


def _other_connections(conn, db_name: str) -> int:
    from sqlalchemy import text

    return int(conn.execute(
        text("SELECT count(*) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
        {"n": db_name},
    ).scalar() or 0)


def _database_exists(conn, db_name: str) -> bool:
    from sqlalchemy import text

    return conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}).first() is not None


def _refuse_if_connected(db_name: str, code: str) -> None:
    _release_own_connections(code)
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            others = _other_connections(conn, db_name)
    finally:
        admin.dispose()
    if others:
        raise StoreImportError(
            f"{others} other connection(s) are open on the store's database: suspend the store in the "
            "master console (or stop the server) and try again"
        )


def _create_staging_database(staging: str) -> None:
    """A new, empty database for the copy (a leftover of an interrupted
    import is dropped first). The store's own database is not touched."""
    from sqlalchemy import text

    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_quote_ident(staging)} WITH (FORCE)"))
            conn.execute(
                text(
                    f"CREATE DATABASE {_quote_ident(staging)} TEMPLATE template0 "
                    "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
                )
            )
    finally:
        admin.dispose()


def _drop_database_quietly(name: str) -> None:
    from sqlalchemy import text

    try:
        admin = _admin_engine()
        try:
            with admin.connect() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)"))
        finally:
            admin.dispose()
    except Exception:  # noqa: BLE001 - cleanup; the error that matters is already raised
        logger.exception("Import: could not drop %s", name)


def _wait_for_no_connections(conn, db_name: str, code: str, seconds: float = 10.0) -> int:
    """Other sessions on `db_name` after this process's own pooled ones are
    closed, waiting up to `seconds` for short-lived ones to finish."""
    deadline = time.monotonic() + seconds
    while True:
        _release_own_connections(code)
        others = _other_connections(conn, db_name)
        if not others or time.monotonic() >= deadline:
            return others
        time.sleep(0.5)


def _swap_in(staging: str, db_name: str, code: str) -> None:
    """The verified staging database becomes the store's database: new
    connections to the old one are refused, the old one is renamed aside,
    the staging one renamed into place, the old one dropped. Refused (and
    nothing changed) while anything else stays connected."""
    from sqlalchemy import text

    retired = _side_name(db_name, RETIRED_SUFFIX)
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_quote_ident(retired)} WITH (FORCE)"))
            had_old = _database_exists(conn, db_name)
            if had_old:
                conn.execute(text(f"ALTER DATABASE {_quote_ident(db_name)} WITH ALLOW_CONNECTIONS false"))
            renamed = False
            try:
                if had_old:
                    others = _wait_for_no_connections(conn, db_name, code)
                    if others:
                        raise StoreImportError(
                            f"{others} other connection(s) opened on the store's database during the import: "
                            "suspend the store in the master console (or stop the server) and try again"
                        )
                    conn.execute(text(f"ALTER DATABASE {_quote_ident(db_name)} RENAME TO {_quote_ident(retired)}"))
                    renamed = True
                conn.execute(text(f"ALTER DATABASE {_quote_ident(staging)} RENAME TO {_quote_ident(db_name)}"))
            except Exception:
                if renamed:
                    conn.execute(text(f"ALTER DATABASE {_quote_ident(retired)} RENAME TO {_quote_ident(db_name)}"))
                if had_old:
                    conn.execute(text(f"ALTER DATABASE {_quote_ident(db_name)} WITH ALLOW_CONNECTIONS true"))
                raise
            if had_old:
                conn.execute(text(f"DROP DATABASE IF EXISTS {_quote_ident(retired)} WITH (FORCE)"))
    finally:
        admin.dispose()


def _after_copy(url: str, code: str, instance_id: Optional[str], owner: Optional[dict]) -> Dict[str, Any]:
    """Stamp, clear the old system's sessions / device tokens, add the owner back."""
    import uuid

    from sqlalchemy import MetaData, Table, text

    from app.platform.db import make_engine

    out: Dict[str, Any] = {}
    eng = make_engine(url, pool_size=1, max_overflow=0)
    try:
        with eng.begin() as conn:
            prev = conn.execute(text("SELECT store_code FROM store_identity WHERE id = 1")).scalar()
            out["previous_stamp"] = prev
            conn.execute(text("DELETE FROM store_identity"))
            conn.execute(
                text(
                    "INSERT INTO store_identity (id, store_code, instance_id, created_at, created_by) "
                    "VALUES (1, :c, :i, :t, 'import')"
                ),
                {"c": code, "i": instance_id or uuid.uuid4().hex, "t": dt.datetime.now(dt.timezone.utc)},
            )
            cleared = {}
            for t in CLEARED_AFTER_IMPORT:
                if _table_exists(conn, t):
                    cleared[t] = int(conn.execute(text(f'DELETE FROM "{t}"')).rowcount or 0)
            out["cleared"] = cleared
            out["owner_added"] = False
            if owner is not None:
                taken = conn.execute(
                    text("SELECT 1 FROM users WHERE lower(username) = lower(:u)"), {"u": owner["username"]}
                ).first()
                if taken is None:
                    users = Table("users", MetaData(), autoload_with=conn)
                    values = {k: v for k, v in owner.items() if k != "id" and k in users.c}
                    conn.execute(users.insert().values(**values))
                    out["owner_added"] = True
                else:
                    out["owner_kept_from_import"] = owner["username"]
    finally:
        eng.dispose()
    return out


def _copy_uploads(source: pathlib.Path, dest: pathlib.Path) -> Dict[str, int]:
    from app.store.identity import MARKER_NAME

    files = skipped = size = 0
    for p in sorted(source.rglob("*")):
        if not p.is_file() or p.name == MARKER_NAME or p.is_symlink():
            continue
        target = dest / p.relative_to(source)
        if target.exists():
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        files += 1
        size += p.stat().st_size
    return {"files": files, "bytes": size, "skipped_existing": skipped}


def import_sqlite(
    code: str,
    sqlite_path,
    *,
    workdir=None,
    replace: bool = False,
    keep_owner: bool = True,
    uploads_from=None,
    keep_workdir: bool = False,
    actor: str = "cli",
    log: Callable[[str], None] = print,
) -> Dict[str, Any]:
    from sqlalchemy import select

    from app.migrate import store_head_revision, upgrade_store_db
    from app.platform import bridge
    from app.platform.audit import audit
    from app.platform.db import platform_session
    from app.platform.models import Store
    from app.platform.provisioning import store_uploads_root

    code = (code or "").strip().upper()
    source = pathlib.Path(sqlite_path).expanduser().resolve()
    if not source.is_file():
        raise StoreImportError(f"{source} not found")
    timings: Dict[str, float] = {}
    t_all = time.monotonic()

    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
        if store is None:
            raise StoreImportError(f"no store {code}: provision it first")
        if store.status not in ("active", "suspended"):
            raise StoreImportError(f"store {code} is {store.status}: finish setting it up first")
        if store.hosting != "shared" or store.db_host_ref:
            raise StoreImportError(f"store {code} is on its own server: import there")
        db_name, instance_id, owner_username = store.db_name, store.instance_id, store.owner_username
    url = bridge.store_url(db_name, None)
    up = None
    if uploads_from:
        up = pathlib.Path(uploads_from).expanduser().resolve()
        if not up.is_dir():
            raise StoreImportError(f"{up} is not a folder")

    t = time.monotonic()
    state = _target_state(url, owner_username if keep_owner else None)
    if state["busy"] and not replace:
        raise StoreImportError(
            f"store {code} already has data ({', '.join(f'{k}: {v}' for k, v in state['busy'].items())}); "
            f"use --replace --confirm {code} to overwrite it"
        )
    timings["check_target"] = round(time.monotonic() - t, 2)

    own_workdir = workdir is None
    work = pathlib.Path(workdir).expanduser().resolve() if workdir else pathlib.Path(
        tempfile.mkdtemp(prefix=f"retailops-import-{code.lower()}-"))
    work.mkdir(parents=True, exist_ok=True)
    snap = work / f"{code.lower()}-import.db"
    if snap.exists():
        raise StoreImportError(f"{snap} already exists: use an empty work folder")
    staging = _side_name(db_name, STAGING_SUFFIX)
    staging_url = bridge.store_url(staging, None)
    staged = False
    try:
        t = time.monotonic()
        log(f"Snapshot of {source.name} (read-only) -> {snap}")
        snap_info = snapshot_sqlite(source, snap)
        timings["snapshot"] = round(time.monotonic() - t, 2)

        t = time.monotonic()
        head = store_head_revision()
        log(f"Upgrading the snapshot from {snap_info['revision']} to {head}")
        rev = upgrade_store_db(f"sqlite:///{snap}")
        if rev != head:
            raise StoreImportError(f"the snapshot is at {rev} after the upgrade, not {head}")
        timings["upgrade_snapshot"] = round(time.monotonic() - t, 2)
        digest = sha256_file(snap)
        counts = sqlite_counts(snap)

        t = time.monotonic()
        _refuse_if_connected(db_name, code)
        log(f"Creating the staging database {staging} (the store's own database is not touched yet)")
        _create_staging_database(staging)
        staged = True
        timings["staging_database"] = round(time.monotonic() - t, 2)

        t = time.monotonic()
        tool = _copy_tool()
        try:
            problems: List[str] = tool.copy(str(snap), staging_url, verify=True, log=log)
        except SystemExit as exc:  # the copy tool's refusals
            raise StoreImportError(f"copy refused: {exc}") from None
        timings["copy_and_verify"] = round(time.monotonic() - t, 2)
        if problems:
            raise StoreImportError(
                f"{len(problems)} difference(s) after the copy: " + "; ".join(problems[:5])
                + " - nothing was changed: the store's database is as it was"
            )
        log("Verified: every table's row count and column checksums match the snapshot")

        t = time.monotonic()
        after = _after_copy(staging_url, code, instance_id, state["owner"])
        log(f"Switching {code} to the imported database")
        _swap_in(staging, db_name, code)
        staged = False
        timings["switch"] = round(time.monotonic() - t, 2)
        t = time.monotonic()
        uploads = None
        if up is not None:
            uploads = _copy_uploads(up, store_uploads_root(code))
        timings["finish"] = round(time.monotonic() - t, 2)
        timings["total"] = round(time.monotonic() - t_all, 2)

        report = {
            "store_code": code,
            "source": source.name,
            "source_revision": snap_info["revision"],
            "revision": head,
            "snapshot_bytes": snap_info["bytes"],
            "snapshot_sha256": digest,
            "tables": len(counts),
            "rows": sum(n for t_, n in counts.items() if t_ != "alembic_version"),
            "differences": 0,
            "replaced": bool(state["busy"]),
            **after,
            "uploads": uploads,
            "timings_s": timings,
        }
        with platform_session() as pdb:
            store = pdb.execute(select(Store).where(Store.code == code)).scalar_one()
            store.schema_revision = head[:32]
            store.settings_version = int(store.settings_version or 1) + 1
            audit("store.imported", actor_type="system", actor_name=actor, store_code=code,
                  details={k: v for k, v in report.items() if k != "store_code"}, db=pdb)
            pdb.commit()
        bridge.store_changed(code)
        report["table_rows"] = counts
        return report
    except Exception as exc:
        _audit_failure(code, source.name, exc, actor)
        raise
    finally:
        if staged:
            _drop_database_quietly(staging)
        if not keep_workdir:
            if own_workdir:
                shutil.rmtree(work, ignore_errors=True)
            else:
                for p in (snap, snap.with_name(snap.name + "-wal"), snap.with_name(snap.name + "-shm")):
                    try:
                        p.unlink()
                    except FileNotFoundError:
                        pass


def _audit_failure(code: str, source_name: str, exc: BaseException, actor: str) -> None:
    """``store.import_failed`` in the audit log (the reason, first 500 characters)."""
    try:
        from app.platform.audit import audit
        from app.platform.db import platform_session

        with platform_session() as pdb:
            audit("store.import_failed", actor_type="system", actor_name=actor, store_code=code,
                  details={"source": source_name, "error": f"{type(exc).__name__}: {exc}"[:500]}, db=pdb)
            pdb.commit()
    except Exception:  # noqa: BLE001 - the raised error is what the operator sees
        logger.exception("Import: could not write store.import_failed")
