// API client functions for track B (docs/WEBSITE_READY_CONTRACT.md 3.6):
// store data: usage, plans, exports, off-boarding (master + the store's own Settings).
// Build on the shared helpers; never add these functions to src/api.js.
import { request, postJSON } from "../api.js";

export { request, postJSON, patchJSON } from "../api.js";

async function getJSON(path) {
  const res = await request(path);
  return res.json();
}

async function putJSON(path, body) {
  const res = await request(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

const enc = encodeURIComponent;

// The server answers relative links (/api/settings/exports/1/download?...);
// in `npm run dev` the API runs on another origin.
export function absoluteLink(url) {
  if (!url || /^https?:\/\//i.test(url)) return url;
  let base = "";
  try {
    const env = import.meta.env || {};
    base = env.VITE_API_BASE || (env.DEV ? "http://localhost:8000" : "");
  } catch {
    base = "";
  }
  return `${base}${url}`;
}

// --- the store's own owner/admin (Setup > Data & usage) ---
export const getOwnUsage = () => getJSON("/api/settings/usage");
export const listOwnExports = () => getJSON("/api/settings/exports");
export const requestOwnExport = () => postJSON("/api/settings/exports", {});
export const ownExportLink = (id) => postJSON(`/api/settings/exports/${enc(id)}/link`, {});

// --- masters (console: Usage & plan, Data) ---
export const usageSummary = () => getJSON("/api/platform/usage/summary");
export const getStoreUsage = (code) => getJSON(`/api/platform/stores/${enc(code)}/usage`);
export const measureStoreUsage = (code) => postJSON(`/api/platform/stores/${enc(code)}/usage/measure`, {});
export const setStorePlan = (code, plan) => putJSON(`/api/platform/stores/${enc(code)}/plan`, { plan });
export const listStoreExports = (code) => getJSON(`/api/platform/stores/${enc(code)}/exports`);
export const requestStoreExport = (code) => postJSON(`/api/platform/stores/${enc(code)}/exports`, {});
export const storeExportLink = (code, id) => postJSON(`/api/platform/stores/${enc(code)}/exports/${enc(id)}/link`, {});
export const getOffboarding = (code) => getJSON(`/api/platform/stores/${enc(code)}/offboarding`);
export const startOffboarding = (code, body) => postJSON(`/api/platform/stores/${enc(code)}/offboarding`, body);
export const cancelOffboarding = (code, body) => postJSON(`/api/platform/stores/${enc(code)}/offboarding/cancel`, body);
export const purgeStoreData = (code, body) => postJSON(`/api/platform/stores/${enc(code)}/offboarding/purge`, body);
