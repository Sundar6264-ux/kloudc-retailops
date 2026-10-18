"""CRUD for manually-logged purchase/receiving records (see PurchaseLogEntry
in models.py for why this exists instead of pulling from Toast).
"""

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PurchaseLogEntry


def create_purchase_entry(
    db: Session,
    item_id: str,
    item_name: Optional[str],
    supplier: Optional[str],
    quantity_received: float,
    unit_cost: Optional[float],
    received_date: dt.date,
    notes: Optional[str],
    logged_by_user_id: Optional[int],
) -> PurchaseLogEntry:
    entry = PurchaseLogEntry(
        item_id=item_id,
        item_name=item_name,
        supplier=supplier,
        quantity_received=quantity_received,
        unit_cost=unit_cost,
        received_date=received_date,
        notes=notes,
        logged_by_user_id=logged_by_user_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_purchase_entries(
    db: Session, start_date: Optional[dt.date] = None, end_date: Optional[dt.date] = None
) -> list[PurchaseLogEntry]:
    stmt = select(PurchaseLogEntry).order_by(
        PurchaseLogEntry.received_date.desc(), PurchaseLogEntry.id.desc()
    )
    if start_date is not None:
        stmt = stmt.where(PurchaseLogEntry.received_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(PurchaseLogEntry.received_date <= end_date)
    return list(db.execute(stmt).scalars())


def delete_purchase_entry(db: Session, entry_id: int) -> bool:
    entry = db.get(PurchaseLogEntry, entry_id)
    if entry is None:
        return False
    db.delete(entry)
    db.commit()
    return True


_DELIVERY_REVIEW_NOTE_PREFIX = "Confirmed via Delivery Review"


def delivery_history(db: Session, days: int, today: dt.date) -> list[dict]:
    """Recent purchase-log entries rolled up per (received date, supplier) -
    the "Recently confirmed deliveries" list on the Delivery Review page.
    `source` says whether a delivery was confirmed through Delivery Review,
    logged by hand (Reconciliation form/CSV), or a mix of both."""
    from app.models import User  # local: keeps this module's imports minimal

    names = {u.id: u.username for u in db.execute(select(User)).scalars()}
    groups: dict = {}
    for entry in list_purchase_entries(db, today - dt.timedelta(days=days), today):
        key = (entry.received_date, entry.supplier or "Unknown supplier")
        g = groups.setdefault(
            key,
            {"items": 0, "quantity": 0.0, "cost": 0.0, "cost_known": 0, "sources": set(), "by": set()},
        )
        g["items"] += 1
        g["quantity"] += entry.quantity_received or 0.0
        if entry.unit_cost is not None:
            g["cost"] += (entry.quantity_received or 0.0) * entry.unit_cost
            g["cost_known"] += 1
        g["sources"].add(
            "delivery_review" if (entry.notes or "").startswith(_DELIVERY_REVIEW_NOTE_PREFIX) else "manual"
        )
        if entry.logged_by_user_id in names:
            g["by"].add(names[entry.logged_by_user_id])

    rows = []
    for (received_date, supplier), g in groups.items():
        rows.append(
            {
                "received_date": received_date.isoformat(),
                "supplier": supplier,
                "item_count": g["items"],
                "total_quantity": round(g["quantity"], 2),
                "estimated_cost": round(g["cost"], 2) if g["cost_known"] else None,
                "source": g["sources"].pop() if len(g["sources"]) == 1 else "mixed",
                "logged_by": sorted(g["by"]),
            }
        )
    rows.sort(key=lambda r: (r["received_date"], r["supplier"]), reverse=True)
    return rows
