"""Toast Orders API: a business date's orders (``ordersBulk``), and the
mapping of Toast's order JSON to the POS-neutral ``Order`` model.

- ``fetch_orders_for_business_date``: raw JSON, moved unchanged from
  ``app/toast_client.py`` in M1 batch MT2 (the old name still works).
- ``fetch_orders_with_retry`` / ``_fetch_with_retry``: the same call retried on
  Toast's 429, moved unchanged in MT3 from ``services/sales_sync.py`` (nightly
  sync, Items Sold live) and ``services/open_orders.py`` (the open checks
  scan). Both return raw JSON, as before; the old names still work.
- ``map_order`` (MT3): Toast order JSON -> ``Order``. This is today's
  ``sales_sync._extract_line_items`` / ``_flatten_selection`` and the
  ``open_orders`` field reads, split so the sync jobs never see Toast JSON.
  Toast rules kept here:
  - timestamps like "2026-08-23T18:28:45.000+0000" (no colon in the offset,
    gotcha #4) via ``_parse_toast_timestamp``;
  - a selection's nested ``modifiers`` become their own lines (any depth),
    each pointing at the selection it modifies (gotcha #11);
  - a deleted order / check counts as voided;
  - a check without its own ``paidDate`` falls back to the order's (the
    sync applies that rule, see ``pos.sync.sales.line_paid_at``).
"""

import datetime as dt
import logging
import time
from typing import Callable, List, Optional

import httpx

from app.config import get_settings
from app.pos.adapters.toast import client as _client
from app.pos.models import Check, Order, OrderLine, to_decimal

# The parse warning and the 429 lines keep the logger names they had before
# the move (the open checks scan's own 429 line came from open_orders).
logger = logging.getLogger("app.services.sales_sync")
_open_orders_logger = logging.getLogger("app.services.open_orders")


def fetch_orders_for_business_date(business_date: dt.date) -> list[dict]:
    """Return raw order JSON objects for a Toast business date, fully paginated."""
    settings = get_settings()
    date_str = business_date.strftime("%Y%m%d")

    orders: list[dict] = []
    with httpx.Client() as client:
        token = _client.get_access_token(client)
        headers = {
            "Authorization": f"Bearer {token}",
            "Toast-Restaurant-External-ID": settings.toast_restaurant_guid,
        }

        page = 1
        page_size = 100
        relogged = False
        while True:
            resp = client.get(
                f"{settings.toast_api_base}/orders/v2/ordersBulk",
                headers=headers,
                params={"businessDate": date_str, "page": page, "pageSize": page_size},
                timeout=60,
            )
            if resp.status_code == 401 and not relogged:
                # Token revoked / expired early: log in again once and repeat
                # this page (otherwise every sync fails until the cached
                # token's own expiry).
                relogged = True
                _client._drop_token()
                headers["Authorization"] = f"Bearer {_client.get_access_token(client)}"
                continue
            if resp.status_code == 404:
                # Toast returns 404 for a business date with no orders yet.
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            orders.extend(batch)
            if len(batch) < page_size:
                break
            page += 1

    return orders


