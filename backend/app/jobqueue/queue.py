"""The PostgreSQL job queue (docs/WEBSITE_READY_CONTRACT.md I2, C3).

Rows live in the platform database (``jobs``, ``job_workers``; p0004). Every
function here works on that database only and is PostgreSQL-only (the queue
is never used in single mode, see ``app.jobqueue.mode``).

Life of a job::

    queued --claim--> running --ok--> done
                         |--error, attempts left--> failed (run_after = now + backoff) --claim--> running ...
                         |--error, no attempts left--> dead
                         '--worker gone / timed out (reaper)--> failed or dead

``failed`` jobs are claimable again once ``run_after`` has passed. A master's
Retry puts a ``failed`` or ``dead`` job back to ``queued`` with fresh attempts.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import text

logger = logging.getLogger(__name__)

STATUSES = ("queued", "running", "failed", "done", "dead")
OPEN_STATUSES = ("queued", "running", "failed")
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600
# A running job whose worker has not sent a heartbeat for this long is
# taken back by the reaper (workers beat every HEARTBEAT_SECONDS).
HEARTBEAT_SECONDS = 15
STUCK_AFTER_SECONDS = 120
# Grace on top of a job's timeout_seconds before the reaper fails it.
TIMEOUT_GRACE_SECONDS = 60
KEEP_FINISHED_DAYS = 14
MAX_ERROR_CHARS = 4000


def _engine():
    from app.platform.db import platform_engine

    return platform_engine()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def backoff_seconds(attempts: int) -> int:
    """30 s, 60 s, 120 s ... after the 1st, 2nd, 3rd failed attempt (max 1 h)."""
    n = max(1, int(attempts))
    return int(min(BACKOFF_BASE_SECONDS * (2 ** (n - 1)), BACKOFF_MAX_SECONDS))


def _row(r) -> Dict[str, Any]:
    d = dict(r._mapping)
    for key in ("payload", "result"):
        raw = d.get(key)
        if isinstance(raw, str) and raw:
            try:
                d[key] = json.loads(raw)
            except ValueError:
                pass
    return d


def _clip(msg: str) -> str:
    msg = str(msg or "")
    return msg if len(msg) <= MAX_ERROR_CHARS else msg[: MAX_ERROR_CHARS - 1] + "…"


# --- enqueue -----------------------------------------------------------------------------


def insert_job(
    kind: str,
    payload: Dict[str, Any],
    *,
    store_code: Optional[str],
    dedupe_key: Optional[str] = None,
    run_after: Optional[dt.datetime] = None,
    priority: int = 0,
    max_attempts: int = 3,
    timeout_seconds: int = 900,
) -> Dict[str, Any]:
    """Add a job; with ``dedupe_key``, a job of the same kind and key that is
    still queued / running / failed is returned instead (``deduped: True``)."""
    now = utcnow()
    params = {
        "kind": kind,
        "store_code": store_code,
        "payload": json.dumps(payload, sort_keys=True),
        "priority": int(priority),
        "max_attempts": max(1, int(max_attempts)),
        "timeout_seconds": max(1, int(timeout_seconds)),
        "dedupe_key": dedupe_key,
        "run_after": run_after or now,
        "now": now,
    }
    cols = "kind, store_code, payload, status, priority, attempts, max_attempts, timeout_seconds, dedupe_key, run_after, created_at, updated_at"
    vals = ":kind, :store_code, :payload, 'queued', :priority, 0, :max_attempts, :timeout_seconds, :dedupe_key, :run_after, :now, :now"
    with _engine().begin() as conn:
        if dedupe_key:
            row = conn.execute(
                text(
                    f"INSERT INTO jobs ({cols}) VALUES ({vals}) "
                    "ON CONFLICT (kind, dedupe_key) WHERE dedupe_key IS NOT NULL "
                    "AND status IN ('queued', 'running', 'failed') DO NOTHING "
                    "RETURNING id, kind, store_code, status"
                ),
                params,
            ).first()
            if row is None:
                existing = conn.execute(
                    text(
                        "SELECT id, kind, store_code, status FROM jobs WHERE kind = :kind AND dedupe_key = :dedupe_key "
                        "AND status IN ('queued', 'running', 'failed') ORDER BY id DESC LIMIT 1"
                    ),
                    params,
                ).first()
                if existing is not None:
                    return {**dict(existing._mapping), "deduped": True}
                # It finished between the two statements: add a new one.
                row = conn.execute(
                    text(f"INSERT INTO jobs ({cols}) VALUES ({vals}) RETURNING id, kind, store_code, status"), params
                ).first()
        else:
            row = conn.execute(
                text(f"INSERT INTO jobs ({cols}) VALUES ({vals}) RETURNING id, kind, store_code, status"), params
            ).first()
    return {**dict(row._mapping), "deduped": False}


# --- workers: claim / finish --------------------------------------------------------------


def claim(worker_id: str, *, kinds: Optional[Sequence[str]] = None, limit: int = 1) -> List[Dict[str, Any]]:
    """Take up to ``limit`` due jobs for ``worker_id`` (highest priority,
    then oldest). ``FOR UPDATE SKIP LOCKED``: two workers never take the
    same job, and neither waits for the other."""
    kind_filter = "AND kind = ANY(:kinds)" if kinds else ""
    sql = text(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_by = :w, locked_at = :now, "
        "heartbeat_at = :now, started_at = :now, updated_at = :now, finished_at = NULL "
        "WHERE id IN (SELECT id FROM jobs WHERE status IN ('queued', 'failed') AND run_after <= :now "
        f"{kind_filter} ORDER BY priority DESC, run_after, id LIMIT :n FOR UPDATE SKIP LOCKED) "
        "RETURNING id, kind, store_code, payload, attempts, max_attempts, timeout_seconds"
    )
    params: Dict[str, Any] = {"w": worker_id, "now": utcnow(), "n": max(1, int(limit))}
    if kinds:
        params["kinds"] = list(kinds)
    with _engine().begin() as conn:
        rows = conn.execute(sql, params).all()
    return [_row(r) for r in rows]


def complete(job_id: int, worker_id: str, attempt: int, result: Optional[Dict[str, Any]] = None) -> bool:
    """Mark a claimed job done. False when the job is no longer ours (the
    reaper took it back, e.g. after a timeout): the late result is dropped."""
    now = utcnow()
    with _engine().begin() as conn:
        n = conn.execute(
            text(
                "UPDATE jobs SET status = 'done', result = :result, error = NULL, finished_at = :now, "
                "updated_at = :now, locked_by = NULL, heartbeat_at = NULL "
                "WHERE id = :id AND status = 'running' AND locked_by = :w AND attempts = :a"
            ),
            {"id": job_id, "w": worker_id, "a": attempt, "now": now,
             "result": None if result is None else json.dumps(result, default=str)},
        ).rowcount
    return bool(n)


def _fail_sql(where: str) -> str:
    # dead when no attempt is left, else failed with backoff (computed in SQL
    # from the attempts so far: 30 s * 2^(attempts-1), at most 1 h).
    return (
        "UPDATE jobs SET "
        "status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'failed' END, "
        "error = :error, finished_at = :now, updated_at = :now, locked_by = NULL, heartbeat_at = NULL, "
        f"run_after = :now + make_interval(secs => LEAST({BACKOFF_BASE_SECONDS} * power(2, GREATEST(attempts, 1) - 1), "
        f"{BACKOFF_MAX_SECONDS})) "
        f"WHERE {where} RETURNING id, status"
    )


def fail(job_id: int, worker_id: str, attempt: int, error: str) -> Optional[str]:
    """Record a failed attempt; returns the new status ("failed" or "dead"),
    or None when the job is no longer ours."""
    with _engine().begin() as conn:
        row = conn.execute(
            text(_fail_sql("id = :id AND status = 'running' AND locked_by = :w AND attempts = :a")),
            {"id": job_id, "w": worker_id, "a": attempt, "error": _clip(error), "now": utcnow()},
        ).first()
    return row.status if row is not None else None


def release(worker_id: str, job_ids: Iterable[int]) -> int:
    """Put this worker's unfinished jobs back (graceful stop): queued again,
    the interrupted attempt not counted."""
    ids = [int(i) for i in job_ids]
    if not ids:
        return 0
    with _engine().begin() as conn:
        return conn.execute(
            text(
                "UPDATE jobs SET status = 'queued', attempts = GREATEST(attempts - 1, 0), locked_by = NULL, "
                "heartbeat_at = NULL, updated_at = :now WHERE id = ANY(:ids) AND status = 'running' AND locked_by = :w"
            ),
            {"ids": ids, "w": worker_id, "now": utcnow()},
        ).rowcount


def heartbeat(worker_id: str, job_ids: Iterable[int] = ()) -> None:
    """The worker and its running jobs are alive."""
    now = utcnow()
    ids = [int(i) for i in job_ids]
    with _engine().begin() as conn:
        conn.execute(text("UPDATE job_workers SET heartbeat_at = :now WHERE id = :w"), {"w": worker_id, "now": now})
        if ids:
            conn.execute(
                text("UPDATE jobs SET heartbeat_at = :now WHERE id = ANY(:ids) AND locked_by = :w AND status = 'running'"),
                {"ids": ids, "w": worker_id, "now": now},
            )


def reap_stuck(now: Optional[dt.datetime] = None, stuck_after: int = STUCK_AFTER_SECONDS) -> List[Dict[str, Any]]:
    """Running jobs whose worker went quiet (no heartbeat for
    ``stuck_after`` s) or that ran past ``timeout_seconds`` (+ grace): count
    the attempt as failed (retry later, or dead). Returns [{id, status}]."""
    now = now or utcnow()
    where = (
        "status = 'running' AND ("
        "COALESCE(heartbeat_at, locked_at) < :stale "
        f"OR started_at + make_interval(secs => timeout_seconds + {TIMEOUT_GRACE_SECONDS}) < :now)"
    )
    with _engine().begin() as conn:
        rows = conn.execute(
            text(_fail_sql(where)),
            {"now": now, "stale": now - dt.timedelta(seconds=stuck_after),
             "error": "The worker stopped responding or the job ran too long; it will be tried again."},
        ).all()
    for r in rows:
        logger.warning("job %s taken back from a silent or slow worker -> %s", r.id, r.status)
    return [dict(r._mapping) for r in rows]


def prune_finished(now: Optional[dt.datetime] = None, keep_days: int = KEEP_FINISHED_DAYS) -> int:
    """Delete done jobs older than ``keep_days`` and stopped workers' rows."""
    now = now or utcnow()
    cutoff = now - dt.timedelta(days=keep_days)
    with _engine().begin() as conn:
        n = conn.execute(text("DELETE FROM jobs WHERE status = 'done' AND finished_at < :c"), {"c": cutoff}).rowcount
        conn.execute(
            text("DELETE FROM job_workers WHERE heartbeat_at < :c"), {"c": cutoff}
        )
    return n


