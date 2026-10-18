# Automation batch: remove manual work with Toast APIs (contract, Oct 11 2026)

Owner: "Build all 1-9. Do nothing for bank or labor for now. Also try to pull all reports live instead of on a
schedule, if the current system supports it."

Evidence for this batch is in `/Users/sundar/.claude/jobs/e26a1611/tmp/toast-analytics/` (the Analytics API
probe: `era.py` client, docs, OpenAPI spec, comparisons) and `/Users/sundar/.claude/jobs/e26a1611/tmp/manual-tasks/`
(the manual-task inventory: stock/menus vs CSV comparison, intraday test, Toast docs notes). **Read both before
designing.**

## Access
- **Standard Toast API** (prod `.env`: `TOAST_CLIENT_ID`, `TOAST_CLIENT_SECRET`, `TOAST_API_BASE`,
  `TOAST_RESTAURANT_GUID`). Scopes: orders, menus, stock, config, labor, cashmgmt and others. All read-only.
- **Toast Analytics API** (new): settings `TOAST_ANALYTICS_CLIENT_ID` and `TOAST_ANALYTICS_CLIENT_SECRET`, optional
  `TOAST_ANALYTICS_BASE` (default `https://ws-api.toasttab.com`). Scope `enterprise-metrics:read`; path prefix
  `/era/v1`.
  - **Where the secret lives:** for dev and tests it's in `/Users/sundar/.claude/jobs/e26a1611/tmp/toast_analytics.env`
    (chmod 600). Never commit, print or log it. The lead adds it to the prod `.env` at deploy time.
  - **The feature must work when it's absent:** fall back to today's behaviour.
- **Rate limits.** Prod shares these limits, so every job and live call must respect them: back off on 429, honour
  `x-toast-ratelimit-*`, and cache results.
  - **Analytics:** day/week reports 10/min and 60/hr. Check, payment and guest reports 60/day.
  - **Standard API:** gotcha 9 in `SKILL.md`.
- **Tests:** never call real Toast; mock every call. Live verification goes against a read-only prod DB copy with
  gentle real GET calls only, done by the verify agents.

## The 9 items (+ live reports)
1. **Inventory snapshot from the API** (daily upload becomes optional).
   - **When:** a nightly job at 04:05 ET. Toast's `closeoutHour` is 4.
   - **What it builds:** an `inventory_snapshots` row set from:
     - `/stock/v1/inventory`: quantity and status;
     - `/menus/v2/menus`: name, price, sku as barcode, category group/category/subcategory, productVariant
       Each/Container pairs;
     - our `orders` table: 7/30/90-day sales and days on hand.
   - **Fields Toast doesn't serve:** cost, supplier, supplier_item_id, last_received_from, inventory_last_received.
     Carry them forward from the latest CSV row per item. `source_filename = "api:<iso time>"`.
   - **Zero and negative counts:** the API reports OUT_OF_STOCK with no quantity. Store the quantity as
     `0 if status=OUT_OF_STOCK`, and keep a flag (an additive column) so Negative inventory keeps using the latest
     CSV's negative counts until the next CSV.
   - **Controls and reporting:**
     - kill switch setting `INVENTORY_API_SNAPSHOTS=true|false` (default false until the lead flips it after a
       side-by-side week);
     - a side-by-side compare endpoint/report (API vs CSV per field) for the owner;
     - an "upload missing / cost refresh due" alert once the CSV is older than N days (setting, default 7).
   - **CSV upload keeps working** and always wins for the fields it has on its date.
2. **Sales accuracy from Analytics.**
   - A job re-pulls the last N business days (default 14; Toast says edits land within 2-4 h, or up to 6 days) after
     close and at a few points in the day. It corrects stored sales.
   - **Preferred route:** re-sync those dates through the existing orders sync, which already upserts by
     selection GUID. Our sync never re-pulled succeeded dates. Use the Analytics day metrics to detect which dates
     changed (compare net/gross/discount/void/refund/order count), then re-sync only those, and record the comparison.
   - **Keep the selection-level model.** Don't replace stored orders with Analytics aggregates; the
     forecast/reorder code needs item GUIDs, and the Analytics item report has them only as daily aggregates. Use
     the Analytics item report where it's better, e.g. live "today" item sales if it's cheaper than the Orders API.
3. **Items Sold double-counting add-ons.** `app/services/items_sold.py` (live and stored paths) and `export.py` must
   exclude modifier rows (`parent_selection_guid IS NULL`), as `weekly_sales.py` does. Show add-ons separately only
   if the UI already does. Check every other `orders` consumer for the same bug (gotcha 17).
4. **Suggested deliveries** (Delivery Review).
   - **How it works:** using consecutive snapshots (API or CSV) plus sales in between, a rise in stock beyond what
     was sold on a vendor's items means a likely delivery. Show a "Suggested" list per vendor with quantities. A
     person still confirms; nothing is written without confirmation.
   - **Watch for:** transfers between Container and shelf (pairs), recounts, and items with no count.
5. **Suggested "Closed in Toast"**: for submitted invoices not yet marked closed, suggest closing once that vendor's
   items show a delivery-like rise (item 4) or Toast's last-received date changes (CSV) after the submission. One tap
   to confirm; never automatic.
6. **Hourly barcode re-check** from `/menus/v2/menus`, using `/menus/v2/metadata` `lastUpdated` as a cheap change
   check.
   - The missing and invalid barcode Fix Issues lists re-evaluate between uploads.
   - A fixed item leaves the list, and a "fixed" close is confirmed, as the existing came-back logic expects.
7. **Vendor delivery schedules inferred** from `placed_orders` and `purchase_log` weekdays (and receiving dates).
   - Shown as suggestions on the Vendor Calendar: "Babco usually delivers Tue/Fri. Add schedule?" with one-click
     accept or edit.
   - Never overwrite a schedule an admin set.
8. **Send the PO to the vendor from the app.**
   - **Contacts:** a `vendor_contacts` table (email, WhatsApp number or group, contact name, notes), managed by admins.
   - **Send PO:** produces the existing PO PDF and emails it through the existing Gmail sender. WhatsApp is
     optional and off by default: the Whapi plan is the sandbox with a 5-chat cap, so new vendor chats would fail
     with 402. Say so in the UI.
   - **On success:** marks the order placed automatically (the same as Mark placed) and logs the send.
   - **Safety:** confirm dialog, idempotency, demo blocked, no sends in tests.
9. **Retire Transfer Review.** Nothing reads `location_transfers`. Hide it from the website and app menus and search
   (keep the data and the route for old app builds), and note it in Help.

**Live reports:** wherever the system computes reports from data that can be fetched live within the rate limits,
compute them on request with short caching, instead of waiting for a scheduled job. Examples:
- today's sales and items sold (already live; make them correct);
- open checks (already every 10 min; consider on-demand refresh with a 1-2 min cache);
- current stock in Supplier Projection, Reorder and Fix Issues: live API stock overlay on the latest snapshot,
  cached about 5 min, behind the item-1 kill switch.

