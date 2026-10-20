// Setup > Item vendors (docs/WEBSITE_READY_CONTRACT.md D4): who supplies each
// item, for a store whose POS has no vendor field (Clover, CSV-only). Pure
// helpers - tested by itemVendorsModel.test.mjs. The server repeats every
// check (services/item_vendors.py); these give the same answer sooner.

export const MAX_VENDORS = 10;
export const MAX_NAME = 80;
export const MAX_CODE = 64;
export const PAGE_SIZE = 50;
export const SHOW_KEY = "kc.tab.item_vendors";

export const SHOW_OPTIONS = [
  { value: "all", label: "All items" },
  { value: "missing", label: "Without a vendor" },
  { value: "with", label: "With a vendor" },
];

export function readShow(storage) {
  try {
    const v = storage?.getItem(SHOW_KEY);
    return SHOW_OPTIONS.some((o) => o.value === v) ? v : "all";
  } catch {
    return "all";
  }
}

// "Main Street Wholesale" / "Main Street Wholesale +2" / "No vendor"
export function vendorSummary(vendors) {
  const list = Array.isArray(vendors) ? vendors : [];
  if (!list.length) return "No vendor";
  return list.length === 1 ? list[0].name : `${list[0].name} +${list.length - 1}`;
}

let seq = 0;
const key = () => `r${++seq}`;

// Server vendors -> editable rows (rank order).
export function editorRows(vendors) {
  const list = (Array.isArray(vendors) ? vendors : []).slice().sort((a, b) => (a.rank || 0) - (b.rank || 0));
  return list.map((v) => ({ key: key(), name: v.name || "", item_code: v.item_code || "" }));
}

export function addRow(rows) {
  return [...rows, { key: key(), name: "", item_code: "" }];
}

export function removeRow(rows, index) {
  return rows.filter((_, i) => i !== index);
}

// Move a row up (-1) or down (+1); the first row is the first choice.
export function moveRow(rows, index, dir) {
  const to = index + dir;
  if (to < 0 || to >= rows.length) return rows;
  const next = rows.slice();
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}

const squash = (s) => String(s ?? "").replace(/\s+/g, " ").trim();
const CONTROL = /[\u0000-\u001f]/;

// Rows -> {errors: {index: message}, vendors: [{name, item_code}]}. A row
// with neither a name nor a code is dropped (an unused "Add vendor").
export function validateRows(rows) {
  const errors = {};
  const vendors = [];
  const seen = new Map();
  (Array.isArray(rows) ? rows : []).forEach((r, i) => {
    const name = squash(r.name);
    const code = String(r.item_code ?? "").trim();
    if (!name && !code) return;
    if (!name) errors[i] = "Enter a vendor name.";
    else if (name.length > MAX_NAME) errors[i] = `At most ${MAX_NAME} characters.`;
    else if (name.includes(";") || CONTROL.test(name)) errors[i] = 'A vendor name can\'t contain ";".';
    else if (code.length > MAX_CODE) errors[i] = `The vendor item code can be at most ${MAX_CODE} characters.`;
    else if (code.includes(";") || CONTROL.test(code)) errors[i] = 'The vendor item code can\'t contain ";".';
    else if (seen.has(name.toLowerCase())) errors[i] = `${name} is listed twice.`;
    if (errors[i]) return;
    seen.set(name.toLowerCase(), i);
    vendors.push({ name, item_code: code || null });
  });
  if (vendors.length > MAX_VENDORS) errors.list = `An item can have at most ${MAX_VENDORS} vendors.`;
  return { errors, vendors };
}

// Changed from what the server holds (names, codes or order)?
export function rowsChanged(rows, vendors) {
  const a = validateRows(rows).vendors.map((v) => `${v.name}\u0001${v.item_code || ""}`);
  const b = (Array.isArray(vendors) ? vendors : [])
    .slice()
    .sort((x, y) => (x.rank || 0) - (y.rank || 0))
    .map((v) => `${v.name}\u0001${v.item_code || ""}`);
  return a.length !== b.length || a.some((x, i) => x !== b[i]);
}

// Import preview / apply result -> one line.
export function importSummary(result) {
  if (!result || typeof result !== "object") return "";
  const rows = Number(result.row_count || 0);
  const errors = Number(result.errors || 0);
  const change = Number(result.items_to_change || 0);
  const parts = [`${rows} ${rows === 1 ? "row" : "rows"}`];
  if (errors) parts.push(`${errors} with ${errors === 1 ? "a problem" : "problems"} (skipped)`);
  if (result.applied) parts.push(`${change} ${change === 1 ? "item" : "items"} updated`);
  else parts.push(change ? `${change} ${change === 1 ? "item" : "items"} will change` : "nothing to change");
  return parts.join(" · ");
}

// Only the rows worth showing in the preview: problems first, then notes.
export function previewRows(result, limit = 50) {
  const rows = Array.isArray(result?.rows) ? result.rows : [];
  const bad = rows.filter((r) => r.error);
  const notes = rows.filter((r) => !r.error && r.note);
  return [...bad, ...notes].slice(0, limit);
}

export function itemVendorsErrorMessage(err) {
  const status = err?.status;
  const body = err?.body && typeof err.body === "object" ? err.body : {};
  const detail = typeof body.detail === "string" ? body.detail : null;
  if (!status) return "Can't reach the server. Check your connection and try again.";
  if (status === 403) return detail && /demo/i.test(detail) ? detail : "Only a store admin can change item vendors.";
  if (status === 413) return "The file is larger than 5 MB. Split it and import each part.";
  return detail || "Something went wrong. Please try again.";
}
