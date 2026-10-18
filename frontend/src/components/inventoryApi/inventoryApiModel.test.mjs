// node --test src/components/inventoryApi/inventoryApiModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  historyChange,
  missedCells,
  nightLabel,
  pctText,
  sourceText,
  streakSummary,
  thresholdsText,
} from "./inventoryApiModel.js";

const T = { nights: 7, qty_pct: 99, price_pct: 99, barcode_pct: 99, category_pct: 100, max_unexplained_qty: 10 };

test("nightLabel is the store date, no time zone shift", () => {
  assert.equal(nightLabel("2026-10-12"), "Mon Oct 12");
  assert.equal(nightLabel("2026-11-01"), "Sun Nov 1");
  assert.equal(nightLabel(null), "-");
  assert.equal(nightLabel("junk"), "junk");
});

test("pctText", () => {
  assert.equal(pctText(99.5), "99.5%");
  assert.equal(pctText(100), "100%");
  assert.equal(pctText(null), "-");
});

test("missedCells marks exactly the misses, boundaries included", () => {
  const ok = { qty_pct: 99, price_pct: 99, barcode_pct: 99, category_pct: 100, qty_unexplained: 10 };
  assert.deepEqual([...missedCells(ok, T)], []);
  const bad = { qty_pct: 98.99, price_pct: 100, barcode_pct: null, category_pct: 99.98, qty_unexplained: 11 };
  assert.deepEqual([...missedCells(bad, T)].sort(), ["barcode", "category", "qty", "unexplained"]);
  assert.deepEqual([...missedCells(null, T)], []);
});

test("streakSummary", () => {
  assert.equal(streakSummary({ ready: false, streak: 0, needed: 7, nights: [] }), "No nightly comparison yet - 7 good nights in a row are needed.");
  assert.equal(streakSummary({ ready: false, streak: 6, needed: 7, nights: [{}] }), "6 of 7 nights in a row so far - 1 more good night needed.");
  assert.equal(streakSummary({ ready: false, streak: 3, needed: 7, nights: [{}] }), "3 of 7 nights in a row so far - 4 more good nights needed.");
  assert.match(streakSummary({ ready: true, streak: 7, needed: 7, switch_on: false, nights: [{}] }), /^Ready: 7 nights/);
  assert.match(streakSummary({ ready: true, streak: 9, needed: 7, switch_on: true, nights: [{}] }), /and it is on/);
});

test("historyChange says where the old value came from", () => {
  assert.equal(historyChange({ on: true, previous_on: false, previous_source: "page" }), "Turned on");
  assert.equal(historyChange({ on: false, previous_on: true, previous_source: "env" }), "Turned off (was on from the server setting)");
  assert.equal(
    historyChange({ on: false, previous_on: false, previous_source: "env" }),
    "Turned off (set here; was off from the server setting)",
  );
});

test("sourceText", () => {
  const when = (iso) => `at ${iso}`;
  assert.equal(sourceText({ source: "page", changed_by: "boss", changed_at: "T" }, when), "Set here by boss, at T.");
  assert.match(sourceText({ source: "env", env_default: false }, when), /INVENTORY_API_SNAPSHOTS is off/);
});

test("thresholdsText quotes the numbers", () => {
  const text = thresholdsText(T);
  assert.match(text, /99% or more of counted items/);
  assert.match(text, /category 100%/);
  assert.match(text, /at most 10 on-hand differences/);
  assert.equal(thresholdsText(null), "");
});