Keep the scheduled jobs as a safety net, and document what became live and what didn't and why.
**Out of scope:** bank deposits, labor.

## Ownership (to avoid collisions)
- **B1 sales:** items 2, 3 and live sales. `services/sales_sync.py`, `items_sold.py`, `export.py`, a new
  `toast_analytics.py` client, scheduler entries for re-pull. Migration 0051 only if needed.
- **B2 inventory:** items 1 and 6 and live stock. New `inventory_api.py`, scheduler, Fix Issues barcode re-check,
  overlay. Migration **0048**.
- **B3 suggestions:** items 4, 5, 7 and 9. `delivery_review`, invoices, vendor calendar, menu hiding. Migration
  **0049**.
- **B4 PO sending:** item 8. Vendor contacts, send PO. Migration **0050**.
- **B5 app (build 9):** app parity for 3, 4, 5, 7, 8 and 9 on the matching mobile screens. The app version stays put
  (build 8 may still be pending upload); note "build 9" in commits.
- **Shared files** (`scheduler.py`, `config.py`, `models.py`, `main.py`, `glossary.py`, `HelpPage.jsx`, `index.css`):
  minimal additive edits, and stage only your own hunks.
- **Python 3.9** (`typing.Optional`) and `UTCDateTime`. Responses stay additive (build 5-8 apps are live). The demo
  guard default-blocks new writes.

## As built - B3 suggestions (items 4, 5, 7, 9)

Migration **0049** `suggestion_dismissals` (kind, suggestion_key, dismissed_by_*, created_at; unique (kind, key)).
Nothing is ever written by a suggestion; only "dismiss" writes (and undo deletes). All three are computed on request
(live, no scheduled job) and cached about 60 s against a fingerprint of the snapshots / purchase log / invoices /
dismissals, so a confirm or a new upload shows at once.

**Deviation (item 4), from the prod-copy measurement:** the main signal is **Toast's own receiving record**, not the
raw stock rise. Every CSV row carries `inventory_last_received` (receive time) and `last_received_from` (Toast's
receiving number, e.g. `IN378`); the store receives every invoice in Toast, and that is what raises the count. An
item whose receive time moved between two snapshots was received in between; items are grouped by receiving number
and the vendor is the one most of the items list (multi-vendor items count 1/n; tie -> the vendor whose invoice was
uploaded last before the receiving). On a CSV day a stock rise *without* a receiving record is a recount or a move
(1 of 56 such lines was a confirmed delivery), so it isn't suggested. On an **API-only snapshot day** (item 1;
`source_filename` "api:...", which can't carry new receiving records) the stock-rise rule is used instead: count now
- max(count before, 0) + units sold between >= 5, Container moves / weighed-item noise / untracked / API out-of-stock
rows left out, and only when 3+ of one vendor's items rise together or the vendor uploaded an invoice around then.

### API (all additive; app build 9 can use them)
- `GET /api/reconciliation/suggested-deliveries?days=3` (feature `delivery_review`; days 1-31 = snapshot pairs ending on
  the latest) -> `{generated_at, through, days, pairs:[{prev_date,date}], count, counts:{handled, dismissed, skipped_*},
  rules, groups:[...]}`. A group: `{key, vendor, received_date (= the newer snapshot date; confirm with this),
  prev_date, evidence: "toast_received"|"stock_rise", reason, toast_receipts:[{id, received_at, item_count, votes}],
  item_count, preselected_count, suggested_units, sales_complete, handled, dismissed, items:[...]}`. An item:
  `{item_id, name, category, supplier_item_id, prev_qty, today_qty, delta, sold_between, sold_today (same value),
  is_new, suggested_qty (null = enter it), cost, evidence, receipt_id, received_at, listed_vendors, vendor_listed,
  is_container, weighed, untracked, preselected, note}`. A vendor-day already in the purchase log (a Delivery Review
  confirm on that date, or a manual log that day / the day before) isn't suggested.
  - **Confirm** = the existing `POST /api/reconciliation/delivery-confirm` `{vendor, received_date, items:[{item_id,
    item_name, quantity_received}]}` - byte-for-byte the manual flow's purchase_log rows.
  - `POST /api/reconciliation/suggested-deliveries/dismiss {key}`; `DELETE .../dismiss?key=` (undo, 404 if not dismissed).
- `GET /api/invoices` items gain `close_suggestion` (null, or `{key, evidence: "toast_received"|"stock_rise",
  receipt_id, received_at, item_count, seen_in_snapshot, reason, dismissed}`), and the response gains
  `close_suggested_count`. Only open invoices of type "invoice" with `can_mark_toast_closed` get one.
  - `GET /api/invoices/close-suggestions` -> `{count, items:[{submission_id, vendor, total_amount, created_at,
    suggestion}]}` (Overview tile / app Home).
  - One tap = the existing `POST /api/invoices/{id}/mark-toast-closed`. "Not closed yet" =
    `POST /api/invoices/{id}/close-suggestion/dismiss` (undo: `DELETE` the same path).
- `GET /api/vendor-calendar/schedule-suggestions` (feature `vendor_calendar`) -> `{lookback_days: 56, through, count,
  suggestions:[{key, vendor, delivery_weekdays (0=Mon), weekday_names, lead_days, lead_days_source:
  "placed_orders"|"default", order_weekdays, evidence:{source, source_label, dates, weekday_counts, covered, total,
  coverage}, reason, dismissed}], schedule_checks:[{vendor, schedule_id, schedule_weekdays, observed_weekdays,
  evidence, reason}], dismissed_count, rules}`. Vendors that already have a schedule never get a suggestion (only a
  read-only `schedule_checks` line when their recent days differ).
  - Accept / Edit = the existing `POST /api/vendor-calendar/schedules {vendor, delivery_weekdays, lead_days}` (400 if
    the vendor already has one - never overwritten). Dismiss = `POST /api/vendor-calendar/schedule-suggestions/dismiss
    {vendor, delivery_weekdays}`; undo `DELETE .../dismiss?key=`.
- Every new write is blocked for the demo login by the global demo guard (not allowlisted).

## As built - B1 sales accuracy (items 2, 3, live sales)

No migration (0051 not used). Comparisons are `job_runs` rows.

### Toast Analytics client: `services/toast_analytics.py`
- **Settings:** `TOAST_ANALYTICS_CLIENT_ID`, `_SECRET`, `_BASE`; uses the existing `TOAST_RESTAURANT_GUID`.
- **When it isn't set up:** `is_configured()` is false and calls raise `AnalyticsNotConfigured`, so the job is a no-op.
- **Reports:** `run_report(kind, time_range, body)` creates a report, then polls it:
  - 202: back off, 2 s growing to 15 s;
  - 404 or 409: ask for a new guid, at most 2 times;
  - 500/502/503/504: retry;
  - 400: raise with Toast's message.
