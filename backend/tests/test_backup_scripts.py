"""Nightly backup + restore drill (scripts/backup, multi-store plan MT1), and
the launchd / Litestream templates: valid, not installed, no credentials."""

import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import plistlib
import sqlite3
import subprocess
import sys

BACKUP = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backup"
sys.path.insert(0, str(BACKUP))

import backup_db  # noqa: E402
import restore_drill  # noqa: E402

NOW = dt.datetime(2026, 10, 15, 3, 30, 0)


def _live_db(path: pathlib.Path, orders: int = 25) -> pathlib.Path:
    """A small WAL-mode database with an open writer, like the live server's."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, business_date TEXT, item TEXT, qty REAL);
        CREATE TABLE inventory_snapshots (id INTEGER PRIMARY KEY, snapshot_date TEXT);
        CREATE TABLE job_runs (id INTEGER PRIMARY KEY, finished_at TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE alembic_version (version_num TEXT);
        INSERT INTO alembic_version VALUES ('0059');
        INSERT INTO inventory_snapshots (snapshot_date) VALUES ('2026-10-14');
        INSERT INTO job_runs (finished_at) VALUES ('2026-10-15 08:00:00');
        INSERT INTO users (username) VALUES ('owner');
        """
    )
    conn.executemany(
        "INSERT INTO orders (business_date, item, qty) VALUES (?, ?, ?)",
        [("2026-10-14", f"item {i}", i * 0.5) for i in range(orders)],
    )
    conn.commit()
    conn.close()
    return path


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_makes_a_checked_gzip_logs_it_and_never_writes_the_source(tmp_path):
    src = _live_db(tmp_path / "storeops.db")
    before = _sha(src)
    dest = tmp_path / "nightly"
    status = backup_db.run(str(src), str(dest), now=NOW)
    assert status["ok"], status
    assert status["integrity"] == "ok" and status["tables"] == 5
    final = pathlib.Path(status["file"])
    assert final.name == "storeops-20261015-033000.db.gz"
    assert _sha(src) == before
    # The archive is a complete, self-contained database (no -wal needed).
    restored = tmp_path / "check.db"
    restored.write_bytes(gzip.decompress(final.read_bytes()))
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 25
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    conn.close()
    assert sorted(p.name for p in dest.iterdir()) == ["backup.log", "last_backup.json", final.name]
    log = (dest / "backup.log").read_text().splitlines()
    assert len(log) == 1 and " OK storeops-20261015-033000.db.gz " in log[0] and "integrity=ok" in log[0]
    assert json.loads((dest / "last_backup.json").read_text())["ok"] is True


def test_backup_sees_committed_rows_still_in_the_wal(tmp_path):
    src = _live_db(tmp_path / "storeops.db")
    writer = sqlite3.connect(src)
    writer.execute("PRAGMA wal_autocheckpoint=0")  # keep the new rows in the -wal file
    writer.execute("INSERT INTO orders (business_date, item, qty) VALUES ('2026-10-15', 'late sale', 1)")
    writer.commit()
    try:
        status = backup_db.run(str(src), str(tmp_path / "nightly"), compress=False, now=NOW)
    finally:
        writer.close()
    assert status["ok"]
    conn = sqlite3.connect(status["file"])
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 26
    conn.close()


def _fake_backup(dest: pathlib.Path, when: dt.datetime, prefix: str = "storeops") -> pathlib.Path:
    path = dest / f"{prefix}-{when.strftime('%Y%m%d-%H%M%S')}.db.gz"
    path.write_bytes(gzip.compress(b"old"))
    return path


def test_retention_deletes_only_backups_older_than_30_days(tmp_path):
    src = _live_db(tmp_path / "storeops.db")
    dest = tmp_path / "nightly"
    dest.mkdir()
    old = [_fake_backup(dest, NOW - dt.timedelta(days=d)) for d in (31, 45)]
    keep = [_fake_backup(dest, NOW - dt.timedelta(days=d)) for d in (1, 29)]
    other_store = _fake_backup(dest, NOW - dt.timedelta(days=90), prefix="xyzgrocery")
    unrelated = dest / "notes.txt"
    unrelated.write_text("mine")
    status = backup_db.run(str(src), str(dest), keep_days=30, now=NOW)
    assert status["ok"]
    assert sorted(status["pruned"]) == sorted(p.name for p in old)
    assert all(not p.exists() for p in old)
    assert all(p.exists() for p in keep + [other_store, unrelated])


