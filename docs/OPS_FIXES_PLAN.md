# Ops fixes (Sep 29) - build contract for parallel agents

Owner's list of 14 changes, built in batches of up to 4 parallel agents. Each fix is checked by a
separate verifier agent, then the lead reviews all of a batch, runs regression, commits and pushes.

Repos (branch `ops-fixes` in both - already checked out, do NOT switch branches):
- Backend + website: `/Users/sundar/spicetown-dev/website` (`backend/` FastAPI, `frontend/` React/Vite)
- Flutter app: `/Users/sundar/spicetown-dev/mobile`
- Read `website/.claude/skills/spicetown/SKILL.md` gotchas before changing backend/web code.

## Batches

Batch 1 (file-disjoint):
- **A. Fix Issues** - new lists: Slow moving flagged "Review"; Dead with qty > 0; Dead newly arrived
  (5+ days, no sales since arriving); Negative inventory moved in; Refill from container (store
  <= 5 with stock in the container). All with the same close/reason/closed-report flow. Web + app.
- **B. Reports** - delete the margin report everywhere; remove Negative inventory from the Reports
  hubs (it moves to Fix Issues - agent A adds it there); make every report in the app's
  More > Reports (and the web Inventory Reports) downloadable.
- **C. Placed orders** - fix the duplicate "recently placed" entry; a proper Placed Orders page on
  web and a convenient screen in the app.
- **H. Home (app)** - home search also finds app features (reports, users/access, scan, etc.);
  invoice upload on Home picks the vendor from a dropdown list (with search inside the list).

Batch 2: D. hide-sales access option; E. similar products + other vendors in Supplier Projection;
F. Shaz International category. Batch 3: G. definitions (Critical, Review, Intermittent...) on
every tab, plus the Help pages for everything above.

## Rules for every agent

1. **Own only your files.** Shared files (listed below) get small, additive edits: re-read right
   before editing, never reformat or reorder someone else's code, never revert changes you didn't
   make. If `git diff` shows changes outside your area, they belong to another agent - leave them.
2. **Never commit, push, switch branches, stash, or reset.** The lead commits.
3. **Never touch prod** (`/Users/sundar/spicetown-backend`, its DB, or the LaunchDaemon). Use the dev
   DB `website/backend/spicetown-dev.db` read-only, or a copy of it in your scratch dir.
4. **Python must stay 3.9-compatible** (`Optional[X]`, no `X | Y`, no `match`); timestamp columns
   use `UTCDateTime()`. No em/en dashes in user-facing text (use "-").
5. **Backward compatibility:** the App Store build reads `/api/reports/*`, `/api/issues/*`,
   `/api/ordering/*`. Changes to existing responses must be additive (new fields/endpoints) unless
   the plan says to remove something.
6. **Migrations** (alembic head is `0031`): C owns `0032` (down_revision `"0031"`). Batch 2 numbers
   are assigned later. Nobody else adds a migration without asking the lead.
