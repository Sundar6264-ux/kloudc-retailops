// Sign-up form checks and the pending page (docs/PLATFORM_CONTRACT.md 13.3, 14).
//   node --test src/signup/signupModel.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  COMMON_TIMEZONES,
  EMPTY_SIGNUP,
  PENDING_POLL_MAX_MS,
  RESERVED_CODES,
  codeProblem,
  passwordProblem,
  pendingUrl,
  pendingView,
  shouldPoll,
  signupBody,
  suggestCode,
  timezoneOptions,
  validateSignup,
} from "./signupModel.js";

const GOOD = {
  ...EMPTY_SIGNUP,
  store_name: "Main Street Market",
  requested_code: "main-street",
  contact_name: "Pat Owner",
  contact_email: " Pat@Example.COM ",
  timezone: "America/New_York",
  pos_provider: "toast",
  owner_username: "pat",
  owner_password: "correct horse",
  owner_password_confirm: "correct horse",
  accept_terms: true,
};

test("a complete form has no problems", () => {
  assert.deepEqual(validateSignup(GOOD), {});
});

test("every required field is checked", () => {
  const errors = validateSignup(EMPTY_SIGNUP);
  for (const k of [
    "store_name",
    "requested_code",
    "contact_name",
    "contact_email",
    "timezone",
    "owner_username",
    "owner_password",
    "accept_terms",
  ]) {
    assert.ok(errors[k], k);
  }
  assert.equal(errors.pos_provider, undefined, "toast is the default");
});

test("password rules: at least 10, not the username, confirmed", () => {
  assert.match(passwordProblem("short"), /10 characters/);
  assert.match(passwordProblem("patpatpat1", "PATPATPAT1"), /username/);
  assert.equal(passwordProblem("0123456789"), null);
  const e = validateSignup({ ...GOOD, owner_password_confirm: "nope" });
  assert.equal(e.owner_password_confirm, "The passwords don't match.");
});

test("Store IDs: pattern, reserved words, and the live availability answer", () => {
  assert.equal(codeProblem("ok_store"), null);
  assert.match(codeProblem("ab"), /At least 3/);
  assert.match(codeProblem("A".repeat(25)), /At most 24/);
  assert.match(codeProblem("_abc"), /starting with a letter/);
  assert.match(codeProblem("my store!"), /Letters, digits/);
  for (const r of RESERVED_CODES) assert.match(codeProblem(r.toLowerCase()), /reserved/, r);
  assert.match(codeProblem(""), /Choose/);
  const taken = validateSignup(GOOD, { codeAvailability: { available: false, reason: "Taken." } });
  assert.equal(taken.requested_code, "Taken.");
  const free = validateSignup(GOOD, { codeAvailability: { available: true } });
  assert.equal(free.requested_code, undefined);
});

test("a store name suggests a Store ID", () => {
  assert.equal(suggestCode("Main Street Market"), "MAIN_STREET_MARKET");
  assert.equal(suggestCode("Joe's Deli & Grill"), "JOE_S_DELI_AND_GRILL");
  assert.equal(suggestCode("  "), "");
  assert.equal(suggestCode("A very long store name that goes on"), "A_VERY_LONG_STORE_NAME_T");
  assert.ok(suggestCode("A very long store name that goes on").length <= 24);
});

test("email, notes length and name length", () => {
  assert.ok(validateSignup({ ...GOOD, contact_email: "nope" }).contact_email);
  assert.ok(validateSignup({ ...GOOD, notes: "x".repeat(2001) }).notes);
  assert.ok(validateSignup({ ...GOOD, store_name: "x".repeat(81) }).store_name);
  assert.ok(validateSignup({ ...GOOD, owner_username: "a b" }).owner_username);
  assert.ok(validateSignup({ ...GOOD, pos_provider: "square" }).pos_provider);
});

test("the request body: normalised, optional blanks left out, no confirmation", () => {
  const body = signupBody({ ...GOOD, contact_phone: "  ", notes: " Two locations soon " });
  assert.deepEqual(body, {
    store_name: "Main Street Market",
    requested_code: "MAIN_STREET",
    contact_name: "Pat Owner",
    contact_email: "pat@example.com",
    timezone: "America/New_York",
    pos_provider: "toast",
    owner_username: "pat",
    owner_password: "correct horse",
    accept_terms: true,
    website: "",
    notes: "Two locations soon",
  });
  assert.ok(!("owner_password_confirm" in body));
  assert.equal(signupBody({ ...GOOD, website: "http://spam" }).website, "http://spam", "the honeypot goes as typed");
});

test("time zones: the common US ones first, then the browser's, no repeats", () => {
  const list = timezoneOptions(["Europe/London", "America/Chicago", "Asia/Kolkata"]);
  assert.deepEqual(list.slice(0, COMMON_TIMEZONES.length), COMMON_TIMEZONES);
  assert.deepEqual(list.slice(COMMON_TIMEZONES.length), ["Europe/London", "Asia/Kolkata"]);
  assert.deepEqual(timezoneOptions(undefined), COMMON_TIMEZONES);
});

test("the pending page: each status, and when to stop polling", () => {
  assert.equal(pendingView({ status: "pending" }).done, false);
  const approved = pendingView({ status: "approved", store_code: "MAIN_STREET" });
  assert.equal(approved.storeCode, "MAIN_STREET");
  assert.match(approved.text, /Store ID MAIN_STREET/);
  const rejected = pendingView({ status: "rejected", message: "Outside our service area." });
  assert.equal(rejected.text, "Outside our service area.");
  assert.equal(pendingView({ status: "withdrawn" }).done, true);
  assert.equal(pendingView(null).state, "unknown");
  assert.equal(pendingUrl(7, "a b"), "/signup/pending?id=7&t=a%20b");

  const t0 = 1_000_000;
  assert.ok(shouldPoll({ done: false, visible: true, startedAt: t0, now: t0 + 1000 }));
  assert.ok(!shouldPoll({ done: false, visible: false, startedAt: t0, now: t0 + 1000 }), "hidden tab");
  assert.ok(!shouldPoll({ done: true, visible: true, startedAt: t0, now: t0 + 1000 }));
  assert.ok(!shouldPoll({ done: false, visible: true, startedAt: t0, now: t0 + PENDING_POLL_MAX_MS }), "an hour");
});
