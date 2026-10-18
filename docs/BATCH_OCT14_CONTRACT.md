# Oct 14 batch: before the App Store submission (2.0.0 build 8). Contract.

The owner's list, website and app both, with the decisions behind each item:

## W1. Today tab: act on orders without opening anything
- **Critical tile.** The Close / Skip this week / Remind me later options are already built but sit behind the ⋯ on
  the tile. Make them quicker:
  - each vendor row in the Critical sheet gets visible action buttons (or swipe actions) with clear labels;
  - add **"Order already placed"** as its own first-class option, beside Close's reasons, Skip this week, Remind
    me later and Other.
- **Orders due tile** (orange, vendor calendar `order_due`) gets the same options per vendor/order:
  - **Skip this week:** uses the existing vendor calendar skip (`vendor_order_skips`).
  - **Order already placed:** marks this due order as handled for this cycle. Record it like "Mark placed", as
    a placed order with source "outside the app" or as a handled flag, whichever fits the data model. Decide,
    document it, and keep Orders due and the 09:00 push consistent.
  - **Other (with note):** hides it for this cycle.
  - **Undo** in each case.
- Admin and store-wide behaviour is the same as the critical dismissals. These actions also appear on the
  website's Vendor Calendar and Reorder pages where those items show.
- **Migration 0053** if needed.

## W2. Search, cart sections, PO email CCs
- **Search by product ID and supplier product ID everywhere a product search exists** (app and website).
  - Every item search, product picker and lookup must also match the Toast item ID/GUID and the supplier
    (vendor) item ID / SKU (`supplier_item_id`; `';'` multi-values), plus the barcode where relevant.
  - **Places to cover:** cart add, Reorder search, Supplier Projection search, PO sections, Fix Issues search,
    Items Sold, the label/product search, Upload invoice vendor/product pickers if they search products, and
    feature search's product results.
  - Find every one; the backend search endpoints get additive matching.
- **App cart:** the same collapsible vendor sections as the website, if the app doesn't already have them.
  - Collapsed by default, a summary header, expand/collapse all, remembered per device.
  - Open the vendor and highlight "Just added" after adds from Reorder / Add items.
- **Send PO: email CCs.**
  - **Default CCs** per vendor contact (stored with `vendor_contacts`).
  - **Per-send CCs** in the preview dialog, prefilled from the defaults and editable, with email validation and a
    maximum of 10.
  - CCs are shown in the send log.
  - App and website.
  - **Migration 0056** if needed.

## W3. On-hand quantity beside sales in projections
- Where Supplier Projection, Reorder or item details show sales by period (e.g. "Jun 1-Jun 7: sold 10 units"),
  add the **average on-hand quantity** for that period beside it: "avg on hand 24". Only when known.
- **Source:** daily `inventory_snapshots` (CSV and API, Container pair included as on hand). Count only days with a
  tracked quantity. Show "-" when there's no data. Say how many days the average is based on, e.g. "(6 of 7 days)",
  when partial.
- Backend additive fields; app and website display.

## W4. Fix Issues by category, with leads and auto-assigned tasks
- **Owner's decision:** "category" can be either Toast category or PO section. **Use PO sections** (the store aisles:
  "Spices - Whole", "Frozen - Sweets & Desserts" and so on). They match how leads are split in the owner's
  document, and items already have them.
- **Fix Issues lists** (app and website) show items **grouped by PO section**, with the section name small above each
  group.
  - **Filters:** section(s), vendor, lead, and whether a task has been assigned.
  - Keep the counts, search and existing close flows.
- **Category and vendor leads:** an admin page (website and app) to assign a lead (user) per PO section and per vendor.
  - **Seed it** from the owner's file `~/SpiceTown Operations - Quick-Reference.pdf`. Read it with pypdf: the
    `/Users/sundar/spicetown-dev/website/backend/.venv` python has pypdf. The leads are:
    - **Amandeep (Frozen & Departments):** all frozen foods, ice cream, cold drinks, fresh sweets, household
      items, health & beauty, tobacco, halal meat, seafood, Street Kitchen. Vendors: Best Foods, Advance Food,
      Shata, Vadilal, Piece of India, Prestige, Jyena, Chandigarh, OnTime, Jain Foods, Kitchen Xpress, Desi Nation,
      Manzar, Empire Halal, Harbs, Ganesha, Restaurant Depot.
    - **Sai (Produce & Grocery):** fresh produce, rice, atta, noodles, oils, ghee, pulses, rotis, batters, RTEs,
      masalas, pickles, sauces, dry fruits, salt, jaggery, tea/coffee, canned foods. Vendors: Chetak, HOS (House
      of Spices), Radhey Foods, Real Foods, RBest, Aromiraa, Concept Foods, Indigenous, KP Produce, Aldi's.
    - **Harshitha (Snacks & Specialty):** snacks, bakery, chikkis, dry sweets, poha, phool makhana, murmuras,
      mukhwas, flowers, puja supplies. Vendors: Raja Foods, Babco, Rimaray, Mysha, Sai Florals, Shaz
      International, Priyems, Arya, JMR, Vijay Home Foods, Shahi Kulfi.
  - **Mapping:** map the document's departments to the 48 PO sections, and the vendor names to inventory vendor
    names. Show the proposed mapping on the admin page for the owner to adjust.
  - **Users:** match to existing users by name: Amandeep, Sai (Sai Durga?), Harshitha. Unmatched means unassigned,
    and the page says so.
- **Auto-assigned daily tasks.**
  - **Who gets it:** every day (at an admin-set time, default 10:00 AM store time), each lead gets ONE Team task
    listing the open Fix Issues items in their sections. **The section lead comes first, then the vendor lead,
    then the admin** (owner's rule).
  - **Completion time** is set **by admins only**, per lead or per issue type: hours until due (default 6 h).
  - **What's in the task:** a link or deep link to Fix Issues filtered to that lead. Items fixed or closed drop off
    the next day.
  - **Never duplicate:** update today's task if it already exists.
  - Team tasks get overdue reminders as usual.
- **Migration 0054.**

## W5. Team tab: richer features (app and website, plus backend)
- **Task templates and recurring.** Reusable tasks with steps (e.g. "Open store") that repeat daily or weekly per team
  or person, with optional assignment by day of week. Check what `recurrence.py` / task templates already do, and
  extend rather than duplicate.
- **Read receipts and mentions:**
  - who read an announcement or message (seen-by list);
  - @mentions with a notification to the person mentioned;
  - pin important messages in a channel.
- **Chat:**
  - photos (check what exists);
  - **voice notes** (record, send, play, with mic permission in context and an Info.plist usage string);
  - **documents** (PDF);
  - **message search** within the chats you can see;
  - all moderation and reporting rules still apply.
- **Task board and stats:**
  - board view (To do / In progress / Done) with drag or tap to move;
  - an overdue view;
  - per-person completion stats for admins.
- The App Store rules for user-generated content stay intact: report and block on new content types, plus the word
  filter on text.
- **Migration 0055.**

## W6. Better invoice scan image quality (app)
- **Investigate the pipeline:**
  - the document scanner (VisionKit via `cunning_document_scanner`): resolution and quality settings;
  - image processing in the scan review screen;
  - letter-page PDF generation: DPI, JPEG quality, grayscale or binarisation, any downscaling;
  - upload compression.
- **Fix it:**
  - full camera resolution;
  - a sensible PDF DPI (200-300) at high JPEG quality, keeping file size reasonable (about 1-3 MB a page);
  - optional auto-enhance or contrast for receipts;
  - keep colour unless a "B&W document" toggle is on;
  - keep OCR accuracy.
- **Show before and after** on sample images: `/Users/sundar/spicetown-dev/invoice-samples-*` and
  `/Users/sundar/spicetown-dev/invoice-eval`.
- The server-side Toast letter-page rebuild must keep the quality.

## Rules for all
- Python 3.9 and `UTCDateTime`. Responses are additive only, because builds 5-7 are live.
- The demo guard default-blocks new writes. Demo-account App Review safety matters: this build goes to Apple.
- Mock Toast and messaging in tests; never send real pushes, WhatsApp or email.
- Never write to the prod DB; use read-only copies.
- Use `nice`, and run flutter tests with `--concurrency=2`.
- Stage only your own hunks. Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- **Migration numbers:** W1 0053, W4 0054, W5 0055, W2 0056. Chain them in that order. If a lower number isn't
  there yet when you create yours, base it on the current head and note it; the final stage fixes the chain.

## As built - W1 (Today-tab order actions)

**Decision: "Order already placed" is a placed order with source "outside"** (not a separate handled flag). Migration
**0053** (`down_revision = "0052"`, additive columns only): `placed_orders.source` ("outside"; NULL = placed from the
app), `placed_orders.note`, `placed_orders.for_delivery_date`; `vendor_order_skips.reason` (NULL = Skip this week,
"other" = Other), `.note`, `.skipped_by_user_id`. Why: everything that already follows Mark placed follows it with no
second rule to keep in step - the Vendor Calendar's `ordered`, Reorder Candidates' "Ordered" tier (so the vendor
leaves the Critical tile and the 9 AM critical push by itself), the 9 AM order-due push and the Team vendor-order
task. "Other" is a skip of that cycle with a note, so the same holds for it (and released app builds, which leave
out `skipped` orders, hide it too).
- **API** (`routers/order_actions.py`, `services/order_actions.py`; admins only, the demo login refused by the
  guard - not allowlisted):
  - `POST /api/order-actions` `{vendor, action: "placed" | "skip" | "other", delivery_date?, note?}`. skip / other
    need `delivery_date` (the order-due's `for_delivery_date`; it must be a delivery day on the vendor's schedule,
    else 400/404); other needs a note (<= 500). placed: with `delivery_date` (Orders due) the order counts for exactly
    that delivery; without (Critical) it counts like Mark placed. Vendor spelling = its schedule's, else Toast's
    (404 for an unknown one). The same cycle marked twice (or a double tap within 2 min) returns the row already
    there (`duplicate: true`). Answer: `{action, action_label, vendor, delivery_date, order_id, note, handled,
    duplicate, message}`.
  - `DELETE /api/order-actions/placed/{order_id}` - Undo: deletes the row, only a source "outside" one (400 for an
    order placed from the cart, 404 when already gone).
  - `DELETE /api/order-actions/skip?vendor=&delivery_date=` - Undo Skip / Other (404 when not skipped).
  - The old `/api/vendor-calendar/skips` routes are unchanged (anyone with the Vendor Calendar can still skip).
- **Additive fields:** order-due events: `handled {kind: skip | other | placed_outside | placed, label, note,
  by_name, at, order_id, can_undo}`, `ordered_source`, `ordered_order_id`, `skip_reason`; delivery events:
  `skip_reason`, `skip_note` (status stays "skipped"). Reorder vendor groups: `ordered_source`. Placed orders
  (`/orders`, `/orders/recent`): `source` ("app" / "outside"), `source_label`, `note`, `for_delivery_date` - an
  outside order has no items (Order again adds nothing; older app builds show it as 0 items). GET
  `/api/reorder/critical-dismissals`: `placed_outside` (vendors Ordered right now because of an outside order).
- **Calendar matching:** a placed order with `for_delivery_date` counts for exactly that delivery; the rest keep the
  window rule (placed after the previous delivery, up to this one).
- **"Skip this week" on an Orders due row skips that order cycle** (the existing per-delivery skip). Every prod
  schedule has one delivery a week, so that is the week; a twice-a-week vendor would skip just that one order.
- **Glossary:** `order_placed_outside`, `order_due_other` (the app's bundled copy is left to the final stage).
- **Website:** Reorder Candidates' critical rows: Order already placed (saved at once; the row shows "Ordered -
  placed outside the app" with Undo; an Undo bar), Skip this week, Remind me later, Close, Other (= Close with reason
  Other). The Hidden from Today panel lists "Marked as already placed". Vendor Calendar: an order-due chip's menu
  offers Order already placed / Skip this week / Other... (note) for admins (Skip only for others, through the old
  route), shows how an order was handled, Undo there and on an Undo bar; chips read Ordered / Skipped / Hidden; on
  phones the menu is a bottom sheet. Overview's schedule is unchanged (it already shows skipped / ordered).
- **App:** the Orders due tile gets a "⋯" / long-press for admins (not demo): the **Orders due** sheet lists each
  order due today/tomorrow with Order already placed, Skip this week, Other (note sheet) and Review order, then
  "Dealt with (N)" with Undo (an order placed from the cart says to undo it on Placed Orders); an in-sheet Undo bar,
  moving to a Home snackbar if the sheet closes. Changes show at once (`OrderDueLocalChanges`, same generation rule as
  the Critical sheet); `todayOrderDueLoadProvider` is the fetch (Home refresh and schedule edits invalidate it). The
  Critical vendors sheet's rows get **Order already placed** (vendor leaves the list; "Marked as already placed (N)"
  with Undo) and **Other** (Close with Other picked). Vendor Calendar screen: an Other skip reads "Order hidden
  (Other)" with its note. Help updated (app and website).
