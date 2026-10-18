"""Weekly Digest (Phase 6): a narrative summary over the trailing 7 days,
pulling from every report already built - reorder candidates, dead stock,
vendor price comparison, reconciliation, barcode data quality, and a
sales total vs. the prior week. Deliberately cheap to build: every underlying
number is computed the normal way by the existing service functions (nothing
new is invented here), then handed to Claude as real, already-correct data
for one single API call to turn into readable prose - never asked to compute
or estimate a number itself.

Generated on demand (a button click), not on a schedule - the user chose
in-app only, no email/notification delivery, so there's no reason to spend an
API call automatically on a week nobody's going to look at.
"""

import datetime as dt
import json
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order
from app.sales_access import TOAST_SALES_DOLLAR_FIELDS
from app.services.anthropic_client import get_anthropic_client
from app.services.barcode_report import get_invalid_barcodes, get_missing_barcodes
from app.services.inventory_intelligence import get_dead_stock
from app.services.reconciliation import get_reconciliation
from app.services.reorder import compute_reorder_candidates
from app.services.vendor_cost import get_vendor_price_comparison

MODEL = "claude-opus-5"


class WeeklyDigestNarrative(BaseModel):
    headline: str
    sales: str
    reorder: str
    money_at_risk: str
    vendor_savings: str
    reconciliation: str
    housekeeping: str


def _sales_totals(db: Session, start: dt.date, end: dt.date) -> dict:
    stmt = select(
        func.coalesce(func.sum(Order.net_price), 0.0),
        func.coalesce(func.sum(Order.quantity), 0.0),
        func.count(func.distinct(Order.toast_check_guid)),
    ).where(
        Order.business_date >= start,
        Order.business_date <= end,
        Order.voided.is_(False),
        Order.top_level(),
    )
    revenue, quantity, check_count = db.execute(stmt).one()
    return {"revenue": round(float(revenue), 2), "quantity": round(float(quantity), 2), "checks": check_count}


def get_weekly_digest_data(db: Session, end_date: dt.date = None) -> dict:
    end_date = end_date or dt.date.today()
    start_date = end_date - dt.timedelta(days=6)
    prev_start = start_date - dt.timedelta(days=7)
    prev_end = start_date - dt.timedelta(days=1)

    this_week = _sales_totals(db, start_date, end_date)
    prior_week = _sales_totals(db, prev_start, prev_end)
    # Insights (docs/INSIGHTS_CONTRACT.md, additive): Toast Analytics' own net
    # sales for the week vs the week before and the 4-week average, from the
    # cache only (None until those days are cached).
    try:
        from app.services.insights import week_compare

        toast_compare = week_compare(db, end_date)
    except Exception:  # noqa: BLE001 - the digest never fails over this
        toast_compare = None

    reconciliation_flags = sorted(
        get_reconciliation(db, start_date, end_date),
        key=lambda r: abs(r["variance_value"] or 0),
        reverse=True,
    )[:5]

    return {
        "week_start": start_date.isoformat(),
        "week_end": end_date.isoformat(),
        "sales": {"this_week": this_week, "prior_week": prior_week, "toast_compare": toast_compare},
        "reorder_candidates": compute_reorder_candidates(db, lookback_days=14, lead_time_days=3)[:5],
        "dead_stock": get_dead_stock(db)[:5],
        "vendor_savings": get_vendor_price_comparison(db)[:5],
        "reconciliation_flags": reconciliation_flags,
        "missing_barcode_count": len(get_missing_barcodes(db)),
        "invalid_barcode_count": len(get_invalid_barcodes(db)),
    }


SYSTEM_PROMPT = """You write a short, plain-language weekly digest for the person running Spice
Town, a real South Asian grocery + halal meat + prepared-food store. You'll be given real
structured data for the trailing 7 days (and the prior 7 days for sales comparison) - every number
in it is already correct, computed the normal way; never invent, round loosely, or estimate a
number that isn't in the data. If a section's data is empty, say so plainly (e.g. "no vendor
savings flagged this week") rather than skipping it or padding it out. Be concise - 1 to 3 short
sentences per section, direct and specific (name real items/vendors from the data), no filler."""

# Added for a reader whose admin ticked "Hide sales numbers" (app/sales_access.py):
# the data has no sales section, and nothing else may hint at what the store sold.
NO_SALES_PROMPT = """

This reader is not shown sales figures. The data has no sales section - do not mention sales,
revenue, takings, checks or how the week sold anywhere (including the headline). Set the "sales"
field to an empty string."""


def _append_compare_line(narrative: dict, data: dict) -> None:
    """Adds the Toast Analytics week compare as a fixed sentence (so the
    number is exactly the one Insights shows, not Claude's retelling)."""
    from app.services.insights import week_compare_line

    line = week_compare_line((data.get("sales") or {}).get("toast_compare"))
    if line:
        narrative["sales"] = ((narrative.get("sales") or "").rstrip() + " " + line).strip()


DEMO_DIGEST_HEADLINE = "The written weekly summary is turned off for the demo account."


def generate_weekly_digest(
    db: Session, end_date: Optional[dt.date] = None, include_sales: bool = True, demo: bool = False
) -> dict:
    """include_sales=False (a "Hide sales numbers" user): the sales totals are
    never sent to Claude or returned - `data` has no "sales" key and
    `narrative.sales` is null. demo=True (an App Review demo login,
    app/demo.py): the same data with a fixed narrative - no Anthropic call."""
    client = None if demo else get_anthropic_client()
    data = get_weekly_digest_data(db, end_date)
    if not include_sales:
        data.pop("sales", None)
        # Toast's per-item 90-day sales figure (dollars) rides on dead stock.
        data["dead_stock"] = [
            {k: v for k, v in item.items() if k not in TOAST_SALES_DOLLAR_FIELDS} for item in data["dead_stock"]
        ]

    if demo:
        narrative = {k: "" for k in WeeklyDigestNarrative.model_fields}
        narrative["headline"] = DEMO_DIGEST_HEADLINE
        if not include_sales:
            narrative["sales"] = None
        return {
            "week_start": data["week_start"],
            "week_end": data["week_end"],
            "narrative": narrative,
            "data": data,
            "demo": True,
        }

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT if include_sales else SYSTEM_PROMPT + NO_SALES_PROMPT,
        messages=[{"role": "user", "content": json.dumps(data, default=str)}],
        output_format=WeeklyDigestNarrative,
    )

    narrative = response.parsed_output.model_dump()
    if not include_sales:
        narrative["sales"] = None
    else:
        _append_compare_line(narrative, data)

    return {
        "week_start": data["week_start"],
        "week_end": data["week_end"],
        "narrative": narrative,
        "data": data,
    }
