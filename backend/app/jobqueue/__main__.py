"""Job queue commands (docs/WEBSITE_READY_CONTRACT.md C3, docs/DEPLOY_MULTI_PROCESS.md).

    python -m app.jobqueue worker [--concurrency 2] [--kinds store_export,store_job]
    python -m app.jobqueue status
    python -m app.jobqueue retry JOB_ID

The worker needs TENANCY_MODE=multi, a PostgreSQL PLATFORM_DATABASE_URL and
JOB_QUEUE_MODE=queue (the same .env as the web process). It refuses to
start otherwise: inline mode runs jobs in the web process and a worker
would never get any.
"""

import argparse
import json
import logging
import sys
from typing import List, Optional


def boot() -> None:
    """Load the app as the web process does (settings, platform registry
    and settings sources, every job kind), without serving HTTP."""
    import app.main  # noqa: F401 - mounts routes, installs the platform sources in multi mode
    from app.jobqueue.kinds import load_all

    load_all()


def _cmd_worker(args) -> int:
    from app import jobqueue
    from app.jobqueue.worker import Worker

    boot()
    if jobqueue.mode() != "queue":
        print(
            "The job queue is off (JOB_QUEUE_MODE=inline, single-store mode or no PostgreSQL platform database): "
            "jobs run inside the web process. Set JOB_QUEUE_MODE=queue to use workers.",
            file=sys.stderr,
        )
        return 2
    from app import livebus

    livebus.start()  # job wake-ups (LIVE_UPDATES_MODE=postgres); polling otherwise
    kinds = [k.strip() for k in (args.kinds or "").split(",") if k.strip()]
    worker = Worker(concurrency=args.concurrency, kinds=kinds or None, poll_seconds=args.poll,
                    grace_seconds=args.grace)
    try:
        worker.run_forever()
    finally:
        livebus.stop()
    return 0


def _cmd_status(_args) -> int:
    from app.jobqueue import queue

    boot()
    out = {"counts": queue.counts(), "workers": queue.list_workers()}
    print(json.dumps(out, default=str, indent=2))
    return 0


def _cmd_retry(args) -> int:
    from app.jobqueue import queue

    boot()
    row = queue.retry(args.job_id)
    if row is None:
        print(f"Job {args.job_id} is not failed or dead (or another copy is already waiting).", file=sys.stderr)
        return 1
    print(json.dumps(row, default=str))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m app.jobqueue")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker", help="run a worker until SIGTERM")
    w.add_argument("--concurrency", type=int, default=2)
    w.add_argument("--kinds", default="", help="comma-separated job kinds (default: all)")
    w.add_argument("--poll", type=float, default=2.0, help="seconds between polls when idle")
    w.add_argument("--grace", type=float, default=25.0, help="seconds running jobs get to finish on SIGTERM")
    w.set_defaults(fn=_cmd_worker)
    s = sub.add_parser("status", help="queue counts and workers (JSON)")
    s.set_defaults(fn=_cmd_status)
    r = sub.add_parser("retry", help="queue a failed or dead job again")
    r.add_argument("job_id", type=int)
    r.set_defaults(fn=_cmd_retry)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
