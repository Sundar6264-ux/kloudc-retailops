// API client functions for track A (docs/WEBSITE_READY_CONTRACT.md 3.6):
// master console: masters, platform settings, store detail.
// Build on the shared helpers; never add these functions to src/api.js.
import { request, postJSON, patchJSON } from "../api.js";

export { request, postJSON, patchJSON };

const P = "/api/platform";
const getJSON = async (path) => (await request(path)).json();
const del = async (path) => (await request(path, { method: "DELETE" })).json();
const enc = encodeURIComponent;

// Masters (A2)
export const listMasters = () => getJSON(`${P}/masters`);
// Adding, removing and resetting a master need a fresh two-step code from a
// master who has two-step sign-in on (docs/SECURITY_REVIEW.md SR-07).
const stepUp = (code) => (code ? { "X-Two-Step-Code": code } : {});
export const addMaster = ({ username, displayName, email, code }) =>
  postJSON(`${P}/masters`, { username, display_name: displayName || null, email: email || null, totp_code: code || null });
export const removeMaster = async (id, code) =>
  (await request(`${P}/masters/${enc(id)}`, { method: "DELETE", headers: stepUp(code) })).json();
export const resetMasterTwoStep = async (id, code) =>
  (
    await request(`${P}/masters/${enc(id)}/reset-two-step`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...stepUp(code) },
      body: "{}",
    })
  ).json();
// Moving your own two-step sign-in to a new phone: a code from the current one first.
export const moveTwoStep = (currentCode) => postJSON(`${P}/masters/me/two-step/move`, { current_code: currentCode });
export const changeMasterPassword = (current, next) => postJSON(`${P}/users/me/password`, { current, new: next });

// Platform settings (A3)
export const getPlatformSettings = () => getJSON(`${P}/settings`);
export const savePlatformSettings = (changes) => patchJSON(`${P}/settings`, changes);
export const resetPlatformSetting = (key) => del(`${P}/settings/${enc(key)}`);

// Store detail (A4)
export const getStoreHealth = (code) => getJSON(`${P}/stores/${enc(code)}/health`);

// Setup > Store settings (A5): the store admin's own store (store session).
export const getOwnStoreSettings = () => getJSON("/api/settings/store");
export const saveOwnStoreSettings = (changes) => patchJSON("/api/settings/store", changes);
export const getHostingStatus = () => getJSON("/api/settings/hosting");
export const requestOwnServer = ({ reason, targetUrl }) =>
  postJSON("/api/settings/hosting/request", { reason, target_api_base_url: targetUrl || null });

// Label printing (docs/LABEL_PRINTING_FEATURE.md 3.2): feature requests and
// the per-store switch (master session).
export const listFeatureRequests = (status = "requested") =>
  getJSON(`${P}/feature-requests${status ? `?status=${enc(status)}` : ""}`);
export const approveFeatureRequest = (id, body) => postJSON(`${P}/feature-requests/${enc(id)}/approve`, body);
export const declineFeatureRequest = (id, note) => postJSON(`${P}/feature-requests/${enc(id)}/decline`, { note });
export const getStoreLabelPrinting = async (code) => (await getJSON(`${P}/stores/${enc(code)}`)).label_printing || null;
export const setLabelPrinting = async (code, { enabled, url } = {}) => {
  const body = {};
  if (enabled !== undefined) body.label_printing_enabled = !!enabled;
  if (url !== undefined) body.labels_url = String(url || "").trim() || null;
  return (
    await request(`${P}/stores/${enc(code)}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  ).json();
};

// Label printing, the store's own side (docs/LABEL_PRINTING_FEATURE.md 3.3).
export const getLabelPrinting = () => getJSON("/api/label-printing");
export const requestLabelPrinting = (note) => postJSON("/api/label-printing/request", note ? { note } : {});
export const cancelLabelPrintingRequest = () => postJSON("/api/label-printing/request/cancel", {});
// A one-time code (60 s) that opens the label site already signed in.
export const openLabelsHandoff = () => postJSON("/api/labels/handoff", {});
