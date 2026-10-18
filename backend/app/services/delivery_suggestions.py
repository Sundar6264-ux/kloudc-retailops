"""Suggested deliveries (docs/AUTOMATION_BATCH_CONTRACT.md item 4).

Delivery Review used to start only from the Overview upload, with a person
picking which vendors delivered. This module finds the deliveries itself, by
comparing consecutive inventory snapshots (CSV uploads or the nightly API
snapshot - any two snapshot dates in a row), and shows them on Delivery Review
as a "Suggested" list per vendor. **It never writes anything**: confirming a
suggestion goes through the same `POST /api/reconciliation/delivery-confirm`
as a manual review, so the purchase_log rows are exactly what a manual
confirm writes.

Two kinds of evidence, measured against what staff actually confirmed on a
prod copy (14 days, Sep 27 - Oct 11 2026: 32 confirmed vendor-days, 584
confirmed lines):

1. **Toast received it** (`evidence = "toast_received"`). The store receives
   every invoice in Toast, and Toast's export then carries the receive time
   (`inventory_last_received`) and Toast's receiving number (`last_received_from`,
   e.g. "IN378") per item. An item whose receive time is newer than in the
   previous snapshot (and after the previous snapshot was taken) was received
   in between. Items are grouped by receiving number and the group's vendor is
   the vendor most of its items list (a multi-vendor item counts 1/n for each)
   - so an item Toast lists under another vendor still lands with the vendor
   that actually delivered it. This alone found all 32 confirmed vendor-days.
2. **Stock rose** (`evidence = "stock_rise"`): an item's count rose by more
   than sales explain - new count - max(previous count, 0) + units sold in
   between - by at least RISE_MIN_UNITS. On its own this is mostly recounts
   and shelf/Container moves (only 1 of 56 such lines was a confirmed
   delivery), so a rise only becomes a suggestion when there's more to it:
   - it is attached (unticked) to a Toast-received group of the same vendor
     and day, or
   - RISE_MIN_ITEMS or more of one vendor's items rose together, or the
     vendor uploaded an invoice in the window (INVOICE_EVIDENCE_DAYS before
     the previous snapshot up to the current one).
   Left out: a shelf row whose Container twin dropped by at least
   TRANSFER_MATCH_SHARE of the rise (a transfer, not a delivery), a count
   that was negative before (counted from 0, so a recount up from -5 doesn't
   read as +5 delivered), an item with no count (untracked) on either side,
   an API snapshot row Toast reports as out of stock (its real count is
   unknown), a weighed item whose rise is under WEIGHED_RISE_SHARE of its
   previous weight, kitchen-made groups (the store pack's kitchen_groups),
   and a multi-vendor item when no one vendor has more evidence that day.

Quantity: count now - count before + units sold between the two snapshots.
Snapshots are taken before the store opens (snapshot D = the opening count of
business day D), so "sold between" = sales on business days [previous, current)
from the synced `orders` table (top-level, non-voided lines). If a day in that
range hasn't synced yet the group says `sales_complete: false` (quantities may
be low until it syncs).

A vendor-day that already has a purchase_log entry is "handled" and not
suggested again: a Delivery Review confirm on the snapshot date, or a manual
log on that day or the day before. A dismissed suggestion (suggestion_dismissals
kind "delivery", key `delivery_key()`) stays hidden.

Everything is computed on request (no job), cached for CACHE_SECONDS against a
fingerprint of the snapshots, purchase log, invoices and dismissals.
"""

import datetime as dt
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dbcompat import fsum, group_order
from app.demo import hide_demo_clause
from app.models import (
    InventorySnapshot,
    InvoiceSubmission,
    JobRun,
    Order,
    PurchaseLogEntry,
    SuggestionDismissal,
)
from app.services.delivery_review import QTY_SWING_THRESHOLD
from app.services.sales_sync import JOB_NAME as SALES_SYNC_JOB
from app.services.suggestion_dismissals import dismissed_keys
from app.services.supplier_projection import _split_suppliers, _supplier_item_id_for, store_container_pairs
from app.store import settings_view as sv
from app.timeutil import restaurant_timezone, to_restaurant_date, utcnow

logger = logging.getLogger(__name__)

