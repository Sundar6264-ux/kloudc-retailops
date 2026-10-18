"""Sales sync: a business date's orders from the POS into `orders` (POS-neutral).

Moved from ``services/sales_sync.py`` in multi-store M1 batch MT3; that name
still works (``app.services.sales_sync``, forwarding here). The job asks the
connector for the date's ``Order`` models and writes one `orders` row per
line - the same rows, values and job_runs row as before M1.

Idempotency: each line carries the POS's unique line id (Toast: the
selection GUID, stored as `toast_selection_guid`). Re-running a date that's
already been synced updates existing rows in place (an upsert keyed on that
id) rather than creating duplicates, so it's safe to re-run or backfill.

A re-sync also follows later edits in the POS: a line voided after the first
sync comes back with `voided` set (upsert), a deleted order/check counts as
voided, and a line that is no longer in the POS's answer for that business
date at all (removed from the order, or the order moved to another date) is
deleted from `orders` - see `_prune_missing`, which only ever runs when the
POS answered with orders. The sales accuracy job (pos/sync/accuracy.py)
relies on this to correct already-synced dates.

Row rules (from the old Toast mapping, now on the neutral models):
- a modifier line is its own row with `parent_selection_guid` set; with
  MODIFIER_PRICE_IN_PARENT (Toast) its price is already inside its parent's,
  so reports count top-level rows only;
- `voided` = the order, its check or the line is voided or deleted;
- `paid_at` = the check's own payment time, else the order's; only for a
  voided / deleted order or check, else the order's placement time
  (`line_paid_at`);
- `unit_price` = net / quantity (net when the quantity is 0).
"""

import datetime as dt
import logging
from typing import Callable, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import JobRun, Order
from app.pos import registry
from app.pos.neutral_ids import TOAST, after_sales_sync, neutral_line_values
from app.pos.models import Check, Order as PosOrder, OrderLine

# Same logger name as before the move (log lines and filters unchanged).
logger = logging.getLogger("app.services.sales_sync")

# The job_runs name the status widget, the accuracy job and the catch-up scan
# read - kept from the Toast-only days on purpose.
JOB_NAME = "toast_sales_sync"


def _check_of(order: PosOrder, line: OrderLine) -> Optional[Check]:
    if line.check_index is not None and 0 <= line.check_index < len(order.checks):
        return order.checks[line.check_index]
    for check in order.checks:
        if check.external_id == line.check_external_id:
            return check
    return None


def line_paid_at(order: PosOrder, check: Optional[Check]) -> Optional[dt.datetime]:
    """When a line counts as paid: its check's own payment time, falling back
    to the order's. Only for a voided / deleted order or check does it fall
    back further to the order's placement time - a comped/voided check never
    gets a real payment time, but it's excluded from every revenue total by
    `voided` regardless of what date it lands on, so this is cosmetic there.
    A real, non-voided order that simply hasn't been paid yet (e.g. a
    prepaid-later future pickup still marked unpaid) must NOT get this
    fallback: it hasn't generated revenue yet, and counting it on the day it
    was placed inflated Items Sold. Its NULL paid_at lets items_sold.py
    exclude or defer it."""
    order_voided = order.voided or order.deleted
    check_voided = bool(check is not None and (check.voided or check.deleted))
    paid_at = (check.paid_at if check is not None else None) or order.paid_at
    if paid_at is None and (order_voided or check_voided):
        paid_at = order.created_at
    return paid_at


def line_rows(order: PosOrder) -> List[Dict]:
    """One `orders` row (as a column dict, without business_date) per line of
    the order, in the order's own line order."""
    order_voided = order.voided or order.deleted
    rows = []
    for line in order.lines:
        check = _check_of(order, line)
        check_voided = bool(check is not None and (check.voided or check.deleted))
        quantity = float(line.quantity)
        net_price = float(line.net_amount)
        rows.append(
            {
                "toast_order_guid": order.external_id,
                "toast_check_guid": line.check_external_id,
                "toast_selection_guid": line.external_id,
                "parent_selection_guid": line.parent_external_id,
                "opened_at": order.opened_at,
                "paid_at": line_paid_at(order, check),
                "item_guid": line.item_external_id,
                "item_name": line.name,
                "quantity": quantity,
                "unit_price": net_price / quantity if quantity else net_price,
                "net_price": net_price,
                "tax_amount": float(line.tax_amount),
                "voided": order_voided or check_voided or bool(line.voided),
                "dining_option": order.dining_option_id,
            }
        )
    return rows


