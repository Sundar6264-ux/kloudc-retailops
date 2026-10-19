// "Create your store" sign-up (docs/PLATFORM_CONTRACT.md 3.2, 13.3, 14):
// the request form's checks, the request body, and the pending page's
// status. Pure helpers - tested by signupModel.test.mjs. The server checks
// everything again; these only save a round trip.
import { COMMON_US_TIME_ZONES } from "../format.js";
import { normalizeStoreCode, validStoreCode } from "../login/loginModel.js";

// Contract 4.1 (plus the server's PLATFORM_LOGIN_CODE, default PLATFORM).
export const RESERVED_CODES = new Set([
  "PLATFORM",
  "MASTER",
  "ADMIN",
  "ROOT",
  "SYSTEM",
  "API",
  "APP",
  "WWW",
  "STATIC",
  "DIRECTORY",
  "SUPPORT",
  "HELP",
  "STOREOPS",
  "RETAILOPS",
  "KLOUDC",
  "TEST",
  "NULL",
  "NONE",
]);

export const POS_OPTIONS = [
  { value: "toast", label: "Toast" },
  { value: "clover", label: "Clover" },
  { value: "csv", label: "Other / spreadsheet upload" },
];

export const MIN_PASSWORD = 10;

// The time zones offered first (the rest come from the browser's list).
export const COMMON_TIMEZONES = COMMON_US_TIME_ZONES;

export const EMPTY_SIGNUP = {
  store_name: "",
  requested_code: "",
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  address: "",
  timezone: "",
  pos_provider: "toast",
  owner_username: "",
  owner_password: "",
  owner_password_confirm: "",
  notes: "",
  accept_terms: false,
  website: "", // honeypot: hidden from people, must stay empty
};

// The Store ID a store name suggests ("Main Street Market" -> MAIN_STREET_MARKET).
export function suggestCode(storeName) {
  const code = String(storeName ?? "")
    .toUpperCase()
    .replace(/&/g, " AND ")
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 24)
    .replace(/_+$/g, "");
  return validStoreCode(code) || "";
}

