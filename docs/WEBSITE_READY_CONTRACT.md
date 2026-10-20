# Website ready - contract (server + website)

Owner's request: "complete all the remaining steps as per our plan and make
the website side ready so that we'll move on to the app later on".
Scope: **server + website only**. The Flutter app repo is not changed; app
follow-ups are listed in section 7 and go into the owner handoff.

Hard deadline **05:00 EDT 2026-10-20**. Time boxes (EDT): Plan 23:10 ·
Build tracks committed **01:40** (stop starting new work at 01:25) ·
Integrate 02:40 · Verify 03:45 · Ship 04:45. A smaller working result beats
a bigger broken one: anything unfinished goes under "Deferred" in the
track's As-built section with a one-line reason.

Sources: `docs/DESIGN_SYSTEM.md`, `docs/PLATFORM_CONTRACT.md` (section 20,
every "Deferred" list, the "Final pass" sections and "Left as is"),
multi-store plan M3 (MT14-MT19) and M5 (MT28-MT32), the owner handoff
(section 6), `docs/PILOT_RUNBOOK.md`.

---

## 0. Ground rules (every track)

- Python 3.12 (`backend/.venv`); pins for any new package go in
  `backend/requirements.txt` (append; C adds `boto3`, `moto`).
- Frontend: a real local `node_modules` (`npm ci` in each worktree; never a
  symlink to another project). No new npm package unless a track's item
  below says so.
- **Additive migrations only.** The platform chain is fixed now (2.3).
- Everything new is **off by default** and the pilot behaves exactly as
  today until the owner switches it on (pilot = `~/kloudc-retailops`,
  Postgres 5434, port 8900, store in pilot mode; touched only in Ship).
- No outside calls: Toast, Clover, Stripe, R2/S3, Whapi, SMTP, FCM and the AI
  provider are fakes / recorded fixtures / moto. Owner-only accounts are
  settings + exact owner steps, never faked as done.
- Design: `DESIGN_SYSTEM.md` tokens, area colours, compact frozen list
  header (search + filter/settings/ⓘ), tile tabs remembered, at most 2
  clicks, AA contrast, 375-1440 px, light + dark, no sideways page scroll.
  Every new page and term gets ⓘ info, a `content/help/pages.json` entry and
  glossary terms (`content/help/glossary.json`). No origin-store name anywhere (name guard).
- Tests: during Build run only your own files plus the guards
  (`tests/test_no_origin_names.py`, `tests/tenancy/test_cache_lint.py`,
  `tests/test_website_ready_interfaces.py`). Full suites, Playwright and
  builds only through the local heavy-job queue script (outside the repo).
- Scratch Postgres: dev cluster `127.0.0.1:5433` user `storeops`, database
  prefixes **`so_wra_` (A), `so_wrb_` (B), `so_wrc_` (C), `so_wrd_` (D)**,
  dropped when done. Preview ports: A 8881, B 8882, C 8883, D 8884.
- Commits: small and logical, each message ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
  Each track appends **"As built - Website ready: <track>"** to
  `docs/PLATFORM_CONTRACT.md` (tests with numbers, decisions, Deferred).
- Branches / worktrees: `web/a-console`, `web/b-data`, `web/c-scale`,
  `web/d-clover`, all from the commit that adds this file.

---

## 1. Every remaining item, with acceptance criteria

Priority: **P1** must ship tonight · **P2** should · **P3** only if time.

### Track A - design + console + performance