# A rise smaller than this isn't a suggestion (same noise floor as the manual
# Delivery Review's QTY_SWING_THRESHOLD).
RISE_MIN_UNITS = float(QTY_SWING_THRESHOLD)
# Without a Toast receiving record, this many of one vendor's items must rise
# together (a delivery is many items; a recount is one).
RISE_MIN_ITEMS = 3
# A weighed item (fractional count) must also rise by this share of its
# previous weight - re-weighing moves weighed counts a few pounds either way.
WEIGHED_RISE_SHARE = 0.2
# A shelf rise is a Container -> shelf move when the Container twin dropped by
# at least this share of the rise.
TRANSFER_MATCH_SHARE = 0.5
# A Toast receive time this much before the previous snapshot still counts as
# "after it" (clock differences between Toast and our snapshot time).
RECEIPT_GRACE = dt.timedelta(minutes=30)
# An invoice uploaded for the vendor from this many days before the previous
# snapshot up to the current one supports a stock-rise group.
INVOICE_EVIDENCE_DAYS = 3
# A tie between vendors on a receipt goes to the one whose invoice upload in
# the last TIE_BREAK_DAYS came last before the receiving (+ TIE_BREAK_SLACK).
TIE_BREAK_DAYS = 10
TIE_BREAK_SLACK = dt.timedelta(hours=3)
# Default / maximum look-back, in snapshot pairs ending on the latest snapshot.
DEFAULT_DAYS = 3
MAX_DAYS = 31
# Kitchen-made groups (their counts are production, not deliveries): the
# store pack's menu.kitchen_groups.
# A snapshot whose upload time isn't on its own date (backfills, odd uploads)
# is taken to be at this store time - the POS closeout hour (store setting
# BUSINESS_DAY_CUTOFF_HOUR).
CACHE_SECONDS = 60

DELIVERY_REVIEW_NOTE_PREFIX = "Confirmed via Delivery Review"
# inventory_snapshots.source_filename of an API-built snapshot (item 1).
API_SOURCE_PREFIX = "api:"

_SNAPSHOT_COLUMNS = (
    InventorySnapshot.item_id,
    InventorySnapshot.name,
    InventorySnapshot.category,
    InventorySnapshot.category_group,
    InventorySnapshot.inventory_quantity,
    InventorySnapshot.supplier,
    InventorySnapshot.supplier_item_id,
    InventorySnapshot.cost,
    InventorySnapshot.price,
    InventorySnapshot.inventory_last_received,
    InventorySnapshot.last_received_from,
    InventorySnapshot.uploaded_at,
    InventorySnapshot.source_filename,
)
# Added by migration 0048 (API snapshots); read when the model has it.
_API_OOS = getattr(InventorySnapshot, "api_out_of_stock", None)


def delivery_key(received_date: dt.date, vendor: Optional[str], receipt_id: Optional[str] = None) -> str:
    """The dismissal key of a suggested delivery: one vendor on one snapshot
    date (a group with no vendor is keyed by its Toast receiving number)."""
    if vendor:
        return f"{received_date.isoformat()}|{vendor.strip().lower()}"
    return f"{received_date.isoformat()}|receipt:{receipt_id or '?'}"


# --- Small helpers -------------------------------------------------------------------------------


_FRACTION = re.compile(r"\.(\d+)")


@lru_cache(maxsize=20000)
def parse_toast_time(raw: Optional[str]) -> Optional[dt.datetime]:
    """Toast's `inventory_last_received` ("2026-10-10T14:37:21.828895Z") as an
    aware UTC datetime; None when blank or unreadable."""
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    # Python 3.9's fromisoformat takes exactly 3 or 6 fraction digits.
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _is_weighed(*quantities: Optional[float]) -> bool:
    return any(q is not None and abs(q - round(q)) > 1e-6 for q in quantities)


