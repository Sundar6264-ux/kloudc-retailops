import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STORE_DETAIL_TABS,
  detailTab,
  healthSummary,
  integrationChip,
  jobChip,
  jobLabel,
  masterActions,
  masterStatus,
  masterUsernameError,
  settingError,
  settingsChanges,
} from "./consoleModel.js";

test("masters: never remove yourself or the last; reset two-step only for others who have it", () => {
  const me = { id: 1, is_me: true, two_step: true };
  const other = { id: 2, is_me: false, two_step: true };
  const noTwoStep = { id: 3, is_me: false, two_step: false };
  assert.deepEqual(masterActions(me, [me, other]), { remove: false, resetTwoStep: false });
  assert.deepEqual(masterActions(other, [me, other]), { remove: true, resetTwoStep: true });
  assert.deepEqual(masterActions(noTwoStep, [me, noTwoStep]), { remove: true, resetTwoStep: false });
  assert.equal(masterActions(other, [other]).remove, false);
  assert.equal(masterStatus({ must_change_password: true }).tone, "warn");
  assert.equal(masterStatus({ two_step: true }).label, "Two-step on");
});

test("masters: username rules match the server", () => {
  assert.equal(masterUsernameError("ab"), "Usernames are 3-64 characters.");
  assert.equal(masterUsernameError("has space"), "Use letters, digits, . _ - or @.");
  assert.equal(masterUsernameError("Second.Master"), null);
});

const SETTINGS = [
  { key: "signup_enabled", kind: "bool", value: true },
  { key: "support_email", kind: "email", value: "support@kloudc.com" },
  { key: "terms_url", kind: "url", value: "" },
  { key: "offboard_grace_days", kind: "int", value: 30, min: 7, max: 365 },
  { key: "default_plan", kind: "choice", value: "custom", choices: ["custom", "trial", "starter", "pro"] },
];

test("settings: only changed keys, typed", () => {
  assert.deepEqual(settingsChanges(SETTINGS, {}), {});
  assert.deepEqual(
    settingsChanges(SETTINGS, { signup_enabled: false, offboard_grace_days: "45", support_email: " support@kloudc.com ", terms_url: "" }),
    { signup_enabled: false, offboard_grace_days: 45 },
  );
});

test("settings: client checks mirror the server's", () => {
  const [, email, url, days, plan] = SETTINGS;
  assert.equal(settingError(days, "3"), "Enter a number from 7 to 365.");
  assert.equal(settingError(days, "4.5"), "Enter a whole number.");
  assert.equal(settingError(days, 60), null);
  assert.ok(settingError(email, "nope"));
  assert.equal(settingError(url, "http://x.example"), "Enter a full https:// link, or leave it empty.");
  assert.equal(settingError(url, ""), null);
  assert.ok(settingError(plan, "gold"));
});

test("store detail: five tabs, remembered tab falls back to Overview", () => {
  assert.deepEqual(STORE_DETAIL_TABS.map((t) => t.label), ["Overview", "Usage & plan", "Settings", "Data", "Audit"]);
  assert.equal(detailTab("data"), "data");
  assert.equal(detailTab("nope"), "overview");
});

test("store detail: health headline, job and integration chips", () => {
  assert.equal(healthSummary({ status: "suspended" }).tone, "warn");
  assert.equal(healthSummary({ status: "active", database: { reachable: false } }).tone, "bad");
  assert.match(healthSummary({ status: "active", database: { reachable: true, jobs: [{ status: "failed" }] } }).text, /1 background job failed/);
  assert.equal(healthSummary({ status: "active", database: { reachable: true, jobs: [{ status: "success" }] } }).tone, "good");
  // The store's own POS not set up: a warning, not "Everything looks fine."
  const ok = { status: "active", database: { reachable: true, jobs: [] } };
  const toastOff = healthSummary({ ...ok, pos_provider: "toast", integrations: [{ kind: "toast", configured: false }] });
  assert.equal(toastOff.tone, "warn");
  assert.equal(toastOff.text, "Toast isn't connected yet, so sales and stock aren't updating.");
  assert.match(healthSummary({ ...ok, pos_provider: "clover", integrations: [] }).text, /^Clover isn't connected/);
  assert.equal(healthSummary({ ...ok, pos_provider: "toast", integrations: [{ kind: "toast", configured: true }] }).tone, "good");
  assert.equal(healthSummary({ ...ok, pos_provider: "csv", integrations: [] }).tone, "good"); // no POS keys to add
  assert.deepEqual(jobChip("failed"), { label: "Failed", tone: "bad" });
  assert.equal(jobChip(undefined).label, "Never");
  assert.equal(jobLabel("sales_sync"), "Sales sync");
  assert.equal(integrationChip({ configured: false }).label, "Not set up");
  assert.equal(integrationChip({ configured: true, status: "ok" }).tone, "good");
});

test("Overview connections hide a POS the store doesn't use", async () => {
  const { visibleConnections } = await import("./consoleModel.js");
  const rows = [
    { kind: "toast", configured: false },
    { kind: "clover", configured: false },
    { kind: "whapi", configured: false },
    { kind: "smtp", configured: true },
  ];
  assert.deepEqual(visibleConnections(rows, "clover").map((r) => r.kind), ["clover", "whapi", "smtp"]);
  assert.deepEqual(visibleConnections(rows, "csv").map((r) => r.kind), ["whapi", "smtp"]);
  assert.deepEqual(visibleConnections([{ kind: "toast", configured: true }], "clover").map((r) => r.kind), ["toast"]);
  assert.deepEqual(visibleConnections(null, "toast"), []);
});
