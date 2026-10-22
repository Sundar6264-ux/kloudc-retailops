import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  getCart,
  getCartProjection,
  updateCartItem,
  deleteCartItem,
  clearSupplierCart,
  markOrderPlaced,
  getRecentPlacedOrders,
  downloadPo,
  exportPurchaseOrderPdf,
  getSectionOptions,
  requestCartRemoval,
  cancelCartRemoval,
  approveCartRemoval,
  rejectCartRemoval,
} from "../api";
import { useAuth } from "../AuthContext.jsx";
import { CartLineSection, OTHER, groupBySection } from "./SectionControls.jsx";
import { formatRestaurantTime } from "../format";
import AddItemsForm from "./cartAdd/AddItemsForm.jsx";
import { plural } from "./cartAdd/cartAddModel.js";
import {
  browserStorage,
  cartVendorFor,
  estimateNote,
  formatMoney,
  isNewLine,
  linkedJustAdded,
  linkedVendor,
  newLineKeys,
  pruneOpenVendors,
  readOpenVendors,
  setVendorOpen,
  summaryParts,
  vendorDomId,
  vendorSummary,
  writeOpenVendors,
} from "./cartAdd/cartVendorPanels.js";
import { Term, TermInfo } from "../glossary.jsx";
import SendPoDialog from "./poSend/SendPoDialog.jsx";
import PoDownloadMenu from "./poDownload/PoDownloadMenu.jsx";
import { contactsLinkFor, sentNotice } from "./poSend/poSendModel.js";
import {
  caseLabel,
  cartContext,
  contextKey,
  switchToCases,
  useSuggestedQty,
} from "./projectionQty/projectionQtyModel.js";

// How long the lines an add just put in stay highlighted (the fade itself
// is in CSS), and how long a vendor opened from a link is outlined.
const NEW_LINES_MS = 6000;
const PULSE_MS = 2200;

