// node --test src/components/team/taskEditModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  assigneeValue,
  buildTaskEdit,
  bulkResultText,
  dueInputFromTask,
  editFormFromTask,
  emptyTeamWarning,
  hasFilters,
  isEmptyTeam,
  personChipText,
  primaryAction,
  progressBadge,
  progressLabel,
  pruneSelection,
  summaryPeople,
  taskFromConflict,
  taskFromResponse,
  taskProgress,
  teamOptionLabel,
  toggleId,
} from "./taskEditModel.js";

const task = (over = {}) => ({
  id: 7,
  title: "Clean cooler",
  description: "Back shelf",
  priority: "normal",
  requires: "none",
  status: "open",
  assignee: { type: "user", id: 9, name: "Harshitha" },
  assignee_user_id: 9,
  assignee_team_id: null,
  due_at: "2026-10-14T19:00:00+00:00",
  due_at_local: "2026-10-14T15:00:00-04:00",
  updated_at: "2026-10-14T18:00:00.123456+00:00",
  items: [
    { id: 1, text: "Empty", done: true },
    { id: 2, text: "Wipe", done: false },
  ],
  ...over,
});

test("empty teams are flagged in pickers; Everyone and older servers are not", () => {
  assert.equal(isEmptyTeam({ name: "Kitchen", member_count: 0 }), true);
  assert.equal(isEmptyTeam({ name: "Review", member_count: 2 }), false);
  assert.equal(isEmptyTeam({ name: "Everyone", is_everyone: true, member_count: 0 }), false);
  assert.equal(isEmptyTeam({ name: "Old" }), false);
  assert.equal(teamOptionLabel({ name: "Kitchen", member_count: 0 }), "Kitchen (no members)");
  assert.equal(teamOptionLabel({ name: "Review", member_count: 2 }), "Review");
  assert.match(emptyTeamWarning({ name: "Kitchen", member_count: 0 }), /Kitchen has no members yet/);
  assert.equal(emptyTeamWarning({ name: "Review", member_count: 2 }), null);
  assert.equal(emptyTeamWarning(null), null);
});

test("due input is store wall time from due_at_local, or computed from due_at", () => {
  assert.equal(dueInputFromTask(task()), "2026-10-14T15:00");
  assert.equal(dueInputFromTask(task({ due_at_local: null })), "2026-10-14T15:00");
  // Winter (EST, -05:00).
  assert.equal(dueInputFromTask(task({ due_at_local: null, due_at: "2026-12-01T17:30:00Z" })), "2026-12-01T12:30");
  assert.equal(dueInputFromTask(task({ due_at: null, due_at_local: null })), "");
});

test("assignee value for a person, a team, or only the nested assignee", () => {
  assert.equal(assigneeValue(task()), "user:9");
  assert.equal(assigneeValue(task({ assignee_user_id: null, assignee_team_id: 3 })), "team:3");
  assert.equal(
    assigneeValue({ assignee: { type: "team", id: 4 } }),
    "team:4",
  );
});

test("an unchanged form sends nothing", () => {
  const t = task();
  const out = buildTaskEdit(t, editFormFromTask(t), { canAssign: true, canEditItems: true });
  assert.deepEqual(out, { patch: null, items: null, error: null });
});

test("only changed fields are sent, with expected_updated_at", () => {
  const t = task();
  const form = { ...editFormFromTask(t), title: "  Clean the cooler ", due: "2026-10-15T09:30", priority: "urgent" };
  const { patch, items } = buildTaskEdit(t, form);
  assert.deepEqual(patch, {
    title: "Clean the cooler",
    due_at: "2026-10-15T09:30:00",
    priority: "urgent",
    expected_updated_at: t.updated_at,
  });
  assert.equal(items, null);
});

test("clearing details and the due time sends nulls", () => {
  const t = task();
  const { patch } = buildTaskEdit(t, { ...editFormFromTask(t), description: " ", due: "" });
  assert.equal(patch.description, null);
  assert.equal(patch.due_at, null);
});

test("an empty title is refused before any request", () => {
  const t = task();
  const out = buildTaskEdit(t, { ...editFormFromTask(t), title: "   " });
  assert.equal(out.patch, null);
  assert.match(out.error, /title/);
});

test("reassign only for assigners, to a person or a team", () => {
  const t = task();
  const toTeam = { ...editFormFromTask(t), assignee: "team:3" };
  assert.equal(buildTaskEdit(t, toTeam, { canAssign: false }).patch, null);
  assert.deepEqual(buildTaskEdit(t, toTeam, { canAssign: true }).patch, {
    assignee_team_id: 3,
    expected_updated_at: t.updated_at,
  });
  const toPerson = { ...editFormFromTask(t), assignee: "user:12" };
  assert.equal(buildTaskEdit(t, toPerson, { canAssign: true }).patch.assignee_user_id, 12);
});

