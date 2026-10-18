// Today-tab order actions (docs/BATCH_OCT14_CONTRACT.md W1): pure helpers
// shared by the Vendor Calendar menu and Reorder Candidates. Node tests:
// node --test src/components/orderActions/orderActionsModel.test.mjs

export const NOTE_MAX = 500;

// How an order-due was dealt with, from the calendar's additive `handled`
// (older servers: from `skipped` / `ordered` alone).
export function handledOf(event) {
  if (!event || event.type !== "order_due") return null;
  if (event.handled && typeof event.handled === "object") return event.handled;
  if (event.skipped) return { kind: "skip", label: "Skipped this week", can_undo: true, order_id: null };
  if (event.ordered) return { kind: "placed", label: "Order placed", can_undo: false, order_id: null };
  return null;
}

// The chip's first word.
export function chipLabel(event) {
  const h = handledOf(event);
  if (!h) return "Order due";
  if (h.kind === "skip") return "Skipped";
  if (h.kind === "other") return "Hidden";
  return "Ordered";
}

// The count beside an order-due chip. Vendors have minimum orders: the
// server marks an order-due `urgent` only with 5+ items needing ordering
// (`min_order_items`); 1-4 is `below_minimum` - shown as "n low", never red.
export function orderDueCount(event) {
  if (!event || handledOf(event)) return "";
  const n = Number(event.items_needing_order) || 0;
  if (event.urgent) return ` (${n})`;
  if (event.below_minimum && n > 0) return ` (${n} low)`;
  return "";
}

// The chip's hover text before anything was done about it.
export function orderDueTitle(event) {
  const n = Number(event?.items_needing_order) || 0;
  const head = `Covers through ${event?.for_delivery_date} (${event?.horizon_days}-day horizon).`;
  if (event?.below_minimum) {
    const min = event.min_order_items || 5;
    return `${head} ${n} item${n === 1 ? "" : "s"} low - fewer than a usual order (${min}+ items), so not urgent.`;
  }
  return `${head} ${n} item(s) need ordering.`;
}

// "Order already placed by boss" / "Other: Rep visits Friday (boss)".
export function handledText(handled) {
  if (!handled) return "";
  const who = handled.by_name ? ` by ${handled.by_name}` : "";
  if (handled.kind === "other") return `Other${handled.note ? `: ${handled.note}` : ""}${handled.by_name ? ` (${handled.by_name})` : ""}`;
  if (handled.kind === "placed") return `Order placed in the app${who}`;
  return `${handled.label || "Handled"}${who}${handled.note ? ` - ${handled.note}` : ""}`;
}

// What the menu offers. Skip stays open to everyone with the Vendor
// Calendar (as before); Order already placed, Other and their Undo are
// admin-only, like the critical-vendor actions. The demo login sees none.
export function menuActions(event, { isAdmin = false, demo = false } = {}) {
  if (!event || event.type !== "order_due" || demo) return [];
  const h = handledOf(event);
  if (h) {
    if (!h.can_undo) return [];
    if (h.kind === "skip") return ["undo"];
    return isAdmin ? ["undo"] : [];
  }
  return isAdmin ? ["placed", "skip", "other"] : ["skip"];
}

// The request an Undo makes: {kind: "placed", orderId} or {kind: "skip",
// vendor, deliveryDate}; null when there's nothing this page can undo.
export function undoRequest(event, handled = handledOf(event)) {
  if (!handled || !handled.can_undo) return null;
  if (handled.kind === "placed_outside") {
    const orderId = handled.order_id ?? event?.ordered_order_id;
    return orderId != null ? { kind: "placed", orderId } : null;
  }
  if (handled.kind === "skip" || handled.kind === "other") {
    return { kind: "skip", vendor: event.vendor, deliveryDate: event.for_delivery_date || event.date };
  }
  return null;
}

// Problem with a note, or null. `required` for Other.
export function noteProblem(note, required) {
  const text = (note || "").trim();
  if (required && !text) return "Add a note for Other.";
  if (text.length > NOTE_MAX) return `Keep the note to ${NOTE_MAX} characters or fewer.`;
  return null;
}

// "Thu Oct 1" from "2026-10-01" (no time zone shift).
export function dayText(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const date = new Date(Date.UTC(y, m - 1, d));
  // "Thu Oct 22" - the server's own wording (no comma), whatever the locale.
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${days[date.getUTCDay()]} ${months[m - 1]} ${d}`;
}

// A loaded Reorder vendor group after "Order already placed" (the server
// would say the same on the next report): Ordered, from outside the app.
export function markGroupPlacedOutside(group, result) {
  return {
    ...group,
    urgency: { tier: "ordered", label: "Ordered" },
    ordered_at: result.handled?.at || new Date().toISOString(),
    ordered_order_id: result.order_id,
    ordered_source: "outside",
    _before_outside: group._before_outside || group,
  };
}

// Undo of the above: the group as it was before.
export function unmarkGroupPlacedOutside(group) {
  return group._before_outside ? group._before_outside : group;
}
