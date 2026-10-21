# Go-live runbook: app.kloudc.com and store #1

Plain steps for the owner, **in this order (A to G)**. Each step says what
to do, how to check it worked, and how to undo it. Commands are typed (or
pasted) into **Terminal** on the Mac mini, as you - only lines that start
with `sudo` ask for your Mac password. `<STORE>` is the store's ID
(store #1's ID is in the handoff file).

Status: **checked in a dry run, 2026-10-21**. A copy of the release ran with
exactly these production settings behind a stand-in for Cloudflare, and
every check passed (see "What the dry run checked" at the end).

| Step | What | Who | Time | Blocks |
|---|---|---|---|---|
| A | Update the pilot to this release | owner | 10 min | everything below |
| B | Sales double-check: keys, then 3 clean daily reports | owner | 2 min + 3 days | F |
| C | kloudc.com DNS to Cloudflare, the tunnel, production settings | owner | 1 h + waiting | D, F, G |
| D | DEMO store for App Review | owner | 2 min | G (review) |
| E | Terms of Service page on kloudc.com | owner (+ lawyer) | 15 min | G (App Store) |
| F | Store #1 go-live day | owner + staff | 1 h | - |
| G | TestFlight (KloudC LLC Apple account) | owner | 1-3 weeks (Apple) | staff on the app |

B's three days and G's Apple enrolment are waiting time: start G step 1
(enrolment) today, and do C, D and E while B's reports come in.

One helper is used throughout: `retailops.sh` runs a server command with the
install's settings loaded (no need to load anything by hand).

---

## A. Update the pilot to this release *(owner, 10 minutes, no sudo)*

1. Back up, then update (copy all four lines):

   ```
   ~/kloudc-retailops/app/deploy/pilot-mac/run-backup.sh backup
   ~/kloudc-retailops/app/deploy/pilot-mac/update.sh main
   curl -s http://127.0.0.1:8900/api/ready
   tail -5 ~/kloudc-retailops/logs/error.log
   ```

2. Check:
   - `update.sh` ends with `ready`; `/api/ready` says `"status":"ready"`
     and `"active_stores":"1"`.
   - `error.log` has no new lines with today's time.
   - `~/kloudc-retailops/app/deploy/pilot-mac/retailops.sh ops check-config`
     says 0 errors. Warnings about Toast Analytics, the public address and
     the off-site backup are expected until B and C.
   - Master console > Stores: the store still shows **pilot mode**.

This release changes no database tables. It adds the daily sales re-check
(works even without Toast Analytics keys), the tunnel and DEMO scripts, and
the fixes from the dry run.

Undo: `~/kloudc-retailops/app/deploy/pilot-mac/update.sh <previous release>`
(the short code `update.sh` printed as "now at" the last time). To come back:
`update.sh main`.

## B. Sales double-check and the daily comparison *(owner)*

**What was wrong.** The daily comparison (07:30) found days that differed
from the current system. Cause: Toast changes closed days later (an item
added to a closed check, a late discount, a late payment). The current
system re-checks the last 14 days with its **Toast Analytics keys**; the
pilot store has no such keys, so it never re-checked. Nothing in the sales
sync itself was wrong. Details: `docs/PARITY_INVESTIGATION.md`.

**What to do.**

1. After step A the pilot re-checks the last 7 days once a day even without
   the keys. The keys make it catch every edit within hours, like the
   current system, so enter them: website > **Store setup > Integrations >
   Toast** > **Analytics client ID** and **Analytics client secret** (the
   same pair the current system uses) > Save > **Test**.
2. Check: Items Sold > **Sales accuracy** no longer says the keys are
   missing.

**How to read the daily comparison.** Every morning after 07:30:

```
tail -5 ~/kloudc-retailops/logs/parity-<STORE>.jsonl | ~/kloudc-retailops/app/backend/.venv/bin/python -c 'import sys,json; [print(r["at"][:10], "CLEAN" if r["ok"] else "DIFFERENT on " + ", ".join(r["days_with_differences"]), *(r.get("problems", []) + r.get("warnings", []))) for r in map(json.loads, sys.stdin)]'
```

One line per morning: the date, then **CLEAN**, or **DIFFERENT on** and
the sales days that differ, then any plain warning. The first report after
step A may still list old days; the next one should be clean.

**Go / no-go rule (decision):** go live (step F) after **3 CLEAN mornings
in a row** with no warning. A DIFFERENT morning starts the count again. If
the same day stays DIFFERENT for 2 mornings, or a warning says "has not
run", don't go live: send the output above to support.

Undo: nothing to undo (the keys only read from Toast).

## C. kloudc.com on Cloudflare, the tunnel, production settings *(owner)*

### C1. Move the kloudc.com DNS to Cloudflare (website and email keep working)

Only the DNS moves. The website (WordPress) and email stay at Bluehost.

1. **Bluehost** (bluehost.com > log in) > **Domains** > kloudc.com >
   **DNS**. Take screenshots of **every** record: A, AAAA, CNAME, **MX**,
   and TXT (SPF `v=spf1`, DKIM, DMARC `_dmarc`, any verification). These
   keep the website and email working.
2. Same page: if **DNSSEC** is on, turn it **off** (Cloudflare turns its own
   on later). Wait 1 hour if you had to turn it off.
3. **Cloudflare** (dash.cloudflare.com, make a free account if needed) >
   **Add a domain** > `kloudc.com` > "Quick scan for DNS records" >
   **Free** plan.
4. Compare Cloudflare's list with your screenshots and add anything
   missing. Set the **website and email records to "DNS only"** (grey
   cloud): `kloudc.com`, `www`, `mail`, `webmail`, `ftp`, `cpanel` and
   anything else from Bluehost. MX and TXT records are always DNS only.
5. Cloudflare shows **two name servers** (like `xxx.ns.cloudflare.com`).
   Bluehost > Domains > kloudc.com > **Name Servers** > **Use custom name
   servers** > enter the two > Save.
6. Wait for Cloudflare's e-mail "kloudc.com is now active" (minutes to a
   few hours). Check: kloudc.com opens; an e-mail **to**
   support@kloudc.com arrives; an e-mail **from** it is delivered.

