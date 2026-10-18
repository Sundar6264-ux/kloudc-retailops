"""Fix Issues: the four inventory-hygiene lists staff work through (missing
barcodes, invalid barcodes, duplicate products, dead stock), with a way to
close each item with a reason and a date-wise report of what was closed.

The detection itself is the existing report code (barcode_report.py,
duplicate_products.py, inventory_intelligence.py), unchanged - the
/api/reports/* endpoints the mobile app reads still return exactly what they
did. This module only adds, on top:
  - kitchen-made categories are left out (they have no barcode and aren't
    stocked like retail, so they only add noise);
  - closed items (issue_resolutions rows) move to a Closed list;
  - "Not an issue" hides an item from its category for good, while "Fixed in
    Toast" is re-checked: if a newer inventory upload still shows the problem,
    the item comes back to the open list.

Sep 2026: five more lists about stock - Negative inventory (moved here from
Reports, same loader), and slow_review / dead_with_stock /
dead_new_arrival / refill_from_container from stock_issues.py. They work
exactly like the first four (same close / reopen / Closed list / report).
"""

import datetime as dt
import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import IssueResolution, IssueResolutionEvent, Order
from app.services.barcode_report import get_invalid_barcodes, get_missing_barcodes
from app.services.duplicate_products import get_duplicate_products
from app.services.inventory_api import (
    live_stock_meta,
    menu_recheck_moment,
    menu_shows_barcode_issue,
    overlay_live_stock,
)
from app.services.inventory_intelligence import get_dead_stock, get_negative_inventory
from app.services.reorder import latest_inventory_by_item
from app.services.stock_issues import (
    dead_new_arrival_items,
    dead_with_stock_items,
    dead_zero_stock_items,
    forecasts_by_item,
    negative_cleared_since,
    refill_cleared_since,
    refill_from_container_items,
    clear_forecast_cache,
    slow_review_items,
)
from app.timeutil import restaurant_timezone, restaurant_today, to_restaurant_date

logger = logging.getLogger(__name__)

# Toast category groups for food made in the store - no barcode, no shelf stock.
# "Event Menu" appeared in Toast on Sep 28 (samosas, chai, lassi for events):
# all 11 of its items were showing as missing barcodes and slow - review.
EXCLUDED_CATEGORY_GROUPS = {"STREET KITCHEN", "CATERING MENU", "EVENT MENU"}

# Lists the hourly Toast menu re-check keeps current between uploads.
BARCODE_ISSUE_TYPES = {"missing_barcode", "invalid_barcode"}
# Lists that read live Toast stock with INVENTORY_API_SNAPSHOTS on.
LIVE_STOCK_ISSUE_TYPES = {"negative_inventory", "refill_from_container"}

NOT_AN_ISSUE = "not_an_issue"
OTHER = "other"
FIXED = "fixed"

# "More time" closes (owner, Sep 30: "If not set, set it to one week then
# move that item from closed to open"): reason -> days. When the snooze ends
# and the item still shows the problem it moves back to open (the daily
# 9:00 job and, lazily, any read of the list); if it's gone it stays closed.
# A mapping so another reason can be given a snooze later.
SNOOZE_DAYS = {"give_time": 7}

# How a closed item came back to the open list (the open row's `reopened.how`).
HOW_SNOOZE_ENDED = "snooze_ended"
HOW_FIXED_STILL_SHOWING = "fixed_but_still_showing"
HOW_DETECTED_AGAIN = "detected_again"
HOW_MANUAL = "manual"  # someone pressed Reopen - history only, not "came back"


class _Context:
    """Data several lists share, loaded at most once per request (the summary
    builds every list)."""

    def __init__(self, db: Session):
        self.db = db
        self._latest = None
        self._last_sold = None
        self._forecasts = None

    @property
    def latest(self) -> dict:
        if self._latest is None:
            self._latest = latest_inventory_by_item(self.db)
        return self._latest

    @property
    def last_sold(self) -> dict:
        if self._last_sold is None:
            self._last_sold = _last_sold_by_item(self.db)
        return self._last_sold

    @property
    def forecasts(self) -> dict:
        if self._forecasts is None:
            self._forecasts = forecasts_by_item(self.db, self.latest, EXCLUDED_CATEGORY_GROUPS)
        return self._forecasts

    @property
    def lead_state(self):
        # W4 (Oct 14): PO section + category / vendor lead per row.
        if getattr(self, "_lead_state", None) is None:
            from app.services.issue_leads import RowState

            self._lead_state = RowState(self.db, self.latest)
        return self._lead_state


