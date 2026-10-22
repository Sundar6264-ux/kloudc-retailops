// Shared checklist model: the item types from docs/CHECKLIST_BUILDER_CONTRACT.md,
// legacy-block normalization, answer encoding, branching and validation. Used
// by the staff form (DailyChecklists), the answers view and the builder's
// live preview, so all three agree on what a checklist means.

export const ITEM_TYPES = [
  { type: "short_text", label: "Short answer", group: "text" },
  { type: "paragraph", label: "Paragraph", group: "text" },
  { type: "multiple_choice", label: "Multiple choice", group: "choice" },
  { type: "checkboxes", label: "Checkboxes", group: "choice" },
  { type: "dropdown", label: "Dropdown", group: "choice" },
  { type: "linear_scale", label: "Linear scale", group: "scale" },
  { type: "rating", label: "Rating", group: "scale" },
  { type: "multiple_choice_grid", label: "Multiple choice grid", group: "grid" },
  { type: "checkbox_grid", label: "Checkbox grid", group: "grid" },
  { type: "date", label: "Date", group: "other" },
  { type: "time", label: "Time", group: "other" },
  { type: "file_upload", label: "File upload (photos)", group: "other" },
  { type: "title_text", label: "Title and description", group: "display" },
  { type: "image", label: "Image", group: "display" },
];

export const TYPE_LABELS = Object.fromEntries(ITEM_TYPES.map((t) => [t.type, t.label]));

export const CHOICE_TYPES = new Set(["multiple_choice", "checkboxes", "dropdown"]);
export const GRID_TYPES = new Set(["multiple_choice_grid", "checkbox_grid"]);
export const SCALE_TYPES = new Set(["linear_scale", "rating"]);
export const DISPLAY_TYPES = new Set(["title_text", "image"]);
export const BRANCH_TYPES = new Set(["multiple_choice", "dropdown"]);
export const OTHER_TYPES = new Set(["multiple_choice", "checkboxes"]);

// Mirrors ISSUE_TAG_LABELS in backend/app/services/checklist_schemas.py. Used
// only when the admin API doesn't send its own list.
export const DEFAULT_ISSUE_TAGS = {
  issue: "Issue",
  morning_prep: "Morning Prep Required",
  low_stock: "Low Stock",
  critical: "Critical Issues",
  cleanup: "Cleanup Not Completed",
  not_turned_off: "Not Turned Off",
  not_done: "Not Done",
};

// A ticked box in a LEGACY checkbox group is stored as its own answer,
// {item_id: "Done"} - see checklist_schemas.py.
export const CHECKBOX_DONE = "Done";
export const OTHER_PREFIX = "Other: ";
export const SUBMIT_TARGET = "submit";
export const NEXT_TARGET = "next";

// --- ids -----------------------------------------------------------------------

function rand(n = 6) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < n; i += 1) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

export function newId(prefix, taken) {
  for (;;) {
    const id = `${prefix}_${rand()}`;
    if (!taken || !taken.has(id)) return id;
  }
}

// --- answer encoding -----------------------------------------------------------

// Answers are always strings (contract). Multi-value answers are JSON array
// strings; anything that isn't one comes back as null.
export function parseArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.startsWith("[")) return null;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map((v) => String(v)) : null;
  } catch {
    return null;
  }
}

export function encodeArray(values) {
  return values && values.length ? JSON.stringify(values) : null;
}

export function gridKey(itemId, rowId) {
  return `${itemId}.${rowId}`;
}

// Items converted from the original three checklists keep their old answer
// keys ("answer_format": "legacy", see backend checklist_engine.py): a grid
// row is answered under its bare row id, and a checkbox group stores
// {option_id: "Done"} per ticked box.
export function isLegacyFormat(item) {
  return item?.answer_format === "legacy";
}

export function rowKey(item, row) {
  return isLegacyFormat(item) ? row.id : gridKey(item.id, row.id);
}

export function isLegacyCheckboxes(item) {
  return item?.type === "checkboxes" && isLegacyFormat(item);
}

