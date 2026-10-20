import { useEffect, useMemo, useState } from "react";
import InvoiceCloseSuggestions from "./suggestions/InvoiceCloseSuggestions.jsx";
import {
  adminDeleteInvoice,
  approveDeleteInvoice,
  cancelDeleteRequest,
  getInvoiceSubmission,
  getInvoiceSubmissions,
  getInvoiceTrash,
  getSuppliers,
  markInvoiceToastClosed,
  overrideInvoiceStatus,
  permanentlyDeleteInvoice,
  requestDeleteInvoice,
  resendInvoiceSubmission,
  restoreInvoice,
  submitInvoiceWithProgress,
  unmarkInvoiceToastClosed,
} from "../api";
import { useAuth } from "../AuthContext.jsx";
import InfoBlock from "./InfoBlock.jsx";
import { INVOICE_STATUS_TERMS, Term, TermInfo } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { formatRestaurantTime, storeDateTimeFormat } from "../format.js";
import { usePosName, useStoreAllows } from "../store/StoreContext.jsx";

// Which send channels this store has (store/gating.js): WhatsApp (store
// feature `whatsapp`) and the email to the POS's AP inbox (`ap_inbox`). A
// channel that's off is neither shown nor waited on nor resent.
const ALL_CHANNELS = { wa: true, ap: true };
function useInvoiceChannels() {
  const wa = useStoreAllows("whatsapp");
  const ap = useStoreAllows("invoice_ap_inbox");
  return useMemo(() => ({ wa, ap }), [wa, ap]);
}

// Store-local calendar date for a timestamp (e.g. "Sep 30, 2026").
function formatStoreDate(iso) {
  return iso ? storeDateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric" }).format(new Date(iso)) : "-";
}

// Every type the backend can return, for labels in the history tables.
const TYPE_OPTIONS = [
  { value: "invoice", label: "Invoice" },
  { value: "return", label: "Return" },
  { value: "other", label: "Something else" },
  // Store <-> container stock movement (owner, Oct 2026): WhatsApp only, no
  // Toast email, needs a direction, vendor optional. See backend's
  // TRANSFER_TYPE / NO_TOAST_TYPES.
  { value: "transfer", label: "Transfer" },
  // No longer offered for NEW submissions (owner: "remove untracked") but
  // old rows keep it and the backend still accepts it - kept here so they
  // still display correctly. WhatsApp only, never emailed to Toast.
  { value: "untracked", label: "Untracked" },
];
// What the Submit form offers - "untracked" is deliberately left out.
const PICKER_TYPES = TYPE_OPTIONS.filter((t) => t.value !== "untracked");

const TRANSFER_DIRECTIONS = [
  { value: "store_to_container", label: "Store \u2192 Container" },
  { value: "container_to_store", label: "Container \u2192 Store" },
];

// Types that never get a Toast email (backend's NO_TOAST_TYPES). Prefers
// the backend's own toast_applicable flag, falling back for older payloads.
function skipsToast(entry) {
  if (typeof entry.toast_applicable === "boolean") return !entry.toast_applicable;
  return entry.submission_type === "untracked" || entry.submission_type === "transfer";
}

// "Transfer: Store -> Container" for a transfer, else the plain type label.
function typeLabel(entry) {
  if (entry.type_label) return entry.type_label;
  return TYPE_OPTIONS.find((t) => t.value === entry.submission_type)?.label || entry.submission_type;
}

function noToastLabel(entry) {
  return entry.submission_type === "transfer" ? "Transfer" : "Untracked";
}

// A channel is "done" once it either succeeded or produced a real error -
// WhatsApp/Toast email now send in the background (see routers/invoices.py),
// so right after Submit/Resend both are neither yet - that's "pending", a
// third state distinct from "failed", not just sent=false.
function channelDone(sent, error) {
  return sent || !!error;
}

