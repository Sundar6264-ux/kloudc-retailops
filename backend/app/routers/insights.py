"""Insights from the Toast Analytics API (docs/INSIGHTS_CONTRACT.md).

Every route needs the `sales_insights` feature (admins have it implicitly).
Loss watch and Odd checks (per-employee data) are admin only. A "Hide sales
numbers" user never gets a dollar amount: the heatmap answers with order
counts only, the prep forecast and returning customers have no dollars at
all, and every other route answers 403. Every answer carries
{generated_at, stale, source: "toast_analytics"}; when Analytics isn't set
up or Toast fails, the routes answer from the cache with stale: true - never
a 500. The only write is acknowledging a flag (admins; the demo login is
refused by the global demo guard).
"""

import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.features import require_feature
from app.models import User
from app.pos.capabilities import Capability
from app.pos.gating import require_capability
from app.sales_access import SALES_HIDDEN_DETAIL, hides_sales
from app.services import insights
from app.services import insights_data as data

FEATURE = "sales_insights"

router = APIRouter(
    prefix="/api/insights",
    tags=["insights"],
    dependencies=[
        Depends(get_current_user),
        Depends(require_feature(FEATURE)),
        # Multi-store MT6: Toast Analytics reports; 409 for a POS without them.
        Depends(require_capability(Capability.ANALYTICS_REPORTS)),
    ],
)

MAX_FORECAST_DAYS_AHEAD = 14


def _no_hidden_sales(user: User) -> None:
    if hides_sales(user):
        raise HTTPException(status_code=403, detail=SALES_HIDDEN_DETAIL)


@router.get("/hourly-heatmap")
def hourly_heatmap(
    weeks: int = Query(8),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if weeks not in insights.HEATMAP_WEEKS:
        raise HTTPException(status_code=422, detail="weeks must be 4, 8 or 12")
    return insights.hourly_heatmap(db, weeks, hide_sales=hides_sales(user))


@router.get("/channels")
def channels(
    weeks: int = Query(8, ge=1, le=12),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _no_hidden_sales(user)
    return insights.channels(db, weeks)


@router.get("/compare")
def compare(
    date: Optional[dt.date] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _no_hidden_sales(user)
    today = data.today_business_date()
    if date is not None and date > today:
        raise HTTPException(status_code=400, detail="That date hasn't happened yet.")
    return insights.compare(db, date)


@router.get("/check-trends")
def check_trends(
    weeks: int = Query(12, ge=1, le=13),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _no_hidden_sales(user)
    return insights.check_trends(db, weeks)


@router.get("/loss-watch", dependencies=[Depends(require_admin)])
def loss_watch(days: int = Query(30, ge=1, le=30), db: Session = Depends(get_db)):
    return insights.loss_watch(db, days)


@router.post("/loss-watch/{flag_id}/ack", dependencies=[Depends(require_admin)])
def ack_loss_watch(flag_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = insights.ack_flag(db, flag_id, user)
    if out is None:
        raise HTTPException(status_code=404, detail="No such flag")
    return out


@router.get("/odd-checks", dependencies=[Depends(require_admin)])
def odd_checks(days: int = Query(7, ge=1, le=30), db: Session = Depends(get_db)):
    return insights.odd_checks(db, days)


@router.get("/menu-performance")
def menu_performance(
    days: int = Query(28, ge=1, le=56),
    group: Literal["all", "kitchen", "grocery"] = Query("all"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _no_hidden_sales(user)
    return insights.menu_performance(db, days, group)


@router.get("/addons")
def addons(
    days: int = Query(28, ge=1, le=56),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _no_hidden_sales(user)
    return insights.addons(db, days)


@router.get("/prep-forecast")
def prep_forecast(date: Optional[dt.date] = Query(None), db: Session = Depends(get_db)):
    today = data.today_business_date()
    if date is not None and date > today + dt.timedelta(days=MAX_FORECAST_DAYS_AHEAD):
        raise HTTPException(status_code=400, detail=f"Pick a date within {MAX_FORECAST_DAYS_AHEAD} days.")
    return insights.prep_forecast(db, date)


@router.get("/returning-customers", dependencies=[Depends(require_capability(Capability.CARD_FINGERPRINTS))])
def returning_customers(weeks: int = Query(8, ge=1, le=12), db: Session = Depends(get_db)):
    return insights.returning_customers(db, weeks)