- **Tests:** backend `tests/test_w1_order_actions.py` (12: each action, Undo, validation, calendar/pushes/reorder/
  Team task agreement, permissions, demo); `test_ops_placed_orders.py` key set gains the 4 fields. Website
  `orderActionsModel.test.mjs` (8). App `test/features/home/today_order_actions_test.dart` (14),
  `order_actions_matrix_test.dart` (every device_matrix size); the critical tests now find the Close sheet's Other
  radio by type (there is also an Other button). Browser check (prod copy migrated to head, port 8810, 1280 / 375):
  `~/.claude/jobs/e26a1611/tmp/w1/scripts/w1ui.mjs` (+ `serve.sh`, `mkusers.py`), screenshots in `tmp/w1/shots/`.
- **Known:** the Vendor Calendar month grid is wider than a 375 px phone (it was before; the menu is now a bottom
  sheet so it never opens off screen). Under heavy load the 10 s Undo bar can close before the calendar reloads -
  Undo stays in the chip's menu.
- **Full runs (Oct 14, ~03:00 store time):** backend 1945 passed; the 5 failures are not W1's - the two
  `test_inventory_api_late_csv` night checks (they depend on the time of day), `test_store_vs_container` (another
  builder's work in progress) and the two glossary checks the final stage settles (bundled copy; an app `termKey`
  expression whose `'other'` literal the key scan read as a key was fixed in mobile 14b9e20). App: home / reorder /
  vendors / help / navigation_reachability - 401 passed; `flutter analyze` clean.

## As built - W2 (search by product / supplier IDs, app cart sections, PO email CCs)

**One code-matching rule, three copies with shared cases.** `backend/app/services/product_match.py`,
`frontend/src/productMatch.js`, `mobile/lib/core/utils/product_match.dart`; the cases live in
`frontend/src/productMatchCases.json` (backend and node tests read it; the app test is a copy).
- Codes = the Toast item id, every vendor SKU (`supplier_item_id`, split on `;`) and every barcode (split on `;`).
- Exact (case and spaces ignored; all-digit barcodes also without leading zeros, so a UPC-A typed as EAN-13 hits)
  beats partial. Partial needs 3+ characters for a SKU, 4+ for a barcode, and 6+ **with a digit or a dash** for the
  Toast id (GUIDs are hex: "facade" or "bae2a" must not match half the catalog).
- Name matching is unchanged everywhere (substring); a code match is an extra OR.

