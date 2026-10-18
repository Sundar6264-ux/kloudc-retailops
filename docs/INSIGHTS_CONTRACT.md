# Insights from the Toast Analytics API (contract, Oct 13 2026)

Owner: "Build all 1-10, in both app and website. Then we'll release the app build." Bank deposits and labor
stay out of scope.

## Data sources
- **Analytics client:** `backend/app/services/toast_analytics.py`, already in production with credentials in the
  prod `.env`.
- **Evidence:** `/Users/sundar/.claude/jobs/e26a1611/tmp/toast-analytics/` (field lists, rate limits, sample
  shapes, docs).
- **Report rate limits:**
  - metrics and menu reports, day/week: 10/min and 60/hr (month/year/custom: 10/hr);
  - `check/day`, `payout/payments` and `guest/payments`: 5/min and **60/day**.
- **Cache:** an `analytics_cache` table (migration **0051**): `report`, `params_hash`, `business_date`,
  `fetched_at`, `payload_json`.
  - **Past business dates** are fetched once and reused, then refreshed by the re-pull window (the last 14 days,
    as edits land up to 6 days later).
  - **Today** is fetched live, with a short cache (60-120 s).
- **Nightly job** (after closeout, about 04:30 ET): check report and guest payments for yesterday, plus a
  re-pull for any day in the last 7 whose metrics changed (reuse sales_accuracy's change detection where
  sensible). Never exceed the 60/day caps; keep a budget.
- **If Analytics isn't configured or Toast fails:** endpoints return what's cached, with `stale: true`. Never 500.

