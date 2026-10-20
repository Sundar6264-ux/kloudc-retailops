// Website ready A1: the website's one "today" helper is the STORE's date.
import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_STORE_TIME_ZONE, setStoreTimeZone, storeToday } from "./format.js";

test("storeToday() across a UTC midnight follows the store's zone", () => {
  const justAfterUtcMidnight = new Date("2026-10-20T01:30:00Z");
  setStoreTimeZone("America/New_York");
  assert.equal(storeToday(justAfterUtcMidnight), "2026-10-19"); // still the 19th in New York
  setStoreTimeZone("Asia/Tokyo");
  assert.equal(storeToday(justAfterUtcMidnight), "2026-10-20");
  setStoreTimeZone("America/Los_Angeles");
  assert.equal(storeToday(new Date("2026-10-20T06:59:00Z")), "2026-10-19");
  assert.equal(storeToday(new Date("2026-10-20T07:00:00Z")), "2026-10-20");
  setStoreTimeZone(DEFAULT_STORE_TIME_ZONE);
});

test("storeToday() defaults to now", () => {
  assert.match(storeToday(), /^\d{4}-\d{2}-\d{2}$/);
});

test("guard: no browser-clock 'today' in the website", async () => {
  const { readFileSync, readdirSync, statSync } = await import("node:fs");
  const { join } = await import("node:path");
  const root = new URL("./", import.meta.url).pathname;
  const walk = (d) =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n);
      return statSync(p).isDirectory() ? walk(p) : /\.(jsx?|mjs)$/.test(n) && !n.endsWith(".test.mjs") ? [p] : [];
    });
  const bad = [];
  for (const f of walk(root)) {
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      if (/^\s*(\/\/|\*)/.test(line)) return;
      // The UTC date of "now", or the device's own calendar date of "now".
      if (/new Date\(\)\.toISOString\(\)\.(slice|split|substring)/.test(line) || /new Date\(\)\.(getDate|toDateString|toLocaleDateString)\(/.test(line)) {
        bad.push(`${f.replace(root, "src/")}:${i + 1}`);
      }
    });
  }
  assert.deepEqual(bad, [], "use storeToday() / storeDateStr() from format.js");
});
