"""One glossary for every status, tag and label the website and the app show
(owner, Sep 29: "show all the definitions of what critical, review,
intermittent, etc means ... so that everyone knows what it means").

Served at GET /api/glossary. The website and the app look terms up by `key`
(the app also bundles a copy so it works offline / on first launch).

Rules quote the real thresholds by importing the constants the code uses, so
the text can't drift from the behaviour (tests/test_ops_glossary.py checks
this too). A few thresholds live as literals inside a function
(`demand_forecast.exception_flags`) - they are mirrored here and the test
checks them against the function's actual behaviour.

Wording: plain English for store staff, 1-2 sentences, no em/en dashes.

Final pass (docs/DESIGN_SYSTEM.md 4.2): the response also carries the
**concept terms** (Store ID, Lead time, Container...) written once in
content/help/glossary.json (keys "concept_*", categories in
glossary-categories.json) and merged here, and every term gains `area`
(the colour family it belongs to), `pages` (the help page ids where it shows),
`where` (text) and `related` (keys).
"""

import datetime as dt
from typing import Optional

from app.services.delivery_review import QTY_SWING_THRESHOLD
from app.services.demand_forecast import DEFAULT_CONFIG as FC
from app.services.purchase_order_log import DUPLICATE_WINDOW, PDF_REUSE_WINDOW, PLACED_CONVERT_WINDOW
from app.services.reconciliation import DEFAULT_INVOICE_GAP_WINDOW_DAYS
from app.services.reorder import (
    CRITICAL_MAX_DAYS_LEFT,
    CRITICAL_MIN_WEEKLY_SALES,
    EMPTY_ORDER_NOW_MIN_WEEKLY_SALES,
    FAST_SELLER_WEEKLY_SALES,
    ORDERED_GRACE_DAYS,
    VENDOR_CRITICAL_MIN_ITEMS,
    VENDOR_INACTIVE_AFTER_DAYS,
    VENDOR_MIN_ORDER_ITEMS,
)
from app.services.stock_issues import (
    FORECAST_LOOKBACK_DAYS,
    NEW_ARRIVAL_MAX_DAYS,
    NEW_ARRIVAL_MIN_DAYS,
    REFILL_STORE_MAX_QTY,
    ZERO_STOCK_MIN_AGE_DAYS,
    ZERO_STOCK_NO_SALE_DAYS,
)
from app.services.vendor_calendar import CONFIRM_WINDOW_DAYS
from app.services.fix_issues import SNOOZE_DAYS
from app.services.checklist_public import DEFAULT_MAX_PER_HOUR as PUBLIC_LINK_DEFAULT_PER_HOUR
from app.services import flyers as _flyers
from app.services import critical_dismissals as _CD
from app.services import delivery_suggestions as _DS
from app.services import invoice_close_suggestions as _ICS
from app.services import schedule_suggestions as _SS
from app.services import inventory_api as _INV
from app.services import po_send as _PO
from app.services import sales_accuracy as _SA
from app.services import insights as _INS
from app.services import insights_data as _INSD
from app.config import Settings as _Settings
from app.services import order_suggestion as _OS

# 2: concept terms merged in; area / pages / where / related on every term.
GLOSSARY_VERSION = 2

# Literals inside demand_forecast.exception_flags (checked by the tests).
PROLONGED_STOCKOUT_DAYS = 7
PROLONGED_STOCKOUT_SHARE = 0.5
DIFFERS_MIN_WEEKLY_GAP = 3
DIFFERS_MIN_RATIO = 3
# The app marks an open order "check" (open too long) after this many minutes
# (mobile lib/features/orders/models/open_order.dart `isStale`).
OPEN_ORDER_STALE_MINUTES = 120


def _default(name: str):
    """A setting's built-in default (not this server's .env value), so the
    text - and the app's bundled copy - is the same everywhere."""
    return _Settings.model_fields[name].default


def _times(csv: str) -> str:
    """"06:30,12:30" -> "6:30 AM and 12:30 PM"."""
    labels = [_ampm(dt.time(*(int(x) for x in part.strip().split(":")))) for part in csv.split(",") if part.strip()]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _n(value: float) -> str:
    """2.0 -> "2", 0.84 -> "0.84"."""
    value = float(value)
    return str(int(value)) if value == int(value) else ("%g" % value)


def _pct(share: float) -> str:
    return _n(round(share * 100, 1)) + "%"


def _hours(delta) -> str:
    return _n(delta.total_seconds() / 3600)


def _ampm(t) -> str:
    """dt.time(21, 0) -> "9 PM", dt.time(9, 30) -> "9:30 AM"."""
    hour = t.hour % 12 or 12
    minutes = f":{t.minute:02d}" if t.minute else ""
    return f"{hour}{minutes} {'AM' if t.hour < 12 else 'PM'}"


_SNOOZE = _n(SNOOZE_DAYS["give_time"]) + " days (one week)" if SNOOZE_DAYS["give_time"] == 7 else _n(SNOOZE_DAYS["give_time"]) + " days"
# What happens after a close, per fix_issues.reason_outcome() (the test
# checks each reason's text has the right one).
_STAYS = "It stays closed for good, even if a later upload still shows it."
_RECHECKED = (
    "Checked again against the next upload: if an upload from a later day still shows the problem, it comes "
    "back to Open."
)
_ONE_TIME = (
    "Covers this time only: once the problem clears, the next time it happens it comes back as a new open item."
)

_TREND_UP = _pct((FC.trend_max - 1) / FC.trend_dampening)
_TREND_DOWN = _pct((1 - FC.trend_min) / FC.trend_dampening)

CATEGORIES = [
    {"key": "reorder", "label": "Reorder urgency", "where": "Reorder Candidates, Home, app Reorder"},
    {"key": "forecast", "label": "Item type and forecast", "where": "New order, Reorder, Fix Issues"},
    {"key": "review", "label": "Review reasons", "where": "New order, Fix Issues (Slow moving - Review)"},
    {"key": "projection", "label": "Projection numbers", "where": "New order, Reorder, Vendor Calendar"},
    {"key": "similar", "label": "Similar products and other vendors", "where": "New order"},
    {"key": "fix_issues", "label": "Fix Issues lists", "where": "Fix Issues (website and app)"},
    {"key": "placed_orders", "label": "Placed orders", "where": "Placed Orders, Purchase Order Cart, Home"},
    {"key": "open_orders", "label": "Open checks", "where": "Open Checks"},
    {"key": "sales", "label": "Sales", "where": "Items Sold, Home (Sales accuracy: admins)"},
    {"key": "invoices", "label": "Invoices", "where": "Invoice Submission, app Invoices"},
    {"key": "vendor_calendar", "label": "Vendor calendar", "where": "Vendor Calendar, Home"},
    {"key": "delivery", "label": "Delivery review", "where": "Delivery Review (Transfer Review is retired)"},
    {"key": "reconciliation", "label": "Reconciliation", "where": "Reconciliation"},
    {"key": "reports", "label": "Inventory reports", "where": "Inventory Reports (website), app Reports"},
    {"key": "marketing", "label": "Marketing", "where": "Flyers (website and app)"},
    {"key": "insights", "label": "Sales insights", "where": "Insights (website and app; Loss watch and Odd checks: admins)"},
    {"key": "other", "label": "Other labels", "where": "Various"},
]


def _t(
    key: str,
    label: str,
    category: str,
    definition: str,
    rule: Optional[str] = None,
    tone: Optional[str] = None,
    aliases: Optional[list] = None,
) -> dict:
    return {
        "key": key,
        "label": label,
        "category": category,
        "definition": definition,
        "rule": rule,
        # Pill colour, where the term shows as a pill: success (green),
        # warning (amber), danger (red), info (blue), neutral (grey), violet.
        "tone": tone,
        "aliases": aliases or [],
    }


