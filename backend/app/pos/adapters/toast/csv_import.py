"""Toast Retail "retail items" export (CSV or XLSX): which columns we keep
and how the file is read.

Moved unchanged from ``services/inventory_parser.py`` in multi-store M1 batch
MT2 (every name still works as ``inventory_parser.<name>``). Columns are
matched by header name (not position), so the parser survives column
reordering or Toast tweaking the export. Only the subset of columns the app
actually uses is kept; everything else in the 74-column export (barcode
config, tare weight, image url, prep stations, par min/max, etc.) is ignored
on purpose.

Writing the rows into ``inventory_snapshots`` (``parse_and_store``, "CSV wins
over an API snapshot of the same date") stays in ``services/inventory_parser.py``.
"""

import io

import pandas as pd


# normalized (lowercase, single-spaced) source header -> model field name
COLUMN_MAP = {
    "item id": "item_id",
    "name": "name",
    "category group": "category_group",
    "category": "category",
    "subcategory": "subcategory",
    "inventory status": "inventory_status",
    "inventory quantity": "inventory_quantity",
    "inventory cost": "inventory_cost",
    "inventory value": "inventory_value",
    "inventory days on hand": "inventory_days_on_hand",
    "cost": "cost",
    "price": "price",
    "gross margin": "gross_margin",
    "gross profit": "gross_profit",
    "last 7 day sales": "last_7_day_sales",
    "last 30 day sales": "last_30_day_sales",
    "last 90 day sales": "last_90_day_sales",
    "last 7 day orders": "last_7_day_orders",
    "last 30 day orders": "last_30_day_orders",
    "last 90 day orders": "last_90_day_orders",
    "supplier": "supplier",
    "last received from": "last_received_from",
    "inventory last received": "inventory_last_received",
    "barcode": "barcode",
    # The exact header Toast uses for this hasn't been confirmed against a real
    # export yet - every plausible variant is mapped here so whichever one
    # actually appears gets picked up automatically. If none of these match,
    # it'll show up in `columns_ignored` (also logged to job_runs.detail) on
    # the next upload - check there for the real header text and add it above.
    "supplier item id": "supplier_item_id",
    "supplier item #": "supplier_item_id",
    "supplier sku": "supplier_item_id",
    "vendor item id": "supplier_item_id",
    "vendor item #": "supplier_item_id",
    "vendor sku": "supplier_item_id",
    "vendor item number": "supplier_item_id",
    "supplier item number": "supplier_item_id",
}

# The receiving-unit columns give each item's case size (units per case). Not
# a 1:1 field copy - the two cells are read together by
# services/receiving_units.case_info into case_size / receiving_unit /
# receiving_units_raw. Header variants are all mapped, like supplier item id
# above; the first of each is the export's own header.
RECEIVING_UNITS_HEADERS = ("receiving units", "receiving unit", "receiving unit names")
RECEIVING_QTYS_HEADERS = (
    "receiving unit quantities",
    "receiving unit quantity",
    "receiving unit qty",
    "receiving unit qtys",
    "receiving quantities",
)

REQUIRED_SOURCE_COLUMNS = {"item id"}

NUMERIC_FIELDS = {
    "inventory_quantity",
    "inventory_cost",
    "inventory_value",
    "inventory_days_on_hand",
    "cost",
    "price",
    "gross_margin",
    "gross_profit",
    "last_7_day_sales",
    "last_30_day_sales",
    "last_90_day_sales",
    "last_7_day_orders",
    "last_30_day_orders",
    "last_90_day_orders",
}


class InventoryParseError(ValueError):
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
        raise InventoryParseError(XLS_NOT_SUPPORTED)
    try:
        if lower.endswith(".csv"):
            return pd.read_csv(io.BytesIO(content), dtype=str)
        if lower.endswith(".xlsx"):
            return pd.read_excel(io.BytesIO(content), dtype=str)
    except Exception as exc:  # noqa: BLE001
        raise InventoryParseError(f"Could not read {filename} as a table: {exc}") from exc
    raise InventoryParseError(
        f"Unsupported file type for {filename!r}: expected .csv or .xlsx"
    )
