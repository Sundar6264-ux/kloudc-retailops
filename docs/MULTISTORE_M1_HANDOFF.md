# Multi-store M1: handoff

**Date:** 2026-10-15. **Branch:** `multistore-m1` (worktree `/Users/sundar/spicetown-dev/website-m1`), its last commit is the "M1 final check and handoff" docs commit (code last changed at `d4d86ad`). **Status:** finished and checked. It is **not merged** into `ops-fixes` or `main`, **not pushed** and **not deployed**, and it should stay that way.

**Your decision, made while M1 was being built:** the multi-store product becomes a **separate product in its own repository**. It is not merged into Spice Town for now ("I don't want to mess up spicetown's app for now"). This document replaces the usual Spice Town deploy runbook. There is nothing to deploy to the Spice Town server.

---

## 1. In plain English

**What M1 built.** Until now the code could only talk to Toast, and Toast details were spread across the whole backend. M1 put Toast behind one plug-in point called the **POS connector**. Everything else in the app now asks the connector for orders, items, stock and employees, and gets back the same neutral shapes whatever the POS is. Adding Clover later means writing a Clover connector. It does not mean touching Sales, Forecast, Reorder or POs.

M1 also added:
- **Neutral ids** on every sales line, next to the Toast ids, so the data no longer assumes Toast (migration 0060).
- **Item vendors as app data** in a new `item_vendors` table. Today the supplier list only lives in Toast's CSV; a Clover store has no such field (migration 0061).
- **POS capabilities.** The server now says which POS it runs and what that POS can do. Toast-only pages and scheduled jobs switch off by themselves for a POS that can't support them.
- **Neutral names** for settings and invoice keys (`STORE_TIMEZONE`, `AP_INBOX_EMAIL`, `ap_*`, `/mark-ap-closed`). The old names keep working.
- **A safety net:** a nightly backup script, a restore drill, Litestream-to-R2 templates, and a **parity harness** that proves the app still gives the same answers.

**Spice Town is untouched.** Nothing from this branch runs on the Spice Town server. `ops-fixes` and `main` are still at `08b43c0`, the commit M1 started from, and Spice Town keeps running that code. Even if this branch were run against Spice Town's data, it gives the same answers: section 2 shows the proof.

**What it enables.** This branch is the starting point of the new product. The next step (M2) creates the new repository and app identity, removes Spice Town's names and rules, and adds per-store settings and a Dockerfile. Clover (M3) and the xyz grocery pilot (M4) follow.

---

## 2. Final check (fresh production copy, Oct 15 18:09)

All checks ran on a read-only copy of the live database taken at 18:09 (434 MB, migration 0059, 125,324 order lines). The copy was deleted afterwards. No real Toast, WhatsApp, email, push or AI call was made.

