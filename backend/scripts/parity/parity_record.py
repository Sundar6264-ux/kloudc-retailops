"""Parity harness: record what the app answers on a copy of a database.

    cd website/backend
    .venv/bin/python scripts/parity/parity_record.py --db /path/to/copy.db --out /path/to/recording

What it does (README.md next to this file has the full story):
1. Copies the database (SQLite backup API, source opened read-only) into a
   scratch folder - the file you pass is never written to.
2. Fingerprints the copy (row counts + per-column checksums of every table).
3. Boots the app in a child process against the copy: schedulers off, every
   outside service blank, outbound network blocked, background threads not
   run, "now" frozen (default: noon store time on the day after the newest
   synced business date, so the same copy always gives the same answers).
4. Calls the read endpoints in parity_endpoints.py as an admin (default: the
   lowest-id admin that isn't a demo login), records every answer, the
   alerts each push trigger would send at 09:00 and 21:30, the morning
   summary lines per recipient, and the scheduled job list.
5. Deletes the scratch copy (--keep-scratch to keep it).

Compare two recordings with parity_diff.py.
"""

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zoneinfo
from typing import Dict, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
STORE_TZ = "America/New_York"
# 2: endpoint answers keep the server's JSON key order (pages list some
#    objects - Fix Issues by_reason / by_type, checklist schemas - in key
#    order, so a reorder is visible) and record Content-Disposition.
FORMAT_VERSION = 2

sys.path.insert(0, str(HERE))

from parity_fingerprint import alembic_head, fingerprint  # noqa: E402


# ---------------------------------------------------------------------------
# Parent: copy, fingerprint, pick "now", run the child
# ---------------------------------------------------------------------------


def copy_database(src: str, dest: str) -> None:
    """Consistent copy of a live (WAL) database without writing to it."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
            # A rollback-journal file opens read-only without a -shm file
            # (the fingerprint reads it that way); the app switches it back
            # to WAL when it connects, as in production.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()


def default_now(db_path: str) -> dt.datetime:
    """Noon store time on the day after the newest synced business date."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT max(business_date) FROM orders").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise SystemExit("No orders in this database - pass --now explicitly.")
    day = dt.date.fromisoformat(str(row[0])[:10]) + dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(12, 0), zoneinfo.ZoneInfo(STORE_TZ))


def default_user(db_path: str) -> str:
    """The lowest-id admin that isn't a demo login and sees sales numbers."""
    demo = {"app_store_review", "appreview"}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT username, coalesce(hide_sales, 0) FROM users WHERE is_admin = 1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for username, hide in rows:
        if username.lower() not in demo and not hide:
            return username
    raise SystemExit("No admin user in this database - pass --user.")


