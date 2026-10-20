"""Object storage for store files (docs/WEBSITE_READY_CONTRACT.md, interface I1).

INTERFACE STUB landed with the website-ready contract. Track C owns this
package and replaces the bodies (S3-compatible backend for Cloudflare R2,
presigned downloads, the local -> S3 migration tool); the names and
signatures below are the contract that tracks B (store export) and D build
against, and they do not change without a contract update.

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


def get_storage() -> ObjectStorage:
    """The storage backend of this process (stub: always local; track C
    adds the S3/R2 backend behind STORAGE_BACKEND=s3)."""
    return LocalStorage()


__all__ = (
    "LocalStorage",
    "ObjectNotFound",
    "ObjectStorage",
    "StorageError",
    "StoredObject",
    "check_key",
    "get_storage",
)
