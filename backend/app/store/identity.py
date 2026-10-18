"""The store identity stamp (StoreOps M2, docs/M2_CONTRACT.md 1.5; plan 9.1).

Each store's database holds one ``store_identity`` row (its store code and a
random instance id), and its upload folder holds a matching
``<UPLOADS_ROOT>/.store_identity`` marker. On startup, before any scheduled
job runs, ``check_store_identity`` compares them with ``STORE_CODE`` and
refuses to start on a mismatch, so a copy-paste mistake in one store's .env
can't point its server at another store's data or files.

| STORE_CODE | DB stamp   | Result                                                    |
|------------|------------|-----------------------------------------------------------|
| blank      | none       | start, warn "identity check skipped" (dev, tests)         |
| blank      | present    | refuse                                                    |
| set        | none       | refuse, unless STORE_IDENTITY_ADOPT=true (stamp "adopt")  |
| set        | same code  | check the uploads marker, start                           |
| set        | other code | refuse                                                    |

Uploads marker (only when STORE_CODE is set): missing and the folder is
missing or empty -> written; missing and the folder has files -> refuse
unless adopting; code or instance id different from the stamp -> refuse.
A refusal raises StoreIdentityError before anything is written.
"""

import datetime as dt
import json
import logging
import pathlib
import uuid
from typing import Optional

from sqlalchemy import CheckConstraint, Integer, String, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import get_settings
from app.db import Base, UTCDateTime

logger = logging.getLogger(__name__)

MARKER_NAME = ".store_identity"


class StoreIdentity(Base):
    """One row: which store this database belongs to (migration 0062)."""

    __tablename__ = "store_identity"
    __table_args__ = (CheckConstraint("id = 1", name="ck_store_identity_one_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_code: Mapped[str] = mapped_column(String)
    instance_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(String)


# The server must not start (or the command must not run). Defined in
# app.tenancy.errors so the engine registry can raise it too (platform
# milestone: the check runs per store, on the first open of its engine).
from app.tenancy.errors import StoreIdentityError  # noqa: E402,F401


def read_stamp(db: Session) -> Optional[dict]:
    """The stamp, or None (no row, or the table isn't there yet)."""
    try:
        row = db.execute(select(StoreIdentity).where(StoreIdentity.id == 1)).scalar_one_or_none()
    except (OperationalError, ProgrammingError):
        db.rollback()
        return None
    if row is None:
        return None
    return {
        "store_code": row.store_code,
        "instance_id": row.instance_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by,
    }


def stamp_database(db: Session, store_code: str, created_by: str, commit: bool = True) -> str:
    """Write the stamp (once). Returns the instance id; the same code again
    returns the existing one. A different code is refused."""
    code = (store_code or "").strip().upper()
    if not code:
        raise StoreIdentityError("A store code is needed to stamp the database.")
    existing = read_stamp(db)
    if existing is not None:
        if existing["store_code"] != code:
            raise StoreIdentityError(f"This database belongs to {existing['store_code']}, not {code}.")
        return existing["instance_id"]
    instance_id = uuid.uuid4().hex
    db.add(
        StoreIdentity(
            id=1,
            store_code=code,
            instance_id=instance_id,
            created_at=dt.datetime.now(dt.timezone.utc),
            created_by=created_by,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return instance_id


def uploads_root(settings=None) -> pathlib.Path:
    s = settings if settings is not None else get_settings()
    raw = (getattr(s, "uploads_root", "") or "uploads").strip() or "uploads"
    return pathlib.Path(raw).expanduser().resolve()


def read_marker(root) -> Optional[dict]:
    path = pathlib.Path(root) / MARKER_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StoreIdentityError(f"{path} is not readable JSON - fix or remove it by hand.") from None
    if not isinstance(data, dict):
        raise StoreIdentityError(f"{path} is not a store identity marker.")
    return data


def write_uploads_marker(root, store_code: str, instance_id: str) -> None:
    folder = pathlib.Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / MARKER_NAME
    tmp = folder / (MARKER_NAME + ".tmp")
    tmp.write_text(json.dumps({"store_code": store_code, "instance_id": instance_id}) + "\n", encoding="utf-8")
    tmp.replace(path)


def _has_files(folder: pathlib.Path) -> bool:
    if not folder.is_dir():
        return False
    return any(p.name != MARKER_NAME for p in folder.iterdir())


def check_store_marker(root, store_code: str, instance_id: str) -> None:
    """Multi mode, first open of a store's engine (app/tenancy/engines.py):
    the store's upload folder marker must match its database stamp. Missing
    marker and an empty / missing folder -> written; missing marker and
    files -> refused; another code or instance -> refused."""
    root = pathlib.Path(root)
    marker = read_marker(root)
    if marker is None:
        if _has_files(root):
            raise StoreIdentityError(
                f"The upload folder {root} has files but no store identity marker (store {store_code})."
            )
        write_uploads_marker(root, store_code, instance_id)
        return
    if marker.get("store_code") != store_code or marker.get("instance_id") != instance_id:
        raise StoreIdentityError(
            f"The upload folder {root} belongs to {marker.get('store_code')} "
            f"(instance {marker.get('instance_id')}), not {store_code} (instance {instance_id})."
        )


def check_store_identity(settings=None, db: Optional[Session] = None) -> None:
    """The startup check (table above). Raises StoreIdentityError."""
    s = settings if settings is not None else get_settings()
    code = (getattr(s, "store_code", "") or "").strip().upper()
    adopt = bool(getattr(s, "store_identity_adopt", False))
    own = db is None
    if own:
        from app.db import SessionLocal

        db = SessionLocal()
    try:
        stamp = read_stamp(db)
        if not code:
            if stamp is not None:
                raise StoreIdentityError(
                    f"STORE_CODE is blank but this database belongs to {stamp['store_code']}. "
                    f"Set STORE_CODE={stamp['store_code']} in this store's .env."
                )
            logger.warning("STORE_CODE is blank: store identity check skipped (dev / tests only)")
            return
        root = uploads_root(s)
        marker = read_marker(root)
        if stamp is None:
            if not adopt:
                raise StoreIdentityError(
                    f"This database has no store identity yet (STORE_CODE={code}). Set the store up with "
                    f"`python -m app.store init --code {code} --pack <pack.json>`, or set "
                    "STORE_IDENTITY_ADOPT=true once to stamp an existing store's database."
                )
            if marker is not None and marker.get("store_code") != code:
                raise StoreIdentityError(
                    f"The upload folder {root} belongs to {marker.get('store_code')}, not {code}."
                )
            instance_id = stamp_database(db, code, "adopt")
            write_uploads_marker(root, code, instance_id)
            logger.warning("Store identity adopted: %s (instance %s)", code, instance_id)
            return
        if stamp["store_code"] != code:
            raise StoreIdentityError(f"This database belongs to {stamp['store_code']}, not {code}.")
        if marker is None:
            if _has_files(root) and not adopt:
                raise StoreIdentityError(
                    f"The upload folder {root} has files but no store identity marker. If it is this "
                    f"store's ({code}) folder, set STORE_IDENTITY_ADOPT=true once."
                )
            write_uploads_marker(root, code, stamp["instance_id"])
            return
        if marker.get("store_code") != code or marker.get("instance_id") != stamp["instance_id"]:
            raise StoreIdentityError(
                f"The upload folder {root} belongs to {marker.get('store_code')} "
                f"(instance {marker.get('instance_id')}), not this database's {code} "
                f"(instance {stamp['instance_id']})."
            )
    finally:
        if own:
            db.close()
