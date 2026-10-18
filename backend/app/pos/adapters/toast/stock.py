"""Toast Stock API: ``/stock/v1/inventory``.

The fetches (raw JSON) moved unchanged from ``services/inventory_api.py`` in
multi-store M1 batch MT2 (``inventory_api.fetch_stock`` / ``fetch_stock_live``
still work). MT3 adds ``stock_levels`` (raw -> ``StockLevel``). Toast gives
``QUANTITY`` with a count, or ``OUT_OF_STOCK`` with none (that covers 0 AND
negative); an item not in the list isn't count-tracked.
"""

from typing import Iterable, List

from app.pos.adapters.toast import client as _client
from app.pos.models import StockLevel, StockStatus, to_decimal

TOAST_QUANTITY = "QUANTITY"
TOAST_OUT_OF_STOCK = "OUT_OF_STOCK"

STOCK_PATH = "/stock/v1/inventory"


def fetch_stock() -> list:
    data = _client.toast_get(STOCK_PATH)
    if not isinstance(data, list):
        raise _client.ToastFetchError("Unexpected /stock/v1/inventory response")
    return data


# The live overlay asks Toast while someone waits for a page: a short timeout
# and no 429 sleeps (a failure just means the snapshot counts this time).
LIVE_STOCK_TIMEOUT_SECONDS = 20.0


def fetch_stock_live() -> list:
    data = _client.toast_get(STOCK_PATH, timeout=LIVE_STOCK_TIMEOUT_SECONDS, max_tries=1)
    if not isinstance(data, list):
        raise _client.ToastFetchError("Unexpected /stock/v1/inventory response")
    return data


def stock_status(raw_status) -> StockStatus:
    """Toast's status string -> StockStatus (any other value: not tracked)."""
    if raw_status == TOAST_QUANTITY:
        return StockStatus.COUNT
    if raw_status == TOAST_OUT_OF_STOCK:
        return StockStatus.OUT
    return StockStatus.UNTRACKED


def stock_level(row: dict) -> StockLevel:
    """One /stock/v1/inventory entry. A QUANTITY entry without a number keeps
    status COUNT with quantity None (the count is not known)."""
    status = stock_status(row.get("status"))
    qty = row.get("quantity")
    return StockLevel(
        item_external_id=row.get("guid"),
        status=status,
        quantity=to_decimal(float(qty)) if status is StockStatus.COUNT and qty is not None else None,
    )


def stock_levels(rows: Iterable, lenient: bool = False) -> List[StockLevel]:
    """Every entry, in Toast's order. ``lenient`` (the live overlay) skips an
    entry that isn't an object, as that path always did; otherwise such an
    entry raises, as the nightly build always did."""
    if lenient:
        return [stock_level(r) for r in rows if isinstance(r, dict)]
    return [stock_level(r) for r in rows]
