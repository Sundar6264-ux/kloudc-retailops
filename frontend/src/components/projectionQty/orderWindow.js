// The ONE order window (Oct 22): how much sales history, and how many days
// the order has to last. Supplier Projection saves the window of every
// projection it runs; the Purchase Order Cart's line suggestions and its
// "Add items" form read it - so an item shows the same suggested qty in
// both (the owner's Garlic 5-Pack: 6 cases on the projection for 30 days,
// "suggested 2" in the cart for the cart's own 1 week). Same defaults as
// the app (3 months of sales, order for 1 month). Pure + a storage handle,
// tested with `node --test src/components/projectionQty/orderWindow.test.mjs`.

export const ORDER_WINDOW_KEY = "retailops_order_window";
export const DEFAULT_ORDER_WINDOW = Object.freeze({ lookbackDays: 90, horizonDays: 30 });
// The server takes up to 365 days of history and 180 days ahead.
const MAX_LOOKBACK = 365;
const MAX_HORIZON = 180;

const whole = (v, max) => (Number.isInteger(v) && v >= 1 && v <= max ? v : null);

// The saved window, or the defaults. Never throws (no storage, bad JSON,
// out-of-range numbers all give the defaults for that part).
export function readOrderWindow(storage) {
  try {
    const raw = storage?.getItem(ORDER_WINDOW_KEY);
    const v = raw ? JSON.parse(raw) : null;
    return {
      lookbackDays: whole(v?.lookbackDays, MAX_LOOKBACK) ?? DEFAULT_ORDER_WINDOW.lookbackDays,
      horizonDays: whole(v?.horizonDays, MAX_HORIZON) ?? DEFAULT_ORDER_WINDOW.horizonDays,
    };
  } catch {
    return { ...DEFAULT_ORDER_WINDOW };
  }
}

// Saves a window (ignores a bad one). Never throws.
export function writeOrderWindow(storage, { lookbackDays, horizonDays }) {
  const lb = whole(lookbackDays, MAX_LOOKBACK);
  const hz = whole(horizonDays, MAX_HORIZON);
  if (lb == null || hz == null) return;
  try {
    storage?.setItem(ORDER_WINDOW_KEY, JSON.stringify({ lookbackDays: lb, horizonDays: hz }));
  } catch {
    // Storage full / blocked: the defaults are used next time.
  }
}

const NAMED = { 7: "1 week", 14: "2 weeks", 21: "3 weeks", 30: "1 month", 60: "2 months", 90: "3 months" };

// 30 -> "1 month", 12 -> "12 days".
export function windowWords(days) {
  return NAMED[days] || `${days} days`;
}
