// node --test src/components/insights/insightsModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  channelsModel,
  dayName,
  delta,
  errorKind,
  filterRows,
  fmtCount,
  fmtMoney,
  fmtMoneyShort,
  fmtPctAbs,
  fmtShare,
  forecastBasis,
  hasRows,
  heatInk,
  heatmapModel,
  heatStep,
  HEAT_RAMP,
  hourLabel,
  hourRangeLabel,
  labelEvery,
  lastSoldText,
  MENU_TABS,
  MENU_VIEWS,
  menuPerformanceRedirect,
  menuTabFor,
  menuViewTabs,
  minutesText,
  niceTicks,
  pctChange,
  peakHours,
  prepForecastText,
  rangeText,
  shortDate,
  showXLabel,
  weekdayIndex,
  addDays,
  colourForIndex,
  OTHER_COLOR,
  SERIES_COLORS,
  MAX_CHANNELS,
  staleText,
  storeTime,
  weekTitle,
} from "./insightsModel.js";
import { ALSO_IN_POS, ALSO_IN_POS_KEYS, FEATURE_ROUTES, INSIGHTS_HUB, INSIGHTS_MAIN_KEYS, insightsHomePath, NAV_GROUPS } from "../../featureRoutes.js";

test("money formats", () => {
  assert.equal(fmtMoney(1234.4), "$1,234");
  assert.equal(fmtMoney(1234.456, { cents: true }), "$1,234.46");
  assert.equal(fmtMoney(-5), "-$5");
  assert.equal(fmtMoney(null), "-");
  assert.equal(fmtMoney(NaN), "-");
  assert.equal(fmtMoneyShort(950), "$950");
  assert.equal(fmtMoneyShort(1250), "$1.3k");
  assert.equal(fmtMoneyShort(12500), "$13k");
});

test("counts and shares", () => {
  assert.equal(fmtCount(1234), "1,234");
  assert.equal(fmtCount(12.34, 1), "12.3");
  assert.equal(fmtCount(undefined), "-");
  assert.equal(fmtShare(0.234), "23%");
  assert.equal(fmtShare(0.054), "5.4%");
  assert.equal(fmtShare(0), "0%");
  assert.equal(fmtShare(1), "100%");
  assert.equal(fmtShare(null), "-");
  assert.equal(fmtPctAbs(-8.24), "8.2%");
  assert.equal(fmtPctAbs(12.6), "13%");
});

test("delta direction, arrow and words", () => {
  assert.deepEqual(
    { dir: delta(8.2).dir, text: delta(8.2).text, words: delta(8.2).words },
    { dir: "up", text: "▲ 8.2%", words: "up 8.2%" },
  );
  assert.equal(delta(-12).text, "▼ 12%");
  assert.equal(delta(0.3).dir, "flat");
  assert.equal(delta(null).dir, "none");
  // never an en/em dash in user-facing text
  for (const v of [8, -8, 0, null]) assert.ok(!/[\u2013\u2014]/.test(delta(v).text));
});

test("pctChange", () => {
  assert.equal(pctChange(110, 100), 10);
  assert.equal(pctChange(90, 100), -10);
  assert.equal(pctChange(5, 0), null);
  assert.equal(pctChange(null, 5), null);
});

test("hours and dates", () => {
  assert.equal(hourLabel(0), "12a");
  assert.equal(hourLabel(12), "12p");
  assert.equal(hourLabel(15, { long: true }), "3 PM");
  assert.equal(hourRangeLabel(11), "11 AM - 12 PM");
  assert.equal(hourRangeLabel(23), "11 PM - 12 AM");
  assert.equal(shortDate("2026-10-06"), "Oct 6");
  assert.equal(shortDate("2026-10-06", { weekday: true }), "Tue, Oct 6");
  assert.equal(weekdayIndex("2026-10-12"), 0); // Monday
  assert.equal(weekdayIndex("2026-10-18"), 6); // Sunday
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-03-08", 1), "2026-03-09"); // DST day, plain dates
});

