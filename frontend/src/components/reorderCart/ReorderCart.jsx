// Add to the Purchase Order Cart from Reorder Candidates - the website's
// version of the app's reorder vendor screen: "+" on each item (qty 1) and
// "Add all Critical + Order now", which reviews the suggested quantities
// first (mobile lib/features/reorder: reorder_vendor_screen.dart,
// bulk_add_sheet.dart). The rules are in reorderCartModel.js.
//
// - Each vendor group adds to THAT vendor's cart (the report lists every
//   item under one vendor, Toast's ";" field already split). "Unassigned"
//   has no cart.
// - The cart is read once when the page opens (and on Calculate), right
//   before every add, and after it. The pre-add read matters: the cart is
//   shared and the phone may have added an item since - that item is left
//   out and the message says so, because re-adding would replace its qty.
// - Adds run one at a time (a queue), each item can only be in one running
//   add, and each action is ONE POST (the server adds the list in one go).
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { addToCart, getCart } from "../../api";
// The cart page's own link helpers: "Open cart" opens the vendor there with
// what was added from here marked "Just added".
import { cartLinkFor, cartLinkState } from "../cartAdd/cartVendorPanels.js";
import {
  QUICK_ADD_QTY,
  addOutcome,
  bulkAlreadyInCart,
  bulkCandidates,
  canAddToVendor,
  canSubmitRows,
  cartItemsPayload,
  cartKeysFor,
  formatStock,
  includedRows,
  isNetworkError,
  itemInCart,
  mergeAddedLines,
  offersCartLink,
  plural,
  qtyProblem,
  resultMessage,
  reviewRows,
  selectedCandidates,
  splitPostable,
} from "./reorderCartModel.js";

const CART_PATH = "/purchase-order";

function itemName(item) {
  return item.name || item.item_id;
}

// --- State -------------------------------------------------------------------

