// Critical vendors hidden from Today: Close / Skip this week / Remind me later
// (docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md). An admin hides a Critical
// vendor from the app's Home Critical tile and the morning critical-vendors
// notification - for everyone. Reorder Candidates still lists the vendor,
// with a "Hidden from Today" tag. The rules (when each one comes back) live
// in the backend (services/critical_dismissals.py) and its glossary terms.
import { useCallback, useEffect, useRef, useState } from "react";
import { getCriticalDismissals, hideCriticalVendor, postOrderAction, undoOrderPlacedOutside } from "../api";
import { formatRestaurantTime } from "../format.js";
import { CRITICAL_DISMISS_TERMS, Term, TermInfo } from "../glossary.jsx";
import { markGroupPlacedOutside, unmarkGroupPlacedOutside } from "./orderActions/orderActionsModel.js";

// Fallbacks if the options didn't load (the server sends the same lists).
const DEFAULT_REASONS = [
  { key: "ordered", label: "Ordered / will order", needs_note: false },
  { key: "not_ordering", label: "Not ordering from this vendor right now", needs_note: false },
  { key: "counts_wrong", label: "Stock counts are wrong", needs_note: false },
  { key: "other", label: "Other", needs_note: true },
];
const DEFAULT_PRESETS = [
  { key: "later_today", label: "Later today" },
  { key: "tomorrow", label: "Tomorrow 9 AM" },
  { key: "in_3_days", label: "In 3 days (9 AM)" },
  { key: "date", label: "Pick a date" },
];
const KINDS = [
  { key: "close", label: "Close" },
  { key: "skip_week", label: "Skip this week" },
  { key: "snooze", label: "Remind me later" },
];
const NOTE_MAX = 500;

export function vendorKey(name) {
  return (name || "").trim().toLowerCase();
}

// What a vendor group's `critical_dismissal` holds, from a POST result.
export function groupDismissal(d) {
  const keys = ["id", "kind", "kind_label", "ends_label", "created_by_name", "until", "reason", "reason_label", "note", "created_at"];
  return Object.fromEntries(keys.map((k) => [k, d[k] ?? null]));
}

function whyText(d) {
  const reason = d.reason_label ? `${d.reason_label}${d.note ? `: ${d.note}` : ""}` : d.note || "";
  return reason;
}

// Buttons inside a vendor row's <summary>: act without opening / closing it.
function quiet(fn) {
  return {
    onClick: (e) => {
      e.preventDefault();
      e.stopPropagation();
      fn();
    },
    onMouseDown: (e) => e.stopPropagation(),
    onKeyDown: (e) => e.stopPropagation(),
  };
}

// Active dismissals (+ the 60-day history for admins) and the choices the
// actions offer.
export function useCriticalDismissals(isAdmin) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const reload = useCallback(
    () =>
      getCriticalDismissals(isAdmin)
        .then((data) => {
          setState({ data, error: null, loading: false });
          return data;
        })
        .catch((err) => {
          setState((s) => ({ ...s, error: err.message, loading: false }));
          return null;
        }),
    [isAdmin],
  );
  useEffect(() => {
    reload();
  }, [reload]);
  return { ...state, reload };
}

// "Hidden from Today: Until Mon Oct 12" on a vendor row, with Show again
// for admins.
export function HiddenChip({ dismissal, canEdit, busy, onShowAgain }) {
  const d = dismissal;
  const why = whyText(d);
  const title = `${d.kind_label}${why ? ` (${why})` : ""}${d.created_by_name ? ` by ${d.created_by_name}` : ""}`;
  return (
    <span className="reorder-hidden-chip">
      <Term k={CRITICAL_DISMISS_TERMS[d.kind]} label={d.kind_label}>
        <span className="pill reorder-hidden-pill" title={title}>
          Hidden from Today: {d.ends_label}
        </span>
      </Term>
      {canEdit && (
        <button type="button" className="link-button reorder-show-again" disabled={busy} {...quiet(onShowAgain)}>
          {busy ? "Showing…" : "Show again"}
        </button>
      )}
    </span>
  );
}

