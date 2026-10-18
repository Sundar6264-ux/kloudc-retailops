// Builder-side helpers: reading the admin API's responses, turning a legacy
// schema into editable items, blank items per type, and the "what changed"
// summary shown before publishing.

import {
  BRANCH_TYPES,
  CHOICE_TYPES,
  DEFAULT_ISSUE_TAGS,
  DISPLAY_TYPES,
  GRID_TYPES,
  OTHER_TYPES,
  SCALE_TYPES,
  TYPE_LABELS,
  newId,
} from "./model.js";

// --- response adapters (tolerant of small shape differences) -------------------

export function definitionsFromList(res) {
  const raw = res?.definitions ?? res?.items ?? res;
  return Array.isArray(raw) ? raw : [];
}

// GET /admin/definitions/{type} -> { definition, published, draft, editable, versions }
// (published = the served current version, editable = the saved draft or the
// current version in items form, with title / description).
export function definitionFromDetail(res) {
  const definition = res || {};
  return {
    definition,
    published: res?.schema || null,
    draft: res?.draft || null,
    editable: res?.editable || null,
    versions: res?.versions || [],
  };
}

// The editor meta's issue tags ([{key, label}]) as {key: label}.
export function issueTagsFromMeta(meta) {
  const raw = meta?.issue_tags;
  if (!raw) return DEFAULT_ISSUE_TAGS;
  if (Array.isArray(raw)) return Object.fromEntries(raw.map((t) => [t.key || t.tag || t.value, t.label]));
  return raw;
}

// Backend validation errors, as a list of readable lines. Handles a plain
// string, FastAPI's [{loc, msg}] and {errors: [...]} / {detail: {errors}}.
export function errorLines(err) {
  const body = err?.body;
  if (Array.isArray(body?.problems) && body.problems.length) return body.problems.map(String);
  const detail = body?.detail ?? body;
  const lines = [];
  const push = (x) => {
    if (x == null) return;
    if (typeof x === "string") lines.push(x);
    else if (x.msg || x.message) {
      const where = x.path || x.item_id || x.section_id || (Array.isArray(x.loc) ? x.loc.join(".") : x.loc);
      lines.push(where ? `${where}: ${x.msg || x.message}` : x.msg || x.message);
    } else lines.push(JSON.stringify(x));
  };
  if (Array.isArray(detail)) detail.forEach(push);
  else if (detail && Array.isArray(detail.errors)) detail.errors.forEach(push);
  else if (detail && typeof detail === "object" && (detail.message || detail.msg)) push(detail);
  else if (typeof detail === "string") push(detail);
  if (!lines.length) lines.push(err?.message || "Something went wrong.");
  return lines;
}

// Ids (items / sections) mentioned in the error lines, to outline those cards.
export function idsInErrors(lines, draft) {
  const ids = new Set();
  const all = [];
  for (const s of draft.sections || []) {
    all.push(s.id);
    for (const it of s.items || []) all.push(it.id);
  }
  for (const line of lines) for (const id of all) if (id && line.includes(id)) ids.add(id);
  return ids;
}

// --- legacy -> items -----------------------------------------------------------

