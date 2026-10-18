# Spice Town Dashboard

Internal ops dashboard for Spice Town, a real South Asian grocery + halal meat + prepared-food store
in Vernon, CT, running on Toast POS/Retail. Not a demo — every figure in this repo's history came
from testing against the live Toast account and the real database.

For anyone (human or AI) picking this project back up, `.claude/skills/spicetown/SKILL.md` is the
detailed operating manual — gotchas, architecture rationale, the established pattern for adding a
feature. This README is the higher-level orientation and setup instructions.

## What's built

1. **Database**: SQLite (`backend/spicetown.db`), via SQLAlchemy + Alembic migrations.
2. **Daily sales sync**: pulls the prior day's orders from Toast's Orders API automatically
   (in-process cron), including nested modifier line items (e.g. "extra cheese"). Self-heals a
   missed night by scanning a rolling window for gaps on every run and on every service restart.
3. **Manual inventory upload**: dashboard upload of Toast's daily "retail items" CSV/XLSX export,
   parsed by header name (not column position). An optional **"we received a delivery today"**
   checkbox lets you pick one or more vendors, then walks you through a **Delivery Review** page
   per vendor: brand-new items and items whose net change (count diff plus that day's real sales
   for the item, so a delivery isn't undercounted just because some of it already sold) was more
   than 5 since the previous upload are suggested as candidates, with that day's file cost shown
   for reference, for you to review/edit and confirm into the purchase log - much less typing than
   logging each item by hand, while staying a real reviewed entry rather than a silent auto-log
   (see Reconciliation below for why that distinction matters). Confirming also captures that
   item's real cost from the file, tagged to the vendor you picked - this is what feeds Vendor
   Price Comparison in Inventory Reports (#6). A second, similar optional checkbox - **"Container
   movement happened today"** - covers stock physically moved between an item's two upload rows:
   the priced/sellable "Each" row and its bare "Container" storage-location duplicate (see #6's
   dead stock/margin note, or the container-merge note under #5). Real upload history shows the
   Container row's own count essentially never changes on its own, so unlike a delivery this can't
   be detected from that row's own delta - **Transfer Review** instead reuses the Each row's
   net-of-sales swing (the same signal Delivery Review uses) in whichever direction you pick
   (Container → Store or Store → Container), for you to confirm into a separate transfer log; it
   can't tell a transfer apart from an unlogged delivery on its own, so it stays a human-reviewed
   suggestion, never auto-logged, same as Delivery Review.
4. **Sales CSV/PDF export** by date range, and an **Items Sold** view for one day (today is pulled
   live from Toast since the nightly sync only ever covers yesterday and earlier; past days are
   served from the already-synced data). Counted by the date payment was actually made, not by
   Toast's scheduled pickup/delivery date — see `paid_at` in `app/models.py`.
5. **Ordering**: a demand forecast (simple moving average per item), a reorder trigger (forecast
   over a vendor lead time you supply vs. latest on-hand), and a per-supplier projection with a
   "need to order" number and both avg/day and avg/week velocity — click either to see the actual
   week-by-week sales behind it. On-hand quantity for an item automatically includes its
   **Container-location duplicate** where one exists (same item name, no price/cost/supplier of
   its own — a bare quantity for stock held in a separate storage location, e.g. a 20lb bag on the
   retail shelf plus the rest of the case in back stock); the combined figure shows a small
   "(+N container)" note so the source is never hidden. Lookback window and projection duration are both dropdowns of
   presets (1/2/3 weeks, 1/2/3 months) with a **Custom…** option that opens a native calendar
   date picker instead. Check the items you want, click **Build purchase order**, and a dedicated
   page opens showing on-hand, SKU, name, and projected need for just those items with a quantity
   field to enter what you're actually ordering; **Download PDF** then produces a vendor-ready
   document with the real Spice Town letterhead (logo, address, phone) up top, and a table with
   only a serial number, the vendor's own SKU (blank if Toast doesn't have one on file), item
   name, and quantity — items left blank or at 0 aren't included. **This isn't a real Toast
   PO** — Toast's Purchasing API exists but the current OAuth credentials don't have that scope
   (confirmed via a live 403); `vendors_reference` stays empty until that's granted, so there's no
   system-of-record integration, just a document.
6. **Inventory Reports**: six reports in one tab, each with CSV download - missing barcodes,
   invalid barcodes (GS1 UPC-A/EAN-8/EAN-13/GTIN-14 checksum), a price change log (diffs
   consecutive inventory uploads per item to find real price changes, plus a name/barcode search
   showing one item's full price/cost history), **dead stock / slow-moving** (on-hand items with
   zero 90-day sales or an excessive days-on-hand figure, sorted by dollars tied up), a
   **margin report** (items that actually sell, worst gross margin first - surfaces real
   money-losers), and **vendor price comparison** (flags an item where the vendor you most
   recently bought it from costs more than another vendor already on file for that same item).
   The first four are pure queries over fields Toast already computes and hands back in the daily
   export - no new forecasting or data collection. Vendor price comparison is different: the daily
   file's own `cost` is one blended number per item with no way to tell which of an item's several
   possible suppliers it belongs to, so this instead uses real vendor-attributed costs captured
   through Delivery Review (see #3) or a manually logged purchase with a cost entered - it only has
   something to show once that data exists.
7. **Reconciliation**: purchased vs. sold vs. counted, the shrinkage/spoilage signal
   (`expected_closing = opening_count + purchased - sold`, compared against the actual counted
   closing stock). "Sold" and "counted" are automatic; "purchased" comes from a manual purchase
   log (item search, supplier, qty, cost, date) since Toast's Purchasing & Receiving API isn't
   accessible with the current OAuth credentials - only as complete as what actually gets logged.
   Purchases can also be **bulk-logged from a CSV/XLSX** (columns matched by header name, same
   pattern as the daily inventory upload) so a whole vendor invoice can be entered in one upload
   instead of one row at a time; unmatched items or bad rows are skipped and listed, not guessed.
   The fastest way to log purchases day-to-day is the inventory upload's Delivery Review flow
   (see #3 above) - the form and CSV upload here stay for anything it misses or a past date.
   A **Demo** panel at the top of the tab walks through the actual formula using one real item and
   its real opening count, real sales, and real closing count for the most recent window the
   upload history supports (self-adjusting as more snapshots accumulate) - including a "try it"
   field that recomputes the expected closing and variance live as you type a hypothetical
   purchased quantity, so the mechanic is clear before you've logged anything for real.
8. **Authentication**: every page and API route requires login. Admin-only user management (create
   users, set/reset any password); any user can change their own password with their current one.
   No forgot-password flow anywhere, by design.
9. **React dashboard**: sidebar-navigated multi-page app, served by the backend itself in
   production (one process, one port). Includes an in-app **Help** page explaining every tab in
   plain language.
10. **Ask Inventory Bot** (Phase 5): a natural-language query layer on top of everything above,
    powered by the Claude API - a floating chat bubble in the bottom-right corner, present on
    every page rather than its own sidebar tab, so it's reachable no matter what you're looking
    at; its conversation survives navigating between pages. Strictly **read-only** by deliberate
    design - it wraps every report already listed above (reorder candidates, supplier projection,
    reconciliation, vendor price comparison, items sold, price history, dead stock/margin, barcode
    reports) as tools the model can call, but none of them write to the database, so a
    misunderstood question can at worst return a wrong answer, never corrupt real data. Model
    routing is Haiku-default/Sonnet-escalation: a cheap classifier call first decides whether a
    question is a simple single-lookup (`claude-haiku-4-5`) or needs combining multiple
    reports/real reasoning (escalated to `claude-sonnet-5`), so most questions stay cheap. Requires
    `ANTHROPIC_API_KEY` in `backend/.env` (see One-time setup below) - without it, opening the chat
    shows a clear "not configured" error and nothing else in the app is affected.
11. **Weekly Digest** (Phase 6): an auto-generated summary on the Overview tab, pulling from every
    report already listed above - sales this week vs. last week, top reorder needs, dead
    stock/margin losers, vendor savings, the biggest reconciliation variances, and barcode data
    quality counts. Every number is computed by the same functions those reports already use;
    Claude (one `claude-opus-5` call, no tool use needed since the numbers are gathered first) only
    turns them into a short written summary per section - it's never asked to compute or estimate a
    figure itself. Generated on demand (a button click), not on a schedule, and shown in-app only -
    no email/notification delivery - so it never spends an API call on a week nobody looks at.
    Shares its "is `ANTHROPIC_API_KEY` configured" check with Ask Inventory Bot.

## Stack

- **Backend**: FastAPI (Python), chosen for pandas (header-name CSV/XLSX parsing) and APScheduler's
  in-process cron support.
- **DB**: SQLite via SQLAlchemy + Alembic (`backend/alembic/versions/`). WAL mode + busy_timeout are
  enabled so a GUI tool or `sqlite3` reading the file never blocks the app's writes.
- **Auth**: cookie-based server-side sessions (not JWT) with bcrypt password hashing — see
  `backend/app/auth.py`.
- **Frontend**: React + Vite + react-router-dom (`HashRouter`, deliberately not `BrowserRouter` —
  the production static-file serving has no SPA catch-all route).

## Layout

```
backend/
  app/
    models.py            orders, inventory_snapshots, vendors_reference, purchase_log, location_transfers, job_runs, users, user_sessions
    auth.py               password hashing, sessions, get_current_user / require_admin
    toast_client.py       Toast Orders API auth + fetch
    timeutil.py            shared UTC/restaurant-timezone helpers
    services/
      sales_sync.py        daily cron logic, idempotent upsert, recurses into modifier selections
      inventory_parser.py  CSV/XLSX parser, matches columns by header name
      export.py            CSV/PDF export of the orders table
      items_sold.py        live (today) vs. stored (past) per-item sales for one date
      forecast.py           avg daily demand per item, excludes modifier lines
      reorder.py             reorder-candidate trigger (forecast over lead time vs. on-hand)
      supplier_projection.py per-supplier demand projection, merges in each item's bare Container-location duplicate row
      weekly_sales.py         per-item week-by-week sales, computed on demand for one item at a time
      po_export.py             draft PO PDF from selected Supplier Projection rows (reportlab), branded letterhead
    assets/                 spice-town-logo.jpg - the real logo, used in the PO PDF letterhead
      price_history.py       price-change detection + name/barcode search across snapshots
      barcode_report.py      missing/invalid barcode reports (GS1 checksum validation)
      inventory_intelligence.py  dead stock/slow-moving + margin report, pure queries, no new plumbing
      purchase_log.py           CRUD for manually-logged receiving records
      purchase_log_import.py    bulk CSV/XLSX import for the purchase log, matches columns by header name
      reconciliation.py          purchased vs. sold vs. counted, the shrinkage/spoilage signal
      delivery_review.py         suggests delivery qty/cost by diffing consecutive inventory uploads
      location_transfer.py       suggests Container<->Each transfer qty from the Each row's net-of-sales swing
      location_transfer_log.py   CRUD for manually-confirmed location transfer records
      vendor_cost.py              flags a costlier current vendor vs. a cheaper one on file, per item
      csv_util.py             shared rows-of-dicts -> CSV helper for the small report exports
      ask_bot.py                read-only Claude API tool-use layer over every report above (Ask Inventory Bot)
      anthropic_client.py       shared Claude API client construction, used by ask_bot.py and weekly_digest.py
      weekly_digest.py           auto-generated weekly summary over every report above (one Claude call, no tools)
    routers/               auth, jobs, inventory, sales, ordering, reports, reconciliation, ask, digest, transfers
  alembic/versions/         0001 initial schema -> 0008 location_transfers
  scripts/
    setup_persistent.sh     one-time launchd + Tailscale serve install
    seed_admin.py            one-time initial admin user seed
    dump_db_to_csv.sh        dumps every table to CSV, sensibly ordered
frontend/
  src/
    pages/                  one file per sidebar page
    components/             one file per reusable UI piece
    AuthContext.jsx          login state, used by every page via useAuth()
```

## One-time setup

```
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in TOAST_CLIENT_ID / TOAST_CLIENT_SECRET / TOAST_RESTAURANT_GUID
                        # and, for the Ask Inventory Bot, ANTHROPIC_API_KEY (optional - everything
                        # else works without it, that one page just shows a clear error)
alembic upgrade head
SEED_ADMIN_PASSWORD=... python3 scripts/seed_admin.py   # creates the first admin login (password from env or prompt; no-op if a user already exists)
```

## Running it permanently (recommended for day-to-day use)

`backend/scripts/setup_persistent.sh` builds the frontend into a static bundle the backend serves
directly (one process, one port), installs a launchd **daemon** (system domain, runs at boot
regardless of GUI login state — appropriate for an always-on server Mac; needs `sudo` once to write
to `/Library/LaunchDaemons`) so it auto-starts and auto-restarts if it crashes, and publishes it
privately on your Tailscale network at a stable HTTPS URL (`https://<this-machine>.<your-tailnet>.ts.net/`)
reachable from any of your own devices already on Tailscale — **not** the public internet (deliberately
never Funneled). Run it once from a real Terminal on this Mac:

```
cd backend/scripts
./setup_persistent.sh
```

After that you never need to manually start anything — just open the tailnet URL and log in. Re-run
the script any time you change frontend code, to rebuild and republish; a **backend** (Python) code
change instead needs:

```
sudo launchctl kickstart -k system/com.spicetown.backend
```

To check on or manage the service directly: `sudo launchctl print system/com.spicetown.backend`,
logs are in `backend/logs/`.

## Running it for local development

Two separate dev servers, talking to each other over CORS instead of the single-process production
setup above — use this when actively changing frontend or backend code.

Backend:
```
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Frontend (separate dev server with hot reload):
```
cd frontend
npm install
cp .env.example .env   # only needed if the backend isn't on localhost:8000
npm run dev
```

## Python 3.12 upgrade (cutover plan)

The backend ran on the system Python 3.9.6, which is past end of life (google-auth now warns about
it). It has been checked on Homebrew's **Python 3.12** (`/opt/homebrew/bin/python3.12`, 3.12.14):

- `requirements.txt` needs **no changes**. The same pins install cleanly on 3.9 and 3.12 (`pip
  check` clean), so prod can pull new code before, during or after its switch.
- All backend tests pass on 3.12 (152), `import app.main` works, and `alembic` sees the same head.
- A second server on 3.12, pointed at a copy of the dev DB with all jobs off, returned the same JSON
  as the live 3.9 dev server for the existing pages (reorder, supplier projection, open orders,
  invoices, reports, cart) and for the Team endpoints. The only differences came from data another
  test run changed between the copy and the comparison.
- The only warnings are inside third-party libraries (reportlab, starlette) and only matter for
  Python 3.14. The project's own time code already uses timezone-aware `datetime.now(timezone.utc)`,
  and APScheduler 3.10 fires correctly across the November DST change.

A venv can't be renamed (its scripts hard-code their own path), so the switch points the process at
a **new** venv folder and keeps the old one untouched for rollback.

**Dev** (the `.venv312` folder already exists in `website/backend`):

1. `./down.sh`
2. In `up.sh`, change `"$BACKEND_DIR/.venv/bin/uvicorn"` to `"$BACKEND_DIR/.venv312/bin/uvicorn"`.
3. `./up.sh` and check `http://127.0.0.1:8001/api/app/config` and the backend log.
4. Rollback: change the path back to `.venv`, then `./down.sh && ./up.sh`.

**Prod** (run in a normal Terminal, because `sudo` needs a password):

```
cd /Users/sundar/spicetown-backend/backend
/opt/homebrew/bin/python3.12 -m venv .venv312
.venv312/bin/pip install --upgrade pip
.venv312/bin/pip install -r requirements.txt
.venv312/bin/pip check
.venv312/bin/python -c "import app.main" && echo import-ok
.venv312/bin/alembic current            # must show the same head as: .venv/bin/alembic current
sudo cp /Library/LaunchDaemons/com.spicetown.backend.plist ~/com.spicetown.backend.plist.py39-backup
sudo sed -i '' 's#backend/.venv/bin/uvicorn#backend/.venv312/bin/uvicorn#' /Library/LaunchDaemons/com.spicetown.backend.plist
grep -n uvicorn /Library/LaunchDaemons/com.spicetown.backend.plist   # should show .venv312
sudo launchctl bootout system/com.spicetown.backend
sudo launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.backend.plist
curl -s http://127.0.0.1:8000/api/app/config && tail -20 logs/backend.error.log
```

`bootout` then `bootstrap` (rather than `kickstart`) is needed because launchd only rereads
`ProgramArguments` when the job is loaded again.

**Prod rollback** (the old `.venv` is never modified):

```
sudo cp ~/com.spicetown.backend.plist.py39-backup /Library/LaunchDaemons/com.spicetown.backend.plist
sudo launchctl bootout system/com.spicetown.backend
sudo launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.backend.plist
```

When 3.12 has run cleanly for a week or two, the old `.venv` folders can be deleted. After that,
new setups use `python3.12 -m venv .venv` in "One-time setup" below.

## Browser smoke test

A Playwright check that signs in and opens every dashboard page (read-only by default):
`cd e2e && npm install && npm run install-browser`, then
`E2E_USER=... E2E_PASS=... npm run smoke` (`E2E_BASE_URL` defaults to the dev backend, http://127.0.0.1:8001). Add `E2E_WRITE=1` (dev only) to also create a task for yourself on Team > Tasks and mark it done - it leaves that task behind.

## Dumping the database

```
./backend/scripts/dump_db_to_csv.sh
```
Every table to its own CSV under `~/spicetown-db-dumps/<timestamp>/`, sorted sensibly per table
(by date, not raw insertion order — insertion order and date order diverge once a table's been
backfilled). `users.csv` includes the bcrypt password hash column — not plaintext, but still worth
deleting dumps after use rather than sharing them.

## Known limitations / not built

- **Real vendor cost/lead-time and a real system-of-record PO** — blocked on Toast Purchasing API
  scope (see "What's built" above); the draft PDF export is a document for a human, not an
  integration.
- **`inventory_snapshots.supplier_item_id`** (vendor SKU) is scaffolded but its real Toast export
  column name was never confirmed — check `job_runs.detail` after the next inventory upload for the
  actual ignored header text if it's showing blank. See the skill's "Session:..." section.
- **No forecasting beyond a simple moving average** — no seasonality/trend modeling yet.
- **No self-service password reset** — by design; an admin resets it instead.
- **Mobile layout hasn't been visually verified in an actual browser** — built and served correctly,
  but not checked on a real phone/responsive mode yet.