// Everything the page's cart controls share. `enabled`: the user may use the
// cart (purchase_order_cart); `demo`: the App Review demo login, which may
// look but never write.
export function useReorderCart({ enabled, demo }) {
  const [cart, setCart] = useState(null); // GET /api/ordering/cart
  const [cartError, setCartError] = useState(null);
  const [selected, setSelected] = useState({}); // vendor -> Set(item_id)
  const [pending, setPending] = useState({}); // vendor -> Set(item_id) being added
  const [results, setResults] = useState({}); // vendor -> {tone, text, link}
  const [review, setReview] = useState(null); // the open review panel

  const cartRef = useRef(null);
  const pendingRef = useRef(new Map());
  const queueRef = useRef(Promise.resolve());
  const readSeq = useRef(0);
  const appliedSeq = useRef(0);
  // vendor -> item_ids added to its cart from this page (this visit), for
  // "Open cart" to mark "Just added" on the cart page.
  const addedRef = useRef(new Map());

  useEffect(() => {
    cartRef.current = cart;
  }, [cart]);

  // Reads the cart; a read that comes back after a newer one is ignored.
  // Returns the cart, or null when it couldn't be read.
  const readCart = useCallback(async () => {
    const seq = ++readSeq.current;
    try {
      const data = await getCart();
      if (seq > appliedSeq.current) {
        appliedSeq.current = seq;
        setCart(data);
        setCartError(null);
      }
      return data;
    } catch (err) {
      if (seq > appliedSeq.current) setCartError(isNetworkError(err) ? "no connection to the server" : err.message);
      return null;
    }
  }, []);

  useEffect(() => {
    if (enabled) readCart();
  }, [enabled, readCart]);

  // vendor -> what's in its cart section (null until the cart is read).
  const keysFor = useMemo(() => {
    const cache = new Map();
    return (vendor) => {
      if (!cart) return null;
      if (!cache.has(vendor)) cache.set(vendor, cartKeysFor(cart.suppliers, vendor));
      return cache.get(vendor);
    };
  }, [cart]);

  const setTicked = useCallback((vendor, ids, on) => {
    setSelected((prev) => {
      const next = new Set(prev[vendor] || []);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return { ...prev, [vendor]: next };
    });
  }, []);

  const clearSelection = useCallback(() => setSelected({}), []);

  // `added`: everything this page has put in the vendor's cart so far (the
  // link marks them all, not only the last add's).
  const showResult = useCallback((vendor, message, link) => {
    const added = [...(addedRef.current.get(vendor) || [])];
    setResults((prev) => ({ ...prev, [vendor]: { ...message, link, added } }));
  }, []);

  const noteAdded = useCallback((vendor, ids) => {
    const set = addedRef.current.get(vendor) || new Set();
    for (const id of ids) if (id) set.add(id);
    addedRef.current.set(vendor, set);
  }, []);

  const dismissResult = useCallback((vendor) => {
    setResults((prev) => {
      const next = { ...prev };
      delete next[vendor];
      return next;
    });
  }, []);

  const markPending = useCallback((vendor, ids, on) => {
    const cur = pendingRef.current.get(vendor) || new Set();
    for (const id of ids) {
      if (on) cur.add(id);
      else cur.delete(id);
    }
    if (cur.size) pendingRef.current.set(vendor, cur);
    else pendingRef.current.delete(vendor);
    setPending(Object.fromEntries([...pendingRef.current].map(([v, s]) => [v, new Set(s)])));
  }, []);

  // Adds rows ({item, qty}) to `vendor`'s cart in one POST, after checking
  // the cart again. Resolves to {ok, message, added?}; never rejects. The
  // vendor's bar shows the message either way.
  //   opts.single  - one item's quick add (the message names it)
  //   opts.leftOut - items left out when the list was made (already in the cart)
  const runAdd = useCallback(
    (vendor, rows, opts = {}) => {
      const ids = rows.map((r) => r.item.item_id);
      if (!canAddToVendor(vendor) || ids.length === 0) return Promise.resolve({ ok: false });
      // A re-click while the same item's add is still running.
      const running = pendingRef.current.get(vendor);
      if (running && ids.some((id) => running.has(id)))
        return Promise.resolve({ ok: false, busy: true, message: { tone: "info", text: "Still adding - wait a moment." } });
      if (demo) {
        const message = resultMessage({ kind: "demo", vendor });
        showResult(vendor, message, false);
        return Promise.resolve({ ok: false, message });
      }
      const single = opts.single ? { name: itemName(rows[0].item), qty: rows[0].qty } : null;
      const untick = (list) => setTicked(vendor, list, false);
      markPending(vendor, ids, true);

      const task = async () => {
        const done = (kind, fields, extra) => {
          const message = resultMessage({ ...fields, kind });
          showResult(vendor, message, offersCartLink(kind));
          return { ...extra, message };
        };
        let base = { vendor, single, leftOut: opts.leftOut || 0, meanwhile: 0 };
        let post = [];
        try {
          // The cart may have changed since the page read it (the phone,
          // another browser): what's in it now is left out, never re-sent.
          const before = await readCart();
          const known = before || cartRef.current;
          const split = splitPostable(rows, known ? cartKeysFor(known.suppliers, vendor) : null);
          post = split.post;
          base = { ...base, meanwhile: split.skipped.length };
          if (post.length === 0) {
            untick(ids);
            return done("none", base, { ok: true });
          }
          const lines = await addToCart(vendor, cartItemsPayload(post));
          noteAdded(vendor, post.map((r) => r.item.item_id));
          // Shown as in the cart at once; the re-read confirms it.
          setCart((c) => mergeAddedLines(c, lines));
          untick(ids);
          readCart();
          return done("added", { ...base, count: post.length }, { ok: true });
        } catch (err) {
          const reason = isNetworkError(err) ? "the connection to the server dropped" : err.message;
          if (post.length === 0) return done("failed", { ...base, reason }, { ok: false });
          // A refusal (4xx) means nothing went in: the server checks the
          // whole list first and adds it in one go.
          if (err.status >= 400 && err.status < 500) return done("failed", { ...base, reason }, { ok: false });
          // No answer, or a server error: look at the cart to say exactly
          // what got in.
          const after = await readCart();
          if (!after) return done("unknown", { ...base, reason, sent: post.length }, { ok: false });
          const { inCart, missing } = addOutcome(post, cartKeysFor(after.suppliers, vendor));
          // The cart badge only follows the event addToCart fires on success.
          if (inCart.length > 0) window.dispatchEvent(new Event("cart:updated"));
          noteAdded(vendor, inCart.map((r) => r.item.item_id));
          if (missing.length === 0) {
            untick(ids);
            return done("added", { ...base, count: post.length, checked: true }, { ok: true });
          }
          if (inCart.length === 0) return done("failed", { ...base, reason }, { ok: false });
          const added = inCart.map((r) => r.item.item_id);
          untick(added);
          return done(
            "partial",
            { ...base, reason, count: inCart.length, sent: post.length, missing: missing.length },
            { ok: false, added }
          );
        }
      };

      const run = queueRef.current.then(task, task);
      queueRef.current = run.catch(() => {});
      return run
        .catch((err) => {
          // Not expected (the task catches its own errors) - but a click
          // must never end in an unhandled error or a panel stuck on Adding.
          const message = { tone: "error", text: `Couldn't add to ${vendor}'s cart: ${err?.message || err}.` };
          showResult(vendor, message, true);
          return { ok: false, message };
        })
        .finally(() => markPending(vendor, ids, false));
    },
    [demo, markPending, noteAdded, readCart, setTicked, showResult]
  );

  const quickAdd = useCallback(
    (vendor, item) => runAdd(vendor, [{ item, qty: QUICK_ADD_QTY }], { single: true }),
    [runAdd]
  );

  const isPending = useCallback((vendor, itemId) => !!pending[vendor]?.has(itemId), [pending]);
  const vendorBusy = useCallback((vendor) => (pending[vendor]?.size || 0) > 0, [pending]);
  // Ticked, and still addable (an item that turned up in the cart since
  // isn't counted or tinted any more).
  const isTicked = useCallback(
    (vendor, item) => !!selected[vendor]?.has(item.item_id) && !itemInCart(keysFor(vendor), item),
    [selected, keysFor]
  );

  // The review panel: `items` with their suggested quantities. `opener` gets
  // focus back when it closes; `fallback` if the opener is gone by then.
  const reviewSeq = useRef(0);
  const openReview = useCallback((vendor, mode, items, leftOut, opener, fallback) => {
    if (!items.length) return;
    reviewSeq.current += 1;
    setReview({ id: reviewSeq.current, vendor, mode, rows: reviewRows(items), leftOut, opener, fallback });
  }, []);
  // Closes panel `id` only: an add that finishes after its panel was closed
  // must not close a newer one.
  const closeReview = useCallback((id) => setReview((r) => (r && r.id === id ? null : r)), []);

  return {
    enabled,
    demo,
    cart,
    cartError,
    keysFor,
    selected,
    setTicked,
    clearSelection,
    isPending,
    isTicked,
    vendorBusy,
    results,
    dismissResult,
    runAdd,
    quickAdd,
    review,
    openReview,
    closeReview,
    readCart,
  };
}