// The backend converts a seeded checklist to `items` when it serves it to the
// editor; this is only a fallback for a schema that still has legacy blocks.
// Legacy ids are kept so answers stay comparable.
export function toEditable(schema) {
  const taken = new Set();
  const sections = (schema.sections || []).map((section, sIdx) => {
    const id = section.id || newId("s", taken);
    taken.add(id);
    if (Array.isArray(section.items) && section.items.length) {
      const out = { id, title: section.title ?? null, description: section.description ?? null, items: section.items };
      if (section.next_section) out.next_section = section.next_section;
      return out;
    }
    const items = [];
    const grids = [...(section.grid ? [section.grid] : []), ...(section.grids || [])];
    grids.forEach((grid, gIdx) => {
      items.push({
        id: grid.id || `${id}_g${gIdx}`,
        type: "multiple_choice_grid",
        title: grid.title || "",
        description: null,
        required: true,
        options: (grid.options || []).map((o) => ({ value: o.value, issue_tag: o.issue_tag || null })),
        rows: (grid.rows || []).map((r) => ({ id: r.id, title: r.label })),
        answer_format: "legacy",
      });
    });
    for (const q of section.questions || []) {
      items.push({
        id: q.id,
        type: q.input === "dropdown" ? "dropdown" : "multiple_choice",
        title: q.label || "",
        description: null,
        required: Boolean(q.required),
        options: (q.options || []).map((o) => ({ value: o.value, issue_tag: o.issue_tag || null })),
      });
    }
    for (const g of section.checkbox_groups || []) {
      items.push({
        id: g.id,
        type: "checkboxes",
        title: g.title || "",
        description: null,
        required: Boolean(g.required),
        options: (g.items || []).map((it) => ({ id: it.id, value: it.label, issue_tag: null })),
        unticked_tag: g.unticked_tag || null,
        answer_format: "legacy",
      });
    }
    return { id, title: section.title ?? null, description: section.description ?? null, items, _sIdx: sIdx };
  });
  const out = { ...schema, sections: sections.map(({ _sIdx, ...s }) => s) };
  // The top-level "anything unusual?" box / confirmation become the last
  // items (legacy_role), as the backend's legacy_to_items does - unless the
  // items already carry them.
  const hasRole = (role) => out.sections.some((s) => (s.items || []).some((it) => it.legacy_role === role));
  const trailing = [];
  const f = schema.issues_field;
  if (f && !hasRole("issues_field")) {
    trailing.push({
      id: f.id, type: "paragraph", title: f.label, description: null, required: Boolean(f.required),
      flag_if_answered: true, nothing_to_report_ok: Boolean(f.required), legacy_role: "issues_field",
      ...(f.hint ? { hint: f.hint } : {}),
    });
  }
  const c = schema.confirmation;
  if (c && !hasRole("confirmation")) {
    trailing.push({
      id: c.id, type: "multiple_choice", title: c.label, description: null, required: Boolean(c.required),
      options: [{ value: c.label, issue_tag: null }], legacy_role: "confirmation",
    });
  }
  if (trailing.length) {
    if (!out.sections.length) out.sections.push({ id: "s1", title: null, description: null, items: [] });
    out.sections[out.sections.length - 1].items.push(...trailing);
  }
  delete out.issues_field;
  delete out.confirmation;
  return out;
}

// --- blank items ---------------------------------------------------------------

export function allIds(draft) {
  const ids = new Set();
  for (const s of draft.sections || []) {
    ids.add(s.id);
    for (const it of s.items || []) {
      ids.add(it.id);
      for (const r of it.rows || []) ids.add(r.id);
      for (const o of it.options || []) if (o.id) ids.add(o.id);
    }
  }
  return ids;
}

export function blankItem(type, taken) {
  const base = { id: newId("q", taken), type, title: "", description: null, required: false };
  if (CHOICE_TYPES.has(type)) {
    return { ...base, title: "Question", options: [{ value: "Option 1", issue_tag: null }] };
  }
  if (GRID_TYPES.has(type)) {
    return {
      ...base,
      title: "Question",
      options: [
        { value: "Column 1", issue_tag: null },
        { value: "Column 2", issue_tag: null },
      ],
      rows: [
        { id: newId("r", taken), title: "Row 1" },
        { id: newId("r", taken), title: "Row 2" },
      ],
      shuffle: false,
    };
  }
  if (type === "linear_scale") return { ...base, title: "Question", scale: { min: 1, max: 5, min_label: "", max_label: "" } };
  if (type === "rating") return { ...base, title: "Question", scale: { min: 1, max: 5 } };
  if (type === "file_upload") return { ...base, title: "Upload a photo", file: { max_files: 1, types: ["image"] } };
  if (type === "title_text") return { ...base, title: "Title", required: false };
  if (type === "image") return { ...base, title: "", image_url: "", required: false };
  return { ...base, title: "Question" };
}