test("heatmap model trims closed hours and scales", () => {
  const data = {
    days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    cells: [
      { dow: 0, hour: 3, avg_net_sales: 0, avg_orders: 0 },
      { dow: 0, hour: 10, avg_net_sales: 100, avg_orders: 4 },
      { dow: 5, hour: 18, avg_net_sales: 400, avg_orders: 12 },
      { dow: 2, hour: 12, avg_net_sales: null, avg_orders: 2 },
    ],
  };
  const m = heatmapModel(data, "avg_net_sales");
  assert.equal(m.max, 400);
  assert.deepEqual(m.hours[0], 10);
  assert.equal(m.hours[m.hours.length - 1], 18);
  assert.equal(m.hours.length, 9);
  assert.equal(m.byKey.get("5:18").avg_orders, 12);
  const counts = heatmapModel(data, "avg_orders");
  assert.equal(counts.max, 12);
  assert.equal(counts.hours[0], 10);
  assert.ok(heatmapModel({ cells: [] }, "avg_orders").empty);
  assert.ok(heatmapModel(null, "avg_orders").empty);
});

test("heat steps and ink contrast", () => {
  assert.equal(heatStep(0, 10), 0);
  assert.equal(heatStep(null, 10), 0);
  assert.equal(heatStep(10, 10), HEAT_RAMP.length - 1);
  assert.equal(heatStep(0.001, 10), 1); // any sale is visibly not-empty
  assert.equal(heatInk(1), "#111827");
  assert.equal(heatInk(HEAT_RAMP.length - 1), "#ffffff");
});

test("day names", () => {
  assert.equal(dayName({ days: ["Mon", "Tue"] }, 1), "Tuesday");
  assert.equal(dayName({ days: ["Monday"] }, 0, { short: true }), "Mon");
  assert.equal(dayName(null, 6), "Sunday");
});

test("nice ticks", () => {
  assert.deepEqual(niceTicks(0, 950, 4), [0, 250, 500, 750, 1000]);
  assert.deepEqual(niceTicks(0, 0), [0, 1]);
  const t = niceTicks(21.3, 27.9, 4);
  assert.ok(t[0] <= 21.3 && t[t.length - 1] >= 27.9);
  assert.ok(t.length <= 8);
});

test("x labels never crowd", () => {
  assert.equal(labelEvery(12, 600, 44), 1);
  assert.equal(labelEvery(84, 300, 44), 13);
  const shown = (n, every) => Array.from({ length: n }, (_, i) => i).filter((i) => showXLabel(i, n, every));
  assert.deepEqual(shown(5, 1), [0, 1, 2, 3, 4]);
  assert.deepEqual(shown(10, 4), [0, 4, 9]); // 8 dropped: too close to 9
  assert.deepEqual(shown(9, 4), [0, 4, 8]);
  assert.deepEqual(shown(1, 1), [0]);
});

test("series colours are fixed, never cycled", () => {
  assert.equal(colourForIndex(0), SERIES_COLORS[0]);
  assert.equal(colourForIndex(SERIES_COLORS.length), OTHER_COLOR);
});

test("channels model: shares, totals, stable order", () => {
  const data = {
    channels: ["In-Store Purchase", "Online Ordering", "No Revenue Center"],
    weeks: [
      {
        week_start: "2026-09-28",
        total_net: 1000,
        by: [
          { channel: "In-Store Purchase", net_sales: 800, orders: 80, share: 0.8 },
          { channel: "Online Ordering", net_sales: 200, orders: 10, share: 0.2 },
        ],
      },
      {
        week_start: "2026-10-05",
        total_net: 1000,
        by: [
          { channel: "In-Store Purchase", net_sales: 700, orders: 70 },
          { channel: "Online Ordering", net_sales: 250, orders: 12 },
          { channel: "No Revenue Center", net_sales: 50, orders: 1 },
        ],
      },
    ],
  };
  const m = channelsModel(data);
  assert.deepEqual(m.keys.map((k) => k.key), data.channels);
  assert.equal(m.rows[1].shares["Online Ordering"], 0.25);
  assert.equal(m.rows[0].shares["No Revenue Center"], 0);
  assert.equal(m.overall[0].total, 1500);
  assert.equal(m.overall[0].share, 0.75);
  assert.equal(m.empty, false);
  const counts = channelsModel(data, { metric: "orders" });
  assert.equal(counts.rows[0].total, 90);
  // channels given as objects, or missing (biggest first)
  const m2 = channelsModel({ weeks: data.weeks });
  assert.equal(m2.keys[0].key, "In-Store Purchase");
  assert.equal(m2.keys[2].key, "No Revenue Center");
  const m3 = channelsModel({ channels: [{ name: "A" }], weeks: [{ week_start: "2026-10-05", by: [{ channel: "A", net_sales: 5 }] }] });
  assert.deepEqual(m3.keys.map((k) => k.key), ["A"]);
  assert.ok(channelsModel({ weeks: [] }).empty);
});

