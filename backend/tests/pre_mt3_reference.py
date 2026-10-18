"""Pre-MT3 reference copies (multi-store M1 batch MT3) - DO NOT EDIT.

The Toast-JSON functions as they were at aa7688e (before MT3 split them into
the Toast adapter's mapping + the POS-neutral sync), copied verbatim from
services/sales_sync.py, services/open_orders.py and services/inventory_api.py.
tests/test_pos_toast_mapping.py feeds the same Toast payloads to these and to
the new code and requires identical results (the same idea as
checklist_legacy_reference.py). Only the imports below were added; the old
modules' helpers they call (latest_csv_row_per_item, sales_by_item, ...)
are unchanged in services/inventory_api.py.
"""

import datetime as dt
import logging
from collections import defaultdict
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot
from app.pos.adapters.toast.catalog import MenuItemInfo, menu_price, merged_barcode, parse_menus
from app.pos.adapters.toast.timeparse import parse_toast_time
from app.services.inventory_api import (
    API_SOURCE_PREFIX,
    CARRIED_FIELDS,
    SALES_WINDOWS,
    _not_api,
    effective_quantity,
    latest_csv_date,
    latest_csv_row_per_item,
    sales_by_item,
    sales_synced_through,
)
from app.timeutil import restaurant_today

logger = logging.getLogger("pre_mt3_reference")


def _norm(v) -> str:
    return (v or "").strip().lower()


def _parse_toast_timestamp(raw: Optional[str], field_name: str) -> Optional[dt.datetime]:
    if not raw:
        return None
    # Toast returns e.g. "2026-08-23T18:28:45.000+0000" - a +HHMM offset with no
    # colon, which datetime.fromisoformat can't parse (even on 3.11+, since the
    # fractional seconds aren't exactly 3 or 6 digits in all cases). strptime's
    # %z accepts both the colon and no-colon offset forms.
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        logger.warning("Could not parse order %s %r", field_name, raw)
        return None


def _flatten_selection(
    selection: dict,
    order_guid: str,
    check_guid: str,
    opened_at: Optional[dt.datetime],
    paid_at: Optional[dt.datetime],
    order_dining_option: Optional[str],
    order_voided: bool,
    check_voided: bool,
    parent_selection_guid: Optional[str],
) -> list[dict]:
    """One selection can carry its own nested `modifiers` (e.g. "extra cheese"),
    each of which is itself a full selection object with its own guid/price/tax
    and can in turn carry further modifiers (e.g. a combo's sub-choices). Every
    level gets its own `orders` row - modifiers have real, separate prices and
    were previously silently dropped, undercounting captured revenue.
    """
    quantity = float(selection.get("quantity") or 0)
    net_price = float(selection.get("price") or 0)
    unit_price = net_price / quantity if quantity else net_price
    tax_amount = float(selection.get("tax") or 0)
    item_obj = selection.get("item") or {}
    selection_guid = selection.get("guid", "")

    rows = [
        {
            "toast_order_guid": order_guid,
            "toast_check_guid": check_guid,
            "toast_selection_guid": selection_guid,
            "parent_selection_guid": parent_selection_guid,
            "opened_at": opened_at,
            "paid_at": paid_at,
            "item_guid": item_obj.get("guid"),
            "item_name": selection.get("displayName") or selection.get("name"),
            "quantity": quantity,
            "unit_price": unit_price,
            "net_price": net_price,
            "tax_amount": tax_amount,
            "voided": order_voided or check_voided or bool(selection.get("voided", False)),
            "dining_option": order_dining_option,
        }
    ]

    for modifier in selection.get("modifiers", []) or []:
        rows.extend(
            _flatten_selection(
                modifier,
                order_guid,
                check_guid,
                opened_at,
                paid_at,
                order_dining_option,
                order_voided,
                check_voided,
                parent_selection_guid=selection_guid,
            )
        )
    return rows