// --- Table pieces -----------------------------------------------------------

// The tick box in a row's first cell. Items already in the vendor's cart
// can't be ticked.
export function RowSelect({ rc, vendor, item }) {
  const inCart = itemInCart(rc.keysFor(vendor), item);
  const name = itemName(item);
  const checked = rc.isTicked(vendor, item);
  return (
    <label className="rc-cart-check" title={inCart ? `Already in ${vendor}'s cart` : undefined}>
      <input
        type="checkbox"
        checked={checked}
        disabled={inCart || rc.isPending(vendor, item.item_id)}
        aria-label={inCart ? `${name} - already in ${vendor}'s cart` : `Select ${name}`}
        onChange={(e) => rc.setTicked(vendor, [item.item_id], e.target.checked)}
      />
    </label>
  );
}

// The header's tick box: ticks / unticks every listed item not in the cart.
export function SelectAll({ rc, vendor, items }) {
  const ref = useRef(null);
  const keys = rc.keysFor(vendor);
  const selectable = items.filter((c) => !itemInCart(keys, c));
  const sel = rc.selected[vendor];
  const count = selectable.filter((c) => sel?.has(c.item_id)).length;
  const all = selectable.length > 0 && count === selectable.length;
  const some = count > 0 && !all;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = some;
  }, [some]);
  return (
    <label className="rc-cart-check">
      <input
        ref={ref}
        type="checkbox"
        checked={all}
        disabled={selectable.length === 0}
        aria-label={`Select all listed ${vendor} items that aren't in the cart`}
        onChange={(e) =>
          rc.setTicked(
            vendor,
            selectable.map((c) => c.item_id),
            e.target.checked
          )
        }
      />
    </label>
  );
}

