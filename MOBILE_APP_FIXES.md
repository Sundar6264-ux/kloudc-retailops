# Mobile App Fixes

Pending fixes for the `spicetown-mobile` app (Spice Town StoreOps), found while working on
spicetown-backend. The app is live in daily use, so batch these into one build, test it on
TestFlight first, then release. Tick each item off here once it ships.

---

## 1. "Add to cart" never sends the supplier code

**Found:** 2026-09-23, from Babco items added to the cart from the phone with a blank Supplier Code.

**Problem:** the three "Add to cart" buttons call `cartRepository.addItem(...)` without
`supplierItemId`. As a result, the Purchase Order PDF's Supplier Code column is blank for anything
added from the phone. The web dashboard sends the code correctly.

Affected files:
- `lib/features/suppliers/screens/supplier_projection_screen.dart` (`_addToCart`, ~line 239)
- `lib/features/reorder/screens/reorder_candidates_screen.dart` (`_addToCart`, ~line 54)
- `lib/features/reorder/screens/reorder_detail_screen.dart` (`_addToCart`, ~line 33)

**Fix:**
- Add a `supplierItemId` field to `SupplierProjectionItem`
  (`lib/features/suppliers/models/supplier_projection_item.dart`), read from
  `json['supplier_item_id']`. The backend already returns it per item, correct for the chosen
  supplier.
- Pass `supplierItemId: item.supplierItemId` in the projection screen's `addItem` call.
- Reorder candidates: the backend doesn't return a supplier code there yet. Either leave it to the
  backend fallback below, or add the field to the reorder endpoint and the model first.

**Backend workaround (already done):** `backend/app/services/po_cart.py` now fills in the code
from the latest inventory whenever an add arrives without one. So this is already fixed for
current app builds. The app fix is for correctness and consistency, not urgency.

---

## 2. Supplier code isn't shown on the Supplier Projection screen

**Problem:** because `SupplierProjectionItem` doesn't read `supplier_item_id`, staff can't see an
item's vendor code while building an order. The web dashboard shows it.

**Fix:** once item 1 adds the field, display it on each projection card (e.g. a small
"Code: MEANC2LB" line), and show nothing when it's blank.

---

## 3. Reorder "Add to cart" can create a cart section named after several suppliers

**Problem:** the Reorder screens use `c.supplier ?? 'Unassigned'` as the cart section. The backend's
`supplier` for a reorder candidate is Toast's raw supplier field, which can list several vendors
(e.g. `"Babco;JMR Distributors LLC"`). Adding such an item from the phone would create a separate
cart section literally named `Babco;JMR Distributors LLC` instead of putting it under one vendor.
Items with no supplier land in an `Unassigned` section. This hasn't happened in real data yet, but
it can.

**Fix (pick one):**
- Split on `;` and, when there's more than one, ask which supplier to order from (simple picker);
  or
- Default to the first listed supplier and let the user change it on the cart screen.

---

## 4. Default cart quantity differs from the web dashboard

**Problem:** on the web, new cart adds from Supplier Projection default to **qty 1** on purpose (the
projected need is shown in its own column, not silently used as the quantity). The mobile app
defaults to the projected need (`needToOrder`, rounded up), and Reorder defaults to the shortfall.
The same action gives different quantities depending on the device.

**Fix:** decide which behaviour is wanted for both, then make the app match. If it's qty 1, change
the `qty:` argument in the three `_addToCart` methods above.

---

## 5. Show the new forecast details on the Supplier Projection screen

**Found:** 2026-09-23, when the backend switched to the availability-adjusted forecast
(`backend/app/services/demand_forecast.py`).

**Already working with no app change:** the app's existing fields now carry the new numbers.
`avg_daily_demand` / `avg_weekly_demand` are demand while the item is on the shelf,
`projected_demand` is summed day by day with weekday and trend factors, and `need_to_order` is now a
whole-unit recommended order that includes a small safety buffer and treats negative on-hand as 0.
The app's default cart qty (`needToOrder` rounded up) therefore becomes the recommended order.

