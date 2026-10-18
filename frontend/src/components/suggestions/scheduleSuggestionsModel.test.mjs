// node --test src/components/suggestions/scheduleSuggestionsModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  MISSING_PREVIEW,
  addAllKeys,
  addAllResultText,
  addAllRows,
  confidenceLabel,
  confidencePill,
  countsLine,
  deliveriesSeenText,
  evidenceSummary,
  initialWeekdays,
  orderDayText,
  scheduleSummary,
  visibleMissing,
} from "./scheduleSuggestionsModel.js";

const sugg = (over = {}) => ({
  key: "raja foods|[3]",
  vendor: "Raja Foods",
  delivery_weekdays: [3],
  lead_days: 1,
  lead_days_source: "default",
  confidence: "high",
  evidence: { summary: "Delivered Thu x8 in the last 12 weeks", weekday_counts: { Thu: 8 }, weeks: 12 },
  ...over,
});

test("confidence label and pill, unknown = Low", () => {
  assert.equal(confidenceLabel("high"), "High");
  assert.equal(confidenceLabel("medium"), "Medium");
  assert.equal(confidenceLabel("??"), "Low");
  assert.equal(confidencePill("high"), "pill-success");
  assert.equal(confidencePill("medium"), "pill-warn");
  assert.equal(confidencePill(undefined), "pill-neutral");
});

test("evidence summary: the server's words, else rebuilt from counts in weekday order", () => {
  assert.equal(evidenceSummary(sugg()), "Delivered Thu x8 in the last 12 weeks");
  const old = { evidence: { weekday_counts: { Fri: 5, Tue: 6 } } };
  assert.equal(evidenceSummary(old), "Delivered Tue x6, Fri x5 in the last 8 weeks");
  assert.equal(evidenceSummary({ evidence: {} }), "");
  assert.equal(evidenceSummary(null), "");
});

