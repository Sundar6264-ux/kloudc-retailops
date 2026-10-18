"""Is Inventory from Toast ready to switch on? (owner, Oct 13 2026)

Scored from the nightly 04:05 comparison of the snapshot built from Toast with
that day's CSV upload (services/inventory_api.py, job_runs
"inventory_api_snapshot", JSON detail "comparison"). Nothing extra is stored:
each night's counts are in its job row, and the pass / fail rule is applied
when read, with the thresholds in force (INVENTORY_API_READY_* settings).

A night passes when all of these hold:
- On hand: of the items counted on either side, the share where Toast's count
  equals the CSV's, or the difference is exactly the units sold after the CSV
  was made (`explained_by_sales`) >= INVENTORY_API_READY_QTY_PCT (99).
- Price same >= INVENTORY_API_READY_PRICE_PCT (99).
- Barcode same >= INVENTORY_API_READY_BARCODE_PCT (99).
- Category (group + category + subcategory all equal) >=
  INVENTORY_API_READY_CATEGORY_PCT (100).
- On-hand differences sales don't explain <= INVENTORY_API_READY_MAX_UNEXPLAINED_QTY (10).

**Ready** = the newest INVENTORY_API_READY_NIGHTS (7) nightly comparisons in a
row passed. A bad night - one that misses a threshold, or a nightly run that
failed (Toast error, a cut-short answer) - resets the streak, and the page says
which night and why. A night with nothing to compare (no CSV for that day yet
at 04:05, or the switch on and the snapshot written from Toast) doesn't count
and doesn't reset. One night = the newest run for that business date.

A CSV that lands after the 04:05 run (before INVENTORY_API_LATE_CSV_UNTIL_HOUR)
gets a second run of the same date (`inventory_api.rerun_for_late_csv`, detail
"rerun"), so that night counts too; if Toast fails on that second run the
night is skipped, not bad.

When the streak reaches the target and the switch is off, admins get the
`inventory_api_ready` push once per streak (dedupe key = the streak's first
night), at 9 AM (quiet hours respected by run_alert_job), and - only if
INVENTORY_API_READY_WHATSAPP is on (default off: the Whapi plan is a 5-chat
sandbox) - one WhatsApp text to the invoice chat (WHAPI_CHAT_ID), also once.
"""

import datetime as dt
import json
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import JobRun, NotificationLog
from app.services import inventory_api_switch
from app.services.inventory_api import JOB_SNAPSHOT
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

ALERT_KEY = "inventory_api_ready"
READY_MESSAGE = "Inventory from Toast is ready - tap to turn on"
PAGE_PATH = "/#/inventory-api"
# How many nights the page lists (and the streak looks back over).
NIGHTS_SHOWN = 30


def thresholds() -> dict:
    s = get_settings()
    return {
        "nights": max(int(s.inventory_api_ready_nights or 0), 1),
        "qty_pct": float(s.inventory_api_ready_qty_pct),
        "price_pct": float(s.inventory_api_ready_price_pct),
        "barcode_pct": float(s.inventory_api_ready_barcode_pct),
        "category_pct": float(s.inventory_api_ready_category_pct),
        "max_unexplained_qty": int(s.inventory_api_ready_max_unexplained_qty),
    }


def page_url() -> str:
    return get_settings().public_base_url.rstrip("/") + PAGE_PATH


def _field(comparison: dict, name: str) -> Optional[dict]:
    return next((f for f in comparison.get("fields") or [] if f.get("field") == name), None)


def night_metrics(comparison: dict) -> dict:
    """The five numbers a night is judged on, from a stored comparison. A
    comparison recorded before the sales explanation existed (no `tracked`)
    is scored on exact matches only - stricter, never kinder."""
    qty = _field(comparison, "inventory_quantity") or {}
    if "tracked" in qty:
        tracked = qty["tracked"]
        matched = qty.get("tracked_same", 0) + qty.get("explained_by_sales", 0)
        explained = qty.get("explained_by_sales", 0)
        unexplained = qty.get("unexplained", 0)
    else:
        tracked = qty.get("compared", 0)
        matched = qty.get("same", 0)
        explained = None
        unexplained = qty.get("differ", 0)
    qty_pct = round(100.0 * matched / tracked, 2) if tracked else None

    placement = comparison.get("placement")
    if placement and placement.get("same_pct") is not None:
        category_pct = placement["same_pct"]
        category_differ = placement.get("differ", 0)
    else:
        parts = [_field(comparison, f) for f in ("category_group", "category", "subcategory")]
        pcts = [p["same_pct"] for p in parts if p and p.get("same_pct") is not None]
        category_pct = min(pcts) if pcts else None
        category_differ = max((p.get("differ", 0) for p in parts if p), default=0)

    price = _field(comparison, "price") or {}
    barcode = _field(comparison, "barcode") or {}
    return {
        "items_compared": comparison.get("items_in_both"),
        "qty_tracked": tracked,
        "qty_pct": qty_pct,
        "qty_explained_by_sales": explained,
        "qty_unexplained": unexplained,
        "price_pct": price.get("same_pct"),
        "price_differ": price.get("differ"),
        "barcode_pct": barcode.get("same_pct"),
        "barcode_differ": barcode.get("differ"),
        "category_pct": category_pct,
        "category_differ": category_differ,
    }