def neutral_line_rows(order: PosOrder, source: str) -> List[Dict]:
    """The POS-neutral `orders` columns (migration 0060) for each line of the
    order, parallel to `line_rows(order)`: built from the connector's models
    and its `source`. For Toast these equal `neutral_line_values(row)` of the
    matching `line_rows` row. `channel` is only written once the POS maps one
    (None for Toast in M1), so a re-sync never blanks a stored channel."""
    rows = []
    for line in order.lines:
        row = {
            "source": source,
            "external_order_id": order.external_id,
            "external_check_id": line.check_external_id,
            "external_line_id": line.external_id,
            "parent_external_line_id": line.parent_external_id,
            "is_modifier": bool(line.is_modifier),
            "dining_option_id": order.dining_option_id,
        }
        if order.channel is not None:
            row["channel"] = order.channel
        rows.append(row)
    return rows


def store_source() -> str:
    """The `source` the store's connector writes (`orders.source`,
    `pos_item_map`, `sync_cursors`): "toast" for a Toast store."""
    return getattr(registry.get_connector(), "source", "") or TOAST


def _upsert_line_items(
    db: Session, business_date: dt.date, line_items: list[dict], neutral_items: Optional[List[Dict]] = None
) -> int:
    """`neutral_items` (parallel to `line_items`, from `neutral_line_rows`)
    gives each row's POS-neutral columns; without it they are derived from the
    Toast-named keys (`neutral_line_values`), as old callers expect."""
    count = 0
    for index, item in enumerate(line_items):
        if not item["toast_selection_guid"]:
            continue
        # The POS-neutral copies (migration 0060) go in the same upsert.
        neutral = neutral_items[index] if neutral_items is not None else neutral_line_values(item)
        values = {**item, **neutral}
        stmt = sqlite_insert(Order).values(business_date=business_date, **values)
        update_cols = {c: getattr(stmt.excluded, c) for c in values if c != "toast_selection_guid"}
        update_cols["business_date"] = business_date
        stmt = stmt.on_conflict_do_update(
            index_elements=[Order.toast_selection_guid],
            set_=update_cols,
        )
        db.execute(stmt)
        count += 1
    return count


def _prune_missing(db: Session, business_date: dt.date, line_items: list[dict], order_count: int) -> int:
    """Deletes this business date's stored lines that the POS no longer
    returns for it. Only when the POS actually answered with orders: an empty
    answer for a date we hold rows for is far more likely a POS hiccup than
    every order vanishing, so nothing is deleted then."""
    if order_count == 0:
        return 0
    fresh = {li["toast_selection_guid"] for li in line_items if li["toast_selection_guid"]}
    stored = db.execute(
        select(Order.id, Order.toast_selection_guid).where(Order.business_date == business_date)
    ).all()
    gone = [row_id for row_id, guid in stored if guid not in fresh]
    for i in range(0, len(gone), 500):
        db.execute(delete(Order).where(Order.id.in_(gone[i : i + 500])))
    return len(gone)


def fetch_orders(
    business_date: dt.date,
    max_retries: int = 4,
    sleep: Optional[Callable[[float], None]] = None,
) -> List[PosOrder]:
    """The date's orders from the store's connector, the POS's rate limit
    retried `max_retries` times (`sleep` waits between tries)."""
    return registry.get_connector().fetch_orders(business_date, max_retries=max_retries, sleep=sleep)


def sync_sales_for_date(db: Session, business_date: dt.date, prune: bool = True) -> JobRun:
    job = JobRun(job_name=JOB_NAME, status="running", business_date=business_date)
    db.add(job)
    db.commit()
    db.refresh(job)

    source = TOAST
    try:
        orders = fetch_orders(business_date)
        source = store_source()
        line_items = [row for order in orders for row in line_rows(order)]
        neutral_items = [row for order in orders for row in neutral_line_rows(order, source)]
        synced = _upsert_line_items(db, business_date, line_items, neutral_items)
        removed = _prune_missing(db, business_date, line_items, len(orders)) if prune else 0
        db.commit()

        job.status = "success"
        job.detail = f"{synced} line items synced from {len(orders)} orders"
        if removed:
            # "Toast" stays in this text: job_runs details are shown as they
            # are (multi-store M2 makes UI text name the store's POS).
            job.detail += f"; {removed} no longer in Toast removed"
    except Exception as exc:  # noqa: BLE001 - want to log any failure and keep going
        db.rollback()
        logger.exception("Sales sync failed for %s", business_date)
        job.status = "failed"
        job.detail = str(exc)
    finally:
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.add(job)
        db.commit()
        db.refresh(job)

    if job.status == "success":
        after_sales_sync(db, business_date, source)  # neutral-id bookkeeping; never raises
        db.refresh(job)
    return job


def latest_job_run(db: Session, job_name: str) -> Optional[JobRun]:
    stmt = (
        select(JobRun)
        .where(JobRun.job_name == job_name)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
