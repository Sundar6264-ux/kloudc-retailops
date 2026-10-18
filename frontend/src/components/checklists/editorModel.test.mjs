// node --test src/components/checklists/editorModel.test.mjs (no deps).
// The save / publish body carries the draft base for the server's 409.
import assert from "node:assert/strict";
import test from "node:test";

import { draftPayload } from "./editorModel.js";

const draft = { title: "T", description: "", sections: [{ id: "s1", title: null, description: null, items: [] }] };

test("without a base the body is unchanged (server: last write wins)", () => {
  assert.deepEqual(draftPayload(draft), { sections: draft.sections, title: "T", description: null });
  assert.equal("base_draft_updated_at" in draftPayload(draft), false);
});

test("a base is sent as is; no draft is sent as null", () => {
  const at = "2026-10-01T12:39:05.865727+00:00";
  assert.equal(draftPayload(draft, { base: at }).base_draft_updated_at, at);
  const none = draftPayload(draft, { base: null });
  assert.equal("base_draft_updated_at" in none, true);
  assert.equal(none.base_draft_updated_at, null);
});

test("legacy_order still rides along", () => {
  assert.equal(draftPayload({ ...draft, legacy_order: true }, { base: null }).legacy_order, true);
});
