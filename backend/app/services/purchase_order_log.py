"""A real, minimal log of purchase orders sent to a vendor - see
`PlacedOrder` in models.py for why this exists and what it deliberately
doesn't try to capture (no dollar total, no live cart reference).

Two kinds of row (`PlacedOrder.status`):
- "pdf": a PO PDF was generated (the app builds one to preview/share it on
  WhatsApp), but nobody has confirmed the order. One row per supplier: a new
  export within PDF_REUSE_WINDOW updates it instead of adding another.
- "placed": someone pressed "Mark placed". That turns the supplier's recent
  "pdf" row (PLACED_CONVERT_WINDOW) into the placed order, so a preview +
  Mark placed is ONE order, not two (the Sep 28 report).
  A repeat Mark placed within DUPLICATE_WINDOW (double tap, network retry)
  returns the order already recorded instead of creating a second one.
"""

import datetime as dt
import json
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CartItem, PlacedOrder, User
from app.services.item_vendors import vendors_of
from app.services.po_cart import add_items, clear_supplier, list_cart
from app.services.reorder import latest_inventory_by_item
from app.timeutil import restaurant_timezone, utcnow
from app.tenancy import advisory_lock, tenant_lock

STATUS_PDF = "pdf"
STATUS_PLACED = "placed"
STATUSES = (STATUS_PDF, STATUS_PLACED)
# PlacedOrder.source (migration 0053): "Order already placed" from the Today
# tab / Vendor Calendar / Reorder (services/order_actions.py).
SOURCE_OUTSIDE = "outside"

PDF_REUSE_WINDOW = dt.timedelta(hours=24)
PLACED_CONVERT_WINDOW = dt.timedelta(hours=48)
DUPLICATE_WINDOW = dt.timedelta(minutes=2)

# Makes "check for an existing row, then write" one step within this process
# (FastAPI runs these sync routes on a thread pool, so two taps can overlap).
_write_lock = tenant_lock("purchase_order_log._write_lock")


def order_items(row: PlacedOrder) -> list:
    try:
        items = json.loads(row.items_json or "[]")
    except ValueError:
        return []
    return items if isinstance(items, list) else []


