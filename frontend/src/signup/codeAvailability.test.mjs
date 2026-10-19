// The live Store ID check against the real sign-up API ("As built - C":
// reason is a code, message the server's text).
//   node --test src/signup/codeAvailability.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { CODE_REASON_TEXT, availabilityReason } from "./signupModel.js";

test("reason codes read in Store ID words", () => {
  assert.equal(availabilityReason({ available: false, reason: "taken", message: "That code is already used by another store." }), CODE_REASON_TEXT.taken);
  assert.equal(availabilityReason({ available: false, reason: "reserved" }), "That Store ID is reserved.");
  assert.equal(availabilityReason({ available: false, reason: "requested" }), CODE_REASON_TEXT.requested);
  assert.equal(availabilityReason({ available: false, reason: "invalid" }), CODE_REASON_TEXT.invalid);
});

test("unknown codes use the server's message; old sentence reasons still show", () => {
  assert.equal(availabilityReason({ available: false, reason: "other", message: "Nope." }), "Nope.");
  assert.equal(availabilityReason({ available: false, reason: "That Store ID is taken." }), "That Store ID is taken.");
  assert.equal(availabilityReason({ available: false, reason: "weird" }), null);
});

test("available or no answer: no reason", () => {
  assert.equal(availabilityReason({ available: true }), null);
  assert.equal(availabilityReason(null), null);
});