// Every key an item's answer uses in the flat answers map.
export function answerKeys(item) {
  if (DISPLAY_TYPES.has(item.type)) return [];
  if (GRID_TYPES.has(item.type)) return (item.rows || []).map((r) => rowKey(item, r));
  if (isLegacyCheckboxes(item)) return (item.options || []).map((o) => o.id);
  return [item.id];
}

export function isOtherValue(value) {
  return typeof value === "string" && value.startsWith(OTHER_PREFIX);
}

// --- normalization -------------------------------------------------------------

// Old schemas nest a single `grid`, a `grids` list, `questions` and
// `checkbox_groups`; new ones use `items`. A section with a non-empty
// `items` list is rendered from it (the backend may still emit the legacy
// blocks next to it for app build 5), otherwise from the legacy blocks.
export function normalizeSection(section, index) {
  const grids = [];
  if (section.grid) grids.push(section.grid);
  if (section.grids) grids.push(...section.grids);
  const items = Array.isArray(section.items) && section.items.length ? section.items : null;
  return {
    ...section,
    key: section.id || `sec_${index}`,
    items,
    grids: items ? [] : grids,
    questions: items ? [] : section.questions || [],
    checkboxGroups: items ? [] : section.checkbox_groups || [],
  };
}

export function normalizeSchema(schema) {
  const sections = (schema.sections || []).map(normalizeSection);
  // A schema served with items carries the "anything unusual?" box and the
  // final confirmation as items (legacy_role) - don't draw them twice.
  const roles = new Set();
  for (const s of sections) for (const it of s.items || []) if (it.legacy_role) roles.add(it.legacy_role);
  const usesItems = sections.some((s) => s.items);
  return {
    ...schema,
    settings: schema.settings || {},
    sections,
    issues_field: usesItems && roles.has("issues_field") ? null : schema.issues_field || null,
    confirmation: usesItems && roles.has("confirmation") ? null : schema.confirmation || null,
  };
}

// Whether the "All as usual" button shows. Legacy schemas (no settings) always
// had it.
export function allAsUsualEnabled(schema) {
  const s = schema.settings;
  if (!s || s.all_as_usual == null) return true;
  return Boolean(s.all_as_usual);
}

// --- branching -----------------------------------------------------------------

// The section a chosen answer jumps to, if any.
function branchTarget(item, answers) {
  if (!BRANCH_TYPES.has(item.type) || !item.go_to_section) return null;
  const value = answers[item.id];
  if (value == null) return null;
  return item.go_to_section[value] || null;
}

// Sections on the path the current answers take, Google Forms style (same
// rule as the backend's visited_sections): an answered branching question in
// the section decides where to go; otherwise the section's own
// `next_section`, else the next section. "submit" ends the form, "next" is
// the next section. Returns the ordered list of visible section keys.
export function visibleSectionKeys(schema, answers) {
  const sections = schema.sections;
  const indexById = {};
  sections.forEach((s, i) => {
    indexById[s.key] = i;
    if (s.id) indexById[s.id] = i;
  });
  const out = [];
  const seen = new Set();
  let i = 0;
  while (i != null && i < sections.length && !seen.has(i)) {
    seen.add(i);
    const section = sections[i];
    out.push(section.key);
    let target = section.next_section || null;
    for (const item of section.items || []) {
      const t = branchTarget(item, answers);
      if (t) target = t;
    }
    if (target === SUBMIT_TARGET) break;
    if (target && indexById[target] != null) i = indexById[target];
    else i += 1;
  }
  return out;
}

// --- requirements, progress, validation -----------------------------------------

// One "slot" per thing that needs an answer: an item, a grid row, a legacy
// row / question / checkbox group, the issues field and the confirmation.
// Used for the progress bar and the submit check.
export function requiredSlots(schema, visibleKeys) {
  const visible = new Set(visibleKeys);
  const slots = [];
  for (const section of schema.sections) {
    if (!visible.has(section.key)) continue;
    for (const grid of section.grids) {
      for (const row of grid.rows) slots.push({ kind: "legacy_value", id: row.id });
    }
    for (const q of section.questions) if (q.required) slots.push({ kind: "legacy_value", id: q.id });
    for (const g of section.checkboxGroups) if (g.required) slots.push({ kind: "legacy_group", id: g.id, group: g });
    for (const item of section.items || []) {
      if (DISPLAY_TYPES.has(item.type) || !item.required) continue;
      if (GRID_TYPES.has(item.type)) {
        for (const row of item.rows || []) slots.push({ kind: "grid_row", id: rowKey(item, row), item });
      } else if (isLegacyCheckboxes(item)) {
        slots.push({ kind: "legacy_boxes", id: item.id, item });
      } else {
        slots.push({ kind: "item", id: item.id, item });
      }
    }
  }
  if (schema.issues_field?.required) slots.push({ kind: "text", id: schema.issues_field.id });
  if (schema.confirmation?.required) slots.push({ kind: "legacy_value", id: schema.confirmation.id });
  return slots;
}

