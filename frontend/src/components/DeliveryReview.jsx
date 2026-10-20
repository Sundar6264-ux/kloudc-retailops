import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { confirmDelivery, getDeliveryCandidates, getDeliveryHistory } from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { Term } from "../glossary.jsx";
import SuggestedDeliveries from "./suggestions/SuggestedDeliveries.jsx";

// Reached only by navigating here from the inventory upload's "Review
// delivery" button, with the checked vendors passed as router state - one
// vendor's suggestions are shown at a time, in sequence, so a multi-vendor
// delivery day is reviewed vendor-by-vendor instead of one giant mixed list.
export default function DeliveryReview() {
  const location = useLocation();
  const navigate = useNavigate();
  const vendors = location.state?.vendors || [];

  const [vendorIndex, setVendorIndex] = useState(0);
  const [candidates, setCandidates] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [qtyById, setQtyById] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [summary, setSummary] = useState([]);
  // Bumped after a suggested delivery is confirmed, so the "Recently
  // confirmed" list below picks it up.
  const [historyVersion, setHistoryVersion] = useState(0);

  const currentVendor = vendors[vendorIndex];
  const done = vendorIndex >= vendors.length;

  useEffect(() => {
    if (done || !currentVendor) return;
    setLoading(true);
    setError(null);
    setCandidates(null);
    getDeliveryCandidates(currentVendor)
      .then((res) => {
        setCandidates(res);
        const initialQty = {};
        res.items.forEach((item) => {
          initialQty[item.item_id] = item.suggested_qty > 0 ? String(item.suggested_qty) : "";
        });
        setQtyById(initialQty);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [vendorIndex, currentVendor, done]);

  function setQty(itemId, value) {
    setQtyById((prev) => ({ ...prev, [itemId]: value }));
  }

  // `item.suggested_qty` already nets today's sales back into the raw count
  // diff (delta + sold_today) so it reflects what was actually received, not
  // just how much the count moved. Editing it away from that isn't blocked
  // (the whole point is you might know better, e.g. some of it was a return,
  // not this delivery), but it needs to be obvious in red that what's about
  // to be logged no longer matches the suggestion, not just quietly accepted.
  function mismatch(item) {
    const raw = qtyById[item.item_id];
    if (raw === "" || raw == null) return false;
    const entered = Number(raw);
    if (Number.isNaN(entered)) return false;
    return Math.abs(entered - item.suggested_qty) > 0.01;
  }

  const includeCount = useMemo(
    () => (candidates ? candidates.items.filter((i) => Number(qtyById[i.item_id]) > 0).length : 0),
    [candidates, qtyById]
  );

  function advance(logged) {
    setSummary((prev) => [...prev, { vendor: currentVendor, logged }]);
    setVendorIndex((i) => i + 1);
  }

  function handleSkip() {
    advance(0);
  }

  async function handleConfirm() {
    if (!candidates) return;
    const items = candidates.items
      .filter((i) => Number(qtyById[i.item_id]) > 0)
      .map((i) => ({
        item_id: i.item_id,
        item_name: i.name,
        quantity_received: Number(qtyById[i.item_id]),
      }));
    if (items.length === 0) {
      advance(0);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await confirmDelivery(currentVendor, candidates.today_date, items);
      advance(res.logged);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (vendors.length === 0) {
    return (
      <>
        <SuggestedDeliveries onConfirmed={() => setHistoryVersion((v) => v + 1)} />
        <section className="card">
          <h2>Review a delivery by hand</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Something missing above? A manual review starts from the inventory upload: on Home, check "We received a
            delivery today", pick the vendor(s), upload, and you'll be brought here to confirm what arrived.
          </p>
          <button onClick={() => navigate("/")}>Go to Home</button>
        </section>
        <RecentDeliveries key={historyVersion} />
      </>
    );
  }

  if (done) {
    const totalLogged = summary.reduce((sum, s) => sum + s.logged, 0);
    return (
      <section className="card">
        <InfoBlock brief="Delivery review complete.">
          {totalLogged} purchase log entr{totalLogged === 1 ? "y" : "ies"} logged across{" "}
          {vendors.length} vendor{vendors.length === 1 ? "" : "s"}.
        </InfoBlock>
        <ul style={{ fontSize: "0.9rem" }}>
          {summary.map((s, i) => (
            <li key={i}>
              {s.vendor}: {s.logged} logged
            </li>
          ))}
        </ul>
        <div className="review-actions">
          <button className="link-button" onClick={() => navigate("/")}>
            Back to Home
          </button>
          <button onClick={() => navigate("/reconciliation")}>Go to Reconciliation →</button>
        </div>
      </section>
    );
  }

  return (
    <section className="card">
      <InfoBlock brief="Review what likely arrived from this vendor, edit if needed, then confirm.">
        These are suggestions from comparing today's inventory count to the previous upload, netted
        against today's actual sales for that item so a delivery isn't undercounted just because
        some of it already sold (count change + sold today = suggested qty). Shown for items that
        list this vendor: brand new items, and items whose net change was more than 5 (up or down -
        a big drop is shown too, since it's worth a look, but won't be pre-filled as a purchase
        quantity). Nothing is logged until you confirm - edit any quantity freely, or clear it to
        skip that item. If what you type doesn't match the suggested quantity, the field turns red
        so it's clear you're overriding it, not just entering it. The Cost column is today's file
        cost for that item - confirming logs it against this vendor, which is what powers Vendor
        Price Comparison in Inventory Reports.
      </InfoBlock>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Vendor {vendorIndex + 1} of {vendors.length}: <strong>{currentVendor}</strong>
      </p>

      {loading && <p className="muted">Comparing today's upload to the previous one…</p>}
      {error && <p className="error">{error}</p>}

      {!loading && candidates && candidates.items.length === 0 && (
        <p className="muted">
          Nothing stood out for {currentVendor} between {candidates.prev_date} and{" "}
          {candidates.today_date} - no new items and no item's count change (net of today's sales)
          was more than 5 units.
        </p>
      )}

      {!loading && candidates && candidates.items.length > 0 && (
        <div className="table-wrap">
          <table className="kc-table--stack">
            <thead>
              <tr>
                <th>Item</th>
                <th>SKU</th>
                <th style={{ textAlign: "right" }}>Prev qty</th>
                <th style={{ textAlign: "right" }}>Today qty</th>
                <th style={{ textAlign: "right" }}>Change</th>
                <th style={{ textAlign: "right" }}>Sold today</th>
                <th style={{ textAlign: "right" }}>Cost</th>
                <th style={{ textAlign: "right" }}>
                  <Term k="delivery_suggested_qty">Qty to log</Term>
                </th>
              </tr>
            </thead>
            <tbody>
              {candidates.items.map((item) => (
                <tr key={item.item_id}>
                  <td>
                    {item.name || item.item_id}
                    {item.is_new && (
                      <span style={{ marginLeft: "0.4rem" }}>
                        <Term k="delivery_new_item">
                          <span className="pill pill-running">New</span>
                        </Term>
                      </span>
                    )}
                  </td>
                  <td>{item.supplier_item_id || "-"}</td>
                  <td style={{ textAlign: "right" }}>{item.prev_qty ?? "-"}</td>
                  <td style={{ textAlign: "right" }}>{item.today_qty}</td>
                  <td
                    style={{ textAlign: "right" }}
                    className={item.delta < 0 ? "text-negative" : "text-positive"}
                  >
                    {item.delta > 0 ? "+" : ""}
                    {item.delta}
                  </td>
                  <td style={{ textAlign: "right" }}>{item.sold_today > 0 ? item.sold_today : "-"}</td>
                  <td style={{ textAlign: "right" }}>{item.cost != null ? `$${item.cost.toFixed(2)}` : "-"}</td>
                  <td style={{ textAlign: "right" }}>
                    <input
                      type="number"
                      min="0"
                      step="any"
                      className={`po-qty-input${mismatch(item) ? " qty-mismatch" : ""}`}
                      value={qtyById[item.item_id] ?? ""}
                      onChange={(e) => setQty(item.item_id, e.target.value)}
                    />
                    {mismatch(item) && (
                      <div className="qty-mismatch-warning">Doesn't match suggested qty ({item.suggested_qty})</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="review-actions">
        <button className="link-button" onClick={handleSkip} disabled={submitting}>
          Skip this vendor
        </button>
        <button disabled={submitting || loading || !candidates} onClick={handleConfirm}>
          {submitting
            ? "Logging…"
            : `Confirm & log (${includeCount}) ${
                vendorIndex + 1 < vendors.length ? "→ next vendor" : "→ finish"
              }`}
        </button>
      </div>
    </section>
  );
}

const SOURCE_LABELS = {
  delivery_review: { label: "Delivery Review", cls: "pill-success" },
  manual: { label: "Logged manually", cls: "pill-info" },
  mixed: { label: "Mixed", cls: "pill-running" },
};

function formatDay(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

// What's already been confirmed (Delivery Review or logged by hand), newest
// first - so this page isn't a dead end when opened from the menu.
function RecentDeliveries() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getDeliveryHistory(14)
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <section className="card">
      <div className="card-header">
        <h2 style={{ margin: 0 }}>Recently confirmed deliveries</h2>
        <span className="muted" style={{ fontSize: "0.82rem" }}>Last 14 days</span>
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.deliveries.length === 0 && (
        <p className="muted">No deliveries logged in the last 14 days.</p>
      )}
      {data && data.deliveries.length > 0 && (
        <div className="table-wrap">
          <table className="kc-table--stack">
            <thead>
              <tr>
                <th>Date</th>
                <th>Vendor</th>
                <th className="num">Items</th>
                <th className="num">Units</th>
                <th className="num">Est. cost</th>
                <th>
                  <Term k="delivery_source">How</Term>
                </th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {data.deliveries.map((d) => {
                const source = SOURCE_LABELS[d.source] || SOURCE_LABELS.mixed;
                return (
                  <tr key={`${d.received_date}-${d.supplier}`}>
                    <td>{formatDay(d.received_date)}</td>
                    <td>{d.supplier}</td>
                    <td className="num">{d.item_count}</td>
                    <td className="num">{d.total_quantity.toLocaleString()}</td>
                    <td className="num">{d.estimated_cost != null ? `$${d.estimated_cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "-"}</td>
                    <td>
                      <span className={`pill ${source.cls}`}>{source.label}</span>
                    </td>
                    <td>{d.logged_by.join(", ") || "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ fontSize: "0.8rem", marginBottom: 0 }}>
        Item-level detail is under Reconciliation → Recent purchase log entries.
      </p>
    </section>
  );
}
