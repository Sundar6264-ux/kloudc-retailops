// Shared helpers for the Team hub task pages. All task times are shown in
// store time (America/New_York), whatever the viewer's own clock says - the
// same rule the backend uses for due times and recurring schedules.

export const STORE_TZ = "America/New_York";

const dayKey = new Intl.DateTimeFormat("en-CA", {
  timeZone: STORE_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

// "2026-09-28" for an instant, in store time.
export function storeDay(date) {
  return dayKey.format(date);
}

export function todayStoreDay() {
  return storeDay(new Date());
}

const timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, hour: "numeric", minute: "2-digit" });
const dateTimeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: STORE_TZ,
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

// "3:00 PM" for today, "Tue, Sep 29, 3:00 PM" otherwise.
export function formatDue(iso) {
  if (!iso) return "No due time";
  const d = new Date(iso);
  return storeDay(d) === todayStoreDay() ? timeFmt.format(d) : dateTimeFmt.format(d);
}

export function formatWhen(iso) {
  return iso ? dateTimeFmt.format(new Date(iso)) : "";
}

// A `<input type="datetime-local">` value is store wall time here; the
// backend reads an offset-less due_at as New York time, so it's sent as is.
export function dueInputToApi(value) {
  if (!value) return null;
  return value.length === 16 ? `${value}:00` : value;
}

// Splits open tasks into the list's sections.
export function sectionTasks(tasks) {
  const today = todayStoreDay();
  const overdue = [];
  const todayList = [];
  const upcoming = [];
  for (const t of tasks) {
    if (t.is_overdue) overdue.push(t);
    else if (t.due_at && storeDay(new Date(t.due_at)) === today) todayList.push(t);
    else upcoming.push(t);
  }
  return { overdue, today: todayList, upcoming };
}

export const REQUIRES_LABELS = { none: "Nothing", photo: "Photo", note: "Note" };

export const STATUS_PILL = {
  open: "pill-info",
  done: "pill-success",
  cant_do: "pill-warn",
  cancelled: "pill-neutral",
};

export const STATUS_LABELS = { open: "Open", done: "Done", cant_do: "Can't do", cancelled: "Cancelled" };

export const EVENT_LABELS = {
  created: "created the task",
  reassigned: "reassigned it",
  done: "completed it",
  completed: "completed it",
  reopened: "reopened it",
  cant_do: "marked it can't do",
  commented: "commented",
  cancelled: "cancelled it",
  edited: "edited it",
  updated: "edited it",
  item_done: "ticked a sub-item",
  started: "started it",
  unstarted: "moved it back to To do",
  moderated: "removed its content",
  item_undone: "unticked a sub-item",
  items_edited: "edited the steps",
};

// The few words after an event's label in the History list ("ticked a
// sub-item: Wipe", "edited it - due Tue, Oct 15, 9:30 AM"), or "".
export function eventDetailText(ev) {
  const d = (ev && ev.detail) || {};
  switch (ev && ev.kind) {
    case "commented":
      return d.body ? `: ${d.body}` : "";
    case "cant_do":
      return d.reason ? `: ${d.reason}` : "";
    case "item_done":
    case "item_undone":
      return d.text ? `: ${d.text}` : "";
    case "reassigned":
      return d.to ? ` to ${d.to}` : "";
    case "edited":
      if (d.due) return d.due.to ? ` - due ${formatDue(d.due.to)}` : " - no due time";
      return "";
    case "items_edited": {
      const bits = [];
      if (d.added && d.added.length) bits.push(`${d.added.length} added`);
      if (d.renamed && d.renamed.length) bits.push(`${d.renamed.length} renamed`);
      if (d.removed && d.removed.length) bits.push(`${d.removed.length} removed`);
      return bits.length ? ` (${bits.join(", ")})` : "";
    }
    case "done":
    case "cancelled":
      return d.bulk ? " (several at once)" : "";
    default:
      return "";
  }
}

// --- Repeat rules ----------------------------------------------------------
// The friendly repeat builder edits this shape; buildRrule turns it into the
// RFC 5545 subset the backend accepts (FREQ, BYDAY, BYMONTHDAY, BYHOUR,
// BYMINUTE), and parseRrule reads a saved rule back into it.

export const WEEKDAYS = [
  { code: "MO", label: "Mon" },
  { code: "TU", label: "Tue" },
  { code: "WE", label: "Wed" },
  { code: "TH", label: "Thu" },
  { code: "FR", label: "Fri" },
  { code: "SA", label: "Sat" },
  { code: "SU", label: "Sun" },
];

const WORK_WEEK = ["MO", "TU", "WE", "TH", "FR"];

export const DEFAULT_REPEAT = { kind: "weekly", days: ["MO"], monthDay: 1, time: "15:00" };

