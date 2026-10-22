import { isNative, getCachedToken, setToken, clearToken } from "./mobileAuth";
import { saveOrShareBlob } from "./downloadOrShare";
import { errorText } from "./errorText";
import { isStoreMismatch, setStoreHeadersOn, storeHeaders } from "./sessionStore";
import { poExportPath, poFallbackName } from "./components/poDownload/poDownloadModel.js";
import { storeToday } from "./format.js";

export { restoreToken } from "./mobileAuth";

// Explicit VITE_API_BASE always wins. Otherwise: in `npm run dev` (separate
// frontend/backend processes) default to localhost:8000. In a production
// build served *by* the backend itself (single process, e.g. behind Tailscale),
// default to "" (relative) so requests follow whatever host/origin the page
// was actually loaded from, rather than hardcoding "localhost". The mobile
// app bundles the same build but always talks to a fixed remote API, so it
// needs VITE_API_BASE set explicitly at build time (see mobile build docs) -
// "" would mean "call capacitor://localhost", which isn't a real API.
const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.DEV ? "http://localhost:8000" : "");

const LOGIN_PATHS = ["/api/auth/login", "/api/auth/mobile-login"];

async function request(path, options = {}) {
  const opts = { ...options };
  // The store this page is signed in to (sessionStore.js): another tab's
  // sign-in to another store makes this answer 409 instead of mixing stores.
  opts.headers = { ...(opts.headers || {}), ...storeHeaders(path) };
  // Native (Capacitor): no session cookie - send the stored bearer token
  // instead. Web: unchanged, credentials: "include" sends the httpOnly
  // session cookie in both dev (cross-origin :5173 -> :8000) and prod
  // (same-origin).
  if (isNative()) {
    const token = getCachedToken();
    opts.headers = { ...(opts.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  } else {
    opts.credentials = "include";
  }
  const res = await fetch(`${API_BASE}${path}`, opts);
  if (!res.ok) {
    let detail = res.statusText;
    let errorBody = null;
    try {
      const body = await res.json();
      errorBody = body;
      // A structured detail (e.g. a list of validation errors) is kept whole
      // on error.body; the message gets readable text instead of
      // "[object Object]".
      // The server's plain sentence (errorText.js; G25/G27) when it sent one.
      detail = errorText(body, typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body));
    } catch {
      // response wasn't JSON; fall back to statusText
    }
    // A 401 on any call other than a login attempt itself means the session
    // expired or was revoked (e.g. an admin reset this user's password) -
    // bounce back to the login screen instead of leaving stale/broken pages up.
    if (res.status === 401 && !LOGIN_PATHS.includes(path)) {
      window.dispatchEvent(new Event("auth:unauthorized"));
    }
    if (isStoreMismatch(res.status, errorBody)) {
      window.dispatchEvent(new Event("auth:store-changed"));
    }
    const error = new Error(detail);
    error.status = res.status;
    error.body = errorBody;
    throw error;
  }
  return res;
}

async function postJSON(path, body) {
  const res = await request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function patchJSON(path, body) {
  const res = await request(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// One login for everyone (docs/PLATFORM_CONTRACT.md 4.5): Store ID +
// username + password. Store ID PLATFORM signs a master in (reply
// {"scope":"platform","user":{...}}). `store_code` is left out when blank:
// a single-store (M2) server accepts that.
export async function login(storeCode, username, password, totp = "") {
  const body = { username, password, ...(storeCode ? { store_code: storeCode } : {}), ...(totp ? { totp } : {}) };
  if (isNative()) {
    const result = await postJSON("/api/auth/mobile-login", body);
    await setToken(result.token);
    return result;
  }
  return postJSON("/api/auth/login", body);
}

export async function logout() {
  try {
    return await postJSON("/api/auth/logout", {});
  } finally {
    if (isNative()) await clearToken();
  }
}

// The store's public branding (docs/M2_CONTRACT.md section 2): name, logo,
// colours, time zone, POS name and capabilities. No login and no cookies
// (it is the same for everyone), so a plain fetch, never the 401 bounce.
// null when the server has no branding endpoint (older server).
// `storeCode` asks a shared (multi-store) server for that store's branding
// (contract 4.4 `?store=CODE`); a single-store server ignores it.
export async function getBranding(storeCode) {
  const q = storeCode ? `?store=${encodeURIComponent(storeCode)}` : "";
  const res = await fetch(`${API_BASE}/api/app/branding${q}`, { credentials: "omit" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`branding ${res.status}`);
  return res.json();
}

export async function getMe() {
  const res = await request("/api/auth/me");
  return res.json();
}

export async function changePassword(currentPassword, newPassword) {
  return postJSON("/api/auth/change-password", { current_password: currentPassword, new_password: newPassword });
}

export async function listUsers() {
  const res = await request("/api/auth/users");
  return res.json();
}

export async function createUser(username, password, isAdmin, allowedFeatures = [], hideSales = false) {
  return postJSON("/api/auth/users", {
    username,
    password,
    is_admin: isAdmin,
    allowed_features: allowedFeatures,
    hide_sales: hideSales,
  });
}

export async function setUserPassword(userId, newPassword) {
  return postJSON(`/api/auth/users/${userId}/set-password`, { new_password: newPassword });
}

export async function listFeatures() {
  const res = await request("/api/auth/features");
  return res.json();
}

export async function setUserFeatures(userId, allowedFeatures) {
  return patchJSON(`/api/auth/users/${userId}/features`, { allowed_features: allowedFeatures });
}

export async function deleteUser(userId) {
  const res = await request(`/api/auth/users/${userId}`, { method: "DELETE" });
  return res.json();
}

// Whether a non-admin may assign tasks to others (Team hub). Admins always can.
export async function setUserCanAssign(userId, canAssign) {
  return patchJSON(`/api/auth/users/${userId}/can-assign`, { can_assign: canAssign });
}

// Admin-only: "Hide sales numbers" for a non-admin (backend/app/sales_access.py).
export async function setUserHideSales(userId, hideSales) {
  return patchJSON(`/api/auth/users/${userId}/hide-sales`, { hide_sales: hideSales });
}

// --- Team hub: teams (admin) ---------------------------------------------

export async function listTeams() {
  const res = await request("/api/team/teams");
  return res.json();
}

export async function createTeam(name, color) {
  return postJSON("/api/team/teams", { name, color });
}

export async function updateTeam(teamId, changes) {
  return patchJSON(`/api/team/teams/${teamId}`, changes);
}

export async function setTeamMembers(teamId, userIds) {
  const res = await request(`/api/team/teams/${teamId}/members`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_ids: userIds }),
  });
  return res.json();
}

// --- Team hub: bootstrap + tasks + recurring schedules ------------------

export async function getTeamBootstrap() {
  const res = await request("/api/team/bootstrap");
  return res.json();
}

// Who the caller can assign a task to (everyone with Tasks for admins and
// can_assign users; just themselves otherwise). The argument is kept so
// existing callers don't change.
// eslint-disable-next-line no-unused-vars
export async function listAssignablePeople(_isAdmin) {
  try {
    const res = await request("/api/team/assignees");
    const body = await res.json();
    return body.people || [];
  } catch {
    return [];
  }
}

// Oct 15 filters (narrow the view, never widen it): `assigneeUserId` (one
// person), `source` ("manual" | "auto" | a source_type), `q` (title words).
export async function listTasks({ view = "mine", status = "open", beforeId, limit, overdue, assigneeUserId, source, q } = {}) {
  const params = new URLSearchParams({ view, status });
  if (beforeId) params.set("before_id", beforeId);
  if (limit) params.set("limit", limit);
  if (overdue) params.set("overdue", "true");
  if (assigneeUserId) params.set("assignee_user_id", assigneeUserId);
  if (source) params.set("source", source);
  if (q && q.trim()) params.set("q", q.trim().slice(0, 200));
  const res = await request(`/api/team/tasks?${params.toString()}`);
  return res.json();
}

export async function createTask(body) {
  return postJSON("/api/team/tasks", body);
}

export async function getTask(taskId) {
  const res = await request(`/api/team/tasks/${taskId}`);
  return res.json();
}

export async function updateTask(taskId, changes) {
  return patchJSON(`/api/team/tasks/${taskId}`, changes);
}

// Multipart: `note` and/or `photo` (a File), whichever the task requires.
// With neither, it's a plain POST: an empty multipart body is rejected (400)
// by the server's form parser.
export async function completeTask(taskId, { note, photo } = {}) {
  let body;
  if (note || photo) {
    body = new FormData();
    if (note) body.append("note", note);
    if (photo) body.append("photo", photo);
  }
  const res = await request(`/api/team/tasks/${taskId}/complete`, { method: "POST", body });
  return res.json();
}

export async function cantDoTask(taskId, reason) {
  return postJSON(`/api/team/tasks/${taskId}/cant-do`, { reason });
}

export async function reopenTask(taskId) {
  const res = await request(`/api/team/tasks/${taskId}/reopen`, { method: "POST" });
  return res.json();
}

export async function commentOnTask(taskId, body) {
  return postJSON(`/api/team/tasks/${taskId}/comments`, { body });
}

export async function setTaskItem(taskId, itemId, done) {
  return patchJSON(`/api/team/tasks/${taskId}/items/${itemId}`, { done });
}

// Oct 15 steps editing (creator / admin; never the demo login): the whole
// list in order - rows with an id keep their tick, rows without are added,
// steps left out are removed. Returns the task (detail).
export async function replaceTaskItems(taskId, items, expectedUpdatedAt) {
  const res = await request(`/api/team/tasks/${taskId}/items`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, ...(expectedUpdatedAt ? { expected_updated_at: expectedUpdatedAt } : {}) }),
  });
  return res.json();
}

