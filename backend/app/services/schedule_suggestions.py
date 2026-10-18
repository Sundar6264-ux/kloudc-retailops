"""Suggested vendor delivery schedules (automation batch item 7, widened Oct 14 - X2).

The Vendor Order Calendar runs on one manual input per vendor - delivery
weekday(s) + order lead days (vendor_schedule.py). This module reads the
weekdays off what already happened and offers "Raja Foods usually delivers on
Thu. Add schedule?" with accept / edit / dismiss, plus "Add all". It **never
changes a schedule**: Accept / Edit go through POST /api/vendor-calendar/
schedules and Add all through `accept_all` - both refuse a vendor that already
has one (any spelling, paused included), so a schedule an admin set is never
overwritten. A vendor that already has a schedule only gets a read-only "check"
line when its recent deliveries fall on other days.

**Evidence (X2: every source, merged into deliveries).** Over the last
LOOKBACK_DAYS, per vendor (names matched by `vendor_key`, see `_Names`):
- invoice uploads (type "invoice"): staff upload the invoice when the delivery
  arrives - the delivery day itself (an "anchor");
- manual purchase-log entries: their received date (an anchor);
- Delivery Review confirms: the snapshot date minus one day (snapshot D is the
  opening count of day D, so the delivery was the day before);
- Toast receiving records: every daily snapshot carries each item's last
  receive time + receiving number, so the snapshots together are Toast's
  receiving history; one receiving = one vendor by the items' votes (";" split,
  delivery_suggestions.toast_receipts) - received in Toast on or after the
  delivery day;
- suggested deliveries' stock rises on API-only snapshot days (the newer
  snapshot's date minus one).
A Toast receiving / confirm / stock rise from ATTACH_BEFORE_DAYS before to
ATTACH_DAYS after an anchor is the same delivery (the nearest anchor); the rest
make deliveries of their own (one per run of days a day apart). A delivery's
day is its earliest piece of evidence; a delivery seen only as Toast receivings
is moved back by the vendor's usual receiving lag (`_receipt_lag`: the median
gap after its invoice uploads).

**Pattern.** A weekday counts with MIN_PER_WEEKDAY+ deliveries and
MIN_WEEKDAY_SHARE+ of all (MIN_PER_WEEKDAY_IF_SEVERAL+ each when two or more
qualify and some have that many). A suggestion needs MIN_COVERED+ deliveries on
the chosen days, spanning MIN_SPAN_DAYS+, covering MIN_COVERAGE+ of all.
Confidence: high = HIGH_MIN_COVERED+ and HIGH_MIN_COVERAGE+, medium =
MEDIUM_MIN_COVERED+ and MEDIUM_MIN_COVERAGE+, else low; score = coverage x
min(1, covered / FULL_SUPPORT); the list is sorted by confidence, then score. Two or more days
that each have fewer than MIN_PER_WEEKDAY_IF_SEVERAL are always low.

**Order day.** Lead days = the median gap from a placed order (status
"placed", outside-placed W1 orders included; one tied to a delivery uses that
date) to the next delivery within MAX_LEAD_GAP_DAYS. With no orders to measure,
`lead_days_source` is "default" (DEFAULT_LEAD_DAYS) and the clients say the
order day is the owner's to set.

**Cadence (Oct 14).** A schedule repeats every week. A vendor whose
deliveries are usually IRREGULAR_MIN_GAP_DAYS+ apart (median gap; every 2-4
weeks or rarer) is `irregular`: the clients say weekly isn't right, and Add
all starts with only High / Medium, non-irregular suggestions ticked
(`add_all_default`; Low and irregular ones stay listed, unticked).

**Vendors without a schedule.** Every active vendor (the latest snapshot's
suppliers, ";" split, plus any vendor seen in the evidence), minus scheduled
and suggested ones, with why there's no suggestion - so none is missing.

Dismissed: suggestion_dismissals kind "vendor_schedule", key
`schedule_key(vendor, weekdays)` - a different pattern later is suggested again.
"""

