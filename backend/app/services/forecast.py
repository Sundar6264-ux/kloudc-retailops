"""Per-item demand forecast from `orders` sales history over a selected lookback window.

Method: simple average daily quantity sold, over the lookback window ending at
`as_of_date` (exclusive). No seasonality/trend modeling - that's a reasonable
next step once there's enough history to validate against, but out of scope
for the first cut.
"""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order


def forecast_daily_demand(
    db: Session, lookback_days: int, as_of_date: dt.date
) -> dict[str, dict]:
    """Returns {item_guid: {"name": str, "avg_daily_demand": float, "total_quantity": float}}
    for every top-level item with at least one non-voided sale in
    [as_of_date - lookback_days, as_of_date). Modifier lines (parent_selection_guid
    set - e.g. a spice-level choice or sauce) are excluded: they're not
    independently reorderable menu items, and mixing them in double-counts
    demand under whatever their own item_guid happens to be.
    """
    start_date = as_of_date - dt.timedelta(days=lookback_days)

    stmt = (
        select(
            Order.item_guid,
            func.max(Order.item_name).label("item_name"),
            func.sum(Order.quantity).label("total_quantity"),
        )
        .where(
            Order.item_guid.isnot(None),
            Order.voided.is_(False),
            Order.top_level(),
            Order.business_date >= start_date,
            Order.business_date < as_of_date,
        )
        .group_by(Order.item_guid)
    )

    results: dict[str, dict] = {}
    for item_guid, item_name, total_quantity in db.execute(stmt):
        results[item_guid] = {
            "name": item_name,
            "total_quantity": float(total_quantity),
            "avg_daily_demand": float(total_quantity) / lookback_days,
        }
    return results