function hasValue(value) {
  if (value == null) return false;
  if (typeof value === "string") {
    const arr = parseArray(value);
    if (arr) return arr.length > 0;
    if (isOtherValue(value)) return value.slice(OTHER_PREFIX.length).trim() !== "";
    return value.trim() !== "";
  }
  return true;
}

export function slotAnswered(slot, answers) {
  if (slot.kind === "legacy_group") return slot.group.items.some((it) => answers[it.id] === CHECKBOX_DONE);
  if (slot.kind === "legacy_boxes") return (slot.item.options || []).some((o) => answers[o.id] === CHECKBOX_DONE);
  return hasValue(answers[slot.id]);
}

// Response validation (contract `validation`), Google Forms style. Returns an
// error message or null. Only checked when there is an answer.
export function validationError(item, value) {
  const v = item.validation;
  if (!v || value == null || value === "") return null;
  const msg = (fallback) => (v.message && v.message.trim()) || fallback;
  const rule = v.rule;
  const target = v.value;
  if (v.kind === "checkbox_count") {
    const n = (parseArray(value) || []).length;
    const k = Number(target);
    if (Number.isNaN(k)) return null;
    if (rule === "at_least" && n < k) return msg(`Tick at least ${k}.`);
    if (rule === "at_most" && n > k) return msg(`Tick at most ${k}.`);
    if (rule === "exactly" && n !== k) return msg(`Tick exactly ${k}.`);
    return null;
  }
  const text = String(value);
  if (v.kind === "number") {
    const num = Number(text.trim());
    const isNum = text.trim() !== "" && !Number.isNaN(num);
    if (!isNum) return msg("Enter a number.");
    const a = Array.isArray(target) ? Number(target[0]) : Number(target);
    const b = Array.isArray(target) ? Number(target[1]) : null;
    switch (rule) {
      case "is_number":
        return null;
      case "whole_number":
        return Number.isInteger(num) ? null : msg("Enter a whole number.");
      case "gt":
        return num > a ? null : msg(`Must be greater than ${a}.`);
      case "gte":
        return num >= a ? null : msg(`Must be ${a} or more.`);
      case "lt":
        return num < a ? null : msg(`Must be less than ${a}.`);
      case "lte":
        return num <= a ? null : msg(`Must be ${a} or less.`);
      case "eq":
        return num === a ? null : msg(`Must be ${a}.`);
      case "neq":
        return num !== a ? null : msg(`Can't be ${a}.`);
      case "between":
        return num >= a && num <= b ? null : msg(`Must be between ${a} and ${b}.`);
      case "not_between":
        return num < a || num > b ? null : msg(`Can't be between ${a} and ${b}.`);
      default:
        return null;
    }
  }
  if (v.kind === "text") {
    const t = String(target ?? "");
    switch (rule) {
      case "contains":
        return text.includes(t) ? null : msg(`Must contain "${t}".`);
      case "not_contains":
        return text.includes(t) ? msg(`Can't contain "${t}".`) : null;
      case "email":
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text.trim()) ? null : msg("Enter an email address.");
      case "url":
        return /^(https?:\/\/)?[^\s/$.?#]+\.\S+$/i.test(text.trim()) ? null : msg("Enter a web address.");
      default:
        return null;
    }
  }
  if (v.kind === "length") {
    const k = Number(target);
    if ((rule === "max_chars" || rule === "max") && text.length > k) return msg(`At most ${k} characters.`);
    if ((rule === "min_chars" || rule === "min") && text.length < k) return msg(`At least ${k} characters.`);
    return null;
  }
  if (v.kind === "regex") {
    let re;
    try {
      re = new RegExp(String(target ?? ""));
    } catch {
      return null;
    }
    if (rule === "not_contains") return re.test(text) ? msg("Answer doesn't match the required pattern.") : null;
    if (rule === "matches") {
      const full = new RegExp(`^(?:${String(target ?? "")})$`);
      return full.test(text) ? null : msg("Answer doesn't match the required pattern.");
    }
    if (rule === "not_matches") {
      const full = new RegExp(`^(?:${String(target ?? "")})$`);
      return full.test(text) ? msg("Answer doesn't match the required pattern.") : null;
    }
    return re.test(text) ? null : msg("Answer doesn't match the required pattern.");
  }
  return null;
}

