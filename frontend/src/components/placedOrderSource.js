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

// A placed order's "Total qty" with cases and units kept apart, the cart
// header's words: "11 cases + 18 units", "11 cases" or "18 units". A line is
// in cases when it has a case size above 1 (case_size, or the cart's case_of);
// every other line is in units. It used to add the raw quantities, so
// 11 cases and 18 units read "29". An order sent without lines (an older
// server) keeps the server's total_qty number.
export function orderQtyText(order, formatQty = (n) => String(n)) {
  const items = Array.isArray(order?.items) ? order.items.filter((l) => l && typeof l === "object") : [];
  if (!items.length) return formatQty(order?.total_qty);
  let cases = 0;
  let units = 0;
  let caseLines = 0;
  for (const line of items) {
    const qty = Number(line.qty) || 0;
    const size = Number(line.case_size) || Number(line.case_of) || 0;
    if (size > 1) {
      cases += qty;
      caseLines += 1;
    } else {
      units += qty;
    }
  }
  const word = (n, w) => `${formatQty(n)} ${n === 1 ? w : `${w}s`}`;
  if (caseLines && caseLines < items.length) return `${word(cases, "case")} + ${word(units, "unit")}`;
  if (caseLines) return word(cases, "case");
  return word(units, "unit");
}
