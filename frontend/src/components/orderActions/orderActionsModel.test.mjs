// node --test src/components/orderActions/orderActionsModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  chipLabel,
  dayText,
  handledOf,
  handledText,
  markGroupPlacedOutside,
  menuActions,
  noteProblem,
  orderDueCount,
  orderDueTitle,
  undoRequest,
  unmarkGroupPlacedOutside,
} from "./orderActionsModel.js";

const due = (extra = {}) => ({
  type: "order_due",
  vendor: "Chetak",
  date: "2026-09-30",
  for_delivery_date: "2026-10-01",
  skipped: false,
  ordered: false,
  handled: null,
  ...extra,
});

test("handledOf reads the server's handled, or falls back on older servers", () => {
  assert.equal(handledOf(due()), null);
  assert.equal(handledOf({ type: "delivery" }), null);
  assert.equal(handledOf(due({ handled: undefined, skipped: true })).kind, "skip");
  assert.equal(handledOf(due({ handled: undefined, ordered: true })).kind, "placed");
  assert.equal(handledOf(due({ handled: undefined, ordered: true })).can_undo, false);
  const h = { kind: "other", label: "Other", note: "x", can_undo: true };
  assert.equal(handledOf(due({ skipped: true, handled: h })), h);
});

test("chip labels", () => {
  assert.equal(chipLabel(due()), "Order due");
  assert.equal(chipLabel(due({ handled: { kind: "skip" } })), "Skipped");
  assert.equal(chipLabel(due({ handled: { kind: "other" } })), "Hidden");
  assert.equal(chipLabel(due({ handled: { kind: "placed_outside" } })), "Ordered");
  assert.equal(chipLabel(due({ handled: { kind: "placed" } })), "Ordered");
});

test("handled text", () => {
  assert.equal(handledText({ kind: "other", note: "Rep visits Fri", by_name: "boss" }), "Other: Rep visits Fri (boss)");
  assert.equal(handledText({ kind: "placed_outside", label: "Order already placed", by_name: "boss" }), "Order already placed by boss");
  assert.equal(handledText({ kind: "placed", by_name: "sai" }), "Order placed in the app by sai");
  assert.equal(handledText({ kind: "skip", label: "Skipped this week" }), "Skipped this week");
  assert.equal(handledText(null), "");
});

test("menu actions by role", () => {
  assert.deepEqual(menuActions(due(), { isAdmin: true }), ["placed", "skip", "other"]);
  assert.deepEqual(menuActions(due(), { isAdmin: false }), ["skip"]);
  assert.deepEqual(menuActions(due(), { isAdmin: true, demo: true }), []);
  assert.deepEqual(menuActions(due({ handled: { kind: "skip", can_undo: true } }), {}), ["undo"]);
  assert.deepEqual(menuActions(due({ handled: { kind: "other", can_undo: true } }), {}), []);
  assert.deepEqual(menuActions(due({ handled: { kind: "other", can_undo: true } }), { isAdmin: true }), ["undo"]);
  assert.deepEqual(menuActions(due({ handled: { kind: "placed", can_undo: false } }), { isAdmin: true }), []);
  assert.deepEqual(menuActions({ type: "delivery" }, { isAdmin: true }), []);
});

test("undo requests", () => {
  assert.deepEqual(undoRequest(due({ handled: { kind: "placed_outside", can_undo: true, order_id: 7 } })), {
    kind: "placed",
    orderId: 7,
  });
  assert.deepEqual(undoRequest(due({ handled: { kind: "other", can_undo: true } })), {
    kind: "skip",
    vendor: "Chetak",
    deliveryDate: "2026-10-01",
  });
  assert.equal(undoRequest(due({ handled: { kind: "placed", can_undo: false, order_id: 3 } })), null);
  assert.equal(undoRequest(due()), null);
});

test("note rules", () => {
  assert.equal(noteProblem("", true), "Add a note for Other.");
  assert.equal(noteProblem("  ", false), null);
  assert.equal(noteProblem("x".repeat(501), false), "Keep the note to 500 characters or fewer.");
  assert.equal(noteProblem("ok", true), null);
});

test("day text never shifts a day", () => {
  assert.equal(dayText("2026-10-01"), "Thu Oct 1");
  assert.equal(dayText("2026-03-08"), "Sun Mar 8"); // DST day
  assert.equal(dayText("junk"), "junk");
  assert.equal(dayText(""), "");
});

test("a reorder group marked placed outside, and undone", () => {
  const g = { vendor: "Chetak", urgency: { tier: "critical", label: "Critical" }, ordered_at: null };
  const placed = markGroupPlacedOutside(g, { order_id: 9, handled: { at: "2026-09-30T12:00:00+00:00" } });
  assert.equal(placed.urgency.tier, "ordered");
  assert.equal(placed.ordered_source, "outside");
  assert.equal(placed.ordered_order_id, 9);
  assert.equal(unmarkGroupPlacedOutside(placed), g);
  assert.equal(unmarkGroupPlacedOutside(g), g);
});

test("vendor minimum orders: 1-4 items are 'low', never the red count", () => {
  assert.equal(orderDueCount(due({ urgent: true, items_needing_order: 7 })), " (7)");
  assert.equal(orderDueCount(due({ urgent: false, below_minimum: true, items_needing_order: 1 })), " (1 low)");
  assert.equal(orderDueCount(due({ urgent: false, below_minimum: false, items_needing_order: 0 })), "");
  // An older server (no below_minimum): nothing beside a non-urgent chip, as before.
  assert.equal(orderDueCount(due({ urgent: false, items_needing_order: 2 })), "");
  // Handled orders show no count.
  assert.equal(orderDueCount(due({ urgent: true, items_needing_order: 9, handled: { kind: "skip" } })), "");
  const low = orderDueTitle(due({ below_minimum: true, items_needing_order: 2, min_order_items: 5, horizon_days: 7 }));
  assert.match(low, /2 items low - fewer than a usual order \(5\+ items\), so not urgent\./);
  assert.match(orderDueTitle(due({ items_needing_order: 6, horizon_days: 7 })), /6 item\(s\) need ordering\./);
});
