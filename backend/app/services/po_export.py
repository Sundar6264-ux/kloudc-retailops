"""Generates a professional purchase-order PDF from the Purchase Order
Builder page (Supplier Projection -> check items -> enter quantities).

This is NOT a real Toast purchase order - Toast's Purchasing API isn't
reachable with the current credentials (skill gotcha #15), so there's no
system of record to draft a PO against. It's a plain document: serial
number, the vendor's own SKU (blank if Toast doesn't have one on file for
that item/vendor), item name, and the quantity the user actually entered.
"""

import datetime as dt
import io
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# The letterhead icon: DOCUMENT_LOGO_FILE (a local image) when set, else the
# bundled KloudC RetailOps icon; no icon when neither file exists. The store's name,
# address and phone are real text next to it (store settings), so their size
# can be tuned independently of the image.
_BUNDLED_ICON = Path(__file__).resolve().parent.parent / "assets" / "branding" / "logo-128.png"


def _icon_file() -> Optional[Path]:
    from app.config import get_settings

    raw = (getattr(get_settings(), "document_logo_file", "") or "").strip()
    path = Path(raw).expanduser() if raw else _BUNDLED_ICON
    return path if path.is_file() else None


def _letterhead():
    """(name, address, phone) for the PO header and footer; blank parts are
    left out."""
    from app.store import settings_view as sv

    return sv.store_display_name(), sv.store_address(), sv.store_phone()


# A warm, light palette (pulled from the logo's own orange/red/tan tones)
# instead of a dark header bar - this is a document meant to look inviting
# and on-brand, not like a spreadsheet printout.
_INK = colors.HexColor("#3f2a1d")  # warm dark brown - body text, not pure black
_ACCENT = colors.HexColor("#d9480f")  # burnt orange - title, accent rule
_HEADER_BG = colors.HexColor("#fbe4cc")  # light peach - table header row
_HEADER_TEXT = colors.HexColor("#7c3410")  # deep warm brown - readable on the peach
_ZEBRA = colors.HexColor("#fdf6ee")  # faint warm tint for alternating rows
_GRID = colors.HexColor("#eaddcc")  # warm light tan grid lines
_SECTION_BG = colors.HexColor("#f3dcc2")  # warm tan - section heading rows
_QTY_HIGHLIGHT = colors.HexColor("#fff6df")  # pale gold - the blank qty column

