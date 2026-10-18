import { useEffect, useMemo, useState } from "react";
import { getStoreVsContainer, downloadStoreVsContainer } from "../api";
import SortableTh from "./SortableTh.jsx";
import { Term } from "../glossary.jsx";
import { useTableControls } from "../useTableControls.js";

// Store vs Container: only items that have both a store (shelf) row and a
// Container row in the latest upload, with each one's stock. Same pairing as
// Fix Issues' Refill from container and Supplier Projection's "+N container".
const qty = (v) => (v == null ? "-" : Number.isInteger(v) ? String(v) : v.toFixed(2));
const sum = (rows, key) => Math.round(rows.reduce((t, r) => t + (r[key] || 0), 0) * 100) / 100;

const COLUMNS = [
  { key: "name", label: "Item" },
  { key: "supplier", label: "Vendor" },
  { key: "category", label: "Category" },
  { key: "store_qty", label: "Store", align: "right" },
  { key: "container_qty", label: "Container", align: "right" },
  { key: "total_qty", label: "Total", align: "right" },
];

// W2: also the Toast item id (shelf or Container row), every vendor SKU and
// the barcode - the same rule the server's search uses.
const SVC_CODE_KEYS = {
  itemId: ["item_id", "container_item_id"],
  supplier: ["supplier_item_id", "all_supplier_item_ids"],
  barcode: ["barcode"],
};

export default function StoreVsContainer() {
  const [vendor, setVendor] = useState("");
  const [vendors, setVendors] = useState([]);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getStoreVsContainer({ vendor })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        // The vendor list ignores the filter, so keep the full one.
        if (!vendor || vendors.length === 0) setVendors(d.vendors || []);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vendor]);

  // No default sort key: rows keep the server order (Low on shelf first,
  // then by name) until a column is clicked.
  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(data?.items, {
    searchKeys: ["name", "supplier", "supplier_item_id", "category", "subcategory"],
    codeKeys: SVC_CODE_KEYS,
  });
  const lowCount = useMemo(() => rows.filter((r) => r.low_store).length, [rows]);

  async function handleDownload() {
    setDownloading(true);
    try {
      await downloadStoreVsContainer({ search: search.trim(), vendor });
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div style={{ marginTop: "1rem" }}>
      <div className="card-header">
        <h3 style={{ margin: 0 }}>
          Store vs Container
          {data && <span className="muted"> ({data.totals.items})</span>}
        </h3>
        <button disabled={downloading || !rows.length} onClick={handleDownload}>
          {downloading ? "Downloading…" : "Download CSV"}
        </button>
      </div>
      <p className="muted" style={{ marginTop: "0.25rem" }}>
        Only products that have both a store (shelf) row and a Container row in Toast, with the stock in each
        {data?.snapshot_date ? ` (upload of ${data.snapshot_date})` : ""}. Items low on the shelf with stock in the
        Container are listed first.
      </p>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <>
          <div className="svc-toolbar">
            <input
              type="text"
              className="table-search"
              placeholder="Search item, vendor, code…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select value={vendor} onChange={(e) => setVendor(e.target.value)} aria-label="Vendor">
              <option value="">All vendors</option>
              {vendors.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
            {lowCount > 0 && (
              <span className="muted svc-low-count">
                {lowCount} low on shelf
              </span>
            )}
          </div>
          <div className="table-wrap">
            <table className="svc-table">
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <SortableTh
                      key={c.key}
                      sortKey={c.key}
                      currentSortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                      align={c.align}
                    >
                      {c.label}
                    </SortableTh>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.item_id}>
                    <td className="cell-name">
                      {r.name}
                      {r.low_store && (
                        <>
                          {" "}
                          <Term k="store_vs_container" label="Low on shelf">
                            <span className="pill pill-warn">Low on shelf</span>
                          </Term>
                        </>
                      )}
                      {r.supplier_item_id && r.supplier_item_id.replace(/;/g, "").trim() && (
                        <div className="muted svc-code">{r.supplier_item_id}</div>
                      )}
                    </td>
                    <td className="cell-wrap">{r.supplier || "-"}</td>
                    <td className="cell-wrap">{r.category || "-"}</td>
                    <td className="num">
                      <strong className={r.store_qty != null && r.store_qty <= 0 ? "issue-negative" : undefined}>
                        {qty(r.store_qty)}
                      </strong>
                    </td>
                    <td className="num">{qty(r.container_qty)}</td>
                    <td className="num">{qty(r.total_qty)}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={COLUMNS.length} className="muted">
                      {data.items.length === 0
                        ? "No products are in both the store and the Container."
                        : `No items match "${search}".`}
                    </td>
                  </tr>
                )}
              </tbody>
              {rows.length > 0 && (
                <tfoot>
                  <tr className="svc-totals">
                    <td colSpan={3}>
                      Total ({rows.length} item{rows.length === 1 ? "" : "s"})
                    </td>
                    <td className="num">{qty(sum(rows, "store_qty"))}</td>
                    <td className="num">{qty(sum(rows, "container_qty"))}</td>
                    <td className="num">{qty(sum(rows, "total_qty"))}</td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </>
      )}
    </div>
  );
}