TERMS = [
    # ---- Reorder urgency (reorder.py _urgency / _vendor_urgency) ----
    _t(
        "urgency_critical",
        "Critical",
        "reorder",
        "A steady seller that is out of stock, or about to be. Order it now: we are losing sales.",
        f"Sells {_n(CRITICAL_MIN_WEEKLY_SALES)} or more a week AND is either empty (0 or less on hand with no sale "
        f"in the last 7 days) or has {_n(CRITICAL_MAX_DAYS_LEFT)} days of stock or less.",
        "danger",
        ["critical", "urgent", "red"],
    ),
    _t(
        "urgency_order_now",
        "Order now",
        "reorder",
        "Stock will run out before the next delivery, so it has to go on this order.",
        "Days of stock left is within the vendor's lead time (its protection period when it has a Vendor Calendar "
        f"schedule). Also an empty item that sells at least {_n(EMPTY_ORDER_NOW_MIN_WEEKLY_SALES)} but under "
        f"{_n(CRITICAL_MIN_WEEKLY_SALES)} a week.",
        "warning",
        ["order now"],
    ),
    _t(
        "urgency_check_count",
        "Check count",
        "reorder",
        "{pos} says 0 or less on hand, but it sold this week, so the count is wrong, not the shelf. "
        "Count the shelf and fix it in {pos} before ordering.",
        "On hand is 0 or less AND it sold at least 1 unit in the last 7 days.",
        "violet",
        ["check count", "wrong count"],
    ),
    _t(
        "urgency_can_wait",
        "Can wait",
        "reorder",
        "There is enough stock for this delivery; it can go on the next order.",
        "Runs out between 1x and 2x the lead time.",
        "info",
        ["can wait"],
    ),
    _t(
        "urgency_low_priority",
        "Low priority",
        "reorder",
        "Plenty of stock, or it hardly sells. No need to order yet.",
        f"More than 2x the lead time of stock, or empty and selling under {_n(EMPTY_ORDER_NOW_MIN_WEEKLY_SALES)} "
        "a week. An inactive vendor is always Low priority.",
        "neutral",
        ["low priority", "low"],
    ),
    _t(
        "urgency_ordered",
        "Ordered",
        "reorder",
        "An order to this vendor is already placed and on the way, so its items are not urgent until it arrives. "
        "Stock numbers still show as they are.",
        "The vendor's latest order was marked placed after its last recorded delivery (Delivery Review, logged "
        "purchase, uploaded invoice or {pos} receiving). Clears when a delivery is recorded, or "
        f"{_n(ORDERED_GRACE_DAYS)} days after the expected delivery (the next scheduled delivery on the Vendor "
        "Calendar, else the lead time). Only that vendor's items change; the same item under another vendor keeps "
        "its real status.",
        "success",
        ["ordered", "on order", "order placed"],
    ),
    _t(
        "vendor_urgency",
        "Vendor status",
        "reorder",
        "A vendor's pill is its most urgent item, but only when there is enough for a real order (vendors have "
        "minimum orders).",
        f"Critical or Order now needs at least {VENDOR_MIN_ORDER_ITEMS} items that are Critical or Order now "
        "between them; with fewer, the vendor shows Check count if any item needs a count check, otherwise Can "
        "wait. Critical also needs "
        f"{VENDOR_CRITICAL_MIN_ITEMS}+ Critical items or one Critical item selling {_n(FAST_SELLER_WEEKLY_SALES)}+ "
        "a week; otherwise Order now.",
        None,
        ["vendor critical", "tier counts"],
    ),
    _t(
        "vendor_inactive",
        "Inactive",
        "reorder",
        "A vendor we seem to have stopped ordering from. Listed last and never urgent.",
        f"No delivery, logged purchase, uploaded invoice or placed order in the last {VENDOR_INACTIVE_AFTER_DAYS} "
        "days.",
        "neutral",
        ["inactive vendor"],
    ),
    _t(
        "lead_time",
        "Lead time",
        "reorder",
        "How many days the stock has to last until the order arrives. Urgency is judged against it.",
        "The vendor's protection period when it has a Vendor Calendar schedule, otherwise the lead time entered "
        "on the page.",
        None,
        ["lead days"],
    ),
    _t(
        "last_placed",
        "Last placed",
        "reorder",
        "When someone last marked an order to this vendor as placed.",
        "Only Placed orders count here, not PDF-only entries.",
    ),
    # ---- Critical vendors hidden from Today (services/critical_dismissals.py) ----
    _t(
        "critical_close",
        "Close (critical vendor)",
        "reorder",
        "An admin marked a critical vendor as handled and picked a reason (ordered or will order, not ordering "
        "from them right now, stock counts are wrong, or other). It is hidden from the Today Critical tile and the "
        "morning critical-vendors notification, for everyone.",
        "Comes back as soon as an item that was not critical when it was closed turns critical for that vendor "
        f"(the same items still critical, or fewer of them, keep it hidden), and after {_n(_CD.CLOSE_MAX_DAYS)} "
        "days at most. Reorder Candidates still lists it, tagged Hidden from Today; an admin can Show again.",
        None,
        ["close", "closed", "handled", "hidden from today"],
    ),
    _t(
        "critical_skip_week",
        "Skip this week",
        "reorder",
        "An admin hid a critical vendor from the Today Critical tile and the morning critical-vendors notification "
        "for the rest of this week, for everyone.",
        "Comes back when the next store week starts: Monday at midnight (12 AM) store time ({store_city}). On a Sunday "
        "that is the next day. Reorder Candidates still lists it; an admin can Show again sooner.",
        None,
        ["skip this week", "skipped this week", "skip week"],
    ),
    _t(
        "critical_snooze",
        "Remind me later",
        "reorder",
        "An admin hid a critical vendor from the Today Critical tile and the morning critical-vendors notification "
        "until a chosen time, for everyone.",
        f"Later today: {_n(_CD.LATER_TODAY_HOURS)} hours from now, but never past {_ampm(_CD.LATER_TODAY_LATEST)} "
        f"(after {_ampm(_CD.LATER_TODAY_CUTOFF)} it is tomorrow {_ampm(_CD.SNOOZE_TIME)} instead). Tomorrow: "
        f"{_ampm(_CD.SNOOZE_TIME)}. In {_n(_CD.SNOOZE_IN_DAYS)} days: {_ampm(_CD.SNOOZE_TIME)} that day. Pick a "
        f"date: {_ampm(_CD.SNOOZE_TIME)} that day, up to {_n(_CD.SNOOZE_MAX_DAYS_AHEAD)} days ahead. Store time "
        "({store_city}); it comes back at that time.",
        None,
        ["remind me later", "snooze", "snoozed", "remind later"],
    ),
    # ---- Item type (demand_forecast.py classes) ----
    _t(
        "class_regular",
        "Regular",
        "forecast",
        "Sells most days. Its forecast uses its own weekday pattern and recent trend.",
        f"Sold on {FC.regular_min_sale_days}+ days AND on at least {_pct(FC.regular_min_sale_share)} of the days "
        "it was in stock (in the chosen sales window).",
        "success",
        ["regular"],
    ),
    _t(
        "class_intermittent",
        "Intermittent",
        "forecast",
        "Sells now and then, not every day. A few quiet days are not read as demand disappearing.",
        f"Sold on more than {FC.slow_max_sale_days} days and on at least {_pct(FC.slow_max_sale_share)} of its "
        f"in-stock days, but not often enough to be Regular (fewer than {FC.regular_min_sale_days} selling days, "
        f"or under {_pct(FC.regular_min_sale_share)} of in-stock days).",
        "warning",
        ["intermittent", "sells occasionally"],
    ),
    _t(
        "class_slow",
        "Slow",
        "forecast",
        "Only a handful of sales. Forecast is a plain average with no safety buffer.",
        f"Sold on {FC.slow_max_sale_days} days or fewer, or on less than {_pct(FC.slow_max_sale_share)} of the "
        "days it was in stock.",
        "info",
        ["slow", "slow moving"],
    ),
    _t(
        "class_dead",
        "Dead",
        "forecast",
        "Not selling at all while it was on the shelf. Forecast is 0, so it is never suggested for ordering.",
        f"No sale in the last {FC.dead_quiet_days} days (or the whole sales window, if that is shorter) while it "
        f"was in stock for at least {_n(FC.dead_min_available_days)} of those days.",
        "danger",
        ["dead", "dead stock", "no sales"],
    ),
    _t(
        "item_type_colours",
        "Item type colours",
        "forecast",
        "The small type pill next to an item's name: green = Regular, amber = Intermittent, blue = Slow, red = Dead.",
        None,
        None,
        ["pill colours", "colors", "item type"],
    ),
    _t(
        "sales_window",
        "Sales window",
        "forecast",
        "How far back sales are looked at to work out demand (for example 3 months).",
        f"Fix Issues uses the New order default of {FORECAST_LOOKBACK_DAYS} days. Vendor Calendar and "
        "the Home Critical tile use the last 30 days.",
        None,
        ["lookback", "window"],
    ),
    # ---- Review (supplier_projection._REVIEW_FLAGS + exception_flags) ----
    _t(
        "review",
        "Review",
        "review",
        "The forecast for this item may be off, so check it before trusting the suggested quantity. "
        "Hover or tap to see why.",
        "Shown when any of these is true: Not enough history, Long stockout, Sales spike capped, Unusual trend, "
        "Negative on-hand, Very different from simple average.",
        "danger",
        ["review", "needs review", "red tag"],
    ),
    _t(
        "flag_insufficient_history",
        "Not enough history",
        "review",
        "The item was in stock for too few days to judge how well it sells.",
        f"Fewer than {_n(FC.insufficient_available_days)} in-stock days in the sales window.",
        "warning",
    ),
    _t(
        "flag_prolonged_stockout",
        "Long stockout",
        "review",
        "It has been out of stock for a long time, so recent sales say little about demand.",
        f"Out of stock for the last {PROLONGED_STOCKOUT_DAYS}+ days in a row, or out of stock on more than "
        f"{_pct(PROLONGED_STOCKOUT_SHARE)} of the sales window.",
        "warning",
    ),
    _t(
        "flag_demand_spike",
        "Sales spike capped",
        "review",
        "One unusually big day (a bulk or catering buy) was trimmed so it doesn't inflate the forecast.",
        f"Any single day above {_n(FC.spike_floor)} units or {_n(FC.spike_multiple)}x the item's typical (median) "
        "selling day, whichever is bigger, is counted at that cap. Only checked when it sold on 3 or more days.",
        "warning",
        ["spike"],
    ),
    _t(
        "flag_unusual_trend",
        "Unusual trend",
        "review",
        "Sales recently jumped or dropped a lot compared to before. The forecast only follows part of it.",
        f"The last {FC.trend_recent_days} days sold more than {_TREND_UP} faster or {_TREND_DOWN} slower than "
        f"the {FC.trend_baseline_days} days before (Regular items only).",
        "warning",
    ),
    _t(
        "flag_negative_inventory",
        "Negative on-hand",
        "review",
        "{pos} shows less than 0 on hand, which can't be real. The count needs fixing.",
        "Shelf + container count below 0 in the latest inventory upload.",
        "warning",
    ),
    _t(
        "flag_differs_from_history",
        "Very different from simple average",
        "review",
        "The new forecast is far from the plain average of past sales, so double-check it.",
        f"The two differ by {DIFFERS_MIN_WEEKLY_GAP}+ units a week AND one is {DIFFERS_MIN_RATIO}x the other "
        "(or one of them is 0).",
        "warning",
    ),
    _t(
        "flag_missing_inventory_history",
        "No inventory counts",
        "review",
        "{pos} has no stock counts for this item, so days out of stock can't be seen. Information only.",
        None,
        "warning",
    ),
    _t(
        "flag_intermittent",
        "Sells occasionally",
        "review",
        "The item is Intermittent. Information only, not a reason to review.",
        None,
        "warning",
    ),
    _t(
        "flag_dead_inventory",
        "No sales in 60 days",
        "review",
        "The item is Dead. Information only.",
        f"No sale in the last {FC.dead_quiet_days} days while it was in stock for at least "
        f"{_n(FC.dead_min_available_days)} of them.",
        "warning",
    ),
    # ---- Projection numbers ----
    _t(
        "avg_demand",
        "Demand / day, / week",
        "projection",
        "How many units the item sells on an average day (or week), counting only days it was on the shelf.",
        f"For Regular and Intermittent items recent days count more (a {_n(FC.half_life_days)}-day half-life); "
        "Slow items use a plain average. Days it was out of stock are left out and days it sold out partway "
        "count as part days. The trend is included.",
        None,
        ["demand", "avg demand", "per day"],
    ),
    _t(
        "availability",
        "On the shelf",
        "projection",
        "The share of days in the sales window the item was in stock.",
        None,
        None,
        ["availability", "stockout days"],
    ),
    _t(
        "trend",
        "Trend",
        "projection",
        "How much recent sales are up or down, applied to the forecast (x1 = flat).",
        f"Regular items only, kept between x{_n(FC.trend_min)} and x{_n(FC.trend_max)}.",
    ),
    _t(
        "weekday_pattern",
        "Weekday pattern",
        "projection",
        "How much busier or quieter each weekday is than an average day (x1 = average).",
        "The store's pattern; Regular items blend in their own.",
    ),
    _t(
        "simple_average",
        "Simple average / day",
        "projection",
        "The old way: units sold divided by all days, including days the item was out of stock.",
    ),
    _t(
        "projected_demand",
        "Projected demand",
        "projection",
        "How many units we expect to sell over the chosen number of days.",
        "Each future day is forecast separately (base demand x weekday x trend) and added up.",
        None,
        ["projected"],
    ),
    _t(
        "safety_buffer",
        "Safety buffer",
        "projection",
        "A few extra units on top of projected demand to cover normal ups and downs in sales.",
        "Only Regular and Intermittent items get one (Regular a bit more). It is bigger when daily sales jump "
        "around more and when the stock has to last longer, and a bit bigger again if the item ran out at any "
        f"point in the sales window. Never more than {_pct(FC.safety_max_share)} of projected demand. Slow and "
        "Dead items get none.",
        None,
        ["buffer", "safety stock"],
    ),
    _t(
        "need_to_order",
        "Need to order",
        "projection",
        "How many units to order so the stock lasts the chosen period.",
        "Projected demand + safety buffer - on hand (shelf + container; a negative count counts as 0), rounded "
        f"up to whole units (a leftover of {_n(FC.round_down_below)} or less is dropped). "
        "A * means the item has no stock count, so the number shown is projected demand.",
        None,
        ["need", "to order", "shortfall"],
    ),
    _t(
        "suggested_order_qty",
        "Suggested order qty",
        "projection",
        "The quantity filled in when you tick an item on New order (and shown on each cart line), with "
        "the numbers it comes from beside it: sold per week, on hand (and its average), the next delivery.",
        "Need to order for the order window (projected demand + safety buffer - on hand, a negative count "
        f"counting as 0, rounded up to whole units; {_n(_OS.ROUND_DOWN_BELOW)} or less is dropped), minus what is "
        "already in other vendors' carts for the same item. If the pack size is known (this vendor's cart line "
        "has Case of above 1, or - for an item not in this vendor's cart - the last placed order from this vendor "
        "did), it is turned into cases, rounded up; an item in this vendor's cart always uses that line's Case of "
        "(1 = units). Never below 0; 0 means enough stock for the window. With no stock count it is the projected "
        f"sales for the window (stock not subtracted). \"avg\" is the average daily count over the last "
        f"{_n(_OS.AVG_ON_HAND_DAYS)} days. When the next delivery is already ordered, it says so and how many of "
        "the item are on that order; the quantity is not lowered by it. New order uses the projection "
        "duration you picked; the cart uses its own order-for period (1 week unless changed). You can always "
        "change the quantity.",
        None,
        ["suggested qty", "suggested", "qty to order", "how many to order"],
    ),
    _t(
        "protection_days",
        "Protection period",
        "projection",
        "How many days today's stock has to last: past the next delivery you can still order for, until the "
        "delivery after that arrives.",
        "From the vendor's Vendor Calendar schedule (delivery days and lead days). Only for vendors with a schedule.",
        None,
        ["protection days", "use protection period"],
    ),
    _t(
        "container_qty",
        "Container qty",
        "projection",
        "Extra stock kept in the Container storage location, added to the shelf count.",
        "{pos} lists it as a second row with the same name, no price and no vendor.",
        None,
        ["container", "+ container"],
    ),
    _t(
        "avg_on_hand",
        "Avg on hand",
        "projection",
        "The item's average stock count over the days of that week, shown next to what sold.",
        "Taken from the daily inventory counts (the morning count of each day), with the Container row added in. "
        "Only days with a count are averaged - \"6 of 7 days\" means one day had no count - and \"-\" means no "
        "day did. A negative number is {pos}'s own count going below zero.",
        None,
        ["average on hand", "on hand", "stock that week"],
    ),
    _t(
        "in_cart",
        "In cart",
        "projection",
        "The item is already in the purchase order cart for this vendor.",
    ),
    # ---- Similar products / other vendors ----
    _t(
        "also_at",
        "Also at",
        "similar",
        "Other vendors {pos} lists for this item, with that vendor's own item code when {pos} has it.",
        None,
        None,
        ["other vendors"],
    ),
    _t(
        "similar_products",
        "Similar",
        "similar",
        "Other products in the store of the same kind (other brands, other sizes, close variants), best matches "
        "first.",
        "Matched on the cleaned-up name (size and brand taken off, spellings made the same). "
        "Kitchen, catering, event menu and Container rows are never listed.",
        None,
        ["similar products", "substitutes"],
    ),
    _t(
        "similar_same_other_brand",
        "Same product, other brand",
        "similar",
        "The same product made by a different brand.",
        None,
        "info",
    ),
    _t(
        "similar_same_other_size",
        "Same product, different size",
        "similar",
        "The same product in a different pack size.",
        None,
        "info",
    ),
    _t(
        "similar_possible_duplicate",
        "Same product and size - possible duplicate listing",
        "similar",
        "Looks like the same product, brand and size listed twice in {pos}. Worth checking.",
        None,
        "info",
    ),
    _t("similar_same_product", "Same product", "similar", "The same product (brand or size unknown).", None, "info"),
    _t(
        "similar_type_other_brand",
        "Similar type, other brand",
        "similar",
        "A close variant (not the exact product) from a different brand.",
        None,
        "info",
    ),
    _t(
        "similar_type_same_brand",
        "Similar type, same brand",
        "similar",
        "A close variant (not the exact product) from the same brand.",
        None,
        "info",
    ),
    _t("similar_type", "Similar type", "similar", "A close variant of the product, not the exact same one.", None, "info"),
    # ---- Fix Issues (fix_issues.ISSUE_TYPES / stock_issues.py) ----
    _t(
        "issue_missing_barcode",
        "Missing barcodes",
        "fix_issues",
        "Items with no barcode in {pos}, so they can't be scanned at the register.",
    ),
    _t(
        "issue_invalid_barcode",
        "Invalid barcodes",
        "fix_issues",
        "The barcode in {pos} is badly formed (wrong length, letters, or a wrong check digit).",
        "Checked against the GS1 rules for UPC-A (12), EAN-8, EAN-13 and GTIN-14.",
    ),
    _t(
        "issue_duplicate",
        "Duplicates",
        "fix_issues",
        "Two or more priced items in {pos} with exactly the same name.",
        "Container rows (same name, no price) are not duplicates and are left out.",
    ),
    _t(
        "issue_dead_stock",
        "Dead inventory",
        "fix_issues",
        "The older list: items with stock and either no sales in {pos}'s own 90-day sales figure, or 90+ days of "
        "stock on hand. Replaced by Dead with stock.",
    ),
    _t(
        "issue_negative_inventory",
        "Negative inventory",
        "fix_issues",
        "{pos} shows less than 0 on hand. A shelf can't hold less than nothing, so the count needs fixing.",
        "On hand below 0 in the latest upload. A close covers this time only: if it goes negative again after "
        "being 0 or more, it comes back. Closing it as Loose / weighed, Discontinued or Not an issue keeps it "
        "closed for good.",
        None,
        ["negative", "below zero"],
    ),
    _t(
        "issue_refill_from_container",
        "Refill from container",
        "fix_issues",
        "The shelf is low but there is stock in the Container. Bring some out.",
        f"Shelf (priced row) at {REFILL_STORE_MAX_QTY} or less while the Container row has more than 0. A close "
        f"covers this time only: once the shelf is above {REFILL_STORE_MAX_QTY} or the container is empty, it can "
        "come back the next time it runs low.",
        None,
        ["refill", "container"],
    ),
    # ---- Store vs Container report (services/store_vs_container.py) ----
    _t(
        "store_vs_container",
        "Low on shelf (Store vs Container)",
        "reports",
        "Store vs Container lists only products that have both a store (shelf) row and a Container row in {pos}, "
        "with the stock in each. Low on shelf means the shelf is low but there is stock in the Container. Bring "
        "some out.",
        f"Rows are paired by exact name in the latest upload: one priced row (store) and one row with no price "
        f"and no vendor (Container). Names with two priced rows are different products and are left out. Low on "
        f"shelf: store at {REFILL_STORE_MAX_QTY} or less while the Container has more than 0, the same rule as "
        "Fix Issues' Refill from container.",
        "warning",
        ["store vs container", "low on shelf", "container stock", "shelf stock"],
    ),
    _t(
        "issue_slow_review",
        "Slow moving - Review",
        "fix_issues",
        "Slow items whose forecast also has the red Review tag. Check the count and sales look right.",
        f"New order forecast ({FORECAST_LOOKBACK_DAYS}-day window) classes it Slow AND it needs Review "
        "AND shelf + container is above 0 (an empty slow item isn't listed). \"Why review\" shows the reason.",
        None,
        ["slow review"],
    ),
    _t(
        "issue_dead_with_stock",
        "Dead with stock",
        "fix_issues",
        "Items that aren't selling but still have stock. Mark down, return, move or stop ordering.",
        f"Forecast classes it Dead (no sale in {FC.dead_quiet_days} days while in stock) AND shelf + container is "
        "above 0.",
        None,
        ["dead stock", "dead inventory"],
    ),
    _t(
        "issue_dead_zero_stock",
        "Dead - zero stock",
        "fix_issues",
        "Items that aren't selling and have nothing left. Decide whether to stop reordering, deactivate them in "
        "{pos}, or reorder on purpose because they should sell.",
        f"Shelf + container is 0 AND either the forecast classes it Dead (no sale in {FC.dead_quiet_days} days while "
        f"in stock), or it has had no sale in {ZERO_STOCK_NO_SALE_DAYS} days (or never sold) and isn't brand new (still "
        "waiting for its first delivery): it has sold before, or it first showed up in the uploads, or was last "
        f"received, at least {ZERO_STOCK_MIN_AGE_DAYS} days before the latest upload. If the shelf or Container count "
        "is below 0 it is on Negative inventory instead, so an item is never on both. Items with no count in {pos} at "
        "all are not listed.",
        None,
        ["dead zero stock", "zero stock", "out of stock dead"],
    ),
    _t(
        "issue_dead_new_arrival",
        "Dead - newly arrived",
        "fix_issues",
        "Products that have never sold a single unit, arrived (or were restocked) recently, and still have stock.",
        f"Never sold, arrived {NEW_ARRIVAL_MIN_DAYS} to {NEW_ARRIVAL_MAX_DAYS} days before the latest upload, and "
        "has stock. Items that ever sold before are never listed.",
        None,
        ["newly arrived", "new arrival"],
    ),
    _t(
        "issue_came_back",
        "Came back",
        "fix_issues",
        "It was closed before and is open again, because the problem is still there. Three ways: the week of "
        "more time ran out, it was closed as fixed but a later upload still shows it, or a problem that comes "
        "and goes (negative count, low shelf) happened again.",
        "The pill says which, with the date and the reason it was closed with. Use the Came back only chip to see "
        "just these. Closing it again works as usual.",
        "warning",
        ["came back", "reopened", "back again"],
    ),
    _t(
        "came_back_snooze_ended",
        "Came back: more time ended",
        "fix_issues",
        f"It was closed as \"New item - giving it more time\". That gives it {_SNOOZE} and the item still "
        "shows on this list, so it moved back to Open.",
        "Checked every morning at 9 AM (and whenever the list is opened). One notification a day lists every "
        "item that came back this way.",
        "warning",
        ["more time ended", "snooze ended"],
    ),
    _t(
        "came_back_fixed_but_still_showing",
        "Came back: still showing after fix",
        "fix_issues",
        "It was closed as fixed (for example Count corrected in {pos}), but an inventory upload from a later day "
        "still shows the problem, so it's open again.",
        "Usually the change wasn't saved in {pos}, or the upload was taken before the fix.",
        "warning",
        ["still showing", "fixed but still showing"],
    ),
    _t(
        "came_back_detected_again",
        "Came back: happened again",
        "fix_issues",
        "Negative inventory and Refill from container come and go. The problem cleared after it was closed and "
        "has now happened again, so it's a new open item.",
        None,
        "warning",
        ["happened again", "detected again"],
    ),
    _t(
        "issue_snoozed",
        "Back on (more time)",
        "fix_issues",
        f"Closed as \"New item - giving it more time\". It stays closed for {_SNOOZE}; on the date shown it "
        "comes back to Open if the item still shows on the list.",
        "If the item has sold by then (or is no longer on the list), it simply stays closed.",
        "info",
        ["back on", "more time", "snoozed", "give it more time"],
    ),
    _t(
        "issue_times_closed",
        "Times closed",
        "fix_issues",
        "How many times this item has been closed on this list. More than once means it keeps coming back.",
        "Every close and reopen is kept in the item's history.",
        None,
        ["closed before", "history"],
    ),
    _t(
        "issue_closed",
        "Closed (reason)",
        "fix_issues",
        "Someone closed the item with a reason. What happens next depends on the reason: some keep it closed "
        "for good, the \"fixed\" reason is checked again against the next upload, \"giving it more time\" "
        f"brings it back after {_SNOOZE}, and on Negative inventory and Refill from container most reasons cover "
        "this time only.",
        "The Closed tab shows the last 7 days. Each reason's own definition says what it does.",
        "success",
        ["closed", "reason"],
    ),
    # ---- Fix Issues close reasons (fix_issues.ISSUE_TYPES). Looked up by
    # reason_term(): reason_<issue_type>_<reason> first, else reason_<reason>
    # (same meaning on every list). Each says what happens next, in the words
    # tests/test_ops_snooze_cartremove.py checks against reason_outcome(). ----
    _t(
        "reason_give_time",
        "New item - giving it more time",
        "fix_issues",
        "It's new and hasn't sold yet, so give it a fair chance on the shelf before doing anything.",
        f"It comes back after {_SNOOZE}: on that day, if it still hasn't sold, it moves back to Open (with a "
        "Came back pill and a notification). If it has sold by then, it stays closed.",
        "info",
        ["more time", "give it more time", "new item"],
    ),
    _t(
        "reason_fixed",
        "Count corrected in {pos}",
        "fix_issues",
        "The on-hand count in {pos} was wrong and has been corrected.",
        _RECHECKED,
        "success",
        ["count corrected"],
    ),
    _t(
        "reason_missing_barcode_fixed",
        "Barcode added in {pos}",
        "fix_issues",
        "The missing barcode has been typed into {pos}.",
        _RECHECKED,
        "success",
    ),
    _t(
        "reason_invalid_barcode_fixed",
        "Barcode corrected in {pos}",
        "fix_issues",
        "The bad barcode has been corrected in {pos}.",
        _RECHECKED,
        "success",
    ),
    _t(
        "reason_duplicate_fixed",
        "Merged / removed in {pos}",
        "fix_issues",
        "The extra copies were merged or deleted in {pos} so only one item has this name.",
        _RECHECKED,
        "success",
    ),
    _t(
        "reason_negative_inventory_fixed",
        "Count corrected in {pos} (negative)",
        "fix_issues",
        "The count was fixed in {pos}, so it is 0 or more now.",
        _RECHECKED + " " + _ONE_TIME,
        "success",
    ),
    _t(
        "reason_refill_from_container_fixed",
        "Moved stock to the shelf",
        "fix_issues",
        "Stock was brought out of the Container onto the shelf, and the counts were moved in {pos}.",
        _RECHECKED + " " + _ONE_TIME,
        "success",
    ),
    _t(
        "reason_no_barcode_needed",
        "No barcode needed",
        "fix_issues",
        "A loose or weighed item (produce, bulk) that is rung up by name or PLU, so it never needs a barcode.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_in_house_code",
        "Store / in-house code",
        "fix_issues",
        "The code is our own store code, not a real UPC/EAN, and it scans fine as is.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_discontinued",
        "Discontinued",
        "fix_issues",
        "We no longer carry or reorder this item.",
        _STAYS,
        "neutral",
        ["item discontinued", "won't reorder"],
    ),
    _t(
        "reason_different_products",
        "Different products (size / variant)",
        "fix_issues",
        "The items share a name but are really different sizes or variants, so they are not duplicates.",
        _STAYS + " Tip: add the size to the names in {pos}.",
        "neutral",
    ),
    _t(
        "reason_markdown",
        "Marked down",
        "fix_issues",
        "The price was lowered (clearance or promotion) to help it sell.",
        _STAYS,
        "neutral",
        ["clearance", "promotion"],
    ),
    _t(
        "reason_returned",
        "Returned to vendor",
        "fix_issues",
        "The stock went back to the vendor.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_seasonal",
        "Seasonal - keep stocking",
        "fix_issues",
        "It sells at a certain time of year (a festival, a season), so slow months are expected.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_deactivated",
        "Deactivated in {pos}",
        "fix_issues",
        "The item was turned off in {pos}, so it is no longer sold or counted.",
        _STAYS,
        "neutral",
        ["deactivate", "turned off"],
    ),
    _t(
        "reason_reorder",
        "Reorder - it should sell",
        "fix_issues",
        "The item should sell, so it goes back on the next order instead of being dropped.",
        _STAYS + " Once it is restocked it leaves this list.",
        "neutral",
        ["reorder", "should sell"],
    ),
    _t(
        "reason_dead_zero_stock_seasonal",
        "Seasonal - will restock",
        "fix_issues",
        "It only sells at a certain time of year (a festival, a season), so it is out for now and will be "
        "restocked before then.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_moved_display",
        "Moved to a better shelf / display",
        "fix_issues",
        "The item was moved somewhere customers will see it.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_reviewed_ok",
        "Reviewed - sales look right",
        "fix_issues",
        "Someone checked the count and the sales, and the slow forecast is correct.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_order_less",
        "Will order less",
        "fix_issues",
        "It sells slowly, so smaller or less frequent orders from now on.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_sold_before_received",
        "Sold before the delivery was received",
        "fix_issues",
        "Items were sold before the delivery was received in {pos}, so the count dipped below 0 for a while.",
        _ONE_TIME,
        "neutral",
    ),
    _t(
        "reason_wrong_item_rung",
        "Rung up under the wrong item",
        "fix_issues",
        "Cashiers rang a different product under this item, so its count went below 0.",
        _ONE_TIME,
        "neutral",
    ),
    _t(
        "reason_loose_weighed",
        "Loose / weighed item - count not tracked",
        "fix_issues",
        "Sold by weight or loose, and we don't keep a real count, so a negative number is expected.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_container_count_wrong",
        "Container count is wrong",
        "fix_issues",
        "There isn't really stock in the Container; its count in {pos} is wrong.",
        _ONE_TIME,
        "neutral",
    ),
    _t(
        "reason_store_count_wrong",
        "Shelf count is wrong - shelf is stocked",
        "fix_issues",
        "The shelf is actually full; its count in {pos} is too low.",
        _ONE_TIME,
        "neutral",
    ),
    _t(
        "reason_keep_in_container",
        "Keeping it in the container for now",
        "fix_issues",
        "We are choosing not to bring it out yet.",
        _ONE_TIME,
        "neutral",
    ),
    _t(
        "reason_not_an_issue",
        "Not an issue",
        "fix_issues",
        "A false alarm: nothing is wrong with this item.",
        _STAYS + " This is true on every list, including Negative inventory and Refill from container.",
        "neutral",
        ["false alarm"],
    ),
    _t(
        "reason_other",
        "Other",
        "fix_issues",
        "Any other reason. The few words typed when closing are shown with it.",
        _STAYS,
        "neutral",
    ),
    _t(
        "reason_negative_inventory_other",
        "Other (negative)",
        "fix_issues",
        "Any other reason. The few words typed when closing are shown with it.",
        _ONE_TIME,
        "neutral",
    ),
    _t(
        "reason_refill_from_container_other",
        "Other (refill)",
        "fix_issues",
        "Any other reason. The few words typed when closing are shown with it.",
        _ONE_TIME,
        "neutral",
    ),
    # ---- Placed orders (purchase_order_log.py) ----
    _t(
        "placed_status_placed",
        "Placed",
        "placed_orders",
        "Someone pressed Mark placed (app) or Order placed (website), recording what was in that vendor's cart "
        "and emptying it.",
        f"Pressing it again within {_n(DUPLICATE_WINDOW.total_seconds() / 60)} minutes with the same items returns "
        "the same order.",
        "success",
        ["placed"],
    ),
    _t(
        "placed_status_pdf",
        "PDF only",
        "placed_orders",
        "A PO PDF was made (downloaded or shared) but nobody marked the order placed. It does not count as ordered.",
        f"Making the PDF again within {_hours(PDF_REUSE_WINDOW)}h updates the same entry; Mark placed within "
        f"{_hours(PLACED_CONVERT_WINDOW)}h turns it into the Placed order.",
        "warning",
        ["pdf", "pdf only"],
    ),
    _t(
        "order_again",
        "Order again",
        "placed_orders",
        "Puts that order's items back into the vendor's cart with the same quantities.",
    ),
    # ---- Open checks (feature key stays open_orders) ----
    _t(
        "open_order_scheduled",
        "Scheduled",
        "open_orders",
        "A paid check for a later pickup. It is expected to stay open until then, so it's not a problem.",
        "A promised pickup time still in the future (the website also requires it to be paid).",
        "info",
        ["scheduled for later"],
    ),
    _t(
        "open_order_too_long",
        "Open too long",
        "open_orders",
        "A check that has been open a long time. See whether it was served and should be closed.",
        f"App: open more than {OPEN_ORDER_STALE_MINUTES // 60} hours and not scheduled for later. Website: every open "
        "check that isn't scheduled for later is listed to look at.",
        "danger",
        ["stale", "check"],
    ),
    _t(
        "open_order_tab_name",
        "Tab name",
        "open_orders",
        "The name typed on the check's tab in {pos}, usually the customer's name. Shown as Tab: Name so you can "
        "tell whose check it is.",
        "Taken from each check on the {pos} order; an order with several checks lists each different tab name "
        "once. Not shown when the tab has no name.",
        None,
        ["tab", "tab name", "customer name", "customer"],
    ),
    # ---- Invoices (invoice_submission.py) ----
    _t(
        "invoice_pending",
        "Pending",
        "invoices",
        "Just submitted or resent; no send has finished yet.",
        None,
        "neutral",
    ),
    _t(
        "invoice_in_progress",
        "In Progress",
        "invoices",
        "Being worked on, nothing has failed: a send may still be running, WhatsApp may be Uncertain, or both "
        "sends are done and it is waiting for someone to confirm it is closed in {pos}.",
        None,
        "info",
    ),
    _t(
        "invoice_completed",
        "Completed",
        "invoices",
        "Sent to WhatsApp and {pos} and marked closed in {pos}. Transfers (and old Untracked uploads) complete once "
        "WhatsApp is sent.",
        None,
        "success",
    ),
    _t(
        "invoice_failed",
        "Failed",
        "invoices",
        "Sending to WhatsApp or the {pos} email failed. Tap or hover for the error, then Resend. (Uncertain - check "
        "WhatsApp is not counted as failed.)",
        None,
        "danger",
    ),
    _t("invoice_sent", "Sent", "invoices", "That channel (WhatsApp or {pos} email) confirmed it was sent. "
       "\"Sent (manual)\" or \"Sent (set by name)\" means an admin set it by hand.", None, "success"),
    _t("invoice_sending", "Sending", "invoices", "The send is still running in the background.", None, "neutral"),
    _t(
        "invoice_uncertain",
        "Uncertain - check WhatsApp",
        "invoices",
        "WhatsApp didn't confirm in time and we couldn't find the message. Look in the group before resending, "
        "to avoid a duplicate.",
        None,
        "warning",
        ["uncertain"],
    ),
    _t(
        "invoice_closed_in_toast",
        "Closed in {pos}",
        "invoices",
        "Someone checked {pos} and confirmed the bill is closed there. Only possible after both sends succeed.",
        "\"To close\" (app Home) counts invoices sent in the last 7 days that nobody has marked closed in {pos} yet.",
        None,
        ["closed", "to close"],
    ),
    _t(
        "invoice_close_suggested",
        "Looks closed in {pos}",
        "invoices",
        "An open invoice whose vendor was received in {pos} after the upload, so it is probably closed there. One "
        "tap marks it Closed in {pos}; Not closed yet hides the suggestion. Nothing is closed automatically.",
        f"A {{pos}} receiving of that vendor no earlier than {_hours(_ICS.UPLOAD_GRACE)} hours before the upload (or, "
        "on a day with only the nightly {pos} stock list, a delivery-like stock rise after it). With several open invoices for one vendor, "
        "the oldest gets the first receiving. Invoices only (not returns).",
        "info",
        ["looks closed", "suggested close"],
    ),
    _t(
        "invoice_untracked",
        "Untracked",
        "invoices",
        "An invoice that never becomes a {pos} bill. It goes to WhatsApp only (no {pos} email), is Completed once "
        "WhatsApp is sent, and can't be marked Closed in {pos}.",
        "No longer offered for new uploads (use Transfer for store/container moves). Older Untracked uploads still "
        "show as before.",
    ),
    _t(
        "invoice_transfer",
        "Transfer",
        "invoices",
        "Stock moved between the store and the container, uploaded with its direction: Store \u2192 Container or "
        "Container \u2192 Store. It goes to WhatsApp only (no {pos} email), is Completed once WhatsApp is sent, and "
        "can't be marked Closed in {pos}.",
        "Vendor is optional. A transfer never counts as a vendor delivery (ordering, reconciliation, reports).",
        None,
        ["store to container", "container to store", "move", "movement"],
    ),
    _t(
        "invoice_delete_requested",
        "Delete requested",
        "invoices",
        "Staff asked for this invoice to be deleted. An admin approves (moves it to Trash) or rejects.",
    ),
    _t(
        "invoice_trash",
        "Trash",
        "invoices",
        "Deleted invoices. An admin can restore them or delete them permanently from here.",
    ),
    _t(
        "invoice_override",
        "Override",
        "invoices",
        "Admin only: set a channel to Sent, Failed or Pending by hand when the automatic send is stuck. Setting "
        "Pending does not send again; use Resend for that.",
    ),
    # ---- Vendor calendar (vendor_calendar.py) ----
    _t(
        "cal_order_due",
        "Order due",
        "vendor_calendar",
        "The last day to place an order for the vendor's next delivery.",
        "Delivery day minus the vendor's lead days.",
    ),
    _t(
        "cal_items_needed",
        "Items needed",
        "vendor_calendar",
        f"New order says at least {VENDOR_MIN_ORDER_ITEMS} items need ordering for this delivery (shown in "
        f"red). Fewer is shown as a count of low items, not as urgent - vendors have minimum orders.",
        f"Need to order above 0 over the days between this delivery and the next one (using the last 30 days of "
        f"sales), for {VENDOR_MIN_ORDER_ITEMS} or more items. Not shown for a skipped or placed order.",
        "danger",
        ["urgent"],
    ),
    _t("cal_upcoming", "Upcoming", "vendor_calendar", "A delivery that hasn't happened yet.", None, "info"),
    _t(
        "cal_delivered",
        "Delivered",
        "vendor_calendar",
        "A purchase was logged for this vendor near the expected date (Delivery Review or Log a Purchase).",
        f"Within {CONFIRM_WINDOW_DAYS} days either side. An uploaded invoice alone does not count.",
        "success",
    ),
    _t(
        "cal_missed",
        "Missed",
        "vendor_calendar",
        "The delivery date has passed and no purchase was logged for it.",
        f"No purchase logged within {CONFIRM_WINDOW_DAYS} days of the date, and the order wasn't skipped.",
        "danger",
    ),
    _t(
        "cal_skipped",
        "Skipped",
        "vendor_calendar",
        "Someone chose Skip this order, so no order or delivery is expected.",
        None,
        "neutral",
        ["order skipped"],
    ),
    # ---- Today-tab order actions (services/order_actions.py, W1) ----
    _t(
        "order_placed_outside",
        "Order already placed",
        "vendor_calendar",
        "An admin said the order to this vendor was already placed some other way (phone, WhatsApp, the vendor's "
        "rep). It is recorded like Mark placed, with no items, for everyone.",
        "The order due stops showing on Today and in the order reminder, and Reorder Candidates shows the vendor as "
        "Ordered (so it leaves the Critical tile) until a delivery is recorded or the ordered window ends. Placed "
        "Orders lists it as Placed outside the app. Undo removes it.",
        "success",
        ["already placed", "placed outside", "ordered outside the app", "order placed"],
    ),
    _t(
        "order_due_other",
        "Other (order hidden)",
        "vendor_calendar",
        "An admin hid one vendor order for this cycle and wrote why, for everyone.",
        "Works like Skip this week for that one order: it leaves Today and the order reminder, and its delivery "
        "shows as Skipped with the note. Undo brings it back.",
        "neutral",
        ["other", "hide order", "hidden order"],
    ),
    _t(
        "schedule_suggested",
        "Suggested schedule",
        "vendor_calendar",
        "Delivery days read off a vendor's recent deliveries, offered for a vendor with no schedule yet. Add, edit or "
        "dismiss it, or Add all; a vendor that already has a schedule is never changed.",
        f"Over the last {_n(_SS.LOOKBACK_DAYS // 7)} weeks, every source counts: invoice uploads and hand-logged "
        f"deliveries (the delivery day), {{pos}} receiving records from the daily inventory files, Delivery Review "
        f"confirms and stock rises (the day before). Pieces up to {_n(_SS.ATTACH_DAYS)} days after an invoice are the "
        f"same delivery. A weekday counts with {_n(_SS.MIN_PER_WEEKDAY)}+ deliveries and "
        f"{_pct(_SS.MIN_WEEKDAY_SHARE)}+ of all, and the days chosen must hold {_n(_SS.MIN_COVERED)}+ deliveries and "
        f"{_pct(_SS.MIN_COVERAGE)}+ of all. Order day = the usual gap from a placed order to the delivery; with no "
        f"orders on record it's yours to set ({_n(_SS.DEFAULT_LEAD_DAYS)} day before until you do). A schedule "
        f"repeats every week, so a vendor whose deliveries are usually {_n(_SS.IRREGULAR_MIN_GAP_DAYS)}+ days apart "
        f"(every 2-4 weeks or rarer) is marked: weekly isn't right for it. Add all starts with High and Medium "
        f"ticked; Low and those vendors stay listed, unticked.",
        "info",
        ["schedule suggestion", "usually delivers", "add all suggestions", "suggested schedules"],
    ),
    _t(
        "schedule_confidence",
        "Suggestion confidence",
        "vendor_calendar",
        "How sure the suggestion is: how many deliveries fell on the suggested days, and what share of all of them.",
        f"High = {_n(_SS.HIGH_MIN_COVERED)}+ deliveries on those days and {_pct(_SS.HIGH_MIN_COVERAGE)}+ of all. "
        f"Medium = {_n(_SS.MEDIUM_MIN_COVERED)}+ and {_pct(_SS.MEDIUM_MIN_COVERAGE)}+. Anything less is Low, and so "
        f"are two days that each have fewer than {_n(_SS.MIN_PER_WEEKDAY_IF_SEVERAL)}. Check a Low one before adding it.",
        "info",
        ["confidence", "high confidence", "low confidence"],
    ),
    _t(
        "schedule_missing",
        "Vendors without a schedule",
        "vendor_calendar",
        "Every vendor {pos} lists on an item (or that delivered lately) with no schedule and no suggestion, and why.",
        f"Too few deliveries in the last {_n(_SS.LOOKBACK_DAYS // 7)} weeks, deliveries on scattered days, or a "
        "dismissed suggestion. Set schedule adds one by hand. The store's own house items aren't listed.",
        "neutral",
        ["no schedule", "set schedule", "missing schedule"],
    ),
    _t(
        "schedule_paused",
        "Paused",
        "vendor_calendar",
        "The vendor's schedule is turned off; no order or delivery days are shown for it.",
        None,
        "neutral",
    ),
    # ---- Delivery / transfer review ----
    _t(
        "delivery_new_item",
        "New",
        "delivery",
        "An item that wasn't in the previous inventory upload, so it probably arrived in this delivery.",
        None,
        "success",
    ),
    _t(
        "delivery_suggested_qty",
        "Suggested qty",
        "delivery",
        "Our guess of how much arrived. Edit it to match the invoice before confirming.",
        "Today's count - previous count + units sold today. Items that moved by "
        f"{QTY_SWING_THRESHOLD} or less (and aren't new) are left out.",
        None,
        ["suggested", "net change", "qty swing"],
    ),
    _t(
        "delivery_source",
        "Source",
        "delivery",
        "How a confirmed delivery was logged: Delivery Review, Logged manually (Log a Purchase), or Mixed (both).",
    ),
    _t(
        "transfer_direction",
        "Container -> Store / Store -> Container",
        "delivery",
        "Which way stock moved between the Container storage location and the shelf.",
        f"Suggested when the shelf count changed by more than {QTY_SWING_THRESHOLD} after allowing for sales.",
        None,
        ["transfer", "container transfer"],
    ),
    _t(
        "delivery_suggested",
        "Suggested delivery",
        "delivery",
        "A delivery the app found in the inventory uploads, one per vendor and upload. Nothing is logged until "
        "someone checks it and presses Confirm, which logs it exactly like a manual review.",
        "Mostly from {pos}'s own receiving: items whose receive time and receiving number (like IN378) are new since "
        "the previous upload, grouped under the vendor most of them list. On a day with only the nightly {pos} "
        f"stock list: {_n(_DS.RISE_MIN_ITEMS)} or more of one vendor's items rising by at least {_n(_DS.RISE_MIN_UNITS)} "
        "more than sales explain (or one, if the vendor uploaded an invoice then); a shelf rise matched by its "
        f"Container row dropping by {_pct(_DS.TRANSFER_MATCH_SHARE)} of it is a move, and a weighed item must rise by "
        f"{_pct(_DS.WEIGHED_RISE_SHARE)} of its weight. Quantity = count now - count before + units sold in between.",
        "success",
        ["suggested deliveries", "received in toast", "stock rose"],
    ),
    # ---- Reconciliation (reconciliation.py) ----
    _t(
        "recon_expected_closing",
        "Expected closing",
        "reconciliation",
        "What should be on hand at the end of the period if every sale and purchase was recorded.",
        "Opening count + purchased - sold.",
    ),
    _t(
        "recon_variance",
        "Variance",
        "reconciliation",
        "Counted minus expected. Below 0 = missing stock (waste, theft, spoilage); above 0 = an unlogged "
        "purchase or a miscount.",
        "Actual closing count - expected closing. Shown in units and dollars (at cost).",
    ),
    _t(
        "recon_invoice_gap",
        "Invoices without a logged purchase",
        "reconciliation",
        "Invoice or Return uploads with no purchase logged for that vendor around the same day (Something else "
        "uploads aren't checked).",
        f"No purchase for the vendor within {DEFAULT_INVOICE_GAP_WINDOW_DAYS} days either side of the invoice.",
    ),
    # ---- Other ----
    _t(
        "hide_sales",
        "Hide sales numbers",
        "other",
        "An admin setting per user: that person doesn't see sales dollars, but ordering and stock numbers stay.",
    ),
    _t(
        "checklist_all_clear",
        "All Clear",
        "other",
        "Every item on the checklist was marked OK.",
        None,
        "success",
    ),
    _t("checklist_issues", "Issues", "other", "At least one checklist item was flagged as a problem.", None, "danger"),
    # ---- Checklist builder (services/checklist_admin.py, checklist_engine.py) ----
    _t(
        "checklist_closing",
        "Closing checklist",
        "other",
        "A checklist an admin marked as a closing checklist. If nobody submits it by 9:30 PM it is listed in the "
        "one closing checklist notification, and the managers get a task for it.",
        "Not submitted by 9:30 PM store time (one notification lists every missing closing checklist).",
    ),
    _t(
        "checklist_version",
        "Checklist version",
        "other",
        "Every time an admin publishes changes to a checklist it gets a new version number. Past submissions keep "
        "showing the questions they answered.",
        "Published vN / Current = the version staff fill in now. Unsaved changes = edits in the editor not saved "
        "yet.",
        None,
        ["published", "current", "unsaved changes"],
    ),
    _t(
        "checklist_draft",
        "Draft",
        "other",
        "Checklist changes an admin saved but has not published yet. Staff keep seeing the published version until "
        "the draft is published.",
        None,
        "warning",
    ),
    _t(
        "checklist_public_link",
        "Public link",
        "other",
        "A link an admin turns on for one checklist so people without an app account (for example part-time "
        "service staff) can fill in that checklist only. They pick or type their name; nothing else in the app "
        "can be opened from it.",
        f"Off, renamed or replaced (Make a new link) = the old link shows \"This link is not active\". At most "
        f"the link's hourly limit (default {PUBLIC_LINK_DEFAULT_PER_HOUR}) submissions an hour.",
        None,
        ["public checklist link", "make a new link"],
    ),
    _t(
        "checklist_public_link_submission",
        "via public link",
        "other",
        "This checklist was filled in on the checklist's public link, not in the app. The name is the one the "
        "person picked or typed. It counts exactly like a submission from the app (WhatsApp, alerts, tasks).",
        None,
        "info",
    ),
    _t(
        "checklist_update_app",
        "Update the app",
        "other",
        "This checklist has questions this version of the app can't show. Update the app to fill it in.",
        None,
        "warning",
    ),
    _t(
        "job_status",
        "Job status",
        "other",
        "The last run of a background job (the nightly sales copy, inventory upload): green = worked, red = failed, "
        "amber = running.",
    ),
    _t("on_sale", "On sale", "other", "The item has a sale price in {pos}.", None, "success"),
    _t("clearance", "Clearance", "other", "The item is marked for clearance.", None, "warning"),
    # ---- PO sections (services/po_sections.py + po_section_rules.py, PO sections page) ----
    _t(
        "po_section",
        "PO section",
        "placed_orders",
        "The heading an item prints under on a vendor's purchase order PDF (for example Spices - Whole or "
        "Frozen - Ice Cream), so the supplier can pick the order shelf by shelf. Lines with no section "
        "(hand-added items, kitchen items) print under Other, always last.",
        "Automatic from the item's category ({pos}'s, with the app's category corrections). Changed on the PO "
        "sections page: an item's own section wins over a vendor rule, and a vendor rule wins over the automatic "
        "section; a rename or merge of a section then applies to all of them. On a vendor's PO that vendor's own "
        "rule is used first. A hand-added cart line can be given a section from the cart. Every change records "
        "who made it and when, and the last change can be undone.",
        "info",
        ["po sections", "picking section", "automatic section", "vendor rule", "rename section",
         "merge section", "back to automatic"],
    ),
    # Build 5's Product categories screen (its chips use this key) now edits
    # PO sections through the same API, so the text explains that.
    _t(
        "category_correction",
        "PO section change",
        "other",
        "In the app's Product categories screen, an item's category is now its PO section: the heading it "
        "prints under on vendor purchase order PDFs. Item change = this item has its own section; Vendor rule "
        "= every item from that vendor goes to one section. {pos} categories are not changed.",
        "An item's own change wins over a vendor rule. Reset to {pos} puts the item back in its automatic "
        "section. Category corrections made earlier ({pos} has an item in the wrong category) still apply "
        "everywhere but are no longer edited in the app or on the website.",
        "violet",
        ["category correction", "category override", "category rule", "category mapping", "corrected category",
         "reset to toast", "item change"],
    ),
    # ---- Purchase order cart removal (ordering.py, Sep 30) ----
    _t(
        "cart_removal_requested",
        "Removal requested",
        "placed_orders",
        "Someone asked for this line to be taken out of the cart. It stays in the cart (and on the PO) until an "
        "admin approves the request; the reason, if given, is shown with it.",
        "Admins get one notification for requests made within 10 minutes of each other. Approve removes the "
        "line, Reject keeps it and clears the request. The person who asked can cancel it.",
        "warning",
        ["request removal", "removal request"],
    ),
    _t(
        "cart_removal_admin_only",
        "Only admins remove cart items",
        "placed_orders",
        "Only an admin can delete a line, clear a vendor's cart or set a quantity to 0. Everyone else uses "
        "Request removal.",
        "Mark placed (which empties that vendor's cart into Placed Orders) is still open to everyone who uses "
        "the cart.",
        None,
        ["admin only", "delete from cart", "clear cart"],
    ),
    # ---- Glossary audit (Sep 30): labels the web / app showed without a
    # definition. ----
    _t(
        "open_order_approval",
        "Approval status ({pos})",
        "open_orders",
        "{pos}'s own approval state for an online or scheduled check, shown as it comes from {pos} (for example "
        "NEEDS_APPROVAL or FUTURE). The chip only shows when the check is not APPROVED.",
        "NEEDS_APPROVAL usually means someone has to accept the order on the {pos} device.",
        "warning",
        ["needs approval", "approval", "not approved"],
    ),
    _t(
        "target_inventory",
        "Target inventory",
        "projection",
        "How much stock you want on hand to cover the chosen days: the projected demand plus the safety buffer.",
        "Need to order = target inventory minus what's on hand (never below 0).",
        None,
        ["target", "target stock"],
    ),
    _t(
        "label_price_flagged",
        "Price flagged",
        "other",
        "The label app saw this item's price change by more than 20% and flagged it as possibly wrong. Check the "
        "price in {pos}; printing still works.",
        None,
        "warning",
        ["price flag", "suspicious price"],
    ),
    _t(
        "label_print_status",
        "Label print status",
        "other",
        "Printed = the label printer finished it. In progress = waiting for or on the printer. Failed = the "
        "printer reported an error; print it again.",
        "Price change = printed automatically because the {pos} price changed. Manual = someone printed it.",
        None,
        ["printed", "in progress", "failed", "price change", "manual"],
    ),
    _t(
        "invoice_type_return",
        "Return",
        "invoices",
        "Paperwork for goods sent back to the vendor (a credit). It goes to WhatsApp and the {pos} email like an "
        "invoice.",
        None,
        None,
        ["return", "credit"],
    ),
    _t(
        "invoice_type_other",
        "Something else",
        "invoices",
        "Any other paperwork. It is still sent to WhatsApp and the {pos} email, but it is not treated as a "
        "delivery: it's left out of Invoices without a logged purchase and of the delivery suggestions.",
        None,
        None,
        ["something else", "other submission"],
    ),
    _t(
        "checklist_issue_tags",
        "Checklist issue types",
        "other",
        "How a problem answer is grouped in the checklist summary (and the WhatsApp message): Not Turned Off "
        "(a heat source left on), Critical Issues, Morning Prep Required, Low Stock, Cleanup Not Completed, Not "
        "Done, or Issue.",
        "Listed in that order, most urgent first. In the checklist editor an option marked \"flags an issue\" "
        "files its answer under one of these.",
        "danger",
        ["not turned off", "critical issues", "morning prep required", "low stock", "cleanup not completed",
         "not done", "flags an issue"],
    ),
    _t(
        "checklist_active",
        "Active / Inactive / Not published",
        "other",
        "Active checklists that have been published show to staff. Inactive ones are hidden from staff (their "
        "past submissions stay). Not published means it has never been published, so nobody can fill it in yet.",
        None,
        None,
        ["active", "inactive", "not published"],
    ),
    _t(
        "checklist_stale",
        "Checklist not submitted (amber)",
        "other",
        "The Home checklist tile turns amber when the latest closing checklist is more than 36 hours old, "
        "which usually means last night's wasn't submitted.",
        None,
        "warning",
        ["stale checklist"],
    ),
    _t(
        "user_admin",
        "Admin",
        "other",
        "An admin can open every page, always sees sales numbers, can assign tasks and manage users.",
        "Everyone else only gets the pages (features) an admin ticks for them. A new user starts with none.",
        "violet",
        ["full access", "admin"],
    ),
    _t(
        "user_features",
        "Features (page access)",
        "other",
        "The pages a non-admin may open. With none ticked they can log in but see nothing.",
        None,
        None,
        ["features", "edit access", "tab access"],
    ),
    _t(
        "user_can_assign",
        "Can assign tasks",
        "other",
        "This person (for example a shift lead) may give tasks to other people. Admins always can.",
        None,
        None,
        ["can assign"],
    ),
    _t(
        "chat_report_status",
        "Report status",
        "other",
        "A reported chat message. Open = waiting for an admin. Resolved = an admin dealt with it (and may have "
        "removed the message). Dismissed = no action needed.",
        None,
        None,
        ["open", "resolved", "dismissed", "reported"],
    ),
    _t(
        "chat_message_deleted",
        "Message deleted",
        "other",
        "The sender deleted it, or a manager removed it (\"Removed by a manager\"). Admins can still see a reported "
        "message's text in Moderation.",
        None,
        "neutral",
        ["deleted", "removed by a manager"],
    ),
    _t(
        "chat_muted",
        "Muted",
        "other",
        "You don't get notifications for this chat unless someone @mentions you.",
        None,
        "neutral",
        ["mute"],
    ),
    _t(
        "chat_blocked",
        "Blocked",
        "other",
        "One of you blocked the other, so direct messages between you are off.",
        None,
        "neutral",
        ["block"],
    ),
    _t(
        "announcement_pinned",
        "Pinned",
        "other",
        "A manager pinned this announcement so it stays at the top.",
        None,
        "info",
        ["pin"],
    ),
    _t(
        "announcement_ack",
        "Waiting (confirm you read it)",
        "other",
        "This announcement asks everyone to confirm they read it. Waiting = not confirmed yet; once you press "
        "Confirm it shows when you did.",
        "Managers see how many people are still waiting.",
        "warning",
        ["waiting", "confirm", "you confirmed", "acknowledge"],
    ),
    _t(
        "task_requires",
        "Requires (photo / note)",
        "other",
        "The task can only be marked done with this proof attached: a photo, or a short note.",
        None,
        None,
        ["requires photo", "requires note", "proof"],
    ),
    _t(
        "task_sections",
        "Today / Upcoming / Done today",
        "other",
        "How your task list is grouped: Overdue = its due time has passed, Today = due later today, Upcoming = "
        "due on a later day or no due time, Done today = finished today.",
        None,
        None,
        ["today", "upcoming", "done today"],
    ),
    # ---- Marketing flyers (services/flyers.py, services/flyer_sender.py) ----
    _t(
        "flyer",
        "Flyer",
        "marketing",
        "A marketing picture or PDF, with an optional message, kept in the Flyers library and sent to the "
        "store's marketing WhatsApp groups.",
        f"A photo is saved as one JPEG (at most {_flyers.MAX_SIDE} px on its longest side, about "
        f"{_flyers.TARGET_MAX_BYTES // _flyers.MB} MB) and a PDF as uploaded (up to {_flyers.MAX_PDF_MB} MB). The files "
        "are kept in the store's flyers folder, one folder per category. A deleted flyer moves to "
        "the _Deleted folder; it is never removed.",
        None,
        ["flyers", "marketing flyer", "promotion"],
    ),
    _t(
        "marketing_group",
        "Marketing group",
        "marketing",
        "A WhatsApp group an admin marked \"Use for flyers\". Flyers can only go to these groups, so a flyer never "
        "reaches a staff group by accident.",
        f"Admins tick it under Manage WhatsApp groups. One send can go to up to {_flyers.MAX_GROUPS_PER_SEND} groups; "
        "they get the flyer one at a time, a few seconds apart.",
        None,
        ["use for flyers", "flyer groups", "marketing groups"],
    ),
    # -- web-added terms (below) --
    _t(
        "task_status",
        "Task status",
        "other",
        "Open = still to do. Done = finished (a team task is done once anyone on the team finishes it). "
        "Can't do = someone couldn't do it and gave a reason. Cancelled = the creator called it off.",
        None,
        None,
        ["open", "done", "can't do", "cancelled"],
    ),
    _t(
        "task_urgent_overdue",
        "Urgent / Overdue",
        "other",
        "Urgent tasks send their notification even during quiet hours. Overdue means the task is still open "
        "and its due time has passed.",
        None,
        "danger",
        ["urgent task", "overdue"],
    ),
    # -- app-added terms (below) --
    _t(
        "home_critical",
        "Critical (Home)",
        "reorder",
        "The number of vendors that need an order right away because steady sellers from them are out of stock "
        "or about to run out. Tap the tile to see them on Reorder Candidates. Admins can hide a vendor from it for "
        "a while (Close, Skip this week, Remind me later).",
        f"Active vendors whose Reorder Candidates status is Critical (last {_n(_CD.TODAY_LOOKBACK_DAYS)} days of "
        f"sales, {_n(_CD.TODAY_LEAD_TIME_DAYS)}-day lead time): at least {VENDOR_MIN_ORDER_ITEMS} items that are "
        f"Critical or Order now, including {VENDOR_CRITICAL_MIN_ITEMS}+ Critical items or one Critical item selling "
        f"{_n(FAST_SELLER_WEEKLY_SALES)}+ a week. Vendors an admin closed, skipped for the week or set to Remind me "
        "later are left out until they come back.",
        "danger",
        ["critical vendors", "home critical"],
    ),
    # -- automation batch (Oct 11): sales accuracy, live data, Inventory from Toast, Send PO --
    _t(
        "sales_addons",
        "Add-ons",
        "sales",
        "A spice level, extra cheese or other choice added to an item. {pos} already includes its price in the "
        "item's price, so Items Sold and the sales download count the item once and never add the add-on on top.",
        None,
        None,
        ["add-on", "add-ons", "modifier", "modifiers"],
    ),
    _t(
        "sales_live_today",
        "Today (live from {pos})",
        "sales",
        "Today's Items Sold and the Home sales tile come straight from {pos} when you open them, including "
        "payments made after midnight for the previous day. Refresh asks {pos} again.",
        f"{{pos}}'s business dates yesterday, today and tomorrow are pulled live and replace the stored copy of those "
        f"dates, so nothing is counted twice. One pull is shared for {_n(_default('live_sales_cache_seconds'))} "
        "seconds. Past days come from the stored sales, which the sales accuracy check keeps in step with {pos}.",
        "info",
        ["live", "today live"],
    ),
    _t(
        "sales_accuracy_match",
        "Matches",
        "sales",
        "Our stored sales for this business date equal {pos}'s own item sales for it.",
        f"Same item sales to the cent and the same quantity as {{pos}} Analytics' item report (add-ons inside their "
        f"item, discounts taken off), checked for the last {_n(_default('sales_repull_days'))} days at "
        f"{_times(_default('sales_repull_times'))} (store time).",
        "success",
        ["sales accuracy", "match"],
    ),
    _t(
        "sales_accuracy_fixed",
        "Fixed",
        "sales",
        "The day differed from {pos} (an edit made after it was copied), so it was copied again from {pos}'s orders "
        "and now matches.",
        "A day is copied again when its item sales or quantity differ from {pos}'s, {pos}'s numbers for it changed "
        f"since the last check, it still has unpaid items and was last copied over {_n(_SA.UNPAID_RESYNC_HOURS)} "
        "hours ago, or it was never copied.",
        "success",
        ["re-synced", "corrected"],
    ),
    _t(
        "sales_accuracy_differs",
        "Differs",
        "sales",
        "Still different from {pos} after copying it again. Usually {pos}'s Analytics haven't caught up with a recent "
        "edit yet ({pos} says 2 to 4 hours); a later check corrects it. Worth a look only if it stays for days.",
        None,
        "warning",
        ["mismatch", "differs"],
    ),
    _t(
        "sales_accuracy_waiting",
        "Waiting for {pos}",
        "sales",
        "This day was already copied again against the same {pos} numbers, so it isn't fetched again until {pos}'s "
        "numbers for it change. This keeps the check from asking {pos} for the same day over and over.",
        None,
        "neutral",
        ["waiting"],
    ),
    _t(
        "live_stock",
        "Live stock",
        "projection",
        "With Inventory from {pos} turned on by an admin, on hand on Reorder Candidates, New order, "
        "Negative inventory and Refill from container is {pos}'s current count, not the count from the last "
        "saved stock list.",
        f"{{pos}}'s stock list is read at most once every {_n(_default('live_stock_cache_seconds') / 60)} minutes. "
        "Out of stock in {pos} shows 0, except that an item the last upload counted below zero keeps that count. "
        "When {pos} doesn't answer, the latest saved counts are used and the page says so.",
        "info",
        ["live", "live count", "on hand live"],
    ),
    _t(
        "inventory_api_snapshot",
        "Nightly {pos} stock list",
        "reports",
        "With Inventory from {pos} turned on, the app builds each day's inventory early in the morning from {pos} "
        "itself, so the daily file upload is no longer needed. An uploaded file for the same day always replaces it.",
        f"Built at {_ampm(dt.time(_default('inventory_api_snapshot_hour'), _default('inventory_api_snapshot_minute')))}: "
        "counts, names, prices, barcodes and categories from {pos}'s stock and menu; 7, 30 and 90-day sales from "
        "our own sales through yesterday; cost, vendor, vendor code and last received carried from each item's "
        "latest uploaded file ({pos} doesn't share them). It isn't saved if {pos}'s stock list is empty or "
        f"has under {_pct(_INV.MIN_BUILD_SHARE)} of the items of the latest upload.",
        "info",
        ["api snapshot", "from toast", "inventory from toast"],
    ),
    _t(
        "inventory_upload_due",
        "Inventory upload due",
        "reports",
        "A reminder to upload {pos}'s inventory file. Even with the nightly {pos} stock list the app needs a file "
        "now and then for cost, vendor codes and last-received dates.",
        f"Sent at 9 AM while the latest uploaded file is {_n(_default('inventory_csv_max_age_days'))} or more days "
        "old. You can switch it off in your alert settings.",
        "warning",
        ["upload due", "cost refresh"],
    ),
    _t(
        "barcode_menu_recheck",
        "Re-checked in {pos}",
        "fix_issues",
        "Between uploads, Missing and Invalid barcodes are checked again against {pos}'s menu, so an item fixed in "
        "{pos} leaves the list within the hour instead of the next morning.",
        f"Every {_n(_default('menu_barcode_recheck_minutes'))} minutes the app asks {{pos}} whether the menu changed "
        "and reads it again only if it did. {pos}'s menu holds an item's first barcode only: extra barcodes in a "
        "list, and a barcode removed in {pos}, are judged at the next upload.",
        "info",
        ["barcode re-check", "menu check"],
    ),
    _t(
        "send_po",
        "Send PO",
        "placed_orders",
        "Emails the vendor's PO PDF (the same file as Download PDF) from the store's email to the vendor's saved "
        "contacts. Once it goes out, the order is recorded as placed and those lines leave the cart.",
        f"Only to emails saved under Vendor contacts (at most {_PO.MAX_RECIPIENTS}); a note of up to "
        f"{_PO.MAX_NOTE} characters. Pressing Send twice never emails twice. WhatsApp is off: the store's "
        "WhatsApp sending of POs is off for this store.",
        None,
        ["send po", "email po", "email the vendor"],
    ),
    _t(
        "vendor_contact_gets_pos",
        "Gets POs",
        "placed_orders",
        "This contact is ticked by default when a PO is sent to the vendor. You can untick it for one send.",
        None,
        "info",
        ["gets pos", "vendor contact"],
    ),
    _t(
        "po_send_sent",
        "Sent",
        "placed_orders",
        "The PO email went out, and the order was recorded as placed.",
        None,
        "success",
    ),
    _t(
        "po_send_partly_sent",
        "Partly sent",
        "placed_orders",
        "The email went out but WhatsApp didn't (or the other way round). The order still counts as placed.",
        None,
        "warning",
    ),
    _t(
        "po_send_failed",
        "Not sent",
        "placed_orders",
        "The email didn't go out, so nothing was recorded and the cart is unchanged. Try again.",
        None,
        "danger",
    ),
    _t(
        "po_send_uncertain",
        "Not confirmed",
        "placed_orders",
        "The connection broke after the email was handed over for sending, so it may have gone out. Look in the store "
        "mailbox's Sent "
        "folder before sending again; if it went, record the order as placed instead.",
        f"The next send to that vendor within {_hours(_PO.UNCERTAIN_LOOKBACK)} hours asks you to check first.",
        "warning",
        ["uncertain", "check gmail", "check sent folder"],
    ),
    _t(
        "po_send_sending",
        "Sending",
        "placed_orders",
        "The PO is being emailed right now. Only one send per vendor runs at a time.",
        None,
        "info",
    ),
]

