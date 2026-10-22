import { useEffect, useMemo, useState, useCallback } from "react";
import { storeTodayStr, storeDateOffsetStr } from "../format.js";
import { useLocation, useNavigate } from "react-router-dom";
import { getSuppliers, getSupplierProjection, getItemWeeklySales, getSimilarItems, addToCart, getCart, updateCartItem, deleteCartItem } from "../api";
import { UndoBar } from "./CriticalDismissals.jsx";
import InfoBlock from "./InfoBlock.jsx";
import { CLASS_TERMS, FLAG_TERMS, SIMILAR_REASON_TERMS, Term, TermInfo } from "../glossary.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { rowCodeMatch } from "../productMatch.js";
import { cartLinkFor } from "./cartAdd/cartVendorPanels.js";
import { LiveStockNote } from "./DataSourceNote.jsx";
import { useAuth } from "../AuthContext.jsx";
import { WeeklySalesTable } from "./weeklySales/WeeklySalesTable.jsx";
import {
  addMessage,
  buildAddItems,
  caseLabel,
  otherCaseSizes,
  caseSizeOf,
  parseQty,
  qtyText,
  qtyWords,
  quickAddLine,
  stepQty,
  suggestionOf,
  updateFor,
} from "./projectionQty/projectionQtyModel.js";
import { readOrderWindow, writeOrderWindow, windowWords } from "./projectionQty/orderWindow.js";
import { browserStorage } from "./cartAdd/cartVendorPanels.js";

const DURATIONS = [
  { value: "7", label: "1 week" },
  { value: "14", label: "2 weeks" },
  { value: "21", label: "3 weeks" },
  { value: "30", label: "1 month" },
  { value: "60", label: "2 months" },
  { value: "90", label: "3 months" },
];

const LOOKBACK_PRESETS = [
  { value: "7", label: "1 week" },
  { value: "14", label: "2 weeks" },
  { value: "30", label: "1 month" },
  { value: "90", label: "3 months" },
];

// Both dropdowns share the same shape: fixed presets plus a "Custom" option
// that reveals a native date input (calendar view) instead of a preset.
const LOOKBACK_OPTIONS = [...LOOKBACK_PRESETS, { value: "custom", label: "Custom range…" }];
const PROJECTION_OPTIONS = [...DURATIONS, { value: "custom", label: "Custom…" }];

function todayStr() {
  return storeTodayStr();
}

function daysAgoStr(days) {
  return storeDateOffsetStr(-days);
}

function daysFromNowStr(days) {
  return storeDateOffsetStr(days);
}

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

const FLAG_LABELS = {
  insufficient_history: "Not enough history",
  prolonged_stockout: "Long stockout",
  demand_spike: "Sales spike capped",
  unusual_trend: "Unusual trend",
  negative_inventory: "Negative on-hand",
  missing_inventory_history: "No inventory counts",
  intermittent: "Sells occasionally",
  dead_inventory: "No sales in 60 days",
  differs_from_history: "Very different from simple average",
};

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// Forecast-class filter chips. Classes combine with OR (Regular + Slow shows
// both); "Review" and "Needs order" narrow further (AND), and all of it
// stacks with the search box. "unclassified" covers items the server sent no
// forecast class for (older servers send no forecast object at all - the
// class chips are hidden entirely then).
const CLASS_FILTERS = [
  { key: "regular", label: "Regular" },
  { key: "intermittent", label: "Intermittent" },
  { key: "slow", label: "Slow" },
  { key: "dead", label: "Dead" },
  { key: "unclassified", label: "Unclassified" },
];
const EMPTY_FILTERS = { classes: [], review: false, needsOrder: false };
const FILTER_STORAGE_KEY = "supplierProjection.filters";

// Remembered for the browser session only; storage can be blocked (private
// mode), so every access is guarded and falls back to "All".
function loadFilters() {
  try {
    const raw = window.sessionStorage.getItem(FILTER_STORAGE_KEY);
    if (!raw) return EMPTY_FILTERS;
    const f = JSON.parse(raw);
    const known = new Set(CLASS_FILTERS.map((c) => c.key));
    return {
      classes: Array.isArray(f?.classes) ? f.classes.filter((c) => known.has(c)) : [],
      review: f?.review === true,
      needsOrder: f?.needsOrder === true,
    };
  } catch {
    return EMPTY_FILTERS;
  }
}

function saveFilters(f) {
  try {
    window.sessionStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(f));
  } catch {
    // ignore - the filter still works, it just isn't remembered
  }
}

function itemClassKey(item) {
  return item.forecast?.classification || "unclassified";
}

// Same figure as the Need column: need-to-order, or projected demand when
// on-hand isn't tracked (the "*" rows).
function itemNeed(item, horizon) {
  const p = item.projections?.[horizon];
  if (!p) return null;
  return p.need_to_order ?? p.projected_demand ?? null;
}

// `skip` leaves one dimension out - used for the per-chip counts, so each
// chip shows how many rows you'd see with it switched on.
function passesFilters(item, f, horizon, skip) {
  if (skip !== "classes" && f.classes.length > 0 && !f.classes.includes(itemClassKey(item))) return false;
  if (skip !== "review" && f.review && !item.forecast?.needs_review) return false;
  if (skip !== "needsOrder" && f.needsOrder && !((itemNeed(item, horizon) ?? 0) > 0)) return false;
  return true;
}

// Mirrors useTableControls' search (name, category, item class).
function matchesSearch(item, search) {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return (
    [item.name, item.category, item.forecast?.classification].some((v) =>
      String(v ?? "").toLowerCase().includes(needle)
    ) || rowCodeMatch(item, search) !== null // W2: Toast ID / SKU / barcode
  );
}

function durationLabel(value) {
  return DURATIONS.find((d) => d.value === String(value))?.label || `${value} days`;
}