def _vendor_norm(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def snapshot_dates(db: Session) -> List[dt.date]:
    return list(
        db.execute(select(InventorySnapshot.snapshot_date).distinct().order_by(InventorySnapshot.snapshot_date))
        .scalars()
        .all()
    )


def load_snapshot(db: Session, day: dt.date) -> Dict[str, object]:
    """item_id -> a light row (only the columns used here) for one snapshot date."""
    cols = _SNAPSHOT_COLUMNS + ((_API_OOS,) if _API_OOS is not None else ())
    rows = db.execute(select(*cols).where(InventorySnapshot.snapshot_date == day)).all()
    return {row.item_id: row for row in rows}


def api_only_dates(db: Session, since: dt.date) -> Set[dt.date]:
    """Snapshot dates (>= since) built only from the Toast API - no CSV upload,
    so no new Toast receiving records on them."""
    rows = db.execute(
        select(InventorySnapshot.snapshot_date, InventorySnapshot.source_filename)
        .where(InventorySnapshot.snapshot_date >= since)
        .distinct()
    ).all()
    has_csv: Dict[dt.date, bool] = defaultdict(bool)
    for day, source in rows:
        has_csv[day] = has_csv[day] or not (source or "").startswith(API_SOURCE_PREFIX)
    return {day for day, csv in has_csv.items() if not csv}


def snapshot_time(day: dt.date, rows: Dict[str, object]) -> dt.datetime:
    """When the snapshot was taken (UTC): its upload time when that is on the
    snapshot's own store date and it isn't a backfill, else the closeout hour."""
    tz = restaurant_timezone()
    fallback = dt.datetime.combine(day, dt.time(sv.business_day_cutoff_hour()), tz).astimezone(dt.timezone.utc)
    times = [
        r.uploaded_at
        for r in rows.values()
        if r.uploaded_at is not None and not (r.source_filename or "").startswith("backfill:")
    ]
    if not times:
        return fallback
    latest = max(times)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.timezone.utc)
    return latest if to_restaurant_date(latest) == day else fallback


def sold_between(db: Session, start: dt.date, end: dt.date) -> Dict[str, float]:
    """Units sold per item on business days [start, end) - top-level lines
    only (a modifier is not an item sold), voided lines left out."""
    if end <= start:
        return {}
    rows = db.execute(
        select(Order.item_guid, fsum(Order.quantity))
        .where(
            Order.business_date >= start,
            Order.business_date < end,
            Order.voided.is_(False),
            Order.top_level(),
            Order.item_guid.is_not(None),
        )
        .group_by(Order.item_guid)
        .order_by(*group_order(Order.item_guid))
    ).all()
    return {item: float(qty or 0) for item, qty in rows}


def sales_synced(db: Session, start: dt.date, end: dt.date) -> bool:
    """True when every business day in [start, end) has a successful sales sync."""
    if end <= start:
        return True
    synced = set(
        db.execute(
            select(JobRun.business_date).where(
                JobRun.job_name == SALES_SYNC_JOB,
                JobRun.status == "success",
                JobRun.business_date >= start,
                JobRun.business_date < end,
            )
        ).scalars()
    )
    day = start
    while day < end:
        if day not in synced:
            return False
        day += dt.timedelta(days=1)
    return True


# --- One snapshot pair ---------------------------------------------------------------------------


class Pair:
    """Two consecutive snapshots and what sold between them."""

    def __init__(self, db: Session, prev_date: dt.date, date: dt.date, prev_rows=None, rows=None):
        self.prev_date = prev_date
        self.date = date
        self.prev = prev_rows if prev_rows is not None else load_snapshot(db, prev_date)
        self.cur = rows if rows is not None else load_snapshot(db, date)
        self.prev_time = snapshot_time(prev_date, self.prev)
        self.time = snapshot_time(date, self.cur)
        self.sold = sold_between(db, prev_date, date)
        self.sales_complete = sales_synced(db, prev_date, date)
        # A CSV upload carries Toast's receiving records; an API-built
        # snapshot ("api:" source, migration 0048) only carries them forward
        # from the last CSV, so a delivery can only show as a stock rise.
        self.has_receipts = any(
            not (r.source_filename or "").startswith(API_SOURCE_PREFIX) for r in self.cur.values()
        )


def _line(pair: Pair, item_id: str, row, prev, evidence: str, net: Optional[float], **extra) -> dict:
    prev_qty = prev.inventory_quantity if prev is not None else None
    today_qty = row.inventory_quantity
    delta = None if today_qty is None else today_qty - (prev_qty or 0)
    sold = pair.sold.get(item_id, 0.0)
    line = {
        "item_id": item_id,
        "name": row.name,
        "category": row.category,
        "prev_qty": _r(prev_qty),
        "today_qty": _r(today_qty),
        "delta": _r(delta),
        "sold_between": _r(sold),
        # Same name as the manual review's column (units sold, here between
        # the two snapshots) so the same row component reads both.
        "sold_today": _r(sold),
        "is_new": prev is None,
        "suggested_qty": _r(net) if net is not None and net > 0 else None,
        "cost": row.cost,
        "evidence": evidence,
        "listed_vendors": _split_suppliers(row.supplier),
        "is_container": row.price is None and not (row.supplier or "").strip(),
        "weighed": _is_weighed(prev_qty, today_qty),
        "untracked": today_qty is None,
        "_row": row,
    }
    line.update(extra)
    return line