export function buildRrule(repeat) {
  // A rule set up elsewhere with options this builder can't show (every 2nd
  // week, several times a day...) is kept exactly as it was until the admin
  // picks a new pattern.
  if (repeat.custom && repeat.raw) return repeat.raw;
  const [h, m] = (repeat.time || "00:00").split(":").map((x) => parseInt(x, 10) || 0);
  const at = `BYHOUR=${h};BYMINUTE=${m}`;
  switch (repeat.kind) {
    case "daily":
      return `FREQ=DAILY;${at}`;
    case "weekdays":
      return `FREQ=WEEKLY;BYDAY=${WORK_WEEK.join(",")};${at}`;
    case "monthly":
      return `FREQ=MONTHLY;BYMONTHDAY=${repeat.monthDay || 1};${at}`;
    default: {
      const days = WEEKDAYS.map((d) => d.code).filter((c) => (repeat.days || []).includes(c));
      return `FREQ=WEEKLY;BYDAY=${(days.length ? days : ["MO"]).join(",")};${at}`;
    }
  }
}

// The rule parts the builder understands; anything else (INTERVAL=2,
// several BYHOURs, BYSETPOS...) makes the rule "custom".
const KNOWN_PARTS = new Set(["FREQ", "BYDAY", "BYMONTHDAY", "BYHOUR", "BYMINUTE", "INTERVAL"]);

function isCustom(parts) {
  if (Object.keys(parts).some((k) => !KNOWN_PARTS.has(k))) return true;
  if (parts.INTERVAL && parts.INTERVAL !== "1") return true;
  if ((parts.BYHOUR || "").includes(",") || (parts.BYMINUTE || "").includes(",")) return true;
  if ((parts.BYMONTHDAY || "").includes(",")) return true;
  if (parts.FREQ === "MONTHLY" && parts.BYDAY) return true;
  if (parts.FREQ === "DAILY" && (parts.BYDAY || parts.BYMONTHDAY)) return true;
  if (parts.FREQ === "WEEKLY" && parts.BYMONTHDAY) return true;
  return !["DAILY", "WEEKLY", "MONTHLY"].includes(parts.FREQ);
}

export function parseRrule(rule) {
  const parts = Object.fromEntries(
    (rule || "")
      .replace(/^RRULE:/i, "")
      .split(";")
      .filter(Boolean)
      .map((p) => p.split("=").map((x) => x.trim().toUpperCase())),
  );
  const custom = !!rule && isCustom(parts);
  const hour = parseInt((parts.BYHOUR || "0").split(",")[0], 10) || 0;
  const minute = parseInt((parts.BYMINUTE || "0").split(",")[0], 10) || 0;
  const time = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  const days = parts.BYDAY ? parts.BYDAY.split(",") : [];
  const extra = custom ? { custom: true, raw: rule } : {};
  if (parts.FREQ === "DAILY") return { ...DEFAULT_REPEAT, kind: "daily", time, ...extra };
  if (parts.FREQ === "MONTHLY") {
    const monthDay = parseInt(parts.BYMONTHDAY || "1", 10) || 1;
    return { ...DEFAULT_REPEAT, kind: "monthly", monthDay, time, ...extra };
  }
  if (days.length === WORK_WEEK.length && WORK_WEEK.every((d) => days.includes(d))) {
    return { ...DEFAULT_REPEAT, kind: "weekdays", days, time, ...extra };
  }
  return { ...DEFAULT_REPEAT, kind: "weekly", days: days.length ? days : ["MO"], time, ...extra };
}

function timeLabel(time) {
  const [h, m] = time.split(":").map((x) => parseInt(x, 10) || 0);
  const suffix = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// "Every Mon, Thu at 3:00 PM" - for lists and the builder's summary line.
export function describeRepeat(rule) {
  const r = parseRrule(rule);
  if (r.custom) return `Custom schedule (${rule})`;
  const at = `at ${timeLabel(r.time)}`;
  if (r.kind === "daily") return `Every day ${at}`;
  if (r.kind === "weekdays") return `Every weekday ${at}`;
  if (r.kind === "monthly") {
    return r.monthDay === -1 ? `Monthly on the last day ${at}` : `Monthly on the ${ordinal(r.monthDay)} ${at}`;
  }
  const labels = WEEKDAYS.filter((d) => r.days.includes(d.code)).map((d) => d.label);
  return `Every ${labels.join(", ")} ${at}`;
}

// 420 -> "7 hours", 90 -> "1 h 30 min".
export function describeAhead(minutes) {
  const m = minutes || 0;
  if (m === 0) return "at the due time";
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h && rest) return `${h} h ${rest} min before`;
  if (h) return `${h} hour${h === 1 ? "" : "s"} before`;
  return `${rest} min before`;
}
