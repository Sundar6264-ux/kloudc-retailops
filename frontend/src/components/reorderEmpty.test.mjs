// node --test src/components/reorderEmpty.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { NOTHING_TO_REORDER, NO_SALES_YET, NO_STOCK_YET, reorderEmptyText } from "./reorderEmpty.js";

test("a new store with no sales says so", () => {
  assert.equal(reorderEmptyText({ has_sales_data: false, has_stock_data: false }), NO_SALES_YET);
  assert.equal(reorderEmptyText({ has_sales_data: false, has_stock_data: true }), NO_SALES_YET);
});

test("sales but no stock counts yet", () => {
  assert.equal(reorderEmptyText({ has_sales_data: true, has_stock_data: false }), NO_STOCK_YET);
});

test("a store with data and an empty list: nothing needs reordering", () => {
  assert.equal(reorderEmptyText({ has_sales_data: true, has_stock_data: true }), NOTHING_TO_REORDER);
});

test("an older server without the flags keeps the plain wording", () => {
  assert.equal(reorderEmptyText({}), NOTHING_TO_REORDER);
  assert.equal(reorderEmptyText(null), NOTHING_TO_REORDER);
});

test("a search with no match names the search", () => {
  assert.equal(reorderEmptyText({ has_sales_data: false }, " rice "), 'No items match "rice".');
});
