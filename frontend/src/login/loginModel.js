// The single login page (docs/PLATFORM_CONTRACT.md sections 4.1, 4.5, 12.3,
// 14): Store ID + Username + Password for everyone. Pure helpers - no React,
// no DOM, no fetch - tested by loginModel.test.mjs.

// localStorage key for the last Store ID used on this browser (contract 14).
export const LAST_STORE_KEY = "storeops_last_store";

// Store ID that signs a master (platform admin) in (setting
// PLATFORM_LOGIN_CODE; this is its default).
export const PLATFORM_LOGIN_CODE = "PLATFORM";

const CODE_PATTERN = /^[A-Z0-9][A-Z0-9_]{2,23}$/;

// Trim, upper-case, "-" -> "_" (the server does the same).
export function normalizeStoreCode(raw) {
  return String(raw ?? "")
    .trim()
    .toUpperCase()
    .replace(/-/g, "_");
}

// The normalised code, or null when it can't be a Store ID.
export function validStoreCode(raw) {
  const code = normalizeStoreCode(raw);
  return CODE_PATTERN.test(code) ? code : null;
}

export function isPlatformCode(code) {
  return normalizeStoreCode(code) === PLATFORM_LOGIN_CODE;
}

// Remembered Store ID (try/catch: private mode, storage off). `storage` is
// injectable for tests; the page passes window.localStorage.
export function readLastStore(storage) {
  try {
    const v = storage?.getItem(LAST_STORE_KEY);
    return v ? validStoreCode(v) || "" : "";
  } catch {
    return "";
  }
}

export function rememberStore(storage, code) {
  try {
    const c = validStoreCode(code);
    if (c) storage?.setItem(LAST_STORE_KEY, c);
  } catch {
    // not remembered - the field is just empty next time
  }
}

// Which Store ID the field starts with: `?store=CODE` from the link, then
// the one remembered on this browser, then the store this server says it
// is (a single-store server's branding), else empty.
export function initialStoreId({ queryStore, remembered, brandingCode } = {}) {
  return validStoreCode(queryStore || "") || validStoreCode(remembered || "") || validStoreCode(brandingCode || "") || "";
}

// An error body from the server: new routes answer {"error","message"},
// FastAPI HTTPException answers {"detail": "..."} or {"detail": {...}}.
export function errorInfo(body) {
  if (!body || typeof body !== "object") return { code: null, message: null, data: {} };
  const detail = body.detail;
  const data = detail && typeof detail === "object" && !Array.isArray(detail) ? { ...body, ...detail } : body;
  const code = typeof data.error === "string" ? data.error : null;
  const message =
    typeof data.message === "string" && data.message
      ? data.message
      : typeof detail === "string" && detail
        ? detail
        : null;
  return { code, message, data };
}

export const LOGIN_MESSAGES = {
  incorrect: "Incorrect store ID, username or password",
  suspended: "This store is paused. Please contact the store owner.",
  notReady: "This store isn't ready yet. You'll be able to sign in once it has been set up.",
  moved: "This store moved to its own server. Taking you there…",
  unreachable: "Can't reach the server. Check your connection and try again.",
  generic: "Couldn't sign in. Please try again in a moment.",
  totp: "Enter the 6-digit code from your authenticator app.",
};

// The two-step sign-in code as typed: digits only, at most 6.
export function cleanTotp(raw) {
  return String(raw ?? "").replace(/\D/g, "").slice(0, 6);
}

function waitText(seconds) {
  const s = Math.max(1, Math.round(Number(seconds) || 0));
  if (s < 90) return `${s} seconds`;
  const m = Math.ceil(s / 60);
  return m === 1 ? "1 minute" : `${m} minutes`;
}

// A failed login -> { kind, message, retryAfter?, apiBaseUrl? }.
// `err` is api.js's Error (status, body) or a network TypeError.
export function loginErrorMessage(err) {
  const status = err?.status;
  if (!status) return { kind: "unreachable", message: LOGIN_MESSAGES.unreachable };
  const { code, message, data } = errorInfo(err.body);
  // A master with two-step sign-in: the password was right, ask for the code.
  if (status === 401 && code === "totp_required") return { kind: "totp", message: message || LOGIN_MESSAGES.totp };
  if (status === 401) return { kind: "incorrect", message: message || LOGIN_MESSAGES.incorrect };
  if (status === 429) {
    const retryAfter = Number(data.retry_after) || 0;
    return {
      kind: "locked",
      retryAfter,
      message: retryAfter
        ? `Too many sign-in tries. Wait ${waitText(retryAfter)} and try again.`
        : "Too many sign-in tries. Wait a few minutes and try again.",
    };
  }
  if (status === 403) {
    if (code === "store_suspended") return { kind: "suspended", message: message || LOGIN_MESSAGES.suspended };
    if (code === "store_not_ready") return { kind: "not_ready", message: message || LOGIN_MESSAGES.notReady };
    return { kind: "forbidden", message: message || LOGIN_MESSAGES.generic };
  }
  if (status === 409 && code === "store_moved") {
    return { kind: "moved", message: LOGIN_MESSAGES.moved, apiBaseUrl: data.api_base_url || null };
  }
  if (status === 409 && code === "store_mismatch") {
    return { kind: "mismatch", message: "You're signed in to another store in this browser. Sign out first." };
  }
  return { kind: "other", message: message || LOGIN_MESSAGES.generic };
}

// Where a `store_moved` reply sends the browser (contract 12.3):
// `<api_base_url>/#/login?store=<CODE>`. Only an https origin (http only for
// localhost / 127.0.0.1 in development) with no path, query or credentials;
// null otherwise (the page then just shows the message).
export function movedLoginUrl(apiBaseUrl, code) {
  const c = validStoreCode(code);
  if (!apiBaseUrl || !c) return null;
  let u;
  try {
    u = new URL(String(apiBaseUrl).trim());
  } catch {
    return null;
  }
  const local = u.hostname === "localhost" || u.hostname === "127.0.0.1";
  if (u.protocol !== "https:" && !(u.protocol === "http:" && local)) return null;
  if (u.username || u.password || u.search || u.hash) return null;
  if (u.pathname && u.pathname !== "/") return null;
  return `${u.origin}/#/login?store=${encodeURIComponent(c)}`;
}

// The signed-in user from /api/auth/me or a login reply: a platform session
// ({"scope":"platform","user":{...}}) is flattened so the shell can read
// `user.scope`, `user.username` either way. Store sessions are unchanged
// (plus scope "store" when an older server leaves it out).
export function normalizeMe(me) {
  if (!me || typeof me !== "object") return null;
  if (me.scope === "platform") {
    const u = me.user && typeof me.user === "object" ? me.user : {};
    return {
      scope: "platform",
      username: u.username || "",
      display_name: u.display_name || u.username || "",
      role: u.role || "master",
      totp_enrolled: !!u.totp_enrolled,
      is_admin: false,
      allowed_features: [],
    };
  }
  return { ...me, scope: me.scope || "store" };
}

export function isPlatformUser(user) {
  return !!user && user.scope === "platform";
}

// "/c/XYZ_GROCERY/kitchen-4821" -> "XYZ_GROCERY": a shared (multi-store)
// server's public checklist links carry the Store ID before the token
// (contract 4.4). A single-store server's "/c/kitchen-4821" has none (null).
export function storeFromPath(pathname) {
  const parts = String(pathname || "")
    .replace(/\/+$/, "")
    .split("/")
    .filter(Boolean);
  if (parts.length !== 3 || parts[0] !== "c") return null;
  return validStoreCode(parts[1]);
}
