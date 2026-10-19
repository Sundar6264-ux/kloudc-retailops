// Master console helpers for the real platform API ("As built - C"): two-step sign-in, hosting off state.
//   node --test src/platform/consoleReal.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOTP_SETUP_MESSAGE, groupSecret, platformErrorMessage, totpSetupNeeded } from "./platformModel.js";

test("the panel opens itself only when TOTP is required and not enrolled", () => {
  assert.equal(totpSetupNeeded(null), false);
  assert.equal(totpSetupNeeded({ require_totp: false, user: { totp_enrolled: false } }), false);
  assert.equal(totpSetupNeeded({ require_totp: true, user: { totp_enrolled: false } }), true);
  assert.equal(totpSetupNeeded({ require_totp: true, user: { totp_enrolled: true } }), false);
  assert.equal(totpSetupNeeded({ require_totp: true }), true);
});

test("a 403 totp_setup_required says what to do, other 403s unchanged", () => {
  assert.equal(platformErrorMessage({ status: 403, body: { error: "totp_setup_required", message: "x" } }), TOTP_SETUP_MESSAGE);
  assert.equal(platformErrorMessage({ status: 403, body: { detail: "nope" } }), "Only a master admin can do that.");
});

test("secret shown in groups of 4", () => {
  assert.equal(groupSecret("JBSWY3DPEHPK3PXP"), "JBSW Y3DP EHPK 3PXP");
  assert.equal(groupSecret("ABCDEF"), "ABCD EF");
  assert.equal(groupSecret(null), "");
});

test("the hosting list's enabled flag shows the off state", async () => {
  const { hostingListOff } = await import("./platformModel.js");
  assert.equal(hostingListOff({ enabled: false, requests: [] }), true);
  assert.equal(hostingListOff({ enabled: true, requests: [] }), false);
  assert.equal(hostingListOff([]), false);
  assert.equal(hostingListOff(null), false);
});

test("pack choices from the real server's pack list (and bare ids)", async () => {
  const { packOptions } = await import("./platformModel.js");
  assert.deepEqual(
    packOptions([
      { pack_id: "generic", label: "Generic grocery", private: false },
      { pack_id: "test_toast", label: "test_toast", private: false },
    ]),
    [
      { id: "generic", label: "Generic grocery (generic)" },
      { id: "test_toast", label: "test_toast" },
    ],
  );
  assert.deepEqual(packOptions(["grocery_us", "grocery_us"]), [{ id: "grocery_us", label: "grocery_us" }]);
  assert.deepEqual(packOptions({ packs: [{ id: "x" }] }), [{ id: "x", label: "x" }]);
  assert.deepEqual(packOptions(null), []);
});
