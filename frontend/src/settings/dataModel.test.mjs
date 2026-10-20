// node --test src/settings/dataModel.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import {
  confirmMatches,
  dailyBars,
  dataErrorMessage,
  exportRow,
  exportsState,
  formatBytes,
  formatCount,
  limitBars,
  offboardingActions,
  offboardingState,
  planNotice,
  timeAgo,
  timelineRows,
  usageTiles,
} from "./dataModel.js";

test("bytes and counts read like people write them", () => {
  assert.equal(formatBytes(null), "Not measured yet");
  assert.equal(formatBytes(1), "1 byte");
  assert.equal(formatBytes(900), "900 bytes");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(25 * 1024 * 1024), "25 MB");
  assert.equal(formatBytes(3 * 1024 ** 3), "3 GB");
  assert.equal(formatCount(12345), "12,345");
  assert.equal(formatCount(undefined), "Not measured yet");
});

test("time ago", () => {
  const now = Date.parse("2026-10-20T04:00:00Z");
  assert.equal(timeAgo(null, now), "Never");
  assert.equal(timeAgo("2026-10-20T03:59:30Z", now), "Just now");
  assert.equal(timeAgo("2026-10-20T03:00:00Z", now), "1 hour ago");
  assert.equal(timeAgo("2026-10-19T22:00:00Z", now), "6 hours ago");
  assert.equal(timeAgo("2026-10-15T04:00:00Z", now), "5 days ago");
});

test("usage tiles: gauges, 30-day counters and last activity", () => {
  const tiles = usageTiles({ metrics: { users: 4, storage_bytes: 2048, orders_synced: 120 }, window_days: 30 });
  const by = Object.fromEntries(tiles.map((t) => [t.key, t]));
  assert.deepEqual(tiles.map((t) => t.key), [
    "users", "items", "storage_bytes", "database_bytes", "orders_synced", "pos_api_calls", "ai_calls", "last_activity_at",
  ]);
  assert.equal(by.users.value, "4");
  assert.equal(by.items.value, "Not measured yet");
  assert.equal(by.storage_bytes.value, "2 KB");
  assert.equal(by.orders_synced.value, "120");
  assert.equal(by.pos_api_calls.value, "0");
  assert.match(by.orders_synced.hint, /last 30 days/);
  assert.equal(by.last_activity_at.value, "Never");
});

test("daily bars fill missing days and scale to the busiest day", () => {
  assert.deepEqual(dailyBars([], "orders_synced"), []);
  const bars = dailyBars(
    [{ day: "2026-10-19", orders_synced: 50 }, { day: "2026-10-17", orders_synced: 100 }],
    "orders_synced",
    4,
  );
  assert.deepEqual(bars.map((b) => [b.day, b.value, b.height]), [
    ["2026-10-16", 0, 0], ["2026-10-17", 100, 100], ["2026-10-18", 0, 0], ["2026-10-19", 50, 50],
  ]);
});

test("limit bars and the store admin's notice", () => {
  const states = [
    { key: "users", label: "Users", used: 6, limit: 5, unit: "count", state: "over" },
    { key: "items", label: "Items", used: 1700, limit: 2000, unit: "count", state: "near" },
    { key: "storage_bytes", label: "File storage", used: null, limit: 1024 ** 3, unit: "bytes", state: "ok" },
  ];
  const bars = limitBars(states);
  assert.deepEqual(bars.map((b) => [b.pct, b.tone, b.stateText]), [
    [100, "bad", "Over the limit"], [85, "warn", "Near the limit"], [0, "good", "Within the limit"],
  ]);
  assert.equal(bars[0].text, "6 of 5");
  assert.equal(bars[2].text, "Not measured yet of 1 GB");
  const over = planNotice({ label: "Trial", limit_states: states });
  assert.equal(over.tone, "bad");
  assert.match(over.text, /over the limit for users on the Trial plan/);
  assert.match(over.text, /not affected/);
  const near = planNotice({ label: "Starter", limit_states: states.slice(1) });
  assert.equal(near.tone, "warn");
  assert.match(near.text, /near the limit for items/);
  assert.equal(planNotice({ label: "Custom", limit_states: [] }), null);
  assert.equal(planNotice({ label: "Pro", limit_states: [{ ...states[2] }] }), null);
});

