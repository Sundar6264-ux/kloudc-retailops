// Placed Orders: an "Order already placed" row (W1, docs/BATCH_OCT14_CONTRACT.md)
// is a placed order with source "outside" and no items. Pure helpers so the page
// says so instead of "0 items / No item details were saved". Node tests:
// node --test src/components/placedOrderSource.test.mjs
import { dayText } from "./orderActions/orderActionsModel.js";

// Older servers send no `source`: every order was placed from the app then.
export function isOutsideOrder(order) {
  return !!order && order.source === "outside";
}

// The short label beside the status ("Placed outside the app").
export function outsideLabel(order) {
  if (!isOutsideOrder(order)) return null;
  return order.source_label || "Placed outside the app";
}

// The expanded row's explanation, or null for an ordinary order.
export function outsideDetail(order) {
  if (!isOutsideOrder(order)) return null;
  const when = order.for_delivery_date ? ` for the ${dayText(order.for_delivery_date)} delivery` : "";
  const who = order.placed_by_name ? ` by ${order.placed_by_name}` : "";
  return {
    text: `Marked "Order already placed"${who}${when} - the order went in outside the app, so no items were recorded here.`,
    note: (order.note || "").trim() || null,
  };
}

// A placed order's line quantity in words: the server's qty_text ("30 cases
// (360 units)") when the line was ordered in cases, else the number as sent
// (an older server, or a line in units). It used to be the bare number, so
// 30 cases of 12 read as "30".
export function lineQtyText(line, formatQty = (n) => String(n)) {
  const text = typeof line?.qty_text === "string" ? line.qty_text.trim() : "";
  if (text && line?.case_size) return text;
  return formatQty(line?.qty);
}
