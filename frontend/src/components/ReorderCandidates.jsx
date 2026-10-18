import { Fragment, useCallback, useMemo, useState } from "react";
import { getReorderCandidates, showCriticalVendorAgain } from "../api";
import { useAuth } from "../AuthContext.jsx";
import InfoBlock from "./InfoBlock.jsx";
import {
  groupDismissal,
  HiddenChip,
  HiddenFromTodayPanel,
  HideActions,
  HideFromTodayDrawer,
  OutsideOrderedTag,
  UndoBar,
  useCriticalDismissals,
  usePlacedOutside,
  vendorKey,
} from "./CriticalDismissals.jsx";
import { CLASS_TERMS, Term, URGENCY_TERMS } from "../glossary.jsx";
import { LiveStockNote } from "./DataSourceNote.jsx";
// Add to the Purchase Order Cart (users with the cart feature).
import {
  CartHowTo,
  CartReviewPanel,
  RowCartAction,
  RowSelect,
  SelectAll,
  VendorCartBar,
  useReorderCart,
} from "./reorderCart/ReorderCart.jsx";
import { canAddToVendor } from "./reorderCart/reorderCartModel.js";
// Week-by-week sales + average on hand behind an item's Avg/day (W3).
import { ItemWeeklySales } from "./weeklySales/WeeklySalesTable.jsx";
import { isoMinusDays } from "./weeklySales/weeklySalesModel.js";
import { rowCodeMatch } from "../productMatch.js";

// Same colour coding as Supplier Projection's item type tags.
const CLASS_PILLS = {
  regular: "pill-success",
  intermittent: "pill-running",
  slow: "pill-info",
  dead: "pill-failed",
};

const CLASS_HINTS = {
  regular: "Regular - sells most days",
  intermittent: "Intermittent - sells now and then",
  slow: "Slow - only a handful of sales",
  dead: "Dead - no sales in 60 days while in stock",
};

// A vendor's status is its worst item's status, except "critical" needs 3+
// critical items or one critical fast seller (fewer shows "order now") - see
// _vendor_urgency in the backend.
const URGENCY_PILLS = {
  critical: "pill-failed",
  order_now: "pill-warn",
  check_count: "pill-check",
  ordered: "pill-success",
  can_wait: "pill-info",
  low_priority: "pill-neutral",
};

const TIER_COUNT_LABELS = [
  ["critical", "critical"],
  ["order_now", "order now"],
  ["check_count", "check count"],
  ["ordered", "ordered"],
  ["can_wait", "can wait"],
  ["low_priority", "low"],
];

// "13 critical · 15 order now · 34 can wait" - the vendor's full picture,
// since its single label only reflects its worst tier. Falls back to
// counting the items itself if the backend didn't send tier_counts.
function TierCounts({ vendor }) {
  const counts =
    vendor.tier_counts ||
    vendor.items.reduce((acc, c) => {
      acc[c.urgency.tier] = (acc[c.urgency.tier] || 0) + 1;
      return acc;
    }, {});
  const parts = TIER_COUNT_LABELS.filter(([key]) => counts[key] > 0);
  if (parts.length === 0) return null;
  return (
    <span className="reorder-tier-counts">
      {parts.map(([key, label], i) => (
        <span key={key}>
          {i > 0 && <span className="muted"> · </span>}
          <span className={`tier-count tier-count-${key}`}>
            {counts[key]} {label}
          </span>
        </span>
      ))}
    </span>
  );
}

// An item's type tag (regular / intermittent / slow / dead).
function ClassPill({ classification }) {
  if (!classification) return null;
  return (
    <Term k={CLASS_TERMS[classification]}>
      <span className={`pill ${CLASS_PILLS[classification] || ""} sp-pill`} title={CLASS_HINTS[classification]}>
        {classification}
      </span>
    </Term>
  );
}

function formatMoney(value) {
  return value == null ? "-" : `$${value.toFixed(2)}`;
}

// A plain "YYYY-MM-DD" (last delivery) is a store calendar day: parsed bare
// it would be UTC midnight and show the day before in New York. Timestamps
// (last placed) are shown in store time.
function formatDate(iso) {
  if (!iso) return "never (in this app)";
  const opts = { month: "short", day: "numeric", year: "numeric" };
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    return new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { ...opts, timeZone: "UTC" });
  }
  return new Date(iso).toLocaleDateString(undefined, { ...opts, timeZone: "America/New_York" });
}