# ---- Sales insights from Toast Analytics (services/insights.py) ----
_LOSS_DISC = _n(_default("loss_watch_large_discount"))
_LOSS_REF = _n(_default("loss_watch_large_refund"))
_LOSS_VOID = _n(_default("loss_watch_large_void"))
_LOSS_MULT = _n(_default("loss_watch_median_multiple"))
_ODD_HOURS = _n(_default("odd_check_open_hours"))
_ODD_REF = _n(_default("odd_check_large_refund"))
_NIGHTLY = _ampm(dt.time(_default("insights_nightly_hour"), _default("insights_nightly_minute")))

TERMS += [
    _t(
        "insights_source",
        "From {pos} Analytics",
        "insights",
        "These numbers come from {pos}'s own reporting ({pos} Analytics), the same figures {pos}'s sales reports "
        "show, not from the orders this app copies from {pos}.",
        f"Past days are saved once and reused. Each night at {_NIGHTLY} the app fetches yesterday and re-checks the "
        f"last {_n(_INSD.REPULL_DAYS)} days, because {{pos}} edits can land days later. Today is fetched live and "
        f"reused for {_n(_default('insights_today_cache_seconds'))} seconds.",
        "info",
        ["toast analytics", "data source", "updated"],
    ),
    _t(
        "insights_stale",
        "Not up to date",
        "insights",
        "Some of the days on this page haven't been fetched from {pos} Analytics yet, or {pos} didn't answer. "
        "You're seeing what was saved last.",
        "Missing days fill in on their own, a few at a time, so the store never goes over {pos}'s limits.",
        "warning",
        ["stale", "out of date", "still loading"],
    ),
    _t(
        "insights_net_sales",
        "Net sales",
        "insights",
        "What the store sold after discounts, refunds and voids, before tax and tips, as {pos} counts it.",
        None,
        None,
        ["net"],
    ),
    _t("insights_orders", "Orders", "insights", "{pos}'s count of orders for the day (voided orders left out).", None, None),
    _t(
        "insights_avg_check",
        "Average check",
        "insights",
        "Net sales divided by the number of orders: what one order is worth on average.",
        None,
        None,
        ["average order", "aov"],
    ),
    _t("insights_guests", "Guests", "insights", "{pos}'s guest count for the day (usually one per order).", None, None),
    _t(
        "insights_heatmap",
        "Busy hours",
        "insights",
        "Average net sales (or orders) for each hour of each weekday, so you can see when the store is busy.",
        "Each square is the average over the last 4, 8 or 12 weeks of that weekday and hour. Hours are the store's "
        "own clock. Staff with sales numbers hidden see order counts only.",
        None,
        ["heatmap", "hourly"],
    ),
    _t(
        "insights_busiest",
        "Busiest hours",
        "insights",
        f"The {_INS.HEATMAP_TOP} weekday hours with the highest average sales.",
        None,
        "success",
    ),
    _t(
        "insights_quietest",
        "Quietest hours",
        "insights",
        f"The {_INS.HEATMAP_TOP} weekday hours with the lowest average sales while the store is usually open.",
        "Only hours with orders on at least half of those weekdays count, so closed hours never show here.",
        "neutral",
    ),
    _t(
        "insights_channel",
        "Channel",
        "insights",
        "How the order came in, from {pos}'s dining option: In-store (the counter), Pick up (the kiosk), "
        "Online ordering, DoorDash, Uber Eats, Catering, or Other.",
        None,
        None,
        ["dining option", "online", "doordash", "kiosk"],
    ),
    _t(
        "insights_channel_share",
        "Share",
        "insights",
        "This channel's part of the week's net sales.",
        None,
        None,
    ),
    _t(
        "insights_vs_last_week",
        "vs last week",
        "insights",
        "Change from the same weekday one week earlier. For today, both days are counted up to the same hour.",
        None,
        None,
        ["compare"],
    ),
    _t(
        "insights_vs_4wk_avg",
        "vs 4-week average",
        "insights",
        "Change from the average of the same weekday over the 4 weeks before.",
        None,
        None,
    ),
    _t(
        "insights_vs_last_year",
        "vs last year",
        "insights",
        "Change from the same weekday 52 weeks earlier. Empty until the store has a year of sales.",
        None,
        None,
    ),
    _t(
        "insights_check_trends",
        "Check trends",
        "insights",
        "Average check, guests and orders per day and per week, to spot a slow drift up or down.",
        None,
        None,
    ),
    _t(
        "insights_loss_watch",
        "Loss watch",
        "insights",
        "Discounts, voids, refunds and comps per day and per employee, with the checks worth a second look "
        "(admins only).",
        f"Built from {{pos}}'s check report, fetched each night at {_NIGHTLY}. Admins get one push at 9 AM when "
        "yesterday has checks to review.",
        None,
        ["loss", "comps", "discounts"],
    ),
    _t("insights_discount", "Discounts", "insights", "Money taken off checks by discounts.", None, None),
    _t(
        "insights_void",
        "Voids",
        "insights",
        "Orders or items cancelled after they were rung in. Per day this is {pos}'s voided order total; per "
        "employee it's the voided items on their checks.",
        None,
        None,
    ),
    _t("insights_refund", "Refunds", "insights", "Money given back after a check was paid.", None, None),
    _t(
        "insights_comp_estimate",
        "Comps (estimate)",
        "insights",
        "Discounts on checks that ended up free (a 100% discount). {pos} doesn't mark comps separately, so this "
        "is an estimate.",
        None,
        None,
        ["comp"],
    ),
    _t(
        "insights_loss_full_comp",
        "Full comp",
        "insights",
        "The whole check was discounted to $0.",
        None,
        "danger",
    ),
    _t(
        "insights_loss_large_discount",
        "Large discount",
        "insights",
        f"A discount of ${_LOSS_DISC} or more on one check.",
        "The amount is a setting (LOSS_WATCH_LARGE_DISCOUNT).",
        "warning",
    ),
    _t(
        "insights_loss_large_refund",
        "Large refund",
        "insights",
        f"A refund of ${_LOSS_REF} or more on one check.",
        "The amount is a setting (LOSS_WATCH_LARGE_REFUND).",
        "warning",
    ),
    _t(
        "insights_loss_large_void",
        "Large void",
        "insights",
        f"Voided items worth ${_LOSS_VOID} or more on one check.",
        "The amount is a setting (LOSS_WATCH_LARGE_VOID).",
        "warning",
    ),
    _t(
        "insights_loss_above_median",
        "Unusual for this employee",
        "insights",
        f"A discount, refund or void more than {_LOSS_MULT} times this employee's usual amount (their median over "
        "30 days).",
        f"Only for employees with at least {_INS.MEDIAN_MIN_CHECKS} such checks in 30 days, and never under "
        f"${_n(_INS.MEDIAN_MIN_AMOUNT)}.",
        "warning",
        ["median", "3x"],
    ),
    _t(
        "insights_unusual",
        "Unusual",
        "insights",
        "This employee has at least one flagged check in the period.",
        None,
        "warning",
    ),
    _t(
        "insights_loss_acknowledged",
        "Acknowledged",
        "insights",
        "An admin has looked at this flag. It stays in the list but never pushes again.",
        None,
        "success",
        ["ack", "acknowledge"],
    ),
    _t(
        "insights_odd_checks",
        "Odd checks",
        "insights",
        "Checks that were open a long time, changed after their day was closed, or had a large refund (admins only).",
        "Part of the same 9 AM Loss watch push.",
        None,
    ),
    _t(
        "insights_odd_open_long",
        "Open too long",
        "insights",
        f"More than {_ODD_HOURS} hours between opening the check and paying it (or still open).",
        "The hours are a setting (ODD_CHECK_OPEN_HOURS). A scheduled order isn't counted: {pos} opens it at the "
        "pickup time.",
        "warning",
    ),
    _t(
        "insights_odd_modified_after_paid",
        "Changed after payment",
        "insights",
        f"The check was changed after its business day was closed (4 AM the next morning), and more than "
        f"{_INS.MODIFIED_AFTER_PAID_MINUTES} minutes after it was paid.",
        f"Same-day changes are normal (the kitchen marking an order done updates it). Not counted: online, "
        f"delivery and kiosk pick up orders (they change when picked up, unless refunded), orders paid on an "
        f"earlier day, and {{pos}}'s own batch updates ({_INS.BATCH_MODIFY_MIN_CHECKS} or more checks changed in "
        "the same minute).",
        "warning",
    ),
    _t(
        "insights_odd_large_refund",
        "Large refund",
        "insights",
        f"A refund of ${_ODD_REF} or more on one check.",
        "The amount is a setting (ODD_CHECK_LARGE_REFUND).",
        "warning",
    ),
    _t(
        "insights_menu_top",
        "Top sellers",
        "insights",
        "Items with the most net sales in the period.",
        None,
        "success",
    ),
    _t(
        "insights_menu_bottom",
        "Bottom sellers",
        "insights",
        "Items that sold, but brought in the least in the period.",
        None,
        "neutral",
    ),
    _t(
        "insights_menu_rising",
        "Rising",
        "insights",
        "Items selling more than in the period before (same number of days).",
        f"Only items that sold at least {_n(_INS.TREND_MIN_PREV_QTY)} in the period before, so 1 to 3 isn't "
        "\"up 200%\".",
        "success",
    ),
    _t(
        "insights_menu_falling",
        "Falling",
        "insights",
        "Items selling less than in the period before (same number of days).",
        f"Only items that sold at least {_n(_INS.TREND_MIN_PREV_QTY)} in the period before.",
        "warning",
    ),
    _t(
        "insights_menu_not_sold",
        "Not sold",
        "insights",
        "Kitchen menu items that didn't sell at all in the period.",
        "Last sold shows the latest sale in the period before, if any.",
        "danger",
    ),
    _t(
        "insights_menu_kitchen",
        "Kitchen",
        "insights",
        "Items on the store's kitchen (prepared-food) menus, without the bottled and canned drinks, which are "
        "store stock. Drinks made in house count.",
        None,
        None,
        ["kitchen menu"],
    ),
    _t(
        "insights_menu_grocery",
        "Grocery",
        "insights",
        "Items on the store's other (retail) menus that aren't kitchen items.",
        None,
        None,
    ),
    _t(
        "insights_attach_rate",
        "Attach rate",
        "insights",
        "How often this add-on is chosen: add-ons sold divided by how many of the items that offer it were sold.",
        "Empty when {pos}'s menu doesn't say which items offer it.",
        None,
        ["modifier", "add-on"],
    ),
    _t(
        "insights_prep_expected",
        "Expected",
        "insights",
        f"How many to prepare: the same weekday over the last {_INS.PREP_WEEKS} weeks, leaving out the highest "
        "and lowest day, adjusted for the recent trend.",
        f"The trend compares the last {_INS.PREP_TREND_DAYS} days with the {_INS.PREP_TREND_DAYS} before, counts "
        f"half of the change, and never moves the number more than {_pct(_INS.PREP_TREND_MAX - 1)} either way. "
        "Kitchen items only.",
        None,
        ["prep list", "forecast"],
    ),
    _t(
        "insights_prep_range",
        "Range",
        "insights",
        "A normal low and high day: the middle half of those same weekdays, with the same trend adjustment.",
        None,
        None,
        ["low", "high"],
    ),
    _t(
        "insights_prep_by_hour",
        "By hour",
        "insights",
        "The expected amount spread over the day using the store's usual share of orders per hour on that weekday.",
        "{pos} doesn't report items by hour, so this is the store's pattern, not the item's own.",
        None,
    ),
    _t(
        "insights_card_customers",
        "Card customers",
        "insights",
        "Different cards that paid in the week. Cash payments aren't counted.",
        f"Cards are recognised only by a scrambled code made by RetailOps; no card number, name or other card "
        f"detail is ever saved, and the codes are deleted after {_n(_INSD.GUEST_RETENTION_DAYS)} days.",
        None,
        ["customers", "privacy"],
    ),
    _t(
        "insights_returning_customer",
        "Returning",
        "insights",
        "A card that paid here before that week (or on two different days in the period).",
        "Only as far back as the saved history, so the first weeks look lower.",
        None,
    ),
    _t(
        "insights_returning_rate",
        "Returning rate",
        "insights",
        "Returning cards divided by all card customers.",
        None,
        None,
    ),
    _t(
        "insights_avg_visits",
        "Average visits",
        "insights",
        "How many different days each card paid in the period, on average.",
        None,
        None,
    ),
    _t(
        "insights_hourly_average",
        "Average for the hour",
        "insights",
        "The average net sales (or orders) in that hour on that weekday over the chosen weeks. The tooltip also "
        "shows the best single day.",
        None,
        None,
    ),
    _t(
        "insights_loss_flag",
        "Flagged",
        "insights",
        "A check worth a second look: a full comp, a large discount, refund or void, or something unusual for "
        "that employee.",
        "Admins can acknowledge a flag once they've looked at it.",
        "warning",
    ),
    _t(
        "insights_channels",
        "Channels",
        "insights",
        "Net sales and orders per week split by how the order came in (counter, kiosk, online, delivery apps, "
        "catering).",
        None,
        None,
    ),
    _t(
        "insights_compare",
        "Daily compare",
        "insights",
        "One day's net sales, orders, average check and guests next to the same weekday last week, the 4-week "
        "average and last year.",
        "Yesterday by default. Today is live and compared up to the same hour.",
        None,
    ),
    _t(
        "insights_menu",
        "Menu performance",
        "insights",
        "Top and bottom sellers, rising and falling items and kitchen items that didn't sell, for 7 to 56 days "
        "against the same number of days before.",
        None,
        None,
    ),
    _t(
        "insights_addons",
        "Add-ons",
        "insights",
        "Modifiers (sizes, spice levels, extras) by how many were sold, what they brought in and how often they're "
        "chosen.",
        None,
        None,
    ),
    _t(
        "insights_prep",
        "Prep forecast",
        "insights",
        "How many of each kitchen item to expect on a day (tomorrow by default), with a low and high and an "
        "hourly split.",
        None,
        None,
    ),
    _t(
        "insights_returning",
        "Returning customers",
        "insights",
        "How many card customers each week had paid here before, and how often they come.",
        f"Counts only. Cards are recognised by a scrambled code made by RetailOps and deleted after "
        f"{_n(_INSD.GUEST_RETENTION_DAYS)} days; no card or name is ever saved or shown.",
        None,
    ),
]

