// node --test src/components/projectionQty/projectionQtyModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_QTY,
  addMessage,
  buildAddItems,
  cartContext,
  contextKey,
  initialQtyText,
  parseQty,
  qtyText,
  updateFor,
  useSuggestedQty,
} from "./projectionQtyModel.js";

const item = (id, qty, extra = {}) => ({
  item_id: id,
  name: `Item ${id}`,
  supplier_item_id: `SKU-${id}`,
  suggested_order: qty === undefined ? undefined : { qty, basis: `suggested ${qty}`, case_of: null, ...extra },
});

test("parseQty", () => {
  assert.deepEqual(parseQty("12"), { ok: true, value: 12 });
  assert.deepEqual(parseQty(" 2.5 "), { ok: true, value: 2.5 });
  assert.deepEqual(parseQty("0"), { ok: true, value: 0 });
  assert.equal(parseQty("").ok, false);
  assert.equal(parseQty("abc").ok, false);
  assert.equal(parseQty("-1").ok, false);
  assert.equal(parseQty("1e308").ok, false);
  assert.equal(parseQty(String(MAX_QTY + 1)).ok, false);
  assert.equal(parseQty(null).ok, false);
});

test("the box starts at the cart qty for an in-cart item, else the suggestion", () => {
  assert.equal(initialQtyText(item("a", 10), null), "10");
  assert.equal(initialQtyText(item("a", 10), { id: 1, qty: 3 }), "3");
  assert.equal(initialQtyText(item("a"), null), ""); // older server: no suggestion
  assert.equal(qtyText(item("a", 10), { a: "7" }, null), "7");
  assert.equal(qtyText(item("a", 10), { a: "" }, null), ""); // a cleared box stays cleared
});

test("buildAddItems sends typed quantities, never 1 by default", () => {
  const items = [item("a", 10), item("b", 0), item("c", 4, { case_of: 12 }), item("d", 5), item("e", 2)];
  const r = buildAddItems(items, ["a", "b", "c", "d", "x"], new Set(["d"]), { a: "8" });
  assert.deepEqual(r.items, [
    { item_id: "a", name: "Item a", supplier_item_id: "SKU-a", qty: 8 },
    { item_id: "c", name: "Item c", supplier_item_id: "SKU-c", qty: 4, case_of: 12 },
  ]);
  assert.deepEqual(r.skippedZero, ["Item b"]);
  assert.deepEqual(r.alreadyInCart, ["d"]); // never re-sent: it would replace the cart qty
  assert.deepEqual(r.invalid, []);
});

test("buildAddItems reports bad boxes by name", () => {
  const r = buildAddItems([item("a", 10), item("b")], ["a", "b"], new Set(), { a: "lots" });
  assert.deepEqual(r.invalid, ["Item a", "Item b"]);
  assert.equal(r.items.length, 0);
  // A typed qty for a zero suggestion is sent.
  const z = buildAddItems([item("b", 0)], ["b"], new Set(), { b: "3" });
  assert.equal(z.items[0].qty, 3);
});

test("updateFor only for a changed, valid qty above 0", () => {
  const line = { id: 9, qty: 3 };
  assert.equal(updateFor(item("a", 10), {}, line), null); // box = cart qty
  assert.deepEqual(updateFor(item("a", 10), { a: "10" }, line), { id: 9, qty: 10 });
  assert.equal(updateFor(item("a", 10), { a: "0" }, line), null); // removal is admin-only, on the cart page
  assert.equal(updateFor(item("a", 10), { a: "x" }, line), null);
  assert.equal(updateFor(item("a", 10), { a: "10" }, null), null);
});

test("addMessage", () => {
  const msg = addMessage("Chetak", { items: [1, 2], skippedZero: ["B"], alreadyInCart: ["d"], invalid: [] });
  assert.equal(
    msg,
    "Added 2 item(s) to the cart for Chetak. Skipped 1 with qty 0 (enough stock): B. 1 already in the cart - change those with Update."
  );
});

test("cart context + use suggested", () => {
  assert.equal(cartContext(null), null);
  assert.deepEqual(cartContext({ compact: "~14/week · suggested 10 · on hand 6", basis: "x", qty: 10 }), {
    text: "~14/week · suggested 10 · on hand 6",
    basis: "x",
    qty: 10,
  });
  assert.equal(useSuggestedQty({ qty: 10 }, 3), 10);
  assert.equal(useSuggestedQty({ qty: 10 }, 10), null);
  assert.equal(useSuggestedQty({ qty: 0 }, 3), null);
  assert.equal(useSuggestedQty(null, 3), null);
});

test("contextKey changes with lines, qty and Case of only", () => {
  const cart = (qty, caseOf = 1, name = "A") => ({
    suppliers: [{ supplier: "V", items: [{ id: 2, item_id: "b", qty: 1, case_of: 1, name: "B" }, { id: 1, item_id: "a", qty, case_of: caseOf, name }] }],
  });
  assert.equal(contextKey(cart(1)), contextKey(cart(1, 1, "renamed")));
  assert.notEqual(contextKey(cart(1)), contextKey(cart(2)));
  assert.notEqual(contextKey(cart(1)), contextKey(cart(1, 6)));
  assert.equal(contextKey(null), "[]");
});