def _extract_line_items(raw_order: dict) -> list[dict]:
    """Flatten a raw Toast order JSON object into one dict per selection (line item),
    including nested modifier selections.
    """
    order_guid = raw_order.get("guid", "")
    opened_at = _parse_toast_timestamp(raw_order.get("openedDate"), "openedDate")
    order_paid_at = _parse_toast_timestamp(raw_order.get("paidDate"), "paidDate")
    order_created_at = _parse_toast_timestamp(raw_order.get("createdDate"), "createdDate")
    # Toast's order payload only returns a diningOption reference (guid), not a human
    # name — resolving that would need a separate call to the Restaurant Config API,
    # out of scope for this phase. Store the guid as-is.
    order_dining_option = (raw_order.get("diningOption") or {}).get("guid")
    # A deleted order (or check, below) never counts as a sale - same as a
    # voided one. Toast keeps returning it with `deleted: true`.
    order_voided = bool(raw_order.get("voided", False)) or bool(raw_order.get("deleted", False))

    items = []
    for check in raw_order.get("checks", []) or []:
        check_guid = check.get("guid", "")
        check_voided = bool(check.get("voided", False)) or bool(check.get("deleted", False))
        # Prefer the check's own paidDate (a split-check order can have checks
        # paid at different times); fall back to the order-level paidDate.
        # Only fall back further to createdDate (placement time) when the
        # order/check is actually voided - a comped/voided check never gets a
        # real paidDate from Toast, but it's excluded from every revenue total
        # downstream via the `voided` flag regardless of what date it lands
        # on, so this fallback is purely cosmetic for that case. A real,
        # non-voided order that simply hasn't been paid yet (e.g. a
        # prepaid-later future-pickup order still marked unpaid) must NOT get
        # this fallback - it hasn't actually generated revenue yet, and using
        # createdDate here was counting its price as "sold" on the day it was
        # placed rather than the day (if ever) it's actually paid, inflating
        # Items Sold for a day that hadn't really earned that money. Leaving
        # check_paid_at as None for that case lets the existing "no paid_at ->
        # skip/fall back to business_date" handling in items_sold.py exclude
        # or defer it correctly instead.
        check_paid_at = _parse_toast_timestamp(check.get("paidDate"), "check paidDate") or order_paid_at
        if check_paid_at is None and (order_voided or check_voided):
            check_paid_at = order_created_at
        for selection in check.get("selections", []) or []:
            items.extend(
                _flatten_selection(
                    selection,
                    order_guid,
                    check_guid,
                    opened_at,
                    check_paid_at,
                    order_dining_option,
                    order_voided,
                    check_voided,
                    parent_selection_guid=None,
                )
            )
    return items


def _employee_name(emp: dict) -> str:
    chosen = (emp.get("chosenName") or "").strip()
    first = (emp.get("firstName") or "").strip()
    last = (emp.get("lastName") or "").strip()
    full = " ".join(p for p in (first, last) if p)
    if chosen and full:
        return f"{full} ({chosen})"
    return chosen or full or "Unknown"


def _order_total(order: dict) -> float:
    total = 0.0
    for check in order.get("checks") or []:
        amt = check.get("totalAmount")
        if isinstance(amt, (int, float)):
            total += amt
    return round(total, 2)


def _line_items(order: dict) -> list[dict]:
    """Top-level (non-modifier, non-voided) items on this order, reusing
    sales_sync.py's own selection-flattening so modifier/voided handling
    can't drift between "what counts as a sold item" and "what shows up
    here" (see the modifier-double-counting gotcha in the project skill).
    """
    items = []
    for row in _extract_line_items(order):
        if row["parent_selection_guid"] is not None or row["voided"]:
            continue
        items.append({"name": row["item_name"] or "Item", "quantity": row["quantity"]})
    return items


def _tab_names(o: dict) -> list:
    """Each check's Toast `tabName` (the name typed on the tab - usually the
    customer's), in check order, trimmed and de-duplicated (case-insensitive).
    Deleted/voided checks are skipped, as are blank names and a tab name that
    is just the order's own number (Toast sometimes fills the tab with it,
    which would read "Check #12 - Tab: 12"). Toast Orders API schema: Check
    object `tabName` (string, optional) - missing on most counter orders.
    """
    display_number = str(o.get("displayNumber") or "").strip()
    seen = set()
    names = []
    for c in o.get("checks") or []:
        if not isinstance(c, dict) or c.get("deleted") or c.get("voided"):
            continue
        raw = c.get("tabName")
        if not isinstance(raw, str):
            continue
        name = " ".join(raw.split())
        if not name or name == display_number:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def tab_label(tab_names: Optional[list]) -> Optional[str]:
    """"Ravi" / "Ravi, Priya" for display, None when no check has a tab name."""
    names = [n for n in (tab_names or []) if n]
    return ", ".join(names) if names else None


def legacy_display_number(order: dict) -> Optional[str]:
    """The tab name folded into `display_number`, for app builds 5-7 that
    only show display_number ("Order #12" card/detail title, "#12" on the
    Home tile; never parsed or used as a key, only shown and substring-
    searched - see mobile open_order_card.dart / open_orders_screen.dart /
    today_card.dart / open_orders_filter.dart at 2ad4fbc). "12 · Tab: Ravi"
    -> "Order #12 · Tab: Ravi". Unchanged when there's no tab name."""
    number = order.get("display_number")
    label = order.get("tab_name")
    if not label:
        return number
    return f"{number if number not in (None, '') else '?'} \u00b7 Tab: {label}"


