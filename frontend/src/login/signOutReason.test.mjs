import { test } from "node:test";
import assert from "node:assert/strict";
import { rememberSignOutReason, suspendedMessage, takeSignOutReason } from "./signOutReason.js";

test("a suspended store's 403 gives the maintenance message", () => {
  assert.equal(
    suspendedMessage({ status: 403, body: { error: "store_suspended", message: "Back at 6 am after an upgrade." } }),
    "Back at 6 am after an upgrade.",
  );
  assert.equal(suspendedMessage({ status: 403, body: { error: "store_suspended" } }), "This store is paused. Contact support.");
  assert.equal(suspendedMessage({ status: 403, body: { error: "master_only" } }), null);
  assert.equal(suspendedMessage({ status: 401, body: {} }), null);
});

test("the reason is shown once", () => {
  const data = {};
  const s = { getItem: (k) => data[k] ?? null, setItem: (k, v) => (data[k] = v), removeItem: (k) => delete data[k] };
  rememberSignOutReason(s, "Paused for maintenance.");
  assert.equal(takeSignOutReason(s), "Paused for maintenance.");
  assert.equal(takeSignOutReason(s), null);
});
