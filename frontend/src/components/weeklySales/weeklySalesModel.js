// Pure helpers for the week-by-week sales breakdown (GET
// /api/ordering/item-weekly-sales) shown on Supplier Projection, Reorder
// Candidates and Fix Issues' item details. Oct 14 batch W3 added each week's
// average on-hand count (avg_on_hand / on_hand_days / period_days) - older
// servers don't send them, which reads as "no count" ("-").

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function isNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

// "2026-06-01" -> "Jun 1". Anything unparseable comes back as is.
export function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso ?? ""));
  if (!m) return String(iso ?? "");
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${month} ${Number(m[3])}` : String(iso);
}

// "Jun 1 - Jun 7" (no em dash, by the owner's rule).
export function weekLabel(week) {
  return `${shortDate(week?.week_start)} - ${shortDate(week?.week_end)}`;
}

// 24 -> "24", 23.5 -> "23.5", -3 -> "-3", 1234.56 -> "1,234.6".
export function formatCount(v) {
  if (!isNum(v)) return "-";
  const rounded = Math.round(v * 10) / 10;
  return rounded.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

// Units sold: up to 2 decimals for weighed items (lb).
export function formatSold(v) {
  if (!isNum(v)) return "0";
  return (Math.round(v * 100) / 100).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

// The on-hand part of one week:
//   value    "24" or "-" (no tracked day)
//   coverage "(6 of 7 days)" when only some of the week's days had a count,
//            "" when every day did or none did
//   negative true when the average is below zero (Toast's count went negative)
//   known    false when there's no figure at all
export function onHandCell(week) {
  const avg = week?.avg_on_hand;
  const days = isNum(week?.on_hand_days) ? week.on_hand_days : 0;
  const period = isNum(week?.period_days) ? week.period_days : 0;
  if (!isNum(avg) || days <= 0) {
    return { value: "-", coverage: "", negative: false, known: false };
  }
  const coverage = period > 0 && days < period ? `(${days} of ${period} day${period === 1 ? "" : "s"})` : "";
  return { value: formatCount(avg), coverage, negative: avg < 0, known: true };
}

// One line for a screen reader / a plain-text summary:
// "Jun 1 - Jun 7: sold 10, avg on hand 24 (6 of 7 days)".
export function weekSummary(week) {
  const oh = onHandCell(week);
  const onHand = oh.known ? `avg on hand ${oh.value}${oh.coverage ? ` ${oh.coverage}` : ""}` : "avg on hand -";
  return `${weekLabel(week)}: sold ${formatSold(week?.quantity)}, ${onHand}`;
}

// True when the server sent the on-hand fields at all (an older server
// doesn't): the column is then left out rather than showing "-" everywhere.
export function hasOnHandFields(weeks) {
  return Array.isArray(weeks) && weeks.some((w) => w && "avg_on_hand" in w);
}

// Revenue column: only when some week has a dollar figure (the server sends
// null to a "Hide sales numbers" user).
export function hasRevenue(weeks) {
  return Array.isArray(weeks) && weeks.some((w) => w && w.revenue !== null && w.revenue !== undefined);
}

// True when no week sold anything (with include_empty_weeks the list can hold
// weeks with 0 sold and on-hand counts only).
export function noSales(weeks) {
  return !Array.isArray(weeks) || weeks.every((w) => !(isNum(w?.quantity) && w.quantity > 0));
}

// "YYYY-MM-DD" `days` before `iso` (UTC arithmetic on a plain date).
export function isoMinusDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}
