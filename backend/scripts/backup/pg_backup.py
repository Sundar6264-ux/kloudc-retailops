#!/usr/bin/env python3
"""Backup of ONE store's PostgreSQL database (platform contract 11.4 / 19 A):
pg_dump, checked, kept 30 days - and a restore drill that proves a dump
restores.

    python3 pg_backup.py backup --url postgresql://storeops_admin@db:5432/so_store_x \\
                                --dest ~/storeops-backups/so_store_x [--name STORE_X]
    python3 pg_backup.py drill  --url postgresql://storeops_admin@db:5432/so_store_x \\
                                --dump ~/storeops-backups/so_store_x/STORE_X-20261018-033000.dump

Standard library only (needs the PostgreSQL client programs pg_dump /
pg_restore / psql of the SERVER's major version or newer; --pg-bin points at
them). Passwords never go on the command line: use ~/.pgpass or PGPASSWORD.
A SQLAlchemy URL (postgresql+psycopg://...) is accepted too.

backup, in order - any failure stops the run with exit code 1:
1. pg_dump --format=custom (compressed; a consistent snapshot of the database
   while the store keeps working) into a temporary file next to --dest.
2. pg_restore --list on it: the archive must be readable and hold the data
   of every table (TABLE DATA entries).
3. Renamed to <name>-<YYYYMMDD-HHMMSS>.dump.
4. Only then: dumps older than --keep-days are deleted - never the newest.
5. One line appended to backup.log, last_backup.json rewritten.

drill: restores a dump into a NEW scratch database on the same server
(<source db>_drill_<stamp>; needs CREATEDB), compares every table's row count
with the live database, drops the scratch database. Exit 0 only when every
count matches (rows written to the live database after the dump was taken
show up as differences - run it right after a backup, or against a quiet
store).

Schedule `backup` nightly per store (cron / launchd / a scheduler job of the
platform) and `drill` monthly. Off-site copies (R2) are a separate step:
upload the .dump files (deferred, platform contract 20).
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
from typing import Dict, List, Optional

DEFAULT_KEEP_DAYS = 30
STAMP_FORMAT = "%Y%m%d-%H%M%S"
NAME_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9_-]+)-(?P<stamp>\d{8}-\d{6})\.dump$")


class BackupError(Exception):
    pass


def libpq_url(url: str) -> str:
    """postgresql+psycopg://... -> postgresql://... (what libpq accepts)."""
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.split("+", 1)[0]
    if scheme not in ("postgresql", "postgres"):
        raise BackupError(f"not a PostgreSQL URL: {scheme}://...")
    if parsed.password:
        raise BackupError("put the password in ~/.pgpass or PGPASSWORD, not in the URL")
    return urllib.parse.urlunsplit(("postgresql",) + tuple(parsed)[1:])


def db_name(url: str) -> str:
    name = urllib.parse.urlsplit(url).path.lstrip("/")
    if not name:
        raise BackupError("the URL names no database")
    return name


def with_db(url: str, name: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/" + name, parsed.query, parsed.fragment))


def _bin(pg_bin: Optional[str], program: str) -> str:
    if pg_bin:
        path = pathlib.Path(pg_bin) / program
        if not path.exists():
            raise BackupError(f"{path} not found")
        return str(path)
    found = shutil.which(program)
    if not found:
        raise BackupError(f"{program} not found on PATH (pass --pg-bin)")
    return found


def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if done.returncode != 0:
        raise BackupError(f"{pathlib.Path(cmd[0]).name} failed ({done.returncode}): {done.stderr.strip()[:500]}")
    return done


def _psql(pg_bin: Optional[str], url: str, sql: str) -> str:
    return _run([_bin(pg_bin, "psql"), "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", url, "-c", sql]).stdout


def table_counts(pg_bin: Optional[str], url: str) -> Dict[str, int]:
    tables = [
        t
        for t in _psql(
            pg_bin, url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        ).split()
        if t
    ]
    if not tables:
        return {}
    sql = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM public.\"{t}\"" for t in tables)
    out = {}
    for line in _psql(pg_bin, url, sql).splitlines():
        if line.strip():
            name, n = line.split("|")
            out[name] = int(n)
    return out