// Switching an item's type keeps what still makes sense (title, description,
// required, options between choice types, rows for grids) like Google Forms.
export function changeType(item, type, taken) {
  const fresh = blankItem(type, taken);
  const next = { ...fresh, id: item.id, title: item.title || fresh.title, description: item.description ?? null };
  if (!DISPLAY_TYPES.has(type)) next.required = Boolean(item.required);
  const hadOptions = Array.isArray(item.options) && item.options.length;
  // Options carry over between choice types, and become a grid's columns
  // (a brand-new question's lone "Option 1" doesn't - the grid's defaults win).
  const untouched = hadOptions && item.options.length === 1 && item.options[0].value === "Option 1";
  if ((CHOICE_TYPES.has(type) || GRID_TYPES.has(type)) && hadOptions && !(GRID_TYPES.has(type) && untouched)) {
    next.options = item.options;
  }
  if (GRID_TYPES.has(type) && item.rows?.length) next.rows = item.rows;
  if (OTHER_TYPES.has(type)) next.other_option = Boolean(item.other_option);
  if ((CHOICE_TYPES.has(type) || GRID_TYPES.has(type)) && item.shuffle) next.shuffle = true;
  if (SCALE_TYPES.has(type) && item.scale) next.scale = { ...next.scale, max: Math.min(10, item.scale.max || 5) };
  if (BRANCH_TYPES.has(type) && item.go_to_section) next.go_to_section = item.go_to_section;
  if (["short_text", "paragraph"].includes(type) && item.validation && item.validation.kind !== "checkbox_count") {
    next.validation = item.validation;
  }
  if (["short_text", "paragraph"].includes(type)) {
    for (const k of ["flag_if_answered", "flag_tag", "nothing_to_report_ok", "hint"]) if (item[k] != null) next[k] = item[k];
    if (type === "paragraph" && next.validation && !["length", "regex"].includes(next.validation.kind)) next.validation = null;
  }
  if (item.summary_label && SUMMARY_TYPES.has(type)) next.summary_label = item.summary_label;
  if (GRID_TYPES.has(type) && item.one_per_column) next.one_per_column = true;
  if (next.options) next.options = next.options.map(({ id, ...o }) => o);
  return next;
}

// Single-answer types whose answer can be shown next to "Submitted by".
export const SUMMARY_TYPES = new Set(["short_text", "paragraph", "multiple_choice", "dropdown", "linear_scale", "rating", "date", "time"]);

export function cloneItem(item, taken) {
  const copy = JSON.parse(JSON.stringify(item));
  copy.id = newId("q", taken);
  taken.add(copy.id);
  delete copy.legacy_role;
  if (copy.options) {
    copy.options = copy.options.map((o) => {
      if (!o.id) return o;
      const id = newId("o", taken);
      taken.add(id);
      return { ...o, id };
    });
  }
  if (copy.rows) copy.rows = copy.rows.map((r) => {
    const id = newId("r", taken);
    taken.add(id);
    return { ...r, id };
  });
  return copy;
}

// --- client-side checks (the backend re-validates) -----------------------------

export function draftProblems(draft) {
  const problems = [];
  const sectionIndex = Object.fromEntries((draft.sections || []).map((s, i) => [s.id, i]));
  const badTarget = (t, sIdx) => t && t !== "submit" && t !== "next" && !(sectionIndex[t] > sIdx);
  if (!String(draft.title || "").trim()) problems.push({ id: null, text: "The checklist needs a title." });
  let answerable = 0;
  (draft.sections || []).forEach((s, sIdx) => {
    const where = `Section ${sIdx + 1}`;
    if (badTarget(s.next_section, sIdx)) {
      problems.push({ id: s.id, text: `${where}: "after section" must go to a later section or submit.` });
    }
    const branching = (s.items || []).filter((it) => it.go_to_section && Object.keys(it.go_to_section).length);
    if (branching.length > 1) {
      problems.push({ id: branching[1].id, text: `${where}: only one question per section can choose the next section.` });
    }
    (s.items || []).forEach((it, iIdx) => {
      const label = `${where}, question ${iIdx + 1}${it.title ? ` ("${it.title}")` : ""}`;
      if (!DISPLAY_TYPES.has(it.type)) answerable += 1;
      if (it.type !== "image" && !String(it.title || "").trim()) {
        problems.push({ id: it.id, text: `${label}: needs ${it.type === "title_text" ? "a title" : "a question"}.` });
      }
      if (it.type === "image" && !String(it.image_url || "").trim()) problems.push({ id: it.id, text: `${label}: add an image.` });
      if (CHOICE_TYPES.has(it.type) || GRID_TYPES.has(it.type)) {
        const values = (it.options || []).map((o) => String(o.value || "").trim());
        if (!values.length) problems.push({ id: it.id, text: `${label}: add at least one option.` });
        if (values.some((v) => !v)) problems.push({ id: it.id, text: `${label}: an option is empty.` });
        if (new Set(values).size !== values.length) problems.push({ id: it.id, text: `${label}: two options are the same.` });
      }
      if (GRID_TYPES.has(it.type)) {
        const rows = (it.rows || []).map((r) => String(r.title || "").trim());
        if (!rows.length) problems.push({ id: it.id, text: `${label}: add at least one row.` });
        if (rows.some((r) => !r)) problems.push({ id: it.id, text: `${label}: a row is empty.` });
      }
      if (it.go_to_section) {
        const values = new Set((it.options || []).map((o) => o.value));
        for (const [answer, target] of Object.entries(it.go_to_section)) {
          if (!values.has(answer)) {
            problems.push({ id: it.id, text: `${label}: a go-to is set for "${answer}", which isn't an option any more.` });
            break;
          }
          if (badTarget(target, sIdx)) {
            problems.push({ id: it.id, text: `${label}: an answer must go to a later section or submit.` });
            break;
          }
        }
      }
      const v = it.validation;
      if (v && !["is_number", "whole_number", "email", "url"].includes(v.rule)) {
        const missing = Array.isArray(v.value) ? v.value.some((x) => x === "" || x == null) : v.value === "" || v.value == null;
        if (missing) problems.push({ id: it.id, text: `${label}: response validation needs a value.` });
      }
    });
  });
  if (!answerable) problems.push({ id: null, text: "Add at least one question staff can answer." });
  return problems;
}