def _pct_text(v) -> str:
    return "-" if v is None else f"{v:g}%"


def judge(metrics: dict, t: dict) -> List[str]:
    """Reasons the night missed (empty = it passed). A missing number fails:
    nothing to compare is not proof of a match."""
    reasons = []

    def below(value, need, what, differ=None):
        if value is None or value < need:
            extra = f", {differ} differ" if differ else ""
            reasons.append(f"{what} {_pct_text(value)} (needs {need:g}%{extra})")

    below(metrics["qty_pct"], t["qty_pct"], "On hand matched or explained by sales")
    below(metrics["price_pct"], t["price_pct"], "Price matched", metrics.get("price_differ"))
    below(metrics["barcode_pct"], t["barcode_pct"], "Barcode matched", metrics.get("barcode_differ"))
    below(metrics["category_pct"], t["category_pct"], "Category matched", metrics.get("category_differ"))
    unexplained = metrics.get("qty_unexplained")
    if unexplained is None or unexplained > t["max_unexplained_qty"]:
        reasons.append(
            f"{unexplained if unexplained is not None else '?'} on-hand differences not explained by sales "
            f"(at most {t['max_unexplained_qty']})"
        )
    return reasons


def _detail(job: JobRun):
    try:
        return json.loads(job.detail) if job.detail else {}
    except ValueError:
        return {"error": job.detail}


def _night(job: JobRun, t: dict) -> dict:
    detail = _detail(job)
    if not isinstance(detail, dict):
        detail = {}
    night = {
        "date": job.business_date.isoformat() if job.business_date else None,
        "run_at": job.started_at.isoformat() if job.started_at else None,
        "metrics": None,
        "reasons": [],
    }
    stored = detail.get("stored") or {}
    rerun = detail.get("rerun") if isinstance(detail.get("rerun"), dict) else None
    if rerun:
        night["rerun"] = rerun.get("reason")
    if job.status == "running":
        night.update(result="running", note="Still running (or stopped before it finished)")
    elif job.status != "success" and rerun:
        # Toast failing on a second try says nothing about the data: the
        # night stays what the nightly run made it - nothing to compare.
        night.update(
            result="skipped",
            note=f"Compared again after the late CSV, but Toast failed: {detail.get('error') or 'no detail'}",
        )
    elif job.status != "success":
        if stored.get("skipped") == "implausible":
            why = f"Toast's answer looked cut short ({stored.get('reason') or 'too few items'})"
        else:
            why = f"The nightly run failed: {detail.get('error') or 'no detail'}"
        night.update(result="bad", reasons=[why])
    elif detail.get("comparison"):
        metrics = night_metrics(detail["comparison"])
        reasons = judge(metrics, t)
        night.update(result="good" if not reasons else "bad", metrics=metrics, reasons=reasons)
        night["sales_explained"] = metrics["qty_explained_by_sales"] is not None
    else:
        if stored.get("written"):
            note = "Snapshot written from Toast (no CSV that day) - nothing to compare"
        else:
            note = "No CSV for that day by the nightly run - nothing to compare"
        night.update(result="skipped", note=note)
    return night


def _newest_run_ids(db: Session) -> List[int]:
    """The newest run of each business date, newest date first - all of
    them (ids only: the details are read only as far as they're needed)."""
    rows = db.execute(
        select(JobRun.id, JobRun.business_date, JobRun.started_at)
        .where(JobRun.job_name == JOB_SNAPSHOT)
        .order_by(JobRun.business_date.desc(), JobRun.started_at.desc(), JobRun.id.desc())
    ).all()
    seen = set()
    ids = []
    for job_id, business_date, started_at in rows:
        key = business_date or (started_at.date() if started_at else None)
        if key in seen:
            continue
        seen.add(key)
        ids.append(job_id)
    return ids


def _iter_nights(db: Session, t: dict):
    for job_id in _newest_run_ids(db):
        job = db.get(JobRun, job_id)
        if job is not None:
            yield _night(job, t)


