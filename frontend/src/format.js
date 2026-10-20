// Times and "today" are always shown in the STORE's own time zone, whatever
// the viewer's device is set to (checking in from out of town). The zone
// comes from GET /api/app/branding (`timezone`, the store's STORE_TIMEZONE
// setting); StoreContext calls setStoreTimeZone() once at boot, before any
// page renders. `timeZoneName: "short"` gives the right standard/daylight
// label for the date in question.
//
// Note: combining dateStyle/timeStyle with timeZoneName isn't supported in
// all Intl implementations, so this spells out the fields explicitly instead.

// Only used until branding answers (and by old servers without it): the
// server's own default (TOAST_TIMEZONE), so nothing shifts when it is unset.
export const DEFAULT_STORE_TIME_ZONE = "America/New_York";

// The US zones a new store most often picks, offered first on the sign-up
// form (signup/signupModel.js timezoneOptions); the rest come from the
// browser's own list.
export const COMMON_US_TIME_ZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
];

let storeTimeZone = DEFAULT_STORE_TIME_ZONE;
const formatterCache = new Map();

export function isValidTimeZone(tz) {
  if (!tz || typeof tz !== "string") return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

// Set the store's IANA zone (e.g. "America/Chicago"). An empty or unknown
// zone is ignored (keeps the previous one). Returns the zone in force.
export function setStoreTimeZone(tz) {
  if (isValidTimeZone(tz) && tz !== storeTimeZone) {
    storeTimeZone = tz;
    formatterCache.clear();
  }
  return storeTimeZone;
}

export function getStoreTimeZone() {
  return storeTimeZone;
}

// The zone as people say it: "America/New_York" -> "New York",
// "America/Indiana/Indianapolis" -> "Indianapolis". For "store time (...)".
export function storeZoneCity() {
  const last = storeTimeZone.split("/").pop() || storeTimeZone;
  return last.replace(/_/g, " ");
}

// A cached Intl.DateTimeFormat in the store's zone. `options` must not set
// timeZone (it is always the store's). Callers keep their own locale.
export function storeDateTimeFormat(locale, options = {}) {
  const key = `${storeTimeZone}|${locale || ""}|${JSON.stringify(options)}`;
  let f = formatterCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { ...options, timeZone: storeTimeZone });
    formatterCache.set(key, f);
  }
  return f;
}

// A formatter-like object for module-level constants: it reads the store's
// zone on every call, so a constant made before branding loads (or before a
// store switch) still formats in the zone in force.
export function lazyStoreFormat(locale, options = {}) {
  return {
    format: (d) => storeDateTimeFormat(locale, options).format(d),
    formatToParts: (d) => storeDateTimeFormat(locale, options).formatToParts(d),
  };
}

// `{ ...opts, timeZone: <store zone> }` for toLocaleDateString / toLocaleTimeString.
export function inStoreZone(options = {}) {
  return { ...options, timeZone: storeTimeZone };
}

const FULL_TIME = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
};

export function formatRestaurantTime(isoString) {
  if (!isoString) return null;
  return storeDateTimeFormat("en-US", FULL_TIME).format(new Date(isoString));
}

// Store-local calendar dates ("YYYY-MM-DD" in the store's zone). A plain
// `new Date().toISOString().slice(0, 10)` is the UTC date, which is already
// tomorrow in the evening in US zones - so "today" defaults went a day ahead.
const DATE_PARTS = { year: "numeric", month: "2-digit", day: "2-digit" };

// The store-local calendar date of a moment (Date, ISO string or ms).
export function storeDateStr(moment) {
  return storeDateTimeFormat("en-CA", DATE_PARTS).format(moment instanceof Date ? moment : new Date(moment));
}

export function storeTodayStr() {
  return storeDateStr(new Date());
}

// The one "today" helper (website ready A1): the STORE's calendar date as
// "YYYY-MM-DD", in the zone /api/auth/me reports (store_timezone; branding's
// timezone before sign-in). `now` is for tests. Never `new Date()` for a date.
export function storeToday(now = new Date()) {
  return storeDateStr(now);
}

// The store-local date `days` days from today (negative = in the past).
export function storeDateOffsetStr(days) {
  const d = new Date(`${storeTodayStr()}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// "2026-10-18" -> "Oct 18, 2026": a store calendar day (no time, no zone
// shift) shown the way the rest of the site writes dates. Anything that
// isn't YYYY-MM-DD comes back unchanged.
export function formatDayLabel(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day || ""))) return day;
  const d = new Date(`${day}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}
