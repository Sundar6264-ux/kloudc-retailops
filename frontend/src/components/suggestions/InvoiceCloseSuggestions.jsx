import { useState } from "react";
import { dismissInvoiceCloseSuggestion, markInvoiceToastClosed, undoDismissInvoiceCloseSuggestion } from "../../api";
import { Term } from "../../glossary.jsx";
import { closeSummary, formatStoreDay } from "./suggestionsModel.js";

function money(n) {
  return n != null ? `$${Number(n).toFixed(2)}` : "$Unknown";
}

// "Looks closed in Toast" (automation batch item 5): open invoices whose
// vendor was received in Toast after the upload (or, on a nightly-snapshot
// day, whose items rose like a delivery). One tap marks it closed - the same
// action as the ○ in the table; "Not closed yet" hides the suggestion.
export default function InvoiceCloseSuggestions({ items, onChanged, readOnly }) {
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const [undo, setUndo] = useState(null); // {id, vendor}
  const suggested = (items || []).filter((i) => i.close_suggestion && i.can_mark_toast_closed);
  const summary = closeSummary(suggested);

  async function run(id, action) {
    setBusyId(id);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  if (!suggested.length && !undo) return null;

  return (
    <section className="card sugg-card" aria-labelledby="close-sugg-title">
      <div className="card-header">
        <h2 id="close-sugg-title" style={{ margin: 0 }}>
          <Term k="invoice_close_suggested">Looks closed in Toast</Term>{" "}
          {suggested.length > 0 && <span className="pill pill-info sugg-count">{suggested.length}</span>}
        </h2>
        {summary && <span className="muted" style={{ fontSize: "0.85rem" }}>{summary}</span>}
      </div>
      {undo && (
        <p className="sugg-notice" role="status">
          Hid the suggestion for {undo.vendor}.{" "}
          <button
            type="button"
            className="link-button"
            onClick={() =>
              run(undo.id, async () => {
                await undoDismissInvoiceCloseSuggestion(undo.id);
                setUndo(null);
              })
            }
          >
            Undo
          </button>
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <ul className="sugg-close-list">
        {suggested.map((item) => (
          <li key={item.id} className="sugg-close-row">
            <div className="sugg-close-text">
              <strong>{item.vendor}</strong>{" "}
              <span className="muted">
                {money(item.total_amount)} · uploaded {formatStoreDay(item.created_at)}
              </span>
              <div className="sugg-reason">{item.close_suggestion.reason}</div>
            </div>
            <div className="sugg-close-actions">
              <button
                type="button"
                className="link-button"
                disabled={busyId === item.id || readOnly}
                onClick={() =>
                  run(item.id, async () => {
                    await dismissInvoiceCloseSuggestion(item.id);
                    setUndo({ id: item.id, vendor: item.vendor });
                  })
                }
              >
                Not closed yet
              </button>
              <button
                type="button"
                disabled={busyId === item.id || readOnly}
                onClick={() => run(item.id, () => markInvoiceToastClosed(item.id))}
              >
                {busyId === item.id ? "Saving…" : "Mark closed in Toast"}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