- **Token:** cached, with one re-login on 401.
- **Rate limits:**
  - our own sliding-window budget per endpoint: day/week POSTs 9/min and 55/h; custom/month/year 9/h; check and
    payments 4/min and 55/day; result GETs 25/min;
  - `x-toast-ratelimit-remaining: 0` holds that endpoint until `-reset`;
  - a 429 waits `Retry-After`.
- **Secrets** never appear in a log line or an exception.
- **Helpers:**
  - `day_metrics(start, end)` and `menu_day_totals(start, end)` request the `week` time range in chunks of up to
    7 days. Toast returns one row per business date; this was checked live for menu week with no groupBy.
  - `menu_items_for_day(day)`.

### Item 3: modifier double count
- Toast's selection `price` already includes the prices of its modifiers. On Oct 10, 29 of 29 checks with priced
  modifiers matched the top-level sum; none matched top-level plus modifiers.
- **Fixed:**
  - `items_sold.py`, both the live and the stored path, now uses `parent_selection_guid IS NULL`;
  - `export.py` leaves modifier rows out (voided rows stay, flagged).
- **Already correct (grep of every `orders` consumer):** `forecast`, `demand_forecast`, `weekly_sales`,
  `weekly_digest`, `fix_issues` last-sold, `similar_products` and `open_orders` line items.
- **Inherit the fix through `get_items_sold`:** Delivery Review, Transfer Review, the Ask bot, `/items-sold/export`
  and the Overview tiles.
- **Tests:** `tests/test_sales_items_sold_accuracy.py` builds an Oct 10-shaped fixture:
  - $6,490.26 top level and $567.55 of modifiers;
  - the old total was $7,057.81;
  - after the fix, every consumer gives $6,490.26.

### Sync follows later Toast edits: `sales_sync.py`
- A deleted order or check is treated as voided.
- A re-sync deletes that business date's stored selections that Toast no longer returns. This happens only when
  Toast returned at least one order, so an empty answer never wipes a day.
- 429 is retried (`fetch_orders_with_retry`).
- A selection voided later was already handled by the upsert (`voided` is updated).

### Item 2: re-pull job (`services/sales_accuracy.py`)
- **Schedule:** `SALES_REPULL_TIMES`, default 06:30, 12:30, 17:30 and 22:30 store time. `SALES_REPULL_ENABLED`
  (default true) and `SCHEDULER_ENABLED` must both be on, and it does nothing without Analytics.
- **Window:** `SALES_REPULL_DAYS`, default 14, ending yesterday. The manual run is capped at 60 days.
- **Deviation, detection:** the per-day comparator is Toast's item-level total, the Analytics `menu` report. It
  equals our top-level non-voided `net_price` sum by construction: modifiers are inside items and check discounts are
  prorated onto items.
  - Metrics `netSalesAmount` doesn't: it also nets out refunds and check-level adjustments. So metrics alone would
    flag every day forever.
  - Day metrics (net, gross, discount, void, refund, order counts) are still pulled. They form part of a per-date
    fingerprint: "Toast changed this day since the last check" triggers a re-sync.
  - Our order count vs `ordersCount` also triggers one re-sync.
- **A date is re-synced on any of these reasons:**
  - `item_net` / `item_qty`: our top-level net or quantity differs from Toast's item total;
  - `orders`: our order count differs from Toast's `ordersCount`;
  - `analytics_changed`: Toast's numbers for the date moved since the last check;
  - `unpaid`: we still hold unpaid lines and the last sync is more than 6 h old;
  - `never_synced`: no successful sync for the date yet.
- **No hammering:** a date already re-synced against the same Toast numbers, with nothing changed on our side, waits
  ("waiting") until Toast's numbers move. This covers Analytics lag and definitional order-count gaps.
- **Recording:** one `job_runs` row per date per run (`job_name="sales_accuracy"`, `business_date`, JSON `detail`:
  analytics view, stored before and after, reasons, verdict match/fixed/mismatch) plus a `sales_repull` summary row.
  Rows older than 120 days are pruned.
- **Admin view:** a "Sales accuracy" card under Items Sold. `GET /api/sales/accuracy?days=` and
  `POST /api/sales/accuracy/run?days=` start a background run; both are `require_admin`, and the demo login is blocked
  by the guard.
- **Rehearsal** (prod copy from Oct 11 13:55, real gentle calls, last 30 days, Sep 11 - Oct 10):
  - **Run 1 (191 s):** 30 dates re-synced, 28 fixed, 1 already matching.
  - **Run 2 (43 s):** no order fetches at all; 29 of 30 days match Toast's item-level net to the cent.
  - **Before the re-pull:** stored sales were higher than Toast on 27 of 30 days, by $1.46 to $461.04 per day, and
    lower on 1 day (Sep 14, -$38.73). Over the 30 days that is $4,975.81 (3.2%) too high. Unpaid stored lines went
    from 568 to 51.
  - **Oct 10 still differs** (-$183.45). The Orders API already shows a check edited on Oct 11 at 10:57 ET; the
    Analytics menu report hadn't caught up yet (Toast documents 2-4 h). The job marks the day "waiting" and picks it
    up when Analytics moves.
  - **Order counts:** off by 1 on 8 days (8 on Sep 27). Toast counts orders without item lines, which we never store.
    These are informational only.
- **Older history** (before the 60-day manual cap) is probably overstated the same way. A one-off backfill (60-day
  "Check now", or a script over the custom range) is the lead's call.

