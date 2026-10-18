// Definitions for every status / tag / label (Critical, Review, Intermittent...).
// The text lives in ONE place, backend/app/glossary.py, served at
// GET /api/glossary; this file fetches it once per login and caches it in
// context. <Term k="urgency_critical">...</Term> adds a small (i) button that
// opens a popover with the definition; /glossary lists everything.
//
// Keys must be string literals: either `k="..."` on a <Term>, or a value in
// one of the maps below. backend/tests/test_ops_glossary.py scans for both
// and fails if a key isn't in the glossary.
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { getGlossary } from "./api";
import { useAuth } from "./AuthContext.jsx";

// ---- value -> glossary key maps (values are glossary keys) ----
export const URGENCY_TERMS = {
  critical: "urgency_critical",
  order_now: "urgency_order_now",
  check_count: "urgency_check_count",
  ordered: "urgency_ordered",
  can_wait: "urgency_can_wait",
  low_priority: "urgency_low_priority",
};

export const CLASS_TERMS = {
  regular: "class_regular",
  intermittent: "class_intermittent",
  slow: "class_slow",
  dead: "class_dead",
};

export const FLAG_TERMS = {
  insufficient_history: "flag_insufficient_history",
  prolonged_stockout: "flag_prolonged_stockout",
  demand_spike: "flag_demand_spike",
  unusual_trend: "flag_unusual_trend",
  negative_inventory: "flag_negative_inventory",
  missing_inventory_history: "flag_missing_inventory_history",
  intermittent: "flag_intermittent",
  dead_inventory: "flag_dead_inventory",
  differs_from_history: "flag_differs_from_history",
};

export const ISSUE_TERMS = {
  missing_barcode: "issue_missing_barcode",
  invalid_barcode: "issue_invalid_barcode",
  duplicate: "issue_duplicate",
  dead_stock: "issue_dead_stock",
  negative_inventory: "issue_negative_inventory",
  refill_from_container: "issue_refill_from_container",
  slow_review: "issue_slow_review",
  dead_with_stock: "issue_dead_with_stock",
  dead_zero_stock: "issue_dead_zero_stock",
  dead_new_arrival: "issue_dead_new_arrival",
};

// Fix Issues "Came back": how an item got back on the open list
// (reopened.how from /api/issues/{type}).
export const CAME_BACK_TERMS = {
  snooze_ended: "came_back_snooze_ended",
  fixed_but_still_showing: "came_back_fixed_but_still_showing",
  detected_again: "came_back_detected_again",
};

// Critical vendors hidden from Today: dismissal kind -> term.
export const CRITICAL_DISMISS_TERMS = {
  close: "critical_close",
  skip_week: "critical_skip_week",
  snooze: "critical_snooze",
};

export const PLACED_STATUS_TERMS = {
  placed: "placed_status_placed",
  pdf: "placed_status_pdf",
};

export const INVOICE_STATUS_TERMS = {
  pending: "invoice_pending",
  in_progress: "invoice_in_progress",
  completed: "invoice_completed",
  failed: "invoice_failed",
};

export const CAL_STATUS_TERMS = {
  upcoming: "cal_upcoming",
  delivered: "cal_delivered",
  missed: "cal_missed",
  skipped: "cal_skipped",
};

export const DELIVERY_SOURCE_TERMS = {
  delivery_review: "delivery_source",
  manual: "delivery_source",
  mixed: "delivery_source",
};

// Sales accuracy card (admins): job verdict -> term.
export const SALES_ACCURACY_TERMS = {
  match: "sales_accuracy_match",
  fixed: "sales_accuracy_fixed",
  mismatch: "sales_accuracy_differs",
  waiting: "sales_accuracy_waiting",
};

// Send PO log: po_sends.status -> term.
export const PO_SEND_TERMS = {
  sending: "po_send_sending",
  sent: "po_send_sent",
  partly_sent: "po_send_partly_sent",
  failed: "po_send_failed",
  uncertain: "po_send_uncertain",
};

// Similar products: the backend sends the reason as text.
export const SIMILAR_REASON_TERMS = {
  "Same product, other brand": "similar_same_other_brand",
  "Same product, different size": "similar_same_other_size",
  "Same product and size - possible duplicate listing": "similar_possible_duplicate",
  "Same product": "similar_same_product",
  "Similar type, other brand": "similar_type_other_brand",
  "Similar type, same brand": "similar_type_same_brand",
  "Similar type": "similar_type",
};

// Glossary tone -> the website's pill class.
export const TONE_PILLS = {
  success: "pill-success",
  warning: "pill-warn",
  danger: "pill-failed",
  info: "pill-info",
  neutral: "pill-neutral",
  violet: "pill-check",
};