// A persistent, shared cart (see backend CartItem/po_cart.py): items checked
// on Supplier Projection land here, across as many visits and suppliers as
// needed, and stay until someone removes them or clears a supplier's
// section - unlike the old one-shot "check items -> build PO -> download"
// flow, there's no router state to lose on a refresh.
//
// Each vendor is a collapsible panel (Oct 8): collapsed by default, with a
// summary header (items, cases, estimate, removal requests) that keeps
// Order placed and Download PDF one click away. Which vendors are open is
// remembered per browser (localStorage); a link to
// "#/purchase-order?vendor=X" opens X; an add from the form opens its
// vendor and highlights the new lines.
export default function PurchaseOrderCart() {
  const [cart, setCart] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Errors from one vendor's actions (PDF, Order placed, a line's qty...),
  // shown in that vendor's panel instead of at the top of a long page.
  const [vendorErrors, setVendorErrors] = useState({});
  const [exportingSupplier, setExportingSupplier] = useState(null);
  const [confirmClearSupplier, setConfirmClearSupplier] = useState(null);
  const [confirmPlacedSupplier, setConfirmPlacedSupplier] = useState(null);
  const [markingPlacedSupplier, setMarkingPlacedSupplier] = useState(null);
  // Send PO (automation item 8): the vendor whose Send dialog is open, and
  // the message after a send (or "record as placed") finished.
  const [sendPoSupplier, setSendPoSupplier] = useState(null);
  const [notice, setNotice] = useState(null);
  // Bumped after a successful "Order placed" so RecentPlacedOrders re-fetches
  // without the two components needing to share full state.
  const [recentOrdersRefreshKey, setRecentOrdersRefreshKey] = useState(0);
  // PO sections: lines are grouped under the heading they print under on the
  // PDF; editors (category_edits) can change a line's section inline.
  const { hasFeature, user } = useAuth();
  const canEditSections = hasFeature("category_edits");
  // Only an admin can remove cart lines (owner, Sep 30); everyone else asks
  // with "Request removal". The server says which via can_remove.
  const canRemove = cart ? !!cart.can_remove : !!user?.is_admin;
  const [busyLine, setBusyLine] = useState(null);
  const [sectionNames, setSectionNames] = useState([]);

  // --- Vendor panels: open / collapsed --------------------------------------
  const [openVendors, setOpenVendors] = useState(() => readOpenVendors(browserStorage()));
  // { supplier, keys, n }: lines an add just put in (highlighted). `n`
  // alternates the CSS animation so "View in cart" can play it again.
  const [flash, setFlash] = useState(null);
  const lastAdded = useRef(null);
  const flashCount = useRef(0);
  // { supplier, n }: a vendor opened from a link / "View in cart" - outlined
  // briefly once it's scrolled into view.
  const [pulse, setPulse] = useState(null);
  const [scrollTo, setScrollTo] = useState(null);
  const [linkNote, setLinkNote] = useState(null);
  const pruned = useRef(false);

  useEffect(() => {
    writeOpenVendors(browserStorage(), openVendors);
  }, [openVendors]);

  useEffect(() => {
    if (!canEditSections) return;
    getSectionOptions()
      .then((res) => setSectionNames((res.sections || []).map((s) => s.section).filter((n) => n !== OTHER)))
      .catch(() => {});
  }, [canEditSections]);

  // Re-fetch without the full-page loading state (keeps the scroll position).
  async function quietRefresh() {
    try {
      setCart(await getCart());
    } catch (err) {
      setError(err.message);
    }
  }

  // For the add form: re-reads the cart and hands it back (null when it
  // couldn't) - before an add, so a page left open doesn't overwrite a line
  // added meanwhile, and after one, to say exactly what reached the cart.
  // No page-level error here: the form explains what it means.
  async function reloadCart() {
    try {
      const fresh = await getCart();
      setCart(fresh);
      return fresh;
    } catch {
      return null;
    }
  }

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const result = await getCart();
      setCart(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  // X1: each line's projection context ("~14/week · suggested 10 · on hand
  // 6"), read after the plain cart is on screen (it takes ~1 s on a big
  // cart) and again whenever the lines, vendors, quantities or Case of
  // change. A failed read just leaves the lines without it.
  const [projections, setProjections] = useState({});
  const [projectionWindow, setProjectionWindow] = useState(null);
  const ctxKey = useMemo(() => contextKey(cart), [cart]);
  useEffect(() => {
    if (!cart || cart.suppliers.length === 0) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      getCartProjection()
        .then((res) => {
          if (cancelled) return;
          const byId = {};
          (res.suppliers || []).forEach((s) =>
            (s.items || []).forEach((i) => {
              if (i.projection) byId[i.id] = i.projection;
            })
          );
          setProjections(byId);
          setProjectionWindow(res.projection_window || null);
        })
        .catch(() => {});
    }, 600);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctxKey]);

  // Once, after the first load: forget remembered vendors that are no
  // longer in the cart (their order was placed), so they start collapsed
  // the next time they have items.
  useEffect(() => {
    if (!cart || pruned.current) return;
    pruned.current = true;
    setOpenVendors((prev) => pruneOpenVendors(prev, cart.suppliers.map((s) => s.supplier)));
  }, [cart]);

  function setVendorError(supplier, message) {
    setVendorErrors((prev) => ({ ...prev, [supplier]: message }));
  }

  function clearVendorError(supplier) {
    setVendorErrors((prev) => {
      if (!(supplier in prev)) return prev;
      const next = { ...prev };
      delete next[supplier];
      return next;
    });
  }

  function toggleVendor(supplier) {
    setOpenVendors((prev) => setVendorOpen(prev, supplier, !prev.has(supplier)));
  }

  // Opens a vendor's panel and scrolls it into view (a link to the cart at
  // that vendor, or "View in cart" after an add).
  const showVendor = useCallback((supplier) => {
    setOpenVendors((prev) => setVendorOpen(prev, supplier, true));
    setScrollTo({ supplier, at: Date.now() });
  }, []);

  useEffect(() => {
    if (!scrollTo) return;
    const panel = document.getElementById(vendorDomId("po-vendor", scrollTo.supplier));
    if (!panel) {
      // Not rendered yet - the next render tries again; a vendor that never
      // shows up is given up on rather than jumped to much later.
      if (Date.now() - scrollTo.at > 5000) setScrollTo(null);
      return;
    }
    setScrollTo(null);
    panel.scrollIntoView({ block: "start", behavior: "smooth" });
    panel.querySelector(".po-vendor-toggle")?.focus({ preventScroll: true });
    setPulse((p) => ({ supplier: scrollTo.supplier, n: (p?.n || 0) + 1 }));
  });

  useEffect(() => {
    if (!pulse) return undefined;
    const t = setTimeout(() => setPulse((p) => (p === pulse ? null : p)), PULSE_MS);
    return () => clearTimeout(t);
  }, [pulse]);

  useEffect(() => {
    if (!flash) return undefined;
    const t = setTimeout(() => setFlash((f) => (f === flash ? null : f)), NEW_LINES_MS);
    return () => clearTimeout(t);
  }, [flash]);

  // The add form put `rows` into `supplier`'s cart (none when everything was
  // already there): open that vendor and highlight the new lines.
  const handleAdded = useCallback((supplier, rows) => {
    setOpenVendors((prev) => setVendorOpen(prev, supplier, true));
    if (!rows || rows.length === 0) return;
    lastAdded.current = { supplier, keys: newLineKeys(rows) };
    flashCount.current += 1;
    setFlash({ ...lastAdded.current, n: flashCount.current });
  }, []);

  // "View in cart" under the add form's message.
  const handleShowVendor = useCallback(
    (supplier) => {
      showVendor(supplier);
      if (lastAdded.current?.supplier === supplier) {
        flashCount.current += 1;
        setFlash({ ...lastAdded.current, n: flashCount.current });
      }
    },
    [showVendor]
  );

  // A link that points at one vendor: "#/purchase-order?vendor=X" (or router
  // state { vendor }). Handled once the cart is in, then dropped from the
  // URL so a reload doesn't jump there again (the panel stays open anyway).
  // Its state can also list item_ids to mark "Just added" (Reorder
  // Candidates' "Open cart" after adding - cartLinkState).
  const location = useLocation();
  const navigate = useNavigate();
  const linked = useMemo(() => linkedVendor(location.search, location.state), [location.search, location.state]);
  const handledLink = useRef(null);
  useEffect(() => {
    // Once the link is handled the URL drops it (below); forget the handled
    // key then. A link typed while the cart is open is a pop navigation
    // whose key is "default" - the same key as a first page load - so a
    // stale key would make that second link do nothing.
    if (!linked) {
      handledLink.current = null;
      return;
    }
    if (!cart || handledLink.current === location.key) return;
    handledLink.current = location.key;
    const name = cartVendorFor(linked, cart.suppliers);
    if (name) {
      setLinkNote(null);
      showVendor(name);
      const justAdded = linkedJustAdded(location.state);
      if (justAdded.length) {
        lastAdded.current = { supplier: name, keys: newLineKeys(justAdded.map((item_id) => ({ item_id }))) };
        flashCount.current += 1;
        setFlash({ ...lastAdded.current, n: flashCount.current });
      }
    } else {
      setLinkNote(`${linked} has nothing in the cart right now.`);
    }
    navigate("/purchase-order", { replace: true });
  }, [linked, cart, location.key, location.state, navigate, showVendor]);

  function showDraft(item, field, value) {
    setCart((prev) => ({
      ...prev,
      suppliers: prev.suppliers.map((s) => ({
        ...s,
        items: s.items.map((i) => (i.id === item.id ? { ...i, [field]: value } : i)),
      })),
    }));
  }

  async function handleFieldChange(item, field, value) {
    // An empty box while retyping (delete "5", type "7") is shown as typed
    // but not saved; leaving it empty restores the saved value on blur.
    if (value === "") {
      showDraft(item, field, value);
      return;
    }
    const num = Number(value);
    if (!Number.isFinite(num) || num < 0) return;
    // Qty 0 drops the line from the order - an admin-only removal. Shown
    // while typing, not saved; blur restores the saved value.
    if (field === "qty" && num < 1 && !canRemove) {
      showDraft(item, field, value);
      return;
    }
    // Optimistic update so typing feels immediate; refresh() would blow away
    // the input's current cursor/focus state on every keystroke. unit_cost
    // doesn't change with qty/case_of, so the estimate below can be
    // recomputed live from this without waiting on the server.
    setCart((prev) => ({
      ...prev,
      suppliers: prev.suppliers.map((s) => ({
        ...s,
        items: s.items.map((i) => (i.id === item.id ? { ...i, [field]: num } : i)),
      })),
    }));
    try {
      await updateCartItem(item.id, { [field]: num });
      clearVendorError(item.supplier);
    } catch (err) {
      setVendorError(item.supplier, err.message);
      refresh();
    }
  }

  // A line added in units while the inventory file says the
  // item comes in cases - one click turns it into whole cases.
  async function handleSwitchCases(item) {
    const change = switchToCases(item);
    if (!change) return;
    try {
      await updateCartItem(item.id, change);
      clearVendorError(item.supplier);
    } catch (err) {
      setVendorError(item.supplier, err.message);
    }
    refresh();
  }

  async function handleRemove(item) {
    clearVendorError(item.supplier);
    try {
      await deleteCartItem(item.id);
      refresh();
    } catch (err) {
      setVendorError(item.supplier, err.message);
    }
  }

  // Request / cancel / approve / reject a line's removal request.
  async function handleRemovalAction(item, action, reason) {
    setBusyLine(item.id);
    setError(null);
    clearVendorError(item.supplier);
    try {
      if (action === "request") await requestCartRemoval(item.id, reason);
      else if (action === "cancel") await cancelCartRemoval(item.id);
      else if (action === "approve") await approveCartRemoval(item.id);
      else if (action === "reject") await rejectCartRemoval(item.id);
      await quietRefresh();
    } catch (err) {
      setVendorError(item.supplier, err.message);
    } finally {
      setBusyLine(null);
    }
  }

  async function handleClearSupplier(supplier) {
    clearVendorError(supplier);
    try {
      await clearSupplierCart(supplier);
      setConfirmClearSupplier(null);
      setOpenVendors((prev) => setVendorOpen(prev, supplier, false));
      refresh();
    } catch (err) {
      setVendorError(supplier, err.message);
    }
  }

  async function handleMarkPlaced(supplier) {
    setMarkingPlacedSupplier(supplier);
    clearVendorError(supplier);
    try {
      await markOrderPlaced(supplier);
      setConfirmPlacedSupplier(null);
      // Its section is gone; next time it has items it starts collapsed.
      setOpenVendors((prev) => setVendorOpen(prev, supplier, false));
      refresh();
      setRecentOrdersRefreshKey((k) => k + 1);
    } catch (err) {
      setVendorError(supplier, err.message);
    } finally {
      setMarkingPlacedSupplier(null);
    }
  }

  // The PO went out: the server already recorded it as placed and took its
  // lines out of the cart.
  function handlePoSent(send) {
    const supplier = send?.supplier || sendPoSupplier;
    setSendPoSupplier(null);
    setNotice({ text: sentNotice(send), warn: send?.status !== "sent" || !send?.placed_order_id });
    clearVendorError(supplier);
    setOpenVendors((prev) => setVendorOpen(prev, supplier, false));
    refresh();
    setRecentOrdersRefreshKey((k) => k + 1);
  }

  // From the Send dialog after an unconfirmed send that did go out.
  async function handlePoMarkPlaced(supplier) {
    await markOrderPlaced(supplier);
    setSendPoSupplier(null);
    setNotice({ text: `Recorded ${supplier}'s order as placed and took it out of the cart.`, warn: false });
    setOpenVendors((prev) => setVendorOpen(prev, supplier, false));
    refresh();
    setRecentOrdersRefreshKey((k) => k + 1);
  }

  async function handleDownload(supplierRow) {
    // The PDF only ever gets name/SKU/qty (cases) - cost and the estimate
    // are a cart-side planning aid, not something to hand the vendor.
    const toOrder = supplierRow.items
      .filter((i) => Number(i.qty) > 0)
      // case_of: the PDF and downloads show Cases / Units per case for a case line.
      .map((i) => ({
        name: i.name,
        supplier_item_id: i.supplier_item_id || null,
        qty: Number(i.qty),
        case_of: Number(i.case_of) || 1,
      }));
    if (toOrder.length === 0) return;
    setExportingSupplier(supplierRow.supplier);
    setError(null);
    clearVendorError(supplierRow.supplier);
    try {
      await exportPurchaseOrderPdf(supplierRow.supplier, toOrder);
    } catch (err) {
      setVendorError(supplierRow.supplier, err.message);
    } finally {
      setExportingSupplier(null);
    }
  }

  const grandEstimate = useMemo(() => {
    if (!cart) return { total: 0, hasAny: false, missing: 0 };
    let total = 0;
    let hasAny = false;
    let missing = 0;
    for (const s of cart.suppliers) {
      for (const i of s.items) {
        if (i.unit_cost != null) {
          total += i.unit_cost * i.qty * i.case_of;
          hasAny = true;
        } else {
          missing += 1;
        }
      }
    }
    return { total, hasAny, missing };
  }, [cart]);

  const vendorNames = useMemo(() => (cart ? cart.suppliers.map((s) => s.supplier) : []), [cart]);
  const openCount = vendorNames.filter((v) => openVendors.has(v)).length;
  const lineCount = cart ? cart.suppliers.reduce((n, s) => n + s.items.length, 0) : 0;

  // Only the first load replaces the page: a later refresh keeps the cart
  // (and the add-items form, with any list not added yet) on screen.
  if (loading && !cart) {
    return (
      <section className="card">
        <p className="muted">Loading cart…</p>
      </section>
    );
  }

  // The page's intro (one line + "? More") is in PurchaseOrderPage's header.
  return (
    <>
      <AddItemsForm
        cartSuppliers={cart?.suppliers || []}
        onRefresh={reloadCart}
        onAdded={handleAdded}
        onShowVendor={handleShowVendor}
      />

      <section className="card po-cart-card" aria-labelledby="po-cart-title">
        <div className="po-cart-head">
          <div className="po-cart-head-text">
            <h2 id="po-cart-title" className="po-cart-title">
              In the cart
            </h2>
            {cart && cart.suppliers.length > 0 && (
              <p className="muted po-cart-overview">
                {plural(cart.suppliers.length, "vendor")} · {plural(lineCount, "item")}
                {grandEstimate.hasAny && <> · est. {formatMoney(grandEstimate.total)}</>}
              </p>
            )}
            {projectionWindow && cart && cart.suppliers.length > 0 && (
              <p className="muted po-cart-proj-note">
                Each line shows its sales per week, on hand and the{" "}
                <Term k="suggested_order_qty">suggested qty</Term> for{" "}
                {projectionWindow.horizon_days === 7 ? "1 week" : `${projectionWindow.horizon_days} days`} (sales from
                the last {projectionWindow.lookback_days === 90 ? "3 months" : `${projectionWindow.lookback_days} days`}).
              </p>
            )}
          </div>
          {cart && cart.suppliers.length > 0 && (
            <div className="po-cart-bulk" role="group" aria-label="All vendors">
              <button
                type="button"
                className="link-button"
                disabled={openCount === vendorNames.length}
                onClick={() => setOpenVendors(new Set(vendorNames))}
              >
                Expand all
              </button>
              <button
                type="button"
                className="link-button"
                disabled={openCount === 0}
                onClick={() => setOpenVendors(new Set())}
              >
                Collapse all
              </button>
            </div>
          )}
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <div className={`pos-notice${notice.warn ? " is-warn" : ""}`} role="status">
            <span>{notice.text}</span>
            <button type="button" className="link-button" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}
        {linkNote && <p className="muted po-cart-linknote">{linkNote}</p>}

        {cart && !canRemove && cart.suppliers.length > 0 && (
          <p className="muted po-removal-note">
            Only an admin can remove items from the cart. Use <strong>Request removal</strong> on a line and an
            admin will approve or reject it. <TermInfo k="cart_removal_admin_only" label="admin-only removal" />
          </p>
        )}

        {(!cart || cart.suppliers.length === 0) && (
          <p className="muted po-cart-empty">
            The cart is empty. Add items above, or from Supplier Projection or Reorder Candidates.
          </p>
        )}

        {cart && cart.suppliers.length > 0 && (
          <div className="po-vendors">
            {cart.suppliers.map((supplierRow) => {
              const supplier = supplierRow.supplier;
              return (
                <VendorPanel
                  key={supplier}
                  supplierRow={supplierRow}
                  open={openVendors.has(supplier)}
                  onToggle={() => toggleVendor(supplier)}
                  pulse={pulse?.supplier === supplier ? pulse.n : 0}
                  flash={flash?.supplier === supplier ? flash : null}
                  vendorError={vendorErrors[supplier] || null}
                  onFieldChange={handleFieldChange}
                  onSwitchCases={handleSwitchCases}
                  projections={projections}
                  onFieldBlur={(value) => {
                    const n = Number(value);
                    if (value === "" || !Number.isFinite(n) || (n < 1 && !canRemove)) refresh();
                  }}
                  onRemove={handleRemove}
                  onDownload={() => handleDownload(supplierRow)}
                  onSendPo={() => {
                    setNotice(null);
                    setSendPoSupplier(supplier);
                  }}
                  exporting={exportingSupplier === supplier}
                  confirmingClear={confirmClearSupplier === supplier}
                  onRequestClear={() => setConfirmClearSupplier(supplier)}
                  onCancelClear={() => setConfirmClearSupplier(null)}
                  onConfirmClear={() => handleClearSupplier(supplier)}
                  confirmingPlaced={confirmPlacedSupplier === supplier}
                  markingPlaced={markingPlacedSupplier === supplier}
                  onRequestPlaced={() => setConfirmPlacedSupplier(supplier)}
                  onCancelPlaced={() => setConfirmPlacedSupplier(null)}
                  onConfirmPlaced={() => handleMarkPlaced(supplier)}
                  canEditSections={canEditSections}
                  sectionNames={sectionNames}
                  onSectionSaved={quietRefresh}
                  onError={(message) => setVendorError(supplier, message)}
                  canRemove={canRemove}
                  userId={user?.id}
                  busyLine={busyLine}
                  onRemovalAction={handleRemovalAction}
                />
              );
            })}
          </div>
        )}

        {cart && cart.suppliers.length > 0 && (
          <div className="po-cart-grand-total">
            <strong>Total PO estimate across every supplier: {formatMoney(grandEstimate.total)}</strong>
            {grandEstimate.missing > 0 && (
              <span className="muted">
                {" "}
                ({grandEstimate.missing} item{grandEstimate.missing === 1 ? "" : "s"} without a known cost, not
                included)
              </span>
            )}
          </div>
        )}
      </section>

      <RecentPlacedOrders refreshKey={recentOrdersRefreshKey} />

      {sendPoSupplier && (
        <SendPoDialog
          supplier={sendPoSupplier}
          isAdmin={!!user?.is_admin}
          isDemo={!!user?.demo}
          onClose={() => setSendPoSupplier(null)}
          onSent={handlePoSent}
          onMarkPlaced={handlePoMarkPlaced}
        />
      )}
    </>
  );
}

