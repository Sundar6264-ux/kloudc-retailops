import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.features import require_feature
from app.models import User
from app.sales_access import TOAST_SALES_DOLLAR_FIELDS, strip_sales_deep
from app.services.csv_util import rows_to_csv
from app.services.fix_issues import (
    ISSUE_TYPES,
    IssueCloseError,
    all_reasons,
    close_issues,
    get_closed_report,
    get_issue_history,
    get_issue_list,
    get_summary,
    reopen_issues,
)
from app.timeutil import restaurant_today
from app.team.source_sync import task_sources_changed

# Website Fix Issues page. Same data as the Inventory Reports feature, so the
# same grant. The mobile app still reads /api/reports/*, which is unchanged.
router = APIRouter(
    prefix="/api/issues", tags=["issues"], dependencies=[Depends(require_feature("inventory_reports"))]
)


class IssueCloseRequest(BaseModel):
    issue_type: str
    issue_keys: list[str] = Field(min_length=1, max_length=5000)
    reason: str
    reason_detail: Optional[str] = Field(default=None, max_length=500)


class IssueReopenRequest(BaseModel):
    issue_type: str
    issue_keys: list[str] = Field(min_length=1, max_length=5000)


def _check_type(issue_type: str) -> None:
    if issue_type not in ISSUE_TYPES:
        raise HTTPException(status_code=404, detail="Unknown issue type.")


@router.get("/reasons")
def reasons():
    return all_reasons()


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    # Additive (W4): `leads` = every saved category / vendor lead, for the
    # Fix Issues lead filter (a lead with nothing open still shows).
    from app.services.issue_leads import lead_people

    return {"items": get_summary(db), "leads": lead_people(db)}


