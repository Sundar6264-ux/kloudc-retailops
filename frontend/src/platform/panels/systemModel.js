// The System panel of the master console (docs/WEBSITE_READY_CONTRACT.md
// C1-C5, names in section 4: Storage · Background jobs · Workers · Live
// updates). Pure helpers - tested by systemModel.test.mjs.

const JOB_STATUS = {
  queued: { label: "Waiting", tone: "info" },
  running: { label: "Running", tone: "warn" },
  failed: { label: "Retrying soon", tone: "warn" },
  dead: { label: "Failed", tone: "bad" },
  done: { label: "Done", tone: "good" },
};

export function jobStatusChip(status) {
  return JOB_STATUS[status] || { label: status ? String(status) : "Unknown", tone: "neutral" };
}

// Filters of the jobs list (segmented control; the choice is remembered).
export const JOB_FILTERS = [
  { key: "attention", label: "Needs attention", statuses: ["dead", "failed"] },
  { key: "waiting", label: "Waiting", statuses: ["queued"] },
  { key: "running", label: "Running", statuses: ["running"] },
  { key: "done", label: "Done", statuses: ["done"] },
  { key: "all", label: "All", statuses: [] },
];

export const JOB_FILTER_KEY = "kc.platform.system.jobs";

export function jobFilter(key) {
  return JOB_FILTERS.find((f) => f.key === key) || JOB_FILTERS[0];
}

const KIND_LABELS = {
  store_job: "Scheduled store job",
  store_export: "Data export",
  store_purge: "Delete store data",
  usage_rollup: "Usage roll-up",
  usage_gauges: "Usage measures",
  jobqueue_maintenance: "Queue clean-up",
};

// "store_export" -> "Data export"; unknown kinds read as a sentence.
export function jobKindLabel(kind) {
  if (!kind) return "Job";
  if (KIND_LABELS[kind]) return KIND_LABELS[kind];
  const words = String(kind).replace(/^clover_/, "Clover ").replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function canRetry(job) {
  return job?.status === "dead" || job?.status === "failed";
}

export function attemptsText(job) {
  const a = Number(job?.attempts || 0);
  const max = Number(job?.max_attempts || 0);
  if (!max) return "";
  if (a === 0) return `${max} ${max === 1 ? "try" : "tries"} allowed`;
  return `Try ${a} of ${max}`;
}

// "just now", "40 s ago", "12 min ago", "3 h ago", "2 days ago"; "in 30 s"
// for a time still ahead (a retry waiting).
export function ago(iso, now = Date.now()) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.round((now - t) / 1000);
  const abs = Math.abs(s);
  let text;
  if (abs < 5) return "just now";
  if (abs < 60) text = `${abs} s`;
  else if (abs < 3600) text = `${Math.round(abs / 60)} min`;
  else if (abs < 86400) text = `${Math.round(abs / 3600)} h`;
  else {
    const d = Math.round(abs / 86400);
    text = `${d} ${d === 1 ? "day" : "days"}`;
  }
  return s >= 0 ? `${text} ago` : `in ${text}`;
}

// One line under a job: when it happened (or will retry) and its store.
export function jobMeta(job, now = Date.now()) {
  const parts = [];
  if (job?.store_code) parts.push(`Store ${job.store_code}`);
  else parts.push("Platform");
  if (job?.status === "failed" && job.run_after) {
    parts.push(Date.parse(job.run_after) > now ? `next try ${ago(job.run_after, now)}` : "next try due now");
  }
  else if (job?.status === "queued" && job.run_after && Date.parse(job.run_after) > now) {
    parts.push(`starts ${ago(job.run_after, now)}`);
  } else if (job?.finished_at) parts.push(`finished ${ago(job.finished_at, now)}`);
  else if (job?.started_at) parts.push(`started ${ago(job.started_at, now)}`);
  else if (job?.created_at) parts.push(`added ${ago(job.created_at, now)}`);
  const tries = attemptsText(job);
  if (tries) parts.push(tries);
  return parts.join(" · ");
}

// The first line of an error, short enough for a row.
export function errorLine(error, max = 160) {
  if (!error) return "";
  const first = String(error).split("\n").find((l) => l.trim()) || "";
  return first.length > max ? `${first.slice(0, max - 1)}…` : first;
}

