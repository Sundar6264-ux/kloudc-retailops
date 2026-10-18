// Pure rules for the Team tab's W5 features (board, stats, templates, chat
// attachments) - no React, no fetch, so node --test can check them.

// --- Task board ---------------------------------------------------------------

export const BOARD_COLUMNS = [
  { key: "todo", label: "To do" },
  { key: "in_progress", label: "In progress" },
  { key: "done", label: "Done" },
];

// Older servers have no board_column: open = To do.
export function boardColumnOf(task) {
  if (task.board_column) return task.board_column === "cant_do" || task.board_column === "cancelled" ? "done" : task.board_column;
  if (task.status === "open") return task.started_at ? "in_progress" : "todo";
  return "done";
}

// Splits open + finished tasks into the board's three columns. Open ones keep
// the server's order (soonest due first); finished ones newest first.
export function groupBoard(openTasks, finishedTasks) {
  const cols = { todo: [], in_progress: [], done: [] };
  for (const t of openTasks || []) cols[boardColumnOf(t)].push(t);
  const finished = [...(finishedTasks || [])].sort((a, b) =>
    String(b.completed_at || b.updated_at || "").localeCompare(String(a.completed_at || a.updated_at || "")),
  );
  for (const t of finished) cols.done.push(t);
  return cols;
}

// What moving `task` to column `to` means:
//   {action: "start" | "unstart" | "complete" | "complete_form" | "reopen"}
// or {action: null, reason} when it can't be moved there.
export function boardMove(task, to) {
  const from = boardColumnOf(task);
  if (from === to) return { action: null, reason: "" };
  const can = task.can || {};
  if (from === "todo" && to === "in_progress") {
    return can.start ? { action: "start" } : { action: null, reason: "Only the person (or team) it's for can start it." };
  }
  if (from === "in_progress" && to === "todo") {
    return can.unstart ? { action: "unstart" } : { action: null, reason: "Only the person (or team) it's for can move it back." };
  }
  if (to === "done") {
    if (!can.complete) return { action: null, reason: "Only the person (or team) it's for can finish it." };
    return task.requires && task.requires !== "none" ? { action: "complete_form" } : { action: "complete" };
  }
  if (from === "done" && to === "todo") {
    return can.reopen ? { action: "reopen" } : { action: null, reason: "Only whoever assigned or finished it can reopen it." };
  }
  if (from === "done" && to === "in_progress") {
    return { action: null, reason: "Reopen it first (drag it to To do)." };
  }
  return { action: null, reason: "" };
}

// --- Stats ----------------------------------------------------------------------

export function pct(rate) {
  if (rate === null || rate === undefined) return "-";
  return `${Math.round(rate * 100)}%`;
}

// 95 -> "1 h 35 min", 2900 -> "2 d 0 h".
export function formatMinutes(minutes) {
  if (minutes === null || minutes === undefined) return "-";
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m} min`;
  if (m < 24 * 60) {
    const h = Math.floor(m / 60);
    const rest = m % 60;
    return rest ? `${h} h ${rest} min` : `${h} h`;
  }
  const d = Math.floor(m / (24 * 60));
  return `${d} d ${Math.floor((m % (24 * 60)) / 60)} h`;
}

// --- Templates --------------------------------------------------------------------

// The template form's weekday rows -> the API's day_assignees object.
// rows: [{day: "SA", who: "user:3" | "team:2" | ""}]
export function dayAssigneesToApi(rows) {
  const out = {};
  for (const r of rows || []) {
    if (!r.who) continue;
    const [type, id] = r.who.split(":");
    const n = parseInt(id, 10);
    if (!Number.isInteger(n)) continue;
    out[r.day] = type === "team" ? { team_id: n } : { user_id: n };
  }
  return out;
}

export function dayAssigneesFromApi(list) {
  const byDay = {};
  for (const d of list || []) byDay[d.day] = `${d.type}:${d.id}`;
  return byDay;
}

// Steps typed one per line -> list (blank lines dropped).
export function stepsFromText(text) {
  return (text || "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

// --- Chat attachments -----------------------------------------------------------

// The attachment of a message: `attachment` (newer servers, any kind) or the
// photo in `media` (older ones).
export function attachmentOf(message) {
  if (message.attachment) return message.attachment;
  if (message.media) return { ...message.media, kind: "image" };
  return null;
}

// The text to draw: none when the server only put a label there for older apps.
export function displayBody(message) {
  if (message.body_is_label) return "";
  return message.body || "";
}

// 12400 -> "0:12"
export function formatDuration(ms) {
  const s = Math.max(0, Math.round((ms || 0) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// 1536 -> "1.5 KB"
export function formatBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// What a channel list / search result says for a message.
export function messagePreview(message) {
  if (message.deleted) return "Message deleted";
  const text = displayBody(message).trim();
  if (text) return text;
  const a = attachmentOf(message);
  if (!a) return "";
  if (a.kind === "audio") return `Voice message (${formatDuration(a.duration_ms)})`;
  if (a.kind === "document") return `Document: ${a.name || "document.pdf"}`;
  return "Photo";
}

// Voice notes must be AAC in MP4 (what the phones can play back). Returns
// the MediaRecorder type to use, or null when this browser can't record it.
export const VOICE_MIME_CANDIDATES = ["audio/mp4;codecs=mp4a.40.2", "audio/mp4"];

export function pickVoiceMime(isTypeSupported) {
  if (typeof isTypeSupported !== "function") return null;
  for (const t of VOICE_MIME_CANDIDATES) {
    try {
      if (isTypeSupported(t)) return t;
    } catch {
      // keep looking
    }
  }
  return null;
}

export const MAX_VOICE_MS = 5 * 60 * 1000;
export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;

// A file picked as a document: only PDFs up to 10 MB. Returns an error message or null.
export function checkDocument(file) {
  if (!file) return "Choose a PDF.";
  const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name || "");
  if (!isPdf) return "Only PDF documents can be sent.";
  if (file.size > MAX_DOCUMENT_BYTES) return "That PDF is too large (10 MB max).";
  return null;
}

// "Seen by 3 of 5"
export function seenSummary(seen) {
  if (!seen || !seen.total) return "";
  return `Seen by ${seen.seen.length} of ${seen.total}`;
}

// Splits `text` around case-insensitive matches of `q`, for highlighting
// search results: [{text, match}].
export function highlightParts(text, q) {
  const src = text || "";
  const needle = (q || "").trim().toLowerCase();
  if (!needle) return [{ text: src, match: false }];
  const parts = [];
  const lower = src.toLowerCase();
  let at = 0;
  for (;;) {
    const i = lower.indexOf(needle, at);
    if (i === -1) break;
    if (i > at) parts.push({ text: src.slice(at, i), match: false });
    parts.push({ text: src.slice(i, i + needle.length), match: true });
    at = i + needle.length;
  }
  if (at < src.length) parts.push({ text: src.slice(at), match: false });
  return parts;
}
