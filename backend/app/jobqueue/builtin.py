"""Job kinds of the queue itself (docs/WEBSITE_READY_CONTRACT.md C3).

- ``store_job``: one run of a scheduled per-store job (``daily_sales_sync``,
  ``insights_nightly``, the startup catch-ups ...). In queue mode the
  leader's scheduler enqueues it at the usual time (``app.tenancy.jobs``)
  and a worker runs the same function in the store's context.
- ``jobqueue_maintenance``: daily, platform level - takes back stuck jobs
  and deletes finished jobs older than 14 days.
"""

import logging
from typing import Any, Dict, Optional

from app.jobqueue import register, register_periodic

logger = logging.getLogger(__name__)

STORE_JOB = "store_job"


def _find_spec(job_id: str):
    from app.tenancy import jobs as tjobs

    spec = tjobs._specs.get(job_id) or tjobs._startup_specs.get(job_id)
    if spec is None:
        # A worker process never started the schedulers: declare every
        # store job once (declarations only record functions).
        from app.scheduler import declare_all_store_jobs

        declare_all_store_jobs()
        spec = tjobs._specs.get(job_id) or tjobs._startup_specs.get(job_id)
    return spec


@register(STORE_JOB, max_attempts=2, timeout_seconds=3600)
def run_store_job(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from app.tenancy.jobs import job_supported_here

    job_id = str(payload.get("job_id") or "")
    spec = _find_spec(job_id)
    if spec is None:
        raise RuntimeError(f"unknown scheduled job {job_id!r}")
    if not job_supported_here(spec.func, spec.capability):
        return {"skipped": "the store's POS does not support this job"}
    spec.func()
    return {"job_id": job_id}


@register("jobqueue_maintenance", max_attempts=1, timeout_seconds=600)
def run_maintenance(_payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.jobqueue import mode, queue

    if mode() != "queue":
        return {"skipped": "inline mode"}
    reaped = queue.reap_stuck()
    pruned = queue.prune_finished()
    return {"reaped": len(reaped), "pruned": pruned}


register_periodic("jobqueue_maintenance", hour=4, minute=10, per_store=False)