import datetime as dt
import json
import re
import statistics
import threading
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.demo import hide_demo_clause
from app.models import InventorySnapshot, InvoiceSubmission, PlacedOrder, PurchaseLogEntry, User, VendorSchedule
from app.services import delivery_suggestions as ds
from app.services.suggestion_dismissals import dismissed_keys
from app.services.supplier_projection import list_suppliers
from app.services.vendor_schedule import create_vendor_schedule, decode_weekdays, list_vendor_schedules, schedule_out
from app.timeutil import restaurant_timezone, restaurant_today, to_restaurant_date

LOOKBACK_DAYS = 84
# Pattern thresholds (see the module docstring).
MIN_PER_WEEKDAY = 2
MIN_WEEKDAY_SHARE = 0.2
MIN_PER_WEEKDAY_IF_SEVERAL = 3
MIN_COVERED = 2
MIN_COVERAGE = 0.5
MIN_SPAN_DAYS = 7
FULL_SUPPORT = 6
HIGH_MIN_COVERED = 5
HIGH_MIN_COVERAGE = 0.75
MEDIUM_MIN_COVERED = 3
MEDIUM_MIN_COVERAGE = 0.6
# Merging evidence into deliveries.
ATTACH_DAYS = 3
ATTACH_BEFORE_DAYS = 1
LAG_MIN_SAMPLES = 3
MAX_RECEIPT_LAG = 3
# Order day.
DEFAULT_LEAD_DAYS = 1
MAX_LEAD_GAP_DAYS = 7
MAX_ACCEPT_KEYS = 200
# Cadence (Oct 14): a schedule is weekly, so a vendor whose deliveries are
# usually IRREGULAR_MIN_GAP_DAYS+ apart (every 2-4 weeks, or rarer) gets a
# warning that weekly isn't right, and starts unticked in Add all.
IRREGULAR_MIN_GAP_DAYS = 11
RARE_MIN_GAP_DAYS = 31
# Add all starts with these confidences ticked (Low stays listed, unticked).
ADD_ALL_DEFAULT_CONFIDENCE = ("high", "medium")

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SOURCES = ("invoices", "toast_receipts", "purchase_log", "stock_rise")
SOURCE_LABELS = {
    "invoices": "invoice uploads",
    "toast_receipts": "Toast receivings",
    "purchase_log": "logged deliveries",
    "stock_rise": "stock rises",
}
SOURCE_SINGULAR = {
    "invoices": "invoice upload",
    "toast_receipts": "Toast receiving",
    "purchase_log": "logged delivery",
    "stock_rise": "stock rise",
}
# Evidence kind -> public source.
_KIND_SOURCE = {
    "invoice": "invoices",
    "manual_log": "purchase_log",
    "review_confirm": "purchase_log",
    "receipt": "toast_receipts",
    "stock_rise": "stock_rise",
}
_ANCHORS = {"invoice", "manual_log"}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

# The store itself (the POS lists the store's own name as the supplier of
# house-made items) - never a delivering vendor: the store pack's
# house_vendors.prefixes.

_SUFFIXES = {"inc", "llc", "co", "corp", "corporation", "ltd", "company", "incorporated"}
_NON_WORD = re.compile(r"[^a-z0-9]+")


def vendor_key(name: Optional[str]) -> str:
    """One spelling per vendor: lower case, "&" = "and", punctuation dropped,
    trailing Inc / LLC / Co / Corp / Ltd left out ("Sri Sai Imports, Inc" =
    "sri sai imports")."""
    text = (name or "").strip().lower().replace("&", " and ")
    words = _NON_WORD.sub(" ", text).split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words) or (name or "").strip().lower()


def schedule_key(vendor: str, weekdays: List[int]) -> str:
    return f"{vendor.strip().lower()}|{json.dumps(sorted(set(weekdays)))}"


def _names(weekdays: Iterable[int]) -> str:
    return "/".join(WEEKDAY_NAMES[w] for w in sorted(weekdays))


def _is_house(key: str) -> bool:
    from app.store import pack as store_pack

    return store_pack.is_house_vendor_key(key)


def _store_day(value: dt.datetime) -> dt.date:
    return to_restaurant_date(value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc))


