# StoreOps M2 contract

M2 of the multi-store plan (plan section 11.3, MT7a-MT13), built in the
StoreOps repositories. Sections 0-9 are the **shared interfaces** that the
parallel M2 builders code against. Each batch appends an "As built - <batch>"
section at the end of this file (app work too). If a builder has to change an
interface, they change it here, in the same commit, and say so in their As
built section.

Sources: `MULTI_STORE_PLAN.md` sections 0, 3.1, 3.1a, 4, 7, 7.1, 9.1, 11.3;
`docs/MULTISTORE_M1_HANDOFF.md` section 3 (clean-out list, file:line as of
`d4d86ad`; in StoreOps the same lines are in import commit `56e095c`);
`docs/MULTISTORE_M1_CONTRACT.md` (M1 interfaces, still in force).

Working decisions (the owner can change them later; every one is a setting or
a build-time define, never a literal spread through the code):

| Decision | Value | Where it lives |
|---|---|---|
| Product name | `StoreOps` | setting `PRODUCT_NAME`; app define `PRODUCT_NAME` |
| Company | `KloudC LLC` | setting `COMPANY_NAME` |
| Bundle id / application id | `com.kloudc.storeops` | setting `APP_BUNDLE_ID`; iOS/Android project files (MT7a app identity) |
| Platform domain | `storeops.invalid` (placeholder; `.invalid` never resolves) | setting `PLATFORM_DOMAIN`; app define `PLATFORM_DOMAIN` |
| Repos | `Sundar6264-ux/storeops` (server + web), `Sundar6264-ux/storeops-app` (Flutter), both private | - |

---

## 0. Ground rules for every M2 batch

1. **Additive to M1.** No existing route, JSON key, query parameter or
   push `data` key is renamed or removed. `/api/app/config`,
   `/api/auth/me`, `/api/notifications/*`, `/mark-toast-closed`,
   `/mark-ap-closed`, every `toast_*` / `ap_*` key keep their shape. New keys
   and new routes only.
2. **Neutral defaults.** A setting's default is a product (StoreOps) value or
   blank, never a Spice Town value. Spice Town's own values exist only in the
   private files of section 1.6, outside git.
3. **Never under `spicetown.shop`.** `PLATFORM_DOMAIN`, `PUBLIC_HOST`,
   `PUBLIC_BASE_URL`, `LABELS_URL`, `SESSION_COOKIE_DOMAIN` and every
   directory `api_base_url` are refused at startup / load time when the host
   is `spicetown.shop` or ends with `.spicetown.shop` (section 1.2).
4. **Python 3.9 compatible** (`Optional[...]`, no `X | Y`, no `match`). Both
   3.9 and 3.12 suites must pass.
5. **No outside calls in tests.** Fake-store fixtures point every outside
   base URL at `http://127.0.0.1:9` (the discard port) so an accidental call
   fails fast. Never Toast/Clover/Whapi/SMTP/FCM/Anthropic for real.
6. **No secrets or business data in git.** `.env.example` holds blank values.
   Only booleans derived from secrets are ever sent to clients.
7. **Migrations.** M2 adds exactly one migration, `0062_store_identity`
   (owner: MT8, section 1.5). The store pack is stored without a new
   migration (section 3.3). A batch that needs another migration takes the
   next free number and records it here first.
8. **Settings fields are added only by MT7** (in `backend/app/config.py`),
   including the ones MT8, MT10 and MT13 read (all listed in section 1.1).
   A batch running before MT7 merges reads them with
   `getattr(get_settings(), "<field>", <default from section 1.1>)`.
9. **File ownership** is in section 8. Touching another batch's file is
   allowed only for a one-line import/registration, noted in As built.

---

## 1. Store settings (MT7)

One process serves one store (plan 3.1); store settings are fields on
`app.config.Settings` (pydantic-settings, read from `.env`). The env var is
the field name in upper case unless the table says otherwise.

### 1.1 Settings table

**Store identity and branding**

