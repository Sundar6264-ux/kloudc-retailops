"""Sources close their automatic tasks right away (Oct 15, docs/
BATCH_OCT15_CONTRACT.md "As built - TEAM TASKS (server)").

Before this, an automatic task only followed its source on the 5-minute
tasks_tick, so Home and Team could disagree for minutes (and a Fix Issues
lead's count never moved during the day). Now each source's write path
calls ONE line after its own commit:

    from app.team.source_sync import task_sources_changed
    task_sources_changed("checklist")

- "checklist": a checklist was submitted -> its task is done.
- "order": an order was placed / skipped / un-done (order actions, Mark
  placed, Critical's Skip this week) -> its task is done / cancelled.
- "invoice": an invoice was closed in Toast or deleted -> done / cancelled.
- "issue_leads": Fix Issues items were closed / reopened -> the leads'
  "N left of M" moves (and a task with nothing left closes itself).

It runs in its own short session (the caller's is never touched), only
runs the cheap "close" half of each rule (never the Vendor Calendar or the
full Fix Issues recompute), and never raises - the source's own response
must not depend on it. The 5-minute tick still runs everything as the
safety net. A closed task writes its change-feed row, which wakes the
clients' long-polls at once.
"""

import logging

from app.db import SessionLocal
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

KINDS = ("checklist", "order", "invoice", "issue_leads")


def _sync(db, kind: str) -> int:
    from app.team import auto_tasks

    now = utcnow()
    if kind == "checklist":
        return auto_tasks.sync_checklist_tasks(db, now)
    if kind == "order":
        return auto_tasks.sync_order_tasks(db, now)
    if kind == "invoice":
        return auto_tasks.sync_invoice_tasks(db, now)
    if kind == "issue_leads":
        from app.services.issue_leads import refresh_lead_progress

        return refresh_lead_progress(db)
    raise ValueError(kind)


def task_sources_changed(*kinds: str) -> int:
    """Brings the automatic tasks of these sources up to date now. Returns
    how many tasks changed; 0 on any problem (logged)."""
    from app.config import get_settings

    if not get_settings().team_tasks_enabled:
        return 0
    changed = 0
    db = SessionLocal()
    try:
        for kind in kinds:
            try:
                changed += _sync(db, kind)
            except Exception:  # noqa: BLE001 - never fails the source's request
                logger.exception("Task source sync %r failed", kind)
                db.rollback()
    finally:
        db.close()
    return changed