ISSUE_TYPES = {
    "missing_barcode": {
        "label": "Missing barcodes",
        "loader": lambda ctx: get_missing_barcodes(ctx.db),
        "reasons": [
            (FIXED, "Barcode added in Toast"),
            ("no_barcode_needed", "No barcode needed (loose / weighed item)"),
            ("discontinued", "Item discontinued"),
        ],
    },
    "invalid_barcode": {
        "label": "Invalid barcodes",
        "loader": lambda ctx: get_invalid_barcodes(ctx.db),
        "reasons": [
            (FIXED, "Barcode corrected in Toast"),
            ("in_house_code", "Store / in-house code - OK as is"),
            ("discontinued", "Item discontinued"),
        ],
    },
    "duplicate": {
        "label": "Duplicates",
        "loader": lambda ctx: get_duplicate_products(ctx.db),
        "reasons": [
            (FIXED, "Merged / removed in Toast"),
            ("different_products", "Different products (size / variant)"),
        ],
    },
    # Legacy (owner, Sep 29: "two dead lists - keep only one"): the website
    # and new app builds show "Dead with stock" instead and no longer list
    # this one. Kept - in the summary too, by key - because the App Store
    # build still reads it.
    "dead_stock": {
        "label": "Dead inventory",
        "loader": lambda ctx: get_dead_stock(ctx.db),
        "reasons": [
            ("markdown", "Marked down / on clearance"),
            ("returned", "Returned to vendor"),
            ("seasonal", "Seasonal - keep stocking"),
            ("discontinued", "Discontinued - won't reorder"),
            (FIXED, "Count corrected in Toast"),
        ],
    },
    # --- Stock lists (Sep 2026). See stock_issues.py for each rule. ---
    "negative_inventory": {
        "label": "Negative inventory",
        "loader": lambda ctx: get_negative_inventory(ctx.db),
        "reasons": [
            (FIXED, "Count corrected in Toast"),
            ("sold_before_received", "Sold before the delivery was received in Toast"),
            ("wrong_item_rung", "Rung up under the wrong item"),
            ("loose_weighed", "Loose / weighed item - count not tracked"),
            ("discontinued", "Item discontinued"),
        ],
        # A negative count comes and goes: a close covers this occurrence
        # only (except these reasons, which are about the item itself). See
        # stock_issues.negative_cleared_since.
        "recurs": {"cleared": negative_cleared_since, "sticky": {"loose_weighed", "discontinued"}},
    },
    "refill_from_container": {
        "label": "Refill from container",
        # Live Toast counts when INVENTORY_API_SNAPSHOTS is on (inventory_api).
        "loader": lambda ctx: refill_from_container_items(overlay_live_stock(ctx.latest)),
        "reasons": [
            (FIXED, "Moved stock to the shelf"),
            ("container_count_wrong", "Container count is wrong"),
            ("store_count_wrong", "Shelf count is wrong - shelf is stocked"),
            ("keep_in_container", "Keeping it in the container for now"),
        ],
        "recurs": {"cleared": refill_cleared_since, "sticky": set()},
    },
    "slow_review": {
        "label": "Slow moving - Review",
        "loader": lambda ctx: slow_review_items(ctx.latest, ctx.forecasts),
        "reasons": [
            ("reviewed_ok", "Reviewed - sales look right"),
            (FIXED, "Count corrected in Toast"),
            ("order_less", "Will order less"),
            ("markdown", "Marked down / on promotion"),
            ("discontinued", "Discontinued - won't reorder"),
        ],
    },
    "dead_with_stock": {
        "label": "Dead with stock",
        "loader": lambda ctx: dead_with_stock_items(ctx.latest, ctx.forecasts),
        "reasons": [
            ("markdown", "Marked down / on clearance"),
            ("returned", "Returned to vendor"),
            ("moved_display", "Moved to a better shelf / display"),
            ("seasonal", "Seasonal - keep stocking"),
            ("discontinued", "Discontinued - won't reorder"),
            (FIXED, "Count corrected in Toast"),
        ],
    },
    # Owner, Oct 2026: "separate list for dead inventory with 0 stock". The
    # dead_with_stock rule at 0 on hand, plus (Oct 4) any item at 0 with no
    # sale in 90 days that isn't brand new; never overlaps another list (see
    # stock_issues.dead_zero_stock_items - Negative inventory comes first).
    "dead_zero_stock": {
        "label": "Dead - zero stock",
        "loader": lambda ctx: dead_zero_stock_items(ctx.db, ctx.latest, ctx.forecasts, ctx.last_sold),
        "reasons": [
            ("discontinued", "Discontinued - won't reorder"),
            ("deactivated", "Deactivated in Toast"),
            ("seasonal", "Seasonal - will restock"),
            ("reorder", "Reorder - it should sell"),
            (FIXED, "Count corrected in Toast"),
        ],
    },
    "dead_new_arrival": {
        "label": "Dead - newly arrived",
        "loader": lambda ctx: dead_new_arrival_items(ctx.db, ctx.latest, ctx.last_sold),
        "reasons": [
            ("moved_display", "Moved to a better shelf / display"),
            ("give_time", "New item - giving it more time"),
            ("markdown", "Marked down / on promotion"),
            ("returned", "Returned to vendor"),
            (FIXED, "Count corrected in Toast"),
        ],
    },
}

