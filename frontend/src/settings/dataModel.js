// Data & usage, Usage & plan, Data (master console): the plain logic behind
// the screens (docs/WEBSITE_READY_CONTRACT.md B1-B5), kept free of React so
// node tests cover it. Names follow contract section 4: "Export all data",
// "data export", "Download link", "Archived" / "Deleting on <date>" /
// "Deleted", "Delete store data", plans Custom / Trial / Starter / Pro.

export const REMEMBER_PANEL_KEY = "kc.data.section";

const UNITS = ["bytes", "KB", "MB", "GB", "TB"];

// Shown until the nightly usage job has measured a number.
export const NOT_MEASURED = "Not measured yet";

export function formatBytes(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return NOT_MEASURED;
  let v = Number(n);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  if (i === 0) return `${v} ${v === 1 ? "byte" : "bytes"}`;
  return `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10} ${UNITS[i]}`;
}

export function formatCount(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return NOT_MEASURED;
  return Number(n).toLocaleString("en-US");
}

// "2 minutes ago" style, against `now` (ms) for tests.
export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "Never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "Never";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "Just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  return `${d} days ago`;
}

export function formatDate(iso, timeZone) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const opts = { year: "numeric", month: "short", day: "numeric" };
  try {
    return d.toLocaleDateString("en-US", timeZone ? { ...opts, timeZone } : opts);
  } catch {
    return d.toLocaleDateString("en-US", opts);
  }
}

export function formatDateTime(iso, timeZone) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const opts = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  try {
    return d.toLocaleString("en-US", timeZone ? { ...opts, timeZone } : opts);
  } catch {
    return d.toLocaleString("en-US", opts);
  }
}

// The usage tiles, in screen order. Gauges show their newest value; counters
// add up over the window (30 days).
export function usageTiles(snapshot, now = Date.now()) {
  const m = snapshot?.metrics || {};
  const days = snapshot?.window_days || 30;
  return [
    { key: "users", label: "Users", value: formatCount(m.users), hint: "Store logins", icon: "users" },
    { key: "items", label: "Items", value: formatCount(m.items), hint: "In the latest catalog", icon: "package" },
    { key: "storage_bytes", label: "File storage", value: formatBytes(m.storage_bytes), hint: "Invoices, flyers, photos", icon: "layers" },
    { key: "database_bytes", label: "Database", value: formatBytes(m.database_bytes), hint: "The store's records", icon: "database" },
    { key: "orders_synced", label: "Sales synced", value: formatCount(m.orders_synced ?? 0), hint: `Order lines, last ${days} days`, icon: "receipt" },
    { key: "pos_api_calls", label: "POS requests", value: formatCount(m.pos_api_calls ?? 0), hint: `Last ${days} days`, icon: "plug" },
    { key: "ai_calls", label: "AI requests", value: formatCount(m.ai_calls ?? 0), hint: `Last ${days} days`, icon: "lightbulb" },
    { key: "last_activity_at", label: "Last activity", value: timeAgo(m.last_activity_at, now), hint: "Someone used the store", icon: "clock" },
  ];
}

// Daily bars for one counter: oldest first, `n` days ending with the newest
// day in the data (missing days are 0). Heights are 0..100.
export function dailyBars(days, metric, n = 30) {
  const byDay = new Map((days || []).map((d) => [d.day, Number(d[metric]) || 0]));
  if (!byDay.size) return [];
  const newest = [...byDay.keys()].sort().pop();
  const end = new Date(`${newest}T00:00:00Z`);
  const out = [];
  for (let i = n - 1; i >= 0; i -= 1) {
    const d = new Date(end.getTime() - i * 86400000).toISOString().slice(0, 10);
    out.push({ day: d, value: byDay.get(d) || 0 });
  }
  const max = Math.max(...out.map((b) => b.value), 0);
  return out.map((b) => ({ ...b, height: max ? Math.max(b.value ? 4 : 0, Math.round((b.value / max) * 100)) : 0 }));
}

export const STATE_TEXT = { ok: "Within the limit", near: "Near the limit", over: "Over the limit" };
export const STATE_TONE = { ok: "good", near: "warn", over: "bad" };

export function limitBars(states) {
  return (states || []).map((s) => {
    const used = s.used ?? null;
    const pct = used === null || !s.limit ? 0 : Math.min(100, Math.round((used / s.limit) * 100));
    const fmt = s.unit === "bytes" ? formatBytes : formatCount;
    return {
      key: s.key,
      label: s.label,
      pct,
      tone: STATE_TONE[s.state] || "good",
      state: s.state,
      stateText: STATE_TEXT[s.state] || STATE_TEXT.ok,
      text: used === null ? `Not measured yet - limit ${fmt(s.limit)}` : `${fmt(used)} of ${fmt(s.limit)}`,
    };
  });
}

