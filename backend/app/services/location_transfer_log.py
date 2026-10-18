"""CRUD for manually-confirmed Container <-> Each stock movement records (see
LocationTransferLogEntry in models.py for why this exists as its own log
rather than reusing purchase_log - it's not a purchase, no vendor/cost).
"""

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LocationTransferLogEntry


def create_transfer_entry(
    db: Session,
    item_id: str,
    item_name: Optional[str],
    direction: str,
    quantity: float,
    transfer_date: dt.date,
    notes: Optional[str],
    logged_by_user_id: Optional[int],
) -> LocationTransferLogEntry:
    entry = LocationTransferLogEntry(
        item_id=item_id,
        item_name=item_name,
        direction=direction,
        quantity=quantity,
        transfer_date=transfer_date,
        notes=notes,
        logged_by_user_id=logged_by_user_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_transfer_entries(
    db: Session, start_date: Optional[dt.date] = None, end_date: Optional[dt.date] = None
) -> list[LocationTransferLogEntry]:
    stmt = select(LocationTransferLogEntry).order_by(
        LocationTransferLogEntry.transfer_date.desc(), LocationTransferLogEntry.id.desc()
    )
    if start_date is not None:
        stmt = stmt.where(LocationTransferLogEntry.transfer_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(LocationTransferLogEntry.transfer_date <= end_date)
    return list(db.execute(stmt).scalars())


def delete_transfer_entry(db: Session, entry_id: int) -> bool:
    entry = db.get(LocationTransferLogEntry, entry_id)
    if entry is None:
        return False
    db.delete(entry)
    db.commit()
    return True
