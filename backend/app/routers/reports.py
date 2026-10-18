from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.features import require_feature
from app.db import get_db
from app.models import User
from app.sales_access import TOAST_SALES_DOLLAR_FIELDS, strip_sales_deep
from app.services.barcode_report import get_invalid_barcodes, get_missing_barcodes
from app.services.csv_util import rows_to_csv
from app.services.duplicate_products import get_duplicate_products
from app.services.inventory_intelligence import get_dead_stock, get_negative_inventory
from app.services.store_vs_container import export_csv as store_vs_container_csv
from app.services.store_vs_container import get_store_vs_container
from app.services.vendor_cost import get_vendor_price_comparison

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_feature("inventory_reports"))])

# Internal fields left out of every CSV download (ids and the upload date
# mean nothing to staff), and readable headers for the rest.
_HIDDEN = {"item_id", "issue_key", "inventory_snapshot_date"}
_HEADERS = {
    "name": "Product",
    "category": "Category",
    "subcategory": "Subcategory",
    "supplier": "Vendor",
    "barcode": "Barcode",
    "reason": "Problem",
    "price": "Price",
    "cost": "Cost",
    "on_hand_qty": "On Hand",
    "days_on_hand": "Days On Hand",
    "last_90_day_sales": "Sales $ (90 days)",
    "inventory_value": "Stock Value",
    "status": "Status",
    "current_vendor": "Current Vendor",
    "current_cost": "Current Cost",
    "current_date": "Current Cost Date",
    "cheaper_vendor": "Cheaper Vendor",
    "cheaper_cost": "Cheaper Cost",
    "cheaper_date": "Cheaper Cost Date",
    "potential_savings": "Potential Savings",
}


def _export_csv(items: list[dict]) -> str:
    return rows_to_csv(
        [{_HEADERS.get(k, k.replace("_", " ").title()): v for k, v in i.items() if k not in _HIDDEN} for i in items]
    )


@router.get("/missing-barcodes")
def missing_barcodes(db: Session = Depends(get_db)):
    items = get_missing_barcodes(db)
    return {"count": len(items), "items": items}


@router.get("/missing-barcodes/export")
def missing_barcodes_export(db: Session = Depends(get_db)):
    items = get_missing_barcodes(db)
    return Response(
        content=_export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="missing-barcodes.csv"'},
    )


@router.get("/invalid-barcodes")
def invalid_barcodes(db: Session = Depends(get_db)):
    items = get_invalid_barcodes(db)
    return {"count": len(items), "items": items}


@router.get("/invalid-barcodes/export")
def invalid_barcodes_export(db: Session = Depends(get_db)):
    items = get_invalid_barcodes(db)
    return Response(
        content=_export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="invalid-barcodes.csv"'},
    )


# last_90_day_sales is Toast's dollar figure: blank for a "Hide sales
# numbers" user (app/sales_access.py).
@router.get("/dead-stock")
def dead_stock(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items = strip_sales_deep(get_dead_stock(db), TOAST_SALES_DOLLAR_FIELDS, user)
    return {"count": len(items), "items": items}


@router.get("/dead-stock/export")
def dead_stock_export(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items = strip_sales_deep(get_dead_stock(db), TOAST_SALES_DOLLAR_FIELDS, user)
    return Response(
        content=_export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="dead-stock.csv"'},
    )


# The margin report was removed (owner's call, ops fixes B). App Store
# builds up to 4 still open it from More > Reports: answer with a message
# the app shows as-is (it displays `detail`) instead of a bare "Not Found".
MARGIN_REMOVED_DETAIL = "The margin report has been removed. Update the Spice Town app to the latest version."


@router.get("/margin", include_in_schema=False)
@router.get("/margin/export", include_in_schema=False)
def margin_report_removed():
    raise HTTPException(status_code=410, detail=MARGIN_REMOVED_DETAIL)


@router.get("/negative-inventory")
def negative_inventory(db: Session = Depends(get_db)):
    items = get_negative_inventory(db)
    return {"count": len(items), "items": items}


@router.get("/negative-inventory/export")
def negative_inventory_export(db: Session = Depends(get_db)):
    items = get_negative_inventory(db)
    return Response(
        content=_export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="negative-inventory.csv"'},
    )


@router.get("/duplicate-products")
def duplicate_products(db: Session = Depends(get_db)):
    items = get_duplicate_products(db)
    return {"count": len(items), "items": items}


@router.get("/duplicate-products/export")
def duplicate_products_export(db: Session = Depends(get_db)):
    items = get_duplicate_products(db)
    return Response(
        content=_export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="duplicate-products.csv"'},
    )


@router.get("/vendor-price-comparison")
def vendor_price_comparison(db: Session = Depends(get_db)):
    items = get_vendor_price_comparison(db)
    return {"count": len(items), "items": items}


@router.get("/vendor-price-comparison/export")
def vendor_price_comparison_export(db: Session = Depends(get_db)):
    items = get_vendor_price_comparison(db)
    return Response(
        content=_export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vendor-price-comparison.csv"'},
    )


# Store vs Container: items with both a store (priced) row and a Container
# row in the latest upload, with each one's stock (services/store_vs_container.py).
@router.get("/store-vs-container")
def store_vs_container(search: Optional[str] = None, vendor: Optional[str] = None, db: Session = Depends(get_db)):
    return get_store_vs_container(db, search=search, vendor=vendor)


@router.get("/store-vs-container/export")
def store_vs_container_export(search: Optional[str] = None, vendor: Optional[str] = None, db: Session = Depends(get_db)):
    items = get_store_vs_container(db, search=search, vendor=vendor)["items"]
    return Response(
        content=store_vs_container_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="store-vs-container.csv"'},
    )