// Every problem with the current answers, keyed by item id (or grid key /
// legacy id), for the visible sections only. Empty object = OK to submit.
export function formErrors(schema, answers, visibleKeys) {
  const errors = {};
  for (const slot of requiredSlots(schema, visibleKeys)) {
    if (!slotAnswered(slot, answers)) {
      const key = slot.kind === "grid_row" ? slot.item.id : slot.id;
      if (slot.kind === "grid_row") errors[key] = "Answer every row.";
      else if (slot.kind === "legacy_group" || slot.kind === "legacy_boxes") errors[key] = "Tick at least one box.";
      else errors[key] = "This question is required.";
    }
  }
  const visible = new Set(visibleKeys);
  for (const section of schema.sections) {
    if (!visible.has(section.key)) continue;
    for (const item of section.items || []) {
      if (errors[item.id]) continue;
      const value = answers[item.id];
      if (OTHER_TYPES.has(item.type) && item.other_option) {
        const vals = item.type === "checkboxes" ? parseArray(value) || [] : value ? [value] : [];
        if (vals.some((x) => isOtherValue(x) && !x.slice(OTHER_PREFIX.length).trim())) {
          errors[item.id] = "Type your \"Other\" answer.";
          continue;
        }
      }
      if (item.type === "checkboxes" && !isLegacyFormat(item)) {
        const picked = parseArray(value) || [];
        // an unticked optional checkbox question isn't count-checked
        if (!picked.length && !item.required) continue;
      }
      if (GRID_TYPES.has(item.type) && item.one_per_column) {
        const seen = new Set();
        let clash = null;
        for (const row of item.rows || []) {
          const v = answers[rowKey(item, row)];
          const picked = item.type === "checkbox_grid" ? parseArray(v) || [] : v != null ? [v] : [];
          for (const c of picked) {
            if (seen.has(c)) clash = c;
            seen.add(c);
          }
        }
        if (clash) {
          errors[item.id] = `"${clash}" can only be picked for one row.`;
          continue;
        }
      }
      if (["short_text", "paragraph"].includes(item.type) && !String(value ?? "").trim()) continue;
      const err = validationError(item, value);
      if (err) errors[item.id] = err;
    }
  }
  return errors;
}

// Only the answers that belong to visible sections are sent (a skipped
// branch isn't part of the submission).
export function answersForSubmit(schema, answers, visibleKeys) {
  const visible = new Set(visibleKeys);
  const keep = new Set();
  for (const section of schema.sections) {
    if (!visible.has(section.key)) continue;
    for (const grid of section.grids) for (const row of grid.rows) keep.add(row.id);
    for (const q of section.questions) keep.add(q.id);
    for (const g of section.checkboxGroups) for (const it of g.items) keep.add(it.id);
    for (const item of section.items || []) for (const k of answerKeys(item)) keep.add(k);
  }
  if (schema.issues_field) keep.add(schema.issues_field.id);
  if (schema.confirmation) keep.add(schema.confirmation.id);
  const out = {};
  for (const [k, v] of Object.entries(answers)) {
    if (v == null || v === "") continue;
    if (keep.has(k)) out[k] = v;
  }
  return out;
}

// --- "All as usual" -------------------------------------------------------------