// "Ordered Sep 30" - an order placed for this vendor that hasn't arrived
// yet (tier "ordered", see ordered_vendors in the backend).
function urgencyText(urgency, orderedAt) {
  if (urgency.tier !== "ordered" || !orderedAt) return urgency.label;
  const day = new Date(orderedAt).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  });
  return `${urgency.label} ${day}`;
}

export default function ReorderCandidates() {
  const { user, hasFeature } = useAuth();
  const isAdmin = !!user?.is_admin;
  // Same gate as the cart page and its floating button; the demo login sees
  // the controls but never writes (useReorderCart says so instead).
  const canCart = hasFeature("purchase_order_cart");
  const rc = useReorderCart({ enabled: canCart, demo: !!user?.demo });
  const { clearSelection, readCart } = rc;
  // Tick boxes and Add buttons: a real vendor (not "Unassigned"), for cart users.
  const cartOnFor = (vendor) => canCart && canAddToVendor(vendor);
  const [lookbackDays, setLookbackDays] = useState(30);
  const [leadTimeDays, setLeadTimeDays] = useState(7);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  // Critical vendors hidden from Today (CriticalDismissals.jsx).
  const dismissals = useCriticalDismissals(isAdmin);
  const reloadDismissals = dismissals.reload;
  const [hideFor, setHideFor] = useState(null); // {vendor, criticalCount, kind}
  const [undo, setUndo] = useState(null); // {id, vendor, message, warning}
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(null);
  // "vendor|item_id" of the item whose weekly sales are open (one at a time).
  const [weeklyOpen, setWeeklyOpen] = useState(null);

  // Updates the loaded report in place (no 3-second recalculation): the
  // server only adds critical_dismissed / critical_dismissal to a vendor.
  const markVendor = useCallback((vendor, dismissal) => {
    setData((d) =>
      d
        ? {
            ...d,
            vendors: d.vendors.map((v) =>
              vendorKey(v.vendor) === vendorKey(vendor)
                ? { ...v, critical_dismissed: !!dismissal, critical_dismissal: dismissal }
                : v
            ),
          }
        : d
    );
  }, []);

  const showAgain = useCallback(
    async (d) => {
      setBusyId(d.id);
      setActionError(null);
      // This one's Undo message (if showing) goes once the request is done.
      const dropUndo = () => setUndo((u) => (u && u.id === d.id ? null : u));
      try {
        await showCriticalVendorAgain(d.id);
        dropUndo();
        // The server ends every active dismissal of the vendor - unless this
        // one had already ended: then another admin's newer action stays (a
        // page loaded before they acted still shows the old one). Follow the
        // server's list rather than assuming the vendor is back.
        const fresh = await reloadDismissals();
        const still = fresh?.items?.find((i) => vendorKey(i.vendor) === vendorKey(d.vendor));
        markVendor(d.vendor, still ? groupDismissal(still) : null);
        if (still) {
          const who = still.created_by_name || "Another admin";
          const until = (still.ends_label || "").replace(/^Until /, "until ");
          setUndo({
            id: null,
            vendor: d.vendor,
            message: `${d.vendor} is still hidden from Today: ${who} has since hidden it again (${still.kind_label}${until ? `, ${until}` : ""}).`,
            warning: null,
          });
        }
      } catch (err) {
        dropUndo();
        setActionError(`Couldn't show ${d.vendor} again: ${err.message}`);
        reloadDismissals();
      } finally {
        setBusyId(null);
      }
    },
    [markVendor, reloadDismissals]
  );

  function openHide(v, kind, reason) {
    setActionError(null);
    setHideFor({ vendor: v.vendor, criticalCount: v.tier_counts?.critical ?? null, kind, reason });
    reloadDismissals(); // fresh "Later today: until ..." times
  }

  function hidden(result) {
    setHideFor(null);
    markVendor(result.vendor, groupDismissal(result));
    setUndo({
      id: result.id,
      vendor: result.vendor,
      message: `${result.vendor} hidden from Today: ${result.ends_label}.`,
      warning: result.warning || null,
    });
    reloadDismissals();
  }

  const closeUndo = useCallback(() => setUndo(null), []);
  // W1: "Order already placed" on a critical vendor (+ its Undo).
  const placedOutside = usePlacedOutside({ setData, setUndo, setActionError, reloadDismissals });

  async function handleRun() {
    setLoading(true);
    setError(null);
    try {
      const result = await getReorderCandidates(lookbackDays, leadTimeDays);
      setData(result);
      // A new report: start the ticks over, and check the cart again (it's
      // shared - the phone may have changed it since the page opened).
      clearSelection();
      if (canCart) readCart();
      // Working the report out can end dismissals (a time that's up, or at
      // the Today settings a Close whose vendor has something new critical),
      // so the Hidden from Today list follows it.
      reloadDismissals();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const visibleVendors = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data.vendors;
    return data.vendors
      .map((v) => ({
        ...v,
        items: v.items.filter(
          (c) =>
            v.vendor.toLowerCase().includes(q) ||
            (c.name || "").toLowerCase().includes(q) ||
            (c.category || "").toLowerCase().includes(q) ||
            rowCodeMatch(c, search) !== null // W2: Toast ID / SKU / barcode
        ),
      }))
      .filter((v) => v.items.length > 0);
  }, [data, search]);

  const visibleItemCount = visibleVendors.reduce((n, v) => n + v.items.length, 0);

  // Each vendor's whole item list (the search box narrows what's shown, not
  // what "Add all Critical + Order now" covers).
  const fullItems = useMemo(() => new Map((data?.vendors || []).map((v) => [v.vendor, v.items])), [data]);

  return (
    <section className="card">
      <InfoBlock brief="Items across every supplier projected to run short within your vendor lead time. For one supplier at a time, use New order.">
        Demand over the vendor lead time - worked out only from days each item was actually in
        stock, with recent sales counting more and busy weekdays taken into account (the same
        forecast as Supplier Projection, so the two pages agree) - compared against the latest
        on-hand count. "Shortfall" is the whole number of units to order, including a small safety
        buffer; a negative on-hand count is treated as 0. Vendor
        lead time is a manual estimate below - Toast's Purchasing API isn't wired up yet, so this
        isn't pulled per-vendor automatically. Nothing here is sent to a vendor. With Purchase Order
        Cart access you can add items to a vendor's cart from here: one at a time (1 each, like the
        app), or ticked items and a vendor's Critical + Order now items with their suggested
        quantities, which you can change first. Admins can hide a Critical vendor from the Home Critical tile and the
        morning notification (Close, Skip this week or Remind me later) - for everyone, until it
        comes back. It stays listed here with a "Hidden from Today" tag.
      </InfoBlock>
      <HiddenFromTodayPanel
        state={dismissals}
        isAdmin={isAdmin}
        busyId={busyId}
        onShowAgain={showAgain}
        busyPlaced={placedOutside.busy}
        onUndoPlaced={placedOutside.undoPlaced}
      />
      <div className="date-range">
        <label>
          Lookback (days)
          <input
            type="number"
            min="1"
            max="365"
            value={lookbackDays}
            onChange={(e) => setLookbackDays(Number(e.target.value))}
          />
        </label>
        <label>
          <Term k="lead_time">Vendor lead time (days)</Term>
          <input
            type="number"
            min="1"
            max="180"
            value={leadTimeDays}
            onChange={(e) => setLeadTimeDays(Number(e.target.value))}
          />
        </label>
      </div>
      <button disabled={loading} onClick={handleRun}>
        {loading ? "Calculating…" : "Calculate"}
      </button>
      {error && <p className="error">{error}</p>}
      {actionError && <p className="error">{actionError}</p>}
      {data && (
        <>
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            {visibleItemCount} item(s) across {visibleVendors.length} vendor(s) projected to run
            short within {data.lead_time_days} day(s), based on the last {data.lookback_days}{" "}
            day(s) of sales (as of {data.as_of}). Most urgent vendors first - see Help for what each
            label means. "Check count" items show 0 on hand but sold this week, so fix the count in
            Toast first. "Ordered" vendors already have an order on the way, so they're listed after the
            urgent ones until the delivery is recorded. Click a vendor for its order schedule, last order placed, and estimated
            cost.
          </p>
          <LiveStockNote liveStock={data.live_stock} />
          {canCart && <CartHowTo rc={rc} />}
          <input
            type="text"
            className="table-search rc-search"
            placeholder="Search vendor, item, category, SKU or barcode…"
            aria-label="Search vendor, item, category, SKU, barcode or Toast item ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="reorder-vendors">
            {visibleVendors.map((v) => (
              <details key={v.vendor} className="reorder-vendor" open={visibleVendors.length <= 3}>
                <summary>
                  <span className="reorder-vendor-name">{v.vendor}</span>
                  {v.inactive ? (
                    <Term k="vendor_inactive">
                      <span
                        className="pill pill-neutral"
                        title="No delivery, invoice or placed order in the last 45 days - looks like a vendor you stopped ordering from"
                      >
                        Inactive
                      </span>
                    </Term>
                  ) : v.urgency.tier === "ordered" ? (
                    <Term k="urgency_ordered">
                      <span
                        className={`pill ${URGENCY_PILLS.ordered}`}
                        title={v.ordered_until ? `Waiting on the delivery - back to its stock status after ${formatDate(v.ordered_until)} if nothing arrives` : undefined}
                      >
                        {urgencyText(v.urgency, v.ordered_at)}
                      </span>
                    </Term>
                  ) : (
                    <Term k="vendor_urgency" label={`${v.urgency.label} (vendor)`}>
                      <span className={`pill ${URGENCY_PILLS[v.urgency.tier] || ""}`}>{v.urgency.label}</span>
                    </Term>
                  )}
                  {!v.inactive && v.urgency.tier === "ordered" && (
                    <OutsideOrderedTag
                      group={v}
                      canEdit={isAdmin && !user?.demo}
                      busy={placedOutside.busy === `order:${v.ordered_order_id}`}
                      onUndo={() => placedOutside.undoPlaced(v.ordered_order_id, v.vendor)}
                    />
                  )}
                  <span className="muted">{v.items.length} item(s)</span>
                  <span className="muted">{formatMoney(v.estimated_order_value)} est.</span>
                  {v.critical_dismissed && v.critical_dismissal && (
                    <HiddenChip
                      dismissal={v.critical_dismissal}
                      canEdit={isAdmin}
                      busy={busyId === v.critical_dismissal.id}
                      onShowAgain={() => showAgain({ ...v.critical_dismissal, vendor: v.vendor })}
                    />
                  )}
                  <TierCounts vendor={v} />
                  {isAdmin && !v.inactive && v.urgency.tier === "critical" && !v.critical_dismissed && (
                    <HideActions
                      vendor={v.vendor}
                      onPick={(kind, reason) => openHide(v, kind, reason)}
                      onPlaced={user?.demo ? undefined : () => placedOutside.place(v)}
                      placing={placedOutside.busy === vendorKey(v.vendor)}
                    />
                  )}
                </summary>
                <div className="reorder-vendor-detail">
                  <dl className="reorder-vendor-meta">
                    <div>
                      <dt>
                        <Term k="protection_days">Stock has to last</Term>
                      </dt>
                      <dd>
                        {v.protection_days != null
                          ? `${v.protection_days} day(s) until the next order arrives`
                          : "No delivery schedule set on the Vendor Calendar"}
                      </dd>
                    </div>
                    <div>
                      <dt>Last delivery</dt>
                      <dd>{formatDate(v.last_delivery_date)}</dd>
                    </div>
                    <div>
                      <dt>
                        <Term k="last_placed">Last order placed (this app)</Term>
                      </dt>
                      <dd>{formatDate(v.last_placed_at)}</dd>
                    </div>
                    <div>
                      <dt>Estimated cost of this order</dt>
                      <dd>{formatMoney(v.estimated_order_value)}</dd>
                    </div>
                  </dl>
                  {canCart && <VendorCartBar rc={rc} vendor={v.vendor} items={fullItems.get(v.vendor) || v.items} />}
                  <div className="table-wrap">
                    <table className={cartOnFor(v.vendor) ? "rc-cart-table" : undefined}>
                      <thead>
                        <tr>
                          {cartOnFor(v.vendor) && (
                            <th className="rc-cart-col-select">
                              <SelectAll rc={rc} vendor={v.vendor} items={v.items} />
                            </th>
                          )}
                          <th>Item</th>
                          <th>Status</th>
                          <th>Category</th>
                          <th className="num">
                            <Term k="avg_demand">Avg/day</Term>
                          </th>
                          <th className="num">
                            <Term k="projected_demand">Forecast (lead time)</Term>
                          </th>
                          <th className="num">On hand</th>
                          <th className="num">
                            <Term k="need_to_order">Shortfall</Term>
                          </th>
                          <th className="num">Est. cost</th>
                        </tr>
                      </thead>
                      <tbody>
                        {v.items.map((c) => {
                          const weeklyKey = `${v.vendor}|${c.item_id}`;
                          const weeklyShown = weeklyOpen === weeklyKey;
                          return (
                          <Fragment key={c.item_id}>
                          <tr
                            className={cartOnFor(v.vendor) && rc.isTicked(v.vendor, c) ? "rc-cart-row-selected" : undefined}
                          >
                            {cartOnFor(v.vendor) && (
                              <td className="rc-cart-col-select">
                                <RowSelect rc={rc} vendor={v.vendor} item={c} />
                              </td>
                            )}
                            <td className="cell-name">
                              {c.name || c.item_id}
                              {cartOnFor(v.vendor) ? (
                                // Type pill on the left, Add 1 to cart / In cart on the right.
                                <div className="rc-cart-item-line">
                                  <ClassPill classification={c.forecast?.classification} />
                                  <RowCartAction rc={rc} vendor={v.vendor} item={c} />
                                </div>
                              ) : (
                                c.forecast?.classification && (
                                  <>
                                    {" "}
                                    <ClassPill classification={c.forecast.classification} />
                                  </>
                                )
                              )}
                            </td>
                            <td>
                              <Term k={URGENCY_TERMS[c.urgency.tier]}>
                                <span className={`pill ${URGENCY_PILLS[c.urgency.tier] || ""}`}>
                                  {urgencyText(c.urgency, c.ordered_at)}
                                </span>
                              </Term>
                            </td>
                            <td>{c.category || "-"}</td>
                            <td className="num">
                              <button
                                type="button"
                                className="sp-value-link"
                                onClick={() => setWeeklyOpen(weeklyShown ? null : weeklyKey)}
                                aria-expanded={weeklyShown}
                                title="Show weekly sales and stock"
                              >
                                {c.avg_daily_demand}
                                <span className="sp-caret">{weeklyShown ? "▲" : "▼"}</span>
                              </button>
                            </td>
                            <td className="num">{c.forecast_over_lead_time}</td>
                            <td className={`num ${c.on_hand_qty < 0 ? "flag" : ""}`}>{c.on_hand_qty}</td>
                            <td className="num">
                              <span
                                className={`qty-chip ${c.on_hand_qty != null && c.on_hand_qty <= 0 ? "qty-chip-urgent" : ""}`}
                              >
                                {c.shortfall}
                              </span>
                            </td>
                            <td className="num">{formatMoney(c.estimated_order_value)}</td>
                          </tr>
                          {weeklyShown && (
                            <tr className="sp-weekly-row">
                              <td colSpan={cartOnFor(v.vendor) ? 9 : 8}>
                                <ItemWeeklySales
                                  itemId={c.item_id}
                                  start={isoMinusDays(data.as_of, data.lookback_days)}
                                  end={data.as_of}
                                  emptyText="No sales or stock counts for this item in the report's window."
                                />
                              </td>
                            </tr>
                          )}
                          </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </details>
            ))}
            {visibleVendors.length === 0 && (
              <p className="muted">No items match "{search}".</p>
            )}
          </div>
        </>
      )}
      {hideFor && (
        <HideFromTodayDrawer
          vendor={hideFor.vendor}
          criticalCount={hideFor.criticalCount}
          initialKind={hideFor.kind}
          initialReason={hideFor.reason}
          options={dismissals.data}
          onCancel={() => setHideFor(null)}
          onDone={hidden}
        />
      )}
      {canCart && <CartReviewPanel rc={rc} />}
      {undo && (
        <UndoBar
          key={undo.id ?? (undo.placedOrderId != null ? `placed-${undo.placedOrderId}` : `note-${undo.vendor}`)}
          message={undo.message}
          warning={undo.warning}
          onUndo={
            undo.placedOrderId != null
              ? () => placedOutside.undoPlaced(undo.placedOrderId, undo.vendor)
              : undo.id != null
                ? () => showAgain({ id: undo.id, vendor: undo.vendor })
                : undefined
          }
          onClose={closeUndo}
        />
      )}
    </section>
  );
}