def nightly_nights(db: Session, t: Optional[dict] = None) -> List[dict]:
    """One entry per business date (its newest run), newest first - the
    NIGHTS_SHOWN newest."""
    t = t or thresholds()
    nights = []
    for night in _iter_nights(db, t):
        nights.append(night)
        if len(nights) >= NIGHTS_SHOWN:
            break
    return nights


def readiness(db: Session, switch_on: Optional[bool] = None) -> dict:
    """The streak is counted over every night back to the last bad one, not
    just the NIGHTS_SHOWN the page lists: its first night is the alert's
    dedupe key, and a streak longer than the list must keep that same first
    night (else the "ready" push would go out again every morning)."""
    t = thresholds()
    nights = []
    streak = 0
    streak_started = None
    reset = None
    for n in _iter_nights(db, t):
        if len(nights) < NIGHTS_SHOWN:
            nights.append(n)
        if reset is None:
            if n["result"] == "good":
                streak += 1
                streak_started = n["date"]
            elif n["result"] == "bad":
                reset = {"date": n["date"], "reasons": n["reasons"]}
        if reset is not None and len(nights) >= NIGHTS_SHOWN:
            break
    last_comparison_at = next((n["run_at"] for n in nights if n["metrics"] is not None), None)
    if switch_on is None:
        switch_on = inventory_api_switch.is_on(db)
    ready = streak >= t["nights"]
    return {
        "ready": ready,
        "streak": streak,
        "needed": t["nights"],
        "streak_started": streak_started,
        "reset": reset,
        "last_comparison_at": last_comparison_at,
        "thresholds": t,
        "nights": nights,
        "switch_on": bool(switch_on),
        # True when the "ready" alert is due (or was sent) for this streak.
        "notify": bool(ready and not switch_on),
    }


# ---------------------------------------------------------------------------
# The "ready" alert: one push to admins per streak, WhatsApp only if allowed
# ---------------------------------------------------------------------------


def dedupe_key(streak_started: str, channel: str = "push") -> str:
    base = f"{ALERT_KEY}:{streak_started}"
    return base if channel == "push" else f"{base}:{channel}"


def _already_sent(db: Session, key: str) -> bool:
    return db.execute(select(NotificationLog.id).where(NotificationLog.dedupe_key == key)).first() is not None


def post_ready_whatsapp(db: Session, streak_started: str) -> str:
    """The WhatsApp text, at most once per streak. Returns what happened:
    "off" (setting off), "sent", "already_sent", "not_configured",
    "uncertain" (timed out - recorded as sent so it is never posted twice)
    or "failed" (retried at the next run)."""
    from app.services import whapi_client

    if not get_settings().inventory_api_ready_whatsapp:
        return "off"
    key = dedupe_key(streak_started, "whatsapp")
    if _already_sent(db, key):
        return "already_sent"
    outcome = "sent"
    try:
        whapi_client.send_whatsapp_message(f"{READY_MESSAGE}: {page_url()}")
    except whapi_client.WhapiNotConfigured:
        logger.warning("Inventory from Toast ready: WhatsApp not configured - not posted")
        return "not_configured"
    except whapi_client.WhapiAmbiguousTimeout:
        logger.warning("Inventory from Toast ready: WhatsApp timed out - may have gone through; not retried")
        outcome = "uncertain"
    except Exception:  # noqa: BLE001
        logger.exception("Inventory from Toast ready: WhatsApp post failed - will retry at the next run")
        return "failed"
    db.add(NotificationLog(key=ALERT_KEY, dedupe_key=key, sent_at=utcnow(), recipient_count=1))
    db.commit()
    return outcome


def inventory_api_ready_alerts(db: Session, now: dt.datetime) -> list:
    """Trigger for the 9 AM `inventory_api_ready` job (run_alert_job checks
    quiet hours before calling this). Returns the push; also posts the
    WhatsApp text when INVENTORY_API_READY_WHATSAPP allows it."""
    from app.services.notifications import Alert  # lazy: keeps this module light

    if inventory_api_switch.is_on(db):
        return []
    r = readiness(db, switch_on=False)
    if not r["ready"] or not r["streak_started"]:
        return []
    try:
        post_ready_whatsapp(db, r["streak_started"])
    except Exception:  # noqa: BLE001 - the push must still go
        db.rollback()
        logger.exception("Inventory from Toast ready: WhatsApp step failed")
    return [
        Alert(
            key=ALERT_KEY,
            dedupe_keys=[dedupe_key(r["streak_started"])],
            title="Inventory from Toast",
            body=READY_MESSAGE,
            data={"url": page_url(), "streak": str(r["streak"])},
        )
    ]
