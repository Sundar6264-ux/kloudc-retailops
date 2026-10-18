import { useCallback, useEffect, useMemo, useState } from "react";
import {
  closeIssues,
  downloadIssues,
  getIssueHistory,
  getIssueList,
  getIssueReasons,
  getIssueReport,
  getIssueSummary,
  reopenIssues,
} from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { CAME_BACK_TERMS, FLAG_TERMS, ISSUE_TERMS, Term, TermInfo, useGlossary } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { groupByCategory } from "../groupByCategory.js";
import { formatRestaurantTime, inStoreZone, storeDateOffsetStr, storeTodayStr } from "../format.js";
import { ItemWeeklySales } from "./weeklySales/WeeklySalesTable.jsx";
import { LiveStockNote, MenuCheckNote } from "./DataSourceNote.jsx";
import { useLocation } from "react-router-dom";
import FixIssueFilters from "./issueLeads/FixIssueFilters.jsx";
import {
  EMPTY_FILTERS,
  LEAD_NONE,
  applyFilters,
  groupBySection,
  hasSections,
  leadCount,
  leadFromSearch,
  leadLine,
} from "./issueLeads/issueLeadsModel.js";
import { currentPosName } from "../store/gating.js";
import { useStoreAllows } from "../store/StoreContext.jsx";

const money = (v) => (v != null ? `$${Number(v).toFixed(2)}` : "-");
const whole = (v) => (v != null ? Math.round(v).toLocaleString() : "-");
const PAGE = 200;
const qty = (v) => (v != null ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "-");
// "14 sold ($62.16)" - Toast's "last N day sales" fields are dollars, its
// "orders" fields (sent as last_N_day_units) are the units sold.
const soldLine = (units, dollars) =>
  units == null && dollars == null
    ? "-"
    : [units != null ? `${qty(units)} sold` : null, dollars != null ? `(${money(dollars)})` : null].filter(Boolean).join(" ");