def _paid_at(o: dict) -> Optional[dt.datetime]:
    """The order is only "paid" if every check on it has a paidDate - a
    split-check order with one paid, one still open isn't fully settled.
    Returns the latest of the checks' paidDate (when it became fully paid).
    """
    checks = o.get("checks") or []
    if not checks:
        return None
    paid_dates = []
    for c in checks:
        d = _parse_toast_timestamp(c.get("paidDate"), "check paidDate")
        if d is None:
            return None  # at least one check unpaid -> order not fully paid
        paid_dates.append(d)
    return max(paid_dates)


def _promised_at(o: dict) -> Optional[dt.datetime]:
    # promisedDate is the guest-facing commitment (e.g. a scheduled pickup/
    # delivery time); estimatedFulfillmentDate is Toast's own estimate when
    # no explicit promise was made. Same "future order" pattern as gotcha
    # #12 in the project skill - a guest can pay tonight for tomorrow.
    raw = o.get("promisedDate") or o.get("estimatedFulfillmentDate")
    return _parse_toast_timestamp(raw, "promisedDate") if raw else None


def _placed_at(o: dict) -> Optional[dt.datetime]:
    """When the order was actually placed. Toast's `openedDate` is NOT that for
    a scheduled order: a guest who pays on the 23rd for a pickup on the 25th
    gets openedDate = the 25th (real order #105, Sep 2026 - gotcha #12), so the
    app showed "opened" in the future and counted "open for" from it.
    `createdDate` is the real placement time; openedDate is only a fallback."""
    return _parse_toast_timestamp(o.get("createdDate"), "createdDate") or _parse_toast_timestamp(
        o.get("openedDate"), "openedDate"
    )


def _is_scheduled(promised_at: Optional[dt.datetime], now: dt.datetime) -> bool:
    """Still waiting for a future pickup/delivery time - expected to be open,
    not something to chase."""
    return promised_at is not None and promised_at > now


def _normalize_open_order(o: dict, business_date: dt.date, names_by_guid: dict, now: dt.datetime) -> dict:
    server = o.get("server") or {}
    server_guid = server.get("guid")
    opened_at = _placed_at(o)
    elapsed_minutes = None
    if opened_at is not None:
        elapsed_minutes = round((now - opened_at).total_seconds() / 60)
    paid_at = _paid_at(o)
    promised_at = _promised_at(o)
    tab_names = _tab_names(o)
    return {
        "guid": o.get("guid"),
        "business_date": business_date.isoformat(),
        "display_number": o.get("displayNumber"),
        "opened_at": opened_at.isoformat() if opened_at else None,
        "elapsed_minutes": elapsed_minutes,
        "server_guid": server_guid,
        "server_name": names_by_guid.get(server_guid, "Unknown"),
        "num_guests": o.get("numberOfGuests"),
        "approval_status": o.get("approvalStatus"),
        "total_amount": _order_total(o),
        "num_checks": len(o.get("checks") or []),
        "tab_names": tab_names,
        "tab_name": tab_label(tab_names),
        "line_items": _line_items(o),
        "paid_at": paid_at.isoformat() if paid_at else None,
        "promised_at": promised_at.isoformat() if promised_at else None,
        "scheduled": _is_scheduled(promised_at, now),
    }


def _placement(info: MenuItemInfo, carried) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if carried is not None:
        mine = (_norm(carried.category_group), _norm(carried.category), _norm(carried.subcategory))
        for menu, group, sub in info.paths:
            if (_norm(menu), _norm(group), _norm(sub)) == mine:
                return carried.category_group, carried.category, carried.subcategory
    p = info.placement()
    return p if p else (None, None, None)


def format_status(status: Optional[str], qty: Optional[float]) -> str:
    """The CSV's own wording: "932 in stock", "1,210 in stock",
    "0.485 in stock", "out of stock", or "in stock" (count not tracked)."""
    if status == "OUT_OF_STOCK":
        return "out of stock"
    if qty is None:
        return "in stock"
    if float(qty).is_integer():
        text = f"{int(qty):,}"
    else:
        text = f"{qty:,.3f}".rstrip("0").rstrip(".")
    return f"{text} in stock"


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _r(v: Optional[float], digits: int = 2) -> Optional[float]:
    return None if v is None else round(v, digits)