TERMS_BY_KEY = {t["key"]: t for t in TERMS}

# Per-store words (multi-store M2, docs/M2_CONTRACT.md section 6): the text
# above says "{pos}" where it means the store's POS ("Toast", "Clover") and
# "{store_city}" for the store's time zone as people say it ("New York").
# They are filled in when the glossary is served, so one server's glossary
# reads in its own store's terms; for a Toast store in New York the text is
# exactly what it was before.
_TEXT_FIELDS = ("label", "definition", "rule", "where")


def _store_words() -> dict:
    from app.config import get_settings
    from app.help_content import pos_word

    s = get_settings()
    tz = getattr(s, "store_timezone", "") or getattr(s, "toast_timezone", "") or "America/New_York"
    city = tz.rsplit("/", 1)[-1].replace("_", " ")
    # A CSV-only store reads "your POS", never "CSV upload" (website ready A7).
    return {"{pos}": pos_word(), "{store_city}": city}


def _fill(text, words: dict):
    if not isinstance(text, str):
        return text
    for k, v in words.items():
        text = text.replace(k, v)
    return text


def localize_term(term: Optional[dict], words: Optional[dict] = None) -> Optional[dict]:
    """A copy of `term` with this store's words filled in."""
    if term is None:
        return None
    words = words or _store_words()
    out = dict(term)
    for f in _TEXT_FIELDS:
        out[f] = _fill(out.get(f), words)
    return out