// The banner for a store admin: null when every limit is fine.
export function planNotice(plan) {
  if (!plan || !plan.limit_states?.length) return null;
  const over = plan.limit_states.filter((s) => s.state === "over").map((s) => s.label.toLowerCase());
  const near = plan.limit_states.filter((s) => s.state === "near").map((s) => s.label.toLowerCase());
  if (over.length) {
    return {
      tone: "bad",
      text: `Your store is over the limit for ${joinWords(over)} on the ${plan.label} plan. Your work is not affected; KloudC support will be in touch about a plan that fits.`,
    };
  }
  if (near.length) {
    return { tone: "warn", text: `Your store is near the limit for ${joinWords(near)} on the ${plan.label} plan.` };
  }
  return null;
}

function joinWords(words) {
  if (words.length < 2) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

// --- exports ------------------------------------------------------------------

const EXPORT_STATUS = {
  queued: { label: "Waiting", tone: "info" },
  running: { label: "Making the file", tone: "info" },
  ready: { label: "Ready", tone: "good" },
  failed: { label: "Failed", tone: "bad" },
  expired: { label: "Expired", tone: "muted" },
};

export function exportRow(e, timeZone) {
  const s = EXPORT_STATUS[e.status] || { label: e.status, tone: "muted" };
  const parts = [];
  if (e.size_bytes) parts.push(formatBytes(e.size_bytes));
  if (e.tables) parts.push(`${formatCount(e.tables)} tables`);
  if (e.files !== null && e.files !== undefined && e.status === "ready") parts.push(`${formatCount(e.files)} files`);
  if (e.status === "ready" && e.expires_at) parts.push(`kept until ${formatDate(e.expires_at, timeZone)}`);
  return {
    id: e.id,
    title: e.reason === "offboarding" ? "Final data export" : "Data export",
    when: formatDateTime(e.created_at, timeZone),
    statusLabel: s.label,
    tone: s.tone,
    meta: parts.join(" · "),
    error: e.status === "failed" ? e.error || "Something went wrong." : null,
    canDownload: e.status === "ready",
    busy: e.status === "queued" || e.status === "running",
    filename: e.filename,
  };
}

export function exportsState(list) {
  const rows = list || [];
  const running = rows.some((e) => e.status === "queued" || e.status === "running");
  const lastFailed = rows[0]?.status === "failed";
  return { running, canRequest: !running, retry: lastFailed, pollMs: running ? 2000 : 0 };
}

// --- off-boarding -------------------------------------------------------------------

export function offboardingState(view, timeZone) {
  const v = view || {};
  if (v.state === "deleted") {
    return { key: "deleted", label: "Deleted", tone: "bad", text: `Store data was deleted on ${formatDate(v.purged_at, timeZone)}.` };
  }
  if (v.state === "scheduled") {
    return {
      key: "scheduled",
      label: `Deleting on ${formatDate(v.purge_after, timeZone)}`,
      tone: "warn",
      text: v.pilot_mode
        ? "This store is in pilot mode, so it is never deleted automatically. A master can delete it by hand."
        : "On that date the store's database, files and saved keys are deleted for good.",
    };
  }
  if (v.status === "archived") {
    return { key: "archived", label: "Archived", tone: "muted", text: "Nobody can sign in. Its data is kept until deletion is scheduled." };
  }
  return { key: "active", label: "Not scheduled", tone: "good", text: "The store's data is kept." };
}

// Which off-boarding actions the Data tab offers.
export function offboardingActions(view) {
  const v = view || {};
  const scheduled = v.state === "scheduled";
  const deleted = v.state === "deleted";
  const finalBusy = ["queued", "running"].includes(v.final_export?.status);
  return {
    start: !deleted && !scheduled && v.status !== "provisioning",
    cancel: scheduled,
    purge: scheduled && v.status === "archived" && !finalBusy && v.hosting !== "dedicated",
    purgeBlockedReason: scheduled && finalBusy ? "The final data export is still being made." : null,
  };
}

export const TIMELINE_TEXT = {
  "store.archived": "Archived",
  "store.offboarding_started": "Deletion scheduled",
  "store.offboarding_cancelled": "Deletion cancelled",
  "store.purge_started": "Deleting store data",
  "store.data_deleted": "Store data deleted",
};

export function timelineRows(view, timeZone) {
  return (view?.timeline || []).map((t) => ({
    key: `${t.at}-${t.action}`,
    label: TIMELINE_TEXT[t.action] || t.action,
    when: formatDateTime(t.at, timeZone),
    who: (t.actor_name || "").startsWith("system") ? "Automatic" : t.actor_name || "",
  }));
}

// Typed Store ID check (case-insensitive, like the server).
export function confirmMatches(typed, code) {
  return String(typed || "").trim().toUpperCase() === String(code || "").trim().toUpperCase() && !!code;
}

export function dataErrorMessage(err) {
  const body = err?.body || {};
  if (body.message) return body.message;
  if (err?.status === 403) return "Only the store's owner or an admin can do this.";
  if (err?.status === 429) return "Too many requests. Try again in a minute.";
  return err?.message || "Something went wrong. Try again.";
}
