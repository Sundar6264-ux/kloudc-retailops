// node --test src/components/team/taskLinks.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { fixIssuesLeadId, fixIssuesLeadPath } from "./taskLinks.js";

test("a lead's daily Fix Issues task links to that lead's list", () => {
  const t = { source_type: "issue_leads", source_id: "12:2026-10-14" };
  assert.equal(fixIssuesLeadId(t), 12);
  assert.equal(fixIssuesLeadPath(t), "/fix-issues?lead=12");
});

test("any other task, or a malformed id, has no link", () => {
  assert.equal(fixIssuesLeadPath({ source_type: "template", source_id: "12:2026-10-14" }), null);
  assert.equal(fixIssuesLeadPath({ source_type: "issue_leads", source_id: "x:2026-10-14" }), null);
  assert.equal(fixIssuesLeadPath({ source_type: "issue_leads", source_id: null }), null);
  assert.equal(fixIssuesLeadPath(null), null);
});
