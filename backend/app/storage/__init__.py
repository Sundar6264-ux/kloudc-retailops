"""Object storage for store files (docs/WEBSITE_READY_CONTRACT.md, interface I1).

Two backends behind one interface (``ObjectStorage``):

- ``local`` (the default, ``STORAGE_BACKEND=local``): files under the
  store's folder, exactly today's layout (``LocalStorage`` below).
- ``s3`` (``STORAGE_BACKEND=s3``): an S3-compatible bucket - Cloudflare R2,
  AWS S3, MinIO - configured with ``STORAGE_S3_*`` (``app/storage/s3.py``).
  Refused at start-up unless endpoint, bucket and both keys are set
  (``check_storage_config``).

Moving existing files: ``python -m app.storage migrate --to s3 [--store CODE]
[--dry-run] [--verify]`` (``app/storage/migrate.py``; copy, verify size and
SHA-256, idempotent and resumable). The names and signatures below are the
contract tracks B (store export) and D build against.

A *key* is a relative, "/"-separated path inside one store's area, e.g.
``invoices/2026/10/abc.pdf`` or ``exports/42.zip``. Every call works on the
current store (``app.tenancy`` context) unless ``code=`` names one. The local
backend keeps today's layout: key ``invoices/x.pdf`` of store ``ABC`` is
``<UPLOADS_ROOT>/ABC/invoices/x.pdf`` in multi mode (``app.tenancy.paths``),
``<UPLOADS_ROOT>/invoices/x.pdf`` in single mode, so nothing moves on disk.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import shutil
import tempfile
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional, Protocol

from app.tenancy.paths import store_path, store_root


class StorageError(RuntimeError):
    """A storage call failed (missing object, refused key, backend down)."""


class ObjectNotFound(StorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    modified_at: Optional[dt.datetime] = None
    content_type: Optional[str] = None


class ObjectStorage(Protocol):
    backend: str  # "local" | "s3"

    def put_bytes(self, key: str, data: bytes, *, content_type: Optional[str] = None,
                  code: Optional[str] = None) -> StoredObject: ...

    def put_file(self, key: str, src: "os.PathLike[str] | str", *, content_type: Optional[str] = None,
                 code: Optional[str] = None) -> StoredObject: ...

    def open(self, key: str, *, code: Optional[str] = None) -> BinaryIO: ...  # binary read; caller closes

    def read_bytes(self, key: str, *, code: Optional[str] = None) -> bytes: ...

    def exists(self, key: str, *, code: Optional[str] = None) -> bool: ...

    def stat(self, key: str, *, code: Optional[str] = None) -> Optional[StoredObject]: ...

    def delete(self, key: str, *, code: Optional[str] = None) -> bool: ...

    def delete_prefix(self, prefix: str, *, code: Optional[str] = None) -> int: ...

    def list(self, prefix: str = "", *, code: Optional[str] = None) -> Iterator[StoredObject]: ...

    def bytes_used(self, prefix: str = "", *, code: Optional[str] = None) -> int: ...

    # A time-limited direct download URL (S3/R2), or None when the server must
    # stream the object itself (local backend, or presigning switched off).
    def presigned_url(self, key: str, *, expires_seconds: int = 300, filename: Optional[str] = None,
                      code: Optional[str] = None) -> Optional[str]: ...

    # The file on this machine (local backend only; None on S3).
    def local_path(self, key: str, *, code: Optional[str] = None) -> Optional[pathlib.Path]: ...


def check_key(key: str) -> str:
    """A key is relative, "/"-separated, without "..", "\\" or empty parts."""
    raw = str(key or "").strip()
    parts = raw.split("/")
    if not raw or raw.startswith("/") or "\\" in raw or any(p in ("", ".", "..") for p in parts):
        raise StorageError(f"storage key {key!r} is not allowed")
    return raw


class LocalStorage:
    """Files under the store's folder (today's behaviour)."""

    backend = "local"

    def _path(self, key: str, code: Optional[str]) -> pathlib.Path:
        return store_path(*check_key(key).split("/"), code=code)

    def put_bytes(self, key, data, *, content_type=None, code=None) -> StoredObject:
        path = self._path(key, code)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise
        return self.stat(key, code=code)  # type: ignore[return-value]

    def put_file(self, key, src, *, content_type=None, code=None) -> StoredObject:
        path = self._path(key, code)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, path)
        except BaseException:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise
        return self.stat(key, code=code)  # type: ignore[return-value]

    def open(self, key, *, code=None) -> BinaryIO:
        path = self._path(key, code)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.open("rb")

    def read_bytes(self, key, *, code=None) -> bytes:
        with self.open(key, code=code) as fh:
            return fh.read()

    def exists(self, key, *, code=None) -> bool:
        return self._path(key, code).is_file()

    def stat(self, key, *, code=None) -> Optional[StoredObject]:
        path = self._path(key, code)
        if not path.is_file():
            return None
        st = path.stat()
        return StoredObject(
            key=check_key(key),
            size=st.st_size,
            modified_at=dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc),
        )

    def delete(self, key, *, code=None) -> bool:
        path = self._path(key, code)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def delete_prefix(self, prefix, *, code=None) -> int:
        n = 0
        for obj in list(self.list(prefix, code=code)):
            n += int(self.delete(obj.key, code=code))
        return n

    def list(self, prefix="", *, code=None) -> Iterator[StoredObject]:
        root = store_root(code)
        base = root / check_key(prefix) if prefix else root
        if not base.exists():
            return
        for path in sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink()):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue  # hidden files (.keys/, identity marker, temp files) are never objects
            st = path.stat()
            yield StoredObject(rel, st.st_size, dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc))

    def bytes_used(self, prefix="", *, code=None) -> int:
        return sum(o.size for o in self.list(prefix, code=code))

    def presigned_url(self, key, *, expires_seconds=300, filename=None, code=None) -> Optional[str]:
        return None

    def local_path(self, key, *, code=None) -> Optional[pathlib.Path]:
        return self._path(key, code)


_backend_cache: dict = {"key": None, "storage": None}


def backend_name() -> str:
    from app.platform.config import pget

    return str(pget("storage_backend") or "local").strip().lower() or "local"


def check_storage_config() -> None:
    """Start-up check (lifespan): STORAGE_BACKEND is "local" or "s3", and
    s3 has its endpoint, bucket and keys. Raises StorageError otherwise."""
    name = backend_name()
    if name == "local":
        return
    if name != "s3":
        raise StorageError(f"STORAGE_BACKEND={name!r} is not one of: local, s3")
    from app.storage.s3 import StorageConfigError, missing_settings

    missing = missing_settings()
    if missing:
        raise StorageConfigError("STORAGE_BACKEND=s3 needs " + ", ".join(missing))


def get_storage() -> ObjectStorage:
    """The storage backend of this process (STORAGE_BACKEND, default local).
    Built once per configuration; cheap to call."""
    name = backend_name()
    if name != "s3":
        return LocalStorage()
    from app.storage.s3 import S3Storage, s3_settings

    cfg = s3_settings()
    key = tuple(sorted(cfg.items()))
    if _backend_cache["key"] != key:
        _backend_cache["storage"] = S3Storage(cfg)
        _backend_cache["key"] = key
    return _backend_cache["storage"]


def storage_status() -> dict:
    """For the masters' System panel: backend, bucket and endpoint host
    (never the keys), and whether the configuration is complete."""
    from urllib.parse import urlparse

    name = backend_name()
    out = {"backend": name, "ok": True, "problem": None, "bucket": None, "endpoint_host": None,
           "presign": False, "keys_set": False}
    if name == "local":
        return out
    from app.storage.s3 import missing_settings, s3_settings

    cfg = s3_settings()
    missing = missing_settings(cfg) if name == "s3" else []
    out.update(
        bucket=cfg["bucket"] or None,
        endpoint_host=(urlparse(cfg["endpoint"]).hostname if cfg["endpoint"] else None),
        presign=bool(cfg["presign"]),
        keys_set=bool(cfg["access_key_id"] and cfg["secret_access_key"]),
    )
    if name != "s3":
        out.update(ok=False, problem=f"Unknown STORAGE_BACKEND {name!r}")
    elif missing:
        out.update(ok=False, problem="Missing " + ", ".join(missing))
    return out


__all__ = (
    "LocalStorage",
    "ObjectNotFound",
    "ObjectStorage",
    "StorageError",
    "StoredObject",
    "backend_name",
    "check_key",
    "check_storage_config",
    "get_storage",
    "storage_status",
)
