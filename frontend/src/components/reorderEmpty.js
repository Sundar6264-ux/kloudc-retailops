// G31: what an empty Reorder Candidates list says. The server's
// has_sales_data / has_stock_data tell a brand-new store (no sales or stock
// counts yet) from one where everything is stocked. A server without the
// flags reads as "everything is stocked".

export const NO_SALES_YET =
  "No sales have come in yet. Once your store's system sends a day of sales, items to reorder show up here.";
export const NO_STOCK_YET =
  "No stock counts have come in yet. Once your store's system sends its stock counts, items to reorder show up here.";
export const NOTHING_TO_REORDER = "Nothing needs reordering right now.";

export function reorderEmptyText(data, search = "") {
  const q = String(search || "").trim();
  if (q) return `No items match "${q}".`;
  if (data?.has_sales_data === false) return NO_SALES_YET;
  if (data?.has_stock_data === false) return NO_STOCK_YET;
  return NOTHING_TO_REORDER;
}