Undo: Bluehost > Name Servers > back to Bluehost's defaults.

### C2. The KloudC tunnel: app.kloudc.com -> this Mac (the ONE sudo command)

A new tunnel only for KloudC RetailOps. The other tunnel already running on
this Mac is never touched. **Never run** `cloudflared service install` or
`cloudflared tunnel login`, and never change anything in `~/.cloudflared`.

1. Cloudflare > **Zero Trust** > Networks > **Tunnels** > Create a tunnel >
   **Cloudflared** > name `kloudc-retailops` > Save.
2. The next page shows an install command with a long token starting with
   `eyJ`. **Do not run that command.** Select and copy **only the token**
   (from `eyJ` to the end).
3. In Terminal (this saves the copied token into the install's secrets
   folder, readable only by you):

   ```
   pbpaste > ~/kloudc-retailops/secrets/tunnel.token && chmod 600 ~/kloudc-retailops/secrets/tunnel.token
   ```

4. Back in Cloudflare > Next > **Public hostname**: subdomain `app`, domain
   `kloudc.com`, path empty, Service **HTTP** `127.0.0.1:8900` > Save.
5. The one sudo command:

   ```
   sudo ~/kloudc-retailops/app/deploy/pilot-mac/install-tunnel.sh
   ```

6. Cloudflare > kloudc.com > **SSL/TLS** > **Full**; SSL/TLS > Edge
   Certificates > **Always Use HTTPS** on; Speed > **Rocket Loader off**;
   Scrape Shield > **Email Address Obfuscation off**; Caching > Cache Rules
   > Create rule "API never cached": hostname equals `app.kloudc.com` and
   URI path starts with `/api/` > **Bypass cache** > Deploy.