# --- worker rows --------------------------------------------------------------------------


def register_worker(worker_id: str, hostname: str, pid: int, kinds: Optional[Sequence[str]], concurrency: int) -> None:
    now = utcnow()
    with _engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO job_workers (id, hostname, pid, kinds, concurrency, started_at, heartbeat_at, jobs_done, jobs_failed) "
                "VALUES (:id, :h, :pid, :kinds, :c, :now, :now, 0, 0) "
                "ON CONFLICT (id) DO UPDATE SET heartbeat_at = :now, stopped_at = NULL"
            ),
            {"id": worker_id, "h": hostname[:128], "pid": pid, "kinds": ",".join(kinds) if kinds else None,
             "c": concurrency, "now": now},
        )


def worker_counts(worker_id: str, *, done: int = 0, failed: int = 0) -> None:
    with _engine().begin() as conn:
        conn.execute(
            text("UPDATE job_workers SET jobs_done = jobs_done + :d, jobs_failed = jobs_failed + :f WHERE id = :w"),
            {"w": worker_id, "d": done, "f": failed},
        )


def stop_worker(worker_id: str) -> None:
    now = utcnow()
    with _engine().begin() as conn:
        conn.execute(text("UPDATE job_workers SET stopped_at = :now, heartbeat_at = :now WHERE id = :w"),
                     {"w": worker_id, "now": now})