const shortDate = (v) =>
  v ? new Date(`${String(v).slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "-";

// A timestamp as its store-local day, e.g. a 9:45 PM reopen is
// that day, not the next UTC day.
const localDate = (v) =>
  v ? new Date(v).toLocaleDateString(undefined, inStoreZone({ month: "short", day: "numeric", year: "numeric" })) : "-";

const TYPES = [
  {
    key: "missing_barcode",
    label: "Missing barcodes",
    hint: "Products that can't be scanned.",
    tone: "blue",
    searchKeys: ["name", "category", "supplier"],
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "category", label: "Category" },
      { key: "supplier", label: "Supplier", cls: "cell-wrap" },
    ],
  },
  {
    key: "invalid_barcode",
    label: "Invalid barcodes",
    hint: "Barcode on file fails the UPC/EAN check.",
    tone: "red",
    searchKeys: ["name", "barcode", "reason", "supplier"],
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "barcode", label: "Barcode", cls: "cell-wrap" },
      { key: "reason", label: "Problem", cls: "cell-wrap" },
      { key: "supplier", label: "Supplier", cls: "cell-wrap" },
    ],
  },
  {
    key: "duplicate",
    label: "Duplicates",
    hint: "Several priced items with the same name.",
    tone: "purple",
    searchKeys: ["name", "category", "supplier"],
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "copies", label: "Copies", align: "right" },
      { key: "prices", label: "Prices", cls: "cell-wrap" },
      { key: "category", label: "Category" },
    ],
  },
  {
    key: "negative_inventory",
    label: "Negative inventory",
    hint: "On hand below zero - recount.",
    tone: "rose",
    searchKeys: ["name", "category", "supplier"],
    defaultSortKey: "on_hand_qty",
    defaultSortDir: "asc",
    hideProductOnHand: true,
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "category", label: "Category" },
      { key: "on_hand_qty", label: "On hand", align: "right", render: (v) => <span className="issue-negative">{qty(v)}</span> },
      { key: "p_sold_30", label: "Sold (30 days)", align: "right", format: qty },
    ],
    facts: (r) => [
      ["On hand", qty(r.on_hand_qty)],
      ["Rule", `The latest upload shows less than zero on hand - the count in ${currentPosName()} is wrong.`],
    ],
  },
  {
    key: "refill_from_container",
    label: "Refill from container",
    hint: "Shelf low, container has stock.",
    tone: "teal",
    searchKeys: ["name", "category", "supplier"],
    defaultSortKey: "store_qty",
    defaultSortDir: "asc",
    hideProductOnHand: true,
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "category", label: "Category" },
      { key: "store_qty", label: "Store qty", align: "right", render: (v) => <strong className={v <= 0 ? "issue-negative" : undefined}>{qty(v)}</strong> },
      { key: "container_qty", label: "Container qty", align: "right", render: (v) => <strong>{qty(v)}</strong> },
      { key: "p_sold_30", label: "Sold (30 days)", align: "right", format: qty },
    ],
    facts: (r) => [
      ["Store (shelf)", qty(r.store_qty)],
      ["Container", qty(r.container_qty)],
      ["Rule", "Shelf count is 5 or less while the Container row still has stock - move some to the shelf."],
    ],
  },
  // The older "Dead inventory" list (dead_stock, Toast's 90-day sales) is no
  // longer shown - "Dead with stock" replaces it (owner, Sep 29). The backend
  // still serves it for the App Store build; old closures still show in the
  // Closed report under their own label.
  {
    key: "dead_with_stock",
    label: "Dead with stock",
    hint: "Dead in forecast, still in stock.",
    tone: "slate",
    searchKeys: ["name", "category", "supplier"],
    defaultSortKey: "inventory_value",
    defaultSortDir: "desc",
    hideProductOnHand: true,
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "category", label: "Category" },
      { key: "on_hand_qty", label: "On hand", align: "right", format: qty },
      { key: "inventory_value", label: "Value tied up", align: "right", format: money },
      { key: "available_days", label: "Days in stock", align: "right", format: whole },
    ],
    facts: (r) => [
      ["Forecast", "Dead - no sales in 60 days while in stock"],
      ["Sold (90 days)", qty(r.units_sold)],
      ["Days in stock", r.available_days != null ? `${whole(r.available_days)} of ${r.window_days}` : "-"],
      ["On hand", `${qty(r.on_hand_qty)}${r.container_qty != null ? ` (incl. ${qty(r.container_qty)} in container)` : ""}`],
      ["Value tied up", money(r.inventory_value)],
    ],
  },
  // Owner, Oct 2026: dead items with nothing left (shelf + container at 0) -
  // candidates to stop reordering or deactivate in Toast. A negative count
  // stays on Negative inventory instead.
  {
    key: "dead_zero_stock",
    label: "Dead - zero stock",
    hint: "Not selling, nothing left in stock.",
    tone: "amber",
    searchKeys: ["name", "category", "supplier"],
    defaultSortKey: "available_days",
    defaultSortDir: "desc",
    hideProductOnHand: true,
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "category", label: "Category" },
      { key: "on_hand_qty", label: "On hand", align: "right", format: qty },
      { key: "available_days", label: "Days in stock", align: "right", format: whole },
      { key: "last_received", label: "Last received", format: (v) => (v ? shortDate(v) : "-") },
    ],
    // Two ways on (backend `zero_stock_rule`): the forecast calls it dead, or
    // "no_sale" - no sale in 90 days (or never) while it sat at 0, so the
    // forecast calls it slow, not dead (owner, Oct 4).
    facts: (r) => [
      r.zero_stock_rule === "no_sale"
        ? [
            "Sales",
            r.product?.last_sold_date
              ? `No sale in 90+ days (last sold ${shortDate(r.product.last_sold_date)})`
              : r.product
                ? "Never sold"
                : "No sale in 90+ days",
          ]
        : ["Forecast", "Dead - no sales in 60 days while in stock"],
      ["Sold (90 days)", qty(r.units_sold)],
      ["Days in stock", r.available_days != null ? `${whole(r.available_days)} of ${r.window_days}` : "-"],
      ["On hand", `${qty(r.on_hand_qty)}${r.container_qty != null ? ` (incl. ${qty(r.container_qty)} in container)` : ""}`],
      ["First in uploads", r.first_seen_date ? shortDate(r.first_seen_date) : "-"],
      ["Rule", `Not selling and nothing left on the shelf or in the container. Stop reordering, deactivate it in ${currentPosName()}, or reorder on purpose if it should sell.`],
    ],
  },
  {
    key: "dead_new_arrival",
    label: "Dead - newly arrived",
    hint: "Brand-new products, no sale since arriving 5-30 days ago.",
    tone: "orange",
    searchKeys: ["name", "category", "supplier"],
    defaultSortKey: "days_since_arrival",
    defaultSortDir: "desc",
    hideProductOnHand: true,
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      { key: "arrived_date", label: "Arrived", render: (v, r) => (v ? `${shortDate(v)} (${r.days_since_arrival} days)` : "-") },
      { key: "on_hand_qty", label: "On hand", align: "right", format: qty },
      { key: "inventory_value", label: "Value", align: "right", format: money },
    ],
    facts: (r) => [
      ["Arrived", `${shortDate(r.arrived_date)} - ${r.days_since_arrival} days before the latest upload`],
      ["Arrival from", r.arrived_via === "first_upload" ? "First showed up in the inventory upload" : `${currentPosName()}'s last received date`],
      ["Rule", "A brand-new product (never sold) that arrived 5-30 days ago and hasn't sold since. Restocks of items that sold before are left out."],
      ["On hand", `${qty(r.on_hand_qty)}${r.container_qty != null ? ` (incl. ${qty(r.container_qty)} in container)` : ""}`],
    ],
  },
  {
    key: "slow_review",
    label: "Slow moving - Review",
    hint: "Slow sellers marked Review.",
    tone: "indigo",
    searchKeys: ["name", "category", "supplier", "review_reason"],
    defaultSortKey: "units_sold",
    defaultSortDir: "desc",
    hideProductOnHand: true,
    columns: [
      { key: "name", label: "Item", cls: "cell-name" },
      {
        key: "review_reason",
        label: "Why review",
        cls: "cell-wrap",
        render: (v, row) => {
          // One (i) per reason when the flag keys are there; else the text.
          const flags = (row?.review_flags || []).filter((f) => FLAG_TERMS[f]);
          const labels = (v || "").split(", ");
          return (
            <span className="cell-wrap">
              <Term k="review">
                <span className="pill pill-failed">Review</span>
              </Term>{" "}
              {flags.length && flags.length === labels.length
                ? flags.map((f, i) => (
                    <span key={f}>
                      {i > 0 && ", "}
                      <Term k={FLAG_TERMS[f]}>{labels[i]}</Term>
                    </span>
                  ))
                : v || "-"}
            </span>
          );
        },
      },
      { key: "on_hand_qty", label: "On hand", align: "right", format: qty },
      { key: "units_sold", label: "Sold (90 days)", align: "right", format: qty },
      { key: "sale_days", label: "Sale days", align: "right", format: whole },
      { key: "weekly_demand", label: "Per week", align: "right", format: qty },
    ],
    facts: (r) => [
      ["Forecast", "Slow - only a handful of sales"],
      ["Why review", r.review_reason || "-"],
      ["Sold (90 days)", `${qty(r.units_sold)} on ${whole(r.sale_days)} day${r.sale_days === 1 ? "" : "s"}`],
      ["Days in stock", r.available_days != null ? `${whole(r.available_days)} of ${r.window_days}` : "-"],
      ["Forecast per week", qty(r.weekly_demand)],
      ["On hand", `${qty(r.on_hand_qty)}${r.container_qty != null ? ` (incl. ${qty(r.container_qty)} in container)` : ""}`],
    ],
  },
];

