// node --test src/components/poDownload/poDownloadModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { PO_FORMATS, poExportPath, poFallbackName } from "./poDownloadModel.js";

test("the menu offers Excel, PDF and CSV in that order", () => {
  assert.deepEqual(
    PO_FORMATS.map((f) => [f.value, f.label]),
    [
      ["xlsx", "Excel"],
      ["pdf", "PDF"],
      ["csv", "CSV"],
    ],
  );
});

test("paths for a placed order, a sent PO and the cart", () => {
  assert.equal(poExportPath({ kind: "order", id: 7 }, "xlsx"), "/api/ordering/orders/7/export?format=xlsx");
  assert.equal(poExportPath({ kind: "send", id: 3 }, "csv"), "/api/ordering/po-sends/3/export?format=csv");
  assert.equal(
    poExportPath({ kind: "cart", supplier: "Raja Foods & Co" }, "pdf"),
    "/api/ordering/cart/export?supplier=Raja%20Foods%20%26%20Co&format=pdf",
  );
});

test("an unknown format or target is refused in plain words", () => {
  assert.throws(() => poExportPath({ kind: "order", id: 1 }, "docx"), /Excel, PDF or CSV/);
  assert.throws(() => poExportPath({ kind: "cart" }, "pdf"), /Nothing to download/);
});

test("fallback file name matches the server's PO-<Vendor>-<date>", () => {
  assert.equal(poFallbackName("Raja Foods & Co.", "xlsx", "2026-10-22"), "PO-Raja-Foods-Co-2026-10-22.xlsx");
  assert.equal(poFallbackName("", "csv", "2026-10-22"), "PO-Vendor-2026-10-22.csv");
});