### Live reports
- **Items Sold "today":** a range that includes today pulls yesterday's, today's and tomorrow's business dates live.
  - The live rows replace the stored rows for those business dates, so the two are never added together.
  - This also picks up after-midnight payments that belong to yesterday's business date.
  - Pulls are shared for `LIVE_SALES_CACHE_SECONDS` (default 60). `?fresh=true` (the page's Refresh) skips the cache
    but reuses a pull younger than 10 s.
  - The response gains an additive `live_fetched_at`.
  - The Overview "Today's sales" tile uses the same path.
- **Open checks:** `GET /api/orders/open/all-time` first refreshes today's part (one Toast call) when the last scan is
  more than 2 minutes old.
  - This happens only where `SCHEDULER_ENABLED` is on and Toast is configured (prod), so dev and tests never call Toast.
  - Older dates still come from the 04:15 full scan, which stays scheduled as a safety net, along with the
    today-refresh interval.
- **Not made live:**
  - **Past days of Items Sold:** stored data, kept correct by the re-pull. A live pull per past date would cost one
    Orders call per day of the range.
  - **Analytics for "today":** its 2-4 h lag makes it worse than the Orders API for intraday numbers.

## As built - B4 (item 8: Send PO), commits 3018e14 (backend) + c217386 (website)

No deviation from item 8. Decisions and details the app (B5) needs:

- **Who:** admins + users with `purchase_order_cart`. `supplier_projection` alone can still add to the cart, but
  can't send (sending emails a vendor in the store's name). Vendor contacts: read with the cart, add / edit / delete
  admins only. The demo login is refused (403) on every route below, reads included (real people's details); its
  writes are also blocked by the demo guard (not allowlisted).
- **Tables (migration 0050, revises 0049):** `vendor_contacts` and `po_sends` (models in `app/po_send_models.py`, not
  models.py). Vendor matching ignores case and outer spaces (`vendor_key`); cart lines still match the vendor's exact
  cart name, like Mark placed.
- **API** (`routers/po_send.py`, all under `/api/ordering`):
  - `GET /vendor-contacts[?vendor=]` -> `{items: [{id, vendor, contact_name, email, whatsapp, notes, send_po, ...}],
    vendors, can_edit, whatsapp_enabled, whatsapp_off_reason, email_configured}`; `POST /vendor-contacts`,
    `PATCH|DELETE /vendor-contacts/{id}` (admin). 422 `{detail}` for a bad email / number or neither given.
  - `GET /po-send/preview?supplier=` -> `{items, item_count, total_qty, zero_qty_lines, contacts, recipients (default
    ticked emails), subject, body_template, note_marker, max_note, pdf_filename, from_address, email_configured,
    whatsapp: {enabled, reason, contacts: [{id, label}]}, can_manage_contacts, recent_sends, uncertain_previous}`.
    The body is `body_template` with the note in place of `note_marker`, or that paragraph dropped
    (`po_send.render_body`; website `poSendModel.renderBody`).
  - `GET /po-send/preview.pdf?supplier=` - the PDF Send would attach; not logged as a "pdf" order.
  - `POST /po-send` `{supplier, idempotency_key (8-100 of [A-Za-z0-9_-], one per dialog), recipients: [emails],
    note?, channels: ["email"], whatsapp_contact_id?, confirm_resend?}` -> 200 `{sent: true, duplicate, send}` (also
    for `partly_sent`); 502 `{detail, code: "failed"|"uncertain", send}` with the cart untouched; 409 `code:
    "in_progress"` (this vendor is being sent right now) or `"check_previous"` (a send in the last 24 h may have gone
    out unconfirmed - check Gmail's Sent folder, then resend with `confirm_resend: true`, or Mark placed); 422
    (recipient not a saved contact of this vendor, no recipients, note > 1000, `code: "whatsapp_off"`); 503 email not
    set up; 400 nothing above 0 in the cart. A repeated key returns the first attempt (`duplicate: true`), never a
    second email; after a 502, retry with a NEW key; after no answer, retry with the SAME key.
  - `GET /po-sends[?supplier=&limit=]` - the send log, newest first. `send` rows: `{id, supplier, status (sending |
    sent | partly_sent | failed | uncertain), subject, pdf_filename, item_count, email_to, email_status,
    email_error, email_message_id, whatsapp_*, placed_order_id, sent_by_name, created_at, finished_at}`.
- **On success** `purchase_order_log.place_sent_order`: the vendor's recent "pdf" row becomes the placed order (as
  Mark placed), the order holds exactly the emailed lines (qty > 0), and only the cart lines that existed when the
  send started are cleared - a line added mid-send stays. `placed_order_id` is null in the rare case the email went
  but recording failed (the website then says to press Order placed).
- **Transport:** Gmail SMTP SSL + app password, the same account/settings as the invoice emails to Toast
  (`GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`); our own `Message-ID` is stored (search `rfc822msgid:` in Sent). A broken
  connection after the message was handed over = `uncertain`, never a silent resend. The Download PDF route now
  builds its PDF with the shared `po_send.build_po_pdf` (same output).
- **WhatsApp:** implemented behind `PO_WHATSAPP_ENABLED` (default false; uploads the PDF to Whapi and sends it as a
  document). Off, the dialog shows the option disabled with the 5-chat / 402 reason. Turning it on needs a paid Whapi
  plan first.
- **Deploy:** migration 0050 (additive) + backend restart; no new required settings.

## As built - B2 inventory (items 1, 6, live stock), commits 5bf295a, 5cb4095, daefdc8

Migration **0048** (additive, plain ADD COLUMN - no table copy of the ~600k snapshot rows):
`inventory_snapshots.api_out_of_stock` + `csv_quantity` (NULL on every CSV row) and a `toast_menu_items` cache.
Code: `services/inventory_api.py`, `routers/inventory_api.py`, website `pages/InventoryApiPage.jsx` (admins,
sidebar "Inventory from Toast"), Help section "Inventory from Toast (admin)". Tests: `tests/test_inventory_api.py`
(every Toast call mocked; a guard fails any real HTTP).

### Settings (all optional; defaults keep today's behaviour except the barcode re-check)
- `INVENTORY_API_SNAPSHOTS` (false): the 04:05 job writes the API snapshot AND the live stock overlay is on.
- `INVENTORY_API_SNAPSHOT_HOUR` / `_MINUTE` (4 / 5). The job waits up to 20 min for the 04:00 sync of yesterday.
- `INVENTORY_CSV_MAX_AGE_DAYS` (7, 0 = never): "Inventory upload due" alert.
- `MENU_BARCODE_RECHECK_MINUTES` (60, 0 = off): hourly metadata GET; full menu only when `lastUpdated` moved.
- `LIVE_STOCK_CACHE_SECONDS` (300).

### Item 1 - rules, measured against the Oct 11 CSV (prod copy, real GETs at 14:17 ET)
- **Built from:** `/stock/v1/inventory` (quantity/status), `/menus/v2/menus` (name, price, sku, placement,
  Each/Container), our `orders` (sales). Carried from each item's latest CSV row: cost, supplier, supplier_item_id,
  last_received_from, inventory_last_received (none for an item with no CSV row yet). **Supplier is carried, not
  read from `plu`**: plu differs from the CSV supplier on 1,096 of 5,220 items (other spellings, e.g. "RBest" vs
  "RBest Produce Inc").
- **On hand 99.90%** (5,033 identical + 182 differing by exactly the units sold since the CSV export at 00:19; 5
  left - e.g. Maruvam 999 in the CSV, out of stock in Toast). Raw same-value rate at 14:17 was 96.4%.
- **Name 99.98%, price 100%, category group / category / subcategory 100%, barcode 99.75%** (99.98% after the
  empty-sku rule below; the 1 left is Bhakti Jav, a barcode added in Toast after the CSV).
  - Name: the catalog product name for an Each/Container variant; a " - Each"/" - Container" suffix pair without
    catalog info counts too (Deep PRE Madhi Toordal, Vital Tea); otherwise the CSV's spelling while it still matches
    the menu name with or without the suffix.
  - Price: blank for SIZE/OPEN pricing, for $0 (every Container row is $0 in the menu - that keeps the existing
    "priced row + bare row sharing a name" Container pairing working), and for menu-specific prices that differ
    between menus (Street Kitchen vs GH); else the menu price. Farmvilla Atta's priced Container row keeps $12.99.
  - Placement: the CSV's own (group, category, subcategory) while it is still one of the item's menu locations,
    else the deepest non-CONTAINER path. Items outside the CSV's category groups with no CSV row (the 2 GH-only
    dishes, Test Menu, the e-commerce mirror) are left out.
  - **Barcode (deviation):** the menu `sku` holds one barcode; the CSV's ";"-list is kept while the sku is one of
    its codes, a different sku replaces it, and **an empty sku never wipes a CSV barcode** - 12 items had a barcode
    in the 00:19 CSV and none in the menu at 11:41, so the menu alone can't prove "missing".