// Oct 15, admins: {task_ids, action: "done" | "cancel", note?} ->
// {done: [ids], skipped: [{id, reason}], tasks: [...]}.
export async function bulkTasks(taskIds, action, note) {
  return postJSON("/api/team/tasks/bulk", { task_ids: taskIds, action, ...(note ? { note } : {}) });
}

// Oct 15 "My team today" (admins; assigners for their own teams).
export async function getTaskSummary() {
  const res = await request("/api/team/task-summary");
  return res.json();
}

export async function getTaskBatch(batchId) {
  const res = await request(`/api/team/task-batches/${encodeURIComponent(batchId)}`);
  return res.json();
}

export async function cancelTaskBatch(batchId) {
  const res = await request(`/api/team/task-batches/${encodeURIComponent(batchId)}/cancel`, { method: "POST" });
  return res.json();
}

// Team media (task proof photos) is auth-checked, so it can't be a plain
// <img src>: fetch it with the session and hand back an object URL. The
// caller revokes it when done.
export async function fetchTeamMediaUrl(url) {
  const res = await request(url);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// --- Team hub: announcements + moderation (Phase 2) -----------------------

// Upload an image first, then refer to it by its token (announcements).
export async function uploadTeamMedia(file) {
  const body = new FormData();
  body.append("file", file);
  const res = await request("/api/team/media", { method: "POST", body });
  return res.json();
}

export async function listAnnouncements({ beforeId, limit } = {}) {
  const params = new URLSearchParams();
  if (beforeId) params.set("before_id", beforeId);
  if (limit) params.set("limit", limit);
  const qs = params.toString();
  const res = await request(`/api/team/announcements${qs ? `?${qs}` : ""}`);
  return res.json();
}

export async function createAnnouncement(body) {
  return postJSON("/api/team/announcements", body);
}

// `{title?, body?, pinned?, deleted?}`
export async function updateAnnouncement(announcementId, changes) {
  return patchJSON(`/api/team/announcements/${announcementId}`, changes);
}

export async function markAnnouncementSeen(announcementId) {
  const res = await request(`/api/team/announcements/${announcementId}/seen`, { method: "POST" });
  return res.json();
}

export async function ackAnnouncement(announcementId) {
  const res = await request(`/api/team/announcements/${announcementId}/ack`, { method: "POST" });
  return res.json();
}

// Admin: `{total, seen, acked, summary, recipients: [{user_id, name, seen_at, acked_at, last_reminded_at}]}`
export async function getAnnouncementRecipients(announcementId) {
  const res = await request(`/api/team/announcements/${announcementId}/recipients`);
  return res.json();
}

export async function remindAnnouncement(announcementId) {
  const res = await request(`/api/team/announcements/${announcementId}/remind`, { method: "POST" });
  return res.json();
}

export async function listReports({ status, beforeId } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (beforeId) params.set("before_id", beforeId);
  const qs = params.toString();
  const res = await request(`/api/team/reports${qs ? `?${qs}` : ""}`);
  return res.json();
}

export async function resolveReport(reportId, { note, deleteMessage } = {}) {
  return postJSON(`/api/team/reports/${reportId}/resolve`, { note: note || null, delete_message: !!deleteMessage });
}

export async function dismissReport(reportId, note) {
  return postJSON(`/api/team/reports/${reportId}/dismiss`, { note: note || null });
}

// Admin overview of every DM block (never the reason - there isn't one).
export async function listAllBlocks() {
  const res = await request("/api/team/blocks?all=true");
  return res.json();
}

export async function listTaskTemplates() {
  const res = await request("/api/team/templates");
  return res.json();
}

export async function createTaskTemplate(body) {
  return postJSON("/api/team/templates", body);
}

export async function updateTaskTemplate(templateId, changes) {
  return patchJSON(`/api/team/templates/${templateId}`, changes);
}

export async function deactivateTaskTemplate(templateId) {
  const res = await request(`/api/team/templates/${templateId}`, { method: "DELETE" });
  return res.json();
}

export async function previewTaskTemplate(rrule, generateAheadMinutes) {
  return postJSON("/api/team/templates/preview", { rrule, generate_ahead_minutes: generateAheadMinutes });
}

export async function getJobStatus() {
  const res = await request("/api/jobs/status");
  return res.json();
}

export async function uploadInventory(file) {
  return uploadInventoryWithProgress(file);
}

// Inventory from Toast (admin page; backend routers/inventory_api.py).
export async function getInventoryApiStatus() {
  const res = await request("/api/inventory-api/status");
  return res.json();
}

export async function getInventoryApiCompare(refresh = false) {
  const res = await request(`/api/inventory-api/compare${refresh ? "?refresh=true" : ""}`);
  return res.json();
}

export async function getInventoryApiNightlyCompare() {
  const res = await request("/api/inventory-api/compare/nightly");
  return res.json();
}

// "Use Toast for inventory" switch (admins): state, where it comes from,
// history and the readiness streak; PUT turns it on/off at once.
export async function getInventoryApiSwitch() {
  const res = await request("/api/inventory-api/switch");
  return res.json();
}

export async function setInventoryApiSwitch(on, note) {
  const res = await request("/api/inventory-api/switch", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: Boolean(on), note: note || null }),
  });
  return res.json();
}

export async function runInventoryApiMenuRecheck() {
  return postJSON("/api/inventory-api/menu-recheck", {});
}

export function uploadInventoryWithProgress(file, onProgress) {
  const formData = new FormData();
  formData.append("file", file);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/inventory/upload`);
    setStoreHeadersOn(xhr, "/api/inventory/upload");
    if (isNative()) {
      const token = getCachedToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    } else {
      xhr.withCredentials = true;
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // response wasn't JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      if (xhr.status === 401) {
        window.dispatchEvent(new Event("auth:unauthorized"));
      }
      const detail = errorText(body, (body && JSON.stringify(body)) || xhr.statusText);
      const error = new Error(detail);
      error.status = xhr.status;
      reject(error);
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(formData);
  });
}

export function submitInvoiceWithProgress(fields, file, onProgress) {
  const formData = new FormData();
  formData.append("vendor", fields.vendor);
  formData.append("submission_type", fields.submissionType);
  if (fields.transferDirection) formData.append("transfer_direction", fields.transferDirection);
  if (fields.totalAmount !== "" && fields.totalAmount != null) {
    formData.append("total_amount", fields.totalAmount);
  }
  if (fields.notes) formData.append("notes", fields.notes);
  formData.append("file", file);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/invoices`);
    setStoreHeadersOn(xhr, "/api/invoices");
    if (isNative()) {
      const token = getCachedToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    } else {
      xhr.withCredentials = true;
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // response wasn't JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      if (xhr.status === 401) {
        window.dispatchEvent(new Event("auth:unauthorized"));
      }
      const detail = errorText(body, (body && JSON.stringify(body)) || xhr.statusText);
      const error = new Error(detail);
      error.status = xhr.status;
      reject(error);
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(formData);
  });
}

export async function getInvoiceSubmissions() {
  const res = await request("/api/invoices");
  return res.json();
}

export async function getInvoiceSubmission(id) {
  const res = await request(`/api/invoices/${id}`);
  return res.json();
}

export async function resendInvoiceSubmission(id, channels) {
  return postJSON(`/api/invoices/${id}/resend`, { channels });
}

// Admin-only safety valve for when the real automated WhatsApp/Toast-email
// attempt is stuck or unreliable - see backend services/invoice_submission.py
// ::override_channel_status. status is "sent" | "failed" | "pending".
export async function overrideInvoiceStatus(id, channel, status) {
  return postJSON(`/api/invoices/${id}/override-status`, { channel, status });
}

// A human's manual confirmation that Toast actually finished processing
// this into a real closed bill - unobservable automatically (no Purchasing
// API access, no webhook), so this is the one place "Completed" comes from.
export async function markInvoiceToastClosed(id) {
  return postJSON(`/api/invoices/${id}/mark-toast-closed`, {});
}

export async function unmarkInvoiceToastClosed(id) {
  return postJSON(`/api/invoices/${id}/unmark-toast-closed`, {});
}

export async function requestDeleteInvoice(id) {
  return postJSON(`/api/invoices/${id}/request-delete`, {});
}

