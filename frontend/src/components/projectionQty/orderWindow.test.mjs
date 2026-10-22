import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_ORDER_WINDOW,
  ORDER_WINDOW_KEY,
  readOrderWindow,
  windowWords,
  writeOrderWindow,
} from "./orderWindow.js";

function memory(initial = {}) {
  const m = new Map(Object.entries(initial));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m };
}

test("defaults match the app: 3 months of sales, order for 1 month", () => {
  assert.deepEqual(readOrderWindow(null), { lookbackDays: 90, horizonDays: 30 });
  assert.deepEqual(readOrderWindow(memory()), DEFAULT_ORDER_WINDOW);
});

test("the projection's window is what the cart reads back", () => {
  const s = memory();
  writeOrderWindow(s, { lookbackDays: 90, horizonDays: 14 });
  assert.deepEqual(readOrderWindow(s), { lookbackDays: 90, horizonDays: 14 });
});

test("bad stored values fall back part by part, never throw", () => {
  assert.deepEqual(readOrderWindow(memory({ [ORDER_WINDOW_KEY]: "{oops" })), DEFAULT_ORDER_WINDOW);
  assert.deepEqual(
    readOrderWindow(memory({ [ORDER_WINDOW_KEY]: JSON.stringify({ lookbackDays: 28, horizonDays: 999 }) })),
    { lookbackDays: 28, horizonDays: 30 }
  );
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.deepEqual(readOrderWindow(throwing), DEFAULT_ORDER_WINDOW);
  writeOrderWindow(throwing, { lookbackDays: 90, horizonDays: 30 });
  const s = memory();
  writeOrderWindow(s, { lookbackDays: 0, horizonDays: 30 });
  assert.equal(s.m.size, 0);
});

test("window words", () => {
  assert.equal(windowWords(30), "1 month");
  assert.equal(windowWords(7), "1 week");
  assert.equal(windowWords(12), "12 days");
});
