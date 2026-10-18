// Product code search (docs/BATCH_OCT14_CONTRACT.md W2): every product
// search also finds an item by its Toast item id, any vendor's SKU (Toast's
// `supplier_item_id`, ";"-separated across an item's vendors) or a barcode
// (";"-separated too). Same rule as the backend (app/services/
// product_match.py) and the app (lib/core/utils/product_match.dart); all
// three run the cases in productMatchCases.json.
//
// - A code equal to the search (ignoring case and spaces) is an exact match.
//   All-digit barcodes also compare without leading zeros.
// - Part of a code only counts for a long enough search: 3+ characters for a
//   vendor SKU, 4+ for a barcode, and 6+ with a digit or a dash for the Toast
//   id (a GUID is hex, so a word like "facade" never hits one).
import { currentPosName } from "./store/gating.js";

export const MIN_SUPPLIER_PART = 3;
export const MIN_BARCODE_PART = 4;
export const MIN_ITEM_ID_PART = 6;

// The POS item id's label follows the store's POS ("Toast ID", "Clover ID").
export const CODE_LABEL = {
  get item_id() {
    return `${currentPosName()} ID`;
  },
  supplier_item_id: "SKU",
  barcode: "Barcode",
};

export function normalizeCode(text) {
  return String(text ?? "").replace(/\s+/g, "").toLowerCase();
}

export function splitCodes(value) {
  return String(value ?? "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
}

function digitsEqual(a, b) {
  if (!/^\d+$/.test(a) || !/^\d+$/.test(b)) return false;
  const x = a.replace(/^0+/, "");
  return x !== "" && x === b.replace(/^0+/, "");
}

// [kind, code as stored, exact] for the best code match, or null. Exact beats
// partial; on a tie the Toast id, then the vendor SKU, then the barcode.
// `supplierCodes` / `barcodes` may be ";" strings or arrays of them.
export function matchCode(query, itemId, supplierCodes, barcodes) {
  const needle = normalizeCode(query);
  if (!needle) return null;
  let partial = null;

  if (itemId) {
    const key = normalizeCode(itemId);
    if (key === needle) return ["item_id", String(itemId), true];
    if (needle.length >= MIN_ITEM_ID_PART && /[0-9-]/.test(needle) && key.includes(needle)) {
      partial = partial || ["item_id", String(itemId), false];
    }
  }
  for (const code of flatCodes(supplierCodes)) {
    const key = normalizeCode(code);
    if (key === needle) return ["supplier_item_id", code, true];
    if (needle.length >= MIN_SUPPLIER_PART && key.includes(needle)) {
      partial = partial || ["supplier_item_id", code, false];
    }
  }
  for (const code of flatCodes(barcodes)) {
    const key = normalizeCode(code);
    if (key === needle || digitsEqual(key, needle)) return ["barcode", code, true];
    if (needle.length >= MIN_BARCODE_PART && key.includes(needle)) {
      partial = partial || ["barcode", code, false];
    }
  }
  return partial;
}

function flatCodes(value) {
  if (Array.isArray(value)) return value.flatMap((v) => splitCodes(v));
  return splitCodes(value);
}

function read(row, key) {
  return typeof key === "function" ? key(row) : row?.[key];
}

// The usual code fields on a list row: item_id; supplier_item_id plus the
// additive all_supplier_item_ids; barcode. Override per page when its rows
// keep them elsewhere (e.g. Fix Issues' nested `product`).
export const DEFAULT_CODE_KEYS = {
  itemId: ["item_id"],
  supplier: ["supplier_item_id", "all_supplier_item_ids"],
  barcode: ["barcode"],
};

export function rowCodeMatch(row, query, keys = DEFAULT_CODE_KEYS) {
  const ids = (keys.itemId || []).map((k) => read(row, k)).filter(Boolean);
  const supplier = (keys.supplier || []).map((k) => read(row, k));
  const barcode = (keys.barcode || []).map((k) => read(row, k));
  let best = null;
  for (const id of ids.length ? ids : [null]) {
    const hit = matchCode(query, id, supplier, barcode);
    if (hit && (hit[2] || !best)) best = hit;
    if (best && best[2]) break;
  }
  return best;
}

// "SKU 2659M" / "Barcode 0186..." / "Toast ID 2d3d...", for a result that
// was found by a code rather than its name.
export function codeMatchLabel(hit) {
  if (!hit) return null;
  const [kind, code] = hit;
  if (kind === "item_id") return `${CODE_LABEL.item_id} ${code.slice(0, 8)}…`;
  return `${CODE_LABEL[kind]} ${code}`;
}
