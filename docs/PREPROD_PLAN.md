# Pre-production plan (2026-10-21)

Owner decisions (final): production runs on the existing pilot install on
the Mac mini (cloud later); public address **app.kloudc.com** through a
Cloudflare Tunnel (kloudc.com DNS moves from Bluehost to Cloudflare); the app
ships under a new KloudC LLC Apple Developer account; store #1 goes live when
the daily parity report is clean again. Parity root cause and fix:
`docs/PARITY_INVESTIGATION.md`.

## 1. Gap triage (G1-G31)

Severity: **High** = blocks go-live or App Review; **Med** = visible to
users, fix now if cheap; **Low** = after go-live. Repos: S = server+website
(`kloudc-retailops`), A = app (`kloudc-retailops-app`).

| ID | Gap (short) | Sev | Repo | Must-fix |
|---|---|---|---|---|
| G1 | 401 after password change has no reason code | Low | S (+A reads it) | |
| G2 | Archived store answers 401 like unknown store | Low | S (+A) | |
| G3 | In-session `store_suspended` drops the master's message | Low | S | |
| G4 | Clover OAuth always returns to the website | Low (no Clover app yet) | S+A | |
| G5 | `pages.json` `"app": null` for 4 new pages | Low | S | cheap, do |
| G6 | `/me` has no `pos_connected` | Low | S (+A) | |
| G7 | Export has no progress % | Low | S+A | |
| G8 | Export Download link relative unless R2 | Med | S | **yes** (public URL changes) |
| G9/G19 | `pos_name` "CSV upload"; branding lacks `pos_provider` | Low | S | |
| G10 | Generic `PUT .../integrations/clover` 501 | Low | S | |
| G11 | Help POS filter misses `sections` map entries | Low | S | |
| G12 | Change-password help says other devices stay signed in | Med | S | **yes** (wrong statement) |
| G13 | Store lookup 10/min per IP | **High** | S | **yes** (store Wi-Fi on switch-over day; behind the tunnel every user shares one IP unless the client IP is read right, see 2.2) |
| G14 | `/api/item-vendors` rebuilds catalog per page | Med | S | should |
| G15/G21 | DEMO refusals lack `demo_read_only` | Med | S | **yes** (App Review uses DEMO) |
| G16/G25 | Raw validator text in 400s | Med | S | should |
| G17 | Store settings help mentions time zone | Low | S | cheap, do |
| G18 | Item ID with "/" can't be edited | Low | S | |
| G20 | `/me` capabilities advertise Clover features before connect | Med | S | should |
| G22 | Help Center technical words (sync/server/GUID/SKU) | Med | S | should (owner wants plain words) |
| G23 | Help "Submit" vs button "Submit invoice" | Low | S | cheap, do |
| G24 | Glossary "SKU" vs app "vendor code" | Low | S | cheap, do |
| G26 | Website "Inventory from Toast" dialog wording | Low | S | cheap, do |
| G27 | Bare "Not Found"/"Internal Server Error" replies | Med | S | should |
| G28 | Labels site "UPC not in catalog" | Low | S | cheap, do |
| G29 | WhatsApp reason names provider/plan/402; glossary says "Gmail" | Med | S | **yes** (internals shown to store users and reviewers) |
| G30 | Sign-in error says "store ID" when the store was found | Med | S | **yes** (first screen a reviewer sees) |
| G31 | Reorder report can't tell "new store" from "all stocked" | Med | S+A | should (the DEMO / new stores) |

**Must-fix before go-live / TestFlight**: G13 (+ client IP), G15/G21,
G8, G12, G29, G30, plus the cheap text ones G5, G17, G23, G24, G26, G28.
Everything else: after go-live unless the build track has time left.

## 2. Production readiness of the pilot install for app.kloudc.com

### 2.1 Settings (pilot `config/pilot.env`; the owner applies them with update.sh)

| Setting | Today | Production |
|---|---|---|
| `PLATFORM_PUBLIC_URL` | tailnet address | `https://app.kloudc.com` |
| `PLATFORM_DOMAIN` | unset (`retailops.invalid`) | `kloudc.com` (also the email Message-ID fallback) |
| `PUBLIC_BASE_URL` | unset | `https://app.kloudc.com` (WhatsApp file links) |
| `CORS_ORIGINS` | tailnet address | `https://app.kloudc.com` (keep the tailnet address while both are used) |
| `SESSION_COOKIE_SECURE` | true | true |
| `SESSION_COOKIE_DOMAIN` | blank | blank (host-only cookie on app.kloudc.com) |
| `DIRECTORY_CLIENT_IP_HEADER` | blank | `CF-Connecting-IP` (only after 2.2 is built) |
| `FORWARDED_ALLOW_IPS` | 127.0.0.1 | 127.0.0.1 (cloudflared and Tailscale both connect locally) |
| `PLATFORM_REQUIRE_TOTP` | blank (= on in production) | `true`, written out |
| `SIGNUP_ENABLED` | false | false until the owner opens sign-ups |
| `TERMS_URL` | blank | `https://app.kloudc.com/terms` (draft page, track P) |
| `APPLE_TEAM_ID` | blank | KloudC LLC team ID (after enrolment) for universal links |
| `FIREBASE_CREDENTIALS_FILE` | set, file not usable (push = dry run) | KloudC RetailOps Firebase service account (owner) |
| `SECURITY_HEADERS` | enforce | enforce (HSTS at Cloudflare too) |

