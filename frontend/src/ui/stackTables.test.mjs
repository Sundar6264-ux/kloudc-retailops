import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cellLabels, columnLabels, STACK_CLASS } from "./stackTables.js";

test("header labels expand by colspan and trim whitespace", () => {
  assert.deepEqual(columnLabels([["Item", 1], [" On\n hand ", 1], ["Qty", 2], ["", 1]]), ["Item", "On hand", "Qty", "Qty", ""]);
});

test("each cell gets its column's label, colspans respected", () => {
  const labels = ["Item", "Vendor", "Qty", "Cost", "Date"];
  assert.deepEqual(cellLabels(labels, [1, 1, 1, 1, 1]), labels);
  assert.deepEqual(cellLabels(labels, [2, 1, 2]), ["Item", "Qty", "Cost"]);
  assert.deepEqual(cellLabels(labels, [5]), ["Item"]);
});

const WIDE = {
  "components/PlacedOrders.jsx": 1,
  "components/InvoiceSubmission.jsx": 2,
  "pages/TeamsPage.jsx": 2,
  "pages/UsersPage.jsx": 1,
  "components/Reconciliation.jsx": 4,
  "components/ItemsSold.jsx": 1,
  "components/InventoryReports.jsx": 2,
  "components/DeliveryReview.jsx": 2,
  "components/OpenOrders.jsx": 1,
  "pages/IssueLeadsPage.jsx": 1,
  "components/poSend/VendorContacts.jsx": 1,
  "pages/TeamModerationPage.jsx": 1,
};

test("the wide tables use the stacking pattern", () => {
  for (const [file, n] of Object.entries(WIDE)) {
    const src = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
    const count = (src.match(new RegExp(STACK_CLASS, "g")) || []).length;
    assert.ok(count >= n, `${file}: ${count} of ${n} tables stack on phones`);
  }
  const css = readFileSync(new URL("../styles/kc.css", import.meta.url), "utf8");
  assert.ok(css.includes(`table.${STACK_CLASS} td::before`) && css.includes("attr(data-label)"));
  const main = readFileSync(new URL("../main.jsx", import.meta.url), "utf8");
  assert.ok(main.includes("installTableStacking"));
});