test("last sold wording", () => {
  assert.equal(lastSoldText("2026-10-13", "2026-10-13"), "Today");
  assert.equal(lastSoldText("2026-10-12", "2026-10-13"), "Yesterday");
  assert.equal(lastSoldText("2026-10-01", "2026-10-13"), "12 days ago");
  assert.equal(lastSoldText(null, "2026-10-13"), "Not in this window");
});

test("prep forecast basis, ranges and text", () => {
  assert.equal(forecastBasis({ date: "2026-10-17" }), "Based on the last 8 Saturdays");
  assert.equal(forecastBasis({ date: "2026-10-17", basis: "Based on the last 6 Saturdays" }), "Based on the last 6 Saturdays");
  assert.equal(rangeText({ low: 4.2, high: 9.6 }), "4-10");
  assert.equal(rangeText({ low: 5, high: 5.2 }), "5");
  assert.equal(rangeText({}), "");
  const text = prepForecastText({
    date: "2026-10-17",
    items: [
      { name: "Chicken Biryani", expected_qty: 23.6, low: 18, high: 30 },
      { name: "Samosa", expected_qty: 40, low: null, high: null },
    ],
  });
  assert.match(text, /^Prep forecast - Saturday, October 17, 2026/);
  assert.match(text, /Chicken Biryani: 24 \(range 18-30\)/);
  assert.match(text, /Samosa: 40$/m);
  assert.ok(!/[\u2013\u2014]/.test(text));
  assert.match(prepForecastText({ date: "2026-10-17", items: [] }), /No kitchen items expected/);
});

test("peak hours", () => {
  const bh = [
    { hour: 11, qty: 2 },
    { hour: 12, qty: 6 },
    { hour: 18, qty: 7 },
    { hour: 19, qty: 0 },
  ];
  assert.deepEqual(peakHours(bh, 2), [12, 18]);
  assert.deepEqual(peakHours(null), []);
});

test("minutes open wording", () => {
  assert.equal(minutesText(45), "45 min");
  assert.equal(minutesText(205), "3 h 25 min");
  assert.equal(minutesText(119.7), "2 h");
  assert.equal(minutesText(180), "3 h");
  assert.equal(minutesText(3000), "2 days");
  assert.equal(minutesText(null), "-");
});

test("errors, rows, search", () => {
  assert.equal(errorKind({ status: 403 }), "forbidden");
  assert.equal(errorKind({ status: 429 }), "busy");
  assert.equal(errorKind(new TypeError("Failed to fetch")), "network");
  assert.equal(errorKind({ status: 500 }), "server");
  assert.ok(hasRows({ top: [], bottom: [1] }, ["top", "bottom"]));
  assert.ok(!hasRows({ top: [] }, ["top", "bottom"]));
  assert.ok(!hasRows(null, ["top"]));
  const rows = [{ name: "Chai", group: "Drinks" }, { name: "Samosa", group: "Snacks" }];
  assert.equal(filterRows(rows, "drink", ["name", "group"]).length, 1);
  assert.equal(filterRows(rows, "  ", ["name"]).length, 2);
});

