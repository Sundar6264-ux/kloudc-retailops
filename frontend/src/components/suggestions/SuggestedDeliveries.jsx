import { useCallback, useEffect, useRef, useState } from "react";
import {
  confirmDelivery,
  dismissSuggestedDelivery,
  getSuggestedDeliveries,
  undoDismissSuggestedDelivery,
} from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { Term } from "../../glossary.jsx";
import InfoBlock from "../InfoBlock.jsx";
import {
  DAY_CHOICES,
  confirmPayload,
  evidenceLabel,
  formatIsoDay,
  formatUnits,
  initialQuantities,
  plural,
  qtyDiffers,
} from "./suggestionsModel.js";

// Delivery Review's "Suggested deliveries" (automation batch item 4): the
// deliveries the app found in the inventory uploads, one panel per vendor and
// day. Nothing is logged until someone presses Confirm, which sends the very
// same request as the manual review (confirmDelivery).
export default function SuggestedDeliveries({ onConfirmed }) {
  const { user } = useAuth();
  const [days, setDays] = useState(3);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(() => new Set());
  const [notice, setNotice] = useState(null); // {text, undoKey?}
  const loadSeq = useRef(0);

  const load = useCallback(
    (n = days) => {
      const seq = ++loadSeq.current;
      setLoading(true);
      setError(null);
      return getSuggestedDeliveries(n)
        .then((res) => {
          if (seq === loadSeq.current) setData(res);
        })
        .catch((err) => {
          if (seq === loadSeq.current) setError(err.message);
        })
        .finally(() => {
          if (seq === loadSeq.current) setLoading(false);
        });
    },
    [days]
  );

  useEffect(() => {
    load(days);
  }, [days, load]);

  function toggle(key) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function handleConfirmed(group, logged) {
    setNotice({ text: `Logged ${plural(logged, "item")} from ${group.vendor} (${formatIsoDay(group.received_date)}).` });
    setOpen((prev) => {
      const next = new Set(prev);
      next.delete(group.key);
      return next;
    });
    await load();
    onConfirmed?.();
  }

  async function handleDismiss(group) {
    try {
      await dismissSuggestedDelivery(group.key);
      setNotice({ text: `Dismissed ${group.vendor} (${formatIsoDay(group.received_date)}).`, undoKey: group.key });
      await load();
    } catch (err) {
      setNotice({ text: err.message, error: true });
    }
  }

  async function handleUndo(key) {
    try {
      await undoDismissSuggestedDelivery(key);
      setNotice(null);
      await load();
    } catch (err) {
      setNotice({ text: err.message, error: true });
    }
  }

  const groups = data?.groups || [];

  return (
    <section className="card sugg-card">
      <div className="card-header">
        <h2 style={{ margin: 0 }}>
          Suggested deliveries{" "}
          {data && (
            <span className={`pill ${groups.length ? "pill-info" : "pill-neutral"} sugg-count`}>{groups.length}</span>
          )}
        </h2>
        <label className="sugg-days">
          From the last{" "}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="How many uploads to look at">
            {DAY_CHOICES.map((n) => (
              <option key={n} value={n}>
                {plural(n, "upload")}
              </option>
            ))}
          </select>
        </label>
      </div>
      <InfoBlock brief="Deliveries found in the inventory uploads. Check the quantities, then confirm - nothing is logged until you do.">
        <Term k="delivery_suggested">Each suggestion</Term> is one vendor on one upload. Most come from Toast itself: when a
        delivery is received in Toast, the next inventory upload shows the receive time and Toast's receiving number (like
        IN378) on each item, so we group those items under the vendor most of them list. On a day with only the nightly
        Toast snapshot (no upload), a delivery shows as several of one vendor's items rising by more than sales explain.
        Quantity = count now - count before + units sold in between. Ticked lines are logged when you press Confirm - the
        same as reviewing the delivery by hand. A vendor whose delivery is already logged isn't suggested again. Not a
        delivery? Dismiss it.
      </InfoBlock>

      {notice && (
        <p className={notice.error ? "error" : "sugg-notice"} role="status">
          {notice.text}{" "}
          {notice.undoKey && (
            <button type="button" className="link-button" onClick={() => handleUndo(notice.undoKey)}>
              Undo
            </button>
          )}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {loading && !data && <p className="muted">Looking through the latest uploads…</p>}
      {data && groups.length === 0 && (
        <p className="muted" style={{ marginBottom: 0 }}>
          Nothing to confirm{data.through ? ` up to the ${formatIsoDay(data.through)} upload` : ""}
          {data.counts?.handled ? ` - ${plural(data.counts.handled, "delivery", "deliveries")} already logged` : ""}.
        </p>
      )}

      {groups.map((group) => (
        <SuggestedGroup
          key={group.key}
          group={group}
          isOpen={open.has(group.key)}
          onToggle={() => toggle(group.key)}
          onConfirmed={(logged) => handleConfirmed(group, logged)}
          onDismiss={() => handleDismiss(group)}
          readOnly={!!user?.demo}
        />
      ))}
    </section>
  );
}

function SuggestedGroup({ group, isOpen, onToggle, onConfirmed, onDismiss, readOnly }) {
  const [qtyById, setQtyById] = useState(() => initialQuantities(group));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const panelId = `sugg-${group.key.replace(/[^a-z0-9]+/gi, "-")}`;
  const payload = confirmPayload(group, qtyById);
  const units = payload.reduce((n, i) => n + i.quantity_received, 0);

  async function handleConfirm() {
    if (!payload.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await confirmDelivery(group.vendor, group.received_date, payload);
      await onConfirmed(res.logged);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`sugg-group${isOpen ? " is-open" : ""}`}>
      <button type="button" className="sugg-group-head" aria-expanded={isOpen} aria-controls={panelId} onClick={onToggle}>
        <span className="sugg-vendor">{group.vendor}</span>
        <span className={`pill ${group.evidence === "toast_received" ? "pill-success" : "pill-warn"} sugg-pill`}>
          {evidenceLabel(group.evidence)}
        </span>
        <span className="sugg-meta">
          {formatIsoDay(group.received_date)} upload · {plural(group.preselected_count, "item")} ·{" "}
          {formatUnits(group.suggested_units)} units
          {group.item_count > group.preselected_count ? ` · ${group.item_count - group.preselected_count} to check` : ""}
        </span>
        <span className="sugg-chevron" aria-hidden="true">
          {isOpen ? "▾" : "▸"}
        </span>
      </button>
      <div id={panelId} hidden={!isOpen} className="sugg-body">
        {isOpen && (
          <>
            <p className="sugg-reason">{group.reason}</p>
            {!group.sales_complete && (
              <p className="sugg-warning">
                Sales for a day in this window haven't synced from Toast yet, so quantities may be a little low.
              </p>
            )}
            <div className="table-wrap">
              <table className="sugg-table">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>SKU</th>
                    <th className="num">Before</th>
                    <th className="num">Now</th>
                    <th className="num">Sold between</th>
                    <th className="num">Cost</th>
                    <th className="num">
                      <Term k="delivery_suggested_qty">Qty to log</Term>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map((item) => {
                    const raw = qtyById[item.item_id] ?? "";
                    const differs = qtyDiffers(item, raw);
                    return (
                      <tr key={item.item_id}>
                        <td className="cell-wrap">
                          {item.name || item.item_id}
                          {item.is_new && (
                            <span style={{ marginLeft: "0.4rem" }}>
                              <Term k="delivery_new_item">
                                <span className="pill pill-running">New</span>
                              </Term>
                            </span>
                          )}
                          {item.is_container && <span className="pill pill-neutral sugg-pill">Container</span>}
                          {item.note && <div className="sugg-note">{item.note}</div>}
                        </td>
                        <td>{item.supplier_item_id || "-"}</td>
                        <td className="num">{item.prev_qty ?? "-"}</td>
                        <td className="num">{item.today_qty ?? "-"}</td>
                        <td className="num">{item.sold_between > 0 ? formatUnits(item.sold_between) : "-"}</td>
                        <td className="num">{item.cost != null ? `$${item.cost.toFixed(2)}` : "-"}</td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            step="any"
                            inputMode="decimal"
                            aria-label={`Quantity of ${item.name || item.item_id} to log`}
                            className={`po-qty-input${differs ? " qty-mismatch" : ""}`}
                            value={raw}
                            placeholder={item.suggested_qty == null ? "qty" : ""}
                            onChange={(e) => setQtyById((prev) => ({ ...prev, [item.item_id]: e.target.value }))}
                          />
                          {differs && (
                            <div className="qty-mismatch-warning">Suggested {formatUnits(item.suggested_qty)}</div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {error && <p className="error">{error}</p>}
            <div className="review-actions">
              <button type="button" className="link-button link-danger" onClick={onDismiss} disabled={busy || readOnly}>
                Not a delivery - dismiss
              </button>
              <button type="button" onClick={handleConfirm} disabled={busy || !payload.length || readOnly}>
                {busy
                  ? "Logging…"
                  : `Confirm & log ${plural(payload.length, "item")}${payload.length ? ` (${formatUnits(units)} units)` : ""}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
