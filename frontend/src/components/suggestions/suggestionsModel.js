// Pure helpers for the suggestion UIs (automation batch items 4, 5, 7):
// Delivery Review's "Suggested deliveries", the invoice page's "Looks closed
// in Toast", and the Vendor Calendar's suggested schedules. No React, no
// fetch - tested with `node --test src/components/suggestions/suggestionsModel.test.mjs`.

export const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const DAY_CHOICES = [1, 3, 7];

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || `${one}s`}`;
}

// Quantities to start a suggested delivery's table with: the suggestion for
// preselected lines, empty (= not logged) for the rest.
export function initialQuantities(group) {
  const out = {};
  for (const item of group?.items || []) {
    out[item.item_id] = item.preselected && item.suggested_qty > 0 ? String(item.suggested_qty) : "";
  }
  return out;
}

// A typed quantity: a positive finite number, or null (blank / 0 / junk = not logged).
export function parseQty(raw) {
  if (raw == null || String(raw).trim() === "") return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}

export function qtyDiffers(item, raw) {
  const n = parseQty(raw);
  if (n == null || item.suggested_qty == null) return false;
  return Math.abs(n - item.suggested_qty) > 0.01;
}

// The delivery-confirm payload (the same body the manual review sends).
export function confirmPayload(group, qtyById) {
  return (group?.items || [])
    .map((item) => ({ item, qty: parseQty(qtyById[item.item_id]) }))
    .filter((x) => x.qty != null)
    .map(({ item, qty }) => ({ item_id: item.item_id, item_name: item.name, quantity_received: qty }));
}

export function evidenceLabel(evidence) {
  return evidence === "toast_received" ? "Received in Toast" : "Stock rose";
}

export function formatIsoDay(iso) {
  if (!iso) return "";
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

// A UTC timestamp as the store's own day ("Sat, Oct 10"), whatever the
// viewer's timezone.
export function formatStoreDay(isoDateTime) {
  if (!isoDateTime) return "";
  const d = new Date(isoDateTime);
  if (Number.isNaN(d.getTime())) return String(isoDateTime);
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "America/New_York" });
}

export function formatUnits(n) {
  if (n == null) return "-";
  const r = Math.round(n * 100) / 100;
  return r.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

// "Thu" / "Mon/Thu" from Python weekday numbers (Monday = 0).
export function weekdayList(days) {
  return [...new Set(days || [])]
    .filter((d) => Number.isInteger(d) && d >= 0 && d <= 6)
    .sort((a, b) => a - b)
    .map((d) => WEEKDAY_NAMES[d])
    .join("/");
}

// Lead days typed in the Edit form: a whole number 0-14, else null.
export function parseLeadDays(raw) {
  if (raw == null || String(raw).trim() === "") return null;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 0 || n > 14) return null;
  return n;
}

// Only the newest of several overlapping loads may update the screen: a slow
// reload started before a quick Undo must not answer after the Undo's own
// reload and put back what the Undo just changed.
export function latestOnly() {
  let seq = 0;
  return {
    start: () => ++seq,
    isLatest: (id) => id === seq,
  };
}

// Close-suggestion summary for the invoice page banner.
export function closeSummary(items) {
  const n = (items || []).filter((i) => i && i.close_suggestion).length;
  if (!n) return null;
  return `${plural(n, "invoice")} look${n === 1 ? "s" : ""} closed in Toast already`;
}