test("steps: rename, add, remove and reorder go as one list; blanks dropped", () => {
  const t = task();
  const form = {
    ...editFormFromTask(t),
    items: [{ id: 2, text: "Wipe well" }, { text: "  " }, { text: "Restock" }],
  };
  const { patch, items } = buildTaskEdit(t, form, { canEditItems: true });
  assert.equal(patch, null);
  assert.deepEqual(items, [{ id: 2, text: "Wipe well" }, { text: "Restock" }]);
  // Without the right to edit steps, they're never sent.
  assert.equal(buildTaskEdit(t, form, { canEditItems: false }).items, null);
});

test("more than 30 steps is refused", () => {
  const t = task({ items: [] });
  const form = { ...editFormFromTask(t), items: Array.from({ length: 31 }, (_, n) => ({ text: `s${n}` })) };
  assert.match(buildTaskEdit(t, form, { canEditItems: true }).error, /30/);
});

test("task from a write response or a 409", () => {
  const full = { id: 7, events: [] };
  assert.equal(taskFromResponse(full, 7), full);
  assert.equal(taskFromResponse({ id: 7 }, 7), null); // list shape: reload instead
  assert.equal(taskFromResponse({ id: 8, events: [] }, 7), null);
  assert.equal(taskFromResponse({ task: full }, 7), full);
  assert.equal(taskFromResponse(null, 7), null);
  const err = Object.assign(new Error("Someone else already finished this task"), { status: 409, body: { task: { id: 7 } } });
  assert.deepEqual(taskFromConflict(err), { id: 7 });
  assert.equal(taskFromConflict(Object.assign(new Error("x"), { status: 409, body: { detail: "x" } })), null);
  assert.equal(taskFromConflict(Object.assign(new Error("x"), { status: 400, body: { task: { id: 7 } } })), null);
});

test("lead progress: N left of M", () => {
  const t = task({ progress: { done: 4, total: 611, left: 607 } });
  assert.deepEqual(taskProgress(t), { done: 4, total: 611, left: 607, pct: 1 });
  assert.equal(progressLabel(t), "607 left of 611");
  assert.equal(taskProgress(task({ progress: null })), null);
  assert.equal(taskProgress(task({ progress: { done: 0, total: 0, left: 0 } })), null);
  assert.equal(taskProgress(task({ progress: { done: 9, total: 5 } })).left, 0);
  // Not repeated next to a title that already says it.
  assert.equal(progressBadge({ ...t, title: "Fix Issues: 607 left of 611" }), null);
  assert.equal(progressBadge({ ...t, title: "Fix Issues: 611 open items for you" }), "607 left of 611");
});

test("main action goes to the real place while open", () => {
  const lead = task({
    action: { kind: "fix_issues", label: "Open the Fix Issues list", route: "fix_issues", web_path: "/fix-issues?lead=12" },
  });
  assert.deepEqual(primaryAction(lead), { label: "Open the Fix Issues list", path: "/fix-issues?lead=12" });
  assert.equal(primaryAction({ ...lead, status: "done" }), null);
  // Older server: no `action`, but a lead task is still recognised.
  assert.deepEqual(primaryAction(task({ source_type: "issue_leads", source_id: "12:2026-10-14" })), {
    label: "Open the Fix Issues list",
    path: "/fix-issues?lead=12",
  });
  assert.equal(primaryAction(task()), null);
});

test("filters, selection and bulk wording", () => {
  assert.equal(hasFilters({ person: "", source: "", q: "  " }), false);
  assert.equal(hasFilters({ q: "cooler" }), true);
  assert.equal(hasFilters({ source: "auto" }), true);
  let s = toggleId(new Set(), 3);
  s = toggleId(s, 4);
  s = toggleId(s, 3);
  assert.deepEqual([...s], [4]);
  const sel = new Set([1, 2]);
  assert.equal(pruneSelection(sel, [{ id: 1 }, { id: 2 }]), sel);
  assert.deepEqual([...pruneSelection(sel, [{ id: 2 }])], [2]);
  const titles = new Map([[2, "Clean cooler"]]);
  assert.equal(
    bulkResultText({ done: [1, 3], skipped: [{ id: 2, reason: "needs a photo" }] }, "done", titles),
    "Marked done: 2. Skipped 1: Clean cooler - needs a photo.",
  );
  assert.equal(bulkResultText({ done: [5], skipped: [] }, "cancel"), "Cancelled: 1.");
});

test("my team today: people with something to show, chip wording", () => {
  const summary = {
    people: [
      { user_id: 1, name: "A", open: 3, overdue: 2, due_today: 1, done_today: 1, cant_do_today: 0 },
      { user_id: 2, name: "B", open: 0, overdue: 0, due_today: 0, done_today: 0, cant_do_today: 0 },
      { user_id: 3, name: "C", open: 0, overdue: 0, due_today: 0, done_today: 2, cant_do_today: 1 },
    ],
  };
  assert.deepEqual(
    summaryPeople(summary).map((p) => p.user_id),
    [1, 3],
  );
  assert.equal(personChipText(summary.people[0]), "3 open · 2 overdue · 1 done");
  assert.equal(personChipText(summary.people[2]), "0 open · 2 done · 1 can't do");
  assert.deepEqual(summaryPeople(null), []);
});
