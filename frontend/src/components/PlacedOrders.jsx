import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getPlacedOrders, orderAgain } from "../api";
import { formatRestaurantTime, storeDateStr as storeZoneDateStr, storeZoneCity } from "../format";
import InfoBlock from "./InfoBlock.jsx";
import { PLACED_STATUS_TERMS, Term, TermInfo } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { cartLinkFor } from "./cartAdd/cartVendorPanels.js";
import { isOutsideOrder, lineQtyText, outsideDetail, outsideLabel } from "./placedOrderSource.js";
import PoDownloadMenu from "./poDownload/PoDownloadMenu.jsx";

const PAGE_SIZE = 50;

const RANGE_OPTIONS = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "1 Month" },
  { value: "90", label: "3 Months" },
  { value: "all", label: "All time" },
  { value: "custom", label: "Custom" },
];

const STATUS_LABELS = { placed: "Placed", pdf: "PDF only" };

// Dates are the store's (its own time zone, format.js), same as the backend filter.

function storeDateStr(date) {
  return storeZoneDateStr(date);
}

function storeDaysAgoStr(days) {
  return storeDateStr(new Date(Date.now() - days * 24 * 60 * 60 * 1000));
}

function formatQty(qty) {
  const n = Number(qty) || 0;
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, "");
}