def _received_lines(pair: Pair) -> List[dict]:
    """Items Toast received between the two snapshots (newer receive time)."""
    lines = []
    floor = pair.prev_time - RECEIPT_GRACE
    ceiling = pair.time + dt.timedelta(hours=1)
    for item_id, row in pair.cur.items():
        received_at = parse_toast_time(row.inventory_last_received)
        if received_at is None or not (floor < received_at <= ceiling):
            continue
        prev = pair.prev.get(item_id)
        prev_received = parse_toast_time(prev.inventory_last_received) if prev is not None else None
        if prev_received is not None and received_at <= prev_received:
            continue
        api_oos = bool(getattr(row, "api_out_of_stock", None))
        net = None
        if row.inventory_quantity is not None and not api_oos:
            if prev is None or prev.inventory_quantity is None:
                base = 0.0
            else:
                base = prev.inventory_quantity
            net = row.inventory_quantity - base + pair.sold.get(item_id, 0.0)
        receipt = (row.last_received_from or "").strip() or None
        if receipt is None:
            # A receive time with no receiving number is a count edit, not an
            # invoice (all 9 such single items on the prod copy were noise).
            continue
        lines.append(
            _line(
                pair,
                item_id,
                row,
                prev,
                "toast_received",
                net,
                receipt_id=receipt,
                received_at=received_at,
            )
        )
    return lines


def _rise_lines(pair: Pair, received_ids: Set[str]) -> Tuple[List[dict], Counter]:
    """Items whose count rose by more than sales explain, without a Toast
    receiving record. Returns (lines, skipped counts by reason)."""
    skipped: Counter = Counter()
    # Shelf row -> its Container twin (paired by name, see store_container_pairs).
    container_of = {store_id: cont_id for store_id, _, cont_id, _ in store_container_pairs(pair.cur)}
    from app.store import pack as store_pack

    kitchen_groups = store_pack.kitchen_groups()
    lines = []
    for item_id, row in pair.cur.items():
        if item_id in received_ids:
            continue
        if (row.category_group or "").strip().upper() in kitchen_groups:
            continue
        if not _split_suppliers(row.supplier):
            continue  # no vendor (Container rows, kitchen items): nothing to suggest
        qty = row.inventory_quantity
        if qty is None or bool(getattr(row, "api_out_of_stock", None)):
            continue
        prev = pair.prev.get(item_id)
        if prev is not None and prev.inventory_quantity is None:
            continue  # untracked before: can't diff
        base = max(prev.inventory_quantity, 0.0) if prev is not None else 0.0
        net = qty - base + pair.sold.get(item_id, 0.0)
        if net < RISE_MIN_UNITS:
            continue
        if _is_weighed(qty, prev.inventory_quantity if prev is not None else None) and net < WEIGHED_RISE_SHARE * base:
            skipped["weighed_noise"] += 1
            continue
        cont_id = container_of.get(item_id)
        if cont_id is not None:
            before, after = pair.prev.get(cont_id), pair.cur.get(cont_id)
            if (
                before is not None
                and after is not None
                and before.inventory_quantity is not None
                and after.inventory_quantity is not None
            ):
                drop = before.inventory_quantity - after.inventory_quantity
                if drop > 0 and drop >= TRANSFER_MATCH_SHARE * net:
                    skipped["container_transfer"] += 1
                    continue
        lines.append(_line(pair, item_id, row, prev, "stock_rise", net))
    return lines, skipped


