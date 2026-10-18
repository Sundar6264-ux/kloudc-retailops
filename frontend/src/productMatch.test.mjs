// node --test src/productMatch.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { codeMatchLabel, matchCode, rowCodeMatch, splitCodes } from "./productMatch.js";

const CASES = JSON.parse(readFileSync(new URL("./productMatchCases.json", import.meta.url), "utf8"));

test("shared cases (same as the backend and the app)", () => {
  const { item_id, supplier_item_ids, barcodes } = CASES.item;
  for (const c of CASES.cases) {
    assert.deepEqual(matchCode(c.q, item_id, supplier_item_ids, barcodes), c.expect, `q=${JSON.stringify(c.q)}`);
  }
});

test("splitCodes drops empty parts", () => {
  assert.deepEqual(splitCodes(" A;;B ; "), ["A", "B"]);
  assert.deepEqual(splitCodes(null), []);
});

test("rowCodeMatch reads the usual fields, nested getters and arrays", () => {
  const row = { item_id: "abc-123456", supplier_item_id: "R1", all_supplier_item_ids: "R1;X-99", barcode: "0042" };
  assert.deepEqual(rowCodeMatch(row, "x-99"), ["supplier_item_id", "X-99", true]);
  assert.deepEqual(rowCodeMatch(row, "42"), ["barcode", "0042", true]);
  assert.equal(rowCodeMatch(row, "zz"), null);
  const nested = { item_id: "i1", product: { supplier_item_id: "P-77", barcode: null } };
  const keys = { itemId: ["item_id"], supplier: [(r) => r.product?.supplier_item_id], barcode: [(r) => r.product?.barcode] };
  assert.deepEqual(rowCodeMatch(nested, "p-77", keys), ["supplier_item_id", "P-77", true]);
  // Several ids (store + Container row): an exact one wins.
  const pair = { store: "aaaa-111111", container: "bbbb-222222" };
  assert.deepEqual(rowCodeMatch(pair, "bbbb-222222", { itemId: ["store", "container"] }), [
    "item_id",
    "bbbb-222222",
    true,
  ]);
});

test("codeMatchLabel", () => {
  assert.equal(codeMatchLabel(["supplier_item_id", "2659M", true]), "SKU 2659M");
  assert.equal(codeMatchLabel(["barcode", "0186", false]), "Barcode 0186");
  assert.equal(codeMatchLabel(["item_id", "2d3d60fa-b1ec", true]), "Toast ID 2d3d60fa…");
  assert.equal(codeMatchLabel(null), null);
});