7. Check:

   ```
   curl -s https://app.kloudc.com/api/health
   sudo ~/kloudc-retailops/app/deploy/pilot-mac/install-tunnel.sh --status
   ```

   The first says `{"status":"ok"}`, the second `state = running`. The
   Tailscale address still works, and so does the site behind the other
   tunnel. In Cloudflare > Tunnels, `kloudc-retailops` is **Healthy**.

Undo: `sudo ~/kloudc-retailops/app/deploy/pilot-mac/install-tunnel.sh --uninstall`
(app.kloudc.com stops; Tailscale keeps working). Delete the tunnel in
Cloudflare if you want it gone.

Good to know: plain HTTPS only (no WebSockets), so nothing else to set.
Cloudflare ends a request after 100 s and refuses uploads over 100 MB;
exports run in the background and uploads are far smaller.

### C3. Production settings, then restart

1. Keep a copy of today's settings, then add the production settings at
   the end of the settings file (later lines win; the Tailscale address
   stays allowed next to app.kloudc.com):

   ```
   cp ~/kloudc-retailops/config/pilot.env ~/kloudc-retailops/config/pilot.env.before-production && cat ~/kloudc-retailops/app/deploy/pilot-mac/production.env.example >> ~/kloudc-retailops/config/pilot.env
   ```

   (What each line does is written next to it in that file.)

2. Restart with the new settings (it checks them first and refuses to
   restart if something is wrong):

   ```
   ~/kloudc-retailops/app/deploy/pilot-mac/update.sh main
   ~/kloudc-retailops/app/deploy/pilot-mac/retailops.sh ops check-config
   ```

   check-config: 0 errors. Expected warnings until later steps: no Apple
   Team ID / Firebase (G), and Toast Analytics if B1 isn't done yet.

3. **Two-step sign-in for masters.** Open https://app.kloudc.com and sign
   in as a master. The console asks you to set up two-step sign-in: scan
   the code with an authenticator app (Google Authenticator, Microsoft
   Authenticator or 1Password) and type the 6-digit number. From then on
   every master sign-in asks for a fresh number. Store logins are not
   affected.

4. **Check on a phone using mobile data** (Wi-Fi off): open
   https://app.kloudc.com, the padlock shows, sign in with a store login.
   A store sign-in link or export link you create now starts with
   `https://app.kloudc.com`.

5. **Off-site backup.** The next night (03:15) writes the first encrypted
   copy into the iCloud Drive folder "KloudC RetailOps backups". The next
   morning, check the copy is there and save the key in your password
   manager - without it the copies can't be opened:

   ```
   ls ~/Library/Mobile\ Documents/com~apple~CloudDocs/KloudC\ RetailOps\ backups
   cat ~/kloudc-retailops/secrets/offsite-backup.key
   ```

   The first line lists a `retailops-<date>.krbak` file. If it lists
   nothing, `tail ~/kloudc-retailops/logs/backup.log` says why (macOS may
   keep a night job out of iCloud Drive: "Operation not permitted"); the
   nightly backups on this Mac keep running either way - tell support.

Undo:

```
cp ~/kloudc-retailops/config/pilot.env.before-production ~/kloudc-retailops/config/pilot.env
~/kloudc-retailops/app/deploy/pilot-mac/update.sh main
```

## D. DEMO store for App Review *(owner, one command)*

Store `DEMO` on the same server: made-up data, every login view-only,
nothing ever sent. Its data is rebuilt every night at 03:45 with the same
review password.

1. One line (about a minute; the `sudo` part only adds the nightly refresh
   job, nothing else is restarted):

   ```
   ~/kloudc-retailops/app/deploy/pilot-mac/retailops.sh platform demo-provision && sudo ~/kloudc-retailops/app/deploy/pilot-mac/install.sh --demo-job
   ```

2. The review password (for App Store Connect, step G):

   ```
   cat ~/kloudc-retailops/secrets/demo_review_password
   ```

3. Check: sign in on https://app.kloudc.com with store ID `DEMO`, user
   `appreview` and that password. Try a change - it says "The demo account
   can look around but can't change the store's real data."