def _pick_vendor(
    votes: Counter, last_upload: Optional[Dict[str, dt.datetime]] = None, at: Optional[dt.datetime] = None
) -> Optional[str]:
    """The vendor most of a receipt's items list. On a tie, the vendor whose
    last invoice upload before `at` is the latest (the receiving in Toast
    follows the invoice upload), then the alphabetically first."""
    if not votes:
        return None
    best = max(votes.values())
    top = sorted((v for v, n in votes.items() if abs(n - best) < 1e-9), key=lambda v: v.lower())
    if len(top) == 1 or not last_upload:
        return top[0]
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)

    def latest(v: str) -> dt.datetime:
        times = [t for t in last_upload.get(_vendor_norm(v), ()) if at is None or t <= at + TIE_BREAK_SLACK]
        return max(times) if times else floor

    return max(top, key=lambda v: (latest(v), [-ord(c) for c in v.lower()]))


def _invoice_uploads(db: Session, start: dt.datetime, end: dt.datetime) -> Dict[str, List[dt.datetime]]:
    """Vendor (lowercased) -> invoice upload times (UTC) in [start, end]."""
    out: Dict[str, List[dt.datetime]] = defaultdict(list)
    for vendor, created in db.execute(
        select(InvoiceSubmission.vendor, InvoiceSubmission.created_at).where(
            InvoiceSubmission.created_at >= start,
            InvoiceSubmission.created_at <= end,
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.submission_type == "invoice",
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),
        )
    ):
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        out[_vendor_norm(vendor)].append(created)
    return out


def pair_groups(db: Session, pair: Pair, mode: Optional[str] = None) -> Tuple[List[dict], Counter]:
    """Every suggested delivery group for one snapshot pair, before the
    handled / dismissed filters. Returns (groups, skipped counts).

    mode "receipts" (the pair's newer snapshot is a CSV upload) uses Toast's
    receiving records only - a rise without one is a recount or a move, since
    receiving in Toast is what raises the count. mode "rises" (an API-built
    snapshot, which can't show new receiving records) uses stock rises. The
    default picks by the snapshot's source; tests and the measurement pass it."""
    if mode is None:
        mode = "receipts" if pair.has_receipts else "rises"
    uploads = _invoice_uploads(db, pair.prev_time - dt.timedelta(days=TIE_BREAK_DAYS), pair.time)
    groups: Dict[Tuple[str, str], dict] = {}
    skipped: Counter = Counter()

    if mode == "receipts":
        # Toast receipts -> a vendor each (votes of the items' listed vendors).
        by_receipt: Dict[str, List[dict]] = defaultdict(list)
        for line in _received_lines(pair):
            by_receipt[line["receipt_id"]].append(line)
        for receipt_id, lines in by_receipt.items():
            votes: Counter = Counter()
            for line in lines:
                listed = sorted(set(line["listed_vendors"]))
                for vendor in listed:
                    votes[vendor] += 1.0 / len(listed)
            received_at = min(l["received_at"] for l in lines)
            vendor = _pick_vendor(votes, uploads, received_at)
            if vendor is None:
                skipped["no_vendor"] += len(lines)
                continue
            group = groups.setdefault(
                ("v", _vendor_norm(vendor)),
                {"vendor": vendor, "evidence": "toast_received", "receipts": [], "lines": []},
            )
            group["receipts"].append(
                {
                    "id": receipt_id,
                    "received_at": received_at,
                    "item_count": len(lines),
                    "votes": [{"vendor": v, "items": round(n, 1)} for v, n in votes.most_common(3)],
                }
            )
            group["lines"].extend(lines)
    else:
        rises, skipped = _rise_lines(pair, set())
        # One vendor per rising item: its only vendor, else the listed vendor
        # with the most rising items that day (a delivery is a cluster).
        rise_votes: Counter = Counter()
        for line in rises:
            listed = set(_vendor_norm(v) for v in line["listed_vendors"])
            for vendor in listed:
                rise_votes[vendor] += 1.0 if len(listed) == 1 else 0.5
        invoice_vendors = {
            v
            for v, times in uploads.items()
            if any(t >= pair.prev_time - dt.timedelta(days=INVOICE_EVIDENCE_DAYS) for t in times)
        }
        rise_groups: Dict[str, dict] = {}
        for line in rises:
            listed = sorted(set(line["listed_vendors"]), key=str.lower)
            if len(listed) == 1:
                vendor = listed[0]
            else:
                scored = sorted(
                    (rise_votes[_vendor_norm(v)] + (0.25 if _vendor_norm(v) in invoice_vendors else 0), v)
                    for v in listed
                )
                if scored[-1][0] == scored[-2][0]:
                    skipped["ambiguous_vendor"] += 1
                    continue
                vendor = scored[-1][1]
            norm = _vendor_norm(vendor)
            rg = rise_groups.setdefault(norm, {"vendor": vendor, "evidence": "stock_rise", "receipts": [], "lines": []})
            rg["lines"].append(line)
        for norm, rg in rise_groups.items():
            if len(rg["lines"]) >= RISE_MIN_ITEMS or norm in invoice_vendors:
                rg["invoice_uploaded"] = norm in invoice_vendors
                groups[("v", norm)] = rg
            else:
                skipped["too_few_items"] += len(rg["lines"])

    return [_finish_group(pair, group) for group in groups.values()], skipped


