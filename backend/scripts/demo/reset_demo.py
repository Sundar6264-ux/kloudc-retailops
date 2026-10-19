"""Rebuild the DEMO store's data (plan 3.1a, MT13): build, check, swap.

    python scripts/demo/reset_demo.py --data-dir /path/to/demo-data [--seed 2026]

Folder layout (``--data-dir``):

    live/            what the DEMO server uses (retailops.db + uploads/)
    next/            the next data set, built while the server keeps serving
    old/             the previous data set, removed after a swap
    .retailops-demo-data  marks the folder as the DEMO store's (required)
    review_password  the review login's password when DEMO_REVIEW_PASSWORD is
                     not set (created once with a random value, mode 600;
                     outside git, kept across resets so App Review's login
                     keeps working)

``reset`` = build ``next/`` with generate_demo_data.py, smoke-check it with
the real app, then (only with ``--swap``, and only while the DEMO server is
stopped) move ``live -> old``, ``next -> live`` and delete ``old``.
run_demo.py does the stop / swap / start itself every night.

Safety: a folder is only ever replaced when it is a generated DEMO data set
(``demo_dataset.json`` and the ``demo_dataset`` row in its database). Any
other ``live/`` folder - for example a real store's - is refused untouched.
"""

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
from typing import Optional

BACKEND = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = BACKEND / "scripts" / "demo" / "generate_demo_data.py"
SMOKE = BACKEND / "scripts" / "demo" / "smoke_demo.py"
DATASET_KEY = "demo_dataset"


class ResetError(RuntimeError):
    pass


def is_demo_dataset(folder: pathlib.Path) -> bool:
    """True only for a folder the generator made: its demo_dataset.json says
    store DEMO and its database holds the demo_dataset marker row."""
    meta = folder / "demo_dataset.json"
    db = folder / "retailops.db"
    if not meta.is_file() or not db.is_file():
        return False
    try:
        if json.loads(meta.read_text()).get("store_code") != "DEMO":
            return False
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM setting_changes WHERE key = ? ORDER BY id DESC LIMIT 1", (DATASET_KEY,)
            ).fetchone()
        finally:
            conn.close()
        return bool(row) and json.loads(row[0]).get("store_code") == "DEMO"
    except (OSError, ValueError, sqlite3.Error):
        return False


def check_replaceable(folder: pathlib.Path) -> None:
    """A folder about to be replaced must be missing, empty, or a DEMO data set."""
    if not folder.exists():
        return
    if folder.is_dir() and not any(folder.iterdir()):
        return
    if not is_demo_dataset(folder):
        raise ResetError(
            f"{folder} is not a generated DEMO data set - refusing to replace it. "
            "The DEMO reset only ever replaces data it made itself."
        )


MARKER = ".retailops-demo-data"


# Entries a new data folder may already hold (launchd creates the log folder
# before the runner starts).
ALLOWED_IN_NEW_FOLDER = {"logs"}


def prepare_data_dir(data_dir: pathlib.Path) -> None:
    """The data folder must be new/empty (it is then marked) or already
    carry the DEMO marker file; anything else is refused untouched."""
    fresh = not data_dir.exists() or (
        data_dir.is_dir() and {p.name for p in data_dir.iterdir()} <= ALLOWED_IN_NEW_FOLDER
    )
    if fresh:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / MARKER).write_text("KloudC RetailOps DEMO store data folder (scripts/demo/reset_demo.py)\n")
        return
    if not (data_dir / MARKER).is_file():
        raise ResetError(f"{data_dir} is not a DEMO data folder (no {MARKER} file) - refusing to touch it.")


def setting(name: str) -> str:
    """A setting from the environment, else from the backend's .env (the
    LaunchDaemon setup keeps DEMO_REVIEW_PASSWORD there)."""
    value = os.environ.get(name)
    if value:
        return value
    env_file = BACKEND / ".env"
    if env_file.is_file():
        from dotenv import dotenv_values

        return (dotenv_values(env_file).get(name) or "").strip()
    return ""


def review_password_file(data_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Where the review password lives when DEMO_REVIEW_PASSWORD is not set."""
    return None if setting("DEMO_REVIEW_PASSWORD") else data_dir / "review_password"


def build_next(data_dir: pathlib.Path, seed: int, days: int, items: int, end_date: str = "", timezone: str = "") -> dict:
    """Build ``next/`` (replacing a half-built one) and smoke-check it."""
    prepare_data_dir(data_dir)
    nxt = data_dir / "next"
    if nxt.exists():
        shutil.rmtree(nxt)
    cmd = [sys.executable, str(GENERATOR), "--out", str(nxt), "--seed", str(seed), "--days", str(days), "--items", str(items)]
    if end_date:
        cmd += ["--end-date", end_date]
    if timezone:
        cmd += ["--timezone", timezone]
    pw_file = review_password_file(data_dir)
    if pw_file is not None:
        cmd += ["--review-password-file", str(pw_file)]
    env = dict(os.environ)
    if pw_file is None:
        env["DEMO_REVIEW_PASSWORD"] = setting("DEMO_REVIEW_PASSWORD")
    proc = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise ResetError(f"generator failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}")
    info = json.loads(proc.stdout.strip().splitlines()[-1])

    # Smoke-check a throwaway copy (the check signs in, which writes a session row).
    check = data_dir / "next-check"
    if check.exists():
        shutil.rmtree(check)
    shutil.copytree(nxt, check)
    if pw_file is not None:
        env["DEMO_REVIEW_PASSWORD"] = pw_file.read_text().strip()
    try:
        proc = subprocess.run(
            [sys.executable, str(SMOKE), "--data", str(check)], cwd=str(BACKEND), capture_output=True, text=True, env=env
        )
    finally:
        shutil.rmtree(check, ignore_errors=True)
    if proc.returncode != 0:
        raise ResetError(f"smoke check failed:\n{proc.stdout[-4000:]}")
    if not is_demo_dataset(nxt):
        raise ResetError("the new data set is missing its DEMO marker")
    return info


def swap(data_dir: pathlib.Path) -> None:
    """live -> old, next -> live, delete old. Call only while the DEMO server
    is stopped (run_demo.py does)."""
    prepare_data_dir(data_dir)
    live, nxt, old = data_dir / "live", data_dir / "next", data_dir / "old"
    if not is_demo_dataset(nxt):
        raise ResetError(f"{nxt} is not a finished DEMO data set")
    check_replaceable(live)
    if old.exists():
        shutil.rmtree(old)  # a swap interrupted after "live -> old"
    if live.exists():
        os.replace(live, old)
    os.replace(nxt, live)
    if old.exists():
        shutil.rmtree(old)


def reset(data_dir: pathlib.Path, seed: int = 2026, days: int = 90, items: int = 400, do_swap: bool = False,
          end_date: str = "", timezone: str = "") -> dict:
    data_dir = data_dir.resolve()
    prepare_data_dir(data_dir)
    check_replaceable(data_dir / "live")  # refuse early, before building anything
    info = build_next(data_dir, seed, days, items, end_date=end_date, timezone=timezone)
    if do_swap:
        swap(data_dir)
    return info


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Rebuild the DEMO store's made-up data")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--items", type=int, default=400)
    p.add_argument("--end-date", default="")
    p.add_argument("--timezone", default="")
    p.add_argument("--swap", action="store_true", help="also move next/ into live/ (DEMO server must be stopped)")
    args = p.parse_args(argv)
    try:
        info = reset(pathlib.Path(args.data_dir), args.seed, args.days, args.items, args.swap, args.end_date, args.timezone)
    except ResetError as exc:
        print(f"reset_demo: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
