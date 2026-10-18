// Pure helpers for the Vendor Calendar's "Suggested schedules" and "Vendors
// without a schedule" (X2, Oct 14). No React, no fetch - tested with
// `node --test src/components/suggestions/scheduleSuggestionsModel.test.mjs`.
import { WEEKDAY_NAMES, plural, weekdayList } from "./suggestionsModel.js";

export const CONFIDENCE_LABELS = { high: "High", medium: "Medium", low: "Low" };
const CONFIDENCE_PILLS = { high: "pill-success", medium: "pill-warn", low: "pill-neutral" };

// How many "vendors without a schedule" show before "Show all".
export const MISSING_PREVIEW = 8;

export function confidenceLabel(level) {
  return CONFIDENCE_LABELS[level] || "Low";
}

export function confidencePill(level) {
  return CONFIDENCE_PILLS[level] || "pill-neutral";
}

// "Delivered Mon x4, Tue x4 in the last 12 weeks" - the server's own words,
// rebuilt from the counts when an older server didn't send them.
export function evidenceSummary(s) {
  const ev = s?.evidence || {};
  if (ev.summary) return ev.summary;
  const counts = ev.weekday_counts || {};
  const parts = WEEKDAY_NAMES.filter((d) => counts[d]).map((d) => `${d} x${counts[d]}`);
  if (!parts.length) return "";
  const weeks = ev.weeks || 8;
  return `Delivered ${parts.join(", ")} in the last ${weeks} weeks`;
}

// "Order 2 days before (Tue for Thu), from 4 placed orders." / the owner sets it.
export function orderDayText(s) {
  if (!s) return "";
  const lead = Number.isInteger(s.lead_days) ? s.lead_days : 1;
  if (s.lead_days_source !== "placed_orders") {
    return `Order day: yours to set - no placed orders on record (${plural(lead, "day")} before until you change it).`;
  }
  const pairs = (s.delivery_weekdays || [])
    .filter((d) => Number.isInteger(d) && d >= 0 && d <= 6)
    .map((d) => `${WEEKDAY_NAMES[(((d - lead) % 7) + 7) % 7]} for ${WEEKDAY_NAMES[d]}`);
  const when = lead === 0 ? "Order the same day" : `Order ${plural(lead, "day")} before`;
  const from = s.lead_samples ? `, from ${plural(s.lead_samples, "placed order")}` : ", from placed orders";
  return `${when}${pairs.length && lead > 0 ? ` (${pairs.join(", ")})` : ""}${from}.`;
}

// The order the dialog lists: as the server sorted (confidence first).
export function addAllRows(suggestions) {
  return (suggestions || []).filter((s) => s && s.key && !s.dismissed);
}

// Add all starts with High and Medium ticked (the server's add_all_default;
// it also leaves out vendors that deliver every 2-4 weeks or rarer, since a
// schedule is weekly). Low ones stay listed, unticked. Older servers: by
// confidence alone.
export function tickedByDefault(s) {
  if (typeof s?.add_all_default === "boolean") return s.add_all_default;
  return s?.confidence === "high" || s?.confidence === "medium";
}

// The keys that start unticked in the Add all dialog.
export function defaultUnticked(suggestions) {
  return new Set(addAllRows(suggestions).filter((s) => !tickedByDefault(s)).map((s) => s.key));
}

// "Delivers every 2-3 weeks, not every week - ... Weekly isn't right for this
// vendor." (the server's words), or "" for a weekly vendor / older server.
export function cadenceWarning(s) {
  return s?.irregular && s?.cadence_text ? s.cadence_text : "";
}

// The short form for the Add all list: "Delivers every 1-2 weeks - weekly
// isn't right" (cadence_label from the server), or "".
export function cadenceShort(s) {
  if (!s?.irregular) return "";
  return s.cadence_label ? `Delivers ${s.cadence_label} - weekly isn't right` : "Delivers every few weeks - weekly isn't right";
}

// The note above the Add all list about what starts unticked: Low ones, and
// High / Medium ones that deliver only every few weeks.
export function defaultsNote(suggestions) {
  const off = addAllRows(suggestions).filter((s) => !tickedByDefault(s));
  const low = off.filter((s) => s.confidence === "low").length;
  const irregular = off.filter((s) => s.confidence !== "low" && s.irregular).length;
  const parts = [];
  if (low) parts.push(`${plural(low, "Low-confidence suggestion")} ${low === 1 ? "starts" : "start"} unticked.`);
  if (irregular) {
    const one = irregular === 1;
    parts.push(
      `${plural(irregular, "High / Medium vendor")} ${one ? "delivers" : "deliver"} only every few weeks - a schedule repeats every week, so ${one ? "it starts" : "they start"} unticked too.`,
    );
  }
  return parts.length ? `${parts.join(" ")} Tick any you still want.` : "";
}

// Keys to send: every listed suggestion the person left ticked.
export function addAllKeys(suggestions, unticked) {
  const off = unticked instanceof Set ? unticked : new Set(unticked || []);
  return addAllRows(suggestions)
    .map((s) => s.key)
    .filter((k) => !off.has(k));
}

// The message after Add all.
export function addAllResultText(result) {
  const created = result?.created || [];
  const skipped = result?.skipped || [];
  const parts = [];
  if (created.length) parts.push(`Added ${plural(created.length, "schedule")}: ${created.map((c) => c.vendor).join(", ")}.`);
  else parts.push("No schedules added.");
  if (skipped.length) {
    const why = skipped.map((s) => `${s.vendor} (${s.why === "has_schedule" ? "already has a schedule" : "no longer suggested"})`);
    parts.push(`Skipped ${skipped.length}: ${why.join(", ")}.`);
  }
  return parts.join(" ");
}

// Vendors without a schedule, shortened unless expanded.
export function visibleMissing(rows, expanded, limit = MISSING_PREVIEW) {
  const list = rows || [];
  return expanded || list.length <= limit ? list : list.slice(0, limit);
}

// "3 seen" / "None seen" chip next to a vendor without a schedule.
export function deliveriesSeenText(row) {
  const n = row?.deliveries_seen || 0;
  return n ? `${plural(n, "delivery", "deliveries")} seen` : "No deliveries seen";
}

// Weekdays to start a "Set schedule" form with: a dismissed suggestion's days,
// else the weekday(s) seen most (only when one stands out), else none.
export function initialWeekdays(row) {
  if (row?.suggested_weekdays?.length) return [...row.suggested_weekdays];
  const counts = row?.weekday_counts || {};
  const entries = WEEKDAY_NAMES.map((d, i) => [i, counts[d] || 0]).filter(([, n]) => n > 0);
  if (!entries.length) return [];
  const top = Math.max(...entries.map(([, n]) => n));
  const best = entries.filter(([, n]) => n === top).map(([i]) => i);
  return top >= 2 && best.length === 1 ? best : [];
}

export function scheduleSummary(weekdays, lead) {
  return `delivers ${weekdayList(weekdays)}, order ${plural(lead, "day")} before`;
}

// Headline counts under the section title.
export function countsLine(counts) {
  if (!counts) return "";
  const parts = [];
  if (counts.scheduled != null) parts.push(`${counts.scheduled} with a schedule`);
  if (counts.suggested != null) parts.push(`${counts.suggested} suggested`);
  if (counts.without_schedule != null) parts.push(`${counts.without_schedule} without`);
  return counts.active_vendors != null ? `${counts.active_vendors} vendors: ${parts.join(", ")}` : parts.join(", ");
}
