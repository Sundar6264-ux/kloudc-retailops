import { useEffect, useState } from "react";
import {
  getPriceChanges,
  downloadPriceChanges,
  searchPriceHistory,
  getVendorPriceComparison,
  downloadVendorPriceComparison,
} from "../api";
import InfoBlock from "./InfoBlock.jsx";
import StoreVsContainer from "./StoreVsContainer.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { groupByCategory } from "../groupByCategory.js";

const money = (v) => (v != null ? `$${v.toFixed(2)}` : "-");

const REPORTS = {
  price_changes: {
    label: "Price change log",
    description: "Items whose price actually changed between two inventory uploads.",
    loadFn: getPriceChanges,
    downloadFn: downloadPriceChanges,
    searchKeys: ["name", "category"],
    defaultSortKey: "last_change_date",
    defaultSortDir: "desc",
    columns: [
      { key: "name", label: "Item" },
      { key: "category", label: "Category" },
      { key: "previous_price", label: "Previous price", format: money, align: "right" },
      { key: "current_price", label: "Current price", format: money, align: "right" },
      { key: "last_change_date", label: "Last changed" },
      { key: "change_count", label: "# changes", align: "right" },
    ],
  },
  vendor_price: {
    label: "Vendor price comparison",
    description:
      "Items you've bought from more than one vendor (via Delivery Review or a manually logged " +
      "purchase with a cost entered), where the vendor you bought from most recently costs more " +
      "than another vendor already on file for that same item - worst savings first.",
    loadFn: getVendorPriceComparison,
    downloadFn: downloadVendorPriceComparison,
    emptyIsGood: false,
    emptyMessage:
      "Nothing to compare yet - this builds up as you confirm deliveries (Delivery Review) or log " +
      "purchases with a cost for the same item from more than one vendor.",
    searchKeys: ["name", "category", "current_vendor", "cheaper_vendor"],
    defaultSortKey: "potential_savings",
    defaultSortDir: "desc",
    columns: [
      { key: "name", label: "Item" },
      { key: "category", label: "Category" },
      { key: "current_vendor", label: "Current Vendor" },
      { key: "current_cost", label: "Current Cost", format: money, align: "right" },
      { key: "current_date", label: "As Of" },
      { key: "cheaper_vendor", label: "Cheaper Vendor" },
      { key: "cheaper_cost", label: "Cheaper Cost", format: money, align: "right" },
      { key: "cheaper_date", label: "As Of" },
      { key: "potential_savings", label: "Savings/unit", format: money, align: "right" },
    ],
  },
  // Own component (vendor filter, Low on shelf pill, totals row).
  store_vs_container: {
    label: "Store vs Container",
    custom: true,
  },
};

