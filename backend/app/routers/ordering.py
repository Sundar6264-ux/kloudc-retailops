import datetime as dt
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.features import require_any_feature, require_feature
from app.models import CartItem, PlacedOrder, User
from app.schemas import CartAddRequest, CartItemOut, CartItemUpdate, CartRemovalRequest, SimplePOExportRequest
from app.services.critical_dismissals import annotate_vendor_groups, is_today_report
from app.services.forecast import forecast_daily_demand
from app.services.po_cart import (
    BAD_CART_NUMBER,
    REMOVAL_ADMIN_ONLY,
    add_items,
    bad_cart_number,
    can_remove,
    clear_removal_request,
    clear_supplier,
    delete_item,
    list_cart,
    removal_requested_out,
    request_removal,
    stock_by_cart_item,
    update_item,
)
from app.services.po_section_rules import cart_line_sections, load_section_rules
from app.services.po_send import build_po_pdf
from app.services.purchase_order_log import (
    STATUSES,
    list_recent_placed_orders,
    mark_supplier_placed,
    order_again,
    order_out,
    placed_order_suppliers,
    record_pdf_export,
    search_placed_orders,
)
from app.services.inventory_api import live_stock_meta
from app.services.order_suggestion import attach_to_projection, cart_projection_context
from app.services.reorder import (
    cached_report,
    compute_reorder_candidates,
    group_candidates_by_vendor,
    latest_inventory_by_item,
)
from app.services.similar_products import similar_counts, similar_items_payload
from app.services.supplier_projection import compute_supplier_projection, list_suppliers, protection_days
from app.services.weekly_sales import container_twin_id, weekly_sales_for_item
from app.sales_access import WEEKLY_SALES_MONEY_FIELDS, strip_sales
from app.timeutil import restaurant_today

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ordering", tags=["ordering"], dependencies=[Depends(get_current_user)])

DEFAULT_HORIZONS_DAYS = [7, 14, 21, 30]


@router.get("/forecast")
def get_forecast(
    lookback_days: int = Query(30, ge=1, le=365),
    as_of: Optional[dt.date] = None,
    db: Session = Depends(get_db),
):
    as_of_date = as_of or restaurant_today()
    demand = forecast_daily_demand(db, lookback_days, as_of_date)
    items = [
        {"item_id": item_id, **data}
        for item_id, data in demand.items()
    ]
    items.sort(key=lambda i: i["avg_daily_demand"], reverse=True)
    return {
        "lookback_days": lookback_days,
        "as_of": as_of_date.isoformat(),
        "window_start": (as_of_date - dt.timedelta(days=lookback_days)).isoformat(),
        "items": items,
    }


