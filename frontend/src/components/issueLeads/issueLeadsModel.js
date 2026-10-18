// Fix Issues by PO section, with category & vendor leads (W4,
// docs/BATCH_OCT14_CONTRACT.md). Pure helpers - no React - so node tests can
// cover them (issueLeadsModel.test.mjs).
//
// Rows from /api/issues/{type} carry (additive, W4):
//   po_section  - the PO section (store aisle) the item prints under, or null
//   lead        - {user_id, name, via: "section"|"vendor"|"fallback", via_name} or null
//   lead_task   - {task_id, day, status, due_at} when the lead's latest daily
//                 Team task lists this item, else null

export const NO_SECTION = "No section";
export const OTHER_SECTION = "Other";
export const LEAD_NONE = "none"; // the "No lead" filter value
export const TASK_ANY = "";
export const TASK_ASSIGNED = "assigned";
export const TASK_NOT_ASSIGNED = "not_assigned";

export const EMPTY_FILTERS = Object.freeze({ sections: [], vendor: "", lead: "", task: TASK_ANY });

export function sectionOf(row) {
  return (row && row.po_section) || NO_SECTION;
}

// Toast's ";"-separated vendors, from the row or its product details (and a
// duplicate's variants).
export function vendorsOf(row) {
  const out = [];
  const add = (raw) => {
    for (const part of String(raw || "").split(";")) {
      const v = part.trim();
      if (v && !out.includes(v)) out.push(v);
    }
  };
  add(row?.product?.supplier || row?.supplier);
  for (const v of row?.variants || []) add(v?.product?.supplier || v?.supplier);
  return out;
}

// Section names, alphabetical with "Other" and "No section" last, each with
// its row count.
export function sectionOptions(rows) {
  const counts = new Map();
  for (const r of rows || []) counts.set(sectionOf(r), (counts.get(sectionOf(r)) || 0) + 1);
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => sectionRank(a.name) - sectionRank(b.name) || a.name.localeCompare(b.name));
}

function sectionRank(name) {
  if (name === NO_SECTION) return 2;
  if (name === OTHER_SECTION) return 1;
  return 0;
}

export function vendorOptions(rows) {
  const counts = new Map();
  for (const r of rows || []) for (const v of vendorsOf(r)) counts.set(v, (counts.get(v) || 0) + 1);
  return [...counts.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => a.name.localeCompare(b.name));
}

// Leads seen in the rows plus the saved leads from the summary (so a lead
// with nothing on this list still shows), by name.
export function leadOptions(rows, savedLeads = []) {
  const byId = new Map();
  for (const p of savedLeads || []) byId.set(String(p.user_id), { id: String(p.user_id), name: p.name, count: 0 });
  let none = 0;
  for (const r of rows || []) {
    if (!r.lead) {
      none += 1;
      continue;
    }
    const id = String(r.lead.user_id);
    const entry = byId.get(id) || { id, name: r.lead.name, count: 0 };
    entry.count += 1;
    byId.set(id, entry);
  }
  return { leads: [...byId.values()].sort((a, b) => a.name.localeCompare(b.name)), none };
}

export function matchesFilters(row, filters) {
  const f = filters || EMPTY_FILTERS;
  if (f.sections?.length && !f.sections.includes(sectionOf(row))) return false;
  if (f.vendor && !vendorsOf(row).some((v) => v.toLowerCase() === f.vendor.toLowerCase())) return false;
  if (f.lead) {
    if (f.lead === LEAD_NONE) {
      if (row.lead) return false;
    } else if (!row.lead || String(row.lead.user_id) !== String(f.lead)) {
      return false;
    }
  }
  if (f.task === TASK_ASSIGNED && !row.lead_task) return false;
  if (f.task === TASK_NOT_ASSIGNED && row.lead_task) return false;
  return true;
}

export function applyFilters(rows, filters) {
  if (!filters || !activeFilterCount(filters)) return rows || [];
  return (rows || []).filter((r) => matchesFilters(r, filters));
}

export function activeFilterCount(filters) {
  const f = filters || EMPTY_FILTERS;
  return (f.sections?.length ? 1 : 0) + (f.vendor ? 1 : 0) + (f.lead ? 1 : 0) + (f.task ? 1 : 0);
}

