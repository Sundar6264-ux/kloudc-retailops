"""Every module that registers job kinds (``@jobqueue.register``) or daily
jobs (``register_periodic``). The web process and each worker import this
module so both know every kind; a module that does not exist (yet) in this
build is skipped."""

import importlib
import logging

logger = logging.getLogger(__name__)

JOB_MODULES = (
    "app.jobqueue.builtin",  # store_job (queued scheduler jobs), queue maintenance
    "app.platform.usage",  # track B: usage roll-up and gauges
    "app.platform.export",  # track B: store data export
    "app.platform.offboarding",  # track B: final export, purge
    "app.pos.adapters.clover.webhooks",  # track D: clover_webhook_sync (webhooks, off by default)
)

_loaded = {"done": False}


def load_all() -> None:
    if _loaded["done"]:
        return
    for name in JOB_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name and name.startswith(exc.name):
                continue  # not in this build
            raise
    _loaded["done"] = True