test("export rows: names, status, Download only when ready, Try again on failure", () => {
  const ready = exportRow({ id: 3, status: "ready", reason: "manual", size_bytes: 2048, tables: 40, files: 3,
    created_at: "2026-10-19T12:00:00Z", expires_at: "2026-10-26T12:00:00Z" }, "UTC");
  assert.equal(ready.title, "Data export");
  assert.equal(ready.statusLabel, "Ready");
  assert.ok(ready.canDownload && !ready.busy);
  assert.match(ready.meta, /2 KB · 40 tables · 3 files · kept until Oct 26, 2026/);
  const final = exportRow({ id: 4, status: "running", reason: "offboarding", created_at: "2026-10-19T12:00:00Z" });
  assert.equal(final.title, "Final data export");
  assert.ok(final.busy && !final.canDownload);
  const failed = exportRow({ id: 5, status: "failed", error: "disk full" });
  assert.equal(failed.error, "disk full");
  assert.equal(exportRow({ id: 6, status: "expired" }).statusLabel, "Expired");

  assert.deepEqual(exportsState([{ status: "running" }]), { running: true, canRequest: false, retry: false, pollMs: 2000 });
  assert.deepEqual(exportsState([{ status: "failed" }, { status: "ready" }]), { running: false, canRequest: true, retry: true, pollMs: 0 });
  assert.deepEqual(exportsState(undefined), { running: false, canRequest: true, retry: false, pollMs: 0 });
});

test("off-boarding states and actions", () => {
  const active = { status: "active", state: null };
  assert.equal(offboardingState(active).label, "Not scheduled");
  assert.deepEqual(offboardingActions(active), { start: true, cancel: false, purge: false, purgeBlockedReason: null });

  const archived = { status: "archived", state: null };
  assert.equal(offboardingState(archived).label, "Archived");
  assert.ok(offboardingActions(archived).start);

  const scheduled = { status: "archived", state: "scheduled", purge_after: "2026-11-18T12:00:00Z", final_export: { status: "ready" } };
  assert.equal(offboardingState(scheduled, "UTC").label, "Deleting on Nov 18, 2026");
  assert.deepEqual(offboardingActions(scheduled), { start: false, cancel: true, purge: true, purgeBlockedReason: null });
  const pilot = offboardingState({ ...scheduled, pilot_mode: true }, "UTC");
  assert.match(pilot.text, /pilot mode/);

  const busy = { ...scheduled, final_export: { status: "running" } };
  assert.equal(offboardingActions(busy).purge, false);
  assert.match(offboardingActions(busy).purgeBlockedReason, /still being made/);
  assert.equal(offboardingActions({ ...scheduled, hosting: "dedicated" }).purge, false);

  const deleted = { status: "archived", state: "deleted", purged_at: "2026-11-18T12:00:00Z" };
  assert.equal(offboardingState(deleted, "UTC").label, "Deleted");
  assert.deepEqual(offboardingActions(deleted), { start: false, cancel: false, purge: false, purgeBlockedReason: null });
});

test("timeline rows and the typed Store ID", () => {
  const rows = timelineRows({
    timeline: [
      { at: "2026-10-19T12:00:00Z", action: "store.archived", actor_name: "platform:owner" },
      { at: "2026-11-18T09:00:00Z", action: "store.data_deleted", actor_name: "system:offboarding" },
    ],
  }, "UTC");
  assert.deepEqual(rows.map((r) => [r.label, r.who]), [["Archived", "platform:owner"], ["Store data deleted", "Automatic"]]);
  assert.ok(confirmMatches(" alpha ", "ALPHA"));
  assert.ok(!confirmMatches("ALPH", "ALPHA"));
  assert.ok(!confirmMatches("", ""));
});

test("error messages", () => {
  assert.equal(dataErrorMessage({ body: { message: "A data export is already being made." } }), "A data export is already being made.");
  assert.equal(dataErrorMessage({ status: 403 }), "Only the store's owner or an admin can do this.");
  assert.equal(dataErrorMessage({ status: 429 }), "Too many requests. Try again in a minute.");
  assert.equal(dataErrorMessage(new Error("boom")), "boom");
});
