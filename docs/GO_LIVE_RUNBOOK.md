# Go-live runbook: app.kloudc.com and store #1

Plain steps for the owner, in order. Each step says what to do, how to check
it worked, and how to undo it. The install folder is `~/kloudc-retailops`
(called `<install>` below); commands run in Terminal on the Mac mini.

Status of this document: **skeleton, pre-production 2026-10-21** (track P).
The parts marked *(owner)* need your accounts or a decision; everything else
is already built and tested.

| # | Step | Who | Blocks |
|---|---|---|---|
| 1 | Toast Analytics keys, then 3 clean daily reports | owner | switch-over |
| 2 | Move kloudc.com DNS to Cloudflare | owner | 3 |
| 3 | The tunnel: app.kloudc.com -> this Mac | owner (sudo once) | 4, App Review |
| 4 | Production settings | owner (copy + update.sh) | 5-7 |
| 5 | Off-site backup | owner (one setting) | go-live |
| 6 | DEMO store for App Review | owner (one command) | TestFlight review |
| 7 | Terms of Service | owner (lawyer read) | App Store |
| 8 | Switch-over day | owner + staff | - |

---

## 1. Sales double-check and the daily comparison *(owner)*

Why: the daily comparison (07:30) found days that differed because Toast
changed closed days later and the pilot store had no Toast Analytics keys
(`docs/PARITY_INVESTIGATION.md`). The server now re-checks the last 7 days
once a day even without the keys, but the keys catch every edit within hours.

1. Website > Store setup > Integrations > Toast: enter **Analytics client ID**
   and **Analytics client secret** (the same pair the current system uses).
   Save, then Test.
2. Check: Items Sold > Sales accuracy (admin) no longer says the keys are
   missing; `<install>/logs/parity.log` has no "Sales double-check is
   limited" line after the next 07:30 run.
3. **Gate (decision)**: switch staff over after **3 clean 07:30 reports in a
   row** (`"ok": true` in `<install>/logs/parity-<STORE>.jsonl`).

Undo: nothing to undo (the keys only read from Toast).

## 2. Move kloudc.com DNS to Cloudflare *(owner)*

The website and email stay where they are; only the DNS moves.

1. Bluehost > Domains > kloudc.com > DNS: take a screenshot of **every**
   record (A, AAAA, CNAME, MX, TXT for SPF/DKIM/DMARC, verification TXT).
2. Cloudflare > Add a site > `kloudc.com` > Free plan. Compare its imported
   records with the screenshot; add anything missing. Keep the website and
   mail records **DNS only** (grey cloud) for now.
3. Bluehost > Name Servers > Custom: the two Cloudflare name servers.
4. Check (after Cloudflare says "Active"): kloudc.com opens, an email to
   support@kloudc.com arrives, an email from it is delivered.

Undo: set the Bluehost name servers back to Bluehost's.

## 3. The tunnel: app.kloudc.com -> this Mac *(owner, sudo once)*

A new Cloudflare Tunnel just for KloudC RetailOps. The other tunnel already
running on this Mac is never touched: no `cloudflared service install`, no
`cloudflared tunnel login`, nothing in `~/.cloudflared`.

1. Cloudflare > Zero Trust > Networks > Tunnels > Create a tunnel >
   Cloudflared > name `kloudc-retailops`.
2. Public hostname: `app` . `kloudc.com` -> Service `HTTP` `127.0.0.1:8900`.
3. The dashboard shows an install command with a long token (it starts
   with `eyJ`). **Do not run that command** - it would replace the other
   tunnel's service. Copy only the token into
   `<install>/secrets/tunnel.token` (one line), then
   `chmod 600 <install>/secrets/tunnel.token`.
4. `sudo <install>/app/deploy/pilot-mac/install-tunnel.sh`
5. Cloudflare settings for kloudc.com: SSL/TLS **Full**; Always Use HTTPS
   on; a Cache Rule "bypass cache" for `app.kloudc.com/api/*`; Rocket
   Loader **off**; Email Address Obfuscation **off** (both rewrite pages).
6. Check: `https://app.kloudc.com/api/health` shows `{"status":"ok"}`;
   `install-tunnel.sh --status` shows it running; the Tailscale address
   still works.

Undo: `sudo <install>/app/deploy/pilot-mac/install-tunnel.sh --uninstall`
(app.kloudc.com stops; Tailscale keeps working). Delete the tunnel in
Cloudflare if you want it gone.

Notes: plain HTTPS only (no WebSockets), so nothing else to set. Cloudflare
ends a request after 100 s and refuses bodies over 100 MB: exports already
run in the background and uploads are far smaller.

