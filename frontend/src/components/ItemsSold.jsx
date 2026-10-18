import { useEffect, useState, useCallback, useRef } from "react";
import { storeTodayStr, storeDateOffsetStr, formatRestaurantTime } from "../format.js";
import { getItemsSold, downloadItemsSold } from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { TermInfo } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { useAuth } from "../AuthContext.jsx";

const RANGE_PRESETS = [
  { value: "today", label: "Today" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "1 Month" },
  { value: "90", label: "3 Months" },
];

// Same shape as the Supplier Projection lookback picker: fixed presets plus
// a "Custom" option that reveals a date-range pair instead of a preset.
const RANGE_OPTIONS = [...RANGE_PRESETS, { value: "custom", label: "Custom" }];

function todayStr() {
  return storeTodayStr();
}

function daysAgoStr(days) {
  return storeDateOffsetStr(-days);
}

// Shown instead of the report when an admin ticked "Hide sales numbers" for
// this user (the server 403s the data too - backend/app/sales_access.py).
function SalesHiddenNotice() {
  return (
    <section className="card">
      <p className="muted" style={{ margin: 0 }}>
        Sales numbers are hidden for your account. Ask an admin if you need them.
      </p>
    </section>
  );
}

export default function ItemsSold() {
  const { canSeeSales } = useAuth();
  if (!canSeeSales) return <SalesHiddenNotice />;
  return <ItemsSoldReport />;
}

function ItemsSoldReport() {
  const [rangePreset, setRangePreset] = useState("today");
  const [startDate, setStartDate] = useState(todayStr());
  const [endDate, setEndDate] = useState(todayStr());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [exporting, setExporting] = useState(false);

  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(data?.items, {
    searchKeys: ["name"],
    codeKeys: true, // W2: Toast ID / SKU / barcode too
    defaultSortKey: "revenue",
    defaultSortDir: "desc",
  });

  // Only the latest range's answer is shown (a slower, older one is dropped).
  const requestId = useRef(0);
  const load = useCallback(async (fresh = false) => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await getItemsSold(startDate, endDate, { fresh });
      if (id === requestId.current) setData(result);
    } catch (err) {
      if (id === requestId.current) setError(err.message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    load();
  }, [load]);

  // Downloads the range the table is showing (the server's own dates).
  async function handleExport() {
    setExporting(true);
    try {
      await downloadItemsSold(data.start_date, data.end_date);
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  }

  function handleRangePresetChange(value) {
    setRangePreset(value);
    if (value === "today") {
      setStartDate(todayStr());
      setEndDate(todayStr());
    } else if (value !== "custom") {
      setStartDate(daysAgoStr(Number(value)));
      setEndDate(todayStr());
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
    <section className="card">
      <InfoBlock brief="Revenue actually paid in this range - see exactly how it's computed below.">
        <p style={{ marginTop: 0 }}>
          <strong>This counts what was actually paid for on each date in this range</strong> - not
          whichever date Toast internally files the order under. If a guest pays today for a
          pickup scheduled tomorrow, that sale counts as <strong>today's</strong>, since that's
          when the money actually came in, even though Toast files the whole order under tomorrow.
          An order that hasn't been paid yet doesn't count anywhere until it actually is.
        </p>
        <p>
          <strong>Revenue is the sum of each sold item's own price</strong> (after any per-item
          discount, before tax) for every item whose payment resolves to a date in this range.
          Add-ons (a spice level, extra cheese) are already part of their item's price in Toast, so
          they are not listed or counted again on their own <TermInfo k="sales_addons" label="Add-ons" />.
          This is a deliberate choice, not Toast's own dashboard figure - Toast's daily total
          instead groups by the day it filed the order under and uses each check's official total
          (which nets out a check-level discount that isn't reflected in the sum of individual
          item prices). The two numbers can differ by a small amount for that reason - that's
          expected, not a sync error.
        </p>
        <p style={{ marginBottom: 0 }}>
          {data?.source === "live" &&
            "Today hasn't been through the nightly sync yet, so this was pulled live from Toast (Refresh pulls again)."}
          {data?.source === "mixed" &&
            "Today's figures were pulled live from Toast; every earlier date in this range is from the already-synced sales data."}
          {(!data || data.source === "stored") && "From the already-synced sales data."}{" "}
          {data?.source && data.source !== "stored" && <TermInfo k="sales_live_today" label="Today (live from Toast)" />}
          {" "}Once Toast Analytics is set up, synced days are also compared with Toast a few times a day
          and re-synced when Toast changed them afterwards (a late void, discount or payment).
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
        <button className="link-button" onClick={() => load(true)} disabled={loading}>
          Refresh
        </button>
      </div>

      {rangePreset === "custom" && (
        <div className="date-range-group" style={{ margin: "0.75rem 0 0" }}>
          <span className="date-range-group-label">Custom range</span>
          <div className="date-range-group-inputs">
            <input type="date" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} />
            <span className="date-range-separator">to</span>
            <input
              type="date"
              value={endDate}
              min={startDate}
              max={todayStr()}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <div className="sp-toolbar">
            <p className="muted" style={{ margin: 0 }}>
              {data.start_date === data.end_date ? data.start_date : `${data.start_date} → ${data.end_date}`}
              {data.live_fetched_at && ` · today live as of ${formatRestaurantTime(data.live_fetched_at)}`}
            </p>
            <button disabled={exporting || loading || data.items.length === 0} onClick={handleExport}>
              {exporting ? "Downloading…" : "Download CSV"}
            </button>
          </div>
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-card-label">Items sold</div>
              <div className="stat-card-value">{data.total_quantity}</div>
            </div>
            <div className="stat-card stat-card-accent">
              <div className="stat-card-label">Revenue</div>
              <div className="stat-card-value">${data.total_revenue.toFixed(2)}</div>
            </div>
          </div>

          {data.items.length === 0 && <p className="muted">No sales recorded for this range.</p>}
          {data.items.length > 0 && (
            <>
              <input
                type="text"
                className="table-search"
                placeholder="Search item, SKU or barcode…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {th("Item", "name")}
                      {th("Qty sold", "quantity", "right")}
                      {th("Revenue", "revenue", "right")}
                      {th("Avg price", "avg_price", "right")}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((item) => (
                      <tr key={item.item_id}>
                        <td>{item.name || item.item_id}</td>
                        <td>{item.quantity}</td>
                        <td>${item.revenue.toFixed(2)}</td>
                        <td>${item.avg_price.toFixed(2)}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td colSpan={4} className="muted">
                          No items match "{search}".
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