// An "untracked" or "transfer" submission never gets a Toast email attempt
// at all (see backend's NO_TOAST_TYPES) - without this, isResolved() would
// wait forever on a Toast channel that will never resolve, polling until
// the window times out on every single one of them.
function isResolved(entry, ch = ALL_CHANNELS) {
  const toastApplicable = ch.ap && !skipsToast(entry);
  return (
    (!ch.wa || channelDone(entry.whatsapp_sent, entry.whatsapp_error)) &&
    (!toastApplicable || channelDone(entry.toast_email_sent, entry.toast_email_error))
  );
}

// Polls GET /api/invoices/{id} until both channels resolve or the window
// runs out (WhatsApp alone can take up to ~2 minutes to time out - see
// whapi_client.py - so this covers a bit more than that). If it gives up
// early, the real outcome is still saved server-side and shows up on the
// next page load/refresh either way - this is just for live feedback.
async function pollInvoiceUntilResolved(id, onUpdate, { intervalMs = 3000, maxAttempts = 45, ch = ALL_CHANNELS } = {}) {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    let entry;
    try {
      entry = await getInvoiceSubmission(id);
    } catch {
      return;
    }
    onUpdate(entry);
    if (isResolved(entry, ch)) return;
  }
}

// overriddenBy is set once an admin has manually overridden this channel's
// status (see override_channel_status on the backend) rather than a real
// Whapi/Toast-email attempt - shown as a small suffix so staff never mistake
// an admin's manual call for a confirmed real send.
// compact=true is the "Past submissions" table's own narrow WhatsApp/Toast
// columns - real feedback was that those columns don't need to take up real
// row space when everything's fine, only when something needs attention.
// A sent/pending/uncertain channel collapses to a small icon (full detail
// still available via the native title tooltip on hover) so the column can
// shrink to almost nothing; a genuine failure is the one state that still
// gets a short visible label, since that's exactly the case worth noticing
// at a glance. Never lets a wrapped label grow row height either way - see
// .invoice-status/.invoice-status-icon's own white-space: nowrap.
// The full-size pill (not the compact table icon, which the legend above
// the table explains) gets an (i) with its definition.
function StatusPill(props) {
  const { sent, error, ambiguous, compact } = props;
  if (compact) return <StatusPillInner {...props} />;
  const k = sent ? "invoice_sent" : ambiguous ? "invoice_uncertain" : error ? "invoice_failed" : "invoice_sending";
  return (
    <Term k={k}>
      <StatusPillInner {...props} />
    </Term>
  );
}

function StatusPillInner({ sent, error, ambiguous, overriddenBy, compact }) {
  const suffix = overriddenBy ? ` (set by ${overriddenBy})` : "";
  if (sent) {
    const title = overriddenBy ? `Manually marked Sent by ${overriddenBy}` : "Sent";
    if (compact) {
      return (
        <span className="invoice-status-icon invoice-status-icon-ok" title={title}>
          ✓
        </span>
      );
    }
    return (
      <span className="invoice-status invoice-status-ok" title={overriddenBy ? title : undefined}>
        Sent{suffix}
      </span>
    );
  }
  if (ambiguous) {
    // Whapi didn't confirm in time, and reconciliation (see whapi_client.py)
    // couldn't find the message in the chat's own history either - shown
    // distinctly from a confirmed failure so staff don't assume it needs
    // resending without checking WhatsApp first, which risks a duplicate.
    const label = "Uncertain - check WhatsApp";
    if (compact) {
      return (
        <span className="invoice-status-icon invoice-status-icon-pending" title={error || label}>
          ?
        </span>
      );
    }
    return (
      <span className="invoice-status invoice-status-pending" title={error}>
        {label}
      </span>
    );
  }
  if (error) {
    // A real problem - always worth a visible (if short) label, compact or
    // not, matching the ask that these columns only need to stand out when
    // there's actually something to notice.
    return (
      <span className="invoice-status invoice-status-failed" title={`${error}${suffix}`}>
        Failed
      </span>
    );
  }
  if (compact) {
    return (
      <span className="invoice-status-icon invoice-status-icon-pending" title="Sending…">
        …
      </span>
    );
  }
  return <span className="invoice-status invoice-status-pending">Sending…</span>;
}

