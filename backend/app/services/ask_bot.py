"""Ask Inventory Bot (Phase 5): a natural-language query layer over the
read-only reports already built elsewhere in this app - reorder candidates,
supplier projection, reconciliation, vendor price comparison, items sold,
price history, dead stock, and barcode reports.

Deliberately READ-ONLY, by explicit choice: every tool below wraps an
existing report function, none of them write to the database (no logging
purchases, no confirming deliveries, no editing prices). A misunderstood or
ambiguous question can at worst return a wrong or incomplete answer - never
corrupt real inventory/purchase data. If the bot should be allowed to take
actions later, that's a deliberate, separate decision - see the skill file.

Model routing is Haiku-default/Sonnet-escalation, as scoped: a cheap Haiku
classifier call decides up front whether the question is a simple single-
lookup (answered with `claude-haiku-4-5`) or something that needs combining
multiple reports / open-ended reasoning (escalated to `claude-sonnet-5`)
before the real tool-use loop runs. Classify-then-run (rather than trying
Haiku and re-running the whole loop on Sonnet if it struggles) means the
actual tool-use loop only ever runs once per question, so most questions stay
cheap and only the harder ones pay for the stronger model. The classifier
defaults to COMPLEX (Sonnet) on any ambiguity or failure - costs more but
never under-serves a hard question.
"""

import datetime as dt
import json
from typing import Optional

import anthropic
from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.sales_access import TOAST_SALES_DOLLAR_FIELDS
from app.services.anthropic_client import get_anthropic_client
from app.services.barcode_report import get_invalid_barcodes, get_missing_barcodes
from app.services.delivery_review import get_delivery_candidates
from app.services.inventory_intelligence import get_dead_stock
from app.services.items_sold import get_items_sold
from app.services.price_history import get_price_changed_items, search_price_history
from app.services.product_match import match_code
from app.services.reconciliation import get_reconciliation, get_reconciliation_demo
from app.services.reorder import compute_reorder_candidates
from app.services.supplier_projection import compute_supplier_projection, list_suppliers
from app.services.vendor_cost import get_vendor_price_comparison
from app.timeutil import restaurant_today

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-5"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

SYSTEM_PROMPT_TEMPLATE = """You are the Ask Inventory Bot for {store_name}, {store_description}. You answer
questions about the store's real inventory, sales, ordering, and reconciliation data using the tools provided -
never guess or estimate a number yourself when a tool can give you the real one.

You are strictly read-only: you cannot log purchases, confirm deliveries, or change any data.
If asked to do something that would require writing data, say so and explain the read-only
scope rather than attempting it.

Every report tool that can return many rows accepts an optional `search` (case-insensitive
substring match on item name) and `limit` (default 20, max 100) - use `search` to narrow down
instead of requesting a huge unfiltered list. If a tool result's `total_matches` is bigger than
`showing`, mention that more rows matched than were shown and offer to narrow the search.

Dates are ISO format (YYYY-MM-DD). Today's date is {today}. Be concise and direct - this is
answering a real question for someone running the store day-to-day, not writing a report."""

def system_prompt() -> str:
    """The prompt for this store (STORE_NAME, STORE_DESCRIPTION) and today."""
    from app.store import settings_view as sv

    return SYSTEM_PROMPT_TEMPLATE.format(
        store_name=sv.store_display_name(),
        store_description=sv.store_description(),
        today=restaurant_today().isoformat(),
    )


# Added for a user whose admin ticked "Hide sales numbers" (app/sales_access.py).
NO_SALES_PROMPT = """

Sales numbers are hidden for this user's account. You have no sales tool, and you must not give
sales revenue, dollar takings, order totals, or how many units of an item sold in a period - not
even worked out from other tools' sold/demand columns. If asked, say politely that sales numbers
are hidden for their account and an admin can change that. Stock, ordering, demand forecasts for
ordering, prices, costs and barcode questions are fine to answer."""


