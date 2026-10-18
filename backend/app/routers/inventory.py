import datetime as dt
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.features import require_feature
from app.pos.capabilities import Capability
from app.pos.gating import require_capability
from app.models import JobRun, PurchaseLogEntry
from app.schemas import InventoryUploadResult
from app.services.csv_util import rows_to_csv
from app.services.fix_issues import warm_fix_issues_cache
from app.services.inventory_api import rerun_after_upload
from app.services.inventory_parser import InventoryParseError, parse_and_store
from app.services.invoice_submission import get_vendors_submitted_today
from app.services.price_history import get_price_changed_items, search_price_history
from app.services.reorder import latest_inventory_by_item
from app.services.po_cart import in_stock_for
from app.services.product_match import rank, snapshot_code_match
from app.services.supplier_projection import (
    _container_qty_by_name,
    _split_suppliers,
    _supplier_item_id_for,
    store_container_pairs,
)
from app.timeutil import restaurant_today

router = APIRouter(prefix="/api/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)])

JOB_NAME = "inventory_upload"


# Deliberately not feature-gated beyond plain login: this lightweight name
# search is a shared helper used by several different pages/features
# (Reconciliation's item picker, PO Cart's "add an item" search, Delivery/
# Transfer Review) - gating it would mean an OR of every feature that
# happens to use it today, and breaking silently the next time a new page
# starts using it too. It only returns id/name/category/supplier - the same
# category of info already visible on several already-gated pages.
@router.get("/items/search")
def search_items(
    q: str = Query(..., min_length=1),
    supplier: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Lightweight name / code search over the latest inventory snapshot, for
    autocomplete (e.g. picking a real item when logging a purchase, or
    finding an existing item to add to a supplier's PO cart) - not a full
    report, just id/name/category/supplier plus a few read-only display
    fields (barcode/price/cost/on-hand qty, straight off the same snapshot
    row - no live Toast call), capped at 25 matches. When `supplier` is
    given, only items actually carried by that vendor are returned (see
    supplier_projection.py's `_split_suppliers` for why a plain `==` check
    doesn't work - one item can list several suppliers), and each match's
    own supplier_item_id (that vendor's SKU) is resolved.

    Additive (Oct 7, for the cart's multi-item add): `in_stock_qty` is the
    cart line's "(in stock: N)" figure - on hand plus the item's Container
    row (`on_hand_qty` stays the shelf row alone, as before); `container_qty`
    is that Container row's count; `container_of` is set on a bare Container
    row that duplicates a priced item (its item_id), so the "search all
    products" list can leave it out - its count is already in that item's
    `in_stock_qty`.
    """
    needle = q.strip().lower()
    supplier_filter = supplier.strip() if supplier else None
    latest = latest_inventory_by_item(db)
    containers = _container_qty_by_name(latest)
    container_of = {container_id: store_id for store_id, _snap, container_id, _qty in store_container_pairs(latest)}
    matches = []
    for item_id, snap in latest.items():
        # Name as before, or (Oct 14, W2) a code: the Toast item id, any
        # vendor's SKU or a barcode - services/product_match.py.
        name_hit = bool(snap.name) and needle in snap.name.lower()
        code_hit = snapshot_code_match(q, item_id, snap)
        if not snap.name or not (name_hit or code_hit):
            continue
        if supplier_filter and supplier_filter not in _split_suppliers(snap.supplier):
            continue
        in_stock, container_qty = in_stock_for(item_id, snap, containers)
        matches.append(
            {
                "_rank": rank(name_hit, code_hit),
                "item_id": item_id,
                "name": snap.name,
                "category": snap.category,
                "supplier": snap.supplier,
                "supplier_item_id": _supplier_item_id_for(snap, supplier_filter) if supplier_filter else None,
                "barcode": snap.barcode,
                "price": snap.price,
                "cost": snap.cost,
                "on_hand_qty": snap.inventory_quantity,
                "in_stock_qty": in_stock,
                "container_qty": container_qty,
                "container_of": container_of.get(item_id),
                # Additive (W2): what the search matched on - "name",
                # "item_id", "supplier_item_id" or "barcode" - and the code
                # itself, so a list can say "SKU 1604" next to a code match.
                "matched_on": "name" if name_hit and (code_hit is None or not code_hit[2]) else code_hit[0],
                "matched_code": None if name_hit and (code_hit is None or not code_hit[2]) else code_hit[1],
                "all_supplier_item_ids": snap.supplier_item_id,
            }
        )
    matches.sort(key=lambda m: (m["_rank"], m["name"]))
    for m in matches:
        m.pop("_rank", None)
    return {"items": matches[:25]}


# Backs the mobile barcode scanner's informational "Item info" panel
# (vendor/cost/on-hand/last received) - deliberately separate from the print
# flow, which still talks to the labels site independently and is untouched
# by this. Same ungated-beyond-login reasoning as items/search above: a
# lookup helper reused by whatever page needs it, not a report of its own.
@router.get("/items/by-barcode")
def get_item_by_barcode(barcode: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    needle = barcode.strip()
    latest = latest_inventory_by_item(db)
    for item_id, snap in latest.items():
        # A snapshot's barcode field can list several values (e.g. different
        # pack sizes) semicolon-separated - splitting first is required, see
        # gotcha #6 (an early barcode report got this wrong and flagged ~220
        # false positives before it was fixed).
        codes = [c.strip() for c in (snap.barcode or "").split(";") if c.strip()]
        if needle in codes:
            last_received = db.execute(
                select(func.max(PurchaseLogEntry.received_date)).where(PurchaseLogEntry.item_id == item_id)
            ).scalar_one_or_none()
            return {
                "item_id": item_id,
                "name": snap.name,
                "supplier": snap.supplier,
                "cost": snap.cost,
                "on_hand_qty": snap.inventory_quantity,
                "last_received_date": last_received.isoformat() if last_received else None,
            }
    raise HTTPException(status_code=404, detail="No inventory item found for this barcode")


# Gated as "overview", not "invoice_submission" - this backs a pre-fill nudge on the Overview
# page's inventory upload ("We received a delivery today"), so whoever uploads inventory should
# see it regardless of whether they personally have Invoice Submission access.
@router.get("/invoice-vendors-today", dependencies=[Depends(require_feature("overview"))])
def invoice_vendors_today(db: Session = Depends(get_db)):
    return {"vendors": get_vendors_submitted_today(db)}


@router.get("/price-changes", dependencies=[Depends(require_feature("inventory_reports"))])
def price_changes(db: Session = Depends(get_db)):
    items = get_price_changed_items(db)
    return {"count": len(items), "items": items}


@router.get("/price-changes/export", dependencies=[Depends(require_feature("inventory_reports"))])
def price_changes_export(db: Session = Depends(get_db)):
    items = get_price_changed_items(db)
    return Response(
        content=rows_to_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="price-change-log.csv"'},
    )


@router.get("/price-history", dependencies=[Depends(require_feature("inventory_reports"))])
def price_history(search: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    return {"items": search_price_history(db, search)}


# InventoryUpload lives on the Overview page, so this is gated as "overview".
@router.post(
    "/upload",
    response_model=InventoryUploadResult,
    # Multi-store MT6: the upload reads the POS's own inventory export.
    dependencies=[Depends(require_feature("overview")), Depends(require_capability(Capability.CSV_CATALOG_IMPORT))],
)
async def upload_inventory(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    snapshot_date: Optional[dt.date] = None,
    db: Session = Depends(get_db),
):
    # The store's day (America/New_York), not the server's clock.
    target_date = snapshot_date or restaurant_today()

    job = JobRun(job_name=JOB_NAME, status="running", business_date=target_date)
    db.add(job)
    db.commit()

    try:
        content = await file.read()
        result = parse_and_store(db, file.filename or "upload", content, target_date)

        job.status = "success"
        # The CSV replaced an API-built snapshot of the same date (said
        # before the ignored-columns marker, which JobDetail splits on).
        replaced = (
            f"Replaced the Toast API snapshot for this date ({result['api_rows_replaced']} rows). "
            if result.get("api_rows_replaced")
            else ""
        )
        job.detail = (
            f"{result['rows_loaded']} items loaded for {target_date.isoformat()}. {replaced}"
            f"Columns ignored (not in COLUMN_MAP): {', '.join(result['columns_ignored']) or 'none'}"
        )
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.add(job)
        db.commit()

        # Fix Issues' Slow / Dead lists need a ~3s forecast per upload: work
        # it out after the response is sent, so the page opens fast. Never
        # blocks or fails the upload (errors are only logged).
        background_tasks.add_task(warm_fix_issues_cache, db.get_bind())
        # Today's file after the 04:05 Toast run: compare the two again so
        # the night counts toward Inventory from Toast readiness (once per
        # date, only early in the day - services/inventory_api.py).
        background_tasks.add_task(rerun_after_upload, db.get_bind(), target_date)
        return result
    except InventoryParseError as exc:
        job.status = "failed"
        job.detail = str(exc)
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.add(job)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.detail = f"Unexpected error: {exc}"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.add(job)
        db.commit()
        raise HTTPException(status_code=500, detail="Unexpected error while processing upload") from exc
