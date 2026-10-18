// Pure helpers for "Send PO" and Vendor contacts (backend: services/po_send.py,
// routers/po_send.py). No React, no fetch - node tests in poSendModel.test.mjs.

// The server's render_body rule: the note replaces the marker paragraph, or
// the marker paragraph (and the blank line before it) goes when there's no
// note. Line endings are normalised and trailing spaces dropped per line.
export function renderBody(template, marker, note) {
  const text = cleanNote(note);
  if (!template) return "";
  if (!marker) return template;
  if (text) return template.split(marker).join(text);
  return template.split(`\n\n${marker}`).join("");
}

export function cleanNote(note) {
  if (typeof note !== "string") return "";
  return note
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/\s+$/, ""))
    .join("\n")
    .trim();
}

export function noteTooLong(note, max) {
  return !!max && cleanNote(note).length > max;
}

// A key per Send dialog: a repeated POST with it (double click, a lost
// answer retried) gets the first attempt back, never a second email.
export function newIdempotencyKey() {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch {
    // not a secure context
  }
  const part = () => Math.random().toString(36).slice(2, 12);
  return `web-${Date.now().toString(36)}-${part()}${part()}`;
}

// Contacts that can receive the PO by email, with whether each starts ticked
// (the preview's `recipients` = send_po contacts with an email).
export function emailOptions(preview) {
  if (!preview) return [];
  const ticked = new Set((preview.recipients || []).map((e) => e.toLowerCase()));
  const seen = new Set();
  const out = [];
  for (const c of preview.contacts || []) {
    if (!c.email) continue;
    const key = c.email.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ email: c.email, name: c.contact_name || "", onPos: !!c.send_po, ticked: ticked.has(key) });
  }
  return out;
}

export function toggleEmail(list, email) {
  const key = (email || "").toLowerCase();
  const has = list.some((e) => e.toLowerCase() === key);
  return has ? list.filter((e) => e.toLowerCase() !== key) : [...list, email];
}

// --- Email CCs (W2, Oct 14) ----------------------------------------------
// Default CCs live on each vendor contact; the Send dialog pre-fills them
// (preview.default_cc) and they can be edited per send. The server checks
// again (services/po_send.normalize_cc): valid addresses, at most 10,
// duplicates and To addresses dropped.

export const MAX_CC = 10;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isEmail(text) {
  return EMAIL_RE.test(String(text || "").trim());
}

// "a@x.com, b@y.com; c@z.com" (or one per line) -> ["a@x.com", ...].
export function parseEmailList(text) {
  return String(text || "")
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// Add typed / pasted addresses to a CC list. Returns { list, error }: the
// valid new ones are added (case-insensitive duplicates and To addresses
// skipped); `error` names a bad address or the 10-address limit.
export function addCc(list, text, to = [], max = MAX_CC) {
  const out = [...(list || [])];
  const taken = new Set([...out, ...(to || [])].map((e) => e.toLowerCase()));
  for (const email of parseEmailList(text)) {
    if (!isEmail(email)) return { list: out, error: `"${email}" isn't an email address.` };
    if (taken.has(email.toLowerCase())) continue;
    if (out.length >= max) return { list: out, error: `At most ${max} CC addresses.` };
    taken.add(email.toLowerCase());
    out.push(email);
  }
  return { list: out, error: null };
}

export function removeCc(list, email) {
  const key = (email || "").toLowerCase();
  return (list || []).filter((e) => e.toLowerCase() !== key);
}

// CCs that would also be in To (a recipient ticked after the CC was added)
// are dropped by the server - drop them here too so the dialog shows what goes.
export function effectiveCc(cc, recipients) {
  const to = new Set((recipients || []).map((e) => e.toLowerCase()));
  return (cc || []).filter((e) => !to.has(e.toLowerCase()));
}

export function ccProblem(cc, max = MAX_CC) {
  const bad = (cc || []).find((e) => !isEmail(e));
  if (bad) return `"${bad}" isn't an email address.`;
  if ((cc || []).length > max) return `At most ${max} CC addresses.`;
  return null;
}

// What stops Send, in plain words (null = ready).
export function sendBlocker(preview, recipients, note, { demo = false, cc = [] } = {}) {
  if (demo) return "The demo account can't send purchase orders.";
  if (!preview) return "Loading…";
  if (!preview.item_count) return "Every line is at 0 - there's nothing to put on the PO.";
  if (!preview.email_configured) return "Email isn't set up on the server yet, so the PO can't be emailed.";
  if (!emailOptions(preview).length) return `No email on file for ${preview.supplier}.`;
  if (!recipients.length) return "Tick at least one email to send to.";
  if (noteTooLong(note, preview.max_note)) return `The note is too long (at most ${preview.max_note} characters).`;
  const ccError = ccProblem(cc, preview.max_cc || MAX_CC);
  if (ccError) return ccError;
  return null;
}

export const STATUS_LABELS = {
  sending: "Sending",
  sent: "Sent",
  partly_sent: "Partly sent",
  failed: "Not sent",
  uncertain: "Not confirmed",
  pending: "Waiting",
};

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || "-";
}

