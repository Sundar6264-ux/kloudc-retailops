// Pure helpers for adding to the Purchase Order Cart from Reorder Candidates
// (ReorderCart.jsx) - no React, no fetch, so they can be tested with
// `node --test src/components/reorderCart/reorderCartModel.test.mjs`.
//
// Quantities follow the app exactly (mobile lib/features/reorder):
// - one item's "Add 1 to cart" adds qty 1, like the + on the app's item card
//   (reorder_vendor_screen.dart adds qty 1 on purpose, so the suggested
//   quantity stays visible instead of silently becoming the order quantity);
// - "Add selected" and "Add all Critical + Order now" open a review list with
//   each item's suggested quantity - the report's Shortfall - which can be
//   changed first (bulk_add_sheet.dart pre-fills formatReorderQty(shortfall));
// - every add is in units: Case of 1, which is what the app sends. The cart's
//   Qty x Case of is the units ordered, so 6 here is 6 units.
// Items already in the vendor's cart are always left out: re-adding one
// replaces the qty on that cart line (po_cart.add_items upserts by item_id).
//
// A few small helpers mirror components/cartAdd/cartAddModel.js (the cart
// page's add form). They are copied, not imported, so a rework of that form
// can't break this page.

// The biggest qty the cart takes (backend po_cart.MAX_CART_QTY).
export const MAX_QTY = 100000;

// One item's quick add, as the app's item card.
export const QUICK_ADD_QTY = 1;

// The tiers "Add all Critical + Order now" picks up (the app's bulkAddTiers).
export const BULK_TIERS = ["critical", "order_now"];

// The report's group for items with no vendor in Toast - not a vendor, so
// there is no cart to add them to from here.
export const UNASSIGNED = "Unassigned";

// What the server says when the App Review demo login tries to write
// (backend demo_guard.DEMO_READ_ONLY).
export const DEMO_READ_ONLY = "The demo account can look around but can't change the store's real data.";

export const norm = (s) => (s || "").trim().toLowerCase();

export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// A vendor group whose items can go into a cart: a real vendor name. The
// report already splits Toast's ";"-separated vendor field and lists each
// item under one vendor, so the group name is a single vendor.
export function canAddToVendor(vendor) {
  const name = (vendor || "").trim();
  return !!name && name !== UNASSIGNED && !name.includes(";");
}

// What's already in `supplier`'s cart section: item_ids, plus the names of
// hand-added lines (no item_id) - the same rule as the cart page's add form.
// Vendor names compare ignoring case and outer spaces.
export function cartKeysFor(cartSuppliers, supplier) {
  const needle = norm(supplier);
  const ids = new Set();
  const names = new Set();
  if (!needle) return { ids, names };
  for (const s of cartSuppliers || []) {
    if (norm(s.supplier) !== needle) continue;
    for (const i of s.items || []) {
      if (i.item_id) ids.add(i.item_id);
      else names.add(norm(i.name));
    }
  }
  return { ids, names };
}

export function isInCart(keys, itemId, name) {
  if (!keys) return false;
  return (!!itemId && keys.ids.has(itemId)) || keys.names.has(norm(name));
}

export function itemInCart(keys, item) {
  return isInCart(keys, item?.item_id, item?.name);
}

// The suggested quantity as the qty box shows it: the Shortfall without a
// pointless ".0" (a weighed item's fraction is kept, up to 2 decimals) - the
// app's formatReorderQty. "" when there is no usable suggestion.
export function suggestedQty(shortfall) {
  const n = Number(shortfall);
  if (shortfall == null || shortfall === "" || !Number.isFinite(n) || n <= 0) return "";
  if (Number.isInteger(n)) return String(n);
  const text = n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return text === "0" ? "" : text;
}

