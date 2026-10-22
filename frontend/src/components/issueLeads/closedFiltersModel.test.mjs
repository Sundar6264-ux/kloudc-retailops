import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EMPTY_CLOSED_FILTERS,
  applyClosedFilters,
  closedCategoryOptions,
  closedFilterCount,
  closedPersonOptions,
  closedReportTileCount,
  closedReportTileHint,
  closedShowing,
  personChips,
} from "./closedFiltersModel.js";

const rows = [
  { issue_key: "1", closed_by: "Ravi", category: "Snacks" },
  { issue_key: "2", closed_by: "ravi ", category: "Rice" },
  { issue_key: "3", closed_by: "Anu", category: "Snacks" },
  { issue_key: "4", closed_by: null, category: "" },
];

test("employee and category options count what each would leave", () => {
  assert.deepEqual(closedPersonOptions(rows), [
    { name: "Anu", count: 1 },
    { name: "Ravi", count: 2 },
    { name: "Unknown", count: 1 },
  ]);
  assert.deepEqual(closedCategoryOptions(rows, { closedBy: "Anu", category: "" }), [{ name: "Snacks", count: 1 }]);
  assert.deepEqual(closedPersonOptions(rows, { closedBy: "", category: "Snacks" }).map((o) => o.name), ["Anu", "Ravi"]);
});

test("filters match ignoring case, with Unknown / No category for blanks", () => {
  assert.deepEqual(applyClosedFilters(rows, { closedBy: "RAVI", category: "" }).map((r) => r.issue_key), ["1", "2"]);
  assert.deepEqual(applyClosedFilters(rows, { closedBy: "Ravi", category: "snacks" }).map((r) => r.issue_key), ["1"]);
  assert.deepEqual(applyClosedFilters(rows, { closedBy: "Unknown", category: "No category" }).map((r) => r.issue_key), ["4"]);
  assert.equal(applyClosedFilters(rows, EMPTY_CLOSED_FILTERS), rows);
  assert.equal(closedFilterCount({ closedBy: "Anu", category: "Rice" }), 2);
  assert.equal(closedFilterCount(EMPTY_CLOSED_FILTERS), 0);
});

test("per-employee counts add up to the filtered total", () => {
  for (const person of closedPersonOptions(rows)) {
    assert.equal(applyClosedFilters(rows, { closedBy: person.name, category: "" }).length >= person.count, true);
  }
  const total = closedPersonOptions(rows).reduce((n, o) => n + o.count, 0);
  assert.equal(total, rows.length);
});

test("which filter is on, in plain words", () => {
  assert.equal(closedShowing(EMPTY_CLOSED_FILTERS), "Everyone · All categories");
  assert.equal(closedShowing({ closedBy: "Ravi", category: "Snacks" }), "Closed by Ravi · Snacks");
});

test("the Closed report tab shows the report's own 30-day count", () => {
  const summary = { a: { closed_count: 3 }, b: { closed_count: 4 } };
  assert.equal(closedReportTileCount(summary, { days: 30, total: 12 }), 12);
  assert.equal(closedReportTileCount(summary, { days: 30, total: 0 }), 0);
  assert.equal(closedReportTileCount(summary, undefined), 7); // older server
  assert.equal(closedReportTileCount(null, undefined), null);
  assert.equal(closedReportTileHint({ days: 30, total: 12 }), "Last 30 days · everyone");
});

test("person chips sort most first and add up", () => {
  const chips = personChips({ Anu: 2, Ravi: 5, Unknown: 1 });
  assert.deepEqual(chips.map((c) => c.name), ["Ravi", "Anu", "Unknown"]);
  assert.equal(chips.reduce((n, c) => n + c.count, 0), 8);
});