const CLOSED_COLUMNS = [
  { key: "reason_label", label: "Reason", render: (v, row) => <ReasonCell row={row} /> },
  { key: "closed_by", label: "Closed by" },
  { key: "closed_at", label: "Closed", format: (v) => (v ? formatRestaurantTime(v) : "-") },
];

function ReasonCell({ row }) {
  return (
    <span className="cell-wrap">
      <Term k="issue_closed" label="a closed reason">
        <span className={`pill ${row.reason === "not_an_issue" ? "pill-neutral" : "pill-success"}`}>{row.reason_label}</span>
      </Term>
      {row.snooze_until && (
        <Term k="issue_snoozed" label="back on a date">
          <span className="pill pill-info issue-came-back">Back on {shortDate(row.snooze_until)}</span>
        </Term>
      )}
      {row.reason_detail && <span className="issue-detail"> {row.reason_detail}</span>}
      {row.no_longer_detected && <span className="issue-detail"> · no longer in the upload</span>}
    </span>
  );
}

// "Came back": a closed item that is on the open list again - its "more
// time" (snooze) ended, a "fixed" close still shows in a newer upload, or a
// come-and-go problem (negative count, low shelf) happened again. The
// backend sends `reopened` for all three; `came_back` (older field) only for
// the "fixed" case.
const CAME_BACK_LABELS = {
  snooze_ended: "Came back - more time ended",
  fixed_but_still_showing: "Came back - still showing after fix",
  detected_again: "Came back - happened again",
};

const isCameBack = (row) => !!(row.reopened || row.came_back);

function cameBackInfo(row) {
  if (row.reopened) {
    const r = row.reopened;
    return {
      how: r.how,
      label: CAME_BACK_LABELS[r.how] || "Came back",
      date: r.at ? localDate(r.at) : null,
      reasonLabel: r.reason_label,
      closedBy: r.closed_by,
      closedAt: r.closed_at,
    };
  }
  if (row.came_back) {
    return {
      how: "fixed_but_still_showing",
      label: CAME_BACK_LABELS.fixed_but_still_showing,
      date: null,
      reasonLabel: row.came_back.reason_label,
      closedBy: row.came_back.closed_by,
      closedAt: row.came_back.closed_at,
    };
  }
  return null;
}

function cameBackTitle(info) {
  return [
    info.reasonLabel && `Closed as "${info.reasonLabel}"`,
    info.closedBy && `by ${info.closedBy}`,
    info.closedAt && `on ${localDate(info.closedAt)}`,
    info.date && `- back on the list ${info.date}`,
  ]
    .filter(Boolean)
    .join(" ");
}

function CameBackPill({ row }) {
  const info = cameBackInfo(row);
  if (!info) return null;
  return (
    <Term k={CAME_BACK_TERMS[info.how] || "issue_came_back"} label="came back">
      <span className="pill pill-warn issue-came-back" title={cameBackTitle(info)}>
        {info.label}
        {info.date ? ` · ${info.date}` : ""}
      </span>
    </Term>
  );
}

function cell(col, row) {
  const v = row[col.key];
  if (col.render) return col.render(v, row);
  if (col.format) return col.format(v);
  return v === null || v === undefined || v === "" ? "-" : v;
}


// Product details from the latest upload (backend `product` object), copied
// onto flat `p_*` fields so the shared table sort/search can use them.
function withProduct(rows) {
  return (rows || []).map((r) => {
    const p = r.product || {};
    return {
      ...r,
      p_on_hand: p.on_hand_qty ?? null,
      p_price: p.price ?? null,
      p_cost: p.cost ?? null,
      p_last_sold: p.last_sold_date ?? null,
      p_code: p.supplier_item_id ?? null,
      // Units, not dollars: Toast's "last 30 day sales" is a $ amount.
      p_sold_30: p.last_30_day_units ?? null,
    };
  });
}

// Search also matches a row's codes (W2): its Toast item id, any vendor SKU
// and barcode, from the row or its `product` details.
const ISSUE_CODE_KEYS = {
  itemId: ["item_id"],
  supplier: [(r) => r.product?.supplier_item_id, "supplier_item_id"],
  barcode: ["barcode", (r) => r.product?.barcode],
};

const PRODUCT_COLUMNS = [
  { key: "p_on_hand", label: "On hand", align: "right", format: qty },
  { key: "p_price", label: "Price", align: "right", format: money },
  { key: "p_cost", label: "Cost", align: "right", format: money },
  { key: "p_last_sold", label: "Last sold", format: (v) => (v ? shortDate(v) : "Never") },
];

function DetailRow({ label, children }) {
  return (
    <div className="issue-detail-row">
      <dt>{label}</dt>
      <dd>{children ?? "-"}</dd>
    </div>
  );
}

