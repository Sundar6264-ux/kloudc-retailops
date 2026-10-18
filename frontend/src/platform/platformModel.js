// The master console (/#/platform, docs/PLATFORM_CONTRACT.md 3, 12, 13.1,
// 14): Requests, Stores, Hosting and Audit on one screen. Pure helpers -
// tested by platformModel.test.mjs.

export const PLATFORM_TABS = [
  { key: "requests", label: "Requests" },
  { key: "stores", label: "Stores" },
  { key: "hosting", label: "Hosting" },
  { key: "audit", label: "Audit" },
];

export const LAST_TAB_KEY = "storeops_platform_tab";

export function readLastTab(storage) {
  try {
    const v = storage?.getItem(LAST_TAB_KEY);
    return PLATFORM_TABS.some((t) => t.key === v) ? v : "requests";
  } catch {
    return "requests";
  }
}

export function rememberTab(storage, key) {
  try {
    if (PLATFORM_TABS.some((t) => t.key === key)) storage?.setItem(LAST_TAB_KEY, key);
  } catch {
    // not remembered
  }
}

const STORE_STATUS = {
  pending: { label: "Pending", tone: "neutral" },
  provisioning: { label: "Setting up", tone: "info" },
  active: { label: "Active", tone: "good" },
  suspended: { label: "Suspended", tone: "warn" },
  failed: { label: "Setup failed", tone: "bad" },
  archived: { label: "Archived", tone: "off" },
};

const SIGNUP_STATUS = {
  pending: { label: "Waiting", tone: "info" },
  approved: { label: "Approved", tone: "good" },
  rejected: { label: "Rejected", tone: "bad" },
  withdrawn: { label: "Withdrawn", tone: "off" },
};

const HOSTING_STATUS = {
  requested: { label: "Requested", tone: "info" },
  approved: { label: "Approved", tone: "good" },
  rejected: { label: "Rejected", tone: "bad" },
  migrating: { label: "Moving", tone: "warn" },
  done: { label: "Done", tone: "good" },
  rolled_back: { label: "Rolled back", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "off" },
};

function chip(table, status) {
  return table[status] || { label: status ? String(status) : "Unknown", tone: "neutral" };
}

export const storeStatusChip = (s) => chip(STORE_STATUS, s);
export const signupStatusChip = (s) => chip(SIGNUP_STATUS, s);
export const hostingStatusChip = (s) => chip(HOSTING_STATUS, s);

export const STORE_STATUS_FILTERS = ["", ...Object.keys(STORE_STATUS)];
export const SIGNUP_STATUS_FILTERS = ["pending", "approved", "rejected", ""];

// What can be done to a store in its status (the buttons on its row).
export function storeActions(store) {
  const s = store?.status;
  const out = [];
  if (s === "active") out.push("suspend");
  if (s === "suspended") out.push("resume");
  if (s === "failed" || s === "pending") out.push("retry");
  if (s && s !== "archived" && s !== "provisioning") out.push("archive");
  if (s === "active" || s === "suspended") out.push("settings");
  if (s === "active" && (store?.hosting || "shared") === "shared") out.push("hosting");
  return out;
}

// The actions a hosting request allows (contract 12.1: approve -> complete;
// rollback within 7 days of done).
export function hostingActions(req, { now = Date.now() } = {}) {
  switch (req?.status) {
    case "requested":
      return ["approve", "reject"];
    case "approved":
    case "migrating":
      return ["complete"];
    case "done": {
      const done = Date.parse(req.completed_at || "");
      return Number.isFinite(done) && now - done <= 7 * 24 * 3600 * 1000 ? ["rollback"] : [];
    }
    default:
      return [];
  }
}

// Free-text search across the rows a tab lists (case-insensitive, every
// word must match somewhere).
export function matchesSearch(row, query, fields) {
  const words = String(query || "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return true;
  const hay = fields
    .map((f) => row?.[f])
    .filter((v) => v != null)
    .join(" ")
    .toLowerCase();
  return words.every((w) => hay.includes(w));
}

export const SIGNUP_SEARCH_FIELDS = ["store_name", "requested_code", "contact_name", "contact_email", "pos_provider"];
export const STORE_SEARCH_FIELDS = ["code", "name", "status", "pos_provider", "pos", "timezone", "hosting"];
export const HOSTING_SEARCH_FIELDS = ["store_code", "code", "reason", "requested_by", "status"];

// GET /api/platform/audit query string (contract 13.1: newest first, max 200).
export function auditQuery({ store = "", action = "", before = "", limit = 100 } = {}) {
  const p = new URLSearchParams();
  if (store.trim()) p.set("store", store.trim().toUpperCase());
  if (action.trim()) p.set("action", action.trim());
  if (before) p.set("before", before);
  p.set("limit", String(Math.min(200, Math.max(1, Number(limit) || 100))));
  return p.toString();
}

// Approve body: only the overrides the master actually set.
export function approveBody({ code = "", packId = "", timezone = "", note = "" } = {}, request = {}) {
  const body = {};
  const c = String(code).trim().toUpperCase().replace(/-/g, "_");
  if (c && c !== request.requested_code) body.code = c;
  if (packId) body.pack_id = packId;
  if (timezone && timezone !== request.timezone) body.timezone = timezone;
  if (note.trim()) body.note = note.trim();
  return body;
}

// The archive confirmation must be the store's code, typed (contract 13.1).
export function archiveConfirmed(store, typed) {
  return !!store?.code && String(typed || "").trim().toUpperCase() === store.code;
}

// An audit row's details (JSON text) -> a short readable line.
export function auditDetails(details) {
  if (details == null || details === "") return "";
  let d = details;
  if (typeof d === "string") {
    try {
      d = JSON.parse(d);
    } catch {
      return d;
    }
  }
  if (!d || typeof d !== "object") return String(d);
  return Object.entries(d)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
}

// Errors on this console -> plain English (the flag being off has its own state).
export function platformErrorMessage(err) {
  const body = err?.body && typeof err.body === "object" ? err.body : {};
  const detail = body.detail && typeof body.detail === "object" ? body.detail : {};
  const code = body.error || detail.error;
  const message = body.message || detail.message || (typeof body.detail === "string" ? body.detail : null);
  if (!err?.status) return "Can't reach the server. Check your connection and try again.";
  if (code === "dedicated_hosting_disabled") {
    return "Dedicated servers are switched off on this platform (DEDICATED_HOSTING_ENABLED).";
  }
  if (err.status === 403) return "Only a master admin can do that.";
  return message || "Something went wrong. Please try again.";
}

export function isHostingDisabled(err) {
  const body = err?.body && typeof err.body === "object" ? err.body : {};
  const detail = body.detail && typeof body.detail === "object" ? body.detail : {};
  return (body.error || detail.error) === "dedicated_hosting_disabled";
}