def _finish_group(pair: Pair, group: dict) -> dict:
    vendor = group["vendor"]
    lines = group["lines"]
    for line in lines:
        row = line.pop("_row")
        line["supplier_item_id"] = _supplier_item_id_for(row, vendor) if vendor else None
        line["vendor_listed"] = bool(vendor) and _vendor_norm(vendor) in {_vendor_norm(v) for v in line["listed_vendors"]}
        if "preselected" not in line:
            line["preselected"] = line["suggested_qty"] is not None
        if "note" not in line:
            if line["untracked"]:
                line["note"] = "Toast received it but keeps no count - enter the quantity"
            elif line["evidence"] == "toast_received" and line["suggested_qty"] is None:
                line["note"] = "Toast received it, but the count didn't rise - check the quantity"
            elif not line["vendor_listed"] and vendor:
                listed = ", ".join(line["listed_vendors"]) or "no vendor"
                line["note"] = f"Toast lists {listed} for this item"
            else:
                line["note"] = None
        received_at = line.get("received_at")
        if isinstance(received_at, dt.datetime):
            line["received_at"] = received_at.isoformat()
    lines.sort(
        key=lambda l: (
            l["evidence"] != "toast_received",
            not l["preselected"],
            -(l["suggested_qty"] or 0),
            (l["name"] or "").lower(),
        )
    )
    receipts = sorted(group["receipts"], key=lambda r: r["received_at"])
    tz = restaurant_timezone()
    if group["evidence"] == "toast_received":
        first = receipts[0]["received_at"].astimezone(tz)
        ids = ", ".join(r["id"] for r in receipts if r["id"]) or "no receiving number"
        n_received = sum(1 for l in lines if l["evidence"] == "toast_received")
        reason = (
            f"Toast received {n_received} item{'s' if n_received != 1 else ''} on "
            f"{first.strftime('%a %b')} {first.day}, {first.strftime('%I:%M %p').lstrip('0')} ({ids})."
        )
    else:
        n = len(lines)
        reason = (
            f"{n} item{'s' if n != 1 else ''} rose by more than sales explain between the "
            f"{pair.prev_date.strftime('%b')} {pair.prev_date.day} and {pair.date.strftime('%b')} {pair.date.day} "
            "snapshots."
        )
        if not pair.has_receipts:
            reason += " The nightly Toast snapshot can't show Toast's receiving records; the inventory upload does."
        if group.get("invoice_uploaded"):
            reason += " An invoice was uploaded for this vendor around then."
    for r in receipts:
        r["received_at"] = r["received_at"].isoformat()
    preselected = [l for l in lines if l["preselected"]]
    return {
        "key": delivery_key(pair.date, vendor, receipts[0]["id"] if receipts else None),
        "vendor": vendor,
        "received_date": pair.date.isoformat(),
        "prev_date": pair.prev_date.isoformat(),
        "evidence": group["evidence"],
        "reason": reason,
        "toast_receipts": receipts,
        "item_count": len(lines),
        "preselected_count": len(preselected),
        "suggested_units": _r(sum(l["suggested_qty"] or 0 for l in preselected)),
        "sales_complete": pair.sales_complete,
        "items": lines,
    }


# --- Handled / dismissed, and the whole list ----------------------------------------------------


