import { useEffect, useState, useCallback, useRef } from "react";
import { storeTodayStr } from "../format.js";
import { getOpenOrders, getAllTimeOpenOrders, rescanAllTimeOpenOrders, getOrderEmployees, downloadOpenOrders } from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { Term, TermInfo } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { useAuth } from "../AuthContext.jsx";
import { currentPosName } from "../store/gating.js";

function todayStr() {
  return storeTodayStr();
}

function formatElapsed(minutes) {
  if (minutes == null) return "-";
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  if (h < 24) return `${h}h ${minutes % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function formatWhen(iso, allTime) {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-US", {
    month: allTime ? "short" : undefined,
    day: allTime ? "numeric" : undefined,
    hour: "numeric",
    minute: "2-digit",
  });
}

// A "paid" order still being open is expected for an online/pickup order
// (Toast charges on placement, closes on fulfillment) rather than a stuck
// dine-in tab - and a guest can pay tonight for a pickup scheduled tomorrow
// (see the project skill's "future order" gotcha), which isn't a problem at
// all. Split those out from the rest so the main list isn't cluttered with
// orders that aren't actually anything to worry about right now.
function isScheduledForLater(o) {
  return Boolean(o.paid_at && o.promised_at && new Date(o.promised_at) > new Date());
}

function OrdersTable({ list, allTime, search, columns, th }) {
  // No Total column for a "Hide sales numbers" user (the server sends null).
  const { canSeeSales } = useAuth();
  const colCount = (allTime ? 9 : 8) - (canSeeSales ? 0 : 1);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {allTime && th("Business date", "business_date")}
            {th("Check #", "display_number")}
            {th(<Term k="open_order_tab_name" label="tab name">Tab</Term>, "tab_name")}
            {th("Opened", "opened_at")}
            {th(columns.durationLabel, columns.durationKey, "right")}
            {th("Opened by", "server_name")}
            {th("Guests", "num_guests", "right")}
            {canSeeSales && th("Total", "total_amount", "right")}
            <th>Items</th>
          </tr>
        </thead>
        <tbody>
          {list.map((o) => (
            <tr key={o.guid}>
              {allTime && <td>{o.business_date}</td>}
              <td>{o.display_number || o.guid.slice(0, 8)}</td>
              {/* The Toast tab name is usually the customer's name (owner, Oct 4). */}
              <td className="open-check-tab">{o.tab_name ? <strong>{o.tab_name}</strong> : <span className="muted">-</span>}</td>
              <td>{formatWhen(o.opened_at, allTime)}</td>
              <td>{columns.durationKey === "promised_at" ? formatWhen(o.promised_at, allTime) : formatElapsed(o.elapsed_minutes)}</td>
              <td>{o.server_name}</td>
              <td>{o.num_guests ?? "-"}</td>
              {canSeeSales && <td>{o.total_amount != null ? `$${o.total_amount.toFixed(2)}` : "-"}</td>}
              <td style={{ minWidth: "16rem" }}>
                {!o.line_items || o.line_items.length === 0
                  ? "-"
                  : o.line_items.map((item, idx) => (
                      <div key={idx}>
                        {item.quantity}x {item.name}
                        {idx < o.line_items.length - 1 ? "," : ""}
                      </div>
                    ))}
              </td>
            </tr>
          ))}
          {list.length === 0 && (
            <tr>
              <td colSpan={colCount} className="muted">
                No checks match "{search}".
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default function OpenOrders() {
  const [allTime, setAllTime] = useState(true);
  const [date, setDate] = useState(todayStr());
  const [employeeGuid, setEmployeeGuid] = useState("");
  const [employees, setEmployees] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [rescanning, setRescanning] = useState(false);
  const [exporting, setExporting] = useState(false);

  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(data?.orders, {
    searchKeys: ["display_number", "tab_name", "server_name"],
    defaultSortKey: "opened_at",
    defaultSortDir: "asc",
  });

  // A slow live Toast read (one date) must not overwrite a newer, faster
  // answer (e.g. the all-time cache after ticking All-time back on).
  const requestId = useRef(0);
  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = allTime ? await getAllTimeOpenOrders(employeeGuid || undefined) : await getOpenOrders(date, employeeGuid || undefined);
      if (id === requestId.current) setData(result);
    } catch (err) {
      if (id === requestId.current) setError(err.message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [allTime, date, employeeGuid]);

  useEffect(() => {
    getOrderEmployees()
      .then((r) => setEmployees(r.employees))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleRescan() {
    setRescanning(true);
    setError(null);
    try {
      await rescanAllTimeOpenOrders();
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setRescanning(false);
    }
  }

  // Same list the page loaded: all-time cache, or the live list for data.date.
  async function handleExport() {
    setExporting(true);
    try {
      await downloadOpenOrders({
        allTime: data.mode === "all_time",
        date: data.date,
        employeeGuid: employeeGuid || undefined,
      });
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

  const scheduledForLater = rows.filter(isScheduledForLater);
  const needsAttention = rows.filter((o) => !isScheduledForLater(o));

  return (
    <section className="card">
      <div className="card-header" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
        <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
          <input type="checkbox" checked={allTime} onChange={(e) => setAllTime(e.target.checked)} />
          All-time
        </label>
        {!allTime && (
          <label style={{ display: "flex", flexDirection: "column", fontSize: "0.82rem", gap: "0.3rem" }}>
            Date
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
        )}
        <label style={{ display: "flex", flexDirection: "column", fontSize: "0.82rem", gap: "0.3rem" }}>
          Opened by
          <select value={employeeGuid} onChange={(e) => setEmployeeGuid(e.target.value)}>
            <option value="">All employees</option>
            {employees.map((e) => (
              <option key={e.guid} value={e.guid}>
                {e.name}
              </option>
            ))}
          </select>
        </label>
        <button className="link-button" onClick={load}>
          Refresh
        </button>
        <button
          className="link-button"
          onClick={handleExport}
          disabled={exporting || loading || !data?.orders?.length}
        >
          {exporting ? "Downloading…" : "Download CSV"}
        </button>
        {allTime && (
          <button className="link-button" onClick={handleRescan} disabled={rescanning}>
            {rescanning ? "Rescanning… (can take a minute or more)" : "Rescan now (slow)"}
          </button>
        )}
      </div>

      <InfoBlock brief={`Checks ${currentPosName()} has recorded that haven't been closed on the POS yet. Tab is the name on the check's tab in ${currentPosName()}, usually the customer.`}>
        <p style={{ marginTop: 0 }}>
          With <strong>All-time off</strong>, this is a live pull straight from {currentPosName()} for one date
          (normally today) - fast, one request. Picking a past date will normally show zero results,
          since by then that day's checks have already been closed.
        </p>
        <p>
          <strong>All-time</strong> shows every open check regardless of date, straight from a
          background cache kept fresh automatically - today's checks refresh every 15 minutes, and
          the full history (catching a check stuck open for days or weeks) is re-swept once a day.
          It's instant to load. <strong>Rescan now</strong> forces a real, immediate full re-scan
          instead of waiting for the next automatic one.
        </p>
        <p style={{ marginBottom: 0 }}>
          <strong>Scheduled for later</strong> is its own section below: a check that's fully paid
          AND promised for a time that hasn't arrived yet (a guest can pay tonight for a pickup
          scheduled tomorrow) isn't a problem - it's just not due. Everything else, including a paid
          check that's already past its promised time, stays in the main list since that one
          actually needs a look.
        </p>
      </InfoBlock>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}

      {data && !loading && (
        <>
          <div className="grid" style={{ marginTop: "0.75rem", marginBottom: "1rem" }}>
            <div>
              <div className="muted">Open checks</div>
              <div style={{ fontSize: "1.5rem", fontWeight: 700 }}>{data.count}</div>
            </div>
            {data.mode === "all_time" && (
              <div>
                <div className="muted">Cache last refreshed</div>
                <div style={{ fontSize: "0.95rem" }}>
                  {data.last_refreshed_at
                    ? new Date(data.last_refreshed_at).toLocaleString("en-US", {
                        month: "short",
                        day: "numeric",
                        hour: "numeric",
                        minute: "2-digit",
                      })
                    : "never yet - waiting on the first background scan"}
                </div>
              </div>
            )}
            {data.mode === "all_time" && data.scanned_from && (
              <div>
                <div className="muted">Covers</div>
                <div style={{ fontSize: "0.95rem" }}>
                  {data.scanned_from} to {data.scanned_to}
                </div>
              </div>
            )}
          </div>

          {data.orders.length === 0 && <p className="muted">No open checks found.</p>}
          {data.orders.length > 0 && (
            <>
              <input
                type="text"
                className="table-search"
                placeholder="Search check #, tab name or employee…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />

              <h3 style={{ fontSize: "0.95rem", marginBottom: "0.4rem" }}>
                Needs a look ({needsAttention.length})
              </h3>
              <OrdersTable
                list={needsAttention}
                allTime={allTime}
                search={search}
                th={th}
                columns={{
                  durationLabel: <Term k="open_order_too_long" label="open too long">Open for</Term>,
                  durationKey: "elapsed_minutes",
                }}
              />

              {scheduledForLater.length > 0 && (
                <>
                  <h3 style={{ fontSize: "0.95rem", margin: "1.25rem 0 0.4rem" }}>
                    Scheduled for later - paid, not yet due ({scheduledForLater.length})
                    <TermInfo k="open_order_scheduled" label="Scheduled" />
                  </h3>
                  <OrdersTable
                    list={scheduledForLater}
                    allTime={allTime}
                    search={search}
                    th={th}
                    columns={{ durationLabel: "Due at", durationKey: "promised_at" }}
                  />
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
