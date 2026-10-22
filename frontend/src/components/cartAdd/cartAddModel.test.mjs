// node --test src/components/cartAdd/cartAddModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_QTY,
  addOutcome,
  bigCase,
  caseOfFor,
  caseUnitsLabel,
  suggestionWords,
  canStepQty,
  canonicalVendor,
  cartKeysFor,
  cartPayload,
  defaultQty,
  exactNameMatches,
  formatQty,
  inStockFor,
  isInCart,
  isNetworkError,
  plural,
  projectionIndex,
  qtyProblem,
  splitPostable,
  splitVendors,
  stepQty,
  suggestion,
  tickRow,
  vendorOptions,
} from "./cartAddModel.js";
import {
  MAX_JUST_ADDED,
  MAX_REMEMBERED,
  OPEN_VENDORS_KEY,
  browserStorage,
  cartLinkFor,
  cartLinkState,
  cartVendorFor,
  estimateNote,
  formatMoney,
  isNewLine,
  linkedJustAdded,
  linkedVendor,
  newLineKeys,
  pruneOpenVendors,
  readOpenVendors,
  setVendorOpen,
  summaryLine,
  summaryParts,
  vendorDomId,
  vendorSummary,
  writeOpenVendors,
} from "./cartVendorPanels.js";

const VENDORS = ["Babco", "Chetak", "Raja Foods", "Shata Traders"];

test("splitVendors splits Toast's ;-separated vendor field", () => {
  assert.deepEqual(splitVendors("Babco; Raja Foods;;"), ["Babco", "Raja Foods"]);
  assert.deepEqual(splitVendors(null), []);
  // A vendor Toast lists twice is shown once (real: Tomato Regular in LBs).
  assert.deepEqual(splitVendors("Raja Foods;RBest Produce Inc;RBest Produce Inc"), ["Raja Foods", "RBest Produce Inc"]);
});

test("canonicalVendor matches a known vendor ignoring case and spaces", () => {
  assert.equal(canonicalVendor("  raja foods ", VENDORS), "Raja Foods");
  assert.equal(canonicalVendor("raja", VENDORS), null);
  assert.equal(canonicalVendor("", VENDORS), null);
});

test("vendorOptions: cart vendors first, then matches, then 'use what I typed'", () => {
  assert.deepEqual(
    vendorOptions("", VENDORS, ["Raja Foods"]).map((o) => o.name),
    ["Raja Foods", "Babco", "Chetak", "Shata Traders"],
  );
  assert.deepEqual(vendorOptions("ra", VENDORS, []), [
    { name: "Raja Foods", isNew: false },
    { name: "Shata Traders", isNew: false },
    { name: "ra", isNew: true },
  ]);
  // A known vendor typed in another case gets no "new vendor" option.
  assert.deepEqual(vendorOptions("BABCO", VENDORS), [{ name: "Babco", isNew: false }]);
  assert.deepEqual(vendorOptions("New Mart ", VENDORS), [{ name: "New Mart", isNew: true }]);
  assert.equal(vendorOptions("", VENDORS, [], 2).length, 2);
  // Before the vendor list loads there's no "use what I typed" option.
  assert.deepEqual(vendorOptions("ra", [], ["Babco"], 20, false), []);
  assert.deepEqual(vendorOptions("bab", [], ["Babco"], 20, false), [{ name: "Babco", isNew: false }]);
});

const CART = [
  {
    supplier: "Babco",
    items: [
      { item_id: "rice", name: "Basmati Rice 10lb" },
      { item_id: null, name: "New Mango Pickle " },
    ],
  },
  { supplier: "Raja Foods", items: [{ item_id: "dal", name: "Toor Dal" }] },
];

test("cartKeysFor / isInCart look at the chosen vendor's section only", () => {
  const keys = cartKeysFor(CART, " babco");
  assert.equal(isInCart(keys, "rice", "Basmati Rice 10lb"), true);
  assert.equal(isInCart(keys, null, "new mango pickle"), true);
  assert.equal(isInCart(keys, "dal", "Toor Dal"), false);
  assert.equal(isInCart(cartKeysFor(CART, ""), "rice", "x"), false);
});

