import { formatRestaurantTime } from "../format.js";
import { TermInfo } from "../glossary.jsx";

// One muted line saying where a page's numbers came from, for the two
// "between uploads" sources added by the automation batch (Oct 11):
// - liveStock: the additive `live_stock` field on Reorder Candidates,
//   Supplier Projection and Fix Issues' Negative / Refill lists. null = the
//   switch is off (snapshot counts, nothing to say); {live: true, as_of} =
//   on hand is Toast's live count; {live: false} = Toast didn't answer, so
//   the snapshot counts were used.
// - menuCheckedAt: Fix Issues' barcode lists - when Toast's menu last changed,
//   sent only when our hourly re-read of it is newer than the latest upload
//   (null = the upload is the source).
export function LiveStockNote({ liveStock, style }) {
  if (!liveStock) return null;
  return (
    <p className="muted data-source-note" style={style}>
      {liveStock.live
        ? `On hand is Toast's live count, as of ${formatRestaurantTime(liveStock.as_of)}.`
        : "On hand is from the latest inventory snapshot: Toast's live count isn't available right now."}{" "}
      <TermInfo k="live_stock" label="Live stock" />
    </p>
  );
}

export function MenuCheckNote({ menuCheckedAt, style }) {
  if (!menuCheckedAt) return null;
  return (
    <p className="muted data-source-note" style={style}>
      Barcodes are from Toast's menu (last changed {formatRestaurantTime(menuCheckedAt)}), read after the
      latest upload.{" "}
      <TermInfo k="barcode_menu_recheck" label="Re-checked in Toast" />
    </p>
  );
}