- **OUT_OF_STOCK:** quantity 0, `api_out_of_stock` set, `csv_quantity` = latest CSV count. Negative inventory
  (`inventory_intelligence`, `stock_issues._at_zero_stock`, `negative_cleared_since`) uses
  `inventory_api.effective_quantity`, so a CSV negative stays until the next CSV. **Known limit (contract-as-written):**
  an item that went negative AFTER the last CSV shows 0 - on the B copy (Oct 11 CSV swapped for the API set), 6 of 35
  negatives (0 in the Oct 10 CSV, -1/-2 on Oct 11) dropped off Negative inventory. They return with the next CSV.
- **Not tracked** (no stock row): quantity NULL, status "in stock", as the CSV has it (650 items). Status text copies
  the CSV ("1,210 in stock", "out of stock"). Toast quantity floats are rounded to 3 places.
- **7/30/90-day units (`_orders`) and dollars (`_sales`)**: top-level, non-voided `orders` rows, `quantity` /
  `net_price`, business days D-N .. D-1. **Deviation:** Toast's CSV figures lag two days - they match D-N-2 .. D-3 for
  93-96% of items (exactly; 96-97% within 5%), vs 74-79% for ours. We count through yesterday on purpose (more
  current). Days on hand = qty / (90-day units / 90), 0 at 0 or below; Toast's own formula isn't reproducible from
  the file (best candidate matched 24%), so it agrees on ~52%. Only Fix Issues item details and the legacy dead
  stock list read it.
- inventory_cost = qty x cost, inventory_value = qty x price, gross_margin / gross_profit from price and cost -
  the CSV's own relationships (checked on 4,441 / 4,530 / 4,813 rows).
- **CSV wins:** the job never writes a date with any CSV row (and inserts with ON CONFLICT DO NOTHING), and
  `inventory_parser.parse_and_store` deletes that date's `api:` rows before loading a CSV (`api_rows_replaced`
  in the result, a sentence in the upload's job detail).
- **Comparison:** switch on or off, the 04:05 run builds the set and, when that day's CSV is already in (the file
  usually arrives 00:00-03:00), stores a per-field comparison in `job_runs` (`inventory_api_snapshot`, JSON detail).
  `GET /api/inventory-api/compare` builds one in memory now vs the latest CSV (2 GETs, cached 10 min). Admin only.
- **Alert** `inventory_upload_due` (features overview, route home -> app Home for builds without it), 9:00 daily
  while the latest real upload (not `api:`, not `backfill:`) is N+ days old, dedupe one per day.

### Item 6 - hourly barcode re-check
- `refresh_menu_cache`: metadata (cheap) every hour; full menu only when `lastUpdated` moved; replaces
  `toast_menu_items`. The 04:05 run refreshes it from its own pull. Admin "Check barcodes now" (429 within 2 min).
- `barcode_report.get_missing_barcodes / get_invalid_barcodes` (so Fix Issues and `/api/reports/*` alike) use the
  menu barcode (same merge rule) when the menu pull is newer than the latest snapshot's upload.
- Fix Issues: a "fixed" close comes back (`reopened.how = fixed_but_still_showing`, `at` = the menu change) when
  the menu's `lastUpdated` is after the close and the item still shows; closed rows get additive `fix_confirmed`;
  lists get additive `menu_checked_at`. Rehearsal: Bhakti Jav (barcode added in Toast on Oct 11) left Missing
  barcodes at once; invalid list unchanged (88).

### Live stock - exactly which screens (switch on only; else snapshot counts, no Toast call)
- Supplier Projection (`compute_supplier_projection`), Reorder Candidates (`compute_reorder_candidates`) and
  everything built on them (9 AM critical vendors push, Vendor Calendar order-due counts, weekly digest, Ask bot).
- Fix Issues: Negative inventory and Refill from container (Slow / Dead lists stay on the snapshot: they are
  forecast lists cached per upload).
- Rule: QUANTITY -> Toast's count; OUT_OF_STOCK -> a known negative stays, else 0; not in Toast's list -> snapshot.
  Additive `live_stock: {live, as_of}` on reorder-candidates, supplier-projection and those two Fix Issues lists.
  One `/stock` GET per 5 minutes at most (0.4 s); a failure falls back silently and isn't retried for a minute.
- Rehearsal (Raja Foods, 90 d, 7-day need): 59 of 440 on-hand figures changed after a day of sales, 2 needs
  changed; Reorder 323 -> 329 items, 3 tier changes, no vendor tier change.

### Downstream reports, CSV (A) vs API snapshot of today (B), prod copy
- Fix Issues: invalid barcodes 88 = 88; slow review 106 = 106; dead with stock 1287 -> 1286 and dead zero stock
  83 -> 85 (items that sold out today); dead newly arrived 30 -> 31 (B carries last-received from the Oct 10 file);
  negative inventory 35 -> 29 (the limit above); missing barcodes 73 -> 84 (B has no Oct 11 file to carry the 12
  barcodes above from - in real use they are carried); legacy `dead_stock` (old app builds only) differs by ~430
  because it reads the lagged Toast 90-day sales.
