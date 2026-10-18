// Pure helpers for the Flyers page (docs/MARKETING_FLYERS_CONTRACT.md).
// Tested by flyerModel.test.mjs: node --test src/components/flyers/flyerModel.test.mjs

export const TITLE_MAX = 120;
export const CAPTION_MAX = 1000;
export const POLL_MS = 3000;

// Used until /api/flyers/meta answers, and for any field it leaves out.
export const DEFAULT_META = {
  categories: [
    { key: "groceries", label: "Groceries" },
    { key: "street_kitchen", label: "Street Kitchen" },
    { key: "flowers", label: "Flowers" },
    { key: "halal_meat", label: "Halal Meat" },
    { key: "spice_town", label: "Spice Town (Generic)" },
    { key: "others", label: "Others" },
  ],
  groups: [],
  whatsapp_connected: true,
  max_image_mb: 25,
  max_pdf_mb: 20,
  accept: ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"],
  max_groups_per_send: 25,
  max_title_chars: TITLE_MAX,
  max_caption_chars: CAPTION_MAX,
};

export function withMetaDefaults(meta) {
  const out = { ...DEFAULT_META, ...(meta || {}) };
  if (!Array.isArray(out.categories) || out.categories.length === 0) out.categories = DEFAULT_META.categories;
  if (!Array.isArray(out.groups)) out.groups = [];
  if (!Array.isArray(out.accept) || out.accept.length === 0) out.accept = DEFAULT_META.accept;
  return out;
}

const IMAGE_TYPES = ["image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp", "image/heic", "image/heif"];
const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "heic", "heif"];

export function fileExtension(name) {
  const m = /\.([A-Za-z0-9]{1,8})$/.exec(name || "");
  return m ? m[1].toLowerCase() : "";
}

// "image" | "pdf" | null, from the browser's type or else the extension
// (browsers other than Safari often give a HEIC file an empty type). The
// server checks the real content either way.
export function fileKind(file) {
  if (!file) return null;
  const type = (file.type || "").toLowerCase();
  if (type === "application/pdf") return "pdf";
  if (IMAGE_TYPES.includes(type)) return "image";
  if (type.startsWith("image/")) return null; // GIF, SVG, TIFF...
  const ext = fileExtension(file.name);
  if (ext === "pdf") return "pdf";
  if (IMAGE_EXTENSIONS.includes(ext)) return "image";
  return null;
}

export function isHeic(file) {
  const type = (file?.type || "").toLowerCase();
  return type === "image/heic" || type === "image/heif" || ["heic", "heif"].includes(fileExtension(file?.name));
}