test("channels beyond the five biggest fold into one grey Other", () => {
  const names = ["In-store", "Pick up", "Online ordering", "DoorDash", "Catering", "Uber Eats", "Other"];
  const data = {
    channels: names,
    weeks: [{ week_start: "2026-10-05", partial: false, days: 7, by: names.map((n, i) => ({ channel: n, net_sales: 100 - i * 10, orders: 1 })) }],
  };
  const m = channelsModel(data);
  assert.equal(m.keys.length, MAX_CHANNELS + 1);
  assert.equal(m.keys[m.keys.length - 1].key, "Other");
  assert.equal(m.keys[m.keys.length - 1].color, OTHER_COLOR);
  assert.equal(m.rows[0].values.Other, 50 + 40); // Uber Eats + Other
  const other = m.overall.find((o) => o.key === "Other");
  assert.deepEqual(other.members, ["Uber Eats", "Other"]);
  const sum = Object.values(m.rows[0].shares).reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(sum - 1) < 1e-9);
  // no Other at all when everything fits
  assert.ok(!channelsModel({ weeks: [{ week_start: "2026-10-05", by: [{ channel: "In-store", net_sales: 5 }] }] }).keys.some((k) => k.key === "Other"));
});

test("week titles mark the week so far", () => {
  assert.equal(weekTitle({ week_start: "2026-10-12", partial: true, days: 1 }), "Week of Oct 12, 2026 (so far, 1 day)");
  assert.equal(weekTitle({ week_start: "2026-10-05", partial: false }), "Week of Oct 5, 2026");
});

