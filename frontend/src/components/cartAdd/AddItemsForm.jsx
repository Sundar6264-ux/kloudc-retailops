import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { addToCart, getSupplierProjection, getSuppliers, searchItems } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { storeDateOffsetStr, storeTodayStr } from "../../format";
import {
  SUGGEST_HORIZON_DAYS,
  SUGGEST_LOOKBACK_DAYS,
  addOutcome,
  canStepQty,
  canonicalVendor,
  cartKeysFor,
  cartPayload,
  defaultQty,
  exactNameMatches,
  formatQty,
  inStockFor,
  isInCart,
  isNetworkError,
  norm,
  plural,
  projectionIndex,
  qtyProblem,
  splitPostable,
  splitVendors,
  stepQty,
  suggestion,
  tickRow,
  vendorOptions,
} from "./cartAddModel.js";
import { codeMatchLabel } from "../../productMatch.js";
import { currentPosName } from "../../store/gating.js";

// One vendor's projection is kept for 5 minutes (like the app's add sheet),
// so adding several lists in a row doesn't recompute it each time.
const PROJECTION_TTL_MS = 5 * 60 * 1000;
const projectionCache = new Map();

function loadProjection(supplier) {
  const hit = projectionCache.get(supplier);
  if (hit && Date.now() - hit.at < PROJECTION_TTL_MS) return hit.promise;
  const promise = getSupplierProjection(
    supplier,
    storeDateOffsetStr(-SUGGEST_LOOKBACK_DAYS),
    storeTodayStr(),
    SUGGEST_HORIZON_DAYS
  ).then((data) => projectionIndex(data, SUGGEST_HORIZON_DAYS));
  projectionCache.set(supplier, { at: Date.now(), promise });
  promise.catch(() => projectionCache.delete(supplier));
  return promise;
}

const MIN_QUERY = 2;
// GET /api/inventory/items/search returns at most this many matches.
const SEARCH_CAP = 25;
// Results listed before "Show all N" - whole rows only, never a box that
// scrolls inside the page (which cut the top row in half).
const RESULTS_SHOWN = 8;

