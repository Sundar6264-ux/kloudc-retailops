# Security review - website ready (internal pen test, OWASP ASVS L2 style)

Date: 2026-10-20 (ship night). Scope: the integrated `main` after "Website ready:
Integrate" (`390a5a1`), server and website only (the Flutter app is out of scope).
Method: code review of every route and module added or changed since `721f846`,
plus the older authentication, session and tenancy code they lean on; attack
probes written as tests; `pip-audit` on the backend environment and `npm audit`
on the website. No real outside service was called.

Every confirmed issue is fixed on `main` with a test. Tests:
`backend/tests/platform/test_security_review.py` (PostgreSQL platform harness),
`backend/tests/test_security_review_store.py` (SQLite and PostgreSQL) and the
real-server browser check `frontend/e2e/screens/security_stepup.mjs`.

## Findings

| ID | Area | Severity | Status |
|---|---|---|---|
| SR-10 | PO PDF: vendor name was reportlab markup (server file read) | High | Fixed |
| SR-07 | Master console: stolen session could add a master or impersonate | Medium | Fixed |
| SR-08 | SSRF: store-typed hosts checked only at Test connection | Medium | Fixed |
| SR-01 | Export all data: CSV formula injection | Medium | Fixed |
| SR-11 | Vulnerable dependencies (multipart, Starlette, Pillow, pypdf) | Medium | Fixed (part deferred) |
| SR-03 | Two-step codes could be replayed inside their window | Medium | Fixed |
| SR-04 | Password change kept other sessions; no limit on guesses | Medium | Fixed |
| SR-02 | `/docs` and `/openapi.json` public in production | Low | Fixed |
| SR-05 | Failed export showed the raw error to the store | Low | Fixed |
| SR-06 | Clover webhook: forged log lines, no limit on unauthenticated calls | Low | Fixed |
| SR-09 | Export ZIP trusted object keys from the bucket | Low (defence in depth) | Fixed |

### SR-10 PO PDF: a vendor name was PDF markup (High)

`services/po_export.py` put the supplier name into a reportlab `Paragraph`
unescaped (`f"Supplier: <b>{supplier}</b>"`). reportlab markup includes
`<img src="...">`, which reads a file from the server's disk. A store admin who
named a vendor `<img src="/path/to/a/file.png"/>` got that image embedded in the
PO PDF. On the shared service that means another store's uploaded photo. It
also broke the PDF when a vendor name had `<` or `&`. Proven by a test that
fails on the old code: the PDF gained an image XObject.
**Fix:** the name is escaped like every other cell in that PDF.
**Test:** `test_po_pdf_supplier_name_cannot_embed_a_server_file`.

### SR-07 Step-up for the most sensitive master actions (Medium)

A master's session cookie (12 hours) alone could add a master (who gets a
temporary password: a lasting back door), reset another master's two-step
sign-in, create a master with a chosen password (`POST /api/platform/users`),
or start a support session that signs in as any store user. Only Delete store
data asked for a fresh two-step code.
**Fix:** `masters.check_step_up` covers all of these when the acting master has
two-step sign-in, which production requires. The code goes in the body
(`totp_code`) or the `X-Two-Step-Code` header, and each code works once
(SR-03). Console > Masters shows a "Your two-step code" field in Add, Remove
and Reset. Masters without two-step (development, tests) are not asked.
**Tests:** `test_sensitive_master_actions_need_a_fresh_code`,
`test_masters_without_two_step_are_not_asked`, and `security_stepup.mjs`
(screens below).

### SR-08 SSRF through store-typed addresses (Medium)

A store admin sets the Toast `api_base`, the WhatsApp `base_url` and the SMTP
`host`/`port`. When the value is saved, private IP literals are refused, and
names are checked after DNS only by Test connection. A name that resolves to
`10.x`, `127.x` or `169.254.x` (or was changed to one later) was still used by
the nightly syncs and by sends from the shared server. SMTP to any host and
port gave a store an internal port scanner through the error messages.
**Fix:** `app/platform/egress.py` checks the address after DNS on every
connection, on the shared service only. Toast goes through the POS clients'
request hook, WhatsApp through its base URL and SMTP before connecting (PO
send, Toast email). The POS vendors' own hosts are trusted.
**Tests:** `test_egress_refuses_private_targets_on_the_shared_service`,
`test_egress_is_off_on_a_single_store_server`.
**Residual:** a DNS answer can change between the check and the connect (DNS
rebinding). Connecting to the IP that was checked would need custom
transports. This is in Deferred.

### SR-01 CSV formula injection in Export all data (Medium)

