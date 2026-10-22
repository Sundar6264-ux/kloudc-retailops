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

// --- Cases and the one-tap Add ---------------------------------------------
import { caseLabel, caseSizeOf, qtyWords, quickAddLine, stepQty, switchToCases } from "./projectionQtyModel.js";

test("case size: cart line decides, else suggestion, else the item's", () => {
  const maggi = { item_id: "m", case_size: 24, suggested_order: { qty: 1, case_of: 24 } };
  assert.equal(caseSizeOf(maggi, null), 24);
  assert.equal(caseSizeOf(maggi, { case_of: 1 }), null); // in the cart in units
  assert.equal(caseSizeOf(maggi, { case_of: 12 }), 12);
  assert.equal(caseSizeOf({ case_size: 6 }, null), 6);
  assert.equal(caseSizeOf({ case_size: 1 }, null), null);
  assert.equal(caseSizeOf({}, null), null);
});

test("case label and words", () => {
  assert.equal(caseLabel(2, 24), "1 case = 24 units · 48 units");
  assert.equal(caseLabel(1, 24), "1 case = 24 units · 24 units");
  assert.equal(caseLabel(5, 1), null);
  assert.equal(caseLabel(5, null), null);
  assert.equal(qtyWords(2, 24), "2 cases (48 units)");
  assert.equal(qtyWords(1, 24), "1 case (24 units)");
  assert.equal(qtyWords(5, null), "5");
});

test("one-tap Add sends the suggested cases (owner's Maggi: need 5 -> 1 case of 24)", () => {
  const maggi = { item_id: "m", name: "Maggi 300g", supplier_item_id: "MG", case_size: 24,
    suggested_order: { qty: 1, case_of: 24 }, projections: { 21: { need_to_order: 5 } } };
  assert.deepEqual(quickAddLine(maggi, 21), { item_id: "m", name: "Maggi 300g", supplier_item_id: "MG", qty: 1, case_of: 24 });
  // Units item.
  const rice = { item_id: "r", name: "Rice", suggested_order: { qty: 16, case_of: null } };
  assert.deepEqual(quickAddLine(rice, 21), { item_id: "r", name: "Rice", supplier_item_id: null, qty: 16 });
  // Enough stock: Add still adds 1.
  assert.equal(quickAddLine({ item_id: "x", suggested_order: { qty: 0 } }, 21).qty, 1);
  // Older server: need in cases from the item's case size.
  const old = { item_id: "o", case_size: 24, projections: { 21: { need_to_order: 30 } } };
  assert.deepEqual(quickAddLine(old, 21), { item_id: "o", name: undefined, supplier_item_id: null, qty: 2, case_of: 24 });
  assert.equal(quickAddLine({ item_id: "z", projections: { 21: { need_to_order: 4.2 } } }, 21).qty, 5);
});

test("stepper never goes below 1", () => {
  assert.equal(stepQty(1, -1), 1);
  assert.equal(stepQty(2, -1), 1);
  assert.equal(stepQty(2, 1), 3);
  assert.equal(stepQty("x", 1), 1);
  assert.equal(stepQty(MAX_QTY, 1), MAX_QTY);
});

test("switch a units line to cases", () => {
  assert.deepEqual(switchToCases({ qty: 16, case_of: 1, item_case_size: 24 }), { case_of: 24, qty: 1 });
  assert.deepEqual(switchToCases({ qty: 48, case_of: 1, item_case_size: 24 }), { case_of: 24, qty: 2 });
  assert.equal(switchToCases({ qty: 2, case_of: 24, item_case_size: 24 }), null);
  assert.equal(switchToCases({ qty: 2, case_of: 1, item_case_size: null }), null);
});