def _limit(items: list[dict], search: str = "", limit: int = DEFAULT_LIMIT) -> dict:
    limit = max(1, min(limit, MAX_LIMIT))
    if search:
        needle = search.strip().lower()
        # Name as before, or (Oct 14, W2) a code on the row: the Toast item
        # id, a vendor SKU or a barcode (services/product_match.py).
        items = [
            i
            for i in items
            if needle in (i.get("name") or "").lower()
            or match_code(
                search,
                i.get("item_id"),
                i.get("all_supplier_item_ids") or i.get("supplier_item_id"),
                i.get("barcode"),
            )
        ]
    total = len(items)
    return {"total_matches": total, "showing": min(total, limit), "items": items[:limit]}


def _json(obj) -> str:
    return json.dumps(obj, default=str)


def _build_tools(db: Session, include_sales: bool = True) -> list:
    @beta_tool
    def list_vendors() -> str:
        """List every vendor/supplier name currently on file, from the latest inventory upload."""
        return _json(list_suppliers(db))

    @beta_tool
    def reorder_candidates(
        lookback_days: int = 14, lead_time_days: int = 3, search: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Items that need reordering soon: forecasted demand over a vendor lead time vs. latest on-hand count, across ALL suppliers.

        Args:
            lookback_days: How many trailing days of sales to average demand from.
            lead_time_days: Vendor lead time in days - how far out to forecast demand.
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        return _json(_limit(compute_reorder_candidates(db, lookback_days, lead_time_days), search, limit))

    @beta_tool
    def supplier_projection(
        supplier: str, lookback_days: int = 14, horizon_days: int = 14, search: str = "", limit: int = DEFAULT_LIMIT
    ) -> str:
        """Demand projection and "need to order" for every item listed against ONE specific supplier, over a projection horizon. Use list_vendors first if you don't know the exact supplier name.

        Args:
            supplier: Exact vendor/supplier name (see list_vendors).
            lookback_days: How many trailing days of sales to average demand from.
            horizon_days: How many days ahead to project demand for.
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        results = compute_supplier_projection(db, supplier, lookback_days, [horizon_days])
        key = str(horizon_days)
        flat = []
        for r in results:
            proj = r["projections"].get(key, {})
            flat.append(
                {
                    "item_id": r["item_id"],
                    "name": r["name"],
                    "category": r["category"],
                    "avg_daily_demand": r["avg_daily_demand"],
                    "avg_weekly_demand": r["avg_weekly_demand"],
                    "on_hand_qty": r["on_hand_qty"],
                    "container_qty": r.get("container_qty"),
                    "projected_demand": proj.get("projected_demand"),
                    "need_to_order": proj.get("need_to_order"),
                }
            )
        return _json(_limit(flat, search, limit))

    @beta_tool
    def items_sold(start_date: str, end_date: str = "", search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Quantity and revenue sold per item over a date range (a single day is just start_date == end_date).

        Args:
            start_date: ISO date (YYYY-MM-DD), start of the range.
            end_date: ISO date (YYYY-MM-DD), end of the range (inclusive). Defaults to start_date.
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date) if end_date else start
        result = get_items_sold(db, start, end)
        limited = _limit(result["items"], search, limit)
        limited.update(
            start_date=result["start_date"],
            end_date=result["end_date"],
            source=result["source"],
            total_quantity=result["total_quantity"],
            total_revenue=result["total_revenue"],
        )
        return _json(limited)

    @beta_tool
    def reconciliation(start_date: str, end_date: str, search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Purchased vs. sold vs. counted for every item over a date range - the shrinkage/spoilage signal (expected_closing = opening_count + purchased - sold, compared to the actual counted closing stock). A large variance means real stock doesn't match what the math expects.

        Args:
            start_date: ISO date (YYYY-MM-DD), start of the window.
            end_date: ISO date (YYYY-MM-DD), end of the window.
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        return _json(_limit(get_reconciliation(db, start, end), search, limit))

    @beta_tool
    def reconciliation_demo() -> str:
        """A worked example of the reconciliation formula using one real item and the most recent window the upload history supports - useful if asked to explain how reconciliation/variance works."""
        return _json(get_reconciliation_demo(db))

    @beta_tool
    def dead_stock(search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Items with real on-hand inventory that either haven't sold at all in 90 days or would take an excessively long time to sell through - sorted by dollars tied up.

        Args:
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        items = get_dead_stock(db)
        if not include_sales:  # Toast's 90-day sales figure is dollars
            items = [{k: v for k, v in i.items() if k not in TOAST_SALES_DOLLAR_FIELDS} for i in items]
        return _json(_limit(items, search, limit))

    @beta_tool
    def missing_barcodes(search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Items with no barcode on file at all.

        Args:
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        return _json(_limit(get_missing_barcodes(db), search, limit))

    @beta_tool
    def invalid_barcodes(search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Items with a barcode on file that fails GS1 checksum validation.

        Args:
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        return _json(_limit(get_invalid_barcodes(db), search, limit))

    @beta_tool
    def price_changed_items(search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Items whose price changed between two consecutive inventory uploads, most recent change first.

        Args:
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        return _json(_limit(get_price_changed_items(db), search, limit))

    @beta_tool
    def price_history(query: str) -> str:
        """One item's full price/cost timeline across every inventory upload on file, by name or barcode search. Returns up to 5 matches.

        Args:
            query: Case-insensitive substring to match against item name or barcode.
        """
        return _json(search_price_history(db, query)[:5])

    @beta_tool
    def vendor_price_comparison(search: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Items where the vendor most recently bought from costs more than a cheaper vendor already on file for the same item. Only covers items with a real vendor-tagged cost captured via Delivery Review or a manually logged purchase, so this can legitimately be empty.

        Args:
            search: Optional case-insensitive substring to filter item names by.
            limit: Max rows to return (default 20, max 100).
        """
        return _json(_limit(get_vendor_price_comparison(db), search, limit))

    @beta_tool
    def delivery_candidates(vendor: str) -> str:
        """Preview only, does not log anything: items that likely arrived from one vendor's delivery, comparing the latest inventory upload to the previous one, net of that day's sales. Use list_vendors first if unsure of the exact name.

        Args:
            vendor: Exact vendor/supplier name (see list_vendors).
        """
        result = get_delivery_candidates(db, vendor)
        if result is None:
            return _json({"error": "Not enough inventory upload history yet (need at least two snapshot dates)."})
        return _json(result)

    return [
        list_vendors,
        reorder_candidates,
        supplier_projection,
        *([items_sold] if include_sales else []),
        reconciliation,
        reconciliation_demo,
        dead_stock,
        missing_barcodes,
        invalid_barcodes,
        price_changed_items,
        price_history,
        vendor_price_comparison,
        delivery_candidates,
    ]


def _classify_complexity(client: anthropic.Anthropic, question: str) -> str:
    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=10,
            system=(
                "Classify the following inventory-bot question as exactly one word: SIMPLE (a "
                "direct, single-lookup factual question answerable with one tool call - e.g. "
                "'how much cilantro do we have', 'price of X', 'any missing barcodes') or COMPLEX "
                "(needs combining multiple reports, comparison across vendors/dates, or open-ended "
                "reasoning/explanation - e.g. 'why did sales drop', 'what needs attention today', "
                "'should I switch vendors for X'). Respond with only SIMPLE or COMPLEX, nothing else."
            ),
            messages=[{"role": "user", "content": question}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "").strip().upper()
        return "SIMPLE" if text.startswith("SIMPLE") else "COMPLEX"
    except Exception:
        return "COMPLEX"


def ask(db: Session, question: str, history: Optional[list[dict]] = None, include_sales: bool = True) -> dict:
    client = get_anthropic_client()
    history = history or []

    complexity = _classify_complexity(client, question)
    model = HAIKU_MODEL if complexity == "SIMPLE" else SONNET_MODEL

    tools = _build_tools(db, include_sales=include_sales)
    messages = [*history, {"role": "user", "content": question}]

    runner = client.beta.messages.tool_runner(
        model=model,
        max_tokens=2048,
        system=system_prompt()
        + ("" if include_sales else NO_SALES_PROMPT),
        tools=tools,
        messages=messages,
    )

    final = None
    tool_calls_made = []
    for message in runner:
        final = message
        for block in message.content:
            if block.type == "tool_use":
                tool_calls_made.append({"tool": block.name, "input": block.input})

    answer = next((b.text for b in final.content if b.type == "text"), "") if final else ""

    return {
        "answer": answer or "(no answer generated - the model returned no text response)",
        "model_used": model,
        "escalated": model == SONNET_MODEL,
        "tool_calls": tool_calls_made,
    }