export async function cancelDeleteRequest(id) {
  return postJSON(`/api/invoices/${id}/cancel-delete-request`, {});
}

export async function approveDeleteInvoice(id) {
  return postJSON(`/api/invoices/${id}/approve-delete`, {});
}

export async function adminDeleteInvoice(id) {
  return postJSON(`/api/invoices/${id}/delete`, {});
}

export async function restoreInvoice(id) {
  return postJSON(`/api/invoices/${id}/restore`, {});
}

export async function permanentlyDeleteInvoice(id) {
  const res = await request(`/api/invoices/${id}/permanent`, { method: "DELETE" });
  return res.json();
}

export async function getInvoiceTrash() {
  const res = await request("/api/invoices/trash");
  return res.json();
}

export async function getReorderCandidates(lookbackDays, leadTimeDays) {
  const params = new URLSearchParams({ lookback_days: lookbackDays, lead_time_days: leadTimeDays });
  const res = await request(`/api/ordering/reorder-candidates?${params.toString()}`);
  return res.json();
}

// Critical vendors hidden from Today (Close / Skip this week / Remind me
// later) - docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md. Store-wide; writes are
// admin-only. `includeHistory` (admins) adds the last 60 days.
export async function getCriticalDismissals(includeHistory = false) {
  const res = await request(`/api/reorder/critical-dismissals${includeHistory ? "?include_history=1" : ""}`);
  return res.json();
}

// body: {vendor, kind: "close" | "skip_week" | "snooze", reason?, note?, preset?, date?}
export async function hideCriticalVendor(body) {
  return postJSON("/api/reorder/critical-dismissals", body);
}

// "Show again" (also Undo): 204, no body.
export async function showCriticalVendorAgain(id) {
  await request(`/api/reorder/critical-dismissals/${id}`, { method: "DELETE" });
}

export async function getSuppliers() {
  const res = await request("/api/ordering/suppliers");
  return res.json();
}

export async function getVendorSchedules() {
  const res = await request("/api/vendor-calendar/schedules");
  return res.json();
}

export async function createVendorSchedule(vendor, deliveryWeekdays, leadDays) {
  return postJSON("/api/vendor-calendar/schedules", {
    vendor,
    delivery_weekdays: deliveryWeekdays,
    lead_days: leadDays,
  });
}

export async function updateVendorSchedule(id, fields) {
  return patchJSON(`/api/vendor-calendar/schedules/${id}`, fields);
}

export async function deleteVendorSchedule(id) {
  const res = await request(`/api/vendor-calendar/schedules/${id}`, { method: "DELETE" });
  return res.json();
}

export async function getVendorCalendar(startDate, endDate) {
  // include_ordered: show already-placed orders as a green "Ordered" chip
  // (the server leaves them out by default for older app builds).
  const params = new URLSearchParams({ start: startDate, end: endDate, include_ordered: "true" });
  const res = await request(`/api/vendor-calendar?${params.toString()}`);
  return res.json();
}

export async function skipVendorOrder(vendor, deliveryDate) {
  const res = await request("/api/vendor-calendar/skips", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vendor, delivery_date: deliveryDate }),
  });
  return res.json();
}

export async function unskipVendorOrder(vendor, deliveryDate) {
  const params = new URLSearchParams({ vendor, delivery_date: deliveryDate });
  const res = await request(`/api/vendor-calendar/skips?${params.toString()}`, { method: "DELETE" });
  return res.json();
}

// Today-tab order actions (docs/BATCH_OCT14_CONTRACT.md W1; admins only).
// body: {vendor, action: "placed" | "skip" | "other", delivery_date?, note?}
export async function postOrderAction(body) {
  return postJSON("/api/order-actions", body);
}

// Undo "Order already placed" (only an order marked that way).
export async function undoOrderPlacedOutside(orderId) {
  const res = await request(`/api/order-actions/placed/${orderId}`, { method: "DELETE" });
  return res.json();
}

// Undo Skip this week / Other for one order cycle.
export async function undoOrderSkip(vendor, deliveryDate) {
  const params = new URLSearchParams({ vendor, delivery_date: deliveryDate });
  const res = await request(`/api/order-actions/skip?${params.toString()}`, { method: "DELETE" });
  return res.json();
}

export async function getInvoiceVendorsToday() {
  const res = await request("/api/inventory/invoice-vendors-today");
  return res.json();
}

export async function getSupplierProjection(supplier, startDate, endDate, horizonDays) {
  const params = new URLSearchParams({ supplier, start: startDate, end: endDate });
  if (horizonDays) params.set("horizons", String(horizonDays));
  const res = await request(`/api/ordering/supplier-projection?${params.toString()}`);
  return res.json();
}

// Each week also has avg_on_hand / on_hand_days / period_days (Oct 14 W3);
// includeEmptyWeeks lists weeks with no sale too (stock counts only).
export async function getItemWeeklySales(itemId, startDate, endDate, { includeEmptyWeeks = false } = {}) {
  const params = new URLSearchParams({ item_id: itemId, start: startDate, end: endDate });
  if (includeEmptyWeeks) params.set("include_empty_weeks", "true");
  const res = await request(`/api/ordering/item-weekly-sales?${params.toString()}`);
  return res.json();
}

// Similar products (other brands/sizes of the same thing) for one item, for
// the Supplier Projection "Similar" drawer.
export async function getSimilarItems(itemId, limit) {
  const params = new URLSearchParams({ item_id: itemId });
  if (limit) params.set("limit", String(limit));
  const res = await request(`/api/ordering/similar-items?${params.toString()}`);
  return res.json();
}

export async function exportPurchaseOrderPdf(supplier, items) {
  const res = await request("/api/ordering/purchase-order/export-pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ supplier, items }),
  });
  const blob = await res.blob();
  const match = (res.headers.get("Content-Disposition") || "").match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `purchase-order-${supplier}.pdf`;
  await saveOrShareBlob(blob, filename);
}

export async function getCart() {
  const res = await request("/api/ordering/cart");
  return res.json();
}

// X1: the cart with each line's projection context (weekly sales, on hand,
// suggested qty + basis) - a second, slower read the cart page makes after
// showing the plain cart. Same window as the Add items suggestions.
export async function getCartProjection({ lookbackDays = 90, horizonDays = 7 } = {}) {
  const params = new URLSearchParams({
    projection: "true",
    lookback_days: String(lookbackDays),
    horizon_days: String(horizonDays),
  });
  const res = await request(`/api/ordering/cart?${params.toString()}`);
  return res.json();
}

export async function addToCart(supplier, items) {
  const result = await postJSON("/api/ordering/cart/items", { supplier, items });
  window.dispatchEvent(new Event("cart:updated"));
  return result;
}