// The latest confirmed orders (PlacedOrder rows with status "placed" - a
// downloaded PDF alone isn't listed here). The full history, with filters,
// is the Placed Orders page.
function RecentPlacedOrders({ refreshKey }) {
  const [orders, setOrders] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getRecentPlacedOrders(10)
      .then((res) => {
        if (!cancelled) setOrders(res.items);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <section className="card" style={{ marginTop: "1.1rem" }}>
      <div className="sp-toolbar" style={{ marginTop: 0 }}>
        <h2 style={{ margin: 0, fontSize: "1.05rem" }}>
          Recent Orders Placed <TermInfo k="placed_status_placed" label="placed" />
        </h2>
        <Link to="/placed-orders">See all placed orders</Link>
      </div>
      {error && <p className="error">{error}</p>}
      {orders === null && !error && <p className="muted">Loading…</p>}
      {orders && orders.length === 0 && <p className="muted">No orders placed yet.</p>}
      {orders && orders.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Supplier</th>
                <th style={{ textAlign: "right" }}>Items</th>
                <th>Placed by</th>
                <th>Placed at</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>{o.supplier}</td>
                  <td style={{ textAlign: "right" }}>{o.item_count}</td>
                  <td>{o.placed_by_name || "-"}</td>
                  <td>{formatRestaurantTime(o.placed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// One vendor's panel: a header that toggles the lines (button with
// aria-expanded / aria-controls - Enter and Space work as on any button),
// with Order placed and Download PDF beside it so they work while it's
// collapsed. The lines table is only rendered while open; its container
// always exists, so aria-controls always points at something.
function VendorPanel({
  supplierRow,
  open,
  onToggle,
  pulse,
  flash,
  vendorError,
  onFieldChange,
  onSwitchCases,
  onFieldBlur,
  onRemove,
  onDownload,
  onSendPo,
  exporting,
  confirmingClear,
  onRequestClear,
  onCancelClear,
  onConfirmClear,
  confirmingPlaced,
  markingPlaced,
  onRequestPlaced,
  onCancelPlaced,
  onConfirmPlaced,
  canEditSections,
  sectionNames,
  onSectionSaved,
  onError,
  canRemove,
  userId,
  busyLine,
  onRemovalAction,
  projections = {},
}) {
  const supplier = supplierRow.supplier;
  const panelId = vendorDomId("po-vendor", supplier);
  const bodyId = `${panelId}-lines`;
  const summary = useMemo(() => vendorSummary(supplierRow.items), [supplierRow.items]);
  const est = estimateNote(summary);
  // Each figure stays on one line ("1 item without a cost" never splits).
  const metaParts = [...summaryParts(summary), ...(est.missing && est.amount ? [est.missing] : [])];
  // Same order as the PDF: section headings A to Z, Other last, names sorted.
  const groups = useMemo(() => (open ? groupBySection(supplierRow.items) : []), [open, supplierRow.items]);

  return (
    <div
      id={panelId}
      className={`po-vendor${open ? " is-open" : ""}${pulse ? ` is-pulse-${pulse % 2}` : ""}`}
    >
      <div className="po-vendor-head">
        <button
          type="button"
          className="po-vendor-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={onToggle}
        >
          <ChevronIcon />
          <span className="po-vendor-title">
            <span className="po-vendor-name-line">
              <span className="po-vendor-name">{supplier}</span>
              {summary.removalRequests > 0 && (
                <span className="pill pill-warn po-vendor-flag">{plural(summary.removalRequests, "removal request")}</span>
              )}
            </span>
            <span className="po-vendor-meta">
              {metaParts.map((part, i) => (
                <Fragment key={i}>
                  {i > 0 && " "}
                  <span className="po-vendor-meta-part">
                    {part}
                    {i < metaParts.length - 1 && " ·"}
                  </span>
                </Fragment>
              ))}
            </span>
          </span>
          <span className="po-vendor-est">
            {est.amount ? (
              <>
                <span className="po-vendor-est-label">est.</span> {est.amount}
              </>
            ) : (
              <span className="po-vendor-est-none">{est.missing}</span>
            )}
          </span>
        </button>
        <div className="po-vendor-actions">
          {/* Who gets this vendor's PO (desktop; on a phone it's in the Send dialog and the panel foot). */}
          <Link className="link-button po-vendor-contacts" to={contactsLinkFor(supplier)}>
            Contacts
          </Link>
          {!confirmingPlaced && (
            <button type="button" className="link-button" onClick={onRequestPlaced}>
              Order placed
            </button>
          )}
          <button
            type="button"
            className="btn-secondary po-vendor-send"
            disabled={summary.orderable === 0}
            onClick={onSendPo}
            title={summary.orderable === 0 ? "Every line is at 0 - nothing to send" : `Email ${supplier} this PO`}
          >
            <MailIcon />
            <span>
              Send<span className="po-pdf-word"> PO</span>
            </span>
          </button>
          {/* Download with a file-type menu (owner, Oct 22): PDF keeps the
              old route (it also logs the PDF on Placed Orders); Excel and
              CSV are made from the same cart lines and aren't logged. */}
          <PoDownloadMenu
            target={{ kind: "cart", supplier }}
            supplier={supplier}
            disabled={summary.orderable === 0 || exporting}
            label={exporting ? "Building PDF…" : `Download (${summary.orderable})`}
            buttonClass="btn-secondary po-vendor-pdf"
            onDownload={(format) => (format === "pdf" ? onDownload() : downloadPo({ kind: "cart", supplier }, format, supplier))}
            onError={onError}
            testId="po-vendor-download"
          />
        </div>
      </div>

      {confirmingPlaced && (
        <div className="po-vendor-confirm" role="group" aria-label={`Order placed: ${supplier}`}>
          <span>Record this order as placed and clear the cart?</span>
          <span className="po-vendor-confirm-actions">
            <button type="button" className="link-button" onClick={onCancelPlaced} disabled={markingPlaced}>
              Cancel
            </button>
            <button type="button" className="po-confirm-yes" onClick={onConfirmPlaced} disabled={markingPlaced}>
              {markingPlaced ? "Saving…" : "Yes, order placed"}
            </button>
          </span>
        </div>
      )}
      {vendorError && (
        <p className="error po-vendor-error" role="alert">
          {vendorError}
        </p>
      )}

      <div id={bodyId} className="po-vendor-body" hidden={!open}>
        {open && (
          <>
            <div className="table-wrap po-vendor-table">
              <table>
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th className="po-line-item-th">Item</th>
                    <th className="num">Qty</th>
                    <th className="num">Case of</th>
                    <th className="num">Cost of</th>
                    <th className="num">Est. cost</th>
                    <th>
                      <span className="po-sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map(([section, lines]) => [
                    <tr key={`section-${section}`} className="po-cart-section-heading">
                      <td colSpan={7}>{section}</td>
                    </tr>,
                    ...lines.map((item) => {
                      const isNew = !!flash && isNewLine(flash.keys, item);
                      return (
                        <tr key={item.id} className={isNew ? `po-line-new po-line-new-${flash.n % 2}` : undefined}>
                          <td className="po-line-sku">{item.supplier_item_id || "-"}</td>
                          <td className="po-line-item">
                            <span className="po-line-name">{item.name}</span>
                            <span className="po-line-sub">
                              {item.supplier_item_id && (
                                <span className="po-line-sku-sm">SKU {item.supplier_item_id}</span>
                              )}
                              {/* Display only - never sent to the PDF (see handleDownload). */}
                              {cartContext(projections[item.id])?.text ? (
                                <LineProjection
                                  item={item}
                                  projection={projections[item.id]}
                                  onUse={(qty) => onFieldChange(item, "qty", String(qty))}
                                />
                              ) : (
                                <span className="po-cart-stock">In stock: {formatStock(item.on_hand_qty)}</span>
                              )}
                              {isNew && <span className="pill pill-info po-line-new-tag">Just added</span>}
                              <CartLineSection
                                line={item}
                                sections={sectionNames}
                                canEdit={canEditSections}
                                onSaved={onSectionSaved}
                                onError={onError}
                              />
                            </span>
                            {item.removal_requested && <RemovalPill request={item.removal_requested} />}
                          </td>
                          <td className="num">
                            <input
                              type="number"
                              min={canRemove ? "0" : "1"}
                              step="1"
                              className="po-qty-input"
                              value={item.qty}
                              aria-label={`Qty (cases) for ${item.name}`}
                              onChange={(e) => onFieldChange(item, "qty", e.target.value)}
                              onBlur={(e) => onFieldBlur(e.target.value)}
                            />
                            {caseLabel(item.qty, item.case_of) && (
                              <span className="po-case-label">{caseLabel(item.qty, item.case_of)}</span>
                            )}
                          </td>
                          <td className="num">
                            <input
                              type="number"
                              min="1"
                              step="1"
                              className="po-qty-input"
                              value={item.case_of}
                              aria-label={`Case of (units per case) for ${item.name}`}
                              onChange={(e) => onFieldChange(item, "case_of", e.target.value)}
                            />
                            {switchToCases(item) && onSwitchCases && (
                              <span className="po-case-label">
                                Comes in cases of {item.item_case_size} ·{" "}
                                <button type="button" className="link-button po-case-switch" onClick={() => onSwitchCases(item)}>
                                  Order in cases
                                </button>
                              </span>
                            )}
                          </td>
                          <td className="num">{item.unit_cost != null ? `$${item.unit_cost.toFixed(2)}` : "-"}</td>
                          <td className="num">
                            {item.unit_cost != null ? `$${(item.unit_cost * item.qty * item.case_of).toFixed(2)}` : "-"}
                          </td>
                          <td className="po-line-actions">
                            <LineRemoval
                              item={item}
                              canRemove={canRemove}
                              userId={userId}
                              busy={busyLine === item.id}
                              onRemove={() => onRemove(item)}
                              onAction={(action, reason) => onRemovalAction(item, action, reason)}
                            />
                          </td>
                        </tr>
                      );
                    }),
                  ])}
                </tbody>
              </table>
            </div>
            <div className="po-vendor-foot">
              <p className="muted po-cart-supplier-estimate">
                {est.amount ? (
                  <>
                    Estimated cost for {supplier}: <strong>{est.amount}</strong>
                    {summary.missingCost > 0 &&
                      ` (${summary.missingCost} item${summary.missingCost === 1 ? "" : "s"} without a known cost, not included)`}
                  </>
                ) : (
                  // Not "$0.00": nothing here has a cost on file.
                  `No cost estimate for ${supplier} - none of its items has a cost on file.`
                )}
              </p>
              <Link className="link-button po-vendor-foot-contacts" to={contactsLinkFor(supplier)}>
                Vendor contacts
              </Link>
              {canRemove &&
                (confirmingClear ? (
                  <span className="po-vendor-clear" role="group" aria-label={`Clear ${supplier}'s cart`}>
                    <span className="muted">Remove all {plural(supplierRow.items.length, "item")}?</span>
                    <button type="button" className="link-button" onClick={onCancelClear}>
                      Cancel
                    </button>
                    <button type="button" className="po-confirm-danger" onClick={onConfirmClear}>
                      Yes, clear
                    </button>
                  </span>
                ) : (
                  <button type="button" className="link-button link-danger" onClick={onRequestClear}>
                    Clear this supplier's cart
                  </button>
                ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ChevronIcon() {
  return (
    <svg className="po-vendor-chevron" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg className="po-btn-icon" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M2.5 4h11v8h-11zM2.75 4.4 8 8.75l5.25-4.35"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// "Removal requested by X" on a cart line (any viewer).
function RemovalPill({ request }) {
  const when = request.at ? formatRestaurantTime(request.at) : null;
  return (
    <Term k="cart_removal_requested" label="removal requested">
      <span
        className="pill pill-warn po-removal-pill"
        title={[when && `Requested ${when}`, request.reason && `Reason: ${request.reason}`].filter(Boolean).join(" · ") || undefined}
      >
        Removal requested{request.by ? ` by ${request.by}` : ""}
        {request.reason ? `: ${request.reason}` : ""}
      </span>
    </Term>
  );
}

// The line's last column. Admin: Remove, plus Approve / Reject when someone
// asked. Everyone else: Request removal (optional reason), or Cancel request
// on their own request.
function LineRemoval({ item, canRemove, userId, busy, onRemove, onAction }) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const req = item.removal_requested;

  if (canRemove) {
    return (
      <span className="po-removal-actions">
        {req && (
          <>
            <button className="link-button" disabled={busy} onClick={() => onAction("approve")}>
              Approve removal
            </button>
            <button className="link-button" disabled={busy} onClick={() => onAction("reject")}>
              Reject
            </button>
          </>
        )}
        <button className="link-button link-danger" disabled={busy} onClick={onRemove}>
          Remove
        </button>
      </span>
    );
  }
  if (req) {
    return req.by_user_id != null && req.by_user_id === userId ? (
      <button className="link-button" disabled={busy} onClick={() => onAction("cancel")}>
        Cancel request
      </button>
    ) : (
      <span className="muted" style={{ fontSize: "0.8rem" }}>Waiting for an admin</span>
    );
  }
  if (asking) {
    return (
      <form
        className="po-removal-form"
        onSubmit={(e) => {
          e.preventDefault();
          onAction("request", reason.trim() || null);
          setAsking(false);
          setReason("");
        }}
      >
        <input
          type="text"
          autoFocus
          maxLength={300}
          placeholder="Reason (optional)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-label="Reason for removal (optional)"
        />
        <button type="submit" disabled={busy}>
          Send request
        </button>
        <button type="button" className="link-button" onClick={() => setAsking(false)}>
          Cancel
        </button>
      </form>
    );
  }
  return (
    <button className="link-button" disabled={busy} onClick={() => setAsking(true)}>
      Request removal
    </button>
  );
}

// X1: a cart line's projection context - "~14/week · suggested 10 · on hand
// 6" (the full basis on hover / focus, and in the (i)) and, when the
// suggestion differs from the line's qty, "Use 10" to set it explicitly.
// Display only, like "In stock" - never sent to the PDF.
function LineProjection({ item, projection, onUse }) {
  const ctx = cartContext(projection);
  const use = useSuggestedQty(projection, item.qty);
  return (
    <span className="po-line-proj">
      <span className="po-line-proj-text" title={ctx.basis || undefined}>
        {ctx.text}
      </span>
      <TermInfo k="suggested_order_qty" label="Suggested order qty" />
      {use != null && (
        <button
          type="button"
          className="link-button po-line-proj-use"
          onClick={() => onUse(use)}
          title={ctx.basis || undefined}
          aria-label={`Set ${item.name} to the suggested ${use}`}
        >
          Use {use}
        </button>
      )}
    </span>
  );
}

// A cart line's "In stock: N": on hand from the latest upload, incl. the
// Container row (backend po_cart.stock_by_cart_item); "-" when unknown.
function formatStock(qty) {
  if (qty == null) return "-";
  return Number.isInteger(qty) ? String(qty) : qty.toFixed(1);
}