// The normal answer for a row/question: the first option that isn't flagged
// as an issue ("Yes", "Done", "We have enough"...). Null if every option is
// an issue. Same rule as the app's "All as usual".
export function normalOption(options) {
  return (options || []).find((o) => !o.issue_tag) || null;
}

// Normal answers for every UNANSWERED single-choice row or question in a
// section - answers already given (including issues) are never overwritten.
// Dropdowns name a person or a thing, so they have no "usual" answer.
export function sectionFills(section, answers) {
  const fills = {};
  for (const grid of section.grids) {
    const normal = normalOption(grid.options);
    if (!normal) continue;
    for (const row of grid.rows) if (answers[row.id] == null) fills[row.id] = normal.value;
  }
  for (const q of section.questions) {
    if (q.input === "dropdown") continue;
    const normal = normalOption(q.options);
    if (normal && answers[q.id] == null) fills[q.id] = normal.value;
  }
  for (const item of section.items || []) {
    if (item.type === "multiple_choice_grid") {
      const normal = normalOption(item.options);
      if (!normal) continue;
      for (const row of item.rows || []) {
        const k = rowKey(item, row);
        if (answers[k] == null) fills[k] = normal.value;
      }
    } else if (item.type === "multiple_choice" && !item.go_to_section && !item.legacy_role) {
      const normal = normalOption(item.options);
      if (normal && answers[item.id] == null) fills[item.id] = normal.value;
    }
  }
  return fills;
}

// --- misc ----------------------------------------------------------------------

// Deterministic shuffle (per form open) so options don't jump on re-render.
export function shuffled(list, seed) {
  const out = [...list];
  let s = seed || 1;
  for (let i = out.length - 1; i > 0; i -= 1) {
    s = (s * 9301 + 49297) % 233280;
    const j = Math.floor((s / 233280) * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

export function scaleRange(item) {
  const sc = item.scale || {};
  const min = item.type === "rating" ? 1 : Number(sc.min ?? 1);
  const max = Number(sc.max ?? 5);
  const out = [];
  for (let n = min; n <= max; n += 1) out.push(n);
  return out;
}

export function issueTagForValue(options, value) {
  const opt = (options || []).find((o) => o.value === value);
  return opt ? opt.issue_tag || null : null;
}

// A checklist picture's address is shown as a link and an <img>: only an
// uploaded picture (a same-site /path), a web address, or a local preview
// (blob:, data:image/) - never javascript: and the like. Since Oct 22 a
// non-admin with "Build checklists" can type this address, and admins open
// the pictures (the server refuses to publish anything else too).
export function isSafeImageUrl(url) {
  // A browser drops tabs and line breaks inside an address and reads "/\\" as
  // "//", so "/<tab>/host" and "/\\host" would load another site: check it the
  // way the browser will read it.
  const u = String(url || "").replace(/[\t\n\r]/g, "").trim();
  if (!u) return false;
  if (u.startsWith("/")) return !/^\/[/\\]/.test(u);
  return /^(https?:\/\/|blob:|data:image\/)/i.test(u);
}

// Deleting a checklist (owner, Oct 22: "but only admins can delete"). Only an
// admin (never the demo login) gets Delete, and only for a checklist the
// server marks can_delete (never filled in, not one of the originals; an
// older server never sends it, so no Delete there). Everyone else turns it
// off; `hint` says why an admin sees no Delete.
export function checklistDeleteAction(definition, user) {
  if (!user?.is_admin || user?.demo) return { canDelete: false, hint: null };
  if (definition?.can_delete === true) return { canDelete: true, hint: null };
  if (definition?.can_delete !== false) return { canDelete: false, hint: null };
  if (definition?.builtin === true) {
    return { canDelete: false, hint: "One of the store's original checklists - turn it off instead of deleting it." };
  }
  const n = Number(definition?.submission_count) || 0;
  const times = n === 1 ? "1 time" : `${n} times`;
  return {
    canDelete: false,
    hint: n > 0 ? `Filled in ${times}, so it can't be deleted - turn it off instead.` : "Already filled in, so it can't be deleted - turn it off instead.",
  };
}

export function checklistDeleteConfirmText(definition) {
  return `Delete "${definition?.title || "this checklist"}"? It has never been filled in. This can't be undone.`;
}