const GlossaryContext = createContext({ terms: {}, categories: [], loaded: false, error: null });

export function GlossaryProvider({ children }) {
  const { user } = useAuth();
  const [state, setState] = useState({ terms: {}, categories: [], loaded: false, error: null });
  const userId = user?.id ?? null;

  useEffect(() => {
    if (userId == null) return undefined;
    let cancelled = false;
    getGlossary()
      .then((g) => {
        if (cancelled) return;
        const terms = {};
        for (const t of g.terms || []) terms[t.key] = t;
        setState({ terms, list: g.terms || [], categories: g.categories || [], loaded: true, error: null });
      })
      .catch((err) => {
        if (!cancelled) setState((s) => ({ ...s, loaded: true, error: err.message }));
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  return <GlossaryContext.Provider value={state}>{children}</GlossaryContext.Provider>;
}

export function useGlossary() {
  return useContext(GlossaryContext);
}

// The popover, positioned (fixed) under the (i) button and kept on screen.
function TermPopover({ term, termKey, anchor, onClose }) {
  const { loaded, error } = useGlossary();
  const ref = useRef(null);
  const [pos, setPos] = useState({ top: -9999, left: -9999 });

  // Returns false when the (i) has scrolled out of view.
  const place = useCallback(() => {
    if (!anchor || !ref.current) return true;
    const r = anchor.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) return false;
    const box = ref.current.getBoundingClientRect();
    const margin = 8;
    let left = Math.min(r.left, window.innerWidth - box.width - margin);
    left = Math.max(margin, left);
    let top = r.bottom + 6;
    if (top + box.height > window.innerHeight - margin && r.top - box.height - 6 > margin) {
      top = r.top - box.height - 6;
    }
    setPos({ top, left });
    return true;
  }, [anchor]);

  useLayoutEffect(() => {
    place();
  }, [place, term]);

  useEffect(() => {
    function onDown(e) {
      if (ref.current?.contains(e.target) || anchor?.contains(e.target)) return;
      onClose();
    }
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    // Follow the (i) when the page or a table scrolls (tapping an (i) at
    // the edge of a sideways-scrolling table scrolls it into view); close
    // only once it has scrolled off screen.
    function onScroll(e) {
      if (ref.current?.contains(e.target)) return;
      if (!place()) onClose();
    }
    // Capture phase: the (i) buttons stop mousedown/keydown from bubbling
    // (so table rows and <summary>s don't react), which would otherwise
    // keep this popover open when another (i) is clicked or Esc is pressed.
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("touchstart", onDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("touchstart", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [anchor, onClose, place]);

  return createPortal(
    <div
      ref={ref}
      className="term-popover"
      role="dialog"
      aria-label={term ? `What ${term.label} means` : "Definition"}
      style={{ top: pos.top, left: pos.left }}
      onClick={(e) => e.stopPropagation()}
    >
      {term ? (
        <>
          <div className="term-popover-title">{term.label}</div>
          <p className="term-popover-def">{term.definition}</p>
          {term.rule && (
            <p className="term-popover-rule">
              <span className="term-popover-rule-label">How it's worked out: </span>
              {term.rule}
            </p>
          )}
        </>
      ) : (
        <p className="term-popover-def muted">
          {!loaded ? "Loading the definition…" : error ? "Couldn't load the definition." : "No definition yet."}
        </p>
      )}
      <Link className="term-popover-link" to={`/glossary?k=${encodeURIComponent(termKey)}`} onClick={onClose}>
        All definitions
      </Link>
    </div>,
    document.body,
  );
}

// A small (i) button that opens the definition of `k`.
export function TermInfo({ k, label }) {
  const { terms } = useGlossary();
  const [open, setOpen] = useState(false);
  const btnRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  const term = terms[k];
  const name = label || term?.label || "this";
  return (
    <>
      <button
        type="button"
        ref={btnRef}
        className="term-info"
        aria-label={`What does ${name} mean?`}
        aria-expanded={open}
        title={term ? `${term.label}: ${term.definition}` : `What does ${name} mean?`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        i
      </button>
      {open && <TermPopover term={term} termKey={k} anchor={btnRef.current} onClose={close} />}
    </>
  );
}

// Wraps a label or pill and puts an (i) right after it. With no key (an
// unknown value) it renders the children unchanged.
export function Term({ k, children, label }) {
  if (!k) return <>{children}</>;
  return (
    <span className="term">
      {children}
      <TermInfo k={k} label={label} />
    </span>
  );
}
