import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  getAllTimeOpenOrders,
  getCart,
  getChecklistSchemas,
  getChecklistSubmissions,
  getDeliveryHistory,
  getInvoiceSubmissions,
  getItemsSold,
  getReorderCandidates,
  getVendorCalendar,
} from "../api";
import { useAuth } from "../AuthContext.jsx";
import { inStoreZone, storeDateStr, storeTodayStr, storeDateOffsetStr } from "../format.js";
import { INVOICE_STATUS_TERMS, TermInfo } from "../glossary.jsx";
import { currentPosName } from "../store/gating.js";
import { useStoreAllows } from "../store/StoreContext.jsx";
import { closingTileGroups } from "./closingChecklists.js";
import { tileErrorText } from "./statTileModel.js";
import Icon from "../ui/icons.jsx";
import { routeForPath } from "../featureRoutes.js";

// Overview dashboard widgets. Every tile/panel is shown only when the user
// has that feature (its endpoint would 403 otherwise), loads on its own, and
// fails quietly to "-" so one slow or broken source never blanks the page.

// Store-local calendar date of a moment, "YYYY-MM-DD" (the store's own time
// zone, format.js) - not the viewing device's own date (someone checking in
// from out of town).
function localIso(date) {
  return storeDateStr(date);
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
      .catch((e) => !cancelled && setState({ loading: false, data: null, error: true, message: e?.message || "" }));
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

const TONE_STATUS = {
  good: { icon: "circle-check", word: "Good" },
  warn: { icon: "triangle-alert", word: "Needs attention" },
  bad: { icon: "octagon-alert", word: "Problem" },
};

function StatTile({ to, label, state, value, sub, tone, small = false, maskable = false, termKey }) {
  // The tile's top edge is the area it opens (identity); its status is an
  // icon + word beside the label (never colour alone).
  const area = routeForPath(to)?.area;
  const status = !state.loading && !state.error ? TONE_STATUS[tone] : null;
  const [hidden, setHidden] = useState(maskable);
  let shown = value;
  let subShown = sub;
  if (state.loading) {
    shown = <span className="stat-tile-skeleton" />;
    subShown = " ";
  } else if (state.error) {
    shown = "-";
    subShown = tileErrorText(state.message);
  } else if (maskable && hidden) {
    shown = "••••••";
  }
  return (
    <Link to={to} className={`stat-tile stat-tile-${state.loading || state.error ? "neutral" : tone || "neutral"}`} data-area={area || undefined}>
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
      {status && (
        <span className={`stat-tile-status stat-tile-status-${tone}${maskable ? " with-eye" : ""}`} title={status.word}>
          <Icon name={status.icon} size={14} />
          <span className="sr-only">{status.word}</span>
        </span>
      )}
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
  // "Today's sales" is the POS's live today (store/gating.js items_sold_today);
  // a store whose POS has none gets only Yesterday's.
  const liveToday = useStoreAllows("items_sold_today");
  const canReorder = hasFeature("reorder_candidates");
  const canCart = hasFeature("purchase_order_cart") || hasFeature("supplier_projection");
  const canOpen = hasFeature("open_orders");
  const canInvoices = hasFeature("invoice_submission");

  const sales = useLoad(canSales, () => getItemsSold(yesterdayIso, yesterdayIso));
  const reorder = useLoad(canReorder, () => getReorderCandidates(30, 7));
  const cart = useLoad(canCart, () => getCart());
  const open = useLoad(canOpen, () => getAllTimeOpenOrders());
  const invoices = useLoad(canInvoices, () => sharedLoad("invoices", getInvoiceSubmissions));
  const todaySales = useLoad(canSales && liveToday, () => getItemsSold(todayIso, todayIso));

  const tiles = [];

  if (canSales && liveToday) {
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
  }
  if (canSales) {
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
        sub={open.data ? (count ? `not yet closed in ${currentPosName()}` : "all closed") : null}
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
                ? `${looksClosed} look${looksClosed === 1 ? "s" : ""} closed in ${currentPosName()}`
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
  return new Date(iso).toLocaleDateString(undefined, inStoreZone({ month: "short", day: "numeric" }));
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

// Several closing checklists in one tile (e.g. the kitchen's, so a third tile
// doesn't crowd the one-row activity strip). Issues if any latest has
// issues; amber if any is stale. `checklists`: [{ name, last }].
function combinedClosingTile(key, label, checklists, to) {
  const latest = checklists.map((c) => c.last).filter(Boolean);
  const anyIssues = latest.some((s) => !s.all_clear);
  // Only a stale submission turns it amber - a missing one just reads "-"
  // (a non-admin sees only their own, and not everyone fills in every one).
  const stale = latest.some((s) => hoursSince(s.created_at) > 36);
  // Compact (the tile is narrow): "Line 9/28 · Prep 9/29", "-" when none.
  const md = (iso) => {
    const [, m, d] = localIso(new Date(iso)).split("-");
    return `${Number(m)}/${Number(d)}`;
  };
  const part = (c) => `${c.name} ${c.last ? md(c.last.created_at) : "-"}`;
  return (
    <StatTile
      key={key}
      to={to}
      label={label}
      termKey={latest.length ? (anyIssues ? "checklist_issues" : "checklist_all_clear") : undefined}
      state={{ loading: false, error: false, data: true }}
      value={latest.length ? (anyIssues ? "Issues" : "All clear") : "None yet"}
      sub={latest.length ? checklists.map(part).join(" · ") : null}
      tone={!latest.length ? "neutral" : anyIssues ? "bad" : stale ? "warn" : "good"}
    />
  );
}

// The store's own closing checklists (closingChecklists.js), each with its
// latest submission. One failed lookup only blanks its own tile.
async function loadClosingGroups() {
  const res = await getChecklistSchemas();
  const groups = closingTileGroups(res?.schemas);
  return Promise.all(
    groups.map(async (g) => ({
      ...g,
      checklists: await Promise.all(
        g.checklists.map(async (c) => {
          try {
            const r = await getChecklistSubmissions({ checklistType: c.type, limit: 1 });
            return { ...c, last: r?.items?.[0] || null, failed: false };
          } catch {
            return { ...c, last: null, failed: true };
          }
        })
      ),
    }))
  );
}

function closingTile(group, to) {
  if (group.checklists.some((c) => c.failed)) {
    return <StatTile key={group.key} to={to} label={group.label} state={{ loading: false, error: true, data: null }} />;
  }
  if (group.checklists.length === 1) {
    const { last } = group.checklists[0];
    return checklistTile(group.key, group.label, { loading: false, error: false, data: { items: last ? [last] : [] } }, to);
  }
  return combinedClosingTile(group.key, group.label, group.checklists, to);
}

const INVOICE_TONES = { completed: "good", in_progress: "info", pending: "neutral", failed: "bad" };

// Second row on Overview (under the Weekly Digest): the most recent activity.
export function ActivityTiles() {
  const { hasFeature } = useAuth();
  const canChecklists = hasFeature("daily_checklists");
  const canInvoices = hasFeature("invoice_submission");
  const canDeliveries = hasFeature("delivery_review");

  const closing = useLoad(canChecklists, loadClosingGroups);
  const invoices = useLoad(canInvoices, () => sharedLoad("invoices", getInvoiceSubmissions));
  const deliveries = useLoad(canDeliveries, () => getDeliveryHistory(14));

  const tiles = [];
  if (canChecklists) {
    if (!closing.data) {
      tiles.push(<StatTile key="closing" to="/daily-checklists" label="Closing checklists" state={closing} />);
    } else {
      for (const group of closing.data) tiles.push(closingTile(group, "/daily-checklists"));
    }
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
