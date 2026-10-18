// Pure helpers for the Inventory from Toast page's switch and readiness
// (backend: services/inventory_api_switch.py, services/inventory_api_readiness.py).
// No React, no fetch - node tests in inventoryApiModel.test.mjs.

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-10-12" -> "Mon Oct 12" (a store business date - no time zone maths).
export function nightLabel(isoDate) {
  if (typeof isoDate !== "string") return "-";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!m) return isoDate;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return `${WEEKDAYS[d.getUTCDay()]} ${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

export function pctText(v) {
  if (v == null || Number.isNaN(Number(v))) return "-";
  return `${Number(v)}%`;
}

// The rule a night is judged on, in one sentence.
export function thresholdsText(t) {
  if (!t) return "";
  const cat = t.category_pct >= 100 ? "category 100%" : `category ${t.category_pct}% or more`;
  return (
    `A night counts when on hand matches (or the difference is exactly what sold after the file) on ` +
    `${t.qty_pct}% or more of counted items, price and barcode match on ${t.price_pct}% / ${t.barcode_pct}% or more, ` +
    `${cat}, and at most ${t.max_unexplained_qty} on-hand differences are left unexplained.`
  );
}

export const RESULT_LABEL = {
  good: "Good",
  bad: "Missed",
  skipped: "Not counted",
  running: "Running",
};

export const RESULT_PILL = {
  good: "pill-success",
  bad: "pill-failed",
  skipped: "pill-neutral",
  running: "pill-info",
};

// Which metric cells of a night missed the bar (for highlighting).
export function missedCells(metrics, t) {
  const out = new Set();
  if (!metrics || !t) return out;
  const below = (v, need) => v == null || Number(v) < Number(need);
  if (below(metrics.qty_pct, t.qty_pct)) out.add("qty");
  if (below(metrics.price_pct, t.price_pct)) out.add("price");
  if (below(metrics.barcode_pct, t.barcode_pct)) out.add("barcode");
  if (below(metrics.category_pct, t.category_pct)) out.add("category");
  if (metrics.qty_unexplained == null || metrics.qty_unexplained > t.max_unexplained_qty) out.add("unexplained");
  return out;
}

// The headline under "Readiness".
export function streakSummary(r) {
  if (!r) return "";
  if (r.ready) {
    return r.switch_on
      ? `${r.streak} nights in a row met the bar - and it is on.`
      : `Ready: ${r.streak} nights in a row met the bar. You can turn it on.`;
  }
  const left = Math.max(r.needed - r.streak, 0);
  if (!r.nights || r.nights.length === 0) {
    return `No nightly comparison yet - ${r.needed} good nights in a row are needed.`;
  }
  return `${r.streak} of ${r.needed} nights in a row so far - ${left} more good night${left === 1 ? "" : "s"} needed.`;
}

// "Turned on" / "Turned off", plus where the old value came from when it was
// the server's starting value.
export function historyChange(h) {
  const now = h.on ? "Turned on" : "Turned off";
  if (h.previous_source === "env") {
    const before = h.previous_on ? "on" : "off";
    return h.on === h.previous_on
      ? `${now} (set here; was ${before} from the server setting)`
      : `${now} (was ${before} from the server setting)`;
  }
  return now;
}

// Where the value in force comes from, for the switch card.
export function sourceText(state, when) {
  if (!state) return "";
  if (state.source === "page") {
    return `Set here by ${state.changed_by || "an admin"}${state.changed_at ? `, ${when(state.changed_at)}` : ""}.`;
  }
  return `Starting value from the server setting (INVENTORY_API_SNAPSHOTS is ${state.env_default ? "on" : "off"}) - nobody has changed it here yet.`;
}

export const NOTE_MAX = 500;
