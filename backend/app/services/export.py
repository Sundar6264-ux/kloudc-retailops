"""Exports captured `orders` line items for a date range as CSV or PDF.

No forecasting/aggregation here on purpose (per spec, step 4 is a clean export
of what's been captured; analysis comes later in step 5).

One row per sold item (top-level selection). Modifier/add-on rows are left
out: Toast already includes a modifier's price in its parent item's price, so
exporting both made the Net Price column add up to more than was sold (see
items_sold.py). Voided rows stay in, marked in the Voided column.
"""

import datetime as dt
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.csv_util import csv_cell

EXPORT_COLUMNS = [
    ("business_date", "Business Date"),
    ("opened_at", "Opened At"),
    ("toast_order_guid", "Order GUID"),
    ("toast_check_guid", "Check GUID"),
    ("item_name", "Item"),
    ("quantity", "Qty"),
    ("unit_price", "Unit Price"),
    ("net_price", "Net Price"),
    ("tax_amount", "Tax"),
    ("voided", "Voided"),
    ("dining_option", "Dining Option"),
]


def _fetch_rows(db: Session, start: dt.date, end: dt.date) -> list[Order]:
    stmt = (
        select(Order)
        .where(
            Order.business_date >= start,
            Order.business_date <= end,
            Order.top_level(),
        )
        .order_by(Order.business_date, Order.opened_at)
    )
    return list(db.execute(stmt).scalars())


def export_csv(db: Session, start: dt.date, end: dt.date) -> str:
    rows = _fetch_rows(db, start, end)

    lines = [",".join(h for _, h in EXPORT_COLUMNS)]
    for row in rows:
        values = []
        for field, _ in EXPORT_COLUMNS:
            values.append(csv_cell(getattr(row, field)))
        lines.append(",".join(values))
    return "\n".join(lines) + "\n"


def export_pdf(db: Session, start: dt.date, end: dt.date) -> bytes:
    rows = _fetch_rows(db, start, end)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter))

    header = [h for _, h in EXPORT_COLUMNS]
    table_data = [header]
    for row in rows:
        table_data.append(
            [str(getattr(row, field) if getattr(row, field) is not None else "") for field, _ in EXPORT_COLUMNS]
        )

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f4")]),
            ]
        )
    )
    doc.build([table])
    return buf.getvalue()
