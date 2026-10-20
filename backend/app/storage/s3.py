"""S3-compatible object storage (Cloudflare R2, AWS S3, MinIO) for app.storage (I1, C1).

Selected with ``STORAGE_BACKEND=s3`` and configured with ``STORAGE_S3_*``
(``app/platform/config.py``): endpoint, bucket, access key id, secret access
key, region (``auto`` for R2) and a key prefix. A store's key ``k`` is the
object ``<STORAGE_S3_PREFIX>/<STORE_CODE>/k``, so one bucket holds every
store and ``delete_prefix`` / ``bytes_used`` stay per store.

Each object carries its SHA-256 in the ``sha256`` metadata field (the
migration tool's ``--verify`` compares it with the local file). Downloads
use presigned GET links with a ``Content-Disposition`` file name
(``STORAGE_PRESIGN=false`` makes the server stream instead).

boto3 is imported lazily: a server on the local backend never loads it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import pathlib
import threading
from typing import Any, BinaryIO, Dict, Iterator, Optional

from app.storage import ObjectNotFound, StorageError, StoredObject, check_key

CHUNK = 1024 * 1024


class StorageConfigError(StorageError):
    """STORAGE_BACKEND / STORAGE_S3_* are incomplete or invalid."""


def s3_settings() -> Dict[str, Any]:
    from app.platform.config import pget

    return {
        "endpoint": str(pget("storage_s3_endpoint") or "").strip(),
        "bucket": str(pget("storage_s3_bucket") or "").strip(),
        "access_key_id": str(pget("storage_s3_access_key_id") or "").strip(),
        "secret_access_key": str(pget("storage_s3_secret_access_key") or "").strip(),
        "region": str(pget("storage_s3_region") or "auto").strip() or "auto",
        "prefix": str(pget("storage_s3_prefix") or "stores").strip().strip("/") or "stores",
        "presign": bool(pget("storage_presign")),
    }


def missing_settings(cfg: Optional[Dict[str, Any]] = None) -> list:
    cfg = cfg or s3_settings()
    names = {
        "endpoint": "STORAGE_S3_ENDPOINT",
        "bucket": "STORAGE_S3_BUCKET",
        "access_key_id": "STORAGE_S3_ACCESS_KEY_ID",
        "secret_access_key": "STORAGE_S3_SECRET_ACCESS_KEY",
    }
    return [env for key, env in names.items() if not cfg.get(key)]


def sha256_file(path: "pathlib.Path | str") -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _store_code(code: Optional[str]) -> str:
    from app.tenancy.errors import NoStoreContext
    from app.tenancy.mode import is_multi_tenant
    from app.tenancy.registry import is_valid_store_code, normalize_store_code, single_store_code

    if code is None:
        if not is_multi_tenant():
            return single_store_code()
        from app.tenancy.context import current_store_code

        code = current_store_code()
        if code is None:
            raise NoStoreContext()
    norm = normalize_store_code(code)
    if is_multi_tenant() and not is_valid_store_code(norm):
        raise StorageError(f"not a store code: {code!r}")
    return norm


class _Reader(io.RawIOBase):
    """A file-like reader over an S3 GetObject body (closes the HTTP stream)."""

    def __init__(self, body):
        self._body = body

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self._body.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    def close(self) -> None:
        try:
            self._body.close()
        finally:
            super().close()


class S3Storage:
    backend = "s3"

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, client: Any = None):
        self.cfg = cfg or s3_settings()
        missing = missing_settings(self.cfg)
        if missing:
            raise StorageConfigError("STORAGE_BACKEND=s3 needs " + ", ".join(missing))
        self.bucket = self.cfg["bucket"]
        self.prefix = self.cfg["prefix"]
        self._client = client
        self._lock = threading.Lock()

    # --- plumbing -----------------------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import boto3
                    from botocore.config import Config

                    self._client = boto3.client(
                        "s3",
                        endpoint_url=self.cfg["endpoint"],
                        aws_access_key_id=self.cfg["access_key_id"],
                        aws_secret_access_key=self.cfg["secret_access_key"],
                        region_name=self.cfg["region"],
                        config=Config(
                            signature_version="s3v4",
                            retries={"max_attempts": 5, "mode": "standard"},
                            s3={"addressing_style": "path"},
                            connect_timeout=10,
                            read_timeout=60,
                        ),
                    )
        return self._client

    def store_prefix(self, code: Optional[str]) -> str:
        return f"{self.prefix}/{_store_code(code)}/"

    def object_key(self, key: str, code: Optional[str] = None) -> str:
        return self.store_prefix(code) + check_key(key)

    @staticmethod
    def _missing(exc: Exception) -> bool:
        resp = getattr(exc, "response", None) or {}
        code = str((resp.get("Error") or {}).get("Code") or "")
        status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return code in ("404", "NoSuchKey", "NotFound") or status == 404

    def _wrap(self, exc: Exception, key: str) -> StorageError:
        if self._missing(exc):
            return ObjectNotFound(key)
        resp = getattr(exc, "response", None) or {}
        code = (resp.get("Error") or {}).get("Code") or type(exc).__name__
        return StorageError(f"object storage: {code}")

    # --- I1 -------------------------------------------------------------------------------------

    def put_bytes(self, key, data, *, content_type=None, code=None) -> StoredObject:
        okey = self.object_key(key, code)
        extra: Dict[str, Any] = {"Metadata": {"sha256": hashlib.sha256(data).hexdigest()}}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self.client.put_object(Bucket=self.bucket, Key=okey, Body=data, **extra)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, key) from exc
        return StoredObject(check_key(key), len(data), dt.datetime.now(dt.timezone.utc), content_type)

    def put_file(self, key, src, *, content_type=None, code=None) -> StoredObject:
        okey = self.object_key(key, code)
        extra: Dict[str, Any] = {"Metadata": {"sha256": sha256_file(src)}}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self.client.upload_file(str(src), self.bucket, okey, ExtraArgs=extra)  # multipart when large
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, key) from exc
        return StoredObject(check_key(key), pathlib.Path(src).stat().st_size, dt.datetime.now(dt.timezone.utc),
                            content_type)

    def open(self, key, *, code=None) -> BinaryIO:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self.object_key(key, code))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, key) from exc
        return io.BufferedReader(_Reader(resp["Body"]), buffer_size=CHUNK)  # type: ignore[return-value]

    def read_bytes(self, key, *, code=None) -> bytes:
        with self.open(key, code=code) as fh:
            return fh.read()

    def head(self, key, *, code=None) -> Optional[Dict[str, Any]]:
        """The raw HEAD answer (size, metadata) or None."""
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self.object_key(key, code))
        except Exception as exc:  # noqa: BLE001
            if self._missing(exc):
                return None
            raise self._wrap(exc, key) from exc

    def exists(self, key, *, code=None) -> bool:
        return self.head(key, code=code) is not None

    def stat(self, key, *, code=None) -> Optional[StoredObject]:
        h = self.head(key, code=code)
        if h is None:
            return None
        return StoredObject(check_key(key), int(h.get("ContentLength") or 0), h.get("LastModified"),
                            h.get("ContentType"))

    def delete(self, key, *, code=None) -> bool:
        if not self.exists(key, code=code):
            return False
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.object_key(key, code))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, key) from exc
        return True

    def _iter(self, prefix: str, code: Optional[str]):
        base = self.store_prefix(code)
        full = base + (check_key(prefix).rstrip("/") + "/" if prefix else "")
        paginator = self.client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full):
                for item in page.get("Contents") or []:
                    yield base, item
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, prefix or "/") from exc

    def delete_prefix(self, prefix, *, code=None) -> int:
        batch, n = [], 0
        for _base, item in self._iter(prefix, code):
            batch.append({"Key": item["Key"]})
            if len(batch) == 1000:
                n += self._delete_batch(batch)
                batch = []
        if batch:
            n += self._delete_batch(batch)
        return n

    def _delete_batch(self, batch) -> int:
        try:
            resp = self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "/") from exc
        errors = resp.get("Errors") or []
        if errors:
            raise StorageError(f"object storage: {len(errors)} object(s) not deleted")
        return len(batch)

    def list(self, prefix="", *, code=None) -> Iterator[StoredObject]:
        for base, item in self._iter(prefix, code):
            rel = item["Key"][len(base):]
            if not rel or rel.startswith(".") or "/." in rel:
                continue
            yield StoredObject(rel, int(item.get("Size") or 0), item.get("LastModified"))

    def bytes_used(self, prefix="", *, code=None) -> int:
        return sum(o.size for o in self.list(prefix, code=code))

    def presigned_url(self, key, *, expires_seconds=300, filename=None, code=None) -> Optional[str]:
        if not self.cfg.get("presign", True):
            return None
        params: Dict[str, Any] = {"Bucket": self.bucket, "Key": self.object_key(key, code)}
        if filename:
            safe = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in str(filename)) or "download"
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        try:
            return self.client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=max(1, min(int(expires_seconds), 7 * 24 * 3600))
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, key) from exc

    def local_path(self, key, *, code=None) -> Optional[pathlib.Path]:
        return None
