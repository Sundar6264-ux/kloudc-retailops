// node --test src/components/salesAccuracyModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { missingKeysLine } from "./salesAccuracyModel.js";

test("the missing-keys line is whole sentences", () => {
  assert.equal(
    missingKeysLine("Sales double-check is limited: Toast Analytics keys are missing", "Toast"),
    "Sales double-check is limited: Toast Analytics keys are missing. Add them in Setup › Integrations so late edits are caught. Until then the app re-checks the last 7 days once a day.",
  );
  assert.match(missingKeysLine("", "Toast"), /^Toast Analytics keys are missing\. Add them/);
  assert.match(missingKeysLine("Already a sentence.", "Toast"), /^Already a sentence\. Add them/);
  assert.doesNotMatch(missingKeysLine("x", "Toast"), /missing Add/);
});