def _prune(dest: pathlib.Path, prefix: str, keep_days: int, now: dt.datetime) -> List[str]:
    dumps = []
    for p in dest.iterdir():
        m = NAME_RE.match(p.name)
        if m and m.group("prefix") == prefix:
            dumps.append((dt.datetime.strptime(m.group("stamp"), STAMP_FORMAT), p))
    dumps.sort()
    removed = []
    cutoff = now - dt.timedelta(days=keep_days)
    for stamp, p in dumps[:-1]:  # never the newest
        if stamp < cutoff:
            p.unlink()
            removed.append(p.name)
    return removed


def backup(url: str, dest: str, *, name: Optional[str] = None, keep_days: int = DEFAULT_KEEP_DAYS,
           pg_bin: Optional[str] = None, now: Optional[dt.datetime] = None) -> Dict:
    url = libpq_url(url)
    now = now or dt.datetime.now()
    prefix = name or db_name(url)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
        raise BackupError(f"--name {prefix!r}: letters, digits, _ and - only")
    out_dir = pathlib.Path(dest).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{prefix}-{now.strftime(STAMP_FORMAT)}.dump"
    tmp = out_dir / f".{final.name}.partial"
    started = dt.datetime.now()
    status = {"database": db_name(url), "file": str(final), "ok": False, "at": now.isoformat(timespec="seconds")}
    try:
        _run([_bin(pg_bin, "pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "-f", str(tmp), "-d", url])
        listing = _run([_bin(pg_bin, "pg_restore"), "--list", str(tmp)]).stdout
        data_entries = sum(1 for line in listing.splitlines() if " TABLE DATA " in line)
        tables = len(table_counts(pg_bin, url))
        if data_entries < tables:
            raise BackupError(f"the dump holds data of {data_entries} tables, the database has {tables}")
        tmp.rename(final)
        status.update(
            ok=True,
            bytes=final.stat().st_size,
            tables=tables,
            seconds=round((dt.datetime.now() - started).total_seconds(), 1),
            removed=_prune(out_dir, prefix, keep_days, now),
        )
        return status
    except BackupError as exc:
        status["error"] = str(exc)
        raise
    finally:
        if tmp.exists():
            tmp.unlink()
        with open(out_dir / "backup.log", "a") as log:
            log.write(json.dumps(status, sort_keys=True) + "\n")
        (out_dir / "last_backup.json").write_text(json.dumps(status, indent=1, sort_keys=True) + "\n")


def drill(url: str, dump: str, *, pg_bin: Optional[str] = None, now: Optional[dt.datetime] = None) -> Dict:
    url = libpq_url(url)
    dump_path = pathlib.Path(dump).expanduser().resolve()
    if not dump_path.is_file():
        raise BackupError(f"{dump_path} not found")
    now = now or dt.datetime.now()
    scratch = f"{db_name(url)}_drill_{now.strftime('%Y%m%d%H%M%S')}"[:63]
    admin = with_db(url, "postgres")
    _psql(pg_bin, admin, f"CREATE DATABASE \"{scratch}\" TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'")
    try:
        _run([_bin(pg_bin, "pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "-d",
              with_db(url, scratch), str(dump_path)])
        live = table_counts(pg_bin, url)
        restored = table_counts(pg_bin, with_db(url, scratch))
        differences = sorted(
            f"{t}: live {live.get(t)} vs restored {restored.get(t)}"
            for t in set(live) | set(restored)
            if live.get(t) != restored.get(t)
        )
        return {"dump": str(dump_path), "scratch_database": scratch, "tables": len(restored),
                "rows": sum(restored.values()), "differences": differences, "ok": not differences}
    finally:
        _psql(pg_bin, admin, f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--url", required=True)
    b.add_argument("--dest", required=True)
    b.add_argument("--name", help="file name prefix (default: the database name), e.g. the store code")
    b.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    b.add_argument("--pg-bin", default=os.environ.get("PG_BIN"))
    d = sub.add_parser("drill")
    d.add_argument("--url", required=True)
    d.add_argument("--dump", required=True)
    d.add_argument("--pg-bin", default=os.environ.get("PG_BIN"))
    args = p.parse_args(argv)
    try:
        if args.command == "backup":
            result = backup(args.url, args.dest, name=args.name, keep_days=args.keep_days, pg_bin=args.pg_bin)
        else:
            result = drill(args.url, args.dump, pg_bin=args.pg_bin)
    except BackupError as exc:
        print(f"pg_backup {args.command}: FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