class _Names:
    """Maps every spelling to one vendor key and a display name: Toast's
    supplier spelling first, then a schedule's, then the commonest spelling in
    the evidence. A spelling that isn't a known vendor but is a whole-word
    prefix of exactly one Toast supplier (or the other way round) is that
    supplier ("RBest" = "RBest Produce Inc")."""

    def __init__(self, inventory: Iterable[str], scheduled: Iterable[str]):
        self.display: Dict[str, str] = {}
        self.inventory_keys: Set[str] = set()
        self._seen: Dict[str, Counter] = defaultdict(Counter)
        for name in inventory:
            key = vendor_key(name)
            if key and key not in self.display:
                self.display[key] = name.strip()
                self.inventory_keys.add(key)
        for name in scheduled:
            key = self.resolve(name)
            self.display.setdefault(key, name.strip())

    def resolve(self, name: str) -> str:
        key = vendor_key(name)
        if key in self.display or not key:
            return key
        matches = [k for k in self.inventory_keys if k.startswith(key + " ") or key.startswith(k + " ")]
        return matches[0] if len(matches) == 1 else key

    def note(self, name: str) -> str:
        key = self.resolve(name)
        self._seen[key][name.strip()] += 1
        return key

    def name_for(self, key: str) -> str:
        if key in self.display:
            return self.display[key]
        seen = self._seen.get(key)
        return seen.most_common(1)[0][0] if seen else key


# --- Evidence ----------------------------------------------------------------------------------


_rise_cache_lock = threading.Lock()
_rise_cache: Dict[tuple, List[str]] = {}


def _stock_rise_vendors(db: Session, prev_date: dt.date, date: dt.date) -> List[str]:
    """Vendors of the suggested deliveries' stock-rise groups for one API-only
    snapshot pair (memoised: a past pair doesn't change unless its rows do)."""
    fp = tuple(
        db.execute(
            select(func.count(InventorySnapshot.id), func.max(InventorySnapshot.id)).where(
                InventorySnapshot.snapshot_date.in_([prev_date, date])
            )
        ).one()
    )
    key = (prev_date, date, fp)
    with _rise_cache_lock:
        if key in _rise_cache:
            return _rise_cache[key]
    try:
        groups, _ = ds.pair_groups(db, ds.Pair(db, prev_date, date), mode="rises")
    except Exception:  # evidence is optional; a broken pair must not break the page
        groups = []
    vendors = sorted({g["vendor"] for g in groups if g.get("vendor")})
    with _rise_cache_lock:
        if len(_rise_cache) > 400:
            _rise_cache.clear()
        _rise_cache[key] = vendors
    return vendors


def _evidence(db: Session, names: _Names, start: dt.date, end: dt.date) -> Dict[str, List[Tuple[dt.date, str]]]:
    """vendor key -> [(day, kind)] - kind is one of _KIND_SOURCE's keys."""
    out: Dict[str, List[Tuple[dt.date, str]]] = defaultdict(list)
    tz = restaurant_timezone()
    # A little before the window, so a delivery right at its start keeps its anchor.
    floor = start - dt.timedelta(days=ATTACH_DAYS)
    floor_dt = dt.datetime.combine(floor, dt.time.min, tz)
    for vendor, created in db.execute(
        select(InvoiceSubmission.vendor, InvoiceSubmission.created_at).where(
            InvoiceSubmission.deleted_at.is_(None),
            InvoiceSubmission.submission_type == "invoice",
            InvoiceSubmission.created_at >= floor_dt,
            hide_demo_clause(db, InvoiceSubmission.submitted_by_user_id, InvoiceSubmission.submitted_by_name),
        )
    ):
        day = _store_day(created)
        if vendor and vendor.strip() and day <= end:
            out[names.note(vendor)].append((day, "invoice"))
    for r in ds.toast_receipts(db, floor, received_after=floor_dt):
        day = to_restaurant_date(r["received_at"])
        if floor <= day <= end:
            out[names.note(r["vendor"])].append((day, "receipt"))
    for supplier, received, notes in db.execute(
        select(PurchaseLogEntry.supplier, PurchaseLogEntry.received_date, PurchaseLogEntry.notes)
        .where(PurchaseLogEntry.received_date >= floor, PurchaseLogEntry.supplier.is_not(None))
        .distinct()
    ):
        if not (supplier or "").strip():
            continue
        confirm = (notes or "").startswith(ds.DELIVERY_REVIEW_NOTE_PREFIX)
        day = received - dt.timedelta(days=1) if confirm else received
        if floor <= day <= end:
            out[names.note(supplier)].append((day, "review_confirm" if confirm else "manual_log"))
    dates = [d for d in ds.snapshot_dates(db) if d >= floor]
    api_only = ds.api_only_dates(db, floor)
    for prev_date, date in zip(dates, dates[1:]):
        if date in api_only and date - dt.timedelta(days=1) <= end:
            for vendor in _stock_rise_vendors(db, prev_date, date):
                out[names.note(vendor)].append((date - dt.timedelta(days=1), "stock_rise"))
    return out