export function statusTone(status) {
  if (status === "sent") return "good";
  if (status === "partly_sent" || status === "uncertain" || status === "sending") return "warn";
  if (status === "failed") return "bad";
  return "info";
}

function joinNames(list) {
  if (list.length <= 1) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

// The cart page's message after a send went out.
export function sentNotice(send) {
  if (!send) return "";
  const to = send.email_status === "sent" ? joinNames(send.email_to || []) : "";
  const parts = [];
  parts.push(to ? `Emailed ${send.supplier}'s PO to ${to}.` : `Sent ${send.supplier}'s PO.`);
  if (to && send.email_cc?.length) parts.push(`CC: ${joinNames(send.email_cc)}.`);
  if (send.whatsapp_status === "sent") parts.push("Also sent on WhatsApp.");
  if (send.placed_order_id) parts.push("Recorded as placed and taken out of the cart.");
  else parts.push("It wasn't recorded as placed - press Order placed for this vendor.");
  if (send.status === "partly_sent") {
    if (send.email_status && send.email_status !== "sent") parts.push(`Email: ${send.email_error || "not sent"}`);
    if (send.whatsapp_status && send.whatsapp_status !== "sent") {
      parts.push(`WhatsApp: ${send.whatsapp_error || "not sent"}`);
    }
  }
  return parts.join(" ");
}

// Searching Gmail's Sent folder for one message: rfc822msgid:<id>.
export function gmailSearch(messageId) {
  if (!messageId) return "";
  return `rfc822msgid:${messageId.replace(/^<|>$/g, "")}`;
}

// Did the request never get an answer (so the same key may be retried)?
export function isNoAnswer(err) {
  return !err || err.status === undefined || err.status === 0;
}

// --- Vendor contacts page -------------------------------------------------

export function vendorKey(name) {
  return (name || "").trim().split(/\s+/).join(" ").toLowerCase();
}

// Contacts grouped by vendor (A to Z), filtered by a search over vendor,
// name, email, WhatsApp and notes.
export function groupContacts(items, search = "") {
  const needle = search.trim().toLowerCase();
  const groups = new Map();
  for (const c of items || []) {
    if (needle) {
      const hay = [c.vendor, c.contact_name, c.email, c.whatsapp, c.notes, ...(c.cc || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(needle)) continue;
    }
    const key = vendorKey(c.vendor);
    if (!groups.has(key)) groups.set(key, { vendor: c.vendor, contacts: [] });
    groups.get(key).contacts.push(c);
  }
  return [...groups.values()].sort((a, b) => a.vendor.localeCompare(b.vendor, undefined, { sensitivity: "base" }));
}

// Cart vendors with no contact that has an email (to set up first).
export function vendorsMissingEmail(cartVendors, contacts) {
  const withEmail = new Set((contacts || []).filter((c) => c.email).map((c) => vendorKey(c.vendor)));
  const seen = new Set();
  const out = [];
  for (const v of cartVendors || []) {
    const key = vendorKey(v);
    if (!key || withEmail.has(key) || seen.has(key)) continue;
    seen.add(key);
    out.push(v);
  }
  return out;
}

export const EMPTY_CONTACT = { vendor: "", contact_name: "", email: "", whatsapp: "", notes: "", send_po: true, cc: "" };

export function contactForm(contact, vendor = "") {
  if (!contact) return { ...EMPTY_CONTACT, vendor };
  return {
    vendor: contact.vendor || "",
    contact_name: contact.contact_name || "",
    email: contact.email || "",
    whatsapp: contact.whatsapp || "",
    notes: contact.notes || "",
    send_po: contact.send_po !== false,
    // Default CCs, edited as one "a@x.com, b@y.com" line.
    cc: (contact.cc || []).join(", "),
  };
}

// A quick client-side check before saving (the server checks again).
export function contactProblem(form) {
  if (!form.vendor.trim()) return "Pick the vendor.";
  if (!form.email.trim() && !form.whatsapp.trim()) return "Add an email or a WhatsApp number.";
  if (form.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) {
    return "That email address doesn't look right.";
  }
  const cc = parseEmailList(form.cc);
  const bad = cc.find((e) => !isEmail(e));
  if (bad) return `CC: "${bad}" isn't an email address.`;
  if (new Set(cc.map((e) => e.toLowerCase())).size > MAX_CC) return `CC: at most ${MAX_CC} addresses.`;
  return null;
}

export function contactPayload(form) {
  return {
    vendor: form.vendor.trim(),
    contact_name: form.contact_name.trim() || null,
    email: form.email.trim() || null,
    whatsapp: form.whatsapp.trim() || null,
    notes: form.notes.trim() || null,
    send_po: !!form.send_po,
    cc: parseEmailList(form.cc),
  };
}

export function contactsLinkFor(vendor) {
  return vendor ? `/vendor-contacts?vendor=${encodeURIComponent(vendor)}` : "/vendor-contacts";
}
