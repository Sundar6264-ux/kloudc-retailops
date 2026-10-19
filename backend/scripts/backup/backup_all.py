#!/usr/bin/env python3
"""Nightly backup of the WHOLE shared service: the platform database and
every store database on the shared cluster, each with pg_backup.py (pg_dump
custom format, checked with pg_restore --list, kept --keep-days days, never
the newest), plus a monthly restore drill of the newest dumps.

    python3 backup_all.py backup --platform-url postgresql://retailops@127.0.0.1:5434/so_platform \\
        --dest ~/kloudc-retailops/backups [--keep-days 30] [--pg-bin /opt/homebrew/opt/postgresql@16/bin]
    python3 backup_all.py drill  --platform-url ... --dest ~/kloudc-retailops/backups [--store CODE]

Layout under --dest: ``_platform/PLATFORM-<stamp>.dump`` and
``<CODE>/<CODE>-<stamp>.dump`` per store, each folder with its own
backup.log / last_backup.json; ``backup_all.log`` (one JSON line per run) and
``last_backup_all.json`` at the top. Exit 0 only when every database was
backed up (or drilled) OK - one store failing never stops the others.

Which stores: the registry's active and suspended stores on the shared
cluster (``stores`` table: hosting 'shared', no dedicated host). Each store
database URL is the platform URL with the database name replaced, unless
--store-url-template (containing {db_name}) says otherwise.

Standard library only, like pg_backup.py (needs psql / pg_dump / pg_restore
of the server's major version). Passwords: ~/.pgpass or PGPASSWORD, never
in a URL. NOT backed up here: the master key ring (RETAILOPS_SECRET_KEYS) -
keep it offline; without it the saved store passwords and keys in these
dumps can't be read.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pg_backup  # noqa: E402

PLATFORM_FOLDER = "_platform"
PLATFORM_NAME = "PLATFORM"
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,23}$")
DB_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
STORES_SQL = (
    "SELECT code, db_name FROM stores WHERE status IN ('active', 'suspended') "
    "AND hosting = 'shared' AND db_host_ref IS NULL ORDER BY code"
)


def list_stores(platform_url: str, pg_bin: Optional[str]) -> List[Tuple[str, str]]:
    out = []
    for line in pg_backup._psql(pg_bin, pg_backup.libpq_url(platform_url), STORES_SQL).splitlines():  # noqa: SLF001
        if not line.strip():
            continue
        code, db_name = line.split("|", 1)
        code, db_name = code.strip(), db_name.strip()
        if not CODE_RE.match(code) or not DB_RE.match(db_name):
            raise pg_backup.BackupError(f"unexpected registry row {line!r}")
        out.append((code, db_name))
    return out


def store_url(platform_url: str, db_name: str, template: Optional[str]) -> str:
    if template:
        if "{db_name}" not in template:
            raise pg_backup.BackupError("--store-url-template must contain {db_name}")
        return template.replace("{db_name}", db_name)
    return pg_backup.with_db(pg_backup.libpq_url(platform_url), db_name)


def _record(dest: pathlib.Path, summary: Dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "backup_all.log", "a") as fh:
        fh.write(json.dumps(summary, sort_keys=True) + "\n")
    name = "last_backup_all.json" if summary["command"] == "backup" else "last_drill.json"
    (dest / name).write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")


def backup_all(platform_url: str, dest: str, *, keep_days: int = pg_backup.DEFAULT_KEEP_DAYS,
               pg_bin: Optional[str] = None, store_url_template: Optional[str] = None,
               now: Optional[dt.datetime] = None) -> Dict:
    started = time.monotonic()
    now = now or dt.datetime.now()
    root = pathlib.Path(dest).expanduser().resolve()
    results: Dict[str, Dict] = {}

    def one(label: str, url: str, folder: str, name: str) -> None:
        try:
            results[label] = pg_backup.backup(url, str(root / folder), name=name, keep_days=keep_days,
                                              pg_bin=pg_bin, now=now)
        except Exception as exc:  # noqa: BLE001 - one failure never stops the others
            results[label] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}

    one(PLATFORM_NAME, platform_url, PLATFORM_FOLDER, PLATFORM_NAME)
    try:
        stores = list_stores(platform_url, pg_bin)
    except Exception as exc:  # noqa: BLE001
        stores = []
        results["_registry"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    for code, db_name in stores:
        try:
            url = store_url(platform_url, db_name, store_url_template)
        except Exception as exc:  # noqa: BLE001
            results[code] = {"ok": False, "error": str(exc)}
            continue
        one(code, url, code, code)
    summary = {
        "command": "backup",
        "at": now.isoformat(timespec="seconds"),
        "ok": all(r.get("ok") for r in results.values()),
        "databases": len(results) - (1 if "_registry" in results else 0),
        "failed": sorted(k for k, r in results.items() if not r.get("ok")),
        "bytes": sum(int(r.get("bytes") or 0) for r in results.values()),
        "seconds": round(time.monotonic() - started, 1),
        "results": results,
    }
    _record(root, summary)
    return summary


def newest_dump(folder: pathlib.Path, prefix: str) -> Optional[pathlib.Path]:
    best = None
    if not folder.is_dir():
        return None
    for p in folder.iterdir():
        m = pg_backup.NAME_RE.match(p.name)
        if m and m.group("prefix") == prefix and (best is None or p.name > best.name):
            best = p
    return best


def drill_all(platform_url: str, dest: str, *, pg_bin: Optional[str] = None, store: Optional[str] = None,
              store_url_template: Optional[str] = None) -> Dict:
    """Restore the newest dump of each database into a scratch database,
    compare table row counts with the live one, drop the scratch database."""
    started = time.monotonic()
    root = pathlib.Path(dest).expanduser().resolve()
    targets: List[Tuple[str, str, pathlib.Path]] = []
    if store is None:
        targets.append((PLATFORM_NAME, platform_url, root / PLATFORM_FOLDER))
    for code, db_name in list_stores(platform_url, pg_bin):
        if store is None or code == store.upper():
            targets.append((code, store_url(platform_url, db_name, store_url_template), root / code))
    results: Dict[str, Dict] = {}
    for label, url, folder in targets:
        dump = newest_dump(folder, label)
        if dump is None:
            results[label] = {"ok": False, "error": "no dump yet"}
            continue
        try:
            results[label] = pg_backup.drill(url, str(dump), pg_bin=pg_bin)
        except Exception as exc:  # noqa: BLE001
            results[label] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    summary = {
        "command": "drill",
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "ok": bool(results) and all(r.get("ok") for r in results.values()),
        "databases": len(results),
        "failed": sorted(k for k, r in results.items() if not r.get("ok")),
        "seconds": round(time.monotonic() - started, 1),
        "results": results,
    }
    _record(root, summary)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("backup", "drill"):
        s = sub.add_parser(name)
        s.add_argument("--platform-url", default=os.environ.get("PLATFORM_DATABASE_URL"), required=False)
        s.add_argument("--dest", required=True)
        s.add_argument("--pg-bin", default=os.environ.get("PG_BIN"))
        s.add_argument("--store-url-template", default=None)
        if name == "backup":
            s.add_argument("--keep-days", type=int, default=pg_backup.DEFAULT_KEEP_DAYS)
        else:
            s.add_argument("--store", default=None)
    args = p.parse_args(argv)
    if not args.platform_url:
        print("backup_all: --platform-url (or PLATFORM_DATABASE_URL) is required", file=sys.stderr)
        return 2
    try:
        if args.command == "backup":
            out = backup_all(args.platform_url, args.dest, keep_days=args.keep_days, pg_bin=args.pg_bin,
                             store_url_template=args.store_url_template)
        else:
            out = drill_all(args.platform_url, args.dest, pg_bin=args.pg_bin, store=args.store,
                            store_url_template=args.store_url_template)
    except pg_backup.BackupError as exc:
        print(f"backup_all {args.command}: FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1, sort_keys=True))
    for label, r in sorted(out["results"].items()):
        state = "OK" if r.get("ok") else f"FAILED {r.get('error') or r.get('differences')}"
        print(f"  {label}: {state}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