def test_retention_never_deletes_the_newest_backup(tmp_path):
    dest = tmp_path / "nightly"
    dest.mkdir()
    only = _fake_backup(dest, NOW - dt.timedelta(days=100))
    assert backup_db.prune(dest, "storeops", 30, NOW) == []
    assert only.exists()


def test_a_failed_backup_logs_the_error_and_deletes_nothing(tmp_path):
    dest = tmp_path / "nightly"
    dest.mkdir()
    old = _fake_backup(dest, NOW - dt.timedelta(days=60))
    newer = _fake_backup(dest, NOW - dt.timedelta(days=50))
    status = backup_db.run(str(tmp_path / "missing.db"), str(dest), now=NOW)
    assert status["ok"] is False and "database not found" in status["error"]
    assert old.exists() and newer.exists()
    assert " FAILED " in (dest / "backup.log").read_text()
    assert json.loads((dest / "last_backup.json").read_text())["ok"] is False


def test_an_integrity_failure_keeps_the_copy_as_corrupt_and_deletes_nothing(tmp_path, monkeypatch):
    src = _live_db(tmp_path / "storeops.db")
    dest = tmp_path / "nightly"
    dest.mkdir()
    old = _fake_backup(dest, NOW - dt.timedelta(days=60))
    newer = _fake_backup(dest, NOW - dt.timedelta(days=50))
    monkeypatch.setattr(backup_db, "integrity_check", lambda path: "*** in database main *** Page 7: btree")
    status = backup_db.run(str(src), str(dest), now=NOW)
    assert status["ok"] is False and "integrity_check failed" in status["error"]
    assert (dest / "storeops-20261015-033000.db.corrupt").exists()
    assert old.exists() and newer.exists()
    assert not list(dest.glob("*.partial*"))


def test_backup_cli_exit_codes(tmp_path):
    src = _live_db(tmp_path / "storeops.db")
    assert backup_db.main(["--db", str(src), "--dest", str(tmp_path / "n")]) == 0
    assert backup_db.main(["--db", str(tmp_path / "nope.db"), "--dest", str(tmp_path / "n")]) == 1


# --- restore drill ---------------------------------------------------------------------------


def test_restore_drill_restores_the_newest_backup_and_reports(tmp_path):
    src = _live_db(tmp_path / "storeops.db", orders=10)
    dest = tmp_path / "nightly"
    assert backup_db.run(str(src), str(dest), now=NOW - dt.timedelta(days=1))["ok"]
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO orders (business_date, item, qty) VALUES ('2026-10-15', 'x', 1)")
    conn.commit()
    conn.close()
    newest = backup_db.run(str(src), str(dest), now=NOW)
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO orders (business_date, item, qty) VALUES ('2026-10-15', 'y', 1)")
    conn.commit()
    conn.close()

    report = restore_drill.drill(restore_drill.pick_backup(dest, "storeops"), compare_db=str(src))
    assert report["ok"], report
    assert report["backup"] == newest["file"]
    assert report["integrity"] == "ok"
    assert report["row_counts"]["orders"] == 11 and report["live_row_counts"]["orders"] == 12
    assert report["alembic_head"] == "0059"
    assert report["newest_sales_business_date"] == "2026-10-15"
    assert report["backup_taken_at"] == NOW.isoformat()
    assert not pathlib.Path(report["work_dir"]).exists()  # temp copy removed
    text = restore_drill.render(report)
    assert "RESTORE DRILL PASSED" in text and "(+1 since)" in text


def test_restore_drill_fails_on_a_damaged_backup(tmp_path):
    dest = tmp_path / "nightly"
    dest.mkdir()
    bad = dest / "storeops-20261015-033000.db.gz"
    bad.write_bytes(gzip.compress(b"this is not a database" * 100))
    assert restore_drill.main(["--backup-dir", str(dest)]) == 1
    truncated = dest / "storeops-20261016-033000.db.gz"
    truncated.write_bytes(gzip.compress(b"x" * 10000)[:50])
    report = restore_drill.drill(truncated)
    assert report["ok"] is False and report["error"]