// --- publish diff --------------------------------------------------------------

function itemLabel(it) {
  return it.title ? `"${it.title}"` : TYPE_LABELS[it.type] || it.type;
}

function sectionLabel(s, idx) {
  return s.title ? `"${s.title}"` : `Section ${idx + 1}`;
}

function sameJSON(a, b) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function itemChanges(a, b) {
  const out = [];
  if (a.type !== b.type) out.push(`type ${TYPE_LABELS[a.type] || a.type} → ${TYPE_LABELS[b.type] || b.type}`);
  if ((a.title || "") !== (b.title || "")) out.push("question text");
  if ((a.description || "") !== (b.description || "")) out.push("description");
  if (Boolean(a.required) !== Boolean(b.required)) out.push(b.required ? "now required" : "now optional");
  const av = (a.options || []).map((o) => o.value);
  const bv = (b.options || []).map((o) => o.value);
  const added = bv.filter((v) => !av.includes(v));
  const removed = av.filter((v) => !bv.includes(v));
  if (added.length) out.push(`options added: ${added.join(", ")}`);
  if (removed.length) out.push(`options removed: ${removed.join(", ")}`);
  if (!added.length && !removed.length && av.join("|") !== bv.join("|")) out.push("options reordered");
  const at = Object.fromEntries((a.options || []).map((o) => [o.value, o.issue_tag || null]));
  const flagChanged = (b.options || []).some((o) => o.value in at && at[o.value] !== (o.issue_tag || null));
  if (flagChanged) out.push("issue flags");
  if (!sameJSON(a.rows, b.rows)) out.push("rows");
  if (Boolean(a.other_option) !== Boolean(b.other_option)) out.push(b.other_option ? "\"Other\" added" : "\"Other\" removed");
  if (Boolean(a.shuffle) !== Boolean(b.shuffle)) out.push(b.shuffle ? "shuffled" : "not shuffled");
  if (!sameJSON(a.scale, b.scale)) out.push("scale");
  if (!sameJSON(a.validation, b.validation)) out.push("response validation");
  if (!sameJSON(a.file, b.file)) out.push("upload settings");
  if ((a.image_url || "") !== (b.image_url || "")) out.push("image");
  if (!sameJSON(a.go_to_section, b.go_to_section)) out.push("branching");
  if (Boolean(a.flag_if_answered) !== Boolean(b.flag_if_answered) || (a.flag_tag || null) !== (b.flag_tag || null)) out.push("flag if answered");
  if ((a.unticked_tag || null) !== (b.unticked_tag || null)) out.push("unticked boxes flag");
  if ((a.summary_label || null) !== (b.summary_label || null)) out.push("shown next to Submitted by");
  if (Boolean(a.one_per_column) !== Boolean(b.one_per_column)) out.push("one response per column");
  return out;
}

