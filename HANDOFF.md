# Spice Town Tech Handoff / SOP

Practical, day-to-day operating procedures for the systems running Spice Town's internal ops.
This is the "how do I keep it running / how do I ship a change / what do I do when something
breaks" document. For what each feature does and why it was built that way, see:

- `README.md` (this repo) - features, stack, setup, one-time install.
- `.claude/skills/spicetown/SKILL.md` (this repo) - the detailed technical operating manual
  (architecture rationale, every gotcha hit and fixed, session-by-session history). Read this
  before making any non-trivial code change.
- `~/spicetown-labels/DEPLOY.md` - the label-printing app's own deploy options.

## 1. System map

Three pieces, two git repos, one always-on Mac (`spicetown-server` on Tailscale):

| Piece | Repo | Runs as | Reachable at |
|---|---|---|---|
| Dashboard (this app) | `spicetown-backend` (this repo) - FastAPI + React, one process | launchd daemon `com.spicetown.backend` | `https://spicetown-server.<your-tailnet>.ts.net/` (tailnet-only, never public) |
| Label scanner/printer | `~/spicetown-labels` - Flask + gunicorn | launchd daemons `com.spicetown.labels` + `com.spicetown.labels.sync` | `https://spicetown-server.tailcc1217.ts.net:8443/` (public Funnel, no login - deliberate, see SKILL.md) |
| Mobile app (iOS/Android) | `spicetown-backend/frontend/{ios,android}` - Capacitor wrapper around the same React app | Not a running service - built and installed per-device from Xcode/Android Studio/`gradlew` | N/A - talks to the dashboard's tailnet URL over the network |

The dashboard serves its own built frontend directly (one process, one port) - there's no
separate frontend server in production. The label scanner is a fully independent app/repo/DB;
the only link between them is a card on the dashboard's Overview tab that opens the label
scanner's URL in a new tab.

## 2. Daily operations (staff-facing)

1. **Inventory upload** (Overview tab): upload Toast's daily "retail items" export every day. If a
   delivery came in, check "We received a delivery today" and pick the vendor(s) - you'll get a
   **Delivery Review** page to confirm suggested quantities into the purchase log. If stock moved
   between the shelf and back-stock Container location, use the "Container movement happened
   today" checkbox the same way (**Transfer Review**).
2. **Reorder Candidates / Supplier Projection**: check before placing an order with a vendor - pick
   items, **Build purchase order**, download the PDF.
3. **Reconciliation**: log any purchase that didn't come through Delivery Review (manual form or
   CSV/XLSX bulk upload) so shrinkage/spoilage tracking stays accurate.
4. **Label printing**: use the label scanner app (link on Overview) to scan/search an item and
   print a shelf price label. Its **Print History** (top of the scanner screen) has a date-range
   filter (Today / Last 7 days / 1 Month / 3 Months / Custom) if you need to look back at what was
   printed on a specific day.
5. **Items Sold**: check revenue/quantity for a day or range (Today / Last 7 days / 1 Month /
   3 Months / Custom, same picker as Supplier Projection).

None of the above requires touching a terminal - it's all in the two web apps.

## 3. Restarting a service

Both run as macOS launchd **daemons** (not per-login agents), so they survive reboots and crashes
on their own. You only need to restart one by hand after a **code** change (see §4) or if
something looks stuck.

```bash
sudo launchctl kickstart -k system/com.spicetown.backend
sudo launchctl kickstart -k system/com.spicetown.labels
```

If either command hangs instead of returning immediately, the job is likely sitting in launchd's
"penalty box" after a crash loop - `kickstart -k` doesn't reliably recover that. Ctrl+C it and do
a real unload/reload instead:

```bash
sudo launchctl bootout system/com.spicetown.backend
sudo launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.backend.plist
```

(swap in `com.spicetown.labels` and its own `.plist` for the labels app).

Check a service's status or logs:

```bash
sudo launchctl print system/com.spicetown.backend
tail -f ~/spicetown-backend/backend/logs/backend.log
tail -f ~/spicetown-backend/backend/logs/backend.error.log
```

## 4. Shipping a code change

**Frontend-only change** (`frontend/src/**`): rebuild, no restart needed - the backend serves
`frontend/dist/` straight from disk on every request.