def test_restore_drill_accepts_a_plain_db_file(tmp_path):
    src = _live_db(tmp_path / "restored-from-r2.db")
    out = tmp_path / "report.json"
    assert restore_drill.main(["--backup", str(src), "--json", str(out)]) == 0
    assert json.loads(out.read_text())["row_counts"]["users"] == 1


# --- templates -------------------------------------------------------------------------------


def test_launchd_templates_are_valid_plists_and_run_the_right_things():
    backup = plistlib.loads((BACKUP / "com.kloudc.storeops.backup.plist").read_bytes())
    assert backup["Label"] == "com.kloudc.storeops.backup"
    assert backup["ProgramArguments"][1].endswith("scripts/backup/backup_db.py")
    assert backup["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
    assert "--keep-days" in backup["ProgramArguments"]
    litestream = plistlib.loads((BACKUP / "com.kloudc.storeops.litestream.plist").read_bytes())
    assert litestream["ProgramArguments"] == ["__STOREOPS_DIR__/backend/scripts/backup/run_litestream.sh"]
    assert litestream["KeepAlive"] == {"SuccessfulExit": False}
    for plist in (backup, litestream):
        flat = json.dumps(plist).lower()
        assert "secret" not in flat and "access_key" not in flat


def test_litestream_template_holds_no_credentials():
    text = (BACKUP / "litestream.yml.template").read_text()
    assert "REPLACE_WITH_ACCOUNT_ID" in text and "REPLACE_WITH_BUCKET_NAME" in text
    assert "REPLACE_WITH_STORE_CODE" in text and "REPLACE_WITH_STOREOPS_DIR" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith(("access-key-id", "secret-access-key"))


def _run_wrapper(conf_dir: pathlib.Path):
    env = dict(os.environ, STOREOPS_LITESTREAM_DIR=str(conf_dir), PATH="/usr/bin:/bin")
    return subprocess.run(
        ["/bin/bash", str(BACKUP / "run_litestream.sh")], capture_output=True, text=True, env=env, timeout=30
    )


def test_litestream_wrapper_stays_off_until_the_owner_sets_it_up(tmp_path):
    conf = tmp_path / "conf"
    conf.mkdir()
    done = _run_wrapper(conf)
    assert done.returncode == 0 and "not started: no config" in done.stdout

    (conf / "litestream.yml").write_text((BACKUP / "litestream.yml.template").read_text())
    (conf / "litestream.env").write_text("LITESTREAM_ACCESS_KEY_ID=\nLITESTREAM_SECRET_ACCESS_KEY=\n")
    os.chmod(conf / "litestream.env", 0o600)
    done = _run_wrapper(conf)
    assert done.returncode == 0 and "REPLACE_WITH_ placeholders" in done.stdout

    (conf / "litestream.yml").write_text("dbs: []\n")
    done = _run_wrapper(conf)
    assert done.returncode == 0 and "LITESTREAM_ACCESS_KEY_ID is empty" in done.stdout

    os.chmod(conf / "litestream.env", 0o644)
    done = _run_wrapper(conf)
    assert done.returncode == 1 and "readable by others" in done.stderr


def test_restore_steps_stop_the_watchdog_and_keep_the_old_wal():
    """The watchdog (com.kloudc.storeops.watchdog) re-bootstraps a
    stopped backend every 5 minutes; the manual restore must stop it before
    the backend, swap a fully unpacked file in with one mv, and keep the old
    database's -wal (it can hold the newest writes after a crash)."""
    readme = (BACKUP / "README.md").read_text()
    steps = readme.split("**To actually restore**", 1)[1].split("## Off-site", 1)[0]
    stop_watchdog = steps.index("bootout system/com.kloudc.storeops.watchdog")
    stop_backend = steps.index("bootout system/com.kloudc.storeops.backend")
    start_backend = steps.index("bootstrap system /Library/LaunchDaemons/com.kloudc.storeops.backend.plist")
    start_watchdog = steps.index("bootstrap system /Library/LaunchDaemons/com.kloudc.storeops.watchdog.plist")
    unpack = steps.index("> storeops.db.restoring")
    assert unpack < stop_watchdog < stop_backend < start_backend < start_watchdog
    assert "rm -f storeops.db-wal" not in steps and "mv storeops.db-wal" in steps
    assert "> storeops.db\n" not in steps  # never unpacked straight over the live name
