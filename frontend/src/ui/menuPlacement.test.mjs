// node --test src/ui/menuPlacement.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { fixedMenuPlace } from "./menuPlacement.js";

const phone = { width: 390, height: 844 };
const btn = (top, h = 36, left = 200, w = 150) => ({ top, bottom: top + h, left, right: left + w });

test("opens downward when the list fits below the button", () => {
  const s = fixedMenuPlace(btn(100), phone, "end", 150);
  assert.equal(s.position, "fixed");
  assert.equal(s.top, 142);
  assert.equal(s.bottom, undefined);
  assert.equal(s.right, 40);
  assert.equal(s.maxHeight, undefined);
});

test("flips upward near the bottom of a phone screen so PDF and CSV stay on screen", () => {
  const s = fixedMenuPlace(btn(760), phone, "end", 150);
  assert.equal(s.top, "auto");
  assert.equal(s.bottom, 844 - 760 + 6);
  assert.equal(s.maxHeight, undefined);
});

test("uses the measured height, not just the guess", () => {
  // 220px below: a 150px list fits, a 300px list doesn't (and there's more room above).
  assert.equal(fixedMenuPlace(btn(588), phone, "end", 150).top, 630);
  assert.equal(fixedMenuPlace(btn(588), phone, "end", 300).top, "auto");
});

test("no room either side: the bigger side, scrolling", () => {
  const short = { width: 390, height: 300 };
  const s = fixedMenuPlace(btn(150), short, "start", 400);
  assert.equal(s.top, "auto");
  assert.equal(s.left, 200);
  assert.equal(s.maxHeight, 150 - 6 - 8);
  assert.equal(s.overflowY, "auto");
});