@router.get("/reorder-candidates", dependencies=[Depends(require_feature("reorder_candidates"))])
def get_reorder_candidates(
    lookback_days: int = Query(30, ge=1, le=365),
    lead_time_days: int = Query(7, ge=1, le=180),
    as_of: Optional[dt.date] = None,
    method: Optional[str] = Query(None, pattern="^(availability|legacy)$"),
    critical_only: bool = Query(
        False,
        description="Additive (Oct 15, speed #7): only the Home Critical tile's vendors (active, tier critical, "
        "hidden ones included) and no flat `candidates` list - ~55 KB instead of ~700 KB.",
    ),
    db: Session = Depends(get_db),
):
    as_of_date = as_of or restaurant_today()
    # Kept in memory a few minutes (speed #3, services/report_cache.py): the
    # same report for every request until sales / stock / orders change.
    report = cached_report(
        db, lookback_days, lead_time_days, as_of_date, method,
        candidates_fn=compute_reorder_candidates, group_fn=group_candidates_by_vendor,
    )
    candidates = report["candidates"]
    # Copies: the annotation below must not land on the cached groups.
    vendors = [dict(group) for group in report["vendors"]]
    # Additive (Oct 6): every vendor group gets critical_dismissed /
    # critical_dismissal - an admin hid it from the Today tile (Close / Skip
    # this week / Remind me later). Vendors and urgency are unchanged. Only the
    # Today tile's own settings can end a Close ("something new turned
    # critical"); other settings show the stored state. The date checked is
    # the one the report was worked out for (the server's date when as_of is
    # left out), so a server clock on another day than the store's never
    # ends one.
    annotate_vendor_groups(
        db, vendors, today_report=is_today_report(lookback_days, lead_time_days, as_of_date, method)
    )
    if critical_only:
        # After the annotation, which judges "came back" on every group.
        candidates = []
        vendors = [g for g in vendors if not g.get("inactive") and (g.get("urgency") or {}).get("tier") == "critical"]
    return {
        "lookback_days": lookback_days,
        "lead_time_days": lead_time_days,
        "as_of": as_of_date.isoformat(),
        "note": (
            "lead_time_days is a manual estimate, not pulled from vendors_reference - "
            "Toast's Purchasing API isn't accessible with the current credentials (403, "
            "missing scope). No PO draft yet for the same reason."
        ),
        "candidates": candidates,
        "vendors": vendors,
        "critical_only": critical_only,
        # Additive: on-hand counts are live from Toast (with
        # INVENTORY_API_SNAPSHOTS on) - {"live", "as_of"}; null = snapshot.
        "live_stock": live_stock_meta(),
    }


# Deliberately not feature-gated beyond plain login - the plain vendor-name
# list is used by both Supplier Projection and the Overview upload page's
# Delivery/Transfer Review vendor picker, and carries no report data itself.
@router.get("/suppliers")
def get_suppliers(db: Session = Depends(get_db)):
    return {"suppliers": list_suppliers(db)}


@router.get("/supplier-projection", dependencies=[Depends(require_feature("supplier_projection"))])
def get_supplier_projection(
    supplier: str = Query(..., min_length=1),
    start: Optional[dt.date] = Query(
        None, description="Start of the sales history window used to compute demand. Defaults to 30 days before `end`."
    ),
    end: Optional[dt.date] = Query(None, description="End of the sales history window (exclusive). Defaults to today."),
    lookback_days: Optional[int] = Query(
        None, ge=1, le=365, description="Deprecated - use start/end instead. Still honored if start/end aren't given."
    ),
    horizons: Optional[str] = Query(
        None, description="Comma-separated day counts, e.g. '7,14,21,30'. Defaults to 7/14/21/30."
    ),
    method: Optional[str] = Query(
        None,
        pattern="^(availability|legacy)$",
        description="Demand method. Defaults to the server setting (availability-adjusted); 'legacy' = old average.",
    ),
    db: Session = Depends(get_db),
):
    as_of_date = end or restaurant_today()
    if start is not None:
        effective_lookback_days = max(1, (as_of_date - start).days)
    elif lookback_days is not None:
        effective_lookback_days = lookback_days
    else:
        effective_lookback_days = 30
    horizons_days = (
        [int(h.strip()) for h in horizons.split(",") if h.strip()]
        if horizons
        else DEFAULT_HORIZONS_DAYS
    )
    items = compute_supplier_projection(db, supplier, effective_lookback_days, horizons_days, as_of_date, method)
    # Additive: how many similar products each item has (the "Similar"
    # button's count). From a per-upload cached index, a few ms per item;
    # null when it can't be computed (the clients then fetch on demand).
    counts = similar_counts(db, [i["item_id"] for i in items])
    for i in items:
        i["similar_count"] = counts.get(i["item_id"])
    # Additive: the PO section each item prints under on this vendor's PO
    # (read-only label on the web table).
    latest = None
    try:
        latest = latest_inventory_by_item(db)
        section_rules = load_section_rules(db)
        for i in items:
            snap = latest.get(i["item_id"])
            i["po_section"] = section_rules.section_for_snap(i["item_id"], snap, supplier) if snap is not None else None
    except Exception:  # noqa: BLE001
        logger.exception("PO section lookup for Supplier Projection failed for %s", supplier)
    try:
        protection = protection_days(db, supplier, as_of_date)
    except Exception:
        logger.exception("Protection period lookup failed for %s", supplier)
        protection = None
    # Additive (X1): each item's `suggested_order` (the cart quantity for the
    # first horizon = the page's order window, with its basis spelled out)
    # and the top-level `order_window`. services/order_suggestion.py.
    order_window = None
    try:
        if latest is None:
            latest = latest_inventory_by_item(db)
        order_window = attach_to_projection(db, supplier, items, horizons_days[0], as_of_date, latest)
    except Exception:  # noqa: BLE001
        logger.exception("Suggested order quantities failed for %s", supplier)
    return {
        "supplier": supplier,
        "order_window": order_window,
        "method": items[0].get("forecast", {}).get("method", "legacy") if items else None,
        # Days of stock needed until the order placed at the vendor's NEXT
        # cycle arrives, from its Vendor Order Calendar schedule (None if
        # the vendor has no schedule).
        "protection_days": protection,
        "lookback_days": effective_lookback_days,
        "window_start": (as_of_date - dt.timedelta(days=effective_lookback_days)).isoformat(),
        "horizons_days": horizons_days,
        "as_of": as_of_date.isoformat(),
        "items": items,
        # Additive: see reorder-candidates.
        "live_stock": live_stock_meta(),
    }


