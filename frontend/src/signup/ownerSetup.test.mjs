// The owner's one-time set-up link page (/#/setup?store=&token=; "As built - C").
//   node --test src/signup/ownerSetup.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { ownerSetupParams, ownerSetupProblem } from "./signupModel.js";

const TOKEN = "x".repeat(43); // secrets.token_urlsafe(32)

test("link values are normalised and checked", () => {
  assert.deepEqual(ownerSetupParams(" main-street ", ` ${TOKEN} `), { store: "MAIN_STREET", token: TOKEN });
  assert.equal(ownerSetupParams("", TOKEN), null);
  assert.equal(ownerSetupParams("MAIN_STREET", ""), null);
  assert.equal(ownerSetupParams("MAIN_STREET", "short"), null);
  assert.equal(ownerSetupParams("X", TOKEN), null);
  assert.equal(ownerSetupParams("bad code!", TOKEN), null);
});

test("passwords: length, match", () => {
  assert.equal(ownerSetupProblem("short", "short"), "At least 10 characters.");
  assert.equal(ownerSetupProblem("long-enough-1", "long-enough-2"), "The passwords don't match.");
  assert.equal(ownerSetupProblem("x".repeat(257), "x".repeat(257)), "At most 256 characters.");
  assert.equal(ownerSetupProblem("long-enough-1", "long-enough-1"), null);
});
