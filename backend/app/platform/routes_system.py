"""System status for masters: storage, job queue, workers, live updates (docs/WEBSITE_READY_CONTRACT.md, track C).

Mounted once by app/platform/routes.py install(). Prefix /api/platform/system;
masters only (the same guards as app/platform/routes.py), 404 on a server
without a platform database. Secrets (S3 keys, database passwords) are never
returned: only whether they are set and the host names.

- ``GET  /status``             storage, job queue counts + workers, live updates, database pooling
- ``GET  /jobs``               jobs, newest first (``status``, ``store``, ``kind``, ``before``, ``limit``)
- ``POST /jobs/{id}/retry``    a failed or dead job queued again (audited)
"""

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.platform import masters
from app.platform.audit import audit
from app.platform.config import pget
from app.platform.db import get_platform_db
from app.platform.errors import PlatformError
from app.platform.models import PlatformUser
from app.platform.throttle import client_ip

router = APIRouter(prefix="/api/platform/system", tags=["platform"])

NO_STORE = {"Cache-Control": "no-store"}
JOB_STATUSES = ("queued", "running", "failed", "done", "dead")


def _host(url: str) -> Optional[str]:
    try:
        return urlparse(url).hostname or None
    except ValueError:
        return None


def _jobs_status() -> Dict[str, Any]:
    from app import jobqueue

    mode = jobqueue.mode()
    out: Dict[str, Any] = {"mode": mode, "counts": None, "workers": [], "error": None}
    try:
        from app.jobqueue import queue

        # The tables exist from p0004 on, whatever the mode: show what is
        # there (an inline server simply has none).
        out["counts"] = queue.counts()
        out["workers"] = queue.list_workers()
    except Exception as exc:  # noqa: BLE001 - shown, never a 500
        out["error"] = type(exc).__name__
    return out


def _database_status() -> Dict[str, Any]:
    mode = str(pget("db_pool_mode") or "direct").strip().lower()
    return {
        "pool_mode": "pgbouncer" if mode == "pgbouncer" else "direct",
        "direct_url_set": bool(str(pget("platform_direct_url") or "").strip()),
        "platform_host": _host(str(pget("platform_database_url") or "")),
    }


@router.get("/status")
def system_status(_: PlatformUser = Depends(masters.require_master), __: Session = Depends(get_platform_db)):
    from app import livebus
    from app.storage import storage_status

    return {
        "storage": storage_status(),
        "jobs": _jobs_status(),
        "live_updates": livebus.status(),
        "database": _database_status(),
    }


@router.get("/jobs")
def list_jobs(
    status: Optional[str] = None,
    store: Optional[str] = None,
    kind: Optional[str] = None,
    before: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
    _: PlatformUser = Depends(masters.require_master),
    __: Session = Depends(get_platform_db),
):
    from app.jobqueue import queue

    if status and status not in JOB_STATUSES:
        raise PlatformError(400, "invalid", "Unknown job status.")
    rows = queue.list_jobs(status, store_code=(store or "").strip().upper() or None, kind=kind or None,
                           before=before, limit=limit)
    return {"jobs": rows, "next_before": rows[-1]["id"] if len(rows) == limit else None}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, request: Request, user: PlatformUser = Depends(masters.require_master),
              db: Session = Depends(get_platform_db)):
    from app.jobqueue import queue
    from app.platform.routes import platform_write

    platform_write(request, user)  # the masters' write rate limit
    before = queue.get_job(job_id)
    if before is None:
        raise PlatformError(404, "not_found", "No such job.")
    row = queue.retry(job_id)
    if row is None:
        raise PlatformError(409, "not_retryable", "Only a failed or stopped job can be retried, and only once at a time.")
    audit(
        "job.retried",
        actor_type="platform_user",
        actor_id=user.id,
        actor_name=masters.actor_name(user),
        store_code=before.get("store_code"),
        target=f"job:{job_id}",
        details={"kind": before.get("kind"), "status": before.get("status"), "attempts": before.get("attempts")},
        client_ip=client_ip(request),
        db=db,
    )
    db.commit()
    from app import jobqueue

    jobqueue._wake_workers()
    return {"job": queue.get_job(job_id)}
