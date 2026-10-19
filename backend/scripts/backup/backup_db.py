#!/usr/bin/env python3
"""Nightly backup of the store database: a consistent copy, checked, kept 30 days.

    python3 backup_db.py --db __RETAILOPS_DIR__/backend/retailops.db \\
                         --dest ~/retailops-backups/nightly

Standard library only, so it runs with any python3 (no app virtualenv needed).

Steps, in order - any failure stops the run with exit code 1:
1. Free-space check: at least twice the database's size must be free at --dest.
2. Copy with SQLite's online backup API from a read-only connection: a
   consistent snapshot even while the server is writing (WAL mode - the
   server's writes are not blocked while it runs). Never touches the source.
3. PRAGMA integrity_check on the copy. Anything but "ok" = failed: the copy
   is kept as <name>.corrupt for a look, nothing is pruned.
4. Gzip it (about 4-5x smaller) and read the .gz back end to end (CRC check).
5. Only after all of that succeeded: delete backups older than --keep-days
   (by the timestamp in the file name). The newest good backup is never
   deleted, however old - a server that stopped backing up for a month
   still has its last good copy.
6. One line appended to the log, and last_backup.json rewritten with the
   result (for a future uptime check / the status page).
"""

import argparse
import datetime as dt
import gzip
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import time
from typing import List, Optional, Tuple

DEFAULT_KEEP_DAYS = 30
NAME_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9_-]+)-(?P<stamp>\d{8}-\d{6})\.db(?P<gz>\.gz)?$")
STAMP_FORMAT = "%Y%m%d-%H%M%S"
CHUNK = 1024 * 1024


class BackupError(Exception):
    pass


