#!/usr/bin/env python3
"""Restore drill: prove the latest backup actually restores.

    python3 restore_drill.py --backup-dir ~/storeops-backups/nightly \\
        [--compare-db __STOREOPS_DIR__/backend/storeops.db]

    python3 restore_drill.py --backup /tmp/restored-from-r2.db   # any one file

Standard library only. Never touches the live database (--compare-db is
opened read-only, for row counts only) and never writes outside a temporary
folder, which is deleted afterwards (--keep to keep it).

Steps: pick the newest backup (by the timestamp in its name), restore it
(gunzip) into a temp folder, PRAGMA integrity_check, foreign_key_check,
row counts for every table, the migration head, and the newest sales /
inventory / job dates - then print a report. With --compare-db, every
table's count is put next to the live one (the live database is normally a
little ahead: it kept working after the backup was taken).

Exit code 0 = the backup restores and is intact, 1 = it doesn't.
"""

import argparse
import datetime as dt
import gzip
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from backup_db import NAME_RE, integrity_check, list_backups, parse_stamp, table_counts  # noqa: E402

# "How fresh is the data in it" - each is skipped when its table is missing
# (a different store's database, or an older schema).
FRESHNESS_QUERIES = {
    "newest_sales_business_date": "SELECT max(business_date) FROM orders",
    "newest_inventory_snapshot_date": "SELECT max(snapshot_date) FROM inventory_snapshots",
    "newest_job_finished_at": "SELECT max(finished_at) FROM job_runs",
    "users": "SELECT count(*) FROM users",
}


def pick_backup(backup_dir: pathlib.Path, prefix: str) -> pathlib.Path:
    backups = list_backups(backup_dir, prefix)
    if not backups:
        raise SystemExit(f"No {prefix}-YYYYMMDD-HHMMSS.db[.gz] backups in {backup_dir}")
    return backups[-1][1]


def restore(backup: pathlib.Path, work: pathlib.Path) -> pathlib.Path:
    target = work / "restored.db"
    if backup.name.endswith(".gz"):
        with gzip.open(backup, "rb") as fin, open(target, "wb") as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
    else:
        shutil.copyfile(backup, target)
    # A WAL-mode file (e.g. straight from `litestream restore`) can't be
    # opened read-only without its -shm; this is our private copy, so make
    # it a plain rollback-journal file first.
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        leftover = pathlib.Path(str(target) + suffix)
        if leftover.exists():
            leftover.unlink()
    return target


def _ro(db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def facts(db: str) -> Dict:
    conn = _ro(db)
    out: Dict = {}
    try:
        try:
            out["alembic_head"] = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        except sqlite3.Error:
            out["alembic_head"] = None
        for key, sql in FRESHNESS_QUERIES.items():
            try:
                out[key] = conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                out[key] = None
        out["foreign_key_problems"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()
    return out


def drill(
    backup: pathlib.Path, compare_db: Optional[str] = None, keep: bool = False, work_root: Optional[str] = None
) -> Dict:
    started = time.time()
    work = pathlib.Path(tempfile.mkdtemp(prefix="restore-drill-", dir=work_root))
    report: Dict = {
        "ok": False,
        "backup": str(backup),
        "backup_bytes": backup.stat().st_size,
        "work_dir": str(work),
        "error": None,
    }
    m = NAME_RE.match(backup.name)
    stamp = parse_stamp(backup.name, m.group("prefix")) if m else None
    if stamp is not None:
        report["backup_taken_at"] = stamp.isoformat()
        report["backup_age_hours"] = round((dt.datetime.now() - stamp).total_seconds() / 3600, 1)
    try:
        t0 = time.time()
        restored = restore(backup, work)
        report["restore_seconds"] = round(time.time() - t0, 1)
        report["restored_bytes"] = restored.stat().st_size
        t0 = time.time()
        report["integrity"] = integrity_check(str(restored))
        report["integrity_seconds"] = round(time.time() - t0, 1)
        counts = table_counts(str(restored))
        report["tables"] = len(counts)
        report["row_counts"] = counts
        report.update(facts(str(restored)))
        if compare_db:
            live = table_counts(compare_db)
            report["live_row_counts"] = live
            report["missing_tables_vs_live"] = sorted(set(live) - set(counts))
        report["ok"] = report["integrity"] == "ok" and len(counts) > 0
        if not report["ok"] and report["error"] is None:
            report["error"] = "integrity_check failed" if report["integrity"] != "ok" else "no tables"
    except Exception as exc:  # noqa: BLE001 - a backup that can't be read is a failed drill
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if keep:
            report["kept"] = True
        else:
            shutil.rmtree(work, ignore_errors=True)
    report["seconds"] = round(time.time() - started, 1)
    return report


def render(report: Dict) -> str:
    lines = [
        "RESTORE DRILL " + ("PASSED" if report["ok"] else "FAILED"),
        f"  backup:        {report['backup']} ({report['backup_bytes']:,} bytes)",
    ]
    if "backup_taken_at" in report:
        lines.append(f"  taken at:      {report['backup_taken_at']} ({report['backup_age_hours']} h ago)")
    if report.get("error"):
        lines.append(f"  error:         {report['error']}")
    if "restored_bytes" in report:
        lines.append(
            f"  restored:      {report['restored_bytes']:,} bytes in {report['restore_seconds']} s"
            f"{' (kept in ' + report['work_dir'] + ')' if report.get('kept') else ' (temp copy deleted)'}"
        )
    if "integrity" in report:
        lines.append(f"  integrity:     {report['integrity']} ({report['integrity_seconds']} s)")
    if "tables" in report:
        lines += [
            f"  tables:        {report['tables']}",
            f"  migration:     {report.get('alembic_head')}",
            f"  newest sales:  {report.get('newest_sales_business_date')}",
            f"  newest upload: {report.get('newest_inventory_snapshot_date')}",
            f"  newest job:    {report.get('newest_job_finished_at')}",
            f"  users:         {report.get('users')}",
            f"  FK problems:   {report.get('foreign_key_problems')} (informational; SQLite doesn't enforce FKs here)",
        ]
        live = report.get("live_row_counts")
        lines.append("  row counts" + (" (backup / live now):" if live else ":"))
        for name, n in sorted(report["row_counts"].items()):
            if live is not None:
                lv = live.get(name)
                delta = "" if lv is None or lv == n else f"  ({lv - n:+,} since)"
                shown = f"{lv:,}" if lv is not None else "-"
                lines.append(f"    {name:42s} {n:>10,} / {shown:>10}{delta}")
            else:
                lines.append(f"    {name:42s} {n:>10,}")
        if live is not None and report.get("missing_tables_vs_live"):
            lines.append(f"  tables in live but not in backup: {', '.join(report['missing_tables_vs_live'])}")
    lines.append(f"  took {report['seconds']} s")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--backup-dir", help="Folder of nightly backups; the newest is drilled")
    src.add_argument("--backup", help="One backup file (.db or .db.gz), e.g. a Litestream restore")
    p.add_argument("--prefix", default="storeops", help="Backup file prefix (default storeops)")
    p.add_argument("--compare-db", help="Live database to compare row counts with (opened read-only)")
    p.add_argument("--work-root", help="Where the temporary restore goes (default: system temp)")
    p.add_argument("--keep", action="store_true", help="Keep the restored copy")
    p.add_argument("--json", help="Also write the report as JSON here")
    args = p.parse_args(argv)
    backup = pathlib.Path(args.backup) if args.backup else pick_backup(pathlib.Path(args.backup_dir).expanduser(), args.prefix)
    report = drill(backup, args.compare_db, args.keep, args.work_root)
    print(render(report))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, default=str) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