`platform/export.py` wrote table cells as they were. Text that staff or the POS
supply (vendor names, notes, chat, item names) and that starts with `=`, `+`,
`-` or `@` runs as a formula when the owner opens the CSV in Excel or Sheets.
The report CSVs already guarded against this (`services/csv_util.csv_cell`).
The new export did not.
**Fix:** the same rule as `csv_cell`. A text cell that starts with
`= + - @ tab CR` gets a leading apostrophe, and plain numbers such as `-3` stay
numbers. `README.txt` and `manifest.json` (`formula_guard`) say so. The key
filter for secret-looking settings now also covers `pass`, `hmac`, `auth_code`
and `dsn`.
**Tests:** `test_export_csv_cells_never_run_as_formulas`,
`test_export_cell_unit_rules`, `test_export_blanks_more_secret_looking_setting_keys`.

### SR-11 Dependencies (Medium)

`pip-audit` (on the backend environment) reported 153 known vulnerabilities
across 7 packages. `npm audit --omit=dev` reported 0 for the website build;
the 7 dev-only findings (vite, esbuild, @capacitor/cli) do not ship.

| Package | Was | Now | Why it matters here |
|---|---|---|---|
| python-multipart | 0.0.9 | 0.0.32 | Form/upload parsing DoS (every upload route) |
| fastapi / starlette | 0.115.0 / 0.38.6 | 0.116.2 / 0.47.3 | Multipart form DoS, large-file spooling |
| Pillow / pillow-heif | 11.3.0 / 0.18.0 | 12.3.0 / 1.8.1 | Decoder memory-safety bugs; staff upload photos |
| pypdf | 5.1.0 | 6.21.1 | Crafted-PDF DoS (Toast emails, invoice PDFs) |
| python-dotenv | 1.0.1 | 1.2.2 | Symlink following in `set_key` (not used; pinned anyway) |
| pytest (dev) | 8.3.3 | 9.0.3 | Shared `/tmp` directory issue on test machines |

The whole PostgreSQL backend suite passed on the new pins before the review
fixes (3194 passed). The post-review runs are in "Tests" below.
**Deferred:** Starlette 1.x (Host-header URL rebuilding, path validation,
`request.form()` part limits). With FastAPI 0.150 and Starlette 1.9, 8 tests
that inspect routes fail, so the upgrade needs its own day. Here, links and
redirects are built from `PLATFORM_PUBLIC_URL`, never from the Host header, and
the server sits behind the tunnel.

### SR-03 Two-step codes could be replayed (Medium)

`totp.verify` accepted any code inside its 90-second window as often as it was
sent (ASVS 2.8.4).
**Fix:** `totp.verify_once` accepts each time step once per master and purpose
(sign-in, step-up). An older step is also refused after a newer one. It is used
for sign-in, step-up, moving two-step to a new phone and Delete store data.
Used steps are kept in the process: the web server runs one process. Enrolment
still takes the code just shown, so enrolling and then signing in straight away
keeps working.
**Tests:** `test_totp_code_is_single_use_per_purpose`,
`test_master_sign_in_code_cannot_be_replayed`.

### SR-04 Changing your password (Medium)

`POST /api/auth/change-password` left every other session of the account
signed in (ASVS 3.3.3). It also had no limit, so a stolen session could guess
the current password at speed.
**Fix:** a successful change ends every other session of that account and keeps
the current one. Five tries a minute per account. The admin "Set password" and
the masters' own change already ended sessions.
**Tests:** `test_change_password_ends_other_sessions_and_keeps_this_one`,
`test_change_password_guesses_are_rate_limited`.

### SR-02 API docs in production (Low)

The main app served `/docs`, `/redoc` and `/openapi.json` to anyone: a full map
of routes and fields. The directory service already turned them off.
**Fix:** off unless `APP_ENV` is development, dev or test (`api_docs_urls`).
**Test:** `test_api_docs_are_off_in_production`.

### SR-05 Raw export errors shown to the store (Low)

A failed export's `error` (for example a database connection message naming the
server and the database role) went to the store admin's Data & usage page.
**Fix:** the store sees "The export could not be finished. Try again, or ask
support if it fails again." Masters still see the detail.
**Test:** `test_store_sees_a_plain_export_error_masters_see_the_detail`.

### SR-06 Clover webhook (Low)

The unauthenticated verification POST wrote its `verificationCode` to the log
as it was sent, so a line break could forge log lines. There was also no limit
on unauthenticated calls.
**Fix:** only `A-Z a-z 0-9 - _` reach the log (anything else gets a 400).
Verification calls and wrong `X-Clover-Auth` calls are limited to 20 a minute
per address.
**Tests:** `test_webhook_verification_code_cannot_forge_log_lines`,
`test_webhook_wrong_auth_is_rate_limited`.

### SR-09 ZIP entry paths (Low, defence in depth)

The export wrote `files/<key>` for every object the storage listed. Keys written
through `app.storage` are always checked, but an object put into the bucket by
something else with `../` in its key would have made a zip-slip entry.
**Fix:** keys that fail `check_key` are left out and logged.
**Test:** `test_export_zip_never_holds_a_path_outside_files`.

## Checked and found sound