## Access
- **New feature key `sales_insights`**, label "Sales insights". Admins have it implicitly.
- **`hide_sales` users never get dollar amounts.** They get 403, or the counts-only variants noted per endpoint.
- **Per-employee data (#5, #6) is admin only.**
- **Demo account:** read-only. The demo guard covers writes; there are none here except "acknowledge alert".

## API (prefix `/api/insights`)
All endpoints are GET unless noted. All money is a number in dollars. Every response includes
`{generated_at, stale, source: "toast_analytics"}`.

1. **`/hourly-heatmap?weeks=4|8|12`** returns `{days: [Mon..Sun], hours: [0..23], cells: [{dow, hour, avg_net_sales, avg_orders, max_net_sales}], busiest: [{dow, hour, avg_net_sales}], quietest: [...]}`.
   - Built from metrics/day with `aggregateBy=HOUR` per business date (cached), averaged over the weeks.
   - `hide_sales` users get `avg_orders` only.
2. **`/channels?weeks=8`** returns `{weeks: [{week_start, total_net, by: [{channel, net_sales, orders, share}]}], channels: [...]}`.
   - The channel is revenue center and dining option (or order source) from metrics groupBy; pick the most
     meaningful split. On this store the revenue centers are In-Store Purchase, Online Ordering and No Revenue
     Center.
3. **`/compare?date=YYYY-MM-DD`** (default yesterday) returns `{date, net_sales, orders, avg_check, guests, vs_last_week: {date, net_sales, pct}, vs_4wk_avg: {...}, vs_last_year: null|{...}}`.
   - It's also added to the existing daily summary push and the weekly digest text, additively.
4. **`/check-trends?weeks=12`** returns `{days: [{date, avg_check, guests, orders, net_sales}], weekly: [...]}`.
5. **`/loss-watch?days=30`** (admin) returns
   `{daily: [{date, discount, void, refund, comp_estimate, discount_checks, void_checks}], by_employee: [{employee, checks, discount, void, refund, unusual: bool}], flagged_checks: [{date, check_number, employee, total, discount, refund, void, reason_flag}]}`.
   - "Unusual" means above 3× the employee's 30-day median or a fixed threshold (setting); a 100% comp counts too.
   - **Alert:** alert key `loss_watch` goes to admins at 09:00 the next day when something is flagged. It's
     combined into one message and respects quiet hours.
   - `POST /loss-watch/{id}/ack` lets an admin acknowledge an alert.
   - Masks nothing for admins; no guest card data.
6. **`/odd-checks?days=7`** (admin) returns `{items: [{date, check_number, opened_at, paid_at, minutes_open, modified_after_paid: bool, refund, total, employee, why: [..]}]}`.
   - Flags: open longer than N hours (setting, default 3), modified after payment, large refund (setting).
   - Rolled into the same 09:00 loss_watch alert.
7. **`/menu-performance?days=28&group=all|kitchen|grocery`** returns `{top: [...], bottom: [...], rising: [...], falling: [...], not_sold: [...]}`.
   - Each item is `{item_guid, name, menu_group, qty, net_sales, prev_qty, change_pct, last_sold}`.
   - Built from the menu report by MENU_ITEM (and MENU_GROUP for grouping).
   - **"Kitchen":** the Street Kitchen menu groups; decide from the menus data and document it.
   - **`not_sold`:** active menu items (kitchen) with no sales in N days. Use `toast_menu_items` from migration 0048
     or the menus API.
8. **`/addons?days=28`** returns `{modifiers: [{name, group, qty, net_sales, attach_rate (vs parent item qty when derivable)}]}`.
   - From the menu report with groupBy=MODIFIER.
9. **`/prep-forecast?date=YYYY-MM-DD`** (default tomorrow) returns `{date, items: [{item_guid, name, expected_qty, low, high, by_hour: [{hour, qty}] | null}]}`.
   - Kitchen items only.
   - Same weekday over the last 8 weeks, trimmed mean, with a trend adjustment. Keep it simple and explainable; show
     the basis ("based on the last 8 Saturdays").
   - Hourly split from the hourly metrics share if the item-level hour isn't available.
10. **`/returning-customers?weeks=8`** returns `{weeks: [{week_start, card_customers, returning, returning_rate, avg_visits}], overall: {...}}`.
    - From guest payments (card fingerprint).
    - **Never return or store card details or names.** Only counts, with fingerprints hashed again (HMAC with a
      server secret) and kept 400 days at most.

## Website
- **Navigation:** a new "Insights" section in the sidebar, gated by `sales_insights`. Admin-only items are hidden
  from others.
- **Pages:**
  - Heatmap: a colour grid with a tooltip.
  - Channels: stacked bars or lines with shares.
  - Daily compare: cards showing ▲/▼ %.
  - Check trends: lines.
  - Loss watch: daily table, per-employee table, flagged checks with Acknowledge.
  - Odd checks: a table.
  - Menu performance: tabs for Top / Bottom / Rising / Falling / Not sold, with a group filter.
  - Add-ons: a table.
  - Prep forecast: a date picker and a printable list.
  - Returning customers: a rate line and KPIs.
- **Charts:** follow the existing site styles. Use lightweight SVG/CSS charts or the chart library the site
  already uses; check `package.json` and don't add a heavy dependency.
- **Every page:** a data-source line ("From Toast Analytics, updated …") and an empty or stale state.
- **Glossary and help:** entries for every metric.
- **CSS:** `:hover:not(:disabled)` on every custom button. Must work at 375px.

## App (build 9)
- **Navigation:** More → **Insights**, gated by the feature, with the same 10 screens laid out for phones.
  - Heatmap: a scrollable grid.
  - Charts: simple line and bar charts with no heavy package unless the app already has one (check `pubspec`).
  - Prep forecast: shareable as text.
- **Home:** an optional Today card line "Sales vs last week ▲8%" for users who can see sales.
- **Search:** entries for each screen.
- **Push:** `loss_watch` notifications open Loss watch.
- **Layout:** must pass the device matrix (`test/support/device_matrix.dart`).
- **Release:** at the very end the lead bumps the version to 2.0.0+9.

## Rules
- Python 3.9 and `UTCDateTime`. Responses are additive only.
- Mock all Toast calls in tests. Never send real pushes or WhatsApp.
- No prod DB writes outside deploy. `nice` for heavy commands.
- Stage only your own hunks. Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## As built - backend (Oct 13)

Commit `24213f1` on ops-fixes (later backend commits add to it). Code: `backend/app/services/insights_data.py`
(cache, fetch, budget, nightly), `services/insights.py` (numbers), `routers/insights.py`, `app/insights_models.py`,
migration `0051_insights.py`, tests `tests/test_insights.py`.

**Conventions.** Money: dollars, 2 decimals. `share` and `returning_rate` are **fractions 0..1**; every `pct` is a
percent change (8.0 = up 8%, null when there's no base). Weeks start on **Monday**; the last week can be `partial`.
"Yesterday" = the last business date closed at the 04:00 closeout. Every answer also carries (additive):
`configured` (Analytics creds present), `updated_at` (newest fetch used, ISO), `filling` (a background fill is
running), `coverage: {dates_expected, dates_cached}`. `stale` is true when any needed date isn't cached yet, Toast
failed on a live pull, or Analytics isn't configured.

**Access.** Router gate = `sales_insights` (admins implicit). hide_sales (non-admin): `hourly-heatmap` answers
counts only (`cells: [{dow, hour, avg_orders}]`, busiest/quietest `{dow, hour, avg_orders}`, `sales_hidden: true`);
`prep-forecast` and `returning-customers` have no dollars and answer normally; channels, compare, check-trends,
menu-performance and addons answer **403** ("Sales numbers are hidden for your account."). `loss-watch`,
`odd-checks` and the ack are admin only (403 otherwise). Demo login: reads work; the ack is refused by the demo guard.

**Endpoints (as built; extra fields are additive).**
1. `/hourly-heatmap?weeks=4|8|12` (default **8**; anything else 422) -> `{weeks, days, hours, cells (all 168), busiest
   (5), quietest (5), days_used, sales_hidden, from, to}`. Quietest only counts hours with orders on at least half
   of those weekdays (store open).
2. `/channels?weeks=1..12` (default 8) -> `{weeks: [{week_start, total_net, by: [{channel, net_sales, orders,
   share}], days, partial}], channels: [names, biggest first], split: "dining_option", totals: [{channel, net_sales,
   orders, share}]}`. **Channel = Toast dining option** (more useful than revenue center here: revenue center puts the
   kiosk and catering into In-Store): In-store, Pick up (the kiosk), Online ordering, DoorDash, Uber Eats, Catering,
   Other. Every week lists every channel (zeros included) for stacking.
3. `/compare?date=YYYY-MM-DD` (default yesterday; future -> 400; today = live, 90 s cache) -> `{date, weekday,
   net_sales, orders, avg_check, guests, vs_last_week: {date, net_sales, pct, orders, orders_pct}, vs_4wk_avg:
   {dates, weeks_used, net_sales, pct, orders, orders_pct}, vs_last_year: null|{date, net_sales, pct}, partial,
   through_hour}`. Today compares up to the same time of day (hourly cache): the base days' hours before the current
   one plus the current hour's share for the minutes gone; after midnight (before the 04:00 closeout) every hour
   since 04:00 counts. A base day without its hourly report is left out (never compared whole; `stale` until it's
   fetched). Last year = 364 days back; null before the store's first sales (May 26 2026).
4. `/check-trends?weeks=1..13` (default 12) -> `{weeks, days: [{date, avg_check, guests, orders, net_sales}],
   weekly: [{week_start, avg_check, guests, orders, net_sales, days, partial}]}`.
5. `/loss-watch?days=1..30` (default 30, admin) -> `{days, daily: [{date, discount, void, refund, comp_estimate,
   discount_checks, void_checks, checks, item_voids}] (newest first), by_employee: [{employee, checks, discount, void,
   refund, unusual, flagged, median_loss}], flagged_checks: [{date, check_number, employee, total, discount, refund,
   void, reason_flag, reasons, id, acked, acked_at, acked_by}], thresholds}`.
   - Reason codes: `full_comp`, `large_discount`, `large_refund`, `large_void`, `above_employee_median`
     (`reason_flag` = the first, most serious). `unusual` = the employee has a flagged check in the window.
   - Void: per day it's Toast's `voidOrdersAmount`; per check / employee it's the voided item lines from our synced
     orders (Toast's check report has no void amount - a voided check shows $0). Comp estimate = discounts on checks
     discounted to $0. Thresholds: settings `LOSS_WATCH_LARGE_DISCOUNT` 50, `_LARGE_REFUND` 25, `_LARGE_VOID` 50,
     `_MEDIAN_MULTIPLE` 3 (needs 5+ checks with a loss in 30 days, never under $25 - at $10 a single voided $15.99 dish was "unusual" on real data).
   - `POST /loss-watch/{id}/ack` (admin; `id` = `flagged_checks[].id` or `items[].id` from odd-checks) -> `{id, kind,
     date, check_number, reasons, acked: true, acked_at, acked_by}`; idempotent; 404 unknown id.
6. `/odd-checks?days=1..30` (default 7, admin) -> `{days, items: [{date, check_number, opened_at, paid_at,
   minutes_open, modified_after_paid, refund, total, employee, why, status, modified_at, id, acked, acked_at,
   acked_by}], thresholds}`. `why` codes: `open_long` (> `ODD_CHECK_OPEN_HOURS` 3 h; opened_at comes from our synced
   orders, the check report only has a date), `modified_after_paid` (changed after the business day's 04:00 closeout and > 60 min after
   payment - same-day edits are routine here, e.g. the kitchen bumping an order; not counted for Online Ordering /
   No Revenue Center / Pick Up (kiosk) orders unless refunded, prepaid orders, or Toast batch edits = 3+ checks of
   the day changed in the same minute), `large_refund` (>= `ODD_CHECK_LARGE_REFUND` 25).
7. `/menu-performance?days=1..56&group=all|kitchen|grocery` (default 28, all) -> `{days, group, period, prev_period,
   top, bottom, rising, falling, not_sold, menu_map, kitchen_rule}`; each item `{item_guid, name, menu_group, qty,
   net_sales, prev_qty, change_pct, last_sold, prev_net_sales}`; 20 per list. Rising/falling need prev_qty >= 3.
   **Kitchen** = items in the two Street Kitchen menus ("STREET KITCHEN", "STREET KITCHEN (GH)") except their bottled
   / canned drinks groups ("Drinks - ..." other than "Drinks - House Drinks"). **Grocery** = Retail, Produce, Halal
   Meat, Fresh Flowers, Fish Market items that aren't kitchen. From the Menus API (`/menus/v2/menus`), cached as
   `menu_map` and refreshed nightly when Toast's lastUpdated moves. `not_sold` = kitchen items with no sale in the
   period (empty for grocery). `toast_menu_items` (0048) is empty on prod and has no menu groups, so it isn't used.
8. `/addons?days=1..56` (default 28) -> `{days, modifiers: [{name, group, qty, net_sales, attach_rate, modifier_guid,
   parent_qty}], menu_map}`; attach_rate = qty / qty sold of the items whose modifier groups offer it (null if unknown, or above 1 - the menu
   then doesn't list every item offering it). Toast's MODIFIER report files some choices under another modifier's
   guid on the same item (a platter's sauce under its size "Regular"), so rows are taken as (guid, name): a name that
   isn't the guid's own joins the option of that name (on the same items when the name is reused); one that matches
   no option is listed under its name with `group` null and `modifier_guid` = the guid Toast gave. Per-name totals then
   match our synced orders (Sep 15 - Oct 12: 2,180 vs 2,194 choices; keyed by guid alone "Chicken" read 174 vs 21).
9. `/prep-forecast?date=YYYY-MM-DD` (default tomorrow; > 14 days ahead -> 400; hide_sales OK) -> `{date, weekday,
   basis, sample_dates, by_hour_source, items: [{item_guid, name, menu_group, expected_qty, low, high, by_hour:
   [{hour, qty}]|null, trend_factor, samples}], menu_map}`. Same weekday x last 8 weeks, mean without the highest and
   lowest day, times a trend factor (last 28 days vs the 28 before, half the change, clamped 0.8..1.2). low/high =
   25th/75th percentile x factor. by_hour = the store's hourly order share on those weekdays (Analytics has no item
   hours). Items under 0.5 expected are left out. `basis` is ready-to-show text ("Based on the last 8 Tuesdays ...").
10. `/returning-customers?weeks=1..12` (default 8; hide_sales OK) -> `{weeks: [{week_start, card_customers,
    returning, returning_rate, avg_visits, days, partial}], overall: {card_customers, returning, returning_rate,
    avg_visits, from, to}, history_from, retention_days: 400}`. Returning = the card paid before that week (or on 2+
    days in the period for `overall`). Only counts ever leave the server.

**Cache / budget.** `analytics_cache` rows per (report, date): metrics_day, metrics_dining, metrics_hour, menu_item,
menu_modifier, check, guest (marker with counts only), menu_map. Nightly job `insights_nightly` 04:30
(`INSIGHTS_NIGHTLY_*`): week reports re-pulled for the last 14 days; hourly and check reports re-pulled only for days
whose day metrics changed (last 14 / 7 days); history filled a little each night (hourly 40 calls a night, so 12
weeks of heatmap takes ~3 nights); check and guest reports capped by `INSIGHTS_CHECK_DAILY_BUDGET` 30 /
`INSIGHTS_GUEST_DAILY_BUDGET` 10 per rolling 24 h, counted in `analytics_call_log` (survives restarts) - one row per
report POST, so the client's retries (5xx) and new requests (404/409) count too and are refused once it's spent. A page with
missing days starts one background fill in production only (`SCHEDULER_ENABLED`); dev never calls Toast on its own.

**Privacy.** Guest payment rows are reduced on arrival to (payment date, HMAC-SHA256 of the card fingerprint with
`INSIGHTS_HMAC_SECRET`, or a random key created once in `INSIGHTS_HMAC_KEY_FILE` chmod 600) in `guest_visits`;
nothing else from the row (last 4, type, PAR, name, raw fingerprint) is stored, logged or returned. Purged after 400
days; cached check rows (they hold server names) after 120.

**Alerts.** `loss_watch` (admins only, `features: ["sales_insights"]`, route `loss_watch`, data `{date}`): one push at
09:00 for unacknowledged Loss watch + Odd checks flags of the last 7 days not pushed before; title "Loss watch: N
checks to review", body like "1 full comp, 2 large discounts, 1 check open too long (Asha K., Ben L.)"; quiet hours
respected by the job runner. Acknowledged flags never push.

**Deviation - daily summary push.** There was no existing daily summary push in the backend (the only daily
summaries are the open-checks ones, which go to people who may have sales hidden). So the compare is a new alert
`daily_sales_summary` ("Yesterday's sales", 09:00, route `insights_compare`, data `{date}`), sent only to users with
`sales_insights` who can see sales: title "Sunday: $6,253 net sales", body "vs last Sun ▲8% · vs 4-week avg ▼3% · 219
orders". The weekly digest gains `data.sales.toast_compare` `{week_start, week_end, net_sales, orders, vs_last_week:
{net_sales, pct}, vs_4wk_avg: {net_sales, pct, weeks_used}}` and one fixed sentence appended to `narrative.sales`.
The digest asks for the week ending today, which is still open: the compare covers the 7 closed days ending on the
last closed business date (`week_end` says which).

**Glossary keys** (category `insights`): insights_source, insights_stale, insights_net_sales, insights_orders,
insights_avg_check, insights_guests, insights_heatmap, insights_hourly_average, insights_busiest, insights_quietest,
insights_channels, insights_channel, insights_channel_share, insights_compare, insights_vs_last_week,
insights_vs_4wk_avg, insights_vs_last_year, insights_check_trends, insights_loss_watch, insights_discount,
insights_void, insights_refund, insights_comp_estimate, insights_loss_flag, insights_loss_full_comp,
insights_loss_large_discount, insights_loss_large_refund, insights_loss_large_void, insights_loss_above_median,
insights_unusual, insights_loss_acknowledged, insights_odd_checks, insights_odd_open_long,
insights_odd_modified_after_paid, insights_odd_large_refund, insights_menu, insights_menu_top, insights_menu_bottom,
insights_menu_rising, insights_menu_falling, insights_menu_not_sold, insights_menu_kitchen, insights_menu_grocery,
insights_addons, insights_attach_rate, insights_prep, insights_prep_expected, insights_prep_range,
insights_prep_by_hour, insights_returning, insights_card_customers, insights_returning_customer,
insights_returning_rate, insights_avg_visits. Reason code -> key: `insights.REASON_GLOSSARY_KEYS`.
**The app's bundled glossary is not regenerated by the backend** - final step: `cd website/backend &&
.venv312/bin/python scripts/export_glossary.py` (until then `test_app_bundled_copy_is_current` fails). Done Oct 13 (mobile
`c125fcb`, ops-fixes).

**Rehearsal (Oct 13, read-only prod copy, 84 real Analytics calls: metrics/week 22, metrics/day 29, menu/week 24,
check/day 5, guest/payments/week 4; Menus API 2 GETs).** Outputs: `~/.claude/jobs/e26a1611/tmp/insights/`
(`numbers.txt`, one JSON per endpoint, `alerts_not_sent.json`, `calls.json`; scripts in `scripts/`). Cold fill took
~14 min (the client's own rate budget paces it); every endpoint then answers from the cache in 7-350 ms (today's live
compare 4.3 s). Menu map: 5,222 items, 111 kitchen. Busiest: Fri 7 PM ($1,336 avg), Fri 6 PM, Thu 6 PM, Sun noon.
Channels (8 weeks): In-store 86.7%, Pick up 7.5%, Online ordering 2.4%, Catering 1.4%, DoorDash 1.3%. Mon Oct 12:
$4,555 net, 165 orders, avg check $27.61, +1.6% vs last Monday, +19.3% vs the 4-week average. Loss watch Oct 8-12:
12 flagged checks (9 full comps incl. $509.13 and $205.57, 3 large voids unusual for the employee); Odd checks: 14,
all open too long (8 still open). Prep for Wed Oct 14 (last 8 Wednesdays): Veg Samosa Each 29.8, Onion Samosa Each
6.9, Chicken Tikka Roll 6.0. Returning customers: 44.9% for the week of Oct 5 (750 cards; only 4 weeks of history in
the rehearsal, so it reads low early on). First loss_watch push would have been "Loss watch: 22 checks to review".

## As built - website "Also in Toast" (Oct 14)

Owner's decision: the Insights that only repeat Toast's own reports move to a separate, de-emphasised section named
**Also in Toast** with the note "Same numbers as Toast's own reports - kept here for convenience." No backend change.
- **Sidebar** (`featureRoutes.js` `NAV_GROUPS`): Insights lists Daily compare, Loss watch (admin), Odd checks (admin),
  Prep forecast, Menu trends, Add-ons, Returning customers; then a nested, collapsed-by-default group **Also in Toast**
  (`subgroups`, `ALSO_IN_TOAST` / `ALSO_IN_TOAST_KEYS`): Sales channels, Check size & guests, Busy hours, Best & worst
  sellers. Opening it shows the note; a link straight into one of its pages opens both levels. Visibility rules are
  unchanged (`adminOnly` / `salesOnly`): a "Hide sales numbers" user still sees Prep forecast, Returning customers and
  (under Also in Toast) Busy hours.
- **Menu performance is split** - one component (`MenuPerformancePage.jsx`, prop `view`), same endpoint:
  `/insights/menu-trends` = **Menu trends** (Rising, Falling, Not sold; main section) and `/insights/best-sellers` =
  **Best & worst sellers** (Top, Bottom; Also in Toast). The open list is `?tab=` (kept on reload).
- **Redirects:** `/insights/menu?tab=top|bottom` -> Best & worst sellers on that tab; `/insights/menu` with any other or
  no tab -> Menu trends (that tab when it has it) - `menuPerformanceRedirect` in `insightsModel.js`. `/insights` and the
  fallback for a page someone can't open go to `insightsHomePath`: Daily compare, or Prep forecast for a hide-sales user
  (was Busy hours). Every other URL is unchanged.
- **Pages:** the four Also in Toast pages show an "Also in Toast" tag + the note under the title (`InsightsPage
  alsoInToast`). Help > Insights follows the new order, with an Also in Toast part at the end.
- Tests: `node --test src/components/insights/insightsModel.test.mjs` (views, redirects, sidebar order/permissions).

## As built - app "Also in Toast" (Oct 14)

Same decision on the app (mobile `13c993b`, branch ops-fixes). No backend change, no new endpoint.
- **Insights hub** (`lib/features/insights/insights_access.dart` enum order, `insights_hub_screen.dart`): Daily compare,
  Loss watch (admin), Odd checks (admin), Prep forecast, Menu trends, Add-ons, Returning customers; then a lower
  **Also in Toast** header with the note and quieter tiles (`HubTile(muted: true)`: outline only, grey icon): In-store
  vs online, Check trends, Busy hours, Best & worst sellers. The Store tab keeps its one Insights tile under Sales &
  Insights (subtitle now names the main screens). Visibility (`InsightScreen.allows`) is unchanged: hide-sales users
  still get Prep forecast, Returning customers and Busy hours (orders).
- **Menu split:** one `MenuPerformanceScreen` (`MenuView.trends` = Menu trends, `MenuView.sellers` = Best & worst
  sellers), same `/menu-performance` request; the view picks the chips (Rising / Falling / Not sold vs Top / Bottom).
  Enum names `menuTrends` and `sellers`.
- **Push keys** (`insightScreenFromKey`, route `insights_<key>`): the old `menu` / `menu_performance` and
  `menu_trends` / `menu_rising` / `menu_falling` / `menu_not_sold` open Menu trends; `sellers` / `best_worst_sellers` /
  `best_sellers` / `top_sellers` / `menu_top` / `menu_bottom` open Best & worst sellers. No backend alert uses them yet.
- **Search** (`feature_index.dart`): one entry per screen; "menu performance" finds both halves first; the Also in
  Toast screens' location reads "Store › Sales & Insights › Insights › Also in Toast".
- The glossary term `insights_menu` ("Menu performance", `glossary.py`, exported to the app's `glossary_data.dart`) is
  left as is; both pages use the per-list terms (`insights_menu_top` ... `insights_menu_not_sold`).