export async function updateCartItem(cartItemId, fields) {
  const res = await request(`/api/ordering/cart/items/${cartItemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  return res.json();
}

export async function deleteCartItem(cartItemId) {
  const res = await request(`/api/ordering/cart/items/${cartItemId}`, { method: "DELETE" });
  const result = await res.json();
  window.dispatchEvent(new Event("cart:updated"));
  return result;
}

// Cart removal requests: only an admin can remove a cart line; anyone else
// asks for it (owner, Sep 30). The requester (or an admin) can cancel; an
// admin approves (deletes the line) or rejects (keeps it).
async function cartRemovalCall(cartItemId, suffix, method, body) {
  const res = await request(`/api/ordering/cart/items/${cartItemId}/removal-request${suffix}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await res.json();
  window.dispatchEvent(new Event("cart:updated"));
  return result;
}

export function requestCartRemoval(cartItemId, reason) {
  return cartRemovalCall(cartItemId, "", "POST", { reason: reason || null });
}

export function cancelCartRemoval(cartItemId) {
  return cartRemovalCall(cartItemId, "", "DELETE");
}

export function approveCartRemoval(cartItemId) {
  return cartRemovalCall(cartItemId, "/approve", "POST", {});
}

export function rejectCartRemoval(cartItemId) {
  return cartRemovalCall(cartItemId, "/reject", "POST", {});
}

export async function clearSupplierCart(supplier) {
  const params = new URLSearchParams({ supplier });
  const res = await request(`/api/ordering/cart?${params.toString()}`, { method: "DELETE" });
  const result = await res.json();
  window.dispatchEvent(new Event("cart:updated"));
  return result;
}

// Like clearSupplierCart, but also records a PlacedOrder snapshot of what
// was in the cart first (see backend's ordering.py::post_mark_order_placed) -
// the real "we actually placed this order" action, distinct from just
// discarding a cart section.
export async function markOrderPlaced(supplier) {
  const params = new URLSearchParams({ supplier });
  const res = await request(`/api/ordering/cart/mark-placed?${params.toString()}`, { method: "POST" });
  const result = await res.json();
  window.dispatchEvent(new Event("cart:updated"));
  return result;
}

// Real order-placement history (PlacedOrder) - see that model's docstring
// for what "placed" means (a PO PDF was exported, or the cart was
// explicitly marked "Order placed"). Was mobile-only until now.
export async function getRecentPlacedOrders(limit = 10) {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await request(`/api/ordering/orders/recent?${params.toString()}`);
  return res.json();
}

// Full placed-order history for the Placed Orders page. `filters`: supplier,
// start/end (store-local YYYY-MM-DD), status ("placed" | "pdf"), search,
// limit, offset - empty values are left out.
export async function getPlacedOrders(filters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const res = await request(`/api/ordering/orders?${params.toString()}`);
  return res.json();
}

// Puts a past order's items back into that vendor's cart.
export async function orderAgain(orderId) {
  const res = await request(`/api/ordering/orders/${orderId}/order-again`, { method: "POST" });
  const result = await res.json();
  window.dispatchEvent(new Event("cart:updated"));
  return result;
}

export async function getItemsSold(start, end, { fresh = false } = {}) {
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  // Refresh button: skip the server's short live cache for today.
  if (fresh) params.set("fresh", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await request(`/api/sales/items-sold${suffix}`);
  return res.json();
}

// Sales accuracy (admin): recent business dates compared with Toast
// Analytics, and what the re-pull job corrected.
export async function getSalesAccuracy(days = 30) {
  const res = await request(`/api/sales/accuracy?days=${days}`);
  return res.json();
}

export async function runSalesAccuracy(days) {
  const suffix = days ? `?days=${days}` : "";
  const res = await request(`/api/sales/accuracy/run${suffix}`, { method: "POST" });
  return res.json();
}


export async function getPriceChanges() {
  const res = await request("/api/inventory/price-changes");
  return res.json();
}

export async function searchPriceHistory(query) {
  const params = new URLSearchParams({ search: query });
  const res = await request(`/api/inventory/price-history?${params.toString()}`);
  return res.json();
}

async function downloadFile(path, fallbackFilename) {
  const res = await request(path);
  const blob = await res.blob();
  const match = (res.headers.get("Content-Disposition") || "").match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : fallbackFilename;
  await saveOrShareBlob(blob, filename);
}

// A PO as Excel / PDF / CSV (PoDownloadMenu). target: { kind: "order" |
// "send", id } or { kind: "cart", supplier }; read-only on the server.
export async function downloadPo(target, format, supplier) {
  await downloadFile(poExportPath(target, format), poFallbackName(supplier || target?.supplier, format, storeToday()));
}

export async function downloadSales(start, end, format) {
  const params = new URLSearchParams({ start, end, format });
  await downloadFile(`/api/sales/export?${params.toString()}`, `sales.${format}`);
}

export async function getMissingBarcodes() {
  const res = await request("/api/reports/missing-barcodes");
  return res.json();
}

export async function getInvalidBarcodes() {
  const res = await request("/api/reports/invalid-barcodes");
  return res.json();
}

export async function downloadMissingBarcodes() {
  await downloadFile("/api/reports/missing-barcodes/export", "missing-barcodes.csv");
}

export async function downloadInvalidBarcodes() {
  await downloadFile("/api/reports/invalid-barcodes/export", "invalid-barcodes.csv");
}

export async function downloadPriceChanges() {
  await downloadFile("/api/inventory/price-changes/export", "price-change-log.csv");
}

export async function getDeadStock() {
  const res = await request("/api/reports/dead-stock");
  return res.json();
}

export async function downloadDeadStock() {
  await downloadFile("/api/reports/dead-stock/export", "dead-stock.csv");
}

export async function getVendorPriceComparison() {
  const res = await request("/api/reports/vendor-price-comparison");
  return res.json();
}

export async function getNegativeInventory() {
  const res = await request("/api/reports/negative-inventory");
  return res.json();
}

export async function downloadNegativeInventory() {
  await downloadFile("/api/reports/negative-inventory/export", "negative-inventory.csv");
}

export async function downloadVendorPriceComparison() {
  await downloadFile("/api/reports/vendor-price-comparison/export", "vendor-price-comparison.csv");
}

// Store vs Container (items with both a store row and a Container row).
function storeVsContainerQuery({ search, vendor } = {}) {
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (vendor) params.set("vendor", vendor);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export async function getStoreVsContainer(filters) {
  const res = await request(`/api/reports/store-vs-container${storeVsContainerQuery(filters)}`);
  return res.json();
}

export async function downloadStoreVsContainer(filters) {
  await downloadFile(`/api/reports/store-vs-container/export${storeVsContainerQuery(filters)}`, "store-vs-container.csv");
}

// CSV downloads for Items Sold, Open Orders and the Reconciliation page's
// invoice gaps / purchase log (same data the page shows, whole list).
export async function downloadItemsSold(start, end) {
  const params = new URLSearchParams({ start, end });
  await downloadFile(`/api/sales/items-sold/export?${params.toString()}`, `items-sold-${start}_to_${end}.csv`);
}

// allTime: the cached all-time list; otherwise the live list for `date`.
export async function downloadOpenOrders({ allTime, date, employeeGuid } = {}) {
  const params = new URLSearchParams({ mode: allTime ? "all_time" : "day" });
  if (!allTime && date) params.set("date", date);
  if (employeeGuid) params.set("employee_guid", employeeGuid);
  await downloadFile(`/api/orders/open/export?${params.toString()}`, "open-checks.csv");
}

export async function downloadInvoiceGaps(startDate, endDate) {
  const params = new URLSearchParams({ start: startDate, end: endDate });
  await downloadFile(
    `/api/reconciliation/invoice-gaps/export?${params.toString()}`,
    `invoice-gaps-${startDate}-${endDate}.csv`
  );
}

export async function downloadPurchaseLog() {
  await downloadFile("/api/reconciliation/purchases/export", "purchase-log.csv");
}

export async function searchItems(query, supplier) {
  const params = new URLSearchParams({ q: query });
  if (supplier) params.set("supplier", supplier);
  const res = await request(`/api/inventory/items/search?${params.toString()}`);
  return res.json();
}

export async function downloadPurchaseLogSampleCsv() {
  const rows = [
    "item name,supplier,quantity,unit cost,received date,notes",
    "Nanak Rasmalai 12 Pcs/1kg,ABC Distributors,12,18.50,2026-08-20,Invoice #4521",
    "Haldiram's Gulab Jamun (Can) 1Kg,ABC Distributors,6,9.75,2026-08-20,Invoice #4521",
  ].join("\n");
  const blob = new Blob([rows], { type: "text/csv" });
  await saveOrShareBlob(blob, "purchase-log-sample.csv");
}

export async function logPurchase(entry) {
  return postJSON("/api/reconciliation/purchases", entry);
}

export function uploadPurchaseLogWithProgress(file, defaultReceivedDate, onProgress) {
  const formData = new FormData();
  formData.append("file", file);
  if (defaultReceivedDate) formData.append("default_received_date", defaultReceivedDate);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/reconciliation/purchases/upload`);
    setStoreHeadersOn(xhr, "/api/reconciliation/purchases/upload");
    if (isNative()) {
      const token = getCachedToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    } else {
      xhr.withCredentials = true;
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // response wasn't JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      if (xhr.status === 401) {
        window.dispatchEvent(new Event("auth:unauthorized"));
      }
      const detail = errorText(body, (body && JSON.stringify(body)) || xhr.statusText);
      const error = new Error(detail);
      error.status = xhr.status;
      reject(error);
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(formData);
  });
}

export async function getPurchases(startDate, endDate) {
  const params = new URLSearchParams();
  const res = await request(`/api/reconciliation/purchases?${params.toString()}`);
  return res.json();
}

export async function deletePurchase(id) {
  const res = await request(`/api/reconciliation/purchases/${id}`, { method: "DELETE" });
  return res.json();
}

export async function getDeliveryCandidates(vendor) {
  const params = new URLSearchParams({ vendor });
  const res = await request(`/api/reconciliation/delivery-candidates?${params.toString()}`);
  return res.json();
}

export async function getDeliveryHistory(days = 14) {
  const res = await request(`/api/reconciliation/delivery-history?days=${days}`);
  return res.json();
}

// --- Suggestions (automation batch items 4, 5, 7) ---------------------------
// Read-only suggestions; confirming one uses the existing write
// (confirmDelivery / markInvoiceToastClosed / createVendorSchedule).

export async function getSuggestedDeliveries(days = 3, includeDismissed = false) {
  const params = new URLSearchParams({ days: String(days) });
  if (includeDismissed) params.set("include_dismissed", "true");
  const res = await request(`/api/reconciliation/suggested-deliveries?${params.toString()}`);
  return res.json();
}

export async function dismissSuggestedDelivery(key) {
  return postJSON("/api/reconciliation/suggested-deliveries/dismiss", { key });
}

export async function undoDismissSuggestedDelivery(key) {
  const params = new URLSearchParams({ key });
  const res = await request(`/api/reconciliation/suggested-deliveries/dismiss?${params.toString()}`, { method: "DELETE" });
  return res.json();
}

export async function getInvoiceCloseSuggestions() {
  const res = await request("/api/invoices/close-suggestions");
  return res.json();
}

export async function dismissInvoiceCloseSuggestion(id) {
  return postJSON(`/api/invoices/${id}/close-suggestion/dismiss`, {});
}

export async function undoDismissInvoiceCloseSuggestion(id) {
  const res = await request(`/api/invoices/${id}/close-suggestion/dismiss`, { method: "DELETE" });
  return res.json();
}

export async function getScheduleSuggestions() {
  const res = await request("/api/vendor-calendar/schedule-suggestions");
  return res.json();
}

export async function dismissScheduleSuggestion(vendor, deliveryWeekdays) {
  return postJSON("/api/vendor-calendar/schedule-suggestions/dismiss", { vendor, delivery_weekdays: deliveryWeekdays });
}

export async function undoDismissScheduleSuggestion(key) {
  const params = new URLSearchParams({ key });
  const res = await request(`/api/vendor-calendar/schedule-suggestions/dismiss?${params.toString()}`, { method: "DELETE" });
  return res.json();
}

// "Add all suggestions" (X2): the keys the person confirmed; a vendor that
// has a schedule by then is skipped, never overwritten.
export async function acceptAllScheduleSuggestions(keys) {
  return postJSON("/api/vendor-calendar/schedule-suggestions/accept-all", { keys });
}

export async function confirmDelivery(vendor, receivedDate, items) {
  return postJSON("/api/reconciliation/delivery-confirm", { vendor, received_date: receivedDate, items });
}

export async function getReconciliationDemo() {
  const res = await request("/api/reconciliation/demo");
  return res.json();
}

export async function getReconciliationReport(startDate, endDate) {
  const params = new URLSearchParams({ start: startDate, end: endDate });
  const res = await request(`/api/reconciliation/report?${params.toString()}`);
  return res.json();
}

export async function downloadReconciliationReport(startDate, endDate) {
  const params = new URLSearchParams({ start: startDate, end: endDate });
  await downloadFile(
    `/api/reconciliation/report/export?${params.toString()}`,
    `reconciliation-${startDate}-${endDate}.csv`
  );
}

export async function getInvoiceGaps(startDate, endDate, windowDays) {
  const params = new URLSearchParams({ start: startDate, end: endDate });
  if (windowDays != null) params.set("window_days", windowDays);
  const res = await request(`/api/reconciliation/invoice-gaps?${params.toString()}`);
  return res.json();
}

export async function askQuestion(question, history) {
  return postJSON("/api/ask", { question, history });
}

export async function generateWeeklyDigest() {
  return postJSON("/api/weekly-digest/generate", {});
}

export async function getTransferCandidates(direction) {
  const params = new URLSearchParams({ direction });
  const res = await request(`/api/transfers/candidates?${params.toString()}`);
  return res.json();
}

export async function confirmTransfers(direction, transferDate, items) {
  return postJSON("/api/transfers/confirm", { direction, transfer_date: transferDate, items });
}

// Daily Closing Checklists - a native replacement for two Google Forms (see
// backend/app/services/checklist_schemas.py). The backend's schema is the
// single source of truth for both checklists' structure; this app renders
// whatever it returns rather than hardcoding either checklist's questions.
export async function getChecklistSchemas() {
  const res = await request("/api/checklists/schemas");
  return res.json();
}

export async function getChecklistSchema(checklistType) {
  const res = await request(`/api/checklists/schema/${checklistType}`);
  return res.json();
}

// `definitionVersion` = the schema `version` the form was opened with. If an
// admin published a new version meanwhile and the answers don't fit it, the
// server answers 409 (the caller reloads the checklist).
export async function submitChecklist(checklistType, answers, definitionVersion) {
  const body = { answers };
  if (definitionVersion != null) body.definition_version = definitionVersion;
  return postJSON(`/api/checklists/${checklistType}/submit`, body);
}

// employeeUserId/status/dates are honored server-side ONLY for an admin - a
// non-admin caller passing any of these still only ever gets their own
// submissions back (enforced in list_submissions() on the backend, not just
// by what this app's UI happens to show).
export async function getChecklistSubmissions({
  checklistType,
  employeeUserId,
  status,
  startDate,
  endDate,
  limit,
} = {}) {
  const params = new URLSearchParams();
  if (checklistType) params.set("checklist_type", checklistType);
  if (employeeUserId != null) params.set("employee_user_id", employeeUserId);
  if (status) params.set("status", status);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (limit) params.set("limit", limit);
  const qs = params.toString();
  const res = await request(`/api/checklists${qs ? `?${qs}` : ""}`);
  return res.json();
}

export async function getChecklistSubmission(id) {
  const res = await request(`/api/checklists/${id}`);
  return res.json();
}

// The schema a submission was made against (version null = current).
export async function getChecklistSchemaVersion(checklistType, version) {
  const qs = version != null ? `?version=${encodeURIComponent(version)}` : "";
  const res = await request(`/api/checklists/schema/${encodeURIComponent(checklistType)}${qs}`);
  return res.json();
}

// A photo for a checklist file-upload question (daily_checklists grant, same
// processing as team photos). Returns {id, token, url}; the answer stores ids.
export async function uploadChecklistPhoto(file) {
  const body = new FormData();
  body.append("file", file);
  const res = await request("/api/checklists/media", { method: "POST", body });
  return res.json();
}

// --- Public checklist link (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md) ---------
// The logged-out page (/c/{slug}-{token}). These never send the session
// cookie or a bearer token, and never trigger the app's "logged out" bounce.

async function publicRequest(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, { ...options, credentials: "omit", referrerPolicy: "no-referrer" });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // not JSON
  }
  if (!res.ok) {
    const error = new Error(
      typeof body?.detail === "string" ? body.detail : "Something went wrong. Check your connection and try again."
    );
    error.status = res.status;
    error.body = body;
    throw error;
  }
  return body;
}

// `store`: the Store ID from a shared server's link (/c/<CODE>/<token>,
// docs/PLATFORM_CONTRACT.md 4.4); null for a single-store server's /c/<token>.
function storeQuery(store) {
  return store ? `?store=${encodeURIComponent(store)}` : "";
}

export function getPublicChecklist(token, store = null) {
  return publicRequest(`/api/public/checklists/${encodeURIComponent(token)}${storeQuery(store)}`);
}

// body: {name, answers, definition_version, media_keys}
export function submitPublicChecklist(token, body, store = null) {
  return publicRequest(`/api/public/checklists/${encodeURIComponent(token)}/submit${storeQuery(store)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Returns {id, key, width, height}; send `id` in the answer and {id: key} in media_keys.
export function uploadPublicChecklistPhoto(token, file, store = null) {
  const body = new FormData();
  body.append("file", file);
  return publicRequest(`/api/public/checklists/${encodeURIComponent(token)}/media${storeQuery(store)}`, {
    method: "POST",
    body,
  });
}

// --- Checklist builder (admin; docs/CHECKLIST_BUILDER_CONTRACT.md) ---------

const CHECKLIST_ADMIN = "/api/checklists/admin/definitions";

async function putJSONChecklist(path, body) {
  const res = await request(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function listChecklistDefinitions() {
  const res = await request(CHECKLIST_ADMIN);
  return res.json();
}

export async function getChecklistDefinition(checklistType) {
  const res = await request(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}`);
  return res.json();
}

export async function createChecklistDefinition(body) {
  return postJSON(CHECKLIST_ADMIN, body);
}

export async function saveChecklistDraft(checklistType, body) {
  return putJSONChecklist(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}`, body);
}

export async function publishChecklistDefinition(checklistType, body = {}) {
  return postJSON(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/publish`, body);
}

export async function duplicateChecklistDefinition(checklistType, body = {}) {
  return postJSON(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/duplicate`, body);
}

export async function updateChecklistDefinition(checklistType, body) {
  return patchJSON(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}`, body);
}

export async function getChecklistDefinitionVersion(checklistType, version) {
  const res = await request(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/versions/${version}`);
  return res.json();
}

// Admin order of the checklists (the staff hub / app tiles order).
export async function reorderChecklistDefinitions(order) {
  return postJSON(`${CHECKLIST_ADMIN}/order`, { order });
}

export async function discardChecklistDraft(checklistType) {
  const res = await request(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/draft`, { method: "DELETE" });
  return res.json();
}

// Item types, issue tags, validation rules, WhatsApp groups the editor offers.
export async function getChecklistEditorMeta() {
  const res = await request("/api/checklists/admin/meta");
  return res.json();
}

// Public link admin (one per checklist). GET answers {exists: false, ...suggested} when there's none.
export async function getChecklistPublicLink(checklistType) {
  const res = await request(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/public-link`);
  return res.json();
}

export async function saveChecklistPublicLink(checklistType, body) {
  return putJSONChecklist(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/public-link`, body);
}

export async function rotateChecklistPublicLink(checklistType) {
  return postJSON(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/public-link/rotate`, {});
}

export async function deleteChecklistPublicLink(checklistType) {
  const res = await request(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/public-link`, { method: "DELETE" });
  return res.json();
}

export async function revertChecklistDefinition(checklistType, version, body = {}) {
  return postJSON(`${CHECKLIST_ADMIN}/${encodeURIComponent(checklistType)}/revert/${version}`, body);
}

// WhatsApp groups a checklist can post to (admin; docs/CHECKLIST_BUILDER_APP_AND_GROUPS_CONTRACT.md).
const WHATSAPP_GROUPS = "/api/checklists/admin/whatsapp-groups";

// {items: [{key, label, chat_id_masked, configured, builtin, active, marketing, used_by: [titles]}]}
export async function listWhatsAppGroups() {
  const res = await request(WHATSAPP_GROUPS);
  return res.json();
}

// {items: [{chat_id, name, already_added_key}], error?} - never throws for a WhatsApp problem.
export async function listAvailableWhatsAppGroups(refresh = false) {
  const res = await request(`${WHATSAPP_GROUPS}/available${refresh ? "?refresh=1" : ""}`);
  return res.json();
}

export async function addWhatsAppGroup(chatId, label) {
  return postJSON(WHATSAPP_GROUPS, { chat_id: chatId, label });
}

// body: {label?, active?, marketing?} - marketing = "Use for flyers".
export async function updateWhatsAppGroup(key, body) {
  return patchJSON(`${WHATSAPP_GROUPS}/${encodeURIComponent(key)}`, body);
}

// 204 on success; 400 built-in, 409 in use (error.body.used_by).
export async function deleteWhatsAppGroup(key) {
  await request(`${WHATSAPP_GROUPS}/${encodeURIComponent(key)}`, { method: "DELETE" });
}

export async function getOpenOrders(date, employeeGuid) {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (employeeGuid) params.set("employee_guid", employeeGuid);
  const qs = params.toString();
  const res = await request(`/api/orders/open${qs ? `?${qs}` : ""}`);
  return res.json();
}

export async function getOrderEmployees() {
  const res = await request("/api/orders/employees");
  return res.json();
}

export async function getAllTimeOpenOrders(employeeGuid) {
  const params = new URLSearchParams();
  if (employeeGuid) params.set("employee_guid", employeeGuid);
  const qs = params.toString();
  // Instant - reads the background-refreshed cache, never hits Toast live.
  const res = await request(`/api/orders/open/all-time${qs ? `?${qs}` : ""}`);
  return res.json();
}

export async function rescanAllTimeOpenOrders() {
  // Slow (minutes) - forces a real full re-scan instead of waiting for the
  // next scheduled background refresh. Only call this from an explicit user
  // action, never automatically.
  return postJSON("/api/orders/open/all-time/rescan", {});
}

// Fix Issues (website). The mobile app still reads /api/reports/*.
export async function getIssueReasons() {
  const res = await request("/api/issues/reasons");
  return res.json();
}

export async function getIssueSummary() {
  const res = await request("/api/issues/summary");
  return res.json();
}

export async function getIssueList(issueType, { cameBack = false } = {}) {
  const res = await request(`/api/issues/${issueType}${cameBack ? "?came_back=1" : ""}`);
  return res.json();
}

// Every close / reopen of one item, newest first, plus how many times it
// was closed (Fix Issues details panel).
export async function getIssueHistory(issueType, issueKey) {
  const params = new URLSearchParams({ key: issueKey });
  const res = await request(`/api/issues/${issueType}/history?${params.toString()}`);
  return res.json();
}

export async function closeIssues(issueType, issueKeys, reason, reasonDetail) {
  return postJSON("/api/issues/close", {
    issue_type: issueType,
    issue_keys: issueKeys,
    reason,
    reason_detail: reasonDetail || null,
  });
}

export async function reopenIssues(issueType, issueKeys) {
  return postJSON("/api/issues/reopen", { issue_type: issueType, issue_keys: issueKeys });
}

export async function getIssueReport(start, end) {
  const params = new URLSearchParams({ start, end });
  const res = await request(`/api/issues/report?${params.toString()}`);
  return res.json();
}

export async function downloadIssues(issueType, status = "open") {
  await downloadFile(`/api/issues/${issueType}/export?status=${status}`, `${issueType}-${status}.csv`);
}

// --- Team hub: chat (team channels + DMs) ---------------------------------

// Oct 15, optional: `wait` (long-poll up to that many seconds, server caps it
// at 20), `include: "tasks"` (each changed task's row too), `signal` (abort).
export async function getTeamUpdates(since, { wait, include, signal } = {}) {
  const params = new URLSearchParams({ since: String(since) });
  if (wait) params.set("wait", String(wait));
  if (include) params.set("include", include);
  const res = await request(`/api/team/updates?${params.toString()}`, signal ? { signal } : {});
  return res.json();
}

export async function listChatChannels() {
  const res = await request("/api/team/channels");
  const body = await res.json();
  return body.channels || [];
}

export async function getChatChannel(channelId) {
  const res = await request(`/api/team/channels/${channelId}`);
  return res.json();
}

// Who I can start a DM with (people blocked either way are left out).
export async function listChatPeople() {
  const res = await request("/api/team/people");
  const body = await res.json();
  return body.people || [];
}

export async function openDirectMessage(userId) {
  return postJSON("/api/team/channels/dm", { user_id: userId });
}

// For the @mention picker.
export async function listChannelMembers(channelId) {
  const res = await request(`/api/team/channels/${channelId}/members`);
  const body = await res.json();
  return body.members || [];
}

// Oldest first. `{messages, has_more}`.
export async function listChatMessages(channelId, { beforeId, afterId, limit } = {}) {
  const params = new URLSearchParams();
  if (beforeId) params.set("before_id", beforeId);
  if (afterId) params.set("after_id", afterId);
  if (limit) params.set("limit", limit);
  const qs = params.toString();
  const res = await request(`/api/team/channels/${channelId}/messages${qs ? `?${qs}` : ""}`);
  return res.json();
}

// Sending again with the same client_uuid returns the original message, so a
// retry after a timeout can never post twice.
export async function sendChatMessage(channelId, { body, clientUuid, mediaToken }) {
  return postJSON(`/api/team/channels/${channelId}/messages`, {
    body: body || "",
    client_uuid: clientUuid,
    media_token: mediaToken || null,
  });
}

export async function markChannelRead(channelId, lastReadMessageId) {
  return postJSON(`/api/team/channels/${channelId}/read`, { last_read_message_id: lastReadMessageId });
}

// `{muted?, hidden?}` (hidden: DMs only)
export async function updateMyChannel(channelId, changes) {
  return patchJSON(`/api/team/channels/${channelId}/me`, changes);
}

// Own message, or (admins) someone else's with a reason.
export async function deleteChatMessage(messageId, reason) {
  const res = await request(`/api/team/messages/${messageId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason || null }),
  });
  return res.json();
}

export async function reportChatMessage(messageId, reason) {
  return postJSON(`/api/team/messages/${messageId}/report`, { reason });
}

export async function getChatSettings() {
  const res = await request("/api/team/chat/settings");
  return res.json();
}

export async function setHidePreviews(hidePreviews) {
  const res = await request("/api/team/chat/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hide_previews: !!hidePreviews }),
  });
  return res.json();
}

// People I've blocked (DMs only).
export async function listMyBlocks() {
  const res = await request("/api/team/blocks");
  const body = await res.json();
  return body.blocks || [];
}

export async function blockUser(userId) {
  return postJSON("/api/team/blocks", { user_id: userId });
}

export async function unblockUser(userId) {
  const res = await request(`/api/team/blocks/${userId}`, { method: "DELETE" });
  return res.json();
}

// --- Team advanced (W5): board, stats, templates "Create task now", pins,
// seen-by, message search, voice notes and documents --------------------

// {started: true} = In progress, false = back to To do.
export async function setTaskStarted(taskId, started) {
  return postJSON(`/api/team/tasks/${taskId}/start`, { started: !!started });
}

// Admins: per-person / per-team completion for tasks created in the last `days`.
export async function getTaskStats(days = 30) {
  const res = await request(`/api/team/task-stats?days=${encodeURIComponent(days)}`);
  return res.json();
}

// `{assignee_user_ids?, assignee_team_id?, due_at?}`; empty = the template's own assignee.
export async function createTaskFromTemplate(templateId, body = {}) {
  return postJSON(`/api/team/templates/${templateId}/create-task`, body);
}

// `{pins, can_pin, max_pins}`
export async function listChannelPins(channelId) {
  const res = await request(`/api/team/channels/${channelId}/pins`);
  return res.json();
}

export async function pinChatMessage(messageId, pinned) {
  const res = await request(`/api/team/messages/${messageId}/pin`, { method: pinned ? "POST" : "DELETE" });
  return res.json();
}

// `{total, seen: [{id, name}], not_seen: [...]}`
export async function getMessageSeenBy(messageId) {
  const res = await request(`/api/team/messages/${messageId}/seen-by`);
  return res.json();
}

// `{results: [message + channel {id, kind, name}], has_more}`
export async function searchChatMessages(q, { channelId, beforeId, limit } = {}) {
  const params = new URLSearchParams({ q });
  if (channelId) params.set("channel_id", channelId);
  if (beforeId) params.set("before_id", beforeId);
  if (limit) params.set("limit", limit);
  const res = await request(`/api/team/messages/search?${params.toString()}`);
  return res.json();
}

// A chat attachment: a photo, a voice note (Blob, AAC in MP4) or a PDF.
// Returns the media object ({token, url, kind, name, duration_ms, ...}).
export async function uploadChatAttachment(file, { filename, durationMs } = {}) {
  const body = new FormData();
  if (filename) body.append("file", file, filename);
  else body.append("file", file);
  if (durationMs) body.append("duration_ms", String(Math.round(durationMs)));
  const res = await request("/api/team/media", { method: "POST", body });
  return res.json();
}

// Definitions of every status / tag / label (backend/app/glossary.py).
export async function getGlossary() {
  const res = await request("/api/glossary");
  return res.json();
}

// ---- PO sections (backend routers/po_sections.py, served on /api/categories/*) ----
// The section each product prints under on the vendor PO PDF. Needs the
// `category_edits` feature (admins always). An item's `section` is where it
// prints now, `automatic_section` where it would print with no rule.

async function jsonRequest(method, path, body) {
  const res = await request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

function queryString(filters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  }
  return params.toString();
}

// filters: q, vendor, section, source (toast = automatic | item | vendor | changed), limit.
export async function getSectionItems(filters = {}) {
  const res = await request(`/api/categories/items?${queryString(filters)}`);
  return res.json();
}

// { groups, combos, vendors, sections: [{section, items, automatic, is_other, renamed_from, ...}] }
export async function getSectionOptions() {
  const res = await request("/api/categories/options");
  return res.json();
}

export async function setItemSection(itemId, section) {
  return jsonRequest("PUT", `/api/categories/items/${encodeURIComponent(itemId)}`, { section });
}

// toAutomatic true = "Back to automatic" (also over a vendor rule); false =
// just remove this item's own rule (its vendor rule applies again).
export async function resetItemSection(itemId, toAutomatic = true) {
  return jsonRequest("DELETE", `/api/categories/items/${encodeURIComponent(itemId)}?to_toast=${toAutomatic}`);
}

export async function bulkSetSection(itemIds, section) {
  return postJSON("/api/categories/items/bulk", { section, item_ids: itemIds });
}

export async function bulkResetSection(itemIds, toAutomatic = true) {
  return postJSON("/api/categories/items/reset", { item_ids: itemIds, to_toast: toAutomatic });
}

// Every current item of a vendor ({vendor}) or of a section ({from_section}) to `section`.
export async function moveItemsToSection(from, section) {
  return postJSON("/api/categories/items/move", { ...from, section });
}

export async function getSectionVendorRules(includeInactive = false) {
  const res = await request(`/api/categories/vendor-rules?include_inactive=${includeInactive}`);
  return res.json();
}

export async function createSectionVendorRule(vendor, section, note) {
  return postJSON("/api/categories/vendor-rules", { vendor, section, note: note || null });
}

export async function deactivateSectionVendorRule(ruleId) {
  return jsonRequest("DELETE", `/api/categories/vendor-rules/${ruleId}`);
}

export async function getSections() {
  const res = await request("/api/categories/sections");
  return res.json();
}

export async function getSectionRenames(includeInactive = false) {
  const res = await request(`/api/categories/renames?include_inactive=${includeInactive}`);
  return res.json();
}

// Rename a section, or merge it into another when `toSection` already exists.
export async function renameSection(fromSection, toSection, note) {
  return postJSON("/api/categories/renames", { from_section: fromSection, to_section: toSection, note: note || null });
}

export async function undoSectionRename(ruleId) {
  return jsonRequest("DELETE", `/api/categories/renames/${ruleId}`);
}

export async function getSectionHistory(filters = {}) {
  const res = await request(`/api/categories/history?${queryString(filters)}`);
  return res.json();
}

export async function getSectionChanges(limit = 20) {
  const res = await request(`/api/categories/changes?limit=${limit}`);
  return res.json();
}

// `batch` = the change shown as newest; the server refuses (409) if another came in since.
export async function undoLastSectionChange(batch) {
  return postJSON("/api/categories/changes/undo", { batch: batch || null });
}

// { suppliers: [{supplier, lines}], supplier, sections: [{section, lines}] }
export async function getSectionCartPreview(supplier) {
  const res = await request(`/api/categories/cart-preview?${queryString({ supplier })}`);
  return res.json();
}

// null / "" = back to automatic (catalog item) or Other (hand-added line).
export async function setCartLineSection(cartItemId, section) {
  return jsonRequest("PUT", `/api/categories/cart-lines/${cartItemId}`, { section: section || null });
}

// ---- Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md, section 4) ----
// Every route needs the `marketing_flyers` feature (admins always). A flyer's
// file_url / thumb_url are relative API paths that need the session, so
// pictures are fetched with fetchFlyerObjectUrl, never put in a bare <img src>.

const FLYERS = "/api/flyers";

// {categories: [{key, label}], groups: [{key, label}] (marketing groups only),
//  whatsapp_connected, max_image_mb, max_pdf_mb, accept, max_groups_per_send}
export async function getFlyerMeta() {
  const res = await request(`${FLYERS}/meta`);
  return res.json();
}

// {items: [Flyer], next_before_id, counts?: {all, <category key>: n}} - newest first.
export async function listFlyers({ category, q, limit = 30, beforeId } = {}) {
  const qs = queryString({ category, q, limit, before_id: beforeId });
  const res = await request(`${FLYERS}${qs ? `?${qs}` : ""}`);
  return res.json();
}

// A Flyer with its `sends` (newest first).
export async function getFlyer(flyerId) {
  const res = await request(`${FLYERS}/${flyerId}`);
  return res.json();
}

// Readable text for a failed upload: FastAPI's 422 detail is a list.
function uploadErrorText(body, fallback) {
  return errorText(body, fallback);
}

// POST /api/flyers (multipart: file, title, category, caption?) -> the new
// Flyer (201). onProgress(0-100) follows the upload; a 400 carries a
// friendly message (unsupported file, too big, bad category, empty title).
export function uploadFlyerWithProgress({ file, title, category, caption }, onProgress) {
  const formData = new FormData();
  formData.append("file", file, file.name);
  formData.append("title", title);
  formData.append("category", category);
  if (caption) formData.append("caption", caption);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${FLYERS}`);
    setStoreHeadersOn(xhr, `${FLYERS}`);
    if (isNative()) {
      const token = getCachedToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    } else {
      xhr.withCredentials = true;
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // response wasn't JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      if (xhr.status === 401) window.dispatchEvent(new Event("auth:unauthorized"));
      const fallback = xhr.status === 413 ? "That file is too big." : xhr.statusText || "Upload failed.";
      const error = new Error(uploadErrorText(body, fallback));
      error.status = xhr.status;
      error.body = body;
      reject(error);
    };
    xhr.onerror = () => reject(new Error("Network error during upload. Check your connection and try again."));
    xhr.send(formData);
  });
}

// changes: {title?, category?, caption?} -> the updated Flyer.
export async function updateFlyer(flyerId, changes) {
  return patchJSON(`${FLYERS}/${flyerId}`, changes);
}

// 204. A soft delete: the file moves to the _Deleted folder on the server.
export async function deleteFlyer(flyerId) {
  await request(`${FLYERS}/${flyerId}`, { method: "DELETE" });
}

// 202 {batch_id, sends: [Send]}. The same idempotency key within 24 h returns
// the original batch, so a repeated click never sends twice. 400 = a group
// that isn't an active marketing group; 409 = WhatsApp isn't connected.
export async function sendFlyer(flyerId, groupKeys, idempotencyKey) {
  return postJSON(`${FLYERS}/${flyerId}/send`, { group_keys: groupKeys, idempotency_key: idempotencyKey });
}

// Failed sends only; 202 with the Send (queued again).
export async function retryFlyerSend(flyerId, sendId) {
  return postJSON(`${FLYERS}/${flyerId}/sends/${sendId}/retry`, {});
}

// A flyer picture (thumb_url / file_url) fetched with the session; returns an
// object URL the caller revokes when done.
export async function fetchFlyerObjectUrl(path) {
  const res = await request(path);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// A plain link to a flyer file (opened in a new tab, like an invoice's "View"
// link: the session cookie goes with it on the website).
export function flyerFileHref(path) {
  return `${API_BASE}${path}`;
}

// --- Vendor contacts + Send PO (backend routers/po_send.py) ---------------
// Contacts: anyone with the cart reads them, admins add / edit / delete.

export async function getVendorContacts(vendor) {
  const params = new URLSearchParams();
  if (vendor) params.set("vendor", vendor);
  const qs = params.toString();
  const res = await request(`/api/ordering/vendor-contacts${qs ? `?${qs}` : ""}`);
  return res.json();
}

export function createVendorContact(body) {
  return postJSON("/api/ordering/vendor-contacts", body);
}

export function updateVendorContact(contactId, body) {
  return patchJSON(`/api/ordering/vendor-contacts/${contactId}`, body);
}

export async function deleteVendorContact(contactId) {
  const res = await request(`/api/ordering/vendor-contacts/${contactId}`, { method: "DELETE" });
  return res.json();
}

// What Send PO would email (recipients, subject, body template, PDF name).
export async function getPoSendPreview(supplier) {
  const params = new URLSearchParams({ supplier });
  const res = await request(`/api/ordering/po-send/preview?${params.toString()}`);
  return res.json();
}

// The PDF Send PO would attach, as an object URL (the caller revokes it).
// Not logged as a "pdf" order, unlike Download PDF.
export async function fetchPoSendPdfUrl(supplier) {
  const params = new URLSearchParams({ supplier });
  const res = await request(`/api/ordering/po-send/preview.pdf?${params.toString()}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// Email the PO; on success the order is recorded as placed and its cart
// lines are cleared (fires cart:updated). Errors carry the server's answer on
// error.body ({detail, code, send}).
export async function sendPurchaseOrder(body) {
  try {
    const result = await postJSON("/api/ordering/po-send", body);
    window.dispatchEvent(new Event("cart:updated"));
    return result;
  } catch (err) {
    window.dispatchEvent(new Event("cart:updated"));
    throw err;
  }
}

export async function getPoSends(supplier, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (supplier) params.set("supplier", supplier);
  const res = await request(`/api/ordering/po-sends?${params.toString()}`);
  return res.json();
}

// ---- Insights (docs/INSIGHTS_CONTRACT.md): Toast Analytics reports ----
// Every answer carries {generated_at, stale, source}. Gated on
// `sales_insights`; loss-watch and odd-checks are admin only.

async function getInsight(report, params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  const res = await request(`/api/insights/${report}${query ? `?${query}` : ""}`);
  return res.json();
}

export const getInsightsHeatmap = (weeks) => getInsight("hourly-heatmap", { weeks });
export const getInsightsChannels = (weeks) => getInsight("channels", { weeks });
export const getInsightsCompare = (date) => getInsight("compare", { date });
export const getInsightsCheckTrends = (weeks) => getInsight("check-trends", { weeks });
export const getInsightsLossWatch = (days) => getInsight("loss-watch", { days });
export const getInsightsOddChecks = (days) => getInsight("odd-checks", { days });
export const getInsightsMenuPerformance = (days, group) => getInsight("menu-performance", { days, group });
export const getInsightsAddons = (days) => getInsight("addons", { days });
export const getInsightsPrepForecast = (date) => getInsight("prep-forecast", { date });
export const getInsightsReturningCustomers = (weeks) => getInsight("returning-customers", { weeks });

// Admin: mark a Loss watch alert as looked at.
export async function ackLossWatch(id) {
  return postJSON(`/api/insights/loss-watch/${encodeURIComponent(id)}/ack`, {});
}

// --- Fix Issues category & vendor leads (admin; W4, BATCH_OCT14) ----------

async function putJSONLeads(path, body) {
  const res = await request(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function getIssueLeads() {
  const res = await request("/api/issue-leads");
  return res.json();
}

export async function saveIssueLeads(body) {
  return putJSONLeads("/api/issue-leads", body);
}

export async function acceptIssueLeadProposal() {
  return postJSON("/api/issue-leads/accept-proposal", {});
}

export async function getIssueLeadPreview() {
  const res = await request("/api/issue-leads/preview");
  return res.json();
}

export async function saveIssueLeadSettings(body) {
  return putJSONLeads("/api/issue-leads/settings", body);
}

export async function runIssueLeadTasksNow() {
  return postJSON("/api/issue-leads/run-now", {});
}

// --- Platform (docs/PLATFORM_CONTRACT.md 13) ------------------------------

// Public, no session (sign-up, store lookup): a plain fetch that never
// sends the cookie and never fires the 401 bounce. Errors carry status and
// the JSON body like request()'s.
async function platformPublicRequest(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, { ...options, credentials: "omit" });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // no JSON body
  }
  if (!res.ok) {
    const message =
      (body && (body.message || (typeof body.detail === "string" ? body.detail : null))) || res.statusText;
    const error = new Error(message);
    error.status = res.status;
    error.body = body;
    throw error;
  }
  return body;
}

function putJSON(path, body) {
  return request(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((res) => res.json());
}

// Store lookup (13.4): the M2 directory's JSON for one Store ID; null for an
// unknown one (404) or a server without the route.
export async function lookupStore(code) {
  try {
    return await platformPublicRequest(`/api/public/stores/${encodeURIComponent(code)}`);
  } catch (e) {
    if (e.status === 404 || e.status === 405) return null;
    throw e;
  }
}

// Sign-up (13.3).
export async function submitSignup(body) {
  return platformPublicRequest("/api/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// One-time owner set-up link (<PLATFORM_PUBLIC_URL>/#/setup?store=&token=),
// made by a master instead of an email: sets the owner's password.
export async function redeemOwnerSetup(storeCode, token, password) {
  return platformPublicRequest("/api/signup/owner-setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ store_code: storeCode, token, password }),
  });
}

export async function checkStoreCode(code) {
  return platformPublicRequest(`/api/signup/code-available?code=${encodeURIComponent(code)}`);
}

export async function getSignupStatus(requestId, token) {
  const q = new URLSearchParams({ request_id: String(requestId), token: String(token) });
  return platformPublicRequest(`/api/signup/status?${q.toString()}`);
}

// Master console (13.1): master session only.
const P = "/api/platform";

export async function platformGet(path) {
  const res = await request(`${P}${path}`);
  return res.json();
}

export async function platformPost(path, body = {}) {
  return postJSON(`${P}${path}`, body);
}

export async function platformPut(path, body = {}) {
  return putJSON(`${P}${path}`, body);
}

export const listSignups = (status = "pending") =>
  platformGet(`/signups${status ? `?status=${encodeURIComponent(status)}` : ""}`);
export const approveSignup = (id, body) => platformPost(`/signups/${id}/approve`, body);
export const rejectSignup = (id, note) => platformPost(`/signups/${id}/reject`, { note });
export const listPlatformStores = (status = "") =>
  platformGet(`/stores${status ? `?status=${encodeURIComponent(status)}` : ""}`);
export const getPlatformStore = (code) => platformGet(`/stores/${encodeURIComponent(code)}`);
export const suspendStore = (code, reason) => platformPost(`/stores/${encodeURIComponent(code)}/suspend`, { reason });
export const resumeStore = (code) => platformPost(`/stores/${encodeURIComponent(code)}/resume`, {});
export const retryProvisioning = (code) => platformPost(`/stores/${encodeURIComponent(code)}/retry-provisioning`, {});
export const archiveStore = (code, confirm) => platformPost(`/stores/${encodeURIComponent(code)}/archive`, { confirm });
export const getStoreSettings = (code) => platformGet(`/stores/${encodeURIComponent(code)}/settings`);
export const putStoreSettings = (code, settings) => platformPut(`/stores/${encodeURIComponent(code)}/settings`, settings);
export const requestHosting = (code, body) => platformPost(`/stores/${encodeURIComponent(code)}/hosting-requests`, body);
export const listHostingRequests = (status = "") =>
  platformGet(`/hosting-requests${status ? `?status=${encodeURIComponent(status)}` : ""}`);
export const hostingAction = (id, action, body = {}) => platformPost(`/hosting-requests/${id}/${action}`, body);
export const getAudit = (query) => platformGet(`/audit?${query}`);
export const listPacks = () => platformGet("/packs");
export const getPlatformConfig = () => platformGet("/config");
export const startTotpSetup = () => platformPost("/users/me/totp/setup", {});
export const enableTotp = (pending, code) => platformPost("/users/me/totp/enable", { pending, code });

// Settings -> Integrations (13.5): store admin.
const I = "/api/settings/integrations";

export async function listIntegrations() {
  const res = await request(I);
  return res.json();
}

export const saveIntegration = (kind, body) => putJSON(`${I}/${encodeURIComponent(kind)}`, body);

// `body` = unsaved values ({public, secrets}) to test before saving, or
// nothing to test what's saved.
export const testIntegration = (kind, body) => postJSON(`${I}/${encodeURIComponent(kind)}/test`, body || {});

export async function deleteIntegration(kind) {
  const res = await request(`${I}/${encodeURIComponent(kind)}`, { method: "DELETE" });
  try {
    return await res.json();
  } catch {
    return null;
  }
}

export const connectClover = () => postJSON(`${I}/clover/connect`, {});

// Shared base helpers for the per-track API modules in src/clients/
// (docs/WEBSITE_READY_CONTRACT.md 3.6): new endpoints get their client
// functions there, not in this file.
export { request, postJSON, patchJSON };