@router.get("/report")
def report(
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    end = end or restaurant_today()
    start = start or end - dt.timedelta(days=29)
    if end < start:
        raise HTTPException(status_code=400, detail="End date is before start date.")
    return strip_sales_deep(get_closed_report(db, start, end), TOAST_SALES_DOLLAR_FIELDS, user)


@router.post("/close")
def close(body: IssueCloseRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _check_type(body.issue_type)
    try:
        n = close_issues(db, body.issue_type, body.issue_keys, body.reason, body.reason_detail, user.username)
    except IssueCloseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task_sources_changed("issue_leads")  # the leads' "N left of M" moves now (Oct 15)
    return {"closed": n}


@router.post("/reopen")
def reopen(body: IssueReopenRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _check_type(body.issue_type)
    n = reopen_issues(db, body.issue_type, body.issue_keys, user.username)
    task_sources_changed("issue_leads")  # the leads' "N left of M" moves now (Oct 15)
    return {"reopened": n}


@router.get("/{issue_type}")
def issue_list(
    issue_type: str,
    came_back: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _check_type(issue_type)
    # Toast's "last N day sales" are dollars: null (at the top level, in
    # `product` and in duplicates' `variants[].product`) for a "Hide sales
    # numbers" user (app/sales_access.py). Units stay.
    # ?came_back=1 (additive): only open rows that were closed and came back.
    data = get_issue_list(db, issue_type, came_back_only=came_back)
    return strip_sales_deep(data, TOAST_SALES_DOLLAR_FIELDS, user)


@router.get("/{issue_type}/history")
def issue_history(issue_type: str, key: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """Every close / reopen of one item (issue_key), newest first."""
    _check_type(issue_type)
    return get_issue_history(db, issue_type, key)


def _field(row: dict, key: str):
    """A row field, falling back to the item's product details."""
    val = row.get(key)
    return val if val is not None else (row.get("product") or {}).get(key)


def _num(val):
    """30.0 -> 30, 5.49 -> 5.49; blanks stay blank."""
    if val is None:
        return None
    return int(val) if float(val).is_integer() else round(val, 2)


# Only the columns staff actually use to fix each issue, with readable
# headers - not every internal field (item ids, issue keys, snapshot dates).
# The phone shows this CSV as a table, so every extra column costs width.
_BASE_COLUMNS = [
    ("Product", lambda r: r.get("name")),
    ("Category", lambda r: _field(r, "category")),
    ("Vendor", lambda r: _field(r, "supplier")),
]
_EXPORT_COLUMNS = {
    "missing_barcode": [
        ("Vendor Code", lambda r: _field(r, "supplier_item_id")),
        ("On Hand", lambda r: _num(_field(r, "on_hand_qty"))),
        ("Price", lambda r: _num(_field(r, "price"))),
    ],
    "invalid_barcode": [
        ("Barcode", lambda r: _field(r, "barcode")),
        # A closed row's "reason" is the close reason, not the barcode problem.
        ("Problem", lambda r: None if r.get("closed_at") else r.get("reason")),
        ("On Hand", lambda r: _num(_field(r, "on_hand_qty"))),
    ],
    "duplicate": [
        ("Copies", lambda r: r.get("copies")),
        ("Prices", lambda r: r.get("prices")),
    ],
    "dead_stock": [
        ("On Hand", lambda r: _num(_field(r, "on_hand_qty"))),
        ("Stock Value", lambda r: _num(_field(r, "inventory_value"))),
        ("Sold (90 days)", lambda r: _num(_field(r, "last_90_day_units"))),
        ("Last Sold", lambda r: _field(r, "last_sold_date")),
    ],
    "negative_inventory": [
        ("On Hand", lambda r: _num(_field(r, "on_hand_qty"))),
        ("Cost", lambda r: _num(_field(r, "cost"))),
        ("Sold (30 days)", lambda r: _num(_field(r, "last_30_day_units"))),
        ("Last Received", lambda r: (_field(r, "last_received") or "")[:10] or None),
    ],
    "refill_from_container": [
        ("Store Qty", lambda r: _num(r.get("store_qty"))),
        ("Container Qty", lambda r: _num(r.get("container_qty"))),
        ("Sold (30 days)", lambda r: _num(_field(r, "last_30_day_units"))),
    ],
    "slow_review": [
        ("Why Review", lambda r: r.get("review_reason")),
        ("On Hand", lambda r: _num(r.get("on_hand_qty"))),
        ("Sold (90 days)", lambda r: _num(r.get("units_sold"))),
        ("Days With A Sale", lambda r: r.get("sale_days")),
        ("Per Week", lambda r: _num(r.get("weekly_demand"))),
        ("Last Sold", lambda r: _field(r, "last_sold_date")),
    ],
    "dead_with_stock": [
        ("On Hand", lambda r: _num(r.get("on_hand_qty"))),
        ("Stock Value", lambda r: _num(_field(r, "inventory_value"))),
        ("Days In Stock (90)", lambda r: _num(r.get("available_days"))),
        ("Last Sold", lambda r: _field(r, "last_sold_date")),
    ],
    "dead_zero_stock": [
        ("On Hand", lambda r: _num(r.get("on_hand_qty"))),
        ("Days In Stock (90)", lambda r: _num(r.get("available_days"))),
        ("Last Sold", lambda r: _field(r, "last_sold_date")),
        ("Last Received", lambda r: (_field(r, "last_received") or "")[:10] or None),
        ("Cost", lambda r: _num(_field(r, "cost"))),
    ],
    "dead_new_arrival": [
        ("Arrived", lambda r: r.get("arrived_date")),
        ("Days Since", lambda r: r.get("days_since_arrival")),
        ("On Hand", lambda r: _num(r.get("on_hand_qty"))),
        ("Stock Value", lambda r: _num(_field(r, "inventory_value"))),
    ],
}
_CLOSED_COLUMNS = [
    (
        "Closed Reason",
        lambda r: " - ".join(x for x in (r.get("reason_label"), r.get("reason_detail")) if x)
        or ("No longer detected" if r.get("no_longer_detected") else None),
    ),
    ("Closed By", lambda r: r.get("closed_by")),
    ("Closed On", lambda r: r.get("closed_date")),
]


@router.get("/{issue_type}/export")
def issue_export(
    issue_type: str,
    status: str = Query("open", pattern="^(open|closed)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _check_type(issue_type)
    # .get: a type without its own extra columns still exports the base ones.
    columns = _BASE_COLUMNS + _EXPORT_COLUMNS.get(issue_type, []) + (_CLOSED_COLUMNS if status == "closed" else [])
    # No export column is a sales dollar figure today; stripped anyway so a
    # future column can't leak it to a "Hide sales numbers" user.
    listed = strip_sales_deep(get_issue_list(db, issue_type)[status], TOAST_SALES_DOLLAR_FIELDS, user)
    rows = [{header: get(r) for header, get in columns} for r in listed]
    if not rows:
        # Still give the file its header row, so an empty export isn't blank.
        rows_csv = ",".join(h for h, _ in columns) + "\n"
    else:
        rows_csv = rows_to_csv(rows)
    return Response(
        content=rows_csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{issue_type}-{status}.csv"'},
    )
