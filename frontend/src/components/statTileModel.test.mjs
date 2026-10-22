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