| Check | Python 3.9.6 | Python 3.12.14 (production runtime) |
|---|---|---|
| Full backend test suite (nothing skipped by hand; the website was built first, so the `frontend/dist` test ran too) | 2454 passed, 12 skipped, 0 failed | 2454 passed, 12 skipped, 0 failed |
| Alembic heads | one head, `0061` | one head, `0061` |
| Migration 0059 -> 0061 | 4.25 s | 4.14 s |
| Downgrade 0061 -> 0059 | 2.05 s | 2.03 s |
| Upgrade again 0059 -> 0061 | 1.87 s | 1.75 s |
| Parity harness: `08b43c0` (Spice Town's current code) against `multistore-m1` on the same copy | **0 differences** in 114 files | **0 differences** in 114 files |

- **Migrations:** at every step `integrity_check` was ok and `foreign_key_check` empty. Every pre-M1 table kept its row count, a checksum of the old `orders` and `open_order_cache` columns never changed, the schema after the downgrade was identical to the original, and the second upgrade gave the same schema as the first. After upgrading: all 125,324 order lines have neutral ids equal to their Toast ids (9,327 modifiers, `source = 'toast'`), 6,559 item ids are mapped, the sync cursor is 2026-10-14, `item_vendors` has 5,541 rows for 5,145 items, and all 25 open-checks rows have the new order id.
- **Parity:** all 110 endpoints answered 200 on both sides, with 0 network attempts and 0 background threads run. The scheduled-job list and the push alerts are byte-identical. The only additions (allowed, 630) are `pos_name` and `capabilities` on `/api/app/config` and `/api/auth/me`, 7 `ap_*` keys on each of the 86 invoices, and the new tables and columns in the database checksums. With `--strict` those 630 additions are the only entries.
- **Website:** 208 node tests passed (0 failed); `vite build` succeeded. M1 changes no website or app code.


### Everything M1 changed (44 commits on `08b43c0`, 113 files)

| Batch | What | Key files |
|---|---|---|
| MT1 safety net | Parity harness; nightly backup; restore drill; Litestream/R2 templates | `backend/scripts/parity/`, `backend/scripts/backup/` |
| MT2 Toast adapter | Toast code moved into one package; connector interface; `POS_PROVIDER` setting; old module names still work | `backend/app/pos/base.py`, `registry.py`, `adapters/toast/` |
| MT3 neutral sync | POS-neutral models (Order, Item, StockLevel, ...); the sales, open-checks, catalog and accuracy jobs read them | `backend/app/pos/models.py`, `backend/app/pos/sync/` |
| MT4 neutral ids | Migration 0060: `source`, `external_*_id`, `is_modifier`, `channel`, `dining_option_id` on `orders`; `sync_cursors`; `pos_item_map`; `Order.top_level()` | `backend/alembic/versions/0060_pos_neutral_ids.py`, `backend/app/pos/neutral_ids.py` |
| MT5 item vendors | Migration 0061: `item_vendors`; one vendor-list rule for 8 parsers; CSV upload fills the table | `backend/app/services/item_vendors.py`, `backend/app/item_vendor_models.py` |
| MT6 capabilities | `pos_name` + `capabilities` in `/api/app/config` and `/api/auth/me`; 409 on Toast-only routes for a POS without the capability; jobs registered by capability; `ap_*` keys; `/mark-ap-closed` | `backend/app/pos/gating.py`, `backend/app/pos/capabilities.py` |
| Integration | The three parallel batches merged; the sync fills the neutral columns; gating reads the connector registry | `backend/tests/test_m1_integration.py` |

The full record, batch by batch with every number, is `docs/MULTISTORE_M1_CONTRACT.md` in the branch.

---

## 3. Splitting `multistore-m1` into the new product repository

**Waiting on you:** the product name and the GitHub repository (and which account or organization owns it; the plan recommends KloudC LLC).

**Nothing below has been done yet.** In particular, the clean-up list in step 3 is a to-do list for M2, not something already changed.

### Step 1. Create the new repository from the branch

The branch lives inside Spice Town's own git store (`/Users/sundar/spicetown-backend/.git`, remote `spicetownbackend/spicetown-backend`). The new product must not push there.

Choose one:
- **A. Keep the history (recommended while the repo is private).** Git can then still bring Spice Town fixes across later with a normal merge.

      git clone --single-branch --branch multistore-m1 /Users/sundar/spicetown-backend ~/kloudc/<product>
      cd ~/kloudc/<product>
      git remote remove origin
      git remote add origin git@github.com:<owner>/<product>.git
      git branch -m multistore-m1 main
      git remote add spicetown /Users/sundar/spicetown-backend   # read-only use: fetch + merge Spice Town fixes
      git push -u origin main

  The history contains Spice Town's own business documents and CSVs (`docs/CATEGORY_*.csv`, `docs/SHAZ_CATEGORY_FIX.*`, contracts). That is fine in a private repo you own, but it is not fine to make public or share with outside developers.
- **B. A fresh history (choose this before the repo is ever made public or shared).** Make one first commit from the branch's files, saying which commit it came from:

      mkdir -p ~/kloudc/<product> && cd ~/kloudc/<product> && git init -b main
      git -C /Users/sundar/spicetown-backend archive multistore-m1 | tar -x
      git add -A && git commit -m "Import from spicetown-backend multistore-m1 @ $(git -C /Users/sundar/spicetown-backend rev-parse --short multistore-m1)"

  Spice Town fixes then have to be copied across by hand (cherry-pick from a `spicetown` remote).

Both ways only **read** the Spice Town repository. Afterwards:
- keep the `multistore-m1` branch in the Spice Town repository as the record of where the product came from (do not merge it, do not push it);
- once the new repo is working, the worktree can be removed with `git -C /Users/sundar/spicetown-backend worktree remove /Users/sundar/spicetown-dev/website-m1` (the branch stays);
- the mobile app is a separate repository (`/Users/sundar/spicetown-dev/mobile`), so it gets its own copy the same way (step 4).

### Step 2. Before the first deploy anywhere: never under `spicetown.shop`

The Spice Town website's login cookie is shared across `.spicetown.shop` (`SESSION_COOKIE_DOMAIN=.spicetown.shop` in Spice Town's `.env`; code at `backend/app/config.py:160-165`, used at `backend/app/auth.py:71` and `:80`). Any other site under that domain would receive Spice Town staff's login cookies. The new product's stores and its demo store must use the new product's own domain, with `SESSION_COOKIE_DOMAIN` left blank or set to that domain.

