// Pure helpers for the Purchase Order Cart's "Add items" form
// (AddItemsForm.jsx) - no React, no fetch, so they can be tested with
// `node --test src/components/cartAdd/cartAddModel.test.mjs`.

// The window the suggested quantity is worked out over - the same as the
// app's add sheet (Cart settings default: 90 days of sales, order for 1 week),
// so the web and the phone suggest the same number for the same item.
export const SUGGEST_LOOKBACK_DAYS = 90;
export const SUGGEST_HORIZON_DAYS = 7;

// The biggest qty the cart takes (backend po_cart.MAX_CART_QTY): a typo, a
// pasted barcode or "1e308" is stopped here - one line over it used to make
// the cart fail to load for everyone.
export const MAX_QTY = 100000;

export const norm = (s) => (s || "").trim().toLowerCase();

// Toast lists several vendors in one field, ";"-separated ("Babco;Raja
// Foods") - split before showing or comparing (project gotcha #6). A vendor
// Toast lists twice ("RBest Produce Inc;RBest Produce Inc") is shown once.
export function splitVendors(raw) {
  return [
    ...new Set(
      (raw || "")
        .split(";")
        .map((s) => s.trim())
        .filter(Boolean)
    ),
  ];
}

// The known vendor `typed` names, spelled the way inventory spells it
// ("babco " -> "Babco"), or null. The cart and the vendor search compare
// names exactly, so a different spelling would start a second cart section
// and find none of the vendor's products.
export function canonicalVendor(typed, vendors) {
  const needle = norm(typed);
  if (!needle) return null;
  return (vendors || []).find((v) => norm(v) === needle) || null;
}

// The vendor picker's options for what's typed: matching vendors (vendors
// already in the cart first), then - when the text isn't a known vendor -
// a "use what I typed" option for a vendor that isn't in inventory yet.
// `allowNew` is false until the vendor list has loaded, so a quick Enter
// can't pick a half-typed name as a new vendor.
export function vendorOptions(text, vendors, cartVendors = [], limit = 20, allowNew = true) {
  const needle = norm(text);
  const seen = new Set();
  const pool = [];
  for (const v of [...cartVendors, ...(vendors || [])]) {
    const key = norm(v);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    pool.push(v);
  }
  const matches = (needle ? pool.filter((v) => norm(v).includes(needle)) : pool).slice(0, limit);
  const options = matches.map((name) => ({ name, isNew: false }));
  if (allowNew && needle && !pool.some((v) => norm(v) === needle)) options.push({ name: text.trim(), isNew: true });
  return options;
}

// What's already in `supplier`'s cart section: item_ids, plus the names of
// hand-added lines (no item_id). Same rule as the app's CartSupplierKeys.
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
  return (!!itemId && keys.ids.has(itemId)) || keys.names.has(norm(name));
}

// item_id -> what the form shows from one vendor's Supplier Projection
// response (GET /api/ordering/supplier-projection).
export function projectionIndex(projection, horizon = SUGGEST_HORIZON_DAYS) {
  const index = new Map();
  for (const item of projection?.items || []) {
    const p = item.projections?.[String(horizon)];
    index.set(item.item_id, {
      need: p && p.need_to_order != null ? Number(p.need_to_order) : null,
      onHand: item.on_hand_qty ?? null,
      weekly: item.avg_weekly_demand ?? null,
      sku: item.supplier_item_id || null,
    });
  }
  return index;
}

// The suggested order for 1 week, as the app works it out: the projection's
// need rounded up; 0 = enough stock; null = no projection for the item.
export function suggestion(proj) {
  if (!proj || proj.need == null || !Number.isFinite(proj.need)) return null;
  return proj.need > 0 ? Math.ceil(proj.need) : 0;
}

// The quantity a newly ticked item starts at: the suggestion, else 1.
export function defaultQty(proj) {
  const s = suggestion(proj);
  return s && s > 0 ? s : 1;
}