- Reorder 323 -> 329 (6 items newly short after today's sales), Supplier Projection need changed on 0-3 items per
  vendor (Chetak 3/587, Vdyas 0/460, Raja 2/440, House of Spices 1/325) - all from on-hand moved by today's sales.

### Deploy
Migration 0048 + backend restart. Nothing changes until `INVENTORY_API_SNAPSHOTS=true` except the hourly barcode
re-check (set `MENU_BARCODE_RECHECK_MINUTES=0` to hold it back) and the new upload-due alert (users can switch it off;
`INVENTORY_CSV_MAX_AGE_DAYS=0` turns it off for everyone). Suggested: leave the switch off for the side-by-side week
and read the nightly comparison on the admin page; then turn it on.

### Measured on a read-only prod copy (Oct 11 2026)
- **Item 4, last 14 days** (14 snapshot pairs Sep 28 - Oct 11; ground truth = 584 lines / 32 vendor-days staff confirmed
  through Delivery Review, 614 lines counting the extra day): **all 32 confirmed vendor-days suggested (100%)**, 39
  suggestions in all (82% group precision; the 7 others are small one-to-five item receipts plus House of Spices IN369,
  32 items received Oct 10 and never confirmed). Ticked lines: 679, of which 582 were confirmed (86% precision), covering
  582 of 614 confirmed lines (95% recall). Quantity: staff always confirmed the raw count change; our quantity adds the
  units sold between the snapshots, so it equals theirs on 382 of 582 lines and is never lower. Stock-rise rule alone
  (what an API-only day would show): 28/32 vendor-days (88%), 70% group precision, 88% line precision, 91% recall.
  Default view now (last 3 uploads): 3 suggestions (House of Spices 32 items, Restaurant Depot 5, Harbs Farm 1); 12
  vendor-days already logged.
- **Item 5:** 3 open invoices now; 1 gets a suggestion (Restaurant Depot, Sep 28: Toast received IN341 on Sep 30).
  Replay of Sep 1 - Oct 11 (75 invoices): 69 would have had a suggestion; of the 68 that staff closed, it would have
  shown before they closed it for 33 (median 8.4 h earlier) and only after for 35 (the receiving shows in the next
  morning's upload); 6 matches look wrong (receipt after the close), 6 invoices had no evidence.
- **Item 7:** suggestions for House of Spices (Thu, 3 of 3 invoice uploads, lead 2 from placed orders), Indigenous
  Foods (Sat, 3 of 4) and KP Produce (Thu, 3 of 4). Raja Foods and Chetak have admin schedules (Thu) and get no
  suggestion; for Raja the inference agrees (Thu, 4 of 5 invoice uploads), Chetak's invoice days are a Thu/Fri mix (no
  pattern, so no check line). Toast receiving dates lag the delivery by about a day (Raja: 7 of 8 on Fri), which is
  why invoice uploads are used first.
- **Item 9 (website):** Transfer Review is off the sidebar, the landing-page fallback, the Overview quick actions (its
  slot now opens Delivery Review) and the upload card's "Container movement" box; the route, API and data stay; Help
  and the page itself say it's retired.
- **Live:** all three are computed per request (~0.3-0.7 s uncached on the prod copy, 60 s cache); no new scheduled job.
  Not live: same-day detection of a receiving (Toast's receive records only arrive with the CSV upload; the live stock
  API has quantities but no receiving numbers).

## As built - Verify 2 (adversarial backend review)

Fixes on top of B1-B4 (backend only; tests `backend/tests/test_verify2_failure_modes.py`):
- **Live Items Sold never ties up the server when Toast is slow or down.** The pull is locked per business date (the
  shared dict lock is never held across a Toast call), a request waits at most 20 s for another request's pull of the
  same date, a 429 gets one retry after at most 5 s, and a failed pull is remembered for 15 s (every request in that
  window fails fast instead of queueing behind another slow call). `/api/sales/items-sold` (and its CSV) answer **503**
  with a plain message instead of a 500 when the live part fails; past ranges never touch Toast.
- **Live stock overlay:** a 20 s timeout and no 429 sleeps (`fetch_stock_live`); one fetch at a time, other requests
  wait at most 5 s and then use the snapshot counts; `live_stock_meta` never waits for a fetch.
- **Open checks on request:** no 429 sleeps on the request (`max_retries=0`), and a failed refresh isn't retried for
  2 minutes (it used to retry on every page view, since only success rows counted as "fresh").
- **API snapshot write guard:** with `INVENTORY_API_SNAPSHOTS` on, a build from an empty stock list, or with under
  half of the latest CSV's rows / counted items, is not written (job `failed`, `stored.skipped = "implausible"`), so a
  Toast hiccup can't make every item "not tracked" for a day. The "CSV exists" check is repeated under the write lock
  (a CSV committing mid-write used to leave API rows beside it).
- **Barcode "came back" between uploads** (item 6) only when Toast's menu itself still shows the problem (its sku is
  empty / invalid, `menu_shows_barcode_issue`). The menu sku is just the first barcode, so a fixed invalid 2nd code of
  a ";"-list (kept from the CSV) used to come back on any later menu change; it now waits for the next upload.
- **Toast 401** (token revoked / expired early): `toast_get`, the orders fetch and the employees fetch log in again
  once instead of failing until the cached token expires. `toast_get` turns network errors into `ToastFetchError`.
- **Re-pull window** ends at the last *closed* business date: a run before the 04:00 closeout (a manual "Check now"
  after midnight) no longer compares and re-syncs the still-open business date (`closed_business_today`, DST-safe).
- Tests blank `TOAST_CLIENT_ID` / `TOAST_CLIENT_SECRET` (the dev and prod `.env` hold real ones).

Checked and fine: migrations 0048-0050 up / down / up on a prod copy (0.6 s up, 5.8 s down, integrity ok, 633,692
snapshot rows kept); every new write route is refused for the demo login (route enumeration test); responses stay
additive for builds 5-8 (`reopened.at` keeps its format, push route `home` exists since build 5); no secret in any
log line, error or response; the PO email (subject / filename non-ASCII encode correctly, CR/LF stripped, recipients
only saved contacts, idempotency key + per-vendor lock); a selection Toast moves to another business date is stored
once whichever date re-syncs first. Worst case new standard-API calls: Items Sold live 3 per 60 s (3 per 10 s with
Refresh held down), open checks 1 per 2 min, live stock 1 per 5 min (1 per min while failing), menu metadata 1 per
hour, re-pull <= 14 order fetches x 4 runs a day - far under Toast's 20/s and ordersBulk 5/s. Analytics: about 4 report
POSTs per scheduled run against 60/h per endpoint.

## As built - Verify 1 (data correctness on a fresh prod copy, Oct 11 14:56)

Read-only prod copy migrated to 0050; real gentle Toast calls (30 ordersBulk + 1 today, 1 stock, 1 menus, ~20 Analytics
report POSTs). Scripts and outputs: `~/.claude/jobs/e26a1611/tmp/v1verify/` (copies deleted).

- **B1 re-pull, Sep 11 - Oct 10:** run 1 (108 s) re-synced 30 days: 28 fixed, 1 already matching, 1 differing. Run 2:
  0 order fetches, **29 / 30 days equal Toast's item-level net and quantity to the cent**. Oct 10: matched at 04:00, then two
  edits made today - order #44 comped to $0 at 10:57 ET (-$199.60), open order #210 +$16.14 at 13:29 ET, a goat line
  +$0.01 - give exactly -$183.45 / +4 units; Analytics hadn't ingested them by 15:22, so the day is "waiting". **Order
  counts: Toast's `ordersCount` = non-voided, non-deleted orders in the raw answer on 30 / 30 days**; ours is lower by
  exactly the orders with no live item line (1 on 7 days, 8 on Sep 27).
- **Item 3:** Items Sold old - new = the modifier rows on every day ($13,890 over 30 days before the re-pull); the export
  drops 472 modifier rows per week. Forecast, weekly sales, digest totals and Reorder are byte-identical old code vs new
  code on the same data.
