// Two-step sign-in on the one login page (masters with TOTP; "As built - C").
//   node --test src/login/loginTotp.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { LOGIN_MESSAGES, cleanTotp, loginErrorMessage } from "./loginModel.js";

test("totp_required asks for the code, other 401s stay 'incorrect'", () => {
  const ask = loginErrorMessage({ status: 401, body: { error: "totp_required", message: "Enter the code." } });
  assert.deepEqual(ask, { kind: "totp", message: "Enter the code." });
  assert.equal(loginErrorMessage({ status: 401, body: { error: "totp_required" } }).message, LOGIN_MESSAGES.totp);
  // inside FastAPI's detail too
  assert.equal(loginErrorMessage({ status: 401, body: { detail: { error: "totp_required" } } }).kind, "totp");
  assert.equal(loginErrorMessage({ status: 401, body: { detail: "Incorrect store ID, username or password" } }).kind, "incorrect");
});

test("cleanTotp keeps at most 6 digits", () => {
  assert.equal(cleanTotp(" 12-34 56 78"), "123456");
  assert.equal(cleanTotp("abc"), "");
  assert.equal(cleanTotp(null), "");
});
