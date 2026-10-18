# Owner checklist: Apple, Google and Firebase records for StoreOps

Batch MT13 (plan 11.3, contract section 9.1). **None of these records has
been created** - each needs the owner's own accounts, payment or legal
identity. Every value the code needs is already a setting or build define
with a placeholder; this list says where each answer goes.

Working decisions (change any of them before step A1; afterwards they are
hard to change): product name **StoreOps**, company **KloudC LLC**, bundle /
application id **`com.kloudc.storeops`**. The existing single-store app record
is not touched (plan D10).

## A. Apple (developer.apple.com and App Store Connect)

1. **App ID** - Certificates, Identifiers & Profiles -> Identifiers -> "+" ->
   App IDs -> App. Description `StoreOps`, Bundle ID (explicit)
   `com.kloudc.storeops`. Capabilities: **Push Notifications** and
   **Associated Domains**. (Sign in with Apple is not used.)
2. **Team ID** - Membership details -> Team ID (10 characters). Put it in
   every store server's `.env` as `APPLE_TEAM_ID=` (the directory serves it
   in `apple-app-site-association`, contract 4.4) and in the app's Xcode
   project signing (MT7a app identity).
3. **Push key** - Keys -> "+" -> Apple Push Notifications service (APNs).
   Download the `.p8` once, keep it outside git (`.gitignore` refuses
   `*.p8`), and upload it to the StoreOps Firebase project (C3), not to any
   server.
4. **App Store Connect record** - Apps -> "+" -> New App: platform iOS,
   name `StoreOps` (if taken: "StoreOps for Retail Teams" or similar - the
   home-screen name can stay StoreOps), primary language English (U.S.),
   bundle ID `com.kloudc.storeops`, SKU e.g. `storeops-ios-1`, user access
   Full.
5. **App Store URL** - after the record exists, copy the app's URL
   (`https://apps.apple.com/app/id<number>`) into each store server's
   `APP_STORE_URL=`.
6. **App Privacy** - answer from the KloudC privacy policy; the data types
   are the same as the previous app's answers (account name, user content:
   photos/documents/messages, device ID for push; no tracking). Add: "Other
   data shared with a third party: questions to Ask Inventory Bot go to
   Anthropic" for stores with AI on.
7. **App Review Information** - from `docs/APP_REVIEW_NOTES.md` (store code
   `DEMO`, user `appreview`, the DEMO password). The DEMO store must be live
   first (`docs/DEMO_STORE.md`).
8. **Age rating, category** - Business (primary), Productivity (secondary).
9. **Unlisted distribution (fallback)** - only if review says the app is too
   narrow for the public store: request it at
   https://developer.apple.com/support/unlisted-app-distribution/ .

## B. Google Play (play.google.com/console)

1. **Create app** - name `StoreOps`, default language English (US), App,
   Free. Package name comes from the first upload: `com.kloudc.storeops`.
2. **Play App Signing** - accept Google-managed signing. Then App
   integrity -> App signing -> copy the **SHA-256 certificate fingerprint**
   of the app signing key (and of the upload key) into every store server's
   `ANDROID_CERT_SHA256=` (comma-separated; served in `assetlinks.json`,
   contract 4.4).
3. **Upload key** - keep the upload keystore outside git (`.gitignore`
   refuses `*.p12`, `*.key`, `*.pem`); note its passwords in the owner's
   password manager only.
4. **App access** - "Restricted": the DEMO instructions from
   `docs/APP_REVIEW_NOTES.md`.
5. **Data safety** - same answers as Apple A6.
6. **Play URL** - nothing to do: `PLAY_STORE_URL` defaults to
   `https://play.google.com/store/apps/details?id=com.kloudc.storeops`.

## C. Firebase (console.firebase.google.com) - push notifications

1. **New project** `StoreOps` (Google Analytics: off). Do not reuse the
   previous app's Firebase project.
2. **Add apps** - iOS with bundle ID `com.kloudc.storeops`; Android with
   package `com.kloudc.storeops` and the SHA-256 fingerprints from B2.
3. **APNs** - Project settings -> Cloud Messaging -> Apple app
   configuration -> upload the `.p8` key from A3 with its Key ID and the
   Team ID.
4. **App config** - in `storeops-app`, run `flutterfire configure
   --project=<storeops project id>`; it rewrites
   `lib/core/notifications/firebase_options.dart` (the placeholder from MT7a
   app identity). The generated `GoogleService-Info.plist` /
   `google-services.json` stay out of git (`.gitignore`); CI gets them from
   repository secrets.
5. **One service account per store server** - IAM -> Service accounts ->
   create `storeops-push-<store>` with only the role **Firebase Cloud
   Messaging API Admin** (plan 7). Create a JSON key, copy it to that
   store's server only, and set `FIREBASE_CREDENTIALS_FILE=` to its path.
   The **DEMO store gets no key** (its pushes never leave the server).

## D. Domain, links and legal (contract 9.1)

1. **Domain** - choose it, then set `PLATFORM_DOMAIN=` on every server and
   the app's `--dart-define=PLATFORM_DOMAIN=` (never a host under the old
   store's domain - the server refuses one at startup).
2. **DNS / Cloudflare** - `directory.`, `static.`, the bare domain (for
   `/s/<CODE>` links) and one host per store, including `demo.` for the DEMO
   store (port from `deploy/demo/`).
3. **Terms** - if KloudC publishes terms of use, set `TERMS_URL=` on every
   server; the website, About screen and login footer show the link only
   when it is set.
4. **Privacy policy** - the KloudC page (`PRIVACY_URL`, already the default)
   must mention the AI provider (Anthropic) for stores that turn AI on, and
   that each store's data is kept on that store's own server.

## E. Order of work

1. A1-A2, B1-B2, C1-C3 (accounts and ids).
2. MT7a app identity commits the bundle id / icons; C4 `flutterfire
   configure`; CI secrets for the Firebase app files and signing.
3. D1-D2 and the DEMO store live (`docs/DEMO_STORE.md`); smoke-test sign-in
   with store code `DEMO` on a phone.
4. A4-A8 and B4-B5; paste the review notes; submit.
