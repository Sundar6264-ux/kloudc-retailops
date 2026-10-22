// Fix Issues › Closed: Employee (who closed it) and Category filters, and the
// Closed report's "which filter is on" line (Oct 22, owner: the per-person
// count and the 30-day report disagreed). Pure functions so node tests cover
// them; the same names as the server's Closed report ("Unknown" for a close
// with no name, "No category" for an item with none).

export const UNKNOWN_PERSON = "Unknown";
export const NO_CATEGORY = "No category";
export const EMPTY_CLOSED_FILTERS = Object.freeze({ closedBy: "", category: "" });

export function personOf(row) {
  return String(row?.closed_by || "").trim() || UNKNOWN_PERSON;
}

export function categoryOf(row) {
  return String(row?.category || "").trim() || NO_CATEGORY;
}

const same = (a, b) => String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();

// Counted ignoring case (the filter matches ignoring case too), shown with
// the first spelling seen.
function counted(values) {
  const counts = new Map();
  for (const v of values) {
    const key = v.toLowerCase();
    const prev = counts.get(key);
    counts.set(key, { name: prev ? prev.name : v, count: (prev ? prev.count : 0) + 1 });
  }
  return [...counts.values()]
    .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
}

// Options with how many rows each would leave: people with the category
// filter applied, categories with the person filter applied.
export function closedPersonOptions(rows, filters = EMPTY_CLOSED_FILTERS) {
  return counted((rows || []).filter((r) => !filters.category || same(categoryOf(r), filters.category)).map(personOf));
}

export function closedCategoryOptions(rows, filters = EMPTY_CLOSED_FILTERS) {
  return counted((rows || []).filter((r) => !filters.closedBy || same(personOf(r), filters.closedBy)).map(categoryOf));
}

export function closedFilterCount(filters) {
  return (filters?.closedBy ? 1 : 0) + (filters?.category ? 1 : 0);
}

export function applyClosedFilters(rows, filters) {
  if (!closedFilterCount(filters)) return rows || [];
  return (rows || []).filter(
    (r) => (!filters.closedBy || same(personOf(r), filters.closedBy)) && (!filters.category || same(categoryOf(r), filters.category))
  );
}

// "Closed by Ravi · Snacks" / "Everyone · All categories" - the server sends
// this as `showing`; worked out here too for an older server.
export function closedShowing(filters) {
  const who = filters?.closedBy ? `Closed by ${filters.closedBy}` : "Everyone";
  const what = filters?.category || "All categories";
  return `${who} · ${what}`;
}

// The Closed report tab's count: the server's closed_report (the report's own
// default - last 30 days, everyone) when it sends one; else the old 7-day sum.
export function closedReportTileCount(summary, closedReport) {
  if (closedReport && typeof closedReport.total === "number") return closedReport.total;
  if (!summary) return null;
  return Object.values(summary).reduce((n, s) => n + (s?.closed_count || 0), 0);
}

export function closedReportTileHint(closedReport) {
  return closedReport ? `Last ${closedReport.days || 30} days · everyone` : "What was closed, day by day.";
}

// Per-person counts, most first (they add up to the report's total).
export function personChips(byPerson) {
  return Object.entries(byPerson || {})
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
}
