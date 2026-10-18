"""Multi-mode process start / stop (called from app/main.py's lifespan).

Order: startup hooks (track C installs the platform registry and settings
sources here: ``add_startup_hook``), the connection-budget check, then
scheduler leader election - only the leader runs the store jobs
(app/scheduler.py ``start_multi_tenant_schedulers``). The flyer sender
thread runs in every process (it sends what that process queued).
"""

import logging
from typing import Callable, List, Optional

from app.tenancy.leader import LeaderLoop

logger = logging.getLogger(__name__)

_startup_hooks: List[Callable[[], None]] = []
_shutdown_hooks: List[Callable[[], None]] = []
_loop: dict = {"leader": None}


def add_startup_hook(fn: Callable[[], None]) -> None:
    if fn not in _startup_hooks:
        _startup_hooks.append(fn)


def add_shutdown_hook(fn: Callable[[], None]) -> None:
    if fn not in _shutdown_hooks:
        _shutdown_hooks.append(fn)


def _elected() -> None:
    from app.scheduler import start_multi_tenant_schedulers

    start_multi_tenant_schedulers()


def _lost() -> None:
    from app.scheduler import shutdown_multi_tenant_schedulers

    shutdown_multi_tenant_schedulers()


def leader_loop() -> Optional[LeaderLoop]:
    return _loop["leader"]


def start_multi_tenant() -> None:
    from app.config import process_settings
    from app.services import flyer_sender
    from app.tenancy.engines import check_connection_budget

    s = process_settings()
    if not (s.store_database_url_template or "").strip():
        raise RuntimeError("TENANCY_MODE=multi needs STORE_DATABASE_URL_TEMPLATE")
    for fn in list(_startup_hooks):
        fn()
    check_connection_budget()
    if s.scheduler_enabled or s.notifications_enabled or s.team_jobs_enabled:
        url = (s.platform_database_url or "").strip()
        if url.startswith("postgresql"):
            loop = LeaderLoop(url, on_elected=_elected, on_lost=_lost)
            _loop["leader"] = loop
            loop.start()
        else:
            logger.warning("No PostgreSQL platform database: schedulers start without leader election")
            _elected()
    flyer_sender.start()


def stop_multi_tenant() -> None:
    from app.services import flyer_sender
    from app.tenancy.engines import dispose_all_engines

    flyer_sender.stop()
    loop = _loop["leader"]
    _loop["leader"] = None
    if loop is not None:
        loop.stop()
    else:
        _lost()
    for fn in list(_shutdown_hooks):
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("shutdown hook failed")
    dispose_all_engines()
