"""Parses a manually-uploaded Toast Retail "retail items" export (CSV or XLSX)
into `inventory_snapshots` rows, matching columns by header name (not position)
so the parser survives column reordering or Toast tweaking the export.

Only the subset of columns the app actually uses is kept; everything else in
the 74-column export (barcode config, tare weight, image url, prep stations,
par min/max, etc.) is ignored on purpose.
"""

import datetime as dt
import io

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.dbcompat import dialect_insert
from app.models import InventorySnapshot
from app.services.item_vendors import refresh_after_csv_upload

# The Toast Retail export's column map and file reading moved to the Toast
# adapter (multi-store M1, batch MT2): app/pos/adapters/toast/csv_import.py.
# These names still work here, and patching one here patches the adapter
# (app/pos/compat.py); parse_and_store below is unchanged.
from app.pos.adapters.toast import csv_import as _toast_csv  # noqa: E402
from app.pos.adapters.toast.csv_import import (  # noqa: E402
    COLUMN_MAP,
    NUMERIC_FIELDS,
    REQUIRED_SOURCE_COLUMNS,
    XLS_NOT_SUPPORTED,
    InventoryParseError,
    _load_dataframe,
    _normalize_header,
)
from app.pos.compat import forwards_for, install_forwarding  # noqa: E402

install_forwarding(
    __name__,
    forwards_for(
        _toast_csv,
        (
            "COLUMN_MAP",
            "REQUIRED_SOURCE_COLUMNS",
            "NUMERIC_FIELDS",
            "InventoryParseError",
            "_normalize_header",
            "XLS_NOT_SUPPORTED",
            "_load_dataframe",
        ),
    ),
)


def parse_and_store(
    db: Session, filename: str, content: bytes, snapshot_date: dt.date
) -> dict:
    df = _load_dataframe(filename, content)
    if df.empty:
        raise InventoryParseError("File has a header row but no data rows.")

    df.columns = [_normalize_header(c) for c in df.columns]
    found_columns = set(df.columns)

    missing_required = REQUIRED_SOURCE_COLUMNS - found_columns
    if missing_required:
        raise InventoryParseError(
            f"Missing required column(s): {sorted(missing_required)}. "
            f"Found columns: {sorted(found_columns)}"
        )

    usable_columns = {src: dest for src, dest in COLUMN_MAP.items() if src in found_columns}

    # A CSV always wins over an API-built snapshot of the same date
    # (services/inventory_api.py): drop that date's API rows first, so no
    # API-only row or flag survives next to the CSV's.
    api_rows_replaced = db.execute(
        delete(InventorySnapshot).where(
            InventorySnapshot.snapshot_date == snapshot_date,
            InventorySnapshot.source_filename.like("api:%"),
        )
    ).rowcount or 0

    rows_loaded = 0
    skipped = 0
    loaded_ids = []
    for _, row in df.iterrows():
        item_id = row.get("item id")
        if pd.isna(item_id) or not str(item_id).strip():
            skipped += 1
            continue

        values: dict = {"item_id": str(item_id).strip()}
        for src_col, field in usable_columns.items():
            if field == "item_id":
                continue
            raw = row.get(src_col)
            if pd.isna(raw):
                values[field] = None
                continue
            if field in NUMERIC_FIELDS:
                cleaned = str(raw).replace("$", "").replace(",", "").replace("%", "").strip()
                try:
                    values[field] = float(cleaned) if cleaned else None
                except ValueError:
                    values[field] = None
            else:
                values[field] = str(raw).strip()

        values["snapshot_date"] = snapshot_date
        values["source_filename"] = filename

        stmt = dialect_insert(InventorySnapshot, db).values(**values)
        update_cols = {k: getattr(stmt.excluded, k) for k in values if k not in ("snapshot_date", "item_id")}
        stmt = stmt.on_conflict_do_update(
            index_elements=[InventorySnapshot.snapshot_date, InventorySnapshot.item_id],
            set_=update_cols,
        )
        db.execute(stmt)
        rows_loaded += 1
        loaded_ids.append(values["item_id"])

    # Multi-store M1 (MT5): the item_vendors table follows the CSV's
    # vendors. Never fails the upload (services/item_vendors.py).
    refresh_after_csv_upload(db, loaded_ids)

    db.commit()

    ignored_columns = sorted(found_columns - set(COLUMN_MAP.keys()))
    return {
        "snapshot_date": snapshot_date.isoformat(),
        "rows_loaded": rows_loaded,
        "rows_skipped": skipped,
        "columns_used": sorted(usable_columns.keys()),
        "columns_ignored": ignored_columns,
        # Additive: rows of an API-built snapshot for this date that the CSV replaced.
        "api_rows_replaced": api_rows_replaced,
    }