const PROJECTION = {
  items: [
    { item_id: "rice", on_hand_qty: 24, avg_weekly_demand: 9.5, supplier_item_id: "BR10", projections: { 7: { need_to_order: 5.2 } } },
    { item_id: "atta", on_hand_qty: 40, avg_weekly_demand: 2, supplier_item_id: null, projections: { 7: { need_to_order: 0 } } },
    { item_id: "salt", on_hand_qty: null, avg_weekly_demand: 1, projections: { 7: { need_to_order: null } } },
  ],
};

test("projectionIndex + suggestion + defaultQty follow the app's add sheet", () => {
  const idx = projectionIndex(PROJECTION);
  assert.deepEqual(idx.get("rice"), { need: 5.2, suggested: null, caseOf: null, onHand: 24, weekly: 9.5, sku: "BR10" });
  assert.equal(suggestion(idx.get("rice")), 6); // rounded up
  assert.equal(defaultQty(idx.get("rice")), 6);
  assert.equal(suggestion(idx.get("atta")), 0); // enough stock
  assert.equal(defaultQty(idx.get("atta")), 1);
  assert.equal(suggestion(idx.get("salt")), null); // on hand not tracked
  assert.equal(defaultQty(idx.get("salt")), 1);
  assert.equal(suggestion(undefined), null);
  assert.equal(defaultQty(undefined), 1);
  assert.equal(projectionIndex(null).size, 0);
});

test("Oct 22: the add form starts at the server's suggested order - CASES when the item has a case size", () => {
  const idx = projectionIndex(
    {
      items: [
        // Need 66 units, 24 to a case: Supplier Projection suggests 3 cases.
        {
          item_id: "tuna",
          case_size: 24,
          projections: { 30: { need_to_order: 66 } },
          suggested_order: { qty: 3, units: 66, case_of: 24, horizon_days: 30 },
        },
        // A units item: the server's number, not the need rounded up.
        { item_id: "loose", projections: { 30: { need_to_order: 8.4 } }, suggested_order: { qty: 9, case_of: null } },
        // Enough stock in cases: starts at 1 case.
        { item_id: "full", projections: { 30: { need_to_order: 0 } }, suggested_order: { qty: 0, case_of: 12 } },
        // An older server (no suggestion) that knows the case size: the need in cases.
        { item_id: "old", case_size: 24, projections: { 30: { need_to_order: 50 } } },
      ],
    },
    30
  );
  assert.equal(suggestion(idx.get("tuna")), 3);
  assert.equal(defaultQty(idx.get("tuna")), 3);
  assert.equal(caseOfFor(idx.get("tuna")), 24);
  assert.equal(caseUnitsLabel(3, 24), "1 case = 24 units · 72 units");
  assert.equal(suggestionWords(3, 24), "3 cases");
  assert.equal(suggestionWords(1, 24), "1 case");
  assert.equal(defaultQty(idx.get("loose")), 9);
  assert.equal(caseOfFor(idx.get("loose")), null);
  assert.equal(caseUnitsLabel(9, null), null);
  assert.equal(suggestionWords(9, null), "9");
  assert.equal(suggestion(idx.get("full")), 0);
  assert.equal(defaultQty(idx.get("full")), 1);
  assert.equal(caseOfFor(idx.get("full")), 12);
  assert.equal(defaultQty(idx.get("old")), 3);
  assert.equal(caseOfFor(idx.get("old")), 24);
  assert.equal(bigCase(1), null);
  assert.equal(bigCase("24"), 24);
  // A row in cases sends its Case of; a units row doesn't.
  assert.deepEqual(
    cartPayload([
      { item_id: "tuna", name: "Tuna", supplier_item_id: null, qty: "3", case_of: 24 },
      { item_id: "loose", name: "Loose", supplier_item_id: null, qty: "9", case_of: null },
    ]),
    [
      { item_id: "tuna", name: "Tuna", supplier_item_id: null, qty: 3, case_of: 24 },
      { item_id: "loose", name: "Loose", supplier_item_id: null, qty: 9 },
    ]
  );
});

test("inStockFor prefers the projection, then in_stock_qty, then the shelf count", () => {
  assert.equal(inStockFor({ in_stock_qty: 24, on_hand_qty: 4 }, { onHand: 30 }), 30);
  assert.equal(inStockFor({ in_stock_qty: 24, on_hand_qty: 4 }, undefined), 24);
  assert.equal(inStockFor({ in_stock_qty: null, on_hand_qty: 4 }, undefined), null);
  assert.equal(inStockFor({ on_hand_qty: 4 }, undefined), 4); // an older server
  assert.equal(inStockFor({}, { onHand: null }), null);
});

