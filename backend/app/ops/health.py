"""Liveness and readiness.

- ``GET /api/health`` (app/main.py, unchanged): the process answers. For
  launchd / Docker restarts.
- ``GET /api/ready``: the process can serve stores. 200 ``{"status":
  "ready", "checks": {...}}`` or 503 ``{"status": "not_ready", ...}``.

  Single mode: the store database answers and is at the Alembic head.
  Multi mode: the platform database answers and is at its head, and no
  active shared store is behind the store schema head (from the registry's
  ``schema_revision``, so no store database is opened). A failing check names
  only the kind of problem, never a URL, a host or a store code. Multi mode
  also reports (never fails on) the job queue depth, worker heartbeats and
  the live updates mode.

Both are logged-out routes (tests/test_review_route_gates.py PUBLIC).
"""

import logging
from typing import Dict, Tuple

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ops"])


def _fail(exc: BaseException) -> str:
    return f"fail: {type(exc).__name__}"


def _single_checks() -> Dict[str, str]:
    from sqlalchemy import text

    from app.db import engine
    from app.migrate import store_head_revision

    checks: Dict[str, str] = {}
    if engine is None:
        return {"database": "fail: no engine"}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            try:
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:  # noqa: BLE001 - no table: not migrated
                conn.rollback()
                current = None
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        return {"database": _fail(exc)}
    head = store_head_revision()
    checks["migrations"] = "ok" if current == head else f"fail: at {current or 'none'}, head {head}"
    return checks


def _multi_checks() -> Dict[str, str]:
    from sqlalchemy import text

    from app.migrate import store_head_revision
    from app.platform.db import platform_engine, platform_head_revision

    checks: Dict[str, str] = {}
    step = "platform_database"
    try:
        with platform_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            try:
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:  # noqa: BLE001
                conn.rollback()
                current = None
            checks["platform_database"] = "ok"
            step = "platform_migrations"
            head = platform_head_revision()
            checks["platform_migrations"] = "ok" if current == head else f"fail: at {current or 'none'}, head {head}"
            step = "store_migrations"
            store_head = store_head_revision()
            behind = conn.execute(
                text(
                    "SELECT count(*) FROM stores WHERE status = 'active' AND hosting = 'shared' "
                    "AND db_host_ref IS NULL AND coalesce(schema_revision, '') <> :head"
                ),
                {"head": store_head},
            ).scalar()
            active = conn.execute(text("SELECT count(*) FROM stores WHERE status = 'active'")).scalar()
            checks["store_migrations"] = "ok" if not behind else f"fail: {behind} store(s) behind {store_head}"
            checks["active_stores"] = str(int(active or 0))
    except Exception as exc:  # noqa: BLE001
        # The check that was running fails (never left out: a check that
        # could not run is not a passed check).
        checks[step] = _fail(exc)
    checks.update(_queue_info())
    return checks


def _queue_info() -> Dict[str, str]:
    """Job queue depth, worker heartbeats and live updates (website-ready
    C6). Information only, never "fail": a web process stays ready without
    workers (jobs wait in the queue; the master console flags it)."""
    try:
        from app import jobqueue, livebus

        out = {"job_queue": jobqueue.mode(), "live_updates": livebus.mode()}
        if out["job_queue"] != "queue":
            return out
        from app.jobqueue import queue

        counts = queue.counts()
        workers = queue.list_workers()
        alive = sum(1 for w in workers if w["state"] == "running")
        silent = sum(1 for w in workers if w["state"] == "silent")
        out["jobs_due"] = f"{counts['due']} (oldest {counts['oldest_due_seconds']} s)"
        out["jobs_dead"] = str(counts["dead"])
        out["job_workers"] = f"{alive} alive" + (f", {silent} silent" if silent else "")
        if livebus.mode() == "postgres":
            out["live_updates"] = "postgres, listening" if livebus.status()["listening"] else "postgres, reconnecting"
        return out
    except Exception as exc:  # noqa: BLE001 - information only
        return {"job_queue": f"unknown ({type(exc).__name__})"}


def readiness() -> Tuple[bool, Dict[str, str]]:
    from app.tenancy import is_multi_tenant

    try:
        checks = _multi_checks() if is_multi_tenant() else _single_checks()
    except Exception as exc:  # noqa: BLE001 - a probe never raises
        logger.exception("readiness check failed")
        checks = {"ready_check": _fail(exc)}
    ok = all(not str(v).startswith("fail") for v in checks.values())
    return ok, checks


@router.get("/api/ready")
def ready():
    ok, checks = readiness()
    body = {"status": "ready" if ok else "not_ready", "checks": checks}
    return JSONResponse(status_code=200 if ok else 503, content=body, headers={"Cache-Control": "no-store"})
