// Master console helpers (docs/PLATFORM_CONTRACT.md 12, 13.1, 14).
//   node --test src/platform/platformModel.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  LAST_TAB_KEY,
  PLATFORM_TABS,
  approveBody,
  archiveConfirmed,
  auditDetails,
  auditQuery,
  hostingActions,
  hostingStatusChip,
  isHostingDisabled,
  matchesSearch,
  platformErrorMessage,
  readLastTab,
  rememberTab,
  signupStatusChip,
  storeActions,
  storeStatusChip,
} from "./platformModel.js";

test("tabs: Requests, Stores, Hosting, Audit; the last one is remembered", () => {
  assert.deepEqual(
    PLATFORM_TABS.map((t) => t.label),
    ["Requests", "Stores", "Hosting", "Audit"],
  );
  const data = {};
  const s = { getItem: (k) => data[k] ?? null, setItem: (k, v) => (data[k] = v) };
  assert.equal(readLastTab(s), "requests");
  rememberTab(s, "audit");
  assert.equal(data[LAST_TAB_KEY], "audit");
  assert.equal(readLastTab(s), "audit");
  rememberTab(s, "nope");
  assert.equal(readLastTab(s), "audit");
  const broken = {
    getItem() {
      throw new Error("x");
    },
    setItem() {
      throw new Error("x");
    },
  };
  assert.equal(readLastTab(broken), "requests");
  assert.doesNotThrow(() => rememberTab(broken, "stores"));
});

test("status chips", () => {
  assert.deepEqual(storeStatusChip("active"), { label: "Active", tone: "good" });
  assert.equal(storeStatusChip("failed").label, "Setup failed");
  assert.equal(storeStatusChip("odd").label, "odd");
  assert.equal(signupStatusChip("pending").label, "Waiting");
  assert.equal(hostingStatusChip("rolled_back").label, "Rolled back");
});

test("store row actions follow its status", () => {
  assert.deepEqual(storeActions({ status: "active", hosting: "shared" }), ["suspend", "archive", "settings", "hosting"]);
  assert.deepEqual(storeActions({ status: "active", hosting: "dedicated" }), ["suspend", "archive", "settings"]);
  assert.deepEqual(storeActions({ status: "suspended" }), ["resume", "archive", "settings"]);
  assert.deepEqual(storeActions({ status: "failed" }), ["retry", "archive"]);
  assert.deepEqual(storeActions({ status: "provisioning" }), []);
  assert.deepEqual(storeActions({ status: "archived" }), []);
  assert.deepEqual(storeActions(null), []);
});

test("hosting request actions; rollback only within 7 days", () => {
  const now = Date.parse("2026-10-18T12:00:00Z");
  assert.deepEqual(hostingActions({ status: "requested" }, { now }), ["approve", "reject"]);
  assert.deepEqual(hostingActions({ status: "approved" }, { now }), ["complete"]);
  assert.deepEqual(hostingActions({ status: "done", completed_at: "2026-10-15T12:00:00Z" }, { now }), ["rollback"]);
  assert.deepEqual(hostingActions({ status: "done", completed_at: "2026-10-01T12:00:00Z" }, { now }), []);
  assert.deepEqual(hostingActions({ status: "rejected" }, { now }), []);
});

test("search: every word must match one of the fields", () => {
  const row = { code: "MAIN_STREET", name: "Main Street Market", status: "active" };
  const fields = ["code", "name", "status"];
  assert.ok(matchesSearch(row, "", fields));
  assert.ok(matchesSearch(row, "main active", fields));
  assert.ok(!matchesSearch(row, "main suspended", fields));
  assert.ok(matchesSearch(row, "  MARKET ", fields));
});

test("audit query: upper-case store, capped limit", () => {
  assert.equal(auditQuery({ store: " xyz ", action: "login.failed" }), "store=XYZ&action=login.failed&limit=100");
  assert.equal(auditQuery({ limit: 5000 }), "limit=200");
  assert.equal(auditQuery(), "limit=100");
});

test("approve body: only real overrides", () => {
  const req = { requested_code: "MAIN_STREET", timezone: "America/New_York" };
  assert.deepEqual(approveBody({ code: "main-street", timezone: "America/New_York" }, req), {});
  assert.deepEqual(approveBody({ code: "main_st", packId: "grocery_us", timezone: "America/Chicago", note: " ok " }, req), {
    code: "MAIN_ST",
    pack_id: "grocery_us",
    timezone: "America/Chicago",
    note: "ok",
  });
});

test("archive needs the code typed", () => {
  assert.ok(archiveConfirmed({ code: "XYZ" }, " xyz "));
  assert.ok(!archiveConfirmed({ code: "XYZ" }, "XY"));
  assert.ok(!archiveConfirmed(null, "XYZ"));
});

test("audit details read as one line", () => {
  assert.equal(auditDetails('{"pack_id":"x","steps":2}'), "pack_id: x · steps: 2");
  assert.equal(auditDetails("not json"), "not json");
  assert.equal(auditDetails({ a: { b: 1 } }), 'a: {"b":1}');
  assert.equal(auditDetails(null), "");
});

test("errors: the dedicated flag, 403, network", () => {
  const err = (status, body) => Object.assign(new Error("x"), { status, body });
  const off = err(409, { error: "dedicated_hosting_disabled" });
  assert.ok(isHostingDisabled(off));
  assert.ok(isHostingDisabled(err(409, { detail: { error: "dedicated_hosting_disabled" } })));
  assert.match(platformErrorMessage(off), /switched off/);
  assert.match(platformErrorMessage(err(403, {})), /master admin/);
  assert.equal(platformErrorMessage(err(400, { error: "bad", message: "Code taken." })), "Code taken.");
  assert.match(platformErrorMessage(new TypeError("x")), /Can't reach/);
  assert.ok(!isHostingDisabled(err(400, {})));
});
