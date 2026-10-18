"""Bulk CSV/XLSX import for the manual purchase log (see purchase_log.py) -
lets a whole vendor invoice/delivery be logged in one upload instead of one
row at a time through the form, matching columns by header name the same way
inventory_parser.py does.

Items are matched against the latest inventory snapshot: by Toast item id if
an "item id" column is present and matches a real item, otherwise by exact
(case-insensitive) item name. A row that can't be matched to a real item, or
that trips a validation problem, is skipped and reported back - nothing
partial gets written for that row.
"""

import datetime as dt
import io
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.services.purchase_log import create_purchase_entry
from app.services.reorder import latest_inventory_by_item

COLUMN_MAP = {
    "item id": "item_id",
    "toast item id": "item_id",
    "item name": "item_name",
    "item": "item_name",
    "name": "item_name",
    "supplier": "supplier",
    "vendor": "supplier",
    "quantity received": "quantity",
    "quantity": "quantity",
    "qty received": "quantity",
    "qty": "quantity",
    "unit cost": "unit_cost",
    "cost": "unit_cost",
    "unit price": "unit_cost",
    "price": "unit_cost",
    "received date": "received_date",
    "date": "received_date",
    "notes": "notes",
    "note": "notes",
    "po #": "notes",
    "invoice #": "notes",
    "po/invoice #": "notes",
}


class PurchaseLogImportError(ValueError):
    pass


def _normalize_header(col: str) -> str:
    return " ".join(str(col).strip().lower().split())


# Old-format Excel (.xls) needs the xlrd package, which isn't installed (and
# isn't worth adding for a format Toast doesn't export): say what to do instead
# of failing with pandas' "Missing optional dependency 'xlrd'".
XLS_NOT_SUPPORTED = "This is an old Excel file (.xls). Save the file as .xlsx or .csv and upload again."


def _load_dataframe(filename: str, content: bytes) -> pd.DataFrame:
    lower = filename.lower()
    if lower.endswith(".xls"):
        raise PurchaseLogImportError(XLS_NOT_SUPPORTED)
    try:
        if lower.endswith(".csv"):
            return pd.read_csv(io.BytesIO(content), dtype=str)
        if lower.endswith(".xlsx"):
            return pd.read_excel(io.BytesIO(content), dtype=str)
    except Exception as exc:  # noqa: BLE001
        raise PurchaseLogImportError(f"Could not read {filename} as a table: {exc}") from exc
    raise PurchaseLogImportError(
        f"Unsupported file type for {filename!r}: expected .csv or .xlsx"
    )


def import_purchase_log(
    db: Session,
    filename: str,
    content: bytes,
    default_received_date: Optional[dt.date],
    logged_by_user_id: Optional[int],
) -> dict:
    df = _load_dataframe(filename, content)
    if df.empty:
        raise PurchaseLogImportError("File has a header row but no data rows.")

    df.columns = [_normalize_header(c) for c in df.columns]
    found_columns = set(df.columns)
    usable_columns = {src: dest for src, dest in COLUMN_MAP.items() if src in found_columns}
    mapped_fields = set(usable_columns.values())

    if "quantity" not in mapped_fields:
        raise PurchaseLogImportError(
            "Missing a quantity column (expected one of: quantity received, quantity, qty received, qty)."
        )
    if "item_id" not in mapped_fields and "item_name" not in mapped_fields:
        raise PurchaseLogImportError(
            "Missing an item column (expected one of: item id, item name, item, name)."
        )
    if "received_date" not in mapped_fields and default_received_date is None:
        raise PurchaseLogImportError(
            "File has no received date column, and no default date was given - pick a date for "
            "rows that don't specify their own."
        )

    by_id = latest_inventory_by_item(db)
    by_name = {snap.name.strip().lower(): (item_id, snap.name) for item_id, snap in by_id.items() if snap.name}

    rows_loaded = 0
    errors: list[str] = []

    for pos, (_, row) in enumerate(df.iterrows()):
        line_no = pos + 2  # +1 for header row, +1 for 1-indexing

        values: dict = {}
        for src_col, field in usable_columns.items():
            raw = row.get(src_col)
            values[field] = None if pd.isna(raw) else str(raw).strip()

        raw_item_id = values.get("item_id")
        raw_item_name = values.get("item_name")

        item_id = None
        item_name = None
        if raw_item_id and raw_item_id in by_id:
            item_id = raw_item_id
            item_name = raw_item_name or by_id[raw_item_id].name
        elif raw_item_name and raw_item_name.strip().lower() in by_name:
            item_id, item_name = by_name[raw_item_name.strip().lower()]
        else:
            label = raw_item_name or raw_item_id or "(blank)"
            errors.append(f"Row {line_no}: no matching item found for \"{label}\".")
            continue

        raw_qty = values.get("quantity")
        if not raw_qty:
            errors.append(f"Row {line_no} ({item_name}): missing quantity.")
            continue
        try:
            quantity = float(raw_qty.replace(",", ""))
        except ValueError:
            errors.append(f"Row {line_no} ({item_name}): quantity \"{raw_qty}\" isn't a number.")
            continue

        raw_cost = values.get("unit_cost")
        unit_cost = None
        if raw_cost:
            try:
                unit_cost = float(raw_cost.replace("$", "").replace(",", ""))
            except ValueError:
                errors.append(f"Row {line_no} ({item_name}): unit cost \"{raw_cost}\" isn't a number.")
                continue

        raw_date = values.get("received_date")
        if raw_date:
            try:
                received_date = pd.to_datetime(raw_date).date()
            except (ValueError, TypeError):
                errors.append(f"Row {line_no} ({item_name}): received date \"{raw_date}\" isn't a valid date.")
                continue
        elif default_received_date is not None:
            received_date = default_received_date
        else:
            errors.append(f"Row {line_no} ({item_name}): missing received date.")
            continue

        create_purchase_entry(
            db,
            item_id=item_id,
            item_name=item_name,
            supplier=values.get("supplier"),
            quantity_received=quantity,
            unit_cost=unit_cost,
            received_date=received_date,
            notes=values.get("notes"),
            logged_by_user_id=logged_by_user_id,
        )
        rows_loaded += 1

    return {
        "rows_loaded": rows_loaded,
        "rows_skipped": len(errors),
        "errors": errors[:20],
        "total_errors": len(errors),
    }
