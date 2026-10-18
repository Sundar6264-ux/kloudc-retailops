// node --test src/components/placedOrderSource.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { isOutsideOrder, outsideDetail, outsideLabel } from "./placedOrderSource.js";

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
