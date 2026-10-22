// node --test src/components/statTileModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { tileErrorText } from "./statTileModel.js";

test("a POS that isn't connected says so; other failures stay 'Couldn't load'", () => {
  assert.equal(
    tileErrorText("Toast isn't connected yet, so today's sales can't be shown. Add the Toast keys on the website: Settings › Integrations."),
    "Not connected",
  );
  assert.equal(tileErrorText("Toast didn't answer a moment ago - try again shortly."), "Couldn't load");
  assert.equal(tileErrorText(undefined), "Couldn't load");
  assert.equal(tileErrorText(""), "Couldn't load");
});

test("Open checks: never checked is not 'all closed'", async () => {
  const { openChecksTile } = await import("./statTileModel.js");
  assert.deepEqual(openChecksTile({ count: 0, orders: [], last_refreshed_at: null }, "Toast"), {
    value: "-",
    sub: "Not checked yet",
    tone: "neutral",
  });
  assert.equal(openChecksTile({ count: 0, orders: [], last_refreshed_at: "2026-10-22T12:00:00Z" }, "Toast").sub, "all closed");
  assert.equal(openChecksTile({ count: 0, orders: [] }, "Toast").sub, "all closed"); // an older server
  const open = openChecksTile({ count: 3, last_refreshed_at: null }, "Toast");
  assert.equal(open.sub, "not yet closed in Toast");
  assert.equal(open.tone, "warn");
  assert.equal(openChecksTile(null, "Toast").value, null);
});
