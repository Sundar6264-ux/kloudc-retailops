// API client functions for track C (docs/WEBSITE_READY_CONTRACT.md 3.6):
// system status: storage, job queue, workers, live updates.
// Build on the shared helpers; never add these functions to src/api.js.
import { request, postJSON } from "../api.js";
import { jobsQuery } from "../platform/panels/systemModel.js";

export { request, postJSON, patchJSON } from "../api.js";

// GET /api/platform/system/status -> {storage, jobs, live_updates, database}
export async function getSystemStatus() {
  const res = await request("/api/platform/system/status");
  return res.json();
}

// GET /api/platform/system/jobs -> {jobs: [...], next_before}
export async function listSystemJobs({ statuses = [], store = "", before = null, limit = 50 } = {}) {
  // The server filters one status at a time: several are fetched and merged
  // newest first (the "Needs attention" filter = failed + retrying).
  const wanted = statuses.length ? statuses : [""];
  const replies = await Promise.all(
    wanted.map(async (status) => {
      const res = await request(`/api/platform/system/jobs${jobsQuery({ status, store, before, limit })}`);
      return res.json();
    }),
  );
  const jobs = replies.flatMap((r) => r.jobs || []).sort((a, b) => b.id - a.id).slice(0, limit);
  const more = replies.some((r) => r.next_before);
  return { jobs, next_before: more && jobs.length ? jobs[jobs.length - 1].id : null };
}

// POST /api/platform/system/jobs/{id}/retry -> {job}
export function retrySystemJob(jobId) {
  return postJSON(`/api/platform/system/jobs/${encodeURIComponent(jobId)}/retry`, {});
}