function ProductDetails({ product: p }) {
  // "Last received" comes from the POS's receiving records (receiving_records).
  const received = useStoreAllows("received_evidence");
  if (!p) return <p className="muted">No product details - this item is no longer in the latest upload.</p>;
  const margin = p.gross_margin != null ? `${Number(p.gross_margin).toFixed(1)}%` : "-";
  return (
    <>
      <h4>Price and cost</h4>
      <dl className="issue-detail-list">
        <DetailRow label="Price">{money(p.price)}</DetailRow>
        <DetailRow label="Cost">{money(p.cost)}</DetailRow>
        <DetailRow label="Margin">{margin}</DetailRow>
      </dl>
      <h4>Stock</h4>
      <dl className="issue-detail-list">
        <DetailRow label="On hand">{qty(p.on_hand_qty)}</DetailRow>
        <DetailRow label="Stock value">{money(p.inventory_value)}</DetailRow>
        <DetailRow label="Days on hand">{p.days_on_hand != null ? whole(p.days_on_hand) : "-"}</DetailRow>
        {received && (
          <DetailRow label="Last received">
            {p.last_received ? shortDate(p.last_received) : "-"}
            {p.last_received_from ? ` (${p.last_received_from})` : ""}
          </DetailRow>
        )}
      </dl>
      <h4>Sales</h4>
      <dl className="issue-detail-list">
        <DetailRow label="Last 7 days">{soldLine(p.last_7_day_units, p.last_7_day_sales)}</DetailRow>
        <DetailRow label="Last 30 days">{soldLine(p.last_30_day_units, p.last_30_day_sales)}</DetailRow>
        <DetailRow label="Last 90 days">{soldLine(p.last_90_day_units, p.last_90_day_sales)}</DetailRow>
        <DetailRow label="Last sold">{p.last_sold_date ? shortDate(p.last_sold_date) : "No sales on record"}</DetailRow>
      </dl>
      <h4>Identifiers</h4>
      <dl className="issue-detail-list">
        <DetailRow label="Vendor">{p.supplier || "-"}</DetailRow>
        <DetailRow label="Supplier code">{p.supplier_item_id || "-"}</DetailRow>
        <DetailRow label="Barcode">{p.barcode || "-"}</DetailRow>
        <DetailRow label="Category">
          {[p.category_group, p.category, p.subcategory].filter(Boolean).join(" > ") || "-"}
        </DetailRow>
        <DetailRow label="From upload">{p.snapshot_date ? shortDate(p.snapshot_date) : "-"}</DetailRow>
      </dl>
    </>
  );
}

// Every time this item was closed or came back (never deleted), newest first.
function IssueHistory({ typeKey, issueKey, timesClosed }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getIssueHistory(typeKey, issueKey)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [typeKey, issueKey]);

  const times = data?.times_closed ?? timesClosed;
  const events = data?.events || [];
  return (
    <>
      <h4>
        History{" "}
        <span className="muted issue-times-closed">
          <Term k="issue_times_closed" label="times closed">
            {times == null ? "" : times === 0 ? "never closed before" : `closed ${times} time${times === 1 ? "" : "s"}`}
          </Term>
        </span>
      </h4>
      {error && <p className="muted">Couldn't load the history.</p>}
      {data && events.length === 0 && <p className="muted">Nothing closed or reopened yet.</p>}
      {events.length > 0 && (
        <ul className="issue-history">
          {events.slice(0, 10).map((e, i) => (
            <li key={`${e.at}-${i}`}>
              <strong>{e.event === "closed" ? "Closed" : CAME_BACK_LABELS[e.how] || "Reopened"}</strong>
              {e.at ? ` ${formatRestaurantTime(e.at)}` : ""}
              {e.reason_label ? ` - ${e.reason_label}` : ""}
              {e.reason_detail ? ` (${e.reason_detail})` : ""}
              {e.by ? ` by ${e.by}` : ""}
              {e.event === "closed" && e.snooze_until ? ` · back on ${shortDate(e.snooze_until)}` : ""}
            </li>
          ))}
          {events.length > 10 && <li className="muted">and {events.length - 10} older</li>}
        </ul>
      )}
    </>
  );
}

// Same window as the app's item details sheet.
const DETAIL_WEEKS = 12;