# --- masters: read and retry ----------------------------------------------------------------


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with _engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, kind, store_code, status, priority, attempts, max_attempts, timeout_seconds, dedupe_key, "
                "run_after, locked_by, result, error, created_at, started_at, finished_at, updated_at, payload "
                "FROM jobs WHERE id = :id"
            ),
            {"id": int(job_id)},
        ).first()
    return _row(row) if row is not None else None


def list_jobs(status: Optional[str] = None, *, store_code: Optional[str] = None, kind: Optional[str] = None,
              limit: int = 50, before: Optional[int] = None) -> List[Dict[str, Any]]:
    where, params = ["1=1"], {"n": max(1, min(int(limit), 200))}
    if status:
        where.append("status = :status")
        params["status"] = status
    if store_code:
        where.append("store_code = :store")
        params["store"] = store_code
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    if before:
        where.append("id < :before")
        params["before"] = int(before)
    with _engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, store_code, status, attempts, max_attempts, run_after, error, created_at, "
                f"started_at, finished_at, updated_at FROM jobs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT :n"
            ),
            params,
        ).all()
    return [dict(r._mapping) for r in rows]


def counts() -> Dict[str, int]:
    out = {s: 0 for s in STATUSES}
    with _engine().connect() as conn:
        for status, n in conn.execute(text("SELECT status, count(*) FROM jobs GROUP BY status")).all():
            out[status] = int(n)
        due = conn.execute(
            text("SELECT count(*) FROM jobs WHERE status IN ('queued', 'failed') AND run_after <= :now"),
            {"now": utcnow()},
        ).scalar()
        oldest = conn.execute(
            text("SELECT min(run_after) FROM jobs WHERE status IN ('queued', 'failed') AND run_after <= :now"),
            {"now": utcnow()},
        ).scalar()
    out["due"] = int(due or 0)
    out["oldest_due_seconds"] = int((utcnow() - oldest).total_seconds()) if oldest is not None else 0
    return out