## 4. Production settings *(owner)*

1. Open `<install>/app/deploy/pilot-mac/production.env.example`; copy its
   lines into `<install>/config/pilot.env` (replace the same names, keep the
   rest). Put the Tailscale address in `CORS_ORIGINS` after
   `https://app.kloudc.com` while both are used.
2. Deploy: `<install>/app/deploy/pilot-mac/update.sh`.
3. Check: `cd <install>/app/backend && .venv/bin/python -m app.ops check-config`
   shows 0 errors and none of these warnings: PLATFORM_DOMAIN, CORS_ORIGINS,
   PUBLIC_HOST, TERMS_URL, BACKUP_OFFSITE_DIR, TOAST_ANALYTICS.
4. Sign in on https://app.kloudc.com as a master: two-step sign-in is
   required (PLATFORM_REQUIRE_TOTP=true).

Undo: restore the previous `pilot.env` lines and run update.sh again.

Later, when the Apple enrolment is done: `APPLE_TEAM_ID` (universal
links); when the KloudC Firebase project exists: `FIREBASE_CREDENTIALS_FILE`.

## 5. Off-site backup *(owner)*

Nightly backups stay on this Mac today. With `BACKUP_OFFSITE_DIR` set (step
4 sets it to an iCloud Drive folder), each nightly backup also writes an
**encrypted** copy there (`retailops-<date>.krbak`, the newest 14 kept).

1. After the first night: copy `<install>/secrets/offsite-backup.key` into
   your password manager. Without it the copies can't be opened.
2. Check (once): `.venv/bin/python scripts/backup/offsite_copy.py open --file
   "<the newest .krbak>" --key-file <install>/secrets/offsite-backup.key
   --out /tmp/restore-check` lists the platform and store dumps.

## 6. DEMO store for App Review *(owner, one command)*

Store `DEMO` on the same server: made-up data, every login view-only,
nothing ever sent. Its data is rebuilt every night at 03:45.

1. `cd <install>/app/backend && SCHEDULER_ENABLED=false .venv/bin/python -m app.platform demo-provision`
   (about a minute).
2. Re-run `sudo <install>/app/deploy/pilot-mac/install.sh` once, so the
   nightly DEMO refresh job (`com.kloudc.retailops.pilot.demo`) is installed.
3. App Store Connect > App Review Information: store ID `DEMO`, user
   `appreview`, password = the line in `<install>/secrets/demo_review_password`.
4. Check: sign in to the app with those; try a change - it says the demo
   account can't change things.

Undo: `python -m app.platform suspend DEMO --reason "not needed"`.

## 7. Terms of Service *(owner)*

`docs/legal/TERMS_DRAFT.html` is a plain-English draft (KloudC LLC,
Connecticut). The server already shows it at https://app.kloudc.com/terms.

1. Have a lawyer read it; change what they say.
2. Optional: paste it into a WordPress page on kloudc.com (Code editor).
   Then point `TERMS_URL` there instead.
3. Put the Terms address in App Store Connect (License Agreement / app
   description) and keep the Privacy Policy address as it is.

## 8. Switch-over day *(owner + staff)*

Before: step 1 gate passed; steps 3-5 done.

1. Integrations: enter the store's **email sending** and **WhatsApp**, Test
   each.
2. Turn pilot mode off for the store (master console > store > Pilot mode,
   or `python -m app.platform pilot-mode <STORE> off`). From now on the
   store sends purchase orders, invoices and messages.
3. Staff: install the app, sign in with the store ID and their user.
4. Keep the old system running read-only for a week as a fallback; the
   daily comparison keeps running.

Rollback (same day): pilot mode on again; staff use the old system; nothing
is lost (both systems keep syncing sales from Toast).

---

## Decisions made for you

- Go-live gate: 3 clean daily comparisons after the Analytics keys, not a
  new 7-day count (the 14 days before were exact; the cause is understood).
- The tunnel is a dashboard-managed tunnel started from a token file by its
  own LaunchDaemon; a locally managed config (`tunnel/config.yml.template`)
  is there as an alternative. Never the other tunnel's files or service.
- Store links use `PUBLIC_HOST=https://app.kloudc.com` (one address for
  everything) instead of one sub-domain per store.
- First off-site target: an iCloud Drive folder (no new account); R2 or a
  disk later by changing `BACKUP_OFFSITE_DIR`.
- The Terms page is served by the server at `/terms`, so it doesn't depend
  on the WordPress site.
- The DEMO store lives on the same server (pack `demo`, upload-only POS,
  demo mode + pilot mode), rebuilt nightly at 03:45, after the backup.
