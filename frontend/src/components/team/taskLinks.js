// Team task drawer: a lead's daily Fix Issues task (W4, source_type
// "issue_leads", source_id "<user id>:<store day>") opens that lead's list,
// like the app's "Open the Fix Issues list" button. Node tests:
// node --test src/components/team/taskLinks.test.mjs

// The lead's user id, or null for any other task.
export function fixIssuesLeadId(task) {
  if (!task || task.source_type !== "issue_leads") return null;
  const m = /^(\d+):/.exec(String(task.source_id || ""));
  return m ? Number(m[1]) : null;
}

// The in-app link for it, or null.
export function fixIssuesLeadPath(task) {
  const id = fixIssuesLeadId(task);
  return id == null ? null : `/fix-issues?lead=${id}`;
}