def _handled(db: Session, start: dt.date, end: dt.date) -> Dict[Tuple[dt.date, str], Set[str]]:
    """(snapshot date, vendor lowercased) -> item ids already in the purchase
    log for that delivery: a Delivery Review confirm on the snapshot date, or
    a manual log on that day or the day before."""
    out: Dict[Tuple[dt.date, str], Set[str]] = defaultdict(set)
    rows = db.execute(
        select(PurchaseLogEntry.item_id, PurchaseLogEntry.supplier, PurchaseLogEntry.received_date, PurchaseLogEntry.notes)
        .where(PurchaseLogEntry.received_date >= start - dt.timedelta(days=1), PurchaseLogEntry.received_date <= end + dt.timedelta(days=1))
    ).all()
    for item_id, supplier, received, notes in rows:
        norm = _vendor_norm(supplier)
        out[(received, norm)].add(item_id)
        if not (notes or "").startswith(DELIVERY_REVIEW_NOTE_PREFIX):
            out[(received + dt.timedelta(days=1), norm)].add(item_id)
    return out


def _pairs_for(db: Session, days: int, through: Optional[dt.date] = None) -> List[Tuple[dt.date, dt.date]]:
    dates = snapshot_dates(db)
    if through is not None:
        dates = [d for d in dates if d <= through]
    pairs = list(zip(dates, dates[1:]))
    return pairs[-days:] if days > 0 else []


def compute_suggestions(
    db: Session,
    days: int = DEFAULT_DAYS,
    through: Optional[dt.date] = None,
    include_dismissed: bool = False,
    include_handled: bool = False,
) -> dict:
    """Suggested deliveries for the last `days` snapshot pairs (newest first)."""
    days = max(1, min(int(days), MAX_DAYS))
    pairs = _pairs_for(db, days, through)
    dismissed = dismissed_keys(db, "delivery")
    counts: Counter = Counter()
    groups: List[dict] = []
    if pairs:
        handled = _handled(db, pairs[0][1], pairs[-1][1])
        cache: Dict[dt.date, dict] = {}
        for prev_date, date in pairs:
            prev_rows = cache.get(prev_date) or load_snapshot(db, prev_date)
            rows = cache.get(date) or load_snapshot(db, date)
            cache = {date: rows}
            pair = Pair(db, prev_date, date, prev_rows, rows)
            found, skipped = pair_groups(db, pair)
            counts.update({f"skipped_{k}": v for k, v in skipped.items()})
            for group in found:
                norm = _vendor_norm(group["vendor"])
                logged = handled.get((date, norm), set()) if group["vendor"] else set()
                group["handled"] = bool(logged)
                group["dismissed"] = group["key"] in dismissed
                if group["handled"] and not include_handled:
                    counts["handled"] += 1
                    continue
                if group["dismissed"] and not include_dismissed:
                    counts["dismissed"] += 1
                    continue
                if logged:
                    group["items"] = [l for l in group["items"] if l["item_id"] not in logged]
                if not group["items"]:
                    continue
                groups.append(group)
    groups.sort(
        key=lambda g: (
            g["received_date"],
            g["evidence"] == "toast_received",
            g["preselected_count"],
        ),
        reverse=True,
    )
    return {
        "generated_at": utcnow().isoformat(),
        "through": pairs[-1][1].isoformat() if pairs else None,
        "days": days,
        "pairs": [{"prev_date": p.isoformat(), "date": d.isoformat()} for p, d in pairs],
        "count": len(groups),
        "groups": groups,
        "counts": dict(counts),
        "rules": {
            "rise_min_units": RISE_MIN_UNITS,
            "rise_min_items": RISE_MIN_ITEMS,
            "weighed_rise_share": WEIGHED_RISE_SHARE,
            "transfer_match_share": TRANSFER_MATCH_SHARE,
        },
    }


# --- Cached entry point --------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Dict[tuple, Tuple[float, dict]] = {}


def _fingerprint(db: Session) -> tuple:
    latest = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar()
    latest_upload = (
        db.execute(select(func.max(InventorySnapshot.uploaded_at)).where(InventorySnapshot.snapshot_date == latest)).scalar()
        if latest
        else None
    )
    return (
        latest,
        latest_upload,
        db.execute(select(func.max(InventorySnapshot.id))).scalar(),
        db.execute(select(func.max(PurchaseLogEntry.id), func.count(PurchaseLogEntry.id))).one(),
        db.execute(select(func.max(InvoiceSubmission.id))).scalar(),
        db.execute(
            select(func.max(SuggestionDismissal.id), func.count(SuggestionDismissal.id)).where(
                SuggestionDismissal.kind == "delivery"
            )
        ).one(),
        db.execute(
            select(func.max(JobRun.id)).where(JobRun.job_name == SALES_SYNC_JOB, JobRun.status == "success")
        ).scalar(),
    )


