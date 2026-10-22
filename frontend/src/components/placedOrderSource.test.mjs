// node --test src/components/placedOrderSource.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { isOutsideOrder, orderQtyText, outsideDetail, outsideLabel } from "./placedOrderSource.js";

const order = (extra = {}) => ({ id: 3, supplier: "Chetak", status: "placed", item_count: 0, items: [], ...extra });

test("only a source outside order is an outside order (older servers send no source)", () => {
  assert.equal(isOutsideOrder(order({ source: "outside" })), true);
  assert.equal(isOutsideOrder(order({ source: "app" })), false);
  assert.equal(isOutsideOrder(order()), false);
  assert.equal(isOutsideOrder(null), false);
  assert.equal(outsideLabel(order()), null);
});

test("label: the server's source_label, else the default wording", () => {
  assert.equal(outsideLabel(order({ source: "outside", source_label: "Placed outside the app" })), "Placed outside the app");
  assert.equal(outsideLabel(order({ source: "outside", source_label: null })), "Placed outside the app");
});

test("detail names who, the delivery and the note", () => {
  const d = outsideDetail(order({ source: "outside", placed_by_name: "Morgan", for_delivery_date: "2026-10-15", note: "  Rep took it Tue  " }));
  assert.equal(d.text, 'Marked "Order already placed" by Morgan for the Thu Oct 15 delivery - the order went in outside the app, so no items were recorded here.');
  assert.equal(d.note, "Rep took it Tue");
  const plain = outsideDetail(order({ source: "outside" }));
  assert.match(plain.text, /^Marked "Order already placed" - the order went in outside the app/);
  assert.equal(plain.note, null);
  assert.equal(outsideDetail(order({ source: "app" })), null);
});

test("a placed line ordered in cases shows cases and units; units stay a number", async () => {
  const { lineQtyText } = await import("./placedOrderSource.js");
  assert.equal(lineQtyText({ qty: 30, case_size: 12, qty_text: "30 cases (360 units)" }), "30 cases (360 units)");
  assert.equal(lineQtyText({ qty: 5, case_size: null, qty_text: "5" }, (n) => `${n}.0`), "5.0");
  assert.equal(lineQtyText({ qty: 2 }, (n) => `#${n}`), "#2"); // an older server: no qty_text
});

test("Total qty keeps cases and units apart, like the cart header", () => {
  const mixed = order({ total_qty: 29, items: [
    { name: "Noodles", qty: 6, case_size: 24 },
    { name: "Rice", qty: 5, case_of: 12 },
    { name: "Ghee", qty: 10, case_size: null },
    { name: "Salt", qty: 8 },
  ] });
  assert.equal(orderQtyText(mixed), "11 cases + 18 units");
  assert.equal(orderQtyText(order({ items: [{ qty: 1, case_size: 6 }] })), "1 case");
  assert.equal(orderQtyText(order({ items: [{ qty: 3, case_of: 1 }, { qty: 1 }] })), "4 units");
  assert.equal(orderQtyText(order({ items: [{ qty: 1 }] })), "1 unit");
  // No lines sent (an older server): the server's number as before.
  assert.equal(orderQtyText(order({ total_qty: 7, items: [] })), "7");
});
