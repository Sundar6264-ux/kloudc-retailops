// Setup > Item vendors view model (docs/WEBSITE_READY_CONTRACT.md D4).
//   node --test src/settings/itemVendorsModel.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_VENDORS,
  SHOW_KEY,
  addRow,
  editorRows,
  importSummary,
  itemVendorsErrorMessage,
  moveRow,
  previewRows,
  readShow,
  removeRow,
  rowsChanged,
  validateRows,
  vendorSummary,
} from "./itemVendorsModel.js";

const V = [
  { name: "Second Source", item_code: null, rank: 2 },
  { name: "Main Street Wholesale", item_code: "BR-10", rank: 1 },
];

test("summary and rows keep rank order", () => {
  assert.equal(vendorSummary([]), "No vendor");
  assert.equal(vendorSummary([V[1]]), "Main Street Wholesale");
  const rows = editorRows(V);
  assert.deepEqual(rows.map((r) => [r.name, r.item_code]), [["Main Street Wholesale", "BR-10"], ["Second Source", ""]]);
  assert.equal(vendorSummary(V.slice().sort((a, b) => a.rank - b.rank)), "Main Street Wholesale +1");
  assert.ok(rows[0].key !== rows[1].key);
});

test("add, remove and move rows", () => {
  let rows = editorRows(V);
  rows = addRow(rows);
  assert.equal(rows.length, 3);
  rows = moveRow(rows, 1, -1);
  assert.equal(rows[0].name, "Second Source");
  assert.equal(moveRow(rows, 0, -1), rows, "can't move the first row up");
  assert.equal(moveRow(rows, 2, 1), rows, "can't move the last row down");
  rows = removeRow(rows, 2);
  assert.deepEqual(rows.map((r) => r.name), ["Second Source", "Main Street Wholesale"]);
});

test("validation matches the server's rules", () => {
  const ok = validateRows([{ name: "  Main   Street ", item_code: " A1 " }, { name: "", item_code: "" }]);
  assert.deepEqual(ok, { errors: {}, vendors: [{ name: "Main Street", item_code: "A1" }] });
  const bad = validateRows([
    { name: "", item_code: "X" },
    { name: "A;B", item_code: "" },
    { name: "Acme", item_code: "" },
    { name: "ACME", item_code: "" },
    { name: "x".repeat(81), item_code: "" },
    { name: "Code", item_code: "a;b" },
  ]);
  assert.deepEqual(Object.keys(bad.errors), ["0", "1", "3", "4", "5"]);
  assert.match(bad.errors[3], /listed twice/);
  const many = validateRows(Array.from({ length: MAX_VENDORS + 1 }, (_, i) => ({ name: `V${i}`, item_code: "" })));
  assert.match(many.errors.list, /at most 10/);
});

test("changed only when names, codes or order differ", () => {
  const rows = editorRows(V);
  assert.equal(rowsChanged(rows, V), false);
  assert.equal(rowsChanged(moveRow(rows, 0, 1), V), true);
  assert.equal(rowsChanged(addRow(rows), V), false, "an empty new row is not a change");
  assert.equal(rowsChanged([], []), false);
});

test("remembered filter", () => {
  const store = new Map([[SHOW_KEY, "missing"]]);
  assert.equal(readShow({ getItem: (k) => store.get(k) }), "missing");
  assert.equal(readShow({ getItem: () => "bogus" }), "all");
  assert.equal(readShow({ getItem: () => { throw new Error("blocked"); } }), "all");
});

test("import summary and preview rows", () => {
  const preview = { row_count: 9, errors: 2, items_to_change: 3, applied: false,
    rows: [{ line: 2 }, { line: 3, error: "No item" }, { line: 4, note: "No vendor" }, { line: 5, error: "Rank" }] };
  assert.equal(importSummary(preview), "9 rows · 2 with problems (skipped) · 3 items will change");
  assert.equal(importSummary({ row_count: 1, errors: 0, items_to_change: 1, applied: true }), "1 row · 1 item updated");
  assert.equal(importSummary({ row_count: 2, errors: 0, items_to_change: 0 }), "2 rows · nothing to change");
  assert.deepEqual(previewRows(preview).map((r) => r.line), [3, 5, 4]);
});

test("error messages", () => {
  const err = (status, body) => Object.assign(new Error("x"), { status, body });
  assert.match(itemVendorsErrorMessage(new TypeError("fetch")), /Can't reach/);
  assert.match(itemVendorsErrorMessage(err(403, { detail: "Admin privileges required" })), /store admin/);
  assert.match(itemVendorsErrorMessage(err(403, { detail: "The demo account can look around" })), /demo/);
  assert.match(itemVendorsErrorMessage(err(413, {})), /5 MB/);
  assert.equal(itemVendorsErrorMessage(err(400, { detail: "Upload a .csv file" })), "Upload a .csv file");
});
