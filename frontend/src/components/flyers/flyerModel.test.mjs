// node --test src/components/flyers/flyerModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  fileKind,
  fileProblem,
  formProblems,
  formatBytes,
  groupByBatch,
  groupProblem,
  latestSendByGroup,
  parseWhatsApp,
  sendSummary,
  titleFromFileName,
  withMetaDefaults,
} from "./flyerModel.js";

const file = (name, type, size = 1000) => ({ name, type, size });

test("file kind comes from the type, else the extension (HEIC often has no type)", () => {
  assert.equal(fileKind(file("a.jpg", "image/jpeg")), "image");
  assert.equal(fileKind(file("a.pdf", "application/pdf")), "pdf");
  assert.equal(fileKind(file("IMG_1.HEIC", "")), "image");
  assert.equal(fileKind(file("menu.PDF", "")), "pdf");
  assert.equal(fileKind(file("a.gif", "image/gif")), null);
  assert.equal(fileKind(file("notes.txt", "text/plain")), null);
  assert.equal(fileKind(null), null);
});

test("size limits follow the meta: 25 MB pictures, 20 MB PDFs", () => {
  const mb = 1024 * 1024;
  assert.equal(fileProblem(file("a.jpg", "image/jpeg", 25 * mb), {}), null);
  assert.match(fileProblem(file("a.jpg", "image/jpeg", 25 * mb + 1), {}), /most is 25 MB/);
  assert.match(fileProblem(file("a.pdf", "application/pdf", 21 * mb), {}), /most is 20 MB/);
  assert.match(fileProblem(file("a.pdf", "application/pdf", 3 * mb), { max_pdf_mb: 2 }), /most is 2 MB/);
  assert.match(fileProblem(file("a.jpg", "image/jpeg", 0), {}), /empty/);
  assert.match(fileProblem(null, {}), /Choose/);
});

test("title is prefilled from the file name", () => {
  assert.equal(titleFromFileName("diwali_garlands-final.JPG"), "Diwali garlands-final");
  assert.equal(titleFromFileName("IMG_0082.PNG"), "IMG 0082");
  assert.equal(titleFromFileName("  weekend   sale .pdf"), "Weekend sale");
  assert.equal(titleFromFileName(".jpg"), "");
  assert.equal(titleFromFileName(`${"x".repeat(200)}.jpg`).length, 120);
});

test("form problems name each missing field", () => {
  const ok = { file: file("a.jpg", "image/jpeg"), title: "Sale", category: "groceries", caption: "" };
  assert.deepEqual(formProblems(ok, {}), {});
  const bad = formProblems({ file: null, title: "  ", category: "", caption: "x".repeat(1001) }, {});
  assert.deepEqual(Object.keys(bad).sort(), ["caption", "category", "file", "title"]);
});

test("group picks: at least one, at most max_groups_per_send", () => {
  assert.match(groupProblem([], {}), /at least one/);
  assert.equal(groupProblem(["a"], {}), null);
  assert.match(groupProblem(["a", "b", "c"], { max_groups_per_send: 2 }), /up to 2/);
});

test("meta defaults fill what the server leaves out", () => {
  const m = withMetaDefaults({ groups: [{ key: "g", label: "G" }] });
  assert.equal(m.categories.length, 6);
  assert.equal(m.max_groups_per_send, 25);
  assert.equal(m.groups.length, 1);
});

test("card summary from send_counts", () => {
  assert.equal(sendSummary(null).text, "Not sent yet");
  assert.equal(sendSummary({ sent: 1, failed: 0, pending: 0 }).text, "Sent to 1 group");
  assert.equal(sendSummary({ sent: 3, failed: 0, pending: 0 }).text, "Sent to 3 groups");
  assert.equal(sendSummary({ sent: 2, failed: 1, pending: 0 }).text, "2 sent, 1 failed");
  assert.equal(sendSummary({ sent: 2, failed: 1, pending: 2 }).pill, "pill-info");
});

test("sends group by batch, newest batch first, in send order inside", () => {
  const sends = [
    { id: 5, batch_id: "b2", target_key: "x", status: "queued", requested_at: "2026-10-04T15:00:00Z" },
    { id: 4, batch_id: "b2", target_key: "y", status: "sent", requested_at: "2026-10-04T15:00:00Z" },
    { id: 2, batch_id: "b1", target_key: "x", status: "failed", requested_at: "2026-10-03T15:00:00Z" },
  ];
  const batches = groupByBatch(sends);
  assert.deepEqual(batches.map((b) => b.key), ["b2", "b1"]);
  assert.deepEqual(batches[0].sends.map((s) => s.id), [4, 5]);
  const latest = latestSendByGroup(sends);
  assert.equal(latest.x.id, 5);
  assert.equal(latest.y.id, 4);
});

test("bytes read like a person would say them", () => {
  assert.equal(formatBytes(500), "500 B");
  assert.equal(formatBytes(2048), "2 KB");
  assert.equal(formatBytes(1.5 * 1024 * 1024), "1.5 MB");
  assert.equal(formatBytes(12 * 1024 * 1024), "12 MB");
  assert.equal(formatBytes(null), "");
});

test("WhatsApp formatting preview", () => {
  assert.deepEqual(parseWhatsApp("Fresh *garlands* today"), [
    { text: "Fresh " },
    { text: "garlands", bold: true },
    { text: " today" },
  ]);
  assert.deepEqual(parseWhatsApp("*_both_*"), [{ text: "both", bold: true, italic: true }]);
  assert.deepEqual(parseWhatsApp("~old~ ```$5```"), [
    { text: "old", strike: true },
    { text: " " },
    { text: "$5", mono: true },
  ]);
  // Not formatting: inside words, a space after the opening mark, no close.
  assert.deepEqual(parseWhatsApp("2*3*4 snake_case_name"), [{ text: "2*3*4 snake_case_name" }]);
  assert.deepEqual(parseWhatsApp("* not bold*"), [{ text: "* not bold*" }]);
  assert.deepEqual(parseWhatsApp("*open"), [{ text: "*open" }]);
  // Bold doesn't run across lines.
  assert.deepEqual(parseWhatsApp("*a\nb*"), [{ text: "*a\nb*" }]);
  assert.deepEqual(parseWhatsApp(""), []);
});

test("short store-local dates and times", async () => {
  const { shortDay, shortDayTime } = await import("./flyerModel.js");
  const now = new Date("2026-10-04T16:00:00Z");
  // 01:30 UTC on Oct 5 is still Oct 4 in New York.
  assert.equal(shortDay("2026-10-05T01:30:00Z", now), "Oct 4");
  assert.equal(shortDay("2025-12-31T18:00:00Z", now), "Dec 31, 2025");
  assert.equal(shortDayTime("2026-10-04T13:31:00Z", now), "Oct 4, 9:31 AM");
  assert.equal(shortDay(null, now), "");
  assert.equal(shortDay("not a date", now), "");
});