### Step 3. What to clean out first (M2's first batch; file:line in the branch as of `d4d86ad`)

**Spice Town data that migrations insert into every new database.** A new store built from these migrations would start with Spice Town's rows. Leave the migrations themselves alone (Spice Town's database already ran them). Instead, the `store-init` command (plan MT8) deletes these rows for any other store and loads that store's own settings:

| Migration | What it inserts | Where |
|---|---|---|
| 0034 | "Shaz International -> Biscuits, Cookies & Rusk" category rule | `backend/alembic/versions/0034_category_overrides.py:37-52` |
| 0037 | Spice Town's category merge rules (`created_by` = "category merge plan (migration 0037)") | `backend/alembic/versions/0037_category_mapping_rules.py:31-39` (list), `:94-108` (insert) |
| 0039 | The three built-in checklists: Spice Town daily closing, Street Kitchen chef closing, Street Kitchen FSA | `backend/alembic/versions/0039_checklist_builder.py:74-82`, data in `backend/app/services/checklist_builtin.py` (titles at `:72`, `:1031`; Google Form links at `:37-38`) |
| 0044 | WhatsApp roles "Spice Town staff group" and "Street Kitchen staff group" | `backend/alembic/versions/0044_whatsapp_groups.py:43-50`; same names in code at `backend/app/services/whatsapp_groups.py:47` |