// Under the item's name: "Add 1 to cart", or "In cart ✓" when it's in the
// vendor's cart already (change it there - re-adding would replace its qty).
export function RowCartAction({ rc, vendor, item }) {
  const name = itemName(item);
  if (itemInCart(rc.keysFor(vendor), item)) {
    return (
      // The tooltip sits on the wrapper: some browsers show none on a
      // disabled button.
      <span className="rc-cart-row-action" title={`Already in ${vendor}'s cart - change it in the cart`}>
        <button
          type="button"
          className="rc-cart-in-cart"
          disabled
          aria-label={`In cart - already in ${vendor}'s cart, change it in the cart`}
        >
          In cart ✓
        </button>
      </span>
    );
  }
  const adding = rc.isPending(vendor, item.item_id);
  return (
    <span className="rc-cart-row-action">
      <button
        type="button"
        className="rc-cart-add-one"
        disabled={adding}
        aria-label={`Add 1 ${name} to ${vendor}'s cart`}
        title={`Adds 1 to ${vendor}'s cart, like the app. To add the suggested quantity, tick it and use Add selected.`}
        onClick={() => rc.quickAdd(vendor, item)}
      >
        {adding ? "Adding…" : "Add 1 to cart"}
      </button>
    </span>
  );
}

// --- The vendor's bar --------------------------------------------------------

// "Open cart" opens the cart at this vendor (expanded, scrolled to), with the
// lines added from this page marked "Just added".
function ResultLine({ vendor, result, onDismiss }) {
  return (
    <p className={`rc-cart-result rc-cart-result-${result.tone}`}>
      <span className="rc-cart-result-text">{result.text}</span>
      {result.link && (
        <Link className="rc-cart-open" to={cartLinkFor(vendor)} state={cartLinkState(vendor, result.added)}>
          Open cart
        </Link>
      )}
      <button type="button" className="rc-cart-result-close" aria-label="Dismiss this message" onClick={onDismiss}>
        ✕
      </button>
    </p>
  );
}

// Above a vendor's table: "Add all Critical + Order now (N)", "Add selected
// (N)" and what the last add did. `items` is the vendor's whole list (not
// narrowed by the search box).
export function VendorCartBar({ rc, vendor, items }) {
  const barRef = useRef(null);
  if (!canAddToVendor(vendor)) {
    return (
      <p className="rc-cart-bar-note rc-cart-unassigned">
        No vendor is set in Toast for these items, so there's no vendor cart to add them to from here. Add them on the{" "}
        <Link to={CART_PATH}>Purchase Order Cart</Link> under the vendor you order them from.
      </p>
    );
  }
  const keys = rc.keysFor(vendor);
  const all = bulkCandidates(items, keys);
  const alreadyAll = bulkAlreadyInCart(items, keys);
  const sel = selectedCandidates(items, rc.selected[vendor], keys);
  const busy = rc.vendorBusy(vendor);
  const result = rc.results[vendor];
  const focusBar = () => barRef.current?.focus();
  return (
    <div className="rc-cart-bar" ref={barRef} tabIndex={-1} aria-label={`Add ${vendor} items to the cart`} role="group">
      <div className="rc-cart-bar-actions">
        {all.length > 0 && (
          <button
            type="button"
            className="btn-secondary rc-cart-btn"
            disabled={busy}
            onClick={(e) => rc.openReview(vendor, "all", all, alreadyAll, e.currentTarget, focusBar)}
          >
            Add all Critical + Order now ({all.length})
          </button>
        )}
        <button
          type="button"
          className="btn-secondary rc-cart-btn"
          disabled={busy || sel.length === 0}
          onClick={(e) => rc.openReview(vendor, "selected", sel, 0, e.currentTarget, focusBar)}
        >
          Add selected ({sel.length})
        </button>
        {all.length === 0 && alreadyAll > 0 && (
          <span className="rc-cart-bar-note">
            {alreadyAll === 1
              ? "Its Critical + Order now item is in the cart."
              : `All ${alreadyAll} Critical + Order now items are in the cart.`}
          </span>
        )}
      </div>
      <div role="status" aria-live="polite">
        {result && result.tone !== "error" && (
          <ResultLine vendor={vendor} result={result} onDismiss={() => rc.dismissResult(vendor)} />
        )}
      </div>
      <div role="alert">
        {result && result.tone === "error" && (
          <ResultLine vendor={vendor} result={result} onDismiss={() => rc.dismissResult(vendor)} />
        )}
      </div>
    </div>
  );
}

