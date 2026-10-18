"""CRUD for the persistent, multi-supplier purchase order cart (see CartItem
in models.py). One shared cart for the store, not per-user - any staff
member can add to or check any supplier's section.
"""

import datetime as dt
import math
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CartItem, InventorySnapshot, User
from app.services.supplier_projection import _container_qty_by_name, _split_suppliers, _supplier_item_id_for
from app.tenancy import advisory_lock, tenant_lock

# The biggest qty (cases) or Case of a cart line may hold. Far above any real
# order, but it stops a typo, a pasted barcode or "1e308" from reaching the
# cart: qty x Case of x cost overflowed to infinity, which JSON can't encode,
# so one such line made GET /cart fail for everyone (Oct 7).
MAX_CART_QTY = 100_000
BAD_CART_NUMBER = f"must be a number no bigger than {MAX_CART_QTY:,}"


def bad_cart_number(value: Optional[float]) -> bool:
    """True for a qty / Case of the cart refuses: not finite, or bigger than
    MAX_CART_QTY either way. None (not sent) is fine; so is 0 or a negative
    number within range - what those mean is the routes' business."""
    return value is not None and (not math.isfinite(value) or abs(value) > MAX_CART_QTY)


# add_items checks "is this item already in the vendor's cart?" and then
# inserts - two adds of the same item at the same moment (a double submit,
# the phone and the website) would both insert. One process serves the app,
# so a lock makes the check-and-insert one step (re-entrant, so a caller that
# already holds it can't deadlock).
_add_lock = tenant_lock("po_cart._add_lock", reentrant=True)