// An on-hand count for display: whole numbers as is, a weighed item's
// fraction to 2 decimals at most, "-" when unknown.
export function formatStock(n) {
  if (n == null || n === "" || !Number.isFinite(Number(n))) return "-";
  const v = Number(n);
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

// Why a qty can't be sent, or null (positive, finite, at most MAX_QTY).
export function qtyProblem(qty) {
  if (qty === "" || qty == null) return "Enter a quantity";
  const n = Number(qty);
  if (Number.isNaN(n)) return "Quantity must be more than 0";
  if (n > MAX_QTY) return `Quantity can't be more than ${MAX_QTY.toLocaleString("en-US")}`;
  if (!Number.isFinite(n) || n <= 0) return "Quantity must be more than 0";
  return null;
}

// A vendor's Critical + Order now items that aren't in its cart yet, in the
// report's order (the app's bulkAddCandidates).
export function bulkCandidates(items, keys) {
  return (items || []).filter((c) => BULK_TIERS.includes(c?.urgency?.tier) && !itemInCart(keys, c));
}

// How many of them are in the cart already (left out of "Add all").
export function bulkAlreadyInCart(items, keys) {
  return (items || []).filter((c) => BULK_TIERS.includes(c?.urgency?.tier) && itemInCart(keys, c)).length;
}

// The ticked items that can still be added (ticked and not in the cart), in
// the report's order. `selected` is a Set (or array) of item_ids.
export function selectedCandidates(items, selected, keys) {
  const ids = selected instanceof Set ? selected : new Set(selected || []);
  return (items || []).filter((c) => ids.has(c.item_id) && !itemInCart(keys, c));
}

// The review list's rows for these items: included, qty = the suggestion.
export function reviewRows(items) {
  return (items || []).map((item) => ({
    key: item.item_id,
    item,
    include: true,
    qty: suggestedQty(item.shortfall),
    suggested: suggestedQty(item.shortfall),
  }));
}

// The rows to send from a review list: ticked ones only.
export function includedRows(rows) {
  return (rows || []).filter((r) => r.include);
}

// True when every included row has a qty the cart takes and there is at
// least one.
export function canSubmitRows(rows) {
  const inc = includedRows(rows);
  return inc.length > 0 && inc.every((r) => !qtyProblem(r.qty));
}

// Splits rows ({item, qty}) into what to send and what to leave out because
// the item is in the vendor's cart (re-adding would replace that line's qty).
export function splitPostable(rows, keys) {
  const post = [];
  const skipped = [];
  for (const row of rows || []) (itemInCart(keys, row.item) ? skipped : post).push(row);
  return { post, skipped };
}

// After an add whose answer didn't come back as a success: which of the rows
// sent are in the vendor's cart now (keys from a fresh GET /cart).
export function addOutcome(sent, keys) {
  const inCart = [];
  const missing = [];
  for (const row of sent || []) (itemInCart(keys, row.item) ? inCart : missing).push(row);
  return { inCart, missing };
}

// The POST /api/ordering/cart/items body items: units, Case of 1 (what the
// app sends). The vendor's own item code is left for the server to fill in
// from the latest inventory (po_cart._lookup_supplier_item_id) - the report
// doesn't carry it.
export function cartItemsPayload(rows) {
  return (rows || []).map((r) => ({
    item_id: r.item.item_id,
    name: (r.item.name || r.item.item_id || "").trim(),
    supplier_item_id: r.item.supplier_item_id || null,
    qty: Number(r.qty),
    case_of: 1,
  }));
}

// The cart as the page knows it, plus the lines a successful POST returned
// (CartItemOut rows) - so the rows show "In cart" at once, before the
// re-read lands. A line the cart already has (same id) is replaced.
export function mergeAddedLines(cart, lines) {
  const base = cart && Array.isArray(cart.suppliers) ? cart : { suppliers: [] };
  if (!lines || lines.length === 0) return base;
  const suppliers = base.suppliers.map((s) => ({ ...s, items: [...(s.items || [])] }));
  for (const line of lines) {
    if (!line || !line.supplier) continue;
    let group = suppliers.find((s) => s.supplier === line.supplier);
    if (!group) {
      group = { supplier: line.supplier, items: [] };
      suppliers.push(group);
    }
    const i = line.id != null ? group.items.findIndex((x) => x.id === line.id) : -1;
    if (i >= 0) group.items[i] = { ...group.items[i], ...line };
    else group.items.push(line);
  }
  return { ...base, suppliers };
}

// A fetch that never got an answer (offline, connection dropped) rejects
// with a TypeError and no HTTP status; a server refusal has a status.
export function isNetworkError(err) {
  return !!err && err.status == null;
}

function trimReason(text) {
  return String(text || "").replace(/[.\s]+$/, "");
}

const was = (n) => (n === 1 ? "was" : "were");
const isAre = (n) => (n === 1 ? "is" : "are");
const itsTheir = (n) => (n === 1 ? "its" : "their");

// Whether a message offers "Open cart": not when nothing changed because
// the add was refused, nor for the demo login.
export function offersCartLink(kind) {
  return kind === "added" || kind === "none" || kind === "partial" || kind === "unknown";
}

// The vendor bar's message after an add, as { tone, text } (tone: success /
// info / error). `r` describes what happened:
//   kind: "added" | "none" | "failed" | "partial" | "unknown" | "demo"
//   vendor, count (added), single ({name, qty} for one item's quick add),
//   leftOut (in the cart when the list was made), meanwhile (added to the
//   cart elsewhere since the page read it - left out, never overwritten),
//   reason (error text), sent / missing (counts), checked (the answer got
//   lost but the cart shows every item).
export function resultMessage(r) {
  const cart = `${r.vendor}'s cart`;
  const notes = [];
  if (r.leftOut > 0)
    notes.push(
      `${plural(r.leftOut, "item")} already in the cart ${was(r.leftOut)} left out - change ${itsTheir(r.leftOut)} qty in the cart.`
    );
  if (r.meanwhile > 0)
    notes.push(
      `${plural(r.meanwhile, "item")} ${was(r.meanwhile)} added to the cart elsewhere meanwhile and left out, so ${itsTheir(r.meanwhile)} qty wasn't changed.`
    );
  const tail = notes.length ? ` ${notes.join(" ")}` : "";
  switch (r.kind) {
    case "added": {
      const what = r.single ? `${r.single.name} (qty ${r.single.qty})` : plural(r.count, "item");
      const lost = r.checked
        ? ` (The server's answer didn't come back, but the cart shows ${r.single || r.count === 1 ? "it" : "them"}.)`
        : "";
      return { tone: "success", text: `Added ${what} to ${cart}.${lost}${tail}` };
    }
    case "none": {
      if (r.single)
        return {
          tone: "info",
          text: `Nothing added: ${r.single.name} was added to ${cart} elsewhere meanwhile, so its qty wasn't changed - change it in the cart if needed.`,
        };
      const n = (r.meanwhile || 0) + (r.leftOut || 0);
      return {
        tone: "info",
        text: `Nothing added: ${n === 1 ? "that item is" : `all ${n} items are`} already in ${cart} (checked again just before adding). Change the qty in the cart if needed.`,
      };
    }
    case "failed":
      return { tone: "error", text: `Couldn't add to ${cart}: ${trimReason(r.reason)}. Nothing was added.` };
    case "partial":
      return {
        tone: "error",
        text: `Only ${r.count} of the ${plural(r.sent, "item")} reached ${cart} (${trimReason(r.reason)}). The other ${r.missing} ${isAre(r.missing)} not in the cart - add ${r.missing === 1 ? "it" : "them"} again.`,
      };
    case "unknown":
      return {
        tone: "error",
        text: `Couldn't add to ${cart} (${trimReason(r.reason)}), and the cart can't be checked right now, so it isn't known whether ${r.sent === 1 ? "it" : "they"} got in. Reload the page and check the cart before adding again.`,
      };
    case "demo":
      return { tone: "info", text: `${DEMO_READ_ONLY} Nothing was added to the cart.` };
    default:
      return { tone: "info", text: "" };
  }
}
