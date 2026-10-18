"""Run the DEMO store: its server plus the nightly data reset (plan 3.1a, MT13).

    python scripts/demo/run_demo.py --data-dir /path/to/demo-data \
        [--host 127.0.0.1] [--port 8030] [--reset-at 03:30] [--seed 2026]

One process for both the Mac mini LaunchDaemon (deploy/demo/*.plist) and the
Docker service (deploy/demo/docker-compose.demo.yml):

1. If ``<data-dir>/live`` has no data yet, build it (reset_demo.py).
2. Start the API server (uvicorn) on ``live/`` with the DEMO settings forced:
   STORE_CODE=DEMO, POS_PROVIDER=csv (the CSV-only connector: no POS calls),
   DEMO_MODE=true (every login is a read-only demo login), and every outside
   credential blank with every outside base URL on the discard port - so
   nothing can be sent anywhere, whatever the .env file says.
3. Every day at ``--reset-at`` (store time): build tomorrow's data in
   ``next/`` while the server keeps serving, stop the server, swap folders,
   start it again (a few seconds of downtime at night).
4. If the server stops by itself, exit non-zero (launchd KeepAlive / Docker
   ``restart: unless-stopped`` start the runner again). SIGTERM stops both.

The runner refuses to start when the folder is not a DEMO data folder or the
settings name another store.
"""

import argparse
import datetime as dt
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
import zoneinfo
from typing import Dict, Optional

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from scripts.demo import reset_demo  # noqa: E402
from scripts.demo.generate_demo_data import SAFE_OVERRIDES  # noqa: E402

logger = logging.getLogger("run_demo")
POLL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 20


class RunnerError(RuntimeError):
    pass


def server_env(data_dir: pathlib.Path, timezone: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The DEMO server's environment: the caller's, then DEMO settings forced."""
    env = dict(os.environ if base is None else base)
    store = (env.get("STORE_CODE") or "").strip().upper()
    if store and store != "DEMO":
        raise RunnerError(f"STORE_CODE is {store!r} - run_demo.py only runs the DEMO store.")
    if (env.get("DEMO_MODE") or "true").strip().lower() in ("0", "false", "no", "off"):
        raise RunnerError("DEMO_MODE is off - run_demo.py only runs the DEMO store.")
    live = data_dir / "live"
    uploads = live / "uploads"
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{live / 'storeops.db'}",
            "UPLOADS_ROOT": str(uploads),
            "INVOICE_UPLOADS_DIR": str(uploads / "invoices"),
            "TEAM_UPLOADS_DIR": str(uploads / "team"),
            "FLYERS_DIR": str(uploads / "flyers"),
            "STORE_CODE": "DEMO",
            "POS_PROVIDER": "csv",
            "DEMO_MODE": "true",
            "STORE_TIMEZONE": timezone,
            "TOAST_TIMEZONE": timezone,
        }
    )
    env.update(SAFE_OVERRIDES)
    names = {n.strip().lower() for n in (env.get("DEMO_USERNAMES") or "").split(",") if n.strip()}
    env["DEMO_USERNAMES"] = ",".join(sorted(names | {"appreview"}))
    return env


def next_reset(now: dt.datetime, reset_at: dt.time, tz: zoneinfo.ZoneInfo) -> dt.datetime:
    """The next ``reset_at`` (store time) strictly after ``now``, as UTC."""
    local = now.astimezone(tz)
    candidate = dt.datetime.combine(local.date(), reset_at, tzinfo=tz)
    if candidate <= local:
        candidate = dt.datetime.combine(local.date() + dt.timedelta(days=1), reset_at, tzinfo=tz)
    return candidate.astimezone(dt.timezone.utc)


class Runner:
    def __init__(self, args):
        self.args = args
        self.data_dir = pathlib.Path(args.data_dir).resolve()
        self.timezone = args.timezone
        self.tz = zoneinfo.ZoneInfo(self.timezone)
        self.reset_at = dt.time.fromisoformat(args.reset_at)
        self.child: Optional[subprocess.Popen] = None
        self.stopping = False

    def build(self, swap_now: bool) -> None:
        logger.info("building the DEMO data set")
        info = reset_demo.reset(
            self.data_dir, self.args.seed, self.args.days, self.args.items, do_swap=swap_now, timezone=self.timezone
        )
        logger.info("built: %s", info.get("counts"))

    def start_server(self) -> None:
        env = server_env(self.data_dir, self.timezone)
        cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", self.args.host, "--port", str(self.args.port)]
        logger.info("starting the DEMO server: %s", " ".join(cmd))
        self.child = subprocess.Popen(cmd, cwd=str(BACKEND), env=env)

    def stop_server(self) -> None:
        if self.child is None or self.child.poll() is not None:
            return
        self.child.terminate()
        try:
            self.child.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait()

    def nightly_reset(self) -> None:
        try:
            self.build(swap_now=False)  # next/ while the server keeps serving
        except reset_demo.ResetError as exc:
            logger.error("nightly reset skipped, yesterday's data stays: %s", exc)
            return
        self.stop_server()
        reset_demo.swap(self.data_dir)
        self.start_server()

    def run(self) -> int:
        server_env(self.data_dir, self.timezone)  # refuse a non-DEMO setup before touching anything
        reset_demo.prepare_data_dir(self.data_dir)
        if self.args.reset_on_start or not reset_demo.is_demo_dataset(self.data_dir / "live"):
            self.build(swap_now=True)
        self.start_server()
        due = next_reset(dt.datetime.now(dt.timezone.utc), self.reset_at, self.tz)
        logger.info("next reset at %s", due.isoformat())
        while not self.stopping:
            time.sleep(POLL_SECONDS)
            if self.child is not None and self.child.poll() is not None and not self.stopping:
                logger.error("the DEMO server stopped (exit %s)", self.child.returncode)
                return 1
            if dt.datetime.now(dt.timezone.utc) >= due:
                self.nightly_reset()
                due = next_reset(dt.datetime.now(dt.timezone.utc), self.reset_at, self.tz)
                logger.info("next reset at %s", due.isoformat())
        self.stop_server()
        return 0

    def handle_signal(self, signum, _frame) -> None:
        logger.info("signal %s: stopping", signum)
        self.stopping = True


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run the DEMO store server with a nightly data reset")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8030)
    p.add_argument("--reset-at", default="03:30", help="store time, HH:MM")
    p.add_argument("--timezone", default=os.environ.get("STORE_TIMEZONE") or "America/New_York")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--items", type=int, default=400)
    p.add_argument("--reset-on-start", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    runner = Runner(parse_args(argv))
    signal.signal(signal.SIGTERM, runner.handle_signal)
    signal.signal(signal.SIGINT, runner.handle_signal)
    try:
        return runner.run()
    except (RunnerError, reset_demo.ResetError) as exc:
        logger.error("%s", exc)
        runner.stop_server()
        return 2


if __name__ == "__main__":
    sys.exit(main())