| ID | Item | Acceptance | P |
|---|---|---|---|
| A1 | **Store date everywhere** (contract "Deferred": server vs store date). The 16 `dt.date.today()` sites - `routers/ordering.py` (69, 97, 168, 299), `routers/jobs.py:41`, `services/stock_issues.py` (192, 406), `similar_products.py:932`, `supplier_projection.py` (266, 307, 442), `po_export.py:120`, `reorder.py:458`, `insights.py:167`, `weekly_digest.py:62` - use the store's `restaurant_today()` (store time zone of the current store). Website: every "today" computed in the browser (`new Date()` for a date) uses the store's zone from `/api/auth/me` through one helper `storeToday()` in `format.js`. | New tests with a frozen clock at 01:30 UTC for an America/New_York store return the store's date on every route above; the ordering tests pass with `TZ=UTC` **and** `TZ=America/New_York`; a guard test fails on any new `date.today()` in `backend/app`. node test for `storeToday()` across a UTC midnight. | P1 |
| A2 | **Master console: Masters tab.** List masters (username, two-step on/off, last sign-in), add a master (temporary password, must change at first sign-in), remove a master (never yourself, never the last), reset another master's two-step (audited). Also close "a master with TOTP can re-enrol without the current code" (needs the current code). | API tests: add/remove/reset, last-master and self rules, audit rows, non-master 403; Playwright/screen at 1280 + 375. | P1 |
| A3 | **Platform settings page** (console tab "Platform settings"): the I5 editable keys (sign-ups on/off, terms URL, support email, default plan, off-boarding grace days, export link hours) saved in the `platform_settings` table (p0003), validated, audited with old -> new. `signup.py` reads `get_platform_setting(SIGNUP_ENABLED)` so the **sign-up toggle** works without a restart. Security switches stay environment-only. The page also renders `<SystemStatusPanel />` (C). | Tests: precedence (console > env > default), bad values refused, audit, sign-up closed/open live; page screenshots light + dark. | P1 |
| A4 | **Store detail** (side drawer from a Stores row, spec 3.2) with tile tabs **Overview** (health: status, provisioning, DB revision, last run + result of each scheduled job, last activity, POS + integration status), **Usage & plan** (`<StoreUsagePanel storeCode>`, B), **Settings** (existing), **Data** (`<StoreDataPanel storeCode status onChanged>`, B), **Audit** (filtered to the store). | `GET /api/platform/stores/{code}/health` test; drawer usable at 375 (full-screen sheet); every tab reachable in 1 click. | P1 |
| A5 | **Store settings pages** (contract "Deferred"): Setup > Store settings for `GET/PATCH /api/settings/store`, and the own-server request (`/api/settings/hosting`, `/hosting/request`) shown there when dedicated hosting is on. | Owner/admin can edit; staff cannot see it; tests for the page model. | P2 |
| A6 | **Wide tables become stacked cards under 768 px** (spec 2.7, "Left as is" now done): one shared pattern (`ui/` component or `kc-table--stack` with `data-label`) applied to every table with more than 4 columns (Placed Orders, Invoices, Teams, Users, Reconciliation, Items Sold, Inventory Reports, console tables, and B/D's new tables). | `e2e/screens/audit.mjs` at 375: no element wider than its card, no sideways scroll; node test of the pattern. | P1 |
| A7 | **`{pos}` wording for CSV-only stores**: help/glossary text renders "your inventory file" / "your POS" instead of "CSV upload" ("CSV upload sales" never appears). | Test renders every help + glossary entry for a CSV-only store: no "CSV upload", no POS brand name. | P1 |
| A8 | **POS-specific notes only on that POS**: help entries/blocks get an optional `"pos": ["toast"]` field (`content/help/schema.json`); the server and website drop them for other POS (e.g. "Toast Purchasing API" only on Toast stores). Master and signed-out readers see them labelled with the POS. | Test: Clover and CSV-only stores see no "Toast Purchasing API"; a Toast store does. | P1 |
| A9 | **One name per thing** (section 4): apply the names on the website (buttons, titles, help, glossary "Shown on"), list them for the app in the handoff. | Grep test for retired names in `frontend/src` and `content/help`. | P1 |
| A10 | **Route-level code splitting**: `React.lazy` + `Suspense` (skeleton) per page, console and Help Center in their own chunks, React in a vendor chunk; routes for **`/settings/data`** (`pages/StoreDataPage.jsx`, B) and **`/setup/item-vendors`** (`pages/ItemVendorsPage.jsx`, D) plus their nav/search entries (Setup area). | `vite build`: no chunk > 500 kB and no Vite size warning; a node test reads the build manifest; first page loads only its own chunk. | P1 |
| A11 | **Accessibility + performance pass** (Lighthouse-style): landmarks, labels, focus order/visible, 44 px targets at 375, alt text, no layout shift from headers, `prefers-reduced-motion`; run Lighthouse via Playwright's Chromium if it is installed locally, else document the manual checklist results. | `audit.mjs` 0 findings at 1440/1280/375 light + dark including every new page; scores or checklist recorded in As built. | P2 |
| A12 | A suspended store's in-session 403 shows the master's maintenance message and returns to sign-in (verifier "Deferred"). | Test + screen. | P3 |

### Track B - data ownership + plans

| ID | Item | Acceptance | P |
|---|---|---|---|
| B1 | **Usage metering** (I3): buffered counters flushed every 60 s to `store_usage_daily (store_code, day, metric, value)` (p0002); nightly gauges via `register_periodic` (users, items, storage bytes from `get_storage().bytes_used(code=)`, database bytes, last activity); `touch_activity()` from the signed-in request path (`app/auth.py`, one call); `GET /api/platform/stores/{code}/usage` (master) and `GET /api/settings/usage` (owner/admin). UI: `StoreUsagePanel` (console) and the usage part of `StoreDataPage`. Single mode: an empty state ("measured on the shared service"). | Tests: record never raises / never blocks; flush adds up per day; gauges; master vs store auth; other store's numbers never visible. | P1 |
| B2 | **Plans + soft limits** (I4): plan per store (p0002, default `custom` for every existing store and `DEFAULT_PLAN` for new ones), limits per plan in code (`trial`, `starter`, `pro`: users, items, storage), `limit_states()`; master picks the plan in `StoreUsagePanel` (audited); store admins see near/over notices on Data & usage; console Stores list flags "over". Never blocks work. | Tests: states ok/near/over at the edges; plan change audited; no limit ever refuses a request. | P1 |
| B3 | **Billing provider interface**: `app/platform/billing/` (`BillingProvider` protocol, `NullBilling` default, `StripeBilling` stub) behind `BILLING_PROVIDER=none`; Stripe needs `STRIPE_SECRET_KEY` + webhook secret and is never called in tests (fake transport). Owner steps in As built. | Tests: none = no-op; stripe without keys refuses at start-up check; payload building with a fake transport. | P2 |
| B4 | **Store data export ZIP**: job `store_export` (I2); ZIP = one CSV per store-database table (streamed, server-side cursor; secrets excluded: password hashes, sessions, tokens, push tokens, saved credentials), `store.json` (profile, settings without secrets, schema revision), `manifest.json` (tables, row counts, sha256 per file) and `files/` (every storage object of the store); written to storage key `exports/<id>.zip`; platform table `store_exports` (status, size, sha256, requested_by, expires_at, error); download through `presigned_url()` or a signed, expiring server link (`EXPORT_LINK_HOURS`); owner self-serve ("Export all data" on Setup > Data & usage) and master (`StoreDataPanel`); one running export per store; audited (requested, ready, downloaded). | Tests on a scratch PG store: ZIP contents and exclusions, link expiry, other store / staff refused, audit rows, a failed job shows Retry. | P1 |
| B5 | **Off-boarding**: Archive (exists) -> automatic final export -> grace period (`OFFBOARD_GRACE_DAYS`) -> purge (drop the store database, `delete_prefix` its storage, delete its credentials, keep a tombstone row) - every step audited; cancel during the grace period; purge needs a master, the typed Store ID and two-step when enabled; a store in pilot mode is never purged automatically; daily purge job via `register_periodic(per_store=False)`. UI in `StoreDataPanel` (timeline + actions). | Tests with scratch databases: full cycle, cancel, guard rails, idempotent purge. | P1 |

### Track C - scale

| ID | Item | Acceptance | P |
|---|---|---|---|
| C1 | **Object storage** (I1): `S3Storage` (boto3, S3-compatible, Cloudflare R2 endpoint), keys under `stores/<CODE>/`, presigned GET with download filename; `STORAGE_BACKEND=s3` refused at start-up unless endpoint, bucket and keys are all set; migration tool `python -m app.storage migrate --to s3 [--store CODE] [--dry-run] [--verify]` (copy, verify size/checksum, idempotent, resumable). | moto tests (in process, no network) for every I1 method; migrate dry-run/verify/re-run; local backend unchanged on disk. | P1 |
| C2 | **Call sites onto I1** where low risk (invoices, flyers, team media, branding assets), one commit each, local layout identical. | Existing tests for those areas pass unchanged; one s3 (moto) round trip per area. | P2 |
| C3 | **Postgres job queue** (I2): `jobs` + `job_workers` tables (p0004) in the platform database; claim with `SELECT ... FOR UPDATE SKIP LOCKED`; retries with backoff, dead after `max_attempts`; dedupe key; heartbeat + stuck-job reaper; worker processes `python -m app.jobqueue worker [--concurrency N] [--kinds ...]` with graceful SIGTERM; `JOB_QUEUE_MODE=queue` only in multi mode with a platform DB (single mode always inline); the leader's scheduler enqueues the existing per-store scheduled jobs instead of running them inline when queue mode is on, and runs `periodic_jobs()`; masters: failed jobs + Retry (`/api/platform/system/...`) in `SystemStatusPanel`. | Tests on scratch PG: two workers never take the same job; retry/dead/dedupe/reaper; inline mode unchanged; a scheduled job lands in the queue in queue mode. | P1 |
| C4 | **Cross-process live updates**: `team/changes.py` publishes `NOTIFY` (`LIVE_UPDATES_MODE=postgres`) and a listener thread per process wakes local long-polls; local mode unchanged. Also covers the integration "Deferred": saving keys in a web process reaches the leader's scheduler (notify -> reconcile). | Test with two listeners on scratch PG: a commit in one wakes the other within 1 s; local mode tests unchanged. | P1 |
| C5 | **PgBouncer-compatible settings**: transaction-pooling mode (`DB_POOL_MODE=pgbouncer`: no prepared statements, NullPool or small pools), direct URLs for advisory-lock leader election and LISTEN (`*_DIRECT_URL`); documented. | Unit tests of engine options; docs. | P2 |
| C6 | **Multi-process deploy**: `docs/DEPLOY_MULTI_PROCESS.md` (web + leader + workers, rollback); pilot-mac worker LaunchDaemon template, **not installed by default**; Docker compose `worker` service; readiness shows queue depth and worker heartbeats. | Docs reviewed; `install.sh` default path unchanged (test/dry-run); compose file validates. | P1 |
| C7 | **POS call metering**: `pos/http.py` records `POS_API_CALLS` per request (I3). | Test with a fake transport. | P1 |

### Track D - M3 Clover connector

| ID | Item | Acceptance | P |
|---|---|---|---|
| D1 | **Clover client** (`pos/adapters/clover/client.py`): region hosts (`us`/Canada `api.clover.com`, `eu` `api.eu.clover.com`, `la` `api.la.clover.com`, `sandbox` `apisandbox.dev.clover.com`), bearer auth, `/v3/merchants/{mId}`, paging `limit=1000` until a short page, windows of at most 90 days, >100-line fallback to `/orders/{id}/line_items`, rate limits **16 rps / 5 concurrent per token and 50 rps / 10 concurrent per app** (process-wide limiter), 429 + `Retry-After` backoff, `User-Agent`; through `pos/http.py` so calls are metered. | Tests with recorded fixtures (synthetic, shaped from the official docs): paging, window edges, 429 retry, limiter timing (fake clock). | P1 |
| D2 | **CloverConnector** (implements `PosConnector`, registered in `pos/registry.py`): catalog + categories (primary-category rule) + item stock (signed) + cost + UPC/SKU + price type; orders + line items + modifications as modifier rows + `unitQty/1000` + voids/refunds + `orderType` channel; business date from `/properties` time zone + store cutoff; open orders (`state=open`); employees; merchant/timezone; `CLOVER_CAPABILITIES` corrected (plan 4.5). Sales sync records `ORDERS_SYNCED` (I3). | Fixture-driven contract tests (same contract as Toast's adapter); idempotent re-run; a day across the cutoff; capability-off pages hidden for a Clover store (`/api/auth/me`). | P1 |
| D3 | **Connect with Clover** (Setup > Integrations): `GET /api/settings/integrations/clover/authorize-url` (signed, store-bound `state`, 10 min), `GET .../callback` (code -> token exchange, access + refresh pair stored atomically through `credentials.py`, merchant id), refresh before the 30-minute expiry (single-flight per store), `POST .../disconnect` (audited); disabled with a clear message until `CLOVER_APP_ID` / `CLOVER_APP_SECRET` are set. **Manual token mode** (merchant id + API token + region) for a single-merchant pilot, with Test connection. | Tests with a fake token endpoint: state tampering/expiry refused, refresh race, disconnect; disabled by default; Integrations page model tests + screens. | P1 |
| D4 | **Item vendors editor** (Setup > Item vendors, `pages/ItemVendorsPage.jsx`, API `/api/item-vendors`): search items, add/edit/remove vendors (rank, vendor item code), "items without a vendor" filter, CSV import (preview with per-row errors, then apply) and CSV template/export; for a Toast store the inventory CSV still wins (read-only note); `vendors_of()` reads the table for stores whose POS has no vendor field. | API tests (auth, validation, import errors, idempotent import), node tests for the page model, screens at 1280 + 375. | P1 |
| D5 | **Help + owner checklist**: help articles "Connect Clover" and "Item vendors", glossary (merchant ID, API token, Connect with Clover, item vendor, ...), `docs/CLOVER_OWNER_CHECKLIST.md` (developer account, app + permissions, redirect URI, sandbox merchant, and the Appendix A checks: modifier prices, per-unit lines, `modifiedTime`, `state=open`, `summaryHour`, cost units, stock webhooks, merchant-token limits, history depth). | Help schema test passes; checklist reviewed. | P1 |
| D6 | Clover webhooks (ids only; need an installed app). | Deferred unless time. | P3 |

### Not in this job (stay Deferred, reasons in As built)

MT31 cutover of other stores; external pen test (MT32 vendor); Clover App
Market billing; Toast partner OAuth; a secret manager; a cross-process global
Clover per-app rate budget (D's limiter is per process; the queue makes it
one process per kind later); any Flutter change.

---

## 2. Ownership map

### 2.1 Files

| Track | Owns (creates or edits) |
|---|---|
| **A** | `app/platform/routes_console.py`, `app/platform/masters.py`, `app/platform/platform_settings.py`, `alembic_platform/versions/p0003_platform_settings.py`, `app/platform/signup.py` (setting read only), `app/help_content.py`, `app/glossary.py`, the 16 date sites in 1.A1 + `app/timeutil.py`, `content/help/schema.json`; website: `App.jsx`, nav/search registries, `featureRoutes.js`, `pages/PlatformConsolePage.jsx`, new `src/platform/*` (not `panels/`), `ui/*`, `styles/*`, `index.css`, `format.js`, `vite.config.*`, `package.json` + lock, the table pages touched by A6, `e2e/screens/*`, `src/clients/console.js` |
| **B** | `app/platform/routes_data.py`, `app/platform/usage.py`, `app/platform/plans.py`, `app/platform/billing/`, `app/platform/export.py`, `app/platform/offboarding.py`, `alembic_platform/versions/p0002_data_ownership.py`, `app/auth.py` (the `touch_activity` call only); website: `pages/StoreDataPage.jsx`, `platform/panels/StoreUsagePanel.jsx`, `platform/panels/StoreDataPanel.jsx`, `src/clients/data.js` |
| **C** | `app/storage/`, `app/jobqueue/`, `app/platform/routes_system.py`, `alembic_platform/versions/p0004_job_queue.py`, `app/scheduler.py`, `app/tenancy/{jobs,leader,boot,engines}.py`, `app/team/changes.py`, `app/pos/http.py`, `app/main.py` lifespan (also calls `usage.start()/stop()` in both modes), `app/db.py` engine options, `deploy/`, `docs/DEPLOY_MULTI_PROCESS.md`, `docs/DEDICATED_HOSTING*.md`, C2 call sites; website: `platform/panels/SystemStatusPanel.jsx`, `src/clients/system.js` |
| **D** | `app/pos/adapters/clover/`, `app/pos/registry.py`, `app/pos/capabilities.py`, `app/pos/gating.py`, `app/pos/sync/*`, `app/platform/routes_clover.py`, `app/platform/credentials.py` (clover spec), `app/platform/connector_tests.py` (clover), `app/platform/routes.py` (only the existing `clover_connect` function), `app/routers/item_vendors.py`, `app/services/item_vendors.py`, `app/item_vendor_models.py`, `app/features.py` (one new key), store migration `0063` if needed (then D also updates `tests/test_migration_chain.py`), `tests/fixtures/clover/`, `docs/CLOVER_OWNER_CHECKLIST.md`; website: `pages/IntegrationsPage.jsx`, `settings/integrationsModel.js`, `pages/ItemVendorsPage.jsx`, `src/clients/clover.js` |

Shared, **append-only** (conflicts are resolved in Integrate): `content/help/pages.json`, `content/help/glossary.json`, `content/help/articles/*` (new files per track), `backend/requirements.txt`, `app/tenancy/global_state_allowlist.txt`, the demo-guard allowlist, `docs/PLATFORM_CONTRACT.md` (one As-built section per track at the end), and each track's own marker block in `app/platform/config.py` `DEFAULTS`.

**Frozen** (nobody edits during Build): `frontend/src/api.js`,
`app/platform/routes.py` outside D's one function and the `install()` block,
`app/main.py` router list, the interface signatures in section 3.

### 2.2 Routes (already mounted, empty)

| Prefix | Module | Track |
|---|---|---|
| `/api/platform` | `app/platform/routes_console.py` (`/masters`, `/settings`, `/stores/{code}/health`) | A |
| `/api/platform` | `app/platform/routes_data.py` `router` (`/stores/{code}/usage`, `/plan`, `/exports`, `/offboarding`) | B |
| `/api/settings` | `app/platform/routes_data.py` `store_router` (`/usage`, `/exports`) | B |
| `/api/platform/system` | `app/platform/routes_system.py` (`/status`, `/jobs`, `/jobs/{id}/retry`) | C |
| `/api/settings/integrations/clover` | `app/platform/routes_clover.py` (`/authorize-url`, `/callback`, `/disconnect`) | D |
| `/api/item-vendors` | `app/routers/item_vendors.py` | D |

Never add a path another track's prefix + path already uses. Master routes
use the guards of `app/platform/routes.py`; every write route is audited.

### 2.3 Migrations (platform chain fixed now)

`p0001` -> **`p0002` B** (usage, plans, exports, off-boarding) -> **`p0003` A**
(platform settings) -> **`p0004` C** (jobs, workers). The three files exist as
empty placeholders; each track fills only its own `upgrade()/downgrade()`.
Store chain: head `0062`; only D may add `0063`.

### 2.4 Settings (`app/platform/config.py` `DEFAULTS`, read with `pget`)

Already reserved: `billing_provider` (B), `storage_backend`,
`job_queue_mode`, `live_updates_mode` (C), `clover_app_id`,
`clover_app_secret`, `clover_environment` (D). Add more only inside your own
marker block (e.g. B `stripe_secret_key`; C `storage_s3_endpoint`,
`storage_s3_bucket`, `storage_s3_access_key_id`, `storage_s3_secret_access_key`,
`storage_s3_region`, `db_pool_mode`; D `clover_redirect_url`). Secrets are
never logged, never returned by an API, never editable in the console.

---

## 3. Shared interfaces (committed with this file; tests in `backend/tests/test_website_ready_interfaces.py`)

Signatures do not change without a contract update. Stubs behave safely
today, so every track builds and tests against them in parallel.

**I1 Storage** `app.storage` (C implements; B, D use): `get_storage()` ->
`ObjectStorage` with `put_bytes`, `put_file`, `open`, `read_bytes`, `exists`,
`stat`, `delete`, `delete_prefix`, `list`, `bytes_used`, `presigned_url`
(None = server must stream), `local_path` (None on S3); keys are relative
`a/b.ext` per store (`code=` or the current store); `StorageError`,
`ObjectNotFound`. Local backend keeps today's paths.

**I2 Jobs** `app.jobqueue` (C implements; B, D use): `@register(kind,
max_attempts=, timeout_seconds=)` on `fn(payload: dict) -> dict | None`
(idempotent, JSON payload); `enqueue(kind, payload, code=, dedupe_key=,
run_after=, priority=) -> JobHandle`; `job_status(id)`; `mode()`;
`register_periodic(kind, hour=, minute=, per_store=)` + `periodic_jobs()`.
Inline today (a thread in the caller's store).

**I3 Usage** `app.platform.usage` (B implements; C, D call): `record(metric,
n=1, code=)`, `touch_activity(code=)` - never raise, never block; counters
`orders_synced`, `pos_api_calls`, `ai_calls`; gauges `users`, `items`,
`storage_bytes`, `database_bytes`; `last_activity_at`; `snapshot(code)`;
`start()/stop()`.

**I4 Plans** `app.platform.plans` (B implements; A reads): `PLAN_IDS`
(`custom`, `trial`, `starter`, `pro`), `DEFAULT_PLAN`, `plan_of(code)`,
`limits_of(plan)`, `limit_states(code)` -> `[{key, label, used, limit,
state: ok|near|over}]`.

**I5 Platform settings** `app.platform.platform_settings` (A implements;
B, C read): `get_platform_setting(key)` for `EDITABLE_KEYS`
(`signup_enabled`, `terms_url`, `support_email`, `default_plan`,
`offboard_grace_days`, `export_link_hours`); console value > environment >
default; security switches are not keys here.

**I6 Clover facts for B/C**: Clover writes its token pair only through
`credentials.py`; B's purge deletes all credentials of a store with the
existing delete path; Clover jobs are registered `clover_*` kinds.

**I7 Website**: shared helpers `request`, `postJSON`, `patchJSON` are
exported from `src/api.js`; each track's client functions live in
`src/clients/{console,data,system,clover}.js` and nowhere else. Console
panels: A renders `<StoreUsagePanel storeCode />`, `<StoreDataPanel
storeCode status onChanged />`, `<SystemStatusPanel />` (files in
`src/platform/panels/`, owned by B, B and C). Pages: A routes
`/settings/data` -> `pages/StoreDataPage.jsx` (B) and `/setup/item-vendors`
-> `pages/ItemVendorsPage.jsx` (D), both lazy, default exports, no props.

---

## 4. Names (one name per thing; the app adopts these later)

| Thing | Name (website now, app later) |
|---|---|
| Ordering start page | **New order** (subtitle "Supplier Projection") |
| Cart | **Purchase Order Cart**; button **Order placed** (app: was "Purchase Cart" / "Mark placed") |
| Lists in Fix Issues | Sentence case: "Missing barcodes", "Missing vendors" (app: was Title Case) |
| Start page | **Home** |
| Setup pages (new) | **Item vendors**, **Data & usage**, **Store settings**, **Integrations** |
| Export | **Export all data**; its file is a **data export**; link = **Download link** (expires) |
| Off-boarding states | **Archived** · **Deleting on <date>** · **Deleted** (purge = "Delete store data") |
| Plans | **Custom**, **Trial**, **Starter**, **Pro**; "limit" / "near the limit" / "over the limit" |
| POS connect | **Connect with Clover** · **Disconnect** · "Merchant ID" · "API token" |
| Two-factor | **Two-step sign-in** (never "TOTP" / "2FA" on screen) |
| Store code | **Store ID** |
| Console tabs | Requests · Stores · Hosting · Audit · **Masters** · **Platform settings** · Help |
| Store detail tabs | Overview · Usage & plan · Settings · Data · Audit |
| System (console) | Storage · Background jobs · Workers · Live updates |

Kept from DESIGN_SYSTEM 3.5: Open Checks, Placed Orders, Vendor Calendar,
Delivery Review, Invoices, Inventory Reports, Reconciliation, Items Sold,
Insights, Tasks, Chat, News, Flyers, Help Center, Glossary.

---

## 5. Integrate and ship

Merge order **C -> B -> D -> A** into `main` (interfaces first, routing/help
last); resolve append-only files by keeping every entry; then the full server
suites (PostgreSQL + SQLite), website node tests, build (chunk check),
`e2e/screens` + `audit.mjs`, `e2e/store`, `e2e/platform`, name guards. Ship:
back up the pilot, deploy `main`, run `p0002-p0004`, restart; every new
switch stays off; pilot parity and health checked; owner handoff updated.

## 6. Owner actions (prepared as settings + steps, never faked)

1. **Clover developer account + app** (developer.clover.com): app with
   inventory/orders/employees/merchant read permissions, redirect URI
   `https://app.kloudc.com/api/settings/integrations/clover/callback`, then
   `CLOVER_APP_ID`, `CLOVER_APP_SECRET`, `CLOVER_ENVIRONMENT=sandbox`; a
   sandbox merchant and the Appendix A checks (`docs/CLOVER_OWNER_CHECKLIST.md`).
2. **Cloudflare R2**: bucket + API token -> `STORAGE_S3_*`, then
   `python -m app.storage migrate --to s3 --dry-run`, `--verify`, then
   `STORAGE_BACKEND=s3`.
3. **Stripe** (when billing starts): account, prices per plan,
   `STRIPE_SECRET_KEY`, webhook secret, `BILLING_PROVIDER=stripe`.
4. Plan limits and prices (defaults are placeholders); terms page URL.
5. Optional: `JOB_QUEUE_MODE=queue` + the worker LaunchDaemon, and
   `LIVE_UPDATES_MODE=postgres`, when more than one server process runs.

## 7. App follow-ups (for the handoff; no app change tonight)

Adopt the section 4 names; Item vendors editor (simple); Data & usage
(read-only usage + "Export all data"); store-date helper for "today"; Connect
with Clover screen states (disabled / connected / manual token); help bundle
regenerated from `content/help`.
