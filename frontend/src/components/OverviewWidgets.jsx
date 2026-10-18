import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  getAllTimeOpenOrders,
  getCart,
  getChecklistSubmissions,
  getDeliveryHistory,
  getInvoiceSubmissions,
  getItemsSold,
  getReorderCandidates,
  getVendorCalendar,
} from "../api";
import { useAuth } from "../AuthContext.jsx";
import { storeTodayStr, storeDateOffsetStr } from "../format.js";
import { INVOICE_STATUS_TERMS, TermInfo } from "../glossary.jsx";

// Overview dashboard widgets. Every tile/panel is shown only when the user
// has that feature (its endpoint would 403 otherwise), loads on its own, and
// fails quietly to "-" so one slow or broken source never blanks the page.

// Store-local (America/New_York) calendar date of a moment, "YYYY-MM-DD" -
// not the viewing device's own date (someone checking in from out of town).
const storeDayFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
function localIso(date) {
  return storeDayFormatter.format(date);
}

// Both tile rows read the invoice list - share one request per page load.
const shared = new Map();
function sharedLoad(key, loader) {
  const hit = shared.get(key);
  if (hit && Date.now() - hit.at < 10000) return hit.promise;
  const promise = loader();
  shared.set(key, { at: Date.now(), promise });
  promise.catch(() => shared.delete(key));
  return promise;
}

