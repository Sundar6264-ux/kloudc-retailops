"""Suggested "Closed in Toast" (docs/AUTOMATION_BATCH_CONTRACT.md item 5).

An invoice upload is emailed to Toast, Toast drafts a bill, and a person
enters it in Toast and receives it - and then has to come back here and tap
"Closed in Toast". This module spots the receiving and suggests the tap; it
**never closes anything itself** (one tap on the existing
POST /api/invoices/{id}/mark-toast-closed does, by a person).

Evidence, for an open invoice (sent to WhatsApp and Toast, not closed, type
"invoice" - a return or "something else" is never received in Toast):

1. **Toast received a delivery from that vendor after the upload** - a Toast
   receiving number (e.g. "IN378") seen in an inventory upload, whose receive
   time is after the invoice upload (minus UPLOAD_GRACE) and whose vendor (by
   the items' votes, see delivery_suggestions.toast_receipts) is the invoice's
   vendor. When a vendor has several invoices, receipts are handed out
   oldest-first (an invoice already closed in Toast still takes its receipt),
   so one receiving never closes two invoices.
2. **A delivery-like stock rise** for that vendor in a nightly API snapshot
   (which can't show receiving numbers) taken after the upload - the stock-
   rise groups of delivery_suggestions, also handed out oldest-first.

A dismissed suggestion (suggestion_dismissals kind "invoice_close", key = the
submission id) stays hidden. Computed on request, cached CACHE_SECONDS against
a fingerprint of invoices, snapshots and dismissals.

Measured on a prod copy (Sep 1 - Oct 11 2026): see the contract's "As built".
"""

import datetime as dt
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.demo import hide_demo_clause
from app.models import InventorySnapshot, InvoiceSubmission, SuggestionDismissal
from app.services import delivery_suggestions as ds
from app.services.invoice_submission import can_mark_toast_closed
from app.services.suggestion_dismissals import dismissed_keys
from app.timeutil import restaurant_timezone, to_restaurant_date
from app.tenancy import tenant_local, tenant_lock

logger = logging.getLogger(__name__)

# A Toast receiving this long before the upload still counts as after it
# (on the prod copy 3 of 73 receipts came 0.2-1.8 h before the upload).
UPLOAD_GRACE = dt.timedelta(hours=3)
# Invoices of the same vendor uploaded this long before the oldest open one
# also take receipts (so an older invoice's receiving isn't reused).
HISTORY_DAYS = 14
# API snapshot pairs looked at for stock-rise evidence.
RISE_PAIRS = 14
CACHE_SECONDS = 60
SUGGESTED_TYPES = ("invoice",)


def close_suggestion_key(submission_id: int) -> str:
    return str(int(submission_id))


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def _when(value: dt.datetime) -> str:
    local = value.astimezone(restaurant_timezone())
    return f"{local.strftime('%a %b')} {local.day}, {local.strftime('%I:%M %p').lstrip('0')}"


def _open_invoices(db: Session) -> List[InvoiceSubmission]:
    rows = db.execute(
        select(InvoiceSubmission).where(
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.toast_closed_at.is_(None),
            InvoiceSubmission.submission_type.in_(SUGGESTED_TYPES),
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),
        )
    ).scalars().all()
    return [e for e in rows if can_mark_toast_closed(e)]


def _vendor_invoices(db: Session, since: dt.datetime) -> Dict[str, List[InvoiceSubmission]]:
    out: Dict[str, List[InvoiceSubmission]] = defaultdict(list)
    for e in db.execute(
        select(InvoiceSubmission)
        .where(
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.submission_type.in_(SUGGESTED_TYPES),
            InvoiceSubmission.created_at >= since,
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),
        )
        .order_by(InvoiceSubmission.created_at, InvoiceSubmission.id)
    ).scalars():
        out[ds._vendor_norm(e.vendor)].append(e)
    return out


def _hand_out(invoices: List[InvoiceSubmission], events: List[Tuple[dt.datetime, dict]]) -> Dict[int, dict]:
    """Oldest invoice first, each takes the earliest unused event at or after
    its upload (minus UPLOAD_GRACE). An invoice already closed only takes an
    event from before it was closed (plus UPLOAD_GRACE): a receiving Toast
    recorded after staff closed the bill isn't that bill's - before this
    check, an older closed invoice with no receipt of its own took the next
    one and the open invoice it belonged to got no suggestion (verify 1:
    Restaurant Depot #58, Oct 6, on the Oct 11 prod copy)."""
    events = sorted(events, key=lambda e: e[0])
    used = set()
    out = {}
    for entry in invoices:
        created = _aware(entry.created_at)
        closed = _aware(entry.toast_closed_at) if entry.toast_closed_at is not None else None
        for i, (at, event) in enumerate(events):
            if closed is not None and at > closed + UPLOAD_GRACE:
                break  # events are in time order: none later can be its own
            if i in used or at < created - UPLOAD_GRACE:
                continue
            used.add(i)
            out[entry.id] = event
            break
    return out