### 2.2 Missing in code (track P)

1. **One client-IP rule.** `platform/throttle.client_ip` trusts the header
   named by `DIRECTORY_CLIENT_IP_HEADER` from any peer and takes its last
   entry; `services/checklist_public.client_ip` trusts `CF-Connecting-IP`
   from anyone. Make one helper: use `CF-Connecting-IP` only when the TCP
   peer is a trusted proxy (`FORWARDED_ALLOW_IPS`, loopback by default),
   IPv6 grouped by /64, else the peer. Use it for login, store lookup,
   support login, public checklist, signup. Test with fakes for spoofing
   from a non-proxy peer.
2. **`check-config` production warnings**: `PLATFORM_DOMAIN` still
   `retailops.invalid`; `PLATFORM_PUBLIC_URL` origin missing from
   `CORS_ORIGINS`; `TERMS_URL` blank; a Toast store without Analytics keys;
   no off-site backup target.
3. **Off-site backups**: today backups stay on this Mac only. Add an
   encrypted copy step to `run-backup.sh` (`BACKUP_OFFSITE_DIR`, default
   off; key in `secrets/`). Decision: first target is an iCloud Drive folder
   (no new account; the owner can switch to R2 later).
4. **DEMO store for App Review on the same platform**: a script/ops command
   that provisions store `DEMO` with `demo_mode` on, sample data (no origin
   names) and the `app_store_review` login, idempotent; tested on dev
   Postgres. The owner runs it on production.
5. **Terms page draft** at `/terms` (plain words, "draft - have a lawyer
   read it"), linked from sign-in once `TERMS_URL` is set.
6. **Tunnel files**: `deploy/pilot-mac/launchd/com.kloudc.retailops.tunnel.plist`
   running `cloudflared tunnel run --token-file <install>/secrets/tunnel.token`
   (its own LaunchAgent; never `cloudflared service install`, never the
   default `~/.cloudflared/config.yml` or `cert.pem`, which belong to the
   other tunnel on this Mac), plus a runbook section.
7. Parity code fixes from `PARITY_INVESTIGATION.md` section 4.

### 2.3 Other checks

- No WebSockets/SSE: everything is plain HTTPS, so the tunnel needs no
  special settings. Cloudflare cuts requests after 100 s and bodies over
  100 MB: exports are already background jobs; uploads are far smaller.
- In Cloudflare: SSL "Full", Always Use HTTPS, cache bypass for `/api/*`,
  Rocket Loader and Email Obfuscation off (they rewrite pages and clash
  with the CSP).
- **Email / WhatsApp / push**: the pilot store has only a Toast connection
  stored. Before switch-over the owner enters the store's email sending
  (PO emails, invoice to Toast) and WhatsApp in Integrations; pilot mode
  stays on until then, so nothing is sent by mistake. Push needs the KloudC
  Firebase project. There is no platform-level email (no password-reset
  mail), so nothing else to set up.
- App Review: account deletion exists in the app; the reviewer uses the DEMO
  store login (view only).

## 3. Build split (3 tracks)

**P - parity + production config + tunnel + DEMO + Terms** (server repo):
section 2.2 items 1-7; a production env example
(`deploy/pilot-mac/production.env.example`); `PILOT_RUNBOOK.md` "Go
public on app.kloudc.com" (DNS move with every existing Bluehost record
copied so the kloudc.com site and email keep working, tunnel, settings,
DEMO, switch-over steps).

**S - server/website gap fixes** (server repo): G13, G15/G21, G8, G12, G29,
G30, G5, G17, G23, G24, G26, G28; then if time: G27, G16/G25, G20, G31, G22,
G14. Additive responses only (older app builds keep working).

**A - app** (app repo): production API config committed as a non-secret
dart-define file (`PLATFORM_DOMAIN=kloudc.com`, `TERMS_URL`), so a release
build can never point at `retailops.invalid`; make sure the missing
`directory.`/`static.kloudc.com` fallbacks fail fast with the plain "Can't
reach the store" line; read the new server codes when present
(`demo_read_only`, plain sign-in error, `has_sales_data` if S ships it);
TestFlight prep: version bump (export-compliance key already in Info.plist),
review notes with the DEMO login, privacy answers, build steps for the
KloudC LLC team in `OWNER_SETUP.md`.
