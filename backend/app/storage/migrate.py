"""Copy store files from the local folders to S3 / R2 (docs/WEBSITE_READY_CONTRACT.md C1).

    python -m app.storage migrate --to s3 [--store CODE] [--dry-run] [--verify]

- Reads every object of each store with the LOCAL backend (today's folders;
  hidden files such as ``.keys/`` and the identity marker are never copied)
  and writes it to the S3 backend from ``STORAGE_S3_*`` - whatever
  ``STORAGE_BACKEND`` says, so it runs while the server still uses local.
- Idempotent and resumable: an object already in the bucket with the same
  size (and, with ``--verify``, the same SHA-256) is skipped, so an
  interrupted run is simply started again.
- ``--dry-run``: writes nothing; reports what would be copied.
- ``--verify``: compares size and SHA-256 of every object after copying
  (with ``--dry-run``: checks only, e.g. before switching
  ``STORAGE_BACKEND=s3``).
- Local files are never changed or deleted: after the switch the old
  folders are a backup until the owner removes them.

Exit code 0 when every object is in the bucket and matches; 1 otherwise.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from app.storage import LocalStorage, StorageError
from app.storage.s3 import S3Storage, sha256_file

logger = logging.getLogger(__name__)


def store_codes(only: Optional[str] = None) -> List[str]:
    from app.tenancy.mode import is_multi_tenant
    from app.tenancy.registry import list_stores, normalize_store_code, single_store_code

    if only:
        return [normalize_store_code(only)]
    if not is_multi_tenant():
        return [single_store_code()]
    statuses = ("active", "suspended", "archived", "provisioning", "pending")
    return sorted({r.code for r in list_stores(statuses, None)})


def migrate_store(code: str, dest: S3Storage, *, dry_run: bool = False, verify: bool = False,
                  src: Optional[LocalStorage] = None) -> Dict[str, Any]:
    src = src or LocalStorage()
    local_code = None if _single() else code
    out: Dict[str, Any] = {"store": code, "objects": 0, "bytes": 0, "copied": 0, "skipped": 0,
                           "would_copy": 0, "verified": 0, "mismatched": [], "failed": []}
    for obj in src.list(code=local_code):
        out["objects"] += 1
        out["bytes"] += obj.size
        path = src.local_path(obj.key, code=local_code)
        try:
            head = dest.head(obj.key, code=code)
            local_sum = sha256_file(path) if (verify or head is not None) else None
            same = (
                head is not None
                and int(head.get("ContentLength") or -1) == obj.size
                and ((head.get("Metadata") or {}).get("sha256") == local_sum)
            )
            if same:
                out["skipped"] += 1
            elif dry_run:
                out["would_copy"] += 1
                if verify:
                    out["mismatched"].append(obj.key)
                continue
            else:
                dest.put_file(obj.key, path, code=code)
                out["copied"] += 1
            if verify:
                head = dest.head(obj.key, code=code)
                ok = (
                    head is not None
                    and int(head.get("ContentLength") or -1) == obj.size
                    and (head.get("Metadata") or {}).get("sha256") == (local_sum or sha256_file(path))
                )
                if ok:
                    out["verified"] += 1
                else:
                    out["mismatched"].append(obj.key)
        except (StorageError, OSError) as exc:
            logger.error("Store %s: %s not copied (%s)", code, obj.key, exc)
            out["failed"].append({"key": obj.key, "error": str(exc)[:200]})
    return out


def _single() -> bool:
    from app.tenancy.mode import is_multi_tenant

    return not is_multi_tenant()


def migrate(codes: Iterable[str], *, dry_run: bool = False, verify: bool = False,
            dest: Optional[S3Storage] = None) -> Dict[str, Any]:
    dest = dest or S3Storage()
    stores = [migrate_store(c, dest, dry_run=dry_run, verify=verify) for c in codes]
    ok = all(not s["failed"] and not s["mismatched"] for s in stores)
    return {
        "to": "s3",
        "bucket": dest.bucket,
        "dry_run": dry_run,
        "verify": verify,
        "ok": ok,
        "stores": stores,
        "totals": {k: sum(s[k] for s in stores) for k in ("objects", "bytes", "copied", "skipped", "would_copy",
                                                          "verified")},
    }


def main_migrate(args) -> int:
    if args.to != "s3":
        print("Only --to s3 is supported.")
        return 2
    report = migrate(store_codes(args.store), dry_run=args.dry_run, verify=args.verify)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1