- **Tenancy and IDOR.** Store routes take the store from the session, never
  from the request. Exports, links, usage and Clover routes all go through
  `current_store_code()`, and a link for another store's export is a 404.
  `tests/platform/test_isolation_attacks.py` and `test_data_ownership.py`
  cover canary crawls, forged token prefixes, header and query overrides,
  IDs that only another store has, and scope crossing. Job payloads carry only
  IDs; the worker reads the store from the job row.
- **Export download link.** 32-byte random token, stored only as its sha256,
  compared in constant time, expires within `EXPORT_LINK_HOURS` (never after
  the export), and a new link ends the old one. The answer is the same 404 for
  bad, expired and used links. Limited to 30 a minute per address and audited.
  The access log records the path without the query (`--no-access-log` on
  uvicorn, path-only middleware log).
- **Presigned R2 links.** These are scoped to one key and carry a sanitized
  `Content-Disposition`. They can't be withdrawn early; this is already in
  Deferred, and `STORAGE_PRESIGN=false` streams every download through the
  server instead.
- **Clover OAuth.** The `state` is sealed with AES-GCM and names the store and
  the admin. It lasts 10 minutes, its nonce works once, and a forged or
  replayed state is refused. The callback's redirect target is fixed, so there
  is no open redirect. Merchant IDs are checked against a pattern, and region
  names map to a fixed host table, so the region can't be used for SSRF.
- **Sessions.** Every sign-in creates a new random token (no fixation), and
  only its sha256 is stored. Tokens are bound to their store. The cookie is
  HttpOnly, Secure in production and SameSite=Lax. Signing out deletes the
  session on the server. Lockout: 5 failures per user or 30 per address in 15
  minutes, kept in the platform database. Master sign-in always runs bcrypt,
  so the reply time does not show whether a username exists.
- **Injection.** New SQL is parameterised. Identifiers are quoted with the
  dialect's preparer (export, `DROP DATABASE`), and the job list filters use
  bound parameters.
- **XSS.** The website has no `dangerouslySetInnerHTML` or `innerHTML`. Help
  links allow only `https:`, `mailto:` and internal targets, and help content
  is repository JSON. Store and vendor names are rendered as React text.
  Server HTML pages escape everything they insert.
- **Headers.** CSP with hashed inline scripts, `frame-ancestors 'self'`,
  `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP, and HSTS in
  production. CORS uses a credentialed allow-list, and the production checks
  refuse `*` and `null`.
- **Secrets.** The credential vault is AES-GCM. Exports leave out session,
  token and credential tables and secret-looking columns and keys. Storage
  status never shows keys. Error answers carry only a request ID.
- **Uploads.** The item-vendor CSV import is capped at 5 MB, one file and three
  fields. Storage keys refuse `..`, `\` and absolute paths.

## Deferred (with reason)

- **Starlette 1.x / FastAPI 0.150**: breaks 8 route-introspection tests. It
  needs its own upgrade window. See the SR-11 mitigation.
- **DNS rebinding between the egress check and the connect (SR-08)**: closing
  it needs pinned-IP transports for httpx and smtplib. The check already
  stops a name that stays private.
- **Two-step "used step" memory is per process**: correct for the one web
  process deployed today (`deploy/pilot-mac`, `docker/entrypoint.sh`). Several
  web processes would need it in the platform database (an additive table).
- **Clover OAuth `state` is not bound to the browser session**: the sealed state
  binds the store and the admin, so a swapped code can only connect the
  attacker's own merchant to the attacker's own store. Binding it to a cookie
  can come with the Clover app go-live.
- **Store sessions last 14 days with no idle timeout**: this is what the phone
  app needs. Revisit with the app (refresh tokens).
- **`POST /api/jobs/sales-sync/run` is open to any signed-in user**: this
  predates the review and is used by the app. It is limited by the POS rate
  limiter. Make it admin-only once the app no longer calls it for staff.

## Owner actions

- Ship: install the new pins (`pip install -r backend/requirements.txt`) on the
  pilot during deploy. Pillow 12 and pillow-heif 1.8 install from wheels.
- Masters who use the console API directly (curl) now send their current code
  as `totp_code` or `X-Two-Step-Code` for add/remove/reset master, create
  master and support sessions.

## Tests

| Run | Result |
|---|---|
| New security tests | 13 platform (PostgreSQL) + 4 store (SQLite and PostgreSQL), all pass; the PDF test fails on the old code |
| Backend, SQLite, full (review fixes, old pins) | 3201 passed, 27 skipped, 1 failed: the known backup-drill flake under load, which passes alone (8 of 8) |
| Backend, PostgreSQL, full (review fixes, new pins) | 3211 passed, 18 skipped, 0 failed |
| Backend, SQLite, full (review fixes, new pins) | 3202 passed, 27 skipped, 0 failed |
| Website node tests | 370 of 370 (367 pass, 3 skipped) |
| `vite build` | OK; largest chunk 434.7 kB |
| `security_stepup.mjs` (real server, 1440 light and 375 dark) | 6 shots, 0 problems |