export default function SupplierProjection() {
  const navigate = useNavigate();
  const location = useLocation();
  // The cart page needs purchase_order_cart (cart writes also take
  // supplier_projection, so adding works without it): only offer the link to
  // the cart to someone who can open it - else it bounces them elsewhere.
  const { hasFeature } = useAuth();
  const canOpenCart = hasFeature("purchase_order_cart");
  const [suppliers, setSuppliers] = useState([]);
  const [supplier, setSupplier] = useState("");

  // Default to a 3-month lookback - the store's real ordering rhythm makes a
  // wider sales history a more useful default starting point than 1 month.
  // Oct 22: starts at the order window last projected (shared with the
  // Purchase Order Cart's suggestions - projectionQty/orderWindow.js).
  const [savedWindow] = useState(() => readOrderWindow(browserStorage()));
  const [lookbackPreset, setLookbackPreset] = useState(() =>
    LOOKBACK_PRESETS.some((p) => p.value === String(savedWindow.lookbackDays)) ? String(savedWindow.lookbackDays) : "90"
  );
  const [startDate, setStartDate] = useState(() =>
    daysAgoStr(LOOKBACK_PRESETS.some((p) => p.value === String(savedWindow.lookbackDays)) ? savedWindow.lookbackDays : 90)
  );
  const [endDate, setEndDate] = useState(todayStr());

  const [durationPreset, setDurationPreset] = useState(() =>
    DURATIONS.some((d) => d.value === String(savedWindow.horizonDays)) ? String(savedWindow.horizonDays) : "custom"
  );
  const [customProjectionDate, setCustomProjectionDate] = useState(daysFromNowStr(savedWindow.horizonDays));

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [selected, setSelected] = useState(() => new Set());
  // item_ids already sitting in this supplier's cart section - pre-ticked on
  // a fresh projection so it's obvious at a glance what's already queued up,
  // and excluded from what actually gets (re-)submitted on "Add to cart" so
  // re-running a projection can't silently stomp a qty someone already
  // edited on the Purchase Order Cart page.
  const [cartItemIds, setCartItemIds] = useState(() => new Set());
  // item_id -> {id, qty, case_of} of this supplier's cart lines (X1): an
  // in-cart item's qty box starts at its cart qty ("In cart: N") and is only
  // changed by its own Update button.
  const [cartLines, setCartLines] = useState({});
  // item_id -> the text typed in that item's Order qty box (X1). Untouched
  // boxes show the server's suggested qty (or the cart qty when in cart).
  const [qtyDrafts, setQtyDrafts] = useState({});
  const [updatingId, setUpdatingId] = useState(null);
  const [expandedItemId, setExpandedItemId] = useState(null);
  const [weeklyCache, setWeeklyCache] = useState({});
  const [weeklyLoading, setWeeklyLoading] = useState(null);
  // Projection item whose "Similar" drawer is open (null = closed).
  const [similarFor, setSimilarFor] = useState(null);
  const closeSimilar = useCallback(() => setSimilarFor(null), []);

  useEffect(() => {
    getSuppliers()
      .then((res) => setSuppliers(res.suppliers))
      .catch((err) => setError(err.message));
  }, []);

  const refreshCartMembership = useCallback(async (supplierName) => {
    if (!supplierName) return new Set();
    try {
      const cart = await getCart();
      const supplierRow = cart.suppliers.find((s) => s.supplier === supplierName);
      const ids = new Set((supplierRow?.items || []).map((i) => i.item_id).filter(Boolean));
      const lines = {};
      (supplierRow?.items || []).forEach((i) => {
        if (i.item_id) lines[i.item_id] = { id: i.id, qty: i.qty, case_of: i.case_of, can_undo_add: i.can_undo_add };
      });
      setCartItemIds(ids);
      setCartLines(lines);
      return ids;
    } catch {
      return new Set();
    }
  }, []);

  // Someone can add/remove cart items elsewhere (the cart page, another
  // projection run) - keep the "already in cart" ticks honest if that
  // happens while this view is open.
  useEffect(() => {
    // The projection's own vendor, not the dropdown (which may have been
    // changed since without re-running).
    const shown = data?.supplier;
    function handleCartUpdated() {
      if (shown) refreshCartMembership(shown);
    }
    window.addEventListener("cart:updated", handleCartUpdated);
    return () => window.removeEventListener("cart:updated", handleCartUpdated);
  }, [data?.supplier, refreshCartMembership]);

  // Arriving from the Vendor Order Calendar's "Order due" chip: pre-fill and
  // auto-run with that vendor and its auto-computed horizon (days until its
  // next delivery), instead of making someone re-pick both by hand. Runs
  // once on mount only - navigating here again from the calendar remounts
  // this page anyway, since it's a different route.
  useEffect(() => {
    const preset = location.state;
    if (!preset?.presetSupplier) return;
    const horizonDays = preset.presetHorizonDays || 30;
    const start = daysAgoStr(30);
    const end = todayStr();
    const target = daysFromNowStr(horizonDays);
    setSupplier(preset.presetSupplier);
    saveFilters(EMPTY_FILTERS);
    setLookbackPreset("30");
    setStartDate(start);
    setEndDate(end);
    setDurationPreset("custom");
    setCustomProjectionDate(target);
    setLoading(true);
    setError(null);
    Promise.all([
      getSupplierProjection(preset.presetSupplier, start, end, horizonDays),
      refreshCartMembership(preset.presetSupplier),
    ])
      .then(([result, cartIds]) => {
        setData(result);
        saveWindow(result, horizonDays);
        setSelected(new Set(cartIds));
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
    // Clear the nav state so a later back/forward in the browser history
    // doesn't silently re-trigger this same auto-run.
    window.history.replaceState({}, document.title);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleLookbackPresetChange(value) {
    setLookbackPreset(value);
    if (value !== "custom") {
      setStartDate(daysAgoStr(Number(value)));
      setEndDate(todayStr());
    }
  }

  // The projection duration is a single day count, but "Custom" lets the
  // user pick a target end date from a calendar instead of a fixed preset -
  // this converts that date back into a day count.
  const effectiveDuration = useMemo(() => {
    if (durationPreset !== "custom") return Number(durationPreset);
    if (!customProjectionDate) return null;
    const diffDays = Math.round((new Date(customProjectionDate) - new Date(todayStr())) / 86400000);
    return diffDays > 0 ? diffDays : null;
  }, [durationPreset, customProjectionDate]);

  const canRun =
    !!supplier &&
    !!effectiveDuration &&
    (lookbackPreset !== "custom" || (!!startDate && !!endDate));

  // Oct 22: the Purchase Order Cart's line suggestions and Add items use
  // the window of the last projection, so both pages suggest the same qty.
  function saveWindow(result, horizon) {
    writeOrderWindow(browserStorage(), {
      lookbackDays: result?.lookback_days,
      horizonDays: result?.horizons_days?.[0] ?? horizon,
    });
  }

  async function handleRun(horizonOverride) {
    const horizon = typeof horizonOverride === "number" ? horizonOverride : effectiveDuration;
    if (!canRun || !horizon) return;
    setLoading(true);
    setError(null);
    setExpandedItemId(null);
    setWeeklyCache({});
    setQtyDrafts({});
    try {
      const [result, cartIds] = await Promise.all([
        getSupplierProjection(supplier, startDate, endDate, horizon),
        refreshCartMembership(supplier),
      ]);
      setData(result);
      // The cart suggests for the window last projected (Oct 22).
      saveWindow(result, horizon);
      // Pre-tick whatever's already in this supplier's cart, so it's obvious
      // at a glance which items are already queued up.
      setSelected(new Set(cartIds));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // The projection duration picked at generate-time is what's actually in
  // `data` (the request), not necessarily the current dropdown/custom-date
  // state if the user changed it without re-running - use the response's
  // own horizon so the table/PDF never say one thing while showing another.
  const activeHorizon = data?.horizons_days?.[0];

  const itemsById = useMemo(() => {
    const map = {};
    (data?.items || []).forEach((item) => {
      map[item.item_id] = item;
    });
    return map;
  }, [data]);

  // Run with the vendor's protection period (stock must last until the order
  // placed at its NEXT cycle arrives), from the Vendor Order Calendar schedule.
  function handleUseProtectionPeriod(days) {
    setDurationPreset("custom");
    setCustomProjectionDate(daysFromNowStr(days));
    handleRun(days);
  }

  // A deep link (Vendor Calendar / Overview schedule "Order due" chip) lands
  // on the full list - a filter remembered from earlier in the session would
  // otherwise silently hide items on the vendor someone came here to order.
  const [filters, setFiltersState] = useState(() =>
    location.state?.presetSupplier ? EMPTY_FILTERS : loadFilters()
  );
  const setFilters = useCallback((update) => {
    setFiltersState((prev) => {
      const next = typeof update === "function" ? update(prev) : update;
      saveFilters(next);
      return next;
    });
  }, []);
  const filtersActive = filters.classes.length > 0 || filters.review || filters.needsOrder;

  function toggleClassFilter(key) {
    setFilters((f) => ({
      ...f,
      classes: f.classes.includes(key) ? f.classes.filter((c) => c !== key) : [...f.classes, key],
    }));
  }

  const flatItems = useMemo(() => {
    if (!activeHorizon) return [];
    return (data?.items || [])
      .filter((item) => passesFilters(item, filters, activeHorizon))
      .map((item) => {
      const p = item.projections[activeHorizon];
      return {
        item_id: item.item_id,
        name: item.name,
        category: item.category,
        // Search only (W2): the item's codes.
        supplier_item_id: item.supplier_item_id,
        all_supplier_item_ids: item.all_supplier_item_ids,
        barcode: item.barcode,
        avg_daily_demand: item.avg_daily_demand,
        avg_weekly_demand: item.avg_weekly_demand,
        on_hand_qty: item.on_hand_qty,
        need: p.need_to_order ?? p.projected_demand ?? null,
        classification: item.forecast?.classification || "",
      };
    });
  }, [data, activeHorizon, filters]);

  // Default sort: most-needed items on top, so the items most worth ordering
  // don't require an extra click to surface out of whatever order the API
  // happened to return.
  const { search, setSearch, sortKey, sortDir, toggleSort, rows: sortedFlatRows } = useTableControls(
    flatItems,
    { searchKeys: ["name", "category", "classification"], codeKeys: true, defaultSortKey: "need", defaultSortDir: "desc" }
  );
  const rows = sortedFlatRows.map((flat) => itemsById[flat.item_id]);

  // Live chip counts: rows matching the search and the other filters, so a
  // chip's number is what you'd see after switching it on.
  const filterCounts = useMemo(() => {
    const items = (data?.items || []).filter((item) => matchesSearch(item, search));
    const counts = { classes: {}, review: 0, needsOrder: 0 };
    items.forEach((item) => {
      if (passesFilters(item, filters, activeHorizon, "classes")) {
        const k = itemClassKey(item);
        counts.classes[k] = (counts.classes[k] || 0) + 1;
      }
      if (item.forecast?.needs_review && passesFilters(item, filters, activeHorizon, "review")) counts.review += 1;
      if ((itemNeed(item, activeHorizon) ?? 0) > 0 && passesFilters(item, filters, activeHorizon, "needsOrder")) {
        counts.needsOrder += 1;
      }
    });
    return counts;
  }, [data, search, filters, activeHorizon]);
  // Which chips to offer: only classes this vendor's items actually have
  // (plus any still switched on, so they can be switched off). No class at
  // all = an older server without forecasts - hide the class chips.
  const classChips = useMemo(() => {
    const present = new Set((data?.items || []).map(itemClassKey));
    const anyClassified = (data?.items || []).some((i) => i.forecast?.classification);
    if (!anyClassified) return CLASS_FILTERS.filter((c) => filters.classes.includes(c.key));
    return CLASS_FILTERS.filter((c) => present.has(c.key) || filters.classes.includes(c.key));
  }, [data, filters.classes]);
  const showReviewChip = filters.review || (data?.items || []).some((i) => i.forecast?.needs_review);

  function th(label, key, align) {
    return (
      <SortableTh sortKey={key} currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align={align}>
        {label}
      </SortableTh>
    );
  }

  function toggleSelected(itemId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  function toggleSelectAll() {
    if (rows.length === 0) return;
    const visibleIds = rows.map((i) => i.item_id);
    const allVisibleSelected = visibleIds.every((id) => selected.has(id));
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        visibleIds.forEach((id) => next.delete(id));
      } else {
        visibleIds.forEach((id) => next.add(id));
      }
      return next;
    });
  }

  async function toggleExpand(itemId) {
    if (expandedItemId === itemId) {
      setExpandedItemId(null);
      return;
    }
    setExpandedItemId(itemId);
    if (weeklyCache[itemId]) return;
    setWeeklyLoading(itemId);
    try {
      // The window this projection was run over, not the (possibly edited
      // since) date inputs.
      // includeEmptyWeeks: a week the item sat unsold (or out of stock)
      // shows too, with its average on-hand count (W3).
      const result = await getItemWeeklySales(itemId, data?.window_start || startDate, data?.as_of || endDate, {
        includeEmptyWeeks: true,
      });
      setWeeklyCache((prev) => ({ ...prev, [itemId]: result.weeks }));
    } catch (err) {
      setWeeklyCache((prev) => ({ ...prev, [itemId]: { error: err.message } }));
    } finally {
      setWeeklyLoading(null);
    }
  }

  const [addingToCart, setAddingToCart] = useState(false);
  const [cartMessage, setCartMessage] = useState(null);

  async function handleAddSelectedToCart() {
    if (!data || !activeHorizon || selected.size === 0) return;
    // The vendor these rows belong to - the dropdown may already show a
    // different one that hasn't been run yet.
    const cartSupplier = data.supplier;
    setAddingToCart(true);
    setCartMessage(null);
    try {
      // Re-read the cart first: an item someone added meanwhile (the phone,
      // the cart page) is left out rather than having its qty replaced.
      const freshIds = await refreshCartMembership(cartSupplier);
      const ids = new Set([...cartItemIds, ...freshIds]);
      // Items already in the cart are pre-ticked purely as an indicator -
      // never re-submitted (that would replace the qty someone set); their
      // own Update button changes them. New ticks go in at the quantity in
      // their box: the suggested order qty unless it was changed (X1).
      const result = buildAddItems(data.items, [...selected], ids, qtyDrafts, cartLines);
      if (result.invalid.length > 0) {
        setCartMessage(`Enter a quantity for: ${result.invalid.join(", ")}.`);
        return;
      }
      if (result.items.length === 0) {
        setCartMessage(
          addMessage(cartSupplier, result) ||
            "Those item(s) are already in the cart - change their quantity with Update or on the Purchase Order Cart page."
        );
        return;
      }
      await addToCart(cartSupplier, result.items);
      setCartMessage(addMessage(cartSupplier, result));
      setCartItemIds((prev) => new Set([...prev, ...result.items.map((i) => i.item_id)]));
      await refreshCartMembership(cartSupplier);
      setQtyDrafts((prev) => {
        const next = { ...prev };
        result.items.forEach((i) => delete next[i.item_id]);
        return next;
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setAddingToCart(false);
    }
  }

  // Owner: opening a window to enter the qty for every item was slow. One
  // tap on a row's Add puts its suggested qty (cases when the case size is
  // known) in the cart, the row turns into a − / + stepper, and a short
  // Undo shows. No window.
  const [quickBusy, setQuickBusy] = useState(null);
  const [undoAdd, setUndoAdd] = useState(null); // {lineId, itemId, message}
  const closeUndoAdd = useCallback(() => setUndoAdd(null), []);

  async function handleQuickAdd(item) {
    if (!data || quickBusy) return;
    const cartSupplier = data.supplier;
    if (cartItemIds.has(item.item_id)) return;
    const line = quickAddLine(item, activeHorizon);
    setQuickBusy(item.item_id);
    setCartMessage(null);
    try {
      const added = await addToCart(cartSupplier, [line]);
      const row = Array.isArray(added) ? added[0] : null;
      setCartItemIds((prev) => new Set([...prev, item.item_id]));
      if (row) {
        setCartLines((prev) => ({
          ...prev,
          [item.item_id]: { id: row.id, qty: row.qty, case_of: row.case_of, can_undo_add: row.can_undo_add },
        }));
      }
      setQtyDrafts((prev) => {
        const next = { ...prev };
        delete next[item.item_id];
        return next;
      });
      setUndoAdd({
        lineId: row?.id ?? null,
        itemId: item.item_id,
        message: `Added ${item.name || item.item_id}: ${qtyWords(line.qty, line.case_of)}.`,
      });
      refreshCartMembership(cartSupplier);
    } catch (err) {
      setCartMessage(`${item.name}: ${err.message}`);
    } finally {
      setQuickBusy(null);
    }
  }

  async function handleUndoAdd() {
    const u = undoAdd;
    if (!u || u.lineId == null) return;
    try {
      await deleteCartItem(u.lineId);
      setCartMessage("Taken back out of the cart.");
    } catch (err) {
      setCartMessage(err.message);
    } finally {
      setUndoAdd(null);
      if (data) refreshCartMembership(data.supplier);
    }
  }

  // − / + on an in-cart row: saved straight away (never below 1).
  async function handleStep(item, delta) {
    const line = cartLines[item.item_id];
    if (!line || !data) return;
    const next = stepQty(line.qty, delta);
    if (next === Number(line.qty)) return;
    setCartLines((prev) => ({ ...prev, [item.item_id]: { ...line, qty: next } }));
    setQtyDrafts((prev) => {
      const copy = { ...prev };
      delete copy[item.item_id];
      return copy;
    });
    try {
      await updateCartItem(line.id, { qty: next });
      window.dispatchEvent(new Event("cart:updated"));
    } catch (err) {
      setCartLines((prev) => ({ ...prev, [item.item_id]: line }));
      setCartMessage(`${item.name}: ${err.message}`);
    }
  }

  // An in-cart item's own Update: replaces that one cart line's qty (X1).
  async function handleUpdateCartQty(item) {
    const update = updateFor(item, qtyDrafts, cartLines[item.item_id]);
    if (!update || !data) return;
    setUpdatingId(item.item_id);
    setCartMessage(null);
    try {
      await updateCartItem(update.id, { qty: update.qty });
      setCartMessage(`${item.name}: cart quantity set to ${qtyWords(update.qty, cartLines[item.item_id]?.case_of)}.`);
      await refreshCartMembership(data.supplier);
      setQtyDrafts((prev) => {
        const next = { ...prev };
        delete next[item.item_id];
        return next;
      });
    } catch (err) {
      setCartMessage(`${item.name}: ${err.message}`);
    } finally {
      setUpdatingId(null);
    }
  }

  return (
    <section className="card">
      <InfoBlock brief="Projected demand vs. on-hand for a chosen supplier and duration - pick items and add them to the purchase order cart, sorted by what's needed most.">
        For a chosen supplier and projection duration: how much of each item you're projected to
        sell, and how much you need to order after what's currently on hand. Demand is worked out
        only from the days an item was actually on the shelf, so a stockout doesn't make it look
        like it doesn't sell. Recent weeks count more than older ones, busy weekdays are taken
        into account, and "Need" includes a small safety buffer for items whose sales jump around.
        Items marked "Review" have something unusual (a long stockout, a sales spike, negative
        on-hand...) - click an item's Avg/Day or Avg/Week figure to see why, along with the
        week-by-week sales it's built from. Rows are sorted with the most-needed items on top by
        default. Items with no known on-hand count show projected demand only (marked *), since it
        isn't netted against stock. Click an item's Avg/Day or Avg/Week figure to see the actual
        week-by-week sales it's built from. Items already sitting in this supplier's cart are
        pre-ticked and marked "in cart" so you can see what's already queued up at a glance -
        re-running the projection won't touch their quantity. Check the items you want to order:
        each ticked item gets a quantity box filled in with its suggested order quantity, with the
        numbers it comes from beside it (sold per week, on hand and its 4-week average, the next
        delivery). Tap a row's Add to put its suggested quantity straight in the cart (Undo
        shows for a few seconds), or tick several and click "Add to cart". An item packed by the
        case is ordered in cases ("1 case = 24 units"). An item in the cart shows − / + on its row;
        type a number in its box to set it exactly.
        The cart is shared and persists across visits, so you can come back and add more (from this or another supplier)
        before adjusting quantities and downloading a PO PDF. See the Help page for the full
        walkthrough.
      </InfoBlock>
      <div className="date-range">
        <label>
          Supplier
          <select value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">Select a supplier…</option>
            {suppliers.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          <Term k="sales_window">Lookback</Term>
          <select value={lookbackPreset} onChange={(e) => handleLookbackPresetChange(e.target.value)}>
            {LOOKBACK_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {lookbackPreset === "custom" && (
          <div className="date-range-group">
            <span className="date-range-group-label">Custom lookback range</span>
            <div className="date-range-group-inputs">
              <input type="date" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} />
              <span className="date-range-separator">to</span>
              <input type="date" value={endDate} min={startDate} max={todayStr()} onChange={(e) => setEndDate(e.target.value)} />
            </div>
          </div>
        )}
        <label>
          Projection duration
          <select value={durationPreset} onChange={(e) => setDurationPreset(e.target.value)}>
            {PROJECTION_OPTIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        {durationPreset === "custom" && (
          <label>
            Project through
            <input
              type="date"
              value={customProjectionDate}
              min={daysFromNowStr(1)}
              onChange={(e) => setCustomProjectionDate(e.target.value)}
            />
          </label>
        )}
      </div>
      <button disabled={!canRun || loading} onClick={() => handleRun()}>
        {loading ? "Calculating…" : "Generate projection"}
      </button>
      {error && <p className="error">{error}</p>}

      {data && activeHorizon && (
        <>
          <div className="sp-toolbar">
            <p className="muted" style={{ margin: 0 }}>
              {rows.length} of {data.items.length} item(s) from {data.supplier}, projected over{" "}
              {durationLabel(activeHorizon)}, as of {data.as_of} (sales window {data.window_start} →{" "}
              {data.as_of}).
            </p>
            <div className="sp-toolbar-actions">
              <span className="muted" style={{ fontSize: "0.82rem" }}>
                {selected.size} selected
              </span>
              <button disabled={selected.size === 0 || addingToCart} onClick={handleAddSelectedToCart}>
                {addingToCart ? "Adding…" : `Add to cart (${selected.size})`}
              </button>
              {/* Opens the cart at this vendor (expanded). */}
              {canOpenCart && (
                <button className="link-button" onClick={() => navigate(cartLinkFor(data.supplier))}>
                  View purchase order cart →
                </button>
              )}
            </div>
          </div>
          <LiveStockNote liveStock={data.live_stock} style={{ marginBottom: 0 }} />
          {cartMessage && <p className="muted">{cartMessage}</p>}
          {data.protection_days && data.protection_days !== activeHorizon && (
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {data.supplier}'s delivery schedule says this order has to last {data.protection_days} days (until
              the order after this one arrives). The suggestions are for {windowWords(activeHorizon)} now.
              <TermInfo k="protection_days" />{" "}
              <button className="link-button" disabled={loading} onClick={() => handleUseProtectionPeriod(data.protection_days)}>
                Order for {data.protection_days} days instead
              </button>
            </p>
          )}
          {data.order_window && (
            <p className="muted sp-order-window">
              Ticked items start at their <Term k="suggested_order_qty">suggested order qty</Term> for{" "}
              {data.order_window.label}
              {data.order_window.delivery ? ` - ${data.order_window.delivery.label}` : " - no delivery schedule"}.
              Tap Add on a row, or tick items and add them together.
            </p>
          )}
          <div className="sp-filters" role="group" aria-label="Filter items">
            <button
              type="button"
              className={`sp-filter-chip${filtersActive ? "" : " active"}`}
              aria-pressed={!filtersActive}
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              All
            </button>
            {classChips.map((c) => (
              <span key={c.key} className="sp-filter-item">
                <button
                  type="button"
                  className={`sp-filter-chip${filters.classes.includes(c.key) ? " active" : ""}`}
                  aria-pressed={filters.classes.includes(c.key)}
                  onClick={() => toggleClassFilter(c.key)}
                  title={CLASS_HINTS[c.key] || "No forecast class for this item"}
                >
                  {c.label} ({filterCounts.classes[c.key] || 0})
                </button>
                {CLASS_TERMS[c.key] && <TermInfo k={CLASS_TERMS[c.key]} label={c.label} />}
              </span>
            ))}
            {showReviewChip && (
              <span className="sp-filter-item">
                <button
                  type="button"
                  className={`sp-filter-chip${filters.review ? " active" : ""}`}
                  aria-pressed={filters.review}
                  onClick={() => setFilters((f) => ({ ...f, review: !f.review }))}
                  title="Only items flagged for review"
                >
                  Review ({filterCounts.review})
                </button>
                <TermInfo k="review" label="Review" />
              </span>
            )}
            <span className="sp-filter-item">
              <button
                type="button"
                className={`sp-filter-chip${filters.needsOrder ? " active" : ""}`}
                aria-pressed={filters.needsOrder}
                onClick={() => setFilters((f) => ({ ...f, needsOrder: !f.needsOrder }))}
                title="Only items whose Need is above 0"
              >
                Needs order ({filterCounts.needsOrder})
              </button>
              <TermInfo k="need_to_order" label="Needs order" />
            </span>
          </div>

          <input
            type="text"
            className="table-search"
            placeholder="Search item, category, SKU or barcode…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />

          <div className="table-wrap">
            <table className="sp-table">
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      checked={rows.length > 0 && rows.every((i) => selected.has(i.item_id))}
                      onChange={toggleSelectAll}
                    />
                  </th>
                  {th("Item", "name")}
                  <th className="num sp-qty-th">
                    <Term k="suggested_order_qty">Order qty</Term>
                  </th>
                  {th("Category", "category")}
                  {th(<Term k="avg_demand">Avg/day</Term>, "avg_daily_demand", "right")}
                  {th("Avg/week", "avg_weekly_demand", "right")}
                  {th(<Term k="container_qty" label="On hand">On hand</Term>, "on_hand_qty", "right")}
                  {th(<Term k="need_to_order">{`Need (${durationLabel(activeHorizon)})`}</Term>, "need", "right")}
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <SupplierProjectionRow
                    key={item.item_id}
                    item={item}
                    activeHorizon={activeHorizon}
                    isSelected={selected.has(item.item_id)}
                    onToggleSelected={() => toggleSelected(item.item_id)}
                    inCart={cartItemIds.has(item.item_id)}
                    isExpanded={expandedItemId === item.item_id}
                    onToggleExpand={() => toggleExpand(item.item_id)}
                    weeklyData={weeklyCache[item.item_id]}
                    weeklyLoading={weeklyLoading === item.item_id}
                    onShowSimilar={() => setSimilarFor(item)}
                    qtyValue={qtyText(item, qtyDrafts, cartLines[item.item_id])}
                    onQtyChange={(text) => setQtyDrafts((prev) => ({ ...prev, [item.item_id]: text }))}
                    cartLine={cartLines[item.item_id]}
                    canUpdate={!!updateFor(item, qtyDrafts, cartLines[item.item_id])}
                    updating={updatingId === item.item_id}
                    onUpdateCart={() => handleUpdateCartQty(item)}
                    quickBusy={quickBusy === item.item_id}
                    onQuickAdd={() => handleQuickAdd(item)}
                    onStep={(delta) => handleStep(item, delta)}
                  />
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="muted">
                      {search
                        ? `No items match "${search}"${filtersActive ? " with these filters" : ""}.`
                        : filtersActive
                          ? "No items match these filters."
                          : "No items to show."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
      {undoAdd && (
        <UndoBar
          key={`undo-add-${undoAdd.lineId}`}
          message={undoAdd.message}
          onUndo={undoAdd.lineId != null ? handleUndoAdd : undefined}
          onClose={closeUndoAdd}
        />
      )}
      {similarFor && data && (
        <SimilarItemsDrawer item={similarFor} fallbackSupplier={data.supplier} onClose={closeSimilar} />
      )}
    </section>
  );
}

function SupplierProjectionRow({
  item,
  activeHorizon,
  isSelected,
  onToggleSelected,
  inCart,
  isExpanded,
  onToggleExpand,
  weeklyData,
  weeklyLoading,
  onShowSimilar,
  qtyValue,
  onQtyChange,
  cartLine,
  canUpdate,
  updating,
  onUpdateCart,
  quickBusy,
  onQuickAdd,
  onStep,
}) {
  const p = item.projections[activeHorizon];
  const suggestion = suggestionOf(item);
  // Order qty in cases when the case size is known.
  const caseSize = caseSizeOf(item, inCart ? cartLine : null);
  const typed = parseQty(qtyValue);
  const caseText = caseLabel(typed.ok ? typed.value : null, caseSize);
  const otherSizes = otherCaseSizes(item, caseSize);
  const qtyBad = (isSelected || inCart) && !parseQty(qtyValue).ok;
  const qtyLabel = `Order qty for ${item.name || item.item_id}`;
  const known = p.need_to_order !== null;
  // Additive fields - older servers don't send them.
  const otherVendors = Array.isArray(item.other_vendors) ? item.other_vendors.filter((v) => v?.name) : [];
  const similarCount = item.similar_count;
  // A known zero means there's nothing to show - hide the link to keep the
  // row tidy. Unknown (null/missing) still offers it; the drawer looks it up.
  const showSimilar = similarCount !== 0;
  const similarLabel = typeof similarCount === "number" && similarCount > 0 ? `Similar (${similarCount})` : "Similar";
  return (
    <>
      <tr className={isSelected ? "sp-row-selected" : ""}>
        <td>
          <input type="checkbox" checked={isSelected} onChange={onToggleSelected} />
        </td>
        <td className="cell-name">
          {item.name || item.item_id}
          {item.forecast?.classification && (
            <>
              {" "}
              <Term k={CLASS_TERMS[item.forecast.classification]}>
                <span
                  className={`pill ${CLASS_PILLS[item.forecast.classification] || ""} sp-pill`}
                  title={CLASS_HINTS[item.forecast.classification]}
                >
                  {item.forecast.classification}
                </span>
              </Term>
            </>
          )}
          {inCart && (
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              {" "}
              (in cart)
            </span>
          )}
          {item.forecast?.needs_review && (
            <>
              {" "}
              <Term k="review">
                <span className="pill pill-failed sp-pill" title={item.forecast.flags.map((f) => FLAG_LABELS[f] || f).join(", ")}>
                  Review
                </span>
              </Term>
            </>
          )}
          {(isSelected || inCart) && suggestion?.basis && (
            <span className="sp-qty-basis">
              {suggestion.basis}
              <TermInfo k="suggested_order_qty" label="Suggested order qty" />
            </span>
          )}
          {item.po_section && (
            <span className="sp-po-section" title="The heading this item prints under on this vendor's PO PDF">
              PO section: {item.po_section}
            </span>
          )}
          {(otherVendors.length > 0 || showSimilar) && (
            <span className="sp-item-sub">
              {otherVendors.length > 0 && (
                <span className="sp-also-at">
                  Also at
                  <TermInfo k="also_at" />: {vendorList(otherVendors)}
                </span>
              )}
              {showSimilar && (
                <button
                  type="button"
                  className="link-button sp-similar-link"
                  onClick={onShowSimilar}
                  title="Other brands and sizes of this product"
                >
                  {similarLabel}
                </button>
              )}
              {showSimilar && (
                <TermInfo k="similar_products" label="Similar" />
              )}
            </span>
          )}
        </td>
        <td className="num sp-qty-cell">
          {inCart && cartLine ? (
            // In the cart: − qty + right on the row. Typing a number
            // saves it on Enter or when the box is left.
            <span className="sp-qty-box sp-stepper">
              <span className="sp-step-row">
                <button
                  type="button"
                  className="sp-step"
                  aria-label={`One less ${item.name || ""}`.trim()}
                  disabled={updating || Number(cartLine.qty) <= 1}
                  onClick={() => onStep(-1)}
                >
                  −
                </button>
                <input
                  type="number"
                  min="1"
                  step="1"
                  inputMode="decimal"
                  className={`po-qty-input sp-qty-input${qtyBad ? " qty-mismatch" : ""}`}
                  value={qtyValue}
                  aria-label={qtyLabel}
                  aria-invalid={qtyBad || undefined}
                  onChange={(e) => onQtyChange(e.target.value)}
                  onBlur={() => canUpdate && onUpdateCart()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && canUpdate) onUpdateCart();
                  }}
                />
                <button
                  type="button"
                  className="sp-step"
                  aria-label={`One more ${item.name || ""}`.trim()}
                  disabled={updating}
                  onClick={() => onStep(1)}
                >
                  +
                </button>
              </span>
              <span className="sp-qty-unit">{caseText || (caseSize ? null : "units")} · in cart</span>
            </span>
          ) : (
            <span className="sp-qty-box sp-qty-add">
              <span className="sp-step-row">
                {isSelected ? (
                  <input
                    type="number"
                    min="0"
                    step="1"
                    inputMode="decimal"
                    className={`po-qty-input sp-qty-input${qtyBad ? " qty-mismatch" : ""}`}
                    value={qtyValue}
                    aria-label={qtyLabel}
                    aria-invalid={qtyBad || undefined}
                    onChange={(e) => onQtyChange(e.target.value)}
                  />
                ) : suggestion ? (
                  <span className="muted sp-qty-hint" title={suggestion.basis}>
                    {suggestion.enough_stock ? "enough" : qtyWords(suggestion.qty, caseSize).replace(/ \(.*\)$/, "")}
                  </span>
                ) : (
                  <span className="muted">-</span>
                )}
                <button
                  type="button"
                  className="sp-add-btn"
                  disabled={quickBusy}
                  onClick={onQuickAdd}
                  title="Put the suggested quantity in the cart"
                >
                  {quickBusy ? "Adding…" : "Add"}
                </button>
              </span>
              {caseSize ? (
                <span className="sp-qty-unit">
                  {isSelected ? caseText : `1 case = ${caseSize} units`}
                </span>
              ) : null}
            </span>
          )}
          {otherSizes && <span className="sp-qty-unit sp-case-others">{otherSizes}</span>}
        </td>
        <td>{item.category || "-"}</td>
        <td className="num">
          <button className="sp-value-link" onClick={onToggleExpand} title="Show weekly sales">
            {item.avg_daily_demand}
            <span className="sp-caret">{isExpanded ? "▲" : "▼"}</span>
          </button>
        </td>
        <td className="num">
          <button className="sp-value-link" onClick={onToggleExpand} title="Show weekly sales">
            {item.avg_weekly_demand}
            <span className="sp-caret">{isExpanded ? "▲" : "▼"}</span>
          </button>
        </td>
        <td className={`num ${item.on_hand_qty < 0 ? "flag" : ""}`}>
          {item.on_hand_qty ?? "-"}
          {item.container_qty != null && (
            <span className="muted sp-container-hint" title={`Includes ${item.container_qty} from a Container storage location`}>
              {" "}
              (+{item.container_qty} container)
              <TermInfo k="container_qty" />
            </span>
          )}
        </td>
        <td className={`num ${known && p.need_to_order > 0 ? "flag" : ""}`}>
          {known ? p.need_to_order : `${p.projected_demand}*`}
          {known && p.need_cases != null && p.need_to_order > 0 && (
            <span className="muted sp-need-cases">
              {" "}
              ({p.need_cases} {p.need_cases === 1 ? "case" : "cases"})
            </span>
          )}
        </td>
      </tr>
      {isExpanded && (
        <tr className="sp-weekly-row">
          <td colSpan={8}>
            {item.forecast && <ForecastDetails item={item} projection={p} horizon={activeHorizon} />}
            {weeklyLoading && <span className="muted">Loading weekly sales…</span>}
            {!weeklyLoading && weeklyData?.error && <span className="error">{weeklyData.error}</span>}
            {!weeklyLoading && Array.isArray(weeklyData) && (
              <WeeklySalesTable weeks={weeklyData} includesContainer={item.container_qty != null} />
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function ForecastDetails({ item, projection, horizon }) {
  const f = item.forecast;
  const fmt = (v) => (v === null || v === undefined ? "-" : v);
  return (
    <div className="sp-forecast">
      <div className="sp-forecast-grid">
        <div>
          <span className="muted">Type</span>
          <Term k={CLASS_TERMS[f.classification]}>
            <span className={`pill ${CLASS_PILLS[f.classification] || ""} sp-pill`}>{f.classification}</span>
          </Term>
        </div>
        <div>
          <span className="muted">
            <Term k="avg_demand">Base demand / day</Term>
          </span>
          {fmt(f.base_daily_demand)}
        </div>
        <div>
          <span className="muted">
            <Term k="availability">On the shelf</Term>
          </span>
          {fmt(f.availability_pct)}% of {f.window_days} days
        </div>
        <div>
          <span className="muted">Stockout days</span>
          {f.stockout_days}
          {f.sellout_days ? ` (+${f.sellout_days} sold out mid-day)` : ""}
        </div>
        <div>
          <span className="muted">Sold last 7 / 14 days</span>
          {f.recent_7_day_sales} / {f.recent_14_day_sales}
        </div>
        <div>
          <span className="muted">
            <Term k="trend">Trend</Term>
          </span>
          ×{f.trend_factor}
        </div>
        <div>
          <span className="muted">
            <Term k="projected_demand">Projected ({horizon}d)</Term>
          </span>
          {projection.projected_demand}
        </div>
        <div>
          <span className="muted">
            <Term k="safety_buffer">Safety buffer</Term>
          </span>
          {fmt(projection.safety_buffer)}
        </div>
        <div>
          <span className="muted">
            <Term k="simple_average">Simple average / day</Term>
          </span>
          {fmt(f.legacy_avg_daily_demand)}
        </div>
      </div>
      <div className="sp-forecast-dow muted">
        <Term k="weekday_pattern">Weekday pattern</Term>:{" "}
        {WEEKDAYS.map((d) => `${d[0].toUpperCase()}${d.slice(1)} ×${f.dow_factors[d]}`).join(" · ")}
      </div>
      {f.flags.length > 0 && (
        <div className="sp-forecast-flags">
          {f.flags.map((flag) => (
            <Term key={flag} k={FLAG_TERMS[flag]}>
              <span className="pill pill-running sp-pill">{FLAG_LABELS[flag] || flag}</span>
            </Term>
          ))}
        </div>
      )}
    </div>
  );
}

// "Acme Foods (AF-123), Best Supply" - vendor names with their item code when known.
function vendorList(vendors) {
  return vendors.map((v) => (v.supplier_item_id ? `${v.name} (${v.supplier_item_id})` : v.name)).join(", ");
}

function fmtQty(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : "-";
}

function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "-";
}

// Right-side panel listing other brands/sizes of the same product, each with
// its own "Add to cart". Reuses the Fix Issues slide-in drawer styles.
function SimilarItemsDrawer({ item, fallbackSupplier, onClose }) {
  const navigate = useNavigate();
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // supplier name -> Set of item_ids already in that supplier's cart section.
  const [cartIds, setCartIds] = useState({});
  // item_id -> index into that product's vendors (when it has several).
  const [vendorChoice, setVendorChoice] = useState({});
  // item_id -> { busy } | { ok: message } | { error: message }
  const [status, setStatus] = useState({});

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);
    setStatus({});
    setVendorChoice({});
    Promise.all([getSimilarItems(item.item_id), getCart().catch(() => null)])
      .then(([res, cart]) => {
        if (cancelled) return;
        setResult(res);
        const map = {};
        (cart?.suppliers || []).forEach((s) => {
          map[s.supplier] = new Set((s.items || []).map((i) => i.item_id).filter(Boolean));
        });
        setCartIds(map);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [item.item_id]);

  const sourceName = result?.name || item.name || item.item_id;
  const sourceMeta = [result?.brand, result?.size].filter(Boolean).join(" · ");
  const products = result?.items || [];

  function targetFor(p) {
    const vendors = (p.vendors || []).filter((v) => v?.name);
    if (vendors.length === 0) {
      return { vendors, fallback: true, target: fallbackSupplier ? { name: fallbackSupplier, supplier_item_id: null } : null };
    }
    const idx = vendorIndex(p, vendors);
    return { vendors, fallback: false, target: vendors[idx] };
  }

  // The vendor picked for a product: the user's choice, else the vendor
  // whose projection this is (when it sells the product too), else the first.
  function vendorIndex(p, vendors) {
    const chosen = vendorChoice[p.item_id];
    if (chosen !== undefined) return Math.min(chosen, vendors.length - 1);
    const own = (fallbackSupplier || "").trim().toLowerCase();
    const i = own ? vendors.findIndex((v) => v.name.trim().toLowerCase() === own) : -1;
    return i >= 0 ? i : 0;
  }

  async function handleAdd(p, target) {
    setStatus((prev) => ({ ...prev, [p.item_id]: { busy: true } }));
    try {
      await addToCart(target.name, [
        { item_id: p.item_id, name: p.name, supplier_item_id: target.supplier_item_id || null, qty: 1 },
      ]);
      setCartIds((prev) => ({ ...prev, [target.name]: new Set([...(prev[target.name] || []), p.item_id]) }));
      setStatus((prev) => ({ ...prev, [p.item_id]: { ok: `Added to the cart for ${target.name}.` } }));
    } catch (err) {
      setStatus((prev) => ({ ...prev, [p.item_id]: { error: err.message } }));
    }
  }

  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="issue-drawer issue-details" role="dialog" aria-label={`Products similar to ${sourceName}`}>
        <div className="issue-drawer-head">
          <h3>{sourceName}</h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Close similar products">
            ✕
          </button>
        </div>
        <p className="muted issue-drawer-items">
          Similar products{sourceMeta ? ` - ${sourceMeta}` : ""}
          {result?.product_type ? ` (${result.product_type})` : ""}
        </p>

        {loading && <p className="muted">Looking for similar products…</p>}
        {!loading && error && <p className="error">{error}</p>}
        {!loading && !error && products.length === 0 && <p className="muted">No similar products found.</p>}

        {!loading && !error && products.length > 0 && (
          <ul className="sp-similar-list">
            {products.map((p) => {
              const { vendors, fallback, target } = targetFor(p);
              const inCart = !!target && !!cartIds[target.name]?.has(p.item_id);
              const st = status[p.item_id] || {};
              const meta = [p.brand, p.size, p.category].filter(Boolean).join(" · ");
              return (
                <li key={p.item_id} className="sp-similar-card">
                  <div className="sp-similar-top">
                    <span className="sp-similar-name">{p.name || p.item_id}</span>
                    {p.reason && (
                      <Term k={SIMILAR_REASON_TERMS[p.reason]}>
                        <span className="pill pill-info sp-pill">{p.reason}</span>
                      </Term>
                    )}
                  </div>
                  {meta && <div className="sp-similar-meta">{meta}</div>}
                  <dl className="sp-similar-facts">
                    <div>
                      <dt>On hand</dt>
                      <dd>
                        {fmtQty(p.on_hand_qty)}
                        {p.container_qty != null && Number(p.container_qty) !== 0 && (
                          <span className="muted sp-container-hint"> (+{fmtQty(p.container_qty)} container)</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Sold 30 days</dt>
                      <dd>{fmtQty(p.units_30d)}</dd>
                    </div>
                    <div>
                      <dt>Price</dt>
                      <dd>{fmtMoney(p.price)}</dd>
                    </div>
                    <div>
                      <dt>Cost</dt>
                      <dd>{fmtMoney(p.cost)}</dd>
                    </div>
                  </dl>
                  <div className="sp-similar-meta">
                    Vendors: {vendors.length > 0 ? vendorList(vendors) : "none on file"}
                  </div>
                  {target ? (
                    <div className="sp-similar-actions">
                      {vendors.length > 1 && (
                        <select
                          className="sp-similar-vendor"
                          aria-label={`Vendor for ${p.name}`}
                          value={vendorIndex(p, vendors)}
                          onChange={(e) => {
                            const idx = Number(e.target.value);
                            setVendorChoice((prev) => ({ ...prev, [p.item_id]: idx }));
                            setStatus((prev) => ({ ...prev, [p.item_id]: {} }));
                          }}
                        >
                          {vendors.map((v, i) => (
                            <option key={`${v.name}-${i}`} value={i}>
                              {v.name}
                            </option>
                          ))}
                        </select>
                      )}
                      {inCart ? (
                        <Term k="in_cart">
                          <span className="pill pill-success sp-pill" title={`Already in the cart for ${target.name}`}>
                            In cart
                          </span>
                        </Term>
                      ) : (
                        <button type="button" disabled={!!st.busy} onClick={() => handleAdd(p, target)}>
                          {st.busy ? "Adding…" : vendors.length === 1 ? `Add to ${target.name}` : "Add to cart"}
                        </button>
                      )}
                      {fallback && !inCart && (
                        <span className="sp-similar-note">No vendor on file - add to {target.name}</span>
                      )}
                    </div>
                  ) : (
                    <div className="sp-similar-note">No vendor on file.</div>
                  )}
                  {st.ok && <div className="sp-similar-ok">{st.ok}</div>}
                  {st.error && <div className="sp-similar-err">{st.error}</div>}
                </li>
              );
            })}
          </ul>
        )}

        <div className="issue-drawer-actions">
          <button
            type="button"
            className="link-button"
            onClick={() => {
              onClose();
              navigate("/purchase-order");
            }}
          >
            View cart
          </button>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
