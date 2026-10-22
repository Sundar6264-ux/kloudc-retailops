# Label printing (store feature) - implementation contract

Status: contract, not built yet (2026-10-22). Three builders work from it at
the same time: **L** (the existing labels site), **S** (KloudC server and
website, this repo) and **A** (KloudC app, `storeops-app`). Section 9 says
who owns which file.

Words used here:
- **Label printing**: the store feature people see. It covers scan a barcode,
  look a product up, see the label, print it, print history, and make a label
  for a product that isn't in the catalog.
- **The existing labels site**: the Flask service that already does this for
  the pilot store. It runs on the pilot Mac on `127.0.0.1:8080`, drives the
  store's one label printer, and today serves only the legacy domain
  (`labels.<blocked-domain>`, rule 0.3 of M2_CONTRACT.md).
- **`<PILOT>`**: the pilot store's code. Real values (the code, its host
  label, launchd job names) are in the private pilot notes, never in git
  (name guard: `backend/tests/test_no_origin_names.py`).

## 0. Why, and the decisions in one place

The KloudC app already has the whole Scan tab (`lib/features/scanner`). It
was copied from the older app. The pilot store still can't use it, for three
reasons:
1. `labels_url` is not set for `<PILOT>`.
2. The only address the labels site has is under `<blocked-domain>`, and
   rule 0.3 refuses that address in three places: server start-up, the
   settings merge, and the app's `ApiHostPolicy`.
3. The labels site accepts only sessions from the older system. It reads
   that system's own sessions table. A KloudC token (`<CODE>.<secret>`, kept
   as sha256 in the store's Postgres) gets 401 on every call.

Decisions:

| # | Decision |
|---|---|
| D1 | Label printing becomes a real per-store feature: on/off, an address, and an options object for later choices. A master turns it on. Stores that don't have it can **ask** for it. |
| D2 | Rule 0.3 stays. The existing labels site gets a **second** public address under the platform domain, `https://labels-<host label>.kloudc.com`. It reaches the same process on `127.0.0.1:8080` through the KloudC tunnel. The legacy address keeps working for the older app. (One level only: `labels-x.kloudc.com`, not `labels.x.kloudc.com`, so Cloudflare's universal certificate covers it.) |
| D3 | The labels site learns a **second** way to sign in. When a request has a KloudC token, the site asks KloudC who it is (`GET /api/labels/session`) and caches the answer for 60 s. No shared secret, nothing in the app, and nothing written to any database. The existing cookie check runs first and is unchanged, so the older app and web scanner behave exactly as today. |
| D4 | Store feature `labels` (the key in `store_features`, unchanged) is on when label printing is on **and** an address is set. The app shows the Scan tab only when both `labels` is in `store_features` and `labels_url` passes the host policy. |
| D5 | Permissions in v1: any signed-in user of a store with label printing on can look up, search, preview and see history. Printing and other changes (print, reprint, print price changes, add a product, dismiss a price change) need `can_print`, which is false for a demo login or the DEMO store. A per-user grant is reserved for later (section 2.5). |
| D6 | The website opens the labels site with a one-time **hand-off code**, never a token in a URL (section 3.4). |

---

## 1. The store feature "Label printing"

### 1.1 Settings (S: `backend/app/config.py`, `tenancy/settings.py`)

These are new per-store settings fields. They are additive, stored in the
platform `store_settings` (key/value JSON), and no store-DB migration is
needed.

| Field | Type, default | Meaning |
|---|---|---|
| `label_printing_enabled` | `Optional[bool] = None` | `True` = on, `False` = off. `None` = legacy: on exactly when `labels_url` is set, which is how every store behaves today. |
| `labels_url` | `str = ""` (exists) | The label site's origin: `https://<host>`, with no path, query or fragment. |
| `label_printing_options` | `Dict[str, Any] = {}` | For the choices that come later (section 1.5). Stored as one JSON object, so new choices never need a migration. |

- Add `label_printing_enabled` and `label_printing_options` to
  `_EXPLICIT_STORE_KEYS` and `_EXPLICIT_PRIVATE_KEYS`, like `labels_url`. They
  are never taken from the process env in multi mode.
- Single mode (one process per store) reads `LABEL_PRINTING_ENABLED` and
  `LABEL_PRINTING_OPTIONS` (JSON text) from `.env`. Add both to `.env.example`
  with blank values.

**Validation** runs on write, in both `validate_store_setting` and
`stores._validate_settings`:
- **`labels_url`**: https, origin only, the host under `PLATFORM_DOMAIN` or in
  `DIRECTORY_EXTRA_API_HOSTS`, and never `refused_host`. This is the same rule
  as `hosting.validate_api_base_url` and the app's `ApiHostPolicy`. Move the
  shared check into one helper, `app/platform/urls.py:validate_origin_url(value, field)`,
  and have hosting call it too.
  - Plain error (`plain_errors.py`): "Label site address: use https://<name>.<platform domain> with nothing after it."
  - Today a store admin can save an address that the app then refuses. That is no longer possible.
- **`label_printing_options`**:
  - must be a JSON object, at most 4096 bytes once serialised;
  - keys match `^[a-z][a-z0-9_]{0,39}$`;
  - nesting is at most 3 levels;
  - values are strings (at most 200 characters), numbers, booleans, null, lists (at most 50 items) or objects.
  - Unknown keys are **kept**, so a newer labels site can read a choice the server doesn't know yet.
- Values already stored are never rejected at merge time because of the new
  host rule. Only writes are checked. (The pilot has no value stored. The
  DEMO store's value is checked in S's tests.)

### 1.2 Derived values (S: `backend/app/store/settings_view.py`)

```python
def label_printing_enabled(s=None) -> bool        # explicit True/False, else bool(labels_url)
def label_printing_state(s=None) -> str           # "on" | "off" | "on_no_address"
def label_printing_options(s=None) -> dict        # {} when unset or invalid
store_features(): "labels": label_printing_enabled(s) and bool(labels_url(s))
```

`on_no_address` means it was turned on but no address is set yet. The master
console shows that state as a warning. `labels` is **not** listed in
`store_features` then.

### 1.3 Where it is exposed (all additive)

| Place | New keys | Notes |
|---|---|---|
| `GET /api/app/branding` (signed in) | `label_printing: {"enabled": bool, "url": str or null, "options": {...}}` | `labels_url` and `store_features` stay exactly as they are. |
| Neutral branding (before sign-in) | `label_printing: null` | Like `labels_url: null`. |
| `GET /api/auth/me` | none | `store_features` already carries `labels`. |
| `GET /api/app/config` | none | It is pre-sign-in. |
| `GET /api/label-printing` (new, any signed-in store user) | `{"enabled", "state", "url", "can_request", "request": {...} or null}` | The status the Request card shows (section 4). |
| `GET /api/labels/session` (new) | section 2.2 | For the labels site only. It is not a UI endpoint. |
| Master `GET /api/platform/stores/{code}` | `label_printing: {"state", "url", "options", "open_request_id"}` | Read by the console. |

### 1.4 Who can change what

| Who | Can |
|---|---|
| Master (console, CLI) | Turn it on or off, set the address, set options, approve or decline requests. |
| Store admin (`PATCH /api/settings/store`) | Edit `labels_url` **only when** `label_printing_state` is `on` or `on_no_address`, or is legacy with an address already set. Otherwise the answer is 409 `label_printing_off`: "Label printing isn't on for this store yet. Ask for it under Label printing." The admin can never send `label_printing_enabled` or `label_printing_options`; those keys are not in `STORE_ADMIN_SETTING_KEYS`, so the existing 400 applies. |
| Anyone else | Read only. |

Turning label printing **off** leaves `labels_url` stored, so turning it back
on needs no retyping. `store_features` drops `labels` straight away (settings
version bump), and the labels site refuses KloudC users within 60 s (its
cache, section 2.3).

### 1.5 Options reserved for "dynamic selections later"

Nothing reads these in v1. The names are reserved so the builders agree on
them:

| Key | Example | Later use |
|---|---|---|
| `printer_profile` | `"brother_62mm"` | Which printer and label roll the store has. |
| `label_size` | `"62x33.5"` | Label size in mm. |
| `variants` | `["standard","sale","clearance","shelf"]` | Which label styles the app offers. The labels site already has these: `VARIANT_STYLE`. |
| `default_variant` | `"standard"` | |
| `fields` | `["department","name","size","price","barcode"]` | Which lines print. The labels site already has these: `ALL_LABEL_FIELDS`. |
| `templates` | `[{"id":"shelf","name":"Shelf tag","variant":"shelf","fields":[...]}]` | Named presets a store picks from. |
| `version` | `1` | Bumped when a reader must know the shape changed. |

The labels site gets the options in every `/api/labels/session` answer
(`label_printing.options`), and the app gets them in branding. So later work
is a UI and labels-site change only, with no server migration.

---

## 2. Signing in: KloudC session to the labels site

### 2.1 Flow (app)

```
app --Authorization: Bearer <CODE>.<secret>--> labels site (labels-<host>.kloudc.com)
labels site: 1. existing cookie check (unchanged)  -> pass? done
             2. KloudC-shaped token?  prefix == STL_KLOUDC_STORE_CODE?  else 401 (no outside call)
             3. cache hit (sha256 of token)?  -> use it
             4. GET {STL_KLOUDC_API_BASE}/api/labels/session  (Bearer, X-Store)
                 200 -> cache 60 s, allow   401/403 -> cache "no" 30 s, refuse
                 timeout / 5xx / 429 -> stale grace (2.3) or 503
```

A **KloudC-shaped token** matches `^[A-Z0-9][A-Z0-9_]{2,23}\.[A-Za-z0-9_-]{20,}$`.
Tokens from the older system have no dot, so the two kinds can never be
confused. The site takes it from:
1. `Authorization: Bearer <token>` (new app builds);
2. otherwise the `session_token` cookie, but only when the legacy check
   failed **and** the value is KloudC-shaped. Installed KloudC app builds send
   their token this way today, so they work without an app update.

### 2.2 KloudC endpoint (S: new `backend/app/label_printing.py`)

`GET /api/labels/session`

- **Headers in:**
  - `Authorization: Bearer <CODE>.<secret>` (required);
  - `X-Store: <CODE>` (required). The existing tenancy middleware answers
    409 `store_mismatch` when it doesn't match the token's store;
  - `Accept: application/json`.
- **Auth:** the normal `get_current_user`, so expiry, sign-out, a password
  change, a suspended store and `store_moved` all behave exactly as on every
  other route.
- **200**, `Cache-Control: no-store`:

```json
{
  "store_code": "XYZ_GROCERY",
  "user": {"id": 12, "username": "maria", "display_name": "Maria"},
  "is_admin": false,
  "demo": false,
  "can_print": true,
  "label_printing": {"enabled": true, "options": {}},
  "session_expires_at": "2026-11-05T14:00:00Z",
  "cache_seconds": 60
}
```

- `can_print = not is_demo_user(user)` (D5). `demo` is `is_demo_user(user)`,
  which is already true for every login on the DEMO store.
- **Errors**, each in the existing `{"error": ..., "message": ...}` shape:

| Code | `error` | When |
|---|---|---|
| 401 | `session_expired` / `unauthorized` | No valid session. Same body as other routes. |
| 403 | `label_printing_off` | `has_store_feature("labels")` is false. |
| 403 | `store_suspended` / `store_not_ready` | Existing behaviour. |
| 409 | `store_mismatch` / `store_moved` | Existing middleware. |
| 429 | `rate_limited` | More than 120 requests a minute for one token (key: sha256 of the token), or more than 3000 a minute for one store. Uses `throttle.check_rate`. |

- It never returns the token, the token hash or any setting other than these.
  It is never logged with the token.
- It is registered in the demo-guard enumeration as **read only**.

### 2.3 Labels site side (L)

**New settings** (`config.py`, all prefixed `STL_`). Every one is off or
blank by default, so main behaves exactly as today until the owner sets them.

| Setting | Default | Rule |
|---|---|---|
| `STL_KLOUDC_AUTH_ENABLED` | `false` | Master switch. |
| `STL_KLOUDC_API_BASE` | `""` | Must match the built-in allow-list: exactly `https://app.kloudc.com`; or `http://127.0.0.1:<port>` / `http://localhost:<port>` (same machine); or `https://<name>.<tailnet>.ts.net[:<port>]`. Anything else: log one error at start-up and leave KloudC sign-in **off** (fail closed). Recommended for the pilot: `http://127.0.0.1:8900`, the KloudC server on the same Mac, with no internet hop. |
| `STL_KLOUDC_STORE_CODE` | `""` | Required when enabled. Upper case, `^[A-Z0-9][A-Z0-9_]{2,23}$`. |
| `STL_KLOUDC_LOGIN_URL` | `https://app.kloudc.com/#/label-printing/open` | Where an unsigned **page** request on a KloudC host goes. |
| `STL_KLOUDC_CACHE_SECONDS` | `60` | Capped at 120. The server's `cache_seconds` can only lower it. |
| `STL_KLOUDC_NEGATIVE_CACHE_SECONDS` | `30` | |
| `STL_KLOUDC_STALE_GRACE_SECONDS` | `300` | |
| `STL_KLOUDC_TIMEOUT_SECONDS` | `3` | Read timeout. The connect timeout is fixed at 2 s. |
| `STL_KLOUDC_MAX_CHECKS_PER_MINUTE` | `120` | Ceiling on uncached calls to KloudC. |

**New module `app/services/kloudc_auth.py`:**
- `is_kloudc_token(value) -> bool`
- `check(token) -> Identity | Refusal | Unavailable`
- Uses `requests` (already a dependency) with `allow_redirects=False`,
  `verify=True`, `timeout=(2, STL_KLOUDC_TIMEOUT_SECONDS)`, one attempt and no
  retries.
- Sends exactly:
  - `Authorization: Bearer <token>`
  - `X-Store: <STL_KLOUDC_STORE_CODE>`
  - `Accept: application/json`
  - `User-Agent: labels-site-kloudc-auth/1`
- Never sends `Cookie`, `CF-Connecting-IP`, `X-Forwarded-For` or any other
  header. KloudC trusts `CF-Connecting-IP` from 127.0.0.1.
- Accepts a 200 only when **all** of these hold:
  - the body is JSON;
  - `store_code == STL_KLOUDC_STORE_CODE`;
  - `label_printing.enabled is true`;
  - `session_expires_at` is in the future.
  Anything else counts as a refusal.

**Cache:**
- In-process dict behind a lock. Gunicorn runs `-w 1 --threads 4`.
- At most 2000 entries; the oldest is dropped first.
- Key: `sha256(token)` hex. **The raw token is never stored and never
  logged.** Logs show the first 8 characters of the hash and the username.
- Value: `{identity, checked_at, fresh_until}`, where
  `fresh_until = min(now + cache_seconds, session_expires_at)`.
- Negative entries hold only `{refused, until}`.

**Failure behaviour:**

| KloudC answer | Labels site does |
|---|---|
| 200 that passes the checks | Allow, cache it. |
| 401 | 401 `{"error":"unauthorized","message":"Sign in again to use label printing."}`. Cached "no" for 30 s. |
| 403 `label_printing_off` | 403 `{"error":"label_printing_off","message":"Label printing is turned off for this store."}`. Cached "no" for 30 s. |
| 403 / 409 other | 401 as above. |
| Timeout, connection error, 5xx, 429 | If the token was allowed within the last `STALE_GRACE` seconds, allow it (logged `kloudc_auth stale_grace`). Otherwise 503 `{"error":"auth_unavailable","message":"Can't check your sign-in right now. Try again in a minute."}`. |
| More than `MAX_CHECKS_PER_MINUTE` uncached checks | 429 `{"error":"rate_limited","message":"Too many sign-in checks. Try again in a minute."}`. No outside call is made. |

- Stale grace never applies after a 401 or 403. A sign-out or "turned off"
  always wins as soon as KloudC answers.
- A **page** request (not `/api/*`) that is refused on a host ending in
  `.kloudc.com` is redirected to `STL_KLOUDC_LOGIN_URL`. On any other host
  the existing `DASHBOARD_LOGIN_URL` redirect is unchanged.

**Gate change** (`app/__init__.py` `_register_dashboard_auth_gate`, the only
edit there):
- The existing paths stay exempt: `/healthz`, `/api/bridge*`, `/static*`.
  Add `/kloudc/enter` (section 3.4).
- The existing cookie check runs first, byte for byte as today.
- Only when it fails and `KLOUDC_AUTH_ENABLED` is on is the KloudC check
  tried. On success it sets `g.auth_source = "kloudc"` and `g.kloudc = identity`.
- The labels site never writes the older system's DB, the KloudC DBs, or new
  columns in its own DB. Print jobs are recorded exactly as today.

**Demo guard** (`app/routes/api.py`, only `_session_token` and `_demo_status`):
- When `g.auth_source == "kloudc"`, `_demo_status()` returns `DEMO` if
  `identity.demo or not identity.can_print`, else `REAL`.
- `_demo_print_refusal` and `_demo_write_refusal` then apply unchanged to
  `/api/print`, `/api/price-changes/print`, `/api/products/custom` and
  `/api/price-changes/dismiss`.
- `_session_token()` keeps its legacy meaning for the older DB path.
- Print requests log `printed_by=<username>` (log only, no DB column).

### 2.4 What is cached, summed up

| Where | What | How long |
|---|---|---|
| Labels site | Identity per sha256(token): user id, username, display name, demo, can_print, options. | 60 s (≤ 120 s, ≤ session end), plus 300 s stale grace only while KloudC is unreachable. |
| Labels site | "No" per sha256(token). | 30 s |
| Labels site | Website hand-off cookie (section 3.4). | 2 h |
| App | Nothing new. It keeps the same token in Keychain. | - |
| KloudC | Nothing new. Settings use the existing per-store settings cache. | - |

### 2.5 Later: a per-user grant

Reserved name: `label_printing`, label "Label printing", in `app/features.py`.
When it is added:
- `can_print = is_admin or has grant`;
- the same release gives the grant to every existing user, so nobody loses
  printing;
- the app and website gate print buttons on it.

It is not part of v1.

---

## 3. Website

### 3.1 Store side (S: `frontend/src`)
- **Store settings page** (`StoreSettingsPage.jsx`, `settings/storeSettingsModel.js`):
  the "Label Scanner link" field becomes a **Label printing** card with three
  states.
  - **On:** "Label printing is on." It shows the label site address (an
    admin can edit it, using the validation from 1.1) and an
    **Open label printing** button.
  - **Off, no request:** "Print shelf labels from your phone: scan a
    barcode, check the price, print." Admins get a **Request** button and
    others see "Ask your store admin."
  - **Requested:** "Asked on <date>. We'll turn it on and let you know."
    Admins also get **Cancel request**.
  - **Declined:** the master's note, plus **Ask again**.
- **Footer link** (`featureRoutes.js` `FOOTER_LINKS`): rename it to "Label
  printing". It stays gated on store feature `labels` (`store/gating.js`).
  It opens through the hand-off (3.4), not `hrefFrom: "labelsUrl"`.
- **New route `/#/label-printing/open`** (`pages/LabelPrintingOpenPage.jsx`):
  - signed in with the feature on: call the hand-off, then
    `window.location.assign(url)`;
  - signed out: the normal login, then back here;
  - feature off: the Request card.
  This is the labels site's `STL_KLOUDC_LOGIN_URL`.
- **Help:** update the `print_labels` / `scan` entries and add a "Label
  printing" glossary line in `content/help`. Plain words, no domain names.

### 3.2 Master console (S)
- **Requests tab** (`platform/platformModel.js`, `PlatformConsolePage.jsx`):
  a second list under sign-ups, **Feature requests**. Columns: store,
  feature ("Label printing"), asked by, when, note. Actions:
  - **Approve:** asks for the label site address, prefilled with the stored
    one if there is one. The address is required unless one is already stored.
  - **Decline:** asks for a short note, which the store sees.
  The tab's count badge includes open feature requests.
- **Store detail** (`platform/StoreDetail.jsx`): a **Label printing** row
  with an On/Off switch, the address, the options as read-only JSON in v1,
  the state chip (On / Off / On, no address), and a link to an open request
  if there is one.
- **Client** (`clients/console.js`): `listFeatureRequests`,
  `approveFeatureRequest`, `declineFeatureRequest`, `setLabelPrinting`.

### 3.3 Server routes for the website and app (S)

| Route | Who | Does |
|---|---|---|
| `GET /api/label-printing` | Signed-in store user | Status (1.3). `can_request = is_admin and not demo and state != "on"`. |
| `POST /api/label-printing/request` `{note?: str ≤ 1000}` | Store admin, not demo | Creates a request (section 4). 409 `already_on` / `already_requested` (the latter returns the open one). Rate limit: 5 an hour per store. |
| `POST /api/label-printing/request/cancel` | Store admin, not demo | Open request becomes `cancelled`. |
| `POST /api/labels/handoff` | Signed-in store user, feature on | Section 3.4. |
| `POST /api/labels/handoff/redeem` | The labels site (no session) | Section 3.4. |
| `GET /api/platform/feature-requests?status&store&feature` | Master | Lists requests. |
| `POST /api/platform/feature-requests/{id}/approve` `{labels_url?, note?}` | Master (`platform_write`) | Puts the settings `label_printing_enabled=true` and `labels_url`, all in one transaction with the status change. Audited: `feature_request.approve` plus the usual `store_settings.put`. |
| `POST /api/platform/feature-requests/{id}/decline` `{note}` | Master | Note required. Audited. |
| `PUT /api/platform/stores/{code}/settings` | Master | Existing route. It now accepts `label_printing_enabled`, `label_printing_options` and `labels_url`. |

### 3.4 Website hand-off (one-time code, no token in a URL)

1. The website calls `POST /api/labels/handoff`. KloudC creates a code: 32
   random bytes, URL-safe.
   - It stores only `sha256(code)` in the platform table `label_handoffs`,
     with `store_code`, the identity fields from 2.2, `expires_at = now + 60 s`
     and `used_at`.
   - It answers `{"url": "<labels_url>/kloudc/enter?code=<code>"}`.
   - Rate limit: 20 a minute per user.
2. The browser opens that URL. The labels site's `GET /kloudc/enter`:
   - is exempt from the gate and only works when `KLOUDC_AUTH_ENABLED`;
   - calls `POST {API_BASE}/api/labels/handoff/redeem` with `{"code": ...}`
     and `X-Store`, with the same timeouts and allow-list as 2.3;
   - KloudC marks the code used: single use, refused once expired or for
     another store. KloudC answers with the 2.2 body, or 410 `code_used` /
     404 `code_unknown`. Redeem rate limit: 300 a minute overall. A code has
     256 bits, so guessing one is not practical;
   - on success the labels site sets the cookie `kc_labels`:
     - signed with Flask `itsdangerous` (`STL_SECRET_KEY`, salt `kloudc-labels-v1`);
     - holds `{store, user_id, username, demo, can_print, iat}`;
     - `Max-Age=7200`, `HttpOnly`, `Secure`, `SameSite=Lax`, host-only, `Path=/`;
   - then sends `303` to `/` with `Referrer-Policy: no-referrer` and
     `Cache-Control: no-store`. A failure goes to `STL_KLOUDC_LOGIN_URL`.
3. The gate accepts a valid `kc_labels` cookie as a KloudC identity, after the
   legacy check and before the Bearer check.

When it runs out after 2 h, the next page load bounces through
`/#/label-printing/open` and comes straight back, if the person is still
signed in to the website.

Known limit, accepted: the browser cookie can outlive a sign-out or "turned
off" by up to 2 h. The app path (2.1) doesn't have this limit.

---

## 4. Request flow (stores without the feature)

In plain words: a store admin taps **Request** under Label printing. The
master sees it under Requests, adds the label site's address and taps
**Approve**, and the store has the Scan tab the next time the app opens.
Decline sends a short note back.

**Platform table**: `feature_requests`, in migration
`alembic_platform/versions/p0005_feature_requests.py`, which also creates
`label_handoffs`. The table is generic, so other features can be requested
the same way later.

| Column | Type |
|---|---|
| id | BigId PK |
| store_id | FK stores, cascade |
| feature | String(40), v1 value `label_printing` |
| status | String(16): `requested` / `approved` / `declined` / `cancelled` |
| requested_by | String(128), the store actor as hosting uses it |
| note | Text, nullable (from the store) |
| reviewed_by, reviewed_at, review_note | Like `dedicated_hosting_requests` |
| created_at | TS |

- Partial unique index: one `requested` row per `(store_id, feature)`.
- `label_handoffs`: `code_hash` String(64) PK, `store_code`, `identity` Text
  (JSON), `expires_at`, `used_at`.
- Rows older than one day are pruned by the existing `prune-logins` job, or by
  a new line in it.

State machine (S: `backend/app/platform/feature_requests.py`):

```
requested --approve--> approved      (sets label_printing_enabled=true + labels_url)
    |---------decline--> declined     (note required, shown to the store)
    |---------cancel---> cancelled    (store admin)
declined / cancelled: the store may ask again (new row)
```

- Approve on a store that is not `active`: 409.
- Approve with no stored address and none given: 400 "Add the label site
  address first."
- A master turning it on by hand (console or CLI) closes any open request as
  `approved`, with note "Turned on by <master>".
- Every step is audited.
- **No push or email in v1.** The store sees the new state on the Request
  card, and the Scan tab appears after the next branding refresh (sign-in or
  app resume).

---

## 5. Pilot store one-time setup (owner runs this; live data is the owner's)

Do this after S's server release and L's `kloudc-auth` branch are deployed by
the owner. Run each step once.

1. **Second address for the existing labels site.** Cloudflare dashboard →
   Zero Trust → Networks → Tunnels → `kloudc-retailops` → Public hostnames →
   Add:
   - hostname `labels-<pilot host label>.kloudc.com`;
   - service `http://127.0.0.1:8080`.
   Leave the older tunnel and `~/.cloudflared` alone.
   Check: `curl -s https://labels-<pilot host label>.kloudc.com/healthz`
   answers ok.
2. **Labels site settings.** Add to the labels site's `.env`:
   ```
   STL_KLOUDC_AUTH_ENABLED=true
   STL_KLOUDC_API_BASE=http://127.0.0.1:8900
   STL_KLOUDC_STORE_CODE=<PILOT>
   STL_KLOUDC_LOGIN_URL=https://app.kloudc.com/#/label-printing/open
   ```
   Then restart its launchd job (`launchctl kickstart -k gui/$(id -u)/<labels job>`;
   the job name is in the private notes). The 15-minute auto-pull alone does
   not load new `.env` values.
3. **Turn the feature on** with either of these:
   - `~/kloudc-retailops/app/deploy/pilot-mac/retailops.sh platform label-printing <PILOT> on --url https://labels-<pilot host label>.kloudc.com`
   - Master console → Stores → `<PILOT>` → Label printing → On, address, Save.
4. **Check:**
   - `retailops.sh platform label-printing <PILOT> status` prints `on` and the
     address;
   - sign in to the app as a store user: the Scan tab is there;
   - scan a known barcode: it shows the product, preview and item info;
   - print 1 copy: it prints and shows "Printed.";
   - Print Label History shows it;
   - the website footer "Label printing" opens the labels site already signed in.
5. **Undo:** `retailops.sh platform label-printing <PILOT> off`. The Scan tab
   goes away on the next refresh, and the labels site refuses KloudC users
   within 60 s. The older app is not affected by any step here.

New CLI command (S, `backend/app/platform/__main__.py`):
`label-printing CODE on|off|status [--url URL]`.
- It goes through `stores.put_settings`, so it is validated and audited as
  `cli:<user>`.
- `on` without `--url` keeps the stored address. If there is none, it fails
  with the 400 above.

---

## 6. App changes (A: `storeops-app`)

- **Auth header** (`core/api/labels_auth_interceptor.dart`):
  - send `Authorization: Bearer <token>` instead of `Cookie: session_token=`,
    and only to the labels host (the host check is unchanged);
  - remove any `Cookie` header;
  - rewrite the comment at lines 5-14: the labels site checks the KloudC
    session with the KloudC server (this contract, section 2).
- **Scan tab rule** (`features/scanner/providers/labels_providers.dart`
  `storeHasLabelsProvider`): show it when `store.hasStoreFeature('labels')`
  **and** `labelsBaseUrl != null`. This removes the case where the two
  disagree. `router.dart` and `app_shell.dart` keep reading the provider
  unchanged.
- **Branding parse** (`core/tenant/tenant_config.dart`): read the optional
  `label_printing` object. Unknown or missing means the old behaviour. Keep
  `options` as a `Map<String, Object?>` for later; nothing uses it in v1.
- **Request card** (new `features/label_printing/`: data, provider, one sheet):
  - Home quick actions (`features/home/screens/home_screen.dart`): when the
    feature is **on**, "Print Label History" is unchanged. When it is **off**
    and the user is an admin, the same slot shows **Label printing**, which
    opens the sheet.
  - Non-admins see no tile, which keeps the screen short.
  - Store settings (`features/store_settings/widgets/store_settings_panel.dart`):
    a **Label printing** row with the state chip. It opens the same sheet. The
    "Label Scanner link" field is editable only when the feature is on (1.4).
  - The sheet uses the text from 3.1, the `GET /api/label-printing` status,
    and buttons **Request** / **Cancel request** / **Ask again**. After a
    request it shows "Asked. We'll turn it on and let you know." Nothing else
    is needed.
  - New paths in `core/api/api_config.dart`: `/api/label-printing`,
    `/api/label-printing/request`, `/api/label-printing/request/cancel`.
- **Error text:**
  - labels site 401 → "Sign in again to use label printing." (no automatic
    sign-out);
  - 403 `label_printing_off` → "Label printing is turned off for this store.";
  - 503 `auth_unavailable` → the server's message.
  - Use `_extractDetail` (it already reads `message`).
- **Everything else stays the current scanner code:** scan, lookup, the
  shared-barcode picker, not found with suggestions, add a product, preview,
  item info, copies, the duplicate-print check, history.
  - Optional catch-up, only if time allows and in its own commit: paged
    history and the reprint-warning sheet from the older app.
- **Search index** (`features/search/data/feature_index.dart`): `scan`,
  `print_labels` and `manual_lookup` stay gated by `capability_gate.dart`.
  Add a `label_printing_request` entry ("Label printing - Request"), shown
  only to admins when the feature is off.

---

## 7. Tests

**L (labels site, `tests/test_kloudc_auth.py`, a fake KloudC with `responses` or a stub server; no outside calls):**
1. KloudC auth off: every existing test passes unchanged, and a KloudC-shaped
   Bearer token gets the existing 401.
2. A legacy cookie still passes **without** any call to KloudC (assert zero
   outbound calls).
3. A valid Bearer token: 200 on lookup, search, preview and history. One
   outbound call, and the second request inside 60 s makes none.
4. A KloudC-shaped `session_token` cookie (installed app builds) is accepted.
5. Prefix ≠ `STL_KLOUDC_STORE_CODE`: 401 with zero outbound calls.
6. Fake token: one outbound call, then 401, and the repeat within 30 s makes
   zero calls. 200 fake tokens in a minute: at most `MAX_CHECKS_PER_MINUTE`
   calls, the rest get 429.
7. A 200 whose `store_code` is wrong, whose `enabled` is false, whose
   `session_expires_at` has passed, or whose body isn't JSON: refused.
8. A 302 from KloudC is not followed: refused.
9. Timeout or 5xx: allowed within stale grace after an earlier success, 503
   otherwise. 401 after a success: refused at once (no grace).
10. `demo: true` or `can_print: false`: 403 on `/api/print`,
    `/api/price-changes/print`, `/api/products/custom` and
    `/api/price-changes/dismiss`; reads still 200.
11. A `STL_KLOUDC_API_BASE` that isn't on the allow-list
    (`https://evil.example`, `http://10.0.0.5:8900`,
    `https://app.kloudc.com.evil.example`): KloudC auth stays off.
12. The token never appears in logs (caplog) or in the cache keys.
13. `/kloudc/enter`: good code sets a cookie with the flags above and 303s to
    `/`; used, unknown or expired codes redirect to the login URL; a tampered
    `kc_labels` is refused; an expired one (over 2 h) is refused.
14. A refused page request on a `*.kloudc.com` host redirects to
    `STL_KLOUDC_LOGIN_URL`. On any other host, `DASHBOARD_LOGIN_URL` as before.

**S (server, `backend/tests/test_label_printing.py`, `test_feature_requests.py`; website node tests):**
1. `store_features` follows the table: `None`+URL means on; `None`+blank
   means off; `True`+blank means `on_no_address` without `labels`;
   `False`+URL means off.
2. `labels_url` validation:
   - accepts `https://labels-x.<platform domain>` and a host in
     `DIRECTORY_EXTRA_API_HOSTS`;
   - refuses http, a path, a query, user-info, `refused_host` (the rule 0.3
     bypass list), and any other domain.
3. Options validation: size, key pattern, depth, list length; unknown keys
   are kept.
4. A store admin can't set `labels_url` while the feature is off (409),
   can while it is on, and can never send `label_printing_enabled` (400).
5. `/api/labels/session`:
   - 200 body exactly as 2.2;
   - 401 expired or signed out, 403 when the feature is off, 409 on an
     `X-Store` mismatch;
   - DEMO store and demo login give `can_print false`;
   - 429 after 120 a minute;
   - `Cache-Control: no-store`;
   - no token in logs.
6. Hand-off: single use, 60 s expiry, other store refused, only the hash is
   stored, rate limits.
7. Request flow:
   - a second open request gets 409;
   - non-admin 403, demo 403;
   - approve sets both settings and the status in one transaction (roll back
     on a bad URL);
   - decline needs a note;
   - cancel works;
   - turning it on by hand closes the open request;
   - audit rows are written.
8. Migration `p0005`: up, down, up on SQLite and Postgres. The partial unique
   index holds.
9. Branding: `label_printing` present when signed in, `null` in the neutral
   body; existing keys unchanged (snapshot).
10. Name guard (`test_no_origin_names.py`) passes, so this doc and every new
    file are free of origin names.
11. Website: `storeSettingsModel.test.mjs`, `platformModel.test.mjs`
    (feature-request actions and badge count) and `featureRoutes.test.mjs`
    (footer gated on `labels`, opens through the hand-off).
12. Playwright: 1280 and 375 wide; admin and non-admin; feature off, on and
    requested.

**A (app):**
1. Interceptor: Bearer only to the labels host, no `Cookie`, nothing to other
   hosts.
2. Scan tab:
   - hidden when `labels` is missing;
   - hidden when the URL is refused by `ApiHostPolicy` even with `labels`;
   - shown when both are present.
3. Home tile swap: on gives Print Label History; off and admin gives Label
   printing; off and non-admin gives nothing. Store settings row states.
4. Request sheet: each state from the fake server's
   `/api/label-printing` (off, requested, declined, on). Tapping Request
   posts once, and a double tap is guarded.
5. Error text for 401, 403 `label_printing_off` and 503 from the labels site.
6. Branding without `label_printing` (older server) behaves as before.
7. `flutter analyze` is clean, the full suite passes, and the device-matrix
   screenshot of the Request sheet is taken at 375 wide.

---

## 8. Backward compatibility and security

**Backward compatibility:**
- **The older app and web scanner:** no change. Their cookie check runs
  first and is untouched. The KloudC path is off until `.env` turns it on.
  The legacy address and its tunnel are not touched.
- **Labels site main:** the branch only adds code. With the new settings
  unset, behaviour is byte-identical, which test L1 checks.
- **KloudC API:** additive keys and routes only (M2 rule 0.1). `labels_url`
  and `store_features` keep their shape. A store with `labels_url` set and no
  `label_printing_enabled` keeps `labels` (the legacy `None` rule).
- **Installed KloudC app builds:** they gate on `labelsBaseUrl` and send the
  token as a cookie. Both still work once the pilot is set up (2.1, step 2).
  The Request card needs the new build.
- **Rule 0.3:** unchanged. No KloudC setting, code or doc names the legacy
  domain.

**Security:**
- **No shared secret in the app or the labels site for this path.** The
  labels site proves nothing about itself. It only asks KloudC "who is this
  token?", which is exactly what the token's owner can already ask with
  `/me`.
- **The token goes only to the store's own labels host (app) and to the
  allow-listed KloudC base (labels site).** Redirects are not followed. TLS is
  verified, except for the same-machine `127.0.0.1` base. The token is never
  logged, never cached raw, never in a URL. The hand-off puts a single-use
  60 s code in the URL, never the token.
- **A fake KloudC token gets nothing:**
  - the wrong store prefix gets 401 with no outside call;
  - the right prefix gets one call, 401, and a 30 s negative cache;
  - a flood is capped at `MAX_CHECKS_PER_MINUTE` outside calls a minute and
    then gets 429;
  - KloudC's own per-token limit is 120 a minute.
  No product data, preview or print happens before the check passes.
- **A real token from another store:** refused by the prefix check, and again
  by `X-Store` / `store_code`.
- **Revocation:** sign-out, a password change, or the feature turned off takes
  effect within 60 s for the app. For the website, within 2 h at most (the
  stated limit). Stale grace applies only while KloudC can't be reached.
- **Demo logins and the DEMO store can't print or change anything on the
  labels site** (`can_print`). This fails closed: an unreadable answer is
  treated as a refusal.
- **The hand-off cookie** is signed, HttpOnly, Secure, SameSite=Lax,
  host-only and 2 h long.
- **The labels site writes no database for sign-in.** KloudC writes only its
  own platform tables (`feature_requests`, `label_handoffs`).

---

## 9. File ownership (no two builders touch the same file)

**L: the existing labels site.**
- Worktree only, never the live folder: `git worktree add <jobs tmp>/labels-wt -b kloudc-auth`.
  Never push `main`; the owner merges and deploys. Never write the site's
  `.env` or database.
- Files:
  - `config.py` (the `STL_KLOUDC_*` fields only);
  - new `app/services/kloudc_auth.py`;
  - `app/__init__.py` (`_register_dashboard_auth_gate` only, plus one
    blueprint registration line);
  - `app/routes/api.py` (`_session_token`, `_demo_status`, and the print log
    line only);
  - new `app/routes/kloudc.py` (`/kloudc/enter`);
  - new `tests/test_kloudc_auth.py`;
  - `.env.example`;
  - new `docs/KLOUDC_AUTH.md`.
- The labels repo is not covered by this repo's name guard. Even so, it never
  copies KloudC secrets or store data.

**S: KloudC server and website (this repo, `main`).**
- Backend:
  - `backend/app/config.py` (2 fields and their validators);
  - `backend/app/tenancy/settings.py` (key sets);
  - `backend/app/store/settings_view.py`;
  - `backend/app/store/branding.py`;
  - new `backend/app/label_printing.py` (store and labels routes) plus one
    `include_router` line in `backend/app/main.py`;
  - new `backend/app/platform/urls.py`;
  - `backend/app/platform/hosting.py` (switch to `urls.py` only);
  - `backend/app/platform/models.py`;
  - new `backend/alembic_platform/versions/p0005_feature_requests.py`;
  - new `backend/app/platform/feature_requests.py`;
  - `backend/app/platform/routes.py` (master feature-request routes, the store
    admin `labels_url` rule);
  - `backend/app/platform/stores.py` (validation);
  - `backend/app/platform/__main__.py` (`label-printing`);
  - `backend/app/platform/throttle.py` (handoff pruning, only if needed);
  - `backend/app/plain_errors.py`;
  - the demo-guard enumeration list;
  - `.env.example`;
  - new backend tests.
- Website (`frontend/src/`):
  - `pages/StoreSettingsPage.jsx`, `settings/storeSettingsModel.js`;
  - `featureRoutes.js`, `store/gating.js`;
  - new `pages/LabelPrintingOpenPage.jsx` plus its route line in `App.jsx`;
  - `clients/console.js`, `api.js` (store calls);
  - `platform/platformModel.js`, `pages/PlatformConsolePage.jsx`,
    `platform/StoreDetail.jsx`;
  - their `.test.mjs` files.
- Also: `content/help/*.json`, `deploy/pilot-mac/` docs only if a step
  changes, and this file's **As built** section.

**A: KloudC app (`storeops-app`, `main`).**
- `lib/core/api/labels_auth_interceptor.dart`;
- `lib/features/scanner/providers/labels_providers.dart` (`storeHasLabelsProvider`
  only);
- `lib/core/tenant/tenant_config.dart` (the `label_printing` parse);
- `lib/core/api/api_config.dart` (3 paths);
- new `lib/features/label_printing/**`;
- `lib/features/home/screens/home_screen.dart` (the tile slot);
- `lib/features/store_settings/widgets/store_settings_panel.dart` and its
  model (the row);
- `lib/features/search/data/feature_index.dart` (one entry);
- `lib/core/glossary/glossary_data.dart`;
- tests under `test/features/label_printing/`, `test/features/scanner/`,
  `test/core/`.
- Optional scanner catch-up stays inside `lib/features/scanner/**`.

**Shared contract points.** If one of these has to change, change it here
first and tell the other two builders:
- the `/api/labels/session` body (2.2);
- the `/api/label-printing` body (1.3);
- the error codes `label_printing_off`, `auth_unavailable`, `already_on` and
  `already_requested`;
- the reserved option names (1.5);
- the hand-off URL shape (3.4).

## As built

(Each builder adds a short dated note here: what shipped, the commits, and
any change from the contract.)

### As built - S (server and website), 2026-10-22

Shipped on `main` as specified, with these notes:
- **Server.** `label_printing_enabled` / `label_printing_options` settings, the
  derived values in `settings_view.py`, `label_printing` in branding (`null`
  in the neutral body), `app/label_printing.py` (status card, request,
  cancel, `/api/labels/session`, hand-off and redeem), the master
  feature-request routes, the `label_printing` block in the master store
  view, the shared address check `app/platform/urls.py` (hosting uses it
  too), the CLI `label-printing CODE on|off|status [--url]`, and platform
  migration `p0005` (`feature_requests`, `label_handoffs`; old hand-off rows
  go with the nightly `prune-logins` job).
- **Changes from the contract (small):**
  - `label_printing_options` is typed `Any` in `config.py` (pydantic-settings
    2.5 can't read a blank env value into a dict); the validator always
    returns a dict.
  - The store admin's `PATCH /api/settings/store` checks the address first
    (a bad one is still 400), then the on/off rule (409 `label_printing_off`).
  - Redeem answers 410 `code_used` for a used **or** expired code, and 403
    `label_printing_off` when the feature was turned off after the code was
    made. The hand-off answer also has `expires_in` (60).
  - The store's card sees a declined request's note as
    `request.review_note`; `request` is the latest request
    (`{id, status, note, created_at, reviewed_at, review_note}`) or `null`.
  - Single-store servers: `/api/labels/session` works; the request and
    hand-off routes answer 409 `platform_mode_required`.
  - `POST /api/labels/handoff` is allowed for demo logins (opening is a
    look; the labels site refuses their prints with `can_print: false`).
- **Website.** Store settings has a Label printing card (on with the address
  and Open label printing, off with Request, requested with Cancel request,
  declined with the note and Ask again); the old "Label Scanner link" field
  is gone from the form. The footer link is "Label printing" and opens
  `/#/label-printing/open` in a new tab. The master console has a Feature
  requests list under sign-ups (counted in the Requests badge) and a Label
  printing row (state chip, switch, address, options read-only) in each
  store's Overview.
- **Help.** The `concept_label_scanner` glossary entry now reads "Label
  printing"; the scan / print-label pages and the troubleshooting entry say
  how to ask for it. The app's bundled copy was synced (only these entries).
- **Not done here:** the Playwright pass (contract 7.12).