def _format_qty(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    return str(int(f)) if f.is_integer() else str(f)


def export_simple_po_pdf(
    supplier: str, items: list[dict], sections: Optional[list[tuple[str, list[dict]]]] = None
) -> bytes:
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "POHeader", parent=styles["Normal"], textColor=_HEADER_TEXT, fontName="Helvetica-Bold", fontSize=9
    )
    header_style_right = ParagraphStyle("POHeaderR", parent=header_style, alignment=TA_RIGHT)
    cell_style = ParagraphStyle("POCell", parent=styles["Normal"], textColor=_INK, fontSize=9, leading=12)
    cell_style_right = ParagraphStyle("POCellR", parent=cell_style, alignment=TA_RIGHT)
    section_style = ParagraphStyle(
        "POSection", parent=styles["Normal"], textColor=_HEADER_TEXT, fontName="Helvetica-Bold", fontSize=8.5,
        leading=10, alignment=TA_CENTER,  # owner, Oct 14: section names centred across the table
    )
    footer_style = ParagraphStyle(
        "POFooter", parent=styles["Normal"], textColor=colors.HexColor("#8a7966"), fontSize=8,
        fontName="Helvetica-Oblique", leading=11, alignment=TA_CENTER,
    )
    # Compact letterhead sizes, deliberately small - the whole header (icon +
    # business name + address/phone + title + supplier/date) needs to fit
    # well under 2 inches from the top of the page, not read like a poster.
    brand_name_style = ParagraphStyle(
        "POBrandName", parent=styles["Normal"], textColor=_ACCENT, fontName="Helvetica-Bold",
        fontSize=13, leading=15,
    )
    brand_detail_style = ParagraphStyle(
        "POBrandDetail", parent=styles["Normal"], textColor=_INK, fontSize=7.5, leading=10,
    )
    title_style_right = ParagraphStyle(
        "POTitleR", parent=styles["Normal"], textColor=_ACCENT, fontName="Helvetica-Bold",
        fontSize=13, leading=15, alignment=TA_RIGHT,
    )
    subtitle_style_right = ParagraphStyle(
        "POSubtitleR", parent=styles["Normal"], textColor=_INK, fontSize=8, leading=10, alignment=TA_RIGHT,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, topMargin=0.4 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )

    # Compact letterhead: small icon top-left (with its own margin, not flush
    # against the page edge), the business name/address/phone as real text
    # next to it (not baked into the logo image, so it can be sized to fit),
    # and the document title/supplier/date right-aligned on the far right -
    # all in one row so the entire header comfortably clears the 2-inch budget.
    store_name, store_address, store_phone = _letterhead()
    brand_block = [Paragraph(xml_escape(store_name), brand_name_style)]
    brand_block += [Paragraph(xml_escape(line), brand_detail_style) for line in (store_address, store_phone) if line]
    doc_block = [
        Paragraph("PURCHASE ORDER", title_style_right),
        Paragraph(f"Supplier: <b>{supplier}</b>", subtitle_style_right),
        Paragraph(f"Prepared: {dt.date.today().isoformat()}", subtitle_style_right),
    ]
    icon_path = _icon_file()
    if icon_path is not None:
        from reportlab.lib.utils import ImageReader

        img_w, img_h = ImageReader(str(icon_path)).getSize()
        icon_width = 0.5 * inch
        icon = Image(str(icon_path), width=icon_width, height=icon_width * (img_h / img_w if img_w else 1))
        header_table = Table(
            [[icon, brand_block, doc_block]],
            colWidths=[icon_width + 0.2 * inch, 2.2 * inch, None],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (0, 0), 0),
                    ("TOPPADDING", (0, 0), (0, 0), 0.05 * inch),
                    ("LEFTPADDING", (1, 0), (2, 0), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (1, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story = [header_table]
    else:
        story = brand_block + doc_block

    story += [
        Spacer(1, 0.1 * inch),
        HRFlowable(width="100%", thickness=1.4, color=_ACCENT, spaceAfter=0.16 * inch),
    ]

    def header_cell(text: str, numeric: bool) -> Paragraph:
        return Paragraph(text, header_style_right if numeric else header_style)

    def body_cell(value, numeric: bool) -> Paragraph:
        # Escaped: Paragraph parses markup, so a hand-typed name containing
        # "<" could otherwise be swallowed or break the whole export.
        text = "" if value is None else xml_escape(str(value))
        return Paragraph(text, cell_style_right if numeric else cell_style)

    header_row = [
        header_cell("S.No", True),
        header_cell("Supplier Code", False),
        header_cell("Item Name", False),
        header_cell("Qty", True),
    ]

    # `sections` (from po_sections.group_po_items) prints each section's
    # lines under a full-width heading row; without it the lines print in the
    # order given, exactly as before. S.No keeps counting across sections.
    groups = sections if sections else [(None, items)]
    table_data = [header_row]
    section_rows = []
    idx = 0
    for section, section_items in groups:
        if section:
            section_rows.append(len(table_data))
            table_data.append([Paragraph(xml_escape(section.upper()), section_style), "", "", ""])
        for item in section_items:
            idx += 1
            table_data.append(
                [
                    body_cell(idx, True),
                    body_cell(item.get("supplier_item_id") or "", False),
                    body_cell(item.get("name"), False),
                    body_cell(_format_qty(item.get("qty")), True),
                ]
            )

    page_width = letter[0] - 1.2 * inch
    serial_width = 0.55 * inch
    qty_width = 0.9 * inch
    supplier_code_width = 1.6 * inch
    name_width = page_width - serial_width - qty_width - supplier_code_width
    col_widths = [serial_width, supplier_code_width, name_width, qty_width]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, _ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ZEBRA]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("BACKGROUND", (-1, 1), (-1, -1), _QTY_HIGHLIGHT),
            ]
            + [
                cmd
                for row in section_rows
                for cmd in (
                    ("SPAN", (0, row), (-1, row)),
                    # Keep a heading on the same page as its first line.
                    ("NOSPLIT", (0, row), (-1, row + 1)),
                    ("BACKGROUND", (0, row), (-1, row), _SECTION_BG),
                    ("TOPPADDING", (0, row), (-1, row), 5),
                    ("BOTTOMPADDING", (0, row), (-1, row), 5),
                )
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.35 * inch))
    story.append(HRFlowable(width="100%", thickness=0.6, color=_GRID, spaceAfter=0.12 * inch))
    story.append(
        Paragraph(
            " &middot; ".join(xml_escape(part) for part in (store_name, store_address, store_phone) if part),
            footer_style,
        )
    )
    from app.store import settings_view as sv

    story.append(Paragraph(f"Created with {xml_escape(sv.product_credit())}", footer_style))

    doc.build(story)
    return buf.getvalue()
