import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyLocal,
  changedCount,
  choiceOptions,
  choiceToEnabled,
  confirmOffText,
  groupDefaults,
  groupItems,
  isChanged,
  needsConfirm,
  overrideCount,
  storeChoice,
} from "./notificationsModel.js";

// Shaped like GET /api/notifications/prefs (Oct 15 server).
const PREFS = {
  items: [
    { key: "closing_checklist", label: "Closing checklist", description: "9:30 PM", enabled: true, group: "needs_action", default: true, essential: true, admin_only: false, explicit: false },
    { key: "task_due_soon", label: "Task due in 30 minutes", description: "", enabled: true, group: "needs_action", default: false, essential: false, admin_only: false, explicit: true },
    { key: "chat_message", label: "Chat messages", description: "", enabled: false, group: "team", default: true, essential: false, admin_only: false, explicit: true },
    { key: "announcement", label: "Announcements", description: "", enabled: true, group: "team", default: true, essential: true, admin_only: false, explicit: true },
    { key: "morning_summary", label: "Morning summary", description: "", enabled: true, group: "daily_summary", default: true, essential: false, admin_only: false, explicit: false },
  ],
  groups: [
    { key: "needs_action", label: "Needs action", description: "Something for you to do now." },
    { key: "team", label: "Team", description: "People talking to you." },
    { key: "daily_summary", label: "Daily summary", description: "The 9 AM Morning summary." },
  ],
};

test("groups in the server's order with their items; empty groups left out", () => {
  const g = groupItems(PREFS);
  assert.deepEqual(g.map((x) => x.label), ["Needs action", "Team", "Daily summary"]);
  assert.deepEqual(g[1].items.map((i) => i.key), ["chat_message", "announcement"]);
  assert.equal(g[0].description, "Something for you to do now.");
});

test("an older server (no group / groups) still shows every switch", () => {
  const old = { items: [{ key: "order_due", label: "Order due", description: "", enabled: true }] };
  const g = groupItems(old);
  assert.equal(g.length, 1);
  assert.equal(g[0].label, "Alerts");
  assert.equal(g[0].items[0].key, "order_due");
  assert.deepEqual(groupItems(null), []);
  // Groups the server didn't list still get their own heading, in app order.
  const g2 = groupItems({ items: [{ key: "loss_watch", label: "Loss watch", enabled: false, group: "admin" }] });
  assert.equal(g2[0].label, "Admin");
});

test("changed = own choice different from the default", () => {
  assert.equal(isChanged(PREFS.items[0]), false); // no own choice
  assert.equal(isChanged(PREFS.items[1]), true); // turned on, default off
  assert.equal(isChanged(PREFS.items[3]), false); // chose the same as the default
  assert.equal(isChanged({ key: "x", enabled: false, explicit: true }), false); // old server: no `default`
  assert.equal(changedCount(PREFS.items), 2);
  assert.equal(changedCount(undefined), 0);
});

test("essential alerts ask before switching off, never before switching on", () => {
  assert.equal(needsConfirm(PREFS.items[0], false), true);
  assert.equal(needsConfirm(PREFS.items[0], true), false);
  assert.equal(needsConfirm(PREFS.items[2], false), false);
  assert.equal(needsConfirm({ ...PREFS.items[0], enabled: false }, true), false);
  assert.match(confirmOffText(PREFS.items[0]), /^Turn off "Closing checklist"\? 9:30 PM You can switch it back on/);
});

test("optimistic flip marks it as the user's own choice", () => {
  const next = applyLocal(PREFS, "morning_summary", false);
  const item = next.items.find((i) => i.key === "morning_summary");
  assert.equal(item.enabled, false);
  assert.equal(item.explicit, true);
  assert.equal(isChanged(item), true);
  assert.equal(PREFS.items[4].enabled, true, "the original isn't mutated");
  assert.equal(next.groups, PREFS.groups);
});

// Shaped like GET /api/notifications/defaults.
const DEFAULTS = {
  items: [
    { key: "morning_summary", label: "Morning summary", group: "daily_summary", roles: ["staff", "admin"], catalog: { staff: false, admin: true }, store: { staff: true, admin: null }, effective: { staff: true, admin: true } },
    { key: "loss_watch", label: "Loss watch", group: "admin", admin_only: true, roles: ["admin"], catalog: { admin: false }, store: { admin: null }, effective: { admin: false } },
    { key: "chat_message", label: "Chat messages", group: "team", roles: ["staff", "admin"], catalog: { staff: true, admin: true }, store: { staff: false, admin: false }, effective: { staff: false, admin: false } },
  ],
  groups: PREFS.groups.concat([{ key: "admin", label: "Admin", description: "Store-level alerts for admins." }]),
};

test("store defaults: select value per role, PUT value, override count", () => {
  assert.equal(storeChoice(DEFAULTS.items[0], "staff"), "on");
  assert.equal(storeChoice(DEFAULTS.items[0], "admin"), "recommended");
  assert.equal(storeChoice(DEFAULTS.items[2], "admin"), "off");
  assert.equal(storeChoice(DEFAULTS.items[1], "staff"), "recommended");
  assert.equal(choiceToEnabled("on"), true);
  assert.equal(choiceToEnabled("off"), false);
  assert.equal(choiceToEnabled("recommended"), null);
  assert.equal(overrideCount(DEFAULTS), 3);
  assert.equal(overrideCount(null), 0);
  assert.deepEqual(choiceOptions(DEFAULTS.items[0], "staff").map((o) => o.label), ["Recommended (Off)", "On", "Off"]);
  assert.deepEqual(choiceOptions(DEFAULTS.items[0], "admin")[0].label, "Recommended (On)");
});

test("store defaults are listed in the same groups as the switches", () => {
  assert.deepEqual(groupDefaults(DEFAULTS).map((g) => g.key), ["team", "daily_summary", "admin"]);
});

test("Reset is offered once there's any own choice; essential store defaults confirm before off", async () => {
  const { hasOwnChoices, defaultNeedsConfirm } = await import("./notificationsModel.js");
  assert.equal(hasOwnChoices(PREFS.items), true);
  assert.equal(hasOwnChoices([{ key: "a", explicit: false }]), false);
  assert.equal(hasOwnChoices(undefined), false);
  const ess = { key: "closing_checklist", essential: true, roles: ["staff", "admin"], catalog: { staff: true, admin: true }, effective: { staff: true, admin: true } };
  assert.equal(defaultNeedsConfirm(ess, "staff", "off"), true);
  assert.equal(defaultNeedsConfirm(ess, "staff", "on"), false);
  assert.equal(defaultNeedsConfirm(ess, "staff", "recommended"), false); // recommended is on
  assert.equal(defaultNeedsConfirm({ ...DEFAULTS.items[0] }, "staff", "off"), false); // not essential
});