// Once above the vendor list: how adding works, and a failed cart read.
export function CartHowTo({ rc }) {
  return (
    <div className="rc-cart-howto">
      <p>
        <strong>Add to cart:</strong> <em>Add 1 to cart</em> puts 1 of that item in the vendor's cart, like the app. To add
        the suggested quantities (the Shortfall, in units), tick items and click <em>Add selected</em>, or use{" "}
        <em>Add all Critical + Order now</em> - you can change each quantity first. Items already in a vendor's cart are
        left out, so their quantity is never changed from here.
      </p>
      {rc.demo && <p className="rc-cart-howto-demo">Demo account: you can look around, but nothing is added to the cart.</p>}
      {rc.cartError && (
        <p className="rc-cart-howto-warn" role="status">
          Couldn't check what's already in the cart ({rc.cartError}), so nothing is marked "In cart" yet. Adding still checks
          the cart first.
        </p>
      )}
    </div>
  );
}

// --- Review panel ("Add selected" / "Add all Critical + Order now") ----------

function focusables(root) {
  return [
    ...root.querySelectorAll("button:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])"),
  ].filter((el) => el.offsetParent !== null);
}

// The app's bulk add sheet as a right-hand panel: each item with its
// suggested quantity (editable), untick to leave one out, one Add.
export function CartReviewPanel({ rc }) {
  if (!rc.review) return null;
  return <ReviewPanel key={rc.review.id} rc={rc} review={rc.review} />;
}