// "Hide from Today: Order already placed · Skip this week · Remind me later
// · Close · Other" on a critical vendor row (admins). Order already placed
// (W1) records the order as placed outside the app, right away (with Undo);
// the others open the panel (Other = Close with the reason Other).
const ROW_ACTIONS = [
  { key: "skip_week", label: "Skip this week" },
  { key: "snooze", label: "Remind me later" },
  { key: "close", label: "Close" },
  { key: "other", label: "Other", kind: "close", reason: "other" },
];

export function HideActions({ vendor, onPick, onPlaced, placing = false }) {
  return (
    <span className="reorder-hide-actions" role="group" aria-label={`Hide ${vendor} from Today`}>
      <span className="reorder-hide-label">Hide from Today:</span>
      {onPlaced && (
        <button
          type="button"
          className="btn-secondary reorder-hide-btn reorder-placed-btn"
          disabled={placing}
          title="The order went in another way (phone, WhatsApp, the rep). Shows the vendor as Ordered."
          {...quiet(onPlaced)}
        >
          {placing ? "Saving…" : "Order already placed"}
        </button>
      )}
      {ROW_ACTIONS.map((k) => (
        <button
          key={k.key}
          type="button"
          className="btn-secondary reorder-hide-btn"
          {...quiet(() => onPick(k.kind || k.key, k.reason))}
        >
          {k.label}
        </button>
      ))}
    </span>
  );
}

// "Order already placed" (W1, services/order_actions.py): saves at once,
// shows the vendor as Ordered in the loaded report (what the server says on
// the next Calculate) and offers Undo. `setData` / `setUndo` are the page's.
export function usePlacedOutside({ setData, setUndo, setActionError, reloadDismissals }) {
  const [busy, setBusy] = useState(null); // vendor key, or "order:<id>" while undoing
  const mapVendor = useCallback(
    (vendor, fn) =>
      setData((d) =>
        d ? { ...d, vendors: d.vendors.map((g) => (vendorKey(g.vendor) === vendorKey(vendor) ? fn(g) : g)) } : d
      ),
    [setData]
  );

  const place = useCallback(
    async (v) => {
      const key = vendorKey(v.vendor);
      setBusy(key);
      setActionError(null);
      try {
        const result = await postOrderAction({ vendor: v.vendor, action: "placed" });
        mapVendor(v.vendor, (g) => markGroupPlacedOutside(g, result));
        setUndo({
          id: null,
          placedOrderId: result.order_id,
          vendor: result.vendor,
          message: `${result.vendor}: order marked as already placed. It shows as Ordered (off the Today Critical tile) until a delivery is recorded.`,
          warning: null,
        });
        reloadDismissals();
      } catch (err) {
        setActionError(`Couldn't mark ${v.vendor} as placed: ${err.message}`);
      } finally {
        setBusy(null);
      }
    },
    [mapVendor, reloadDismissals, setActionError, setUndo]
  );

  const undoPlaced = useCallback(
    async (orderId, vendor) => {
      setBusy(`order:${orderId}`);
      setActionError(null);
      const dropUndo = () => setUndo((u) => (u && u.placedOrderId === orderId ? null : u));
      try {
        await undoOrderPlacedOutside(orderId);
        dropUndo();
        let restored = false;
        mapVendor(vendor, (g) => {
          if (g.ordered_order_id !== orderId) return g;
          const before = unmarkGroupPlacedOutside(g);
          restored = before !== g;
          return before;
        });
        if (!restored) {
          setUndo({
            id: null,
            vendor,
            message: `${vendor} is no longer marked as placed. Calculate again to see its stock status.`,
            warning: null,
          });
        }
        reloadDismissals();
      } catch (err) {
        dropUndo();
        setActionError(`Couldn't undo ${vendor}: ${err.message}`);
      } finally {
        setBusy(null);
      }
    },
    [mapVendor, reloadDismissals, setActionError, setUndo]
  );

  return { busy, place, undoPlaced };
}

