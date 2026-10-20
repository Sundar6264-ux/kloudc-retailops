"""Tiny shared CSV-from-dicts helper for the various small report exports
(barcode reports, price change log) - not the main orders export, which has
its own richer column mapping in services/export.py (it uses csv_cell too).
"""

import re

# A cell starting with one of these is run as a formula by Excel / Sheets /
# Numbers ("CSV injection"). Exported text includes things staff type (purchase
# notes, category names, submitter names) and Toast item names, so such a cell
# gets a leading apostrophe - the spreadsheet then shows it as plain text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# ...except a plain number ("-3", "-2.5", "+1"): negative quantities are real
# data and must stay numbers.
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?([eE][+-]?\d+)?")


def neutralize_formula(text: str) -> str:
    """``text`` with a leading apostrophe when a spreadsheet would run it as
    a formula (plain numbers such as "-3" are left alone)."""
    if text.startswith(_FORMULA_START) and not _PLAIN_NUMBER.fullmatch(text):
        return "'" + text
    return text


def csv_cell(val) -> str:
    """One CSV cell: None -> "", formula-looking text neutralized, quoted
    when it holds a comma, quote or line break."""
    text = neutralize_formula("" if val is None else str(val))
    if "," in text or '"' in text or "\n" in text or "\r" in text:
        text = '"' + text.replace('"', '""') + '"'
    return text


def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    headers = list(rows[0].keys())
    lines = [",".join(csv_cell(h) for h in headers)]
    for row in rows:
        lines.append(",".join(csv_cell(row.get(h)) for h in headers))
    return "\n".join(lines) + "\n"