// The "(in stock: N)" figure, matching the cart line it becomes: the
// projection's on hand (shelf + Container row), else the search's
// in_stock_qty (same figure; servers before Oct 7 only send on_hand_qty,
// the shelf row alone).
export function inStockFor(result, proj) {
  if (proj && proj.onHand != null) return proj.onHand;
  if (result?.in_stock_qty !== undefined) return result.in_stock_qty;
  return result?.on_hand_qty ?? null;
}

export function formatQty(n) {
  if (n == null || n === "" || !Number.isFinite(Number(n))) return "-";
  const v = Number(n);
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

// Why a To-add row's qty can't be sent, or null.
export function qtyProblem(qty) {
  if (qty === "" || qty == null) return "Enter a quantity";
  const n = Number(qty);
  if (Number.isNaN(n)) return "Quantity must be more than 0";
  if (n > MAX_QTY) return `Quantity can't be more than ${MAX_QTY.toLocaleString("en-US")}`;
  if (!Number.isFinite(n) || n <= 0) return "Quantity must be more than 0";
  return null;
}

// The To-add stepper (- / +): one down, never below 1, or one up, never
// above MAX_QTY. A blank or unusable box counts as 0, so + makes it 1.
// Rounded so 1.1 + 1 doesn't show as 2.1000000000000001.
export function stepQty(qty, delta) {
  const n = qty === "" || qty == null ? NaN : Number(qty);
  const base = Number.isFinite(n) ? n : 0;
  const next = Math.round((base + delta) * 10000) / 10000;
  return String(Math.min(MAX_QTY, Math.max(1, next)));
}

// Whether - / + would change the qty (the buttons are disabled otherwise).
export function canStepQty(qty, delta) {
  const n = qty === "" || qty == null ? NaN : Number(qty);
  if (delta < 0) return Number.isFinite(n) && n > 1;
  return !(Number.isFinite(n) && n >= MAX_QTY);
}

// Ticks an inventory item into the To-add list, or unticks it when it's
// already there. A hand-typed row with the same name is replaced by the
// inventory item (keeping a qty someone typed for it), so one product can't
// go to the cart twice - once with its SKU and stock, once as a "new item".
export function tickRow(rows, row) {
  if (rows.some((r) => r.item_id && r.item_id === row.item_id)) return rows.filter((r) => r.item_id !== row.item_id);
  const i = rows.findIndex((r) => r.manual && norm(r.name) === norm(row.name));
  if (i < 0) return [...rows, row];
  const typed = rows[i];
  const next = [...rows];
  next[i] = { ...row, ...(typed.qtyAuto ? {} : { qty: typed.qty, qtyAuto: false }), replaced: typed.name };
  return next;
}

// Inventory rows (from GET /api/inventory/items/search) with exactly this
// name, leaving out bare Container duplicates - for "is the item typed in
// as new already in inventory?".
export function exactNameMatches(items, name) {
  const key = norm(name);
  if (!key) return [];
  return (items || []).filter((i) => !i.container_of && norm(i.name) === key);
}

// A fetch that never got an answer (offline, connection dropped) rejects
// with a TypeError and no HTTP status; a server refusal has a status.
export function isNetworkError(err) {
  return !!err && err.status == null;
}

// After an add whose answer didn't come back as a success: which of the
// rows sent are in the vendor's cart now (keys from a fresh GET /cart), and
// which aren't.
export function addOutcome(sent, keys) {
  const inCart = [];
  const missing = [];
  for (const row of sent) (isInCart(keys, row.item_id, row.name) ? inCart : missing).push(row);
  return { inCart, missing };
}

// Splits the To-add list into what to send and what to leave out because
// it's already in the vendor's cart (re-adding would replace that line's
// qty).
export function splitPostable(rows, keys) {
  const post = [];
  const skipped = [];
  for (const row of rows) {
    if (isInCart(keys, row.item_id, row.name)) skipped.push(row);
    else post.push(row);
  }
  return { post, skipped };
}

// The POST /api/ordering/cart/items body items for the rows to send.
export function cartPayload(rows) {
  return rows.map((r) => ({
    item_id: r.item_id || null,
    name: r.name.trim(),
    supplier_item_id: r.supplier_item_id || null,
    qty: Number(r.qty),
  }));
}

export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