def _orders(db: Session, names: _Names, start: dt.date) -> Dict[str, List[Tuple[dt.date, Optional[dt.date]]]]:
    """vendor key -> [(order day, the delivery it's for or None)] - placed orders only."""
    tz = restaurant_timezone()
    out: Dict[str, List[Tuple[dt.date, Optional[dt.date]]]] = defaultdict(list)
    for supplier, placed_at, for_delivery in db.execute(
        select(PlacedOrder.supplier, PlacedOrder.placed_at, PlacedOrder.for_delivery_date).where(
            PlacedOrder.status == "placed",
            PlacedOrder.placed_at >= dt.datetime.combine(start, dt.time.min, tz),
            hide_demo_clause(db, PlacedOrder.placed_by_user_id, PlacedOrder.placed_by_name),
        )
    ):
        if (supplier or "").strip():
            out[names.resolve(supplier)].append((_store_day(placed_at), for_delivery))
    return out


# --- Deliveries from evidence --------------------------------------------------------------------


def _receipt_lag(samples: List[int]) -> int:
    """Days a vendor's Toast receivings usually come after the delivery: the
    (lower) median of its receivings' gaps after its invoice uploads, with at
    least LAG_MIN_SAMPLES to go on; 0 otherwise."""
    if len(samples) < LAG_MIN_SAMPLES:
        return 0
    return max(0, min(int(statistics.median_low(samples)), MAX_RECEIPT_LAG))


def build_deliveries(evidence: List[Tuple[dt.date, str]]) -> Tuple[List[dict], int]:
    """Merge one vendor's evidence into deliveries: [{day, kinds: Counter}]
    sorted by day, and the receiving lag used (see the module docstring).
    Several pieces of one kind on one day (two receiving numbers, two
    invoices) count once."""
    evidence = sorted(set(evidence))
    anchors = sorted({d for d, k in evidence if k in _ANCHORS})
    events: Dict[dt.date, dict] = {a: {"day": a, "kinds": Counter()} for a in anchors}
    for d, k in evidence:
        if k in _ANCHORS:
            events[d]["kinds"][k] += 1
    loose: List[Tuple[dt.date, str]] = []
    lag_samples: List[int] = []
    for d, k in sorted(e for e in evidence if e[1] not in _ANCHORS):
        best = None
        for a in anchors:
            diff = (d - a).days
            if -ATTACH_BEFORE_DAYS <= diff <= ATTACH_DAYS:
                rank = (abs(diff), diff < 0)
                if best is None or rank < best[0]:
                    best = (rank, a, diff)
        if best is None:
            loose.append((d, k))
            continue
        event = events[best[1]]
        event["kinds"][k] += 1
        event["day"] = min(event["day"], d)
        if k == "receipt":
            lag_samples.append(best[2])
    lag = _receipt_lag(lag_samples)
    clusters: List[List[Tuple[dt.date, str]]] = []
    for d, k in loose:
        if clusters and (d - clusters[-1][-1][0]).days <= 1:
            clusters[-1].append((d, k))
        else:
            clusters.append([(d, k)])
    for cluster in clusters:
        day = min(d - dt.timedelta(days=lag) if k == "receipt" else d for d, k in cluster)
        event = events.setdefault(day, {"day": day, "kinds": Counter()})
        event["kinds"].update(k for _, k in cluster)
    merged: Dict[dt.date, dict] = {}
    for event in events.values():
        target = merged.setdefault(event["day"], {"day": event["day"], "kinds": Counter()})
        target["kinds"].update(event["kinds"])
    return sorted(merged.values(), key=lambda e: e["day"]), lag


