import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getFlyer, retryFlyerSend } from "../../api";
import { POLL_MS, hasPending, newIdempotencyKey, plural } from "./flyerModel.js";
import { SendRows, sendProgressText } from "./SendStatus.jsx";
import useDialog from "./useDialog.js";
import WhatsAppText from "./WhatsAppText.jsx";

// The confirm step of a send, then its live per-group status.
//
// `performSend(idempotencyKey, setStage)` does the work (upload first if the
// flyer isn't saved yet, then POST /send) and resolves to {flyer, sends}.
// The key is made once per open, so "Try again" after a lost answer can
// never send twice (the server returns the first batch for a repeated key).
// While anything is waiting or sending, GET /api/flyers/{id} is polled every
// 3 s. Closing the dialog doesn't stop the sending - that runs on the server.
export default function SendFlyerModal({ title, categoryLabel, caption, kindLabel, preview, groups, performSend, onClose }) {
  const [stage, setStage] = useState("confirm"); // confirm | working | status
  const [workText, setWorkText] = useState("");
  const [error, setError] = useState(null);
  const [flyerId, setFlyerId] = useState(null);
  const [batchId, setBatchId] = useState(null);
  const [trackedIds, setTrackedIds] = useState([]);
  const [sendsById, setSendsById] = useState({});
  const [pollError, setPollError] = useState(false);
  const [pollTick, setPollTick] = useState(0);
  const [retrying, setRetrying] = useState(() => new Set());
  const keyRef = useRef(newIdempotencyKey());
  const mounted = useRef(true);
  // The parent learns whether a send went out: {sends} (their latest
  // statuses) once it did, else null.
  const close = () => onClose(stage === "status" ? { sends: trackedIds.map((id) => sendsById[id]).filter(Boolean) } : null);
  const dialog = useDialog(() => close(), { initialFocus: "[data-autofocus]" });

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const sends = trackedIds.map((id) => sendsById[id]).filter(Boolean);
  const pending = hasPending(sends);
  // No rows in the send answer: find this batch's rows on the first poll.
  const waitingForRows = stage === "status" && trackedIds.length === 0 && pollTick < 5;

  async function send() {
    setError(null);
    setStage("working");
    setWorkText("Starting…");
    try {
      const result = await performSend(keyRef.current, (text) => mounted.current && setWorkText(text));
      if (!mounted.current) return;
      const list = result.sends || [];
      setFlyerId(result.flyer.id);
      setBatchId(result.batchId || null);
      setSendsById(Object.fromEntries(list.map((s) => [s.id, s])));
      setTrackedIds(list.map((s) => s.id));
      setStage("status");
    } catch (err) {
      if (!mounted.current) return;
      setError(err.message || "Something went wrong. Try again.");
      setStage("confirm");
    }
  }

  // The status view replaces the buttons; keep focus inside the dialog.
  useEffect(() => {
    if (stage === "status") dialog.ref.current?.querySelector("[data-autofocus]")?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  // Live status: poll while anything is waiting or sending.
  useEffect(() => {
    if (stage !== "status" || !flyerId || !(pending || waitingForRows)) return undefined;
    const timer = setTimeout(async () => {
      try {
        const flyer = await getFlyer(flyerId);
        if (!mounted.current) return;
        const rows = flyer.sends || [];
        if (trackedIds.length === 0 && batchId) {
          const mine = rows.filter((s) => s.batch_id === batchId);
          setTrackedIds(mine.map((s) => s.id));
        }
        setSendsById((prev) => {
          const next = { ...prev };
          for (const s of rows) {
            if (s.id in next || (trackedIds.length === 0 && batchId && s.batch_id === batchId)) next[s.id] = s;
          }
          return next;
        });
        setPollError(false);
      } catch {
        if (mounted.current) setPollError(true);
      } finally {
        if (mounted.current) setPollTick((t) => t + 1);
      }
    }, pollError ? POLL_MS * 2 : POLL_MS);
    return () => clearTimeout(timer);
  }, [stage, flyerId, batchId, trackedIds, pending, waitingForRows, pollTick, pollError]);

  async function retry(list) {
    setError(null);
    for (const s of list) {
      setRetrying((prev) => new Set(prev).add(s.id));
      try {
        const fresh = await retryFlyerSend(flyerId, s.id);
        if (!mounted.current) return;
        if (fresh?.id != null) {
          setSendsById((prev) => ({ ...prev, [fresh.id]: fresh }));
          if (fresh.id !== s.id) setTrackedIds((prev) => prev.map((id) => (id === s.id ? fresh.id : id)));
        }
      } catch (err) {
        if (mounted.current) setError(`${s.target_label}: ${err.message}`);
      } finally {
        if (mounted.current) {
          setRetrying((prev) => {
            const next = new Set(prev);
            next.delete(s.id);
            return next;
          });
        }
      }
    }
    if (mounted.current) setPollTick((t) => t + 1);
  }

  const failed = sends.filter((s) => s.status === "failed");
  const n = groups.length;
  const working = stage === "working";

  // In a portal: opened from the drawer, it must not be laid out inside it.
  return createPortal(
    <div className="fly-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !working && close()}>
      <div
        className="fly-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fly-send-title"
        {...dialog}
        onKeyDown={(e) => (working && e.key === "Escape" ? e.stopPropagation() : dialog.onKeyDown(e))}
      >
        <div className="fly-modal-head">
          <h3 id="fly-send-title">{stage === "status" ? "Sending your flyer" : "Send this flyer?"}</h3>
          <button
            type="button"
            className="fly-icon-btn"
            aria-label="Close"
            onClick={close}
            disabled={working}
          >
            ×
          </button>
        </div>
        <div className="fly-modal-body">
          <div className="fly-confirm-flyer">
            <div className="fly-confirm-preview">{preview}</div>
            <div className="fly-confirm-text">
              <p className="fly-confirm-title">{title}</p>
              <p className="fly-confirm-meta">
                {categoryLabel}
                {kindLabel ? ` · ${kindLabel}` : ""}
              </p>
              {caption ? (
                <p className="fly-confirm-caption">
                  <WhatsAppText text={caption} />
                </p>
              ) : (
                <p className="fly-confirm-meta">No message - the flyer goes on its own.</p>
              )}
            </div>
          </div>

          {stage !== "status" && (
            <>
              <p className="fly-confirm-to">
                To {n} WhatsApp {plural(n, "group")}:
              </p>
              <ul className="fly-confirm-groups">
                {groups.map((g) => (
                  <li key={g.key}>{g.label}</li>
                ))}
              </ul>
              <p className="fly-hint">
                It goes to one group at a time, a few seconds apart. Everyone in {n === 1 ? "that group" : "these groups"} will
                see it.
              </p>
            </>
          )}

          {stage === "status" && (
            <>
              <p className="fly-progress-text" aria-live="polite">
                {sends.length ? sendProgressText(sends) : waitingForRows ? "Starting…" : "Sent to the server."}
              </p>
              <SendRows sends={sends} onRetry={(s) => retry([s])} retrying={retrying} busy={pending} />
              {pollError && <p className="fly-hint">Couldn't check the status just now - trying again.</p>}
              {pending && (
                <p className="fly-hint">
                  You can close this. Sending carries on, and the Spice Town phone app tells you when it's done.
                </p>
              )}
            </>
          )}

          {error && (
            <p className="error fly-modal-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="fly-modal-actions">
          {stage !== "status" ? (
            <>
              <button type="button" className="btn-secondary" data-autofocus onClick={close} disabled={working}>
                Back
              </button>
              <button type="button" onClick={send} disabled={working}>
                {working ? workText || "Sending…" : error ? "Try again" : `Send to ${n} ${plural(n, "group")}`}
              </button>
            </>
          ) : (
            <>
              {failed.length > 0 && !pending && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => retry(failed)}
                  disabled={retrying.size > 0}
                >
                  Retry failed ({failed.length})
                </button>
              )}
              <button type="button" data-autofocus onClick={close}>
                {pending ? "Close" : "Done"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
