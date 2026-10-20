"""Off-boarding: archive -> final data export -> grace period -> delete store data
(docs/WEBSITE_READY_CONTRACT.md B5; track B).

States of ``store_offboarding`` (one row per store, kept as the tombstone):
- ``scheduled``  the store is archived, a final export was made, and its
                 data is deleted on ``purge_after`` (``OFFBOARD_GRACE_DAYS``)
- ``cancelled``  a master stopped it during the grace period
- ``deleted``    the store database was dropped, its files and credentials
                 deleted; the ``stores`` row stays (archived) as a tombstone

Guard rails: deleting needs a master, the typed Store ID and the master's
two-step code when the master has two-step sign-in; a store in pilot mode is
never deleted automatically; a dedicated store's database is deleted on its
own server. Every step is audited. Deleting again is a no-op.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select

from app.platform.audit import audit
from app.platform.errors import PlatformError
from app.platform.models import utcnow

logger = logging.getLogger(__name__)

STATES = ("scheduled", "cancelled", "deleted")


def grace_days() -> int:
    from app.platform.platform_settings import OFFBOARD_GRACE_DAYS, get_platform_setting

    try:
        return max(1, min(int(get_platform_setting(OFFBOARD_GRACE_DAYS)), 365))
    except (TypeError, ValueError):
        return 30


def _iso(v: Optional[dt.datetime]) -> Optional[str]:
    return v.isoformat() if v else None


def _aware(v: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=dt.timezone.utc)
    return v


def is_pilot(db, store) -> bool:
    from app.platform.stores import settings_values

    return bool(settings_values(db, store).get("pilot_mode"))


def view(code: str) -> Dict[str, Any]:
    """The Data tab's off-boarding block: state, dates, final export and the
    timeline (audit rows of this store's off-boarding)."""
    from app.platform.audit import list_audit
    from app.platform.data_models import StoreExport, StoreOffboarding
    from app.platform.db import platform_session
    from app.platform.export import export_view
    from app.platform.stores import store_by_code

    with platform_session() as db:
        store = store_by_code(db, code)
        row = db.get(StoreOffboarding, store.code)
        final = db.get(StoreExport, row.final_export_id) if row is not None and row.final_export_id else None
        timeline = []
        for action in ("store.archived", "store.offboarding_*", "store.purge*", "store.data_deleted"):
            timeline.extend(list_audit(db, store=store.code, action=action, limit=20))
        timeline.sort(key=lambda r: r["id"])
        return {
            "store_code": store.code,
            "status": store.status,
            "state": row.state if row is not None else None,
            "purge_after": _iso(row.purge_after) if row is not None else None,
            "started_at": _iso(row.started_at) if row is not None else None,
            "started_by": row.started_by if row is not None else None,
            "cancelled_at": _iso(row.cancelled_at) if row is not None else None,
            "purged_at": _iso(row.purged_at) if row is not None else None,
            "purged_by": row.purged_by if row is not None else None,
            "final_export": export_view(final) if final is not None else None,
            "pilot_mode": is_pilot(db, store),
            "grace_days": grace_days(),
            "hosting": store.hosting,
            "timeline": [
                {"at": t["at"], "action": t["action"], "actor_name": t["actor_name"], "details": t["details"]}
                for t in timeline
            ],
        }


def start(code: str, *, actor: str, confirm: str, reason: Optional[str] = None,
          client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Archive the store (when it is not already), schedule deletion after
    the grace period and make the final data export."""
    from app.platform import stores
    from app.platform.data_models import StoreOffboarding
    from app.platform.db import platform_session
    from app.platform.export import request_export

    with platform_session() as db:
        store = stores.store_by_code(db, code)
        if (confirm or "").strip().upper() != store.code:
            raise PlatformError(400, "invalid", f"Type the Store ID ({store.code}) to confirm.")
        row = db.get(StoreOffboarding, store.code)
        if row is not None and row.state in ("scheduled", "deleted"):
            raise PlatformError(409, "conflict", "This store is already " + (
                "scheduled for deletion." if row.state == "scheduled" else "deleted."))
        if store.status in ("provisioning",):
            raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
        if store.status != "archived":
            stores.archive(db, store.code, actor=actor, confirm=store.code, reason=reason or "Off-boarding",
                           client_ip=client_ip)
            store = stores.store_by_code(db, code)
        now = utcnow()
        purge_after = now + dt.timedelta(days=grace_days())
        if row is None:
            row = StoreOffboarding(store_code=store.code, state="scheduled", started_by=actor, started_at=now,
                                   updated_at=now)
            db.add(row)
        else:  # a cancelled one starts again
            row.state, row.started_by, row.started_at = "scheduled", actor, now
            row.cancelled_at = row.cancelled_by = None
        row.purge_after, row.note, row.updated_at = purge_after, (reason or None), now
        row.db_name = store.db_name
        audit("store.offboarding_started", actor_type="platform_user", actor_name=actor, store_code=store.code,
              details={"purge_after": purge_after.isoformat(), "grace_days": grace_days(), "reason": reason},
              client_ip=client_ip, db=db)
        db.commit()
    try:
        final = request_export(code, actor="system:offboarding", actor_type="system", reason="offboarding",
                               client_ip=client_ip)
        with platform_session() as db:
            row = db.get(StoreOffboarding, code.strip().upper())
            row.final_export_id = final["id"]
            db.commit()
    except PlatformError as exc:
        if exc.status != 409:
            raise
        # An export already running becomes the final one when it is ready.
        _adopt_running_export(code)
    return view(code)


def _adopt_running_export(code: str) -> None:
    from app.platform.data_models import StoreExport, StoreOffboarding
    from app.platform.db import platform_session

    with platform_session() as db:
        running = db.execute(
            select(StoreExport).where(StoreExport.store_code == code.upper(), StoreExport.status.in_(("queued", "running")))
        ).scalar_one_or_none()
        row = db.get(StoreOffboarding, code.upper())
        if running is not None and row is not None:
            row.final_export_id = running.id
            running.reason = "offboarding"
            db.commit()


def cancel(code: str, *, actor: str, restore: bool = False, client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Stop a scheduled deletion. ``restore``: the store becomes suspended
    (from archived), so a master can Resume it."""
    from app.platform import bridge, stores
    from app.platform.data_models import StoreOffboarding
    from app.platform.db import platform_session

    with platform_session() as db:
        store = stores.store_by_code(db, code)
        row = db.get(StoreOffboarding, store.code)
        if row is None or row.state != "scheduled":
            raise PlatformError(409, "conflict", "Nothing to cancel: this store is not scheduled for deletion.")
        now = utcnow()
        row.state, row.cancelled_by, row.cancelled_at, row.updated_at = "cancelled", actor, now, now
        if restore and store.status == "archived":
            store.status = "suspended"
            store.status_reason = "Deletion cancelled"
            store.maintenance_message = store.maintenance_message or stores.DEFAULT_MAINTENANCE
            store.settings_version = int(store.settings_version or 1) + 1
            store.updated_at = now
        audit("store.offboarding_cancelled", actor_type="platform_user", actor_name=actor, store_code=store.code,
              details={"restore": bool(restore), "status": store.status}, client_ip=client_ip, db=db)
        db.commit()
    if restore:
        bridge.store_changed(code.strip().upper())
    return view(code)


def _check_two_step(user, code: Optional[str]) -> None:
    """The master's current two-step code, when the master has two-step sign-in."""
    from app.platform.masters import check_step_up

    check_step_up(user, code)


def purge(code: str, *, actor: str, confirm: str, master=None, totp_code: Optional[str] = None,
          automatic: bool = False, client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Delete store data: drop the store database, delete its files and its
    credentials, keep the tombstone. Idempotent: deleting again returns the
    view. ``automatic`` (the daily job) never deletes a pilot-mode store."""
    from app.platform import bridge, stores
    from app.platform.data_models import StoreExport, StoreOffboarding
    from app.platform.db import platform_session

    actor_type = "system" if automatic else "platform_user"
    with platform_session() as db:
        store = stores.store_by_code(db, code)
        row = db.get(StoreOffboarding, store.code)
        if row is not None and row.state == "deleted":
            return view(store.code)
        if (confirm or "").strip().upper() != store.code:
            raise PlatformError(400, "invalid", f"Type the Store ID ({store.code}) to confirm.")
        if row is None or row.state != "scheduled" or store.status != "archived":
            raise PlatformError(409, "conflict", "Archive the store and schedule its deletion first.")
        if not automatic:
            _check_two_step(master, totp_code)
        if automatic and is_pilot(db, store):
            raise PlatformError(409, "pilot_mode", "A store in pilot mode is never deleted automatically.")
        if store.hosting != "shared" or store.db_host_ref:
            raise PlatformError(409, "conflict", "A dedicated store's database is deleted on its own server.")
        final = db.get(StoreExport, row.final_export_id) if row.final_export_id else None
        if final is not None and final.status in ("queued", "running"):
            raise PlatformError(409, "export_running", "The final data export is still being made.")
        if automatic and (final is None or final.status != "ready"):
            raise PlatformError(409, "no_final_export", "The final data export is not ready; delete by hand.")
        db_name = store.db_name
        audit("store.purge_started", actor_type=actor_type, actor_name=actor, store_code=store.code,
              details={"automatic": automatic, "db_name": db_name}, client_ip=client_ip, db=db)
        db.commit()

    bridge.store_stopped(code.strip().upper())
    code = code.strip().upper()
    steps: Dict[str, Any] = {}
    steps["database_dropped"] = _drop_database(db_name)
    steps["files_deleted"] = _delete_files(code)
    steps["credentials_deleted"] = _delete_credentials(code, actor=actor, actor_type=actor_type)

    with platform_session() as db:
        from app.platform.models import StoreSetting

        store = stores.store_by_code(db, code)
        res = db.execute(delete(StoreSetting).where(StoreSetting.store_id == store.id))
        steps["settings_deleted"] = int(res.rowcount or 0)
        for exp in db.execute(select(StoreExport).where(StoreExport.store_code == code,
                                                       StoreExport.status.in_(("ready", "queued", "running")))).scalars():
            exp.status, exp.link_token_hash = "expired", None
        row = db.get(StoreOffboarding, code)
        now = utcnow()
        row.state, row.purged_at, row.purged_by, row.updated_at = "deleted", now, actor, now
        store.status_reason = f"Store data deleted {now.date().isoformat()}"
        store.settings_version = int(store.settings_version or 1) + 1
        store.updated_at = now
        audit("store.data_deleted", actor_type=actor_type, actor_name=actor, store_code=code,
              details={"automatic": automatic, **steps}, client_ip=client_ip, db=db)
        db.commit()
    return view(code)


def _drop_database(db_name: str) -> bool:
    from sqlalchemy import text

    from app.platform import bridge
    from app.platform.db import make_engine
    from app.platform.provisioning import _database_exists, _quote_ident

    admin_url = bridge.admin_url()
    if not admin_url.startswith("postgresql"):
        return False
    admin = make_engine(admin_url, isolation_level="AUTOCOMMIT", pool_size=1, max_overflow=0)
    try:
        if not _database_exists(admin, db_name):
            return False
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {_quote_ident(db_name)} WITH (FORCE)"))
        return True
    finally:
        admin.dispose()


def _delete_files(code: str) -> int:
    from app.storage import get_storage

    from app.storage import StorageError

    storage = get_storage()
    try:
        n = storage.delete_prefix("", code=code)
    except StorageError:  # a backend that refuses the empty prefix: one by one
        n = sum(int(storage.delete(o.key, code=code)) for o in list(storage.list(code=code)))
    if storage.backend == "local":
        # Also the hidden files (keys, identity marker) and empty folders.
        from app.tenancy.paths import store_root, uploads_base

        root = store_root(code)
        base = uploads_base()
        if root.exists() and root.parent == base and root.name == code:
            shutil.rmtree(root, ignore_errors=True)
    return n


def _delete_credentials(code: str, *, actor: str, actor_type: str) -> List[str]:
    from app.platform import credentials as creds
    from app.platform.db import platform_session

    with platform_session() as db:
        kinds = creds.configured_kinds(code, db)
    done = []
    for kind in kinds:
        try:
            if creds.delete_credential(code, kind, actor=actor, actor_type=actor_type):
                done.append(kind)
        except Exception:  # noqa: BLE001 - an unknown kind (e.g. an old one) is removed directly
            from app.platform.models import Store, StoreCredential

            with platform_session() as db:
                store = db.execute(select(Store).where(Store.code == code)).scalar_one()
                db.execute(delete(StoreCredential).where(StoreCredential.store_id == store.id,
                                                         StoreCredential.kind == kind))
                db.commit()
            done.append(kind)
    return done


def purge_due(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The daily job: delete every store whose grace period has ended (never
    a pilot-mode store; never without a ready final export)."""
    from app.platform.data_models import StoreOffboarding
    from app.platform.db import platform_session

    now = now or utcnow()
    with platform_session() as db:
        due = [r.store_code for r in db.execute(
            select(StoreOffboarding).where(StoreOffboarding.state == "scheduled", StoreOffboarding.purge_after <= now)
        ).scalars()]
    out: Dict[str, Any] = {"deleted": [], "skipped": {}}
    for code in due:
        try:
            purge(code, actor="system:offboarding", confirm=code, automatic=True)
            out["deleted"].append(code)
        except PlatformError as exc:
            out["skipped"][code] = exc.code
        except Exception as exc:  # noqa: BLE001 - one store never stops the others
            logger.exception("automatic deletion of %s failed", code)
            out["skipped"][code] = str(exc)
    return out


def _register_jobs() -> None:
    from app import jobqueue

    @jobqueue.register("offboarding_daily", max_attempts=1, timeout_seconds=3600)
    def offboarding_daily(payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.platform.export import expire_old_exports, fail_stale

        result = purge_due()
        result["exports_expired"] = expire_old_exports()
        result["exports_failed_stale"] = fail_stale()
        return result

    # 09:00 UTC (early morning in the Americas): once a day for the platform.
    jobqueue.register_periodic("offboarding_daily", hour=9, minute=0, per_store=False)


_register_jobs()


__all__ = ("STATES", "cancel", "grace_days", "purge", "purge_due", "start", "view")