# --- Pattern + confidence ----------------------------------------------------------------------


def infer_weekdays(days: Iterable[dt.date]) -> Tuple[Optional[dict], str]:
    """(pattern, "") or (None, why): "none" (no deliveries), "few" (fewer
    than MIN_COVERED), "scattered" (no weekday pattern)."""
    days = sorted(set(days))
    total = len(days)
    if total == 0:
        return None, "none"
    counts = Counter(d.weekday() for d in days)
    if total < MIN_COVERED:
        return None, "few"
    chosen = sorted(w for w, n in counts.items() if n >= MIN_PER_WEEKDAY and n / total >= MIN_WEEKDAY_SHARE)
    if len(chosen) > 1:
        strong = [w for w in chosen if counts[w] >= MIN_PER_WEEKDAY_IF_SEVERAL]
        if strong:
            chosen = strong
    if not chosen:
        return None, "few" if total <= MIN_COVERED + 1 else "scattered"
    covered = sum(counts[w] for w in chosen)
    on_days = [d for d in days if d.weekday() in chosen]
    if covered < MIN_COVERED or covered / total < MIN_COVERAGE:
        return None, "scattered"
    if (on_days[-1] - on_days[0]).days < MIN_SPAN_DAYS:
        return None, "few"
    coverage = covered / total
    if len(chosen) > 1 and any(counts[w] < MIN_PER_WEEKDAY_IF_SEVERAL for w in chosen):
        confidence = "low"  # two thin days (2 + 2) could be chance
    elif covered >= HIGH_MIN_COVERED and coverage >= HIGH_MIN_COVERAGE:
        confidence = "high"
    elif covered >= MEDIUM_MIN_COVERED and coverage >= MEDIUM_MIN_COVERAGE:
        confidence = "medium"
    else:
        confidence = "low"
    weeks = {(d - dt.timedelta(days=d.weekday())) for d in on_days}
    return {
        "weekdays": chosen,
        "covered": covered,
        "total": total,
        "coverage": round(coverage, 2),
        "counts": {WEEKDAY_NAMES[w]: counts[w] for w in range(7) if counts[w]},
        "weeks_seen": len(weeks),
        "confidence": confidence,
        "score": int(round(100 * coverage * min(1.0, covered / FULL_SUPPORT))),
    }, ""


def _weeks_text(lo: int, hi: int) -> str:
    if lo == hi:
        return f"about every {lo} weeks" if lo > 1 else "about every week"
    return f"every {lo}-{hi} weeks"


def cadence(days: Iterable[dt.date]) -> dict:
    """How often the vendor delivers, from the gaps between deliveries.
    {cadence: "weekly" | "every_few_weeks" | "rare" | "unknown", irregular,
    median_gap_days, cadence_weeks, text}. Irregular = median gap
    IRREGULAR_MIN_GAP_DAYS+; "rare" = median over RARE_MIN_GAP_DAYS. The
    text gives the usual range (middle half of the gaps, in weeks). A schedule
    repeats every week, so an irregular vendor would get an order due every
    week - the clients say weekly isn't right."""
    days = sorted(set(days))
    gaps = sorted((b - a).days for a, b in zip(days, days[1:]))
    if not gaps:
        return {"cadence": "unknown", "irregular": False, "median_gap_days": None, "cadence_weeks": None, "text": "", "label": ""}
    median_gap = statistics.median(gaps)
    out = {"median_gap_days": round(float(median_gap), 1), "cadence_weeks": max(1, int(round(median_gap / 7.0)))}
    if median_gap < IRREGULAR_MIN_GAP_DAYS:
        return {**out, "cadence": "weekly", "irregular": False, "text": "", "label": ""}
    tail = " - a schedule repeats every week, so it would put an order due every week. Weekly isn't right for this vendor."
    if median_gap > RARE_MIN_GAP_DAYS:
        label = "about once a month or less"
        return {**out, "cadence": "rare", "irregular": True, "text": f"Delivers {label}{tail}", "label": label}
    if len(gaps) >= 2:
        q1, _, q3 = statistics.quantiles(gaps, n=4, method="inclusive")
    else:
        q1 = q3 = gaps[0]
    lo = max(1, int(round(q1 / 7.0)))
    hi = max(lo, int(round(q3 / 7.0)))
    label = _weeks_text(lo, hi)
    return {
        **out,
        "cadence": "every_few_weeks",
        "irregular": True,
        "text": f"Delivers {label}, not every week{tail}",
        "label": label,
    }