// What the file input offers: the server's MIME list plus extensions, so a
// HEIC with no type can still be picked.
export function acceptAttribute(meta) {
  const types = withMetaDefaults(meta).accept;
  return [...types, ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"].join(",");
}

export function formatBytes(n) {
  if (n == null || !Number.isFinite(Number(n))) return "";
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${Math.round(v / 1024)} KB`;
  const mb = v / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export function plural(n, word, many) {
  return n === 1 ? word : many || `${word}s`;
}

// Store-local (America/New_York) short times, like format.js does for full ones.
const STORE_TZ = "America/New_York";
const dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, month: "short", day: "numeric" });
const dayYearFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, month: "short", day: "numeric", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, hour: "numeric", minute: "2-digit" });
const yearFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, year: "numeric" });

function validDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

// "Oct 4" this year, "Oct 4, 2025" otherwise.
export function shortDay(iso, now = new Date()) {
  const d = validDate(iso);
  if (!d) return "";
  return yearFmt.format(d) === yearFmt.format(now) ? dayFmt.format(d) : dayYearFmt.format(d);
}

// "Oct 4, 9:31 AM"
export function shortDayTime(iso, now = new Date()) {
  const d = validDate(iso);
  if (!d) return "";
  return `${shortDay(iso, now)}, ${timeFmt.format(d)}`;
}

// A message for a file the server would refuse, else null.
export function fileProblem(file, meta) {
  if (!file) return "Choose a flyer file first.";
  const m = withMetaDefaults(meta);
  const kind = fileKind(file);
  if (!kind) return "That file type isn't supported. Use a JPEG, PNG, WebP or HEIC picture, or a PDF.";
  if (file.size === 0) return "That file is empty.";
  const maxMb = kind === "pdf" ? m.max_pdf_mb : m.max_image_mb;
  if (file.size > maxMb * 1024 * 1024) {
    return `That ${kind === "pdf" ? "PDF" : "picture"} is ${formatBytes(file.size)}. The most is ${maxMb} MB.`;
  }
  return null;
}

// "diwali_garlands-final.JPG" -> "Diwali garlands-final". Only a starting
// point: the title stays editable, and the server never uses the client's
// file name for the stored path.
export function titleFromFileName(name) {
  let base = String(name || "").replace(/\.[A-Za-z0-9]{1,8}$/, "");
  base = base
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/[_+]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!base) return "";
  base = base.charAt(0).toUpperCase() + base.slice(1);
  return base.slice(0, TITLE_MAX).trim();
}

// Field -> message, for the upload form. Empty object = good to go.
export function formProblems({ file, title, category, caption }, meta) {
  const m = withMetaDefaults(meta);
  const problems = {};
  const fp = fileProblem(file, meta);
  if (fp) problems.file = fp;
  const t = (title || "").trim();
  if (!t) problems.title = "Give the flyer a title.";
  else if (t.length > m.max_title_chars) problems.title = `Keep the title to ${m.max_title_chars} characters.`;
  if (!category) problems.category = "Choose the type of flyer.";
  if ((caption || "").length > m.max_caption_chars) {
    problems.caption = `Keep the message to ${m.max_caption_chars.toLocaleString("en-US")} characters.`;
  }
  return problems;
}

export function groupProblem(selectedKeys, meta) {
  const n = (selectedKeys || []).length;
  if (!n) return "Pick at least one WhatsApp group.";
  const max = withMetaDefaults(meta).max_groups_per_send;
  if (n > max) return `Pick up to ${max} groups at a time.`;
  return null;
}

// ---- sends ----

export const SEND_STATUS = {
  queued: { label: "Waiting", pill: "pill-neutral" },
  sending: { label: "Sending…", pill: "pill-info" },
  sent: { label: "Sent", pill: "pill-success" },
  failed: { label: "Failed", pill: "pill-failed" },
};

export function statusInfo(status) {
  return SEND_STATUS[status] || { label: status || "Unknown", pill: "pill-neutral" };
}

export function isPending(send) {
  return send?.status === "queued" || send?.status === "sending";
}

export function hasPending(sends) {
  return (sends || []).some(isPending);
}

export function countSends(sends) {
  const counts = { sent: 0, failed: 0, pending: 0 };
  for (const s of sends || []) {
    if (s.status === "sent") counts.sent += 1;
    else if (s.status === "failed") counts.failed += 1;
    else if (isPending(s)) counts.pending += 1;
  }
  return counts;
}

// The one-line send state of a flyer card, from its send_counts.
export function sendSummary(counts) {
  const sent = counts?.sent || 0;
  const failed = counts?.failed || 0;
  const pending = counts?.pending || 0;
  if (pending) return { text: `Sending to ${pending} ${plural(pending, "group")}…`, pill: "pill-info" };
  if (failed && sent) return { text: `${sent} sent, ${failed} failed`, pill: "pill-warn" };
  if (failed) return { text: `${failed} failed`, pill: "pill-failed" };
  if (sent) return { text: `Sent to ${sent} ${plural(sent, "group")}`, pill: "pill-success" };
  return { text: "Not sent yet", pill: "pill-neutral" };
}

// Sends grouped by batch (one Send click), newest batch first; inside a
// batch, in the order they go out.
export function groupByBatch(sends) {
  const batches = [];
  const byKey = new Map();
  for (const s of sends || []) {
    const key = s.batch_id || `send-${s.id}`;
    let batch = byKey.get(key);
    if (!batch) {
      batch = { key, requestedAt: s.requested_at, requestedBy: s.requested_by_name, sends: [] };
      byKey.set(key, batch);
      batches.push(batch);
    }
    batch.sends.push(s);
    if (s.requested_at && (!batch.requestedAt || s.requested_at < batch.requestedAt)) batch.requestedAt = s.requested_at;
  }
  for (const b of batches) b.sends.sort((a, c) => (a.id || 0) - (c.id || 0));
  return batches;
}

// target_key -> the newest send to that group.
export function latestSendByGroup(sends) {
  const out = {};
  for (const s of sends || []) {
    const prev = out[s.target_key];
    if (!prev || (s.id || 0) > (prev.id || 0)) out[s.target_key] = s;
  }
  return out;
}

// Retry-able sends: failed ones, newest first.
export function failedSends(sends) {
  return (sends || []).filter((s) => s.status === "failed");
}

export function newIdempotencyKey() {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch {
    // not a secure context
  }
  const part = () => Math.random().toString(36).slice(2, 12);
  return `web-${Date.now().toString(36)}-${part()}${part()}`;
}

// ---- WhatsApp formatting (the caption preview) ----
// *bold*, _italic_, ~strikethrough~ and ```monospace```, as WhatsApp shows
// them. Returns [{text, bold?, italic?, strike?, mono?}]; never HTML.

const MARKERS = [
  { mark: "```", style: "mono" },
  { mark: "*", style: "bold" },
  { mark: "_", style: "italic" },
  { mark: "~", style: "strike" },
];

const isWordChar = (ch) => !!ch && /[\p{L}\p{N}]/u.test(ch);
const isSpace = (ch) => !ch || /\s/.test(ch);

function findClose(text, mark, from, multiline) {
  let j = text.indexOf(mark, from);
  while (j !== -1) {
    const inner = text.slice(from, j);
    if (!multiline && inner.includes("\n")) return -1;
    if (inner.length > 0 && !isSpace(text[j - 1]) && !isWordChar(text[j + mark.length])) return j;
    j = text.indexOf(mark, j + 1);
  }
  return -1;
}

export function parseWhatsApp(text, styles = {}) {
  const out = [];
  let plain = "";
  const flush = () => {
    if (plain) out.push({ text: plain, ...styles });
    plain = "";
  };
  const src = String(text || "");
  let i = 0;
  while (i < src.length) {
    let matched = false;
    if (!styles.mono) {
      for (const { mark, style } of MARKERS) {
        if (styles[style] || !src.startsWith(mark, i)) continue;
        const start = i + mark.length;
        if (isWordChar(src[i - 1]) || isSpace(src[start])) continue;
        const close = findClose(src, mark, start, style === "mono");
        if (close === -1) continue;
        flush();
        const inner = src.slice(start, close);
        const nested = { ...styles, [style]: true };
        if (style === "mono") out.push({ text: inner, ...nested });
        else out.push(...parseWhatsApp(inner, nested));
        i = close + mark.length;
        matched = true;
        break;
      }
    }
    if (!matched) {
      plain += src[i];
      i += 1;
    }
  }
  flush();
  return out;
}