| Field | Env var | Type | Default | Meaning |
|---|---|---|---|---|
| `store_code` | `STORE_CODE` | str | `""` | The store's code. Pattern `^[A-Z0-9][A-Z0-9_]{2,23}$` (stored upper case). Blank only in dev/tests: identity check skipped (1.5), push `store` key omitted. |
| `store_name` | `STORE_NAME` | str | `""` | Display name. Blank -> `product_name`. |
| `store_short_name` | `STORE_SHORT_NAME` | str | `""` | Short name (tab bar, push titles). Blank -> effective `store_name`. |
| `store_address` | `STORE_ADDRESS` | str | `""` | One line, printed as is (PO PDF header/footer, PO email/WhatsApp). Blank -> line omitted. |
| `store_phone` | `STORE_PHONE` | str | `""` | Printed as is. Blank -> omitted. |
| `store_email` | `STORE_EMAIL` | str | `""` | Reply-To on PO emails. Blank -> no Reply-To header (today's behaviour). |
| `store_description` | `STORE_DESCRIPTION` | str | `""` | One sentence for the Ask bot and weekly digest prompts ("a ... store in ..."). Blank -> `"a retail store"`. |
| `product_name` | `PRODUCT_NAME` | str | `"StoreOps"` | Product name: PDF producer tag, old-app message, web shell fallback. |
| `company_name` | `COMPANY_NAME` | str | `"KloudC LLC"` | Legal footer / About. |
| `platform_domain` | `PLATFORM_DOMAIN` | str | `"storeops.invalid"` | Product domain. Store hosts are `<host-label>.<platform_domain>`. |
| `public_host` | `PUBLIC_HOST` | str | `""` | This store's public origin (`https://...`, no trailing slash). Blank -> `https://<host-label>.<platform_domain>` where host-label = `store_code` lower-cased with `_` -> `-`; blank and no `store_code` -> the request's own base URL (dev). |
| `logo_url` | `LOGO_URL` | str | `""` | Absolute `https://` URL or a path on this server. Blank -> `/branding/logo.png` (bundled StoreOps logo). |
| `icon_url` | `ICON_URL` | str | `""` | Same rules. Blank -> `/branding/icon.png`. |
| `brand_color` | `BRAND_COLOR` | str | `"#0F766E"` | Primary colour, `^#[0-9A-Fa-f]{6}$`. |
| `brand_secondary_color` | `BRAND_SECONDARY_COLOR` | str | `"#334155"` | Secondary colour, same pattern. |
| `store_timezone` | `STORE_TIMEZONE` | str | `""` -> `TOAST_TIMEZONE` (default `"America/New_York"`) | Existing M1 field. Must be a valid IANA zone (`zoneinfo`). |
| `currency` | `CURRENCY` | str | `"USD"` | ISO 4217. Step 1 UIs are only guaranteed for `USD`; other values start with a warning. |
| `store_locale` | `STORE_LOCALE` | str | `"en-US"` | BCP 47. (Not `LOCALE`, which can clash with the shell.) |
| `demo_mode` | `DEMO_MODE` | bool | `False` | DEMO store (plan 3.1a): every user is treated as a demo user by `demo_guard` (nothing is sent out) and the nightly reset job runs (MT13). |

**Store rules that are settings (not pack)**

| Field | Env var | Type | Default | Replaces |
|---|---|---|---|---|
| `business_day_cutoff_hour` | `BUSINESS_DAY_CUTOFF_HOUR` | int 0-23 | `4` | `CLOSEOUT_HOUR` (`pos/sync/accuracy.py:86`, `services/insights_data.py:73`) |
| `first_sales_date` | `FIRST_SALES_DATE` | str `YYYY-MM-DD` | `""` (no floor) | `FIRST_REAL_SALES_DATE` (`pos/sync/accuracy.py:74`, `pos/sync/open_orders.py:53`) |
| `pos_provider` | `POS_PROVIDER` | str | `"toast"` | M1 field. M2 values: `toast`, `clover` (capability table only, no adapter until M3), `csv` (CSV-only connector, MT13). |

**Outside channels (store features, 1.3)**

| Field | Env var | Type | Default | Notes |
|---|---|---|---|---|
| `ap_inbox_email` | `AP_INBOX_EMAIL` | Optional[str] | `None` -> `TOAST_INVOICE_EMAIL` | M1 field. Blank = AP email channel off. |
| `toast_invoice_email` | `TOAST_INVOICE_EMAIL` | str | **`""`** (was Spice Town's inbox) | Old name, still read. |
| `labels_url` | `LABELS_URL` | str | `""` | Labels site origin. Blank = no labels for this store (Scan tab, Print Label History, website "Label Scanner" link hidden; the app never sends a token anywhere for labels). Replaces `LABELS_APP_URL` (`frontend/src/featureRoutes.js`) and the app's `LABELS_BASE_URL` define. |
| `ai_enabled` | `AI_ENABLED` | bool | `False` | Per-store AI switch (Apple 5.1.2(i)). Off -> `ask_bot` dropped from `allowed_features`, weekly digest skips the AI paragraph. The key `ANTHROPIC_API_KEY` is still needed for real calls. |
| `whatsapp_group_chat_ids` | `WHATSAPP_GROUP_CHAT_IDS` | str (JSON object) | `""` | Role key -> Whapi chat id, e.g. `{"store":"120363000000000000@g.us"}`. Wins over the old `WHAPI_SPICE_TOWN_CHECKLIST_GROUP_ID` (role `store`) and `WHAPI_STREET_KITCHEN_CHECKLIST_GROUP_ID` (role `kitchen`), which are still read. Rows in `whatsapp_groups` win over both (today's rule). |
| `smtp_host` | `SMTP_HOST` | str | `"smtp.gmail.com"` | Replaces the literal at `toast_email.py:81`, `po_send.py:65-66`. |
| `smtp_port` | `SMTP_PORT` | int | `465` | |
| `smtp_ssl` | `SMTP_SSL` | bool | `True` | `True` = implicit TLS (465); `False` = STARTTLS (587). |
| `smtp_username` | `SMTP_USERNAME` | str | `""` -> `GMAIL_ADDRESS` | Old name still read. |
| `smtp_password` | `SMTP_PASSWORD` | str | `""` -> `GMAIL_APP_PASSWORD` | Secret. Old name still read. |
| `email_from_name` | `EMAIL_FROM_NAME` | str | `""` -> effective `store_name` | Display name on outgoing mail. |
| `email_fallback_domain` | `EMAIL_FALLBACK_DOMAIN` | str | `""` -> `platform_domain` | Replaces the `spicetown.shop` fallback sender domain (`po_send.py:413`). |

**Files, links, app release**

| Field | Env var | Type | Default | Notes |
|---|---|---|---|---|
| `database_url` | `DATABASE_URL` | str | **`"sqlite:///./storeops.db"`** | Was `./spicetown.db`. |
| `uploads_root` | `UPLOADS_ROOT` | str | `"uploads"` | Parent of every upload folder; holds the identity marker (1.5). |
| `flyers_dir` | `FLYERS_DIR` | str | **`"uploads/flyers"`** | Was `~/Spice Town Flyers`. |
| `public_base_url` | `PUBLIC_BASE_URL` | str | **`""`** -> effective `public_host` | Was the Spice Town tailnet address. |
| `store_pack_file` | `STORE_PACK_FILE` | str | `""` | Read-only pack override (3.3). Parity runs and dev only. |
| `store_identity_adopt` | `STORE_IDENTITY_ADOPT` | bool | `False` | One-off: stamp an existing unstamped database / upload folder (1.5). |
| `app_bundle_id` | `APP_BUNDLE_ID` | str | `"com.kloudc.storeops"` | AASA / assetlinks, Play URL. |
| `apple_team_id` | `APPLE_TEAM_ID` | str | `""` | Owner. Blank -> AASA served with an empty `appIDs` list. |
| `android_cert_sha256` | `ANDROID_CERT_SHA256` | str | `""` | Owner. Comma-separated SHA-256 fingerprints for `assetlinks.json`. |
| `app_store_url` | `APP_STORE_URL` | str | **`""`** | Was a Spice Town search URL. Blank -> `null` in JSON. |
| `play_store_url` | `PLAY_STORE_URL` | str | **`""`** -> `https://play.google.com/store/apps/details?id=<app_bundle_id>` | |
| `privacy_url` | `PRIVACY_URL` | str | `"https://kloudc.com/kloudc-privacy-policy/"` | Same URL the app already uses (`legal_links.dart`). |
| `terms_url` | `TERMS_URL` | str | `""` | Blank -> `null`. |
| `support_url` | `SUPPORT_URL` | str | `"https://kloudc.com/kloudc-support/"` | |

**Directory service only (MT10; read by `app.directory`, not by store servers)**

| Field | Env var | Type | Default |
|---|---|---|---|
| `directory_stores_file` | `DIRECTORY_STORES_FILE` | str | `"directory/stores.json"` |
| `directory_rate_per_minute` | `DIRECTORY_RATE_PER_MINUTE` | int | `10` |
| `directory_rate_per_hour` | `DIRECTORY_RATE_PER_HOUR` | int | `100` |
| `directory_client_ip_header` | `DIRECTORY_CLIENT_IP_HEADER` | str | `""` (socket peer); `"CF-Connecting-IP"` behind Cloudflare |
| `directory_extra_api_hosts` | `DIRECTORY_EXTRA_API_HOSTS` | str | `""` (comma list of extra allowed `api_base_url` hosts) |

Unchanged M1/legacy fields keep their names and defaults, except the ones
marked in bold above. The existing `whapi_spice_town_checklist_group_id` and
`whapi_street_kitchen_checklist_group_id` fields stay (old names still read)
and are on the name allowlist (section 9.2).

### 1.2 Validation and derived values (`app/store/settings_view.py`, MT7)

`Settings` validators (startup fails with a clear message on a bad value):
- `store_code`: upper-cased, then the pattern above; blank allowed.
- `brand_color`, `brand_secondary_color`: hex pattern.
- `store_timezone`: `zoneinfo.ZoneInfo(...)` must load.
- `first_sales_date`: blank or `date.fromisoformat`.
- `business_day_cutoff_hour`: 0-23.
- `platform_domain`, `public_host`, `public_base_url`, `labels_url`,
  `session_cookie_domain`: refused when the host is `spicetown.shop` or ends
  with `.spicetown.shop` (rule 0.3). Test: `tests/test_store_settings.py`.

Derived values are computed in one place so every caller agrees:

```python
# app/store/settings_view.py
def store_display_name(s=None) -> str        # store_name or product_name
def store_short_name(s=None) -> str          # store_short_name or store_display_name
def store_host_label(s=None) -> str          # "storeops-test-toast" for STOREOPS_TEST_TOAST; "" if no code
def effective_public_host(s=None, request_base: Optional[str] = None) -> str
def effective_public_base_url(s=None) -> str # public_base_url or effective_public_host
def absolute_url(path_or_url: str, s=None) -> str  # "/branding/logo.png" -> "<public_host>/branding/logo.png"
def store_features(s=None) -> List[str]      # section 1.3, fixed order
def whatsapp_role_chat_ids(s=None) -> Dict[str, str]
def smtp_config(s=None) -> SmtpConfig        # host, port, ssl, username, password, from_name
```

### 1.3 Store features (non-POS switches)

Sent next to the M1 POS `capabilities` as a separate list `store_features`
(M1 rule: store-level switches are not `Capability` members). Fixed order:

| Value | On when | Switches |
|---|---|---|
| `ap_inbox` | effective `ap_inbox_email` is non-blank | Invoice "email to AP inbox", "Mark AP closed" |
| `labels` | `labels_url` is non-blank | Scan tab, Print Label History, website Label Scanner link |
| `whatsapp` | `whapi_api_token` is non-blank | WhatsApp sends: checklist groups, invoice chat, flyer sends, PO WhatsApp |
| `ai` | `ai_enabled` is true | Ask bot tile/route (`ask_bot` feature), digest AI paragraph |

Server side, `features_for_store` (M1, `app/pos/gating.py`) also drops a
feature whose store feature is off: new map
`FEATURE_STORE_FEATURES = {"ask_bot": {"ai"}}` next to `FEATURE_CAPABILITIES`.

### 1.4 `.env.example` (MT7 writes it; blank secrets)

`backend/.env.example` lists every field in 1.1 grouped as above, secrets
blank (`TOAST_CLIENT_SECRET=`, `SMTP_PASSWORD=`, `WHAPI_API_TOKEN=`,
`ANTHROPIC_API_KEY=`, `FIREBASE_CREDENTIALS_FILE=`), and a comment per group.
No real store values.

### 1.5 The `store_identity` stamp (MT8 owns; plan 9.1)

Migration **`0062_store_identity`** (down_revision `0061`):

```sql
CREATE TABLE store_identity (
  id           INTEGER PRIMARY KEY CHECK (id = 1),   -- one row
  store_code   VARCHAR NOT NULL,
  instance_id  VARCHAR NOT NULL,                     -- uuid4 hex, made at stamp time
  created_at   DATETIME NOT NULL,                    -- UTC
  created_by   VARCHAR NOT NULL                      -- "store-init" | "adopt"
);
```
Downgrade drops the table. No rows are inserted by the migration.

Upload-folder marker: `<uploads_root>/.store_identity`, JSON
`{"store_code": "STOREOPS_TEST_TOAST", "instance_id": "<same uuid hex>"}`.

```python
# app/store/identity.py
class StoreIdentityError(RuntimeError): ...
def read_stamp(db) -> Optional[dict]                 # {"store_code","instance_id","created_at","created_by"} or None
def stamp_database(db, store_code: str, created_by: str) -> str   # returns instance_id; refuses if a different code is stamped
def write_uploads_marker(root: str, store_code: str, instance_id: str) -> None
def check_store_identity(settings=None, db=None) -> None          # raises StoreIdentityError
```

`check_store_identity` runs in the FastAPI lifespan **before** the
schedulers start (`app/main.py`, one call). Rules:

| `STORE_CODE` | DB stamp | Result |
|---|---|---|
| blank | none | start, log a warning ("identity check skipped") - dev and tests |
| blank | present | **refuse**: "STORE_CODE is blank but this database belongs to <code>" |
| set | none | **refuse** unless `STORE_IDENTITY_ADOPT=true` (then stamp with `created_by="adopt"`); message says to run `python -m app.store init` |
| set | same code | check the uploads marker (below), start |
| set | other code | **refuse**: "This database belongs to <code>, not <STORE_CODE>" |

Uploads marker (only when `STORE_CODE` is set): missing marker and the
`uploads_root` folder is missing or empty -> write it; missing marker and the
folder has files -> refuse unless adopt; marker code or instance_id differs
from the DB stamp -> refuse. Refusal = `StoreIdentityError`, process exits
non-zero, nothing written. Tests: `tests/test_store_identity.py` covers every
row of the table plus the marker cases.

### 1.6 How Spice Town's values move out (private pack + env, never in git)

Spice Town is not a StoreOps store (D10). Its values are kept only so the
parity harness can prove the de-hardcoded code still gives Spice Town's
answers. They live **outside git** in `$STOREOPS_PRIVATE_DIR`
(default `/Users/sundar/.claude/jobs/e26a1611/tmp/multitenant/m2/private/`;
`backend/packs/private/` is gitignored as a second safe place):

- `spicetown.pack.json` - a store pack (section 3) holding every rule constant
  in the table below;
- `spicetown.env` - the non-secret Spice Town settings below (no tokens, no
  passwords, nothing read from production `.env`);
- `forbidden_tokens.txt` - optional extra words for the fresh-store test
  (staff names), one per line.

Both are produced by **`backend/scripts/packs/extract_legacy_pack.py`**
(committed, no data inside): `--legacy-backend <dir>` imports the constants
from an unpacked copy of the import commit
(`git archive 56e095c backend | tar -x -C <tmp>`), so no Spice Town
repository is read; `--out-dir $STOREOPS_PRIVATE_DIR`. MT8 runs it once
**before** removing the constants and checks the pack against the old
functions (3.6).

| Legacy constant / literal (as of `56e095c`) | Becomes | Spice Town value goes to |
|---|---|---|
| store name in `po_export.py:103,220`, `po_send.py:56`, `checklist_public.py:251`, `invoice_submission.py:74` (`TRANSFER_DEFAULT_VENDOR`) | `STORE_NAME` (effective name) | `spicetown.env` |
| address / phone `po_export.py:32-33`, `po_send.py:57-58` | `STORE_ADDRESS`, `STORE_PHONE` | `spicetown.env` |
| `ask_bot.py:53-54`, `weekly_digest.py:97-98` description | `STORE_DESCRIPTION` | `spicetown.env` |
| `reports.py:108` old-app message, `toast_pdf.py:411` producer | `PRODUCT_NAME` (product, not store) | - |
| `FIRST_REAL_SALES_DATE` | `FIRST_SALES_DATE` | `spicetown.env` |
| `CLOSEOUT_HOUR` | `BUSINESS_DAY_CUTOFF_HOUR` | `spicetown.env` |
| `America/New_York` in `recurrence.py:39`, `tasks_models.py:54` | effective `store_timezone` | `spicetown.env` (`STORE_TIMEZONE`) |
| `toast_invoice_email` default | `AP_INBOX_EMAIL` | `spicetown.env` |
| `public_base_url` default | `PUBLIC_BASE_URL` | `spicetown.env` |
| `LABELS_APP_URL` (website), `LABELS_BASE_URL` (app) | `LABELS_URL` | `spicetown.env` |
| `flyers_dir` default | `FLYERS_DIR` | `spicetown.env` |
| `po_send.py:413` fallback domain | `EMAIL_FALLBACK_DOMAIN` | `spicetown.env` |
| Ask bot on | `AI_ENABLED=true` | `spicetown.env` |
| `EXCLUDED_CATEGORY_GROUPS` (`fix_issues.py:59`) | pack `menu.excluded_category_groups` | `spicetown.pack.json` |
| `KITCHEN_GROUPS` (`delivery_suggestions.py:115`) | pack `menu.kitchen_groups` | pack |
| `GROCERY_MENUS` (`insights.py:68`) | pack `menu.grocery_menus` | pack |
| `po_sections.py:25-150` + `section_for` | pack `po_sections.rules` | pack |
| `HOUSE_VENDOR_PREFIXES` (`schedule_suggestions.py:139`) | pack `house_vendors.prefixes` | pack |
| `channel_of` (`insights.py:454`) | pack `channels` | pack |
| `DOC_LEADS`, `DOC_FALLBACK`, `SECTION_PROPOSALS`, `SECTION_NOTES` (`issue_leads.py:91-200`) | pack `issue_leads` | pack |
| flyer `CATEGORIES` (`flyers.py:77`) | pack `flyer_categories` | pack |
| built-in checklists (`checklist_builtin.py`, migration 0039) | pack `checklists` | stays in Spice Town's DB (migration rows); pack lists none |
| WhatsApp roles (migration 0044, `whatsapp_groups.py:47`) | pack `whatsapp_roles` | stays in DB; pack lists the same two keys |
| category rules (migrations 0034, 0037) | pack `category_rules` | stays in DB; pack lists none |

Parity runs (MT7/MT8 exit criterion) export `spicetown.env` into the
environment and set `STORE_PACK_FILE=$STOREOPS_PRIVATE_DIR/spicetown.pack.json`,
`STORE_CODE` blank (identity check skipped, nothing written to the copy), then
record with `scripts/parity/parity_record.py` and diff against the
`multistore-m1` (`56e095c`) recording on the same copy. Pass = 0 differences;
allowed additions = the new keys of sections 2 and 5.6 and the new
`store_identity` table.

---

## 2. `GET /api/app/branding` (MT7)

Public, **no auth**, no cookies read, never 401. Router
`app/routers/branding.py` (`prefix="/api/app"`), registered before the static
mount. Builder `app/store/branding.py: branding_payload(request_base=None) -> dict`
(also used by tests and MT10's `build_static --refresh-branding`).

Response (fake store `STOREOPS_TEST_TOAST`, section 7):

```json
{
  "schema": 1,
  "store_code": "STOREOPS_TEST_TOAST",
  "name": "Test Toast Market",
  "short_name": "Toast Test",
  "product": {"name": "StoreOps", "company": "KloudC LLC"},
  "logo_url": "https://storeops-test-toast.storeops.invalid/branding/logo.png",
  "icon_url": "https://storeops-test-toast.storeops.invalid/branding/icon.png",
  "primary_color": "#1D4ED8",
  "secondary_color": "#93C5FD",
  "timezone": "America/Chicago",
  "currency": "USD",
  "locale": "en-US",
  "address": "100 Example Ave, Springfield, IL 62701",
  "phone": "(555) 010-0100",
  "pos_name": "Toast",
  "capabilities": [
    "orders_by_business_date", "open_orders", "live_today_sales", "catalog",
    "catalog_change_stamp", "stock_levels", "receiving_records", "employees",
    "day_totals", "analytics_reports", "card_fingerprints",
    "csv_catalog_import", "modifier_price_in_parent"
  ],
  "store_features": ["ap_inbox", "labels", "whatsapp", "ai"],
  "labels_url": "https://labels-storeops-test-toast.storeops.invalid",
  "public_host": "https://storeops-test-toast.storeops.invalid",
  "legal": {
    "company": "KloudC LLC",
    "privacy_url": "https://kloudc.com/kloudc-privacy-policy/",
    "terms_url": null,
    "support_url": "https://kloudc.com/kloudc-support/"
  },
  "min_build": {"ios": 0, "android": 0},
  "store_url": {"ios": null, "android": "https://play.google.com/store/apps/details?id=com.kloudc.storeops"},
  "demo": false
}
```

Field rules:
- `store_code`: `null` when `STORE_CODE` is blank (dev). Clients in
  production builds refuse a `null` code (5.3).
- `name` / `short_name`: effective values (1.2), never blank.
- `logo_url`, `icon_url`: always absolute (`absolute_url`).
- `capabilities`: exactly `gating.capability_values()` (M1, enum order).
- `store_features`: section 1.3, fixed order, only the ones that are on.
- `labels_url`, `address`, `phone`, `legal.terms_url`, `store_url.*`: `null`
  when blank. URLs never end with `/`.
- `min_build`, `store_url`: the same values as `/api/app/config`.
- `demo`: `demo_mode`.
- New keys may be added later; clients ignore unknown keys. `schema` changes
  only on a breaking change (none planned).

Caching headers:
- `Cache-Control: public, max-age=300, stale-while-revalidate=86400`
- `ETag: "<first 32 hex of sha256 of the compact JSON body>"`; a matching
  `If-None-Match` gets `304` with no body.
- `Vary: Accept-Encoding`. No `Set-Cookie`.

Branding assets: `GET /branding/logo.png` and `/branding/icon.png` serve the
bundled StoreOps placeholders from `backend/app/assets/branding/` (MT7a draws
them; MT7 wires the route), `Cache-Control: public, max-age=86400`. A store
with its own logo sets `LOGO_URL` to an absolute URL.

**Additive keys on existing responses (MT7):**
- `GET /api/auth/me`: `store_code` (string or null), `store_features` (list,
  as above). `pos_name` / `capabilities` stay (M1).
- `GET /api/app/config`: **unchanged**.
- Push `data`: `store` (section 5.6).

Tests (`tests/test_branding.py`): shape and types for all three fake stores;
no auth needed; `null` rules; ETag/304; no secret values present (assert the
fake token/password strings never appear in the body); Spice Town names never
appear with the fake settings.

---
## 3. Store pack (MT8)

A store pack is one JSON file holding the store's rules that used to be Python
constants. Packs in git are product or fake ones only:

| File | `pack_id` | Use |
|---|---|---|
| `backend/packs/generic.json` | `generic` | Built-in default when nothing else is set; also shipped as package data |
| `backend/packs/test_toast.json` | `storeops_test_toast` | Fake store STOREOPS_TEST_TOAST (section 7) |
| `backend/packs/test_clover.json` | `storeops_test_clover` | Fake store STOREOPS_TEST_CLOVER |
| `backend/packs/demo.json` | `demo` | DEMO store (MT13 writes it; until then a copy of `generic.json` with `pack_id` `demo`) |
| `$STOREOPS_PRIVATE_DIR/spicetown.pack.json` | `spicetown_legacy` | Parity only, **never committed** (1.6) |

### 3.1 Schema (version 1)

Validated by pydantic models in `app/store/pack.py` (`StorePack`). The JSON
Schema `backend/packs/store_pack.v1.schema.json` is generated from them
(`python -m app.store pack schema`) and committed for reference. Unknown
top-level keys are an error; every section is optional and falls back to the
`generic.json` value of that section.

```json
{
  "pack_version": 1,
  "pack_id": "storeops_test_toast",
  "label": "Fake Toast store for tests",

  "menu": {
    "excluded_category_groups": ["KITCHEN", "CATERING"],
    "kitchen_groups": ["KITCHEN", "CATERING"],
    "grocery_menus": ["retail", "produce"]
  },

  "po_sections": {
    "other_section": "Other",
    "extra_sections": [],
    "rules": [
      {"when": {"group": ["KITCHEN", "CATERING"]}, "section": "Other"},
      {"when": {"any": [{"category": ["Frozen Foods"]}, {"name_regex": "\\bfrozen\\b"}]},
       "first_match": [
         {"name_regex": "ice ?cream|kulfi", "section": "Frozen - Ice Cream"},
         {"name_regex": "pizza|samosa", "section": "Frozen - Snacks"}
       ]},
      {"when": {"category": ["Frozen Foods"]}, "lookup": "subcategory",
       "map": {"Frozen Vegetables": "Frozen - Vegetables"}},
      {"when": {"any": [{"category": ["Frozen Foods"]}, {"name_regex": "\\bfrozen\\b"}]},
       "section": "Frozen - Other"},
      {"when": {"group": ["PRODUCE"]}, "section": "Produce"},
      {"lookup": "category_subcategory", "map": {"Snacks|Chips": "Chips"}},
      {"lookup": "category", "map": {"Beverages": "Drinks", "Snacks": "Snacks"}},
      {"section_from": "category"}
    ]
  },

  "house_vendors": {"prefixes": ["test toast market"]},

  "channels": {
    "empty": "Other",
    "rules": [
      {"equals": ["no dining option"], "channel": "Other"},
      {"starts_with": ["in-store", "in store"], "channel": "In-store"},
      {"equals": ["pick up", "pickup", "take out", "takeout"], "channel": "Pick up"}
    ],
    "split_on": " - ",
    "rename": {"online ordering": "Online ordering"},
    "default": "Other"
  },

  "issue_leads": {
    "leads": [
      {"key": "lead_a", "name": "Lead A", "title": "Grocery",
       "departments": "Dry goods and drinks.", "vendors": ["Acme Foods"]}
    ],
    "fallback": {"key": "admin", "name": "Store admin", "title": "Inventory & Purchase"},
    "section_proposals": {
      "drinks": {"lead": "lead_a", "basis": "document", "why": "Drinks"},
      "snacks": {"lead": "lead_a", "basis": "guess", "why": "Not in the document - next to Drinks"}
    },
    "section_notes": {"other": "Kitchen items and anything without a section."}
  },

  "checklists": [
    {
      "checklist_type": "daily_closing",
      "title": "Daily Closing Checklist",
      "short_name": "Closing",
      "description": "Complete before leaving.",
      "sort_order": 0,
      "settings": {"closing": true, "whatsapp_group": "store", "all_as_usual": false},
      "schema": {
        "format": "items",
        "legacy_order": false,
        "sections": [
          {"id": "s1", "title": "Closing", "description": null, "items": [
            {"id": "q_doors", "type": "multiple_choice", "title": "Front doors locked?",
             "description": null, "required": true,
             "options": [{"value": "Yes", "issue_tag": null}, {"value": "No", "issue_tag": "issue"}]},
            {"id": "q_notes", "type": "paragraph", "title": "Anything unusual?",
             "description": null, "required": false, "flag_if_answered": true}
          ]}
        ]
      }
    }
  ],

  "whatsapp_roles": [{"key": "store", "label": "Store staff group"}],

  "flyer_categories": [
    {"key": "groceries", "label": "Groceries", "folder": "Groceries"},
    {"key": "offers", "label": "Offers", "folder": "Offers"},
    {"key": "others", "label": "Others", "folder": "Others"}
  ],

  "category_rules": []
}
```

### 3.2 Section semantics

**`menu`** - category-group names compared upper-cased and trimmed
(`excluded_category_groups`, `kitchen_groups`); `grocery_menus` compared
lower-cased and trimmed. Generic: all three empty lists (nothing excluded,
every menu counts as grocery).

**`po_sections`** - replaces `section_for` and `_builtin_sections`
(`po_sections.py`). Input: `(category_group, category, subcategory, name)`.
`rules` are tried in order; the first rule that **produces** a section wins;
if none does, `other_section`.
- `when` (optional; missing = always): every key must hold.
  - `group`: list; item group upper-cased + trimmed is in it.
  - `category`, `subcategory`: list; trimmed value is in it (`""` matches blank).
  - `name_regex` / `name_not_regex`: Python `re.search` on the lower-cased,
    trimmed name (must / must not match).
  - `any`: list of `when` objects; at least one holds.
- Exactly one outcome per rule:
  - `section`: that string.
  - `lookup` + `map`: key is the `subcategory`, the `category`, the upper-cased
    `group`, or `"category|subcategory"` for `category_subcategory`; a miss
    produces nothing (next rule).
  - `first_match`: list of `{name_regex, section}` tried in order; no match
    produces nothing.
  - `section_from`: `"category"` or `"subcategory"`: that trimmed value if
    non-blank, else nothing.
- Section list for the PO sections page = every section a rule can produce +
  `extra_sections` + `other_section`, sorted by `section_sort_key`
  (alphabetical, `other_section` last).
- Generic: `{"other_section": "Other", "rules": [{"section_from": "category"}]}`
  (a new store's PO sections are its categories).
- The admin-set `po_section_rules` table still wins over the pack (today's rule).

**`house_vendors.prefixes`** - a vendor whose `vendor_key()` starts with one of
these is the store itself, never a delivering vendor. Generic: `[]`.

**`channels`** - replaces `channel_of`. Input: dining-option string `s`.
Blank `s` -> `empty`. Else `low = s.strip().lower()`; `rules` in order
(`equals`: `low` in list; `starts_with`: `low` starts with one); else the part
of the trimmed `s` before the first `split_on`; if its lower case is a key of
`rename` the mapped label, else that part as written; blank -> `default`.
Generic = the rules in the example (Toast dining-option conventions; no
store names).

**`issue_leads`** - replaces `DOC_LEADS`, `DOC_FALLBACK`, `SECTION_PROPOSALS`,
`SECTION_NOTES`. `section_proposals` keys are lower-case section names;
`basis` is `"document"` or `"guess"`; `lead` must be a `leads[].key` or the
fallback key. Generic: `leads: []`, `fallback` as in the example,
`section_proposals: {}`, `section_notes: {}`.

**`checklists`** - checklist definitions in the builder's items format
(`docs/CHECKLIST_BUILDER_CONTRACT.md`; validated with
`checklist_engine.validate_schema` and `validate_settings`). `settings.whatsapp_group`
must be a `whatsapp_roles[].key` or null. Written as rows (definition +
version 1) by `store-init` only (3.5). **MT8 also changes the
`checklist_schemas` built-in fallback** (`_builtin_registry`): when
`checklist_definitions` is empty it serves the active pack's checklists, never
`checklist_builtin`'s (which stays only because migration 0039 imports it).
Generic: one neutral closing checklist like the example.

**`whatsapp_roles`** - rows for `whatsapp_groups` (`builtin=1`, `chat_id`
null). Replaces `BUILTIN_GROUPS`. Generic: `[{"key": "store", "label": "Store staff group"}]`.

**`flyer_categories`** - replaces flyer `CATEGORIES`; `key` matches
`^[a-z0-9_]{1,40}$`, `folder` is a plain folder name under `flyers_dir`. The
API and app read the list from the server (MT12 removes any hard-coded keys
in the app). Generic: as in the example.

**`category_rules`** - rows for `category_overrides`, columns as stored:
`{"match_type": "supplier" | "category", "match_value": "...", "category_group",
"category", "subcategory", "note"}` (`match_value` for `category` uses
`category_overrides.encode_category_match`). Written by `store-init` with
`created_by = "store-init (<pack_id>)"`. Generic: `[]`.

### 3.3 Where the active pack comes from

```python
# app/store/pack.py
class StorePack(BaseModel): ...                 # sections above, defaults filled from generic.json
def load_pack_file(path: str) -> StorePack      # parse + validate; raises PackError with a readable list
def current_pack(db=None) -> StorePack          # cached per process
def reload_pack() -> None                       # clears the cache (tests, after `pack load`)
def section_for(group, category, subcategory, name, pack=None) -> str
def section_names(pack=None) -> Tuple[str, ...]
def channel_of(dining_option, pack=None) -> str
```

Precedence for `current_pack()`:
1. `STORE_PACK_FILE` set -> that file (read-only; parity and dev). Nothing is
   written to the database.
2. else the newest `setting_changes` row with `key = "store_pack"`
   (`value` = the pack JSON) - written by `store-init` / `pack load` with
   `changed_by_name = "store-init"`. No new table, history for free.
3. else `generic.json`.

Call sites keep their old module-level names as thin wrappers
(`po_sections.section_for(...)` calls `pack.section_for(...)`,
`insights.channel_of` likewise), so imports elsewhere do not change. A pack
change needs a server restart (`reload_pack` exists for tests).

### 3.4 Commands (`python -m app.store ...`, `app/store/__main__.py`)

| Command | Does |
|---|---|
| `init --code CODE --pack PATH [--adopt]` | **store-init** (3.5) |
| `pack validate PATH` | validate, print errors, exit 1 on error |
| `pack load PATH` | validate, then write the `store_pack` row (runtime parts only; no rows seeded) |
| `pack show` | print the active pack JSON and where it came from |
| `pack schema` | print the JSON Schema |
| `identity show` | print the stamp and the uploads marker |

All commands use `DATABASE_URL` / `UPLOADS_ROOT` from settings. Docker:
`docker compose run --rm api python -m app.store init --code XYZ --pack /packs/xyz.json`.

### 3.5 `store-init` (one transaction, idempotent for the same code + pack)

1. Refuse unless the database is at Alembic head.
2. Refuse if a stamp exists with a different code. Refuse if `orders`,
   `invoice_submissions` or `checklist_submissions` have rows, unless
   `--adopt` (an existing store).
3. Remove the rows the old migrations seeded (a no-op on a second run):
   - `category_overrides` with `match_type='supplier' AND match_value='Shaz International'`
     (0034) and with `created_by='category merge plan (migration 0037)'` (0037);
   - `checklist_definitions` (and their `checklist_definition_versions`) whose
     `checklist_type` is one of `checklist_builtin.SEED_DEFINITIONS`' types
     (0039);
   - `whatsapp_groups` rows `key IN ('store','kitchen') AND builtin=1` (0044).
   With `--adopt` nothing is removed. The teams seeded by migration 0026
   (Everyone, Grocery, Kitchen, Managers) are generic and stay.
4. Load the pack: write the `store_pack` row; insert `checklists`,
   `whatsapp_roles`, `category_rules` that are not already there (by
   `checklist_type` / `key` / `(match_type, match_value)`).
5. `stamp_database(db, CODE, "store-init")` and `write_uploads_marker(...)`.
6. Print a JSON summary: `{"store_code", "pack_id", "removed": {...counts},
   "inserted": {...counts}, "instance_id"}`.

### 3.6 Tests (MT8)

- `tests/test_store_pack.py`: schema errors are readable; empty pack = generic
  defaults; every rule outcome type; channel rules; the PO sections page lists
  `section_names`.
- **Legacy equivalence** (`tests/test_store_pack_legacy.py`, skips when
  `$STOREOPS_PRIVATE_DIR/spicetown.pack.json` is absent): the private pack
  reproduces the old `section_for`, `channel_of`, menu sets, house vendors and
  lead proposals exactly - over every `(group, category, subcategory, name)`
  tuple in a scratch copy of the catalog when one is given
  (`STOREOPS_PARITY_DB`) plus synthetic cases for every rule.
- **Fresh-store test** (`tests/test_fresh_store.py`, plan 9.1): new SQLite
  file, `alembic upgrade head`, `store-init` for `STOREOPS_TEST_TOAST` with
  `packs/test_toast.json`; then every text column of every table (except
  `alembic_version`) is searched, case-insensitive and on word boundaries, for
  the committed list `spice town`, `spicetown`, `street kitchen`, `vernon`,
  `shaz international` plus `forbidden_tokens.txt` when present. Zero hits.
  Also: stamp present, marker present, a second `init` is a no-op, `init` with
  another code is refused.
- Parity harness 0 differences with the private pack + env (1.6).

---
## 4. Store directory (MT10)

Tells the one public app which server a store code belongs to (plan 3.1, 7.1).
Two forms with the same JSON: a small service and static files (the fallback).
Both are built from one stores file. Hosting (Cloudflare) is an owner step.

### 4.1 Stores file

`directory/stores.json` is the real list and is **gitignored**;
`directory/stores.example.json` (committed) holds the three fake stores.

```json
{
  "schema": 1,
  "stores": [
    {
      "code": "STOREOPS_TEST_TOAST",
      "name": "Test Toast Market",
      "api_base_url": "https://storeops-test-toast.storeops.invalid",
      "status": "active",
      "message": null,
      "branding": {
        "short_name": "Toast Test",
        "logo_url": "https://storeops-test-toast.storeops.invalid/branding/logo.png",
        "icon_url": "https://storeops-test-toast.storeops.invalid/branding/icon.png",
        "primary_color": "#1D4ED8",
        "secondary_color": "#93C5FD",
        "timezone": "America/Chicago",
        "pos_name": "Toast"
      },
      "min_build": {"ios": 0, "android": 0}
    }
  ]
}
```

Load-time checks (service start and `build_static`; a bad file is refused as
a whole): `code` matches the store-code pattern and is unique; `status` is
`active` or `maintenance`; `api_base_url` is `https://`, has no path, and its
host ends with `.<PLATFORM_DOMAIN>` or is listed in
`DIRECTORY_EXTRA_API_HOSTS`; never `spicetown.shop` (rule 0.3); colours match
the hex pattern; `timezone` loads. A store that is retired is removed from the
file (it then answers 404 like an unknown code).

### 4.2 `GET /v1/stores/{code}`

Service: `backend/app/directory/` (its own ASGI app, `uvicorn
app.directory.main:app --port 8090`; never mounted in a store server).

Code handling: trim, upper-case, `-` -> `_`, then the pattern
`^[A-Z0-9][A-Z0-9_]{2,23}$`.

`200`:
```json
{
  "schema": 1,
  "code": "STOREOPS_TEST_TOAST",
  "name": "Test Toast Market",
  "api_base_url": "https://storeops-test-toast.storeops.invalid",
  "status": "active",
  "message": null,
  "branding": {
    "short_name": "Toast Test",
    "logo_url": "https://storeops-test-toast.storeops.invalid/branding/logo.png",
    "icon_url": "https://storeops-test-toast.storeops.invalid/branding/icon.png",
    "primary_color": "#1D4ED8",
    "secondary_color": "#93C5FD",
    "timezone": "America/Chicago",
    "pos_name": "Toast"
  },
  "min_build": {"ios": 0, "android": 0},
  "updated_at": "2026-10-18T00:00:00Z"
}
```
`status: "maintenance"`: the app shows `message` (or a default line) and does
not log in. `updated_at` = the stores file's modification time (UTC).

Errors (body always `{"error": <code>, "message": <plain English>}`):

| Status | `error` | When | Headers |
|---|---|---|---|
| 400 | `invalid_code` | fails the pattern | `Cache-Control: no-store` |
| 404 | `unknown_store` | not in the file (unknown or retired - same answer) | `Cache-Control: public, max-age=60` |
| 429 | `rate_limited` | over a limit; body also has `"retry_after": <seconds>` | `Retry-After`, `Cache-Control: no-store` |
| 503 | `directory_unavailable` | stores file missing/invalid at request time | `Cache-Control: no-store`, `Retry-After: 30` |

`200` headers: `Cache-Control: public, max-age=300, stale-while-revalidate=3600`,
`ETag` (same rule as branding) with `304` support, `Content-Type:
application/json`, `Access-Control-Allow-Origin: *` (GET only).

Rate limit: per client IP (`DIRECTORY_CLIENT_IP_HEADER` when set, else the
socket peer), sliding windows of `DIRECTORY_RATE_PER_MINUTE` (10) and
`DIRECTORY_RATE_PER_HOUR` (100); every request counts, 404s included, so codes
can't be enumerated quickly. In memory (one directory process). The owner
also adds a Cloudflare rate-limit rule (section 9).

Other routes on the directory host:
- `GET /healthz` -> `{"ok": true, "stores": <count>}`.
- `GET /.well-known/apple-app-site-association` and
  `GET /.well-known/assetlinks.json` (4.4).
- `GET /s/{code}` -> a small HTML page "Open this store in the StoreOps app"
  with App Store / Play links (QR codes point here, 5.4). Unknown codes get the
  same page (no lookup, no leak).

### 4.3 Static fallback

`python -m app.directory.build_static --stores directory/stores.json --out <dir>
[--refresh-branding]` writes:
- `<dir>/v1/stores/<CODE>.json` - the exact `200` body above;
- `<dir>/.well-known/apple-app-site-association`, `<dir>/.well-known/assetlinks.json`;
- `<dir>/s/index.html` (the open-in-app page, code read from the URL in JS);
- `<dir>/_headers` (Cloudflare Pages header rules: `application/json` for the
  JSON files and the AASA file, the cache headers above).
`--refresh-branding` fetches each store's `/api/app/branding` and copies the
branding subset (dev only against fake stores; never run against a real store
from tests).

Fallback hosts (app defines, 5.1): primary `https://directory.<PLATFORM_DOMAIN>`,
static `https://static.<PLATFORM_DOMAIN>` - both serve `/v1/stores/<CODE>`
(static adds `.json`).

### 4.4 `apple-app-site-association` and `assetlinks.json`

Served with `Content-Type: application/json`, no redirect, from the directory
host, the static host, the bare `<PLATFORM_DOMAIN>`, **and every store
server** (store servers serve public checklist links `/c/...`; route
`app/routers/well_known.py`, MT10):

```json
{
  "applinks": {
    "details": [
      {
        "appIDs": ["<APPLE_TEAM_ID>.com.kloudc.storeops"],
        "components": [
          {"/": "/s/*", "comment": "store-code links and QR codes"},
          {"/": "/c/*", "comment": "public checklist links"}
        ]
      }
    ]
  }
}
```
`appIDs` is `[]` while `APPLE_TEAM_ID` is blank. `assetlinks.json`:
`[{"relation": ["delegate_permission/common.handle_all_urls"], "target":
{"namespace": "android_app", "package_name": "com.kloudc.storeops",
"sha256_cert_fingerprints": [<ANDROID_CERT_SHA256 values>]}}]` (empty list
while blank).

Tests (`tests/test_directory.py`): known/unknown/invalid codes, normalisation,
maintenance, 304, rate limit (both windows, `Retry-After`), stores-file
validation (bad host, `spicetown.shop`, duplicate code), 503 on a broken file,
static build output identical to the service bodies, AASA/assetlinks with and
without the owner ids.

---

## 5. App tenant config (MT11, MT12)

### 5.1 Build-time defines (`EnvConfig`, MT11)

`EnvConfig` keeps only build-time values; `apiBaseUrl` and `labelsBaseUrl`
are removed from it (they come from the tenant):

| Define | Default | Meaning |
|---|---|---|
| `APP_ENV` | `production` | `development` allows http and localhost API hosts and the manual server field |
| `PRODUCT_NAME` | `StoreOps` | |
| `PLATFORM_DOMAIN` | `storeops.invalid` | |
| `DIRECTORY_URL` | `https://directory.<PLATFORM_DOMAIN>` | |
| `STATIC_DIRECTORY_URL` | `https://static.<PLATFORM_DOMAIN>` | |
| `EXTRA_ALLOWED_API_HOSTS` | `""` | comma list |
| `DEV_API_BASE_URL` | `""` | development only: skip the directory, use this server |

### 5.2 `TenantConfig` (`lib/core/tenant/tenant_config.dart`)

```dart
class TenantConfig {
  final String storeCode;          // "STOREOPS_TEST_TOAST" (upper case)
  final String name;               // branding.name
  final String shortName;          // branding.short_name
  final Uri apiBaseUrl;            // directory api_base_url (never from branding)
  final Uri publicHost;            // branding.public_host - replaces ChecklistPublicLink.publicHost
  final Uri? labelsBaseUrl;        // branding.labels_url; null => no labels client, Scan tab hidden
  final String timezone;           // IANA, branding.timezone
  final String currency;           // "USD"
  final String locale;             // "en-US"
  final String posName;            // "Toast"
  final Set<String> capabilities;  // branding.capabilities
  final Set<String> storeFeatures; // branding.store_features
  final Color primaryColor;
  final Color secondaryColor;
  final Uri logoUrl;
  final Uri iconUrl;
  final String? address;
  final String? phone;
  final LegalLinks legal;          // company, privacyUrl, termsUrl?, supportUrl
  final int minBuildIos;           // max(directory.min_build, server min_build)
  final int minBuildAndroid;
  final String status;             // "active" | "maintenance"
  final String? statusMessage;
  final bool demo;
  final DateTime fetchedAt;        // UTC

  bool hasCapability(String c) => capabilities.contains(c);
  bool hasStoreFeature(String f) => storeFeatures.contains(f);
  factory TenantConfig.fromStored(Map<String, dynamic> json);
  Map<String, dynamic> toStored();
}
```

Providers (Riverpod):
- `tenantConfigProvider` - `NotifierProvider<TenantConfigNotifier, TenantConfig?>`;
  `null` = no store chosen -> the store-code screen. Methods:
  `Future<TenantConfig> resolve(String code)`, `Future<void> activate(TenantConfig)`,
  `Future<void> refreshBranding()`, `Future<void> switchStore()`.
- `storeScopeProvider` - `Provider<String?>` = active store code. **Every
  store-scoped provider watches it directly or through `apiClientProvider`**,
  so a switch rebuilds them all.
- `apiClientProvider` watches `tenantConfigProvider`; builds Dio with
  `apiBaseUrl`; throws `StateError` when no tenant.
- `labelsApiClientProvider` - `null` when `labelsBaseUrl == null`.

Validation of `apiBaseUrl` before use: `https`, no path, host ends with
`.<PLATFORM_DOMAIN>` or is in `EXTRA_ALLOWED_API_HOSTS`; development builds
also allow `http://localhost`, `127.0.0.1`, `10.0.2.2`. Otherwise the code is
refused ("This store's server address isn't allowed").

### 5.3 Resolving a store code

1. Normalise (trim, upper, `-` -> `_`), check the pattern.
2. `GET DIRECTORY_URL/v1/stores/CODE` (8 s timeout). 404 -> "No store with
   that code" (no fallback). 400 -> same message. 429 -> "Too many tries, wait
   N seconds".
3. Network error or 5xx -> `GET STATIC_DIRECTORY_URL/v1/stores/CODE.json`.
4. Still failing -> the saved `storeops.tenant.<CODE>` record if any (banner
   "Offline - using saved store details"), else "Can't reach StoreOps".
5. `status == "maintenance"` -> show the message, stop.
6. `GET <api_base_url>/api/app/branding`. Its `store_code` must equal CODE
   (case-insensitive) - else refuse ("This server belongs to another store").
   `null` code is accepted only in development builds.
7. `GET <api_base_url>/api/app/config` for the update gate; min build =
   max(directory, server).
8. Save `storeops.tenant.<CODE>` and `storeops.active_store`, apply the theme,
   show the branded login.

QR codes and links: `https://<PLATFORM_DOMAIN>/s/<CODE>` (universal link /
app link) or a bare code. The scanner accepts only those two forms; any other
URL is ignored.

### 5.4 Storage keys

Secure storage (`flutter_secure_storage`) unless marked prefs
(`shared_preferences`). `<CODE>` = upper-case store code.

| Key | Holds | Replaces |
|---|---|---|
| `storeops.active_store` | active store code | - |
| `storeops.known_stores` (prefs) | JSON list of `{code, name, last_used}` for the store-code screen's quick picks; no tokens | - |
| `storeops.tenant.<CODE>` | `TenantConfig.toStored()` (directory record + branding) | `EnvConfig.apiBaseUrl` |
| `session_token.<CODE>` | bearer token for that store's server only | `session_token` |
| `biometric_enabled.<CODE>`, `biometric_asked.<CODE>` (prefs as today) | per-store biometric choice | `biometric_enabled`, `biometric_asked` |
| `push_last_registered_token.<CODE>` (prefs) | FCM token registered with that store | `push_last_registered_token` |
| `push_pending_unregister` (prefs) | JSON list of `{token, store_code, api_base_url}` still to unregister | same key, was a list of tokens |
| `notifications_prompt_answer` (prefs) | unchanged, device-wide | - |

The app is a new install identity (new bundle id), so there are no old keys
to carry over; on first launch MT11 deletes any un-suffixed `session_token`,
`biometric_*` and `push_last_registered_token` it finds (defensive).

Token rules: the auth interceptor attaches `session_token.<active>` only when
the request host equals the tenant's `apiBaseUrl` host; the labels
interceptor only for `labelsBaseUrl`'s host. A token is never sent to any
other host.

### 5.5 Switch-store sequence (MT11)

Entry: More > "Switch store", or a push for another store (5.6). Order is
fixed and tested with a recording fake:

1. Confirm ("Sign out of <name> and switch store?").
2. If signed in: `POST <old>/api/notifications/unregister` `{"token": <fcm>}`
   (no auth; on failure queue `{token, store_code, api_base_url}` in
   `push_pending_unregister`); then `POST <old>/api/auth/logout` (best effort).
3. Delete `session_token.<OLD>` and `push_last_registered_token.<OLD>`.
4. Cancel in-flight requests (the old Dio's `CancelToken`), stop poll timers
   (team badges/chat long-poll).
5. Clear caches: set `storeops.active_store` to null (rebuilds every provider
   through `storeScopeProvider`), `ProviderCache.clearAll()`
   (`provider_cache.dart`), `PaintingBinding.instance.imageCache.clear()`,
   delete `<temp>/store/<OLD>/`.
6. Store-code screen -> 5.3 -> branded login (`POST /api/auth/mobile-login`).
7. After login: send queued unregisters for any store (each to its own
   `api_base_url`), then `POST <new>/api/notifications/devices`
   `{"token", "platform", "previous_token": null}`; remember
   `push_last_registered_token.<NEW>`.

Plain sign-out (no switch) does steps 2-5 but keeps the store selected and
returns to that store's login.

### 5.6 Push payload `store` field

Server (MT7): `push.send_to_tokens` adds `data["store"] = settings.store_code`
when non-blank (one choke point; alerts and chat both go through it). All
other `data` keys unchanged. Example:

```json
{"type": "critical_vendor", "route": "reorder", "vendor": "Acme Foods", "store": "STOREOPS_TEST_TOAST"}
```

App (MT12, `push_target.dart` / `notification_router.dart`): `PushTarget`
gains `store` (String?, upper-cased). On tap:
- `store` missing, or equal to the active store -> route as today;
- different -> dialog "This alert is for <saved name or code>. Switch store?";
  Yes runs 5.5 and lands on that store's login (the route is **not** carried
  over); No dismisses. Store B's data is never opened from store A.
- Foreground message for another store: shown, not routed.

### 5.7 Endpoints the app uses for tenancy (all existing or section 2)

`GET /api/app/branding` (new), `GET /api/app/config`, `POST /api/auth/mobile-login`,
`GET /api/auth/me` (+`store_code`, `store_features`), `POST /api/auth/logout`,
`POST /api/notifications/devices`, `DELETE /api/notifications/devices?token=`,
`POST /api/notifications/unregister`, plus the directory (section 4).

### 5.8 Time zone, theme, strings (MT12)

- `StoreTime` (`lib/core/utils/store_time.dart`) uses the `timezone` package
  with `TenantConfig.timezone` (`tz.initializeTimeZones()` at startup); no
  hard-coded US Eastern offsets remain. DST tests for New York, Chicago and
  Los Angeles.
- Theme: `ColorScheme.fromSeed(seedColor: primaryColor)` with
  `secondary: secondaryColor`; text on brand colours keeps 4.5:1 contrast
  (fallback to the generated `onPrimary`).
- Store name, POS name ("Also in Toast" -> "Also in <posName>"), login footer,
  About and legal links come from `TenantConfig`. Checklist tiles come from
  `GET /api/checklists/schemas`, not hard-coded Spice Town types.

---

## 6. Capability and store-feature gating (MT9 website, MT12 app)

A page/tile is shown when **all** hold: the user's grant (server-filtered
`allowed_features`, M1), the existing role rules (`admin`, `adminOnly`,
`salesOnly`, `noDemo`), every required capability is in `capabilities`, and
every required store feature is in `store_features`. A direct URL, deep link
or push route to a hidden page shows a "Not available for this store" page
(website component `NotAvailable`; app screen `NotAvailableScreen`), never a
crash or a 404. The server's 409 (M1) stays the backstop.

| Website route (`featureRoutes.js` key) | App tile (`featureIndex` id) | Needs capabilities | Needs store features | Partial (element hidden, page stays) |
|---|---|---|---|---|
| `/open-orders` (`open_orders`) | `open_orders` | `open_orders` | - | - |
| `/insights/*` (all `insights_*` keys) | `insights`, `insights_*` | `analytics_reports` | - | - |
| `/insights/returning-customers` (`insights_returning`), `/insights/loss-watch` (`insights_loss_watch`) | `insights_returning`, `insights_loss_watch` | `analytics_reports`, `card_fingerprints` | - | - |
| `/inventory-api` (`setup_inventory_api`, label "Inventory from <pos>") | `inventory_from_toast` | `catalog`, `stock_levels` | - | - |
| - (upload control inside Inventory Reports) | `upload_inventory` | `csv_catalog_import` | - | website: upload button hidden |
| `/items-sold` (`items_sold`) | `items_sold` | - | - | "Today" (live) needs `live_today_sales` |
| `/fix-issues`, `/reconciliation`, `/delivery-review` | `fix_issues`, `reconciliation`, `delivery_review` | - | - | "received" evidence needs `receiving_records` |
| - (Price change log inside Inventory Reports) | `price_change_log` | `catalog` | - | website: the Price change log tab hidden |
| `/` Overview (`overview`) | - (Home) | - | - | sales-accuracy badge needs `day_totals` |
| `/invoices` (`invoice_submission`) | `invoice_submission`, `upload_invoice`, `invoice_trash` | - | - | "Email to AP inbox" and "Mark AP closed" need `ap_inbox`; WhatsApp send needs `whatsapp` |
| footer "Label Scanner" (`labels`, href = `labels_url`) | `scan`, `print_labels`, `manual_lookup` (the whole Scan tab) | - | `labels` | - |
| - | `ask_bot` | - | `ai` | (server also drops `ask_bot` from `allowed_features`, 1.3) |
| `/flyers` (`marketing_flyers`) | `flyers` | - | - | "Send to WhatsApp" needs `whatsapp` |
| `/daily-checklists`, `/checklists/admin` | `daily_checklists`, `checklist_builder` | - | - | WhatsApp group picker needs `whatsapp` |
| `/purchase-order` Send PO | `cart` | - | - | WhatsApp option needs `whatsapp` (plus today's `po_whatsapp_enabled`) |
| sidebar subgroup "Also in Toast" | Insights "Also in Toast" | `analytics_reports` | - | label becomes "Also in <pos_name>" |

Single source per client: website `frontend/src/store/gating.js`
(`ROUTE_REQUIREMENTS`, keyed like `ROUTES_BY_KEY`); app
`lib/core/tenant/capability_gate.dart` (`featureRequirements`, keyed by
`featureIndex` id). Both have a test that every row of this table is present
and that, for each fake store (section 7), the visible set equals the expected
set listed in that test.

Website per-store items (MT9): time zone from branding (`format.js` gets
`setStoreTimeZone(tz)`; `FixIssues.jsx:49`, `DailyChecklists.jsx:31` read it),
document title / login logo / colours from branding (`StoreContext`,
`frontend/src/store/StoreContext.jsx`, fetched once at boot before login),
"{pos}" text in Help and the glossary (`backend/app/glossary.py` substitutes
`pos_name` server-side; MT9 owns that file).

---

## 7. Fake stores for tests

Three stores, as settings fixtures and packs. Every outside URL points at
`http://127.0.0.1:9`. Values are made up; `555` numbers and `.invalid` hosts.

| | `STOREOPS_TEST_TOAST` | `STOREOPS_TEST_CLOVER` | `DEMO` |
|---|---|---|---|
| `STORE_NAME` / short | Test Toast Market / Toast Test | Test Clover Grocery / Clover Test | StoreOps Demo Market / Demo |
| `STORE_TIMEZONE` | `America/Chicago` | `America/Los_Angeles` | `America/New_York` |
| `BRAND_COLOR` / secondary | `#1D4ED8` / `#93C5FD` (blue) | `#15803D` / `#86EFAC` (green) | `#0F766E` / `#334155` (product default) |
| `POS_PROVIDER` | `toast` | `clover` | `csv` |
| capabilities | `TOAST_CAPABILITIES` (M1) | `CLOVER_CAPABILITIES` (below) | `CSV_CAPABILITIES` (below) |
| `store_features` | `ap_inbox`, `labels`, `whatsapp`, `ai` | none | none |
| `AP_INBOX_EMAIL` | `ap@storeops-test-toast.storeops.invalid` | `""` | `""` |
| `LABELS_URL` | `https://labels-storeops-test-toast.storeops.invalid` | `""` | `""` |
| `WHAPI_API_TOKEN` / `WHAPI_BASE_URL` | `test-not-a-real-token` / `http://127.0.0.1:9` | blank | blank |
| `AI_ENABLED` | `true` (key blank) | `false` | `false` |
| `DEMO_MODE` | `false` | `false` | `true` |
| `STORE_ADDRESS` / `STORE_PHONE` | `100 Example Ave, Springfield, IL 62701` / `(555) 010-0100` | `200 Sample St, Fresno, CA 93650` / `(555) 010-0200` | `300 Demo Rd, Hartford, CT 06101` / `(555) 010-0300` |
| pack | `packs/test_toast.json` | `packs/test_clover.json` | `packs/demo.json` |

`CLOVER_CAPABILITIES` (new in `app/pos/capabilities.py`, MT7; plan 4.5
Clover column, unverified items left out), enum order:
`orders_incremental, open_orders, live_today_sales, catalog, stock_levels,
stock_negative_counts, item_cost, employees`.
`CSV_CAPABILITIES`: `csv_catalog_import`.
`gating.PROVIDERS` gains `"clover": ("Clover", CLOVER_CAPABILITIES)` and
`"csv": ("CSV upload", CSV_CAPABILITIES)` as fallback entries (no adapter in
M2 for clover; MT13 registers the csv-only adapter). With these, every Toast
POS job (`run_daily_sales_sync` needs `orders_by_business_date`, etc.) is
dropped for the Clover and DEMO stores by the M1 job gating.

`test_clover.json` differs from `test_toast.json` in: no `kitchen_groups` /
`excluded_category_groups`, `grocery_menus: []`, `house_vendors.prefixes:
["test clover grocery"]`, one lead `lead_b` / "Lead B", flyer categories
`groceries`, `others`, and PO sections `[{"lookup": "category", "map":
{"Dairy": "Dairy & Eggs"}}, {"section_from": "category"}]`.

Where the fixtures live:

| Client | Fixture |
|---|---|
| Backend | `backend/tests/fixtures/stores/{STOREOPS_TEST_TOAST,STOREOPS_TEST_CLOVER,DEMO}.env`; `backend/tests/store_fixtures.py`: `use_store(monkeypatch, code)` sets the env, clears `get_settings` / gating / pack caches; pytest fixture `fake_store` parametrised over the three |
| Directory | `directory/stores.example.json` (the three, as in 4.1) |
| Website | `frontend/src/test/fixtures/branding.js` (the three branding bodies) and `frontend/e2e/fixtures/branding-<code>.json` for Playwright |
| App | `test/fixtures/tenants.dart`: `testToastTenant`, `testCloverTenant`, `demoTenant` (`TenantConfig` consts matching the branding bodies) |

Expected branding bodies for all three are generated once by MT7
(`python -m app.store branding --env tests/fixtures/stores/<code>.env`) and
committed as `backend/tests/fixtures/stores/<code>.branding.json`; the web and
app fixtures are copies of those files (a test in each client compares
field names with the committed JSON).

---

## 8. File ownership and test commands

| Batch | Owns (creates / edits) |
|---|---|
| **MT7a identity** (server) | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.github/workflows/*`, `backend/app/assets/branding/*`, `frontend/public/` icons, `frontend/capacitor.config.json`, removal of Spice Town-only files (handoff 3 step 3 last list) |
| **MT7a identity** (app) | iOS/Android project ids and names (`com.kloudc.storeops`, "StoreOps"), app icons, `firebase_options.dart` placeholder (push off until configured), `pubspec.yaml` metadata (the Dart package name stays `spicetown_app` until a single rename commit after all app batches merge) |
| **MT7** settings + branding | `backend/app/config.py`, `backend/.env.example`, `app/store/settings_view.py`, `app/store/branding.py`, `app/routers/branding.py`, `app/pos/capabilities.py` (`CLOVER_`/`CSV_CAPABILITIES`), `app/pos/gating.py` (fallback entries, `FEATURE_STORE_FEATURES`), `app/routers/auth.py` (`/me` keys), `app/services/push.py` (`store`), the string sites in 1.6's settings rows (`po_export`, `po_send`, `checklist_public`, `reports`, `invoice_submission`, `toast_pdf`, `ask_bot`, `weekly_digest`, `toast_email`, `team/recurrence.py`, `team/tasks_models.py`, `pos/sync/accuracy.py`, `pos/sync/open_orders.py`, `services/insights_data.py`), fake-store `.env` + `.branding.json` fixtures |
| **MT8** pack + identity | `alembic/versions/0062_store_identity.py`, `app/store/identity.py`, `app/store/pack.py`, `app/store/__main__.py`, `backend/packs/*` (except `demo.json` content), `scripts/packs/extract_legacy_pack.py`, rule sites in 1.6's pack rows (`fix_issues`, `delivery_suggestions`, `insights` constants + `channel_of`, `schedule_suggestions`, `po_sections`, `issue_leads`, `flyers`, `whatsapp_groups`, `checklist_schemas`), the identity call in `app/main.py` |
| **MT9** website | `frontend/src/**`, `frontend/index.html`, `frontend/e2e/**`, `backend/app/glossary.py` |
| **MT10** directory | `backend/app/directory/**`, `directory/**`, `app/routers/well_known.py` (+ its include line in `app/main.py`) |
| **MT11** app login/switch | `lib/core/tenant/**` (except `capability_gate.dart`), `lib/core/config/env_config.dart`, `lib/core/api/**`, `lib/core/storage/**`, `lib/core/notifications/push_token_storage.dart`, store-code screen, More > Switch store |
| **MT12** app tz/brand/push | `lib/core/utils/store_time.dart` + its call sites, `lib/core/theme/**`, `lib/core/tenant/capability_gate.dart`, tile gating in `store_sections.dart` / `feature_index.dart` / tabs, `push_target.dart`, `notification_router.dart`, Spice Town strings in `lib/` |
| **MT13** demo + release | `backend/app/pos/adapters/csv_only/**`, `backend/packs/demo.json`, `backend/scripts/demo/**` (made-up data generator, nightly reset), DEMO compose service, release notes; may add its reset job name to `POS_NEUTRAL_JOB_FUNCS` (one line) |

Heavy commands go through `/Users/sundar/.claude/jobs/e26a1611/tmp/m1heavy.sh`:
- backend (from `/Users/sundar/storeops/storeops/backend`):
  `.../website/backend/.venv/bin/python -m pytest -q` (3.9) and
  `.../website/backend/.venv312/bin/python -m pytest -q` (3.12);
- website: `node --test` suites and `npx vite build` from `frontend/`
  (`node_modules` is the symlink);
- app: `flutter analyze` and `flutter test --concurrency=2` from
  `/Users/sundar/storeops/storeops-app`.
Docker images are built and tested only by GitHub Actions.

---

## 9. Owner placeholders and grep tests

### 9.1 Owner actions (prepared as placeholders, never faked)

| Item | Placeholder | Owner step |
|---|---|---|
| Domain + DNS | `PLATFORM_DOMAIN=storeops.invalid` | buy/choose the domain; set `PLATFORM_DOMAIN` and the app define; DNS for `directory.`, `static.`, store hosts |
| Cloudflare | none | account; Pages project for the static directory; Tunnel or proxy for store hosts; a rate-limit rule on `/v1/stores/*` |
| Apple | `APPLE_TEAM_ID=""`, `APP_STORE_URL=""` | App ID `com.kloudc.storeops` with Associated Domains + Push; App Store Connect record "StoreOps" |
| Google Play | `ANDROID_CERT_SHA256=""` | app `com.kloudc.storeops`; upload-key fingerprint |
| Firebase | `firebase_options.dart` placeholder, `FIREBASE_CREDENTIALS_FILE=""` | new "StoreOps" project; `flutterfire configure`; per-store service account with only "Firebase Cloud Messaging API Admin" (plan 7) |
| R2 / Litestream | templates only | bucket + scoped token (handoff section 4) |
| Legal | `TERMS_URL=""` | terms page URL if wanted |

### 9.2 Name grep tests

- Server: `backend/tests/test_no_spicetown_names.py` searches `backend/app`
  (not `alembic/versions`, not `app/services/checklist_builtin.py`),
  `frontend/src`, `frontend/index.html`, `frontend/capacitor.config.json` for
  `spice ?town|street kitchen|vernon|spicetown\.shop` (case-insensitive).
  Every hit must be on the allowlist `backend/tests/data/spicetown_name_allowlist.txt`
  (`<path>:<exact substring>` per line, e.g. the legacy setting names in
  `config.py`). MT7, MT8 and MT9 each shrink the list for their files; at the
  M2 exit it holds only legacy setting names and comments explaining them.
- App: `test/identity/no_spicetown_names_test.dart` does the same over `lib/`
  (allowlist `test/identity/allowlist.txt`), owned by MT12; MT7a-app clears
  the identity files.

---

## As built - MT7a part 1 (repositories)

**Repositories (both private, owner `Sundar6264-ux`, default branch `main`):**
- `storeops` (server + web): https://github.com/Sundar6264-ux/storeops
- `storeops-app` (Flutter app): https://github.com/Sundar6264-ux/storeops-app

Local paths: `/Users/sundar/storeops/storeops`, `/Users/sundar/storeops/storeops-app`.
Each has exactly one remote, `origin`, pointing at the StoreOps GitHub
repository. No remote points at Spice Town's GitHub or local folders.

**Secret and data scan of the full history (both repos, before any push).**
Every blob reachable from every ref was checked for private keys, AWS keys,
Google service-account JSON, Google API keys, Anthropic keys, GitHub tokens,
Slack tokens/webhooks, Stripe live keys, long literal password/secret/token
assignments, and for committed `.env`, database, signing-key, Firebase config,
CSV/XLSX/TSV/PDF files. A second pass looked at every quoted
password/secret/token/api-key literal added in any commit.

Server (source: `multistore-m1` @ `f384bdc`, 291 commits):
- business data: 5 CSV exports under `docs/` (category review, fixes approved /
  rejected, merge pass 2, Shaz fix) and `backend/tests/data/similar_products_catalog.tsv`
  (the store's catalog: item ids, names, suppliers, prices);
- a real hard-coded initial admin password in `backend/scripts/seed_admin.py`
  (present since the first commit);
- not secrets: `frontend/.env.mobile` (only the API base URL), the fake
  `SECRET` in `backend/tests/test_sales_accuracy_job.py`, test passwords.

=> **History dropped.** `storeops` is a fresh import: one commit of the
`multistore-m1` tree with the 5 CSVs, the catalog TSV and its labels JSON left
out, and `seed_admin.py` changed to read `SEED_ADMIN_USERNAME` /
`SEED_ADMIN_PASSWORD` from the environment (or prompt). Re-scan of the new
history: no findings beyond the known non-secrets above.

App (source: `ops-fixes` @ `f99c2e0`, 232 commits):
- only finding: `lib/core/notifications/firebase_options.dart` (2 versions)
  holds the Spice Town Firebase project's client config, including a Google
  API key. Firebase client keys are identifiers shipped inside every app build,
  not secrets; the StoreOps app gets its own Firebase project (MT7a app
  identity), which replaces this file.
- no keys, env files, databases, signing files or business data.

=> **History kept** for `storeops-app`.

**Other changes in this batch (server):**
- root `.gitignore`: env files (except `.env.example` and `frontend/.env.mobile`),
  databases, signing keys, Firebase config files, `node_modules` (also as a
  symlink), and the store catalog fixture paths;
- `backend/tests/test_ops_similar_products.py`: the 4 tests that need the
  labelled catalog fixture skip with a reason when it is absent
  (file: 28 passed, 4 skipped).

**Deferred:** a synthetic catalog + labels fixture so the similar-products
quality-floor tests run again in StoreOps.

---

## As built - MT7a part 2 (identity, Docker, CI)

Server + web (`storeops`, commits `4c7f6ca`..`6d2cc7f` plus this section):

**Clean-out (handoff section 3, step 3 last list).**
- Removed: the origin store's business docs and batch contracts under `docs/`
  (kept: `M2_CONTRACT.md`, `MULTISTORE_M1_CONTRACT.md`,
  `MULTISTORE_M1_HANDOFF.md`), `docs/architecture/`, root `HANDOFF.md`,
  `MOBILE_APP_CONTEXT.md`, `MOBILE_APP_FIXES.md`, `WEBSITE_REQUIREMENTS.md`.
  Code comments that cite the removed contracts now point at files that only
  exist in the origin repository's history; harmless, left for the owning
  batches to reword.
- `.claude/skills/spicetown/` replaced by `.claude/skills/storeops/SKILL.md`
  (ground rules, architecture, 12 generic gotchas, feature pattern, commands).
- Mac-mini deploy scripts removed (backend/cloudflared/updater/watchdog
  plists, `setup_persistent.sh`, `update_cloudflared.sh`, `watchdog.sh`).
  Generic templates in `backend/scripts/launchd/`
  (`com.kloudc.storeops.backend.plist`, `.watchdog.plist`, `watchdog.sh`,
  README) with `__STOREOPS_DIR__` / `__RUN_AS_USER__`.
- `backend/scripts/backup/`: plists renamed to `com.kloudc.storeops.backup` /
  `.litestream` with placeholders; prefix default `storeops`; config dir
  `~/.config/storeops` (`STOREOPS_LITESTREAM_DIR`); Litestream template needs
  `REPLACE_WITH_STORE_CODE` and `REPLACE_WITH_STOREOPS_DIR`; README rewritten
  with the Docker path. `run_litestream.sh` now reads permissions with GNU
  `stat -c` first (BSD `stat -f` second): on Linux `stat -f` is filesystem
  status, which made the 600 check refuse a correct file (found by CI).
- `dump_db_to_csv.sh` defaults to `backend/storeops.db`.

**Identity.**
- Website shell: `<title>StoreOps</title>` + SVG favicon; "StoreOps" on the
  login page, sidebar brand and public checklist header (MT9 files, one string
  each; MT9 replaces them with the branding `store_name` / `product` values).
  FastAPI title "StoreOps" (`app/main.py`, one line; not in parity's
  recording).
- Placeholder artwork: `backend/scripts/branding/make_placeholder_art.py`
  (Pillow; a geometric storefront on `#2457A6`, no store artwork) writes
  `backend/app/assets/branding/{logo.svg, logo-128.png, logo-512.png,
  app-icon-1024.png}` (the defaults MT7's `/api/app/branding` serves),
  `frontend/public/{logo.svg, logo-header.png, favicon-32.png, favicon-512.png,
  apple-touch-icon.png}`, and with `--app-repo` the app's `app_icon.png` and
  `wordmark.png`. The unused store logo JPEG is removed;
  **`backend/app/assets/spice-town-icon.jpg` stays** because
  `services/po_export.py` (MT7) loads it for the PO PDF letterhead - MT7
  switches it to the branding logo setting and deletes the file.
- Capacitor wrapper: `appId` `com.kloudc.storeops`, `appName` "StoreOps"
  (config, Android namespace/applicationId/strings, `MainActivity` package,
  iOS bundle id and display name); npm package `storeops-frontend`.
  `frontend/.env.mobile` holds `https://demo.storeops.invalid`.
- `README.md` rewritten for StoreOps.

**Docker** (`Dockerfile`, `docker/entrypoint.sh`, `docker/litestream.yml`,
`.dockerignore`, `docker-compose.yml`, `.env.example`).
- Multi-stage: `node:22-bookworm-slim` runs `npm ci` + `vite build`;
  runtime `python:3.12-slim-bookworm` + `tesseract-ocr` (5.3.0) + `tini`;
  user `storeops` (uid/gid 10001) owns `/data` only; `test ! -e .env` at build.
- Container paths (image ENV and compose `environment`, which wins over
  `.env`): `DATABASE_URL=sqlite:////data/storeops.db`,
  `INVOICE_UPLOADS_DIR=/data/uploads/invoices`,
  `TEAM_UPLOADS_DIR=/data/uploads/team`, `FLYERS_DIR=/data/uploads/flyers`,
  `INSIGHTS_HMAC_KEY_FILE=/data/insights_hmac.key`,
  `FRONTEND_DIST_DIR=/app/frontend/dist`. MT7's neutral defaults do not change
  these.
- Entrypoint: makes the upload folders, `alembic upgrade head`, then
  `uvicorn app.main:app --workers 1 --proxy-headers` (one worker: jobs run
  in-process; `FORWARDED_ALLOW_IPS` from `.env`). Any other command runs as a
  one-off (`docker compose run --rm store python scripts/seed_admin.py`).
- `HEALTHCHECK` on `/api/health` (Python urllib, interval 30 s, start 60 s).
- Compose: service `store` (`env_file: .env`, volume `store-data:/data`,
  port `${STORE_HTTP_BIND:-127.0.0.1}:${STORE_HTTP_PORT:-8000}`), service
  `litestream` (`litestream/litestream:0.3.13`, profile `backup`, waits for
  `store` healthy, config `docker/litestream.yml` with
  `${LITESTREAM_R2_ACCOUNT_ID}` / `${LITESTREAM_R2_BUCKET}` /
  `stores/${STORE_CODE}/db`; keys `LITESTREAM_ACCESS_KEY_ID` /
  `LITESTREAM_SECRET_ACCESS_KEY` from `.env`). One folder + one
  `COMPOSE_PROJECT_NAME` per store on a machine.
- Root `.env.example`: compose values, store basics, Toast, optional
  services, Litestream - all blank; the full settings list stays in
  `backend/.env.example` (MT7).

**CI** (`.github/workflows/ci.yml`, GitHub Actions, run `39148433449` green):
- backend: pytest on Python 3.9 and 3.12 with Tesseract, outside base URLs at
  `http://127.0.0.1:9`: **2448 passed, 22 skipped** on each;
- website: `npm ci`, `node --test` (**208 passed**), `vite build`, title check;
- docker: buildx build (GHA cache); non-root (uid 10001); no `.env`, `*.db`,
  keys, Firebase files, CSV/TSV under `/app` or `/data`; no tests in the
  image; `docker compose config` with and without `--profile backup`; boot on
  an empty database via compose: `/api/health` 200, website served with the
  StoreOps title, `alembic current` = `0061 (head)`, Docker health
  `healthy`; restart on the existing database; **full backend suite inside
  the image: 2448 passed, 22 skipped**.
- Not covered by CI: booting the image on a copy of a real store database
  (no store data may enter GitHub). The parity run below covers the same
  code on a production copy locally.

**Name grep test** (`backend/tests/test_no_spicetown_names.py`, section 9.2):
- identity scope, zero hits allowed: `README.md`, `Dockerfile`,
  `.dockerignore`, `docker-compose.yml`, `.env.example`,
  `frontend/{index.html, capacitor.config.json, package.json, .env.mobile}`,
  `frontend/src/pages/LoginPage.jsx`, and the folders `.claude`, `.github`,
  `docker`, `frontend/public`, `frontend/android`, `frontend/ios`,
  `backend/app/assets/branding`, `backend/scripts/{launchd,backup,branding}`;
- code scope (`backend/app`, `frontend/src`): every hit must match
  `backend/tests/data/spicetown_name_allowlist.txt` (`<path>:<substring>`);
  **136 entries** today across MT7, MT8 and MT9 files; an entry that matches
  nothing fails the test, so each batch deletes its lines as it moves values
  out;
- explicitly excluded: `backend/alembic/versions/`,
  `backend/app/services/checklist_builtin.py`, `backend/packs/`.

**Other test change:** `test_public_checklist_link.py::
test_page_paths_trailing_slash_and_nested` asserts `GET /` only when the
`frontend` static mount exists (it is made at import time only when
`frontend/dist` exists; a fresh checkout and the CI backend job have none).

**Local checks:** full suite 3.12 on this machine 2450 passed / 1 failed
(the static-mount assertion above, fixed after) / 19 skipped; the touched
files pass on 3.9 too. **Parity** (`scripts/parity`, Python 3.12, a fresh
read-only copy of the production database, deleted after): import commit
`56e095c` vs this batch: **114 files compared, DIFFERENCES: 0**.

App (`storeops-app`, commits `812beb2`..`06c6a98`, history kept):
- iOS `PRODUCT_BUNDLE_IDENTIFIER` `com.kloudc.storeops` (+ `.RunnerTests`),
  `CFBundleDisplayName` / `CFBundleName` "StoreOps", permission strings say
  StoreOps; no `DEVELOPMENT_TEAM` (owner picks the team in Xcode). Android
  `namespace` / `applicationId` `com.kloudc.storeops`, `MainActivity` in
  `kotlin/com/kloudc/storeops`, label "StoreOps". Release builds are still
  signed with the debug key until the owner adds an upload key.
- Icons: `assets/icon/app_icon.png` (generated) -> `dart run
  flutter_launcher_icons` (iOS AppIcon set + Android mipmaps). The login and
  splash wordmark is `assets/icon/wordmark.png` (old store wordmark deleted;
  two one-line path changes in `login_screen.dart` / `splash_screen.dart`).
- `lib/core/notifications/firebase_options.dart`: no project values in git.
  `appFirebaseOptions` is built by `firebaseOptionsFrom(...)` from build
  defines `FIREBASE_PROJECT_ID`, `FIREBASE_MESSAGING_SENDER_ID`,
  `FIREBASE_STORAGE_BUCKET`, `FIREBASE_IOS_API_KEY`, `FIREBASE_IOS_APP_ID`,
  `FIREBASE_ANDROID_API_KEY`, `FIREBASE_ANDROID_APP_ID` (+ `APP_BUNDLE_ID`,
  default `com.kloudc.storeops`); any missing value -> null -> push off
  ("Notifications aren't set up yet"). MT11's `EnvConfig` may later read the
  same define names; they are part of this contract now.
- No `GoogleService-Info.plist` / `google-services.json` existed; `.gitignore`
  now blocks them, signing keys, `key.properties` and env files.
- Removed `MOBILE_APP_CONTEXT.md`, `SPICETOWN_FLUTTER_CLAUDE_HANDOFF.md`; new
  `README.md`; `docs/backend-api-map.md` describes hosts generically;
  `docs/OWNER_SETUP.md` (Apple App ID + APNs key + App Store record, Play app
  + upload key, StoreOps Firebase project, build defines, per-store FCM
  service account).
- `pubspec.yaml`: description only; package name stays `spicetown_app`.
- Tests: `test/identity/app_identity_test.dart` (bundle ids, display names,
  no Firebase config files, no legacy names in pubspec/README/docs/CI/iOS and
  Android project files); notification tests for the placeholder;
  `test/audit/final_ios_metadata_test.dart` expects StoreOps; fake
  `packageName`s are `com.kloudc.storeops`. Local: `flutter analyze` no
  issues; `flutter test --concurrency=2` 5343 passed, 6 skipped, 2 failed
  (the iOS metadata audit, fixed in `93c51bf`; that file 9/9 after).
- CI: `.github/workflows/ci.yml` (Flutter 3.47.2 stable: pub get, analyze,
  test --concurrency=2, a check that no Firebase/signing file is tracked);
  run `39149278647` green: analyze no issues, **5345 passed, 6 skipped**.

**Deferred / for other batches:**
- MT7: `po_export.py` letterhead off `spice-town-icon.jpg`; `config.py`
  defaults (`DATABASE_URL`, `FLYERS_DIR`, `PUBLIC_BASE_URL`, ...); the
  allowlist lines of its files.
- MT8 / MT9 / MT12: their allowlist lines; the app's in-`lib/` strings
  ("Spice Town" in home/login text, `integration_test/` expectations).
- `e2e/regression/*` still uses the origin store's local paths (it runs
  against production copies on the origin machine); left as is.
- The Capacitor wrapper keeps Capacitor's default splash/icon art.

---

## As built - MT9 (website per store)

Branch `m2/mt9-website` (worktree `/Users/sundar/storeops/worktrees/mt9`),
rebased on `main` after MT7a. Files: `frontend/src/**`, `frontend/index.html`,
`frontend/e2e/**`, `backend/app/glossary.py` (+ its test and the name
allowlist lines for these files).

**Store context (`frontend/src/store/`)**
- `StoreContext.jsx`: `StoreProvider` fetches `GET /api/app/branding` once at
  boot, before login (plain fetch, `credentials: "omit"`, 5 s cap; 404 or an
  error -> product defaults). It sets the document title (`<name> - StoreOps`,
  or `StoreOps` with no store name), favicon (`icon_url`), `theme-color`, the
  CSS variables and the store time zone. Hooks: `useBranding()` (branding
  only: login page, public checklist page), `useStore()` (branding + `/me`),
  `usePosName()`, `usePosText()`, `useStoreAllows(element)`.
- `branding.js`: `normalizeBranding(raw)` (every field has a fallback;
  `capabilities` / `storeFeatures` stay `null` = "not told"),
  `storeView(branding, me)` (`/me`'s `pos_name`, `capabilities`,
  `store_features`, `store_code` win when present), `brandCssVars()`:
  `--brand`, `--brand-secondary`, `--accent` (the brand colour darkened only
  as far as needed for 4.5:1 with white text), `--accent-hover`,
  `--accent-soft`, `--accent-rgb`. `index.css` defaults are the product
  colours; the old hard-coded red shadows use `rgba(var(--accent-rgb), a)`.
- `gating.js`: `ROUTE_REQUIREMENTS` (keyed like `ROUTES_BY_KEY`, plus the
  footer key `labels`), `FEATURE_REQUIREMENTS` (grant keys: `open_orders`,
  `sales_insights`, `ask_bot` -> `ai`) and `ELEMENT_REQUIREMENTS` (page
  controls): `inventory_upload`, `price_change_log`, `items_sold_today`,
  `received_evidence`, `sales_accuracy_badge`, `invoice_ap_inbox`,
  `whatsapp`, `also_in_pos`. `null` capabilities / store features (an older
  server) = everything on, except `labels`, which needs `labels_url`.
  `currentPosName()` / `setCurrentPosName()`: the POS name for text built
  outside React (row facts, labels in module-level tables), set from
  branding at boot and from `/me` after login.

**Gating as built**
- `AuthContext.hasFeature` applies `FEATURE_REQUIREMENTS` for everyone,
  admins included; `routeVisible`, `buildSidebar`, `firstAccessiblePath` and
  `footerLinks` take `store` and apply `ROUTE_REQUIREMENTS`.
- A direct URL to a page the store can't have **redirects** to
  `#/not-available?page=<route key>` (`pages/NotAvailablePage.jsx`, "Not
  available for this store"), before the grant check. `/insights` and
  `/inventory-api` included.
- Contract table differences: the inventory upload card is on **Overview**
  (not Inventory Reports) and is hidden there; the sales-accuracy check is
  on **Items Sold** (admins), hidden without `day_totals`; Items Sold without
  `live_today_sales` drops "Today" and opens on a new "Yesterday" choice;
  "received evidence" = the Fix Issues detail's "Last received" row.
- Invoices: without `ap_inbox` the AP email result line, its table column,
  its override, the "Closed" column and "Looks closed in <pos>" are hidden
  and the AP channel is neither waited on nor resent; without `whatsapp` the
  same for WhatsApp. Flyers without `whatsapp`: library only (no group
  picker, no Send). Checklist builder: group picker hidden. Send PO:
  WhatsApp option hidden.

**Text**
- Sidebar: "Inventory from <pos>", "Also in <pos>" + note
  (`ALSO_IN_POS`, renamed from `ALSO_IN_TOAST`; route labels may hold
  `{pos}`, rendered with `routeLabel(route, posName)`). Store name and logo
  in the sidebar, login page and public checklist page (`StoreLogo`: the
  image, or the first letter on the brand colour if it can't load).
- Help: every POS mention is `{pos}`; the jump list renders titles with
  `posText`. Other pages: visible "Toast" text follows `currentPosName()`
  (JSX text, strings inside functions, getters for module-level tables such
  as `CODE_LABEL.item_id`, `JOB_LABELS.toast_sales_sync`). For a Toast store
  every sentence reads as before. Code identifiers (`markInvoiceToastClosed`,
  `toast_*` keys) are unchanged (contract rule 0.1).
- `backend/app/glossary.py`: POS mentions are `{pos}`, "New York" is
  `{store_city}` (new placeholder: the last part of `STORE_TIMEZONE`,
  `_` -> space); `get_glossary()` and `reason_term()` fill them per request
  (`localize_term`). Store-specific menu, folder and house-item wording made
  neutral.
- Neutral wording everywhere in `frontend/src` (no store, kitchen, place or
  domain names; flyer default categories are Groceries / Others; WhatsApp
  role defaults "Store staff group" / "Kitchen staff group"); browser
  storage keys renamed `storeops_*` (sidebar, cart panels, tasks layout,
  mobile token); sales export file `sales.csv`.

**Time zone**: `format.js` `setStoreTimeZone(tz)` / `getStoreTimeZone()` /
`storeDateTimeFormat()` / `lazyStoreFormat()` / `inStoreZone()` /
`storeDateStr()` / `storeZoneCity()`; `DEFAULT_STORE_TIME_ZONE` (the server
default) is the only zone literal in `frontend/src`. All former
`America/New_York` sites (FixIssues, DailyChecklists, ReorderCandidates,
InvoiceSubmission, insights, PlacedOrders, OverviewWidgets, team, chat,
flyers, suggestions) and "store time (New York)" texts use it; the Overview
greeting and date are store time.

**Tests**
- `node --test`: `store/gating.test.mjs` (every contract row; visible pages /
  links / controls for the three fake stores; `/me` over branding; older
  server), `store/branding.test.mjs` (normalising, title, contrast, time
  zone switching incl. Chicago/New York day boundary, web fixtures vs the
  server's `.branding.json` field names when MT7's files exist),
  `store/noStoreNames.test.mjs` (no origin-store names in `frontend/src` +
  `index.html`; no zone literal outside `format.js`); updated
  `featureRoutes`, `insightsModel`, `flyerModel`, `productMatch` tests.
- Backend: `tests/test_glossary_store_words.py` (Toast store reads as
  before, Clover store never says Toast, city from the zone, reason texts
  filled, no names). Allowlist: every `frontend/` and `glossary.py` line
  removed.
- Fixtures: `frontend/src/test/fixtures/branding.js` and
  `frontend/e2e/fixtures/branding-<code>.json` (the three bodies of section
  7, written from the contract; MT7's generated files are compared by name).

**Playwright (`frontend/e2e/store/`)**: `run.sh` builds the website, makes
two fresh databases (alembic head, the old migrations' seeded rows removed
as in 3.5 step 3, four generated logins: admin, staff, "Hide sales numbers"
staff, the App Review demo login), serves the fake Toast store (8861) and
the fake Clover store (8862) with `serve.py`, and runs `stores.mjs` at 1280
and 375 for every role: name / title / logo / `--accent` colour, store time
in Team > Tasks, menu contents per store, gated URLs -> Not available,
Label Scanner, no origin-store names, no "Toast" on the Clover store.
`serve.py` blocks every non-loopback lookup and connection in-process, points
every outside base URL at `127.0.0.1:9`, turns schedulers/push off and
replaces smtplib. Before MT7 merges it adds, from the fixture, the Clover
provider entry and `GET /api/app/branding` (it prints which fallbacks it
used); after MT7 the real ones are used.

**Results (Oct 18)**
- `node --test` (all 226 website tests): 226 passed. `vite build` ok.
- Backend: glossary-related files (`test_ops_glossary`, `test_glossary_store_words`,
  `test_no_spicetown_names`, `test_flyers`, `test_insights`, `test_ops_fix_issues`,
  `test_review_route_gates`, `test_suggestions_b3`, `test_ops_snooze_cartremove`,
  `test_critical_dismissals`, `test_store_vs_container`,
  `test_open_checks_tab_names`, `test_regress_min_order_and_add_all`): all pass on 3.9; the
  first three also on 3.12.
- Playwright (`run.sh`, two stores x 4 roles x 1280/375): 658 checks, 0
  failures. 81 hits classed as **server text** (the same words are in an API
  response on that page), all for the server batches below. Harness
  fallbacks used: branding route and Clover provider (MT7 not merged yet).

**For other batches (found by the Playwright sweep; server data, not website)**
- MT8 (store pack / store-init): built-in checklists are re-seeded with the
  origin store's titles when the table is empty (`checklist_admin.ensure_seeded`);
  `/api/flyers/meta` categories (`services/flyers.py`); house-vendor /
  issue-lead names (`issue_leads.py`, `schedule_suggestions.py`,
  `similar_products.py`); Fix Issues close-reason labels still say Toast
  (`services/fix_issues.py`; the glossary `does` text is already per store).
- MT7 (settings / string sites): notification type descriptions in
  `services/notifications.py` say Toast; `items_sold.py` / `routers/sales.py`
  POS error messages say Toast (and a Clover store without an adapter gets
  500/503 from Items Sold today; `/api/orders/employees` 500s when the POS is
  unreachable). Invoices: the server should report the AP email channel as
  not applicable (`toast_applicable: false`) when `ap_inbox` is off; the
  website already hides and never waits on it.
- First e2e run only: the harness used a wrong setting name for the Toast API
  base, so the fake Toast store's server tried Toast's login endpoint 16
  times with blank credentials (all refused, 401; no data). Fixed: the
  harness now sets `TOAST_API_BASE` / `TOAST_ANALYTICS_BASE` to the discard
  port, asserts them, and blocks every non-loopback lookup and connection.
- Deferred: Insights pages' own explanatory text still says Toast (only
  stores with `analytics_reports`, i.e. Toast, can open them); `/me` has no
  `timezone`, so the zone comes from branding only.

---

## As built - MT11 (app: store-code login, tenant config, switch store)

Repository `storeops-app`, branch `m2/mt11-mt12` fast-forwarded into `main`
and pushed: `197fc76` (store time), `bbd2212` (MT11), `6f7dfe2` (MT12),
`1809961` (merge of MT7a's identity work), `fb25016` (store links,
post-merge fixes), `ba945b3` (branding fixture test). Interfaces as in sections 5.1-5.5,
with these additions and differences:

**Build-time (5.1).** `EnvConfig` (`lib/core/config/env_config.dart`) holds
`APP_ENV`, `PRODUCT_NAME`, `PLATFORM_DOMAIN`, `DIRECTORY_URL`,
`STATIC_DIRECTORY_URL`, `EXTRA_ALLOWED_API_HOSTS`, `DEV_API_BASE_URL`
(ignored outside development). `apiBaseUrl` / `labelsBaseUrl` are gone. A
compile-time `appDisplayName` (= `PRODUCT_NAME`) is used where the OS shows
the app's name ("Settings > StoreOps").

**Tenant layer (`lib/core/tenant/`).**
- `tenant_config.dart`: `TenantConfig` (fields of 5.2, plus `posLabel`,
  `publicUrl()`, `copyWith`), `DirectoryRecord`, `LegalLinks`,
  `parseHexColor`. `fromSources(directory, apiBaseUrl, branding,
  labelsBaseUrl, serverMinBuild*)`; `toStored()` / `fromStored()` round-trip.
  Min build = max(directory, `/api/app/config`, branding `min_build`).
- `store_code.dart`: `validStoreCode()` (trim, upper, `-`->`_`, pattern of
  4.2); `storeCodeFromScan()` accepts only `https://<PLATFORM_DOMAIN>/s/<CODE>`
  (port 443, exactly two path segments) or a bare code.
- `api_host_policy.dart`: `ApiHostPolicy` - https, no path/query/userinfo,
  host ends with `.<PLATFORM_DOMAIN>` or is in `EXTRA_ALLOWED_API_HOSTS`;
  development also `localhost`, `127.0.0.1`, `10.0.2.2` over http. Applied to
  `api_base_url` (refused before any request) and to branding `labels_url`
  (a refused labels URL = no labels for that store).
- `tenant_http.dart`: `TenantHttp` (unauthenticated GET/POST, no cookies, no
  redirects, every status returned) - directory, branding, app config and
  cross-store push unregister. `tenantHttpProvider` is the test seam.
- `store_resolver.dart`: the 8 steps of 5.3. Differences: a static-copy 404
  is treated as "not reachable" (saved record, else "Can't reach StoreOps"),
  not "No store with that code", because the static copy may lag a new store;
  a directory answer whose `code` differs from the asked code is refused; with
  the directory down and a saved record, the saved record's own server is
  still asked for fresh branding (offline only when that fails too).
- `tenant_storage.dart`: `TenantStorage` (`DeviceTenantStorage`,
  `MemoryTenantStorage` for tests) with the keys of 5.4; known stores capped
  at 8; `deleteLegacyKeys()` runs at every launch (cheap, defensive).
- `tenant_providers.dart`: `envConfigProvider` (moved here; re-exported from
  `auth_providers.dart`), `tenantConfigProvider`
  (`TenantConfigNotifier`: `restore`, `resolve`, `activate(config, offline)`,
  `refreshBranding()` -> `bool moved`, `switchStore({toCode})`,
  `clearActive`), `storeScopeProvider`, `tenantOfflineProvider`,
  `tenantStorageProvider`, `storeResolverProvider`.
- `active_store.dart`: `ActiveStore` - a synchronous mirror of the active
  `TenantConfig` for code that can't watch providers (models, device-pref
  keys via `ActiveStore.scopedKey(base)` = `base.<CODE>`, `StoreTime`).
- `store_switcher.dart`: `StoreSwitcher.switchStore({toCode})` and
  `resetSession({keepStore})`; `appRestartProvider`;
  `storeTempCleanerProvider`; `StoreSwitcher.pendingCodes` (a
  `ValueNotifier`) carries a code to open across the restart.

**Switch / sign-out (5.5).** Order as specified (tested with a recording fake
in `test/core/tenant/store_switch_test.dart`); the only reordering is inside
step 2-3: `push_last_registered_token.<OLD>` is deleted before the unregister
call (as before M2, so a failed unregister is queued, not lost). Step 5 goes
further than the contract: after clearing, **the app starts over in a new
ProviderContainer** (`StoreOpsRoot` in `lib/app/bootstrap.dart` owns the
container; `appRestartProvider` replaces it). Every provider - including
plain `StateProvider` selections and kept-alive lists that don't watch
`storeScopeProvider` - every tab's navigation stack and the Team poll timer
are disposed. Plain sign-out (More > Sign Out) does the same but keeps the
store. `ProviderCache.clearAll()` exists (`provider_cache.dart`) and is
called too. `<temp>/store/<CODE>/` is real: every temp file the app writes
(document viewer, PO PDF share, flyer share, invoice scans, voice notes) now
goes there (`lib/core/storage/store_temp_dir.dart`). A 401 still only
signs out (no restart), so its "session expired" message survives.

**Clients and tokens.** `ApiClient(baseUrl:)` (was `env:`) with `close()`;
`apiClientProvider` watches `(storeCode, apiBaseUrl)`, throws `StateError`
with no store, and closes the old client when replaced. `AuthInterceptor`
attaches the bearer token only when the request host == the tenant's API
host (and removes a stray header otherwise); `LabelsAuthInterceptor` only
for the labels host. `labelsApiClientProvider` is `null` without a labels
site; `storeHasLabelsProvider` drives the Scan tab and the `/scan` route.
Server moved (a refresh finds a new `api_base_url`): the token is forgotten
locally, nothing is sent to the new host, "Your store moved to a new server.
Please sign in again."

**Per store on the device.** Besides 5.4's keys, these shared_preferences
keys are now `<key>.<CODE>`: `recent_vendors_v1`, `cart_open_vendors`,
`store_section`, `invoice_learned_vendors_v1`,
`team_rules_accepted_version_<userId>`.

**Screens.** `StoreCodeScreen` (`/store-code`, code field, Scan QR code via
`storeQrScannerProvider` -> `StoreQrScanScreen`, recent stores, a
development note with the directory URL), branded `LoginScreen`
(`StoreBrandHeader`: store logo with an icon fallback, store name; offline
and maintenance notices; maintenance disables Sign In; "Not <short name>?
Change store"), `LoginFooter` ("For <store> staff", the store's privacy
link), `SplashScreen` (store logo, or the product wordmark), More > Switch
store (confirm "Sign out of <name> and switch store?") and a `switch_store`
search entry, About (store, code, POS, the store's legal links). Router:
unauthenticated with no store -> `/store-code`; `/s/<CODE>` app links set the
pending code when no store is chosen. The update gate uses the higher of the
directory's and the server's minimum and re-checks when the store changes.

**Fixtures.** `test/fixtures/tenants.dart`: `testToastTenant`,
`testCloverTenant`, `demoTenant` (section 7 values), `tenantOverrides()`,
`FixedTenantNotifier`, and `legacyFixtureStore` (only for the old invoice /
public-link fixtures, which were captured at the origin store).
`test/fixtures/branding/<CODE>.branding.json` are copies of MT7's
`backend/tests/fixtures/stores/<CODE>.branding.json` (taken from the MT7
branch); `branding_fixtures_test.dart` builds each `TenantConfig` from them
and requires it to equal the fixture, and checks the key set.

**Tests (MT11):** `test/core/tenant/` (store_code 9, tenant_config 7,
store_resolver 16, store_switch 5 incl. the StoreOpsRoot "no previous store
data" widget test), `test/core/storage/per_store_keys_test.dart` (5),
interceptor host tests (auth +2, labels +1),
`test/features/authentication/store_code_screen_test.dart` (12, incl. a
device matrix 320x568 / 375x667 / 390x844 / 320x1024 split / 1024x1366 at
2x text), `test/features/account/switch_store_tile_test.dart` (7), update
gate +2.

**Deferred (MT11):** universal links / app links need the owner's
Associated Domains + `FlutterDeepLinkingEnabled` (MT7a/owner); `/c/...`
public checklist links are not opened in the app. A link for another store
while a store is chosen just opens the app (no switch prompt).

## As built - MT12 (app: store time, brand theme, gating, push store, names)

**Store time (5.8).** `StoreTime` keeps its API; zone from
`StoreTime.setTimezone()` (called on every tenant activation), tz database
`package:timezone` `latest.dart` (full range, ~1.1 MB source), loaded in
`bootstrap()` and lazily elsewhere. New: `offsetAt()`, `fromStoreWallClock()`,
`timezone`. `endOfStoreDay` uses the offset at that midnight. Task create /
schedule requests send `StoreTime.timezone`.

**Theme.** `AppTheme.forBrand(primary, secondary, brightness)` (MT11 wiring
in `app.dart`); `BrandColors` keeps text on brand colour and brand-coloured
text on the surfaces at >= 4.5:1 (white/near-black, else the seed's
`onPrimary`; accents lightened/darkened step-wise). `AppColors.primary` is
now the product default teal `#0F766E`; FABs and the More avatar use the
theme's primary.

**Gating (6).** `lib/core/tenant/capability_gate.dart`:
`featureRequirements` (open_orders, insights + every `insights_<screen>`,
inventory_from_toast, upload_inventory, price_change_log, scan /
print_labels / manual_lookup, ask_bot), `storeAllowsFeature(id, store)` (no
store known = nothing hidden), `StoreParts` partial getters (`whatsapp`,
`apInbox`, `liveTodaySales`, `receivingRecords`, `dayTotals`, `posLabel`),
`NotAvailableScreen`, `StoreFeatureGate`. `FeatureEntry.visibleFor(user)`
(grant AND store) replaces `access.allows` in search, Store sections and More
> Setup; `canOpenInsights` and `InsightScreen.allows` check the store too.
Push routes to open orders, inventory from POS, loss watch and insights are
wrapped in `StoreFeatureGate` (`pushScreenContent()` unwraps for tests).
"Also in <pos>" and "Inventory from <pos>" (`FeatureEntry.displayTitle`).
The three hard-coded checklist search entries (`checklist_spice_town`, ...)
are removed: a store's checklists are reached through Daily Checklists,
which lists the server's schemas.

**Push (5.6).** `PushTarget.store` (upper case). AppShell: another store's
push -> dialog "This alert is for <saved name or code>. Switch store?"; Yes
-> `switchStore(toCode:)` (lands on that store's sign-in, route not carried);
No -> dismissed. The Team poller ignores another store's foreground push.

**Names (9.2).** `test/identity/no_spicetown_names_test.dart` over `lib/`
(pattern of 9.2; the package name `spicetown_app` and `_`-separated legacy
checklist type ids are not hits); `test/identity/allowlist.txt` is empty.
Changes: OS-permission hints use `appDisplayName`; Home title = store short
name; PO share caption / scanned-PDF creator; invoice parser's own-store
names come from the tenant (`ActiveStore`), not a literal; neutral glossary
and help text; generic flyer default categories (`groceries`, `others`) and
checklist-builder WhatsApp groups (`store`, `kitchen`); no staff names in
public-link suggestions or search keywords; comments name "the backend" /
"the labels app" instead of the old repos.

**Tests (MT12):** store_time_zones 20 (NY/Chicago/LA: offsets, spring
forward, fall back, local midnight, round trip, end of day across DST),
capability_gate 12 (section 6 rows, visible sets per fake store, Store
sections, insights, "Also in <pos>", partial gates, "Not available" for
pushes), push_store_routing 5, app_theme_buttons 9 (each fake store's theme
and arbitrary colours at 4.5:1), names 2.

**Also (MT12):** public checklist links no longer prefill built-in names;
with no server suggestion a new link starts on "Type their name".

**Totals (both batches):** `flutter analyze` no issues;
`flutter test --concurrency=2` (heavy queue, at `fb25016`): **5454 passed,
6 skipped, 0 failed**; plus 4 in `branding_fixtures_test.dart` at
`ba945b3`. No simulator run (Regression stage).

**Deferred (MT12):** the partial gates of section 6 are available as
`StoreParts` getters but not yet wired into the individual screens (Items
Sold "Today", "received" evidence, Home's accuracy badge, AP inbox actions,
WhatsApp send options); the server's 409 / its own channel switches remain
the backstop. Glossary/help text still says "Toast" in POS explanations (the
server's `/api/glossary` replaces the bundled copy; MT9 substitutes
`pos_name` there). Chat bubble colour is the product teal, not the store's.
