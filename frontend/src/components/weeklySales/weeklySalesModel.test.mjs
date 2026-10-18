import { test } from "node:test";
import assert from "node:assert/strict";
import {
  shortDate,
  weekLabel,
  formatCount,
  formatSold,
  onHandCell,
  weekSummary,
  hasOnHandFields,
  hasRevenue,
  noSales,
  isoMinusDays,
} from "./weeklySalesModel.js";

const week = (o) => ({ week_start: "2026-06-01", week_end: "2026-06-07", quantity: 10, revenue: 20, ...o });

test("dates read like the owner's example", () => {
  assert.equal(shortDate("2026-06-01"), "Jun 1");
  assert.equal(shortDate("2026-10-12"), "Oct 12");
  assert.equal(shortDate("junk"), "junk");
  assert.equal(shortDate(null), "");
  assert.equal(weekLabel(week()), "Jun 1 - Jun 7");
});

test("counts: whole, one decimal, negative, thousands, missing", () => {
  assert.equal(formatCount(24), "24");
  assert.equal(formatCount(23.46), "23.5");
  assert.equal(formatCount(-3), "-3");
  assert.equal(formatCount(1234.56), "1,234.6");
  assert.equal(formatCount(null), "-");
  assert.equal(formatCount(NaN), "-");
  assert.equal(formatSold(435.083), "435.08");
  assert.equal(formatSold(undefined), "0");
});

test("on hand: full week, partial week, no count, older server", () => {
  assert.deepEqual(onHandCell(week({ avg_on_hand: 24, on_hand_days: 7, period_days: 7 })), {
    value: "24",
    coverage: "",
    negative: false,
    known: true,
  });
  assert.equal(onHandCell(week({ avg_on_hand: 24, on_hand_days: 6, period_days: 7 })).coverage, "(6 of 7 days)");
  assert.equal(onHandCell(week({ avg_on_hand: 5, on_hand_days: 1, period_days: 1 })).coverage, "");
  assert.equal(onHandCell(week({ avg_on_hand: 5, on_hand_days: 1, period_days: 2 })).coverage, "(1 of 2 days)");
  const none = onHandCell(week({ avg_on_hand: null, on_hand_days: 0, period_days: 7 }));
  assert.equal(none.value, "-");
  assert.equal(none.known, false);
  assert.equal(onHandCell(week()).value, "-"); // no fields at all
  assert.equal(onHandCell(week({ avg_on_hand: -82.5, on_hand_days: 7, period_days: 7 })).negative, true);
  assert.equal(onHandCell(null).value, "-");
});

test("summary line", () => {
  assert.equal(
    weekSummary(week({ avg_on_hand: 24, on_hand_days: 6, period_days: 7 })),
    "Jun 1 - Jun 7: sold 10, avg on hand 24 (6 of 7 days)",
  );
  assert.equal(weekSummary(week({ quantity: 0 })), "Jun 1 - Jun 7: sold 0, avg on hand -");
});

test("columns and empty states", () => {
  assert.equal(hasOnHandFields([week()]), false);
  assert.equal(hasOnHandFields([week({ avg_on_hand: null })]), true);
  assert.equal(hasOnHandFields(null), false);
  assert.equal(hasRevenue([week({ revenue: null })]), false);
  assert.equal(hasRevenue([week({ revenue: null }), week()]), true);
  assert.equal(noSales([]), true);
  assert.equal(noSales([week({ quantity: 0 }), week({ quantity: 0 })]), true);
  assert.equal(noSales([week({ quantity: 0 }), week({ quantity: 0.5 })]), false);
  assert.equal(noSales(undefined), true);
});

test("window start from a lookback", () => {
  assert.equal(isoMinusDays("2026-10-14", 30), "2026-09-14");
  assert.equal(isoMinusDays("2026-03-01", 1), "2026-02-28");
  assert.equal(isoMinusDays("bad", 3), "bad");
});