**Store name, address, phone and logo** (these become store settings, plan MT7):
- PO PDF header and footer: `backend/app/services/po_export.py:32-33` (address, phone), `:103` (name), `:220` (footer)
- PO email and WhatsApp text: `backend/app/services/po_send.py:56-58` (name, address, phone), `:376`, `:393`, `:413` (fallback sender domain `spicetown.shop`)
- Public checklist page: `backend/app/routers/checklist_public.py:251`
- Old-app message: `backend/app/routers/reports.py:108`
- Invoice transfer default vendor: `backend/app/services/invoice_submission.py:74`
- PDF producer tag: `backend/app/services/toast_pdf.py:411`
- Flyer folder and categories: `backend/app/config.py:219`, `backend/app/services/flyers.py:5-8`, `:77`, `:158`; glossary text `backend/app/glossary.py:1789`
- Settings with Spice Town defaults: `backend/app/config.py:185` (`whapi_spice_town_checklist_group_id`), `:194` (`toast_invoice_email` default `spicetown@invoices.toasttab.com`), `:210` (`public_base_url` default is Spice Town's tailnet address), `:25` (time zone default New York)
- Website: `frontend/index.html:10` (page title), `frontend/src/pages/LoginPage.jsx:51-52`, `frontend/src/components/Sidebar.jsx:119-121`, and 12 more files under `frontend/src` that mention "Spice Town" (`grep -rl "Spice Town" frontend/src`); time zone fixed at `frontend/src/format.js:10`
- Logo and icons: `frontend/public/logo-header.png`, `favicon-32.png`, `favicon-512.png`, `apple-touch-icon.png`
- Capacitor wrapper: `frontend/capacitor.config.json` (`appId` `com.spicetown.app`, `appName` "Spice Town")

**Ask-bot and weekly digest store description** (becomes a `store_description` setting, plus a per-store AI on/off switch):
- `backend/app/services/ask_bot.py:53-54` ("Ask Inventory Bot for Spice Town, a real South Asian grocery + halal meat + prepared-food store in Vernon, CT")
- `backend/app/services/weekly_digest.py:97-98` (the same description)

**Spice Town rules written into the code** (these become the "store pack", plan MT8):
- First real sales date: `backend/app/pos/sync/accuracy.py:74`, `backend/app/pos/sync/open_orders.py:53`
- Close-out hour 4 AM: `backend/app/pos/sync/accuracy.py:86`, `backend/app/services/insights_data.py:73`
- Kitchen and menu groups: `backend/app/services/fix_issues.py:59`, `backend/app/services/delivery_suggestions.py:115`, `backend/app/services/insights.py:68`
- Sales channel names: `backend/app/services/insights.py:454`
- House vendor ("spicetown"): `backend/app/services/schedule_suggestions.py:139`
- PO sections and frozen-food rules: `backend/app/services/po_sections.py:25-150`
- Named staff in Fix Issues leads: `backend/app/services/issue_leads.py:135` (fallback lead) and `:142` onward (section-to-staff map)
- Recurring task time zone: `backend/app/team/recurrence.py:39`

**Files in the repository that are Spice Town's own, not the product's:**
- `docs/` business files (category CSVs, Shaz fix, Spice Town batch contracts), `HANDOFF.md`, `MOBILE_APP_CONTEXT.md`, `MOBILE_APP_FIXES.md`, `WEBSITE_REQUIREMENTS.md`
- `.claude/skills/spicetown/SKILL.md` (about 3,700 lines of Spice Town operating notes; keep a trimmed product version)
- `backend/scripts/backup/com.spicetown.*.plist` and the Spice Town paths in `backend/scripts/backup/README.md`
- Test fixtures that use Spice Town names are harmless, but the M2 "fresh store" test should search a new store's database for "Spice Town", "Street Kitchen", "Vernon" and staff names (plan section 9.1).

### Step 4. New app identity (mobile)

The app today is `com.spicetown.app`, named "Spice Town" (`mobile/ios/Runner.xcodeproj/project.pbxproj:520`, `mobile/android/app/build.gradle.kts:8,19`, `pubspec.yaml` `name: spicetown_app`, login footer "For Spice Town staff" at `mobile/lib/features/authentication/widgets/login_footer.dart:15`, server address `https://ops.spicetown.shop` at `mobile/lib/core/config/env_config.dart:32-33`). Because the product is now separate, M2 gives the new app:
- its own bundle id and application id (not `com.spicetown.app`, so Spice Town's App Store record and installs are not touched);
- its own name, icon and Firebase project;
- store-code login (plan MT11) instead of a fixed server address.

Spice Town's app keeps shipping from the existing mobile repository as before.

---

## 4. Backups and Litestream (off until you add keys)

The scripts are in the branch under `backend/scripts/backup/`. Full steps are in its `README.md`. They use only the Python standard library and never write to the live database.

- **`backup_db.py`** (nightly copy): a consistent read-only copy, an integrity check, gzip, kept 30 days. A failed run deletes nothing, and the newest backup is never deleted. On Spice Town's Oct 15 database it took 14.5 s and produced 120 MB.
- **`restore_drill.py`**: restores the newest backup into a temp folder, checks it, and compares row counts with the live database. The Oct 15 drill matched all 59 tables.
- **Litestream to Cloudflare R2** (continuous, about 10 s behind): templates only (`litestream.yml.template`, `run_litestream.sh`, `com.spicetown.litestream.plist`). The start script refuses to run until `~/.config/spicetown/litestream.env` holds real keys (permissions 600). Nothing is installed.

**Your steps when you want it** (from the README):
1. In Cloudflare, create a private R2 bucket, for example `kloudc-store-backups`. One bucket serves every store, one folder per store (`stores/<store>/`).
2. Create an R2 API token with **Object Read & Write** on that bucket only. Note the Access Key ID, Secret Access Key and Account ID.
3. `brew install litestream`.
4. Copy the template to `~/.config/spicetown/litestream.yml`, fill in the account id and bucket, and put the two keys in `~/.config/spicetown/litestream.env` (`chmod 600`). Never paste keys into a chat.
5. Run `run_litestream.sh --check`, then install the LaunchDaemon, then prove a restore from R2 into `/tmp` with `restore_drill.py`.

**Spice Town note:** because this branch is not deployed, these scripts are **not on the Spice Town server**. They are app-independent. If you want Spice Town backed up now, copying just `backend/scripts/backup/` into Spice Town's repository is a small separate change, and installing it is your call. Nothing has been done.

For the new product, the same scripts run inside each store's container (section 6), with the store's own R2 folder.

---

## 5. Parity harness: how to use it

The harness proves a code change gives the same answers as before on real data. It is in `backend/scripts/parity/` (`README.md` has the details).

1. **Take a read-only copy** of a database:

       sqlite3 "file:/path/to/spicetown.db?mode=ro" ".backup /tmp/work/copy.db"

2. **Record the old code** (from a checkout or `git archive` of the old commit, with `scripts/parity/*.py` copied in):

       cd <old>/backend && python scripts/parity/parity_record.py --db /tmp/work/copy.db --out /tmp/work/rec-old --scratch-root /tmp/work

3. **Record the new code**, with `--migrate` if it adds migrations:

       cd <new>/backend && python scripts/parity/parity_record.py --db /tmp/work/copy.db --out /tmp/work/rec-new --migrate --scratch-root /tmp/work

4. **Compare:**

       python scripts/parity/parity_diff.py /tmp/work/rec-old /tmp/work/rec-new

   `DIFFERENCES: 0` is the pass mark. New JSON keys, tables and columns are listed as allowed additions (`--strict` counts them too). A new push alert or scheduled job always counts as a difference.

What it does: it works on a scratch copy, with the schedulers off, the network blocked, outside services blank and "now" fixed. It calls 110 read endpoints as an admin and records push alerts, morning summaries, the scheduled-job list and a checksum of every table before and after. One recording takes about 45-60 s.

Things to know:
- Compare recordings made with the **same Python version**. 3.9 and 3.12 add up decimals slightly differently, so the tool refuses to compare them.
- `scripts/parity/sync_compare.py` is the row-by-row check of the sync jobs (old against new code with the same Toast answers). It is for changes to the sync code.
- In the new product the harness is the regression gate for every change that must not alter a store's numbers, and a plan step for each Postgres move (M5).

---

## 6. Hosting: Docker first, no Kubernetes until 20-50 stores

- **Each store is one container** of the same image: the API, the website and the scheduled jobs, with the store's own volume (SQLite database, uploads, flyers) and its own `.env`. Litestream runs next to it, streaming that store's database to its R2 folder.
- **Several stores fit on one small VM** using Docker Compose, with a reverse proxy (Caddy) or a Cloudflare Tunnel in front, one host name per store on the product's domain. A deploy loop updates the demo store first and real stores after, stopping on a failed health check.
- **No Kubernetes until about 20-50 stores.** Below that it adds cost and moving parts without a benefit Compose can't give. By the time it would help, Step 2 of the plan (one shared service, a Postgres database per store) changes the shape anyway; decide then between Kubernetes and a managed container platform.
- **The Dockerfile is an M2 deliverable.** Needs: Python 3.12 (the production runtime), `requirements.txt`, the Tesseract binary for invoice OCR, the built website (`frontend/dist`), `alembic upgrade head` on start, and a health check. The image must hold no secrets.
- **Spice Town stays on the Mac mini** as it is. It does not move into Docker as part of this work.

---

## 7. Deferred and open items

**From M1 (carried into the new product):**
- The website and app do not yet hide pages based on `capabilities` (M2).
- Items Sold "today" and the Toast-only push alerts (loss watch, inventory-API ready) are not gated by capability.
- `toast_email.py` and `toast_pdf.py` are not yet moved to `pos/ap_inbox/`.
- `orders.channel` stays empty for Toast. Filling it needs Toast's dining-option names.
- Reports and the sales upsert key still read the Toast columns. Switching them is the later "contract" step (plan 8.1).
- The connector's analytics report, "changed since" order pull and CSV catalog import are not built.
- POS errors are not yet turned into 409/503 answers.
- The CSV upload's vendor refresh adds about 2 s per upload. A faster query is noted.
- `/api/app/config`, which needs no login, now also shows the POS name and capability list.
- Three behaviours differ only on malformed Toast data that Toast has never sent (contract section 7 of MT3).

**Your decisions:**
- The new product's name and GitHub repository (and its owner, recommended KloudC LLC).
- Whether to keep the git history (option A) or start fresh (option B).
- R2 bucket and keys for Litestream; whether to install the nightly backup (for Spice Town too).
- The plan's open decisions D1-D9 (`MULTI_STORE_PLAN.md` section 14).

---

## 8. Where things are

- Branch: `multistore-m1` in `/Users/sundar/spicetown-backend/.git`, worktree `/Users/sundar/spicetown-dev/website-m1`
- Batch-by-batch record: `/Users/sundar/spicetown-dev/website-m1/docs/MULTISTORE_M1_CONTRACT.md` (includes the final check)
- This handoff: `/Users/sundar/spicetown-dev/MULTISTORE_M1_HANDOFF.md` (a copy is in the branch at `docs/MULTISTORE_M1_HANDOFF.md`)
- The plan: `/Users/sundar/spicetown-dev/MULTI_STORE_PLAN.md`
- Proof logs and recordings: `/Users/sundar/.claude/jobs/e26a1611/tmp/multitenant/m1/` (`final/` for this check). It still holds MT1's production copy `prodcopy.db` (434 MB, Oct 15 13:48) and the baselines recorded on it; they contain business data and can be deleted once you no longer need to re-check M1 against them.
- Branches kept: `m1-mt4`, `m1-mt5`, `m1-mt6` (already merged into `multistore-m1`)
