import datetime as dt
import logging
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
import httpx
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.features import require_feature
from app.pos.capabilities import Capability
from app.pos.gating import require_capability
from app.sales_access import require_sales_access
from app.services.csv_util import rows_to_csv
from app.services.export import export_csv, export_pdf
from app.services.items_sold import LiveSalesUnavailable, get_items_sold
from app.services import sales_accuracy, toast_analytics
from app.store.settings_view import store_slug
from app.timeutil import restaurant_today
from app.toast_client import ToastAPIError

router = APIRouter(prefix="/api/sales", tags=["sales"], dependencies=[Depends(get_current_user)])

logger = logging.getLogger(__name__)


def _items_sold_or_503(db: Session, start_date: dt.date, end_date: dt.date, fresh: bool = False) -> dict:
    """get_items_sold, with Toast being down or slow for today's live part
    answered as a plain 503 (instead of a 500 with a stack trace)."""
    try:
        return get_items_sold(db, start_date, end_date, fresh=fresh)
    except LiveSalesUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (httpx.HTTPError, ToastAPIError) as exc:
        logger.warning("Items Sold: Toast live pull failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Toast didn't answer for today's sales just now - try again in a moment."
        ) from exc


# Sales numbers: 403 for a user whose admin ticked "Hide sales numbers"
# (app/sales_access.py), even with the items_sold grant.
@router.get("/items-sold", dependencies=[Depends(require_feature("items_sold")), Depends(require_sales_access)])
def items_sold(
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    date: Optional[dt.date] = None,
    fresh: bool = False,
    db: Session = Depends(get_db),
):
    # `date` is kept as a back-compat alias for the old single-date param.
    # `fresh` (the page's Refresh button) skips the short live cache for today.
    start_date = start or date or restaurant_today()
    end_date = end or start_date
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="`end` must be on or after `start`")
    result = _items_sold_or_503(db, start_date, end_date, fresh=fresh)
    _add_code_fields(db, result.get("items") or [])
    return result


def _add_code_fields(db: Session, items: list) -> None:
    """Additive (Oct 14, W2): each row's vendor SKUs and barcode from the
    latest upload, so the page's search also finds an item by its codes
    (services/product_match.py). A copy per row - the live result is cached."""
    from app.services.product_match import code_fields
    from app.services.reorder import latest_inventory_by_item

    try:
        latest = latest_inventory_by_item(db)
    except Exception:  # noqa: BLE001 - codes are a search aid, never a reason to fail the list
        logger.exception("Items Sold: couldn't load item codes")
        return
    for i, item in enumerate(items):
        items[i] = {**item, **code_fields(latest.get(item.get("item_id")), item)}


@router.get(
    "/items-sold/export", dependencies=[Depends(require_feature("items_sold")), Depends(require_sales_access)]
)
def items_sold_export(
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    db: Session = Depends(get_db),
):
    """The Items Sold list (same numbers the app/web show) as a CSV - one row
    per item, highest revenue first."""
    start_date = start or restaurant_today()
    end_date = end or start_date
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="`end` must be on or after `start`")
    result = _items_sold_or_503(db, start_date, end_date)
    rows = [
        {
            "Item": i["name"] or i.get("item_id"),
            "Qty Sold": i["quantity"],
            "Revenue": i["revenue"],
            "Avg Price": i["avg_price"],
        }
        for i in result["items"]
    ]
    filename = f"items-sold-{result['start_date']}_to_{result['end_date']}.csv"
    return Response(
        content=rows_to_csv(rows) or "Item,Qty Sold,Revenue,Avg Price\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# SalesDownload lives on the Inventory Reports page, so this is gated as
# "inventory_reports", not a standalone feature of its own.
@router.get("/export", dependencies=[Depends(require_feature("inventory_reports")), Depends(require_sales_access)])
def export_sales(
    start: dt.date = Query(..., description="Start of date range, inclusive"),
    end: dt.date = Query(..., description="End of date range, inclusive"),
    format: Literal["csv", "pdf"] = Query("csv"),
    db: Session = Depends(get_db),
):
    if end < start:
        raise HTTPException(status_code=400, detail="`end` must be on or after `start`")

    filename = f"{store_slug()}-sales-{start.isoformat()}_to_{end.isoformat()}.{format}"

    if format == "csv":
        content = export_csv(db, start, end)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    content = export_pdf(db, start, end)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Sales accuracy (services/sales_accuracy.py): admin-only. Recent business
# dates compared with Toast Analytics, and what the re-pull job corrected.
@router.get("/accuracy", dependencies=[Depends(require_admin), Depends(require_capability(Capability.DAY_TOTALS))])
def sales_accuracy_report(days: int = Query(30, ge=1, le=120), db: Session = Depends(get_db)):
    return sales_accuracy.accuracy_report(db, days)


def _run_repull_in_background(days: Optional[int]) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        sales_accuracy.run_sales_repull(db, days=days)
    finally:
        db.close()


@router.post(
    "/accuracy/run",
    status_code=202,
    dependencies=[Depends(require_admin), Depends(require_capability(Capability.DAY_TOTALS))],
)
def sales_accuracy_run(days: Optional[int] = Query(None, ge=1, le=sales_accuracy.MAX_DAYS)):
    """Start one re-pull now (in the background - it takes a minute: Toast
    builds each Analytics report on request). Poll GET /accuracy for the
    result."""
    if not toast_analytics.is_configured():
        raise HTTPException(status_code=409, detail="Toast Analytics is not set up on this server.")
    if sales_accuracy.is_running():
        raise HTTPException(status_code=409, detail="A sales accuracy check is already running.")
    threading.Thread(target=_run_repull_in_background, args=(days,), name="sales-repull", daemon=True).start()
    return {"started": True}