function useLoad(enabled, loader) {
  const [state, setState] = useState({ loading: enabled, data: null, error: false });
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    loader()
      .then((data) => !cancelled && setState({ loading: false, data, error: false }))
      .catch(() => !cancelled && setState({ loading: false, data: null, error: true }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);
  return state;
}

const money = (n) =>
  `$${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

function EyeIcon({ crossedOut }) {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
      {crossedOut && <line x1="3" y1="3" x2="21" y2="21" />}
    </svg>
  );
}

function StatTile({ to, label, state, value, sub, tone, small = false, maskable = false, termKey }) {
  const [hidden, setHidden] = useState(maskable);
  let shown = value;
  let subShown = sub;
  if (state.loading) {
    shown = <span className="stat-tile-skeleton" />;
    subShown = " ";
  } else if (state.error) {
    shown = "-";
    subShown = "Couldn't load";
  } else if (maskable && hidden) {
    shown = "••••••";
  }
  return (
    <Link to={to} className={`stat-tile stat-tile-${state.loading || state.error ? "neutral" : tone || "neutral"}`}>
      {maskable && !state.loading && (
        <button
          type="button"
          className="stat-tile-eye"
          aria-label={hidden ? "Show amount" : "Hide amount"}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setHidden((h) => !h);
          }}
        >
          <EyeIcon crossedOut={hidden} />
        </button>
      )}
      <span className="stat-tile-label">
        {label}
        {termKey && <TermInfo k={termKey} label={label} />}
      </span>
      <span className={`stat-tile-value${small ? " stat-tile-value-sm" : ""}`} title={small && typeof shown === "string" ? shown : undefined}>
        {shown}
      </span>
      <span className="stat-tile-sub">{subShown}</span>
    </Link>
  );
}

export function StatTiles() {
  const { hasFeature, canSeeSales } = useAuth();
  const todayIso = storeTodayStr();
  const yesterdayIso = storeDateOffsetStr(-1);

  // No sales tiles (and no sales requests) for a "Hide sales numbers" user.
  const canSales = hasFeature("items_sold") && canSeeSales;
  const canReorder = hasFeature("reorder_candidates");
  const canCart = hasFeature("purchase_order_cart") || hasFeature("supplier_projection");
  const canOpen = hasFeature("open_orders");
  const canInvoices = hasFeature("invoice_submission");

  const sales = useLoad(canSales, () => getItemsSold(yesterdayIso, yesterdayIso));
  const reorder = useLoad(canReorder, () => getReorderCandidates(30, 7));
  const cart = useLoad(canCart, () => getCart());
  const open = useLoad(canOpen, () => getAllTimeOpenOrders());
  const invoices = useLoad(canInvoices, () => sharedLoad("invoices", getInvoiceSubmissions));
  const todaySales = useLoad(canSales, () => getItemsSold(todayIso, todayIso));

  const tiles = [];

  if (canSales) {
    const t = todaySales.data;
    tiles.push(
      <StatTile
        key="today"
        to="/items-sold"
        label="Today's sales"
        state={todaySales}
        value={t ? money(t.total_revenue) : null}
        sub={t ? `${Math.round(t.total_quantity).toLocaleString()} items · live` : null}
        tone={t && t.total_revenue > 0 ? "good" : "neutral"}
        maskable
      />
    );
    const d = sales.data;
    tiles.push(
      <StatTile
        key="sales"
        to="/items-sold"
        label="Yesterday's sales"
        state={sales}
        value={d ? money(d.total_revenue) : null}
        sub={d ? `${Math.round(d.total_quantity).toLocaleString()} items sold` : null}
        tone="neutral"
        maskable
      />
    );
  }

  if (canReorder) {
    // Items of a vendor with an order on the way ("ordered") are waiting on
    // that delivery, not in need of reordering. Older backends send no
    // vendors - fall back to the flat list.
    const list = reorder.data
      ? reorder.data.vendors
        ? reorder.data.vendors.flatMap((v) => v.items).filter((c) => c.urgency?.tier !== "ordered")
        : reorder.data.candidates || []
      : [];
    const out = list.filter((c) => c.on_hand_qty != null && c.on_hand_qty <= 0).length;
    tiles.push(
      <StatTile
        key="reorder"
        to="/reorder"
        label="Need reordering"
        termKey="need_to_order"
        state={reorder}
        value={reorder.data ? list.length.toLocaleString() : null}
        sub={reorder.data ? (out ? `${out} at 0 or less` : "within 7 days") : null}
        tone={out ? "bad" : list.length ? "warn" : "good"}
      />
    );
  }

  if (canCart) {
    const d = cart.data;
    tiles.push(
      <StatTile
        key="cart"
        to="/purchase-order"
        label="PO cart"
        state={cart}
        value={d ? d.total_items.toLocaleString() : null}
        sub={
          d
            ? d.suppliers.length
              ? `${d.suppliers.length} supplier${d.suppliers.length === 1 ? "" : "s"}`
              : "cart is empty"
            : null
        }
        tone={d && d.total_items ? "info" : "neutral"}
      />
    );
  }

  if (canOpen) {
    const count = open.data ? open.data.count ?? (open.data.orders || []).length : null;
    tiles.push(
      <StatTile
        key="open"
        to="/open-orders"
        label="Open checks"
        state={open}
        value={count != null ? count.toLocaleString() : null}
        sub={open.data ? (count ? "not yet closed in Toast" : "all closed") : null}
        tone={count ? "warn" : "good"}
      />
    );
  }

  if (canInvoices) {
    const items = invoices.data?.items || [];
    const todays = items.filter((i) => i.created_at && localIso(new Date(i.created_at)) === todayIso);
    const failed = items.filter((i) => i.processing_status === "failed").length;
    // Additive field (automation batch item 5); older backends send none.
    const looksClosed = invoices.data?.close_suggested_count || 0;
    tiles.push(
      <StatTile
        key="invoices"
        to="/invoices"
        label="Invoices today"
        termKey={!failed && looksClosed ? "invoice_close_suggested" : "invoice_failed"}
        state={invoices}
        value={invoices.data ? todays.length.toLocaleString() : null}
        sub={
          invoices.data
            ? failed
              ? `${failed} failed to send`
              : looksClosed
                ? `${looksClosed} look${looksClosed === 1 ? "s" : ""} closed in Toast`
                : "none failed to send"
            : null
        }
        tone={failed ? "bad" : looksClosed ? "info" : "neutral"}
      />
    );
  }

  if (tiles.length === 0) return null;
  // Fewer than 6 tiles (sales hidden, or fewer grants): stretch them across
  // the row instead of leaving empty slots on the right.
  return <div className={`stat-tiles${tiles.length < 6 ? " stat-tiles-fit" : ""}`}>{tiles}</div>;
}

export function TodaySchedule() {
  const { hasFeature } = useAuth();
  const navigate = useNavigate();
  const enabled = hasFeature("vendor_calendar");
  const todayIso = storeTodayStr();
  const tomorrowIso = storeDateOffsetStr(1);
  const cal = useLoad(enabled, () => getVendorCalendar(todayIso, tomorrowIso));

  if (!enabled) return null;
  const events = cal.data?.events || [];
  const days = [
    { iso: todayIso, label: "Today" },
    { iso: tomorrowIso, label: "Tomorrow" },
  ];

  return (
    <section className="card">
      <div className="card-header">
        <h2 style={{ margin: 0 }}>
          Vendor schedule <TermInfo k="cal_items_needed" label="items to order" />
        </h2>
        <Link to="/vendor-calendar" className="card-link">
          Calendar →
        </Link>
      </div>
      {cal.loading && <p className="muted">Loading…</p>}
      {cal.error && <p className="muted">Couldn't load the vendor calendar.</p>}
      {cal.data && events.length === 0 && (
        <p className="muted" style={{ marginBottom: 0 }}>
          No orders due or deliveries expected today or tomorrow.
        </p>
      )}
      {cal.data &&
        events.length > 0 &&
        days.map((day) => {
          const dayEvents = events.filter((e) => e.date === day.iso);
          if (dayEvents.length === 0) return null;
          return (
            <div key={day.iso} className="schedule-day">
              <div className="schedule-day-label">{day.label}</div>
              {dayEvents.map((e) =>
                e.type === "order_due" ? (
                  <button
                    key={`${e.type}-${e.vendor}`}
                    type="button"
                    className={`schedule-row schedule-row-button ${e.urgent ? "schedule-row-bad" : ""}`}
                    onClick={() =>
                      navigate("/supplier-projection", {
                        state: { presetSupplier: e.vendor, presetHorizonDays: e.horizon_days },
                      })
                    }
                  >
                    <span className={`schedule-dot ${e.urgent ? "dot-bad" : "dot-neutral"}`} />
                    <span className="schedule-text">
                      {e.skipped ? "Skipped" : e.ordered ? "Ordered" : "Order due"}: <strong>{e.vendor}</strong>
                    </span>
                    <span className="schedule-meta">
                      {e.skipped
                        ? "order skipped"
                        : e.ordered
                          ? "order placed"
                          : e.urgent
                          ? `${e.items_needing_order} items to order →`
                          : e.below_minimum && e.items_needing_order > 0
                          ? `${e.items_needing_order} low - not an order yet →`
                          : "nothing needed →"}
                    </span>
                  </button>
                ) : (
                  <div key={`${e.type}-${e.vendor}`} className="schedule-row">
                    <span
                      className={`schedule-dot ${
                        e.status === "delivered"
                          ? "dot-good"
                          : e.status === "missed"
                            ? "dot-bad"
                            : e.status === "skipped"
                              ? "dot-neutral"
                              : "dot-info"
                      }`}
                    />
                    <span className="schedule-text">
                      Delivery: <strong>{e.vendor}</strong>
                    </span>
                    <span className="schedule-meta">
                      {{ upcoming: "expected", delivered: "confirmed", missed: "not logged", skipped: "skipped" }[e.status]}
                    </span>
                  </div>
                )
              )}
            </div>
          );
        })}
    </section>
  );
}

function shortDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "America/New_York" });
}

function hoursSince(iso) {
  return (Date.now() - new Date(iso).getTime()) / 3600000;
}

// Checklist tile: green all clear, red issues, amber when the last one is
// more than ~a day and a half old (last night's wasn't submitted).
function checklistTile(key, label, state, to) {
  const last = state.data?.items?.[0];
  const stale = last && hoursSince(last.created_at) > 36;
  return (
    <StatTile
      key={key}
      to={to}
      label={label}
      termKey={last ? (last.all_clear ? "checklist_all_clear" : "checklist_issues") : undefined}
      state={state}
      value={state.data ? (last ? (last.all_clear ? "All clear" : "Issues") : "None yet") : null}
      sub={
        last
          ? `${stale ? "last " : ""}${shortDate(last.created_at)} · ${last.submitted_by_name}${
              last.via_public_link ? " (via public link)" : ""
            }`
          : null
      }
      tone={!last ? "neutral" : !last.all_clear ? "bad" : stale ? "warn" : "good"}
    />
  );
}

// Kitchen closing tile: the chef's and the Food Service Associate's closing
// checklists share one tile (a third tile would crowd the one-row activity
// strip). Issues if either latest has issues; amber if either is stale.
function kitchenTile(chefState, fsaState, to) {
  const loading = chefState.loading || fsaState.loading;
  const error = chefState.error || fsaState.error;
  const state = { loading, error, data: chefState.data && fsaState.data ? true : null };
  const chef = chefState.data?.items?.[0];
  const fsa = fsaState.data?.items?.[0];
  const latest = [chef, fsa].filter(Boolean);
  const anyIssues = latest.some((s) => !s.all_clear);
  // Only a stale submission turns it amber - a missing one just reads "none"
  // (a non-admin sees only their own, and a chef never submits the FSA one).
  const stale = latest.some((s) => hoursSince(s.created_at) > 36);
  // Compact (the tile is narrow): "Chef 9/28 · FSA 9/29", "-" when none.
  const md = (iso) => {
    const [, m, d] = localIso(new Date(iso)).split("-");
    return `${Number(m)}/${Number(d)}`;
  };
  const part = (name, s) => `${name} ${s ? md(s.created_at) : "-"}`;
  return (
    <StatTile
      key="kitchen"
      to={to}
      label="Kitchen closing"
      termKey={latest.length ? (anyIssues ? "checklist_issues" : "checklist_all_clear") : undefined}
      state={state}
      value={state.data ? (latest.length ? (anyIssues ? "Issues" : "All clear") : "None yet") : null}
      sub={latest.length ? `${part("Chef", chef)} · ${part("FSA", fsa)}` : null}
      tone={!latest.length ? "neutral" : anyIssues ? "bad" : stale ? "warn" : "good"}
    />
  );
}

const INVOICE_TONES = { completed: "good", in_progress: "info", pending: "neutral", failed: "bad" };

// Second row on Overview (under the Weekly Digest): the most recent activity.
export function ActivityTiles() {
  const { hasFeature } = useAuth();
  const canChecklists = hasFeature("daily_checklists");
  const canInvoices = hasFeature("invoice_submission");
  const canDeliveries = hasFeature("delivery_review");

  const store = useLoad(canChecklists, () =>
    getChecklistSubmissions({ checklistType: "spice_town_closing", limit: 1 })
  );
  const kitchen = useLoad(canChecklists, () =>
    getChecklistSubmissions({ checklistType: "street_kitchen_closing", limit: 1 })
  );
  const kitchenFsa = useLoad(canChecklists, () =>
    getChecklistSubmissions({ checklistType: "street_kitchen_fsa", limit: 1 })
  );
  const invoices = useLoad(canInvoices, () => sharedLoad("invoices", getInvoiceSubmissions));
  const deliveries = useLoad(canDeliveries, () => getDeliveryHistory(14));

  const tiles = [];
  if (canChecklists) {
    tiles.push(checklistTile("store", "Store closing", store, "/daily-checklists"));
    tiles.push(kitchenTile(kitchen, kitchenFsa, "/daily-checklists"));
  }
  if (canInvoices) {
    const last = invoices.data?.items?.[0];
    tiles.push(
      <StatTile
        key="invoice"
        to="/invoices"
        label="Last invoice"
        termKey={last ? INVOICE_STATUS_TERMS[last.processing_status] : undefined}
        small
        state={invoices}
        value={invoices.data ? (last ? last.vendor : "None yet") : null}
        sub={
          last
            ? `${last.total_amount != null ? money(last.total_amount) + " · " : ""}${shortDate(last.created_at)}`
            : null
        }
        tone={last ? INVOICE_TONES[last.processing_status] || "neutral" : "neutral"}
      />
    );
  }
  if (canDeliveries) {
    const last = deliveries.data?.deliveries?.[0];
    tiles.push(
      <StatTile
        key="delivery"
        to="/delivery-review"
        label="Last delivery"
        small
        state={deliveries}
        value={deliveries.data ? (last ? last.supplier : "None in 14 days") : null}
        sub={last ? `${last.item_count} items · ${shortDate(`${last.received_date}T12:00:00`)}` : null}
        tone={last ? "good" : "neutral"}
      />
    );
  }
  if (tiles.length === 0) return null;
  return <div className="stat-tiles stat-tiles-activity">{tiles}</div>;
}