function DetailsDrawer({ type, row, view, onClose, onCloseIssue, onReopen }) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="issue-drawer issue-details" role="dialog" aria-label={`${row.name} details`}>
        <div className="issue-drawer-head">
          <h3>{row.name}</h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Close details">
            ✕
          </button>
        </div>
        <p className="muted issue-drawer-items">
          {type.label}
          {row.reason ? ` - ${row.reason}` : ""}
          {row.status ? ` - ${row.status}` : ""}
        </p>
        {isCameBack(row) && (
          <p>
            <CameBackPill row={row} />
            <br />
            <span className="issue-detail">{cameBackTitle(cameBackInfo(row))}</span>
          </p>
        )}
        {view === "closed" && row.snooze_until && (
          <p>
            <Term k="issue_snoozed" label="back on a date">
              <span className="pill pill-info">Back on {shortDate(row.snooze_until)}</span>
            </Term>{" "}
            <span className="issue-detail">If it's still on the list then, it comes back to Open.</span>
          </p>
        )}
        <IssueHistory typeKey={type.key} issueKey={row.issue_key} timesClosed={row.times_closed} />
        {view === "closed" && row.reason_label && (
          <p>
            Closed as <strong>{row.reason_label}</strong>
            {row.reason_detail ? ` - ${row.reason_detail}` : ""}
            {row.closed_by ? ` by ${row.closed_by}` : ""}
            {row.closed_at ? `, ${formatRestaurantTime(row.closed_at)}` : ""}
          </p>
        )}
        {view === "open" && type.facts && (
          <>
            <h4>Why it's listed</h4>
            <dl className="issue-detail-list">
              {type.facts(row).map(([label, value]) => (
                <DetailRow key={label} label={label}>
                  {value}
                </DetailRow>
              ))}
            </dl>
          </>
        )}
        {row.variants?.length ? (
          row.variants.map((v, i) => (
            <section key={v.item_id || i} className="issue-variant">
              <h4 className="issue-variant-title">
                Copy {String.fromCharCode(65 + i)}
                {v.category ? ` - ${v.category}` : ""}
              </h4>
              <ProductDetails product={v.product} />
            </section>
          ))
        ) : (
          <ProductDetails product={row.product} />
        )}
        {!row.variants?.length && row.item_id && (
          // Week-by-week sales with the average on hand beside each week (W3).
          <section className="issue-weekly">
            <h4>Weekly sales (last {DETAIL_WEEKS} weeks)</h4>
            <ItemWeeklySales
              itemId={row.item_id}
              start={storeDateOffsetStr(-DETAIL_WEEKS * 7)}
              end={storeTodayStr()}
              emptyText={`No sales or stock counts in the last ${DETAIL_WEEKS} weeks.`}
              hideWhenForbidden
            />
          </section>
        )}
        <div className="issue-drawer-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Done
          </button>
          {view === "open" ? (
            <button type="button" onClick={() => onCloseIssue(row)}>
              Close issue
            </button>
          ) : (
            <button type="button" onClick={() => onReopen(row)}>
              Reopen
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function CloseDrawer({ type, rows, reasons, onCancel, onDone }) {
  const { terms } = useGlossary();
  const [reason, setReason] = useState("");
  const [detail, setDetail] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const needsDetail = reason === "other";
  const canSave = reason && (!needsDetail || detail.trim()) && !saving;

  async function save(e) {
    e.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await closeIssues(type.key, rows.map((r) => r.issue_key), reason, needsDetail ? detail.trim() : null);
      onDone();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <form className="issue-drawer" onSubmit={save}>
        <div className="issue-drawer-head">
          <h3>{rows.length === 1 ? "Close this issue" : `Close ${rows.length} issues`}</h3>
          <button type="button" className="link-button" onClick={onCancel} aria-label="Cancel">
            ✕
          </button>
        </div>
        <p className="muted issue-drawer-items">
          {rows.length === 1 ? rows[0].name : `${rows.slice(0, 3).map((r) => r.name).join(", ")}${rows.length > 3 ? ` and ${rows.length - 3} more` : ""}`}
        </p>
        <fieldset className="issue-reasons">
          <legend>Why is it closed?</legend>
          {reasons.map((r) => (
            <label key={r.key} className={`issue-reason${reason === r.key ? " selected" : ""}`}>
              <input type="radio" name="reason" value={r.key} checked={reason === r.key} onChange={() => setReason(r.key)} />
              <span>
                {r.label}
                {r.glossary_key && <TermInfo k={r.glossary_key} label={r.label} />}
                {r.does ? (
                  <small>
                    {r.does}
                    {/* What happens next lives in the term's rule text. */}
                    {terms[r.glossary_key]?.rule && !r.does.includes(terms[r.glossary_key].rule)
                      ? ` ${terms[r.glossary_key].rule}`
                      : ""}
                  </small>
                ) : (
                  <>
                    {r.key === "not_an_issue" && <small>Never shown again in {type.label.toLowerCase()}.</small>}
                    {r.key === "fixed" && <small>Comes back if the next inventory upload still shows it.</small>}
                  </>
                )}
              </span>
            </label>
          ))}
        </fieldset>
        {needsDetail && (
          <label className="issue-other">
            Details
            <textarea
              autoFocus
              rows={3}
              maxLength={500}
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder="What was done, or why this isn't a problem"
            />
          </label>
        )}
        {error && <p className="error">{error}</p>}
        <div className="issue-drawer-actions">
          <button type="button" className="btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" disabled={!canSave}>
            {saving ? "Closing…" : rows.length === 1 ? "Close issue" : `Close ${rows.length}`}
          </button>
        </div>
      </form>
    </div>
  );
}

function IssueTable({ type, reasons, onChanged, lead, onLeadChange, savedLeads }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState("open");
  const [selected, setSelected] = useState(() => new Set());
  const [closing, setClosing] = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [limit, setLimit] = useState(PAGE);
  const [otherExpanded, setOtherExpanded] = useState(false);
  const [groupLimits, setGroupLimits] = useState({});
  const [detailsRow, setDetailsRow] = useState(null);
  const [cameBackOnly, setCameBackOnly] = useState(false);
  // W4: section / vendor / task filters (per list); the lead filter lives in
  // FixIssues so it stays across lists and comes from the task's link.
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const allFilters = useMemo(() => ({ ...filters, lead }), [filters, lead]);

  const load = useCallback(() => {
    setError(null);
    return getIssueList(type.key)
      .then(setData)
      .catch((err) => setError(err.message));
  }, [type.key]);

  useEffect(() => {
    setData(null);
    setView("open");
    setCameBackOnly(false);
    setSelected(new Set());
    load();
  }, [load]);

  useEffect(() => {
    setSelected(new Set());
    setLimit(PAGE);
    setOtherExpanded(false);
    setGroupLimits({});
  }, [view, cameBackOnly, allFilters]);

  const cameBackCount = data ? data.came_back_count ?? (data.open || []).filter(isCameBack).length : 0;
  const baseColumns = [...type.columns, ...PRODUCT_COLUMNS.filter((c) => !(type.hideProductOnHand && c.key === "p_on_hand"))];
  const columns = view === "open" ? baseColumns : [...type.columns.slice(0, 2), ...CLOSED_COLUMNS];
  const tabRows = useMemo(() => {
    const list = data?.[view] || [];
    return withProduct(view === "open" && cameBackOnly ? list.filter(isCameBack) : list);
  }, [data, view, cameBackOnly]);
  const viewRows = useMemo(() => applyFilters(tabRows, allFilters), [tabRows, allFilters]);
  const bySection = hasSections(data?.[view]);
  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(viewRows, {
    searchKeys: view === "open" ? type.searchKeys : [...type.searchKeys, "reason_label", "reason_detail", "closed_by"],
    codeKeys: ISSUE_CODE_KEYS,
    defaultSortKey: view === "open" ? type.defaultSortKey || "name" : "closed_at",
    defaultSortDir: view === "open" ? type.defaultSortDir || "asc" : "desc",
  });
  // W4: grouped by PO section (the store aisle), the section's name small
  // above its rows; an older server without po_section keeps categories.
  const { grouped, groups, shown: groupedShown } = bySection
    ? { grouped: rows.length > 0, ...groupBySection(rows, { groupLimits }) }
    : groupByCategory(rows, { groupLimits });
  const otherGroup = grouped ? groups.find((g) => g.isOther) : null;
  // While the "Other categories" bucket is collapsed, its rows aren't
  // actually on screen - keep "select all shown"/CSV export scoped to what's
  // visible, same principle as the un-grouped table only ever meaning what's
  // rendered.
  const shown = grouped
    ? otherGroup && !otherExpanded
      ? groupedShown.filter((r) => !otherGroup.items.includes(r))
      : groupedShown
    : rows.slice(0, limit);
  const allShownSelected = shown.length > 0 && shown.every((r) => selected.has(r.issue_key));

  function showMoreIn(category) {
    setGroupLimits((prev) => ({ ...prev, [category]: (prev[category] ?? 20) + 40 }));
  }

  function toggle(key) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      shown.forEach((r) => (allShownSelected ? next.delete(r.issue_key) : next.add(r.issue_key)));
      return next;
    });
  }

  async function reopen(keys) {
    setBusyKey(keys.length === 1 ? keys[0] : "bulk");
    try {
      await reopenIssues(type.key, keys);
      setSelected(new Set());
      await load();
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyKey(null);
    }
  }

  async function afterClose() {
    setClosing(null);
    setSelected(new Set());
    await load();
    onChanged();
  }

  const selectedRows = (data?.[view] || []).filter((r) => selected.has(r.issue_key));

  function renderRow(row) {
    return (
      <tr key={row.issue_key} className={selected.has(row.issue_key) ? "sp-row-selected" : ""}>
        <td className="col-check">
          <input type="checkbox" checked={selected.has(row.issue_key)} onChange={() => toggle(row.issue_key)} aria-label={`Select ${row.name}`} />
        </td>
        {columns.map((c, i) => (
          <td key={c.key} className={[c.cls, c.align === "right" ? "num" : ""].filter(Boolean).join(" ") || undefined}>
            {i === 0 ? (
              <>
                <button type="button" className="link-button issue-item-link" onClick={() => setDetailsRow(row)}>
                  {cell(c, row)}
                </button>
                {(row.p_code || row.product?.supplier || row.supplier) && (
                  <span className="issue-item-sub">
                    {[row.p_code && `Code ${row.p_code}`, row.product?.supplier || row.supplier].filter(Boolean).join(" · ")}
                  </span>
                )}
                {bySection && (row.lead || view === "open") && (
                  <span className="issue-item-sub fi-lead-line">
                    {leadLine(row.lead)}
                    {row.lead_task && (
                      <span className="pill pill-info fi-task-pill" title={`Listed in ${row.lead.name}'s Fix Issues task for ${shortDate(row.lead_task.day)}`}>
                        In task
                      </span>
                    )}
                  </span>
                )}
              </>
            ) : (
              cell(c, row)
            )}
            {i === 0 && view === "open" && <CameBackPill row={row} />}
          </td>
        ))}
        <td className="col-action">
          {view === "open" ? (
            <button type="button" className="issue-close-btn" onClick={() => setClosing([row])}>
              Close →
            </button>
          ) : (
            <button type="button" className="link-button" disabled={busyKey === row.issue_key} onClick={() => reopen([row.issue_key])}>
              Reopen
            </button>
          )}
        </td>
      </tr>
    );
  }

  return (
    <div className="issue-panel">
      <div className="issue-toolbar">
        <div className="segmented" role="tablist">
          <button type="button" role="tab" aria-selected={view === "open"} className={view === "open" ? "active" : ""} onClick={() => setView("open")}>
            Open{data ? ` (${data.open_count})` : ""}
          </button>
          <button type="button" role="tab" aria-selected={view === "closed"} className={view === "closed" ? "active" : ""} onClick={() => setView("closed")}>
            Closed{data ? ` (${data.closed_count})` : ""}
          </button>
        </div>
        {data?.[view]?.length > 0 && (
          <input type="text" className="table-search" placeholder="Search name, SKU or barcode…" aria-label={`Search name, SKU, barcode or ${currentPosName()} item ID`} value={search} onChange={(e) => setSearch(e.target.value)} />
        )}
        {view === "open" && data && (cameBackCount > 0 || cameBackOnly) && (
          <span className="sp-filter-item">
            <button
              type="button"
              className={`sp-filter-chip${cameBackOnly ? " active" : ""}`}
              aria-pressed={cameBackOnly}
              onClick={() => setCameBackOnly((v) => !v)}
            >
              Came back only ({cameBackCount})
            </button>
            <TermInfo k="issue_came_back" label="came back" />
          </span>
        )}
        {ISSUE_TERMS[type.key] && (
          <span className="muted issue-type-term">
            About this list
            <TermInfo k={ISSUE_TERMS[type.key]} label={type.label} />
          </span>
        )}
        <span className="issue-toolbar-spacer" />
        {selected.size > 0 && view === "open" && (
          <button type="button" onClick={() => setClosing(selectedRows)}>
            Close {selected.size} selected
          </button>
        )}
        {selected.size > 0 && view === "closed" && (
          <button type="button" className="btn-secondary" disabled={busyKey === "bulk"} onClick={() => reopen([...selected])}>
            Reopen {selected.size} selected
          </button>
        )}
        <button type="button" className="link-button" disabled={!data?.[view]?.length} onClick={() => downloadIssues(type.key, view)}>
          Download CSV
        </button>
      </div>

      {data && bySection && (
        <FixIssueFilters
          rows={tabRows}
          savedLeads={savedLeads}
          filters={allFilters}
          shownCount={viewRows.length}
          onChange={(next) => {
            const { lead: nextLead, ...rest } = next;
            setFilters(rest);
            if (nextLead !== lead) onLeadChange(nextLead);
          }}
        />
      )}
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && view === "open" && <LiveStockNote liveStock={data.live_stock} />}
      {data && view === "open" && <MenuCheckNote menuCheckedAt={data.menu_checked_at} />}
      {data && data[view].length === 0 && (
        <p className={view === "open" ? "success" : "muted"}>
          {view === "open" ? "Nothing open here - all clear." : "Nothing closed yet. Closed issues show up here with their reason."}
        </p>
      )}
      {data && view === "open" && cameBackOnly && viewRows.length === 0 && data.open.length > 0 && (
        <p className="muted">Nothing on this list has come back.</p>
      )}
      {data && tabRows.length > 0 && viewRows.length === 0 && !cameBackOnly && (
        <p className="muted">Nothing here matches the filters.</p>
      )}

      {data && viewRows.length > 0 && (
        <div className="table-wrap issue-table-wrap">
          <table className="issue-table">
            <thead>
              <tr>
                <th className="col-check">
                  <input type="checkbox" checked={allShownSelected} onChange={toggleAll} aria-label="Select all shown" />
                </th>
                {columns.map((c) => (
                  <SortableTh key={c.key} sortKey={c.key} currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align={c.align}>
                    {c.label}
                  </SortableTh>
                ))}
                <th className="col-action" />
              </tr>
            </thead>
            {grouped ? (
              groups.map((g) => (
                <tbody key={g.section ?? g.category}>
                  <tr className={`issue-group-header${bySection ? " fi-section-header" : ""}`}>
                    <td colSpan={columns.length + 2}>
                      {bySection ? (
                        <span className="fi-section-name">
                          {g.section} <span className="muted">({g.totalInGroup})</span>
                        </span>
                      ) : g.isOther ? (
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
                  {(!g.isOther || otherExpanded) && g.items.map((row) => renderRow(row))}
                  {(!g.isOther || otherExpanded) && g.totalInGroup > g.items.length && (
                    <tr>
                      <td colSpan={columns.length + 2}>
                        <button type="button" className="link-button issue-more" onClick={() => showMoreIn(g.section ?? g.category)}>
                          Show more ({g.totalInGroup - g.items.length} left in {g.section ?? g.category})
                        </button>
                      </td>
                    </tr>
                  )}
                </tbody>
              ))
            ) : (
              <tbody>{shown.map((row) => renderRow(row))}</tbody>
            )}
            {rows.length === 0 && search && (
              <tbody>
                <tr>
                  <td colSpan={columns.length + 2} className="muted">
                    No items match "{search}".
                  </td>
                </tr>
              </tbody>
            )}
          </table>
        </div>
      )}
      {!grouped && rows.length > limit && (
        <button type="button" className="link-button issue-more" onClick={() => setLimit((l) => l + PAGE)}>
          Show more ({rows.length - limit} left)
        </button>
      )}

      {closing && <CloseDrawer type={type} rows={closing} reasons={reasons} onCancel={() => setClosing(null)} onDone={afterClose} />}
      {detailsRow && !closing && (
        <DetailsDrawer
          type={type}
          row={detailsRow}
          view={view}
          onClose={() => setDetailsRow(null)}
          onCloseIssue={(row) => {
            setDetailsRow(null);
            setClosing([row]);
          }}
          onReopen={(row) => {
            setDetailsRow(null);
            reopen([row.issue_key]);
          }}
        />
      )}
    </div>
  );
}