// Why a Store ID can't be requested (null = fine as far as the page knows).
export function codeProblem(raw) {
  const code = normalizeStoreCode(raw);
  if (!code) return "Choose a Store ID.";
  if (code.length < 3) return "At least 3 letters or digits.";
  if (code.length > 24) return "At most 24 characters.";
  if (!validStoreCode(code)) return "Letters, digits and _ only, starting with a letter or digit.";
  if (RESERVED_CODES.has(code)) return "That Store ID is reserved. Choose another.";
  return null;
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const USERNAME = /^[A-Za-z0-9._-]{3,64}$/;

// Field -> message for everything the person must fix (empty = ready).
// GET /api/signup/code-available -> {code, available, reason?, message?}:
// the server's `reason` is a code (taken / reserved / requested / invalid),
// shown in the site's "Store ID" words; an unknown one shows its message.
export const CODE_REASON_TEXT = {
  taken: "That Store ID is taken.",
  reserved: "That Store ID is reserved.",
  requested: "Someone has already asked for that Store ID.",
  invalid: "Store IDs are 3 to 24 letters, numbers or underscores.",
};

export function availabilityReason(reply) {
  if (!reply || reply.available) return null;
  const code = typeof reply.reason === "string" ? reply.reason : "";
  if (CODE_REASON_TEXT[code]) return CODE_REASON_TEXT[code];
  if (typeof reply.message === "string" && reply.message) return reply.message;
  // An older server (or the mock) sent the sentence itself as `reason`.
  return code && /\s/.test(code) ? code : null;
}

// `codeAvailability` is the live check's answer ({available, reason}) or null.
export function validateSignup(form, { codeAvailability = null } = {}) {
  const f = { ...EMPTY_SIGNUP, ...(form || {}) };
  const errors = {};
  const name = f.store_name.trim();
  if (name.length < 2) errors.store_name = "Enter the store's name.";
  else if (name.length > 80) errors.store_name = "At most 80 characters.";
  const codeErr = codeProblem(f.requested_code);
  if (codeErr) errors.requested_code = codeErr;
  else if (codeAvailability && codeAvailability.available === false) {
    errors.requested_code = codeAvailability.reason || "That Store ID is taken. Choose another.";
  }
  if (!f.contact_name.trim()) errors.contact_name = "Enter your name.";
  if (!EMAIL.test(f.contact_email.trim())) errors.contact_email = "Enter a valid email address.";
  if (!f.timezone) errors.timezone = "Choose the store's time zone.";
  if (!POS_OPTIONS.some((p) => p.value === f.pos_provider)) errors.pos_provider = "Choose the store's POS.";
  if (!USERNAME.test(f.owner_username.trim())) {
    errors.owner_username = "3-64 letters, digits, dot, dash or underscore.";
  }
  const pwErr = passwordProblem(f.owner_password, f.owner_username);
  if (pwErr) errors.owner_password = pwErr;
  else if (f.owner_password !== f.owner_password_confirm) errors.owner_password_confirm = "The passwords don't match.";
  if (f.notes.length > 2000) errors.notes = "At most 2000 characters.";
  if (!f.accept_terms) errors.accept_terms = "Please accept the terms to continue.";
  return errors;
}

export function passwordProblem(password, username = "") {
  const pw = String(password ?? "");
  if (pw.length < MIN_PASSWORD) return `At least ${MIN_PASSWORD} characters.`;
  if (username && pw.toLowerCase() === String(username).trim().toLowerCase()) {
    return "The password can't be the username.";
  }
  return null;
}

// What's sent to POST /api/signup (contract 13.3). Blank optional fields are
// left out; the confirmation never leaves the page.
export function signupBody(form) {
  const f = { ...EMPTY_SIGNUP, ...(form || {}) };
  const body = {
    store_name: f.store_name.trim(),
    requested_code: normalizeStoreCode(f.requested_code),
    contact_name: f.contact_name.trim(),
    contact_email: f.contact_email.trim().toLowerCase(),
    timezone: f.timezone,
    pos_provider: f.pos_provider,
    owner_username: f.owner_username.trim(),
    owner_password: f.owner_password,
    accept_terms: !!f.accept_terms,
    website: f.website || "",
  };
  for (const k of ["contact_phone", "address", "notes"]) {
    const v = String(f[k] ?? "").trim();
    if (v) body[k] = v;
  }
  return body;
}

// The browser's time zone list, the common US ones first (no duplicates).
export function timezoneOptions(all) {
  const list = Array.isArray(all) && all.length ? all : COMMON_TIMEZONES;
  const rest = list.filter((z) => !COMMON_TIMEZONES.includes(z));
  return [...COMMON_TIMEZONES, ...rest];
}

// --- the pending page ---------------------------------------------------

export const PENDING_POLL_MS = 30_000;
export const PENDING_POLL_MAX_MS = 60 * 60 * 1000;
export const SIGNUP_STORAGE_KEY = "retailops_signup_request";

export function pendingUrl(requestId, token) {
  return `/signup/pending?id=${encodeURIComponent(requestId)}&t=${encodeURIComponent(token)}`;
}

// GET /api/signup/status -> what the page shows.
export function pendingView(status) {
  const s = status && typeof status === "object" ? status : {};
  const code = typeof s.store_code === "string" ? s.store_code : null;
  switch (s.status) {
    case "approved":
      return {
        state: "approved",
        title: "Your store is approved",
        text: code ? `Sign in with Store ID ${code} and the username and password you chose.` : "You can sign in now.",
        storeCode: code,
        done: true,
      };
    case "rejected":
      return {
        state: "rejected",
        title: "Your request was not approved",
        text: s.message || "Contact support if you have questions.",
        storeCode: null,
        done: true,
      };
    case "withdrawn":
      return { state: "withdrawn", title: "This request was withdrawn", text: "", storeCode: null, done: true };
    case "pending":
      return {
        state: "pending",
        title: "Waiting for approval",
        text: "We're reviewing your request. This page checks again every 30 seconds - you can also come back later.",
        storeCode: null,
        done: false,
      };
    default:
      return { state: "unknown", title: "Request not found", text: "Check the link, or sign up again.", storeCode: null, done: true };
  }
}

// Whether the pending page should check again (visible, not finished, not
// past an hour since it opened).
export function shouldPoll({ done, visible, startedAt, now }) {
  return !done && !!visible && now - startedAt < PENDING_POLL_MAX_MS;
}

// One-time owner set-up link (/#/setup?store=&token=, made by a master):
// what's wrong with the typed passwords, or null. The username is the one
// the link was made for (the server answers with it).
export function ownerSetupProblem(password, confirm) {
  const p = passwordProblem(password);
  if (p) return p;
  if (String(password) !== String(confirm ?? "")) return "The passwords don't match.";
  if (String(password).length > 256) return "At most 256 characters.";
  return null;
}

// The link's own values, or null when one is missing / malformed.
export function ownerSetupParams(store, token) {
  const s = String(store ?? "").trim().toUpperCase().replace(/-/g, "_");
  const t = String(token ?? "").trim();
  if (!/^[A-Z0-9][A-Z0-9_]{2,23}$/.test(s) || t.length < 16 || t.length > 200) return null;
  return { store: s, token: t };
}
