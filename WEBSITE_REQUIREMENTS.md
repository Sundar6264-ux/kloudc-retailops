# Website Requirements

Pending feature requests for the spicetown-backend dashboard (website only, unless a section below
says it's also queued for the mobile app in `MOBILE_APP_FIXES.md`). Not built yet - this is a
planning doc, ticked off / moved to "done" once each ships.

---

## 1. Group lists/reports by category, hide unimportant categories — DONE (2026-09-24)

**Decided:** scope = all dashboard lists/reports; priority = item count (biggest category first).

**Built:** `frontend/src/groupByCategory.js` - a shared, pure, framework-free grouping helper.
Groups rows by their `category` field, sorts groups biggest-first, and folds any category with 2 or
fewer items into a single collapsible "Other categories" bucket at the end (collapsed by default -
nothing is ever dropped, just deprioritized and tucked away). Skips grouping entirely for short
lists (under 10 rows) so small tabs like Duplicates render exactly as before. Each category is
capped independently (20 items, "Show N more in this category" to reveal more) rather than one
global page limit - a single global cap would mean the very first page is entirely eaten by
whichever one category happens to be biggest (real data: Dead Inventory has 2,631 items and its
top category alone has 426 - a flat 200-row cap would show only that one category and never reveal
the other 32 exist). Wired into:
- `FixIssues.jsx`'s `IssueTable` (covers all 4 tabs: Missing barcodes, Invalid barcodes,
  Duplicates, Dead inventory) - existing search/sort/bulk-select/CSV-export all still operate on
  whatever's currently visible, unchanged.
- `InventoryReports.jsx`'s `ReportTable` (covers all 4 reports: Price change log, Margin report,
  Vendor price comparison, Negative inventory).

Reorder Candidates already got vendor-grouping (items 3/4 above) - layering category grouping on
top there was left out as unnecessary/over-complicating on top of an already-grouped view.

**Mobile:** not touched - this was a pure frontend (React) change, no backend/API response shape
changed, so there's nothing for the mobile app to inherit here. If the mobile app's own equivalent
lists (Fix Issues, Reorder Candidates - see `MOBILE_APP_FIXES.md`) want the same treatment, that's
a separate, distinctly-scoped mobile-side request.

---

## 2. Click a product anywhere on the dashboard -> full detail view

**Requested:** 2026-09-24.

**What:** clicking a product row on any dashboard page opens a detail view with as much information
about that product as the system has (name, category, barcode(s), variants, vendor(s) and codes,
current on-hand, recent sales, forecast classification, etc).

**Where I'd leave this out, with reasoning:**
- **Already-expanded rows** - e.g. duplicate-product groups, which already list every variant
  inline. A second click-through to "more details" for the same data is two ways to see the same
  thing, not helpful.
- **Print/export views** (CSV exports, PDF purchase orders) - meant to be static, scannable
  documents; a click target means nothing outside the app.
- **Bulk-action contexts** - rows selected via checkboxes for a batch close/add-to-cart action.
  A row-wide click-to-detail handler risks accidentally opening a modal instead of toggling
  selection, unless the click target is deliberately narrowed (e.g. only the product name is
  clickable, not the row/checkbox).
- **Historical/denormalized references** - e.g. an old purchase-log entry naming an item that's
  since been deleted from the catalog. There may be no live product left to show a detail view of.

Everywhere else (Fix Issues, Reorder Candidates, Supplier Projection, Dead Inventory, Vendor
Calendar item lists) - click opens a modal/drawer with full detail, without navigating away from
the underlying list/filter state.

**Mobile:** not carrying this over automatically. "The dashboard" here means the wide web layout,
and a phone screen already shows less per row, so a full-detail drill-down is probably wanted on
mobile too, but as its own separately-scoped request - a modal vs. a full screen push is a
different interaction pattern, not a direct port.

---

## 3. Reorder Candidates: group by vendor, tag each vendor with an urgency label — DONE (2026-09-24)

**Decided:** all dashboard lists in scope for item 1 eventually; here, a vendor's status is its
single worst item's status.

**Built:** `backend/app/services/reorder.py` - `_urgency()` classifies each candidate as
**Critical** (on hand at/under 0), **Order now** (would run out before half the lead time has
passed), **Can wait** (would run out sometime within the lead time), or **Low priority** (more
days of cover than the lead time itself - only on the list because of the safety buffer). Renamed
the placeholder "Skip" wording to "Low priority" to avoid colliding with the unrelated "Skip this
order" feature on the Vendor Calendar (MOBILE_APP_FIXES.md item 7).
`group_candidates_by_vendor()` groups by vendor (splitting multi-vendor `supplier` strings the same
way Supplier Projection does - an item with several vendors counts under each, since this is a
read-only planning view, not a cart with the single-section conflict mobile has), takes the worst
item's tier as the vendor's tier, and sorts vendors worst-first.
`GET /api/ordering/reorder-candidates` now also returns a `vendors` array alongside the unchanged
`candidates` array (backward compatible). Frontend: `ReorderCandidates.jsx` now renders vendor
groups instead of one flat table.

**Mobile:** still queued in `MOBILE_APP_FIXES.md` item 10 - the backend data is ready, the app
doesn't consume it yet.

---

## 4. Click a vendor (in the new grouped Reorder Candidates view) -> vendor detail — DONE (2026-09-24)

**Built together with #3.** Clicking a vendor (a native `<details>/<summary>`, matching this app's
existing expand-for-detail pattern elsewhere) shows: days of stock cover left until the next order
would arrive (`protection_days`, from the same Vendor Calendar schedule Supplier Projection
already reads), the last time a PO was placed for that vendor from this app (`PlacedOrder` table),
and an estimated $ cost of the pending order (shortfall × each item's `cost` from the latest
inventory snapshot, summed - `null`/"-" when cost data is missing). Vendor contact info was left
out: `vendors_reference` is scaffolded but empty (no Toast Purchasing API scope yet - see
`reorder.py`'s own module docstring), so there's nothing real to show there yet.

**Mobile:** still queued in `MOBILE_APP_FIXES.md` item 10, same reasoning as #3.

---

## Not a website item

"Camera option for scanner" (from the same request batch this file came from) is mobile-only -
logged directly in `MOBILE_APP_FIXES.md` instead.
