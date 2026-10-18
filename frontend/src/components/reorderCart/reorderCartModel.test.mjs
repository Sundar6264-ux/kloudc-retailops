// node --test src/components/reorderCart/reorderCartModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  BULK_TIERS,
  DEMO_READ_ONLY,
  MAX_QTY,
  QUICK_ADD_QTY,
  addOutcome,
  bulkAlreadyInCart,
  bulkCandidates,
  canAddToVendor,
  canSubmitRows,
  cartItemsPayload,
  cartKeysFor,
  formatStock,
  includedRows,
  isInCart,
  isNetworkError,
  itemInCart,
  mergeAddedLines,
  offersCartLink,
  plural,
  qtyProblem,
  resultMessage,
  reviewRows,
  selectedCandidates,
  splitPostable,
  suggestedQty,
} from "./reorderCartModel.js";

const item = (item_id, tier, shortfall, extra = {}) => ({
  item_id,
  name: extra.name || item_id.toUpperCase(),
  shortfall,
  urgency: { tier, label: tier },
  ...extra,
});

// One vendor group's items, in the report's order (critical first).
const ITEMS = [
  item("maida", "critical", 32),
  item("rice", "critical", 6),
  item("dal", "order_now", 12),
  item("ghee", "check_count", 4),
  item("salt", "can_wait", 3),
  item("tea", "ordered", 9),
];

const CART = [
  {
    supplier: "Vdyas",
    items: [
      { id: 1, item_id: "rice", name: "RICE", qty: 1 },
      { id: 2, item_id: null, name: " Fresh Curry Leaves ", qty: 2 },
    ],
  },
  { supplier: "Babco", items: [{ id: 3, item_id: "dal", name: "DAL", qty: 4 }] },
];

test("the app's quantities: quick add 1, bulk = Critical + Order now", () => {
  assert.equal(QUICK_ADD_QTY, 1);
  assert.deepEqual(BULK_TIERS, ["critical", "order_now"]);
  assert.equal(MAX_QTY, 100000); // backend po_cart.MAX_CART_QTY
});

test("canAddToVendor: a real vendor only, never Unassigned or a raw multi-vendor field", () => {
  assert.equal(canAddToVendor("Vdyas"), true);
  assert.equal(canAddToVendor("Unassigned"), false);
  assert.equal(canAddToVendor(" Unassigned "), false);
  assert.equal(canAddToVendor(""), false);
  assert.equal(canAddToVendor(null), false);
  assert.equal(canAddToVendor("Babco;JMR Distributors LLC"), false);
});

test("cartKeysFor / itemInCart look at that vendor's section only, ignoring case and spaces", () => {
  const keys = cartKeysFor(CART, " vdyas ");
  assert.equal(isInCart(keys, "rice", "RICE"), true);
  assert.equal(itemInCart(keys, { item_id: "dal", name: "DAL" }), false); // Babco's, not Vdyas's
  // A hand-added line (no item_id) counts by name.
  assert.equal(itemInCart(keys, { item_id: "leaves", name: "fresh curry leaves" }), true);
  assert.equal(itemInCart(cartKeysFor(CART, ""), { item_id: "rice", name: "RICE" }), false);
  assert.equal(itemInCart(null, { item_id: "rice" }), false); // cart not read yet
  assert.equal(cartKeysFor(undefined, "Vdyas").ids.size, 0);
});

test("suggestedQty is the Shortfall as the app shows it (formatReorderQty)", () => {
  assert.equal(suggestedQty(32), "32");
  assert.equal(suggestedQty(32.0), "32");
  assert.equal(suggestedQty(2.5), "2.5");
  assert.equal(suggestedQty(1.25), "1.25");
  assert.equal(suggestedQty(1.256), "1.26");
  assert.equal(suggestedQty(2.001), "2"); // no stray "2."
  assert.equal(suggestedQty(20.004), "20");
  assert.equal(suggestedQty(0.001), "");
  assert.equal(suggestedQty(0), "");
  assert.equal(suggestedQty(-3), "");
  assert.equal(suggestedQty(null), "");
  assert.equal(suggestedQty(undefined), "");
  assert.equal(suggestedQty(Number.NaN), "");
  assert.equal(suggestedQty(Infinity), "");
});

test("formatStock", () => {
  assert.equal(formatStock(0), "0");
  assert.equal(formatStock(-3), "-3");
  assert.equal(formatStock(12.5), "12.5");
  assert.equal(formatStock(1.256), "1.26");
  assert.equal(formatStock(4.001), "4");
  assert.equal(formatStock(null), "-");
  assert.equal(formatStock(undefined), "-");
  assert.equal(formatStock(Number.NaN), "-");
});

