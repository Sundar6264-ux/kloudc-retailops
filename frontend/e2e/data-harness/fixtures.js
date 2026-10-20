// Response shapes of app/platform/routes_data.py (fake store ALPHA, made-up numbers).
const DAY = 86400000;
const now = Date.parse("2026-10-20T03:00:00Z");
const iso = (ms) => new Date(ms).toISOString();
const day = (n) => iso(now - n * DAY).slice(0, 10);

const days = Array.from({ length: 30 }, (_, i) => ({
  day: day(i),
  orders_synced: i % 7 === 6 ? 0 : 180 + ((i * 37) % 120),
  pos_api_calls: 40 + (i % 5) * 6,
  ai_calls: i % 3,
}));
const sum = (k) => days.reduce((s, d) => s + (d[k] || 0), 0);
const GB = 1024 ** 3;

export const metrics = {
  orders_synced: sum("orders_synced"),
  pos_api_calls: sum("pos_api_calls"),
  ai_calls: sum("ai_calls"),
  users: 6,
  items: 1712,
  storage_bytes: Math.round(0.42 * GB),
  database_bytes: 186 * 1024 * 1024,
  last_activity_at: iso(now - 7 * 60000),
};

const plans = [
  { id: "custom", label: "Custom", limits: { users: null, items: null, storage_bytes: null } },
  { id: "trial", label: "Trial", limits: { users: 5, items: 2000, storage_bytes: GB } },
  { id: "starter", label: "Starter", limits: { users: 15, items: 10000, storage_bytes: 10 * GB } },
  { id: "pro", label: "Pro", limits: { users: 60, items: 50000, storage_bytes: 50 * GB } },
];

export function planView(id = "trial") {
  const p = plans.find((x) => x.id === id);
  const st = (used, limit) => (limit == null ? "ok" : used > limit ? "over" : used >= limit * 0.8 ? "near" : "ok");
  const limit_states = id === "custom" ? [] : [
    { key: "users", label: "Users", used: metrics.users, limit: p.limits.users, unit: "count", state: st(metrics.users, p.limits.users) },
    { key: "items", label: "Items", used: metrics.items, limit: p.limits.items, unit: "count", state: st(metrics.items, p.limits.items) },
    { key: "storage_bytes", label: "File storage", used: metrics.storage_bytes, limit: p.limits.storage_bytes, unit: "bytes", state: st(metrics.storage_bytes, p.limits.storage_bytes) },
  ];
  const order = { ok: 0, near: 1, over: 2 };
  const state = limit_states.reduce((w, s) => (order[s.state] > order[w] ? s.state : w), "ok");
  return { plan: id, label: p.label, limits: p.limits, limit_states, state, plans };
}

export function usage(planId) {
  return { store_code: "ALPHA", metrics, days, updated_at: iso(now - 60000), window_days: 30, plan: planView(planId) };
}

export const exportsList = [
  { id: 14, store_code: "ALPHA", status: "ready", reason: "manual", requested_by: "store:ALPHA:ann", size_bytes: 48 * 1024 * 1024,
    sha256: "ab".repeat(32), tables: 58, rows: 412093, files: 316, error: null, downloads: 1,
    created_at: iso(now - 2 * 3600000), started_at: iso(now - 2 * 3600000), finished_at: iso(now - 2 * 3600000 + 90000),
    expires_at: iso(now + 7 * DAY), filename: "alpha-data-export-20261020-14.zip" },
  { id: 9, store_code: "ALPHA", status: "failed", reason: "manual", requested_by: "store:ALPHA:ann", size_bytes: null,
    sha256: null, tables: null, rows: null, files: null, error: "The store's database did not answer. Try again.", downloads: 0,
    created_at: iso(now - 3 * DAY), started_at: iso(now - 3 * DAY), finished_at: iso(now - 3 * DAY + 30000), expires_at: null,
    filename: "alpha-data-export-20261017-9.zip" },
  { id: 4, store_code: "ALPHA", status: "expired", reason: "manual", requested_by: "platform:owner", size_bytes: 41 * 1024 * 1024,
    sha256: "cd".repeat(32), tables: 58, rows: 398000, files: 290, error: null, downloads: 2,
    created_at: iso(now - 12 * DAY), started_at: null, finished_at: null, expires_at: iso(now - 5 * DAY),
    filename: "alpha-data-export-20261008-4.zip" },
];

const timelineBase = [
  { at: iso(now - 26 * 3600000), action: "store.archived", actor_name: "platform:owner", details: {} },
  { at: iso(now - 26 * 3600000 + 1000), action: "store.offboarding_started", actor_name: "platform:owner", details: {} },
];

export function offboarding(kind) {
  const base = { store_code: "ALPHA", grace_days: 30, hosting: "shared", pilot_mode: false, cancelled_at: null, purged_at: null, purged_by: null };
  if (kind === "active") return { ...base, status: "active", state: null, purge_after: null, started_at: null, started_by: null, final_export: null, timeline: [] };
  if (kind === "deleted") {
    return { ...base, status: "archived", state: "deleted", purge_after: iso(now - DAY), started_at: iso(now - 31 * DAY), started_by: "platform:owner",
      purged_at: iso(now - DAY), purged_by: "system:offboarding", final_export: { ...exportsList[2], reason: "offboarding", status: "expired" },
      timeline: [...timelineBase, { at: iso(now - DAY), action: "store.purge_started", actor_name: "system:offboarding", details: {} },
        { at: iso(now - DAY + 5000), action: "store.data_deleted", actor_name: "system:offboarding", details: {} }] };
  }
  return { ...base, status: "archived", state: "scheduled", purge_after: iso(now + 29 * DAY), started_at: iso(now - 26 * 3600000), started_by: "platform:owner",
    final_export: { ...exportsList[0], id: 15, reason: "offboarding", expires_at: iso(now + 29 * DAY) }, timeline: timelineBase };
}
