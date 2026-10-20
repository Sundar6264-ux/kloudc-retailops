// API client functions for track D (docs/WEBSITE_READY_CONTRACT.md 3.6):
// Clover connect (OAuth + manual token) and the item vendors editor.
// Build on the shared helpers; never add these functions to src/api.js.
import { request, postJSON, patchJSON } from "../api.js";
import { saveOrShareBlob } from "../downloadOrShare";

export { request, postJSON, patchJSON };

const C = "/api/settings/integrations/clover";
const V = "/api/item-vendors";

async function getJSON(path) {
  const res = await request(path);
  return res.json();
}

// --- Connect with Clover (Settings > Integrations) ---------------------------

// {oauth_enabled, environment, connected, mode, merchant_id, merchant_name,
//  region, token_expires_at, last4, status, last_tested_at, last_test_message, editable}
export const getCloverStatus = () => getJSON(`${C}/status`);

// {url, expires_in}; 409 clover_app_not_configured until the owner sets up the Clover app.
export const getCloverAuthorizeUrl = () => getJSON(`${C}/authorize-url`);

// Manual token mode: {merchant_id, api_token, region}. A blank token keeps the saved one.
export const saveCloverToken = (body) => postJSON(`${C}/manual`, body);

export const disconnectClover = () => postJSON(`${C}/disconnect`, {});

// The shared "Test connection" route (Settings > Integrations).
export const testCloverConnection = () => postJSON(`/api/settings/integrations/clover/test`, {});

// --- Item vendors (Setup > Item vendors) ---------------------------------------

// {items:[{item_id,name,barcode,category,vendors:[{name,item_code,rank,source}]}],
//  total, catalog_total, missing_count, vendor_names, editable, pos, read_only_reason}
export function listItemVendors({ q = "", show = "all", limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ q, show, limit: String(limit), offset: String(offset) });
  return getJSON(`${V}?${params}`);
}

export async function saveItemVendors(itemId, vendors) {
  const res = await request(`${V}/items/${encodeURIComponent(itemId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vendors }),
  });
  return res.json();
}

// Preview (apply=false) or apply a CSV import.
export async function importItemVendors(file, { apply = false } = {}) {
  const body = new FormData();
  body.append("file", file);
  const res = await request(`${V}/import${apply ? "?apply=1" : ""}`, { method: "POST", body });
  return res.json();
}

async function download(path, filename) {
  const res = await request(path);
  const blob = await res.blob();
  await saveOrShareBlob(blob, filename);
}

export const downloadItemVendors = () => download(`${V}/export.csv`, "item-vendors.csv");
export const downloadItemVendorsTemplate = () => download(`${V}/template.csv`, "item-vendors-template.csv");
