"""Old home of the sales sync - moved, this name still works.

Multi-store M1 batch MT3 split it in two:

    the job (JOB_NAME, sync_sales_for_date, _upsert_line_items,
    _prune_missing, latest_job_run)        -> app/pos/sync/sales.py (POS-neutral,
                                              reads Order models)
    Toast's order JSON -> Order (map_order), the 429-retried fetch
    (fetch_orders_with_retry), _parse_toast_timestamp,
    fetch_orders_for_business_date          -> app/pos/adapters/toast/orders.py

Every name below forwards to its new home (``app.pos.compat``): reading
``sales_sync.X`` gives the new module's current X, and patching
``sales_sync.X`` (monkeypatch, mock.patch) patches it there - so
``monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", fake)``
still feeds ``sales_sync.sync_sales_for_date``, through the Toast connector.

``_extract_line_items`` / ``_flatten_selection`` (Toast JSON -> `orders` row
dicts) are kept here for old callers: the Toast mapping plus the neutral row
rule, the same rows as before.
"""

import datetime as dt  # noqa: F401 - the old module exposed these names
import logging  # noqa: F401
import time  # noqa: F401
from typing import Callable, Optional  # noqa: F401

import httpx  # noqa: F401
from sqlalchemy import delete, select  # noqa: F401
from sqlalchemy.orm import Session  # noqa: F401

from app.models import JobRun, Order  # noqa: F401
from app.pos.adapters.toast import orders as _toast_orders
from app.pos.compat import forwards_for, install_forwarding
from app.pos.models import Check as _Check
from app.pos.models import Order as _PosOrder
from app.pos.sync import sales as _sales

install_forwarding(
    __name__,
    {
        **forwards_for(
            _sales, ("JOB_NAME", "logger", "_upsert_line_items", "_prune_missing", "sync_sales_for_date", "latest_job_run")
        ),
        # MT4 (m1-mt4) imported these two into this module; they now live in pos/sync/sales.
        **forwards_for(_sales, ("after_sales_sync", "neutral_line_values")),
        **forwards_for(
            _toast_orders, ("fetch_orders_for_business_date", "fetch_orders_with_retry", "_parse_toast_timestamp")
        ),
    },
)


def _extract_line_items(raw_order: dict) -> list[dict]:
    """Flatten a raw Toast order JSON object into one `orders` row dict per
    selection (line item), including nested modifier selections."""
    return _sales.line_rows(_toast_orders.map_order(raw_order, None))


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
    """One Toast selection and its nested modifiers as `orders` row dicts,
    with the order/check context given (paid_at already resolved)."""
    order = _PosOrder(
        external_id=order_guid,
        business_date=None,
        opened_at=opened_at,
        dining_option_id=order_dining_option,
        voided=bool(order_voided),
        checks=(_Check(external_id=check_guid, paid_at=paid_at, voided=bool(check_voided)),),
        lines=tuple(_toast_orders.map_selection(selection, check_guid, parent_selection_guid, check_index=0)),
    )
    return _sales.line_rows(order)