test("formatQty", () => {
  assert.equal(formatQty(3), "3");
  assert.equal(formatQty(2.25), "2.3");
  assert.equal(formatQty(null), "-");
  assert.equal(formatQty(""), "-");
});

test("qtyProblem", () => {
  assert.equal(qtyProblem("2"), null);
  assert.equal(qtyProblem("0.5"), null);
  assert.ok(qtyProblem(""));
  assert.ok(qtyProblem("0"));
  assert.ok(qtyProblem("-1"));
  assert.ok(qtyProblem("abc"));
  // Too big for the cart: 1e308 made GET /cart fail for everyone; a pasted
  // barcode is caught too.
  assert.equal(MAX_QTY, 100000);
  assert.equal(qtyProblem(String(MAX_QTY)), null);
  assert.match(qtyProblem("100001"), /can't be more than 100,000/);
  assert.match(qtyProblem("1e308"), /can't be more than/);
  assert.match(qtyProblem("1e309"), /can't be more than/); // Infinity
  assert.match(qtyProblem("8901058000012"), /can't be more than/);
  assert.match(qtyProblem("-1e309"), /more than 0/);
});

const row = (item_id, name, extra = {}) => ({ key: item_id ? `item:${item_id}` : `new:${name.toLowerCase()}`, item_id, name, qty: "1", qtyAuto: true, manual: !item_id, ...extra });

test("tickRow ticks, unticks, and replaces a hand-typed row of the same name", () => {
  let rows = tickRow([], row("rice", "Basmati Rice 10lb", { qty: "6" }));
  assert.deepEqual(rows.map((r) => r.item_id), ["rice"]);
  // Ticking the same item again (from the other search mode) unticks it.
  assert.deepEqual(tickRow(rows, row("rice", "Basmati Rice 10lb")), []);
  // Typed as new first, then the inventory item ticked: one row, the item's.
  rows = [row(null, "deep chai mix masala"), row("atta", "Atta 20lb")];
  rows = tickRow(rows, row("chai", "Deep Chai Mix Masala", { qty: "3" }));
  assert.deepEqual(rows.map((r) => [r.item_id, r.name, r.qty, r.replaced]), [
    ["chai", "Deep Chai Mix Masala", "3", "deep chai mix masala"], // untouched typed qty -> the item's own
    ["atta", "Atta 20lb", "1", undefined],
  ]);
  // A qty someone typed for the hand-typed row is kept.
  rows = tickRow([row(null, "Deep Chai Mix Masala", { qty: "5", qtyAuto: false })], row("chai", "Deep Chai Mix Masala", { qty: "3" }));
  assert.deepEqual([rows[0].qty, rows[0].qtyAuto], ["5", false]);
});

test("exactNameMatches finds inventory items by exact name, never a Container duplicate", () => {
  const items = [
    { item_id: "chai", name: "Deep Chai Mix Masala", container_of: null },
    { item_id: "chai2", name: "Deep Chai Mix Masala 2", container_of: null },
    { item_id: "ctr", name: "Maggi 600g", container_of: "maggi" },
  ];
  assert.deepEqual(exactNameMatches(items, " deep chai mix MASALA ").map((i) => i.item_id), ["chai"]);
  assert.deepEqual(exactNameMatches(items, "Maggi 600g"), []);
  assert.deepEqual(exactNameMatches(items, ""), []);
  assert.deepEqual(exactNameMatches(undefined, "x"), []);
});

test("isNetworkError: no HTTP status = no answer", () => {
  assert.equal(isNetworkError(new TypeError("Failed to fetch")), true);
  assert.equal(isNetworkError(Object.assign(new Error("Forbidden"), { status: 403 })), false);
});

test("addOutcome splits what was sent by whether the cart has it now", () => {
  const sent = [row("rice", "Basmati Rice 10lb"), row("dal", "Toor Dal"), row(null, "New Mango Pickle")];
  const keys = cartKeysFor(CART, "Babco"); // rice + "new mango pickle" are in Babco's cart
  const { inCart, missing } = addOutcome(sent, keys);
  assert.deepEqual(inCart.map((r) => r.name), ["Basmati Rice 10lb", "New Mango Pickle"]);
  assert.deepEqual(missing.map((r) => r.name), ["Toor Dal"]);
});

test("splitPostable leaves out rows already in the vendor's cart; cartPayload shapes the POST", () => {
  const keys = cartKeysFor(CART, "Babco");
  const rows = [
    { item_id: "rice", name: "Basmati Rice 10lb", supplier_item_id: "BR10", qty: "2" },
    { item_id: "atta", name: "Atta 20lb", supplier_item_id: null, qty: "6" },
    { item_id: null, name: " Fresh Curry Leaves ", supplier_item_id: "CL1", qty: "1.5" },
    { item_id: null, name: "new mango pickle", qty: "1" },
  ];
  const { post, skipped } = splitPostable(rows, keys);
  assert.deepEqual(skipped.map((r) => r.name), ["Basmati Rice 10lb", "new mango pickle"]);
  assert.deepEqual(cartPayload(post), [
    { item_id: "atta", name: "Atta 20lb", supplier_item_id: null, qty: 6 },
    { item_id: null, name: "Fresh Curry Leaves", supplier_item_id: "CL1", qty: 1.5 },
  ]);
});

test("plural", () => {
  assert.equal(plural(1, "item"), "1 item");
  assert.equal(plural(3, "item"), "3 items");
});

test("stepQty / canStepQty: the To-add stepper never goes below 1 or above MAX_QTY", () => {
  assert.equal(stepQty("3", 1), "4");
  assert.equal(stepQty("3", -1), "2");
  assert.equal(stepQty("1", -1), "1");
  assert.equal(stepQty("1.5", -1), "1"); // never below 1
  assert.equal(stepQty("1.1", 1), "2.1"); // no float noise
  assert.equal(stepQty("", 1), "1"); // blank box: + makes it 1
  assert.equal(stepQty("abc", 1), "1");
  assert.equal(stepQty("-4", 1), "1");
  assert.equal(stepQty("1e309", 1), "1"); // Infinity counts as unusable
  assert.equal(stepQty(String(MAX_QTY), 1), String(MAX_QTY));
  assert.equal(stepQty("250000", -1), String(MAX_QTY));
  assert.equal(canStepQty("1", -1), false);
  assert.equal(canStepQty("", -1), false);
  assert.equal(canStepQty("2", -1), true);
  assert.equal(canStepQty("1.5", -1), true);
  assert.equal(canStepQty("", 1), true);
  assert.equal(canStepQty(String(MAX_QTY), 1), false);
  assert.equal(canStepQty("99999", 1), true);
});

// --- Vendor panels: open/closed remembered per browser ----------------------

class MemoryStorage {
  constructor(init = {}) {
    this.map = new Map(Object.entries(init));
  }
  getItem(k) {
    return this.map.has(k) ? this.map.get(k) : null;
  }
  setItem(k, v) {
    this.map.set(k, String(v));
  }
  removeItem(k) {
    this.map.delete(k);
  }
}

class BrokenStorage {
  getItem() {
    throw new Error("SecurityError: blocked");
  }
  setItem() {
    throw new Error("QuotaExceededError");
  }
  removeItem() {
    throw new Error("SecurityError: blocked");
  }
}

test("readOpenVendors: nothing stored, junk or a blocked storage = all collapsed", () => {
  assert.deepEqual([...readOpenVendors(new MemoryStorage())], []);
  assert.deepEqual([...readOpenVendors(null)], []);
  assert.deepEqual([...readOpenVendors(new BrokenStorage())], []);
  assert.deepEqual([...readOpenVendors(new MemoryStorage({ [OPEN_VENDORS_KEY]: "{not json" }))], []);
  assert.deepEqual([...readOpenVendors(new MemoryStorage({ [OPEN_VENDORS_KEY]: '{"Babco":true}' }))], []);
  // Only non-blank strings are kept.
  const s = new MemoryStorage({ [OPEN_VENDORS_KEY]: JSON.stringify(["Babco", 7, null, "  ", "Raja Foods"]) });
  assert.deepEqual([...readOpenVendors(s)], ["Babco", "Raja Foods"]);
  // A runaway list is cut.
  const big = new MemoryStorage({ [OPEN_VENDORS_KEY]: JSON.stringify(Array.from({ length: 500 }, (_, i) => `V${i}`)) });
  assert.equal(readOpenVendors(big).size, MAX_REMEMBERED);
});

test("writeOpenVendors round-trips, removes the key when nothing is open, and never throws", () => {
  const s = new MemoryStorage();
  assert.equal(writeOpenVendors(s, new Set(["Raja Foods", "Babco"])), true);
  assert.equal(s.getItem(OPEN_VENDORS_KEY), JSON.stringify(["Babco", "Raja Foods"]));
  assert.deepEqual([...readOpenVendors(s)].sort(), ["Babco", "Raja Foods"]);
  assert.equal(writeOpenVendors(s, new Set()), true);
  assert.equal(s.getItem(OPEN_VENDORS_KEY), null);
  assert.equal(writeOpenVendors(new BrokenStorage(), new Set(["Babco"])), false);
  assert.equal(writeOpenVendors(null, new Set(["Babco"])), false);
  // No window here (node): no storage, and no throw.
  assert.equal(browserStorage(), null);
});

test("setVendorOpen / pruneOpenVendors return the same Set when nothing changes", () => {
  const open = new Set(["Babco"]);
  assert.equal(setVendorOpen(open, "Babco", true), open);
  assert.equal(setVendorOpen(open, "Chetak", false), open);
  assert.equal(setVendorOpen(open, "", true), open);
  assert.deepEqual([...setVendorOpen(open, "Chetak", true)], ["Babco", "Chetak"]);
  assert.deepEqual([...setVendorOpen(open, "Babco", false)], []);
  assert.deepEqual([...open], ["Babco"]); // never mutated
  // A vendor no longer in the cart (order placed) is forgotten.
  const two = new Set(["Babco", "Chetak"]);
  assert.equal(pruneOpenVendors(two, ["Babco", "Chetak", "Vadilal"]), two);
  assert.deepEqual([...pruneOpenVendors(two, ["Chetak"])], ["Chetak"]);
  assert.deepEqual([...pruneOpenVendors(two, [])], []);
});

test("vendorSummary / summaryLine / estimateNote: the collapsed header's figures", () => {
  const items = [
    { qty: 2, case_of: 12, unit_cost: 1.5, removal_requested: { by: "Asha" } },
    { qty: 3, case_of: 1, unit_cost: 2 },
    { qty: 0, case_of: 1, unit_cost: 4 }, // left at 0: not on the PDF
    { qty: "", case_of: 6, unit_cost: 1 }, // being retyped: counts as 0
    { qty: 1, case_of: 1, unit_cost: null }, // hand-added, no cost
  ];
  const s = vendorSummary(items);
  // Cases only on case lines (Oct 22): the 3 "Each" lines add units.
  assert.deepEqual(s, {
    lines: 5,
    cases: 2,
    looseUnits: 4,
    caseLines: 2,
    unitLines: 3,
    units: 28,
    estimate: 42,
    missingCost: 1,
    removalRequests: 1,
    orderable: 3,
  });
  assert.equal(summaryLine(s), "5 items · 2 cases + 4 units · 28 units total");
  assert.deepEqual(summaryParts(s), ["5 items", "2 cases + 4 units", "28 units total"]);
  assert.deepEqual(estimateNote(s), { amount: "$42.00", missing: "1 item without a cost" });
  // A line in units ("Each") is never counted as a case.
  const one = vendorSummary([{ qty: 1, case_of: 1, unit_cost: 1234.5 }]);
  assert.equal(summaryLine(one), "1 item · 1 unit");
  assert.deepEqual(estimateNote(one), { amount: "$1,234.50", missing: null });
  assert.equal(summaryLine(vendorSummary([{ qty: 1.5, case_of: 1, unit_cost: 1 }])), "1 item · 1.5 units");
  // Case lines only: the cases and the units they hold.
  assert.equal(
    summaryLine(vendorSummary([{ qty: 2, case_of: 12, unit_cost: 1 }, { qty: 1, case_of: 6, unit_cost: 1 }])),
    "2 items · 3 cases · 30 units"
  );
  // The iPhone check's cart: 11 cases on 3 lines + 18 units on 2 lines
  // (was "5 items · 29 cases · 198 units").
  const mixed = vendorSummary([
    { qty: 5, case_of: 24, unit_cost: 1 },
    { qty: 4, case_of: 12, unit_cost: 1 },
    { qty: 2, case_of: 6, unit_cost: 1 },
    { qty: 10, case_of: 1, unit_cost: 1 },
    { qty: 8, case_of: 1, unit_cost: 1 },
  ]);
  assert.equal(summaryLine(mixed), "5 items · 11 cases + 18 units · 198 units total");
  // Nothing with a cost.
  assert.deepEqual(estimateNote(vendorSummary([{ qty: 2, case_of: 1, unit_cost: null }])), { amount: null, missing: "No cost on file" });
  assert.deepEqual(vendorSummary([]), {
    lines: 0,
    cases: 0,
    looseUnits: 0,
    caseLines: 0,
    unitLines: 0,
    units: 0,
    estimate: 0,
    missingCost: 0,
    removalRequests: 0,
    orderable: 0,
  });
  assert.equal(summaryLine(vendorSummary([])), "0 items");
  assert.equal(formatMoney(NaN), "$0.00");
});

test("linkedVendor / cartVendorFor / cartLinkFor: links that open one vendor", () => {
  assert.equal(linkedVendor("?vendor=Raja%20Foods", null), "Raja Foods");
  assert.equal(linkedVendor("", { vendor: " Chetak " }), "Chetak");
  assert.equal(linkedVendor("?vendor=Babco", { vendor: "Chetak" }), "Babco"); // the URL wins
  assert.equal(linkedVendor("?vendor=", null), null);
  assert.equal(linkedVendor(undefined, undefined), null);
  assert.equal(linkedVendor("", { vendor: 5 }), null);
  const cart = [{ supplier: "Raja Foods" }, { supplier: "Chandigarh Distributors Inc." }];
  assert.equal(cartVendorFor("raja foods ", cart), "Raja Foods");
  assert.equal(cartVendorFor("Babco", cart), null);
  assert.equal(cartLinkFor("Chandigarh Distributors Inc."), "/purchase-order?vendor=Chandigarh%20Distributors%20Inc.");
  // The link's query reads back as the same name, "&" and all.
  assert.equal(linkedVendor(`?${cartLinkFor("A & B Foods").split("?")[1]}`), "A & B Foods");
  assert.equal(cartLinkFor(""), "/purchase-order");
});

test("cartLinkState / linkedJustAdded: a link that marks lines 'Just added'", () => {
  assert.deepEqual(cartLinkState("Vdyas", ["a", "b", "a", "", null, 7]), { vendor: "Vdyas", justAdded: ["a", "b"] });
  assert.deepEqual(cartLinkState("Vdyas", []), { vendor: "Vdyas" });
  assert.deepEqual(cartLinkState("Vdyas"), { vendor: "Vdyas" });
  assert.equal(cartLinkState("V", Array.from({ length: 900 }, (_, i) => `id${i}`)).justAdded.length, MAX_JUST_ADDED);
  // The state reads back on the cart page; the vendor still comes from the link.
  const state = cartLinkState("Chetak", ["x1", "x2"]);
  assert.deepEqual(linkedJustAdded(state), ["x1", "x2"]);
  assert.equal(linkedVendor(`?${cartLinkFor("Chetak").split("?")[1]}`, state), "Chetak");
  // Anything odd is no ids, never a throw.
  for (const bad of [null, undefined, "x", 5, {}, { justAdded: "a" }, { justAdded: [1, {}, " "] }]) {
    assert.deepEqual(linkedJustAdded(bad), []);
  }
});

test("newLineKeys / isNewLine: the lines an add just put in", () => {
  const keys = newLineKeys([
    { item_id: "rice", name: "Basmati Rice 10lb" },
    { item_id: null, name: " Fresh Moringa Leaves " },
  ]);
  assert.equal(isNewLine(keys, { item_id: "rice", name: "x" }), true);
  assert.equal(isNewLine(keys, { item_id: null, name: "fresh moringa leaves" }), true);
  // An inventory line is matched by item_id only, never by a typed name.
  assert.equal(isNewLine(keys, { item_id: "other", name: "Fresh Moringa Leaves" }), false);
  assert.equal(isNewLine(keys, { item_id: "dal", name: "Toor Dal" }), false);
  assert.equal(isNewLine(null, { item_id: "rice" }), false);
});

test("vendorDomId: a safe, distinct id per vendor", () => {
  const a = vendorDomId("po-vendor", "Chandigarh Distributors Inc.");
  assert.match(a, /^po-vendor-chandigarh-distributors-inc-[0-9a-z]+$/);
  assert.notEqual(vendorDomId("p", "A&B"), vendorDomId("p", "A B"));
  assert.match(vendorDomId("p", "!!!"), /^p-vendor-[0-9a-z]+$/);
});