def _lead_days(orders: List[Tuple[dt.date, Optional[dt.date]]], deliveries: List[dt.date]) -> Tuple[Optional[int], int]:
    gaps = []
    ordered = sorted(deliveries)
    for placed, for_delivery in orders:
        if for_delivery is not None and 0 <= (for_delivery - placed).days <= MAX_LEAD_GAP_DAYS:
            gaps.append((for_delivery - placed).days)
            continue
        nxt = next((d for d in ordered if d >= placed), None)
        if nxt is not None and (nxt - placed).days <= MAX_LEAD_GAP_DAYS:
            gaps.append((nxt - placed).days)
    if not gaps:
        return None, 0
    return int(round(statistics.median(gaps))), len(gaps)


def _counts_text(counts: Dict[str, int]) -> str:
    return ", ".join(f"{day} x{n}" for day, n in sorted(counts.items(), key=lambda x: WEEKDAY_NAMES.index(x[0])))


def _sources_text(sources: Dict[str, int]) -> str:
    parts = [f"{n} {SOURCE_LABELS[s] if n != 1 else SOURCE_SINGULAR[s]}" for s, n in sources.items() if n]
    return ", ".join(parts)


def _delivery_rows(deliveries: List[dict]) -> List[dict]:
    return [
        {
            "date": e["day"].isoformat(),
            "weekday": WEEKDAY_NAMES[e["day"].weekday()],
            "sources": sorted({_KIND_SOURCE[k] for k in e["kinds"]}, key=SOURCES.index),
        }
        for e in deliveries
    ]


def _source_counts(deliveries: List[dict]) -> Dict[str, int]:
    """How many deliveries each source saw."""
    counts = {s: 0 for s in SOURCES}
    for e in deliveries:
        for s in {_KIND_SOURCE[k] for k in e["kinds"]}:
            counts[s] += 1
    return counts