def build_api_rows(
    db: Session,
    stock: list,
    menus: dict,
    snapshot_date: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    sales_end_offset_days: int = 1,
) -> Tuple[Dict[str, dict], dict]:
    """item_id -> InventorySnapshot field dict (not written), plus build
    stats. Pure apart from reading our DB."""
    snapshot_date = snapshot_date or restaurant_today()
    now = now or dt.datetime.now(dt.timezone.utc)
    source = API_SOURCE_PREFIX + now.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    menu = parse_menus(menus)
    stock_by_guid = {s.get("guid"): s for s in stock if s.get("guid")}
    carried = latest_csv_row_per_item(db)
    latest_day = latest_csv_date(db)
    groups = {
        _norm(g)
        for (g,) in db.execute(
            select(InventorySnapshot.category_group).where(InventorySnapshot.snapshot_date == latest_day, _not_api()).distinct()
        )
    } if latest_day else set()
    sales = sales_by_item(db, snapshot_date, sales_end_offset_days)

    rows: Dict[str, dict] = {}
    stats = defaultdict(int)
    for guid, info in menu.items():
        prev = carried.get(guid)
        group, category, sub = _placement(info, prev)
        if prev is None and _norm(group) not in groups:
            stats["skipped_not_inventory"] += 1
            continue
        s = stock_by_guid.get(guid)
        status = (s or {}).get("status")
        out_of_stock = status == "OUT_OF_STOCK"
        if status == "QUANTITY" and s.get("quantity") is not None:
            qty = round(float(s["quantity"]), 3)  # Toast sends float noise (85.39999999999989)
        elif out_of_stock:
            qty = 0.0
        else:
            qty = None
        price = menu_price(info)
        cost = prev.cost if prev is not None else None
        sold = sales.get(guid) or {"units": {n: 0.0 for n in SALES_WINDOWS}, "dollars": {n: 0.0 for n in SALES_WINDOWS}}
        units90 = sold["units"][90]
        if qty is None:
            doh = None
        elif qty <= 0:
            doh = 0.0
        elif units90 > 0:
            doh = round(qty / (units90 / 90.0), 1)
        else:
            doh = None
        name = info.name
        if prev is not None and prev.name and _norm(prev.name) in (_norm(info.item_name), _norm(info.base_name)):
            name = prev.name  # the CSV's spelling, while the menu still agrees with it
        row = {
            "snapshot_date": snapshot_date,
            "item_id": guid,
            "name": name,
            "category_group": group,
            "category": category,
            "subcategory": sub,
            "inventory_status": format_status(status, qty),
            "inventory_quantity": qty,
            "inventory_cost": _r(qty * cost) if qty is not None and cost is not None else None,
            "inventory_value": _r(qty * price) if qty is not None and price is not None else None,
            "inventory_days_on_hand": doh,
            "price": price,
            "gross_margin": _r((price - cost) / price * 100) if price and cost is not None else None,
            "gross_profit": _r(price - cost) if price is not None and cost is not None else None,
            "barcode": merged_barcode(info.sku, prev.barcode if prev is not None else None),
            "source_filename": source,
            "uploaded_at": now,
            "api_out_of_stock": True if out_of_stock else None,
            "csv_quantity": (prev.inventory_quantity if prev is not None else None) if out_of_stock else None,
        }
        for n in SALES_WINDOWS:
            row[f"last_{n}_day_orders"] = sold["units"][n]
            row[f"last_{n}_day_sales"] = sold["dollars"][n]
        for f in CARRIED_FIELDS:
            row[f] = getattr(prev, f) if prev is not None else None
        rows[guid] = row
        stats["rows"] += 1
        stats["out_of_stock" if out_of_stock else ("counted" if qty is not None else "not_tracked")] += 1
        if prev is None:
            stats["new_items_no_csv"] += 1
    stats["stock_rows"] = len(stock_by_guid)
    stats["stock_not_in_menu"] = sum(1 for g in stock_by_guid if g not in menu)
    meta = {
        "source_filename": source,
        "snapshot_date": snapshot_date.isoformat(),
        "built_at": now.isoformat(),
        "carried_from_csv_date": latest_day.isoformat() if latest_day else None,
        "sales_through": (snapshot_date - dt.timedelta(days=sales_end_offset_days)).isoformat(),
        "sales_synced_through": _iso(sales_synced_through(db)),
        "menu_last_updated": _iso(parse_toast_time(menus.get("lastUpdated"))),
        "stats": dict(stats),
    }
    return rows, meta


def live_quantity(snap, status: Optional[str], qty) -> Optional[float]:
    if status == "QUANTITY" and qty is not None:
        return round(float(qty), 3)
    if status == "OUT_OF_STOCK":
        current = effective_quantity(snap)
        # Toast says "0 or below": keep a known negative count, else 0.
        return current if current is not None and current < 0 else 0.0
    return getattr(snap, "inventory_quantity", None)
