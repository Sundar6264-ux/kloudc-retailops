// Pure rules for the Tasks page's Oct 15 features: the drawer's Edit /
// Reassign / steps editor, empty-team warnings, lead-task progress, the main
// action button, filters, bulk results and "My team today". No React, no
// fetch: node --test src/components/team/taskEditModel.test.mjs

import { fixIssuesLeadPath } from "./taskLinks.js";
import { storeDateTimeFormat } from "../../format.js";

// --- Teams ------------------------------------------------------------------------

// A named team nobody is in: a task for it reaches nobody (the server refuses
// it). Everyone and teams from an older server (no member_count) are fine.
export function isEmptyTeam(team) {
  return !!team && !team.is_everyone && team.member_count === 0;
}

export function teamOptionLabel(team) {
  return isEmptyTeam(team) ? `${team.name} (no members)` : team.name;
}

// The warning under a team picker, or null.
export function emptyTeamWarning(team) {
  if (!isEmptyTeam(team)) return null;
  return `${team.name} has no members yet, so nobody would get this task. Add people on the Teams page, or pick a person.`;
}

// --- Edit form ----------------------------------------------------------------------

// "2026-10-14T15:00" (store wall time, for <input type="datetime-local">) from
// the task's due_at_local ("2026-10-14T15:00:00-04:00"), or "".
export function dueInputFromTask(task) {
  const local = task && task.due_at_local;
  if (local && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(local)) return local.slice(0, 16);
  if (!task || !task.due_at) return "";
  const parts = Object.fromEntries(
    storeDateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(task.due_at))
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

// "user:5" / "team:3" for a task's assignee.
export function assigneeValue(task) {
  if (!task) return "";
  if (task.assignee_user_id != null) return `user:${task.assignee_user_id}`;
  if (task.assignee_team_id != null) return `team:${task.assignee_team_id}`;
  const a = task.assignee;
  if (a && a.id != null) return `${a.type === "team" ? "team" : "user"}:${a.id}`;
  return "";
}

export function editFormFromTask(task) {
  return {
    title: task.title || "",
    description: task.description || "",
    due: dueInputFromTask(task),
    priority: task.priority || "normal",
    requires: task.requires || "none",
    assignee: assigneeValue(task),
    items: (task.items || []).map((i) => ({ id: i.id, text: i.text })),
  };
}

function cleanSteps(items) {
  return (items || []).map((i) => ({ ...i, text: (i.text || "").trim() })).filter((i) => i.text);
}

function sameSteps(a, b) {
  if (a.length !== b.length) return false;
  return a.every((x, n) => x.id === b[n].id && x.text === b[n].text);
}

// What Save sends: {patch, items, error}. `patch` = PATCH /tasks/{id} body
// (only what changed, plus expected_updated_at) or null; `items` = PUT
// /tasks/{id}/items list or null when the steps didn't change.
export function buildTaskEdit(task, form, { canAssign = false, canEditItems = false } = {}) {
  const patch = {};
  const title = (form.title || "").trim();
  if (!title) return { patch: null, items: null, error: "The title can't be empty." };
  if (title !== task.title) patch.title = title;
  const description = (form.description || "").trim();
  if (description !== (task.description || "")) patch.description = description || null;
  if ((form.due || "") !== dueInputFromTask(task)) {
    patch.due_at = form.due ? (form.due.length === 16 ? `${form.due}:00` : form.due) : null;
  }
  if (form.priority && form.priority !== (task.priority || "normal")) patch.priority = form.priority;
  if (form.requires && form.requires !== (task.requires || "none")) patch.requires = form.requires;
  if (canAssign && form.assignee && form.assignee !== assigneeValue(task)) {
    const [kind, id] = form.assignee.split(":");
    if (kind === "team") patch.assignee_team_id = Number(id);
    else patch.assignee_user_id = Number(id);
  }
  let items = null;
  if (canEditItems) {
    const before = (task.items || []).map((i) => ({ id: i.id, text: i.text }));
    const after = cleanSteps(form.items).map((i) => (i.id != null ? { id: i.id, text: i.text } : { text: i.text }));
    if (after.length > 30) return { patch: null, items: null, error: "A task can have at most 30 steps." };
    if (!sameSteps(before, after)) items = after;
  }
  const hasPatch = Object.keys(patch).length > 0;
  if (hasPatch && task.updated_at) patch.expected_updated_at = task.updated_at;
  return { patch: hasPatch ? patch : null, items, error: null };
}

// --- Write responses and 409s ---------------------------------------------------

// A full task (detail) from a write's response, or null (e.g. a comment's
// response, or an older server).
export function taskFromResponse(res, taskId) {
  if (res && res.id === taskId && Array.isArray(res.events)) return res;
  if (res && res.task && res.task.id === taskId && Array.isArray(res.task.events)) return res.task;
  return null;
}

// The current task a 409 carries ({"detail", "task"}), or null.
export function taskFromConflict(err) {
  if (!err || err.status !== 409) return null;
  const t = err.body && err.body.task;
  return t && t.id != null ? t : null;
}

// --- Progress and the main action -------------------------------------------------

// {done, total, left, pct} for a task that tracks a count, else null.
export function taskProgress(task) {
  const p = task && task.progress;
  if (!p || !p.total) return null;
  const total = Math.max(0, Number(p.total) || 0);
  const done = Math.min(total, Math.max(0, Number(p.done) || 0));
  return { done, total, left: total - done, pct: total ? Math.round((done / total) * 100) : 0 };
}

export function progressLabel(task) {
  const p = taskProgress(task);
  return p ? `${p.left} left of ${p.total}` : null;
}

// The label for a list row / card, unless the title already says it (lead
// tasks are titled "Fix Issues: N left of M").
export function progressBadge(task) {
  const label = progressLabel(task);
  return label && !String(task.title || "").includes(label) ? label : null;
}

// Where the task's real work is ({label, path}) while it's open, or null. The
// source (checklist, order, invoice, Fix Issues) closes the task by itself.
export function primaryAction(task) {
  if (!task || task.status !== "open") return null;
  const a = task.action;
  if (a && a.web_path) return { label: a.label || "Open", path: a.web_path };
  const lead = fixIssuesLeadPath(task);
  return lead ? { label: "Open the Fix Issues list", path: lead } : null;
}

// --- Filters -------------------------------------------------------------------------

export const SOURCE_FILTERS = [
  { key: "", label: "All tasks" },
  { key: "manual", label: "Made by people" },
  { key: "auto", label: "Automatic" },
];

export function hasFilters(f) {
  return !!(f && (f.person || f.source || (f.q || "").trim()));
}

// --- Bulk ----------------------------------------------------------------------------

// "Done: 3. Skipped 1 - needs a photo (Clean cooler)."
export function bulkResultText(res, action, titles = new Map()) {
  const doneIds = res.done || res[action] || [];
  const verb = action === "cancel" ? "Cancelled" : "Marked done";
  const parts = [`${verb}: ${doneIds.length}.`];
  const skipped = res.skipped || [];
  if (skipped.length) {
    const why = skipped
      .slice(0, 3)
      .map((s) => `${titles.get(s.id) || `#${s.id}`} - ${s.reason}`)
      .join("; ");
    parts.push(`Skipped ${skipped.length}: ${why}${skipped.length > 3 ? "; …" : ""}.`);
  }
  return parts.join(" ");
}

// Toggles one id in a Set (returns a new Set).
export function toggleId(selected, id) {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

// Keeps only the selected ids still shown as open (after a reload).
export function pruneSelection(selected, openTasks) {
  const live = new Set((openTasks || []).map((t) => t.id));
  const next = new Set([...selected].filter((id) => live.has(id)));
  return next.size === selected.size ? selected : next;
}

// --- My team today ---------------------------------------------------------------

// People worth a chip: anyone with something open or finished today, most
// overdue first (the server's order), at most `max`.
export function summaryPeople(summary, max = 12) {
  return ((summary && summary.people) || [])
    .filter((p) => p.open || p.done_today || p.cant_do_today)
    .slice(0, max);
}

export function personChipText(p) {
  const bits = [`${p.open} open`];
  if (p.overdue) bits.push(`${p.overdue} overdue`);
  if (p.done_today) bits.push(`${p.done_today} done`);
  if (p.cant_do_today) bits.push(`${p.cant_do_today} can't do`);
  return bits.join(" · ");
}