def _connect_read_only(db_path: str) -> sqlite3.Connection:
    """Read-only handle on the live database. mode=ro is what we want; if
    the file can't be opened that way (e.g. its -shm file is missing and
    can't be created read-only) fall back to a normal handle, which still
    only reads: the backup API never writes to its source."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn
    except sqlite3.OperationalError:
        return sqlite3.connect(db_path)


def integrity_check(db_path: str) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    return "; ".join(str(r[0]) for r in rows[:20])


def table_counts(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {n: conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def snapshot(src: str, dest_file: str) -> None:
    source = _connect_read_only(src)
    try:
        target = sqlite3.connect(dest_file)
        try:
            # One step (pages=-1): a single read transaction, so the copy is
            # one consistent point in time. In WAL mode the server keeps
            # writing meanwhile. ~1 s for the 430 MB Oct 2026 database.
            source.backup(target, pages=-1)
            # A self-contained file: no -wal/-shm needed to open the copy.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()
    # Switching the copy out of WAL can leave an empty -shm behind.
    for suffix in ("-wal", "-shm"):
        leftover = pathlib.Path(dest_file + suffix)
        if leftover.exists():
            leftover.unlink()


def gzip_file(src: str, dest: str) -> None:
    with open(src, "rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, CHUNK)


def verify_gzip(path: str) -> int:
    """Reads the whole archive (gzip checks its CRC at the end); returns the
    uncompressed size."""
    size = 0
    with gzip.open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                return size
            size += len(block)


def parse_stamp(name: str, prefix: str) -> Optional[dt.datetime]:
    m = NAME_RE.match(name)
    if not m or m.group("prefix") != prefix:
        return None
    try:
        return dt.datetime.strptime(m.group("stamp"), STAMP_FORMAT)
    except ValueError:
        return None


def list_backups(dest: pathlib.Path, prefix: str) -> List[Tuple[dt.datetime, pathlib.Path]]:
    found = []
    for p in dest.iterdir():
        stamp = parse_stamp(p.name, prefix)
        if stamp is not None and p.is_file():
            found.append((stamp, p))
    return sorted(found)


def prune(dest: pathlib.Path, prefix: str, keep_days: int, now: dt.datetime) -> List[str]:
    """Deletes backups older than keep_days, never the newest one."""
    backups = list_backups(dest, prefix)
    if len(backups) <= 1:
        return []
    cutoff = now - dt.timedelta(days=keep_days)
    removed = []
    for stamp, path in backups[:-1]:
        if stamp < cutoff:
            path.unlink()
            removed.append(path.name)
    # Leftovers of a run that died half way (older than a day).
    for p in dest.glob(f"{prefix}-*.partial*"):
        if now - dt.datetime.fromtimestamp(p.stat().st_mtime) > dt.timedelta(days=1):
            p.unlink()
            removed.append(p.name)
    return removed


def _log(log_path: pathlib.Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")


def run(
    db: str,
    dest: str,
    keep_days: int = DEFAULT_KEEP_DAYS,
    prefix: str = "retailops",
    compress: bool = True,
    log: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> dict:
    """One backup. Returns the status dict (also written to last_backup.json)."""
    started = time.time()
    now = now or dt.datetime.now()
    dest_dir = pathlib.Path(dest).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    log_path = pathlib.Path(log).expanduser() if log else dest_dir / "backup.log"
    name = f"{prefix}-{now.strftime(STAMP_FORMAT)}.db"
    partial = dest_dir / (name + ".partial")
    status = {
        "ok": False,
        "db": str(db),
        "started_at": now.isoformat(timespec="seconds"),
        "file": None,
        "bytes": None,
        "db_bytes": None,
        "integrity": None,
        "tables": None,
        "pruned": [],
        "seconds": None,
        "error": None,
    }
    try:
        if not os.path.isfile(db):
            raise BackupError(f"database not found: {db}")
        db_bytes = os.path.getsize(db)
        wal = db + "-wal"
        if os.path.isfile(wal):
            db_bytes += os.path.getsize(wal)
        status["db_bytes"] = db_bytes
        free = shutil.disk_usage(dest_dir).free
        if free < 2 * db_bytes:
            raise BackupError(f"not enough free space at {dest_dir}: {free} bytes free, need {2 * db_bytes}")

        snapshot(db, str(partial))
        result = integrity_check(str(partial))
        status["integrity"] = result
        if result != "ok":
            corrupt = dest_dir / (name + ".corrupt")
            partial.rename(corrupt)
            raise BackupError(f"integrity_check failed: {result} (kept as {corrupt.name})")
        status["tables"] = len(table_counts(str(partial)))

        if compress:
            final = dest_dir / (name + ".gz")
            gz_partial = dest_dir / (name + ".gz.partial")
            gzip_file(str(partial), str(gz_partial))
            if verify_gzip(str(gz_partial)) != partial.stat().st_size:
                raise BackupError("gzip check failed: size mismatch")
            gz_partial.rename(final)
            partial.unlink()
        else:
            final = dest_dir / name
            partial.rename(final)
        status["file"] = str(final)
        status["bytes"] = final.stat().st_size
        status["ok"] = True
        status["pruned"] = prune(dest_dir, prefix, keep_days, now)
    except Exception as exc:  # noqa: BLE001 - every failure is logged and reported
        status["error"] = f"{type(exc).__name__}: {exc}"
        for leftover in (partial, dest_dir / (name + ".gz.partial")):
            if leftover.exists():
                leftover.unlink()
    status["seconds"] = round(time.time() - started, 1)
    (dest_dir / "last_backup.json").write_text(json.dumps(status, indent=1) + "\n")
    if status["ok"]:
        line = (
            f"{status['started_at']} OK {pathlib.Path(status['file']).name} {status['bytes']} bytes "
            f"integrity=ok tables={status['tables']} pruned={len(status['pruned'])} {status['seconds']}s"
        )
    else:
        line = f"{status['started_at']} FAILED {status['error']}"
    _log(log_path, line)
    return status


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="Database file to back up (opened read-only)")
    p.add_argument("--dest", required=True, help="Folder the backups go into")
    p.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="Delete backups older than this (default 30)")
    p.add_argument("--prefix", default="retailops", help="File name prefix, one per store (default retailops)")
    p.add_argument("--no-compress", action="store_true", help="Keep a plain .db instead of .db.gz")
    p.add_argument("--log", help="Log file (default <dest>/backup.log)")
    args = p.parse_args(argv)
    if args.keep_days < 1:
        p.error("--keep-days must be at least 1")
    status = run(args.db, args.dest, args.keep_days, args.prefix, not args.no_compress, args.log)
    print(json.dumps(status, indent=1))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
