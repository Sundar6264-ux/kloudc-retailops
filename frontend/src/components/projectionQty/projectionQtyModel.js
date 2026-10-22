// X1 (Oct 14): order quantities on Supplier Projection and the cart's
// projection context. Pure helpers (node tests in projectionQtyModel.test.mjs).
//
// The server works out each item's `suggested_order` (qty + the numbers it is
// built from, `basis`) - services/order_suggestion.py; these helpers only
// turn the typed boxes into what gets sent.

// Same cap as the cart (po_cart.MAX_CART_QTY / cartAddModel.MAX_QTY).
export const MAX_QTY = 100000;

// "12" -> {ok, value: 12}; blank / junk / negative / too big -> {ok: false}.
export function parseQty(text) {
  const raw = String(text ?? "").trim();
  if (raw === "") return { ok: false, error: "empty" };
  const n = Number(raw);
  if (!Number.isFinite(n)) return { ok: false, error: "not a number" };
  if (n < 0) return { ok: false, error: "below 0" };
  if (n > MAX_QTY) return { ok: false, error: "too big" };
  return { ok: true, value: n };
}

export function suggestionOf(item) {
  const s = item?.suggested_order;
  return s && typeof s === "object" ? s : null;
}

// The box's starting text: an item already in the cart starts at its cart
// qty (never the suggestion - re-adding would replace a qty someone set);
// otherwise the suggestion; "" when there is none (an older server).
export function initialQtyText(item, cartLine) {
  if (cartLine && Number.isFinite(Number(cartLine.qty))) return String(cartLine.qty);
  const s = suggestionOf(item);
  return s && Number.isFinite(Number(s.qty)) ? String(s.qty) : "";
}

export function qtyText(item, drafts, cartLine) {
  const d = drafts?.[item.item_id];
  return d !== undefined ? d : initialQtyText(item, cartLine);
}

// What "Add to cart" sends for the ticked items that are NOT in the cart yet:
// each one's typed qty (the suggestion unless changed) and, when the server
// knew the pack size, its Case of. Ticks with qty 0 are skipped (enough
// stock); blank / bad numbers stop the add (`invalid`, names).
export function buildAddItems(items, tickedIds, cartIds, drafts, cartLines = {}) {
  const ticked = new Set(tickedIds);
  const out = { items: [], skippedZero: [], invalid: [], alreadyInCart: [] };
  for (const item of items || []) {
    if (!ticked.has(item.item_id)) continue;
    if (cartIds.has(item.item_id)) {
      out.alreadyInCart.push(item.item_id);
      continue;
    }
    const parsed = parseQty(qtyText(item, drafts, cartLines[item.item_id]));
    const name = item.name || item.item_id;
    if (!parsed.ok) {
      out.invalid.push(name);
      continue;
    }
    if (parsed.value === 0) {
      out.skippedZero.push(name);
      continue;
    }
    const line = {
      item_id: item.item_id,
      name: item.name,
      supplier_item_id: item.supplier_item_id || null,
      qty: parsed.value,
    };
    const caseOf = suggestionOf(item)?.case_of;
    if (Number.isFinite(caseOf) && caseOf > 1) line.case_of = caseOf;
    out.items.push(line);
  }
  return out;
}

// An in-cart item's box differs from its cart qty and is a valid number above
// 0 (0 would take the line out - only admins remove, from the cart page).
export function updateFor(item, drafts, cartLine) {
  if (!cartLine) return null;
  const parsed = parseQty(qtyText(item, drafts, cartLine));
  if (!parsed.ok || parsed.value <= 0) return null;
  if (Math.abs(parsed.value - Number(cartLine.qty)) < 1e-9) return null;
  return { id: cartLine.id, qty: parsed.value };
}

// "Added 3 item(s) ... Skipped 1 with qty 0 (enough stock)."
export function addMessage(supplier, result) {
  const parts = [];
  if (result.items.length) parts.push(`Added ${result.items.length} item(s) to the cart for ${supplier}.`);
  if (result.skippedZero.length) {
    parts.push(`Skipped ${result.skippedZero.length} with qty 0 (enough stock): ${result.skippedZero.join(", ")}.`);
  }
  if (result.alreadyInCart.length) {
    parts.push(`${result.alreadyInCart.length} already in the cart - change those with Update.`);
  }
  return parts.join(" ");
}