```bash
cd ~/spicetown-backend/frontend
npm run build
```

**Backend change** (`backend/app/**`): needs a restart to actually load, since it's a long-running
Python process.

```bash
sudo launchctl kickstart -k system/com.spicetown.backend
```

**Labels app change** (`~/spicetown-labels/app/**`, including HTML templates - it caches compiled
templates in memory in production): restart that service too.

```bash
sudo launchctl kickstart -k system/com.spicetown.labels
```

**Committing/pushing**: each app is its own independent git repo/remote - commit and push each
separately.

```bash
cd ~/spicetown-backend && git add <files> && git commit -m "..." && git push origin main
cd ~/spicetown-labels && git add <files> && git commit -m "..." && git push origin main
```

⚠️ `spicetown-labels` has a background job (`com.spicetown.labels.sync`) that `git pull`s from
`origin/main` and re-syncs the catalog every 15 minutes on its own - anything pushed there
effectively self-deploys. Don't push half-finished work to that repo's `main`.

## 5. Mobile app (iOS/Android)

The mobile app is the same React codebase, wrapped by Capacitor - not a separate frontend to
maintain. It talks to the dashboard over the network using the tailnet URL baked into
`frontend/.env.mobile` at build time (update that one file, then rebuild, if the domain ever
changes - e.g. moving off Tailscale onto a real domain).

```bash
cd ~/spicetown-backend/frontend
npm run build:mobile        # builds + runs `npx cap sync` for both platforms
npx cap open ios            # opens Xcode
npx cap open android        # opens Android Studio
```

- **Android**: `./gradlew assembleDebug` (from `frontend/android/`) produces a real, installable
  debug `.apk` at `android/app/build/outputs/apk/debug/app-debug.apk` - no developer account
  needed, just enable "install from unknown sources" for that file on the phone.
- **iOS**: needs Xcode's own device-signing flow (a free Apple ID is enough for local installs,
  which expire after 7 days - just hit Run again in Xcode to refresh). There's no equivalent of
  Android's debug-APK shortcut for iOS without a paid Apple Developer account.
- Login uses a bearer token (`Authorization` header) stored via `@capacitor/preferences` instead
  of the web's cookie session - same `user_sessions` table on the backend either way, so there's
  nothing extra to keep in sync.
- Status as of this handoff: builds and installs cleanly on both platforms, safe-area/notch layout
  fixed; visual polish is still being iterated on based on real-device feedback - treat it as an
  actively-evolving Phase 1, not a finished product.

## 6. Backing up the database

```bash
cd ~/spicetown-backend
./backend/scripts/dump_db_to_csv.sh
```

Dumps every table to CSV under `~/spicetown-db-dumps/<timestamp>/`. `users.csv` includes bcrypt
password hashes (not plaintext, but still delete dumps after use rather than sharing them).

## 7. User accounts

No self-service password reset, by design. An admin user manages accounts from the dashboard's
user management screen (create users, reset anyone's password, grant/revoke per-tab access). A
password reset there instantly invalidates that user's existing session (server-side, not a JWT),
so a lost/stolen session can always be killed by resetting the password.

## 8. If the dashboard or label app is unreachable

1. Confirm the Mac itself is on and connected: `tailscale status` (from the Mac, or any device on
   the tailnet).
2. Check the service is actually running: `sudo launchctl print system/com.spicetown.backend` (or
   `.labels`) - look for a nonzero `state` other than `running`, or a recent crash in the logs
   (§3).
3. Try the bootout+bootstrap cycle in §3 rather than repeated `kickstart -k` if it seems stuck.
4. If Toast data looks stale: the nightly sales sync only ever covers yesterday-and-earlier and
   self-heals a missed night automatically on the next run/restart - today's numbers are always
   pulled live instead, so a "gap" for today specifically is expected, not a bug.

## 9. Known limitations (see README.md for the full list)

- No real Toast Purchasing/Receiving API access - purchase orders are documents for a human to
  send, not a system-of-record integration; reconciliation's "purchased" figure is only as
  complete as what's actually logged (manually or via Delivery Review).
- No forecasting beyond a simple moving average.
- Mobile app is a real, working Phase 1 (Capacitor wrapper, dual auth, native file share) - later
  phases (barcode scanner merge, push notifications, offline caching) are unstarted.
