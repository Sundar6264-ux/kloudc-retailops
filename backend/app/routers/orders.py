import datetime as dt
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth import get_current_user
from app.features import require_feature
from app.models import User
from app.pos.capabilities import Capability
from app.pos.gating import require_capability
from app.sales_access import OPEN_ORDER_MONEY_FIELDS, strip_sales
from app.services.csv_util import rows_to_csv
from app.services.open_orders import (
    ensure_today_fresh,
    get_cached_open_orders,
    get_employees,
    get_open_orders,
    legacy_display_number,
    refresh_open_orders_cache,
    refresh_today_in_background,
)
from app.timeutil import restaurant_timezone, restaurant_today

router = APIRouter(
    prefix="/api/orders",
    tags=["orders"],
    # Multi-store MT6: Open Checks needs a POS with open orders (409 otherwise).
    dependencies=[Depends(require_feature("open_orders")), Depends(require_capability(Capability.OPEN_ORDERS))],
)

# Shown to staff as "Open Checks" (owner, Oct 4); the routes, the feature key
# `open_orders` and the JSON shape keep their old names for app builds 5-7.

# App build 8+ sends this header: it shows `tab_names` itself and wants the
# raw `display_number`.
TAB_NAMES_CLIENT_HEADER = "X-Client-Tab-Names"


def _is_legacy_app(request: Request) -> bool:
    """Builds 5-7 of the iOS app: Dio on dart:io sends a "Dart/x.y (dart:io)"
    User-Agent and no tab-names header. The website (a browser) and build 8+
    never match, so their `display_number` stays raw."""
    if request.headers.get(TAB_NAMES_CLIENT_HEADER):
        return False
    return request.headers.get("user-agent", "").startswith("Dart/")


def _for_client(orders: list, request: Request) -> list:
    """Old app builds only show `display_number` (never parse it), so the tab
    name rides along there: "Order #12 · Tab: Ravi". Everyone else gets the
    additive `tab_names` / `tab_name` fields and an untouched display_number."""
    if not _is_legacy_app(request):
        return orders
    return [{**o, "display_number": legacy_display_number(o)} for o in orders]


@router.get("/open")
def open_orders(
    request: Request,
    date: Optional[dt.date] = Query(default=None),
    employee_guid: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
):
    """Live, single-date view (one Toast API call) - fast, normally used for
    "today". For every open order regardless of date, see /open/all-time.
    """
    target_date = date or restaurant_today()
    orders = get_open_orders(target_date, employee_guid=employee_guid or None)
    # "Hide sales numbers" users get every order without its total (null).
    orders = strip_sales(orders, OPEN_ORDER_MONEY_FIELDS, user)
    orders = _for_client(orders, request)
    return {"mode": "day", "date": target_date.isoformat(), "count": len(orders), "orders": orders}


@router.get("/open/all-time")
def open_orders_all_time(
    request: Request,
    employee_guid: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read of the background-refreshed cache (see scheduler.py - today-only
    refresh every few minutes, full history once daily). When today's part
    is more than 2 minutes old it's refreshed in the background (one Toast
    call, open_orders.refresh_today_in_background) and this answer is the
    cache as it is - the next read is fresh (speed #8: no ~0.9 s wait for
    Toast on Home). Older dates come from the daily full scan - POST
    /open/all-time/rescan forces that on demand.
    """
    refresh_today_in_background(db)
    result = get_cached_open_orders(db, employee_guid=employee_guid or None)
    orders = strip_sales(result["orders"], OPEN_ORDER_MONEY_FIELDS, user)
    orders = _for_client(orders, request)
    return {
        "mode": "all_time",
        "scanned_from": result["scanned_from"],
        "scanned_to": result["scanned_to"],
        "last_refreshed_at": result["last_refreshed_at"],
        "last_job_status": result["last_job_status"],
        "count": len(orders),
        "orders": orders,
    }


_OPEN_ORDER_CSV_HEADERS = (
    "Business Date,Check #,Tab,Opened At,Server,Guests,Status,Total,Checks,Items,Paid At,Promised At,Scheduled\n"
)


def _qty(value) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value or 0)


def _local_time(value) -> Optional[str]:
    """Toast times come back as UTC ISO strings; the spreadsheet shows them
    in store-local time (America/New_York), like the app and web do."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(restaurant_timezone()).strftime("%Y-%m-%d %H:%M")


def _open_order_rows(orders: list) -> list:
    rows = []
    for o in orders:
        items = "; ".join(
            f"{_qty(li.get('quantity'))}x {li.get('name') or 'Item'}" for li in (o.get("line_items") or [])
        )
        rows.append(
            {
                "Business Date": o.get("business_date"),
                "Check #": o.get("display_number"),
                "Tab": o.get("tab_name") or "",
                "Opened At": _local_time(o.get("opened_at")),
                "Server": o.get("server_name"),
                "Guests": o.get("num_guests"),
                "Status": o.get("approval_status"),
                "Total": o.get("total_amount"),
                "Checks": o.get("num_checks"),
                "Items": items,
                "Paid At": _local_time(o.get("paid_at")),
                "Promised At": _local_time(o.get("promised_at")),
                "Scheduled": "Yes" if o.get("scheduled") else "No",
            }
        )
    return rows


@router.get("/open/export")
def open_orders_export(
    mode: Literal["day", "all_time"] = Query("day"),
    date: Optional[dt.date] = Query(default=None),
    employee_guid: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The Open Checks list as a CSV. `mode=day` is the same live single-date
    read as /open (today by default); `mode=all_time` is the cached all-time
    list from /open/all-time, with today's part refreshed first the same way
    (at most one Toast call when the last scan is over 2 minutes old)."""
    if mode == "all_time":
        ensure_today_fresh(db)
        orders = get_cached_open_orders(db, employee_guid=employee_guid or None)["orders"]
        filename = "open-checks-all-time.csv"
    else:
        target_date = date or restaurant_today()
        try:
            orders = get_open_orders(target_date, employee_guid=employee_guid or None)
        except Exception as exc:  # Toast down / auth / rate limit: a clear message, not a bare 500
            raise HTTPException(
                status_code=502, detail="Could not reach Toast for open checks right now - try again in a minute."
            ) from exc
        filename = f"open-checks-{target_date.isoformat()}.csv"
    orders = strip_sales(orders, OPEN_ORDER_MONEY_FIELDS, user)  # blank Total column
    return Response(
        content=rows_to_csv(_open_order_rows(orders)) or _OPEN_ORDER_CSV_HEADERS,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_rescan_lock = threading.Lock()


@router.post("/open/all-time/rescan")
def open_orders_rescan(db: Session = Depends(get_db)):
    """Forces a full re-scan right now (slow - minutes, one Toast call per
    historical business date) instead of waiting for the next scheduled
    background refresh. Manual escape hatch, not the normal path.
    """
    # One manual full scan at a time: each is ~100+ Toast calls over minutes,
    # and the rate limit is shared with the nightly sync - a second tap (or a
    # second phone) while one runs gets a clear answer instead of a 2nd scan.
    if not _rescan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A full re-scan is already running - try again in a few minutes.")
    try:
        job = refresh_open_orders_cache(db, full=True)
    finally:
        _rescan_lock.release()
    return {"status": job.status, "detail": job.detail}


@router.get("/employees", dependencies=[Depends(require_capability(Capability.EMPLOYEES))])
def employees():
    return {"employees": get_employees()}