- **Bug found and fixed (993db54):** a check paid 2+ days away from its business date counted on no Items Sold date
  ($1,810.51 / 35 orders before the re-pull, $5,157.89 / 62 after it, since Aug 1). Now 30 / 30 days equal the
  top-level sum by payment day.
- **B2, API snapshot of Oct 11 built on a copy without the Oct 11 CSV vs that CSV:** name 99.98%, price 100%, group 100%,
  category / subcategory 99.98%, barcode 99.75% (12 got barcodes between the two CSVs with an empty menu sku; 1 is
  Bhakti Jav, right in the API). On hand 99.89% after netting today's sales (4,953 same + 216 by exactly units sold + 45
  OUT_OF_STOCK; 12 count edits after the CSV). **Carry-forward identical to the newest CSV row on 5,221 / 5,221 rows** for
  all five fields; 29 items with no CSV history carry nothing. Every downstream difference is explained item by item:
  Missing barcodes +12 / -1 (above), Negative 35 -> 29 (the 6 OUT_OF_STOCK negatives, contract limit), Dead with stock
  -1 / Dead zero stock +2 (sold out today; one CSV-untracked item Toast calls out of stock), Dead newly arrived +1
  (receipt after the Oct 10 CSV), legacy `dead_stock` 428 = 426 days-on-hand formula + 2 on hand (not lagged sales),
  Reorder +6 items / 5 tier / 0 vendor tier changes and Projection 376 rows (281 today's sales, 43 OUT_OF_STOCK -> 0,
  21 count edits, 31 vendor lists changed in the Oct 11 CSV).
- **CSV wins:** a CSV upload for an API date replaced all 5,221 API rows; the job then skips (`csv_exists`) and only
  compares; an API insert over a racing CSV row is ignored.
- **Kill switch off vs pre-batch code (53d3f5b), same copy, every GET report (127 calls):** inventory reports and all
  23 Fix Issues / barcode endpoints identical except additive fields. Other differences are intended: B1's Items Sold /
  export fix, B3's glossary + Transfer Review, B2's `inventory_upload_due` alert pref. With the hourly menu re-check
  cache filled (on regardless of the switch): only Bhakti Jav leaves Missing barcodes.
- **B3 suggested deliveries, 14 pairs vs Delivery Review confirms:** reproduced B3's numbers exactly - 32 / 32
  vendor-days (100%), 82% group precision, lines 86% precision / 95% recall, quantity never below the confirmed one;
  stock-rise only 88% / 70%. `compute_suggestions(include_handled=True)` still strips handled lines (unused flag).
- **Close suggestions:** invariants hold (type, open, receipt after upload - 3 h, one receipt per invoice). Time-travel
  replay (state just before each of 72 closes): 33 showed before the close. **Bug fixed (036a2c9):** a closed invoice
  took a receipt Toast recorded after its close (RD #58 lost IN358); now 34.
- **Schedule suggestions:** recounted independently (House of Spices Thu 3/3, Indigenous Foods Sat 3/4, KP Produce Thu
  3/4); scheduled vendors (Raja, Chetak) get none. Evidence is thin (3-4 dates each).

## As built - Final pass (integration and close-out, Oct 11)

Walked items 1-9 + live reports against backend, website and app. Small gaps closed here (no migration, no new
settings); runbook: `~/spicetown-dev/DEPLOY_AUTOMATION.md`.
- **Glossary:** new category `sales` and 17 terms - `sales_addons`, `sales_live_today`, `sales_accuracy_match` /
  `_fixed` / `_differs` / `_waiting`, `live_stock`, `inventory_api_snapshot`, `inventory_upload_due`,
  `barcode_menu_recheck`, `send_po`, `vendor_contact_gets_pos`, `po_send_sent` / `_partly_sent` / `_failed` /
  `_uncertain` / `_sending`. Rule texts quote setting *defaults* (`Settings.model_fields`, never this server's .env) and
  service constants; `test_automation_batch_terms_quote_the_code` pins them. App bundle regenerated.
- **Website:** (i) definitions on the Sales accuracy verdict pills / "waiting", Send PO status pills, "Gets POs", the
  Items Sold add-ons / live-today notes and the Inventory from Toast settings rows. One line saying where the numbers
  came from (`components/DataSourceNote.jsx`): "On hand is Toast's live count, as of ..." (Reorder Candidates, Supplier
  Projection, Fix Issues Negative / Refill - only when `live_stock` is sent) and "Barcodes are from Toast's menu (last
  changed ...)" on the barcode lists (`menu_checked_at` is the menu's last change, sent only when our re-read is newer
  than the latest upload). Sales accuracy card: **Check the last 60 days** (the server's cap) for the one-off history
  correction after deploy. The admin page now explains a `stored.skipped = "implausible"` night (it printed "undefined
  items stored"). Help: Open Checks refresh timing, barcode re-check, the stale "no live stock feed" / "send the PO
  yourself" limitations, and a new "What's live and what's on a schedule" section.
- **Backend:** the all-time Open Checks CSV refreshes today's part first, like the screen (`ensure_today_fresh`; same
  2-minute rule and failure backoff). Test: `tests/test_automation_closeout.py`.
- **App (build 9):** pull-to-refresh on Items Sold sends `fresh=true` (the website's Refresh; plain loads still share the
  60 s pull - an older server ignores the parameter); the same live-stock line on Reorder Candidates, Supplier
  Projection and Fix Issues Negative / Refill, and the barcode line on the barcode lists (all hidden when the fields are
  absent); Help updated for items 1-3, 6 and the live reports.
- **Not changed (larger, listed for the lead):** the app's Send PO sheet shows no send-status pills (nothing to attach
  the new terms to); the Sales accuracy table lists the last 30 days even after a 60-day check (the run summary covers
  all 60).

## As built - "Use Toast for inventory" switch + readiness (Oct 13)

Owner-approved follow-up to B2. Migration **0052** (`setting_changes`, additive; revises 0051). Code:
`services/inventory_api_switch.py`, `services/inventory_api_readiness.py`, `app/setting_models.py`; website
`components/inventoryApi/` on `pages/InventoryApiPage.jsx`. Tests: `tests/test_inventory_api_readiness.py`,
`node --test src/components/inventoryApi/inventoryApiModel.test.mjs`.

### Switch
- **Precedence:** the newest `setting_changes` row (key `inventory_api_snapshots`) wins; with no row, the .env value
  `INVENTORY_API_SNAPSHOTS` (default false) is used - it is only the starting value. Once set on the page, editing .env
  changes nothing. If the table can't be read (code before the migration) the .env value is used.
- Read on every use (no cache): the 04:05 job (`run_api_snapshot`, write = switch) and the live stock overlay
  (`live_stock_enabled`) follow a change at once - no restart, every worker. A change also drops the live stock cache.