// "placed outside the app · Undo" after a vendor's Ordered pill.
export function OutsideOrderedTag({ group, canEdit, busy, onUndo }) {
  if (group.ordered_source !== "outside") return null;
  return (
    <span className="reorder-outside-tag">
      <Term k="order_placed_outside" label="Order already placed">
        <span className="muted">placed outside the app</span>
      </Term>
      {canEdit && group.ordered_order_id != null && (
        <button type="button" className="link-button reorder-show-again" disabled={busy} {...quiet(onUndo)}>
          {busy ? "Undoing…" : "Undo"}
        </button>
      )}
    </span>
  );
}

// The right-hand panel (same as Fix Issues' close panel): pick how, then why /
// until when, plus an optional note.
export function HideFromTodayDrawer({ vendor, criticalCount, initialKind, initialReason, options, onCancel, onDone }) {
  const [kind, setKind] = useState(initialKind || "close");
  const [reason, setReason] = useState(initialReason || "");
  const [preset, setPreset] = useState("tomorrow");
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const formRef = useRef(null);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  useEffect(() => {
    formRef.current?.querySelector("input[name='crit-kind']:checked")?.focus();
  }, []);

  const reasons = options?.reasons?.length ? options.reasons : DEFAULT_REASONS;
  const presets = options?.presets?.length ? options.presets : DEFAULT_PRESETS;
  const datePreset = presets.find((p) => p.key === "date") || {};
  const needsNote = kind === "close" && reasons.find((r) => r.key === reason)?.needs_note;
  const canSave =
    !saving &&
    (kind === "close" ? !!reason && (!needsNote || note.trim()) : kind === "snooze" ? preset !== "date" || !!date : true);

  const hints = {
    close: `Handled. Back when something new turns critical, ${options?.close_max_days ?? 14} days at most.`,
    skip_week: `Back when the new week starts${options?.skip_week?.ends_label ? ` (${options.skip_week.ends_label.replace(/^Until /, "")})` : " (Monday)"}.`,
    snooze: "Back at the time you pick.",
  };

  async function save(e) {
    e.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    const body = { vendor, kind };
    if (note.trim()) body.note = note.trim();
    if (kind === "close") body.reason = reason;
    if (kind === "snooze") {
      body.preset = preset;
      if (preset === "date") body.date = date;
    }
    try {
      onDone(await hideCriticalVendor(body));
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <form
        ref={formRef}
        className="issue-drawer crit-hide-drawer"
        onSubmit={save}
        role="dialog"
        aria-modal="true"
        aria-label={`Hide ${vendor} from Today`}
      >
        <div className="issue-drawer-head">
          <h3>Hide from Today</h3>
          <button type="button" className="link-button" onClick={onCancel} aria-label="Cancel">
            ✕
          </button>
        </div>
        <p className="muted issue-drawer-items">
          <strong className="crit-hide-vendor">{vendor}</strong>
          {criticalCount != null && ` · ${criticalCount} critical item${criticalCount === 1 ? "" : "s"}`}. Hidden for
          everyone from the Home Critical tile and the morning notification. Reorder Candidates still lists it.
        </p>

        <fieldset className="issue-reasons">
          <legend>How?</legend>
          {KINDS.map((k) => (
            <label key={k.key} className={`issue-reason${kind === k.key ? " selected" : ""}`}>
              <input type="radio" name="crit-kind" value={k.key} checked={kind === k.key} onChange={() => setKind(k.key)} />
              <span>
                {k.label}
                <TermInfo k={CRITICAL_DISMISS_TERMS[k.key]} label={k.label} />
                <small>{hints[k.key]}</small>
              </span>
            </label>
          ))}
        </fieldset>

        {kind === "close" && (
          <fieldset className="issue-reasons">
            <legend>Why is it handled?</legend>
            {reasons.map((r) => (
              <label key={r.key} className={`issue-reason${reason === r.key ? " selected" : ""}`}>
                <input type="radio" name="crit-reason" value={r.key} checked={reason === r.key} onChange={() => setReason(r.key)} />
                <span>{r.label}</span>
              </label>
            ))}
          </fieldset>
        )}

        {kind === "snooze" && (
          <fieldset className="issue-reasons">
            <legend>Remind me</legend>
            {presets.map((p) => (
              <label key={p.key} className={`issue-reason${preset === p.key ? " selected" : ""}`}>
                <input type="radio" name="crit-preset" value={p.key} checked={preset === p.key} onChange={() => setPreset(p.key)} />
                <span>
                  {p.label}
                  {p.key === "date" ? (
                    <small>9 AM that day, up to 30 days ahead.</small>
                  ) : (
                    p.ends_label && <small>{p.ends_label}</small>
                  )}
                </span>
              </label>
            ))}
            {preset === "date" && (
              <label className="issue-other">
                Date
                <input
                  type="date"
                  value={date}
                  min={datePreset.min_date || undefined}
                  max={datePreset.max_date || undefined}
                  onChange={(e) => setDate(e.target.value)}
                  required
                />
              </label>
            )}
          </fieldset>
        )}

        <label className="issue-other">
          {needsNote ? "Note (needed for Other)" : "Note (optional)"}
          <textarea
            rows={3}
            maxLength={NOTE_MAX}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={kind === "close" ? "e.g. Ordered by phone, arriving Thursday" : "e.g. Owner is ordering this one"}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="issue-drawer-actions">
          <button type="button" className="btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" disabled={!canSave}>
            {saving ? "Hiding…" : "Hide from Today"}
          </button>
        </div>
      </form>
    </div>
  );
}

// After an action: "Acme Foods hidden from Today: Until Mon Oct 12 [Undo]".
export function UndoBar({ message, warning, onUndo, onClose }) {
  const [undoing, setUndoing] = useState(false);
  useEffect(() => {
    const t = setTimeout(onClose, warning ? 15000 : 10000);
    return () => clearTimeout(t);
  }, [onClose, warning]);
  return (
    <div className="reorder-undo-bar" role="status" aria-live="polite">
      <span className="reorder-undo-text">
        {message}
        {warning && <span className="reorder-undo-warning">{warning}</span>}
      </span>
      {onUndo && (
        <button
          type="button"
          className="link-button"
          disabled={undoing}
          onClick={async () => {
            setUndoing(true);
            await onUndo();
          }}
        >
          {undoing ? "Undoing…" : "Undo"}
        </button>
      )}
      <button type="button" className="reorder-undo-close" onClick={onClose} aria-label="Dismiss">
        ✕
      </button>
    </div>
  );
}

function endedText(h) {
  if (h.active) return `Hidden now · ${h.ends_label}`;
  const who = h.cleared_by_name ? ` by ${h.cleared_by_name}` : "";
  const when = h.cleared_at ? `, ${formatRestaurantTime(h.cleared_at)}` : "";
  return `${h.cleared_reason_label || "Ended"}${who}${when}`;
}

// "Hidden from Today (N)": the active dismissals (everyone with the page),
// Show again and the last 60 days (admins).
export function HiddenFromTodayPanel({ state, isAdmin, busyId, onShowAgain, busyPlaced, onUndoPlaced }) {
  const items = state.data?.items || [];
  const history = state.data?.history || [];
  // W1: vendors Ordered right now because of "Order already placed".
  const placed = state.data?.placed_outside || [];
  if (!isAdmin && items.length === 0 && placed.length === 0) return null;
  return (
    <details className="reorder-hidden-panel">
      <summary>
        <span className="reorder-hidden-title">
          Hidden from Today ({state.loading ? "…" : items.length})
          {placed.length > 0 && <span className="muted"> · {placed.length} marked as already placed</span>}
        </span>
        <span className="muted reorder-hidden-sub">
          Critical vendors an admin closed, skipped this week or set to Remind me later - off the Home Critical tile and
          the morning notification for everyone until they come back.
        </span>
      </summary>
      {state.error && <p className="error">Couldn't load hidden vendors: {state.error}</p>}
      {items.length === 0 ? (
        <p className="muted reorder-hidden-empty">No vendor is hidden right now.</p>
      ) : (
        <ul className="reorder-hidden-list">
          {items.map((d) => {
            const why = whyText(d);
            return (
              <li key={d.id}>
                <div className="reorder-hidden-main">
                  <div>
                    <strong>{d.vendor}</strong>{" "}
                    <Term k={CRITICAL_DISMISS_TERMS[d.kind]} label={d.kind_label}>
                      <span className="pill reorder-hidden-pill">{d.kind_label}</span>
                    </Term>{" "}
                    <span className="reorder-hidden-ends">{d.ends_label}</span>
                  </div>
                  <div className="muted reorder-hidden-meta">
                    {why && <span className="reorder-hidden-why">{why} · </span>}
                    {d.created_by_name ? `by ${d.created_by_name}, ` : ""}
                    {formatRestaurantTime(d.created_at)}
                  </div>
                </div>
                {isAdmin && (
                  <button
                    type="button"
                    className="btn-secondary reorder-hide-btn"
                    disabled={busyId === d.id}
                    onClick={() => onShowAgain(d)}
                  >
                    {busyId === d.id ? "Showing…" : "Show again"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {placed.length > 0 && (
        <>
          <h4 className="reorder-hidden-history-title">
            <Term k="order_placed_outside" label="Order already placed">
              Marked as already placed ({placed.length})
            </Term>
          </h4>
          <ul className="reorder-hidden-list">
            {placed.map((p) => (
              <li key={p.order_id}>
                <div className="reorder-hidden-main">
                  <div>
                    <strong>{p.vendor}</strong> <span className="pill pill-success">Ordered</span>{" "}
                    <span className="reorder-hidden-ends">placed outside the app</span>
                  </div>
                  <div className="muted reorder-hidden-meta">
                    {p.note && <span className="reorder-hidden-why">{p.note} · </span>}
                    {p.placed_by_name ? `by ${p.placed_by_name}, ` : ""}
                    {formatRestaurantTime(p.placed_at)}
                  </div>
                </div>
                {isAdmin && onUndoPlaced && (
                  <button
                    type="button"
                    className="btn-secondary reorder-hide-btn"
                    disabled={busyPlaced === `order:${p.order_id}`}
                    onClick={() => onUndoPlaced(p.order_id, p.vendor)}
                  >
                    {busyPlaced === `order:${p.order_id}` ? "Undoing…" : "Undo"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {isAdmin && (
        <>
          <h4 className="reorder-hidden-history-title">History (last 60 days)</h4>
          {history.length === 0 ? (
            <p className="muted reorder-hidden-empty">Nothing hidden in the last 60 days.</p>
          ) : (
            <div className="table-wrap">
              <table className="reorder-hidden-history">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Vendor</th>
                    <th>Action</th>
                    <th>Why / note</th>
                    <th>By</th>
                    <th>Ended</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.id}>
                      <td>{formatRestaurantTime(h.created_at)}</td>
                      <td>{h.vendor}</td>
                      <td>
                        {h.kind_label}
                        {h.kind === "close" && h.critical_item_count != null && (
                          <span className="muted"> ({h.critical_item_count} critical)</span>
                        )}
                      </td>
                      <td>{whyText(h) || "-"}</td>
                      <td>{h.created_by_name || "-"}</td>
                      <td className={h.active ? "reorder-hidden-active" : undefined}>{endedText(h)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </details>
  );
}
