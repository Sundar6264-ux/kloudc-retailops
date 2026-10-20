import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.features import require_feature
from app.db import get_db
from app.models import User
from app.sales_access import hides_sales
from app.schemas import JobStatusOut
from app.services.open_orders import JOB_NAME as OPEN_ORDERS_JOB
from app.services.sales_sync import JOB_NAME as SALES_SYNC_JOB, latest_job_run, sync_sales_for_date
from app.timeutil import restaurant_today

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_feature("overview"))])

TRACKED_JOBS = [SALES_SYNC_JOB, "inventory_upload", OPEN_ORDERS_JOB]


@router.get("/status", response_model=list[JobStatusOut])
def get_job_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    results = []
    for job_name in TRACKED_JOBS:
        job = latest_job_run(db, job_name)
        if job:
            if job_name == SALES_SYNC_JOB and hides_sales(user):
                # "312 line items synced from 145 orders" is how much the store
                # sold that day: no detail for a "Hide sales numbers" user
                # (app/sales_access.py). Status and time stay.
                job = JobStatusOut.model_validate(job).model_copy(update={"detail": None})
            results.append(job)
    return results


@router.post("/sales-sync/run", response_model=JobStatusOut)
def run_sales_sync_now(
    business_date: Optional[dt.date] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Manually trigger the sales sync (e.g. for backfilling a missed day or testing)."""
    target_date = business_date or (restaurant_today() - dt.timedelta(days=1))
    job = sync_sales_for_date(db, target_date)
    if hides_sales(user):  # the detail is the day's order/line counts
        return JobStatusOut.model_validate(job).model_copy(update={"detail": None})
    return job