def top_vendors(db_path: str, n: int = 10) -> List[str]:
    """The n vendors with the most items in the newest inventory upload
    (raw SQL on inventory_snapshots, not app code, so the choice can't move
    when a later batch changes how vendors are read). Ties by name."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT supplier FROM inventory_snapshots "
            "WHERE snapshot_date = (SELECT max(snapshot_date) FROM inventory_snapshots) AND supplier IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    counts: Dict[str, int] = {}
    for (supplier,) in rows:
        for name in {p.strip() for p in str(supplier).split(";") if p.strip()}:
            counts[name] = counts.get(name, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def git_state() -> Dict[str, Optional[str]]:
    def run(*args: str) -> Optional[str]:
        try:
            return subprocess.run(
                ["git", *args], cwd=str(BACKEND), capture_output=True, text=True, timeout=20
            ).stdout.strip()
        except Exception:  # noqa: BLE001 - not a git checkout: fine
            return None

    status = run("status", "--porcelain", "--untracked-files=no")
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"), "dirty": bool(status)}


def record(args: argparse.Namespace) -> int:
    src = str(pathlib.Path(args.db).resolve())
    if not os.path.isfile(src):
        raise SystemExit(f"Database not found: {src}")
    out = pathlib.Path(args.out).resolve()
    if out.exists():
        if not args.force:
            raise SystemExit(f"{out} already exists (use --force to replace it)")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "endpoints").mkdir()

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="parity-", dir=args.scratch_root))
    started = time.time()
    try:
        work_db = scratch / "work.db"
        print(f"Copying {src} -> {work_db}", flush=True)
        copy_database(src, str(work_db))

        if args.migrate:
            _migrate(str(work_db), str(scratch))

        now = dt.datetime.fromisoformat(args.now) if args.now else default_now(str(work_db))
        if now.tzinfo is None:
            now = now.replace(tzinfo=zoneinfo.ZoneInfo(STORE_TZ))
        user = args.user or default_user(str(work_db))
        vendors = top_vendors(str(work_db))

        print("Fingerprinting the copy", flush=True)
        t0 = time.time()
        fp = fingerprint(str(work_db))
        fp_seconds = round(time.time() - t0, 1)
        _write_json(out / "fingerprint.json", fp)

        sys.path.insert(0, str(HERE))
        from parity_sandbox import harness_env

        env = dict(os.environ)
        env.update(harness_env(str(work_db), str(scratch)))
        cmd = [
            sys.executable,
            str(HERE / "parity_record.py"),
            "--child",
            "--db",
            str(work_db),
            "--out",
            str(out),
            "--now",
            now.isoformat(),
            "--user",
            user,
            "--vendors-json",
            json.dumps(vendors),
        ]
        if args.only:
            cmd += ["--only", args.only]
        print(f"Booting the app against the copy (now = {now.isoformat()}, user = {user})", flush=True)
        child = subprocess.run(cmd, cwd=str(scratch), env=env)
        if child.returncode != 0:
            print(f"Recording failed (child exit {child.returncode}); partial output removed", file=sys.stderr)
            shutil.rmtree(out, ignore_errors=True)
            return child.returncode

        # Again after the calls: read endpoints must not write. On the Oct 15
        # copy the only change is the harness's own login (user_sessions, one
        # row); its token is random, so that one column is left out.
        fp_after = fingerprint(str(work_db))
        fp_after["tables"].get("user_sessions", {}).get("columns", {}).pop("token", None)
        _write_json(out / "fingerprint_after.json", fp_after)

        meta = json.loads((out / "meta.json").read_text())
        meta.update(
            {
                "source_db": src,
                "alembic_head": alembic_head(str(work_db)),
                "fingerprint_seconds": fp_seconds,
                "total_seconds": round(time.time() - started, 1),
                "git": git_state(),
            }
        )
        _write_json(out / "meta.json", meta)
        print(_summary(out, meta), flush=True)
        return 0
    finally:
        if args.keep_scratch:
            print(f"Scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def _migrate(work_db: str, scratch: str) -> None:
    """alembic upgrade head on the scratch copy (this checkout's migrations),
    for checking a batch that adds a migration against a pre-migration copy."""
    from parity_sandbox import harness_env

    env = dict(os.environ)
    env.update(harness_env(work_db, scratch))
    before = alembic_head(work_db)
    print(f"Migrating the copy: alembic upgrade head (from {before})", flush=True)
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
    )
    if done.returncode != 0:
        raise SystemExit(f"alembic upgrade failed (exit {done.returncode})")
    print(f"  now at {alembic_head(work_db)}", flush=True)


def _summary(out: pathlib.Path, meta: Dict) -> str:
    statuses: Dict[str, int] = {}
    for item in meta.get("endpoints", []):
        statuses[str(item["status"])] = statuses.get(str(item["status"]), 0) + 1
    lines = [
        f"Recorded {len(meta.get('endpoints', []))} endpoints into {out}",
        f"  status codes: {statuses}",
        f"  now: {meta['now']}  user: {meta['user']}  alembic: {meta.get('alembic_head')}",
        f"  network attempts blocked: {len(meta.get('blocked_network', []))}"
        f"  background threads not run: {len(meta.get('suppressed_threads', []))}",
        f"  took {meta.get('total_seconds')} s (fingerprint {meta.get('fingerprint_seconds')} s)",
    ]
    bad = [i["name"] for i in meta.get("endpoints", []) if i["status"] >= 500]
    if bad:
        lines.append(f"  5xx answers (recorded as-is): {', '.join(bad)}")
    return "\n".join(lines)


def _write_json(path: pathlib.Path, value, sort_keys: bool = True) -> None:
    path.write_text(json.dumps(value, indent=1, sort_keys=sort_keys, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# Child: boot the app and record (runs with the harness environment)
# ---------------------------------------------------------------------------


def child(args: argparse.Namespace) -> int:
    import parity_sandbox

    if os.environ.get("PYTHONHASHSEED") != "0" or os.environ.get("DATABASE_URL") != f"sqlite:///{args.db}":
        raise SystemExit("The child must be started by parity_record.py (harness environment missing).")

    # Before anything from app is imported: no import-time call can get out.
    parity_sandbox.install_network_guard()
    parity_sandbox.install_thread_guard()
    sys.path.insert(0, str(BACKEND))

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.main import app

    settings = get_settings()
    expected = f"sqlite:///{args.db}"
    if str(engine.url) != expected or settings.whapi_api_token or settings.toast_client_id:
        raise SystemExit(f"Refusing to record: engine is {engine.url}, expected {expected}")

    import parity_clock

    now = dt.datetime.fromisoformat(args.now)
    parity_clock.freeze(now)
    store_today = now.astimezone(zoneinfo.ZoneInfo(STORE_TZ)).date()

    from app.auth import create_session
    from app.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == args.user).one()
        token, _ = create_session(db, user.id)
    finally:
        db.close()

    from parity_endpoints import ISSUE_TYPES, build_calls

    vendors = json.loads(args.vendors_json)
    calls = build_calls(store_today, vendors, ISSUE_TYPES)
    if args.only:
        calls = [c for c in calls if args.only in c.name]

    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update({"Authorization": f"Bearer {token}"})
    out = pathlib.Path(args.out)
    index: List[Dict] = []
    for call in calls:
        t0 = time.time()
        resp = client.get(call.path, params=call.params or {})
        seconds = round(time.time() - t0, 3)
        entry = {
            "name": call.name,
            "path": call.path,
            "params": call.params or {},
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            # The CSV exports' download file name.
            "content_disposition": resp.headers.get("content-disposition"),
        }
        if "json" in entry["content_type"]:
            entry["json"] = resp.json()
        elif entry["content_type"].startswith("text/"):
            entry["text"] = resp.text
        else:
            entry["bytes"] = len(resp.content)
            entry["sha256"] = hashlib.sha256(resp.content).hexdigest()
        # Key order kept as the server sent it (parity_diff checks it).
        _write_json(out / "endpoints" / f"{call.name}.json", entry, sort_keys=False)
        index.append({"name": call.name, "status": resp.status_code, "seconds": seconds})
        print(f"  {resp.status_code} {seconds:7.2f}s  {call.name}", flush=True)

    alerts = _record_alerts(now, store_today)
    _write_json(out / "alerts.json", alerts)
    jobs = _record_scheduler_jobs(settings)
    _write_json(out / "scheduler_jobs.json", jobs)

    meta = {
        "format_version": FORMAT_VERSION,
        "now": now.isoformat(),
        "store_today": store_today.isoformat(),
        "user": args.user,
        "top_vendors": vendors,
        "endpoints": index,
        "blocked_network": parity_sandbox.blocked_attempts(),
        "suppressed_threads": parity_sandbox.suppressed_threads(),
        "python": platform.python_version(),
        "recorded_at_wall_clock": parity_clock._REAL_DATETIME.now(dt.timezone.utc).isoformat(),
    }
    _write_json(out / "meta.json", meta)
    return 0


def _alert_dict(alert) -> Dict:
    if dataclasses.is_dataclass(alert):
        return dataclasses.asdict(alert)
    return dict(vars(alert))


def _record_alerts(now: dt.datetime, store_today: dt.date) -> Dict:
    """What every push trigger would send at 09:00 and 21:30 store time on
    the frozen day, and each Morning summary recipient's lines - computed,
    never dispatched (the network is blocked anyway)."""
    from app.db import SessionLocal
    from app.services import notification_triggers as nt

    tz = zoneinfo.ZoneInfo(STORE_TZ)
    result: Dict = {}
    for label, hour, minute in (("0900", 9, 0), ("2130", 21, 30)):
        at = dt.datetime.combine(store_today, dt.time(hour, minute), tz)
        by_key: Dict = {}
        for key in sorted(nt.TRIGGERS):
            db = SessionLocal()
            try:
                by_key[key] = [_alert_dict(a) for a in nt.TRIGGERS[key](db, at)]
            except Exception as exc:  # noqa: BLE001 - recorded, so a change shows up
                by_key[key] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                db.rollback()
                db.close()
        result[f"triggers_at_{label}"] = by_key

    # Morning summary: the same filtering run_morning_job does, minus sending.
    at = dt.datetime.combine(store_today, dt.time(9, 0), tz)
    db = SessionLocal()
    try:
        from app.services.notifications import enabled_user_ids, sent_dedupe_keys

        computed = {}
        for key in nt.MORNING_SOURCES:
            alerts = result["triggers_at_0900"].get(key)
            if not isinstance(alerts, list):
                continue
            computed[key] = [
                nt.Alert(**a) for a in alerts if set(a["dedupe_keys"]) - sent_dedupe_keys(db, a["dedupe_keys"])
            ]
        users = nt._summary_recipients(db)
        separate = {key: enabled_user_ids(db, key, users) for key in nt.MORNING_SOURCES}
        result["morning_summary"] = {
            u.username: [line for _, line in nt.morning_summary_lines(db, u, computed, separate)] for u in users
        }
    except Exception as exc:  # noqa: BLE001
        result["morning_summary"] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.rollback()
        db.close()
    return result


def _record_scheduler_jobs(settings) -> Dict:
    """Every job the three schedulers would register in production
    (SCHEDULER_ENABLED, NOTIFICATIONS_ENABLED and TEAM_JOBS_ENABLED on),
    without starting any of them.

    The function is recorded by name, not module path: MT2 moves Toast code
    into app/pos/ with the old modules kept as re-exports, which is not a
    change in what runs."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from app import scheduler as sched

    BackgroundScheduler.start = lambda self, *a, **k: None
    settings.scheduler_enabled = True
    settings.notifications_enabled = True
    settings.team_jobs_enabled = True
    out: Dict = {}
    for label, start in (
        ("main", sched.start_scheduler),
        ("notifications", sched.start_notification_scheduler),
        ("team", sched.start_team_scheduler),
    ):
        try:
            scheduler = start()
            jobs = []
            for job in scheduler.get_jobs() if scheduler is not None else []:
                func = job.func
                jobs.append(
                    {
                        "id": job.id,
                        # __name__: team jobs are wrapped (app/team/jobs.py
                        # with_session) and carry the wrapped job's name there.
                        "func": getattr(func, "__name__", None) or repr(func),
                        "trigger": str(job.trigger),
                        "args": [str(a) for a in job.args],
                        "kwargs": {k: str(v) for k, v in job.kwargs.items()},
                        # Not set on a job that was never added to a running
                        # scheduler unless add_job passed it: "default" then.
                        "misfire_grace_time": getattr(job, "misfire_grace_time", "default"),
                        "coalesce": getattr(job, "coalesce", "default"),
                        "max_instances": getattr(job, "max_instances", "default"),
                    }
                )
            out[label] = sorted(jobs, key=lambda j: j["id"])
        except Exception as exc:  # noqa: BLE001
            out[label] = {"error": f"{type(exc).__name__}: {exc}"}
    sched._scheduler = sched._notification_scheduler = sched._team_scheduler = None
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="Database to record (only read; a copy is used)")
    p.add_argument("--out", required=True, help="Folder to write the recording into")
    p.add_argument("--now", help="Fixed 'now', ISO 8601 (default: noon store time after the newest business date)")
    p.add_argument("--user", help="Admin username to call as (default: lowest-id non-demo admin)")
    p.add_argument("--only", help="Only endpoints whose name contains this (debugging)")
    p.add_argument("--migrate", action="store_true", help="Run this checkout's 'alembic upgrade head' on the copy first")
    p.add_argument("--force", action="store_true", help="Replace --out if it exists")
    p.add_argument("--keep-scratch", action="store_true", help="Keep the scratch copy afterwards")
    p.add_argument("--scratch-root", help="Where the scratch copy goes (default: system temp)")
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--vendors-json", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.child:
        return child(args)
    return record(args)


if __name__ == "__main__":
    sys.exit(main())
