import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { storeTodayStr, storeDateOffsetStr } from "../format.js";
import {
  searchItems,
  logPurchase,
  getPurchases,
  deletePurchase,
  getReconciliationDemo,
  getReconciliationReport,
  downloadReconciliationReport,
  getInvoiceGaps,
  downloadInvoiceGaps,
  downloadPurchaseLog,
  uploadPurchaseLogWithProgress,
  downloadPurchaseLogSampleCsv,
} from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { Term, TermInfo } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { currentPosName } from "../store/gating.js";

function todayStr() {
  return storeTodayStr();
}

function daysAgoStr(days) {
  return storeDateOffsetStr(-days);
}

function money(v) {
  return v != null ? `$${v.toFixed(2)}` : "-";
}

function round2(v) {
  return Math.round(v * 100) / 100;
}

function ItemPicker({ selectedItem, onSelect }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (selectedItem || query.trim().length < 2) {
      setResults([]);
      return;
    }
    const handle = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await searchItems(query.trim());
        setResults(res.items);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [query, selectedItem]);

  if (selectedItem) {
    return (
      <div className="item-picker-selected">
        <span>
          <strong>{selectedItem.name}</strong>{" "}
          <span className="muted">{selectedItem.category && `· ${selectedItem.category}`}</span>
        </span>
        <button type="button" className="link-button" onClick={() => onSelect(null)}>
          Change
        </button>
      </div>
    );
  }

  return (
    <div className="item-picker">
      <input
        type="text"
        placeholder="Search item name, SKU or barcode…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {searching && <p className="muted item-picker-status">Searching…</p>}
      {!searching && results.length > 0 && (
        <ul className="item-picker-results">
          {results.map((item) => (
            <li key={item.item_id}>
              <button type="button" onClick={() => onSelect(item)}>
                <strong>{item.name}</strong>{" "}
                <span className="muted">{item.category && `· ${item.category}`}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {!searching && query.trim().length >= 2 && results.length === 0 && (
        <p className="muted item-picker-status">No items matched "{query}".</p>
      )}
    </div>
  );
}

function PurchaseLogUpload({ onImported }) {
  const [file, setFile] = useState(null);
  const [defaultDate, setDefaultDate] = useState(todayStr());
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!file) return;

    setSubmitting(true);
    setProgress(0);
    setProcessing(false);
    setResult(null);
    setError(null);
    try {
      const data = await uploadPurchaseLogWithProgress(file, defaultDate, (pct) => {
        setProgress(pct);
        if (pct >= 100) setProcessing(true);
      });
      setResult(data);
      setFile(null);
      e.target.reset();
      onImported?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
      setProcessing(false);
    }
  }

  return (
    <details className="recon-bulk-upload">
      <summary>Bulk-log purchases from a CSV instead</summary>
      <InfoBlock brief="Log a whole vendor invoice in one upload instead of typing each item into the form above.">
        Columns are matched by header name, in any order. Needed: an item column (<code>item
        name</code> or <code>item id</code>) and a <code>quantity</code> column. Optional:{" "}
        <code>supplier</code>, <code>unit cost</code>, <code>received date</code> (rows without
        one use the default date below), and <code>notes</code>. Item names must match what's in
        the latest inventory upload exactly (not case-sensitive) - any row that can't be matched
        is skipped and listed below, nothing else about it is guessed.
      </InfoBlock>
      <button type="button" className="link-button" onClick={downloadPurchaseLogSampleCsv}>
        Download sample CSV
      </button>
      <form onSubmit={handleSubmit} className="upload-form">
        <input
          type="file"
          accept=".csv,.xlsx,.xls"
          disabled={submitting}
          onChange={(e) => setFile(e.target.files[0] || null)}
        />
        <label className="recon-bulk-upload-date">
          Default date (for rows with no date column)
          <input
            type="date"
            value={defaultDate}
            max={todayStr()}
            onChange={(e) => setDefaultDate(e.target.value)}
          />
        </label>
        <button type="submit" disabled={!file || submitting}>
          {submitting ? (processing ? "Processing…" : `Uploading… ${progress}%`) : "Upload"}
        </button>
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
        <div style={{ marginTop: "0.5rem" }}>
          <p className={result.rows_skipped > 0 ? "error" : "success"}>
            {result.rows_loaded} row(s) logged
            {result.rows_skipped > 0 && `, ${result.rows_skipped} skipped`}.
          </p>
          {result.errors.length > 0 && (
            <ul className="muted" style={{ fontSize: "0.85rem", margin: "0.25rem 0 0", paddingLeft: "1.2rem" }}>
              {result.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
              {result.total_errors > result.errors.length && (
                <li>...and {result.total_errors - result.errors.length} more.</li>
              )}
            </ul>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </details>
  );
}

// The worked example with this store's real numbers. Lives in Help
// (Reconciliation section) and loads only when opened - it used to be
// fetched on every Reconciliation page load (Oct 15 nav clean-up).
export function ReconciliationWorkedExample() {
  const [opened, setOpened] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tryQty, setTryQty] = useState("0");

  useEffect(() => {
    if (!opened) return;
    getReconciliationDemo()
      .then((res) => {
        setData(res);
        // Default the "try it" input to whatever purchased amount would
        // exactly zero out the highlighted item's variance, so the demo
        // opens already showing the payoff instead of a blank input.
        setTryQty(String(Math.max(0, Math.round(res.highlight.variance_qty * 100) / 100)));
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [opened]);

  const h = data?.highlight;
  const tryValue = Number(tryQty) || 0;
  const newExpected = useMemo(() => (h ? round2(h.opening_qty + tryValue - h.sold) : null), [h, tryValue]);
  const newVariance = useMemo(
    () => (h ? round2(h.actual_closing_qty - newExpected) : null),
    [h, newExpected]
  );

  return (
    <details className="recon-demo" onToggle={(e) => e.currentTarget.open && setOpened(true)}>
      <summary>Worked example: see how this works with your real data</summary>
      <InfoBlock brief="A real example from your own store's numbers.">
        Real inventory counts and real {currentPosName()} sales, one real item, showing the formula in action.
      </InfoBlock>

      {opened && loading && <p className="muted">Loading a real example from your data…</p>}
      {error && <p className="error">{error}</p>}

      {h && (
        <>
          <p>
            <strong>{h.name}</strong>
            {h.category ? ` (${h.category})` : ""}, {data.start} to {data.end}: opening{" "}
            <strong>{h.opening_qty}</strong> + purchased <strong>{h.purchased}</strong> (nothing
            logged yet) - sold <strong>{h.sold}</strong> = expected{" "}
            <strong>{h.expected_closing_qty}</strong>. Actual count was{" "}
            <strong>{h.actual_closing_qty}</strong> - a variance of{" "}
            <strong className={h.variance_qty < 0 ? "text-negative" : "text-positive"}>
              {h.variance_qty > 0 ? "+" : ""}
              {h.variance_qty}
            </strong>
            {h.variance_qty > 0
              ? ", most likely a delivery that hasn't been logged as a purchase yet."
              : ", possibly shrinkage/spoilage or simply an unlogged purchase."}
          </p>

          <div className="recon-demo-try">
            <label>
              Try it - what if this many had been logged as purchased?
              <input
                type="number"
                step="any"
                value={tryQty}
                onChange={(e) => setTryQty(e.target.value)}
              />
            </label>
            <p style={{ margin: "0.6rem 0 0" }}>
              New expected closing = {h.opening_qty} + {tryValue} - {h.sold} ={" "}
              <strong>{newExpected}</strong>. New variance = {h.actual_closing_qty} - {newExpected} ={" "}
              <strong className={Math.abs(newVariance) < 0.01 ? "text-positive" : "text-negative"}>
                {newVariance}
              </strong>
              {Math.abs(newVariance) < 0.01 && " - logging that purchase fully explains it."}
            </p>
          </div>

          <p className="muted" style={{ fontSize: "0.82rem", marginTop: "1rem" }}>
            {data.item_count} item(s) had activity in this window; a few more below for context, all
            showing 0 purchased for the same reason - not a loss finding yet, just nothing logged.
          </p>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ textAlign: "right" }}>Opening</th>
                  <th style={{ textAlign: "right" }}>Sold</th>
                  <th style={{ textAlign: "right" }}>Actual closing</th>
                  <th style={{ textAlign: "right" }}>
                    <Term k="recon_variance">Variance</Term>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.item_id}>
                    <td>{item.name || item.item_id}</td>
                    <td style={{ textAlign: "right" }}>{item.opening_qty}</td>
                    <td style={{ textAlign: "right" }}>{item.sold}</td>
                    <td style={{ textAlign: "right" }}>{item.actual_closing_qty}</td>
                    <td className={item.variance_qty < 0 ? "flag" : ""} style={{ textAlign: "right" }}>
                      {item.variance_qty}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  );
}

function LogPurchaseForm({ onLogged }) {
  const [selectedItem, setSelectedItem] = useState(null);
  const [supplier, setSupplier] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [receivedDate, setReceivedDate] = useState(todayStr());
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  function handleSelectItem(item) {
    setSelectedItem(item);
    if (item?.supplier) {
      setSupplier(item.supplier.split(";")[0].trim());
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!selectedItem || !quantity) return;
    setSubmitting(true);
    setError(null);
    try {
      await logPurchase({
        item_id: selectedItem.item_id,
        item_name: selectedItem.name,
        supplier: supplier || null,
        quantity_received: Number(quantity),
        unit_cost: unitCost ? Number(unitCost) : null,
        received_date: receivedDate,
        notes: notes || null,
      });
      setSelectedItem(null);
      setSupplier("");
      setQuantity("");
      setUnitCost("");
      setNotes("");
      onLogged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="recon-form">
      <label className="recon-form-field recon-form-field-wide">
        Item
        <ItemPicker selectedItem={selectedItem} onSelect={handleSelectItem} />
      </label>
      <label className="recon-form-field">
        Supplier
        <input type="text" value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="Vendor name" />
      </label>
      <label className="recon-form-field">
        Qty received
        <input
          type="number"
          min="0"
          step="any"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          required
        />
      </label>
      <label className="recon-form-field">
        Unit cost
        <input type="number" min="0" step="0.01" value={unitCost} onChange={(e) => setUnitCost(e.target.value)} placeholder="optional" />
      </label>
      <label className="recon-form-field">
        Received date
        <input type="date" value={receivedDate} max={todayStr()} onChange={(e) => setReceivedDate(e.target.value)} required />
      </label>
      <label className="recon-form-field recon-form-field-wide">
        Notes
        <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="optional - PO/invoice #, etc." />
      </label>
      <div className="recon-form-submit">
        <button type="submit" disabled={!selectedItem || !quantity || submitting}>
          {submitting ? "Logging…" : "Log purchase"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function RecentEntries({ refreshSignal }) {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [exporting, setExporting] = useState(false);

  async function load() {
    try {
      const res = await getPurchases();
      setEntries(res.items);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
  }, [refreshSignal]);

  async function handleDelete(id) {
    setDeletingId(id);
    try {
      await deletePurchase(id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      await downloadPurchaseLog();
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div style={{ marginTop: "1rem" }}>
      <div className="sp-toolbar">
        <h3 style={{ fontSize: "0.95rem", margin: 0 }}>Recent purchase log entries</h3>
        <button disabled={exporting || !entries?.length} onClick={handleExport}>
          {exporting ? "Downloading…" : "Download CSV"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!entries && !error && <p className="muted">Loading…</p>}
      {entries && entries.length === 0 && <p className="muted">Nothing logged yet.</p>}
      {entries && entries.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Item</th>
                <th>Supplier</th>
                <th>Qty</th>
                <th>Unit cost</th>
                <th>Notes</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{e.received_date}</td>
                  <td>{e.item_name || e.item_id}</td>
                  <td>{e.supplier || "-"}</td>
                  <td>{e.quantity_received}</td>
                  <td>{money(e.unit_cost)}</td>
                  <td>{e.notes || "-"}</td>
                  <td>
                    <button
                      type="button"
                      className="link-button link-danger"
                      disabled={deletingId === e.id}
                      onClick={() => handleDelete(e.id)}
                    >
                      {deletingId === e.id ? "Deleting…" : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ReconciliationReport() {
  const [startDate, setStartDate] = useState(daysAgoStr(7));
  const [endDate, setEndDate] = useState(todayStr());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [exporting, setExporting] = useState(false);

  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(data?.items, {
    searchKeys: ["name", "category", "supplier"],
    codeKeys: true, // W2: Toast ID / barcode too
    defaultSortKey: "variance_qty",
  });

  async function handleRun() {
    setLoading(true);
    setError(null);
    try {
      const result = await getReconciliationReport(startDate, endDate);
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      await downloadReconciliationReport(startDate, endDate);
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  }

  function th(label, key, align) {
    return (
      <SortableTh sortKey={key} currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align={align}>
        {label}
      </SortableTh>
    );
  }

  return (
    <div style={{ marginTop: "1.5rem", paddingTop: "1.25rem", borderTop: "1px solid var(--border)" }}>
      <h3 style={{ fontSize: "0.95rem", margin: "0 0 0.5rem" }}>Reconciliation report</h3>
      <div className="date-range">
        <label>
          From
          <input type="date" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={endDate} min={startDate} max={todayStr()} onChange={(e) => setEndDate(e.target.value)} />
        </label>
      </div>
      <button disabled={loading} onClick={handleRun}>
        {loading ? "Calculating…" : "Generate report"}
      </button>
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <div className="sp-toolbar">
            <p className="muted" style={{ margin: 0 }}>
              {rows.length} of {data.items.length} item(s) with purchase or sales activity in this
              window.
            </p>
            <button disabled={exporting || data.items.length === 0} onClick={handleExport}>
              {exporting ? "Downloading…" : "Download CSV"}
            </button>
          </div>
          <input
            type="text"
            className="table-search"
            placeholder="Search item, category, or supplier…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {th("Item", "name")}
                  {th("Category", "category")}
                  {th("Supplier", "supplier")}
                  {th("Opening", "opening_qty", "right")}
                  {th("Purchased", "purchased", "right")}
                  {th("Sold", "sold", "right")}
                  {th(<Term k="recon_expected_closing">Expected closing</Term>, "expected_closing_qty", "right")}
                  {th("Actual closing", "actual_closing_qty", "right")}
                  {th(<Term k="recon_variance">Variance (qty)</Term>, "variance_qty", "right")}
                  {th("Variance ($)", "variance_value", "right")}
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.item_id}>
                    <td>{item.name || item.item_id}</td>
                    <td>{item.category || "-"}</td>
                    <td>{item.supplier || "-"}</td>
                    <td>{item.opening_qty}</td>
                    <td>{item.purchased}</td>
                    <td>{item.sold}</td>
                    <td>{item.expected_closing_qty}</td>
                    <td>{item.actual_closing_qty}</td>
                    <td className={item.variance_qty < 0 ? "flag" : ""}>{item.variance_qty}</td>
                    <td className={item.variance_value < 0 ? "flag" : ""}>{money(item.variance_value)}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={10} className="muted">
                      No items match "{search}".
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

const INVOICE_GAP_TYPE_LABELS = { invoice: "Invoice", return: "Return" };

function InvoiceGaps() {
  const [startDate, setStartDate] = useState(daysAgoStr(7));
  const [endDate, setEndDate] = useState(todayStr());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [exporting, setExporting] = useState(false);

  // Downloads the window that was last checked (not unsaved date edits).
  async function handleExport() {
    setExporting(true);
    try {
      await downloadInvoiceGaps(data.start, data.end);
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  }

  async function handleRun() {
    setLoading(true);
    setError(null);
    try {
      const result = await getInvoiceGaps(startDate, endDate);
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ marginTop: "1.5rem", paddingTop: "1.25rem", borderTop: "1px solid var(--border)" }}>
      <h3 style={{ fontSize: "0.95rem", margin: "0 0 0.25rem" }}>
        Invoices without a logged purchase <TermInfo k="recon_invoice_gap" />
      </h3>
      <p className="muted" style={{ margin: "0 0 0.5rem" }}>
        Invoices/returns submitted in this window (Invoice Submission tab) from a vendor with no
        purchase logged nearby (Delivery Review or a manual entry above) - a real invoice that
        might have been forgotten. This checks by vendor and date only, not by item - an invoice's
        own item names don't reliably match inventory item names.
      </p>
      <div className="date-range">
        <label>
          From
          <input type="date" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={endDate} min={startDate} max={todayStr()} onChange={(e) => setEndDate(e.target.value)} />
        </label>
      </div>
      <button disabled={loading} onClick={handleRun}>
        {loading ? "Checking…" : "Check for gaps"}
      </button>
      {error && <p className="error">{error}</p>}

      {data && data.items.length === 0 && (
        <p className="success" style={{ marginTop: "0.75rem" }}>
          No gaps - every invoice/return submitted in this window has a purchase logged nearby.
        </p>
      )}

      {data && data.items.length > 0 && (
        <>
        <div className="sp-toolbar" style={{ marginTop: "0.75rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            {data.items.length} invoice(s)/return(s) from {data.start} to {data.end}.
          </p>
          <button disabled={exporting} onClick={handleExport}>
            {exporting ? "Downloading…" : "Download CSV"}
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Vendor</th>
                <th>Type</th>
                <th style={{ textAlign: "right" }}>Total</th>
                <th>Submitted by</th>
                <th>Invoice</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((g) => (
                <tr key={g.id}>
                  <td>{g.submission_date}</td>
                  <td>{g.vendor}</td>
                  <td>{INVOICE_GAP_TYPE_LABELS[g.submission_type] || g.submission_type}</td>
                  <td style={{ textAlign: "right" }}>{g.total_amount != null ? money(g.total_amount) : "$Unknown"}</td>
                  <td>{g.submitted_by_name || "-"}</td>
                  <td>
                    <a href={g.file_url} target="_blank" rel="noopener noreferrer">
                      View
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}
    </div>
  );
}

export default function Reconciliation() {
  const { hasFeature } = useAuth();
  const [refreshSignal, setRefreshSignal] = useState(0);

  return (
    <section className="card">
      <InfoBlock brief="Purchased vs. sold vs. counted - the shrinkage/spoilage signal.">
        {currentPosName()}'s own Purchasing &amp; Receiving data isn't accessible with this account's current
        API credentials, so "purchased" here comes from what you log by hand below - it's only as
        complete as what actually gets logged. For each item in a chosen window:{" "}
        <code>expected closing = opening count + purchased - sold</code>, compared against the
        actual counted closing stock. A negative variance means less is physically on hand than
        the math says there should be - shrinkage, spoilage, or simply a purchase that didn't get
        logged. <strong>Until you've been logging purchases for a while, expect most items to
        show a variance</strong> - that's not real loss, it just means nothing's been logged as
        purchased for them yet, so every bit of restocking looks unexplained. The report only gets
        meaningful once logging is routine. "Invoices without a logged purchase" below is a
        different, simpler check: it flags a real invoice (from the Invoice Submission tab) whose
        vendor has no purchase logged around that date - a good sign a delivery was invoiced but
        never entered here.
      </InfoBlock>

      {hasFeature("help") && (
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          New to this? <Link to="/help" state={{ section: "reconciliation" }}>Help</Link> has a worked example with
          your own numbers.
        </p>
      )}

      <LogPurchaseForm onLogged={() => setRefreshSignal((n) => n + 1)} />
      <PurchaseLogUpload onImported={() => setRefreshSignal((n) => n + 1)} />
      <RecentEntries refreshSignal={refreshSignal} />
      <ReconciliationReport />
      <InvoiceGaps />
    </section>
  );
}
