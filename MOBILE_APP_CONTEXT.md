# Spice Town — System Context for Mobile App Design

This is a handoff document, not a spec. It describes what exists today (two
working apps, real production data, a partially-built mobile wrapper) so a
designer/AI with no prior context can propose a proper mobile app
implementation. Nothing here is aspirational except the sections explicitly
marked "not built yet."

## 1. The business

Spice Town is a real, operating South Asian grocery + halal meat + prepared
food store in Vernon, CT. It runs on **Toast POS/Retail** for checkout and
inventory. Everything below is real production software running against
real store data — not a demo or prototype.

## 2. The two applications

There are **two separate codebases, two separate git repos, two separate
Python venvs**, both currently running on the same physical machine (a Mac
Mini, always-on, on the store's LAN):

| | **spicetown-backend** | **spicetown-labels** |
|---|---|---|
| What it is | Internal ops dashboard: sales, inventory, ordering, reconciliation, invoice logging | Barcode-scan price-label printer for the shelf floor |
| Stack | FastAPI (Python) + React/Vite frontend, SQLite | Flask (Python) + server-rendered HTML/vanilla JS, SQLite |
| Users | Store owner + a handful of staff, admin-gated dashboard | Any staff member with a barcode scanner/phone at a printer station |
| Repo | `github.com/spicetownbackend/spicetown-backend` (private) | `github.com/spicetownbackend/spicetown-labels` (private) |
| This doc's focus | **Primary target for the mobile app** | Secondary — see §7, there's a planned integration point |

They currently only touch each other in two places: (a) the dashboard has an
"Open label scanner ↗" link out to the labels app's own URL, and (b) both
independently sync product/price data from the same Toast account. A future
mobile phase is expected to actually merge label-printing/barcode-scanning
into the main app (see §8).

## 3. spicetown-backend — architecture

- **Backend**: FastAPI, SQLAlchemy + Alembic migrations, SQLite (WAL mode).
  Runs on **Python 3.9** on the deployment machine — no PEP 604 `X | None`
  syntax anywhere, `typing.Optional[X]` throughout.
- **Frontend**: React + Vite + `react-router-dom`, using `HashRouter` (not
  `BrowserRouter` — the backend's static file serving has no SPA catch-all
  route, see §6).
- **Deployment**: the frontend is built to static files (`npm run build` →
  `frontend/dist`) and served **by the FastAPI process itself** — one
  process, one port. Runs as a macOS **LaunchDaemon** (not a LaunchAgent —
  needs to run headless, no GUI login required).
- **Scheduling**: in-process APScheduler (not OS cron) for the nightly Toast
  sales sync and a few other periodic jobs.
- **One paid external API dependency**: the Claude API (Anthropic), used
  only for a natural-language "Ask Inventory Bot" and a weekly digest
  feature — nothing else in the app depends on an LLM.

## 4. Authentication (important for mobile — largely already solved)

- **Web**: cookie-based sessions (not JWT). An opaque token in an
  HttpOnly+Secure+SameSite=Lax cookie, looked up against a `user_sessions`
  table (so server-side revocation is instant — delete the row).
- **Mobile already has its own path, already built and verified**:
  `POST /api/auth/mobile-login` does the same credential check as the web
  login but returns the raw session token in the **JSON body** instead of a
  cookie (a deliberate separate endpoint — never added to the web login
  response, to avoid exposing a JS-readable token to web XSS). The mobile
  client then sends that token as `Authorization: Bearer <token>` on every
  request. `logout()` accepts the token either way so a mobile client can
  actually revoke its own session server-side.
- Roles: **admin** (full access to everything, bypasses all feature gates)
  and **non-admin** (per-user, admin-grantable access to individual
  dashboard tabs — see §5's `FEATURES` list). No self-service signup, no
  forgot-password flow anywhere by design — only an admin creates a user or
  resets a password.
- **No shared secrets or SSO** — it's just this one app's own user table.

## 5. What the dashboard actually does (the pages / feature surface)

Every page below is gated by an admin-grantable "feature key" (a non-admin
user only sees tabs they've been explicitly granted). This is the canonical
list, from `backend/app/features.py`:

| Feature key | Page | What it does |
|---|---|---|
| `overview` | Overview | Home page: job status widget, inventory upload, weekly digest, sales download |
| `open_orders` | Open Orders | Live pull of currently-open (not yet closed) Toast orders, single-date + an "all-time scan" (cached, background-refreshed) to catch orders stuck open for weeks |
| `items_sold` | Items Sold | Revenue/items sold over a date range, live for today, stored for past dates |
| `reorder_candidates` | Reorder Candidates | Cross-supplier "what needs reordering" based on demand forecast vs. on-hand |
| `supplier_projection` | Supplier Projection | Per-vendor version of the above; drives the purchase-order cart |
| `vendor_calendar` | Vendor Order Calendar | Derives order-due/delivery dates per vendor from a one-time-entered weekday+lead-time schedule, flags "need to order" automatically |
| `purchase_order_cart` | Purchase Order Cart | Persistent, shared, multi-supplier cart; PDF export of a purchase order (not a real Toast PO — Toast's Purchasing API is not accessible with current credentials, see §6) |
| `delivery_review` | Delivery Review | Confirm what arrived from a vendor by diffing inventory snapshots; feeds a manual purchase log |
| `transfer_review` | Transfer Review | Same idea for inter-location stock transfers (a store-specific "Container vs. shelf" split Toast doesn't track natively) |
| `inventory_reports` | Inventory Reports | Missing/invalid barcodes, price-change log, dead stock, margin report, vendor price comparison, sales CSV/PDF export |
| `reconciliation` | Reconciliation | Purchased-vs-sold-vs-counted shrinkage signal; also flags invoices with no matching logged purchase |
| `invoice_submission` | Invoice Submission | Replaces a Google Form workflow: upload an invoice/return/other doc, auto-sends to a WhatsApp group (Whapi) and emails Toast's bill-import inbox; admin-approve delete-to-trash workflow |
| `ask_bot` | Ask Inventory Bot | Claude-powered natural-language Q&A over all the read-only reports above (floating chat bubble, not a page) |
| `help` | Help | Plain-language explanation of every tab, for the store owner |

Not gatable (always available to any logged-in user): Change Password. Admin
-only, not part of the grantable list: the Users management page.

## 6. Toast POS integration — what's real and what's blocked

- **OAuth2 client-credentials flow** against Toast's standard API
  (`ws-api.toasttab.com`). Credentials are one client ID/secret for the
  whole restaurant, currently granted scopes: `cashmgmt:read config:read
  delivery_info.address:read digital_schedule:read guest.pi:read
  kitchen:read labor.employees:read labor:read menus:read orders:read
  packaging:read stock:read restaurants:read`.
- **Working and used**: Orders API (nightly sales sync + live "today" pulls),
  Menus API (`/menus/v2/menus` — item name/price/barcode/category/supplier,
  the supplier field is store-repurposed via the `plu` field), Stock API
  (`/stock/v1/inventory` — confirmed accurate live on-hand quantities, not
  currently used by the dashboard but validated), Labor API (employee names
  for "who opened this order").
- **Confirmed blocked with current credentials**: the entire Purchasing &
  Receiving API (`/purchasing/v1/*`) — 403 on vendors, 404 on every guessed
  purchase-order/invoice/receiving path. This is why there's no real
  automated PO placement or per-vendor cost data anywhere in the app — it's
  all manual-entry workarounds (purchase log, delivery review) until/unless
  that scope is granted.
- **No inventory cost/margin data from any live API** — `cost`,
  `gross_margin` etc. only ever came from the old manual CSV inventory
  upload. This may matter for a mobile design that wants those numbers live.
- **The actual product catalog (name/price/barcode/category/supplier) is
  fully available live** via the Menus API — this is what `spicetown-labels`
  now uses instead of a manual file (see §7).

## 7. spicetown-labels (the sibling app — planned integration point)

A separate Flask app, originally deployed on Render's free tier, migrated
onto the same Mac Mini this year. Staff scan a barcode (camera-based web
scanner, QuaggaJS) or search by name, and print a price label on a physical
**Brother QL-810W** label printer (driven directly via CUPS on this Mac).

Recently upgraded (this month) from reading a manually-uploaded CSV to a
**live Toast sync**:
- Pulls the catalog from the same Toast Menus API, refreshed every 30
  minutes (was nightly-only before).
- **Auto-prints a fresh label for every price change** it detects and logs
  it to a print history table — no manual "review and print" step needed
  anymore for the normal case (a manual review UI still exists as a
  fallback for failures).
- SQLite is the authoritative local cache; a `DataProvider` abstraction
  (`file` vs `toast`) means the whole pricing source is a one-line config
  swap.

**Why this matters for the mobile app**: the store's own stated roadmap
(from prior planning conversations) is to eventually merge label-
printing/barcode-scanning **into** the main dashboard mobile app as a native
feature — using the phone's camera for barcode scan (rather than a fixed
scanner station), rather than keeping it a separate web app staff have to
navigate to. That merge has **not been started** — today the dashboard's
mobile app just links out to the labels app's own URL, same as the web
version does. A mobile design should treat "bring barcode scan + print
trigger into the main app" as a real, explicitly-requested future phase, not
scope-creep to invent.

## 8. Mobile app — what's already built (Capacitor wrapper, Phase 1)

A decision was already made and implemented: **wrap the existing React web
app with Capacitor** rather than write a separate React Native or native
app. Rationale: reuses ~95% of already-built, already-verified UI; ships as
a real installable binary (bundled locally, not a live-loaded webview); gets
native plugin access (filesystem, share sheet, etc.) via JS.

**What exists today** (all under `frontend/`):

- `capacitor.config.json` — `appId: com.spicetown.app`, `webDir:
  dist-mobile`.
- `frontend/ios/` and `frontend/android/` — real generated platform
  projects, committed to git.
- **Two separate frontend builds, never sharing an output directory**:
  - `npm run build` → `dist/` (web, relative API base — same-origin, served
    directly by the FastAPI backend)
  - `npm run build:mobile` → `vite build --mode mobile && npx cap sync` →
    `dist-mobile/` (mobile, **absolute** API base baked in from
    `.env.mobile`'s `VITE_API_BASE`)
- `frontend/.env.mobile` — the **single value to change** when the domain
  changes (see §9): `VITE_API_BASE=https://ops.spicetown.shop` currently.
  Not gitignored — deliberately a visible one-line diff in git history
  whenever the domain changes.
- `frontend/src/mobileAuth.js` — token storage via `@capacitor/preferences`,
  restored once at app launch before the first `/me` call.
- `frontend/src/downloadOrShare.js` — every file-download call site (PDF/CSV
  exports) branches: web keeps a normal `<a download>` click; native writes
  the blob to the app's cache dir via `@capacitor/filesystem` and hands it
  to `@capacitor/share`'s OS share sheet, since a Capacitor WebView doesn't
  reliably trigger browser-style downloads.
- Backend CORS already allows `capacitor://localhost` (iOS) and
  `http://localhost` (Android's Capacitor default).
- Safe-area handling (`viewport-fit=cover`, `env(safe-area-inset-*)` on
  every fixed-position UI element — sidebar toggle, cart button, chat
  widget, mobile drawer) — needed because Capacitor renders edge-to-edge
  behind the notch/Dynamic Island/home indicator, unlike Safari.
- Tap targets and input font sizes fixed for mobile (16px+ inputs to avoid
  iOS auto-zoom, 44px+ minimum tap targets).

**Verified**: the app actually builds and installs on both a real iOS
Simulator and a real Android debug build (`.apk`, self-signed debug key,
sideloadable without a Play Store account — no Apple-equivalent shortcut
exists for iOS, that needs a paid Apple Developer account for anything
beyond the Simulator).

**NOT verified / NOT built yet** — this is the honest gap list:
- **No actual login flow has been driven through the simulator/device UI**
  (automation permissions weren't available in that environment) — the auth
  mechanism was verified in pieces (token issuance, CORS, bearer-header
  auth) but never end-to-end through real taps.
- **No stacked-card / mobile-optimized layout for the dense data tables**
  (Reorder Candidates, Supplier Projection, Inventory Reports,
  Reconciliation). They currently rely on horizontal scroll inside a
  `.table-wrap` — functional, not designed for a phone.
- **No push notifications.**
- **No offline caching / TanStack Query layer** — the app assumes a live
  network connection to the backend at all times, same as the web version.
- **No barcode scanning / label printing inside the mobile app** (see §7).
- **Android has no SDK/emulator set up on the deployment machine currently**
  — only the project scaffold and one real debug APK build have been done;
  ongoing Android testing would need that re-established.
- **A real Apple Developer Program membership does not exist yet** — the iOS
  side has only ever been tested via Simulator + local device pairing, never
  TestFlight or the App Store.

## 9. Infrastructure, networking, and the domain migration (both apps done)

- **Machine**: a Mac Mini, always-on, on the store's LAN, running both apps
  as **launchd LaunchDaemons** (system-domain, run as the `sundar` user, not
  root — `RunAtLoad` + `KeepAlive` for auto-restart on crash).
- **The dashboard is now on a real domain**: `https://ops.spicetown.shop`,
  a subdomain of `spicetown.shop` (bought and DNS-hosted on Cloudflare),
  exposed via **Cloudflare Tunnel** (`cloudflared`), not Tailscale Funnel
  anymore. A named tunnel (`spicetown-ops`) runs as its own LaunchDaemon
  (`com.spicetown.cloudflared`, `backend/scripts/com.spicetown.cloudflared.plist`)
  with an ingress rule in `~/.cloudflared/config.yml` routing
  `ops.spicetown.shop` → `http://localhost:8000`. Cloudflare's edge
  terminates TLS and reaches the tunnel over an outbound-only connection —
  **no router port-forwarding, no static public IP, and no manual
  certificate management needed**, which was the main open question this
  section used to flag. The old Tailscale Funnel entry for the dashboard's
  root (443) has been turned off (`tailscale funnel --https=443 off`).
- **The labels app has moved too**: `https://labels.spicetown.shop` →
  `http://localhost:8080`, a second hostname under the *same* Cloudflare
  Tunnel (`spicetown-ops`) — just one more `ingress` entry in
  `~/.cloudflared/config.yml` plus one more `cloudflared tunnel route dns`
  call, no new tunnel or daemon. It's now also **gated on a valid
  spicetown-backend login** — the labels app has no login of its own by
  original design, so it borrows the dashboard's: the dashboard's session
  cookie is widened to `Domain=.spicetown.shop`
  (`SESSION_COOKIE_DOMAIN` in backend `.env`) so the same cookie reaches
  both subdomains, and spicetown-labels validates it directly against
  spicetown-backend's `user_sessions` table via a read-only SQLite
  connection (`app/services/dashboard_auth.py` in that repo). No cookie/an
  expired one → redirect to the dashboard login for a page view, 401 JSON
  for an API call; `/healthz` and `/api/bridge/*` stay exempt. Controlled
  by `STL_REQUIRE_DASHBOARD_LOGIN` (labels app `.env`), off by default so
  local dev/tests never need a real dashboard DB on disk.
- `PUBLIC_BASE_URL` (backend `.env`, builds the WhatsApp invoice-link URL),
  `VITE_API_BASE` (`frontend/.env.mobile`), and `LABELS_APP_URL`
  (`frontend/src/components/Sidebar.jsx`) are all updated to their new
  `*.spicetown.shop` addresses.
- Render (spicetown-labels' old host) is still live in parallel, kept
  running until the local Mac deployment proves itself over more real use —
  not yet decommissioned.

## 10. Data model summary (spicetown-backend)

SQLite tables (via SQLAlchemy models in `backend/app/models.py`):
`Order` (line-item sales, synced from Toast), `InventorySnapshot`
(per-upload inventory state), `VendorReference` (currently unused, blocked
on Purchasing API access), `User` / `UserSession`, `JobRun` (background job
audit trail — nightly sync, digest generation, etc.), `PurchaseLogEntry`
(manual receiving log), `LocationTransferLogEntry` (container↔shelf transfer
log), `CartItem` (purchase-order cart), `OpenOrderCache` (background-
refreshed snapshot of currently-open Toast orders), `VendorSchedule`
(one-time-entered vendor delivery cadence), `InvoiceSubmission` (uploaded
invoices/returns + their WhatsApp/email send status + a soft-delete/trash
workflow).

## 11. Constraints worth knowing before designing a mobile backend change

- Backend runs on **Python 3.9** — no `X | None` type-hint syntax.
- SQLite silently drops timezone info on read; the app has its own
  `UTCDateTime` column type to work around this. Every timestamp the
  frontend shows is explicitly formatted in `America/New_York`
  regardless of device timezone (the store's own timezone, not the
  viewer's) — a mobile app should keep that convention, not switch to
  device-local time.
- No test suite runs against a separate test database anywhere in this
  project — every feature has historically been verified directly against
  the real production DB/API (with careful, scoped cleanup). This is a
  deliberate project convention, not an oversight.
- The dashboard has **no forgot-password / self-service signup flow**, by
  design. A mobile app's auth screens should match that constraint (login
  only; password reset is admin-mediated).

## 12. What this document is for

Use this to design a **mobile app implementation plan** for Spice Town's
internal ops dashboard, starting from a working Capacitor-wrapped shell with
solved auth, CORS, file-download, and safe-area problems — not from zero.
Open, real design questions worth addressing:

- Mobile-appropriate layouts for the dense report tables (§8).
- Whether/how to bring barcode scanning + label printing into the app
  natively (§7) instead of linking out to a separate tool.
- Push notifications — for what events (a price change flagged for review,
  a vendor order coming due, an invoice submission failure)?
- Offline behavior — is any of this meaningfully useful offline, or should
  the app just clearly degrade to "no connection" (current behavior)?
- Whether a real Apple Developer Program enrollment is worth it yet, versus
  staying Simulator/Android-debug-APK only for internal staff use.
