"""Export all data: one ZIP of everything a store has (docs/WEBSITE_READY_CONTRACT.md B4; track B).

The ZIP holds
- ``tables/<table>.csv``  one CSV per store-database table, streamed with a
  server-side cursor; secrets are left out (password hashes, sessions,
  tokens, push tokens, saved credentials, secret-looking setting values)
- ``files/<key>``         every storage object of the store (``app.storage``)
- ``store.json``          profile, plan, settings without secrets, schema revision
- ``manifest.json``       tables with row counts, files, sha256 of every entry,
  and what was left out

It is written to storage key ``exports/<id>.zip`` of the store by the
``store_export`` job (I2) and tracked in the platform table
``store_exports``. One export runs at a time per store. A download link is a
signed, expiring server link (``EXPORT_LINK_HOURS``) or, on S3/R2, a
presigned URL. Every step is audited (requested, ready, failed, link,
downloaded).
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import hashlib
import io
import json
import logging
import os
import re
import secrets
import tempfile
import zipfile
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Tuple

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.platform.audit import audit
from app.platform.errors import PlatformError
from app.platform.models import utcnow

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
KEEP_DAYS = 7  # a ready export (and its file) is kept this long
EXPORT_PREFIX = "exports/"
CHUNK = 1024 * 256
STREAM_ROWS = 2000

# Tables never exported (sessions, tokens, credentials, bookkeeping).
EXCLUDED_TABLES = frozenset({
    "alembic_version", "user_sessions", "device_tokens", "platform_sessions", "store_credentials",
    "owner_setup_tokens", "support_sessions", "login_attempts",
})
_EXCLUDED_TABLE_WORDS = ("session", "token", "credential", "secret")
# Columns never exported, in any table.
_SECRET_COLUMN = re.compile(
    r"(password|passwd|secret|token|api_?key|credential|totp|private_?key|ciphertext|nonce|(^|_)hash$|_enc$)",
    re.IGNORECASE,
)
# Key/value tables: a row whose key looks secret has its value columns blanked.
_SECRET_KEY = re.compile(r"(password|secret|token|api_?key|credential|private_?key|webhook)", re.IGNORECASE)
_VALUE_COLUMNS = ("value", "previous_value", "old_value", "new_value")

STATUSES = ("queued", "running", "ready", "failed", "expired")


def table_excluded(name: str) -> bool:
    lower = name.lower()
    return lower in EXCLUDED_TABLES or any(w in lower for w in _EXCLUDED_TABLE_WORDS)


def column_excluded(name: str) -> bool:
    return bool(_SECRET_COLUMN.search(name))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(v: Optional[dt.datetime]) -> Optional[str]:
    return v.isoformat() if v else None


def _aware(v: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=dt.timezone.utc)
    return v


def link_hours() -> int:
    from app.platform.platform_settings import EXPORT_LINK_HOURS, get_platform_setting

    try:
        return max(1, min(int(get_platform_setting(EXPORT_LINK_HOURS)), 24 * 7))
    except (TypeError, ValueError):
        return 24


# --- views ---------------------------------------------------------------------------


def export_view(row) -> Dict[str, Any]:
    now = utcnow()
    expires = _aware(row.expires_at)
    status = row.status
    if status == "ready" and expires is not None and expires <= now:
        status = "expired"
    return {
        "id": row.id,
        "store_code": row.store_code,
        "status": status,
        "reason": row.reason,
        "requested_by": row.requested_by,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "tables": row.tables,
        "rows": row.rows,
        "files": row.files,
        "error": row.error,
        "downloads": row.downloads or 0,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "expires_at": _iso(row.expires_at),
        "filename": filename_of(row),
    }


def filename_of(row) -> str:
    stamp = (row.created_at or utcnow()).strftime("%Y%m%d")
    return f"{row.store_code.lower()}-data-export-{stamp}-{row.id}.zip"


def list_exports(code: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session

    with platform_session() as db:
        rows = db.execute(
            select(StoreExport).where(StoreExport.store_code == code.upper()).order_by(StoreExport.id.desc()).limit(limit)
        ).scalars()
        return [export_view(r) for r in rows]


def _get(db, code: str, export_id: int):
    from app.platform.data_models import StoreExport

    row = db.get(StoreExport, int(export_id))
    if row is None or row.store_code != code.upper():
        raise PlatformError(404, "not_found", "No such data export.")
    return row


# --- requesting --------------------------------------------------------------------------


def request_export(code: str, *, actor: str, actor_type: str, reason: str = "manual",
                   client_ip: Optional[str] = None, run: bool = True) -> Dict[str, Any]:
    """Queue an export of store ``code``. 409 while one is queued or running."""
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session
    from app.platform.stores import store_by_code

    with platform_session() as db:
        store = store_by_code(db, code)
        if store.status in ("pending", "provisioning"):
            raise PlatformError(409, "conflict", f"Store {store.code} is {store.status}.")
        off = _offboarding_state(db, store.code)
        if off == "deleted":
            raise PlatformError(409, "conflict", "This store's data has been deleted.")
        row = StoreExport(store_code=store.code, status="queued", reason=reason, requested_by=actor[:128],
                          requested_by_type=actor_type, created_at=utcnow())
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise PlatformError(409, "export_running", "A data export is already being made for this store.") from None
        audit("store.export_requested", actor_type=actor_type, actor_name=actor, store_code=store.code,
              target=str(row.id), details={"reason": reason}, client_ip=client_ip, db=db)
        db.commit()
        export_id = row.id
        view = export_view(row)
    if run:
        _enqueue(export_id, store.code)
    return view


def _offboarding_state(db, code: str) -> Optional[str]:
    from app.platform.data_models import StoreOffboarding

    row = db.get(StoreOffboarding, code)
    return row.state if row is not None else None


def _enqueue(export_id: int, code: str) -> None:
    from app import jobqueue

    try:
        jobqueue.enqueue("store_export", {"export_id": export_id}, dedupe_key=f"store_export:{code}")
    except Exception as exc:  # noqa: BLE001 - the row must not stay "queued" forever
        logger.exception("store_export %s could not be queued", export_id)
        _finish(export_id, error=f"Could not start: {exc}")


# --- the job ---------------------------------------------------------------------------------


def _finish(export_id: int, *, error: Optional[str] = None, result: Optional[Dict[str, Any]] = None) -> None:
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session

    with platform_session() as db:
        row = db.get(StoreExport, export_id)
        if row is None:
            return
        row.finished_at = utcnow()
        if error is not None:
            row.status, row.error = "failed", error[:2000]
            audit("store.export_failed", actor_type="system", store_code=row.store_code, target=str(row.id),
                  details={"error": error[:500]}, db=db)
        else:
            assert result is not None
            row.status = "ready"
            row.error = None
            for k in ("storage_key", "size_bytes", "sha256", "tables", "rows", "files"):
                setattr(row, k, result[k])
            row.expires_at = result.get("expires_at") or (utcnow() + dt.timedelta(days=KEEP_DAYS))
            audit("store.export_ready", actor_type="system", store_code=row.store_code, target=str(row.id),
                  details={"size_bytes": row.size_bytes, "tables": row.tables, "rows": row.rows, "files": row.files},
                  db=db)
        db.commit()


def run_export(export_id: int) -> Dict[str, Any]:
    """Build the ZIP of one queued export (idempotent: a ready export is
    left as it is; a running one is built again)."""
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session

    with platform_session() as db:
        row = db.get(StoreExport, int(export_id))
        if row is None:
            return {"skipped": "unknown export"}
        if row.status in ("ready", "failed", "expired"):
            return {"skipped": row.status}
        row.status, row.started_at = "running", utcnow()
        code = row.store_code
        expires_at = None
        if row.reason == "offboarding":
            from app.platform.data_models import StoreOffboarding

            off = db.get(StoreOffboarding, code)
            if off is not None and off.purge_after is not None:
                expires_at = off.purge_after  # kept for the whole grace period
        db.commit()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f"export-{code}-", suffix=".zip")
        os.close(fd)
        summary = build_zip(code, tmp)
        from app.storage import get_storage

        key = f"{EXPORT_PREFIX}{export_id}.zip"
        get_storage().put_file(key, tmp, content_type="application/zip", code=code)
        result = {"storage_key": key, **summary, "expires_at": expires_at}
        _finish(export_id, result=result)
        return {"export_id": export_id, "size_bytes": summary["size_bytes"]}
    except Exception as exc:  # noqa: BLE001 - recorded on the row; Retry makes a new one
        logger.exception("store export %s failed", export_id)
        _finish(export_id, error=str(exc) or exc.__class__.__name__)
        return {"export_id": export_id, "failed": True}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _register_job() -> None:
    from app import jobqueue

    @jobqueue.register("store_export", max_attempts=2, timeout_seconds=3600)
    def store_export_job(payload: Dict[str, Any]) -> Dict[str, Any]:
        return run_export(int(payload["export_id"]))


_register_job()


# --- building the ZIP ------------------------------------------------------------------------


class _HashingWriter(io.RawIOBase):
    """Write-through to a ZIP entry, counting bytes and hashing them."""

    def __init__(self, raw: BinaryIO):
        self.raw = raw
        self.sha = hashlib.sha256()
        self.size = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        self.sha.update(data)
        self.size += len(data)
        self.raw.write(data)
        return len(data)


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return v


def _store_row(code: str):
    from app.platform.db import platform_session
    from app.platform.stores import settings_values, store_by_code

    with platform_session() as db:
        store = store_by_code(db, code)
        settings = settings_values(db, store)
        return store, settings


def _store_url(store) -> str:
    from app.platform import bridge

    return bridge.store_url(store.db_name, store.db_host_ref)


def _table_rows(conn, table: str, columns: List[str]) -> Iterator[Tuple]:
    preparer = conn.dialect.identifier_preparer
    cols = ", ".join(preparer.quote(c) for c in columns)
    sql = text(f"SELECT {cols} FROM {preparer.quote(table)}")
    result = conn.execution_options(stream_results=True, yield_per=STREAM_ROWS).execute(sql)
    try:
        for row in result:
            yield tuple(row)
    finally:
        result.close()


def _clean_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in values.items() if not _SECRET_KEY.search(k) and not column_excluded(k)}


def build_zip(code: str, dest_path: str) -> Dict[str, Any]:
    """Write the export ZIP of store ``code`` to ``dest_path``; returns
    {size_bytes, sha256, tables, rows, files}."""
    from app.platform.db import make_engine
    from app.platform.plans import plan_of
    from app.storage import get_storage

    store, settings = _store_row(code)
    url = _store_url(store)
    eng = make_engine(url) if url.startswith("sqlite") else make_engine(url, pool_size=1, max_overflow=0)
    manifest_tables: List[Dict[str, Any]] = []
    excluded_tables: List[str] = []
    excluded_columns: Dict[str, List[str]] = {}
    total_rows = 0
    schema_revision = None
    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        try:
            with eng.connect() as conn:
                insp = inspect(conn)
                names = sorted(insp.get_table_names())
                if "alembic_version" in names:
                    schema_revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                for table in names:
                    if table_excluded(table):
                        excluded_tables.append(table)
                        continue
                    all_cols = [c["name"] for c in insp.get_columns(table)]
                    cols = [c for c in all_cols if not column_excluded(c)]
                    dropped = [c for c in all_cols if c not in cols]
                    if dropped:
                        excluded_columns[table] = dropped
                    if not cols:
                        excluded_tables.append(table)
                        continue
                    key_idx = cols.index("key") if "key" in cols else None
                    value_idx = [i for i, c in enumerate(cols) if c in _VALUE_COLUMNS] if key_idx is not None else []
                    path = f"tables/{table}.csv"
                    n = 0
                    with zf.open(path, "w", force_zip64=True) as raw:
                        hw = _HashingWriter(raw)
                        text_out = io.TextIOWrapper(hw, encoding="utf-8", newline="")
                        writer = csv.writer(text_out)
                        writer.writerow(cols)
                        for row in _table_rows(conn, table, cols):
                            cells = [_cell(v) for v in row]
                            if key_idx is not None and value_idx and _SECRET_KEY.search(str(row[key_idx] or "")):
                                for i in value_idx:
                                    cells[i] = ""
                            writer.writerow(cells)
                            n += 1
                        text_out.flush()
                        text_out.detach()
                    total_rows += n
                    manifest_tables.append({"name": table, "file": path, "rows": n, "columns": cols,
                                            "bytes": hw.size, "sha256": hw.sha.hexdigest()})
        finally:
            eng.dispose()

        storage = get_storage()
        manifest_files: List[Dict[str, Any]] = []
        for obj in storage.list("", code=store.code):
            if obj.key.startswith(EXPORT_PREFIX):
                continue  # earlier exports are not part of the data
            path = f"files/{obj.key}"
            with storage.open(obj.key, code=store.code) as src, zf.open(path, "w", force_zip64=True) as raw:
                hw = _HashingWriter(raw)
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    hw.write(chunk)
            manifest_files.append({"key": obj.key, "path": path, "bytes": hw.size, "sha256": hw.sha.hexdigest()})

        created = utcnow()
        store_json = {
            "format_version": FORMAT_VERSION,
            "exported_at": created.isoformat(),
            "store": {
                "store_id": store.code,
                "name": store.name,
                "status": store.status,
                "timezone": store.timezone,
                "pos_provider": store.pos_provider,
                "pack_id": store.pack_id,
                "hosting": store.hosting,
                "created_at": _iso(store.created_at),
                "plan": plan_of(store.code),
            },
            "settings": _clean_settings(settings),
            "schema_revision": schema_revision,
        }
        sj = json.dumps(store_json, indent=2, sort_keys=True, default=str).encode()
        zf.writestr("store.json", sj)
        manifest = {
            "format_version": FORMAT_VERSION,
            "store_id": store.code,
            "created_at": created.isoformat(),
            "schema_revision": schema_revision,
            "tables": manifest_tables,
            "files": manifest_files,
            "store_json_sha256": hashlib.sha256(sj).hexdigest(),
            "excluded": {
                "tables": sorted(excluded_tables),
                "columns": excluded_columns,
                "why": "Passwords, sessions, sign-in and push tokens and saved credentials are never exported.",
            },
            "totals": {"tables": len(manifest_tables), "rows": total_rows, "files": len(manifest_files)},
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        zf.writestr(
            "README.txt",
            "Data export of store {code}.\n\n"
            "tables/  one CSV file per table (UTF-8, first row = column names)\n"
            "files/   every file the store keeps (invoices, flyers, team media, ...)\n"
            "store.json     store profile and settings (no secrets)\n"
            "manifest.json  row counts and a SHA-256 checksum of every file\n".format(code=store.code),
        )
    sha = hashlib.sha256()
    with open(dest_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            sha.update(chunk)
    return {
        "size_bytes": os.path.getsize(dest_path),
        "sha256": sha.hexdigest(),
        "tables": len(manifest_tables),
        "rows": total_rows,
        "files": len(manifest_files),
    }


# --- download links -----------------------------------------------------------------------------


def create_link(code: str, export_id: int, *, actor: str, actor_type: str, base_url: str = "",
                client_ip: Optional[str] = None) -> Dict[str, Any]:
    """A Download link for a ready export: a presigned storage URL (S3/R2)
    or a signed server link, valid ``EXPORT_LINK_HOURS`` (never longer than
    the export is kept). Making a new link ends the previous one."""
    from app.platform.db import platform_session
    from app.storage import get_storage

    with platform_session() as db:
        row = _get(db, code, export_id)
        view = export_view(row)
        if view["status"] != "ready":
            raise PlatformError(409, "not_ready", "This data export is not ready." if view["status"] != "expired"
                                else "This data export has expired. Make a new one.")
        now = utcnow()
        expires = now + dt.timedelta(hours=link_hours())
        if row.expires_at is not None:
            expires = min(expires, _aware(row.expires_at))
        seconds = max(60, int((expires - now).total_seconds()))
        url = None
        try:
            url = get_storage().presigned_url(row.storage_key, expires_seconds=seconds, filename=filename_of(row),
                                              code=row.store_code)
        except Exception:  # noqa: BLE001 - fall back to the server link
            url = None
        if url is None:
            token = secrets.token_urlsafe(32)
            row.link_token_hash = _hash(token)
            url = f"{base_url.rstrip('/')}/api/settings/exports/{row.id}/download?token={token}"
        row.link_expires_at = expires
        audit("store.export_link_created", actor_type=actor_type, actor_name=actor, store_code=row.store_code,
              target=str(row.id), details={"expires_at": expires.isoformat()}, client_ip=client_ip, db=db)
        db.commit()
        return {"id": row.id, "url": url, "expires_at": expires.isoformat(), "filename": filename_of(row)}


def open_download(export_id: int, token: str, *, client_ip: Optional[str] = None) -> Tuple[BinaryIO, str, int]:
    """(stream, filename, size) for a signed server link; 404 for any bad,
    expired or used-up link (never says which)."""
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session
    from app.storage import ObjectNotFound, get_storage

    gone = PlatformError(404, "link_expired", "This download link has expired. Make a new one.")
    if not token or len(token) > 200:
        raise gone
    with platform_session() as db:
        row = db.get(StoreExport, int(export_id))
        now = utcnow()
        if (
            row is None
            or not row.link_token_hash
            or not secrets.compare_digest(row.link_token_hash, _hash(token))
            or row.link_expires_at is None
            or _aware(row.link_expires_at) <= now
            or export_view(row)["status"] != "ready"
        ):
            raise gone
        try:
            stream = get_storage().open(row.storage_key, code=row.store_code)
        except ObjectNotFound:
            raise gone from None
        row.downloads = int(row.downloads or 0) + 1
        audit("store.export_downloaded", actor_type="system", actor_name="download link", store_code=row.store_code,
              target=str(row.id), details={"downloads": row.downloads}, client_ip=client_ip, db=db)
        db.commit()
        return stream, filename_of(row), int(row.size_bytes or 0)


# --- clean-up ---------------------------------------------------------------------------------


def expire_old_exports(now: Optional[dt.datetime] = None) -> int:
    """Delete the files of exports past their keep date (daily job)."""
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session
    from app.storage import get_storage

    now = now or utcnow()
    n = 0
    with platform_session() as db:
        rows = list(db.execute(select(StoreExport).where(StoreExport.status == "ready",
                                                         StoreExport.expires_at <= now)).scalars())
        for row in rows:
            try:
                if row.storage_key:
                    get_storage().delete(row.storage_key, code=row.store_code)
            except Exception:  # noqa: BLE001 - try again tomorrow
                logger.warning("export file %s not deleted", row.storage_key, exc_info=True)
                continue
            row.status, row.link_token_hash = "expired", None
            n += 1
        db.commit()
    return n


def fail_stale(now: Optional[dt.datetime] = None, *, hours: int = 6) -> int:
    """Exports stuck in queued/running for hours (the process died): failed,
    so the store can try again."""
    from app.platform.data_models import StoreExport
    from app.platform.db import platform_session

    now = now or utcnow()
    cutoff = now - dt.timedelta(hours=hours)
    with platform_session() as db:
        rows = list(db.execute(select(StoreExport).where(StoreExport.status.in_(("queued", "running")),
                                                         StoreExport.created_at <= cutoff)).scalars())
        for row in rows:
            row.status, row.error, row.finished_at = "failed", "Stopped before it finished. Try again.", now
        db.commit()
    return len(rows)


__all__ = (
    "EXCLUDED_TABLES", "KEEP_DAYS", "build_zip", "column_excluded", "create_link", "expire_old_exports",
    "export_view", "fail_stale", "list_exports", "open_download", "request_export", "run_export", "table_excluded",
)