- Rows are never updated or deleted: who (`changed_by_name`), when, value, the value before and where it came from
  (`previous_source` db / env), optional note (500 chars). Setting the value already set on the page writes nothing.
- API (admins; the demo login's PUT is refused by the global guard): `GET /api/inventory-api/switch` ->
  `{on, source: "page"|"env", env_default, changed_at, changed_by, note, history: [...], readiness: {ready, streak,
  needed, last_comparison_at}}`; `PUT` `{on: bool, note?: str}` -> same + `changed`. `GET /status` is additive:
  `settings.inventory_api_snapshots` is now the switch in force, plus `switch` and `readiness` (full, with nights).
- Website: switch card at the top; Turn on asks first (what changes: the nightly write, the live screens, CSV still
  needed for costs; warns when not ready; optional note); Turn off is immediate. Switch history table at the bottom.

### Readiness (computed when read from the nightly job rows; nothing extra stored)
- The nightly comparison now also stores, additively: on hand `tracked` (counted on either side), `tracked_same`,
  `explained_by_sales` (CSV count minus units paid after the CSV upload time and before the stock pull = Toast's count,
  or <= 0 for OUT_OF_STOCK), `unexplained`, `matched_or_explained_pct`, `unexplained_examples`; top-level `placement`
  (group + category + subcategory all equal) and `sales_window`. "Compare now" gets the same keys without the sales
  part (today's sales aren't synced yet).
- A night passes when on hand matched-or-explained >= `INVENTORY_API_READY_QTY_PCT` (99), price >= `_PRICE_PCT` (99),
  barcode >= `_BARCODE_PCT` (99), category >= `_CATEGORY_PCT` (100) and unexplained on-hand differences <=
  `_MAX_UNEXPLAINED_QTY` (10). Ready = the newest `INVENTORY_API_READY_NIGHTS` (7) nights in a row passed.
- A missed night or a failed run (Toast error, cut-short answer) resets the streak; the page shows the date and
  reasons. A night with no CSV by 04:05 (or the API snapshot written) is "not counted" and doesn't reset. One night =
  the newest run of that business date. Comparisons recorded before this change are scored on exact matches only.
- Sales time = the line's paid time, else its opened time: on the Oct 11 rehearsal (CSV 00:19, pull 14:17, real saved
  Toast answers on a prod copy) it explained 182 of 187 on-hand differences (99.89%, 5 left - the B2 numbers) vs 176
  with the opened time. That rehearsal night passes every threshold (price 100%, barcode 99.98%, category 100%).

### Ready alert
- Push `inventory_api_ready` (admins only, route `inventory_api`, data `url` = PUBLIC_BASE_URL + `/#/inventory-api`,
  body "Inventory from Toast is ready - tap to turn on"), 9:00 daily job, quiet hours respected, only while the
  switch is off, once per streak (dedupe `inventory_api_ready:<streak's first night>`; a new streak after a reset
  alerts again). App builds without the route open Home.
- WhatsApp: only with `INVENTORY_API_READY_WHATSAPP=true` (default false - the Whapi plan is a 5-chat sandbox, no chat
  added): one text with the link to `WHAPI_CHAT_ID` (the invoice chat), once per streak (notification_log
  `...:whatsapp`; a timeout is recorded as sent so it is never posted twice).

### Deploy
Migration 0052 + backend restart; website build. Nothing changes until an admin flips the switch (the .env value stays
in force). Prod on Oct 13: no nightly comparison recorded yet (the running backend predates B2), so the streak is 0/7.

### Verify pass (Oct 13, adversarial; throwaway server on a prod copy at 0052, Toast mocked)
Held: flips reach the live overlay at once (off = no Toast call and `live_stock` null; on = one fetch, then cached;
each flip drops the cache), .env vs page precedence across a restart, 401 / 403 (staff, demo) / 422, the 9 AM push
(admins with devices only, quiet hours, never while on, once), the app's parser on the real answers, the page at
1280 / 375. Fixed (tests in `test_inventory_api_readiness.py`, "Verify pass" block):
- **Simultaneous changes broke the history** (20 parallel PUTs: 5 of 10 rows had a wrong "before" or were "turned
  on, was on"). `set_switch` now reads and writes under one lock; the newest change is the highest id (a clock step
  back can't hide one).
- **A streak longer than the 30 listed nights re-sent the "ready" push every morning** (its first night - the dedupe
  key - slid with the list). The streak is now counted over every night back to the last bad one; the list stays 30.
  "Last comparison" is the newest compared night even when the newest run failed.
- **The nightly run read the switch before Toast's GETs** (minutes): it now reads it after they answer, so a flip
  during the run applies to that run.
- Website: right after turning on, the Off button read "Turning off…" while the status reloaded.
Not changed (by design): a night with no run at all (server down at 04:05) is not listed and doesn't reset, like a
night without a CSV. DST (Nov 1): the sales window and nights are UTC / business-date based - checked by a test.

### Late CSV re-run (Oct 13 follow-up)
The day's CSV often lands after the 04:05 run (prod: Sep 24 06:49, Sep 26 06:18, Sep 30 05:07, Oct 2 07:58 store
time), so that night had nothing to compare and didn't count. Now a CSV for **today** that lands after the date's
nightly run triggers a second run of the same date (`inventory_api.rerun_for_late_csv`): Toast's stock + menu are read
again (same two GETs) and compared with the file; it never writes inventory (the CSV is in). The job row is an
ordinary `inventory_api_snapshot` run for that business date with additive detail `rerun: {reason: "late_csv",
csv_uploaded_at}`; being the newest run of the date, it is that night for readiness (night entry gains `rerun`).
- **Triggers:** the upload (background task after the response, only with `SCHEDULER_ENABLED` - dev never calls Toast),
  and the nightly job's own end (a file that came in while it waited for Toast).
- **Due only when** (`late_csv_rerun_due`, reason codes in brackets): the date is today [not_today]; before
  `INVENTORY_API_LATE_CSV_UNTIL_HOUR` store time, default **9**, 0 = never [off / after_cutoff]; today's CSV is in
  [no_csv]; the date has a finished nightly run [no_nightly_run] that succeeded [nightly_failed - a failed night stays
  bad]; no run of the date compared already [already_compared]; none is running (a "running" row over 1 h old is
  treated as dead) [running]; fewer than 2 re-runs tried [tries_used]. One at a time per process (a second caller
  skips). So it is idempotent per date: once compared, never again, even for a re-uploaded file.
- **Why the cutoff:** the store sells from ~09:30 (6 weeks of prod orders: 9 lines before 09:00), and today's sales
  aren't synced until the next 04:00, so a later pull would count sales since the file as unexplained differences - a
  false bad night that resets the streak. Of the prod uploads Sep 10 - Oct 13 every late one but Sep 18 (10:47) was
  before 9.
- **Toast failing on the re-run** marks the night "not counted" (note says so), never bad; one more try on the next
  trigger.
Tests: `tests/test_inventory_api_late_csv.py`.
