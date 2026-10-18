"""Platform-level jobs (contract 6.2: "Platform jobs (C)"). Track B's leader
scheduler registers ``PLATFORM_JOBS`` once per deployment (no store
context): ``(job_id, func, cron kwargs in UTC)``."""

import logging

logger = logging.getLogger(__name__)


def prune_login_attempts_job() -> None:
    from app.platform.db import is_configured, platform_session
    from app.platform.throttle import prune_login_attempts

    if not is_configured():
        return
    with platform_session() as db:
        n = prune_login_attempts(db, 7)
    logger.info("platform: %d old login attempts pruned", n)


PLATFORM_JOBS = [
    ("platform_prune_login_attempts", prune_login_attempts_job, {"hour": 3, "minute": 30}),
]