# Also Reorder Candidates' and Fix Issues' item details (W3): per-item units
# and on-hand counts only - revenue is still stripped for "Hide sales" users.
@router.get(
    "/item-weekly-sales",
    dependencies=[Depends(require_any_feature("supplier_projection", "reorder_candidates", "inventory_reports"))],
)
def get_item_weekly_sales(
    item_id: str = Query(..., min_length=1),
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    include_empty_weeks: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Units per week are demand (kept); `revenue` is null for a "Hide sales
    # numbers" user (app/sales_access.py). Each week also has avg_on_hand /
    # on_hand_days / period_days (services/weekly_sales.py); weeks with no
    # sale are listed only with include_empty_weeks (old app builds: off).
    weeks = strip_sales(
        weekly_sales_for_item(db, item_id, start, end, include_empty_weeks=include_empty_weeks),
        WEEKLY_SALES_MONEY_FIELDS,
        user,
    )
    return {
        "item_id": item_id,
        "weeks": weeks,
        # Additive (W3): the on-hand averages include a Container-location twin.
        "on_hand_includes_container": container_twin_id(db, item_id) is not None,
    }


@router.get("/similar-items", dependencies=[Depends(require_feature("supplier_projection"))])
def get_similar_items(
    item_id: str = Query(..., min_length=1),
    limit: Optional[int] = Query(None, ge=1, le=25),
    db: Session = Depends(get_db),
):
    """Other products in the store like this one (other brands / sizes of
    the same product), with stock, price, cost, vendors and 30-day units
    sold. Offline matcher - see services/similar_products.py."""
    payload = similar_items_payload(db, item_id, limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="Item not found in the latest inventory upload.")
    return payload


@router.post("/purchase-order/export-pdf", dependencies=[Depends(require_feature("purchase_order_cart"))])
def export_purchase_order_pdf(
    payload: SimplePOExportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items to include in the order.")
    items = [item.model_dump() for item in payload.items]
    # Grouped into picking sections (supplier complaint: cart-order POs were
    # hard to pick), with owner-edited PO sections and hand-added lines'
    # sections applied. Purely a server-side layout change - the
    # request/response are unchanged for web and already-installed mobile
    # builds. Any grouping failure falls back to the old ungrouped PDF so an
    # export can never break. Shared with "Send PO" (services/po_send.py).
    pdf_bytes = build_po_pdf(db, payload.supplier, items)
    # Logged as the supplier's one "pdf" row (updated on every export, not a
    # new row each time) - the app builds this PDF just to preview/share it,
    # so it's not a confirmed order. "Mark placed" turns that row into the
    # placed order (services/purchase_order_log.py).
    record_pdf_export(db, payload.supplier, items, user)
    safe_supplier = "".join(c if c.isalnum() else "-" for c in payload.supplier.lower()).strip("-")
    filename = f"purchase-order-{safe_supplier}-{restaurant_today().isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/orders/recent", dependencies=[Depends(require_feature("purchase_order_cart"))])
def get_recent_placed_orders(limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    # Confirmed ("placed") orders only - a PDF preview isn't an order. The
    # installed apps read this shape; `status`/`total_qty` are additive.
    return {"items": [order_out(r) for r in list_recent_placed_orders(db, limit)]}


@router.get("/orders", dependencies=[Depends(require_feature("purchase_order_cart"))])
def get_placed_orders(
    supplier: Optional[str] = Query(None),
    start: Optional[dt.date] = Query(None, description="Store-local date (America/New_York), inclusive"),
    end: Optional[dt.date] = Query(None, description="Store-local date (America/New_York), inclusive"),
    status: Optional[str] = Query(None, description='"placed", "pdf", or omit for both'),
    search: Optional[str] = Query(None, description="Vendor, item name or item code"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Full placed-order history, newest first, for the Placed Orders pages."""
    if status in ("", "all"):
        status = None
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(STATUSES)}.")
    if start and end and end < start:
        raise HTTPException(status_code=400, detail="end must be on or after start.")
    rows, total = search_placed_orders(
        db, supplier=supplier or None, start=start, end=end, status=status, search=search, limit=limit, offset=offset
    )
    return {
        "items": [order_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "suppliers": placed_order_suppliers(db),
    }


@router.post("/orders/{order_id}/order-again", dependencies=[Depends(require_feature("purchase_order_cart"))])
def post_order_again(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Add a past order's items back into that vendor's cart."""
    row = db.get(PlacedOrder, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return order_again(db, row, user)


# --- Purchase order cart --------------------------------------------------
# A persistent, shared cart (see CartItem/po_cart.py) so a Supplier Projection
# review doesn't have to end in one immediate PDF: items can be added across
# multiple visits and multiple suppliers, then reviewed/edited/exported later
# from the Purchase Order Cart page.


def _cart_item_out(
    row: CartItem,
    unit_cost: Optional[float] = None,
    stock: Optional[tuple] = None,
    section: Optional[tuple] = None,
) -> CartItemOut:
    total_units = row.qty * row.case_of
    line_cost = unit_cost * total_units if unit_cost is not None else None
    return CartItemOut(
        id=row.id,
        supplier=row.supplier,
        item_id=row.item_id,
        name=row.name,
        supplier_item_id=row.supplier_item_id,
        qty=row.qty,
        case_of=row.case_of,
        added_at=row.added_at,
        unit_cost=unit_cost,
        total_units=total_units,
        line_cost=line_cost,
        on_hand_qty=stock[0] if stock else None,
        container_qty=stock[1] if stock else None,
        section=section[0] if section else None,
        section_source=section[1] if section else None,
        removal_requested=removal_requested_out(row),
    )


def _require_remover(user: User) -> None:
    """Only admins take lines out of the cart (owner, Sep 30); everyone else
    uses Request removal. Old app builds show this detail as a snackbar."""
    if not can_remove(user):
        raise HTTPException(status_code=403, detail=REMOVAL_ADMIN_ONLY)


_cart_access = Depends(require_any_feature("supplier_projection", "purchase_order_cart"))


def _require_sane_numbers(qty: Optional[float], case_of: Optional[float]) -> None:
    """422 with a plain-text detail (the website and the app show it as is)
    for a qty / Case of that isn't finite or is over po_cart.MAX_CART_QTY -
    one such line used to make GET /cart fail for everyone."""
    for label, value in (("Qty", qty), ("Case of", case_of)):
        if bad_cart_number(value):
            raise HTTPException(status_code=422, detail=f"{label} {BAD_CART_NUMBER}.")


@router.get("/cart", dependencies=[_cart_access])
def get_cart(
    projection: bool = Query(False, description="X1: add each line's projection context (slower; opt-in)."),
    lookback_days: int = Query(90, ge=7, le=365),
    horizon_days: int = Query(7, ge=1, le=180),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = list_cart(db)
    # Cost is looked up fresh from the latest inventory upload every time,
    # not stored on the cart row - it should always reflect Toast's current
    # cost, and a hand-added item (no item_id) simply has none to look up.
    latest_inventory = latest_inventory_by_item(db)
    # Additive: "(in stock: N)" per line (shelf + Container row), display only.
    stock = stock_by_cart_item(rows, latest_inventory)
    # Additive: the PO section each line prints under (display only).
    try:
        sections = cart_line_sections(db, rows, latest_inventory)
    except Exception:  # noqa: BLE001
        logger.exception("PO section lookup for the cart failed")
        sections = {}

    # Additive (X1, opt-in with ?projection=1): each line's projection context
    # - weekly sales, on hand, the suggested qty for `horizon_days` and why
    # (services/order_suggestion.py). Old app builds never ask for it.
    contexts: dict = {}
    if projection:
        try:
            contexts = cart_projection_context(db, rows, lookback_days, horizon_days, restaurant_today())
        except Exception:  # noqa: BLE001
            logger.exception("Cart projection context failed")
            contexts = {}

    by_supplier: dict[str, list] = {}
    for row in rows:
        snap = latest_inventory.get(row.item_id) if row.item_id else None
        unit_cost = snap.cost if snap is not None else None
        out = _cart_item_out(row, unit_cost, stock.get(row.id), sections.get(row.id))
        if projection:
            out.projection = contexts.get(row.id)
        by_supplier.setdefault(row.supplier, []).append(out)

    suppliers = []
    grand_estimate_total = 0.0
    grand_has_estimate = False
    for supplier, items in sorted(by_supplier.items()):
        known_costs = [i.line_cost for i in items if i.line_cost is not None]
        estimate_total = round(sum(known_costs), 2) if known_costs else None
        if estimate_total is not None:
            grand_estimate_total += estimate_total
            grand_has_estimate = True
        suppliers.append(
            {
                "supplier": supplier,
                "items": items,
                "estimate_total": estimate_total,
                "items_missing_cost": sum(1 for i in items if i.unit_cost is None),
            }
        )

    return {
        "suppliers": suppliers,
        "total_items": len(rows),
        "grand_estimate_total": round(grand_estimate_total, 2) if grand_has_estimate else None,
        # Additive (Sep 30): may this user delete / clear lines (admins only)?
        "can_remove": can_remove(user),
        # Additive (X1): the window the lines' projection context is for
        # (only with ?projection=1).
        "projection_window": {"lookback_days": lookback_days, "horizon_days": horizon_days} if projection else None,
    }


@router.post("/cart/items", response_model=list[CartItemOut], dependencies=[_cart_access])
def post_cart_items(
    payload: CartAddRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items to add.")
    for item in payload.items:
        _require_sane_numbers(item.qty, item.case_of)
    if not can_remove(user) and any(item.qty <= 0 for item in payload.items):
        # Re-adding an item already in the cart replaces its qty, so qty 0
        # here would take the line out of the order.
        existing = {
            r.item_id for r in list_cart(db) if r.supplier == payload.supplier and r.item_id
        }
        if any(item.qty <= 0 and item.item_id in existing for item in payload.items):
            raise HTTPException(status_code=403, detail=REMOVAL_ADMIN_ONLY)
    rows = add_items(db, payload.supplier, [item.model_dump() for item in payload.items], user.id)
    return [_cart_item_out(row) for row in rows]


@router.patch("/cart/items/{cart_item_id}", response_model=CartItemOut, dependencies=[_cart_access])
def patch_cart_item(
    cart_item_id: int,
    payload: CartItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_sane_numbers(payload.qty, payload.case_of)
    if payload.qty is not None and payload.qty <= 0:
        # Qty 0 drops the line from the PO - a removal (admins only).
        _require_remover(user)
    row = update_item(db, cart_item_id, qty=payload.qty, case_of=payload.case_of)
    if row is None:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    return _cart_item_out(row)


@router.delete("/cart/items/{cart_item_id}", dependencies=[_cart_access])
def delete_cart_item(
    cart_item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _require_remover(user)
    if not delete_item(db, cart_item_id):
        raise HTTPException(status_code=404, detail="Cart item not found.")
    return {"deleted": True}


@router.delete("/cart", dependencies=[_cart_access])
def delete_cart_supplier(
    supplier: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_remover(user)
    count = clear_supplier(db, supplier)
    return {"deleted": count}


# --- Removal requests (Sep 30): staff ask, an admin approves / rejects. ----


def _cart_row(db: Session, cart_item_id: int) -> CartItem:
    row = db.get(CartItem, cart_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    return row


@router.post(
    "/cart/items/{cart_item_id}/removal-request", response_model=CartItemOut, dependencies=[_cart_access]
)
def post_removal_request(
    cart_item_id: int,
    payload: Optional[CartRemovalRequest] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = request_removal(db, cart_item_id, user, payload.reason if payload else None)
    if row is None:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    return _cart_item_out(row)


@router.delete(
    "/cart/items/{cart_item_id}/removal-request", response_model=CartItemOut, dependencies=[_cart_access]
)
def delete_removal_request(
    cart_item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    row = _cart_row(db, cart_item_id)
    if row.removal_requested_at is not None and not (
        user.is_admin or row.removal_requested_by_user_id == user.id
    ):
        raise HTTPException(status_code=403, detail="Only the person who asked, or an admin, can cancel it.")
    return _cart_item_out(clear_removal_request(db, row))


def _pending(db: Session, cart_item_id: int) -> CartItem:
    row = _cart_row(db, cart_item_id)
    if row.removal_requested_at is None:
        raise HTTPException(status_code=409, detail="There is no removal request on this line.")
    return row


@router.post("/cart/items/{cart_item_id}/removal-request/approve", dependencies=[Depends(require_admin)])
def approve_removal_request(cart_item_id: int, db: Session = Depends(get_db)):
    row = _pending(db, cart_item_id)
    delete_item(db, row.id)
    return {"deleted": True}


@router.post(
    "/cart/items/{cart_item_id}/removal-request/reject",
    response_model=CartItemOut,
    dependencies=[Depends(require_admin)],
)
def reject_removal_request(cart_item_id: int, db: Session = Depends(get_db)):
    return _cart_item_out(clear_removal_request(db, _pending(db, cart_item_id)))


# "Order placed" - the real order-placed moment, however it happened (phone
# call, WhatsApp, in person, or after sharing the PDF): snapshots what's in
# that supplier's cart into PlacedOrder history, then clears the cart, as one
# explicit action. A PDF generated for this supplier in the last 48h becomes
# this order (one row, not two), and a repeat within ~2 minutes returns the
# order already recorded. "Clear supplier cart" (above) stays as-is for
# genuinely discarding items with no order-placed record at all.
@router.post("/cart/mark-placed", dependencies=[_cart_access])
def post_mark_order_placed(
    supplier: str = Query(..., min_length=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row, duplicate = mark_supplier_placed(db, supplier, user)
    if row is None:
        raise HTTPException(status_code=400, detail=f"No items in the cart for '{supplier}'.")
    from app.team.source_sync import task_sources_changed

    task_sources_changed("order")  # its Team order task closes now (Oct 15)
    # `placed`/`item_count` are what installed apps read; the rest is additive.
    return {"placed": True, "item_count": row.item_count, "order_id": row.id, "duplicate": duplicate}