def fetch_orders_with_retry(
    business_date: dt.date,
    max_retries: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """fetch_orders_for_business_date, retrying Toast's 429 (gotcha #9) with
    backoff and Retry-After. Any other error is raised as is."""
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            return fetch_orders_for_business_date(business_date)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < max_retries:
                retry_after = e.response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else delay
                except ValueError:
                    wait = delay
                logger.info("Toast 429 for %s, retrying in %.1fs", business_date, wait)
                sleep(wait)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Failed to fetch orders for {business_date} after {max_retries} retries")


def _fetch_with_retry(business_date: dt.date, max_retries: int = 4) -> list[dict]:
    """fetch_orders_for_business_date, retrying on Toast's 429 (real rate
    limit hit roughly once per 25-30 sequential day-requests during the
    original historical backfill - see the project skill) with exponential
    backoff, honoring Retry-After when Toast sends one. (The open checks
    scan's variant: a Retry-After that isn't a number raises, as before.)
    """
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            return fetch_orders_for_business_date(business_date)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < max_retries:
                retry_after = e.response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                _open_orders_logger.info("Toast 429 for %s, retrying in %.1fs", business_date, wait)
                time.sleep(wait)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Failed to fetch orders for {business_date} after {max_retries} retries")


# ---------------------------------------------------------------------------
# Toast order JSON -> Order
# ---------------------------------------------------------------------------


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


def _parse_quietly(raw) -> Optional[dt.datetime]:
    """Like _parse_toast_timestamp without the warning: for fields nothing
    read before M1 (closedDate), so a bad value adds no new log line."""
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%f%z")
    except (TypeError, ValueError):
        return None


def map_check(check: dict) -> Check:
    """One Toast Check object. ``total`` only when Toast sent a number."""
    amount = check.get("totalAmount")
    raw_tab = check.get("tabName")
    return Check(
        external_id=check.get("guid", ""),
        paid_at=_parse_toast_timestamp(check.get("paidDate"), "check paidDate"),
        total=to_decimal(amount) if isinstance(amount, (int, float)) else None,
        tab_name=raw_tab if isinstance(raw_tab, str) else None,
        voided=bool(check.get("voided", False)),
        deleted=bool(check.get("deleted", False)),
    )


def map_selection(
    selection: dict, check_guid: str, parent_selection_guid: Optional[str], check_index: Optional[int] = None
) -> List[OrderLine]:
    """One selection and, after it, its nested ``modifiers`` (each a full
    selection with its own guid / price / tax, which can carry modifiers
    again) - depth first, the order the rows were always written in."""
    selection_guid = selection.get("guid", "")
    item_obj = selection.get("item") or {}
    lines = [
        OrderLine(
            external_id=selection_guid,
            check_external_id=check_guid,
            parent_external_id=parent_selection_guid,
            is_modifier=parent_selection_guid is not None,
            item_external_id=item_obj.get("guid"),
            name=selection.get("displayName") or selection.get("name"),
            quantity=to_decimal(float(selection.get("quantity") or 0)),
            # Toast's selection `price` is the net (after discounts, before
            # tax) and already includes its modifiers' prices.
            net_amount=to_decimal(float(selection.get("price") or 0)),
            tax_amount=to_decimal(float(selection.get("tax") or 0)),
            voided=bool(selection.get("voided", False)),
            check_index=check_index,
        )
    ]
    for modifier in selection.get("modifiers", []) or []:
        lines.extend(map_selection(modifier, check_guid, parent_selection_guid=selection_guid, check_index=check_index))
    return lines


def map_order(raw_order: dict, business_date: dt.date) -> Order:
    """A Toast order JSON object -> ``Order``. ``business_date`` is the date
    the order was asked for (ordersBulk is per business date)."""
    opened_at = _parse_toast_timestamp(raw_order.get("openedDate"), "openedDate")
    order_paid_at = _parse_toast_timestamp(raw_order.get("paidDate"), "paidDate")
    created_at = _parse_toast_timestamp(raw_order.get("createdDate"), "createdDate")
    checks = []
    lines: List[OrderLine] = []
    for index, raw_check in enumerate(raw_order.get("checks", []) or []):
        check = map_check(raw_check)
        checks.append(check)
        for selection in raw_check.get("selections", []) or []:
            lines.extend(map_selection(selection, check.external_id, parent_selection_guid=None, check_index=index))
    promised_raw = raw_order.get("promisedDate") or raw_order.get("estimatedFulfillmentDate")
    closed_raw = raw_order.get("closedDate")
    # The sales sync and Items Sold never read promisedDate / server before
    # M1, so an odd value there (not a string / not an object) must not fail
    # their whole date: it maps to None (a non-string promise is logged).
    if promised_raw and not isinstance(promised_raw, str):
        logger.warning("Could not parse order %s %r", "promisedDate", promised_raw)
        promised_raw = None
    server = raw_order.get("server")
    return Order(
        external_id=raw_order.get("guid", ""),
        business_date=business_date,
        opened_at=opened_at,
        created_at=created_at,
        paid_at=order_paid_at,
        closed=bool(closed_raw),
        closed_at=_parse_quietly(closed_raw),
        promised_at=_parse_toast_timestamp(promised_raw, "promisedDate") if promised_raw else None,
        # Toast's order payload only returns a diningOption reference (guid),
        # not a name - resolving it needs the Restaurant Config API (not
        # done). The guid is kept as is (gotcha #5).
        dining_option_id=(raw_order.get("diningOption") or {}).get("guid"),
        display_number=raw_order.get("displayNumber"),
        guests=raw_order.get("numberOfGuests"),
        server_external_id=server.get("guid") if isinstance(server, dict) else None,
        approval_status=raw_order.get("approvalStatus"),
        checks=tuple(checks),
        lines=tuple(lines),
        voided=bool(raw_order.get("voided", False)),
        deleted=bool(raw_order.get("deleted", False)),
    )


def is_open(raw_order: dict) -> bool:
    """Toast's "open": never closed, not voided, not deleted."""
    return not (raw_order.get("closedDate") or raw_order.get("voided") or raw_order.get("deleted"))