// The store date `n` days before the store's today (plain-date arithmetic:
// stepping a device-local Date back 24h can skip a store day across a DST
// change when the device is in another zone).
function isoDaysAgo(n) {
  return storeDateOffsetStr(-n);
}

const RANGES = [
  { key: "7", label: "Last 7 days", days: 6 },
  { key: "30", label: "Last 30 days", days: 29 },
  { key: "90", label: "Last 3 months", days: 89 },
  { key: "custom", label: "Custom…" },
];

function ClosedReport() {
  const [range, setRange] = useState("30");
  const [start, setStart] = useState(isoDaysAgo(29));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const preset = RANGES.find((r) => r.key === range);
    if (preset?.days != null) {
      setStart(isoDaysAgo(preset.days));
      setEnd(isoDaysAgo(0));
    }
  }, [range]);

  useEffect(() => {
    if (!start || !end) return undefined;
    // Only the latest range's answer is shown (custom dates change per keystroke).
    let cancelled = false;
    setData(null);
    setError(null);
    getIssueReport(start, end)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [start, end]);

  const typeLabel = Object.fromEntries(TYPES.map((t) => [t.key, t.label]));

  return (
    <div className="issue-panel">
      <div className="issue-toolbar">
        <select value={range} onChange={(e) => setRange(e.target.value)} aria-label="Date range">
          {RANGES.map((r) => (
            <option key={r.key} value={r.key}>
              {r.label}
            </option>
          ))}
        </select>
        {range === "custom" && (
          <>
            <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} aria-label="From" />
            <span className="muted">to</span>
            <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} aria-label="To" />
          </>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.total === 0 && <p className="muted">No issues were closed in this period.</p>}
      {data && data.total > 0 && (
        <>
          <div className="issue-report-summary">
            <div className="issue-report-total">
              <strong>{data.total}</strong>
              <span>closed</span>
            </div>
            <div className="issue-report-reasons">
              {Object.entries(data.by_reason).map(([label, n]) => (
                <span key={label} className="issue-reason-chip">
                  {label} <b>{n}</b>
                </span>
              ))}
            </div>
          </div>
          {data.days.map((day) => (
            <div key={day.date} className="issue-report-day">
              <div className="issue-report-day-head">
                <h4>{new Date(`${day.date}T12:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" })}</h4>
                <span className="muted">
                  {day.total} closed ·{" "}
                  {Object.entries(day.by_type)
                    .map(([t, n]) => `${typeLabel[t] || day.items.find((it) => it.issue_type === t)?.issue_label || t} ${n}`)
                    .join(" · ")}
                </span>
              </div>
              <div className="table-wrap issue-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>Issue</th>
                      <th>Reason</th>
                      <th>Closed by</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {day.items.map((it) => (
                      <tr key={`${it.issue_type}-${it.issue_key}`}>
                        <td className="cell-name">{it.name || "-"}</td>
                        <td>
                          <Term k={ISSUE_TERMS[it.issue_type]}>
                            <span className={`issue-type-tag tone-${TYPES.find((t) => t.key === it.issue_type)?.tone || "blue"}`}>{it.issue_label}</span>
                          </Term>
                        </td>
                        <td>
                          <ReasonCell row={it} />
                        </td>
                        <td>{it.closed_by || "-"}</td>
                        <td>{new Date(it.closed_at).toLocaleTimeString("en-US", inStoreZone({ hour: "numeric", minute: "2-digit" }))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export default function FixIssues() {
  const location = useLocation();
  const [active, setActive] = useState("missing_barcode");
  const [summary, setSummary] = useState(null);
  // W4: the lead filter - from the daily task's link (?lead=<id>), kept
  // across lists; savedLeads = every saved category / vendor lead.
  const [lead, setLead] = useState(() => leadFromSearch(location.search));
  const [savedLeads, setSavedLeads] = useState([]);
  useEffect(() => {
    const fromLink = leadFromSearch(location.search);
    if (fromLink) setLead(fromLink);
  }, [location.search]);
  const [reasons, setReasons] = useState(null);
  const [error, setError] = useState(null);

  const loadSummary = useCallback(() => {
    getIssueSummary()
      .then((d) => {
        setSummary(Object.fromEntries(d.items.map((i) => [i.issue_type, i])));
        setSavedLeads(d.leads || []);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    loadSummary();
    getIssueReasons()
      .then(setReasons)
      .catch((err) => setError(err.message));
  }, [loadSummary]);

  const activeType = useMemo(() => TYPES.find((t) => t.key === active), [active]);

  // On a phone the tiles fill the screen, so bring the picked list into
  // view instead of leaving it below the fold.
  function pick(key) {
    setActive(key);
    if (window.matchMedia?.("(max-width: 768px)").matches) {
      requestAnimationFrame(() => document.querySelector(".issue-card")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }

  return (
    <>
      <InfoBlock brief="Work through each list and close items with a reason. Closed items move to the Closed list, and the Closed report shows what was closed each day.">
        The lists come from the latest inventory upload. Kitchen and catering menu items are left out, since
        they're made in the store and don't need barcodes or shelf stock. To close an item, click Close → on its row
        (or tick several and use Close selected), then pick a reason. "Not an issue" hides the item from that list for
        good. "Fixed in {currentPosName()}" is checked again: if the next inventory upload still shows the same problem, the item
        comes back to the open list marked "Came back". "New item - giving it more time" closes it for one week: after
        that, if it still hasn't sold, it comes back to the open list marked "Came back - more time ended" (the
        Closed list shows "Back on" with the date). Use "Came back only" to see just those. Anything closed by
        mistake can be reopened from the Closed list. The stock lists (negative inventory, refill from container, dead with stock, dead - newly
        arrived, slow moving - review) work the same way; the slow and dead-with-stock lists use the same forecast
        as Supplier Projection, over its default 3-month window. A negative count or a low shelf comes and goes, so
        closing one covers that time only: once a later upload shows it cleared, it comes back if it happens again.
      </InfoBlock>
      {error && <p className="error">{error}</p>}
      {lead && (
        <p className="fi-lead-banner">
          Showing{" "}
          <strong>
            {lead === LEAD_NONE ? "items with no lead" : `${savedLeads.find((p) => String(p.user_id) === lead)?.name || "one lead"}'s items`}
          </strong>{" "}
          on every list.{" "}
          <button type="button" className="link-button" onClick={() => setLead("")}>
            Show everyone's
          </button>
        </p>
      )}

      <div className="issue-tabs" role="tablist">
        {TYPES.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={active === t.key}
            className={`issue-tab tone-${t.tone}${active === t.key ? " active" : ""}`}
            onClick={() => pick(t.key)}
          >
            <span className="issue-tab-count">{summary ? (lead ? leadCount(summary[t.key], lead) : summary[t.key]?.open_count) ?? "-" : "…"}</span>
            <span className="issue-tab-label">{t.label}</span>
            <span className="issue-tab-hint">{t.hint}</span>
            {summary?.[t.key]?.came_back_count > 0 && (
              <span className="issue-tab-cameback">{summary[t.key].came_back_count} came back</span>
            )}
          </button>
        ))}
        <button
          type="button"
          role="tab"
          aria-selected={active === "report"}
          className={`issue-tab tone-green${active === "report" ? " active" : ""}`}
          onClick={() => pick("report")}
        >
          <span className="issue-tab-count">
            {summary ? Object.values(summary).reduce((n, s) => n + (s.closed_count || 0), 0) : "…"}
          </span>
          <span className="issue-tab-label">Closed report</span>
          <span className="issue-tab-hint">What was closed, day by day.</span>
        </button>
      </div>

      <section className="card issue-card">
        {active === "report" ? (
          <ClosedReport />
        ) : reasons ? (
          <IssueTable
            key={active}
            type={activeType}
            reasons={reasons[active].reasons}
            onChanged={loadSummary}
            lead={lead}
            onLeadChange={setLead}
            savedLeads={savedLeads}
          />
        ) : (
          <p className="muted">Loading…</p>
        )}
      </section>
    </>
  );
}
