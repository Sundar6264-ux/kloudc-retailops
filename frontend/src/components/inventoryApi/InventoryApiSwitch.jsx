import { useState } from "react";
import { createPortal } from "react-dom";
import useDialog from "../flyers/useDialog.js";
import {
  NOTE_MAX,
  RESULT_LABEL,
  RESULT_PILL,
  historyChange,
  missedCells,
  nightLabel,
  pctText,
  sourceText,
  streakSummary,
  thresholdsText,
} from "./inventoryApiModel.js";
import { currentPosName } from "../../store/gating.js";

// The "Use Toast for inventory" switch, the nightly readiness streak and the
// switch's history (backend: services/inventory_api_switch.py,
// services/inventory_api_readiness.py). Turning on asks first and explains
// what changes; turning off is immediate.

function TurnOnDialog({ status, readiness, saving, error, onConfirm, onClose }) {
  const [note, setNote] = useState("");
  const dialog = useDialog(() => !saving && onClose(), { initialFocus: "[data-autofocus]" });
  const time = status?.settings?.snapshot_time || "04:05";
  const screens = status?.live_stock_screens || [];
  const notReady = readiness && !readiness.ready;
  return createPortal(
    <div className="fly-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !saving && onClose()}>
      <div className="fly-modal" role="dialog" aria-modal="true" aria-labelledby="invapi-on-title" {...dialog}>
        <div className="fly-modal-head">
          <h3 id="invapi-on-title">Turn on Inventory from {currentPosName()}?</h3>
          <button type="button" className="fly-icon-btn" aria-label="Close" onClick={onClose} disabled={saving}>
            ×
          </button>
        </div>
        <div className="fly-modal-body">
          <p style={{ marginTop: 0 }}>It takes effect right away - no restart. What changes:</p>
          <ul className="invapi-list">
            <li>
              Each night at {time} the day's stock list is made from {currentPosName()}'s stock and menu and saved, on any
              day without an uploaded file. A file uploaded for a day still replaces it.
            </li>
            <li>
              These read {currentPosName()}'s current count (at most 5 minutes old) instead of the last saved list:
              <ul className="invapi-list">
                {screens.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </li>
            <li>Costs, vendor codes and last received dates still come from the latest uploaded file - upload one now and then.</li>
            <li>You can turn it off here at any time; that is immediate too.</li>
          </ul>
          {notReady && (
            <p className="invapi-warn" role="note">
              Not ready yet: {readiness.streak} of {readiness.needed} nights in a row have met the bar
              {readiness.reset ? ` (last miss: ${nightLabel(readiness.reset.date)})` : ""}. You can still turn it on.
            </p>
          )}
          <label className="invapi-note">
            Note (optional)
            <input
              type="text"
              value={note}
              maxLength={NOTE_MAX}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. 7 good nights, checked Reorder"
              data-autofocus
            />
          </label>
          {error && <p className="error fly-modal-error">{error}</p>}
        </div>
        <div className="fly-modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" onClick={() => onConfirm(note)} disabled={saving}>
            {saving ? "Turning on…" : "Turn on"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export function SwitchCard({ sw, status, readiness, when, onChange }) {
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  async function apply(on, note) {
    // Which way it's going: the card flips to the new state before the
    // status reload ends, and the Off button mustn't read "Turning off…"
    // right after turning on.
    setSaving(on ? "on" : "off");
    setError(null);
    setMessage(null);
    try {
      await onChange(on, note);
      setConfirming(false);
      setMessage(
        on
          ? `Turned on. Live counts show on the next page load; tonight the day's stock list is saved from ${currentPosName()}.`
          : "Turned off. Pages use the uploaded file's counts again; each night the POS is only compared with it.",
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  const on = Boolean(sw?.on);
  return (
    <section className="card invapi-switch-card">
      <div className="invapi-switch-row">
        <div>
          <h3 style={{ margin: 0 }}>Use {currentPosName()} for inventory</h3>
          <div className="invapi-switch-state">
            <span className={`pill ${on ? "pill-success" : "pill-neutral"}`}>{on ? "On" : "Off"}</span>{" "}
            <span className="muted">{sw ? sourceText(sw, when) : "Loading…"}</span>
          </div>
          {readiness && (
            <div className="muted">
              Readiness: {readiness.streak} / {readiness.needed} nights
              {readiness.ready ? " - ready" : ""}
            </div>
          )}
        </div>
        <div className="invapi-switch-actions">
          {on ? (
            <button type="button" className="btn-secondary" disabled={!sw || Boolean(saving)} onClick={() => apply(false)}>
              {saving === "off" ? "Turning off…" : "Turn off"}
            </button>
          ) : (
            <button type="button" disabled={!sw || Boolean(saving)} onClick={() => setConfirming(true)}>
              Turn on…
            </button>
          )}
        </div>
      </div>
      {!confirming && error && <p className="error">{error}</p>}
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      {confirming && (
        <TurnOnDialog
          status={status}
          readiness={readiness}
          saving={Boolean(saving)}
          error={error}
          onConfirm={(note) => apply(true, note)}
          onClose={() => {
            setConfirming(false);
            setError(null);
          }}
        />
      )}
    </section>
  );
}

export function ReadinessCard({ readiness, when }) {
  if (!readiness) return null;
  const t = readiness.thresholds;
  return (
    <section className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Readiness</h3>
        <span className={`pill ${readiness.ready ? "pill-success" : "pill-neutral"}`}>
          {readiness.streak} / {readiness.needed} nights
        </span>
      </div>
      <p className="invapi-streak">{streakSummary(readiness)}</p>
      {readiness.reset && (
        <p className="invapi-warn">
          Streak reset by the night of {nightLabel(readiness.reset.date)}: {readiness.reset.reasons.join("; ")}.
        </p>
      )}
      <p className="muted">
        Last comparison: {readiness.last_comparison_at ? when(readiness.last_comparison_at) : "none yet"}.{" "}
        {thresholdsText(t)} A failed nightly run resets the streak; a night with no CSV to compare doesn't count either
        way. When it reaches {readiness.needed} with the switch off, admins get one push at 9 AM.
      </p>
      {readiness.nights.length > 0 && (
        <div className="table-wrap">
          <table className="invapi-nights">
            <thead>
              <tr>
                <th>Night</th>
                <th>Result</th>
                <th className="num">On hand</th>
                <th className="num">Price</th>
                <th className="num">Barcode</th>
                <th className="num">Category</th>
                <th className="num">Unexplained</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {readiness.nights.map((n) => {
                const m = n.metrics;
                const miss = missedCells(m, t);
                const cell = (key) => (miss.has(key) ? "num invapi-miss" : "num");
                return (
                  <tr key={`${n.date}-${n.run_at}`}>
                    <td>{nightLabel(n.date)}</td>
                    <td>
                      <span className={`pill ${RESULT_PILL[n.result] || "pill-neutral"}`}>
                        {RESULT_LABEL[n.result] || n.result}
                      </span>
                    </td>
                    <td className={cell("qty")}>{m ? pctText(m.qty_pct) : "-"}</td>
                    <td className={cell("price")}>{m ? pctText(m.price_pct) : "-"}</td>
                    <td className={cell("barcode")}>{m ? pctText(m.barcode_pct) : "-"}</td>
                    <td className={cell("category")}>{m ? pctText(m.category_pct) : "-"}</td>
                    <td className={cell("unexplained")}>{m ? m.qty_unexplained ?? "-" : "-"}</td>
                    <td className="cell-wrap">
                      {n.reasons.length > 0 ? n.reasons.join("; ") : n.note || ""}
                      {m && m.qty_explained_by_sales > 0 && (
                        <span className="muted">
                          {n.reasons.length > 0 ? " " : ""}({m.qty_explained_by_sales} on-hand differences explained by
                          sales after the file)
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function SwitchHistory({ history, when }) {
  return (
    <section className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Switch history</h3>
      </div>
      {!history || history.length === 0 ? (
        <p className="muted">Not changed here yet - the server setting's starting value is in force.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Change</th>
                <th>By</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td>{when(h.changed_at)}</td>
                  <td>{historyChange(h)}</td>
                  <td>{h.changed_by || "-"}</td>
                  <td className="cell-wrap">{h.note || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
