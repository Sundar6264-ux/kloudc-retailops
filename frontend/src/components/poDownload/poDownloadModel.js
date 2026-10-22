// Download a PO as Excel / PDF / CSV (owner, Oct 22). Nothing new is stored:
// the server makes the file from the PO as it is kept today
// (backend/app/services/po_download.py). Pure helpers, so node --test can
// check them without a browser.

export const PO_FORMATS = [
  { value: "xlsx", label: "Excel", hint: ".xlsx" },
  { value: "pdf", label: "PDF", hint: ".pdf" },
  { value: "csv", label: "CSV", hint: ".csv" },
];

const FORMAT_VALUES = new Set(PO_FORMATS.map((f) => f.value));

// Where a PO's file comes from:
//   { kind: "order", id }      - the purchase order log (Placed Orders)
//   { kind: "send", id }       - the Send PO log (exactly what was sent)
//   { kind: "cart", supplier } - what the cart would send right now
export function poExportPath(target, format) {
  if (!FORMAT_VALUES.has(format)) throw new Error("Pick Excel, PDF or CSV.");
  const fmt = `format=${encodeURIComponent(format)}`;
  if (target?.kind === "order") return `/api/ordering/orders/${encodeURIComponent(target.id)}/export?${fmt}`;
  if (target?.kind === "send") return `/api/ordering/po-sends/${encodeURIComponent(target.id)}/export?${fmt}`;
  if (target?.kind === "cart" && target.supplier) {
    return `/api/ordering/cart/export?supplier=${encodeURIComponent(target.supplier)}&${fmt}`;
  }
  throw new Error("Nothing to download.");
}

// PO-<Vendor>-<date>.<ext>, the server's own naming - used only when the
// response has no file name.
export function poFallbackName(supplier, format, date) {
  const vendor = String(supplier || "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "Vendor";
  return `PO-${vendor}-${date}.${format}`;
}