// Every order logged from the Purchase Order Cart, newest first: "Placed"
// (someone pressed Order placed) or "PDF only" (a PO PDF was made but the
// order was never marked placed). Filters run on the server.
export default function PlacedOrders() {
  const [rangePreset, setRangePreset] = useState("30");
  const [startDate, setStartDate] = useState(storeDaysAgoStr(30));
  const [endDate, setEndDate] = useState(storeDateStr(new Date()));
  const [supplier, setSupplier] = useState("");
  const [status, setStatus] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [orders, setOrders] = useState([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [suppliers, setSuppliers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [reorderState, setReorderState] = useState({});
  // Bumped on every new filter load: a slower, older response (an earlier
  // search keystroke, a previous vendor) must not overwrite the newer one.
  const requestId = useRef(0);

  const { sortKey, sortDir, toggleSort, rows } = useTableControls(orders, {
    defaultSortKey: "placed_at",
    defaultSortDir: "desc",
  });

  // Typing in the search box waits a moment before asking the server.
  useEffect(() => {
    const handle = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(handle);
  }, [searchInput]);

  const filters = useCallback(
    (offset) => ({
      supplier,
      status,
      search,
      start: rangePreset === "all" ? "" : startDate,
      end: rangePreset === "all" ? "" : endDate,
      limit: PAGE_SIZE,
      offset,
    }),
    [supplier, status, search, rangePreset, startDate, endDate]
  );

  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await getPlacedOrders(filters(0));
      if (id !== requestId.current) return;
      setOrders(res.items);
      setTotal(res.total);
      setHasMore(res.has_more);
      setSuppliers(res.suppliers || []);
    } catch (err) {
      if (id === requestId.current) setError(err.message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  async function loadMore() {
    const id = requestId.current;
    setLoadingMore(true);
    try {
      const res = await getPlacedOrders(filters(orders.length));
      if (id !== requestId.current) return; // the filters changed meanwhile
      const seen = new Set(orders.map((o) => o.id));
      setOrders((prev) => [...prev, ...res.items.filter((o) => !seen.has(o.id))]);
      setTotal(res.total);
      setHasMore(res.has_more);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingMore(false);
    }
  }

  function handleRangePresetChange(value) {
    setRangePreset(value);
    if (value !== "custom" && value !== "all") {
      setStartDate(storeDaysAgoStr(Number(value)));
      setEndDate(storeDateStr(new Date()));
    }
  }

  async function handleOrderAgain(order) {
    setReorderState((s) => ({ ...s, [order.id]: "busy" }));
    try {
      await orderAgain(order.id);
      setReorderState((s) => ({ ...s, [order.id]: "done" }));
    } catch (err) {
      setReorderState((s) => ({ ...s, [order.id]: { error: err.message } }));
    }
  }

  function th(label, key, align) {
    return (
      <SortableTh sortKey={key} currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align={align}>
        {label}
      </SortableTh>
    );
  }

  const filtered = supplier || status || search || rangePreset !== "all";

  return (
    <section className="card">
      <InfoBlock brief="Every purchase order from the cart, newest first - click an order to see what was on it.">
        <p style={{ marginTop: 0 }}>
          An order shows here as <strong>Placed</strong> once someone clicks <strong>Order placed</strong> on
          the Purchase Order Cart. It records exactly what was in
          that vendor's cart at that moment.
        </p>
        <p style={{ marginBottom: 0 }}>
          <strong>PDF only</strong> means a PO PDF was made (downloaded, or opened in the app to share on
          WhatsApp) but the order was never marked placed. Making the PDF again updates the same entry, and
          marking the order placed within 2 days turns it into the Placed order, so one order is never listed
          twice. Dates are store time ({storeZoneCity()}). <strong>Order again</strong> puts an order's items back into
          that vendor's cart with the same quantities.
        </p>
      </InfoBlock>

      <div className="card-header">
        <div className="range-pills">
          {RANGE_OPTIONS.map((p) => (
            <button
              key={p.value}
              type="button"
              className={`range-pill${rangePreset === p.value ? " active" : ""}`}
              onClick={() => handleRangePresetChange(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button className="link-button" onClick={load}>
          Refresh
        </button>
      </div>

      {rangePreset === "custom" && (
        <div className="date-range-group" style={{ margin: "0.75rem 0 0" }}>
          <span className="date-range-group-label">Custom range</span>
          <div className="date-range-group-inputs">
            <input type="date" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} />
            <span className="date-range-separator">to</span>
            <input type="date" value={endDate} min={startDate} onChange={(e) => setEndDate(e.target.value)} />
          </div>
        </div>
      )}

      <div className="placed-orders-toolbar">
        <select value={supplier} onChange={(e) => setSupplier(e.target.value)} aria-label="Vendor">
          <option value="">All vendors</option>
          {suppliers.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <div className="segmented" role="tablist" aria-label="Status">
          {[
            ["", "All"],
            ["placed", "Placed"],
            ["pdf", "PDF only"],
          ].map(([value, label]) => (
            <button
              key={value || "all"}
              type="button"
              role="tab"
              aria-selected={status === value}
              className={status === value ? "active" : ""}
              onClick={() => setStatus(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          type="text"
          className="table-search"
          placeholder="Search item, code or vendor…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}

      {!loading && orders.length === 0 && !error && (
        <p className="muted">{filtered ? "No orders match these filters." : "No orders placed yet."}</p>
      )}

      {!loading && orders.length > 0 && (
        <>
          <p className="muted placed-orders-count">
            {total} order{total === 1 ? "" : "s"}
            {orders.length < total ? ` - showing ${orders.length}` : ""}
          </p>
          <div className="table-wrap">
            <table className="kc-table--stack">
              <thead>
                <tr>
                  {th("Placed at", "placed_at")}
                  {th("Vendor", "supplier")}
                  {th("Status", "status")}
                  {th("Items", "item_count", "right")}
                  {th("Total qty", "total_qty", "right")}
                  {th("By", "placed_by_name")}
                </tr>
              </thead>
              <tbody>
                {rows.map((o) => {
                  const open = expandedId === o.id;
                  const again = reorderState[o.id];
                  // "Order already placed" (W1): placed outside the app, no items.
                  const outside = isOutsideOrder(o);
                  const outsideInfo = outsideDetail(o);
                  return (
                    <Fragment key={o.id}>
                      <tr>
                        <td>
                          <button
                            type="button"
                            className="sp-value-link"
                            onClick={() => setExpandedId(open ? null : o.id)}
                            aria-expanded={open}
                            title="Show items"
                          >
                            {formatRestaurantTime(o.placed_at)}
                            <span className="sp-caret"> {open ? "▲" : "▼"}</span>
                          </button>
                        </td>
                        <td className="cell-name">{o.supplier}</td>
                        <td>
                          <Term k={PLACED_STATUS_TERMS[o.status]}>
                            <span className={`pill placed-status-pill ${o.status === "pdf" ? "pill-warn" : "pill-success"}`}>
                              {STATUS_LABELS[o.status] || o.status}
                            </span>
                          </Term>
                          {outside && (
                            <Term k="order_placed_outside">
                              <span className="placed-outside-label">{outsideLabel(o)}</span>
                            </Term>
                          )}
                        </td>
                        <td className="num">{outside ? "-" : o.item_count}</td>
                        <td className="num">{outside ? "-" : formatQty(o.total_qty)}</td>
                        <td>{o.placed_by_name || "-"}</td>
                      </tr>
                      {open && (
                        <tr className="sp-weekly-row">
                          {/* Full width, no "Placed at" label on a phone. */}
                          <td colSpan={6} data-label="">
                            <div className="placed-orders-detail">
                              {outsideInfo ? (
                                <div className="placed-outside-detail">
                                  <span className="muted">{outsideInfo.text}</span>
                                  {outsideInfo.note && <span className="placed-outside-note">Note: {outsideInfo.note}</span>}
                                </div>
                              ) : o.items.length === 0 ? (
                                <span className="muted">No item details were saved for this order.</span>
                              ) : (
                                <table className="sp-weekly-table">
                                  <thead>
                                    <tr>
                                      <th>Item</th>
                                      <th>Code</th>
                                      <th style={{ textAlign: "right" }}>Qty</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {o.items.map((line, i) => (
                                      <tr key={`${line.name}-${i}`}>
                                        <td className="cell-wrap">{line.name}</td>
                                        <td>{line.supplier_item_id || "-"}</td>
                                        <td className="num">{lineQtyText(line, formatQty)}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              )}
                              {o.items.length > 0 && (
                                <div className="placed-orders-again">
                                  <button
                                    type="button"
                                    className="btn-secondary"
                                    disabled={again === "busy"}
                                    onClick={() => handleOrderAgain(o)}
                                  >
                                    {again === "busy" ? "Adding…" : "Order again"}
                                  </button>
                                  <TermInfo k="order_again" label="Order again" />
                                  <PoDownloadMenu target={{ kind: "order", id: o.id }} supplier={o.supplier} align="start" fixed />
                                  {again === "done" && (
                                    <span className="muted">
                                      Added to {o.supplier}'s cart - <Link to={cartLinkFor(o.supplier)}>open the cart</Link>
                                    </span>
                                  )}
                                  {again?.error && <span className="error">{again.error}</span>}
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <div className="placed-orders-more">
              <button type="button" className="btn-secondary" disabled={loadingMore} onClick={loadMore}>
                {loadingMore ? "Loading…" : `Load more (${total - orders.length} left)`}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
