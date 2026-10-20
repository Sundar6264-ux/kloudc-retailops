"""Background jobs (docs/WEBSITE_READY_CONTRACT.md, interface I2).

INTERFACE STUB landed with the website-ready contract. Track C owns this
package and replaces the bodies with the PostgreSQL queue (``SELECT ... FOR
UPDATE SKIP LOCKED``, separate worker processes, retries, dedupe). Tracks B
(store export, purge, usage roll-up) and D (Clover backfill) register and
enqueue jobs through these names only.

Today (and whenever ``JOB_QUEUE_MODE`` is ``inline``, the default) a job runs
in a daemon thread of the calling process, in the caller's store (or in
``code=``'s store), exactly once, with no retry.

Handlers are plain functions ``fn(payload: dict) -> Optional[dict]``. The
payload must be JSON (it is stored in the queue table); the return value is
saved as the job's result. A handler must be idempotent: the queue may run
it again after a worker dies mid-job.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

Handler = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]


@dataclass(frozen=True)
class JobSpec:
    kind: str
    fn: Handler
    max_attempts: int = 3
    timeout_seconds: int = 900


@dataclass(frozen=True)
class JobHandle:
    id: Optional[int]  # queue row id; None when run inline
    kind: str
    status: str  # "queued" | "running" | "done" | "failed" | "dead" | "started" (inline)
    store_code: Optional[str] = None


class UnknownJobKind(KeyError):
    pass


# kind -> JobSpec. Filled at import time by @register (code, not data).
_HANDLERS: Dict[str, JobSpec] = {}


def register(kind: str, *, max_attempts: int = 3, timeout_seconds: int = 900) -> Callable[[Handler], Handler]:
    """Decorator: ``@register("store_export")`` on a module-level function.
    A kind is registered once; the module must be imported by the web
    process and by the workers (track C imports ``app.jobqueue.kinds``)."""

    def deco(fn: Handler) -> Handler:
        existing = _HANDLERS.get(kind)
        if existing is not None and existing.fn is not fn:
            raise ValueError(f"job kind {kind!r} is already registered")
        _HANDLERS[kind] = JobSpec(kind, fn, max_attempts, timeout_seconds)
        return fn

    return deco


@dataclass(frozen=True)
class PeriodicSpec:
    kind: str
    hour: int  # store-local (per_store) or UTC (platform) hour
    minute: int
    per_store: bool


# kind -> PeriodicSpec. Filled at import time by register_periodic (code).
_PERIODIC: Dict[str, PeriodicSpec] = {}


def register_periodic(kind: str, *, hour: int, minute: int = 0, per_store: bool = True) -> None:
    """Ask the scheduler to enqueue ``kind`` once a day: at ``hour:minute``
    in each active store's time zone (``per_store``, payload ``{}`` in that
    store) or once at that UTC time for the platform. Track C's scheduler
    (leader only) reads ``periodic_jobs()``; the stub only records it."""
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("hour/minute out of range")
    handler_for(kind)  # register the handler first
    _PERIODIC[kind] = PeriodicSpec(kind, hour, minute, per_store)


def periodic_jobs() -> Dict[str, PeriodicSpec]:
    return dict(_PERIODIC)


def handler_for(kind: str) -> JobSpec:
    try:
        return _HANDLERS[kind]
    except KeyError:
        raise UnknownJobKind(kind) from None


def mode() -> str:
    """"inline" (stub, and the default) or "queue" (track C: JOB_QUEUE_MODE
    =queue with a platform database; single mode is always inline)."""
    return "inline"


def enqueue(
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    code: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    run_after: Optional[dt.datetime] = None,
    priority: int = 0,
) -> JobHandle:
    """Run ``kind`` with ``payload`` in the background, in store ``code`` (or
    the current store; platform-level when there is none).

    ``dedupe_key``: while a job of this kind with the same key is queued or
    running, enqueueing again returns that job instead of a new one (queue
    mode). ``run_after``: not before this UTC time (queue mode; inline runs
    now). Never raises for a handler failure - only for an unknown kind or a
    payload that is not JSON."""
    spec = handler_for(kind)
    body = dict(payload or {})
    json.dumps(body)  # payloads must be JSON in queue mode; fail the same way inline

    from app.tenancy.context import current_store_code, run_in_store, spawn_in_store

    store = code or current_store_code()

    def run() -> None:
        try:
            if code:
                run_in_store(code, spec.fn, body)
            else:
                spec.fn(body)
        except Exception:
            logger.exception("job %s failed (inline)", kind)

    spawn_in_store(run, name=f"job-{kind}")
    return JobHandle(id=None, kind=kind, status="started", store_code=store)


def job_status(job_id: int) -> Optional[Dict[str, Any]]:
    """{id, kind, status, attempts, result, error, created_at, finished_at}
    of a queued job, or None (inline jobs are not recorded)."""
    return None


__all__ = (
    "JobHandle",
    "JobSpec",
    "PeriodicSpec",
    "UnknownJobKind",
    "enqueue",
    "handler_for",
    "job_status",
    "mode",
    "periodic_jobs",
    "register",
    "register_periodic",
)