// The cart line's compact context, e.g. "~14/week · suggested 10 · on hand 6".
export function cartContext(projection) {
  if (!projection || typeof projection !== "object") return null;
  return { text: projection.compact || null, basis: projection.basis || null, qty: projection.qty ?? null };
}

// Whether to offer "Use N" on a cart line: a suggestion above 0 that differs
// from the line's qty.
export function useSuggestedQty(projection, lineQty) {
  const q = projection?.qty;
  if (!Number.isFinite(q) || q <= 0) return null;
  return Math.abs(q - Number(lineQty)) < 1e-9 ? null : q;
}

// Changes when anything the cart context depends on changes (lines, vendors,
// qty, Case of) - the cart page re-reads the context only then.
export function contextKey(cart) {
  const lines = [];
  for (const s of cart?.suppliers || []) {
    for (const i of s.items || []) lines.push([i.id, i.item_id, s.supplier, i.qty, i.case_of]);
  }
  lines.sort((a, b) => a[0] - b[0]);
  return JSON.stringify(lines);
}

// --- Cases and the one-tap Add ---------------------------------------------
// Owner: "we order cases from vendor not the units" - when an item's case
// size is known (the inventory file's receiving units, a cart line's Case
// of), the order qty is in CASES. Old field names stay: `qty` is the number
// typed / sent to the vendor; `case_of` is units per case.

function bigCase(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 1 ? n : null;
}

// Units per case for this row: its cart line's Case of decides once it is
// in the cart (1 there = units); otherwise the suggestion's pack size, else
// the item's own case_size (an older suggestion without one).
export function caseSizeOf(item, cartLine) {
  if (cartLine) return bigCase(cartLine.case_of);
  return bigCase(suggestionOf(item)?.case_of) ?? bigCase(item?.case_size);
}

const fmt = (n) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100));

// "1 case = 24 units · 48 units" beside the qty; null for an item in units.
export function caseLabel(qty, caseSize) {
  const size = bigCase(caseSize);
  if (!size) return null;
  const q = Number(qty);
  const total = Number.isFinite(q) && q >= 0 ? ` · ${fmt(q * size)} units` : "";
  return `1 case = ${fmt(size)} units${total}`;
}

// "2 cases (48 units)" / "5" - the same words the PO uses.
export function qtyWords(qty, caseSize) {
  const size = bigCase(caseSize);
  const q = Number(qty) || 0;
  if (!size) return fmt(q);
  return `${fmt(q)} ${q === 1 ? "case" : "cases"} (${fmt(q * size)} units)`;
}

// What one tap on a row's Add sends: the suggested qty (cases when the case
// size is known). A suggestion of 0 ("enough stock") or none at all still
// adds 1 - Add was tapped on purpose; the stepper changes it after. On an
// older server (no suggestion) the need is turned into cases here.
export function quickAddLine(item, horizon) {
  const s = suggestionOf(item);
  const size = bigCase(s?.case_of) ?? (s ? null : bigCase(item?.case_size));
  let qty;
  if (s && Number.isFinite(Number(s.qty))) qty = Number(s.qty);
  else {
    const need = Number(item?.projections?.[horizon]?.need_to_order);
    qty = Number.isFinite(need) && need > 0 ? (size ? Math.ceil(need / size) : Math.ceil(need)) : 0;
  }
  if (!(qty > 0)) qty = 1;
  const line = {
    item_id: item.item_id,
    name: item.name,
    supplier_item_id: item.supplier_item_id || null,
    qty,
  };
  if (size) line.case_of = size;
  return line;
}

// − / + on an in-cart row: never below 1 (0 is a removal - admins, from the
// cart page, or the Undo right after adding).
export function stepQty(qty, delta) {
  const q = Number(qty);
  const next = (Number.isFinite(q) ? q : 0) + delta;
  return Math.min(MAX_QTY, Math.max(1, Math.round(next * 100) / 100));
}

// The cart line offering "Switch to cases": in units (Case of 1) while the
// inventory file says the item comes in cases. qty units -> whole cases.
export function switchToCases(line) {
  const size = bigCase(line?.item_case_size);
  if (!size || bigCase(line?.case_of)) return null;
  const units = Number(line.qty) || 0;
  return { case_of: size, qty: Math.max(1, Math.ceil(units / size - 1e-9)) };
}
