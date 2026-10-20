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