function ReviewPanel({ rc, review }) {
  const { vendor, mode, leftOut } = review;
  const { closeReview, runAdd } = rc;
  const uid = useId();
  const [rows, setRows] = useState(review.rows);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const busyRef = useRef(false);
  const formRef = useRef(null);

  // Closing while an add runs is fine (the app's sheet allows it too): the
  // add carries on and the vendor's bar says how it went.
  const close = useCallback(() => closeReview(review.id), [closeReview, review.id]);

  // Esc closes; Tab stays inside the panel.
  useEffect(() => {
    function onKey(e) {
      const root = formRef.current;
      if (!root) return;
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "Tab") {
        const els = focusables(root);
        if (els.length === 0) return;
        const first = els[0];
        const last = els[els.length - 1];
        if (!root.contains(document.activeElement)) {
          e.preventDefault();
          first.focus();
        } else if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);

  // Focus moves into the panel, and back to the button that opened it.
  useEffect(() => {
    formRef.current?.querySelector("input[type='checkbox']")?.focus();
    const { opener, fallback } = review;
    return () => {
      if (opener && opener.isConnected && !opener.disabled) opener.focus();
      else fallback?.();
    };
  }, [review]);

  const included = includedRows(rows);
  const canSubmit = canSubmitRows(rows) && !busy;
  const badQty = included.some((r) => qtyProblem(r.qty));

  function update(key, fields) {
    setError(null);
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...fields } : r)));
  }

  async function submit(e) {
    e.preventDefault();
    if (!canSubmitRows(rows) || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    const res = await runAdd(
      vendor,
      included.map((r) => ({ item: r.item, qty: r.qty })),
      { leftOut }
    );
    busyRef.current = false;
    setBusy(false);
    if (res.ok) {
      closeReview(review.id);
      return;
    }
    // Some got in (rare - the server adds the list in one go): keep the rest.
    if (res.added?.length) setRows((prev) => prev.filter((r) => !res.added.includes(r.key)));
    setError(res.message?.text ? res.message : { tone: "error", text: "Couldn't add the items." });
  }

  const titleId = `${uid}-title`;
  const subId = `${uid}-sub`;
  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <form
        ref={formRef}
        className="issue-drawer rc-cart-review"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subId}
        onSubmit={submit}
        noValidate
      >
        <div className="issue-drawer-head">
          <h3 id={titleId} className="rc-cart-review-title">
            Add to {vendor}'s cart
          </h3>
          <button type="button" className="link-button" onClick={close} aria-label="Close the panel">
            ✕
          </button>
        </div>
        <p id={subId} className="muted rc-cart-review-sub">
          {mode === "all" ? `${vendor}'s Critical and Order now items` : "The items you ticked"}, each with its suggested
          quantity: the Shortfall, in units (Case of 1). Change a quantity, or untick an item to leave it out.
        </p>
        {leftOut > 0 && (
          <p className="rc-cart-review-note">
            {plural(leftOut, "item")} {leftOut === 1 ? "is" : "are"} already in the cart and left out - change{" "}
            {leftOut === 1 ? "its" : "their"} qty in the cart.
          </p>
        )}
        <ul className="rc-cart-review-list">
          {rows.map((row) => (
            <ReviewRow key={row.key} row={row} busy={busy} uid={uid} onChange={(fields) => update(row.key, fields)} />
          ))}
        </ul>
        {error && (
          <p className={`rc-cart-result rc-cart-result-${error.tone} rc-cart-review-error`} role="alert">
            <span className="rc-cart-result-text">{error.text}</span>
          </p>
        )}
        <div className="issue-drawer-actions rc-cart-review-actions">
          {badQty && <span className="rc-cart-review-hint">Every ticked item needs a quantity above 0.</span>}
          <button type="button" className="btn-secondary" onClick={close}>
            {busy ? "Close" : "Cancel"}
          </button>
          <button type="submit" disabled={!canSubmit}>
            {busy ? "Adding…" : `Add ${plural(included.length, "item")} to cart`}
          </button>
        </div>
      </form>
    </div>
  );
}

function ReviewRow({ row, busy, uid, onChange }) {
  const { item } = row;
  const name = itemName(item);
  const problem = row.include ? qtyProblem(row.qty) : null;
  const errId = `${uid}-err-${row.key}`;
  const tier = item.urgency?.tier;
  return (
    <li className={`rc-cart-review-row${row.include ? "" : " is-excluded"}`}>
      <label className="rc-cart-check">
        <input
          type="checkbox"
          checked={row.include}
          disabled={busy}
          aria-label={`Add ${name}`}
          onChange={(e) => onChange({ include: e.target.checked })}
        />
      </label>
      <div className="rc-cart-review-info">
        <span className="rc-cart-review-name">{name}</span>
        <span className="rc-cart-review-meta">
          {item.urgency?.label && <span className={`tier-count tier-count-${tier}`}>{item.urgency.label}</span>}
          {item.urgency?.label && " · "}
          on hand {formatStock(item.on_hand_qty)} · <span className="rc-cart-review-suggested">Suggested {row.suggested || "-"}</span>
        </span>
        {problem && (
          <span id={errId} className="rc-cart-review-problem">
            {problem}
          </span>
        )}
      </div>
      <label className="rc-cart-review-qty">
        <span aria-hidden="true">Qty</span>
        <input
          type="number"
          inputMode="decimal"
          min="0"
          step="any"
          value={row.qty}
          disabled={busy || !row.include}
          aria-label={`Qty of ${name}${row.suggested ? `, suggested ${row.suggested}` : ""}`}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? errId : undefined}
          onChange={(e) => onChange({ qty: e.target.value })}
        />
      </label>
    </li>
  );
}