def _qty(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _lines_key(items: list) -> list:
    """What makes two snapshots "the same order": name, code and qty per
    line, order-independent."""
    return sorted(
        (
            str(i.get("name") or "").strip().lower(),
            str(i.get("supplier_item_id") or "").strip().lower(),
            round(_qty(i.get("qty")), 3),
        )
        for i in items
        if isinstance(i, dict)
    )


def _latest(db: Session, supplier: str, status: str, since: dt.datetime) -> Optional[PlacedOrder]:
    stmt = (
        select(PlacedOrder)
        .where(PlacedOrder.supplier == supplier, PlacedOrder.status == status, PlacedOrder.placed_at >= since)
        .order_by(PlacedOrder.placed_at.desc(), PlacedOrder.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _fill(row: PlacedOrder, items: list, user: User, status: str) -> None:
    row.status = status
    row.item_count = len(items)
    row.items_json = json.dumps(items)
    row.placed_by_user_id = user.id
    row.placed_by_name = user.username
    row.placed_at = utcnow()


def record_pdf_export(db: Session, supplier: str, items: list, user: User) -> PlacedOrder:
    """Log a PO PDF export as the supplier's one "pdf" row (updated in place
    when there's already one from the last 24h)."""
    with _write_lock, advisory_lock(db, "purchase_order_log"):
        row = _latest(db, supplier, STATUS_PDF, utcnow() - PDF_REUSE_WINDOW)
        if row is None:
            row = PlacedOrder(supplier=supplier)
            db.add(row)
        _fill(row, items, user, STATUS_PDF)
        db.commit()
        db.refresh(row)
        return row


def mark_supplier_placed(db: Session, supplier: str, user: User) -> tuple:
    """Snapshot `supplier`'s cart as a placed order and clear the cart.

    Returns (row, duplicate). `row` is None when the cart is empty and nothing
    was placed for this supplier in the last couple of minutes (the caller
    answers 400, as before). `duplicate` is True when an identical order had
    just been recorded - that row is returned and no new one is created.
    """
    with _write_lock, advisory_lock(db, "purchase_order_log"):
        now = utcnow()
        cart_rows = [r for r in list_cart(db) if r.supplier == supplier]
        just_placed = _latest(db, supplier, STATUS_PLACED, now - DUPLICATE_WINDOW)
        if not cart_rows:
            return just_placed, just_placed is not None

        snapshot = [
            {
                "name": r.name,
                "supplier_item_id": r.supplier_item_id,
                "qty": r.qty,
                "item_id": r.item_id,
                "case_of": r.case_of,
            }
            for r in cart_rows
        ]
        if just_placed is not None and _lines_key(order_items(just_placed)) == _lines_key(snapshot):
            clear_supplier(db, supplier)
            return just_placed, True

        row = _latest(db, supplier, STATUS_PDF, now - PLACED_CONVERT_WINDOW)
        if row is None:
            row = PlacedOrder(supplier=supplier)
            db.add(row)
        _fill(row, snapshot, user, STATUS_PLACED)
        db.commit()
        db.refresh(row)
        clear_supplier(db, supplier)
        return row, False


def place_sent_order(db: Session, supplier: str, user: User, snapshot: list, cart_item_ids: list) -> tuple:
    """Mark placed for a PO that was just sent from the app (services/po_send.py).

    The same as Mark placed (the supplier's recent "pdf" row becomes the
    placed order, a repeat within DUPLICATE_WINDOW returns the order already
    recorded), except that it records exactly `snapshot` - the lines on the
    PDF that went out - and clears only the cart lines it came from
    (`cart_item_ids`). A line added to the cart while the email was being sent
    wasn't in the PO, so it stays in the cart. Returns (row, duplicate).
    """
    with _write_lock, advisory_lock(db, "purchase_order_log"):
        now = utcnow()
        just_placed = _latest(db, supplier, STATUS_PLACED, now - DUPLICATE_WINDOW)
        if just_placed is not None and _lines_key(order_items(just_placed)) == _lines_key(snapshot):
            row, duplicate = just_placed, True
        else:
            row = _latest(db, supplier, STATUS_PDF, now - PLACED_CONVERT_WINDOW)
            if row is None:
                row = PlacedOrder(supplier=supplier)
                db.add(row)
            _fill(row, snapshot, user, STATUS_PLACED)
            duplicate = False
        ids = [i for i in cart_item_ids if isinstance(i, int)]
        if ids:
            for cart_row in db.execute(select(CartItem).where(CartItem.id.in_(ids))).scalars():
                db.delete(cart_row)
        db.commit()
        db.refresh(row)
        return row, duplicate


def list_recent_placed_orders(db: Session, limit: int = 10) -> list:
    """Newest confirmed ("placed") orders - what Home's Recent Orders Placed
    shows. PDF-only rows are left out."""
    stmt = (
        select(PlacedOrder)
        .where(PlacedOrder.status == STATUS_PLACED)
        .order_by(PlacedOrder.placed_at.desc(), PlacedOrder.id.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def _utc_start_of(day: dt.date) -> dt.datetime:
    local = dt.datetime.combine(day, dt.time.min).replace(tzinfo=restaurant_timezone())
    return local.astimezone(dt.timezone.utc)


def _matches(row: PlacedOrder, needle: str) -> bool:
    if needle in (row.supplier or "").lower():
        return True
    for item in order_items(row):
        if not isinstance(item, dict):
            continue
        if needle in str(item.get("name") or "").lower() or needle in str(item.get("supplier_item_id") or "").lower():
            return True
    # W2 (Oct 14): an order line's Toast item id too (product_match rule).
    from app.services.product_match import any_code_match

    return any_code_match(needle, order_items(row))


def search_placed_orders(
    db: Session,
    supplier: Optional[str] = None,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple:
    """(rows, total) newest first. `start`/`end` are store-local dates
    (inclusive). `search` matches the vendor name or any item's name/code."""
    stmt = select(PlacedOrder)
    if supplier:
        stmt = stmt.where(PlacedOrder.supplier == supplier)
    if status:
        stmt = stmt.where(PlacedOrder.status == status)
    if start:
        stmt = stmt.where(PlacedOrder.placed_at >= _utc_start_of(start))
    if end:
        stmt = stmt.where(PlacedOrder.placed_at < _utc_start_of(end + dt.timedelta(days=1)))
    stmt = stmt.order_by(PlacedOrder.placed_at.desc(), PlacedOrder.id.desc())

    needle = (search or "").strip().lower()
    if needle:
        # Items live in a JSON snapshot, so the match is done here. A few
        # orders a day - the whole filtered history is small.
        rows = [r for r in db.execute(stmt).scalars() if _matches(r, needle)]
        return rows[offset : offset + limit], len(rows)

    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = list(db.execute(stmt.limit(limit).offset(offset)).scalars())
    return rows, total


def placed_order_suppliers(db: Session) -> list:
    """Every vendor with an order on record, most recently ordered first -
    the vendor filter's options."""
    stmt = (
        select(PlacedOrder.supplier, func.max(PlacedOrder.placed_at).label("last"))
        .group_by(PlacedOrder.supplier)
        .order_by(func.max(PlacedOrder.placed_at).desc())
    )
    return [r.supplier for r in db.execute(stmt)]


def order_out(row: PlacedOrder) -> dict:
    items = order_items(row)
    return {
        "id": row.id,
        "supplier": row.supplier,
        "status": row.status or STATUS_PLACED,
        "item_count": row.item_count,
        "total_qty": round(sum(_qty(i.get("qty")) for i in items if isinstance(i, dict)), 3),
        "placed_by_name": row.placed_by_name,
        "placed_at": row.placed_at.isoformat(),
        "items": items,
        # W1 (additive): "outside" = marked "Order already placed" - no lines.
        "source": row.source or "app",
        "source_label": "Placed outside the app" if row.source == SOURCE_OUTSIDE else None,
        "note": row.note,
        "for_delivery_date": row.for_delivery_date.isoformat() if row.for_delivery_date else None,
    }


def _resolve_item_id(latest_inventory: dict, name: str, supplier: str) -> Optional[str]:
    """A Toast item_id for an older snapshot line that has none (PDF rows and
    pre-0032 orders only kept name/code/qty): same name, preferring an item
    this vendor supplies, then a priced one (not a Container duplicate)."""
    key = name.strip().lower()
    if not key:
        return None
    candidates = [s for s in latest_inventory.values() if (s.name or "").strip().lower() == key]
    if not candidates:
        return None

    def rank(snap) -> tuple:
        # Empty slots kept, and [""] for no supplier, as the old inline split.
        vendors = vendors_of(snap, keep_empty=True) or [""]
        return (supplier not in vendors, snap.price is None)

    return sorted(candidates, key=rank)[0].item_id


def order_again(db: Session, row: PlacedOrder, user: User) -> dict:
    """Put an order's lines back into that vendor's cart (same quantities).
    A line already in the cart keeps its row (its qty is set to the order's)."""
    lines = [i for i in order_items(row) if isinstance(i, dict) and (i.get("name") or "").strip()]
    if not lines:
        return {"supplier": row.supplier, "added": 0}
    latest_inventory = None
    in_cart_by_name = {
        (c.name or "").strip().lower(): c
        for c in db.execute(select(CartItem).where(CartItem.supplier == row.supplier)).scalars()
    }
    to_add = []
    for line in lines:
        item_id = line.get("item_id")
        if not item_id:
            existing = in_cart_by_name.get(line["name"].strip().lower())
            if existing is not None:
                item_id = existing.item_id
            else:
                if latest_inventory is None:
                    latest_inventory = latest_inventory_by_item(db)
                item_id = _resolve_item_id(latest_inventory, line["name"], row.supplier)
        qty = _qty(line.get("qty")) or 1.0
        if not item_id:
            existing = in_cart_by_name.get(line["name"].strip().lower())
            if existing is not None:
                existing.qty = qty
                continue
        to_add.append(
            {
                "item_id": item_id,
                "name": line["name"],
                "supplier_item_id": line.get("supplier_item_id"),
                "qty": qty,
                "case_of": line.get("case_of"),
            }
        )
    db.commit()
    add_items(db, row.supplier, to_add, user.id)
    return {"supplier": row.supplier, "added": len(lines)}