def compute_close_suggestions(db: Session, include_dismissed: bool = False) -> Dict[int, dict]:
    """submission id -> suggestion, for every open invoice that has evidence."""
    open_entries = _open_invoices(db)
    if not open_entries:
        return {}
    dismissed = dismissed_keys(db, "invoice_close")
    oldest = min(_aware(e.created_at) for e in open_entries)
    history_start = oldest - dt.timedelta(days=HISTORY_DAYS)
    by_vendor = _vendor_invoices(db, history_start)
    open_vendors = {ds._vendor_norm(e.vendor) for e in open_entries}

    # 1. Toast receipts (seen in inventory uploads).
    receipts: Dict[str, List[Tuple[dt.datetime, dict]]] = defaultdict(list)
    since_date = to_restaurant_date(history_start) - dt.timedelta(days=1)
    for r in ds.toast_receipts(db, since_date, received_after=history_start - UPLOAD_GRACE):
        norm = ds._vendor_norm(r["vendor"])
        if norm not in open_vendors or r["received_at"] < history_start - UPLOAD_GRACE:
            continue
        receipts[norm].append(
            (
                r["received_at"],
                {
                    "evidence": "toast_received",
                    "receipt_id": r["id"],
                    "received_at": r["received_at"].isoformat(),
                    "item_count": r["item_count"],
                    "seen_in_snapshot": r["first_seen"].isoformat(),
                    "reason": (
                        f"Toast received {r['id']} from {r['vendor']} ({r['item_count']} "
                        f"item{'s' if r['item_count'] != 1 else ''}) on {_when(r['received_at'])}, after this "
                        "invoice was uploaded. If the bill is closed in Toast, mark it closed."
                    ),
                },
            )
        )
    found: Dict[int, dict] = {}
    for norm in open_vendors:
        found.update(_hand_out(by_vendor.get(norm, []), receipts.get(norm, [])))

    # 2. Stock rises in nightly API snapshots (no receiving numbers there).
    missing = [e for e in open_entries if e.id not in found]
    if missing:
        rises: Dict[str, List[Tuple[dt.datetime, dict]]] = defaultdict(list)
        dates = [d for d in ds.snapshot_dates(db) if d >= to_restaurant_date(oldest) - dt.timedelta(days=1)]
        pairs = list(zip(dates, dates[1:]))[-RISE_PAIRS:]
        want = {ds._vendor_norm(e.vendor) for e in missing}
        api_dates = ds.api_only_dates(db, pairs[0][0]) if pairs else set()
        for prev_date, date in pairs:
            if date not in api_dates:
                continue  # a CSV upload: its receipts were used above
            pair = ds.Pair(db, prev_date, date)
            groups, _ = ds.pair_groups(db, pair, "rises")
            for g in groups:
                norm = ds._vendor_norm(g["vendor"])
                if norm not in want:
                    continue
                rises[norm].append(
                    (
                        pair.time,
                        {
                            "evidence": "stock_rise",
                            "receipt_id": None,
                            "received_at": None,
                            "item_count": g["item_count"],
                            "seen_in_snapshot": date.isoformat(),
                            "reason": (
                                f"{g['item_count']} of {g['vendor']}'s items rose by more than sales explain in the "
                                f"{date.strftime('%b')} {date.day} snapshot, after this invoice was uploaded. If the "
                                "bill is closed in Toast, mark it closed."
                            ),
                        },
                    )
                )
        for norm in want:
            open_of_vendor = [e for e in by_vendor.get(norm, []) if e.id not in found and e.toast_closed_at is None]
            found.update(_hand_out(open_of_vendor, rises.get(norm, [])))

    out = {}
    for entry in open_entries:
        suggestion = found.get(entry.id)
        if suggestion is None:
            continue
        key = close_suggestion_key(entry.id)
        if key in dismissed and not include_dismissed:
            continue
        out[entry.id] = {**suggestion, "key": key, "dismissed": key in dismissed}
    return out


_cache_lock = tenant_lock("invoice_close_suggestions._cache_lock")
_cache: Dict[tuple, Tuple[float, Dict[int, dict]]] = tenant_local("invoice_close_suggestions._cache", dict)


def _fingerprint(db: Session) -> tuple:
    return (
        db.execute(
            select(
                func.max(InvoiceSubmission.id),
                func.count(InvoiceSubmission.toast_closed_at),
                func.count(InvoiceSubmission.deleted_at),
                func.sum(cast(InvoiceSubmission.whatsapp_sent, Integer)),
                func.sum(cast(InvoiceSubmission.toast_email_sent, Integer)),
            )
        ).one(),
        db.execute(select(func.max(InventorySnapshot.id), func.max(InventorySnapshot.snapshot_date))).one(),
        db.execute(
            select(func.max(SuggestionDismissal.id), func.count(SuggestionDismissal.id)).where(
                SuggestionDismissal.kind == "invoice_close"
            )
        ).one(),
    )


def get_close_suggestions(db: Session) -> Dict[int, dict]:
    """Cached compute_close_suggestions (dismissed ones left out). Never raises:
    a failure here must not break the invoice list."""
    try:
        key = tuple(tuple(x) for x in _fingerprint(db))
        now = time.monotonic()
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        result = compute_close_suggestions(db)
        with _cache_lock:
            _cache.clear()
            _cache[key] = (now + CACHE_SECONDS, result)
        return result
    except Exception:  # pragma: no cover - logged, list still works
        logger.exception("Close-in-Toast suggestions failed")
        return {}


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
