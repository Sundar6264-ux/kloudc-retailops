import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { fetchPoSendPdfUrl, getPoSendPreview, sendPurchaseOrder } from "../../api";
import { formatRestaurantTime } from "../../format";
import { PO_SEND_TERMS, Term } from "../../glossary.jsx";
import useDialog from "../flyers/useDialog.js";
import { useStore, useStoreAllows } from "../../store/StoreContext.jsx";
import {
  MAX_CC,
  addCc,
  contactsLinkFor,
  effectiveCc,
  emailOptions,
  gmailSearch,
  isNoAnswer,
  newIdempotencyKey,
  removeCc,
  renderBody,
  sendBlocker,
  statusLabel,
  statusTone,
  toggleEmail,
} from "./poSendModel.js";

const TONE_CLASS = { good: "pill-success", warn: "pill-warn", bad: "pill-failed", info: "pill-info" };

export function SendStatusPill({ status }) {
  return (
    <Term k={PO_SEND_TERMS[status]}>
      <span className={`pill ${TONE_CLASS[statusTone(status)]} pos-status-pill`}>{statusLabel(status)}</span>
    </Term>
  );
}

// "Send PO" for one cart vendor: who it goes to, the subject and message
// (with an optional note), the PDF that's attached, then Send. On success the
// server records the order as placed and clears the vendor's PO lines; the
// parent gets `onSent(send)`. A failure keeps the dialog open with the reason
// and the cart untouched. One idempotency key per attempt: a lost answer is
// retried with the SAME key (the server never emails twice for one key); a
// definite answer (failed / not confirmed) gets a new key for the next try.
export default function SendPoDialog({ supplier, isAdmin, isDemo, onClose, onSent, onMarkPlaced }) {
  const store = useStore();
  const [preview, setPreview] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [recipients, setRecipients] = useState([]);
  // CC (W2): pre-filled from the contacts' default CCs, editable per send.
  const [cc, setCc] = useState([]);
  const [ccDraft, setCcDraft] = useState("");
  const [ccError, setCcError] = useState(null);
  const [note, setNote] = useState("");
  const [useWhatsapp, setUseWhatsapp] = useState(false);
  const [whatsappContactId, setWhatsappContactId] = useState(null);
  // compose | sending | failed | uncertain | check_previous
  const [stage, setStage] = useState("compose");
  const [error, setError] = useState(null);
  const [lastSend, setLastSend] = useState(null);
  const [checkedSent, setCheckedSent] = useState(false);
  const [marking, setMarking] = useState(false);
  const [pdf, setPdf] = useState({ open: false, url: null, loading: false, error: null });
  const keyRef = useRef(newIdempotencyKey());
  const mounted = useRef(true);
  const pdfUrlRef = useRef(null);
  const sending = stage === "sending";
  const dialog = useDialog(() => !sending && !marking && onClose(), { initialFocus: "[data-autofocus]" });

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
    };
  }, []);

  async function load() {
    setLoadError(null);
    try {
      const p = await getPoSendPreview(supplier);
      if (!mounted.current) return;
      setPreview(p);
      setRecipients(p.recipients || []);
      // Only on the first load: a reload (after "check previous") keeps edits.
      setCc((prev) => (preview ? prev : p.default_cc || []));
      const wa = p.whatsapp?.contacts || [];
      setWhatsappContactId(wa.length ? wa[0].id : null);
      if (p.uncertain_previous) {
        setLastSend(p.uncertain_previous);
        setStage("check_previous");
      }
    } catch (err) {
      if (mounted.current) setLoadError(err.message || "Couldn't load the preview.");
    }
  }

  useEffect(() => {
    load();
    // Loaded once per open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const options = useMemo(() => emailOptions(preview), [preview]);
  const body = preview ? renderBody(preview.body_template, preview.note_marker, note) : "";
  const maxCc = preview?.max_cc || MAX_CC;
  const sendCc = effectiveCc(cc, recipients);
  const blocker = preview ? sendBlocker(preview, recipients, note, { demo: isDemo, cc: sendCc }) : null;
  const needsCheck = stage === "uncertain" || stage === "check_previous";
  const canSend = !!preview && !blocker && !sending && !marking && (!needsCheck || checkedSent);
  // A store without WhatsApp (store feature) never shows the option.
  const storeWhatsapp = useStoreAllows("whatsapp");
  const waEnabled = storeWhatsapp && !!preview?.whatsapp?.enabled;
  const waContacts = preview?.whatsapp?.contacts || [];

  async function togglePdf() {
    if (pdf.open) {
      setPdf((p) => ({ ...p, open: false }));
      return;
    }
    if (pdf.url) {
      setPdf((p) => ({ ...p, open: true }));
      return;
    }
    setPdf({ open: true, url: null, loading: true, error: null });
    try {
      const url = await fetchPoSendPdfUrl(supplier);
      if (!mounted.current) {
        URL.revokeObjectURL(url);
        return;
      }
      pdfUrlRef.current = url;
      setPdf({ open: true, url, loading: false, error: null });
    } catch (err) {
      if (mounted.current) setPdf({ open: true, url: null, loading: false, error: err.message });
    }
  }

  // Adds what's typed in the CC box (Enter, comma, the Add button or leaving
  // the box). Returns the list it would send with.
  function commitCcDraft() {
    if (!ccDraft.trim()) return cc;
    const { list, error: problem } = addCc(cc, ccDraft, recipients, maxCc);
    setCc(list);
    setCcError(problem);
    if (!problem) setCcDraft("");
    return list;
  }

  async function send() {
    if (!canSend) return;
    // A typed but not yet added address still goes (or stops the send).
    if (ccDraft.trim()) {
      const { error: problem } = addCc(cc, ccDraft, recipients, maxCc);
      if (problem) {
        setCcError(problem);
        return;
      }
    }
    const ccToSend = effectiveCc(commitCcDraft(), recipients);
    setError(null);
    setStage("sending");
    const channels = ["email", ...(waEnabled && useWhatsapp ? ["whatsapp"] : [])];
    try {
      const result = await sendPurchaseOrder({
        supplier,
        idempotency_key: keyRef.current,
        recipients,
        cc: ccToSend,
        note: note.trim() ? note : null,
        channels,
        whatsapp_contact_id: channels.includes("whatsapp") ? whatsappContactId : null,
        confirm_resend: needsCheck && checkedSent,
      });
      if (!mounted.current) return;
      onSent(result.send);
    } catch (err) {
      if (!mounted.current) return;
      const code = err.body?.code;
      if (isNoAnswer(err)) {
        // We don't know whether it went: the same key again is safe.
        setError("No answer from the server, so it isn't known whether the PO went. Press Try again - it can't be sent twice.");
        setStage("compose");
        return;
      }
      if (err.status === 502 && err.body?.send) {
        keyRef.current = newIdempotencyKey();
        setLastSend(err.body.send);
        setCheckedSent(false);
        setError(err.message);
        setStage(code === "uncertain" ? "uncertain" : "failed");
        return;
      }
      if (code === "check_previous") {
        setCheckedSent(false);
        setError(null);
        await load();
        if (mounted.current) setStage("check_previous");
        return;
      }
      setError(err.message || "Something went wrong. Try again.");
      setStage("compose");
    }
  }

  async function markPlacedInstead() {
    setMarking(true);
    setError(null);
    try {
      await onMarkPlaced(supplier);
    } catch (err) {
      if (mounted.current) setError(err.message);
    } finally {
      if (mounted.current) setMarking(false);
    }
  }

  const title = `Send PO to ${supplier}`;
  const sendLabel = sending
    ? "Sending…"
    : stage === "failed" || (error && stage === "compose")
      ? "Try again"
      : needsCheck
        ? "Send again"
        : `Send PO${recipients.length > 1 ? ` to ${recipients.length} emails` : ""}`;

  return createPortal(
    <div
      className="fly-modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && !sending && !marking && onClose()}
    >
      <div
        className="fly-modal pos-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pos-title"
        aria-busy={sending || undefined}
        {...dialog}
      >
        <div className="fly-modal-head">
          <h3 id="pos-title">{title}</h3>
          <button
            type="button"
            className="fly-icon-btn"
            aria-label="Close"
            onClick={onClose}
            disabled={sending || marking}
          >
            ×
          </button>
        </div>

        <div className="fly-modal-body pos-body">
          {!preview && !loadError && <p className="muted">Preparing the email…</p>}
          {loadError && (
            <p className="error" role="alert">
              {loadError}
            </p>
          )}

          {preview && (
            <>
              {needsCheck && lastSend && (
                <div className="pos-callout pos-callout-warn" role="alert">
                  <p>
                    <strong>
                      {stage === "uncertain" ? "This send wasn't confirmed." : "The last send wasn't confirmed."}
                    </strong>{" "}
                    {lastSend.created_at ? `${formatRestaurantTime(lastSend.created_at)}` : ""}
                    {lastSend.sent_by_name ? ` by ${lastSend.sent_by_name}` : ""}
                    {lastSend.email_to?.length ? `, to ${lastSend.email_to.join(", ")}` : ""}
                    {lastSend.email_cc?.length ? ` (CC ${lastSend.email_cc.join(", ")})` : ""}. The connection broke
                    while sending, so the email may have gone out. The cart wasn't changed.
                  </p>
                  {lastSend.email_message_id && (
                    <p className="pos-muted-small">
                      Check the Sent folder of the store's mailbox - search for <code>{gmailSearch(lastSend.email_message_id)}</code>
                    </p>
                  )}
                  <div className="pos-callout-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={markPlacedInstead}
                      disabled={marking || sending || isDemo}
                    >
                      {marking ? "Saving…" : "It went out - record as placed"}
                    </button>
                    <label className="pos-check">
                      <input
                        type="checkbox"
                        checked={checkedSent}
                        onChange={(e) => setCheckedSent(e.target.checked)}
                        disabled={sending || marking}
                      />
                      I checked: it isn't in the Sent folder, send it again
                    </label>
                  </div>
                </div>
              )}

              <fieldset className="pos-field">
                <legend>To</legend>
                {options.length === 0 ? (
                  <p className="pos-callout pos-callout-info">
                    No email on file for {supplier}.{" "}
                    {isAdmin ? (
                      <Link to={contactsLinkFor(supplier)} onClick={onClose}>
                        Add a contact
                      </Link>
                    ) : (
                      "Ask an admin to add one on Vendor contacts."
                    )}
                  </p>
                ) : (
                  <ul className="pos-recipients">
                    {options.map((o) => {
                      const checked = recipients.some((r) => r.toLowerCase() === o.email.toLowerCase());
                      return (
                        <li key={o.email}>
                          <label className="pos-check">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => setRecipients((prev) => toggleEmail(prev, o.email))}
                              disabled={sending}
                            />
                            <span>
                              {o.name ? <strong>{o.name}</strong> : null}
                              {o.name ? " " : ""}
                              <span className="pos-email">{o.email}</span>
                              {!o.onPos && <span className="pos-muted-small"> (not on POs by default)</span>}
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
                <p className="pos-muted-small">
                  From {preview.from_address ? `${store.name} <${preview.from_address}>` : "the store's email"} ·{" "}
                  <Link to={contactsLinkFor(supplier)} onClick={onClose}>
                    {isAdmin ? "Edit contacts" : "Vendor contacts"}
                  </Link>
                </p>
              </fieldset>

              {options.length > 0 && (
                <fieldset className="pos-field pos-cc">
                  <legend>
                    CC <span className="pos-muted-small">(optional · {sendCc.length} of {maxCc})</span>
                  </legend>
                  {cc.length > 0 && (
                    <ul className="pos-cc-list" aria-label="CC addresses">
                      {cc.map((email) => {
                        const inTo = recipients.some((r) => r.toLowerCase() === email.toLowerCase());
                        return (
                          <li key={email} className={`pos-cc-chip${inTo ? " is-muted" : ""}`}>
                            <span className="pos-email">{email}</span>
                            {inTo && <span className="pos-muted-small"> (already in To)</span>}
                            <button
                              type="button"
                              className="pos-cc-remove"
                              aria-label={`Remove ${email} from CC`}
                              onClick={() => {
                                setCc((prev) => removeCc(prev, email));
                                setCcError(null);
                              }}
                              disabled={sending}
                            >
                              ×
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <div className="pos-cc-add">
                    <input
                      type="email"
                      inputMode="email"
                      autoComplete="off"
                      value={ccDraft}
                      placeholder={cc.length ? "Add another email" : "Add an email to copy, e.g. owner@example.com"}
                      aria-label="Add a CC email"
                      aria-invalid={ccError ? true : undefined}
                      aria-describedby={ccError ? "pos-cc-error" : undefined}
                      onChange={(e) => {
                        setCcDraft(e.target.value);
                        setCcError(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === ",") {
                          e.preventDefault();
                          commitCcDraft();
                        }
                      }}
                      onBlur={() => ccDraft.trim() && commitCcDraft()}
                      disabled={sending || cc.length >= maxCc}
                    />
                    <button
                      type="button"
                      className="btn-secondary pos-cc-add-btn"
                      onClick={commitCcDraft}
                      disabled={sending || !ccDraft.trim()}
                    >
                      Add
                    </button>
                  </div>
                  {ccError && (
                    <p id="pos-cc-error" className="error pos-cc-error" role="alert">
                      {ccError}
                    </p>
                  )}
                  <p className="pos-muted-small">
                    {preview.default_cc?.length
                      ? "Filled in from the contacts' default CCs. Changes here are for this send only."
                      : isAdmin
                        ? "Default CCs can be saved on each contact (Vendor contacts)."
                        : "Everyone in CC gets a copy of this email."}
                  </p>
                </fieldset>
              )}

              {storeWhatsapp && (
              <div className="pos-field pos-whatsapp">
                <label className={`pos-check${waEnabled ? "" : " is-disabled"}`}>
                  <input
                    type="checkbox"
                    checked={waEnabled && useWhatsapp}
                    onChange={(e) => setUseWhatsapp(e.target.checked)}
                    disabled={!waEnabled || sending || waContacts.length === 0}
                    aria-describedby="pos-wa-why"
                  />
                  Also send on WhatsApp
                </label>
                {waEnabled && useWhatsapp && waContacts.length > 1 && (
                  <select
                    value={whatsappContactId ?? ""}
                    onChange={(e) => setWhatsappContactId(Number(e.target.value))}
                    aria-label="WhatsApp contact"
                    disabled={sending}
                  >
                    {waContacts.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                )}
                <p id="pos-wa-why" className="pos-muted-small">
                  {!waEnabled
                    ? preview.whatsapp?.reason
                    : waContacts.length === 0
                      ? `No WhatsApp number on file for ${supplier}.`
                      : `To ${waContacts.find((c) => c.id === whatsappContactId)?.label || waContacts[0].label}.`}
                </p>
              </div>
              )}

              <div className="pos-field">
                <span className="pos-label">Subject</span>
                <p className="pos-subject">{preview.subject}</p>
              </div>

              <div className="pos-field">
                <label className="pos-label" htmlFor="pos-note">
                  Add a note <span className="pos-muted-small">(optional - goes in the message)</span>
                </label>
                <textarea
                  id="pos-note"
                  rows={2}
                  maxLength={(preview.max_note || 1000) + 50}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  disabled={sending}
                  placeholder="e.g. Please deliver Tuesday morning."
                />
                <span className="pos-label pos-label-gap">Message</span>
                <pre className="pos-message" aria-label="Email message">
                  {body}
                </pre>
              </div>

              <div className="pos-field pos-attachment">
                <span className="pos-label">Attached</span>
                <div className="pos-attachment-row">
                  <span className="pos-file">
                    {preview.pdf_filename} · {preview.item_count} {preview.item_count === 1 ? "item" : "items"}
                    {preview.zero_qty_lines > 0 && (
                      <span className="pos-muted-small">
                        {" "}
                        ({preview.zero_qty_lines} at 0 left off)
                      </span>
                    )}
                  </span>
                  <button
                    type="button"
                    className="link-button"
                    onClick={togglePdf}
                    aria-expanded={pdf.open}
                    disabled={!preview.item_count}
                  >
                    {pdf.open ? "Hide PDF" : "Preview PDF"}
                  </button>
                </div>
                {pdf.open && (
                  <div className="pos-pdf">
                    {pdf.loading && <p className="muted">Building the PDF…</p>}
                    {pdf.error && <p className="error">{pdf.error}</p>}
                    {pdf.url && (
                      <>
                        <iframe className="pos-pdf-frame" title={`PO PDF for ${supplier}`} src={pdf.url} />
                        <a href={pdf.url} target="_blank" rel="noopener noreferrer" className="pos-muted-small">
                          Open the PDF in a new tab
                        </a>
                      </>
                    )}
                  </div>
                )}
              </div>

              <p className="pos-muted-small pos-what">
                Sending emails the PDF, then records the order as placed (like <strong>Order placed</strong>) and
                takes these lines out of the cart. If it doesn't go out, nothing changes.
              </p>

              {preview.recent_sends?.length > 0 && !needsCheck && (
                <p className="pos-muted-small">
                  Last send: <SendStatusPill status={preview.recent_sends[0].status} />{" "}
                  {formatRestaurantTime(preview.recent_sends[0].created_at)}
                  {preview.recent_sends[0].sent_by_name ? ` by ${preview.recent_sends[0].sent_by_name}` : ""}
                </p>
              )}
            </>
          )}

          {/* Not confirmed: the callout at the top says it (with what to do). */}
          {error && !needsCheck && (
            <p className="error pos-error" role="alert">
              {error}
            </p>
          )}
          {/* The "No email on file" callout above already says it. */}
          {preview && blocker && !error && options.length > 0 && (
            <p className="pos-muted-small pos-blocker" aria-live="polite">
              {blocker}
            </p>
          )}
        </div>

        <div className="fly-modal-actions">
          <button type="button" className="btn-secondary" data-autofocus onClick={onClose} disabled={sending || marking}>
            Cancel
          </button>
          {preview && (
            <button type="button" className="pos-send-btn" onClick={send} disabled={!canSend}>
              {sendLabel}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