// "Add items" on the Purchase Order Cart - the website's version of the
// app's add sheet, built for adding several items in one go:
// - pick the vendor once (a chip with "Change");
// - "This vendor's products | All products": the vendor's items, or the
//   whole catalog with each result's own vendor. Whatever is ticked goes
//   into the chosen vendor's cart, like the app;
// - ticked results collect in a "To add" table, each with a qty (the
//   suggested order for 1 week when there's a projection, else 1). The
//   search can change between ticks without losing the list;
// - the footer's "Add to <vendor>'s cart" sends the list in one request,
//   then clears it, keeping the vendor and the search mode for the next list;
// - an item that isn't in inventory yet can be typed in by hand ("Add it as
//   a new item") into the same list.
// Items already in the vendor's cart are greyed out and can't be ticked:
// re-adding one replaces the qty already on that cart line.
// `onRefresh` re-reads the cart and returns it (null when it couldn't): Add
// calls it first, so a page left open doesn't overwrite a line someone added
// meanwhile, and again after a failed add, to say exactly what got in.
// `onAdded(vendor, rows)` tells the page what reached the cart (it opens
// that vendor and highlights the lines); `onShowVendor(vendor)` is the
// message's "View in cart".
export default function AddItemsForm({ cartSuppliers = [], onRefresh, onAdded, onShowVendor }) {
  const { hasFeature } = useAuth();
  const canProject = hasFeature("supplier_projection");
  const uid = useId();
  const cartVendors = useMemo(() => cartSuppliers.map((s) => s.supplier), [cartSuppliers]);

  // --- Vendor ------------------------------------------------------------
  const [vendors, setVendors] = useState([]);
  const [vendorsLoaded, setVendorsLoaded] = useState(false);
  // The chosen vendor ("" while picking one). Starts as the cart's only
  // vendor when there is exactly one, as on the app.
  const [supplier, setSupplier] = useState(() => (cartVendors.length === 1 ? cartVendors[0] : ""));
  const [vendorText, setVendorText] = useState("");
  const [vendorOpen, setVendorOpen] = useState(false);
  const [vendorActive, setVendorActive] = useState(0);
  const [confirmChange, setConfirmChange] = useState(false);

  // --- Search --------------------------------------------------------------
  const [vendorOnly, setVendorOnly] = useState(true);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searchedFor, setSearchedFor] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [resultsOpen, setResultsOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);

  // --- Manual (not in inventory) -----------------------------------------
  const [manualMode, setManualMode] = useState(false);
  const [manualName, setManualName] = useState("");
  const [manualSku, setManualSku] = useState("");
  const [manualError, setManualError] = useState(null);
  // The typed name turned out to be an inventory item: { name, items }.
  const [manualMatch, setManualMatch] = useState(null);
  const [manualChecking, setManualChecking] = useState(false);
  // Bumped whenever the typed item is edited or the form is left, so an
  // inventory check still running for the old text is ignored.
  const manualCheckId = useRef(0);

  // --- To-add list / submit ---------------------------------------------
  const [toAdd, setToAdd] = useState([]);
  const [saving, setSaving] = useState(false);
  // Set synchronously, so a second click before the re-render can't send
  // the list twice.
  const savingRef = useRef(false);
  const [error, setError] = useState(null);
  const [added, setAdded] = useState(null);

  // Projection for the chosen vendor: { supplier, status, index }.
  const [projection, setProjection] = useState(null);

  // "How this works" (the long explanation, closed by default).
  const [howOpen, setHowOpen] = useState(false);

  const vendorRef = useRef(null);
  const searchRef = useRef(null);
  const manualNameRef = useRef(null);
  const resultsRef = useRef(null);
  const focusNext = useRef(null);
  // A result checkbox to focus once "Show all" has rendered it.
  const focusResultAt = useRef(null);
  const footerRef = useRef(null);
  const [footerStuck, setFooterStuck] = useState(false);

  // Moves focus once the element for the new state is on the page.
  useEffect(() => {
    if (focusNext.current) {
      const target = { vendor: vendorRef, search: searchRef, manual: manualNameRef }[focusNext.current];
      focusNext.current = null;
      target?.current?.focus();
    }
    if (focusResultAt.current != null) {
      const box = resultBoxes()[focusResultAt.current];
      focusResultAt.current = null;
      box?.focus();
    }
  });

  useEffect(() => {
    let cancelled = false;
    getSuppliers()
      .then((res) => {
        if (!cancelled) setVendors(res.suppliers || []);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setVendorsLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A vendor typed in that isn't in inventory has no projection to ask for
  // (an empty or failed vendor list doesn't count against it).
  const knownVendor = !vendorsLoaded || vendors.length === 0 || vendors.some((v) => v === supplier);

  // Sales projection for the chosen vendor (suggested qty + Need labels).
  useEffect(() => {
    if (!supplier || !canProject || !knownVendor) {
      setProjection(null);
      return undefined;
    }
    let cancelled = false;
    setProjection({ supplier, status: "loading", index: new Map() });
    loadProjection(supplier)
      .then((index) => {
        if (!cancelled) setProjection({ supplier, status: "ready", index });
      })
      .catch(() => {
        if (!cancelled) setProjection({ supplier, status: "error", index: new Map() });
      });
    return () => {
      cancelled = true;
    };
  }, [supplier, canProject, knownVendor]);

  const projIndex = projection?.supplier === supplier ? projection.index : null;
  const projReady = projection?.supplier === supplier && projection.status === "ready";
  const projLoading = projection?.supplier === supplier && projection.status === "loading";

  // Once the projection arrives, rows still at their starting qty take the
  // suggestion (a qty someone typed is never changed).
  useEffect(() => {
    if (!projReady || !projIndex) return;
    setToAdd((prev) => {
      let changed = false;
      const next = prev.map((r) => {
        if (!r.qtyAuto || !r.item_id || !projIndex.has(r.item_id)) return r;
        const qty = String(defaultQty(projIndex.get(r.item_id)));
        if (qty === r.qty) return r;
        changed = true;
        return { ...r, qty };
      });
      return changed ? next : prev;
    });
  }, [projReady, projIndex]);

  // Debounced search: the vendor's products, or the whole catalog.
  const q = query.trim();
  const searchKey = `${vendorOnly ? supplier : ""}|${q}`;
  useEffect(() => {
    if (!supplier || manualMode || q.length < MIN_QUERY) {
      setResults([]);
      setSearching(false);
      setSearchError(null);
      setSearchedFor(null);
      return undefined;
    }
    let cancelled = false;
    const handle = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await searchItems(q, vendorOnly ? supplier : undefined);
        if (cancelled) return;
        setResults(res.items || []);
        setSearchError(null);
      } catch (err) {
        if (cancelled) return;
        setResults([]);
        setSearchError(err.message);
      } finally {
        if (!cancelled) {
          setSearching(false);
          setSearchedFor(searchKey);
        }
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [q, supplier, vendorOnly, manualMode, searchKey]);

  // A new search starts with the short list again.
  useEffect(() => {
    setShowAll(false);
  }, [searchKey]);

  const inCartKeys = useMemo(() => cartKeysFor(cartSuppliers, supplier), [cartSuppliers, supplier]);
  const tickedIds = useMemo(() => new Set(toAdd.filter((r) => r.item_id).map((r) => r.item_id)), [toAdd]);

  // The whole-catalog list leaves out an item's bare Container row: its
  // count is already in that item's "in stock".
  const shown = useMemo(
    () => (vendorOnly ? results : results.filter((r) => !r.container_of)),
    [results, vendorOnly]
  );
  const searchDone = searchedFor === searchKey && !searching;
  const noMatches = searchDone && !searchError && q.length >= MIN_QUERY && shown.length === 0;
  const showArea = resultsOpen && q.length >= MIN_QUERY;
  // The previous query's results stay listed while the next search runs.
  const listVisible = showArea && shown.length > 0;
  const visibleResults = showAll ? shown : shown.slice(0, RESULTS_SHOWN);
  const hiddenCount = shown.length - visibleResults.length;
  // "All products" lists other vendors' items, which have no code for the
  // chosen vendor - a column of dashes is left out.
  const showSku = vendorOnly || visibleResults.some((r) => projIndex?.get(r.item_id)?.sku || r.supplier_item_id);

  // --- Vendor actions ----------------------------------------------------
  const options = useMemo(
    () => vendorOptions(vendorText, vendors, cartVendors, 20, vendorsLoaded),
    [vendorText, vendors, cartVendors, vendorsLoaded]
  );
  // Inventory's spelling wins over a cart section's.
  const allVendors = useMemo(() => [...vendors, ...cartVendors], [vendors, cartVendors]);

  function chooseVendor(name) {
    const chosen = canonicalVendor(name, allVendors) || (name || "").trim();
    if (!chosen) return;
    setSupplier(chosen);
    setVendorText("");
    setVendorOpen(false);
    setVendorActive(0);
    setQuery("");
    setResults([]);
    setResultsOpen(true);
    setManualMode(false);
    setManualError(null);
    setManualMatch(null);
    setAdded(null);
    setError(null);
    focusNext.current = "search";
  }

  function changeVendor() {
    // Never while an add is running: its rows are still in the list, and
    // the list belongs to the vendor it's being added to.
    if (savingRef.current) return;
    setConfirmChange(false);
    setToAdd([]);
    setSupplier("");
    setVendorText("");
    setQuery("");
    setResults([]);
    setManualMode(false);
    setManualError(null);
    setManualMatch(null);
    setAdded(null);
    setError(null);
    focusNext.current = "vendor";
  }

  // Switches between "This vendor's products" and "All products". The other
  // mode's results are dropped at once, so they're never listed (or ticked)
  // under the wrong heading while the new search runs.
  function setMode(only) {
    if (only === vendorOnly) return;
    setVendorOnly(only);
    setResults([]);
    setSearchedFor(null);
    setResultsOpen(true);
  }

  // Radio-group keys for the two search modes: any arrow switches, and focus
  // follows the checked one (only it is in the Tab order).
  function onScopeKeyDown(e) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const next = !vendorOnly;
    setMode(next);
    e.currentTarget.querySelector(`[data-scope="${next ? "vendor" : "all"}"]`)?.focus();
  }

  function onVendorKeyDown(e) {
    const open = vendorOpen && options.length > 0;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setVendorOpen(true);
      setVendorActive((i) => (open ? Math.min(i + 1, options.length - 1) : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setVendorActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && options[vendorActive]) chooseVendor(options[vendorActive].name);
      // What's typed, as a vendor not in inventory - once the list has
      // loaded, so it isn't just a vendor that hasn't arrived yet.
      else if (vendorText.trim() && vendorsLoaded) chooseVendor(vendorText);
    } else if (e.key === "Escape" && vendorOpen) {
      e.preventDefault();
      setVendorOpen(false);
    }
  }

  // --- Ticking -------------------------------------------------------------
  function rowFor(item) {
    const proj = projIndex?.get(item.item_id);
    return {
      key: `item:${item.item_id}`,
      item_id: item.item_id,
      name: item.name,
      supplier_item_id: proj?.sku || item.supplier_item_id || null,
      vendors: item.supplier || "",
      result: item,
      qty: String(defaultQty(proj)),
      qtyAuto: true,
      manual: false,
    };
  }

  function toggle(item) {
    if (isInCart(inCartKeys, item.item_id, item.name)) return;
    setAdded(null);
    setToAdd((prev) => tickRow(prev, rowFor(item)));
  }

  function resultBoxes() {
    return resultsRef.current ? [...resultsRef.current.querySelectorAll('input[type="checkbox"]:not(:disabled)')] : [];
  }

  function onSearchKeyDown(e) {
    if (e.key === "Escape") {
      if (resultsOpen) {
        e.preventDefault();
        setResultsOpen(false);
      }
    } else if (e.key === "ArrowDown") {
      if (!listVisible) {
        setResultsOpen(true);
        return;
      }
      const boxes = resultBoxes();
      if (boxes.length) {
        e.preventDefault();
        boxes[0].focus();
      }
    } else if (e.key === "Enter") {
      // Enter ticks the top match (the first one not already in the cart) -
      // only once the results are for what's typed now.
      e.preventDefault();
      if (!listVisible || !searchDone) return;
      const top = shown.find((r) => !isInCart(inCartKeys, r.item_id, r.name));
      if (top && !tickedIds.has(top.item_id)) toggle(top);
    }
  }

  function onResultKeyDown(e, item) {
    if (e.key === "Enter") {
      e.preventDefault();
      toggle(item);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const boxes = resultBoxes();
      const i = boxes.indexOf(e.currentTarget);
      const next = e.key === "ArrowDown" ? i + 1 : i - 1;
      if (next < 0) searchRef.current?.focus();
      else if (next < boxes.length) boxes[next].focus();
      else if (hiddenCount > 0) {
        // Past the last row shown: list the rest and carry on into them.
        focusResultAt.current = next;
        setShowAll(true);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      // Focus first: the search box's onFocus opens the results again, so
      // closing them has to come after it.
      searchRef.current?.focus();
      setResultsOpen(false);
    }
  }

  // --- Manual items --------------------------------------------------------
  function startManual() {
    setManualMode(true);
    setManualName(q);
    setManualSku("");
    setManualError(null);
    setManualMatch(null);
    focusNext.current = "manual";
  }

  function leaveManual() {
    manualCheckId.current += 1;
    setManualChecking(false);
    setManualMode(false);
    setManualName("");
    setManualSku("");
    setManualError(null);
    setManualMatch(null);
    focusNext.current = "search";
  }

  // "Show it in the search": back to the search, for the name typed, in
  // whichever mode lists the inventory item.
  function showMatchInSearch() {
    const match = manualMatch;
    if (!match) return;
    const vendorHasIt = match.items.some((i) => splitVendors(i.supplier).includes(supplier));
    leaveManual();
    if (!vendorHasIt && vendorOnly) setMode(false);
    setQuery(match.name);
    setResultsOpen(true);
  }

  // `asNew`: add it as typed even though inventory has an item by that name.
  async function addManual(asNew = false) {
    const name = manualName.trim();
    if (!name || manualChecking) return;
    const key = norm(name);
    if (toAdd.some((r) => norm(r.name) === key)) {
      setManualError(`"${name}" is already in your list.`);
      return;
    }
    if (isInCart(inCartKeys, null, name)) {
      setManualError(`"${name}" is already in ${supplier}'s cart - change its qty in the cart below.`);
      return;
    }
    if (!asNew) {
      // A product that IS in inventory should be ticked from the search -
      // as a typed "new item" it would reach the PO with no SKU, cost or
      // stock, and could go in a second time once it's ticked.
      const checkId = ++manualCheckId.current;
      setManualChecking(true);
      let matches = [];
      try {
        matches = exactNameMatches((await searchItems(name)).items, name);
      } catch {
        // Can't check right now - it's added as typed.
      }
      // Edited, or "back to search" clicked, while the check ran.
      if (checkId !== manualCheckId.current) return;
      setManualChecking(false);
      if (matches.length) {
        setManualMatch({ name, items: matches });
        return;
      }
    }
    setAdded(null);
    setToAdd((prev) => [
      ...prev,
      {
        key: `new:${key}`,
        item_id: null,
        name,
        supplier_item_id: manualSku.trim() || null,
        vendors: "",
        result: null,
        qty: "1",
        // "Still at its starting qty" - ticking the inventory item of the
        // same name then takes that item's own starting qty (tickRow).
        qtyAuto: true,
        manual: true,
      },
    ]);
    setQuery("");
    leaveManual();
  }

  // --- To-add list ---------------------------------------------------------
  function setRowQty(key, qty) {
    setToAdd((prev) => prev.map((r) => (r.key === key ? { ...r, qty, qtyAuto: false } : r)));
  }

  function removeRow(key) {
    setToAdd((prev) => prev.filter((r) => r.key !== key));
  }

  const { post: postable, skipped: listedInCart } = useMemo(() => splitPostable(toAdd, inCartKeys), [toAdd, inCartKeys]);
  const badQty = postable.some((r) => qtyProblem(r.qty));
  const canSubmit = !!supplier && postable.length > 0 && !badQty && !saving;

  // The footer bar sticks to the bottom of the screen while the list is
  // longer than the screen, with a shadow while stuck. bottom: -1px in the
  // CSS makes "stuck" read as "not fully visible".
  const hasList = toAdd.length > 0;
  useEffect(() => {
    const el = footerRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver(([entry]) => setFooterStuck(entry.intersectionRatio < 1), { threshold: [1] });
    io.observe(el);
    return () => {
      io.disconnect();
      setFooterStuck(false);
    };
  }, [hasList]);

  function clearSearchAfterAdd() {
    setQuery("");
    setResults([]);
    setResultsOpen(true);
    focusNext.current = "search";
  }

  async function handleSubmit() {
    if (!canSubmit || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError(null);
    setAdded(null);
    const vendor = supplier;
    // The rows listed when Add was clicked; anything ticked while the add
    // runs stays in the list for the next one.
    const listed = toAdd;
    const dropFromList = (rows) => {
      const keys = new Set(rows.map((r) => r.key));
      setToAdd((prev) => prev.filter((r) => !keys.has(r.key)));
    };
    let sent = null;
    try {
      // Re-read the cart first: if the page has been open a while, the phone
      // or someone else may have added one of these to the vendor's cart
      // since - sending it again would replace that line's qty.
      const before = await onRefresh?.();
      const keys = before ? cartKeysFor(before.suppliers, vendor) : inCartKeys;
      const { post, skipped: inCartNow } = splitPostable(listed, keys);
      if (post.length === 0) {
        setAdded({ count: 0, supplier: vendor, skipped: inCartNow.length });
        dropFromList(listed);
        onAdded?.(vendor, []);
        return;
      }
      sent = post;
      await addToCart(vendor, cartPayload(post));
      setAdded({ count: post.length, supplier: vendor, skipped: inCartNow.length });
      dropFromList(listed);
      clearSearchAfterAdd();
      onAdded?.(vendor, post);
      // Not awaited: the form is free again at once; the cart below (and the
      // greying in the search) follow when the re-read lands.
      onRefresh?.();
    } catch (err) {
      if (!sent) {
        setError(`Couldn't add the items: ${err.message}`);
        return;
      }
      // The add failed, or its answer never came back (the server may have
      // added them anyway): re-read the cart and say exactly what's in it.
      const reason = isNetworkError(err) ? "the connection to the server dropped" : err.message.replace(/[.\s]+$/, "");
      const after = await onRefresh?.();
      if (!after) {
        setError(
          `Couldn't add the items (${reason}), and the cart can't be checked right now, so it isn't known ` +
            `whether they reached ${vendor}'s cart. Reload the page and check the cart before adding them again.`
        );
        return;
      }
      const { inCart, missing } = addOutcome(sent, cartKeysFor(after.suppliers, vendor));
      // Some did get in: the cart badge only refreshes on this event, which
      // addToCart fires on a success only.
      if (inCart.length > 0) window.dispatchEvent(new Event("cart:updated"));
      if (missing.length === 0) {
        // Every one is in the cart: the add went through, only the answer got lost.
        setAdded({ count: sent.length, supplier: vendor, skipped: 0, checked: true });
        dropFromList(listed);
        clearSearchAfterAdd();
        onAdded?.(vendor, sent);
      } else if (inCart.length === 0) {
        setError(`Couldn't add the items: ${reason}. Nothing was added - your list is still here.`);
      } else {
        dropFromList(inCart);
        onAdded?.(vendor, inCart);
        setError(
          `Only ${inCart.length} of the ${plural(sent.length, "item")} reached ${vendor}'s cart (${reason}). ` +
            `The other ${missing.length} ${missing.length === 1 ? "is" : "are"} still in your list - add ${missing.length === 1 ? "it" : "them"} again.`
        );
      }
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  // --- Render ----------------------------------------------------------------
  const titleId = `${uid}-title`;
  const vendorInputId = `${uid}-vendor`;
  const vendorListId = `${uid}-vendors`;
  const resultsId = `${uid}-results`;
  const hintId = `${uid}-hint`;
  const howId = `${uid}-how`;
  const listTitleId = `${uid}-list-title`;
  const countId = `${uid}-count`;
  const searchLabel = vendorOnly ? `Search ${supplier} products` : "Search all products";

  return (
    <section className="card po-add-card" aria-labelledby={titleId}>
      <div className="po-add-head">
        <h2 id={titleId} className="po-add-title">
          Add items
        </h2>

        {!supplier ? (
          <div className="po-add-vendor-field">
            <label className="po-add-label" htmlFor={vendorInputId}>
              Vendor
            </label>
            <div className="item-picker po-add-vendor-picker">
              <input
                id={vendorInputId}
                ref={vendorRef}
                type="text"
                role="combobox"
                aria-expanded={vendorOpen && options.length > 0}
                aria-controls={vendorOpen && options.length > 0 ? vendorListId : undefined}
                aria-autocomplete="list"
                aria-activedescendant={
                  vendorOpen && options[vendorActive] ? `${vendorListId}-${vendorActive}` : undefined
                }
                value={vendorText}
                placeholder="Start typing a vendor name"
                autoComplete="off"
                onChange={(e) => {
                  setVendorText(e.target.value);
                  setVendorOpen(true);
                  setVendorActive(0);
                }}
                onFocus={() => setVendorOpen(true)}
                onBlur={() => {
                  setVendorOpen(false);
                  // A known vendor typed out in full counts as picked.
                  const exact = canonicalVendor(vendorText, allVendors);
                  if (exact) chooseVendor(exact);
                }}
                onKeyDown={onVendorKeyDown}
              />
              {vendorOpen && options.length > 0 && (
                <ul id={vendorListId} role="listbox" aria-label="Vendors" className="item-picker-results po-vendor-options">
                  {options.map((o, i) => (
                    <li
                      key={`${o.isNew ? "new" : "v"}:${o.name}`}
                      id={`${vendorListId}-${i}`}
                      role="option"
                      aria-selected={i === vendorActive}
                      className={`${i === vendorActive ? "is-active" : ""}${o.isNew ? " po-vendor-new" : ""}`}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        chooseVendor(o.name);
                      }}
                      onMouseEnter={() => setVendorActive(i)}
                    >
                      {o.isNew ? (
                        <>
                          Use "<strong>{o.name}</strong>" - not a vendor in inventory yet
                        </>
                      ) : (
                        o.name
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : (
          <div className="po-add-vendor-field po-add-vendor">
            <span className="po-add-label">Vendor</span>
            <span className={`po-add-vendor-chip${knownVendor ? "" : " is-new"}`}>
              <span className="po-add-vendor-name">{supplier}</span>
            </span>
            {!knownVendor && <span className="po-add-vendor-new">Not a vendor in inventory</span>}
            {!confirmChange && (
              <button
                type="button"
                className="link-button"
                disabled={saving}
                title={saving ? "Wait until the add finishes" : undefined}
                onClick={() => (toAdd.length > 0 ? setConfirmChange(true) : changeVendor())}
              >
                Change
              </button>
            )}
          </div>
        )}

        <button
          type="button"
          className="info-toggle po-add-how-toggle"
          aria-expanded={howOpen}
          aria-controls={howId}
          onClick={() => setHowOpen((v) => !v)}
        >
          {howOpen ? "How this works ▲" : "How this works"}
        </button>
      </div>

      {!supplier && (
        <p className="po-add-help muted">
          {vendorsLoaded ? "Pick the vendor first - everything you tick goes into its cart." : "Loading vendors…"}
        </p>
      )}

      <div id={howId} className="info-block-full muted po-add-how" hidden={!howOpen}>
        <ul>
          <li>Pick the vendor once - everything you tick goes into its cart.</li>
          <li>
            <strong>This vendor's products</strong> searches what {currentPosName()} lists under that vendor;{" "}
            <strong>All products</strong> searches everything, and whatever you tick still goes into the vendor you
            picked (the same as the app).
          </li>
          <li>
            Tick as many as you need. They collect in <strong>To add</strong> - change the search or the list you're
            searching and keep ticking.
          </li>
          <li>
            Each qty starts at the suggested order for 1 week ({SUGGEST_LOOKBACK_DAYS} days of sales minus what's in
            stock, the same as the app), or 1. A qty you type or step is kept.
          </li>
          <li>Greyed items are already in the vendor's cart - change their qty in the cart below.</li>
          <li>
            Not in inventory yet? Search for it, then <strong>Add it as a new item</strong>.
          </li>
          <li>Keyboard: Enter ticks the top match, ↓ moves into the results, Esc closes them.</li>
        </ul>
        <p className="po-add-how-more">
          The full guide is on the <Link to="/help">Help</Link> page (Supplier Projection, "Purchase Order Cart").
        </p>
      </div>

      {confirmChange && (
        <div className="po-add-confirm" role="group" aria-label="Change vendor">
          <span>
            Your list has {plural(toAdd.length, "item")} not added yet. Discard {toAdd.length === 1 ? "it" : "them"} and
            pick another vendor?
          </span>
          <span className="po-add-confirm-actions">
            <button type="button" className="link-button" onClick={() => setConfirmChange(false)}>
              Keep the list
            </button>
            <button type="button" className="link-button link-danger" disabled={saving} onClick={changeVendor}>
              Discard and change
            </button>
          </span>
        </div>
      )}

      {supplier && (
        <>
          {!manualMode && (
            <div className="po-add-search">
              <div className="po-add-toolbar">
                <div
                  className="range-pills po-add-scope"
                  role="radiogroup"
                  aria-label="Search in"
                  onKeyDown={onScopeKeyDown}
                >
                  <button
                    type="button"
                    role="radio"
                    data-scope="vendor"
                    aria-checked={vendorOnly}
                    tabIndex={vendorOnly ? 0 : -1}
                    className={`range-pill${vendorOnly ? " active" : ""}`}
                    onClick={() => setMode(true)}
                  >
                    This vendor's products
                  </button>
                  <button
                    type="button"
                    role="radio"
                    data-scope="all"
                    aria-checked={!vendorOnly}
                    tabIndex={vendorOnly ? -1 : 0}
                    className={`range-pill${vendorOnly ? "" : " active"}`}
                    onClick={() => setMode(false)}
                  >
                    All products
                  </button>
                </div>
                <div className="po-add-searchbox">
                  <SearchIcon />
                  <input
                    ref={searchRef}
                    type="text"
                    value={query}
                    placeholder={`Search name, SKU, barcode or ${currentPosName()} ID`}
                    aria-label={searchLabel}
                    aria-describedby={hintId}
                    aria-controls={listVisible ? resultsId : undefined}
                    autoComplete="off"
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setResultsOpen(true);
                    }}
                    onFocus={() => setResultsOpen(true)}
                    onKeyDown={onSearchKeyDown}
                  />
                </div>
              </div>
              <span id={hintId} className="po-sr-only">
                Enter ticks the top match, down arrow moves into the results, Escape closes them.
              </span>
              {!vendorOnly && (
                <p className="po-add-note">
                  Ticked items go into <strong>{supplier}</strong>'s cart, whichever vendor they're listed under.
                </p>
              )}

              {/* One status line for the search (no jump between "Searching…" and
                  the result count); the last results stay listed until new ones
                  arrive, so the To-add list below doesn't move while typing. */}
              {showArea && (
                <div className="po-add-status" role="status">
                  {!searchDone ? (
                    "Searching…"
                  ) : searchError ? (
                    <span className="po-add-status-error">Search failed: {searchError}</span>
                  ) : noMatches ? (
                    <>
                      No matches for "{q}" {vendorOnly ? `under ${supplier}` : "in inventory"}.{" "}
                      {vendorOnly && (
                        <>
                          <button type="button" className="link-button" onClick={() => setMode(false)}>
                            Search all products
                          </button>{" "}
                          or{" "}
                        </>
                      )}
                      <button type="button" className="link-button" onClick={startManual}>
                        Add it as a new item →
                      </button>
                    </>
                  ) : (
                    <>
                      {shown.length} {shown.length === 1 ? "match" : "matches"}
                      {results.length >= SEARCH_CAP && ` (the first ${SEARCH_CAP} - type more to narrow it down)`}
                    </>
                  )}
                </div>
              )}

              {listVisible && (
                <div className={`po-add-results-box${vendorOnly ? "" : " is-all"}${showSku ? "" : " no-sku"}`}>
                  <div className="po-add-cols po-add-results-head" aria-hidden="true">
                    <span />
                    <span>Item</span>
                    {!vendorOnly && <span>Vendor</span>}
                    {showSku && <span>SKU</span>}
                    <span className="po-col-num">In stock</span>
                    {vendorOnly && <span className="po-col-num">Per week</span>}
                    <span className="po-col-end">Status</span>
                  </div>
                  <ul id={resultsId} ref={resultsRef} className="po-add-results" aria-label={`Results for "${q}"`}>
                    {visibleResults.map((item) => (
                      <ResultRow
                        key={item.item_id}
                        item={item}
                        proj={projIndex?.get(item.item_id)}
                        inCart={isInCart(inCartKeys, item.item_id, item.name)}
                        ticked={tickedIds.has(item.item_id)}
                        showVendors={!vendorOnly}
                        showSku={showSku}
                        onToggle={() => toggle(item)}
                        onKeyDown={(e) => onResultKeyDown(e, item)}
                      />
                    ))}
                  </ul>
                  {shown.length > RESULTS_SHOWN && (
                    <button
                      type="button"
                      className="po-add-more"
                      aria-expanded={showAll}
                      aria-controls={resultsId}
                      onClick={() => setShowAll((v) => !v)}
                    >
                      {showAll ? "Show fewer" : `Show all ${shown.length}`}
                      <ChevronIcon up={showAll} />
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {manualMode && (
            <div className="po-add-manual" role="group" aria-labelledby={`${uid}-manual-title`}>
              <div className="po-add-manual-head">
                <p id={`${uid}-manual-title`} className="po-add-manual-title">
                  New item - not in inventory yet
                </p>
                <button type="button" className="link-button" onClick={leaveManual}>
                  ← back to search
                </button>
              </div>
              <div className="po-add-manual-fields">
                <label className="po-add-manual-field">
                  <span>Item name</span>
                  <input
                    ref={manualNameRef}
                    type="text"
                    value={manualName}
                    placeholder="Item name"
                    onChange={(e) => {
                      manualCheckId.current += 1;
                      setManualChecking(false);
                      setManualName(e.target.value);
                      setManualError(null);
                      setManualMatch(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addManual();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        leaveManual();
                      }
                    }}
                  />
                </label>
                <label className="po-add-manual-field po-add-manual-sku">
                  <span>SKU (optional)</span>
                  <input
                    type="text"
                    value={manualSku}
                    placeholder="Vendor SKU"
                    onChange={(e) => setManualSku(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addManual();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        leaveManual();
                      }
                    }}
                  />
                </label>
                {!manualMatch && (
                  <button
                    type="button"
                    className="btn-secondary po-add-manual-add"
                    disabled={!manualName.trim() || manualChecking}
                    onClick={() => addManual()}
                  >
                    {manualChecking ? "Checking…" : "Add to the list"}
                  </button>
                )}
              </div>
              {manualMatch && (
                <div className="po-add-confirm" role="group" aria-label="Already in inventory">
                  <span>
                    "{manualMatch.items[0].name}" is already in inventory
                    {splitVendors(manualMatch.items[0].supplier).length > 0 &&
                      ` (${splitVendors(manualMatch.items[0].supplier).join(", ")})`}
                    . Tick it from the search so it brings its SKU and stock.
                  </span>
                  <span className="po-add-confirm-actions">
                    <button type="button" className="link-button" onClick={showMatchInSearch}>
                      Show it in the search
                    </button>
                    <button type="button" className="link-button" onClick={() => addManual(true)}>
                      Add as a new item anyway
                    </button>
                  </span>
                </div>
              )}
              {manualError && (
                <p className="error po-add-manual-error" role="alert">
                  {manualError}
                </p>
              )}
            </div>
          )}

          {projection?.supplier === supplier && projection.status === "error" && (
            <p className="po-add-note">
              Couldn't load {supplier}'s sales, so there are no suggested quantities - new rows start at 1.
            </p>
          )}

          {toAdd.length > 0 && (
            <div className="po-add-list">
              <h3 id={listTitleId} className="po-add-list-title">
                To add <span className="po-add-count">{toAdd.length}</span>
              </h3>
              <div className="po-add-list-box">
                <div className="po-add-rowcols po-add-list-head" aria-hidden="true">
                  <span>Item</span>
                  <span className="po-col-num">In stock</span>
                  <span>Note</span>
                  <span className="po-col-qty">Qty</span>
                  <span />
                </div>
                <ul aria-labelledby={listTitleId}>
                  {toAdd.map((row) => (
                    <ToAddRow
                      key={row.key}
                      row={row}
                      supplier={supplier}
                      proj={row.item_id ? projIndex?.get(row.item_id) : undefined}
                      projLoading={projLoading && !!row.item_id}
                      projReady={projReady}
                      inCart={isInCart(inCartKeys, row.item_id, row.name)}
                      saving={saving}
                      onQty={(v) => setRowQty(row.key, v)}
                      onRemove={() => removeRow(row.key)}
                    />
                  ))}
                </ul>
              </div>
            </div>
          )}
        </>
      )}

      {error && (
        <p className="error po-add-error" role="alert">
          {error}
        </p>
      )}
      <div className="po-add-outcome">
        <div role="status" aria-live="polite">
          {added && added.count === 0 && (
            <p className="muted po-add-done">
              Nothing added: {added.skipped === 1 ? "that item is" : `all ${added.skipped} items are`} already in{" "}
              {added.supplier}'s cart (the cart was checked again just before adding). Change the qty in the cart
              below if needed.
            </p>
          )}
          {added && added.count > 0 && (
            <p className="success po-add-done">
              Added {plural(added.count, "item")} to {added.supplier}.
              {added.checked && " (The server's answer didn't come back, but the cart shows them all.)"}
              {added.skipped > 0 &&
                ` ${plural(added.skipped, "item")} already in the cart ${added.skipped === 1 ? "was" : "were"} left out - change ${added.skipped === 1 ? "its" : "their"} qty in the cart below if needed.`}
            </p>
          )}
        </div>
        {added && onShowVendor && (
          <button type="button" className="link-button po-add-view" onClick={() => onShowVendor(added.supplier)}>
            View in cart ↓
          </button>
        )}
      </div>

      {supplier && toAdd.length > 0 && (
        // "N items · Add to <vendor>'s cart", kept together on the left: the
        // chat bubble sits at the bottom right of the screen.
        <div ref={footerRef} className={`po-add-footer${footerStuck ? " is-stuck" : ""}`}>
          <span className="po-add-footer-count" id={countId}>
            <strong>{plural(postable.length, "item")}</strong>
            {listedInCart.length > 0 && <span className="muted"> ({listedInCart.length} already in the cart)</span>}
          </span>
          <button
            type="button"
            className="po-add-submit"
            disabled={!canSubmit}
            aria-describedby={countId}
            onClick={handleSubmit}
          >
            {saving ? "Adding…" : `Add to ${supplier}'s cart`}
          </button>
          {badQty && <span className="po-add-footer-warn">Every row needs a quantity above 0.</span>}
        </div>
      )}
    </section>
  );
}

// One search result: a checkbox row with aligned columns (item, vendor in
// "All products", SKU, in stock, per week, status). Greyed out (and not
// tickable) when it's already in the vendor's cart.
function ResultRow({ item, proj, inCart, ticked, showVendors, showSku = true, onToggle, onKeyDown }) {
  const s = suggestion(proj);
  const sku = proj?.sku || item.supplier_item_id;
  const vendors = splitVendors(item.supplier);
  const matchNote =
    item.matched_on && item.matched_on !== "name" && item.matched_code
      ? codeMatchLabel([item.matched_on, item.matched_code, true])
      : null;

  let tag = null;
  if (inCart) tag = <span className="pill pill-neutral po-add-tag">Already in cart</span>;
  else if (s != null && s > 0) tag = <span className="pill pill-warn po-add-tag">Need {s}</span>;
  else if (s === 0) tag = <span className="pill pill-success po-add-tag">Enough</span>;

  return (
    <li className={`${inCart ? "is-in-cart" : ""}${ticked ? " is-ticked" : ""}`}>
      <label
        className="po-add-cols po-add-result"
        title={inCart ? "Already in cart - change its qty in the cart below" : undefined}
      >
        <input type="checkbox" checked={ticked} disabled={inCart} onChange={onToggle} onKeyDown={onKeyDown} />
        <span className="po-add-result-name">
          {item.name}
          {/* Found by a code, not its name (W2): say which one. */}
          {matchNote && <small className="po-add-match">Matches {matchNote}</small>}
        </span>
        {/* Their own grid columns on a wide screen; one wrapped line under
            the name on a phone. */}
        <span className="po-add-result-meta">
          {showVendors && (
            <span className="po-add-result-vendor" title={vendors.length > 2 ? vendors.join(", ") : undefined}>
              <span className="po-cell-label">Vendor: </span>
              {vendors.length ? vendors.join(", ") : "No vendor listed"}
            </span>
          )}
          {showSku && (
            <span className={`po-add-result-sku${sku ? "" : " is-empty"}`}>
              <span className="po-cell-label">SKU </span>
              {sku || "-"}
            </span>
          )}
          <span className="po-add-result-stock po-col-num">
            <StockPill qty={inStockFor(item, proj)} />
          </span>
          {!showVendors && (
            <span className={`po-add-result-weekly po-col-num${proj?.weekly != null ? "" : " is-empty"}`}>
              {proj?.weekly != null ? `${formatQty(proj.weekly)}/wk` : "-"}
            </span>
          )}
        </span>
        <span className="po-add-result-tag po-col-end">{tag}</span>
      </label>
    </li>
  );
}

// One row of the To-add table: item (+ SKU), in stock, what the qty is
// based on, the qty with - / + and a remove (x).
function ToAddRow({ row, supplier, proj, projLoading, projReady, inCart, saving, onQty, onRemove }) {
  const s = suggestion(proj);
  const stock = row.manual ? null : inStockFor(row.result, proj);
  const problem = inCart ? null : qtyProblem(row.qty);
  const vendors = splitVendors(row.vendors);
  // Ticked from "all products": not one of the chosen vendor's items.
  const otherVendor = !row.manual && !vendors.some((v) => v === supplier);

  let basis = null;
  if (inCart) basis = <span>Already in {supplier}'s cart - left out (change its qty in the cart below)</span>;
  else if (row.manual) basis = <span className="po-add-suggest is-new">New item - not in inventory</span>;
  else if (s != null && s > 0)
    basis = (
      <span
        className="po-add-suggest is-need"
        title={`Projected need for 1 week from the last ${SUGGEST_LOOKBACK_DAYS} days of sales, minus what's in stock - the same suggestion as the app`}
      >
        Suggested {s} for 1 week
      </span>
    );
  else if (s === 0) basis = <span className="po-add-suggest is-enough">Enough stock for 1 week</span>;
  else if (otherVendor) basis = <span>{vendors.length ? `Listed under ${vendors.join(", ")}` : "No vendor listed"}</span>;
  else if (projLoading) basis = <span>Checking sales…</span>;
  // The projection is in but has no suggestion for this item: say why the
  // qty is a plain 1 rather than leave an unexplained number.
  else if (projReady && proj) basis = <span>No stock count on file, so no suggestion - qty starts at 1</span>;
  else if (projReady) basis = <span>No sales projection for it, so no suggestion - qty starts at 1</span>;

  const meta = [
    row.supplier_item_id && `SKU ${row.supplier_item_id}`,
    row.replaced && `replaces the new item you typed ("${row.replaced}")`,
  ].filter(Boolean);

  const locked = inCart || saving;
  return (
    <li
      className={`po-add-rowcols po-add-row${inCart ? " is-in-cart" : ""}${problem ? " has-problem" : ""}${row.manual ? " is-manual" : ""}`}
    >
      <div className="po-add-row-item">
        <span className="po-add-row-name">{row.name}</span>
        {meta.length > 0 && <span className="po-add-row-sku">{meta.join(" · ")}</span>}
      </div>
      <div className="po-add-row-stock po-col-num">
        <StockPill qty={stock} />
      </div>
      <div className="po-add-row-meta">{basis}</div>
      <div className="po-add-row-qty po-col-qty">
        <span className="po-stepper">
          <button
            type="button"
            className="po-stepper-btn"
            tabIndex={-1}
            aria-label={`Decrease qty for ${row.name}`}
            disabled={locked || !canStepQty(row.qty, -1)}
            onClick={() => onQty(stepQty(row.qty, -1))}
          >
            <span aria-hidden="true">−</span>
          </button>
          <input
            type="number"
            inputMode="decimal"
            min="1"
            step="1"
            value={row.qty}
            disabled={locked}
            aria-label={`Qty for ${row.name}`}
            aria-invalid={problem ? true : undefined}
            onChange={(e) => onQty(e.target.value)}
          />
          <button
            type="button"
            className="po-stepper-btn"
            tabIndex={-1}
            aria-label={`Increase qty for ${row.name}`}
            disabled={locked || !canStepQty(row.qty, 1)}
            onClick={() => onQty(stepQty(row.qty, 1))}
          >
            <span aria-hidden="true">+</span>
          </button>
        </span>
      </div>
      <button
        type="button"
        className="po-add-row-remove"
        aria-label={`Remove ${row.name} from the list`}
        title="Remove from the list"
        disabled={saving}
        onClick={onRemove}
      >
        <CloseIcon />
      </button>
      {problem && <p className="po-add-row-problem error">{problem}</p>}
    </li>
  );
}

// "24" in a small pill (red when nothing's left); "-" when there's no count.
// The words "in stock" show on phones (no column headings there) and are
// always read out by screen readers.
function StockPill({ qty }) {
  const known = qty != null && qty !== "" && Number.isFinite(Number(qty));
  if (!known) {
    return (
      <span className="po-stock-pill is-unknown">
        -<span className="po-stock-unit"> in stock</span>
      </span>
    );
  }
  const v = Number(qty);
  return (
    <span className={`po-stock-pill${v <= 0 ? " is-out" : ""}`}>
      {formatQty(v)}
      <span className="po-stock-unit"> in stock</span>
    </span>
  );
}

function SearchIcon() {
  return (
    <svg className="po-add-search-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="m10.6 10.6 3.4 3.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

function ChevronIcon({ up }) {
  return (
    <svg
      className={`po-add-more-icon${up ? " is-up" : ""}`}
      width="12"
      height="12"
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