def compute_schedule_suggestions(
    db: Session, today: Optional[dt.date] = None, include_dismissed: bool = False
) -> dict:
    today = today or restaurant_today()
    start = today - dt.timedelta(days=LOOKBACK_DAYS)
    weeks = LOOKBACK_DAYS // 7
    schedules = list_vendor_schedules(db)
    names = _Names(list_suppliers(db), [s.vendor for s in schedules])
    by_key_schedule: Dict[str, VendorSchedule] = {}
    for s in schedules:
        by_key_schedule.setdefault(names.resolve(s.vendor), s)
    evidence = _evidence(db, names, start, today)
    orders = _orders(db, names, start - dt.timedelta(days=MAX_LEAD_GAP_DAYS))
    dismissed = dismissed_keys(db, "vendor_schedule")

    suggestions, checks, without = [], [], []
    hidden = 0
    vendor_keys = (set(names.inventory_keys) | set(evidence)) - {""}
    for key in sorted(vendor_keys):
        if _is_house(key):
            continue
        name = names.name_for(key)
        deliveries, lag = build_deliveries(evidence.get(key, []))
        deliveries = [e for e in deliveries if start <= e["day"] <= today]
        days = [e["day"] for e in deliveries]
        pattern, why = infer_weekdays(days)
        existing = by_key_schedule.get(key)
        all_counts = Counter(d.weekday() for d in days)
        counts = {WEEKDAY_NAMES[w]: all_counts[w] for w in range(7) if all_counts[w]}
        sources = _source_counts(deliveries)
        evidence_out = {
            "source": "combined",
            "source_label": "deliveries",
            "dates": [d.isoformat() for d in days],
            "weekday_counts": counts,
            "covered": pattern["covered"] if pattern else 0,
            "total": len(days),
            "coverage": pattern["coverage"] if pattern else 0,
            "weeks": weeks,
            "weeks_seen": pattern["weeks_seen"] if pattern else 0,
            "sources": sources,
            "sources_text": _sources_text(sources),
            "receipt_lag_days": lag,
            "deliveries": _delivery_rows(deliveries),
            "summary": f"Delivered {_counts_text(counts)} in the last {weeks} weeks" if counts else "",
        }
        if existing is not None:
            current = decode_weekdays(existing.delivery_weekdays)
            if pattern and set(current) != set(pattern["weekdays"]):
                checks.append(
                    {
                        "vendor": existing.vendor,
                        "schedule_id": existing.id,
                        "schedule_weekdays": sorted(current),
                        "observed_weekdays": pattern["weekdays"],
                        "evidence": evidence_out,
                        "reason": (
                            f"{existing.vendor}'s schedule says {_names(current) or 'no day'}, but "
                            f"{pattern['covered']} of its last {pattern['total']} deliveries were on "
                            f"{_names(pattern['weekdays'])}. The schedule is left as it is."
                        ),
                    }
                )
            continue
        last = days[-1].isoformat() if days else None
        row_base = {
            "vendor": name,
            "in_inventory": key in names.inventory_keys,
            "deliveries_seen": len(days),
            "last_delivery": last,
            "weekday_counts": counts,
            "summary": evidence_out["summary"],
            "sources_text": evidence_out["sources_text"],
        }
        if pattern is None:
            without.append({**row_base, "why": why, "reason": _why_text(why, counts, weeks, days)})
            continue
        weekdays = pattern["weekdays"]
        skey = schedule_key(name, weekdays)
        if skey in dismissed and not include_dismissed:
            hidden += 1
            without.append(
                {
                    **row_base,
                    "why": "dismissed",
                    "dismissed_key": skey,
                    "suggested_weekdays": weekdays,
                    "reason": f"You dismissed the suggestion ({_names(weekdays)}).",
                }
            )
            continue
        vendor_orders = orders.get(key, [])
        lead, lead_samples = _lead_days(vendor_orders, days)
        order_counts = Counter(d.weekday() for d, _ in vendor_orders)
        lead_days = lead if lead is not None else DEFAULT_LEAD_DAYS
        how_often = cadence(days)
        suggestions.append(
            {
                "key": skey,
                "vendor": name,
                "delivery_weekdays": weekdays,
                "weekday_names": [WEEKDAY_NAMES[w] for w in weekdays],
                "lead_days": lead_days,
                "lead_days_source": "placed_orders" if lead is not None else "default",
                "lead_samples": lead_samples,
                "order_weekdays": sorted(order_counts, key=lambda w: (-order_counts[w], w)),
                "schedule_order_weekdays": sorted({(w - lead) % 7 for w in weekdays}) if lead is not None else [],
                "confidence": pattern["confidence"],
                "confidence_score": pattern["score"],
                "in_inventory": key in names.inventory_keys,
                "evidence": evidence_out,
                "dismissed": skey in dismissed,
                # Additive (Oct 14): how often deliveries come, and whether
                # "Add all" starts with it ticked (High / Medium, and not
                # irregular - a weekly schedule isn't right for those).
                "cadence": how_often["cadence"],
                "irregular": how_often["irregular"],
                "median_gap_days": how_often["median_gap_days"],
                "cadence_weeks": how_often["cadence_weeks"],
                "cadence_text": how_often["text"],
                # Short form for lists: "every 1-2 weeks" (empty when weekly).
                "cadence_label": how_often["label"],
                "add_all_default": pattern["confidence"] in ADD_ALL_DEFAULT_CONFIDENCE and not how_often["irregular"],
                "reason": (
                    f"{name} usually delivers on {_names(weekdays)}: {pattern['covered']} of the last "
                    f"{pattern['total']} deliveries ({weeks} weeks) were on {_names(weekdays)}."
                ),
            }
        )
    suggestions.sort(
        key=lambda s: (CONFIDENCE_ORDER[s["confidence"]], -s["confidence_score"], -s["evidence"]["covered"], s["vendor"].lower())
    )
    without.sort(key=lambda r: (r["why"] != "dismissed", -r["deliveries_seen"], r["vendor"].lower()))
    scheduled_keys = {k for k in by_key_schedule if not _is_house(k)}
    return {
        "lookback_days": LOOKBACK_DAYS,
        "through": today.isoformat(),
        "count": len(suggestions),
        "suggestions": suggestions,
        "schedule_checks": checks,
        "dismissed_count": hidden,
        "vendors_without_schedule": without,
        "counts": {
            "active_vendors": len({k for k in vendor_keys if not _is_house(k)} | scheduled_keys),
            "scheduled": len(scheduled_keys),
            "suggested": len(suggestions),
            "without_schedule": len(without),
            "dismissed": hidden,
        },
        "rules": {
            "lookback_days": LOOKBACK_DAYS,
            "min_covered": MIN_COVERED,
            "min_per_weekday": MIN_PER_WEEKDAY,
            "min_weekday_share": MIN_WEEKDAY_SHARE,
            "min_coverage": MIN_COVERAGE,
            "high": {"min_covered": HIGH_MIN_COVERED, "min_coverage": HIGH_MIN_COVERAGE},
            "medium": {"min_covered": MEDIUM_MIN_COVERED, "min_coverage": MEDIUM_MIN_COVERAGE},
            "irregular_min_gap_days": IRREGULAR_MIN_GAP_DAYS,
            "add_all_default_confidence": list(ADD_ALL_DEFAULT_CONFIDENCE),
        },
    }