def reason_term(issue_type: str, reason: str) -> Optional[dict]:
    """The glossary term for a Fix Issues close reason: the list's own
    (reason_<issue_type>_<reason>) when the meaning differs on that list,
    else the shared reason_<reason>. This store's words filled in."""
    term = TERMS_BY_KEY.get(f"reason_{issue_type}_{reason}") or TERMS_BY_KEY.get(f"reason_{reason}")
    return localize_term(term)


# ---- area and pages per category (final pass) ----
# The area a status term's category belongs to (its colour family on the
# glossary page) and the help page ids (content/help/pages.json) it shows on.
CATEGORY_AREA = {
    "reorder": "ordering",
    "forecast": "ordering",
    "review": "ordering",
    "projection": "ordering",
    "similar": "ordering",
    "placed_orders": "ordering",
    "vendor_calendar": "ordering",
    "fix_issues": "stock",
    "invoices": "stock",
    "delivery": "stock",
    "reconciliation": "stock",
    "reports": "stock",
    "open_orders": "sales",
    "sales": "sales",
    "insights": "sales",
    "marketing": "team",
    "other": None,
}
CATEGORY_PAGES = {
    "reorder": ["reorder", "home"],
    "forecast": ["new_order", "reorder", "fix_issues"],
    "review": ["new_order", "fix_issues"],
    "projection": ["new_order", "reorder", "cart"],
    "similar": ["new_order"],
    "placed_orders": ["placed_orders", "cart"],
    "vendor_calendar": ["vendor_calendar", "home"],
    "fix_issues": ["fix_issues"],
    "invoices": ["invoices"],
    "delivery": ["delivery_review"],
    "reconciliation": ["reconciliation"],
    "reports": ["inventory_reports"],
    "open_orders": ["open_checks"],
    "sales": ["items_sold", "home"],
    "insights": ["insights"],
    "marketing": ["flyers"],
    "other": [],
}
# "Other labels" are spread over the product: their area and pages by key prefix.
_OTHER_BY_PREFIX = [
    ("checklist_", "team", ["daily_checklists", "checklist_builder"]),
    ("chat_", "team", ["team_chat"]),
    ("task_", "team", ["team_tasks"]),
    ("announcement_", "team", ["team_news"]),
    ("user_", "setup", ["users"]),
    ("hide_sales", "setup", ["users"]),
    ("label_", "stock", ["print_labels", "scan"]),
    ("job_status", "home", ["home"]),
    ("on_sale", "stock", ["scan"]),
    ("clearance", "stock", ["scan"]),
    ("category_correction", "setup", ["po_sections"]),
]


