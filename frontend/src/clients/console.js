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
export const addMaster = ({ username, displayName, email }) =>
  postJSON(`${P}/masters`, { username, display_name: displayName || null, email: email || null });
export const removeMaster = (id) => del(`${P}/masters/${enc(id)}`);
export const resetMasterTwoStep = (id) => postJSON(`${P}/masters/${enc(id)}/reset-two-step`, {});
// Moving your own two-step sign-in to a new phone: a code from the current one first.
export const moveTwoStep = (currentCode) => postJSON(`${P}/masters/me/two-step/move`, { current_code: currentCode });
export const changeMasterPassword = (current, next) => postJSON(`${P}/users/me/password`, { current, new: next });

// Platform settings (A3)
export const getPlatformSettings = () => getJSON(`${P}/settings`);
export const savePlatformSettings = (changes) => patchJSON(`${P}/settings`, changes);
export const resetPlatformSetting = (key) => del(`${P}/settings/${enc(key)}`);

// Store detail (A4)
export const getStoreHealth = (code) => getJSON(`${P}/stores/${enc(code)}/health`);
