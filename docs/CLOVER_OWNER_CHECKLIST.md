# Clover: owner checklist

What the owner does once so a Clover store can connect, and the checks to run
on a Clover sandbox before the first real Clover store. Everything in RetailOps
for Clover is built and **off until these steps are done**: the Clover card
shows "Not set up on this service yet" for Connect with Clover, and a Clover
store keeps every POS page and job off until it is connected.

Built in website-ready track D (`docs/PLATFORM_CONTRACT.md`, "As built -
Website ready: D"). Plan reference: multi-store plan 4.7 and Appendix A.

---

## 1. Clover developer account and app (about 1 hour, plus Clover's review)

1. Create a developer account at **developer.clover.com** (free). Use the
   KloudC business email, not a personal one.
2. In the developer dashboard, create an app named **KloudC RetailOps**.
3. **Permissions** (read only - RetailOps never writes to Clover):
   - Inventory: Read
   - Orders: Read
   - Employees: Read
   - Merchant: Read
   - Payments: Read (refunds and payment times)
4. **App settings → REST configuration**:
   - Site URL: `https://app.kloudc.com`
   - Default OAuth response: **Code**
   - Alternate launch path / redirect URI:
     `https://app.kloudc.com/api/settings/integrations/clover/callback`
5. Copy the **App ID** and **App Secret** into the server's settings
   (`~/kloudc-retailops/secrets/retailops.env` on the pilot Mac - the secrets
   file, mode 600 - or `.env.platform` on a cloud VM):

   ```
   CLOVER_APP_ID=...
   CLOVER_APP_SECRET=...
   CLOVER_ENVIRONMENT=sandbox        # sandbox | us | eu | la
   # Only if the public address differs from PLATFORM_PUBLIC_URL:
   # CLOVER_REDIRECT_URL=https://app.kloudc.com/api/settings/integrations/clover/callback
   ```

   Restart the server. Settings > Integrations > Clover now shows
   **Connect with Clover**.
6. Keep `CLOVER_ENVIRONMENT=sandbox` until every check in section 3 passes.
   Then switch to `us` (US and Canada), `eu` or `la` - the app must be
   approved for that region (Clover App Market review, or a private app).

The App Secret is never shown by RetailOps, never logged, and never editable
from the master console.

## 2. Sandbox merchant (about 30 minutes)

1. In the developer dashboard, open **Test merchants** and create one
   (US, Retail). Note its **Merchant ID** (13 letters and digits).
2. Add test data in the sandbox web dashboard (sandbox.dev.clover.com):
   - 20+ items with UPC codes, some with **cost**, two categories each on a
     few items, one **per-unit** item (e.g. sold by lb), one **variable
     price** item, one hidden item;
   - turn on **stock tracking** and set counts, including one below zero;
   - two employees (one deleted);
   - order types: Dine In, To Go / Pick Up, Delivery;
   - orders: a plain sale, one with a modifier that has a price, one with a
     line discount (%) and an order discount ($), a weighed item, a refund,
     an order left **open**, an order rung up between midnight and 4 AM.
3. In RetailOps, sign up (or provision) a test store with POS = Clover, then
   either:
   - **Connect with Clover** (needs section 1), or
   - **API token**: in the sandbox dashboard, Account & Setup → API Tokens →
     create a token with the read permissions above; paste it with the
     Merchant ID, region **Sandbox (testing)**, Save, **Test connection**.

## 3. Checks on the sandbox (plan Appendix A)

Run each and write the answer next to it. Anything that differs from the
"expected" column is a mapping change in `app/pos/adapters/clover/mapping.py`
with a fixture in `backend/tests/fixtures/clover/`.

| # | Check | How | Expected by the adapter |
|---|---|---|---|
| 1 | **Modifier prices** | Order with a priced modifier; compare Items Sold with Clover's sales report | Line price excludes modifiers; each modifier is its own row with its own amount (`MODIFIER_PRICE_IN_PARENT` off) |
| 2 | **Per-unit lines** | Sell the same fixed-price item ×3 | Clover sends 3 line items (one per unit); each is its own row, quantity 1 |
| 3 | **Weighed items** | Sell 1.5 lb of the per-unit item | `unitQty` = 1500 → quantity 1.5; net = price × 1.5 |
| 4 | **`modifiedTime` filter** | Edit an old order, then run a changed-orders pull | `filter=modifiedTime>=…` returns it |
| 5 | **`state=open`** | Leave an order open; open Open Checks | `filter=state=open` returns it; it disappears once paid |
| 6 | **`summaryHour`** | Compare `/properties.summaryHour` with Clover's day-end report | Business date = local time minus the store's cutoff (`BUSINESS_DAY_CUTOFF_HOUR`, default 4); confirm whether summaryHour should set the cutoff |
| 7 | **Cost units** | Item with cost $1.25 | `cost` = 125 (cents) → 1.25 |
| 8 | **Tax** | Taxed line | `taxRates[].rate` 1000000 = 10 %; tax = net × rate (rounded half up) - compare with Clover's tax report |
| 9 | **Discounts** | % and $ line discounts, an order-level discount | Line discounts applied to their line; order-level discounts spread over the sold lines by their net (compare the day total) |
| 10 | **Refunds** | Full and partial refund | Full refund → order and lines voided; partial refund: compare totals and note |
| 11 | **Stock** | Item at -2 | `/item_stocks` quantity -2 shows as a negative count |
| 12 | **Rate limits** | Run a 90-day backfill | No 429 errors (16/s and 5 at once per token are respected); if 429s appear, note `Retry-After` |
| 13 | **Merchant-token limits** | Ask Clover (P9) | Is a merchant API token allowed in production, and its limits |
| 14 | **History depth** | Backfill a year in 90-day windows | How far back orders come |
| 15 | **More than 100 lines** | One order with 101+ lines | Completed from `/orders/{id}/line_items` |
| 16 | **Stock webhooks** | (needs an installed app) | Deferred: RetailOps pulls on a schedule |
| 17 | **Totals** | Day's net sales in RetailOps vs Clover's dashboard for 3 days | Match to the cent (after #9 / #10 notes) |

## 4. After the checks

- Switch `CLOVER_ENVIRONMENT` to the production region once Clover approves
  the app, and update the redirect URI in the Clover app if the domain
  changes.
- A second Clover store must use **Connect with Clover** (Clover describes a
  merchant API token as meant for one merchant).
- Vendors: Clover has no vendor field. Each Clover store fills
  **Setup › Item vendors** by hand or with **Import CSV** (template on the
  page).

## 5. Not built yet (deferred)

- Clover webhooks (they carry ids only and need an installed app; scheduled
  pulls stay the main path).
- Amount-only refunds (a partial refund that names no line): lines stay as sold; a line Clover marks `refunded` is voided.
- One Clover rate budget shared by several server processes (today: per
  process).
- Clover App Market billing.