const WORKER_STATE = {
  running: { label: "Running", tone: "good" },
  silent: { label: "Not responding", tone: "bad" },
  stopped: { label: "Stopped", tone: "neutral" },
};

export function workerChip(state) {
  return WORKER_STATE[state] || { label: state ? String(state) : "Unknown", tone: "neutral" };
}

export function workerMeta(w, now = Date.now()) {
  const parts = [];
  if (w?.running) parts.push(`${w.running} running`);
  parts.push(`${w?.jobs_done || 0} done`);
  if (w?.jobs_failed) parts.push(`${w.jobs_failed} failed`);
  if (w?.concurrency) parts.push(`${w.concurrency} at a time`);
  if (w?.heartbeat_at) parts.push(`checked in ${ago(w.heartbeat_at, now)}`);
  return parts.join(" · ");
}

// The four summary tiles: { key, label, icon, value, detail, tone, section }.
export function systemTiles(status) {
  const s = status || {};
  const storage = s.storage || {};
  const jobs = s.jobs || {};
  const live = s.live_updates || {};
  const counts = jobs.counts || {};
  const workers = Array.isArray(jobs.workers) ? jobs.workers : [];
  const alive = workers.filter((w) => w.state === "running").length;
  const silent = workers.filter((w) => w.state === "silent").length;
  const attention = (counts.dead || 0) + (counts.failed || 0);
  const queue = jobs.mode === "queue";

  let storageTile;
  if (storage.backend === "s3") {
    storageTile = {
      value: "Object storage",
      detail: storage.ok
        ? `Bucket ${storage.bucket}${storage.endpoint_host ? ` at ${storage.endpoint_host}` : ""}`
        : storage.problem || "Not set up",
      tone: storage.ok ? "good" : "bad",
    };
  } else {
    storageTile = { value: "On this server", detail: "Each store's files in its own folder", tone: "neutral" };
  }

  let jobsTile;
  if (jobs.error) jobsTile = { value: "Can't read", detail: "The job tables did not answer", tone: "bad" };
  else if (!queue) jobsTile = { value: "Inline", detail: "Jobs run inside the web server", tone: "neutral" };
  else {
    jobsTile = {
      value: attention ? `${attention} need attention` : "Queue",
      detail: `${counts.due || 0} waiting · ${counts.running || 0} running`,
      tone: attention ? "bad" : "good",
    };
  }

  let workersTile;
  if (!queue) workersTile = { value: "None needed", detail: "Inline mode has no workers", tone: "neutral" };
  else if (!alive) workersTile = { value: "No worker", detail: "Start one: python -m app.jobqueue worker", tone: "bad" };
  else {
    workersTile = {
      value: `${alive} running`,
      detail: silent ? `${silent} not responding` : "All checking in",
      tone: silent ? "warn" : "good",
    };
  }

  let liveTile;
  if (live.mode === "postgres") {
    liveTile = live.listening
      ? { value: "Across processes", detail: "PostgreSQL notify, connected", tone: "good" }
      : { value: "Not connected", detail: "Reconnecting by itself", tone: "bad" };
  } else liveTile = { value: "This process", detail: "One server process (local)", tone: "neutral" };

  return [
    { key: "storage", label: "Storage", icon: "database", section: "storage", ...storageTile },
    { key: "jobs", label: "Background jobs", icon: "list-checks", section: "jobs", ...jobsTile },
    { key: "workers", label: "Workers", icon: "server", section: "workers", ...workersTile },
    { key: "live", label: "Live updates", icon: "repeat", section: "live_updates", ...liveTile },
  ];
}

export function databaseLine(database) {
  const d = database || {};
  if (d.pool_mode === "pgbouncer") {
    return d.direct_url_set
      ? "Database connections: through PgBouncer, with a direct line for the scheduler and live updates."
      : "Database connections: through PgBouncer. Set PLATFORM_DIRECT_URL for the scheduler and live updates.";
  }
  return "Database connections: direct, pooled by each server process.";
}

// ?status=dead&store=ABC&before=12&limit=50 (empty values left out).
export function jobsQuery({ status = "", store = "", before = null, limit = 50 } = {}) {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  if (store) q.set("store", String(store).trim().toUpperCase());
  if (before) q.set("before", String(before));
  if (limit && limit !== 50) q.set("limit", String(limit));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function retryMessage(job) {
  return `${jobKindLabel(job?.kind)} is queued again.`;
}
