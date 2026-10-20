// The System panel helpers (docs/WEBSITE_READY_CONTRACT.md track C).
//   node --test src/platform/panels/systemModel.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  JOB_FILTERS,
  ago,
  attemptsText,
  canRetry,
  databaseLine,
  errorLine,
  jobFilter,
  jobKindLabel,
  jobMeta,
  jobStatusChip,
  jobsQuery,
  systemTiles,
  workerChip,
  workerMeta,
} from "./systemModel.js";

const NOW = Date.parse("2026-10-20T03:00:00Z");

test("job statuses read as plain words with a tone", () => {
  assert.deepEqual(jobStatusChip("queued"), { label: "Waiting", tone: "info" });
  assert.deepEqual(jobStatusChip("failed"), { label: "Retrying soon", tone: "warn" });
  assert.deepEqual(jobStatusChip("dead"), { label: "Failed", tone: "bad" });
  assert.deepEqual(jobStatusChip("done"), { label: "Done", tone: "good" });
  assert.equal(jobStatusChip("odd").label, "odd");
});

test("filters: Needs attention first (failed + retrying), unknown falls back to it", () => {
  assert.equal(JOB_FILTERS[0].key, "attention");
  assert.deepEqual(jobFilter("attention").statuses, ["dead", "failed"]);
  assert.deepEqual(jobFilter("all").statuses, []);
  assert.equal(jobFilter("nope").key, "attention");
});

test("kind labels", () => {
  assert.equal(jobKindLabel("store_export"), "Data export");
  assert.equal(jobKindLabel("store_job"), "Scheduled store job");
  assert.equal(jobKindLabel("clover_backfill"), "Clover backfill");
  assert.equal(jobKindLabel("some_new_kind"), "Some new kind");
  assert.equal(jobKindLabel(""), "Job");
});

test("retry only for failed or retrying jobs", () => {
  assert.equal(canRetry({ status: "dead" }), true);
  assert.equal(canRetry({ status: "failed" }), true);
  for (const s of ["queued", "running", "done"]) assert.equal(canRetry({ status: s }), false);
});

test("relative times, past and ahead", () => {
  assert.equal(ago("2026-10-20T02:59:58Z", NOW), "just now");
  assert.equal(ago("2026-10-20T02:59:20Z", NOW), "40 s ago");
  assert.equal(ago("2026-10-20T02:48:00Z", NOW), "12 min ago");
  assert.equal(ago("2026-10-20T00:00:00Z", NOW), "3 h ago");
  assert.equal(ago("2026-10-18T03:00:00Z", NOW), "2 days ago");
  assert.equal(ago("2026-10-20T03:00:30Z", NOW), "in 30 s");
  assert.equal(ago(null, NOW), "");
  assert.equal(ago("garbage", NOW), "");
});

test("job meta line: store, timing, tries", () => {
  assert.equal(
    jobMeta({ store_code: "ALPHA", status: "failed", run_after: "2026-10-20T03:01:00Z", attempts: 1, max_attempts: 3 }, NOW),
    "Store ALPHA · next try in 1 min · Try 1 of 3",
  );
  assert.equal(
    jobMeta({ store_code: null, status: "done", finished_at: "2026-10-20T02:50:00Z", attempts: 1, max_attempts: 1 }, NOW),
    "Platform · finished 10 min ago · Try 1 of 1",
  );
  assert.equal(
    jobMeta({ store_code: "ALPHA", status: "failed", run_after: "2026-10-20T02:59:00Z", attempts: 1, max_attempts: 2 }, NOW),
    "Store ALPHA · next try due now · Try 1 of 2",
  );
  assert.equal(attemptsText({ attempts: 0, max_attempts: 3 }), "3 tries allowed");
  assert.equal(attemptsText({}), "");
});

test("error line: first non-empty line, clipped", () => {
  assert.equal(errorLine("\nValueError: no luck\n  at x"), "ValueError: no luck");
  assert.equal(errorLine("x".repeat(300)).length, 160);
  assert.equal(errorLine(null), "");
});

test("workers", () => {
  assert.deepEqual(workerChip("silent"), { label: "Not responding", tone: "bad" });
  assert.equal(
    workerMeta({ running: 2, jobs_done: 10, jobs_failed: 1, concurrency: 4, heartbeat_at: "2026-10-20T02:59:50Z" }, NOW),
    "2 running · 10 done · 1 failed · 4 at a time · checked in 10 s ago",
  );
});

test("tiles: the default single-process server reads calm and neutral", () => {
  const tiles = systemTiles({
    storage: { backend: "local", ok: true },
    jobs: { mode: "inline", counts: { queued: 0 }, workers: [] },
    live_updates: { mode: "local" },
    database: { pool_mode: "direct" },
  });
  assert.deepEqual(tiles.map((t) => t.label), ["Storage", "Background jobs", "Workers", "Live updates"]);
  assert.deepEqual(tiles.map((t) => t.value), ["On this server", "Inline", "None needed", "This process"]);
  assert.ok(tiles.every((t) => t.tone === "neutral" && t.icon && t.section));
});

test("tiles: queue mode with failures, no worker, R2 storage, live updates down", () => {
  const tiles = systemTiles({
    storage: { backend: "s3", ok: true, bucket: "kc-files", endpoint_host: "acct.r2.cloudflarestorage.com" },
    jobs: { mode: "queue", counts: { dead: 2, failed: 1, due: 4, running: 0 }, workers: [{ state: "stopped" }] },
    live_updates: { mode: "postgres", listening: false },
  });
  const by = Object.fromEntries(tiles.map((t) => [t.key, t]));
  assert.equal(by.storage.detail, "Bucket kc-files at acct.r2.cloudflarestorage.com");
  assert.equal(by.jobs.value, "3 need attention");
  assert.equal(by.jobs.detail, "4 waiting · 0 running");
  assert.equal(by.jobs.tone, "bad");
  assert.equal(by.workers.value, "No worker");
  assert.equal(by.live.value, "Not connected");
});

test("tiles: incomplete S3 settings and healthy workers", () => {
  const tiles = systemTiles({
    storage: { backend: "s3", ok: false, problem: "Missing STORAGE_S3_BUCKET" },
    jobs: { mode: "queue", counts: {}, workers: [{ state: "running" }, { state: "silent" }] },
    live_updates: { mode: "postgres", listening: true },
  });
  const by = Object.fromEntries(tiles.map((t) => [t.key, t]));
  assert.equal(by.storage.tone, "bad");
  assert.equal(by.storage.detail, "Missing STORAGE_S3_BUCKET");
  assert.equal(by.workers.value, "1 running");
  assert.equal(by.workers.tone, "warn");
  assert.equal(by.live.tone, "good");
});

test("database line and jobs query", () => {
  assert.match(databaseLine({ pool_mode: "direct" }), /direct/);
  assert.match(databaseLine({ pool_mode: "pgbouncer", direct_url_set: false }), /PLATFORM_DIRECT_URL/);
  assert.equal(jobsQuery({}), "");
  assert.equal(jobsQuery({ status: "dead", store: " alpha ", before: 12 }), "?status=dead&store=ALPHA&before=12");
  assert.equal(jobsQuery({ limit: 20 }), "?limit=20");
});
