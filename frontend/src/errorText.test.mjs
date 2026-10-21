// G25 + G27: the website shows the server's plain sentence, never a JSON
// dump of a validation list or a bare "Not Found".
import { test } from "node:test";
import assert from "node:assert/strict";
import { errorText } from "./errorText.js";

test("a form error (422 list) shows the plain message", () => {
  const body = {
    detail: [{ loc: ["body", "qty"], msg: "Must be a whole number.", type: "int_parsing" }],
    error: "invalid_input",
    message: "Quantity: must be a whole number.",
  };
  assert.equal(errorText(body), "Quantity: must be a whole number.");
});

test("a bare phrase gives way to the sentence", () => {
  assert.equal(
    errorText({ detail: "Not Found", error: "not_found", message: "That page or item wasn't found." }),
    "That page or item wasn't found.",
  );
  assert.equal(errorText({ detail: "Method Not Allowed", message: "Nope." }), "Nope.");
});

test("a real sentence in detail is kept", () => {
  assert.equal(errorText({ detail: "That username is already taken", message: "x" }), "That username is already taken");
  assert.equal(errorText({ detail: "Current password is incorrect" }), "Current password is incorrect");
});

test("older servers: platform shape, a list without message, nothing at all", () => {
  assert.equal(errorText({ error: "invalid", message: "Main colour: pick a colour." }), "Main colour: pick a colour.");
  assert.equal(errorText({ detail: { message: "Inside detail." } }), "Inside detail.");
  assert.equal(errorText({ detail: [{ msg: "A" }, { msg: "B" }] }), "A B");
  assert.equal(errorText(null, "Fallback."), "Fallback.");
  assert.equal(errorText({}, "Fallback."), "Fallback.");
});