7. **Help pages are batch 3's.** Don't edit `frontend/src/pages/HelpPage.jsx` or the app's help
   content; instead append 2-4 lines per feature (what it is, how it's computed) to
   `website/docs/OPS_FIXES_HELP_NOTES.md` under your agent letter.
8. **Test servers:** use FastAPI `TestClient`/pytest where possible. If you need a live server, use
   your assigned port (A 8101, B 8102, C 8103, H 8104, verifiers 8111-8114) with
   `SCHEDULER_ENABLED=false NOTIFICATIONS_ENABLED=false TEAM_JOBS_ENABLED=false` and a COPY of the
   dev DB; kill it when done. Port 8001 is the owner's dev server - don't touch it.
9. **Checks before you report done:**
   - Backend: `cd website/backend && .venv312/bin/python -m pytest -q` (all pass) and add tests for
     new backend logic in `backend/tests/test_ops_<area>.py`.
   - Web: `cd website/frontend && npx vite build --outDir /tmp/<scratch>` (don't overwrite `dist/`
     while others run).
   - App: `cd mobile && flutter analyze` (no new issues) and `flutter test` for touched areas; add
     widget/unit tests for new UI logic.
10. Report back: files changed, what you built, how you verified it, and anything unfinished.

## Shared files (additive edits only)

Backend: `app/main.py`, `app/models.py`, `app/features.py`, `app/routers/reports.py`,
`app/routers/ordering.py`, `app/services/reorder.py`.
Web: `frontend/src/api.js`, `frontend/src/App.jsx`, `frontend/src/featureRoutes.js`,
`frontend/src/components/Sidebar.jsx`, `frontend/src/index.css` (append new rules at the end).
App: `lib/app/router.dart`, `lib/core/api/api_config.dart`, `lib/core/widgets/app_shell.dart`.

## Ownership, batch 1

- A: `backend/app/services/fix_issues.py`, `backend/app/routers/issues.py`, new loader modules it
  needs, `frontend/src/components/FixIssues.jsx`, `mobile/lib/features/fix_issues/**`.
  Can read (not rewrite) `inventory_intelligence.py`, `demand_forecast.py`.
- B: `backend/app/services/inventory_intelligence.py` (margin removal only - keep
  `get_negative_inventory` and `get_dead_stock` working, A imports them), `ask_bot.py`,
  `weekly_digest.py`, `frontend/src/components/InventoryReports.jsx`,
  `frontend/src/components/WeeklyDigest.jsx`, `mobile/lib/features/inventory_reports/**`,
  `mobile/lib/features/reports/**`.
- C: `backend/app/services/purchase_order_log.py`, `PlacedOrder` in models.py, migration 0032,
  placed-order routes in `ordering.py`, new web page(s) for placed orders,
  `frontend/src/components/PurchaseOrderCart.jsx`, `mobile/lib/features/purchase_cart/**`,
  `mobile/lib/features/ordering_hub/**`.
- H: `mobile/lib/features/home/**`, `mobile/lib/features/search/**`, and the invoice vendor picker
  (`mobile/lib/features/invoices/**` vendor-selection widgets, `mobile/lib/features/suppliers/widgets/vendor_picker.dart`
  if that's the picker used). Home's recently-placed section belongs to C if it needs changing -
  coordinate by leaving it alone.

## Batch 2 (after batch 1 was committed as cb758ff / 92f5796)

A follow-up agent may still be finishing batch 1 changes in: Fix Issues files (web + app), the app's
`lib/features/search/**` (feature_index.dart, product_search_screen.dart) and
`lib/features/inventory_reports/**`. Batch 2 agents must NOT edit those files; if you need a change
there, put it in your report and the lead will do it.

Migrations: `0033_users_hide_sales.py` already exists (lead wrote it: `users.hide_sales` Boolean,
default false, model field added). F owns `0034` (down_revision `"0033"`). No other migrations.

Ports: D 8121, E 8122, F 8123, verifiers 8131-8133.

- **D. Hide sales (per-user)** - owns: auth/users routes and schemas (`app/routers/auth.py`,
  `app/schemas.py` user parts), a new `app/sales_access.py` helper, the sales-bearing
  endpoints/services (overview/today sales, items sold, weekly digest sales, open orders totals,
  Ask bot sales tools), web `UsersPage.jsx`, `AuthContext.jsx`, `OverviewWidgets.jsx`,
  `ItemsSold.jsx`, `WeeklyDigest.jsx`, `OpenOrders.jsx`, app `lib/core/auth/**`,
  `lib/features/admin/**`, `lib/features/home/**` (sales tiles only), `lib/features/sales/**`,
  `lib/features/orders/**`.
- **E. Similar products + other vendors in Supplier Projection** - owns: new
  `app/services/similar_products.py` (+ data files it needs under `app/data/`),
  `app/services/supplier_projection.py` (additive fields only), the projection routes in
  `app/routers/ordering.py`, web `SupplierProjection.jsx`, app `lib/features/suppliers/**`
  (except `widgets/vendor_picker.dart`), and the add-item sheet `lib/features/purchase_cart/screens/add_cart_item_sheet.dart`
  if it shows projection details.
- **F. Shaz International category** - owns: migration 0034, a new `app/services/category_overrides.py`,
  where overrides are applied (`app/services/reorder.py::latest_inventory_by_item` or wherever is
  central - additive), `app/services/po_sections.py`, and a Toast fix list in `docs/`.

## Batch 4 (after batch 3 = cf74dad / abf904a)

Migrations: next free numbers are 0035 (S) then 0036 (R, down_revision "0035"). If S needs no migration it
tells the lead in its report; R must then set down_revision to "0034" (lead will fix up). Ports: P 8151, Q 8152,
R 8153, S 8154, verifiers 8161-8164.
- **P. Supplier Projection class filter** - owns `SupplierProjection.jsx`, app `lib/features/suppliers/**` (not vendor_picker.dart).
- **Q. Cart in-stock bracket + greyed already-added items** - owns `PurchaseOrderCart.jsx`, `po_cart.py`, cart routes in `ordering.py` (additive), `/api/inventory/items/search` in `routers/inventory.py` (additive params only), app `lib/features/purchase_cart/**`.
- **R. Category edits** - owns `category_overrides.py` (service + router), new web page(s), new app screen(s), `features.py` (new key), additive edits to `featureRoutes.js`/`App.jsx`/`Sidebar.jsx`/`router.dart`/`api_config.dart`/`feature_index.dart`/`more_screen.dart`.
- **S. New checklist (FSA closing)** - owns checklist backend (`checklist_schemas.py`, `checklist_submission.py`, `routers/checklists.py`, checklist notification code), web `DailyChecklists.jsx`, app `lib/features/checklists/**`. Spec: `docs/FSA_CHECKLIST_FORM.md`.