test("stale note says why", () => {
  assert.equal(staleText({ stale: false }), null);
  assert.match(staleText({ stale: true, configured: false }), /isn't set up/);
  assert.match(staleText({ stale: true, configured: true, filling: true, coverage: { dates_expected: 10, dates_cached: 7 } }), /still being read.*7 of 10 days/);
  assert.equal(
    staleText({ stale: true, configured: true, filling: false, coverage: { dates_expected: 30, dates_cached: 29 } }),
    "29 of 30 days are saved; the rest are added automatically.",
  );
  assert.doesNotMatch(staleText({ stale: true, configured: true, filling: false, coverage: { dates_expected: 2, dates_cached: 0 } }), /background/);
  assert.match(staleText({ stale: true, configured: true, coverage: { dates_expected: 5, dates_cached: 5 } }), /didn't answer/);
});

test("stale note prefers the server's note", () => {
  const note = "Oct 13 isn't in yet: Toast lets us read check details 30 times a day and today's are used up. It's added automatically by 4:30 AM tomorrow.";
  const data = { stale: true, configured: true, filling: true, note, coverage: { dates_expected: 30, dates_cached: 29, pending: "waiting_budget" } };
  assert.equal(staleText(data), note);
  // Odd checks, 1 day: the note, never "0 of 1 days".
  assert.equal(staleText({ stale: true, configured: true, note: "Oct 13 isn't in yet.", coverage: { dates_expected: 1, dates_cached: 0 } }), "Oct 13 isn't in yet.");
  // A note on a fresh answer is ignored; an empty note falls back.
  assert.equal(staleText({ stale: false, note: "x" }), null);
  assert.match(staleText({ stale: true, configured: false, note: "  " }), /isn't set up/);
});

test("store times", () => {
  assert.equal(storeTime("2026-10-10T18:06:31Z", "2026-10-10"), "2:06 PM");
  assert.equal(storeTime("2026-10-11T13:02:00Z", "2026-10-10"), "Oct 11, 9:02 AM");
  assert.equal(storeTime(null, "2026-10-10"), "");
  assert.equal(storeTime("2026-10-13T09:05:00Z", null, { withDate: true }), "Oct 13, 5:05 AM");
});

test("tiny shares and bad requests", () => {
  assert.equal(fmtShare(0.0003), "<0.1%");
  assert.equal(errorKind({ status: 400 }), "bad_request");
  assert.equal(errorKind({ status: 422 }), "bad_request");
});

test("menu performance splits into Menu trends and Best & worst sellers", () => {
  assert.deepEqual(menuViewTabs("trends").map((t) => t.key), ["rising", "falling", "not_sold"]);
  assert.deepEqual(menuViewTabs("sellers").map((t) => t.label), ["Top", "Bottom"]);
  assert.deepEqual(menuViewTabs("nope"), []);
  // Every old tab lives on exactly one of the two pages.
  const all = [...MENU_VIEWS.trends.tabs, ...MENU_VIEWS.sellers.tabs].sort();
  assert.deepEqual(all, MENU_TABS.map((t) => t.key).sort());
  assert.equal(menuTabFor("trends", "falling"), "falling");
  assert.equal(menuTabFor("trends", "top"), "rising");
  assert.equal(menuTabFor("trends", null), "rising");
  assert.equal(menuTabFor("sellers", "bottom"), "bottom");
  assert.equal(menuTabFor("sellers", "not_sold"), "top");
});

test("old Menu performance links redirect to the right page and tab", () => {
  assert.equal(menuPerformanceRedirect(""), "/insights/menu-trends");
  assert.equal(menuPerformanceRedirect(), "/insights/menu-trends");
  assert.equal(menuPerformanceRedirect("?tab=top"), "/insights/best-sellers?tab=top");
  assert.equal(menuPerformanceRedirect("?tab=bottom"), "/insights/best-sellers?tab=bottom");
  assert.equal(menuPerformanceRedirect("?tab=rising"), "/insights/menu-trends?tab=rising");
  assert.equal(menuPerformanceRedirect("?tab=not_sold"), "/insights/menu-trends?tab=not_sold");
  assert.equal(menuPerformanceRedirect("?tab=bogus"), "/insights/menu-trends");
  assert.equal(menuPerformanceRedirect("?days=7&tab=top"), "/insights/best-sellers?days=7&tab=top");
  // Both new pages are real routes.
  const paths = FEATURE_ROUTES.map((r) => r.path);
  assert.ok(paths.includes(MENU_VIEWS.trends.path));
  assert.ok(paths.includes(MENU_VIEWS.sellers.path));
  assert.ok(!paths.includes("/insights/menu"));
});

test("Insights hub (final pass): one menu link; main tiles, then Also in Toast", () => {
  const byKey = Object.fromEntries(FEATURE_ROUTES.map((r) => [r.key || r.feature, r]));
  // One "Insights" link in the Sales group; the pages are the hub's tiles.
  const group = NAV_GROUPS.find((g) => g.key === "sales");
  assert.ok(group.features.includes(INSIGHTS_HUB.key));
  assert.ok(!group.features.some((k) => byKey[k]?.feature === "sales_insights"));
  assert.deepEqual(
    INSIGHTS_MAIN_KEYS.map((k) => byKey[k].label),
    ["Daily compare", "Loss watch", "Odd checks", "Prep forecast", "Menu trends", "Add-ons", "Returning customers"],
  );
  assert.equal(ALSO_IN_POS.label, "Also in {pos}");
  assert.equal(ALSO_IN_POS.note, "Same numbers as {pos}'s own reports - kept here for convenience.");
  assert.deepEqual(
    ALSO_IN_POS_KEYS.map((k) => byKey[k].label),
    ["Sales channels", "Check size & guests", "Busy hours", "Best & worst sellers"],
  );
  // Every Insights page is on the hub exactly once.
  const listed = [...INSIGHTS_MAIN_KEYS, ...ALSO_IN_POS_KEYS].sort();
  const insightRoutes = FEATURE_ROUTES.filter((r) => r.feature === "sales_insights").map((r) => r.key).sort();
  assert.deepEqual(listed, insightRoutes);
  assert.deepEqual([...INSIGHTS_HUB.hubOf].sort(), insightRoutes);
  // Permissions unchanged: the same pages stay admin-only / sales-only.
  const flags = (k) => [!!byKey[k].adminOnly, !!byKey[k].salesOnly];
  assert.deepEqual(flags("insights_loss_watch"), [true, false]);
  assert.deepEqual(flags("insights_odd_checks"), [true, false]);
  for (const k of ["insights_compare", "insights_channels", "insights_check_trends", "insights_menu_trends", "insights_menu_sellers", "insights_addons"]) {
    assert.deepEqual(flags(k), [false, true], k);
  }
  for (const k of ["insights_heatmap", "insights_prep", "insights_returning"]) {
    assert.deepEqual(flags(k), [false, false], k);
  }
});

test("Insights home: the first main-section page the user can open", () => {
  const byPath = Object.fromEntries(FEATURE_ROUTES.map((r) => [r.path, r]));
  assert.equal(insightsHomePath(true), "/insights/daily");
  assert.equal(insightsHomePath(false), "/insights/prep-forecast");
  const home = byPath[insightsHomePath(false)];
  assert.ok(!home.salesOnly && !home.adminOnly);
});
