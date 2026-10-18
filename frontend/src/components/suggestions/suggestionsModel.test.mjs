// node --test src/components/suggestions/suggestionsModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  closeSummary,
  confirmPayload,
  evidenceLabel,
  formatIsoDay,
  formatStoreDay,
  formatUnits,
  initialQuantities,
  latestOnly,
  parseLeadDays,
  parseQty,
  plural,
  qtyDiffers,
  weekdayList,
} from "./suggestionsModel.js";

const group = {
  items: [
    { item_id: "a", name: "Rice", preselected: true, suggested_qty: 24 },
    { item_id: "b", name: "Dal", preselected: false, suggested_qty: 6 },
    { item_id: "c", name: "Flowers", preselected: false, suggested_qty: null },
    { item_id: "d", name: "Zero", preselected: true, suggested_qty: 0 },
  ],
};

test("initial quantities: only preselected lines with a positive suggestion", () => {
  assert.deepEqual(initialQuantities(group), { a: "24", b: "", c: "", d: "" });
  assert.deepEqual(initialQuantities(null), {});
});

test("parseQty takes positive finite numbers only", () => {
  assert.equal(parseQty("3"), 3);
  assert.equal(parseQty(" 2.5 "), 2.5);
  for (const bad of ["", " ", null, undefined, "0", "-1", "abc", "1e309", "Infinity"]) {
    assert.equal(parseQty(bad), null, String(bad));
  }
});

test("qtyDiffers flags an edit away from the suggestion", () => {
  assert.equal(qtyDiffers(group.items[0], "24"), false);
  assert.equal(qtyDiffers(group.items[0], "20"), true);
  assert.equal(qtyDiffers(group.items[0], ""), false);
  assert.equal(qtyDiffers(group.items[2], "5"), false); // nothing suggested
});

test("confirm payload matches the manual review's body", () => {
  assert.deepEqual(confirmPayload(group, { a: "24", b: "", c: "3", d: "0" }), [
    { item_id: "a", item_name: "Rice", quantity_received: 24 },
    { item_id: "c", item_name: "Flowers", quantity_received: 3 },
  ]);
  assert.deepEqual(confirmPayload({}, {}), []);
});

test("labels and formatting", () => {
  assert.equal(evidenceLabel("toast_received"), "Received in Toast");
  assert.equal(evidenceLabel("stock_rise"), "Stock rose");
  assert.equal(formatIsoDay("2026-10-11"), "Sun, Oct 11");
  assert.equal(formatIsoDay(""), "");
  // 1:30 AM UTC on Oct 11 is still Oct 10 in the store.
  assert.equal(formatStoreDay("2026-10-11T01:30:00+00:00"), "Sat, Oct 10");
  assert.equal(formatStoreDay(null), "");
  assert.equal(formatUnits(1234.567), "1,234.57");
  assert.equal(formatUnits(null), "-");
  assert.equal(plural(1, "item"), "1 item");
  assert.equal(plural(2, "item"), "2 items");
  assert.equal(plural(2, "entry", "entries"), "2 entries");
});

test("weekday list and lead days", () => {
  assert.equal(weekdayList([3]), "Thu");
  assert.equal(weekdayList([3, 0, 3, 9, -1]), "Mon/Thu");
  assert.equal(weekdayList(null), "");
  assert.equal(parseLeadDays("1"), 1);
  assert.equal(parseLeadDays("0"), 0);
  for (const bad of ["", "-1", "1.5", "15", "x"]) assert.equal(parseLeadDays(bad), null, bad);
});

test("close summary", () => {
  assert.equal(closeSummary([]), null);
  assert.equal(closeSummary([{ close_suggestion: { key: "1" } }, { close_suggestion: null }]), "1 invoice looks closed in Toast already");
  assert.equal(
    closeSummary([{ close_suggestion: { key: "1" } }, { close_suggestion: { key: "2" } }]),
    "2 invoices look closed in Toast already",
  );
});

test("latestOnly: an older overlapping load never wins", () => {
  const loads = latestOnly();
  const dismissReload = loads.start();
  const undoReload = loads.start();
  assert.equal(loads.isLatest(dismissReload), false);
  assert.equal(loads.isLatest(undoReload), true);
  const other = latestOnly();
  assert.equal(other.isLatest(other.start()), true, "each list keeps its own count");
});