def get_suggested_deliveries(db: Session, days: int = DEFAULT_DAYS, include_dismissed: bool = False) -> dict:
    key = (tuple(_fingerprint(db)), days, include_dismissed)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    result = compute_suggestions(db, days=days, include_dismissed=include_dismissed)
    with _cache_lock:
        _cache.clear()
        _cache[key] = (now + CACHE_SECONDS, result)
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- Toast receipts over a date range (invoice close + schedule suggestions) --------------------


def toast_receipts(db: Session, since: dt.date, received_after: Optional[dt.datetime] = None) -> List[dict]:
    """Every Toast receiving seen in snapshots dated `since` or later, one per
    receiving number: {id, received_at (UTC), vendor (by the items' votes, as
    above), item_count, first_seen (the first snapshot date showing it)}.
    Receipts whose items list no vendor are left out. `received_after` skips
    older receive times in SQL (Toast's ISO text sorts by time), which keeps
    this fast - most items were last received long ago."""
    conditions = [
        InventorySnapshot.snapshot_date >= since,
        InventorySnapshot.inventory_last_received.is_not(None),
        InventorySnapshot.inventory_last_received != "",
    ]
    if received_after is not None:
        floor = (received_after.astimezone(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        conditions.append(InventorySnapshot.inventory_last_received >= floor)
    # No GROUP BY in SQL: SQLite would walk the item_id index over the whole
    # table for it (~2 s on prod); this reads the date range (~30 ms).
    first_seen_by: Dict[Tuple[str, str], tuple] = {}
    for item_id, raw_time, receipt_id, supplier, day in db.execute(
        select(
            InventorySnapshot.item_id,
            InventorySnapshot.inventory_last_received,
            InventorySnapshot.last_received_from,
            InventorySnapshot.supplier,
            InventorySnapshot.snapshot_date,
        ).where(*conditions)
    ):
        key = (item_id, raw_time)
        seen = first_seen_by.get(key)
        if seen is None or day < seen[4]:
            first_seen_by[key] = (item_id, raw_time, receipt_id, supplier, day)
    rows = list(first_seen_by.values())
    receipts: Dict[str, dict] = {}
    for item_id, raw_time, receipt_id, supplier, first_seen in rows:
        received_at = parse_toast_time(raw_time)
        if received_at is None or (received_after is not None and received_at < received_after):
            continue
        key = (receipt_id or "").strip() or "?" + received_at.strftime("%Y-%m-%dT%H:%M")
        entry = receipts.setdefault(
            key, {"id": None if key.startswith("?") else key, "received_at": received_at, "items": set(), "votes": Counter(), "first_seen": first_seen}
        )
        entry["received_at"] = min(entry["received_at"], received_at)
        entry["first_seen"] = min(entry["first_seen"], first_seen)
        if item_id in entry["items"]:
            continue
        entry["items"].add(item_id)
        listed = sorted(set(_split_suppliers(supplier)))
        for vendor in listed:
            entry["votes"][vendor] += 1.0 / len(listed)
    tz_floor = dt.datetime.combine(since, dt.time.min, restaurant_timezone()) - dt.timedelta(days=TIE_BREAK_DAYS)
    uploads = _invoice_uploads(db, tz_floor, utcnow())
    out = []
    for entry in receipts.values():
        if entry["id"] is None:
            continue  # a count edit, not an invoice (see _received_lines)
        vendor = _pick_vendor(entry["votes"], uploads, entry["received_at"])
        if vendor is None:
            continue
        out.append(
            {
                "id": entry["id"],
                "received_at": entry["received_at"],
                "vendor": vendor,
                "item_count": len(entry["items"]),
                "vendor_items": round(entry["votes"][vendor], 1),
                "first_seen": entry["first_seen"],
            }
        )
    out.sort(key=lambda r: r["received_at"])
    return out


def __getattr__(name):
    # The old constant: the store pack's kitchen_groups (read at call time).
    if name == "KITCHEN_GROUPS":
        from app.store import pack as _pack

        return set(_pack.kitchen_groups())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
