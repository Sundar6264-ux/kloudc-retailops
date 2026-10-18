"""Background jobs for the Team hub, collected here so each phase can add its
own (recurring-task generation, reminders, chat retention) without editing
app/scheduler.py: call register_team_job at import time of the module that
owns the job. app/scheduler.py::start_team_scheduler runs everything
registered, in America/New_York, when TEAM_JOBS_ENABLED is on.
"""

from typing import Callable, List, NamedTuple

from app.db import SessionLocal


class TeamJob(NamedTuple):
    id: str
    func: Callable[[], None]
    # A factory (tz -> APScheduler trigger), so triggers are built in the
    # scheduler's own timezone.
    trigger: Callable[[object], object]
    misfire_grace_time: int


TEAM_JOBS: List[TeamJob] = []


def register_team_job(job_id: str, func: Callable[[], None], trigger, misfire_grace_time: int = 300) -> None:
    if any(j.id == job_id for j in TEAM_JOBS):
        return
    TEAM_JOBS.append(TeamJob(job_id, func, trigger, misfire_grace_time))


def with_session(fn: Callable) -> Callable[[], None]:
    """Wraps `fn(db)` as a job that opens and closes its own session."""

    def _run() -> None:
        db = SessionLocal()
        try:
            fn(db)
        finally:
            db.close()

    _run.__name__ = getattr(fn, "__name__", "team_job")
    return _run


def _register_core_jobs() -> None:
    from apscheduler.triggers.cron import CronTrigger

    from app.team.changes import prune_team_changes

    register_team_job(
        "team_changes_prune",
        with_session(prune_team_changes),
        lambda tz: CronTrigger(hour=3, minute=30, timezone=tz),
        misfire_grace_time=3600,
    )

    from app.team.media import prune_unattached_media

    register_team_job(
        "team_media_prune_unattached",
        with_session(prune_unattached_media),
        lambda tz: CronTrigger(hour=3, minute=40, timezone=tz),
        misfire_grace_time=3600,
    )


_register_core_jobs()