Good to know: someone signed in to DEMO at 03:45 is signed out by the
nightly refresh and simply signs in again.

Undo: `~/kloudc-retailops/app/deploy/pilot-mac/retailops.sh platform suspend DEMO --reason "not needed"`.

## E. Terms of Service on kloudc.com *(owner, after a lawyer's read)*

The draft (KloudC LLC, Connecticut) is `docs/legal/TERMS_DRAFT.html`. The
server already shows the same text at https://app.kloudc.com/terms, which
is what step C3 points to until this step is done.

1. Have a lawyer read it; make their changes in the WordPress page below.
2. Copy the page to the clipboard:

   ```
   pbcopy < ~/kloudc-retailops/app/docs/legal/TERMS_DRAFT.html
   ```

3. kloudc.com/wp-admin > **Pages > Add New Page**. Title: `Terms of
   Service`. Top-right **⋮ (Options) > Code editor**, paste, then replace
   `[date of publishing]` with today's date. Back to **Visual editor** to
   look it over.
4. Page settings (right side) > **URL / Permalink**: `kloudc-terms-of-service`
   > **Publish**. Open https://kloudc.com/kloudc-terms-of-service/ to check.
5. Point the app and website at it, then restart:

   ```
   echo "TERMS_URL='https://kloudc.com/kloudc-terms-of-service/'" >> ~/kloudc-retailops/config/pilot.env
   ~/kloudc-retailops/app/deploy/pilot-mac/update.sh main
   ```

6. Check: the sign-in page's **Terms** link opens the kloudc.com page (the
   app reads the same setting from the server, no new build needed).
7. App Store Connect: put the same address in the app description (Terms
   of Use) or License Agreement; the Privacy Policy address stays
   `https://kloudc.com/kloudc-privacy-policy/`.

Undo: `echo "TERMS_URL='https://app.kloudc.com/terms'" >> ~/kloudc-retailops/config/pilot.env`
and `update.sh main` (the last line wins).

## F. Store #1 go-live day *(owner + staff, about 1 hour after closing)*

Before: B shows 3 CLEAN mornings; C done (app.kloudc.com works on a phone).
The app can come later (G): staff use the website until then.

The exact commands for store #1 - pausing the current system's sends,
the last data copy, turning pilot mode off - are in the handoff file,
section "Pre-production done - go-live steps", part F. In short:

1. Back up: `~/kloudc-retailops/app/deploy/pilot-mac/run-backup.sh backup`.
2. Pause the current system, copy its latest data (invoices, checklists,
   orders entered during the pilot) into the store, then turn the current
   system's sends off (it keeps running for the label dashboard).
3. Website > Store setup > **Integrations**: enter the store's **email
   sending** and **WhatsApp**, press **Test** for each.
4. Pilot mode off - from now on the store sends purchase orders, invoices
   and messages:

   ```
   ~/kloudc-retailops/app/deploy/pilot-mac/retailops.sh platform pilot-mode <STORE> off
   ```

5. Staff sign in at **https://app.kloudc.com** with Store ID `<STORE>` and
   their usual username and password. After TestFlight (G), the same in the
   app.

**First week (5 minutes a day):** purchase orders and invoices arrive at
the vendors (ask one vendor on day 1); WhatsApp and email Test still pass;
Items Sold > Sales accuracy has no warning; `tail -5
~/kloudc-retailops/logs/error.log` has nothing new; the backup e-mail /
`~/kloudc-retailops/logs/backup.log` says OK each morning; staff can sign
in.

**Rollback (any time in the first weeks):** pilot mode on again
(`retailops.sh platform pilot-mode <STORE> on`), turn the current system's
sends back on, staff go back to the old app. Nothing is lost; anything
entered only in the new system after go-live stays there - note those few
items by hand.

## G. TestFlight for the KloudC LLC Apple account *(owner)*

The full steps are in the app repo's `docs/TESTFLIGHT.md`; in order:

1. **Enrol KloudC LLC** (1-3 weeks): free D-U-N-S number, a company Apple
   Account with two-factor on, developer.apple.com/programs/enroll >
   Enroll as an organization. Note the **Team ID** when approved.