def list_workers(stale_after: int = STUCK_AFTER_SECONDS) -> List[Dict[str, Any]]:
    now = utcnow()
    with _engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT w.id, w.hostname, w.pid, w.kinds, w.concurrency, w.started_at, w.heartbeat_at, w.stopped_at, "
                "w.jobs_done, w.jobs_failed, (SELECT count(*) FROM jobs j WHERE j.locked_by = w.id AND "
                "j.status = 'running') AS running FROM job_workers w "
                "WHERE w.stopped_at IS NULL OR w.stopped_at > :recent ORDER BY w.started_at DESC LIMIT 50"
            ),
            {"recent": now - dt.timedelta(hours=24)},
        ).all()
    out = []
    for r in rows:
        d = dict(r._mapping)
        if d["stopped_at"] is not None:
            d["state"] = "stopped"
        elif d["heartbeat_at"] < now - dt.timedelta(seconds=stale_after):
            d["state"] = "silent"
        else:
            d["state"] = "running"
        out.append(d)
    return out


def retry(job_id: int) -> Optional[Dict[str, Any]]:
    """A master's Retry: a failed or dead job is queued again now with a
    fresh set of attempts. None when the job is not failed / dead (or a
    newer job with the same dedupe key is already open)."""
    now = utcnow()
    with _engine().begin() as conn:
        row = conn.execute(
            text(
                "UPDATE jobs SET status = 'queued', attempts = 0, run_after = :now, updated_at = :now, "
                "finished_at = NULL, locked_by = NULL WHERE id = :id AND status IN ('failed', 'dead') "
                "AND NOT (dedupe_key IS NOT NULL AND EXISTS (SELECT 1 FROM jobs o WHERE o.kind = jobs.kind AND "
                "o.dedupe_key = jobs.dedupe_key AND o.id <> jobs.id AND o.status IN ('queued', 'running', 'failed'))) "
                "RETURNING id, kind, store_code, status"
            ),
            {"id": int(job_id), "now": now},
        ).first()
    return dict(row._mapping) if row is not None else None
