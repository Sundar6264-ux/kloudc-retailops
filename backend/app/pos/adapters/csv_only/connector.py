"""``CsvOnlyConnector``: the PosConnector for ``POS_PROVIDER=csv`` (MT13).

A store without a POS API (and the DEMO store) gets its catalog and stock
only from uploaded files. The only capability is ``csv_catalog_import``;
every other connector method raises ``NotSupported`` through
``BaseConnector`` (HTTP 409 in routers), so:
- the M1 job gating drops every POS job (daily sales sync, open checks,
  day totals, insights, inventory API snapshot, menu re-check);
- ``features_for_store`` drops Open Checks and Sales insights;
- the website and app hide the pages that need a POS (contract section 6).

Read-only and offline by construction: no HTTP client is imported here.
"""

import csv
import io
from typing import Dict, FrozenSet, List, Optional, Tuple

from app.pos import capabilities as _caps
from app.pos.base import BaseConnector
from app.pos.capabilities import Capability
from app.pos.models import CatalogRow, ConnectorHealth
from app.pos.adapters.csv_only import template as _template

# MT7 adds CSV_CAPABILITIES to app/pos/capabilities.py (contract section 7);
# until then the same one-member set is defined here.
CSV_CAPABILITIES: FrozenSet[Capability] = getattr(
    _caps, "CSV_CAPABILITIES", frozenset({Capability.CSV_CATALOG_IMPORT})
)

DISPLAY_NAME = "CSV upload"

XLS_NOT_SUPPORTED = "This is an old Excel file (.xls). Save the file as .xlsx or .csv and upload again."


class CsvParseError(ValueError):
    """The file can't be read as a catalog (shown to the person uploading)."""


def _read_table(filename: str, content: bytes) -> Tuple[List[str], List[List[str]]]:
    """(header row, data rows) of a .csv or .xlsx file, all cells as text."""
    lower = (filename or "").lower()
    if lower.endswith(".xls"):
        raise CsvParseError(XLS_NOT_SUPPORTED)
    if lower.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        rows = [r for r in csv.reader(io.StringIO(text))]
    elif lower.endswith(".xlsx"):
        try:
            import pandas as pd  # only needed for spreadsheets

            df = pd.read_excel(io.BytesIO(content), dtype=str)
        except Exception as exc:  # noqa: BLE001
            raise CsvParseError(f"Could not read {filename} as a table: {exc}") from exc
        rows = [list(df.columns)] + [["" if v is None or v != v else str(v) for v in r] for r in df.values.tolist()]
    else:
        raise CsvParseError(f"Unsupported file type for {filename!r}: expected .csv or .xlsx")
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise CsvParseError("The file is empty.")
    return [str(c) for c in rows[0]], rows[1:]


def _number(raw: str) -> Optional[float]:
    cleaned = (raw or "").replace("$", "").replace(",", "").replace("%", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class CsvOnlyConnector(BaseConnector):
    source = "csv"
    display_name = DISPLAY_NAME
    capabilities: FrozenSet[Capability] = CSV_CAPABILITIES

    def health(self) -> ConnectorHealth:
        # Nothing to connect to: always "configured".
        return ConnectorHealth(
            source=self.source,
            configured=True,
            detail="No POS connection: the catalog and stock come from uploaded CSV files.",
        )

    # --- catalog files -------------------------------------------------------------------------

    def column_map(self, headers: List[str]) -> Dict[int, str]:
        """Column index -> inventory_snapshots field, for the headers this
        connector knows (canonical or alias). The first column wins when two
        headers map to the same field."""
        out: Dict[int, str] = {}
        taken = set()
        for i, h in enumerate(headers):
            field = _template.field_for_header(h)
            if field and field not in taken:
                out[i] = field
                taken.add(field)
        return out

    def parse_catalog_export(self, filename: str, content: bytes) -> List[CatalogRow]:
        """Rows of an uploaded catalog file, mapped to inventory_snapshots
        fields. Needs an item id column ("Item ID", or an alias such as
        "SKU"); rows without an id are skipped. Numbers lose "$", "," and
        "%"; blank cells are None."""
        self.require(Capability.CSV_CATALOG_IMPORT)
        headers, rows = _read_table(filename, content)
        mapping = self.column_map(headers)
        if "item_id" not in mapping.values():
            found = ", ".join(sorted({_template.normalize_header(h) for h in headers if str(h).strip()}))
            raise CsvParseError(f"Missing required column: Item ID (or SKU). Found columns: {found}")
        out: List[CatalogRow] = []
        for row in rows:
            values: Dict[str, object] = {}
            for i, field in mapping.items():
                raw = (row[i] if i < len(row) else "") or ""
                raw = str(raw).strip()
                if field in _template.NUMERIC_FIELDS:
                    values[field] = _number(raw)
                else:
                    values[field] = raw or None
            item_id = values.pop("item_id", None)
            if not item_id:
                continue
            out.append(CatalogRow(item_external_id=str(item_id), fields=tuple(sorted(values.items()))))
        return out

    def canonical_csv(self, filename: str, content: bytes) -> bytes:
        """The same file as CSV with every known header renamed to its
        canonical template name (unknown columns kept as they are), so a file
        using aliases ("SKU", "UPC", "Qty on hand") can go through the
        existing Inventory upload unchanged."""
        headers, rows = _read_table(filename, content)
        renamed = []
        by_field = {f: h for h, f in _template.TEMPLATE_COLUMNS}
        mapping = self.column_map(headers)
        for i, h in enumerate(headers):
            renamed.append(by_field[mapping[i]] if i in mapping else h)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(renamed)
        writer.writerows(rows)
        return buf.getvalue().encode("utf-8")
