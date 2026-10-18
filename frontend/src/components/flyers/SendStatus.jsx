import { countSends, hasPending, plural, shortDayTime, statusInfo } from "./flyerModel.js";

// One row per WhatsApp group of a send: its status, when it went or why it
// failed, and Retry for a failed one.
export function SendRows({ sends, onRetry, retrying, busy }) {
  return (
    <ul className="fly-sends">
      {sends.map((s) => {
        const info = statusInfo(s.status);
        const isRetrying = retrying?.has(s.id);
        return (
          <li key={s.id} className={`fly-send fly-send-${s.status}`}>
            <span className="fly-send-name">{s.target_label || s.target_key}</span>
            <span className={`pill ${info.pill} fly-send-pill`}>{info.label}</span>
            {s.status === "sent" && s.sent_at && <span className="fly-send-detail">{shortDayTime(s.sent_at)}</span>}
            {s.status === "failed" && s.error && <span className="fly-send-detail fly-send-error">{s.error}</span>}
            {s.status === "failed" && onRetry && (
              <button
                type="button"
                className="link-button fly-send-retry"
                disabled={busy || isRetrying}
                onClick={() => onRetry(s)}
                aria-label={`Retry sending to ${s.target_label || s.target_key}`}
              >
                {isRetrying ? "Retrying…" : "Retry"}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// "Sending… 1 of 3 sent" / "Sent to 3 groups" / "2 sent, 1 failed".
export function sendProgressText(sends) {
  const total = (sends || []).length;
  const c = countSends(sends);
  if (!total) return "";
  if (hasPending(sends)) return `Sending… ${c.sent} of ${total} sent${c.failed ? `, ${c.failed} failed` : ""}`;
  if (!c.failed) return `Sent to ${c.sent} ${plural(c.sent, "group")}.`;
  if (!c.sent) return `Not sent: ${c.failed === 1 ? "the group" : `all ${c.failed} groups`} failed.`;
  return `${c.sent} sent, ${c.failed} failed.`;
}
