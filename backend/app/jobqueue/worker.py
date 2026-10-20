"""Worker processes for the job queue (docs/WEBSITE_READY_CONTRACT.md C3).

``python -m app.jobqueue worker [--concurrency N] [--kinds a,b]`` runs one
``Worker``: N threads that each claim one due job at a time (``SKIP LOCKED``,
so any number of workers on any number of machines share the queue safely),
run it in its store, and record done / failed / dead. A heartbeat thread
keeps the worker row and its running jobs alive (``HEARTBEAT_SECONDS``); the
reaper (``reap_stuck``, run by every worker each minute) takes back jobs of
a worker that died.

SIGTERM / SIGINT: stop claiming, let running jobs finish for up to
``grace_seconds``, then put any unfinished job back in the queue (the
interrupted attempt is not counted) and exit. launchd / Docker send SIGTERM
first and wait 30 s; the default grace is 25 s.

Job timeouts: Python cannot stop a thread, so a job that runs past its
``timeout_seconds`` keeps running in its thread, but the reaper counts the
attempt as failed and retries it elsewhere, and the late result is dropped
(``complete`` checks the attempt). Handlers are idempotent (I2).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from typing import Dict, Optional, Sequence

from app.jobqueue import queue

logger = logging.getLogger(__name__)

REAP_EVERY_SECONDS = 60.0


def new_worker_id() -> str:
    return f"{socket.gethostname()[:80]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Worker:
    def __init__(
        self,
        *,
        concurrency: int = 2,
        kinds: Optional[Sequence[str]] = None,
        poll_seconds: float = 2.0,
        grace_seconds: float = 25.0,
        worker_id: Optional[str] = None,
        heartbeat_seconds: float = queue.HEARTBEAT_SECONDS,
    ):
        self.concurrency = max(1, int(concurrency))
        self.kinds = [k for k in (kinds or []) if k] or None
        self.poll_seconds = float(poll_seconds)
        self.grace_seconds = float(grace_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.id = worker_id or new_worker_id()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list = []
        self._running: Dict[int, int] = {}  # job id -> attempt
        self._lock = threading.Lock()
        self.done = 0
        self.failed = 0

    # --- life cycle -----------------------------------------------------------------------

    def start(self) -> None:
        queue.register_worker(self.id, socket.gethostname(), os.getpid(), self.kinds, self.concurrency)
        try:
            from app import livebus

            livebus.subscribe("jobs", lambda _code, _data: self._wake.set())
        except Exception:  # noqa: BLE001 - polling still works
            logger.debug("no job wake-ups", exc_info=True)
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"jobworker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        hb = threading.Thread(target=self._beat, name="jobworker-heartbeat", daemon=True)
        hb.start()
        self._threads.append(hb)
        logger.info("job worker %s started (concurrency %d, kinds %s)", self.id, self.concurrency, self.kinds or "all")

    def request_stop(self, *_args) -> None:
        self._stop.set()
        self._wake.set()

    def stop(self) -> None:
        """Graceful stop (see the module doc)."""
        self.request_stop()
        deadline = time.monotonic() + self.grace_seconds
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            left = list(self._running)
        if left:
            n = queue.release(self.id, left)
            logger.warning("job worker %s: %d unfinished job(s) put back in the queue", self.id, n)
        try:
            queue.stop_worker(self.id)
        except Exception:  # noqa: BLE001
            logger.exception("worker row not marked stopped")
        logger.info("job worker %s stopped (%d done, %d failed)", self.id, self.done, self.failed)

    def run_forever(self) -> None:
        import signal

        self.start()
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        while not self._stop.wait(1.0):
            pass
        self.stop()

    # --- work ----------------------------------------------------------------------------------

    def run_once(self) -> bool:
        """Claim and run at most one job. True when a job was run."""
        jobs = queue.claim(self.id, kinds=self.kinds, limit=1)
        if not jobs:
            return False
        self._run(jobs[0])
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 - database hiccup: wait and try again
                logger.exception("job worker %s: claim failed", self.id)
                ran = False
            if not ran:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()

    def _run(self, job: dict) -> None:
        from app.jobqueue import UnknownJobKind, run_handler
        from app.tenancy.errors import StoreMoved, StoreNotActive, UnknownStore

        job_id, attempt = int(job["id"]), int(job["attempts"])
        with self._lock:
            self._running[job_id] = attempt
        started = time.monotonic()
        try:
            payload = job.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            result = run_handler(job["kind"], payload, job.get("store_code"))
            if result is not None and not isinstance(result, dict):
                result = {"value": result}
        except UnknownJobKind:
            self._failed(job_id, attempt, f"No handler for job kind {job['kind']!r} in this worker.")
        except (StoreNotActive, UnknownStore, StoreMoved) as exc:
            # The store was suspended, archived or moved after the job was
            # queued: skipped, as the in-process scheduler does - not a
            # failure for a master to look at.
            logger.info("job %s (%s) skipped for store %s: %s", job_id, job["kind"], job.get("store_code"), exc)
            if queue.complete(job_id, self.id, attempt, {"skipped": type(exc).__name__}):
                self.done += 1
                self._count(done=1)
        except Exception as exc:  # noqa: BLE001 - recorded on the job
            logger.exception("job %s (%s) failed", job_id, job["kind"])
            tb = traceback.format_exception_only(type(exc), exc)
            self._failed(job_id, attempt, "".join(tb).strip())
        else:
            if queue.complete(job_id, self.id, attempt, result):
                self.done += 1
                self._count(done=1)
            logger.info("job %s (%s) done in %.1f s", job_id, job["kind"], time.monotonic() - started)
        finally:
            with self._lock:
                self._running.pop(job_id, None)

    def _failed(self, job_id: int, attempt: int, error: str) -> None:
        status = queue.fail(job_id, self.id, attempt, error)
        if status is not None:
            self.failed += 1
            self._count(failed=1)

    def _count(self, *, done: int = 0, failed: int = 0) -> None:
        try:
            queue.worker_counts(self.id, done=done, failed=failed)
        except Exception:  # noqa: BLE001 - counters are informational
            logger.debug("worker counters not saved", exc_info=True)

    def _beat(self) -> None:
        last_reap = 0.0
        while not self._stop.wait(self.heartbeat_seconds):
            with self._lock:
                ids = list(self._running)
            try:
                queue.heartbeat(self.id, ids)
                if time.monotonic() - last_reap >= REAP_EVERY_SECONDS:
                    last_reap = time.monotonic()
                    queue.reap_stuck()
            except Exception:  # noqa: BLE001
                logger.exception("job worker %s: heartbeat failed", self.id)
