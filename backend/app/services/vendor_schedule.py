"""CRUD for the one-time manual vendor delivery-schedule config (delivery
weekday(s) + order lead time) that services/vendor_calendar.py's recurring
calendar is built on top of - see that module's docstring for how a single
row here turns into an indefinitely-recurring, self-updating calendar with
no further manual entry needed.
"""

import datetime as dt
import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VendorSchedule
from app.timeutil import utcnow


def _encode_weekdays(weekdays: List[int]) -> str:
    return json.dumps(sorted({w for w in weekdays if 0 <= w <= 6}))


def decode_weekdays(raw: str) -> List[int]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, int) and 0 <= v <= 6]


def list_vendor_schedules(db: Session, active_only: bool = False) -> List[VendorSchedule]:
    stmt = select(VendorSchedule).order_by(VendorSchedule.vendor)
    if active_only:
        stmt = stmt.where(VendorSchedule.active.is_(True))
    return list(db.execute(stmt).scalars().all())


def get_vendor_schedule_by_name(db: Session, vendor: str) -> Optional[VendorSchedule]:
    stmt = select(VendorSchedule).where(VendorSchedule.vendor == vendor.strip())
    return db.execute(stmt).scalars().first()


def create_vendor_schedule(db: Session, vendor: str, delivery_weekdays: List[int], lead_days: int) -> VendorSchedule:
    row = VendorSchedule(
        vendor=vendor.strip(),
        delivery_weekdays=_encode_weekdays(delivery_weekdays),
        lead_days=lead_days,
        active=True,
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_vendor_schedule(
    db: Session,
    schedule_id: int,
    delivery_weekdays: Optional[List[int]] = None,
    lead_days: Optional[int] = None,
    active: Optional[bool] = None,
) -> Optional[VendorSchedule]:
    row = db.get(VendorSchedule, schedule_id)
    if row is None:
        return None
    if delivery_weekdays is not None:
        row.delivery_weekdays = _encode_weekdays(delivery_weekdays)
    if lead_days is not None:
        row.lead_days = lead_days
    if active is not None:
        row.active = active
    db.commit()
    db.refresh(row)
    return row


def delete_vendor_schedule(db: Session, schedule_id: int) -> bool:
    row = db.get(VendorSchedule, schedule_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def schedule_out(row: VendorSchedule) -> dict:
    return {
        "id": row.id,
        "vendor": row.vendor,
        "delivery_weekdays": decode_weekdays(row.delivery_weekdays),
        "lead_days": row.lead_days,
        "active": row.active,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, dt.datetime) else row.created_at,
    }
