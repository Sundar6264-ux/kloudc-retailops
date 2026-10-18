import { test } from "node:test";
import assert from "node:assert/strict";
import {
  LEAD_NONE,
  TASK_ASSIGNED,
  TASK_NOT_ASSIGNED,
  activeFilterCount,
  applyFilters,
  buildSaveBody,
  changeCount,
  groupBySection,
  hasSections,
  leadCount,
  leadFromSearch,
  leadLine,
  leadOptions,
  pickerValue,
  sectionOptions,
  vendorOptions,
  vendorsOf,
} from "./issueLeadsModel.js";

const A = { user_id: 1, name: "Amandeep", via: "section", via_name: "Beverages" };
const S = { user_id: 2, name: "Sai Durga", via: "vendor", via_name: "Chetak" };
const rows = [
  { issue_key: "a", name: "Coke", po_section: "Beverages", supplier: "Best Foods", lead: A, lead_task: { task_id: 9 } },
  { issue_key: "b", name: "Rice", po_section: "Rice", product: { supplier: "Chetak;Raja Foods" }, lead: S, lead_task: null },
  { issue_key: "c", name: "Odd", po_section: "Other", supplier: "", lead: null, lead_task: null },
  { issue_key: "d", name: "Gone", po_section: null, lead: null },
];

test("vendors split on ; from product or row, variants included", () => {
  assert.deepEqual(vendorsOf(rows[1]), ["Chetak", "Raja Foods"]);
  assert.deepEqual(vendorsOf({ supplier: "X", variants: [{ product: { supplier: "Y;X" } }] }), ["X", "Y"]);
});

test("section options: alphabetical, Other then No section last", () => {
  assert.deepEqual(sectionOptions(rows).map((o) => o.name), ["Beverages", "Rice", "Other", "No section"]);
});

test("vendor and lead options", () => {
  assert.deepEqual(vendorOptions(rows).map((v) => v.name), ["Best Foods", "Chetak", "Raja Foods"]);
  const { leads, none } = leadOptions(rows, [{ user_id: 3, name: "Harshitha" }]);
  assert.deepEqual(leads.map((l) => [l.name, l.count]), [["Amandeep", 1], ["Harshitha", 0], ["Sai Durga", 1]]);
  assert.equal(none, 2);
});

test("filters", () => {
  assert.deepEqual(applyFilters(rows, { sections: ["Rice", "Other"] }).map((r) => r.issue_key), ["b", "c"]);
  assert.deepEqual(applyFilters(rows, { vendor: "raja foods" }).map((r) => r.issue_key), ["b"]);
  assert.deepEqual(applyFilters(rows, { lead: "1" }).map((r) => r.issue_key), ["a"]);
  assert.deepEqual(applyFilters(rows, { lead: LEAD_NONE }).map((r) => r.issue_key), ["c", "d"]);
  assert.deepEqual(applyFilters(rows, { task: TASK_ASSIGNED }).map((r) => r.issue_key), ["a"]);
  assert.deepEqual(applyFilters(rows, { task: TASK_NOT_ASSIGNED }).map((r) => r.issue_key), ["b", "c", "d"]);
  assert.equal(activeFilterCount({ sections: ["x"], vendor: "", lead: "1", task: "" }), 2);
  assert.equal(applyFilters(rows, {}).length, 4);
});

test("group by section keeps sort order and caps per group", () => {
  const many = Array.from({ length: 25 }, (_, i) => ({ issue_key: String(i), po_section: i % 2 ? "B" : "A" }));
  const { groups } = groupBySection(many, { perGroupLimit: 5, groupLimits: { B: 20 } });
  assert.deepEqual(groups.map((g) => [g.section, g.items.length, g.totalInGroup]), [["A", 5, 13], ["B", 12, 12]]);
  assert.deepEqual(groups[0].items.slice(0, 2).map((r) => r.issue_key), ["0", "2"]);
});

test("old server rows have no sections", () => {
  assert.equal(hasSections([{ name: "x" }]), false);
  assert.equal(hasSections(rows), true);
});

test("lead from the task link", () => {
  assert.equal(leadFromSearch("?lead=12"), "12");
  assert.equal(leadFromSearch("?lead=none"), "none");
  assert.equal(leadFromSearch("?lead=<script>"), "");
  assert.equal(leadFromSearch(""), "");
});

test("lead line and tile counts", () => {
  assert.equal(leadLine(A), "Lead: Amandeep");
  assert.equal(leadLine(S), "Lead: Sai Durga (vendor Chetak)");
  assert.equal(leadLine(null), "No lead");
  const item = { open_count: 9, open_by_lead: { 1: 4 }, open_no_lead: 5 };
  assert.equal(leadCount(item, "1"), 4);
  assert.equal(leadCount(item, "2"), 0);
  assert.equal(leadCount(item, LEAD_NONE), 5);
  assert.equal(leadCount(item, ""), 9);
  assert.equal(leadCount({ open_count: 3 }, "1"), null);
});

test("admin page: picker shows saved, else proposal; save body", () => {
  const page = {
    sections: [
      { section: "Rice", saved: false, lead: null, proposal: { user_id: 2 } },
      { section: "Beverages", saved: true, lead: { user_id: 1 }, proposal: { user_id: 1 } },
      { section: "Dairy", saved: false, lead: null, proposal: null },
    ],
    vendors: [{ vendor: "Chetak", saved: true, lead: null, proposal: { user_id: 2 } }],
    fallback: { saved: false, lead: null, proposal: { user_id: 5 } },
  };
  assert.equal(pickerValue(page.sections[0], {}, "section:Rice"), "2");
  assert.equal(pickerValue(page.vendors[0], {}, "vendor:Chetak"), "");
  const first = buildSaveBody(page, {});
  assert.deepEqual(first.sections, [
    { name: "Rice", user_id: 2 },
    { name: "Dairy", user_id: null },
  ]);
  assert.deepEqual(first.vendors, []);
  assert.deepEqual(first.fallback, { user_id: 5 });
  const edits = { "section:Beverages": "2", "vendor:Chetak": "2" };
  const only = buildSaveBody(page, edits, { includeUnsaved: false });
  assert.deepEqual(only.sections, [{ name: "Beverages", user_id: 2 }]);
  assert.deepEqual(only.vendors, [{ name: "Chetak", user_id: 2 }]);
  assert.equal(only.fallback, undefined);
  assert.equal(changeCount(only), 2);
});