_COMMON_REASONS = [
    (NOT_AN_ISSUE, "Not an issue (duplicate / false alarm)"),
    (OTHER, "Other"),
]


def reasons_for(issue_type: str) -> list[tuple[str, str]]:
    # An unknown (retired) type still has the common reasons, so an old
    # stored close can be labelled instead of raising.
    return ISSUE_TYPES.get(issue_type, {}).get("reasons", []) + _COMMON_REASONS


def reason_label(issue_type: str, reason: str) -> str:
    return dict(reasons_for(issue_type)).get(reason, reason)


def reason_outcome(issue_type: str, reason: str) -> str:
    """What happens after a close with this reason: "snooze" (comes back
    after SNOOZE_DAYS if still showing), "rechecked" (the fixed reason:
    comes back if a later upload still shows it), "one_occurrence"
    (recurring lists: covers this time only) or "stays_closed"."""
    if reason in SNOOZE_DAYS:
        return "snooze"
    if reason == FIXED:
        return "rechecked"  # on a recurring list it also covers this time only
    recurs = ISSUE_TYPES.get(issue_type, {}).get("recurs")
    if recurs and reason not in recurs["sticky"] | {NOT_AN_ISSUE}:
        return "one_occurrence"
    return "stays_closed"


def all_reasons() -> dict:
    # Lazy: the glossary imports from services, never the other way at load.
    from app.glossary import reason_term

    out = {}
    for t, cfg in ISSUE_TYPES.items():
        reasons = []
        for k, label in reasons_for(t):
            term = reason_term(t, k)
            reasons.append(
                {
                    "key": k,
                    "label": label,
                    # Additive (Sep 30): what the reason means and does next.
                    "glossary_key": term["key"] if term else None,
                    "does": " ".join(x for x in (term["definition"], term["rule"]) if x) if term else None,
                    "outcome": reason_outcome(t, k),
                    "snooze_days": SNOOZE_DAYS.get(k),
                }
            )
        out[t] = {"label": cfg["label"], "reasons": reasons}
    return out


def _issue_key(issue_type: str, item: dict) -> str:
    if issue_type == "duplicate":
        return (item.get("name") or "").strip().lower()
    return str(item["item_id"])


def _last_sold_by_item(db: Session) -> dict:
    """item_id -> the last business date it sold (real top-level lines only,
    not modifiers or voids - see the skill's gotcha #11)."""
    rows = db.execute(
        select(Order.item_guid, func.max(Order.business_date))
        .where(Order.item_guid.is_not(None), Order.top_level(), Order.voided.is_(False))
        .group_by(Order.item_guid)
    ).all()
    return {item_guid: day for item_guid, day in rows}