// Human-readable list of what publishing `next` changes compared to `prev`
// (the current published version; null for a first publish).
export function diffSchemas(prev, next) {
  if (!prev) return ["First version of this checklist."];
  const p = toEditable(prev);
  const n = toEditable(next);
  const lines = [];
  if ((p.title || "") !== (n.title || "")) lines.push(`Title changed to "${n.title}".`);
  if ((p.description || "") !== (n.description || "")) lines.push("Description changed.");
  const pSec = Object.fromEntries(p.sections.map((s, i) => [s.id, { s, i }]));
  const nSec = Object.fromEntries(n.sections.map((s, i) => [s.id, { s, i }]));
  n.sections.forEach((s, i) => {
    if (!pSec[s.id]) lines.push(`Added section ${sectionLabel(s, i)}.`);
    else {
      const old = pSec[s.id].s;
      if ((old.title || "") !== (s.title || "")) lines.push(`Renamed section ${sectionLabel(old, pSec[s.id].i)} to ${sectionLabel(s, i)}.`);
      else if ((old.description || "") !== (s.description || "")) lines.push(`Section ${sectionLabel(s, i)}: description changed.`);
      if ((old.next_section || null) !== (s.next_section || null)) lines.push(`Section ${sectionLabel(s, i)}: "after section" changed.`);
    }
  });
  p.sections.forEach((s, i) => {
    if (!nSec[s.id]) lines.push(`Removed section ${sectionLabel(s, i)}.`);
  });
  const pOrder = p.sections.map((s) => s.id).filter((id) => nSec[id]);
  const nOrder = n.sections.map((s) => s.id).filter((id) => pSec[id]);
  if (pOrder.join("|") !== nOrder.join("|")) lines.push("Sections reordered.");

  const pItems = {};
  p.sections.forEach((s) => (s.items || []).forEach((it, idx) => (pItems[it.id] = { it, s, idx })));
  const nItems = {};
  n.sections.forEach((s) => (s.items || []).forEach((it, idx) => (nItems[it.id] = { it, s, idx })));
  // Reordered = the questions both versions have, per section, aren't in the
  // same relative order (inserting or removing one isn't a reorder).
  let moved = 0;
  for (const s of n.sections) {
    const now = (s.items || []).map((it) => it.id).filter((id) => pItems[id] && pItems[id].s.id === s.id);
    const before = (pSec[s.id]?.s.items || []).map((it) => it.id).filter((id) => now.includes(id));
    if (now.join("|") !== before.join("|")) moved += 1;
  }
  for (const [id, { it, s }] of Object.entries(nItems)) {
    const old = pItems[id];
    if (!old) {
      lines.push(`Added ${TYPE_LABELS[it.type]?.toLowerCase() || it.type} ${itemLabel(it)}${s.title ? ` in "${s.title}"` : ""}.`);
      continue;
    }
    const ch = itemChanges(old.it, it);
    if (ch.length) lines.push(`Changed ${itemLabel(it)}: ${ch.join("; ")}.`);
    if (old.s.id !== s.id) lines.push(`Moved ${itemLabel(it)} to ${s.title ? `"${s.title}"` : "another section"}.`);
  }
  for (const [id, { it }] of Object.entries(pItems)) {
    if (!nItems[id]) lines.push(`Removed ${itemLabel(it)}.`);
  }
  if (moved) lines.push("Questions reordered.");
  if (!sameJSON(p.issues_field, n.issues_field)) lines.push("The \"anything unusual\" field changed.");
  if (!sameJSON(p.confirmation, n.confirmation)) lines.push("The final confirmation changed.");
  return lines.length ? lines : ["No changes to the questions."];
}

// The body PUT /definitions/{type} (save draft) and publish take. `base` is
// the draft_updated_at of the detail being edited (null = it had no draft):
// the server answers 409 when someone else saved, published or discarded
// since. Left out (undefined), the server doesn't check (last write wins).
export function draftPayload(draft, { base } = {}) {
  const body = {
    sections: draft.sections,
    title: draft.title || "",
    description: draft.description || null,
  };
  if (draft.legacy_order) body.legacy_order = true;
  if (base !== undefined) body.base_draft_updated_at = base ?? null;
  return body;
}
