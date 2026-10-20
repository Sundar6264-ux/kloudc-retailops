import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STORE_SETTING_FIELDS,
  draftErrors,
  fieldError,
  hostingView,
  storeSettingsChanges,
  unavailableReason,
} from "./storeSettingsModel.js";

const field = (k) => STORE_SETTING_FIELDS.find((f) => f.key === k);

test("the page edits exactly the server's store-admin keys", () => {
  assert.deepEqual(
    STORE_SETTING_FIELDS.map((f) => f.key).sort(),
    ["ai_enabled", "brand_color", "brand_secondary_color", "email_from_name", "icon_url", "labels_url", "logo_url",
      "store_address", "store_email", "store_name", "store_phone", "store_short_name"],
  );
});

test("checks: name required, email, https links, hex colours, lengths", () => {
  assert.equal(fieldError(field("store_name"), " "), "Your store needs a name.");
  assert.equal(fieldError(field("store_phone"), ""), null);
  assert.ok(fieldError(field("store_email"), "nope"));
  assert.equal(fieldError(field("logo_url"), "http://x.example/logo.png"), "Enter a full https:// link.");
  assert.equal(fieldError(field("logo_url"), "https://cdn.example.com/logo.png"), null);
  assert.ok(fieldError(field("brand_color"), "blue"));
  assert.equal(fieldError(field("brand_color"), "#1E6FD9"), null);
  assert.ok(fieldError(field("store_short_name"), "x".repeat(41)));
  assert.deepEqual(draftErrors({ store_email: "bad", store_phone: "555" }), { store_email: "Enter an email address like orders@example.com." });
});

test("only changes are sent; cleared text is null", () => {
  const saved = { store_name: "Corner Market", store_phone: "555-0100", ai_enabled: false };
  assert.deepEqual(storeSettingsChanges(saved, {}), {});
  assert.deepEqual(storeSettingsChanges(saved, { store_name: " Corner Market ", store_phone: "", ai_enabled: true }), {
    store_phone: null,
    ai_enabled: true,
  });
});

test("single-store servers and staff get a clear state, not an error", () => {
  assert.equal(unavailableReason({ status: 409, body: { error: "platform_mode_required" } }), "single");
  assert.equal(unavailableReason({ status: 403, body: {} }), "forbidden");
  assert.equal(unavailableReason({ status: 500 }), null);
});

test("own server: hidden unless offered; a pending request replaces the form", () => {
  assert.deepEqual(hostingView({ enabled: false }), { show: false });
  assert.equal(hostingView({ enabled: true, hosting: "shared", requests: [] }).pending, null);
  assert.equal(hostingView({ enabled: true, requests: [{ status: "requested", id: 3 }] }).pending.id, 3);
  assert.equal(hostingView({ enabled: true, hosting: "dedicated", requests: [] }).dedicated, true);
});