def _product(snap, last_sold: Optional[dt.date]) -> dict:
    """Everything useful about one item from its latest upload, so a Fix Issues
    row can be judged without opening Toast (cost, stock, vendor code, sales,
    when it last sold). Purely additive - every existing row field is kept."""
    if snap is None:
        return {"last_sold_date": last_sold.isoformat() if last_sold else None}
    return {
        "price": snap.price,
        "cost": snap.cost,
        "gross_margin": snap.gross_margin,
        "on_hand_qty": snap.inventory_quantity,
        "inventory_value": snap.inventory_value,
        "days_on_hand": snap.inventory_days_on_hand,
        "supplier": snap.supplier,
        "supplier_item_id": snap.supplier_item_id,
        "barcode": snap.barcode,
        "category_group": snap.category_group,
        "category": snap.category,
        "subcategory": snap.subcategory,
        "last_7_day_sales": snap.last_7_day_sales,
        "last_30_day_sales": snap.last_30_day_sales,
        "last_90_day_sales": snap.last_90_day_sales,
        # Toast's "last N day sales" are DOLLARS; its "last N day orders"
        # columns are the units sold (e.g. 14 units = $62.16 at $4.49).
        "last_7_day_units": snap.last_7_day_orders,
        "last_30_day_units": snap.last_30_day_orders,
        "last_90_day_units": snap.last_90_day_orders,
        "last_received_from": snap.last_received_from,
        "last_received": snap.inventory_last_received,
        "last_sold_date": last_sold.isoformat() if last_sold else None,
        "snapshot_date": snap.snapshot_date.isoformat() if snap.snapshot_date else None,
    }


def _detected(
    db: Session, issue_type: str, ctx: Optional[_Context] = None
) -> tuple[list[dict], Optional[dt.date]]:
    """Current items for one issue type, kitchen categories removed, each tagged
    with its issue_key and a `product` object of full item details. Also
    returns the latest upload date they came from."""
    ctx = ctx or _Context(db)
    latest = ctx.latest
    groups = {item_id: (snap.category_group or "").upper() for item_id, snap in latest.items()}
    latest_date = max((s.snapshot_date for s in latest.values()), default=None)
    last_sold = ctx.last_sold
    items = []
    for item in ISSUE_TYPES[issue_type]["loader"](ctx):
        if groups.get(item["item_id"]) in EXCLUDED_CATEGORY_GROUPS:
            continue
        product = _product(latest.get(item["item_id"]), last_sold.get(item["item_id"]))
        items.append({**item, "issue_key": _issue_key(issue_type, item), "product": product})
    if issue_type == "duplicate":
        items = _group_duplicates(items)
    return items, latest_date


def _group_duplicates(rows: list[dict]) -> list[dict]:
    """One entry per shared name, with each Toast row listed under variants."""
    by_key: dict = {}
    for row in rows:
        entry = by_key.get(row["issue_key"])
        if entry is None:
            entry = by_key[row["issue_key"]] = {
                "issue_key": row["issue_key"],
                "item_id": row["item_id"],
                "name": row["name"],
                "category": row["category"],
                "supplier": row["supplier"],
                "product": row.get("product"),
                "variants": [],
            }
        entry["variants"].append(
            {
                **{k: row.get(k) for k in ("item_id", "category", "supplier", "price", "on_hand_qty")},
                "product": row.get("product"),
            }
        )
    out = list(by_key.values())
    for e in out:
        e["copies"] = len(e["variants"])
        e["prices"] = ", ".join(
            f"${v['price']:.2f}" for v in e["variants"] if v.get("price") is not None
        )
    return out


def _resolution_out(issue_type: str, r: IssueResolution) -> dict:
    return {
        "reason": r.reason,
        "reason_label": reason_label(issue_type, r.reason),
        "reason_detail": r.reason_detail,
        "closed_by": r.closed_by_name,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "closed_date": to_restaurant_date(r.closed_at).isoformat() if r.closed_at else None,
    }


def snooze_until_for(res) -> Optional[dt.date]:
    """The store-local day a "more time" close comes back, or None for a
    reason without a snooze. Rows closed before migration 0040 have no
    stored day: closed day + the reason's days."""
    days = SNOOZE_DAYS.get(res.reason)
    if days is None:
        return None
    if res.snooze_until is not None:
        return res.snooze_until
    if res.closed_at is None:
        return None
    return to_restaurant_date(res.closed_at) + dt.timedelta(days=days)


