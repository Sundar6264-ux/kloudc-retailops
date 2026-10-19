"""The KloudC RetailOps catalog CSV template (CSV-only stores, MT13).

The canonical headers are exactly the column names the Inventory upload page
already understands (``services/inventory_parser.py`` / the Toast Retail
export map), so a file made from this template uploads unchanged on any
store. ``ALIASES`` adds the header names other POS / spreadsheet exports
commonly use ("SKU", "UPC", "Qty on hand", "Vendor" ...); the connector's
``parse_catalog_export`` and ``canonical_csv`` accept them too.

Headers are compared lower-cased with runs of whitespace collapsed, the same
rule as the upload page.
"""

import csv
import io
from typing import Dict, List, Optional, Sequence

# canonical header (as written in the template) -> inventory_snapshots field
TEMPLATE_COLUMNS: Sequence = (
    ("Item ID", "item_id"),
    ("Name", "name"),
    ("Category Group", "category_group"),
    ("Category", "category"),
    ("Subcategory", "subcategory"),
    ("Inventory Status", "inventory_status"),
    ("Inventory Quantity", "inventory_quantity"),
    ("Cost", "cost"),
    ("Price", "price"),
    ("Supplier", "supplier"),
    ("Supplier Item ID", "supplier_item_id"),
    ("Barcode", "barcode"),
    ("Last 7 Day Sales", "last_7_day_sales"),
    ("Last 30 Day Sales", "last_30_day_sales"),
    ("Last 90 Day Sales", "last_90_day_sales"),
)

TEMPLATE_HEADERS: List[str] = [h for h, _ in TEMPLATE_COLUMNS]
REQUIRED_HEADER = "item id"

NUMERIC_FIELDS = frozenset(
    {
        "inventory_quantity",
        "cost",
        "price",
        "last_7_day_sales",
        "last_30_day_sales",
        "last_90_day_sales",
    }
)

# other header spellings (normalized) -> canonical header (normalized)
ALIASES: Dict[str, str] = {
    "sku": "item id",
    "item #": "item id",
    "item number": "item id",
    "product id": "item id",
    "item name": "name",
    "product name": "name",
    "description": "name",
    "department": "category group",
    "group": "category group",
    "sub category": "subcategory",
    "sub-category": "subcategory",
    "status": "inventory status",
    "qty": "inventory quantity",
    "quantity": "inventory quantity",
    "qty on hand": "inventory quantity",
    "on hand": "inventory quantity",
    "stock": "inventory quantity",
    "unit cost": "cost",
    "retail price": "price",
    "unit price": "price",
    "vendor": "supplier",
    "vendor name": "supplier",
    "vendor sku": "supplier item id",
    "vendor item #": "supplier item id",
    "supplier sku": "supplier item id",
    "upc": "barcode",
    "ean": "barcode",
    "plu": "barcode",
}

_FIELD_BY_HEADER: Dict[str, str] = {h.lower(): f for h, f in TEMPLATE_COLUMNS}


def normalize_header(raw: object) -> str:
    return " ".join(str(raw).strip().lower().split())


def canonical_header(raw: object) -> Optional[str]:
    """The canonical (normalized) header for ``raw``, or None if unknown."""
    h = normalize_header(raw)
    if h in _FIELD_BY_HEADER:
        return h
    return ALIASES.get(h)


def field_for_header(raw: object) -> Optional[str]:
    h = canonical_header(raw)
    return _FIELD_BY_HEADER.get(h) if h else None


def template_csv(rows: Sequence[Dict[str, object]] = ()) -> str:
    """The template as CSV text: the header row, then ``rows`` (dicts keyed by
    canonical header or field name). Used for the download and by the DEMO
    data generator."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(TEMPLATE_HEADERS)
    for row in rows:
        values = []
        for header, field in TEMPLATE_COLUMNS:
            value = row.get(header, row.get(field))
            values.append("" if value is None else value)
        writer.writerow(values)
    return out.getvalue()