**Optional app additions** (the backend already returns these, and older builds ignore them):
- Each item has a `forecast` object with `classification` (regular / intermittent / slow / dead),
  `availability_pct`, `stockout_days`, `sellout_days`, `recent_7_day_sales`,
  `recent_14_day_sales`, `trend_factor`, `dow_factors`, `flags` and `needs_review`. Show a
  "Review" badge when `needs_review` is true, and the details on tap.
- Each projection also has `safety_buffer`, `target_inventory` and `shortfall`.
- The top-level `protection_days` is the number of days the vendor's delivery schedule says stock
  must last. Offer it as a one-tap horizon, as the web page does.

Affected files: `lib/features/suppliers/models/supplier_projection_item.dart` and
`lib/features/suppliers/screens/supplier_projection_screen.dart`. Parse every new field as optional,
because `method=legacy` responses (and the `PROJECTION_METHOD=legacy` kill switch) omit `forecast`.

---

## 6. Show the colour-coded item type next to each item

**Requested:** 2026-09-23, to match the web Supplier Projection table.

**What:** a small coloured pill after the item name on each Supplier Projection card, from
`forecast.classification`:

| Type | Colour (web) | Meaning |
|---|---|---|
| regular | green | sells most days |
| intermittent | amber | sells now and then |
| slow | blue | only a handful of sales |
| dead | red | no sales in 60 days while in stock |

Show nothing when `forecast` is missing (`method=legacy` responses or the kill switch). This
overlaps with item 5, so ship them together.

---

## 7. Skipped vendor orders on the Vendor Calendar screen

**Found:** 2026-09-23, when the website got "Skip this order" on the Vendor Order Calendar.

**What current builds do (safe, nothing breaks):** calendar events now carry `skipped` (bool).
- A skipped order-due event has `urgent: false`, so the app shows a plain "Order due" chip.
- A skipped delivery has `status: "skipped"`, which the app's `switch` falls through to
  "Upcoming delivery".

**Fix:**
- Parse `json['skipped'] as bool? ?? false` in `VendorCalendarEvent`
  (`lib/features/vendors/models/vendor_calendar_event.dart`).
- In `_EventTile` (`vendor_calendar_screen.dart`), show "Order skipped" / "Delivery skipped" with
  a neutral chip, and add a `'skipped'` case.
- Optional: offer Skip / Undo from the tile. The endpoints are
  `POST /api/vendor-calendar/skips` `{vendor, delivery_date}` and
  `DELETE /api/vendor-calendar/skips?vendor=&delivery_date=`, and `delivery_date` is the event's
  `for_delivery_date`.

---

## 8. Fix Issues: drop kitchen categories, close items with a reason, closed list and report

**Requested:** 2026-09-23. Already built on the website (Reports > Fix Issues).

**What current builds do (safe, nothing breaks):** the app's Fix Issues screen still reads
`/api/reports/missing-barcodes`, `/invalid-barcodes`, `/duplicate-products` and `/dead-stock`, which
are unchanged. So the phone still shows Street Kitchen / Catering Menu items and doesn't hide items
closed on the website.

**Fix:** switch the four cards and their list screens to the new endpoints (all gated on
`inventory_reports`, same as today):
- `GET /api/issues/summary`: `{items: [{issue_type, label, open_count, closed_count}]}` for the four
  counts. `issue_type` is `missing_barcode`, `invalid_barcode`, `duplicate` or `dead_stock`.
- `GET /api/issues/{issue_type}`: `{open: [...], closed: [...], open_count, closed_count}`. Each row
  has the same fields as the old report plus `issue_key`. Street Kitchen and Catering Menu are already
  left out. Duplicates come one row per name with `copies`, `prices` and `variants`. An open row may
  carry `came_back` (it was closed as fixed but the next upload still shows it), so show a
  "Still showing after fix" badge. Closed rows add `reason`, `reason_label`, `reason_detail`,
  `closed_by`, `closed_at` and, sometimes, `no_longer_detected`.
- `GET /api/issues/reasons`: the reason list per type, as `{key, label}`, so the app doesn't hardcode
  it.
- **Swipe left on a row** to close it: show the reasons as a multiple-choice sheet. If `other` is
  picked, require a text box. Then call `POST /api/issues/close`
  `{issue_type, issue_keys: [...], reason, reason_detail}`. A 400 carries a readable `detail`.
- An Open / Closed toggle on each list, with Reopen on closed rows: `POST /api/issues/reopen`
  `{issue_type, issue_keys}`.