def _area_pages(term: dict):
    cat = term.get("category")
    if cat == "other":
        for prefix, area, pages in _OTHER_BY_PREFIX:
            if term["key"].startswith(prefix):
                return area, list(pages)
        return None, []
    return CATEGORY_AREA.get(cat), list(CATEGORY_PAGES.get(cat, []))


def _with_place(term: dict, cat_where: dict) -> dict:
    area, pages = _area_pages(term)
    return {
        **term,
        "area": area,
        "pages": pages,
        "where": cat_where.get(term.get("category"), ""),
        "related": [],
    }


def _concepts():
    """Concept terms and their categories from content/help (empty when the
    folder isn't there, so the status glossary always works)."""
    from app import help_content

    terms = []
    pos = help_content.store_pos()
    for t in help_content.concept_terms():
        if isinstance(t.get("pos"), list) and pos is not None and pos not in t["pos"]:
            continue  # a term about another POS (website ready A8)
        terms.append(
            {
                "key": t["key"],
                "label": t["label"],
                "category": t["category"],
                "definition": t["definition"],
                "rule": None,
                "tone": None,
                "aliases": list(t.get("aliases") or []),
                "area": t.get("area"),
                "pages": list(t.get("pages") or []),
                "where": t.get("where") or "",
                "related": list(t.get("related") or []),
            }
        )
    cats = [
        {"key": c["key"], "label": c["label"], "where": c.get("where", ""), "area": c.get("area")}
        for c in help_content.concept_categories()
    ]
    return cats, terms


def get_glossary() -> dict:
    words = _store_words()
    cat_where = {c["key"]: c.get("where", "") for c in CATEGORIES}
    concept_cats, concept_terms = _concepts()
    terms = [localize_term(_with_place(t, cat_where), words) for t in TERMS]
    terms += [localize_term(t, words) for t in concept_terms if t["key"] not in TERMS_BY_KEY]
    categories = [
        {**c, "label": _fill(c.get("label"), words), "area": CATEGORY_AREA.get(c["key"])} for c in CATEGORIES
    ]
    categories += [{**c, "label": _fill(c.get("label"), words)} for c in concept_cats]
    return {"version": GLOSSARY_VERSION, "categories": categories, "terms": terms}