test("qtyProblem: positive, finite, at most the cart's cap", () => {
  assert.equal(qtyProblem("6"), null);
  assert.equal(qtyProblem("0.5"), null);
  assert.equal(qtyProblem(String(MAX_QTY)), null);
  assert.equal(qtyProblem(""), "Enter a quantity");
  assert.equal(qtyProblem(null), "Enter a quantity");
  assert.match(qtyProblem("0"), /more than 0/);
  assert.match(qtyProblem("-2"), /more than 0/);
  assert.match(qtyProblem("abc"), /more than 0/);
  assert.match(qtyProblem("100001"), /can't be more than 100,000/);
  assert.match(qtyProblem("1e308"), /can't be more than/);
  assert.match(qtyProblem("1e309"), /can't be more than/); // Infinity
  assert.match(qtyProblem("-1e309"), /more than 0/);
  assert.match(qtyProblem("8901058000012"), /can't be more than/); // a pasted barcode
});

test("bulkCandidates: Critical + Order now not in the cart; bulkAlreadyInCart counts the rest", () => {
  const keys = cartKeysFor(CART, "Vdyas");
  assert.deepEqual(
    bulkCandidates(ITEMS, keys).map((c) => c.item_id),
    ["maida", "dal"] // rice is in Vdyas's cart; check count / can wait / ordered never
  );
  assert.equal(bulkAlreadyInCart(ITEMS, keys), 1);
  // Cart not read (yet): nothing is known to be in it.
  assert.deepEqual(bulkCandidates(ITEMS, null).map((c) => c.item_id), ["maida", "rice", "dal"]);
  assert.equal(bulkAlreadyInCart(ITEMS, null), 0);
  // An "Ordered" vendor (every item tier "ordered") has nothing to add all.
  assert.deepEqual(bulkCandidates([item("tea", "ordered", 9)], keys), []);
  assert.deepEqual(bulkCandidates(undefined, keys), []);
});

test("selectedCandidates: ticked and not in the cart, in the report's order", () => {
  const keys = cartKeysFor(CART, "Vdyas");
  assert.deepEqual(
    selectedCandidates(ITEMS, new Set(["salt", "rice", "maida"]), keys).map((c) => c.item_id),
    ["maida", "salt"]
  );
  assert.deepEqual(selectedCandidates(ITEMS, ["ghee"], keys).map((c) => c.item_id), ["ghee"]);
  assert.deepEqual(selectedCandidates(ITEMS, undefined, keys), []);
});

test("reviewRows pre-fill the suggestion; canSubmitRows needs one valid ticked row", () => {
  const rows = reviewRows([item("maida", "critical", 32), item("loose", "order_now", 2.5)]);
  assert.deepEqual(
    rows.map((r) => [r.key, r.include, r.qty, r.suggested]),
    [
      ["maida", true, "32", "32"],
      ["loose", true, "2.5", "2.5"],
    ]
  );
  assert.equal(canSubmitRows(rows), true);
  // A bad qty on a ticked row blocks; on an unticked row it doesn't matter.
  const bad = rows.map((r, i) => (i === 1 ? { ...r, qty: "0" } : r));
  assert.equal(canSubmitRows(bad), false);
  const skipped = bad.map((r, i) => (i === 1 ? { ...r, include: false } : r));
  assert.equal(canSubmitRows(skipped), true);
  assert.deepEqual(includedRows(skipped).map((r) => r.key), ["maida"]);
  assert.equal(canSubmitRows(rows.map((r) => ({ ...r, include: false }))), false);
  assert.equal(canSubmitRows([]), false);
  // No usable shortfall: the box starts empty, so it must be filled in.
  const [empty] = reviewRows([item("x", "critical", 0)]);
  assert.equal(empty.qty, "");
  assert.equal(canSubmitRows([empty]), false);
});

test("cartItemsPayload: units with Case of 1, the server fills the vendor's code", () => {
  assert.deepEqual(
    cartItemsPayload([
      { item: item("maida", "critical", 32, { name: " Just Organik Maida Flour 2lb " }), qty: "32" },
      { item: item("loose", "order_now", 2.5, { supplier_item_id: "LS-1" }), qty: "2.5" },
      { item: { item_id: "noname", name: null }, qty: 1 },
    ]),
    [
      { item_id: "maida", name: "Just Organik Maida Flour 2lb", supplier_item_id: null, qty: 32, case_of: 1 },
      { item_id: "loose", name: "LOOSE", supplier_item_id: "LS-1", qty: 2.5, case_of: 1 },
      { item_id: "noname", name: "noname", supplier_item_id: null, qty: 1, case_of: 1 },
    ]
  );
});

test("splitPostable leaves out what's in the cart now; addOutcome sorts what was sent", () => {
  const keys = cartKeysFor(CART, "Vdyas");
  const rows = [
    { item: item("maida", "critical", 32), qty: "32" },
    { item: item("rice", "critical", 6, { name: "RICE" }), qty: "6" },
    { item: item("dal", "order_now", 12), qty: "12" },
  ];
  const { post, skipped } = splitPostable(rows, keys);
  assert.deepEqual(post.map((r) => r.item.item_id), ["maida", "dal"]);
  assert.deepEqual(skipped.map((r) => r.item.item_id), ["rice"]);
  const { inCart, missing } = addOutcome(rows, keys);
  assert.deepEqual(inCart.map((r) => r.item.item_id), ["rice"]);
  assert.deepEqual(missing.map((r) => r.item.item_id), ["maida", "dal"]);
});

test("mergeAddedLines shows a POST's lines as in the cart at once", () => {
  const added = [
    { id: 9, supplier: "Vdyas", item_id: "maida", name: "MAIDA", qty: 32 },
    { id: 10, supplier: "Jain Foods", item_id: "papad", name: "PAPAD", qty: 1 },
    { id: 1, supplier: "Vdyas", item_id: "rice", name: "RICE", qty: 6 }, // replaces line 1
  ];
  const cart = { suppliers: CART, can_remove: true };
  const merged = mergeAddedLines(cart, added);
  assert.equal(merged.can_remove, true);
  assert.equal(itemInCart(cartKeysFor(merged.suppliers, "Vdyas"), { item_id: "maida" }), true);
  assert.equal(itemInCart(cartKeysFor(merged.suppliers, "jain foods"), { item_id: "papad" }), true);
  const vdyas = merged.suppliers.find((s) => s.supplier === "Vdyas");
  assert.equal(vdyas.items.length, 3);
  assert.equal(vdyas.items.find((i) => i.id === 1).qty, 6);
  // The original isn't touched.
  assert.equal(CART[0].items.length, 2);
  assert.equal(CART[0].items[0].qty, 1);
  // No cart read yet: the lines alone.
  assert.equal(itemInCart(cartKeysFor(mergeAddedLines(null, added).suppliers, "Vdyas"), { item_id: "maida" }), true);
  assert.equal(mergeAddedLines(cart, []), cart);
});

test("isNetworkError: no HTTP status = no answer", () => {
  assert.equal(isNetworkError(new TypeError("Failed to fetch")), true);
  assert.equal(isNetworkError(Object.assign(new Error("Forbidden"), { status: 403 })), false);
  assert.equal(isNetworkError(null), false);
});

test("resultMessage says what reached which vendor's cart", () => {
  assert.deepEqual(resultMessage({ kind: "added", vendor: "Vdyas", count: 3 }), {
    tone: "success",
    text: "Added 3 items to Vdyas's cart.",
  });
  assert.equal(
    resultMessage({ kind: "added", vendor: "Vdyas", count: 1, single: { name: "Maida 2lb", qty: 1 } }).text,
    "Added Maida 2lb (qty 1) to Vdyas's cart."
  );
  assert.equal(
    resultMessage({ kind: "added", vendor: "Vdyas", count: 9, leftOut: 2, meanwhile: 1 }).text,
    "Added 9 items to Vdyas's cart. 2 items already in the cart were left out - change their qty in the cart. " +
      "1 item was added to the cart elsewhere meanwhile and left out, so its qty wasn't changed."
  );
  assert.match(resultMessage({ kind: "added", vendor: "Vdyas", count: 2, checked: true }).text, /the cart shows them\.\)$/);
  assert.match(
    resultMessage({ kind: "added", vendor: "Vdyas", count: 1, checked: true, single: { name: "Maida 2lb", qty: 1 } }).text,
    /^Added Maida 2lb \(qty 1\) to Vdyas's cart\. \(The server's answer didn't come back, but the cart shows it\.\)$/
  );

  const none = resultMessage({ kind: "none", vendor: "Vdyas", meanwhile: 1, single: { name: "Maida 2lb", qty: 1 } });
  assert.equal(none.tone, "info");
  assert.match(none.text, /^Nothing added: Maida 2lb was added to Vdyas's cart elsewhere meanwhile/);
  assert.match(resultMessage({ kind: "none", vendor: "Vdyas", meanwhile: 3 }).text, /all 3 items are already in Vdyas's cart/);
  assert.match(resultMessage({ kind: "none", vendor: "Vdyas", meanwhile: 1 }).text, /that item is already in Vdyas's cart/);

  assert.deepEqual(resultMessage({ kind: "failed", vendor: "Vdyas", reason: "Qty must be a number no bigger than 100,000." }), {
    tone: "error",
    text: "Couldn't add to Vdyas's cart: Qty must be a number no bigger than 100,000. Nothing was added.",
  });
  assert.equal(
    resultMessage({ kind: "partial", vendor: "Vdyas", count: 2, sent: 5, missing: 3, reason: "the connection dropped" }).text,
    "Only 2 of the 5 items reached Vdyas's cart (the connection dropped). The other 3 are not in the cart - add them again."
  );
  assert.match(resultMessage({ kind: "unknown", vendor: "Vdyas", sent: 1, reason: "x" }).text, /isn't known whether it got in/);
  assert.deepEqual(resultMessage({ kind: "demo", vendor: "Vdyas" }), {
    tone: "info",
    text: `${DEMO_READ_ONLY} Nothing was added to the cart.`,
  });
});

test("offersCartLink: Open cart only where the cart has something to look at", () => {
  assert.deepEqual(
    ["added", "none", "partial", "unknown", "failed", "demo"].map(offersCartLink),
    [true, true, true, true, false, false]
  );
});

test("plural", () => {
  assert.equal(plural(1, "item"), "1 item");
  assert.equal(plural(0, "item"), "0 items");
  assert.equal(plural(4, "item"), "4 items");
});
