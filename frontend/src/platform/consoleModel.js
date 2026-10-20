// Pure helpers for the console's Masters and Platform settings tabs and the
// store detail drawer (website ready A2-A4). Tested by consoleModel.test.mjs.

// --- Masters -------------------------------------------------------------------------

// What a row may do: never remove yourself or the last master; reset
// two-step only for another master who has it on.
export function masterActions(master, all) {
  const active = (all || []).length;
  return {
    remove: !master.is_me && active > 1,
    resetTwoStep: !master.is_me && !!master.two_step,
  };
}

export function masterUsernameError(raw) {
  const u = String(raw || "").trim().toLowerCase();
  if (u.length < 3 || u.length > 64) return "Usernames are 3-64 characters.";
  if (!/^[a-z0-9._@-]+$/.test(u)) return "Use letters, digits, . _ - or @.";
  return null;
}

export function masterStatus(m) {
  if (m.must_change_password) return { label: "Must change password", tone: "warn" };
  if (!m.two_step) return { label: "Two-step off", tone: "neutral" };
  return { label: "Two-step on", tone: "good" };
}

// --- Platform settings ---------------------------------------------------------------

// The changed keys only, typed like the server wants them.
export function settingsChanges(settings, draft) {
  const out = {};
  for (const s of settings || []) {
    if (!(s.key in (draft || {}))) continue;
    let v = draft[s.key];
    if (s.kind === "int") v = typeof v === "number" ? v : String(v).trim() === "" ? NaN : Number(v);
    if (s.kind === "bool") v = !!v;
    if (typeof v === "string") v = v.trim();
    if (v !== s.value) out[s.key] = v;
  }
  return out;
}

// A plain message for a value the server would refuse, else null (the server
// checks again; this only saves a round trip).
export function settingError(setting, value) {
  switch (setting.kind) {
    case "int": {
      const n = typeof value === "number" ? value : Number(String(value).trim());
      if (!Number.isInteger(n)) return "Enter a whole number.";
      if (n < setting.min || n > setting.max) return `Enter a number from ${setting.min} to ${setting.max}.`;
      return null;
    }
    case "email":
      return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value || "").trim()) ? null : "Enter an email address like support@example.com.";
    case "url": {
      const v = String(value || "").trim();
      return !v || /^https:\/\/[^\s/]+\.[^\s]+$/.test(v) ? null : "Enter a full https:// link, or leave it empty.";
    }
    case "choice":
      return (setting.choices || []).includes(value) ? null : "Choose one of the plans.";
    default:
      return null;
  }
}

export const SOURCE_LABELS = { console: "Set here", environment: "Server value", default: "Default" };

export const PLAN_LABELS = { custom: "Custom", trial: "Trial", starter: "Starter", pro: "Pro" };

// --- Store detail ---------------------------------------------------------------------

export const STORE_DETAIL_TABS = [
  { key: "overview", label: "Overview", icon: "layout-dashboard" },
  { key: "usage", label: "Usage & plan", icon: "chart-line" },
  { key: "settings", label: "Settings", icon: "settings" },
  { key: "data", label: "Data", icon: "database" },
  { key: "audit", label: "Audit", icon: "scroll-text" },
];
export const DETAIL_TAB_KEY = "kc.tab.platform_store";

export function detailTab(remembered) {
  return STORE_DETAIL_TABS.some((t) => t.key === remembered) ? remembered : "overview";
}

const JOB_TONE = { success: "good", failed: "bad", running: "info" };
const JOB_LABEL = { success: "OK", failed: "Failed", running: "Running" };

export function jobChip(status) {
  return { label: JOB_LABEL[status] || status || "Never", tone: JOB_TONE[status] || "neutral" };
}

// "sales_sync" -> "Sales sync"
export function jobLabel(name) {
  const s = String(name || "").replace(/[_-]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

export function integrationChip(i) {
  if (!i.configured) return { label: "Not set up", tone: "neutral" };
  if (i.status === "ok" || i.status === "connected") return { label: "Working", tone: "good" };
  if (i.status === "failed" || i.status === "error") return { label: "Failing", tone: "bad" };
  return { label: "Saved", tone: "info" };
}

// The Overview's Connections list: a POS the store doesn't use (Toast on a
// Clover store, both on a CSV store) is noise unless something was saved.
const POS_KINDS = new Set(["toast", "clover"]);
export function visibleConnections(integrations, posProvider) {
  const pos = String(posProvider || "").toLowerCase();
  return (Array.isArray(integrations) ? integrations : []).filter(
    (i) => i && (!POS_KINDS.has(String(i.kind).toLowerCase()) || i.configured || String(i.kind).toLowerCase() === pos),
  );
}

// The Overview's headline: one line a master can act on.
export function healthSummary(h) {
  if (!h) return null;
  if (h.status !== "active") return { tone: "warn", text: `The store is ${h.status}.` };
  if (!h.database?.reachable) return { tone: "bad", text: "The store's database can't be reached." };
  const failed = (h.database.jobs || []).filter((j) => j.status === "failed");
  if (failed.length) return { tone: "bad", text: `${failed.length} background job${failed.length > 1 ? "s" : ""} failed last time.` };
  return { tone: "good", text: "Everything looks fine." };
}