def _why_text(why: str, counts: Dict[str, int], weeks: int, days: List[dt.date]) -> str:
    if why == "none":
        return f"No deliveries recorded in the last {weeks} weeks."
    if why == "few":
        if len(days) == 1:
            return f"Only 1 delivery in the last {weeks} weeks ({days[0].strftime('%a %b')} {days[0].day})."
        return f"Only {len(days)} deliveries in the last {weeks} weeks ({_counts_text(counts)}) - not enough to see a pattern."
    return f"Deliveries fall on scattered days ({_counts_text(counts)})."


# --- Add all ------------------------------------------------------------------------------------

_accept_lock = threading.Lock()
# POST /schedules takes the same lock (VX12): its "no schedule under any
# spelling yet" check and the insert must not interleave with Add all or a
# second POST (a double tap made a 500 from the unique constraint, or a
# second schedule under another spelling).
schedule_write_lock = _accept_lock


def find_schedule_for_vendor(db: Session, vendor: str) -> Optional[VendorSchedule]:
    """A schedule for this vendor under any spelling (paused ones included)."""
    key = vendor_key(vendor)
    for row in list_vendor_schedules(db):
        if vendor_key(row.vendor) == key:
            return row
    return None


def accept_all(db: Session, keys: List[str], user: Optional[User] = None) -> dict:
    """Create a schedule for every listed suggestion still offered, exactly as
    suggested. A vendor that has a schedule by now (any spelling, paused
    included) is skipped ("has_schedule") - never overwritten - and so is a
    key no longer suggested ("not_suggested": dismissed, or the pattern
    changed)."""
    created, skipped = [], []
    with _accept_lock:
        current = {s["key"]: s for s in compute_schedule_suggestions(db)["suggestions"]}
        for key in dict.fromkeys(keys):
            s = current.get(key)
            vendor = s["vendor"] if s else key.split("|", 1)[0]
            existing = find_schedule_for_vendor(db, vendor)
            if s is None and existing is None:
                skipped.append({"key": key, "vendor": vendor, "why": "not_suggested",
                                "reason": "No longer suggested."})
                continue
            if existing is not None:
                skipped.append({"key": key, "vendor": s["vendor"] if s else existing.vendor, "why": "has_schedule",
                                "reason": f"{existing.vendor} already has a schedule - left as it is."})
                continue
            row = create_vendor_schedule(db, s["vendor"], s["delivery_weekdays"], s["lead_days"])
            created.append({**schedule_out(row), "key": key, "lead_days_source": s["lead_days_source"]})
    return {"created": created, "skipped": skipped, "created_count": len(created), "skipped_count": len(skipped)}


def __getattr__(name):
    # The old constant: the store pack's house vendor prefixes.
    if name == "HOUSE_VENDOR_PREFIXES":
        from app.store import pack as store_pack

        return tuple(store_pack.current_pack().house_vendors.prefixes)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