**Backend (additive only).**
- `GET /api/inventory/items/search` matches codes; results are ordered exact code -> name (the old order) ->
  partial code, still capped at 25. New fields `matched_on` ("name" | "item_id" | "supplier_item_id" | "barcode"),
  `matched_code`, `all_supplier_item_ids`. With `supplier=` the vendor filter still applies (any vendor's SKU can
  find the item; `supplier_item_id` stays that vendor's own).
- Server-side searches: price history (`/api/inventory/price-history`, vendor SKUs from the latest upload - the
  history loader stays column-only), PO sections (`/api/categories/items`), Store vs Container (+ the Container row's
  id and every vendor SKU), Placed orders (+ order lines' Toast ids), the Ask bot's `search`.
- Lists the pages filter on the device got `all_supplier_item_ids` + `barcode` (Toast's raw `;` strings): Reorder
  candidates (`_with_urgency_and_value`, both paths), Supplier Projection items, Items Sold rows (added in the router,
  so the CSV export is unchanged), Store vs Container rows (CSV unchanged - fixed column list). Fix Issues rows
  already carry `product.supplier_item_id` / `barcode`.
- Perf: the per-item check is a few string ops; `items/search` on the prod copy answered in ~0.7 s under heavy load
  (the time is `latest_inventory_by_item`, as before).

**Where each search got it.**
- Website: `useTableControls` gained `codeKeys` (true = item_id / supplier_item_id / all_supplier_item_ids /
  barcode; or getters). On: Supplier Projection (table + chip counts), Reorder (vendor groups), Fix Issues (row +
  `product`), Items Sold, Store vs Container (+ Container id), Reconciliation, Inventory Reports. Server-side ones
  (cart Add items, Reconciliation item picker, PO sections, price history, placed orders) come from the backend.
  Add items shows "Matches SKU 2659M" under a result found by a code. Placeholders say "SKU or barcode".
- App: Reorder (`ReorderCandidate.matchesSearch`), Supplier Projection (`ProjectionFilters.matchesSearch`, incl.
  the other vendors' codes), Fix Issues list, Missing barcodes (Toast id), Store vs Container, the add-to-cart
  sheet (vendor-only mode filters on the phone; "Matches SKU ..." in both modes), product search (feature search's
  product results) with "Matches ...", Log a purchase and PO sections (server). **Search Catalog (label printer
  lookup)**: the labels app only knows names/barcodes, so a query that finds nothing there by name is also run
  through `items/search`; a SKU / Toast-id hit's first barcode is looked up in the label catalog (best effort, max 5).
- Not covered: the app's Items Sold has no search box (none was added); the Upload Invoice pickers search vendors
  only, not products.

**App cart vendor sections** (`providers/cart_panels.dart`, `purchase_cart_screen.dart`).
- 2+ vendors: each starts collapsed; the header (tap / screen reader button with expanded state) shows the name,
  "N items · N cases · N units" (units only with a Case of > 1), removal-request count and the vendor's total. Send
  PO / PDF / Mark placed / Add item stay usable while collapsed. "N vendors · Expand all · Collapse all" above.
- **Deviation:** a cart with ONE vendor shows it open with no chevron (nothing to scan past; also kept every
  single-vendor screen/test unchanged).
- The vendor total moved from beside the name to under it: beside it, a 6-figure total at 2x text on an SE left
  the name ~40 px and broke every word into letters (the card measured 12,500 px tall).
- Open vendors are remembered per phone (shared_preferences `cart_open_vendors`, JSON list of vendor keys; junk /
  unreadable = all collapsed), pruned to vendors still in the cart.
- "Just added": every add (Reorder card / detail / Add all, Supplier Projection, similar products, Add items) marks
  the line (item id, or the typed name) in `cartJustAddedProvider`; the cart opens that vendor, scrolls to it and
  tints the lines with a "Just added" tag for 6 s, also for adds made while the cart is open (Add items). Marks
  wait up to 10 min for the cart to load them. The add messages from Reorder / Projection carry **View cart**
  ("View" at large text: a snack bar action never wraps and "View cart" overflowed an SE at 2x), which opens the
  cart on that vendor. The cart push (`route: cart`) now passes its `vendor` too. `PurchaseCartScreen(openVendor:)`;
  a vendor with nothing in the cart says "<vendor> has nothing in the cart right now."

**Send PO email CCs** (migration **0056**, `down_revision = "0055"`; additive columns only).
- `vendor_contacts.cc_json` (a contact's default CCs, max 10) and `po_sends.email_cc_json` (what a send used).
- Contacts API: `cc` in POST / PATCH (list; a pasted "a, b" counts as two), `cc` in `contact_out`. Invalid address
  or > 10 -> 422; duplicates (any case) and the contact's own email dropped. Admin-only as before.
- Preview: `default_cc` (the default recipients' CCs, contact order, minus To) and `max_cc` (10).
- Send: `cc` (list). **Decision:** CC accepts any valid address (the To list is still saved contacts only) - the
  owner asked for editable per-send CCs; the old "nothing typed can be emailed" rule now applies to To only. Same
  gate as sending (purchase_order_cart, never the demo login). Duplicates / To addresses dropped; > 10 or a bad
  address -> 422 and nothing is logged or sent. The email gets a `Cc:` header and the SMTP envelope is To + CC. A
  send without CCs makes exactly the old `send_email` call.
- Send log: `email_cc` on every send row (`/po-sends`, the send response). Shown in the website's Recent PO sends
  and sent notice, and in the app's new **Recent PO sends** list at the bottom of Vendor contacts.
- Website: CC chips + "Add a CC email" box (Enter / comma / Add; a typed address still in the box when pressing
  Send is added or stops the send), "N of 10", an address also ticked in To is struck through and not sent; Default
  CC field on the contact form; contact rows show "CC ...".
- App: the same in the Send PO sheet (`_CcSection`), hidden on an older server (no `max_cc` in the preview, which
  would silently ignore CCs); Default CC field in the contact editor; "CC: ..." on contact cards; confirm dialog and
  the sent message name the CCs.

**Tests.** Backend: `tests/test_w2_product_code_search.py` (shared cases + every endpoint above),
`tests/test_w2_po_send_cc.py` (contacts, preview, send, log, validation, migration up/down); `test_po_send.py`'s 0050
model check now ignores the two 0056 columns. Website: `src/productMatch.test.mjs`, `poSendModel.test.mjs` (+2).
App: `test/core/product_match_test.dart`, `test/features/w2_product_code_search_test.dart`,
`test/features/search/product_code_match_ui_test.dart` (matrix), `test/features/purchase_cart/cart_vendor_panels_test.dart`
(rules + screen + matrix), `send_po_test.dart` (CC group + matrix; its fakes take `cc`), `audit_c_cart_test.dart`
seeds both vendors open (its checks are about the lines). Browser check on a prod copy (port 8811, fake SMTP):
`~/.claude/jobs/e26a1611/tmp/w2/ui/` (`serve.sh`, `seed.py`, `check.mjs`; screenshots in `shots/`).

## As built - W3 (average on hand beside weekly sales)

Commits: website `0c0cf98` (backend + website; three placement slips in that commit were corrected in place and
landed inside `02cbcd4`), app `09f100f`. No migration.

- **Where "sales by period" is shown, and what changed.** The only per-period breakdown is
  `GET /api/ordering/item-weekly-sales` (`services/weekly_sales.py`). It was on Supplier Projection (web: Avg/day or
  Avg/week expands the row; app: the item's details sheet) and on the app's Fix Issues details sheet. Reorder and the
  website's Fix Issues drawer had none, so they gained one: Reorder (web) - Avg/day is now a button that opens the
  breakdown row for the report's lookback window; Reorder (app) - a "Weekly sales" card on the item detail, same
  window; Fix Issues (web) - "Weekly sales (last 12 weeks)" in the item drawer, like the app. Toast's rolling
  "last 7/30/90 days sold" lines are not periods we own and were left alone.
- **API (additive).** Each week gains `avg_on_hand` (1 decimal, null = no count that week), `on_hand_days` (days with a
  count) and `period_days` (days of that week inside the requested window - the first and last weeks can be partial).
  Top level gains `on_hand_includes_container`. New opt-in `include_empty_weeks=true` also lists weeks with no sale
  (quantity 0, revenue 0) so a week the item sat unsold or out of stock shows; leading weeks before the item's first
  count or sale are left out. Default off, so app builds 5-9 see exactly the old list. Gate widened from
  `supplier_projection` to `supplier_projection | reorder_candidates | inventory_reports` (revenue still stripped for
  Hide sales users; on hand is not sales and is kept).
- **The average.** Snapshot D is the opening count of business day D (`demand_forecast.py`), so the counts averaged
  for a Monday-Sunday week are the snapshots dated inside it (CSV, the Jun-Aug `backfill:` rows and `api:` rows
  alike - there is one row per item per date). Only days where the item's own row has a number count; the bare
  Container-location twin (same rule as `supplier_projection._container_qty_by_name`, looked up for one name on the
  latest upload) is added on the days it has a count. A Container count on a day the shelf row has none does not make
  a tracked day. Toast's number is averaged as is: a negative count stays negative (shown red), an API out-of-stock
  row is its stored 0.
- **Display.** Web `components/weeklySales/` (`weeklySalesModel.js` + 6 node tests, `WeeklySalesTable.jsx`): columns
  Week ("Jun 1 - Jun 7") / Sold / Avg on hand (i) / Revenue; "(6 of 7 days)" after a partial figure, "-" for none,
  "No sales in this window - stock counts only." when nothing sold, "On hand includes the Container count." when it
  does. On phones the breakdown sticks to the visible left edge of the sideways-scrolling item table and its labels
  wrap. App `features/suppliers/widgets/weekly_sales_table.dart`: the same columns, coverage under the figure, a note
  with the (i) under the table. New glossary term `avg_on_hand` (the app's bundled copy is regenerated by the final
  stage; `test_app_bundled_copy_is_current` fails until then).
- **Verified on a read-only prod copy (0052, Oct 14).** 5 real items over the 90 days to Oct 14, every week compared
  with an independent sqlite3 computation from the raw snapshots and orders: Amma Sona Masoori Rice 20lb and Laxmi
  Chakki Atta 20lb (Container twins: 16 shelf + 68 container etc.), Cilantro Bunch, Whole Chicken Clean & Cut (weighed,
  weeks with 1-3 of 7 counted days, one week with none, negative averages down to -875.4) and Deep Dahi Whole Milk 5lb.
  70 weeks, 0 mismatches (script `~/.claude/jobs/e26a1611/tmp/w3onhand/scripts/verify_prod.py`).
- **Tests.** Backend `tests/test_weekly_on_hand.py` (17). App `test/features/suppliers/weekly_on_hand_test.dart` (model,
  wording, and Reorder detail / Fix Issues details / Supplier Projection details on all 10 matrix devices). Playwright
  at 1280 and 375 on port 8812 (admin, a Reorder-only user, a Hide sales user): `scripts/ui.mjs`, screenshots in
  `~/.claude/jobs/e26a1611/tmp/w3onhand/shots/`.

## As built - W4 (Fix Issues by PO section, category & vendor leads, daily lead tasks)

Commits: website `54b109d` (backend), `3200427` (website); mobile `f7acd18` (app). Migration **0054**
(`issue_leads`, `issue_lead_task_runs`; revises 0053). Task settings are `setting_changes` rows (key
`issue_lead_tasks`, migration 0052) - no table of their own.

**Backend** (`app/issue_lead_models.py`, `services/issue_leads.py`, `routers/issue_leads.py`):
- Every Fix Issues row (open and closed, every type) gains additive `po_section` (the PO section it prints
  under - `po_section_rules`, item > vendor > automatic, renames applied; null for a closed item no longer in
  the upload), `lead` (`{user_id, name, via: section|vendor|fallback, via_name}` or null) and `lead_task`
  (`{task_id, day, status, due_at}` when the lead's latest daily task lists this item, else null). Summary items
  gain `open_by_lead` ({user id: count}) and `open_no_lead`; the summary response gains `leads` (every saved
  lead, by name). Old app builds ignore all of it.
- Who: section lead, else the first of the item's `;` vendors that has a lead (Toast's order), else the
  everything-else lead (kind `fallback`). A saved "No lead" (user_id NULL) falls through. **Only saved rows count**:
  until an admin saves the leads page nothing is assigned and no task is made.
- Proposal: the document was read once with pypdf and typed in as `DOC_LEADS` / `SECTION_PROPOSALS` (basis
  `document` when the department is named, `guess` when not - shown as "Guess - check"); the app never reads the
  PDF. Users matched by name (same username, else a unique first name: Sai -> "Sai Durga"); demo logins are never
  leads. Vendors matched by same letters / prefix / initials / near-identical core name (e.g. HOS -> House of
  Spices, Indigeneous -> Indigenous Foods, Prestige -> Prestige Concept LLC, never Concept Foods).
- Admin API (require_admin; demo writes refused by the global guard): `GET /api/issue-leads` (page: sections,
  vendors, fallback, people, document, settings, last run), `PUT /api/issue-leads` (`{sections:[{name,user_id}],
  vendors:[...], fallback:{user_id}}`, upsert; response = page + `saved_count`), `POST /accept-proposal` (saves the
  proposal for every unsaved row), `GET /preview`, `GET|PUT /settings` (`enabled`, `time` 06:00-20:00,
  `default_hours` 1-72, `hours_by_user`, `hours_by_type`, `types_off`), `POST /run-now`.
- Daily task: a rule inside `auto_tasks.auto_tasks_tick` (the 5-minute `tasks_tick`), once per store day from the
  set time (default 10:00), marked by a `job_runs` row `issue_lead_tasks` (business_date). One Team task per lead
  (`assignee_user_id`, created by the system user, `source_type` `issue_leads`, `source_id` `<user id>:<day>`),
  title "Fix Issues: N open items for you", description = counts per list, biggest sections and a link
  `<PUBLIC_BASE_URL>/#/fix-issues?lead=<id>`. Due = created + hours (lead override, else the shortest
  `hours_by_type` among the task's lists, else 6). Run again the same day (Run now): today's task is updated
  (title, text, due from its own created time), never duplicated. **Decision:** the next day's task replaces an
  earlier one still open (`cancelled`, "Replaced by the <day> list"); a lead left with nothing gets the old one
  closed `done` ("Nothing left on the Fix Issues list"); a task someone reopened is never closed by the system
  (`auto_tasks._close`). The legacy `dead_stock` list is never in a task. Overdue reminders are the normal task
  job's. Pushes: the normal `task_assigned`.

**Website**: Fix Issues groups every list by PO section (small upper-case name + count above each group;
alphabetical, Other / No section last; old servers keep category grouping), with filters: sections (multi),
vendor, lead (incl. "No lead"), task (in a lead's task / not yet), "N of M shown", Clear filters. Each row shows
"Lead: name" (+ "In task"). `?lead=<id>` filters every list and the tile counts, with a "Showing X's items" banner.
New admin page **/issue-leads** ("Category & vendor leads", sidebar admin block): tabs PO sections / Vendors /
Everything else, the proposal pre-filled and marked Not saved, one Save for everything shown, task settings,
Preview, Run now, the document card with unmatched vendors. Phone layout stacks rows. Node tests:
`components/issueLeads/issueLeadsModel.test.mjs` (9).

**App**: same grouping (`IssueSectionHeader`) and a Filters sheet (`widgets/issue_filters_bar.dart`, rules in
`fix_issues/issue_filters.dart`), lead line on cards, landing + lists open filtered to a lead
(`FixIssuesScreen(leadId:, leadName:)`), task detail "Open the Fix Issues list" for `issue_leads` tasks
(`TeamTask.sourceType/sourceId`, additive), More > Admin > **Category & vendor leads** (+ feature search entry).
`groupByCategory` gained an optional `compareCategories`. Tests: `test/features/fix_issues/w4_sections_leads_test.dart`
(models, filters, save rule, screens, full device matrix for the list + sheet, landing and leads screen).

**Prod copy (Oct 14, read-only `.backup` at 0052, migrated to head in the copy only)**:
- Users matched: Amandeep -> Amandeep (10), Sai -> Sai Durga (12), Harshitha -> Harshitha (9); everything-else
  (Sundar, per the document's "Inventory & Purchase") -> Sundar (11, admin).
- Sections (48 + Other): document-based for 39, guesses for 6 (Dairy -> Amandeep; Millets, Cereals & Oats and
  Baking & Cooking Essentials -> Sai; Chocolates & Candy, Papad, Plants & Seeds -> Harshitha). No proposal (left to
  vendor lead / admin): Baby Food, Pantry Staples (poha and murmuras sit with grocery staples), Organic &
  Specialty, Other (kitchen, catering, tobacco).
- Vendors: 37 of 38 document vendors matched to inventory names; **Sai Florals** isn't in the inventory.
  Inventory vendors with no lead in the document (18): Ansu Foods, Costco, Deen Distributors LLC, Delight Foods,
  Good Luck Traders Inc., JH GLOBAL TRADE LLC, Kotecha Brothers LLC, Mango Mania, Rithik Paan, Shakti
  Distributors, Singh & Singh, Skvl Organic World Inc, SpiceTown, SpiceTown - Street Kitchen, Sri Sai Imports,
  SS Grocers & Imports, Swadesi, **Vdyas (469 items)**.
- With the proposal accepted (105 rows), the next daily run would give: **Sai Durga 631** (605 via section, 26 via
  vendor), **Amandeep 624** (555 / 69), **Harshitha 442** (431 / 11), **Sundar 7** (everything else), 0 unassigned
  (1,704 open items). About 75% of each list is "Dead with stock" (Sai 473, Amandeep 483, Harshitha 325) - the
  owner may want to untick that list for the daily task (`types_off`) so the tasks stay workable.
- Speed: annotation adds well under 0.1 s to a list; the summary is ~2 s on the copy (the Fix Issues forecast
  warm-up is what's slow when cold, as before). Preview / a run = one summary's work.

**Not done / notes**: nothing is assigned on prod until an admin saves the page (deliberate). The website's Team
task drawer shows the link inside the task text (the app has a button). The app's bundled glossary was not
regenerated (no glossary terms were added).

## As built - W5 (Team tab: templates, board, stats, read receipts, mentions, pins, voice notes, PDFs, search)

Migration **0055** (`0055_team_advanced.py`, down 0054; plain ADD/DROP COLUMN, so `messages` / `team_media` keep
AUTOINCREMENT on downgrade - checked down/up on a prod copy). All API changes are additive; builds 5-9 keep working.

**Templates and recurring (extends `task_templates` / `recurrence.py`, no second system)**
- New columns: `items_json` (steps, copied as sub-items onto every task made from it), `day_assignees_json`
  (`{"SA": {"user_id": 3}, "SU": {"team_id": 2}}` - the store-local weekday of each occurrence's due time picks the
  assignee, other days use the template's own) and `repeats` (false = a saved template used only by "Create task
  now"; its `rrule` is `""` and the generator skips it). A weekday assignee who lost Tasks / an archived team skips
  only that occurrence (logged once).
- `POST /api/team/templates` / `PATCH` take `items`, `day_assignees` (object or `[{day, user_id|team_id}]`, `{}`
  clears), `repeats`; `template_out` adds `items`, `day_assignees` (with names), `repeats`, and `next.assignee`.
- `POST /api/team/templates/{id}/create-task` (assigners; same assignment rules as any new task): one task from the
  template (title, description, requires, steps) for its own assignee (today's weekday one) or whoever is sent.
  Linked by `source_type "template"` / `source_id`, NOT `template_id` (that stays the recurring occurrence key).
- Website "Recurring tasks and templates": steps (one per line), "On a schedule / Only when I create it", "Someone
  else on some days?" (7 weekday selects), "Create task now" per row. App: "Create from a template" sheet (assigners)
  with **Create now**; template editing stays on the website; the app's own "Repeat" option now saves sub-items as
  steps.

**Board, overdue view, stats**
- `tasks.started_at / started_by_*`: the board's In progress. The status stays `open` (older apps know nothing else).
  `POST /api/team/tasks/{id}/start {"started": true|false}` (whoever can complete it); events `started` /
  `unstarted`; reopen and reassignment clear it. `task_out` adds `started_at`, `started_by`, `board_column`
  (todo | in_progress | done | cant_do | cancelled), `can.start`, `can.unstart`.
- `GET /api/team/tasks?...&overdue=true`: open tasks past their due time.
- `GET /api/team/task-stats?days=7|14|30|90` (admins): tasks CREATED in the window, cancelled left out. Per person:
  assigned / their tasks done (completion rate) / everything they finished (team tasks count for the finisher) / on
  time vs late (vs `due_at`; no due time = on time) / overdue now / can't do / median minutes created -> done. Per team
  rows for team tasks. Totals.
- Website Tasks: List | Board | Stats (admins), "Overdue only". Board = 3 columns, drag-and-drop or the card's
  buttons (Start, Back to To do, Done, Reopen); a photo/note task dragged to Done opens its proof form; a long column
  shows 15 cards + "Show all N". App: second toolbar row (Board, Overdue, templates, stats); the phone board shows
  one column at a time (segmented, with counts) and moves by **tap** (buttons) - no dragging on a phone.

**Read receipts, mentions, pins**
- Announcements already had per-recipient seen / acknowledged for admins (`/announcements/{id}/recipients`) - unchanged.
  Messages: `GET /api/team/messages/{id}/seen-by` -> `{total, seen, not_seen}` (current members other than the sender;
  read marker >= the message). Website ⋯ › Seen by…, app long-press › Seen by, on my own messages.
- @mentions: new alert `chat_mention` ("Chat mentions", ALERTS, feature team_chat, route chat). The mentioned person
  gets "X mentioned you in Kitchen" instead of the plain chat push - even in a muted chat, never throttled, never
  across a DM block or into a hidden DM, one per message (`chat_mention:{message_id}`), not in quiet hours (like all
  chat pushes). With Mentions switched off they get the normal chat push rules (a mute then holds).
- Pins: `messages.pinned_*`; `POST|DELETE /api/team/messages/{id}/pin`, `GET /api/team/channels/{id}/pins`
  (`{pins, can_pin, max_pins}`). Who: either person in a DM; admins and `can_assign` users (shift leads) in a team chat.
  Max 20 per chat (409). A deleted message drops off. `message_out` adds `pinned`, `pinned_at`, `pinned_by`.

**Voice notes and PDFs in chat**
- `team_media.kind` image | audio | document, `display_name`, `duration_ms`, `page_count`. `POST /api/team/media`
  (unchanged route) routes by declared type; audio/PDF only for users with team_chat (others: refused as before).
  Only chat messages may attach them (tasks / announcements: photos only).
- Voice: AAC in MP4 only (`ftyp`, a `moov` with `mp4a`; duration from `mvhd`, else the recorder's `duration_ms`),
  10 MB / 5 min. **Stored as uploaded, not re-encoded** (no audio toolchain on the server - deviation from the image
  rule); served as `audio/mp4` + nosniff, only to channel members. PDF: `%PDF-`, 10 MB, <= 300 pages, the flyers'
  pypdf inflate cap, password-protected refused. The document NAME is kept for display (`display_name`: last path
  part, control/bidi chars dropped, word-filtered, `.pdf`) - deviation from design §9.1 "original filename never
  stored"; it's never used for the path.
- Media JSON: a photo keeps exactly its old shape (checklist tests pin it); a voice note / PDF adds `kind`,
  `content_type`, `bytes`, `name`, `duration_ms`, `page_count` (clients read a missing `kind` as image).
- Older apps: `media` stays photo-only; new `attachment` carries any kind; a voice/PDF message without text gets
  `body` "Voice message (0:12)" / "Document: x.pdf" with `body_is_label: true` (new clients draw the attachment and
  hide that text; search never matches it - the stored body stays empty). Push preview and channel list use the label.
- App: `record` 7.1.2 (AAC-LC, 64 kbps mono) + `just_audio` 0.10.6 (plays from a temp file fetched with the login);
  tap mic -> recording bar (time / 5:00, Cancel, Send); attach -> Photo / Document (PDF). Mic asked for only on the
  first tap. `NSMicrophoneUsageDescription` (Info.plist), `RECORD_AUDIO` (Android), PrivacyInfo `AudioData`
  (linked, app functionality). **Two new plugins: run `pod install` before archiving** (pubspec version unchanged).
- Website: plays voice notes; records only where `MediaRecorder` can make `audio/mp4` (Safari, recent Chrome/Edge),
  otherwise the mic is hidden and the hint says to record in the app. 📎 sends a PDF; Open shows it in a new tab.

**Search**: `GET /api/team/messages/search?q=&channel_id=&before_id=&limit=` - >= 2 characters, LIKE wildcards
escaped, case-insensitive, text or PDF name, newest first, paged; only chats the person sees in their list (not
archived team chats, not hidden/blocked DMs; admins don't search other people's chats); deleted messages never found.
Each result has `channel {id, kind, name}`. Website: 🔍 above the list (all chats, opens and flashes the hit) and
Search in a chat; app: search icon on the chat list and in a conversation; feature search entries `chat_search`,
`task_stats` (admins).

**UGC (App Review 1.2)**: voice notes and PDFs are messages, so Report / admin delete / retention cover them; the
moderation queue (website and app) shows `attachment` so managers can play / open it (admins still can after removal,
until retention blanks it). Word filter: message text, template steps, PDF names. Block unchanged (DMs). Demo guard:
`GET /task-stats` refused (403) for the demo login and its app button/search entry hidden (real staff's
performance); new writes default-blocked; allowlisted only `POST /tasks/{id}/start` (tasks assigned to it) and pin/unpin (its own,
already team-scoped chats); template create-task is refused for the demo.

**Tests**: backend `tests/test_team_w5.py` (32 tests incl. parametrized cases); website `teamAdvancedModel.test.mjs` (7) + Playwright on a prod copy
(port 8814) at 1280 and 375, 68 checks (board drag/tap, overdue, stats, templates + create now, voice play, PDF chip,
pins, search, seen-by, moderation, help, no page h-scroll); app `test/features/team/team_w5_test.dart` (models,
board moves, overdue, templates/stats, voice/PDF/pin/seen-by/search, recording send/cancel, device matrix for the
board, stats, conversation + recording bar, search).
**Note**: website commit 02cbcd4 (W5 website) also swept in another agent's already-staged hunks
(FixIssues.jsx, ReorderCandidates.jsx, SupplierProjection.jsx, api.js `include_empty_weeks` fix) - their work, kept
as is, just committed under the W5 message.

## As built - W6 (invoice scan image quality)

**Where the quality went (measured, not guessed).** Read on the store's real uploads (read-only) and 41 page images from
iOS Quartz scans (Apple's own VisionKit output, ~2300x3200 at JPEG 83 - the closest thing on disk to what the app's
scanner returns; VisionKit itself gives the app lossless PNG):
- The app's PDFs were 2200 px on the long side (~210 dpi on Letter) at **JPEG 70**, 250-450 KB a page. Apple's own scans
  of the same invoices were ~290 dpi at JPEG 83.
- **Scaling used nearest neighbour** (`image`'s `copyResize` default): whole rows and columns of pixels were dropped,
  so letters came out jagged/broken and QR codes garbled. Biggest single loss (SSIM vs the camera image 0.928 -> 0.948
  from the interpolation alone).
- **Re-crop** went through two extra generations: cut to 2400 px (nearest) at JPEG 90, cropped (JPEG 100), then scaled
  again to 2200 (nearest) at JPEG 70.
- **Black & white** was one global Otsu threshold: a shadow or a dim corner turned into a black slab.
- The VisionKit scanner itself was fine: camera path saves full resolution PNG (`imageOfPage`), no downscale (only
  gallery imports are cut to 2048, and the app opens the camera only). Upload is the PDF as built, no further
  compression.
- Server (`toast_pdf.py`): app PDFs with Letter pages pass through untouched. Rebuilt files (photos, odd-shaped old
  app PDFs) were scaled UP to a full 300 dpi Letter canvas (margins included) at JPEG 80: no information gained,
  twice the bytes, and the 5 MB budget then falls back to grayscale.

**App changes** (`scan_page_processing.dart`, `scan_review_screen.dart`, `invoice_scan_pipeline.dart`):
- `scanMaxLongSide` 2200 -> **3000** (~285 dpi on Letter), `scanJpegQuality` 70 -> **85**; Black & white pages at
  **JPEG 90** (`scanBwJpegQuality`, ringing around pure black text at lower qualities).
- Every downscale uses **area averaging** (`scanDownscaleInterpolation`), ordinary pages, tall pages, the OCR copy and
  the crop source.
- **Auto-enhance** chip on Review scan (off by default, any mode but B&W): `flattenBackground` (paper brightness
  measured on a coarse grid, 90th percentile per cell, 3x3 median, divided out, gain capped at 3x, dark cells left
  alone) + `stretchContrast` (1st/97th percentile, same curve on R/G/B so colour stays; flat pages untouched). Preview
  is a colour-matrix approximation, like the B&W preview. `ScanReviewResult.enhance`, `prepare(..., enhance:)`,
  `processScanPages(..., enhance:)` - all additive with false defaults.
- **Black & white = flattenBackground then Otsu** (so it ignores Auto-enhance; the chip is disabled there).
- Colour is kept unless Grayscale / Black & white is chosen (unchanged default: Colour).
- **Re-crop** source is now 3000 px (area averaging) at JPEG 95; the cropper writes JPEG 100; the final page is then
  not scaled again.
- Text recognition still reads a plain colour copy (2400 px, JPEG 90), now area-averaged; Auto-enhance never touches it.
- Help: the "Scanning a paper invoice" bullet mentions Auto-enhance and ~285 dpi.

**Server change** (`toast_pdf.py`): each rebuilt page's JPEG keeps the chunk's own pixels (only scaled DOWN to at most
300 dpi; a small photo is raised to `MIN_DPI` 200, never further), drawn on an exact 612x792 page at the same spot the
OCR text layer goes (Pillow writes the one-image page, pypdf places it; white margins cost nothing). Encodings
ladder: RGB 85/300 -> RGB 75/300 -> RGB 70/250 -> L 70/250 -> L 60/200 -> L 50/150 (quality goes before colour).
Text layer unchanged (verified identical extracted text on two real rebuilt receipts).

**Before / after** (all under `~/.claude/jobs/e26a1611/tmp/scan-quality/`: `out/before_after_summary.csv`,
`out/measurements.csv`, `out/server_compare.txt`, side-by-side crops in `out/visual/`; scripts in `scripts/`). OCR
accuracy = recall of the source image's own words / money amounts, Tesseract and Apple Vision; 32 distinct
ordinary pages:

| | KB/page (median) | dpi | SSIM | Vision words | Vision amounts | Tesseract words | Tesseract amounts |
|---|---|---|---|---|---|---|---|
| Colour before | 381 | 210 | 0.928 | 0.954 | 0.953 | 0.845 | 0.858 |
| Colour after | 856 | 286 | 0.978 | 0.976 | 0.962 | 0.869 | 0.875 |
| B&W before | 404 | 210 | - | 0.872 | 0.873 | 0.792 | 0.861 |
| B&W after | 798 | 286 | - | 0.880 | 0.915 | 0.840 | 0.882 |
| Colour + Auto-enhance | 839 | 286 | 0.971 | 0.971 | 0.966 | 0.854 | 0.896 |
| Re-cropped before / after | 404 / 813 | 210 / 269 | - | 0.924 / 0.916 | 0.925 / 0.948 | 0.813 / 0.840 | 0.766 / 0.845 |

Largest page 1.58 MB (was 0.69). Long receipts (4 narrow sources): B&W amounts 0.68 -> 0.72 (Vision), 0.27 -> 0.56
(Tesseract); Auto-enhance Tesseract amounts 0.72 -> 0.87. Known: on 3 low-res photos (471-1320 px) Auto-enhance and
B&W read slightly worse with Tesseract (n=3; Vision unchanged) - both are optional.
Server, 19 real files it rebuilds + 1 large photo: total 23.7 MB -> 13.8 MB, all pages still colour, Tesseract word
recall 0.593 -> 0.631, amounts 0.685 -> 0.753; new app PDFs (colour, B&W, long receipt) pass through byte-identical.

**Tests:** app `test/features/invoices/scan_quality_test.dart` (new: defaults, averaging vs nearest, flatten, enhance
keeps colour, B&W under a shadow, JPEG 90 B&W, size bound), review screen Auto-enhance + device matrix (10 shapes),
pipeline + submit sheet pass-through; backend `tests/test_toast_pdf.py` (+5: no upscale to 300, full-res kept at q85
in colour, tall chunks keep their width, colour kept before quality, dpi rules) - the new ones fail on the old code.
Not run on a device (no simulator in this task): VisionKit output size on the store's iPhones and on-phone timing
(desktop: +0.7 s a page for Auto-enhance, +1 s for B&W, in the background isolate) are worth a look on TestFlight.

## As built - Verify 1 (backend and data, adversarial pass over W1-W6)

Tests: `backend/tests/test_v1_batch_oct14.py` (9; each bug's test fails on the code as the builders committed it).
Prod copy: read-only `.backup` of the live DB at 0052, migrated in the copy only; deleted afterwards.

**Fixed**
- **0056 downgrade dropped AUTOINCREMENT** on `vendor_contacts` and `po_sends` (batch mode rebuilds the table
  without it; 0050 requires it - ids show in the UI and logs and must never be reused). Now plain
  `ALTER TABLE ... DROP COLUMN`, like 0055. 0053's batch downgrade is harmless (`placed_orders` /
  `vendor_order_skips` aren't AUTOINCREMENT; schema identical after down, checked on the prod copy).
- **"Order already placed" tied to one delivery leaked into other cycles.** The calendar and the 9 AM order-due push
  count a row with `for_delivery_date` for exactly that delivery, but the Team vendor-order task (`auto_tasks.
  _placed_since`) and Reorder's `ordered_vendors` (Ordered tier, Critical tile, 9 AM critical push, the Critical
  sheet's "Marked as already placed") still used only the day it was marked. Marking LAST week's (or next week's) chip
  on the website calendar on this week's order day therefore closed / never made this week's Team task while the
  calendar and push said the order was still due, and a retroactive mark made the vendor "Ordered" (hiding it from
  Critical) and shadowed an earlier real Mark placed. Now: `_placed_since(..., delivery_day)` counts a tied row for its
  own delivery only (whenever it was marked - also the reverse case: a row tied to this delivery marked weeks early
  closes this cycle's task, as the calendar shows it ordered); `ordered_vendors` skips a row tied to a delivery BEFORE
  the day it was marked (bookkeeping, not an order on the way). Rows without `for_delivery_date` (Critical tile, Mark
  placed) behave exactly as before.
- **Daily lead task for someone without Team Tasks** (W4): every other path that gives a person a task refuses that
  (`tasks_service._assignee_user`, `recurrence.problem_with`), the daily run didn't - the task was invisible to them.
  Now skipped; the run's answer gains additive `no_tasks_access` [{user_id, name, count}] and the job detail (shown as
  the leads page's last run) says "X can't see Team Tasks - no task (N items)". All three prod leads have Tasks.

**Checked, no change needed**
- Migrations 0052 -> 0053 -> 0054 -> 0055 -> 0056: one head; up, down to 0052 (schema equal to the original apart from
  SQLite's quoting of rebuilt tables), up again on the prod copy; integrity and foreign-key checks OK.
- Demo guard: every new write route (`/api/order-actions*`, `/api/issue-leads*`, `/api/team/templates/{id}/create-task`)
  is refused for the demo login; the three allowlisted (`tasks/{id}/start` with the assigned-task check, pin / unpin)
  are member-scoped by the route (`get_message_for(allow_admin=False)`, `can_pin`: the non-admin demo can pin only in
  its own DMs). The prod demo login (`app_store_review`) is not an admin.
- **Old app builds parse today's JSON**: builds 5 (9985842), 6 (fd00501), 7 (2ad4fbc) and 9 (7bc7612), each build's
  own models in its own package, fed real answers from a seeded prod copy (an outside order with no lines, an Other
  skip, saved leads + lead tasks, a started task with steps, voice note / PDF / pinned messages, chat_mention in prefs):
  vendor calendar, recent + paged placed orders, reorder, projection, weekly sales, Fix Issues summary and three lists,
  task list + detail, channels, messages, notification prefs - 7/7 per build. Released builds never send
  `include_ordered`, so an order-due marked "Order already placed" is simply hidden for them (as Mark placed).
- **W3 averages**: independent sqlite recompute from raw snapshots for 423 items sold in the last 91 days (25 with a
  Container twin), 2,596 weeks: every `avg_on_hand` / `on_hand_days` matches. One row per item per date on prod (0
  duplicates), so the average is well defined.
- **W4 mapping vs the PDF** (re-read with pypdf): all 38 document vendors resolve as the page proposes (37 matched,
  Sai Florals not in inventory); users Amandeep 10, Sai Durga 12, Harshitha 9, Sundar 11. The document gives Tobacco
  Products to Amandeep, but tobacco prints under "Other" (mixed with kitchen / catering), which is left unproposed; no
  open Fix Issues item on prod is tobacco today. The document also names Dhurba as Halal Meat lead (page 2) while page 1
  gives Halal Meat to Amandeep - the proposal follows page 1.
- **W4 daily task**: idempotent (one success `job_runs` row per store day; a re-run / Run now updates; next day
  replaces); DST change days (Nov 1 2026, Mar 14 2027) run once, due exactly the set hours later in real time (test).
  A failing run retries every 5-minute tick (one `job_runs` row each) until it succeeds - noted, not changed.
- **W2 search**: the code rule is pure Python (no SQL), so `%`, `_`, `;` are literal; `;`-only SKUs never match; exact
  barcode ignores leading zeros; message search escapes LIKE wildcards. **CC**: each address is split on `,;` and
  whitespace and must match the ASCII address pattern, so CR/LF can't reach the `Cc:` header; envelope = To + CC.
- **W5 uploads**: voice = `ftyp` first + `moov` with `mp4a`, 10 MB / 5 min (a missing header duration falls back to the
  client's, capped); PDF = `%PDF-` + the flyers' inflate-capped pypdf check, 300 pages, 10 MB; both served with
  nosniff to channel members only. Mentions: one mention push per message per person, through mute and the 1-minute
  throttle - the same reach mentions already had before W5 (plain chat push), so no new spam vector; there is still no
  per-sender message rate limit (pre-existing).

**Full backend run**: 1957 passed, 1 failed (`test_app_bundled_copy_is_current` - the final stage regenerates the app's
glossary), 8 skipped. Scripts: `~/.claude/jobs/e26a1611/tmp/v1b/scripts/` (`onhand_check.py`, `fixtures.py`) and
`dart/v1_contract_test.dart` (copy into an old build's package as `test/`).

## As built - Verify 2 (app + website end-to-end across W1-W6)

Commits: website `c8cd5c0`, app `3751062`. No migration, no backend change.

**How it was checked.** Throwaway backend on port 8815 (`~/.claude/jobs/e26a1611/tmp/v2e2e/`: `serve.sh`,
`serve_fake.py` - fake SMTP into `outbox/`, every other credential blank, scheduler / notifications / team jobs off),
a fresh read-only prod copy migrated to head (0056), temp users in the COPY only: admin, staff (ordering + reports +
team), a shift lead (`can_assign`), a Hide sales user, a demo login (`DEMO_USERNAMES`), plus a session for the real lead
Sai Durga in the copy. The website was built from `git archive HEAD` (the shared working copy's index.css holds another
agent's re-ordering).
- `scripts/sweep.mjs`: 18 changed pages x 6 logins x 1280 / 375 (216 loads) - console clean, no 4xx/5xx beyond the
  sandbox's live-Toast calls, no page sideways scroll; every screenshot looked at (contact sheets in `sheets/`).
- `scripts/flows.mjs` (1280 and 375): W1 Order already placed from Reorder -> Placed Orders -> Undo (and Undo of a cart
  order refused); W2 Send PO with a contact's default CC + a typed CC through the fake SMTP (Cc header, envelope To+CC,
  send log, Recent PO sends), code search on Fix Issues (SKU, Toast-id prefix) and cart Add items ("Matches SKU");
  W3 Reorder's weekly table (Avg on hand; no Revenue for Hide sales); W4 save the 105-row proposal, Run now twice (one
  task per lead), lead filter, the lead's task in Tasks / Board, `?lead=12`; W5 board / stats / chat search /
  templates; Help; the demo login refused on order actions, task stats, leads and Send PO, and shown no critical
  actions or Stats. `scripts/hover.mjs`: no button on the changed pages turns the global red on hover.
- App: device-matrix coverage reviewed for every new/changed screen; the gaps got `test/features/v2_e2e_matrix_test.dart`
  (below). navigation_reachability (Home untouched; `issue_leads` under More > Admin, `chat_search` / `task_stats` as
  hub children), push routes (`chat_mention` -> chat with `channel_id`; lead tasks use `task_assigned` -> task; cart push
  -> `PurchaseCartScreen(openVendor:)`) and permissions (admin-only, never-demo entries) checked.

**Fixed (confirmed bugs):**
- *W1 outside orders looked like empty orders.* Placed Orders (website) and the app's Placed Orders / Home "Recent
  Orders Placed" showed an "Order already placed" row as `0` items and "No item details were saved for this order",
  although the glossary says Placed Orders lists it as Placed outside the app. Website: "Placed outside the app" under
  the status, `-` for items / qty, and the expanded row explains it (who, which delivery) with the note
  (`components/placedOrderSource.js` + node tests). App: `PlacedOrder` reads `source` / `source_label` / `note` /
  `for_delivery_date` (additive), the tile says "Placed outside the app" instead of "0 items", the full list shows the
  note (3 lines max).
- *W5 "Create from a template" threw on an iPhone SE at 2x text* (ListTile: "Trailing widget consumes the entire tile
  width"). Each template is now title / details / Create now under them.
- *W5 Tasks toolbar (admins / shift leads):* `Flexible` + `Spacer` gave Board / Overdue only half the free width, so on
  an SE "Overdue" sat under the template and stats buttons. Now `Expanded`.
- *W4 website:* the lead's daily task drawer had the Fix Issues link only as plain text; it now has an "Open the Fix
  Issues list" button (`/fix-issues?lead=<id>`, from `source_type` issue_leads / `source_id` "<id>:<day>", like the app).
  The leads page's proposal line ("Document: ... Guess - check ...") ran off the phone layout (a td is nowrap site-wide);
  it wraps now.
- *Vendor Calendar on a 375 px phone* (pre-existing, noted by W1): the month grid widened the whole page (143 px of
  sideways page scroll). The grid now scrolls inside its own box on phones (min 640 px); the order menu is still the
  bottom sheet; desktop unchanged.

**Tests added:** website `placedOrderSource.test.mjs` (3), `team/taskLinks.test.mjs` (2) - 143 node tests pass. App
`test/features/v2_e2e_matrix_test.dart`: outside-order model + tile, Placed Orders with outside orders, the admin Tasks
toolbar + templates sheet + a lead task's Start / Open the Fix Issues list, and the moderation queue with a reported
voice note and PDF - each on all 10 device-matrix shapes (both SE 2x failures above were found by it).

**Runs:** backend full 1958 passed, 2 failed - `test_app_bundled_copy_is_current` (final stage regenerates the app's
glossary) and `test_admins_flipping_at_once_keep_a_true_history` (a race test; failed 1 of 4 runs, passes on rerun, not
W1-W6). Flutter full suite (`--concurrency=2`): 5003 passed, 6 skipped, 1 failed - `glossary_test` (the bundled glossary, final
stage). flutter analyze: 1 info in Verify 3's `v3_blocked_pins_search_test.dart` (deprecated matcher), none in app code.
Team, purchase cart, home and app/ (navigation) suites: 972 passed.

**Not changed / for the owner:** the chat bubble (bottom right, fixed) still covers the end of the last visible line
on some phone pages - site-wide, as before. Items Sold / Open checks / today's sales show "Couldn't load" in the sandbox
only (they call Toast live).

## As built - Verify 3 (App Store safety of W1-W6, 2.0.0)

Audit of everything new against the App Review Guidelines for the 2.0.0 submission (first review since 1.0.0),
with file:line evidence; plan updated in `~/spicetown-dev/APP_STORE_REVIEW_PLAN_2026-10-01.md` (4.1, 4.2, What's New,
section 7).

**Fixed (with tests):**
- **1.2 Block didn't reach pins or search.** A person I blocked is folded in team chats and the chat list, but their
  pinned message showed in full in the pinned bar / pinned list, and message search listed their messages in full.
  Server: `chat_service.search_messages` leaves out senders the searcher blocked (DmBlock, blocker side only - the
  blocked person still finds the blocker's messages; nothing tells them). App: `PinnedBar` shows "Message from
  someone you blocked" and the pinned list folds the message (tap to show, like the chat); `ChatSearchScreen` drops
  a blocked sender's hits (an older server's answer too). New `blockedUserIdsProvider` (chat_providers.dart). Help
  bullet updated. Tests: backend `tests/test_v3_app_review_blocked.py`; app
  `test/features/team/v3_blocked_pins_search_test.dart` (+ device matrix) and journey step 15.
- **1.2 Report wasn't reachable from a voice note's play button.** Its `IconButton` tooltip took the long-press (a
  tooltip shows on long-press on phones), so long-pressing the obvious part of a voice note showed "Play voice
  message" instead of the message actions (Report). The tooltip is gone; the button keeps its VoiceOver label via
  `MergeSemantics` + `Semantics(label:)` (`chat_attachments.dart`). Test: journey step 13 + the widget test above.

**Checked OK (evidence):**
- 1.2 UGC: Report on every message kind - the long-press sheet has Report for any message not mine
  (`conversation_screen.dart` `_messageActions`), PDFs and voice notes included (journey step 13 reports both);
  moderation queue shows voice/PDF (`moderation.py:211-221`, app `reports_screen.dart:215-220`); word filter on PDF
  names (`media.py:368 clean_display_name`); mention pushes never cross a block (`chat_service.py` mention recipients,
  `blocked_between`) and a demo login's message only notifies its own teammates; pins need membership
  (`set_pinned` uses `allow_admin=False`) and in team chats a manager / shift lead (`can_pin`); the demo login can pin
  only in its DMs.
- 5.1.1: `NSMicrophoneUsageDescription` (Info.plist:35-36) says what it is for; the mic is asked for only on the first
  mic tap (`conversation_screen.dart` `_startRecording` -> `hasPermission()`), never at launch or on opening a chat
  (journey step 14 counts the asks: 0 before the tap); a "no" gives a plain message and records nothing. Camera /
  photos strings unchanged. PDFs come from the system document picker (no permission).
- PrivacyInfo.xcprivacy: AudioData (linked, app functionality, no tracking) added by W5 (line 49); PDFs are Other
  User Content (already declared). App Store Connect answers updated in the plan's 4.1 (Audio Data is the only new
  type).
- 2.1 demo (`app_store_review`, non-admin, 17 features, hide_sales): W1 order actions are admin-and-not-demo
  (`order_due_actions.dart:18`, `critical_dismissals_providers.dart:16`); Send PO and Vendor contacts hidden
  (`po_send_providers.dart:23`, `store_sections.dart:131`; journey step 10 asserts both); Task stats hidden
  (`tasks_segment.dart:190`) and refused by the server (`router_tasks.py:139-143`); templates only for assigners
  (`can_assign` false for the demo); Category & vendor leads, Inventory from Toast, Flyers never for the demo
  (`FeatureAccess.admin` / `anyOfNotDemo`); W3 weekly sales and W4 section grouping are read-only views. The demo
  guard allowlists only `POST /tasks/{id}/start` (its own tasks) and pin/unpin of the W5 writes (`demo_guard.py`).
  Every tile the demo sees is covered by `navigation_reachability_test` / `nav_device_matrix_test` (demo users in
  `test/support/nav_users.dart`).
- The prod review team (copy, Oct 14): Review team with the demo + Sundar, 2 tasks (one photo), 3 channel messages
  and 1 DM. No voice note / PDF / pin yet - the plan's 4.2 asks the owner to add one of each from Sundar's login.

**Journey test** `test/audit/app_review_journey_test.dart` (iPhone 15 + iPad Pro 13 landscape) gains 13-15 (voice play,
Report on a voice note and a PDF, no Pin for the demo, mic asked only on tap / denied path / record + send, PDF send,
search, block -> pins and search folded) and 16 (Overdue, Board + Start, no stats / templates); step 10 asserts no
Send PO / Vendor contacts.

**Not changed / for the owner:** website pins bar still shows a blocked person's pin (the website only folds DMs; not
part of the app review); voice and PDF content can't be word-filtered (text and PDF names are; Report + 24 h
moderation cover the rest); `pod install` before archiving (record / just_audio aren't in `ios/Podfile.lock` yet;
`flutter build ipa` runs it); pubspec is `2.0.0+9` - the build number App Store Connect needs is whatever is next
after the last upload, not changed here.

**Runs (Oct 14):** backend full 1957 passed, 1 failed (`test_app_bundled_copy_is_current` - the final stage regenerates
the app glossary); `tests/test_v3_app_review_blocked.py` + W5 search tests pass. App: `flutter analyze` clean; full
suite (`--concurrency=2`) failures only `glossary_test` (bundled `avg_on_hand`, final stage) and
`flyers_entry_points_test`, which only lacked W4's admin-only `issue_leads` in the demo's hidden list (fixed, passes);
journey 12/12 at iPhone 15 + iPad Pro 13; `v3_blocked_pins_search_test` 14/14 (10 matrix devices). Commits: website
(this section, the search fix and test), mobile `eec7e94`.

## As built - Final (integration and close-out)

- **Migrations:** the chain is 0052 -> 0053 (W1) -> 0054 (W4) -> 0055 (W5) -> 0056 (W2), one head (`alembic heads` =
  0056). No down_revision needed fixing. New `tests/test_migration_chain.py` fails on a second head, a merge point or
  a change to the batch's order. Rehearsed on a fresh read-only prod copy (0052): up to 0056 in ~1 s, integrity and
  foreign keys ok, down to 0052 and up again ok, AUTOINCREMENT kept on vendor_contacts / po_sends / messages /
  team_media. The new tables are empty. Prod has 0 vendor contacts, so CCs start empty. The copy was deleted.
- **Glossary:** the app's bundled copy was regenerated (mobile `7c21ff9`: `avg_on_hand`, `order_placed_outside`,
  `order_due_other`). `test_app_bundled_copy_is_current` and the app's `glossary_test` pass.
- **Contract walk W1-W6:** every item is built (see each As built section). Small gaps closed here: none needed in
  code. `flutter analyze` had one info (deprecated `containsSemantics` in Verify 3's test), fixed in mobile `a79d258`
  (`isSemantics`; checked that it still fails on a wrong label). Left as listed, not built:
  - The app's Items Sold has no product search box at all. The owner's ask was "wherever product search is present",
    so none was added. The website's Items Sold has code search.
  - Upload Invoice pickers only search vendors, so no code search is needed there.
  - Price change log, Reconciliation and Vendor price comparison (website) match by Toast ID and barcode, not vendor
    SKU: their rows don't carry SKUs.
  - The website's pinned bar still shows a blocked person's pinned message. The website hides blocked people only in
    direct messages, and the app (the App Review surface) folds them everywhere.
  - "Skip this week" on an Orders due row skips that one order. Every prod schedule delivers once a week, so in
    practice that is the week.
- **Shared working tree:** `frontend/src/index.css` had an uncommitted pure reordering of HEAD's lines (same lines,
  different order; a leftover of another worker's private-index commit). It was restored to HEAD, the version
  Verify 2 tested. A copy is at `~/.claude/jobs/e26a1611/tmp/final/index.css.uncommitted-reorder.bak`. The
  uncommitted W3 / W4 sections of this file were kept, and the As built sections were put in W1-W6 order.
- **Full regression (Oct 14, final heads):**
  - Backend: 1963 passed, 8 skipped, 0 failed on `.venv` (3.9) and on `.venv312` (3.12, the prod venv).
  - Website: node tests 143/143 (15 files); `vite build` ok.
  - App: `flutter analyze` no issues; full `flutter test --concurrency=2` 5004 passed, 6 skipped (the flyers
    live-server tests), 0 failed.
- **Runbook:** `~/spicetown-dev/DEPLOY_OCT14.md` (server deploy 0052 -> 0056, Sundar's sudo command, checks,
  rollback, owner setup: leads page, task times, vendor CCs). The app version was not bumped (still 2.0.0+9).

## As built - Final regression + known fixes (Oct 14, before the 2.0.0 submission)

Server `ops-fixes` (head 0056, prod still 0052 on `4393ac4`), app `ops-fixes` (2.0.0+9, not bumped).
- **Fix (a) - vendor minimum orders on the Vendor Calendar.** An order-due was `urgent` (red chip with a count, red
  "Items needed" in the app, red dot on Overview) with ONE item needing ordering - on the prod copy KP Produce and
  Indigenous Foods were red every week for 1 item. Now `urgent` needs `vendor_calendar.ORDER_MIN_ITEMS` items
  (= Reorder's `VENDOR_MIN_ORDER_ITEMS` = 5, the same as the "Place the order" Team task's
  `TEAM_AUTOTASK_ORDER_MIN_ITEMS`). Additive event fields: `below_minimum` (1-4 items, not skipped / placed) and
  `min_order_items`. Website: "(1 low)" in the normal chip colour with a hover text, legend "(n) = items needed (5+),
  (n low) = fewer, not an order yet", Overview "1 low - not an order yet". App: "Order due: X · 1 low" + "Fewer than a
  usual order (5+ items)" (grey), Orders due sheet / vendor picker "1 low". The 9 AM order-due push still goes (it is
  the vendor's order day) but says "1 item low - fewer than a usual order, review" instead of "1 item to order".
  Glossary `cal_items_needed` states the 5-item rule (app bundle regenerated). Old builds see `urgent: false` and show
  the count without red. Tests: `tests/test_regress_min_order_and_add_all.py`, app `vendor_calendar_test.dart`,
  `today_order_actions_test.dart`, web `orderActionsModel.test.mjs`.
- **Fix (b) - Add all suggestions.** Suggestions carry additive `cadence` (weekly / every_few_weeks / rare / unknown),
  `irregular`, `median_gap_days`, `cadence_weeks`, `cadence_text`, `cadence_label` and `add_all_default`; `rules`
  gains `irregular_min_gap_days` (11) and `add_all_default_confidence`. Irregular = median gap between deliveries 11+
  days; the text gives the middle half of the gaps in weeks ("Delivers every 1-2 weeks, not every week - a schedule
  repeats every week, so it would put an order due every week. Weekly isn't right for this vendor."). Add all
  (website dialog, app dialog) starts with High and Medium ticked and Low unticked but listed; **irregular High /
  Medium vendors also start unticked** (a weekly schedule would put an order due every week) - each row says
  "Delivers every 2-3 weeks - weekly isn't right" and a note above the list says what starts unticked. Cards show the
  full warning. accept-all itself is unchanged (it adds whatever keys are sent). Prod copy: 11 suggestions -> 2 ticked
  (Harbs Farm, Vijay Home Foods); RBest (High, every 1-2 weeks), Concept Foods, Costco, Jain Foods, Vdyas (Medium,
  every 2-4 weeks or monthly) and the 4 Low ones unticked.
- **Regression:** backend 2030 passed / 8 skipped on `.venv` (3.9) and `.venv312` (3.12); website node tests 161/161
  (17 files), `vite build` ok; app `flutter analyze` clean, full `flutter test --concurrency=2` 5059 passed, 6 skipped,
  1 failed = `help_screen_test` "limits section", caught mid-edit by another worker changing `help_content.dart` at the
  same time - it passes on re-run (8/8); the vendors / home suites pass with this pass's changes (96 + 29).
- **Migration rehearsal** (fresh read-only prod copy at 0052): 0052 -> 0056 -> 0052 -> 0056 on `.venv`, then down/up
  again on `.venv312`; `integrity_check` ok and `foreign_key_check` empty at every step; row counts identical for all
  56 tables at 0052 before / after the round trip and all 58 at 0056 (the 2 new tables, `issue_leads` and
  `issue_lead_task_runs`, empty); 14 AUTOINCREMENT tables and `sqlite_sequence` unchanged. The only schema text change
  is `placed_orders` / `vendor_order_skips` rebuilt by 0053's batch downgrade (same columns, indexes, unique key; not
  AUTOINCREMENT tables).
- **Throwaway-server smoke** (prod copy at head, port 8847, every sender blanked, temp logins in the copy only:
  admin, staff, hide-sales staff, lead, a no-calendar user, and `app_store_review` with a copy-only password): 19 new
  routes since `4393ac4`, none removed. Every new / changed GET as each role: admin 200 everywhere; staff 403 only on
  admin pages (leads, task stats, templates); hide-sales 200 with no sales dollars in projection / reorder and 403 on
  Items Sold (by design); demo 200 on reads, 403 on vendor contacts / PO sends / leads / task stats and on every write
  (DEMO_READ_ONLY); a user without the feature 403. Writes as admin each worked and were undone (Order already placed
  + Undo, Other + Undo, Other without a note 400, Add all defaults, lead save / clear, settings, task start, template
  -> task, pin / unpin, seen-by, message search); staff / lead / demo refused where expected (pin: "Only managers and
  shift leads can pin"). Old app models at 9985842 (build 5), fd00501 (6), 2ad4fbc (7), 7bc7612 (9) and 13c993b (9 +
  Insights) parse today's answers (calendar with `below_minimum`, placed orders, reorder, projection x2, weekly sales x2,
  cart x2, Fix Issues, tasks, chat, prefs; schedule suggestions for 9 / 13c993b).
- **Browser check** (Playwright, admin 1280 / staff 375 / demo 375 / admin 375): calendar shows "(1 low)" chips not
  red and red chips only with 5+; Add all dialog ticks exactly Harbs Farm + Vijay Home Foods; no horizontal scroll, no
  console errors, no failed calls. Screenshots: `~/.claude/jobs/e26a1611/tmp/final-regress/shots/`.
- **Seen, not changed:** the calendar API takes ~9-19 s for 3 weeks on the prod copy on this (loaded) Mac with the old
  and the new code alike (one Supplier Projection per scheduled vendor, no cache); Reorder Candidates ~55 s and a cold
  Fix Issues summary ~2 min while other test suites were running.

## As built - Consolidation (Oct 14, all regression reports re-checked before 2.0.0)

Every open item from Suite A, Suite B, the final regression and the simulator review was re-checked.
- **Fixed:** the Announcements page's sentence pills were Title Cased by `.pill`'s `text-transform: capitalize`
  ("1 Waiting For You To Confirm", "You Confirmed · ..."). They carry `.ann-pill` (`text-transform: none`); Suite B's
  chat flow checks both read as sentences. Suite B's `run.sh` with no arguments ran nothing (zsh does not split a
  `${@:-a b c}` default; it tried one script named "team chat ... sweep") - fixed. Suite B's `setup.sh` now also copies `~/Spice Town Flyers`
  into its work folder: without the files the copy's existing flyers (7-9, PDFs) 404 on `/thumb` and the sweep's "no
  failed API calls" fails (the README had it as an optional step).
- **Not a bug (evidence):** "microphone prompt at sign-in" in the simulator review. In every walk's device log the only
  `kTCCServiceMicrophone` request comes after the walk tapped the chat mic (main walks: right after the attach sheet;
  the mic walk: after login + Team rules, 14 s later), and the fresh-install prompt walks (login, Face ID offer,
  notification prompt) have none. App code asks only in `conversation_screen.dart` on the mic tap; `record` /
  `just_audio` register no audio session at start-up. Still worth one look on the TestFlight build on a real device.
- **By design, left:** website pinned bar / group messages from a blocked person (blocking folds messages in the app
  only - APP_REVIEW_BUILD7_CONTRACT section 6); website has no task / comment Report (app has it); website demo sees
  Send PO and is refused; at qty 1 a non-admin cart line shows the request-removal icon on the stepper and beside it
  (the stepper says what minus does); demo invoice "Completed" + "Not sent (demo)"; the 10 s calendar Undo bar.
- **Owner decisions (unchanged):** Vendor Calendar speed (measured on a fresh prod copy with the Mac at load ~2-3:
  3 weeks with today's 5 schedules 5.4 s; 6 weeks after Add all = 16 schedules 10.5 s, about half of it per-vendor repeats
  of the same store-wide loads - batching is the fix if wanted); past "missed" deliveries before a schedule existed
  (schedules have `created_at`, but delete + re-add - the only way to change Add all's order day - would then hide that
  vendor's history); everything listed in the final regression section.
- **Re-run on HEAD:** backend 2030 passed / 8 skipped on `.venv` and `.venv312`; node tests 161/161, `vite build` ok;
  `flutter analyze` clean, `flutter test --concurrency=2` 5063 passed / 6 skipped; Suite A on port 8833 (fresh prod
  copy at 0056) 1522 checks, 0 failed; Suite B on port 8833 (fresh prod copy at 0056) every flow ALL OK at
  1280/1024/375, sweep ALL OK (after the flyer files were in place).