// Group already filtered + sorted rows by PO section, keeping their order
// inside a section. Sections alphabetical, "Other" / "No section" last.
// Each group is capped (`perGroupLimit`, raised per section by `groupLimits`).
export function groupBySection(rows, { perGroupLimit = 20, groupLimits = {} } = {}) {
  const map = new Map();
  for (const r of rows || []) {
    const s = sectionOf(r);
    if (!map.has(s)) map.set(s, []);
    map.get(s).push(r);
  }
  const groups = [...map.entries()]
    .sort((a, b) => sectionRank(a[0]) - sectionRank(b[0]) || a[0].localeCompare(b[0]))
    .map(([section, items]) => {
      const cap = groupLimits[section] ?? perGroupLimit;
      return { section, items: items.slice(0, cap), totalInGroup: items.length };
    });
  return { groups, shown: groups.flatMap((g) => g.items) };
}

// Rows from an older server have no po_section at all: then the page keeps
// its old category grouping.
export function hasSections(rows) {
  return (rows || []).some((r) => r && Object.prototype.hasOwnProperty.call(r, "po_section"));
}

// "?lead=12" (the daily task's link) -> "12"; junk -> "".
export function leadFromSearch(search) {
  try {
    const raw = new URLSearchParams(search || "").get("lead") || "";
    return /^\d{1,9}$/.test(raw) || raw === LEAD_NONE ? raw : "";
  } catch {
    return "";
  }
}

export function leadLine(lead) {
  if (!lead) return "No lead";
  if (lead.via === "vendor") return `Lead: ${lead.name} (vendor ${lead.via_name})`;
  if (lead.via === "fallback") return `Lead: ${lead.name} (everything else)`;
  return `Lead: ${lead.name}`;
}

// Per-tile count while a lead filter is on: the summary's open_by_lead.
export function leadCount(summaryItem, lead) {
  if (!summaryItem || !lead) return summaryItem?.open_count ?? null;
  if (!summaryItem.open_by_lead) return null; // older server
  if (lead === LEAD_NONE) return summaryItem.open_no_lead ?? null;
  return summaryItem.open_by_lead[String(lead)] ?? 0;
}

// --- Leads admin page ---------------------------------------------------------

// The value a row's picker shows: its saved lead, else (not saved yet) the
// document's proposal. "" = no lead.
export function pickerValue(row, edits, key) {
  if (edits && Object.prototype.hasOwnProperty.call(edits, key)) return edits[key];
  if (row.saved) return row.lead?.user_id != null ? String(row.lead.user_id) : "";
  return row.proposal?.user_id != null ? String(row.proposal.user_id) : "";
}

// The save body for every row whose shown value differs from what's saved,
// plus every unsaved row once the page is saved (so the proposal the owner
// looked at is what gets stored).
export function buildSaveBody(page, edits, { includeUnsaved = true } = {}) {
  const body = { sections: [], vendors: [] };
  const consider = (kind, list, nameKey) => {
    for (const row of list || []) {
      const key = `${kind}:${row[nameKey]}`;
      const value = pickerValue(row, edits, key);
      const saved = row.saved ? (row.lead?.user_id != null ? String(row.lead.user_id) : "") : null;
      const changed = saved === null ? includeUnsaved || Object.prototype.hasOwnProperty.call(edits || {}, key) : value !== saved;
      if (changed) body[kind === "section" ? "sections" : "vendors"].push({ name: row[nameKey], user_id: value ? Number(value) : null });
    }
  };
  consider("section", page.sections, "section");
  consider("vendor", page.vendors, "vendor");
  const fb = page.fallback;
  if (fb) {
    const value = pickerValue(fb, edits, "fallback");
    const saved = fb.saved ? (fb.lead?.user_id != null ? String(fb.lead.user_id) : "") : null;
    if (saved === null ? includeUnsaved || Object.prototype.hasOwnProperty.call(edits || {}, "fallback") : value !== saved) {
      body.fallback = { user_id: value ? Number(value) : null };
    }
  }
  return body;
}

export function changeCount(body) {
  return (body.sections?.length || 0) + (body.vendors?.length || 0) + (body.fallback ? 1 : 0);
}

export function basisLabel(proposal) {
  if (!proposal) return null;
  return proposal.basis === "guess" ? "Guess - check" : "From the document";
}