def _event(issue_type: str, key: str, event: str, **fields) -> IssueResolutionEvent:
    return IssueResolutionEvent(
        issue_type=issue_type, issue_key=key, event=event, at=fields.pop("at", None) or _now(), **fields
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def reopen_ended_snoozes(
    db: Session, issue_type: str, items: list[dict], today: Optional[dt.date] = None
) -> list[IssueResolutionEvent]:
    """Moves every "more time" close of `issue_type` whose snooze has ended
    and whose item still shows the problem (`items`, the detected list) back
    to open: deletes the resolution and records a reopen in the history.
    An item no longer detected stays closed. Safe to run twice at once:
    only the run that actually deleted the row records the reopen."""
    today = today or restaurant_today()
    detected = {i["issue_key"]: i for i in items}
    due = [
        r
        for r in db.query(IssueResolution).filter(
            IssueResolution.issue_type == issue_type, IssueResolution.reason.in_(list(SNOOZE_DAYS))
        )
        if r.issue_key in detected and (snooze_until_for(r) or dt.date.max) <= today
    ]
    events = []
    for r in due:
        gone = db.execute(delete(IssueResolution).where(IssueResolution.id == r.id)).rowcount
        if not gone:
            continue
        item = detected[r.issue_key]
        events.append(
            _event(
                issue_type,
                r.issue_key,
                "reopened",
                item_id=r.item_id,
                item_name=item.get("name") or r.item_name,
                reason=r.reason,
                reason_detail=r.reason_detail,
                how=HOW_SNOOZE_ENDED,
                by_name=None,
                snooze_until=snooze_until_for(r),
            )
        )
    if due:
        db.add_all(events)
        db.commit()
        db.expire_all()
    if events:
        logger.info("Fix Issues: %d %s item(s) back after more time", len(events), issue_type)
    return events


def _events_by_key(db: Session, issue_type: str) -> dict:
    out = defaultdict(list)
    rows = (
        db.query(IssueResolutionEvent)
        .filter(IssueResolutionEvent.issue_type == issue_type)
        .order_by(IssueResolutionEvent.at, IssueResolutionEvent.id)
    )
    for e in rows:
        out[e.issue_key].append(e)
    return out


def _upload_moment(day: Optional[dt.date]) -> Optional[str]:
    """An upload day as a timestamp (noon store time) for `reopened.at` when
    it came back through an upload - the day is all that's known."""
    if day is None:
        return None
    return dt.datetime.combine(day, dt.time(12, 0), restaurant_timezone()).astimezone(dt.timezone.utc).isoformat()


def _reopened_out(issue_type: str, how: str, at, reason: Optional[str], closed_by, closed_at) -> dict:
    return {
        "at": at.isoformat() if isinstance(at, dt.datetime) else at,
        "reason": reason,
        "reason_label": reason_label(issue_type, reason) if reason else None,
        "how": how,
        "closed_by": closed_by,
        "closed_at": closed_at.isoformat() if isinstance(closed_at, dt.datetime) else closed_at,
    }


def is_came_back(row: dict) -> bool:
    return bool(row.get("reopened") or row.get("came_back"))


def get_issue_list(
    db: Session,
    issue_type: str,
    ctx: Optional[_Context] = None,
    came_back_only: bool = False,
    today: Optional[dt.date] = None,
) -> dict:
    today = today or restaurant_today()
    ctx = ctx or _Context(db)
    items, latest_date = _detected(db, issue_type, ctx)
    # Barcode lists between uploads (contract item 6): when Toast's menu was
    # re-checked after the latest upload, a "fixed" close made before the
    # menu's last change and still showing comes back, and one that no
    # longer shows is confirmed.
    recheck = menu_recheck_moment(db, ctx.latest) if issue_type in BARCODE_ISSUE_TYPES else None
    # Lazy half of the "more time" reopen, so the list is right even before
    # the 9:00 job has run (or when it's switched off).
    reopen_ended_snoozes(db, issue_type, items, today)
    resolutions = {
        r.issue_key: r
        for r in db.query(IssueResolution).filter(IssueResolution.issue_type == issue_type)
    }
    events = _events_by_key(db, issue_type)

    # The Closed tab is a 7-day rolling window, not an all-time archive - older
    # closed items simply age out of it (the "Closed report" tab is the
    # all-time record, with its own date-range picker).
    closed_cutoff = today - dt.timedelta(days=6)

    def _within_window(res: IssueResolution) -> bool:
        return res.closed_at is not None and to_restaurant_date(res.closed_at) >= closed_cutoff

    # Recurring problems (negative count, low shelf): a close only covers the
    # occurrence it was made for. If an upload after the close showed the
    # problem cleared, the item showing it again is a new, open occurrence.
    recurs = ISSUE_TYPES[issue_type].get("recurs")
    recurred = set()
    if recurs:
        pending = {
            item["issue_key"]: (item, to_restaurant_date(res.closed_at))
            for item in items
            for res in [resolutions.get(item["issue_key"])]
            if res is not None
            and res.closed_at is not None
            and res.reason not in recurs["sticky"] | {NOT_AN_ISSUE}
        }
        recurred = recurs["cleared"](db, pending)

    def _history(key: str) -> dict:
        evs = events.get(key, [])
        return {"times_closed": sum(1 for e in evs if e.event == "closed")}

    def _closed_out(res: IssueResolution) -> dict:
        until = snooze_until_for(res)
        # Additive: "Back on <date>" while a "more time" close is running.
        return {**_resolution_out(issue_type, res), "snooze_until": until.isoformat() if until and until > today else None}

    open_items, closed_items = [], []
    for item in items:
        key = item["issue_key"]
        res = resolutions.get(key)
        hist = _history(key)
        if res is None:
            last = events.get(key, [None])[-1]
            reopened = None
            if last is not None and last.event == "reopened" and last.how != HOW_MANUAL:
                closes = [e for e in events[key] if e.event == "closed"]
                prev = closes[-1] if closes else None
                reopened = _reopened_out(
                    issue_type, last.how, last.at, last.reason,
                    prev.by_name if prev else None, prev.at if prev else None,
                )
            open_items.append({**item, **hist, "reopened": reopened})
            continue
        if key in recurred:
            # A recurring problem that cleared after the close and is back.
            reopened = _reopened_out(
                issue_type, HOW_DETECTED_AGAIN, _upload_moment(latest_date), res.reason,
                res.closed_by_name, res.closed_at,
            )
            open_items.append({**item, **hist, "reopened": reopened})
            continue
        by_upload = latest_date is not None and latest_date > to_restaurant_date(res.closed_at)
        # Between uploads, only when the menu itself still shows the problem
        # (its sku is just the first barcode - see menu_shows_barcode_issue).
        by_menu = (
            recheck is not None
            and res.closed_at is not None
            and recheck > res.closed_at
            and menu_shows_barcode_issue(db, issue_type, item.get("item_id"))
        )
        came_back = res.reason == FIXED and (by_upload or by_menu)
        if came_back:
            reopened = _reopened_out(
                issue_type, HOW_FIXED_STILL_SHOWING,
                _upload_moment(latest_date) if by_upload else recheck.isoformat(), res.reason,
                res.closed_by_name, res.closed_at,
            )
            open_items.append(
                {**item, **hist, "came_back": _resolution_out(issue_type, res), "reopened": reopened}
            )
        elif _within_window(res):
            closed_items.append({**item, **_closed_out(res)})

    # Closed rows whose item no longer shows the problem (fixed, deleted in
    # Toast, sold through) still belong in the Closed list.
    present = {i["issue_key"] for i in items}
    for key, res in resolutions.items():
        if key in present or not _within_window(res):
            continue
        row = {
            "issue_key": key,
            "item_id": res.item_id,
            "name": res.item_name,
            "category": res.category,
            "no_longer_detected": True,
            **_closed_out(res),
        }
        if res.reason == FIXED and res.closed_at is not None:
            # Additive: a check newer than the close (an upload, or for
            # barcodes the hourly menu re-check) no longer shows it.
            row["fix_confirmed"] = bool(
                (latest_date is not None and latest_date > to_restaurant_date(res.closed_at))
                or (recheck is not None and recheck > res.closed_at)
            )
        closed_items.append(row)
    closed_items.sort(key=lambda i: i.get("closed_at") or "", reverse=True)

    # Additive (W4, Oct 14): po_section / lead / lead_task on every row, so
    # lists can be grouped by PO section and filtered by lead. Never fails
    # the list.
    try:
        from app.services.issue_leads import annotate_rows

        annotate_rows(ctx.lead_state, issue_type, open_items)
        annotate_rows(ctx.lead_state, issue_type, closed_items)
    except Exception:  # noqa: BLE001
        logger.exception("Fix Issues: section / lead annotation failed")

    came_back_count = sum(1 for i in open_items if is_came_back(i))
    if came_back_only:
        open_items = [i for i in open_items if is_came_back(i)]

    return {
        "issue_type": issue_type,
        "label": ISSUE_TYPES[issue_type]["label"],
        "inventory_snapshot_date": latest_date.isoformat() if latest_date else None,
        "open_count": len(open_items),
        "closed_count": len(closed_items),
        # Additive (Sep 30): open rows that were closed and came back.
        "came_back_count": came_back_count,
        # Additive: live Toast counts on the two stock-condition lists
        # (inventory_api.overlay_live_stock); null = snapshot counts.
        "live_stock": live_stock_meta() if issue_type in LIVE_STOCK_ISSUE_TYPES else None,
        # Additive: barcode lists - when Toast's menu was last re-checked
        # newer than the latest upload (null = the upload is the source).
        "menu_checked_at": recheck.isoformat() if recheck is not None else None,
        "open": open_items,
        "closed": closed_items,
    }


def get_issue_history(db: Session, issue_type: str, issue_key: str) -> dict:
    """Every close and reopen of one item, newest first."""
    rows = (
        db.query(IssueResolutionEvent)
        .filter(IssueResolutionEvent.issue_type == issue_type, IssueResolutionEvent.issue_key == issue_key)
        .order_by(IssueResolutionEvent.at.desc(), IssueResolutionEvent.id.desc())
        .all()
    )
    return {
        "issue_type": issue_type,
        "issue_key": issue_key,
        "times_closed": sum(1 for e in rows if e.event == "closed"),
        "events": [
            {
                "event": e.event,
                "at": e.at.isoformat() if e.at else None,
                "by": e.by_name,
                "reason": e.reason,
                "reason_label": reason_label(issue_type, e.reason) if e.reason else None,
                "reason_detail": e.reason_detail,
                "how": e.how,
                "snooze_until": e.snooze_until.isoformat() if e.snooze_until else None,
            }
            for e in rows
        ],
    }


def reopen_all_ended_snoozes(db: Session, today: Optional[dt.date] = None) -> list[IssueResolutionEvent]:
    """The daily job's half: every list with a "more time" close that has
    ended. Only those lists are loaded."""
    today = today or restaurant_today()
    due_types = sorted(
        {
            r.issue_type
            for r in db.query(IssueResolution).filter(IssueResolution.reason.in_(list(SNOOZE_DAYS)))
            if r.issue_type in ISSUE_TYPES and (snooze_until_for(r) or dt.date.max) <= today
        }
    )
    if not due_types:
        return []
    ctx = _Context(db)
    events = []
    for issue_type in due_types:
        items, _ = _detected(db, issue_type, ctx)
        events += reopen_ended_snoozes(db, issue_type, items, today)
    return events


def warm_fix_issues_cache(bind=None, refresh: bool = False) -> None:
    """Pre-compute the forecast behind the Slow / Dead lists (the slow part
    of the Fix Issues page, ~3s on real data) so the first page load after a
    new inventory upload, a restart or the nightly sales sync is fast.

    Run from a background task / scheduler job only - never inside a
    request. `bind` is the engine to read (default: the app's), and
    `refresh` drops the cached result first (new sales, same upload). Never
    raises: a failure is logged and the page just computes on first load."""
    try:
        if bind is None:
            from app.db import engine as bind
        if refresh:
            clear_forecast_cache()
        db = Session(bind=bind)
        try:
            _Context(db).forecasts
        finally:
            db.close()
    except Exception:
        logger.exception("Fix Issues cache warm-up failed; the page will compute it on first load")


def _open_by_lead(rows: list[dict]) -> tuple[dict, int]:
    from app.services.issue_leads import summary_by_lead

    return summary_by_lead(rows)


def get_summary(db: Session) -> list[dict]:
    ctx = _Context(db)  # one inventory / sales / forecast load for all lists
    out = []
    for issue_type, cfg in ISSUE_TYPES.items():
        data = get_issue_list(db, issue_type, ctx)
        open_by_lead, open_no_lead = _open_by_lead(data["open"])
        out.append(
            {
                "issue_type": issue_type,
                "label": cfg["label"],
                "open_count": data["open_count"],
                "closed_count": data["closed_count"],
                "came_back_count": data["came_back_count"],
                # Additive (W4): open items per lead (user id -> count).
                "open_by_lead": open_by_lead,
                "open_no_lead": open_no_lead,
            }
        )
    return out


class IssueCloseError(ValueError):
    pass


def close_issues(
    db: Session,
    issue_type: str,
    issue_keys: list[str],
    reason: str,
    reason_detail: Optional[str],
    closed_by: Optional[str],
) -> int:
    if reason not in dict(reasons_for(issue_type)):
        raise IssueCloseError("Pick a reason from the list.")
    detail = (reason_detail or "").strip() or None
    if reason == OTHER and not detail:
        raise IssueCloseError("Type a few words about why, for Other.")

    items, _ = _detected(db, issue_type)
    by_key = defaultdict(list)
    for item in items:
        by_key[item["issue_key"]].append(item)

    now = _now()  # patchable in tests (store-local snooze day)
    days = SNOOZE_DAYS.get(reason)
    snooze_until = to_restaurant_date(now) + dt.timedelta(days=days) if days else None
    closed = 0
    for key in dict.fromkeys(issue_keys):
        found = by_key.get(key)
        existing = (
            db.query(IssueResolution)
            .filter(IssueResolution.issue_type == issue_type, IssueResolution.issue_key == key)
            .one_or_none()
        )
        if not found and existing is None:
            continue  # not a current issue - nothing to close
        row = existing or IssueResolution(issue_type=issue_type, issue_key=key)
        if found:
            row.item_id = None if issue_type == "duplicate" else found[0]["item_id"]
            row.item_name = found[0].get("name")
            row.category = found[0].get("category")
        row.reason = reason
        row.reason_detail = detail
        row.closed_by_name = closed_by
        row.closed_at = now
        row.snooze_until = snooze_until
        if existing is None:
            db.add(row)
        db.add(
            _event(
                issue_type, key, "closed", at=now, item_id=row.item_id, item_name=row.item_name,
                reason=reason, reason_detail=detail, by_name=closed_by, snooze_until=snooze_until,
            )
        )
        closed += 1
    db.commit()
    return closed


def reopen_issues(
    db: Session, issue_type: str, issue_keys: list[str], reopened_by: Optional[str] = None
) -> int:
    rows = (
        db.query(IssueResolution)
        .filter(IssueResolution.issue_type == issue_type, IssueResolution.issue_key.in_(issue_keys))
        .all()
    )
    for r in rows:
        db.add(
            _event(
                issue_type, r.issue_key, "reopened", item_id=r.item_id, item_name=r.item_name,
                reason=r.reason, how=HOW_MANUAL, by_name=reopened_by,
            )
        )
        db.delete(r)
    db.commit()
    return len(rows)


def get_closed_report(db: Session, start: dt.date, end: dt.date) -> dict:
    """Closed issues grouped by the (store-local) day they were closed, newest
    day first, with per-type and per-reason counts."""
    rows = db.query(IssueResolution).order_by(IssueResolution.closed_at.desc()).all()
    days: dict = {}
    by_reason_total: dict = defaultdict(int)
    for r in rows:
        # A close stored for an issue type that no longer exists (retired, or
        # renamed) is left out rather than failing the whole report.
        if r.issue_type not in ISSUE_TYPES:
            continue
        day = to_restaurant_date(r.closed_at)
        if day < start or day > end:
            continue
        entry = days.setdefault(
            day, {"date": day.isoformat(), "total": 0, "by_type": defaultdict(int), "items": []}
        )
        label = reason_label(r.issue_type, r.reason)
        entry["total"] += 1
        entry["by_type"][r.issue_type] += 1
        by_reason_total[label] += 1
        entry["items"].append(
            {
                "issue_type": r.issue_type,
                "issue_label": ISSUE_TYPES.get(r.issue_type, {}).get("label", r.issue_type),
                "issue_key": r.issue_key,
                "name": r.item_name,
                "category": r.category,
                "reason": r.reason,
                "reason_label": label,
                "reason_detail": r.reason_detail,
                "closed_by": r.closed_by_name,
                "closed_at": r.closed_at.isoformat(),
            }
        )
    out_days = []
    for day in sorted(days, reverse=True):
        e = days[day]
        e["by_type"] = dict(e["by_type"])
        out_days.append(e)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": sum(d["total"] for d in out_days),
        "by_reason": dict(sorted(by_reason_total.items(), key=lambda kv: -kv[1])),
        "days": out_days,
    }
