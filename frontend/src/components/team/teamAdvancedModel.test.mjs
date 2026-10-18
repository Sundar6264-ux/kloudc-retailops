import test from "node:test";
import assert from "node:assert/strict";
import {
  attachmentOf,
  boardColumnOf,
  boardMove,
  checkDocument,
  dayAssigneesFromApi,
  dayAssigneesToApi,
  displayBody,
  formatBytes,
  formatDuration,
  formatMinutes,
  groupBoard,
  highlightParts,
  messagePreview,
  pct,
  pickVoiceMime,
  seenSummary,
  stepsFromText,
} from "./teamAdvancedModel.js";

const t = (over) => ({ id: 1, status: "open", requires: "none", can: {}, ...over });

test("board columns, including older servers without board_column", () => {
  assert.equal(boardColumnOf(t({ board_column: "in_progress" })), "in_progress");
  assert.equal(boardColumnOf(t({ started_at: "2026-10-14T10:00:00Z" })), "in_progress");
  assert.equal(boardColumnOf(t({})), "todo");
  assert.equal(boardColumnOf(t({ status: "cant_do", board_column: "cant_do" })), "done");
  assert.equal(boardColumnOf(t({ status: "done" })), "done");
  const cols = groupBoard(
    [t({ id: 1 }), t({ id: 2, board_column: "in_progress" })],
    [t({ id: 3, status: "done", completed_at: "2026-10-01" }), t({ id: 4, status: "done", completed_at: "2026-10-05" })],
  );
  assert.deepEqual(cols.todo.map((x) => x.id), [1]);
  assert.deepEqual(cols.in_progress.map((x) => x.id), [2]);
  assert.deepEqual(cols.done.map((x) => x.id), [4, 3]);
});

test("board moves follow what the server says the user can do", () => {
  assert.deepEqual(boardMove(t({ can: { start: true } }), "in_progress"), { action: "start" });
  assert.equal(boardMove(t({ can: {} }), "in_progress").action, null);
  assert.deepEqual(boardMove(t({ board_column: "in_progress", can: { unstart: true } }), "todo"), { action: "unstart" });
  assert.deepEqual(boardMove(t({ can: { complete: true } }), "done"), { action: "complete" });
  assert.deepEqual(boardMove(t({ requires: "photo", can: { complete: true } }), "done"), { action: "complete_form" });
  assert.deepEqual(boardMove(t({ status: "done", can: { reopen: true } }), "todo"), { action: "reopen" });
  assert.equal(boardMove(t({ status: "done", can: { reopen: true } }), "in_progress").action, null);
  assert.equal(boardMove(t({}), "todo").action, null);
});

test("stats formatting", () => {
  assert.equal(pct(null), "-");
  assert.equal(pct(0.667), "67%");
  assert.equal(formatMinutes(null), "-");
  assert.equal(formatMinutes(45), "45 min");
  assert.equal(formatMinutes(95), "1 h 35 min");
  assert.equal(formatMinutes(120), "2 h");
  assert.equal(formatMinutes(2900), "2 d 0 h");
});

test("template weekday assignees and steps", () => {
  assert.deepEqual(
    dayAssigneesToApi([
      { day: "SA", who: "user:3" },
      { day: "SU", who: "team:2" },
      { day: "MO", who: "" },
      { day: "TU", who: "user:x" },
    ]),
    { SA: { user_id: 3 }, SU: { team_id: 2 } },
  );
  assert.deepEqual(dayAssigneesFromApi([{ day: "SA", type: "user", id: 3 }]), { SA: "user:3" });
  assert.deepEqual(stepsFromText(" Lights on \n\nCount till\n"), ["Lights on", "Count till"]);
});

test("attachments, labels and previews", () => {
  const voice = { body: "Voice message (0:12)", body_is_label: true, attachment: { kind: "audio", duration_ms: 12400 } };
  assert.equal(displayBody(voice), "");
  assert.equal(messagePreview(voice), "Voice message (0:12)");
  const doc = { body: "", attachment: { kind: "document", name: "Invoice.pdf" } };
  assert.equal(messagePreview(doc), "Document: Invoice.pdf");
  assert.deepEqual(attachmentOf({ media: { url: "/x" } }), { url: "/x", kind: "image" });
  assert.equal(messagePreview({ media: { url: "/x" } }), "Photo");
  assert.equal(messagePreview({ deleted: true }), "Message deleted");
  assert.equal(messagePreview({ body: "hi", attachment: { kind: "audio" } }), "hi");
  assert.equal(formatDuration(65000), "1:05");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(3 * 1024 * 1024), "3.0 MB");
});

test("voice recording type and document checks", () => {
  assert.equal(pickVoiceMime((x) => x === "audio/mp4"), "audio/mp4");
  assert.equal(pickVoiceMime((x) => x.startsWith("audio/webm")), null);
  assert.equal(pickVoiceMime(undefined), null);
  assert.equal(pickVoiceMime(() => { throw new Error("x"); }), null);
  assert.equal(checkDocument({ name: "a.pdf", type: "application/pdf", size: 10 }), null);
  assert.match(checkDocument({ name: "a.docx", type: "application/msword", size: 10 }), /Only PDF/);
  assert.match(checkDocument({ name: "a.pdf", type: "", size: 11 * 1024 * 1024 }), /too large/);
});

test("seen summary and search highlighting", () => {
  assert.equal(seenSummary({ total: 3, seen: [{}, {}], not_seen: [{}] }), "Seen by 2 of 3");
  assert.equal(seenSummary({ total: 0, seen: [], not_seen: [] }), "");
  assert.deepEqual(highlightParts("Rice and rice", "RICE"), [
    { text: "Rice", match: true },
    { text: " and ", match: false },
    { text: "rice", match: true },
  ]);
  assert.deepEqual(highlightParts("abc", ""), [{ text: "abc", match: false }]);
});
