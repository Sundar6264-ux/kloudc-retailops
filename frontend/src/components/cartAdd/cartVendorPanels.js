// Pure helpers for the Purchase Order Cart's vendor panels
// (PurchaseOrderCart.jsx): which vendors are open, remembered per browser,
// the summary on each collapsed header, deep links and the "just added"
// highlight - no React, no fetch, so they can be tested with
// `node --test src/components/cartAdd/cartAddModel.test.mjs`.
import { canonicalVendor, formatQty, norm, plural } from "./cartAddModel.js";

// localStorage key: a JSON list of the vendor names whose panels are open.
export const OPEN_VENDORS_KEY = "retailops_cart_open_vendors";
// A cart never has more vendors than inventory does (55 on prod); anything
// longer is junk and is cut, so a bad value can't grow without limit.
export const MAX_REMEMBERED = 200;

// The browser's localStorage, or null when it can't be used (private mode,
// blocked site data, no window in tests). Reading the property itself can
// throw, so it's wrapped too.
export function browserStorage() {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

// The remembered open vendors as a Set. Never throws: no storage, nothing
// stored, bad JSON or a value that isn't a list of names gives an empty Set
// (every panel collapsed - the default).
export function readOpenVendors(storage) {
  try {
    const raw = storage ? storage.getItem(OPEN_VENDORS_KEY) : null;
    if (!raw) return new Set();
    const list = JSON.parse(raw);
    if (!Array.isArray(list)) return new Set();
    return new Set(list.filter((v) => typeof v === "string" && v.trim() !== "").slice(0, MAX_REMEMBERED));
  } catch {
    return new Set();
  }
}

// Stores the open vendors (nothing open = the key is removed). Returns
// false when the browser refused (full / blocked storage) - the page works
// the same, it just won't remember.
export function writeOpenVendors(storage, open) {
  try {
    if (!storage) return false;
    if (!open || open.size === 0) storage.removeItem(OPEN_VENDORS_KEY);
    else storage.setItem(OPEN_VENDORS_KEY, JSON.stringify([...open].sort().slice(0, MAX_REMEMBERED)));
    return true;
  } catch {
    return false;
  }
}

// `open` with `vendor` opened or closed. The same Set comes back when
// nothing changes, so a React state update can be skipped.
export function setVendorOpen(open, vendor, isOpen) {
  if (!vendor || open.has(vendor) === isOpen) return open;
  const next = new Set(open);
  if (isOpen) next.add(vendor);
  else next.delete(vendor);
  return next;
}

// Only the vendors still in the cart: a vendor whose order was placed (its
// section gone) starts collapsed again the next time it has items. Same Set
// back when nothing was dropped.
export function pruneOpenVendors(open, cartVendors) {
  const keep = new Set(cartVendors || []);
  let changed = false;
  const next = new Set();
  for (const v of open) {
    if (keep.has(v)) next.add(v);
    else changed = true;
  }
  return changed ? next : open;
}

// What a vendor's collapsed header shows. Qty is cases (the PDF's "Qty to
// order (in Cases)"), Case of the units in one case. A qty being retyped
// ("" in the box) counts as 0, like the estimate always has.
export function vendorSummary(items) {
  let cases = 0;
  let units = 0;
  let estimate = 0;
  let missingCost = 0;
  let removalRequests = 0;
  let orderable = 0;
  for (const i of items || []) {
    const qty = Number(i.qty) || 0;
    const caseOf = i.case_of == null || i.case_of === "" ? 1 : Number(i.case_of) || 0;
    cases += qty;
    units += qty * caseOf;
    if (qty > 0) orderable += 1;
    if (i.unit_cost != null) estimate += i.unit_cost * qty * caseOf;
    else missingCost += 1;
    if (i.removal_requested) removalRequests += 1;
  }
  return { lines: (items || []).length, cases, units, estimate, missingCost, removalRequests, orderable };
}

export function formatMoney(n) {
  const v = Number(n);
  return (Number.isFinite(v) ? v : 0).toLocaleString("en-US", { style: "currency", currency: "USD" });
}

// ["14 items", "37 cases", "120 units"] - units only when some line's case
// holds more than one unit (otherwise it would repeat the cases figure).
export function summaryParts(s) {
  const parts = [plural(s.lines, "item"), `${formatQty(s.cases)} ${s.cases === 1 ? "case" : "cases"}`];
  if (Math.abs(s.units - s.cases) > 1e-9) parts.push(`${formatQty(s.units)} ${s.units === 1 ? "unit" : "units"}`);
  return parts;
}

// "14 items · 37 cases · 120 units"
export function summaryLine(s) {
  return summaryParts(s).join(" · ");
}

// The estimate as the header shows it: the dollar figure, and how many
// lines it leaves out because Toast has no cost for them.
export function estimateNote(s) {
  if (s.lines > 0 && s.missingCost === s.lines) return { amount: null, missing: "No cost on file" };
  return {
    amount: formatMoney(s.estimate),
    missing: s.missingCost > 0 ? `${plural(s.missingCost, "item")} without a cost` : null,
  };
}

// The vendor a link to the cart points at - "#/purchase-order?vendor=X"
// or router state { vendor: X } - or null.
export function linkedVendor(search, state) {
  let fromQuery = null;
  try {
    fromQuery = new URLSearchParams(search || "").get("vendor");
  } catch {
    fromQuery = null;
  }
  const fromState = state && typeof state.vendor === "string" ? state.vendor : null;
  const v = (fromQuery || fromState || "").trim();
  return v || null;
}

// The cart section a linked vendor name means (the cart's own spelling,
// ignoring case and outer spaces), or null when it has nothing in the cart.
export function cartVendorFor(name, cartSuppliers) {
  return canonicalVendor(name, (cartSuppliers || []).map((s) => s.supplier));
}

// The website path that opens the cart at one vendor (expanded).
export function cartLinkFor(vendor) {
  return vendor ? `/purchase-order?vendor=${encodeURIComponent(vendor)}` : "/purchase-order";
}

// At most this many lines are marked "Just added" from a link (a vendor's
// whole Critical + Order now list is far below it).
export const MAX_JUST_ADDED = 500;

function cleanIds(list) {
  const ids = (Array.isArray(list) ? list : []).filter((id) => typeof id === "string" && id.trim() !== "");
  return [...new Set(ids)].slice(0, MAX_JUST_ADDED);
}

// Router state to go with cartLinkFor(vendor) when the link should also mark
// the lines with these item_ids "Just added" (Reorder Candidates' "Open cart"
// after adding). No ids = just the vendor.
export function cartLinkState(vendor, justAdded) {
  const ids = cleanIds(justAdded);
  return ids.length ? { vendor, justAdded: ids } : { vendor };
}

// The item_ids a link asks the cart to mark "Just added" (router state from
// cartLinkState), or [] - never throws on a missing or odd state.
export function linkedJustAdded(state) {
  return cleanIds(state && typeof state === "object" ? state.justAdded : null);
}

// Which cart lines an add just put in: item_ids for inventory items, names
// for hand-typed ones (a hand-added line has no item_id - the same identity
// rule the cart and the add form use).
export function newLineKeys(rows) {
  const ids = new Set();
  const names = new Set();
  for (const r of rows || []) {
    if (r.item_id) ids.add(r.item_id);
    else if (r.name) names.add(norm(r.name));
  }
  return { ids, names };
}

export function isNewLine(keys, line) {
  if (!keys || !line) return false;
  return line.item_id ? keys.ids.has(line.item_id) : keys.names.has(norm(line.name));
}

// A DOM id for a vendor's panel (vendor names have spaces, dots, "&"...).
export function vendorDomId(prefix, vendor) {
  const slug = norm(vendor)
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  let hash = 0;
  for (const ch of vendor || "") hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return `${prefix}-${slug || "vendor"}-${hash.toString(36)}`;
}