function ReportTable({ report }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [otherExpanded, setOtherExpanded] = useState(false);
  const [groupLimits, setGroupLimits] = useState({});

  useEffect(() => {
    setData(null);
    setError(null);
    setOtherExpanded(false);
    setGroupLimits({});
    report
      .loadFn()
      .then(setData)
      .catch((err) => setError(err.message));
  }, [report]);

  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(data?.items, {
    searchKeys: report.searchKeys,
    codeKeys: true, // W2: Toast ID / barcode too
    defaultSortKey: report.defaultSortKey,
    defaultSortDir: report.defaultSortDir || "asc",
  });
  const { grouped, groups } = groupByCategory(rows, { groupLimits });

  function showMoreIn(category) {
    setGroupLimits((prev) => ({ ...prev, [category]: (prev[category] ?? 20) + 40 }));
  }

  function renderRow(item) {
    return (
      <tr key={item.item_id}>
        {report.columns.map((c) => (
          <td key={c.key}>{c.format ? c.format(item[c.key]) : item[c.key] || "-"}</td>
        ))}
      </tr>
    );
  }

  async function handleDownload() {
    setDownloading(true);
    try {
      await report.downloadFn();
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
          {report.label}
          {data && <span className="muted"> ({data.count})</span>}
        </h3>
        <button disabled={downloading || !data?.count} onClick={handleDownload}>
          {downloading ? "Downloading…" : "Download CSV"}
        </button>
      </div>
      <p className="muted" style={{ marginTop: "0.25rem" }}>
        {report.description}
      </p>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.items.length === 0 && (
        <p className={report.emptyIsGood === false ? "muted" : "success"}>
          {report.emptyMessage || "None found - clean."}
        </p>
      )}
      {data && data.items.length > 0 && (
        <>
          <input
            type="text"
            className="table-search"
            placeholder="Search…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {report.columns.map((c) => (
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
              {grouped ? (
                groups.map((g) => (
                  <tbody key={g.category}>
                    <tr className="issue-group-header">
                      <td colSpan={report.columns.length}>
                        {g.isOther ? (
                          <button type="button" className="link-button issue-group-toggle" onClick={() => setOtherExpanded((v) => !v)}>
                            {otherExpanded ? "▾" : "▸"} Other categories ({g.minorCategoryCount} categories, {g.totalInGroup} item{g.totalInGroup === 1 ? "" : "s"})
                          </button>
                        ) : (
                          <span>
                            {g.category} <span className="muted">({g.totalInGroup})</span>
                          </span>
                        )}
                      </td>
                    </tr>
                    {(!g.isOther || otherExpanded) && g.items.map((item) => renderRow(item))}
                    {(!g.isOther || otherExpanded) && g.totalInGroup > g.items.length && (
                      <tr>
                        <td colSpan={report.columns.length}>
                          <button type="button" className="link-button issue-more" onClick={() => showMoreIn(g.category)}>
                            Show more ({g.totalInGroup - g.items.length} left in {g.category})
                          </button>
                        </td>
                      </tr>
                    )}
                  </tbody>
                ))
              ) : (
                <tbody>{rows.map((item) => renderRow(item))}</tbody>
              )}
              {rows.length === 0 && (
                <tbody>
                  <tr>
                    <td colSpan={report.columns.length} className="muted">
                      No items match "{search}".
                    </td>
                  </tr>
                </tbody>
              )}
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function PriceHistorySearch() {
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);

  async function handleSearch(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setSearchError(null);
    try {
      const result = await searchPriceHistory(query.trim());
      setSearchResults(result.items);
    } catch (err) {
      setSearchError(err.message);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div style={{ marginTop: "1rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
      <h3 style={{ margin: "0 0 0.25rem", fontSize: "0.95rem" }}>Look up one item's full price history</h3>
      <p className="muted" style={{ margin: "0 0 0.75rem" }}>
        Type an item name or barcode to see every price it's had on file, not just recent changes.
      </p>
      <form onSubmit={handleSearch} style={{ display: "flex", gap: "0.75rem" }}>
        <input
          type="text"
          placeholder="Item name or barcode…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1, maxWidth: "320px" }}
        />
        <button type="submit" disabled={searching || !query.trim()}>
          {searching ? "Searching…" : "Search"}
        </button>
      </form>
      {searchError && <p className="error">{searchError}</p>}

      {searchResults && searchResults.length === 0 && (
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          No items matched "{query}".
        </p>
      )}

      {searchResults && searchResults.length > 0 && (
        <div style={{ marginTop: "1rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
          {searchResults.map((item) => (
            <div key={item.item_id}>
              <strong>{item.name}</strong>{" "}
              <span className="muted">
                {item.barcode && `· ${item.barcode}`} {item.category && `· ${item.category}`}
              </span>
              <div className="table-wrap" style={{ marginTop: "0.4rem" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Price</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...item.history].reverse().map((h) => (
                      <tr key={h.date}>
                        <td>{h.date}</td>
                        <td>{money(h.price)}</td>
                        <td>{money(h.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function InventoryReports() {
  const [selected, setSelected] = useState(null);

  return (
    <section className="card">
      <InfoBlock brief="Price changes, vendor price comparison and Store vs Container. Every report here has a Download CSV button. Barcode problems, duplicates, dead stock and negative inventory are on Fix Issues.">
        The price change log compares consecutive inventory uploads per item - only as complete as your
        upload history, and it also lets you look up one item's full price/cost timeline. Vendor price
        comparison isn't built from the inventory file's own `cost` field (that's one blended number per item);
        it's built from real vendor-attributed costs captured through Delivery Review or a manually logged
        purchase, so it only has something to show once that data exists. Store vs Container lists only the
        products that have both a store (shelf) row and a Container row in the latest upload, with the stock in
        each, the same pairing Supplier Projection and Refill from container use. Missing and invalid barcodes,
        duplicates, dead stock and negative inventory are on Fix Issues, where each item can be closed with a
        reason.
      </InfoBlock>

      {!selected && (
        <div className="report-tiles">
          {Object.entries(REPORTS).map(([key, report]) => (
            <button key={key} className="report-tile" onClick={() => setSelected(key)}>
              <span className="report-tile-title">{report.label}</span>
              <span className="report-tile-cta">View report →</span>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <>
          <button className="link-button" onClick={() => setSelected(null)}>
            ← Choose a different report
          </button>
          {selected === "price_changes" && <PriceHistorySearch />}
          {REPORTS[selected].custom ? (
            selected === "store_vs_container" && <StoreVsContainer />
          ) : (
            <ReportTable report={REPORTS[selected]} />
          )}
        </>
      )}
    </section>
  );
}