- Optional: a "Closed report" screen from `GET /api/issues/report?start=&end=`, which returns
  `{total, by_reason, days: [{date, total, by_type, items}]}`, newest day first.

"Not an issue" hides an item from its list for good. A "fixed" reason comes back if the next upload
still shows the problem. The server handles both, so the app only needs to show what it gets.

---

## 9. Camera option for the global product search

**Requested:** 2026-09-24.

**Current state:** the app already has camera-based barcode scanning
(`lib/features/scanner/screens/barcode_scanner_screen.dart`, via `mobile_scanner`), but only under
the Scan tab, for label lookup/printing. The separate global product search
(`lib/features/search/screens/product_search_screen.dart` - "every other screen's item picker",
per its own doc comment) is text-only, with no camera entry point.

**What:** add a camera-scan option to that global search, so any flow that opens the item picker
(Reorder, Supplier Projection add-to-cart, etc.) can scan a barcode instead of typing a name.
Likely the simplest version reuses the existing `MobileScannerController`/`BarcodeScannerScreen`
plumbing and feeds a successful scan into the same lookup the text field already calls, rather than
building a second scanning implementation.

**Open question:** confirm this is the intended scope (adding a scan option to the *global* search)
rather than a different scanner-related screen - the request as given was just "camera option for
scanner," and the app already has one scanner surface.

---

## 10. Reorder Candidates: group by vendor, tag with an urgency label, tap a vendor for detail

**Requested:** 2026-09-24. Full spec lives on the website side, in `WEBSITE_REQUIREMENTS.md` items
3-4 (also website work, not built yet there either) - logged here too since
`reorder_candidates_screen.dart` already exists in the app and should stay in sync with the web
grouping/labels once the backend supports it, rather than falling behind the way Fix Issues briefly
did (item 8 above).

**Blocked on:** the backend/website side landing first - there's no vendor-status field or grouping
to consume yet.

## 11. Product categories screen now edits PO sections: relabel it

**Requested:** 2026-09-30. `/api/categories/*` now edits PO SECTIONS (the heading each item prints
under on the vendor PO PDF), in the exact shapes build 5 parses, so build 5 already works: an item's
"category" is its section, "Category group" always says PO section, subcategory is ignored, and
"Toast" on a tile means the automatic section. Build 5 was verified against the new API with a
temporary contract test (every call parsed and accepted, release folder left clean).

**For the next build:** rename the screen to "PO sections"; show one "Section" field (dropdown of
`/api/categories/options` combos + type a new one) instead of group / category / subcategory; say
"Automatic" instead of "Toast" and "Back to automatic" instead of "Reset to Toast"; optionally show the
section heading groups in the cart (`section` / `section_source` on each `GET /api/ordering/cart` line)
and a Section control per cart line (`PUT /api/categories/cart-lines/{id}` `{section}`). The help text
and glossary in the dev repo already describe PO sections.

## 12. Add items sheet, "all products": hide Container rows, show shelf + container stock

**Requested:** 2026-10-07 (found while building the website's multi-item add). With "Only <vendor>
products" off, the sheet's catalog search lists an item's bare Container-location row as a second result
with the same name and no vendor, and catalog results show the shelf count alone (`on_hand_qty`), while the
cart line it becomes shows shelf + container.

**Backend (additive, live once deployed):** every `GET /api/inventory/items/search` result now also has
`in_stock_qty` (shelf + Container row - the cart's "(in stock: N)" figure), `container_qty`, and
`container_of` (set on a bare Container row that duplicates a priced item). Existing fields are unchanged,
so installed builds are unaffected.

**For the next build:** in `add_cart_item_sheet.dart`, leave out catalog results with `container_of` set,
and show `in_stock_qty` (falling back to `on_hand_qty` on an older server).

---

## Release checklist

- [ ] Make the fixes above in `spicetown-mobile`
- [ ] `flutter analyze` and tests pass
- [ ] Test on a real phone: add Babco items from Supplier Projection and Reorder, and confirm the
      cart shows the code and the PO PDF prints it
- [ ] Bump the build number, upload to TestFlight, and test there before the production release
- [ ] Update this file (tick items / note the build that shipped them)