// Short labels on purpose - real feedback that the Override column was too
// wide. The channel name (WA/Toast) lives in the placeholder option itself
// now (see channelLabel below) instead of a separate sibling label element,
// so there's nothing else taking up row width beside the select itself.
const OVERRIDE_OPTIONS = [
  { value: "sent", label: "Sent" },
  { value: "failed", label: "Failed" },
  { value: "pending", label: "Pending" },
];

// Admin-only manual status control - the safety valve for when the real
// automated attempt (WhatsApp/Toast email) is stuck or unreliable. A plain
// <select> that resets to its placeholder after firing, rather than a
// controlled value reflecting current status, since this is an action
// ("do this now"), not a persistent setting.
function OverrideSelect({ channelLabel, busy, onChoose }) {
  return (
    <select
      value=""
      disabled={busy}
      onChange={(e) => {
        const status = e.target.value;
        e.target.value = "";
        if (status) onChoose(status);
      }}
      className="invoice-override-select"
      title={`Manually override ${channelLabel === "WA" ? "WhatsApp" : `${channelLabel} email`} status`}
    >
      <option value="">{channelLabel}</option>
      {OVERRIDE_OPTIONS.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

const PROCESSING_STATUS_LABELS = {
  pending: "Pending",
  in_progress: "In Progress",
  completed: "Completed",
  failed: "Failed",
};

function ProcessingStatusBadge({ status }) {
  const label = PROCESSING_STATUS_LABELS[status] || status;
  return (
    <Term k={INVOICE_STATUS_TERMS[status]}>
      <span className={`invoice-processing-status invoice-processing-status-${status}`}>
        {label}
      </span>
    </Term>
  );
}

// The form's one-line summary for this store's channels.
function invoiceBrief(ch, pos) {
  const parts = [];
  if (ch.wa) parts.push("sends a WhatsApp message with a link to the file");
  if (ch.ap) parts.push(`emails ${pos} so it can draft the bill automatically`);
  return parts.length ? `Submitting ${parts.join(", and ")}.` : "Submitting saves the file and its details for the team.";
}

function sendsText(ch, pos) {
  return [ch.wa && "WhatsApp", ch.ap && `the ${pos} email`].filter(Boolean).join(" and ").replace(/^./, (c) => c.toUpperCase());
}

function SubmissionForm({ suppliers, onSubmitted }) {
  const { user, store } = useAuth();
  const ch = useInvoiceChannels();
  const pos = usePosName();
  const [vendor, setVendor] = useState("");
  const [vendorFocused, setVendorFocused] = useState(false);
  const [submissionType, setSubmissionType] = useState("invoice");
  const [transferDirection, setTransferDirection] = useState("");
  const isTransfer = submissionType === "transfer";
  const [totalAmount, setTotalAmount] = useState("");
  const [notes, setNotes] = useState("");
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Was a native <input list> + <datalist> - real report: Chrome on Android
  // (and mobile browsers generally) has long-standing, inconsistent support
  // for <datalist> and can simply show no suggestions at all, with no error
  // to notice. A plain client-side filtered dropdown (matching this app's
  // existing .item-picker pattern used elsewhere) behaves identically
  // everywhere since it's just React state, not a native form control a
  // browser vendor has to bother implementing well. The list is small and
  // already loaded, so this filters in-memory rather than debouncing a
  // server search like the item-picker's other use does.
  const vendorMatches = useMemo(() => {
    const needle = vendor.trim().toLowerCase();
    const pool = needle ? suppliers.filter((s) => s.toLowerCase().includes(needle)) : suppliers;
    return pool.slice(0, 20);
  }, [vendor, suppliers]);
  const showVendorDropdown = vendorFocused && vendorMatches.length > 0;

  // A transfer needs a direction instead of a vendor (vendor is optional and
  // the backend fills in the store's own name when it's left blank).
  const canSubmit = !!file && !submitting && (isTransfer ? !!transferDirection : !!vendor.trim());

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setProgress(0);
    setProcessing(false);
    setResult(null);
    setError(null);
    try {
      const data = await submitInvoiceWithProgress(
        {
          vendor: vendor.trim(),
          submissionType,
          totalAmount,
          notes,
          transferDirection: isTransfer ? transferDirection : null,
        },
        file,
        (pct) => {
          setProgress(pct);
          if (pct >= 100) setProcessing(true);
        }
      );
      setResult(data);
      setVendor("");
      setTransferDirection("");
      setTotalAmount("");
      setNotes("");
      setFile(null);
      e.target.reset();
      onSubmitted?.();
      if (!isResolved(data, ch)) {
        pollInvoiceUntilResolved(data.id, (updated) => {
          setResult(updated);
          onSubmitted?.();
        });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
      setProcessing(false);
    }
  }

  return (
    <section className="card">
      <h2>Submit an invoice</h2>
      <InfoBlock brief={invoiceBrief(ch, pos)}>
        The record is saved instantly.
        {(ch.wa || ch.ap) && (
          <>
            {" "}
            {sendsText(ch, pos)} send in the background right after, since either can take a while, so Submit
            doesn't sit and wait on them. You'll see "Sending…" for a few seconds until each one resolves, live,
            with no need to refresh. The record is always saved even if a send ultimately fails - a failed send
            shows up in the list below with a "Resend" button, so nothing has to be resubmitted from scratch.
          </>
        )}{" "}
        Choosing <strong>Transfer</strong> (stock moved Store {"\u2192"} Container or Container {"\u2192"} Store)
        {ch.ap ? ` skips the ${pos} email entirely -` : ""}
        {ch.wa ? " the team hears about it over WhatsApp only, it" : " it"} needs a direction, the vendor is
        optional, and it never counts as a vendor delivery.
      </InfoBlock>
      <form onSubmit={handleSubmit} className="recon-form">
        <label className="recon-form-field recon-form-field-wide item-picker">
          {isTransfer ? "Vendor (optional for a transfer)" : "Vendor"}
          <input
            type="text"
            value={vendor}
            onChange={(e) => setVendor(e.target.value)}
            onFocus={() => setVendorFocused(true)}
            onBlur={() => setVendorFocused(false)}
            placeholder={isTransfer ? `Leave blank for ${store.name}` : "Start typing a vendor name…"}
            autoComplete="off"
            required={!isTransfer}
          />
          {showVendorDropdown && (
            <ul className="item-picker-results">
              {vendorMatches.map((s) => (
                <li key={s}>
                  <button
                    type="button"
                    // Fires before the input's onBlur (which would otherwise
                    // close this dropdown first and swallow the click).
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setVendor(s);
                      setVendorFocused(false);
                    }}
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </label>
        <div className="recon-form-field">
          Type
          <div className="invoice-type-options">
            {PICKER_TYPES.map((opt) => (
              <label
                key={opt.value}
                className={`invoice-type-option${
                  submissionType === opt.value ? " invoice-type-option-selected" : ""
                }`}
              >
                <input
                  type="radio"
                  name="invoice-submission-type"
                  checked={submissionType === opt.value}
                  onChange={() => setSubmissionType(opt.value)}
                />
                {opt.label}
              </label>
            ))}
          </div>
        </div>
        {isTransfer && (
          <div className="recon-form-field">
            Direction
            <div className="invoice-type-options">
              {TRANSFER_DIRECTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={`invoice-type-option${
                    transferDirection === opt.value ? " invoice-type-option-selected" : ""
                  }`}
                >
                  <input
                    type="radio"
                    name="invoice-transfer-direction"
                    checked={transferDirection === opt.value}
                    onChange={() => setTransferDirection(opt.value)}
                    required
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          </div>
        )}
        <label className="recon-form-field">
          Total
          <input
            type="number"
            min="0"
            step="0.01"
            value={totalAmount}
            onChange={(e) => setTotalAmount(e.target.value)}
            placeholder="optional"
          />
        </label>
        <label className="recon-form-field">
          Submitted by
          <input type="text" value={user?.username || ""} disabled />
        </label>
        <label className="recon-form-field recon-form-field-wide">
          Notes
          <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="optional" />
        </label>
        <label className="recon-form-field recon-form-field-wide">
          File (PDF or photo)
          <input
            type="file"
            accept=".pdf,image/*"
            disabled={submitting}
            onChange={(e) => setFile(e.target.files[0] || null)}
            required
          />
        </label>
        <div className="recon-form-submit">
          <button type="submit" disabled={!canSubmit}>
            {submitting ? (processing ? "Processing…" : `Uploading… ${progress}%`) : "Submit"}
          </button>
        </div>
      </form>
      {submitting && (
        <div className="progress-bar" style={{ marginTop: "0.75rem" }}>
          <div
            className={`progress-bar-fill${processing ? " progress-bar-fill-indeterminate" : ""}`}
            style={{ width: processing ? "100%" : `${progress}%` }}
          />
        </div>
      )}
      {result && (
        <div className="invoice-submit-result">
          <p className="success">{isResolved(result, ch) ? "Submitted." : "Submitted - sending now…"}</p>
          <p>
            <span className="muted">{typeLabel(result)}</span>{" "}
            <ProcessingStatusBadge status={result.processing_status} />
          </p>
          <ul>
            {ch.wa && (
            <li>
              WhatsApp:{" "}
              <StatusPill
                sent={result.whatsapp_sent}
                error={result.whatsapp_error}
                ambiguous={result.whatsapp_ambiguous}
              />
              {!result.whatsapp_sent && result.whatsapp_error && (
                <span className="muted"> - {result.whatsapp_error}</span>
              )}
            </li>
            )}
            {ch.ap && (
            <li>
              {pos} email:{" "}
              {skipsToast(result) ? (
                <span className="muted">Not applicable ({noToastLabel(result)})</span>
              ) : (
                <>
                  <StatusPill sent={result.toast_email_sent} error={result.toast_email_error} />
                  {!result.toast_email_sent && result.toast_email_error && (
                    <span className="muted"> - {result.toast_email_error}</span>
                  )}
                </>
              )}
            </li>
            )}
          </ul>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function DeleteActions({ item, isAdmin, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);

  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (item.delete_requested_at) {
    if (!isAdmin) {
      return (
        <Term k="invoice_delete_requested">
          <span className="muted invoice-delete-status">Delete requested</span>
        </Term>
      );
    }
    return (
      <div className="invoice-delete-cell">
        <Term k="invoice_delete_requested">
          <span className="muted invoice-delete-status">Requested by {item.delete_requested_by_name || "someone"}</span>
        </Term>
        <div>
          <button type="button" className="link-button" disabled={busy} onClick={() => run(() => approveDeleteInvoice(item.id))}>
            Approve
          </button>{" "}
          <button type="button" className="link-button" disabled={busy} onClick={() => run(() => cancelDeleteRequest(item.id))}>
            Reject
          </button>
        </div>
        {error && <span className="error invoice-delete-status">{error}</span>}
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <button type="button" className="link-button link-danger" disabled={busy} onClick={() => run(() => requestDeleteInvoice(item.id))}>
        {busy ? "Requesting…" : "Request delete"}
      </button>
    );
  }

  if (confirming) {
    return (
      <div className="invoice-delete-cell">
        <span className="muted invoice-delete-status">Move to trash?</span>
        <div>
          <button type="button" className="link-button" disabled={busy} onClick={() => setConfirming(false)}>
            Cancel
          </button>{" "}
          <button type="button" disabled={busy} onClick={() => run(() => adminDeleteInvoice(item.id))}>
            {busy ? "Deleting…" : "Yes, delete"}
          </button>
        </div>
        {error && <span className="error invoice-delete-status">{error}</span>}
      </div>
    );
  }

  return (
    <button type="button" className="link-button link-danger" onClick={() => setConfirming(true)}>
      Delete
    </button>
  );
}

function InvoiceTrash({ refreshKey, onChanged }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);

  useEffect(() => {
    getInvoiceTrash()
      .then((res) => setItems(res.items))
      .catch((err) => setError(err.message));
  }, [refreshKey]);

  async function handleRestore(id) {
    setBusyId(id);
    setError(null);
    try {
      await restoreInvoice(id);
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  async function handlePermanentDelete(id) {
    setBusyId(id);
    setError(null);
    try {
      await permanentlyDeleteInvoice(id);
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
      setConfirmId(null);
    }
  }

  // Nothing in the trash - don't clutter the page with an empty card.
  if (items && items.length === 0) return null;

  return (
    <section className="card">
      <h2>
        Trash <TermInfo k="invoice_trash" label="Trash" />
      </h2>
      <p className="muted" style={{ marginTop: 0, marginBottom: "0.75rem" }}>
        Deleted submissions stay here, recoverable, until permanently deleted.
      </p>
      {error && <p className="error">{error}</p>}
      {items === null && <p className="muted">Loading…</p>}
      {items && items.length > 0 && (
        <div className="table-wrap">
          <table className="kc-table--stack">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Type</th>
                <th>Deleted</th>
                <th>Deleted by</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.vendor}</td>
                  <td>{typeLabel(item)}</td>
                  <td>{formatRestaurantTime(item.deleted_at) || "-"}</td>
                  <td>{item.deleted_by_name || "-"}</td>
                  <td>
                    {confirmId === item.id ? (
                      <>
                        <button
                          type="button"
                          className="link-button"
                          disabled={busyId === item.id}
                          onClick={() => setConfirmId(null)}
                        >
                          Cancel
                        </button>{" "}
                        <button type="button" disabled={busyId === item.id} onClick={() => handlePermanentDelete(item.id)}>
                          {busyId === item.id ? "Deleting…" : "Yes, delete forever"}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="link-button"
                          disabled={busyId === item.id}
                          onClick={() => handleRestore(item.id)}
                        >
                          {busyId === item.id ? "Restoring…" : "Restore"}
                        </button>{" "}
                        <button type="button" className="link-button link-danger" onClick={() => setConfirmId(item.id)}>
                          Delete permanently
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SubmissionsTable({ items, onResend, isAdmin, onDataChanged }) {
  const ch = useInvoiceChannels();
  const pos = usePosName();
  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(items, {
    searchKeys: ["vendor", "type_label", "submitted_by_name", "notes"],
    defaultSortKey: "created_at",
    defaultSortDir: "desc",
  });
  const [resendingId, setResendingId] = useState(null);
  const [closureBusyId, setClosureBusyId] = useState(null);
  const [overrideBusyKey, setOverrideBusyKey] = useState(null);

  async function handleResend(id, channels) {
    setResendingId(id);
    try {
      await onResend(id, channels);
    } finally {
      setResendingId(null);
    }
  }

  async function handleOverride(id, channel, status) {
    const key = `${id}:${channel}`;
    setOverrideBusyKey(key);
    try {
      await overrideInvoiceStatus(id, channel, status);
      onDataChanged();
    } finally {
      setOverrideBusyKey(null);
    }
  }

  async function handleMarkClosed(id) {
    setClosureBusyId(id);
    try {
      await markInvoiceToastClosed(id);
      onDataChanged();
    } finally {
      setClosureBusyId(null);
    }
  }

  async function handleUnmarkClosed(id) {
    setClosureBusyId(id);
    try {
      await unmarkInvoiceToastClosed(id);
      onDataChanged();
    } finally {
      setClosureBusyId(null);
    }
  }

  return (
    <section className="card">
      <h2>Past submissions</h2>
      <p className="muted invoice-legend">
        {[ch.wa && "WA", ch.ap && pos].filter(Boolean).join(" / ") || "Status"} columns: <Term k="invoice_sent">✓ Sent</Term> <Term k="invoice_sending">… Sending</Term>{" "}
        <Term k="invoice_uncertain">? Uncertain</Term> <Term k="invoice_failed">Failed</Term>{" "}
        <Term k="invoice_transfer">N/A = Transfer</Term> <Term k="invoice_untracked">or Untracked</Term>
      </p>
      <input
        type="text"
        placeholder="Search vendor, submitted by, notes…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: "0.75rem" }}
      />
      <div className="table-wrap">
        <table className="kc-table--stack">
          <thead>
            <tr>
              <SortableTh sortKey="created_at" currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort}>
                Date
              </SortableTh>
              <SortableTh sortKey="vendor" currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort}>
                Vendor
              </SortableTh>
              <th>Type</th>
              <SortableTh sortKey="total_amount" currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="right">
                Total
              </SortableTh>
              <th>Submitted by</th>
              <th>Status</th>
              {ch.ap && (
                <th className="invoice-col-narrow" title={`Marked closed in ${pos}`}>
                  <Term k="invoice_closed_in_toast">Closed</Term>
                </th>
              )}
              <th>File</th>
              {ch.wa && (
                <th className="invoice-col-narrow" title="WhatsApp">
                  WA
                </th>
              )}
              {ch.ap && (
                <th className="invoice-col-narrow" title={`${pos} email`}>
                  {pos}
                </th>
              )}
              <th>Resend</th>
              {isAdmin && (
                <th className="invoice-col-narrow" title="Admin-only: manually override a channel's status">
                  <Term k="invoice_override">Override</Term>
                </th>
              )}
              <th>Delete</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => {
              const failedChannels = [
                ch.wa && !item.whatsapp_sent && item.whatsapp_error && "whatsapp",
                ch.ap && !skipsToast(item) && !item.toast_email_sent && item.toast_email_error && "toast_email",
              ].filter(Boolean);
              return (
                <tr key={item.id}>
                  <td>{formatStoreDate(item.created_at)}</td>
                  <td>{item.vendor}</td>
                  <td>{typeLabel(item)}</td>
                  <td style={{ textAlign: "right" }}>
                    {item.total_amount != null ? `$${item.total_amount.toFixed(2)}` : "$Unknown"}
                  </td>
                  <td>{item.submitted_by_name || "-"}</td>
                  <td>
                    <ProcessingStatusBadge status={item.processing_status} />
                  </td>
                  {ch.ap && (
                  <td className="invoice-col-narrow">
                    {item.toast_closed_at ? (
                      <button
                        type="button"
                        className="invoice-status-icon invoice-status-icon-ok invoice-status-icon-button"
                        disabled={closureBusyId === item.id}
                        onClick={() => handleUnmarkClosed(item.id)}
                        title={`Closed by ${item.toast_closed_by_name || "someone"} - click to undo`}
                      >
                        ✓
                      </button>
                    ) : item.can_mark_toast_closed ? (
                      <>
                        <button
                          type="button"
                          className="invoice-status-icon invoice-status-icon-pending invoice-status-icon-button"
                          disabled={closureBusyId === item.id}
                          onClick={() => handleMarkClosed(item.id)}
                          title={
                            item.close_suggestion
                              ? `${item.close_suggestion.reason} Click to mark closed in ${pos}.`
                              : `Click to mark closed in ${pos}`
                          }
                        >
                          {closureBusyId === item.id ? "…" : "○"}
                        </button>
                        {item.close_suggestion && (
                          <span className="pill pill-info sugg-pill" title={item.close_suggestion.reason}>
                            Looks closed
                          </span>
                        )}
                      </>
                    ) : skipsToast(item) ? (
                      <span className="muted" title={`${noToastLabel(item)} submissions are never emailed to ${pos}`}>
                        N/A
                      </span>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                  )}
                  <td>
                    <a href={item.file_url} target="_blank" rel="noopener noreferrer">
                      View
                    </a>
                  </td>
                  {ch.wa && (
                  <td className="invoice-col-narrow">
                    <StatusPill
                      compact
                      sent={item.whatsapp_sent}
                      error={item.whatsapp_error}
                      ambiguous={item.whatsapp_ambiguous}
                      overriddenBy={item.whatsapp_status_overridden_by}
                    />
                  </td>
                  )}
                  {ch.ap && (
                  <td className="invoice-col-narrow">
                    {skipsToast(item) ? (
                      <span className="muted" title={`${noToastLabel(item)} submissions never get a ${pos} email`}>
                        N/A
                      </span>
                    ) : (
                      <StatusPill
                        compact
                        sent={item.toast_email_sent}
                        error={item.toast_email_error}
                        overriddenBy={item.toast_email_status_overridden_by}
                      />
                    )}
                  </td>
                  )}
                  <td>
                    {failedChannels.length > 0 && (
                      <button
                        type="button"
                        className="link-button"
                        disabled={resendingId === item.id}
                        onClick={() => handleResend(item.id, failedChannels)}
                      >
                        {resendingId === item.id ? "Resending…" : "Resend"}
                      </button>
                    )}
                  </td>
                  {isAdmin && (
                    <td className="invoice-col-narrow">
                      <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                        {ch.wa && (
                          <OverrideSelect
                            channelLabel="WA"
                            busy={overrideBusyKey === `${item.id}:whatsapp`}
                            onChoose={(status) => handleOverride(item.id, "whatsapp", status)}
                          />
                        )}
                        {ch.ap && !skipsToast(item) && (
                          <OverrideSelect
                            channelLabel={pos}
                            busy={overrideBusyKey === `${item.id}:toast_email`}
                            onChoose={(status) => handleOverride(item.id, "toast_email", status)}
                          />
                        )}
                      </div>
                    </td>
                  )}
                  <td>
                    <DeleteActions item={item} isAdmin={isAdmin} onChanged={onDataChanged} />
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={isAdmin ? 12 : 11} className="muted">
                  No submissions {search ? "match your search" : "yet"}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function InvoiceSubmission() {
  const { user } = useAuth();
  const ch = useInvoiceChannels();
  const [items, setItems] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [trashKey, setTrashKey] = useState(0);

  const loadItems = useMemo(
    () => () =>
      getInvoiceSubmissions()
        .then((res) => setItems(res.items))
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false)),
    []
  );

  useEffect(() => {
    loadItems();
    getSuppliers()
      .then((res) => setSuppliers(res.suppliers))
      .catch(() => setSuppliers([]));
  }, [loadItems]);

  async function handleResend(id, channels) {
    const data = await resendInvoiceSubmission(id, channels);
    await loadItems();
    if (!isResolved(data, ch)) {
      await pollInvoiceUntilResolved(id, loadItems, { ch });
    }
  }

  // Shared by both the main table's delete/approve/reject actions and the
  // Trash section's restore/permanent-delete - either side can move a
  // submission between the two lists, so both need to refresh together.
  async function refreshAll() {
    await loadItems();
    setTrashKey((k) => k + 1);
  }

  return (
    <>
      <SubmissionForm suppliers={suppliers} onSubmitted={loadItems} />
      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}
      {/* "Looks closed in <pos>" only matters with the AP inbox channel. */}
      {!loading && !error && ch.ap && (
        <InvoiceCloseSuggestions items={items} onChanged={refreshAll} readOnly={!!user?.demo} />
      )}
      {!loading && !error && (
        <SubmissionsTable items={items} onResend={handleResend} isAdmin={!!user?.is_admin} onDataChanged={refreshAll} />
      )}
      {user?.is_admin && <InvoiceTrash refreshKey={trashKey} onChanged={refreshAll} />}
    </>
  );
}