2. **App ID** `com.kloudc.retailops` with Push Notifications and
   Associated Domains; an **APNs key** (.p8, download once, keep in your
   password manager).
3. **App Store Connect** > New App "KloudC RetailOps", bundle
   `com.kloudc.retailops`, SKU `kloudc-retailops-ios`; App Privacy answers
   (the table in TESTFLIGHT.md section 5).
4. Optional now, needed for push: a **Firebase** project (TESTFLIGHT.md
   section 6).
5. **Xcode** > Runner > Signing & Capabilities > Team **KloudC LLC** (once).
6. Build and upload (app folder):

   ```
   cd ~/storeops/storeops-app && flutter clean && flutter pub get && tool/build_release.sh
   ```

   Then drag `build/ios/ipa/*.ipa` into **Transporter** > Deliver.
7. Server: turn on store links that open the app:

   ```
   echo "APPLE_TEAM_ID='<your Team ID>'" >> ~/kloudc-retailops/config/pilot.env
   ~/kloudc-retailops/app/deploy/pilot-mac/update.sh main
   ```

8. TestFlight > Internal Testing > group `KloudC team` > add testers >
   select the build. App Review information: store ID `DEMO`, user
   `appreview`, the password from step D (notes text: TESTFLIGHT.md
   section 11).

Every new upload needs a higher build number (`1.0.0+2`, `1.0.0+3`, ...).

---

## Decisions made for you

- The steps are ordered A to G by what each one unblocks; waiting time (B's
  three mornings, Apple's enrolment) runs alongside C, D and E.
- One helper, `deploy/pilot-mac/retailops.sh`, runs every server command
  with the install's settings loaded, so no step asks you to load settings
  by hand (a command run without them would look at the wrong database).
- The production settings are **appended** to `pilot.env` (bash loads it,
  later lines win) instead of edited by hand, with a copy kept for undo;
  the Tailscale address stays allowed automatically.
- The DEMO refresh job is installed on its own (`install.sh --demo-job`)
  instead of re-running the whole installer, so nothing else restarts.
- The Terms move to a WordPress page only after the lawyer's read; until
  then `TERMS_URL` points at the server's own copy.
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

## What the dry run checked

On 2026-10-21 a copy of the release (`git archive` of `main`) ran on this
Mac's test database server with the settings of step C3, behind a small
local stand-in for Cloudflare that adds the visitor's address
(`CF-Connecting-IP`), the way the tunnel will. The live pilot was not
touched. All of these passed:

- Masters: sign-in without two-step set-up opens nothing but the set-up;
  after it, sign-in needs the 6-digit code.
- Lockout: 30 wrong passwords from one visitor lock that visitor only;
  another visitor signs in normally. A visitor can't fake its address
  (Cloudflare overwrites it). Lockouts, the audit log and now the server's
  request log record the visitor's address, not the tunnel's.
- Links: store sign-in links, owner set-up links and data export download
  links all start with `https://app.kloudc.com`.
- Export: progress goes 0 -> 100 and the file downloads.
- DEMO: made in 16 seconds, the review login reads but every change is
  refused with the plain demo line, the nightly refresh runs in 15 seconds
  and keeps the review password.
- Daily comparison: on a made-up store with the late Toast edits seen in
  the pilot, the comparison showed the differences before the daily
  re-check and 0 after it.
- Terms page at `/terms`, the website with its security headers, the
  Connect with Clover hand-back page, and an unknown address answering
  "Not Found" (older app builds need those words).
- Tunnel: `install-tunnel.sh --dry-run` renders its own LaunchDaemon and
  config (token mode) and nothing else; the installed cloudflared
  (2026.10.1) supports `--token-file`.
- App (simulator, same server): see the app repo's `docs/APP_PHASE.md`,
  "As built - Pre-production: Verify".

Good to know: a person signed in to the DEMO store at 03:45 (Eastern) is
signed out by the nightly refresh and simply signs in again.