test("order day: measured lead names the weekday pairs, else it's the owner's", () => {
  assert.equal(
    orderDayText(sugg({ lead_days: 2, lead_days_source: "placed_orders", lead_samples: 4, delivery_weekdays: [0, 3] })),
    "Order 2 days before (Sat for Mon, Tue for Thu), from 4 placed orders.",
  );
  assert.equal(
    orderDayText(sugg({ lead_days: 1, lead_days_source: "placed_orders", lead_samples: 1 })),
    "Order 1 day before (Wed for Thu), from 1 placed order.",
  );
  assert.equal(
    orderDayText(sugg({ lead_days: 0, lead_days_source: "placed_orders", lead_samples: 2 })),
    "Order the same day, from 2 placed orders.",
  );
  assert.match(orderDayText(sugg()), /^Order day: yours to set - no placed orders on record \(1 day before/);
  assert.equal(orderDayText(null), "");
});

test("Add all: rows skip dismissed ones, keys leave out unticked", () => {
  const list = [sugg(), sugg({ key: "kp|[2]", vendor: "KP" }), sugg({ key: "x|[1]", dismissed: true }), null];
  assert.deepEqual(addAllRows(list).map((s) => s.key), ["raja foods|[3]", "kp|[2]"]);
  assert.deepEqual(addAllKeys(list, new Set(["kp|[2]"])), ["raja foods|[3]"]);
  assert.deepEqual(addAllKeys(list, ["raja foods|[3]"]), ["kp|[2]"]);
  assert.deepEqual(addAllKeys(null, null), []);
});

test("Add all result message names what was added and why others were skipped", () => {
  assert.equal(
    addAllResultText({
      created: [{ vendor: "A" }, { vendor: "B" }],
      skipped: [{ vendor: "C", why: "has_schedule" }, { vendor: "d", why: "not_suggested" }],
    }),
    "Added 2 schedules: A, B. Skipped 2: C (already has a schedule), d (no longer suggested).",
  );
  assert.equal(addAllResultText({ created: [{ vendor: "A" }], skipped: [] }), "Added 1 schedule: A.");
  assert.equal(addAllResultText({}), "No schedules added.");
});

test("vendors without a schedule: preview then all", () => {
  const rows = Array.from({ length: 12 }, (_, i) => ({ vendor: `V${i}` }));
  assert.equal(visibleMissing(rows, false).length, MISSING_PREVIEW);
  assert.equal(visibleMissing(rows, true).length, 12);
  assert.equal(visibleMissing(rows.slice(0, 3), false).length, 3);
  assert.deepEqual(visibleMissing(null, false), []);
  assert.equal(deliveriesSeenText({ deliveries_seen: 1 }), "1 delivery seen");
  assert.equal(deliveriesSeenText({ deliveries_seen: 3 }), "3 deliveries seen");
  assert.equal(deliveriesSeenText({}), "No deliveries seen");
});

test("Set schedule starts from a dismissed suggestion's days or one clear weekday", () => {
  assert.deepEqual(initialWeekdays({ suggested_weekdays: [1, 4] }), [1, 4]);
  assert.deepEqual(initialWeekdays({ weekday_counts: { Mon: 1, Thu: 3, Sat: 1 } }), [3]);
  assert.deepEqual(initialWeekdays({ weekday_counts: { Mon: 2, Thu: 2 } }), []); // a tie isn't a pick
  assert.deepEqual(initialWeekdays({ weekday_counts: { Wed: 1 } }), []); // one delivery isn't a pick
  assert.deepEqual(initialWeekdays(null), []);
});

test("summary lines", () => {
  assert.equal(scheduleSummary([3, 0], 2), "delivers Mon/Thu, order 2 days before");
  assert.equal(
    countsLine({ active_vendors: 54, scheduled: 5, suggested: 11, without_schedule: 38 }),
    "54 vendors: 5 with a schedule, 11 suggested, 38 without",
  );
  assert.equal(countsLine(null), "");
});

test("Add all starts with High and Medium ticked, Low and every-few-weeks vendors unticked but listed", async () => {
  const { defaultUnticked, tickedByDefault, cadenceWarning, defaultsNote, addAllKeys, addAllRows } = await import(
    "./scheduleSuggestionsModel.js"
  );
  const rows = [
    { key: "a", vendor: "A", confidence: "high", add_all_default: true, irregular: false },
    { key: "b", vendor: "B", confidence: "medium", add_all_default: true, irregular: false },
    { key: "c", vendor: "C", confidence: "low", add_all_default: false, irregular: false },
    {
      key: "d", vendor: "D", confidence: "medium", add_all_default: false, irregular: true,
      cadence_text: "Delivers every 2-3 weeks, not every week - a schedule repeats every week, so it would put an order due every week. Weekly isn't right for this vendor.",
    },
  ];
  const off = defaultUnticked(rows);
  assert.deepEqual([...off].sort(), ["c", "d"]);
  assert.deepEqual(addAllKeys(rows, off), ["a", "b"]);
  assert.equal(addAllRows(rows).length, 4); // every one still listed
  assert.equal(cadenceWarning(rows[0]), "");
  assert.match(cadenceWarning(rows[3]), /Weekly isn't right/);
  assert.equal(
    defaultsNote(rows),
    "1 Low-confidence suggestion starts unticked. 1 High / Medium vendor delivers only every few weeks - a schedule repeats every week, so it starts unticked too. Tick any you still want.",
  );
  // A Low one that is also every-few-weeks counts as Low only.
  const both = [...rows, { key: "e", vendor: "E", confidence: "low", add_all_default: false, irregular: true, cadence_label: "about once a month or less" }];
  assert.match(defaultsNote(both), /^2 Low-confidence suggestions start unticked\. 1 High \/ Medium vendor delivers/);
  const { cadenceShort } = await import("./scheduleSuggestionsModel.js");
  assert.equal(cadenceShort(both[4]), "Delivers about once a month or less - weekly isn't right");
  assert.equal(cadenceShort(rows[3]), "Delivers every few weeks - weekly isn't right");
  assert.equal(cadenceShort(rows[0]), "");
  // Older server (no add_all_default): by confidence alone.
  assert.equal(tickedByDefault({ confidence: "high" }), true);
  assert.equal(tickedByDefault({ confidence: "medium" }), true);
  assert.equal(tickedByDefault({ confidence: "low" }), false);
  assert.equal(tickedByDefault({}), false);
  assert.equal(defaultsNote(rows.slice(0, 2)), "");
});