def _lookup_supplier_item_id(db: Session, item_id: Optional[str], supplier: str) -> Optional[str]:
    """The vendor's SKU for `item_id` from its latest inventory snapshot. Used
    when a client adds an item without sending one - the mobile app's
    projection/reorder "Add to cart" never did, so the PO PDF's Supplier Code
    came out blank. Filling it here fixes already-installed app builds too.
    """
    if not item_id:
        return None
    snap = db.execute(
        select(InventorySnapshot)
        .where(InventorySnapshot.item_id == item_id)
        .order_by(InventorySnapshot.snapshot_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snap is None:
        return None
    # An item added from "all products" under a vendor that doesn't carry it
    # (the cart line is for the chosen vendor): the only codes on file belong
    # to the item's own vendors, and one of those must never print on this
    # vendor's PO. Leave it blank (Oct 7, 2026).
    wanted = (supplier or "").strip().lower()
    own = {s.strip().lower(): s for s in _split_suppliers(snap.supplier)}
    if wanted not in own:
        return None
    # Toast's own spelling, so "raja foods " still finds Raja Foods' code.
    return _supplier_item_id_for(snap, own[wanted])


def in_stock_for(item_id: str, snap, containers: dict) -> tuple:
    """(on_hand_qty, container_qty) for one inventory row: its on-hand from the
    latest upload plus its bare Container-location duplicate row, the same
    combined figure Supplier Projection and Fix Issues show
    (stock_issues._combined_on_hand). `containers` is
    `_container_qty_by_name(latest_inventory)`. Shared by the cart lines and
    the add-item search so "(in stock: N)" reads the same before and after
    an item is added.
    """
    container = containers.get(snap.name)
    # The item's own row can't be its own Container duplicate.
    container_qty = container[1] if container and container[0] != item_id else None
    on_hand = snap.inventory_quantity
    if container_qty is not None:
        on_hand = (on_hand or 0.0) + container_qty
    return on_hand, container_qty


def stock_by_cart_item(rows: list[CartItem], latest_inventory: dict) -> dict[int, tuple]:
    """cart row id -> (on_hand_qty, container_qty) for the cart's "(in stock: N)"
    bracket (see `in_stock_for`). A hand-added item (no item_id) or one no
    longer in the latest upload gets (None, None) - the clients show
    "(in stock: -)". Display only: never sent to the PO PDF or stored in the
    placed-order snapshot.
    """
    containers = _container_qty_by_name(latest_inventory)
    out: dict[int, tuple] = {}
    for row in rows:
        snap = latest_inventory.get(row.item_id) if row.item_id else None
        if snap is None:
            out[row.id] = (None, None)
            continue
        out[row.id] = in_stock_for(row.item_id, snap, containers)
    return out


def add_items(db: Session, supplier: str, items: list[dict], added_by_user_id: Optional[int]) -> list[CartItem]:
    """Add one or more items to `supplier`'s cart section. An item with a
    real `item_id` that's already in that supplier's cart has its quantity
    replaced (re-adding the same reorder-candidate item updates the
    quantity rather than creating a duplicate row); a hand-added item with
    no `item_id` always becomes its own new row, since name alone isn't a
    reliable identity for a real product.

    The list is one transaction: either every item is added or none is. An
    `item_id` listed twice in one call is one line (the later entry wins) -
    the session doesn't autoflush, so the second lookup used to miss the
    first one's pending row and insert a second line, after which every
    re-add of that item failed (Oct 7). Returns one row per entry in
    `items`, in order (a repeated item_id gets the same row again).
    """
    with _add_lock, advisory_lock(db, "po_cart"):
        added = []
        by_item_id: dict = {}
        for item in items:
            item_id = item.get("item_id")
            if not item.get("supplier_item_id"):
                item = {**item, "supplier_item_id": _lookup_supplier_item_id(db, item_id, supplier)}
            existing = by_item_id.get(item_id) if item_id else None
            if existing is None and item_id:
                # .first(): a cart that already holds the item twice (from
                # before this fix) still takes the add instead of failing.
                existing = (
                    db.execute(
                        select(CartItem)
                        .where(CartItem.supplier == supplier, CartItem.item_id == item_id)
                        .order_by(CartItem.id)
                    )
                    .scalars()
                    .first()
                )
            if existing:
                existing.qty = item["qty"]
                existing.case_of = item.get("case_of") or existing.case_of
                existing.name = item.get("name") or existing.name
                existing.supplier_item_id = item.get("supplier_item_id") or existing.supplier_item_id
                row = existing
            else:
                row = CartItem(
                    supplier=supplier,
                    item_id=item_id,
                    name=item["name"],
                    supplier_item_id=item.get("supplier_item_id"),
                    qty=item["qty"],
                    case_of=item.get("case_of") or 1.0,
                    added_by_user_id=added_by_user_id,
                )
                db.add(row)
            if item_id:
                by_item_id[item_id] = row
            added.append(row)
        db.commit()
    for row in {id(r): r for r in added}.values():
        db.refresh(row)
    return added


def list_cart(db: Session) -> list[CartItem]:
    stmt = select(CartItem).order_by(CartItem.supplier, CartItem.added_at, CartItem.id)
    return list(db.execute(stmt).scalars())


def update_item(
    db: Session, cart_item_id: int, qty: Optional[float] = None, case_of: Optional[float] = None
) -> Optional[CartItem]:
    row = db.get(CartItem, cart_item_id)
    if row is None:
        return None
    if qty is not None:
        row.qty = qty
    if case_of is not None:
        row.case_of = case_of
    db.commit()
    db.refresh(row)
    return row


def delete_item(db: Session, cart_item_id: int) -> bool:
    row = db.get(CartItem, cart_item_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def clear_supplier(db: Session, supplier: str) -> int:
    rows = list(db.execute(select(CartItem).where(CartItem.supplier == supplier)).scalars())
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


# --- Removal requests (Sep 30, owner: "Admins only can remove items added to
# cart (others can request removal)") -----------------------------------------

REMOVAL_ADMIN_ONLY = "Only an admin can remove items from the cart. Use Request removal."


def can_remove(user: User) -> bool:
    return bool(user.is_admin)


def removal_requested_out(row: CartItem) -> Optional[dict]:
    if row.removal_requested_at is None:
        return None
    return {
        "at": row.removal_requested_at.isoformat(),
        "by": row.removal_requested_by,
        "by_user_id": row.removal_requested_by_user_id,
        "reason": row.removal_requested_reason,
    }


def request_removal(db: Session, cart_item_id: int, user: User, reason: Optional[str]) -> Optional[CartItem]:
    """Marks the line. Asking again (same person) just updates the reason;
    a line someone else already asked about keeps the first request."""
    row = db.get(CartItem, cart_item_id)
    if row is None:
        return None
    reason = (reason or "").strip() or None
    if row.removal_requested_at is None:
        row.removal_requested_at = dt.datetime.now(dt.timezone.utc)
        row.removal_requested_by = user.username
        row.removal_requested_by_user_id = user.id
        row.removal_requested_reason = reason
    elif row.removal_requested_by_user_id == user.id and reason:
        row.removal_requested_reason = reason
    db.commit()
    db.refresh(row)
    return row


def clear_removal_request(db: Session, row: CartItem) -> CartItem:
    row.removal_requested_at = None
    row.removal_requested_by = None
    row.removal_requested_by_user_id = None
    row.removal_requested_reason = None
    db.commit()
    db.refresh(row)
    return row
