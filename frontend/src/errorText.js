// The sentence to show for a failed request (pre-production G25 + G27).
// The server sends {detail, error, message}: `message` is always a plain
// sentence; `detail` may be a list (a form error, 422) or a bare HTTP phrase
// ("Not Found") that older app builds still read. Prefer the sentence.
const BARE_PHRASES = new Set([
  "Bad Request",
  "Unauthorized",
  "Forbidden",
  "Not Found",
  "Method Not Allowed",
  "Internal Server Error",
  "Bad Gateway",
  "Service Unavailable",
]);

export function errorText(body, fallback = "Something went wrong. Try again.") {
  if (!body || typeof body !== "object") return fallback;
  const { detail, message } = body;
  const hasMessage = typeof message === "string" && message.trim() !== "";
  if (typeof detail === "string" && detail.trim() !== "" && !(hasMessage && BARE_PHRASES.has(detail))) return detail;
  if (hasMessage) return message;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof detail.message === "string") {
    return detail.message;
  }
  if (Array.isArray(detail) && detail.length) {
    const msgs = detail.map((d) => (d && typeof d.msg === "string" ? d.msg : "")).filter(Boolean);
    if (msgs.length) return msgs.join(" ");
  }
  return fallback;
}
