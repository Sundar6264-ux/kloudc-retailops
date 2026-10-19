# StoreOps M2 contract

> **Name note (2026-10-19):** this record was written while the product was
> called **StoreOps**; it is now **KloudC RetailOps** (app id
> `com.kloudc.retailops`, Dart package `kloudc_retailops`, `RETAILOPS_*`
> environment names - the old `STOREOPS_*` names still work). The names
> below are the ones used at the time. The store the product was first built
> for is called "the pilot store" here.

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
   blank, never a pilot-store value. The pilot store's own values exist only in the
   private files of section 1.6, outside git.
3. **Never under `<blocked-domain>`.** `PLATFORM_DOMAIN`, `PUBLIC_HOST`,
   `PUBLIC_BASE_URL`, `LABELS_URL`, `SESSION_COOKIE_DOMAIN` and every
   directory `api_base_url` are refused at startup / load time when the host
   is `<blocked-domain>` or ends with `.<blocked-domain>` (section 1.2).
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
| `store_email` | `STORE_EMAIL` | str | `""` | Reply-To on PO emails. Blank -> the sending address (today's behaviour; MT7 corrected this row). |
| `store_description` | `STORE_DESCRIPTION` | str | `""` | One sentence for the Ask bot and weekly digest prompts ("a ... store in ..."). Blank -> `"a retail store"`. |
| `product_name` | `PRODUCT_NAME` | str | `"StoreOps"` | Product name: PDF producer tag, old-app message, web shell fallback. |
| `company_name` | `COMPANY_NAME` | str | `"KloudC LLC"` | Legal footer / About. |
| `platform_domain` | `PLATFORM_DOMAIN` | str | `"storeops.invalid"` | Product domain. Store hosts are `<host-label>.<platform_domain>`. |
| `public_host` | `PUBLIC_HOST` | str | `""` | This store's public origin (`https://...`, no trailing slash). Blank -> `https://<host-label>.<platform_domain>` where host-label = `store_code` lower-cased with `_` -> `-`; blank and no `store_code` -> the request's own base URL (dev). |
| `logo_url` | `LOGO_URL` | str | `""` | Absolute `https://` URL or a path on this server. Blank -> `/branding/logo.png` (bundled StoreOps logo). |
| `icon_url` | `ICON_URL` | str | `""` | Same rules. Blank -> `/branding/icon.png`. |
| `document_logo_file` | `DOCUMENT_LOGO_FILE` | str | `""` | (MT7 addition) Local image on the PO PDF letterhead. Blank -> the bundled `assets/branding/logo-128.png`. |
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
| `toast_invoice_email` | `TOAST_INVOICE_EMAIL` | str | **`""`** (was the pilot store's inbox) | Old name, still read. |
| `labels_url` | `LABELS_URL` | str | `""` | Labels site origin. Blank = no labels for this store (Scan tab, Print Label History, website "Label Scanner" link hidden; the app never sends a token anywhere for labels). Replaces `LABELS_APP_URL` (`frontend/src/featureRoutes.js`) and the app's `LABELS_BASE_URL` define. |
| `ai_enabled` | `AI_ENABLED` | bool | `False` | Per-store AI switch (Apple 5.1.2(i)). Off -> `ask_bot` dropped from `allowed_features`, weekly digest skips the AI paragraph. The key `ANTHROPIC_API_KEY` is still needed for real calls. |
| `ai_consent_notice` | `AI_CONSENT_NOTICE` | str | `""` -> `app/legal.py` `DEFAULT_AI_CONSENT_NOTICE` | Text of the AI notice the app shows before the first Ask bot question; served by `GET /api/app/legal` (MT13). |
| `whatsapp_group_chat_ids` | `WHATSAPP_GROUP_CHAT_IDS` | str (JSON object) | `""` | Role key -> Whapi chat id, e.g. `{"store":"120363000000000000@g.us"}`. Wins over the old `WHAPI_STORE_CHECKLIST_GROUP_ID` (role `store`) and `WHAPI_KITCHEN_CHECKLIST_GROUP_ID` (role `kitchen`), which are still read. Rows in `whatsapp_groups` win over both (today's rule). |
| `smtp_host` | `SMTP_HOST` | str | `"smtp.gmail.com"` | Replaces the literal at `toast_email.py:81`, `po_send.py:65-66`. |
| `smtp_port` | `SMTP_PORT` | int | `465` | |
| `smtp_ssl` | `SMTP_SSL` | bool | `True` | `True` = implicit TLS (465); `False` = STARTTLS (587). |
| `smtp_username` | `SMTP_USERNAME` | str | `""` -> `GMAIL_ADDRESS` | Old name still read. |
| `smtp_password` | `SMTP_PASSWORD` | str | `""` -> `GMAIL_APP_PASSWORD` | Secret. Old name still read. |
| `email_from_name` | `EMAIL_FROM_NAME` | str | `""` -> effective `store_name` | Display name on outgoing mail. |
| `email_fallback_domain` | `EMAIL_FALLBACK_DOMAIN` | str | `""` -> `platform_domain` | Replaces the `<blocked-domain>` fallback sender domain (`po_send.py:413`). |

**Files, links, app release**

| Field | Env var | Type | Default | Notes |
|---|---|---|---|---|
| `database_url` | `DATABASE_URL` | str | **`"sqlite:///./storeops.db"`** | Was `./pilot.db`. |
| `uploads_root` | `UPLOADS_ROOT` | str | `"uploads"` | Parent of every upload folder; holds the identity marker (1.5). |
| `flyers_dir` | `FLYERS_DIR` | str | **`"uploads/flyers"`** | Was `~/the pilot store Flyers`. |
| `public_base_url` | `PUBLIC_BASE_URL` | str | **`""`** -> effective `public_host` | Was the pilot store tailnet address. |
| `store_pack_file` | `STORE_PACK_FILE` | str | `""` | Read-only pack override (3.3). Parity runs and dev only. |
| `store_identity_adopt` | `STORE_IDENTITY_ADOPT` | bool | `False` | One-off: stamp an existing unstamped database / upload folder (1.5). |
| `app_bundle_id` | `APP_BUNDLE_ID` | str | `"com.kloudc.storeops"` | AASA / assetlinks, Play URL. |
| `apple_team_id` | `APPLE_TEAM_ID` | str | `""` | Owner. Blank -> AASA served with an empty `appIDs` list. |
| `android_cert_sha256` | `ANDROID_CERT_SHA256` | str | `""` | Owner. Comma-separated SHA-256 fingerprints for `assetlinks.json`. |
| `app_store_url` | `APP_STORE_URL` | str | **`""`** | Was a pilot-store search URL. Blank -> `null` in JSON. |
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
marked in bold above. The existing `whapi_store_checklist_group_id` and
`whapi_kitchen_checklist_group_id` fields stay (old names still read)
and are on the name allowlist (section 9.2).

### 1.2 Validation and derived values (`app/store/settings_view.py`, MT7)

`Settings` validators (startup fails with a clear message on a bad value):
- `store_code`: upper-cased, then the pattern above; blank allowed.
- `brand_color`, `brand_secondary_color`: hex pattern.
- `store_timezone`: `zoneinfo.ZoneInfo(...)` must load.
- `first_sales_date`: blank or `date.fromisoformat`.
- `business_day_cutoff_hour`: 0-23.
- `platform_domain`, `public_host`, `public_base_url`, `labels_url`,
  `session_cookie_domain`: refused when the host is `<blocked-domain>` or ends
  with `.<blocked-domain>` (rule 0.3). Test: `tests/test_store_settings.py`.

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

Invoices (verify pass, see "As built - M2 backend verify"): with `whatsapp`
or `ap_inbox` off, a send that fails as "not configured" is recorded as
`WHATSAPP_OFF` / `AP_EMAIL_OFF` (`invoice_submission.py`) and is **not
applicable**, never failed: no "Invoice failed to send" alert, the invoice is
done once the store's own channels are done, `toast_applicable` /
`ap_applicable` are `false` when the AP channel is off, and "Mark AP closed"
needs only the AP email when WhatsApp is off. A channel the store does use but
that is misconfigured still fails as before.

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

### 1.6 How the pilot store's values move out (private pack + env, never in git)

The pilot store is not a StoreOps store (D10). Its values are kept only so the
parity harness can prove the de-hardcoded code still gives the pilot store's
answers. They live **outside git** in `$STOREOPS_PRIVATE_DIR`
(default `~/.claude/jobs/e26a1611/tmp/multitenant/m2/private/`;
`backend/packs/private/` is gitignored as a second safe place):

- `origin.pack.json` - a store pack (section 3) holding every rule constant
  in the table below;
- `origin.env` - the non-secret the pilot store settings below (no tokens, no
  passwords, nothing read from production `.env`);
- `forbidden_tokens.txt` - optional extra words for the fresh-store test
  (staff names), one per line.

Both are produced by **`backend/scripts/packs/extract_legacy_pack.py`**
(committed, no data inside): `--legacy-backend <dir>` imports the constants
from an unpacked copy of the import commit
(`git archive 56e095c backend | tar -x -C <tmp>`), so no pilot-store
repository is read; `--out-dir $STOREOPS_PRIVATE_DIR`. MT8 runs it once
**before** removing the constants and checks the pack against the old
functions (3.6).

| Legacy constant / literal (as of `56e095c`) | Becomes | The pilot store value goes to |
|---|---|---|
| store name in `po_export.py:103,220`, `po_send.py:56`, `checklist_public.py:251`, `invoice_submission.py:74` (`TRANSFER_DEFAULT_VENDOR`) | `STORE_NAME` (effective name) | `origin.env` |
| address / phone `po_export.py:32-33`, `po_send.py:57-58` | `STORE_ADDRESS`, `STORE_PHONE` | `origin.env` |
| `ask_bot.py:53-54`, `weekly_digest.py:97-98` description | `STORE_DESCRIPTION` | `origin.env` |
| `reports.py:108` old-app message, `toast_pdf.py:411` producer | `PRODUCT_NAME` (product, not store) | - |
| `FIRST_REAL_SALES_DATE` | `FIRST_SALES_DATE` | `origin.env` |
| `CLOSEOUT_HOUR` | `BUSINESS_DAY_CUTOFF_HOUR` | `origin.env` |
| `America/New_York` in `recurrence.py:39`, `tasks_models.py:54` | effective `store_timezone` | `origin.env` (`STORE_TIMEZONE`) |
| `toast_invoice_email` default | `AP_INBOX_EMAIL` | `origin.env` |
| `public_base_url` default | `PUBLIC_BASE_URL` | `origin.env` |
| `LABELS_APP_URL` (website), `LABELS_BASE_URL` (app) | `LABELS_URL` | `origin.env` |
| `flyers_dir` default | `FLYERS_DIR` | `origin.env` |
| `po_send.py:413` fallback domain | `EMAIL_FALLBACK_DOMAIN` | `origin.env` |
| Ask bot on | `AI_ENABLED=true` | `origin.env` |
| `EXCLUDED_CATEGORY_GROUPS` (`fix_issues.py:59`) | pack `menu.excluded_category_groups` | `origin.pack.json` |
| `KITCHEN_GROUPS` (`delivery_suggestions.py:115`) | pack `menu.kitchen_groups` | pack |
| `GROCERY_MENUS` (`insights.py:68`) | pack `menu.grocery_menus` | pack |
| `po_sections.py:25-150` + `section_for` | pack `po_sections.rules` | pack |
| `HOUSE_VENDOR_PREFIXES` (`schedule_suggestions.py:139`) | pack `house_vendors.prefixes` | pack |
| `channel_of` (`insights.py:454`) | pack `channels` | pack |
| `DOC_LEADS`, `DOC_FALLBACK`, `SECTION_PROPOSALS`, `SECTION_NOTES` (`issue_leads.py:91-200`) | pack `issue_leads` | pack |
| flyer `CATEGORIES` (`flyers.py:77`) | pack `flyer_categories` | pack |
| built-in checklists (`checklist_builtin.py`, migration 0039) | pack `checklists` | stays in the pilot store's DB (migration rows); pack lists none |
| WhatsApp roles (migration 0044, `whatsapp_groups.py:47`) | pack `whatsapp_roles` | stays in DB; pack lists the same two keys |
| category rules (migrations 0034, 0037) | pack `category_rules` | stays in DB; pack lists none |

Parity runs (MT7/MT8 exit criterion) export `origin.env` into the
environment and set `STORE_PACK_FILE=$STOREOPS_PRIVATE_DIR/origin.pack.json`,
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
fake token/password strings never appear in the body); the pilot store names never
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
| `$STOREOPS_PRIVATE_DIR/origin.pack.json` | `origin_legacy` | Parity only, **never committed** (1.6) |

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
every menu counts as grocery). MT8 additions: `kitchen_menu_prefixes` (POS
menus whose lower-cased name starts with one are the kitchen's - Insights
kitchen items; generic `[]`) and `kitchen_label` (generic `"Kitchen"`, used in
the menu map's `kitchen_rule` text). `po_sections.other_section` must be
`"Other"` in version 1. `issue_leads.document_title` (generic `"Suggested
leads"`) is the Leads page's document title.

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
`docker compose run --rm --no-deps -v "$PWD/xyz.json:/packs/xyz.json:ro" store python -m app.store init --code XYZ --pack /packs/xyz.json`
(the compose service is `store`; corrected by the identity verifier).

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
  `$STOREOPS_PRIVATE_DIR/origin.pack.json` is absent): the private pack
  reproduces the old `section_for`, `channel_of`, menu sets, house vendors and
  lead proposals exactly - over every `(group, category, subcategory, name)`
  tuple in a scratch copy of the catalog when one is given
  (`STOREOPS_PARITY_DB`) plus synthetic cases for every rule.
- **Fresh-store test** (`tests/test_fresh_store.py`, plan 9.1): new SQLite
  file, `alembic upgrade head`, `store-init` for `STOREOPS_TEST_TOAST` with
  `packs/test_toast.json`; then every text column of every table (except
  `alembic_version`) is searched, case-insensitive and on word boundaries, for
  the committed list the origin store's names,
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
`DIRECTORY_EXTRA_API_HOSTS`; never `<blocked-domain>` (rule 0.3); colours match
the hex pattern; `timezone` loads. A store that is retired is removed from the
file (it then answers 404 like an unknown code).

### 4.2 `GET /v1/stores/{code}`

Production: the Cloudflare Worker `directory/worker/` (MT10 decision; its
bodies are generated by `build_static --worker-data`, 4.3). Reference
implementation, dev server and self-host option: `backend/app/directory/`
(its own ASGI app, `uvicorn app.directory.main:app --port 8090`; never
mounted in a store server). `tests/test_directory.py` replays the same
requests against both and requires identical status, body and headers.

Code handling: trim ASCII whitespace (space, tab, CR, LF, VT, FF - the same set in the service and the Worker), upper-case, `-` -> `_`, then the pattern
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
application/json`, `Access-Control-Allow-Origin: *` (GET only). Every JSON
answer on `/v1/stores/*` (errors too) carries `Access-Control-Allow-Origin: *`
and `X-Content-Type-Options: nosniff`; `304` carries `ETag`, `Cache-Control`
and no body. Any other path answers `404` `{"error": "not_found", ...}`
(`no-store`). The Worker also answers `HEAD`; other methods get `405`.

Rate limit: per client IP (`DIRECTORY_CLIENT_IP_HEADER` when set, else the
socket peer), sliding windows of `DIRECTORY_RATE_PER_MINUTE` (10) and
`DIRECTORY_RATE_PER_HOUR` (100); every request counts, 404s included, so codes
can't be enumerated quickly. In memory (one directory process). The owner
also adds a Cloudflare rate-limit rule (section 9).

Other routes on the directory host:
- `GET /healthz` -> `{"ok": true, "stores": <count>}`.
- `GET /.well-known/apple-app-site-association` and
  `GET /.well-known/assetlinks.json` (4.4).
- `GET /s/{code}` (also `/s`, `/s/`) -> a small HTML page "Open this store in
  the StoreOps app" with App Store / Play links (QR codes point here, 5.4).
  Unknown codes get the same page (no lookup, no leak): the code is read from
  the URL by an inline script (textContent only) under a hash-based CSP, so
  all three forms serve identical bytes. An `APP_STORE_URL` that is not a
  listing (`.../id<N>`) or a `PLAY_STORE_URL` for another package is ignored
  with a warning.

### 4.3 Static fallback

`python -m app.directory.build_static --stores directory/stores.json --out <dir>
[--worker-data directory/worker/src/data.json] [--refresh-branding]` writes:
- `<dir>/v1/stores/<CODE>.json` - the exact `200` body above;
- `<dir>/.well-known/apple-app-site-association`, `<dir>/.well-known/assetlinks.json`;
- `<dir>/s/index.html` (the open-in-app page, code read from the URL in JS);
- `<dir>/_headers` (Cloudflare Pages header rules: `application/json` for the
  JSON files and the AASA file, the cache headers above);
- `<dir>/_redirects` (`/s/*` rewritten to `/s/index.html`, status 200);
- `<dir>/404.html` (required: without a top-level 404 page Pages treats the
  site as a single-page app and would answer unknown codes `200`);
- with `--worker-data`: the Worker's bundled data module (every body,
  ETag, error body, header value and rate setting; gitignored).
Stale `v1/stores/*.json` files for codes no longer in the file are deleted.
`--refresh-branding` fetches each store's `/api/app/branding` and copies the
branding subset (dev only against fake stores; never run against a real store
from tests).

Fallback hosts (app defines, 5.1): primary `https://directory.<PLATFORM_DOMAIN>`,
static `https://static.<PLATFORM_DOMAIN>` - both serve `/v1/stores/<CODE>`
(static adds `.json`).

### 4.4 `apple-app-site-association` and `assetlinks.json`

Served with `Content-Type: application/json`, `Cache-Control: public,
max-age=3600`, no redirect, from the directory
host, the static host, the bare `<PLATFORM_DOMAIN>`, **and every store
server** (route `app/routers/well_known.py`, MT10, so a store host can be
added to Associated Domains later). Only `/s/*` is claimed: the app has no
`/c/` route (MT11 As built), so public checklist links `/c/...` must keep
opening in the browser; add `/c/*` here only together with an app route.

```json
{
  "applinks": {
    "details": [
      {
        "appIDs": ["<APPLE_TEAM_ID>.com.kloudc.storeops"],
        "components": [
          {"/": "/s/*", "comment": "store-code links and QR codes"}
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
validation (bad host, `<blocked-domain>`, duplicate code), 503 on a broken file,
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
  `GET /api/checklists/schemas`, not hard-coded pilot-store types.

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
dropped for the Clover and DEMO stores by the M1 job gating. (Verify pass:)
a provider with only a fallback entry has no connector, so the server's own
gates - `require_capability`, `has_capability`, the job gating - use
`gating.usable_capabilities()`: the advertised set when the POS has an
adapter, else only `OFFLINE_CAPABILITIES` (`csv_catalog_import`). Branding,
`/me` `capabilities` and `allowed_features` still carry the advertised set;
a POS route answers 409 "isn't connected" and no POS job is registered.

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
| **MT7a identity** (server) | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.github/workflows/*`, `backend/app/assets/branding/*`, `frontend/public/` icons, `frontend/capacitor.config.json`, removal of the pilot store-only files (handoff 3 step 3 last list) |
| **MT7a identity** (app) | iOS/Android project ids and names (`com.kloudc.storeops`, "StoreOps"), app icons, `firebase_options.dart` placeholder (push off until configured), `pubspec.yaml` metadata (the Dart package name stays `pilot_app` until a single rename commit after all app batches merge) |
| **MT7** settings + branding | `backend/app/config.py`, `backend/.env.example`, `app/store/settings_view.py`, `app/store/branding.py`, `app/routers/branding.py`, `app/pos/capabilities.py` (`CLOVER_`/`CSV_CAPABILITIES`), `app/pos/gating.py` (fallback entries, `FEATURE_STORE_FEATURES`), `app/routers/auth.py` (`/me` keys), `app/services/push.py` (`store`), the string sites in 1.6's settings rows (`po_export`, `po_send`, `checklist_public`, `reports`, `invoice_submission`, `toast_pdf`, `ask_bot`, `weekly_digest`, `toast_email`, `team/recurrence.py`, `team/tasks_models.py`, `pos/sync/accuracy.py`, `pos/sync/open_orders.py`, `services/insights_data.py`), fake-store `.env` + `.branding.json` fixtures |
| **MT8** pack + identity | `alembic/versions/0062_store_identity.py`, `app/store/identity.py`, `app/store/pack.py`, `app/store/__main__.py`, `backend/packs/*` (except `demo.json` content), `scripts/packs/extract_legacy_pack.py`, rule sites in 1.6's pack rows (`fix_issues`, `delivery_suggestions`, `insights` constants + `channel_of`, `schedule_suggestions`, `po_sections`, `issue_leads`, `flyers`, `whatsapp_groups`, `checklist_schemas`), the identity call in `app/main.py` |
| **MT9** website | `frontend/src/**`, `frontend/index.html`, `frontend/e2e/**`, `backend/app/glossary.py` |
| **MT10** directory | `backend/app/directory/**`, `directory/**`, `app/routers/well_known.py` (+ its include line in `app/main.py`) |
| **MT11** app login/switch | `lib/core/tenant/**` (except `capability_gate.dart`), `lib/core/config/env_config.dart`, `lib/core/api/**`, `lib/core/storage/**`, `lib/core/notifications/push_token_storage.dart`, store-code screen, More > Switch store |
| **MT12** app tz/brand/push | `lib/core/utils/store_time.dart` + its call sites, `lib/core/theme/**`, `lib/core/tenant/capability_gate.dart`, tile gating in `store_sections.dart` / `feature_index.dart` / tabs, `push_target.dart`, `notification_router.dart`, the pilot store strings in `lib/` |
| **MT13** demo + release | `backend/app/pos/adapters/csv_only/**`, `backend/packs/demo.json`, `backend/scripts/demo/**` (made-up data generator, nightly reset), DEMO compose service, release notes; may add its reset job name to `POS_NEUTRAL_JOB_FUNCS` (one line) |

Heavy commands go through `~/.claude/jobs/e26a1611/tmp/m1heavy.sh`:
- backend (from `~/storeops/storeops/backend`):
  `.../website/backend/.venv/bin/python -m pytest -q` (3.9) and
  `.../website/backend/.venv312/bin/python -m pytest -q` (3.12);
- website: `node --test` suites and `npx vite build` from `frontend/`
  (`node_modules` is the symlink);
- app: `flutter analyze` and `flutter test --concurrency=2` from
  `~/storeops/storeops-app`.
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

- Server: `backend/tests/test_no_origin_names.py` searches `backend/app`
  (not `alembic/versions`, not `app/services/checklist_builtin.py`),
  `frontend/src`, `frontend/index.html`, `frontend/capacitor.config.json` for
  the hashed origin-name list (tests/origin_names.py)
  (case-insensitive; the last added by the verify pass - the fresh-store
  test's list). Where `$STOREOPS_PRIVATE_DIR/forbidden_tokens.txt` exists, its
  words (staff names) must not appear at all in `backend/app` or
  `frontend/src` (skips in CI).
  Every hit must be on the allowlist `backend/tests/data/origin_name_allowlist.txt`
  (`<path>:<exact substring>` per line, e.g. the legacy setting names in
  `config.py`). MT7, MT8 and MT9 each shrink the list for their files; at the
  M2 exit it holds only legacy setting names and comments explaining them.
- App: `test/identity/no_origin_names_test.dart` does the same over `lib/`
  (allowlist `test/identity/allowlist.txt`), owned by MT12; MT7a-app clears
  the identity files.

---

## As built - MT7a part 1 (repositories)

**Repositories (both private, owner `Sundar6264-ux`, default branch `main`):**
- `storeops` (server + web): https://github.com/Sundar6264-ux/storeops
- `storeops-app` (Flutter app): https://github.com/Sundar6264-ux/storeops-app

Local paths: `~/storeops/storeops`, `~/storeops/storeops-app`.
Each has exactly one remote, `origin`, pointing at the StoreOps GitHub
repository. No remote points at the pilot store's GitHub or local folders.

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
  holds the pilot store Firebase project's client config, including a Google
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
- `.claude/skills/pilot/` replaced by `.claude/skills/storeops/SKILL.md`
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
  **`backend/app/assets/pilot-icon.jpg` stays** because
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

**Name grep test** (`backend/tests/test_no_origin_names.py`, section 9.2):
- identity scope, zero hits allowed: `README.md`, `Dockerfile`,
  `.dockerignore`, `docker-compose.yml`, `.env.example`,
  `frontend/{index.html, capacitor.config.json, package.json, .env.mobile}`,
  `frontend/src/pages/LoginPage.jsx`, and the folders `.claude`, `.github`,
  `docker`, `frontend/public`, `frontend/android`, `frontend/ios`,
  `backend/app/assets/branding`, `backend/scripts/{launchd,backup,branding}`;
- code scope (`backend/app`, `frontend/src`): every hit must match
  `backend/tests/data/origin_name_allowlist.txt` (`<path>:<substring>`);
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
- Removed `MOBILE_APP_CONTEXT.md`, `PILOT_FLUTTER_CLAUDE_HANDOFF.md`; new
  `README.md`; `docs/backend-api-map.md` describes hosts generically;
  `docs/OWNER_SETUP.md` (Apple App ID + APNs key + App Store record, Play app
  + upload key, StoreOps Firebase project, build defines, per-store FCM
  service account).
- `pubspec.yaml`: description only; package name stays `pilot_app`.
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
- MT7: `po_export.py` letterhead off `pilot-icon.jpg`; `config.py`
  defaults (`DATABASE_URL`, `FLYERS_DIR`, `PUBLIC_BASE_URL`, ...); the
  allowlist lines of its files.
- MT8 / MT9 / MT12: their allowlist lines; the app's in-`lib/` strings
  ("the pilot store" in home/login text, `integration_test/` expectations).
- `e2e/regression/*` still uses the origin store's local paths (it runs
  against production copies on the origin machine); left as is.
- The Capacitor wrapper keeps Capacitor's default splash/icon art.

---

## As built - MT9 (website per store)

Branch `m2/mt9-website` (worktree `~/storeops/worktrees/mt9`),
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
  `test_no_origin_names`, `test_flyers`, `test_insights`, `test_ops_fix_issues`,
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
The three hard-coded checklist search entries (`checklist_pilot`, ...)
are removed: a store's checklists are reached through Daily Checklists,
which lists the server's schemas.

**Push (5.6).** `PushTarget.store` (upper case). AppShell: another store's
push -> dialog "This alert is for <saved name or code>. Switch store?"; Yes
-> `switchStore(toCode:)` (lands on that store's sign-in, route not carried);
No -> dismissed. The Team poller ignores another store's foreground push.

**Names (9.2).** `test/identity/no_origin_names_test.dart` over `lib/`
(pattern of 9.2; the package name `pilot_app` and `_`-separated legacy
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

## As built - MT7 (store settings, branding) and MT8 (store pack, identity)

Branch `m2-mt7-mt8` (worktree `~/storeops/wt-mt7-mt8`), rebased on `main` after MT7a part 2 and MT9, merged fast-forward.

### MT7 - store settings, de-hardcode, branding

**Settings** (`backend/app/config.py`): every field of 1.1, with the validators
of 1.2 (store code pattern + upper-casing, hex colours, IANA zone,
`FIRST_SALES_DATE`, cutoff 0-23, and the refused legacy domain on
`PLATFORM_DOMAIN` / `PUBLIC_HOST` / `PUBLIC_BASE_URL` / `LABELS_URL` /
`SESSION_COOKIE_DOMAIN`). Defaults changed as marked in 1.1 (`./storeops.db`,
`uploads/flyers`, blank AP inbox, blank public base URL, blank app-store URLs).
**Interface addition:** `DOCUMENT_LOGO_FILE` (local image for the PO PDF
letterhead; blank = the bundled StoreOps icon `assets/branding/logo-128.png`).
`backend/.env.example` lists every setting (generated from `Settings`, secrets
blank); `tests/test_store_settings.py` fails if one is missing.

**Derived values** (`app/store/settings_view.py`): the functions of 1.2 plus
`store_slug` (file-name prefix), `sales_floor` / `first_sales_date`,
`business_day_cutoff_hour`, `store_timezone_name`, `ap_inbox_address`,
`play_store_url` / `app_store_url`, `logo_url` / `icon_url`, `store_email`,
`email_fallback_domain`.

**Sites moved to settings** (the 1.6 settings rows, plus what a full grep of
`backend/app` found):

| Was | Now |
|---|---|
| PO PDF header/footer name, address, phone, icon (`po_export`) | `STORE_NAME` / `STORE_ADDRESS` / `STORE_PHONE` (blank lines left out), `DOCUMENT_LOGO_FILE`; text is XML-escaped |
| PO email subject/body/signature, WhatsApp caption, From name, Message-ID domain, Reply-To (`po_send`) | same settings; `EMAIL_FROM_NAME`; `EMAIL_FALLBACK_DOMAIN`; Reply-To = `STORE_EMAIL` or (blank, today's behaviour) the sending address - **the contract said "no Reply-To" for blank; today's code always set it, so that is kept** |
| SMTP host/port/TLS + Gmail names (`po_send`, `toast_email`) | `SMTP_HOST` / `SMTP_PORT` / `SMTP_SSL` (STARTTLS when false), `SMTP_USERNAME` / `SMTP_PASSWORD` falling back to `GMAIL_*` |
| Public checklist "not active" page brand (`routers/checklist_public`) | store name + `LOGO_URL` (CSP `img-src` adds the logo's origin when absolute) |
| Old-app margin message (`routers/reports`), PDF producer (`toast_pdf`), FastAPI title | `PRODUCT_NAME` |
| Transfer default vendor (`invoice_submission`), automatic-task actor `SYSTEM_NAME` (`team/tasks_service`, `auto_tasks`, `issue_leads`), removed-account task event (`team/bootstrap`) | effective store name |
| Team rules text (`team/rules`) | store name; "call <STORE_PHONE>" only when set |
| Ask bot + weekly digest prompts | `STORE_NAME` + `STORE_DESCRIPTION` (blank = "a retail store") |
| Sales export file name `pilot-sales-...` (`routers/sales`) | `<store_slug>-sales-...` |
| `FIRST_REAL_SALES_DATE` (`pos/sync/accuracy`, `pos/sync/open_orders`, `insights_data`, `insights`) | `FIRST_SALES_DATE` via `sales_floor()`; blank = no floor. Open-checks full scan without a floor starts at the oldest stored business date (max 365 days back) |
| `CLOSEOUT_HOUR` (`accuracy`, `insights_data`, `insights`), delivery-suggestion snapshot fallback time | `BUSINESS_DAY_CUTOFF_HOUR` |
| Recurring-task default zone (`team/recurrence`, `team/tasks_models` column default) | effective store time zone |
| WhatsApp role chat ids (`whatsapp_groups._env_chat_id`) | `whatsapp_role_chat_ids()` (`WHATSAPP_GROUP_CHAT_IDS` over the two legacy settings) |
| `public_base_url` reads (`main` invoice login redirect, `invoice_submission`, `checklist_public`, `issue_leads`, `inventory_api_readiness`) | `effective_public_base_url()` |
| `/api/app/config` `store_url` | blank `APP_STORE_URL` -> `null`; blank `PLAY_STORE_URL` -> Play listing of `APP_BUNDLE_ID` (shape unchanged) |
| Push app name `pilot-notifications` | `storeops-notifications` (internal Firebase app name) |

The old module-level names stay readable for existing imports and tests
through module `__getattr__` (PEP 562), always reading the current settings or
pack: `accuracy/sales_accuracy/open_orders/insights_data.FIRST_REAL_SALES_DATE`
and `CLOSEOUT_HOUR`, `invoice_submission.TRANSFER_DEFAULT_VENDOR`,
`tasks_service.SYSTEM_NAME`, `recurrence.DEFAULT_TIMEZONE`, `rules.RULES_TEXT`,
`reports.MARGIN_REMOVED_DETAIL`. `sales_accuracy` forwards the two names
explicitly (they are no longer module globals).

**Store features** (1.3): `settings_view.store_features()`;
`gating.FEATURE_STORE_FEATURES = {"ask_bot": {"ai"}}` used by
`features_for_store`; new `gating.require_store_feature("ai")` on `/api/ask`
(409 "Not available for this store."). Weekly digest with `AI_ENABLED=false`:
the numbers with a fixed headline and the additive key `"ai_off": true`, no
Anthropic call.

**Capabilities** (section 7): `CLOVER_CAPABILITIES`, `CSV_CAPABILITIES` in
`app/pos/capabilities.py`; `gating.PROVIDERS` gains `clover` ("Clover") and
`csv` ("CSV upload") fallback entries.

**Branding** (section 2): `app/store/branding.py` (`branding_payload`,
`compact_json`, `etag_for`) and `app/routers/branding.py` (`/api/app/branding`
with ETag/304/`Vary`, no cookie; `/branding/logo.png` and `/branding/icon.png`
serve MT7a's bundled `assets/branding/logo-512.png` and `app-icon-1024.png`).
Legal page URLs are returned as written (the KloudC pages end with `/`);
origins (`public_host`, `labels_url`) never end with `/`. `/api/auth/me` gains
`store_code` and `store_features`; `push.send_to_tokens` adds `store` through
`push.with_store` when `STORE_CODE` is set.

**Fake stores** (section 7): `backend/tests/fixtures/stores/{STOREOPS_TEST_TOAST,STOREOPS_TEST_CLOVER,DEMO}.env`
and `<code>.branding.json` (generated with `python -m app.store branding --env
<file>`; the Toast body is byte-for-byte the section 2 example);
`tests/store_fixtures.py` (`use_store`, `fake_store`, `use_legacy_pack`,
`legacy_checklist_seeds`).

### MT8 - store pack and store identity

**Pack** (`app/store/pack.py`): pydantic models with `extra="forbid"`,
readable `PackError` lines, section-level fallback to `generic.json`, compiled
rule closures (regexes compiled once per pack), the precedence of 3.3
(`STORE_PACK_FILE` > newest `setting_changes` `store_pack` row > generic; a
stored pack that no longer validates logs an error and falls back to generic),
`set_override` for tests. JSON Schema committed as
`backend/packs/store_pack.v1.schema.json` (a test keeps it in sync).
**Interface changes (additive):**
- `menu.kitchen_menu_prefixes` (list) and `menu.kitchen_label` (string): the
  Insights kitchen-menu rule (`insights_data.is_kitchen_path`, the legacy
  `KITCHEN_MENU_PREFIX`) and its `kitchen_rule` text. Generic: none / "Kitchen".
- `issue_leads.document_title`: the Leads page's document title (was a literal).
- `po_sections.other_section` must be `"Other"` in pack version 1 (the PO code
  treats that name as fixed).
- An empty `grocery_menus` means every non-kitchen menu is grocery.
- `similar_products` excludes `rules.json`'s groups (now `[]`) plus the pack's
  `excluded_category_groups`.

**Rule sites now read the pack:** `po_sections.section_for` / `BUILTIN_SECTIONS`
(-> `builtin_sections()`; `po_section_rules` calls it), `fix_issues`
(`excluded_groups()`), `store_vs_container`, `delivery_suggestions`
(`kitchen_groups`), `insights` (`GROCERY_MENUS`, `channel_of`),
`insights_data` (kitchen menus), `schedule_suggestions` (house vendors,
compared after the same normalisation as `vendor_key`), `issue_leads`
(`DOC_LEADS`, `DOC_FALLBACK`, `SECTION_PROPOSALS`, `SECTION_NOTES`, document
title), `flyers` (`categories()`), `whatsapp_groups` (`builtin_groups()`),
`checklist_schemas` (fallback registry = `store_seed_definitions()`, the
pack's checklists) and `checklist_admin.ensure_seeded` / `seed_rows` /
`rows_for_seeds`. Old constant names stay readable (module `__getattr__`).

**Packs:** `generic.json`, `test_toast.json`, `test_clover.json` (no channels or
checklists section: shows the generic fallback), `demo.json` (copy of generic,
`pack_id` `demo`, for MT13 to fill).

**Commands** (`python -m app.store`): `init`, `pack validate|load|show|schema`,
`identity show`, and `branding [--env FILE]` (prints the branding body; used to
generate the fixtures).

**store-init** (3.5): as specified, one transaction, then the marker. Removal
is narrowed so a second run is a true no-op even though the pack re-adds a
`store` role: 0034's rule only when `created_by IS NULL`, 0044's roles only when
`created_by = 'migration 0044'`. Inserts skip existing `checklist_type` / `key`
/ `(match_type, match_value)`. The pack row is written only when it differs from
the newest one. Pack checklists are written with note "From the store pack
(<pack_id>)".

**Identity** (1.5): migration `0062_store_identity`, `app/store/identity.py`
(model `StoreIdentity`, the functions of 1.5), called first in `main.lifespan`.

**Other batches' files touched (one-liners):** `docker-compose.yml`
(`UPLOADS_ROOT: /data/uploads`, so the identity marker lives in the store's
volume) and `.github/workflows/ci.yml` (the compose boot step runs
`alembic upgrade head && python -m app.store init --code CI --pack
packs/generic.json` first - without a stamp the server now refuses to start).
`backend/app/assets/pilot-icon.jpg` removed (nothing uses it; the parity
env points `DOCUMENT_LOGO_FILE` at a private copy).

**Name allowlist:** one entry left, `config.py`'s `REFUSED_DOMAIN =
"<blocked-domain>"` (the refusal of rule 0.3); every other backend line was moved
to settings / the pack or reworded (MT9 cleared the website and glossary).

### Legacy values (1.6)

`backend/scripts/packs/extract_legacy_pack.py --from-commit 56e095c --out-dir
$STOREOPS_PRIVATE_DIR [--catalog-db <scratch copy>]` writes, outside git:
`origin.pack.json`, `origin.env` (also `APP_STORE_URL`, `PLAY_STORE_URL`,
`DOCUMENT_LOGO_FILE`; no `LABELS_URL` - refused domain), `legacy_cases.json`
(the legacy functions' answers: 15,972 `section_for` cases incl. every
catalog item of the Oct 18 copy, the 48 built-in sections, 23 dining options,
9 vendor names, 28 menu/group pairs, the menu sets and the leads),
`forbidden_tokens.txt` (staff names) and the legacy letterhead image. The
script reads constants from the unpacked commit; the `section_for` if-chain is
restated as 19 pack rules and checked against the oracle.

### Tests

| Run | Result |
|---|---|
| Full backend suite, Python 3.9 (after the rebase on `main`) | **2566 passed, 19 skipped, 0 failed** |
| Full backend suite, Python 3.12 (same tree) | **2566 passed, 19 skipped, 0 failed** |
| Same modules with no private pack (what CI sees) | the `legacy_pack` tests (29 functions, 31 cases) and `test_store_pack_legacy.py` (8) skip with a reason |

New test files (78 tests): `test_store_settings` (12), `test_branding` (13),
`test_store_identity` (12), `test_store_pack` (17), `test_store_pack_legacy`
(8), `test_fresh_store` (7: `alembic upgrade head` on a new file +
`python -m app.store init` in child processes, every text column searched for
the five legacy names plus the private staff-name list: 0 hits; stamp, marker,
no-op second init, other code refused, unmigrated database refused, store data
refused unless `--adopt`), `test_blank_store_names` (9: every parity endpoint
except the glossary, public pages, PO PDF text, PO email and WhatsApp text,
prompts, team rules, export file name, checklist seeding - no legacy name with
default settings). Plus 2 in `test_po_send` (store settings in the PO text,
SMTP/Reply-To/From/Message-ID), 1 in `test_pos_gating` (Clover / CSV
fallbacks), 1 in `test_pos_sync_neutral` (full-scan start without a floor).

GitHub Actions on `main` (run `39154243122`, commit `b60cc24`): backend 3.9
and 3.12 **2525 passed, 60 skipped** each (the private-pack tests skip there),
website job green, Docker job green - compose boot runs `store-init` first
(`STORE_CODE=STOREOPS_CI`; the two-letter `CI` no longer passes the store-code
pattern), `/api/health` 200, healthy, restart on the existing database, and the
full suite inside the image 2525 passed / 60 skipped.

How the older suite runs on product defaults (`tests/conftest.py`):
- the checklist fallback serves the legacy built-in fixtures
  (`checklist_builtin`) unless a test is marked `pack_checklists`;
- 14 modules (12 whole, 2 per test) written around two WhatsApp roles, six
  flyer categories and kitchen groups use `suite_pack` (`tests/fixtures/packs/suite.json`,
  made-up names; their test data now says "KITCHEN" / "MainStore");
- tests of the origin store's own rules (PO section map, Fix Issues lead
  document, category corrections) are marked `legacy_pack`: the private pack +
  settings, skipped where those files are absent;
- `AI_ENABLED=true` in the test environment (the Ask bot / digest tests
  predate the switch); fake-store tests switch it per store.


### Parity

`scripts/parity` on a fresh read-only copy of the production database (Oct 18
12:56, migration 0059, 128,561 order lines), migrated once to 0061 with the
import commit's migrations so both sides start from the same rows (the
0060/0061 backfills stamp `created_at` with the time they run; recording both
sides with `--migrate` from 0059 showed only those timestamp columns).
Baseline = import commit `56e095c` (= `multistore-m1`) with its own defaults;
new = this branch with `origin.env` exported, `STORE_PACK_FILE` = the
private pack, `STORE_CODE` blank, `--migrate` (0061 -> 0062).

| Python | Files | DIFFERENCES | Additions (allowed) |
|---|---|---|---|
| 3.9 | 114 | **0** | `auth.me` `store_code`, `store_features`; table `store_identity` (both fingerprints) |
| 3.12 | 114 | **0** | the same 4 |

All 110 endpoints 200 on both sides, 0 network attempts, 0 background threads;
`--strict` lists exactly the 4 additions. After rebasing on `main`, a third
recording (3.9) differs from the baseline only in 10 `/api/glossary` text
fields - MT9's store-neutral help wording (`glossary.py`, owned by MT9), not
these batches. The database copies and recordings were deleted afterwards.


### Deferred

- `checklist_builtin.py` stays (migration 0039 imports it) and so do the
  legacy fixtures the older checklist tests use; new checklist tests should use
  `pack_checklists` + a fake store.
- `tests/data/prod_category_combos_2026-09-30.json` (category combinations of
  the origin store's catalog, from the import) is still committed; a made-up
  replacement would let `test_every_prod_catalog_combo_has_a_section` stay.
- The `legacy_pack` tests and `test_store_pack_legacy` skip in CI. CI could
  generate the private files on the fly (`extract_legacy_pack.py
  --from-commit 56e095c`, no catalog) if the owner wants them there.
- `app/data/similar_products` vocabulary (brands, synonyms) is South Asian
  grocery vocabulary from the origin catalog; not names, left as product data.
- Website and app read `labels_url` / `store_features` from branding (MT9,
  MT11/MT12); the server side is done.
- Docker deploys: a new store needs `python -m app.store init --code <CODE>
  --pack <pack.json>` once before its first start (CI does it); README
  wording is MT7a's.


## As built - website verification (adversarial pass on MT9)

Branch `m2/verify-website`, merged fast-forward into `main`. Website files
only, plus the MT9 e2e harness; no server code changed.

Confirmed and fixed:
- **Overview closing tiles were hard-wired to the origin store.** They asked
  for `store_closing`, `kitchen_closing` and `kitchen_fsa`,
  so every new store read "None yet" forever (and requested types it doesn't
  have). The name scan missed it (underscore form). Now
  `components/closingChecklists.js`: the active checklists with
  `settings.closing` from `GET /api/checklists/schemas`, `whatsapp_group ==
  "kitchen"` ones on a Kitchen closing tile, the rest on Store closing, no
  tile when there are none.
- **Overview "Today's sales" ignored gating.** It called the live today
  endpoint on every store; it now needs `items_sold_today`
  (`live_today_sales`), like Items Sold's Today choice.
- **Device time zone leaks.** Open Orders times and the Overview sync line
  formatted clock times in the viewer's zone; Vendor Calendar's "today" was
  the UTC date (tomorrow on a US evening) and its grid keys were
  `toISOString()` of local midnights (a day early east of UTC); Fix Issues
  ranges and chat "Yesterday" stepped a device Date back 24h (skips a store
  day across DST when the device is in another zone). All now use the store
  zone (`inStoreZone`, `storeTodayStr`, `storeDateOffsetStr`).
- **Origin-store names in visible text:** a staff first name and a vendor
  name in Help, FSA-specific Help wording, a real vendor in the Ask bot
  example questions; staff names in comments and node test fixtures.
- **Gating gap:** Inventory from <pos> "Check barcodes now" needs
  `catalog_change_stamp` (409 on Clover); new element `menu_recheck`.
- **Favicon:** the branding swap left index.html's `type="image/svg+xml"` on
  the store's PNG icon; the type is dropped.
- **e2e harness:** fake stores now run `python -m app.store init` with their
  pack; `seed.py` no longer deletes the pack's "store" WhatsApp group on a
  stamped store.

Tests added: `closingChecklists.test.mjs`; `noStoreNames.test.mjs` catches
`store`/`kitchen` forms and fails on any hour-showing
`toLocaleString`/`toLocaleTimeString` without the store zone (both scans
fail on the pre-fix tree); Playwright checks the closing tiles and that
Today's sales appears exactly when the store has `live_today_sales`.

Numbers: website `node --test` 231/231; `vite build` OK; backend
`test_no_origin_names.py` + `test_glossary_store_words.py` pass (3.9);
Playwright (two fresh fake stores via store-init, 4 roles, 1280/375):
674 checks, 0 failures; server-text hits 46 (MT9 run: 81), all server
strings saying "Toast" on the Clover store.

Found, not fixed (server, MT7/MT8 files):
- `app/services/checklist_public.py` `SUGGESTED` holds two origin-store staff
  first names and the origin checklist types (dead for a new store, but
  staff names ship in the product).
- `notification_triggers.CLOSING_CHECKLIST_TYPES` (unused constant) and
  `models.py` comments name origin checklist types.
- Clover fake store: `/api/sales/items-sold` today 500/503 and
  `/api/orders/employees` 500 (no Clover connector on `main`; MT13 branch
  has the items-sold fallback). Notification descriptions say "Toast" on a
  Clover store.
- Website has no AI consent notice before the Ask bot (the app has one,
  MT13); not in the contract for the website.
- Vendor names (not staff) remain in a few node test fixtures and comments.
---

## As built - Verify (identity, adversarial)

Branch `verify/identity` in both repositories (worktrees
`~/storeops/worktrees/verify-identity{,-app}`). Each item was
first confirmed with a test that fails, then fixed.

**Confirmed and fixed (server + web):**
- **Docker `UPLOADS_ROOT`.** Compose pinned `UPLOADS_ROOT=/data/uploads`
  (MT8 identity marker), the image did not. With the Dockerfile's own
  `docker run -v storeops-data:/data` usage, the marker went to
  `/app/backend/uploads`: outside the volume, and not writable by the
  non-root user. Fixed in the Dockerfile `ENV`; `tests/test_docker_config.py`
  keeps every compose path pin equal to the image default.
- **Litestream sidecar ran as root** on the shared volume (root-owned
  `-wal`/`-shm` when it opens the database first). Now `user: "10001:10001"`
  (the store's uid), checked by the same test file.
- **Staff names in product code.** `checklist_public.SUGGESTED` prefilled two
  of the origin store's staff first names as a public link's allowed names
  (MT12 removed the same names from the app). A new list link now starts
  with no names. The origin store already has that link, so parity is
  unchanged.
- **Origin-store business data in git.**
  `tests/data/checklist_real_answers_2026-09-30.json` (every stored
  checklist submission) and `prod_category_combos_2026-09-30.json` moved to
  the private folder (1.6). Their tests now skip without that folder, and the
  catalog-combo test runs on the legacy pack (on the suite pack it passed
  trivially). Both files remain in the pushed history (owner decision).
- **A test reached into the origin store's production folder.**
  `test_migration_0040_up_and_down` copied the newest `.backup` of the
  production database out of its live folder on every full run on this Mac.
  It now uses only the copies named in `MIGRATION_DEV_DB_COPY` /
  `MIGRATION_PROD_DB_COPY`. Two guards were added to the name test: no code or
  test may name that folder, and no origin data file may be in `tests/data`.
- **e2e regression defaults.** Suite B defaulted to the origin store's
  working copy (it built and served that code, not this repo's) and to
  copying its production database and flyer folder. Suite A defaulted to that
  database too. They now use this repository by default, and the source
  database must be named (`SUITE_B_SOURCE_DB`, `REG_PROD_DB`). The suites'
  origin-store UI expectations stay for the Regression stage.
- **Docs.** The README, compose and `.env.example` quick starts were missing
  the one-time `python -m app.store init` step. Without it, a store with
  `STORE_CODE` set refuses to start, so the container loops on restart. In
  3.4, the service name in the example was wrong (`api` instead of `store`).

**Confirmed and fixed (app):**
- **Rule 0.3 in `ApiHostPolicy`.** `<blocked-domain>` was refused only because
  it is not under the default `PLATFORM_DOMAIN`. A build with
  `PLATFORM_DOMAIN` or `EXTRA_ALLOWED_API_HOSTS` naming it would have sent
  session tokens there. It is now refused outright (any case, trailing dots
  too). The one line in `lib/` is on the name allowlist.
- The `firebase_options.dart` comment now points at the setup doc that exists
  (`docs/OWNER_SETUP.md`).

**Dismissed:**
- The pilot store's Firebase client config in the app's history: already
  documented in MT7a part 1 (client identifiers, not secrets).
- The Capacitor wrapper and the Flutter app share `com.kloudc.storeops`.
  They did the same under the origin ids, and only one of them ships.
- Legacy `_`-separated checklist type ids and `WHAPI_STORE_*` setting
  names are allowed by contract 9.2.
- No secrets matched in any branch of either repo (Anthropic, AWS, private
  key, GitHub, Slack and Google key patterns).

**Tests (this branch):**

| Run | Result |
|---|---|
| Backend, full, Python 3.9 | 2570 passed, 20 skipped |
| Backend, full, Python 3.12 | 2570 passed, 20 skipped |
| App, `flutter analyze` | no issues |
| App, full `flutter test --concurrency=2` | 5459 passed, 6 skipped, 0 failed |

The parity recording was not re-run. Nothing changed touches a recorded
endpoint for the origin store: its service link already exists, so it never
gets the suggestion. The rest of the changes are tests, docs and deploy
files.
## As built - Verify (app track, adversarial)

Repository `storeops-app`, branch `m2/verify-app`, rebased on `main` @ `b84f9f2` (after the identity
verifier's app commits) and fast-forwarded into `main`. Each fix has a test that failed before it.

**Confirmed and fixed**
- `b802819` **Cross-store bleed (5.5 step 5).** Flyer files/thumbnails
  (`flyers_providers.dart` `_bytesMemory`) and team photos
  (`team_media.dart` `_mediaMemory`) are kept in top-level maps outside the
  ProviderContainer, keyed by the server's relative URL. `/api/flyers/1/thumb`
  is another flyer at every store, so after a switch store B's flyer 1 drew
  store A's picture (the StoreOpsRoot restart does not touch top-level
  state). `StoreSwitcher.resetSession` now clears both and
  `ProviderFreshness` (the "Updated 2:08 AM" times). Test
  `test/core/tenant/store_memory_bleed_test.dart` (3).
- `7ebda2c` **Hard-coded US Eastern offsets (5.8).**
  `critical_dismissal.dart` `_wallToUtc` (the provisional "hide vendor"
  end time) tried UTC-4 then UTC-5; at Chicago/Los Angeles stores the vendor
  came back 1-3 h early until the server answered. Now
  `StoreTime.fromStoreWallClock`. Tests for Chicago and Los Angeles in
  `critical_provisional_test.dart`.
- `3846aa9` **Gating holes (6).** The Inventory Reports hub showed Price
  Change Log without the `catalog` capability (only its search entry was
  gated); Home's Today card fetched open checks for an admin at a store
  without `open_orders` (server 409). Both use `storeAllowsFeature`. Tests
  `inventory_reports_hub_gating_test.dart` (3), `today_card_test.dart` (+1).
- `9162376` **Gating hole (6), App Review path.** Home's first quick action
  "Print Label History" showed at stores without a labels site (Clover,
  DEMO) and opened a screen whose repository throws. Now
  `storeHasLabelsProvider`, like the Scan tab. Tests in
  `home_screen_test.dart` (+3).
- `eae10c3` **8 s lookup timeout (5.3).** `DioTenantHttp` set send/receive
  timeouts only; with no connect timeout a host that drops packets held
  the store-code screen for the OS default (~75 s) instead of falling back
  to the static copy. `connectTimeout` added. Test `tenant_http_test.dart` (2).
- `8401e31` **`APP_ENV` fail-safe (5.1).** Any value but the exact word
  `production` made a development build (http/localhost servers,
  `DEV_API_BASE_URL`), so `APP_ENV=prod` was quietly development. Now only
  `development` is. Test `test/core/config/env_config_test.dart` (2).
- `f8b1ad1` **Origin-store name in `integration_test` (9.2).** `app_test.dart`
  expected "the pilot store" on Home (now the tenant's short name) and its fakes
  used origin-store checklist titles.
- `8534e98` **DEMO wording.** The demo-login note said "nothing is sent to
  WhatsApp, Toast or the printer" and Upload Inventory asked for "the Toast
  Retail export" on the DEMO store (POS "CSV upload"). Store-neutral now.
  Test `test/identity/demo_store_wording_test.dart` (2).

**Checked, no change**
- Branding fixtures: the app copies equal `backend/tests/fixtures/stores/
  <CODE>.branding.json` on server `main` byte-for-byte (after JSON sort).
- `/api/app/config` key `min_supported_build`, push `data["store"]`
  (`with_store`), `/api/notifications/unregister`: app and server agree.
- Token host checks (auth and labels interceptors), no ATS/cleartext
  exceptions, bundle id `com.kloudc.storeops` everywhere, no
  `DEVELOPMENT_TEAM`, no Firebase or signing files tracked.
- Push for another store: dialog, no routing; gated push routes open
  "Not available".
- `ApiHostPolicy`'s explicit refusal of the origin store's domain came in
  with the identity verifier's `ac3cff4`; the store-code tests cover it.

**Found, not fixed (for later batches / owner)**
- Store-specific POS text: about 70 visible strings still say "Toast"
  (Items Sold, Open Checks, invoices "Close in Toast", Fix Issues "Toast
  ID", schedule suggestions, Insights). Clover and DEMO stores read them.
  The website did this in MT9; the app's deferred partial gates (MT12) are
  the natural place.
- Device-local "today" (not store time) in `vendorCalendarPageStart`,
  `reconciliation_providers.dart` default range, Supplier Projection and
  Log Purchase date defaults. Pre-M2; off by a day only when the phone's
  zone differs from the store's around midnight.
- `integration_test/app_test.dart` also has pre-M2 stale expectations
  ("Connected" pill, "Print Labels" title) - Regression stage.
- Test fixtures still carry origin-store data: real vendor names, staff
  usernames, OCR text of real invoices (`long_receipt_fixtures.dart`,
  `invoice_scan_parser_test.dart`), `ops.<blocked-domain>` links. They were
  in the kept history too; removing them is an owner decision.
- Git history (kept by decision) contains the origin store's Firebase iOS
  client config (`firebase_options.dart` before `c6bec1a`). Not a server
  secret, but it names the origin project; rewriting history needs the
  owner.
- `m2/mt13-legal` conflicts with `main` in `legal_links.dart`,
  `about_screen.dart`, `login_footer.dart` (MT11 already added
  `LegalLinks`/About/footer) - for the merge step.

**Tests:** `flutter analyze` no issues; full `flutter test --concurrency=2`
(heavy queue, before the rebase onto `b84f9f2`): **5476 passed, 6 skipped, 0 failed** (was
5454 + 4 at `ba945b3`; +18 new). No simulator run (Regression stage).
After the rebase: `flutter analyze` clean and test/core, test/identity and
the touched feature tests (447) pass. `main` = `8534e98`, pushed.




## As built - M2 backend verify (adversarial pass over MT7 + MT8)

Run on `main` @ `81e4f1d` (MT7a, MT7, MT8, MT9, MT11/MT12 docs merged; MT10
and MT13 still on their branches), rebased onto the website and identity
verify passes (`2aed034`), whose website name fixes it kept. Every finding below has a failing test
first, in `backend/tests/test_m2_backend_verify.py` unless noted.

| # | Finding | Fix |
|---|---|---|
| 1 | A Clover store (fallback entry, no adapter) kept `open_orders`, `stock_levels`... as usable: `/api/orders/open` and `/api/orders/employees` raised `UnknownPosProvider` (500), and the open-orders scans, the 16:00/21:30 open-checks summaries and the inventory snapshot stayed scheduled, failing on every run | `gating.usable_capabilities()` (section 7 note): routes 409 "Not available for this store yet: its point of sale (Clover) isn't connected.", those jobs are not registered, `has_capability` is false. Advertised capabilities unchanged (branding fixtures unchanged). Toast stores: identical |
| 2 | `store-init` skipped the "store data" check whenever the database was already stamped, so running `init` again (no `--adopt`) on an adopted store deleted its migration-seeded rows - its WhatsApp roles with their chat ids, its checklists | the check runs whenever `--adopt` is absent (contract 3.5 step 2 as written). A fresh store's second init (no data) is still a no-op |
| 3 | `Dockerfile` did not set `UPLOADS_ROOT`: a plain `docker run` (the command in its own header) put the identity marker in `/app/backend/uploads` - outside the volume and not writable by the `storeops` user, so `init` and the first start failed | found in parallel by the identity verify (`17e1f9c` sets `ENV UPLOADS_ROOT=/data/uploads`); this pass adds a test that every container path compose sets is the same in the Dockerfile |
| 4 | `.dockerignore` did not exclude the private pack files, so a local `docker build` with `backend/packs/private/` present would bake a real store's pack into the image | `backend/packs/private`, `**/*.pack.json`, `**/legacy_cases.json`, `**/forbidden_tokens.txt` added |
| 5 | Rule 0.3 bypasses: `//<blocked-domain>`, `x@<blocked-domain>`, `https://<blocked-domain>\@example.com` (browsers read the host as the legacy domain), `%2e` | `refused_host` normalises backslashes, scheme-relative URLs, user-info and percent-escapes |
| 6 | A store without WhatsApp and/or an AP inbox (both optional store features, 1.3) marked **every** invoice "failed" and pushed "Invoice failed to send" to every admin for each one; with WhatsApp off a bill could never be marked closed | section 1.3 note above (`WHATSAPP_OFF` / `AP_EMAIL_OFF`). Rows written before this, and every pilot-store-shaped row, read exactly as before |
| 7 | Origin-store names left in code the name test did not cover: the supplier of migration 0034 in three backend comments, and staff/owner first names and origin vendors (private `forbidden_tokens.txt`) in backend comments (the website ones were fixed in parallel by the website verify, `97567d1`) | reworded / made-up names; `test_no_origin_names.py` now also matches `shaz international` (one allowlisted line: `store/__main__.py`'s `LEGACY_CATEGORY_SUPPLIER`, which removes that row) and checks the private staff names over `backend/app` and `frontend/src` when the file is present |

Tests after the rebase (local; CI on the push): backend Python 3.9 and 3.12
**2589 passed, 20 skipped, 0 failed** each (19 new verify tests; the private
staff-name check runs here and skips in CI); website `node --test` 231/231,
`vite build` OK.

Dismissed after checking:
- Secrets: no key, token or private-key pattern in any of the 46 commits; both `.env.example` files have blank secrets.
- Python 3.9: `compileall` with 3.9 over `app`, `scripts`, `alembic` is clean; no `X | Y` annotations.
- Branding fixtures: the website's e2e fixtures and `frontend/src/test/fixtures` equal `backend/tests/fixtures/stores/*.branding.json` field for field.
- Every GET route (parity list plus all parameterless `/api` routes) for the three fake stores with the pack checklists: no legacy store name or staff name in any response; the only 5xx left are M1's (a Toast store whose POS is unreachable: `/api/orders/open`, `/api/orders/employees` raise `ConnectError`; `/api/inventory-api/compare` answers 503 with no CSV) - unchanged from the pilot store, not M2.
- Time zones: every scheduler, recurrence default, task column default, closeout and floor reads the store settings; no `America/New_York` left outside defaults and docstrings.
- Parity: the fixes are no-ops for a Toast store with both channels configured (connector present; the off-sentinels are only written when a store feature is off), so the MT7/MT8 parity result (0 differences) stands; `test_store_pack_legacy.py` passes locally with the private pack.

Deferred (other tracks / owner):
- `storeops-app` (MT12's files, app tree had other uncommitted work so it was not touched): `lib/features/invoices/data/invoice_scan_parser.dart` holds a built-in table of the origin store's vendors **with their phone numbers**; `help_content.dart` and `ask_bot_screen.dart` name the origin store's vendors; staff first names in four doc comments.
- MT10/MT13 merge: `docs/M2_CONTRACT.md` conflicts; MT13's `packs/demo.json` replaces main's copy; `DEMO_MODE` does nothing on `main` until MT13 merges.
- Store codes may end with `_` (pattern of 1.1), which makes a host label ending in `-` (not a valid DNS label); left as the contract's pattern.
- `SMTP_USERNAME` doubles as the From address; a provider whose login is not an email address (SES) needs a separate `EMAIL_FROM_ADDRESS` setting - a contract addition for later.

## As built - MT10 (store directory)

Branch `m2/mt10-directory` (worktree `~/storeops/worktrees/mt10`).

**Decision: Worker in production, Python as reference.** The directory runs
as a Cloudflare Worker (`directory/worker/`, no dependencies). Its bodies are
not computed in JS. `python -m app.directory.build_static --worker-data`
precomputes every store body, ETag, error body, header value and rate
setting into `directory/worker/src/data.json` (gitignored), which Wrangler
bundles. So the Worker, the static files and the Python service
(`backend/app/directory/`, `uvicorn app.directory.main:app`) serve the same
bytes. `tests/test_directory.py::test_worker_answers_exactly_like_the_service`
replays 20 requests against both (200 / 404 / 400 / 304 / 429 / healthz /
AASA / assetlinks / open page / unknown path, with a fake clock) and
requires the same status, body and 8 headers. It needs Node 18+ and is
skipped without it; GitHub's Ubuntu runners have Node.

**Files**
- `backend/app/directory/`:
  - `config.py`: `DirectoryConfig`, read with `getattr(get_settings(), ...)`,
    falling back to env, then the section 1.1 defaults.
  - `stores.py`: file checks and bodies.
  - `ratelimit.py`
  - `wellknown.py`
  - `open_page.py`
  - `main.py`: `create_app(config, clock=)`. Module-level `app` is lazy, so
    importing it never needs a stores file. A bad file or setting makes
    uvicorn exit with "Application startup failed".
  - `build_static.py`
- `directory/`:
  - `stores.example.json`: the 3 fake stores. DEMO is on
    `demo.storeops.invalid` with pos_name `CSV upload`.
  - `.gitignore`: `stores.json`, `dist/`, `worker/src/data.json`,
    `worker/wrangler.toml`, `.wrangler/`.
  - `README.md`: the owner's deploy steps.
  - `worker/`: `src/directory.js`, `src/index.js`, `test/directory.test.mjs`,
    `test/parity.mjs`, `wrangler.toml.example`, `package.json`.
- `backend/app/routers/well_known.py`: store servers serve the AASA and
  assetlinks bodies from settings. Its include line is in `app/main.py`.

**Other batches' files touched (one line each):**
- `tests/test_review_route_gates.py`: the 2 `/.well-known/*` routes are added
  to `PUBLIC`.
- `tests/data/origin_name_allowlist.txt`: 1 entry. The rule 0.3 guard
  `FORBIDDEN_DOMAIN` is the only literal of the legacy domain in MT10 code.

**Interface changes made in section 4 (in this commit)**
- `build_static --worker-data`, `_redirects`, `404.html`. Without a top-level
  404 page, Cloudflare Pages treats the site as an SPA and answers unknown
  codes 200.
- `not_found` JSON for other paths.
- `/s` and `/s/` also serve the page.
- Error responses on `/v1` also carry CORS and `nosniff`.
- The AASA cache header.
- Store-link validation on the open page.

**Behaviour notes**
- The stores file is re-read when its mtime or size changes, and
  `updated_at` is its mtime. A file that becomes missing or invalid gives 503
  `directory_unavailable` until it is fixed. An invalid file at startup stops
  the service.
- Rate limit: refused requests are not counted, so `Retry-After` is exact.
  - Python: in memory, LRU-capped at 50k clients.
  - Worker: per isolate, plus the optional `DIRECTORY_RATE_LIMITER` binding
    (`[[ratelimits]]`, 10/60 s; when it refuses, the Worker answers 429 with
    `Retry-After: 60`), plus a Cloudflare WAF rule (owner).
  - Client IP: Python uses `DIRECTORY_CLIENT_IP_HEADER` (the last value of
    a comma list such as `X-Forwarded-For`: the one the proxy in front
    appended; the client controls the rest) or the socket peer. The Worker
    uses `CF-Connecting-IP`.
- Before MT7 merges, `Settings` still has the legacy `app_store_url` /
  `play_store_url` defaults. The directory ignores both, with a warning, and
  never shows them: an App Store URL must be a listing (`/id<N>`), and a Play
  URL must name `APP_BUNDLE_ID`. Store servers' well-known route validates
  the owner ids the same way and leaves out malformed ones.
- The open page sets no `'unsafe-inline'`. Its CSP allows only hashes of
  the inline script and style. The code comes from the URL and is shown
  with `textContent` only.

**Tests**
- `tests/test_directory.py`: 52 tests.
- `node --test directory/worker/test/directory.test.mjs`: 7 tests.
- Full backend suite on the rebased branch: 2503 passed, 19 skipped on both
  Python 3.9 and 3.12.

**Owner steps** (`directory/README.md` "Deploy"; nothing deployed):
1. Domain on Cloudflare; set `PLATFORM_DOMAIN`.
2. Write `stores.json`.
3. Run `build_static --out ../directory/dist --worker-data ...`.
4. `npx wrangler deploy`, after copying `wrangler.toml.example` and setting
   the account and route.
5. `npx wrangler pages deploy ../dist` and add the custom domain
   `static.<domain>`.
6. WAF rate-limit rule on `/v1/stores/*`.
7. Serve AASA / `/s/*` on the bare domain (a Worker route or a Pages custom
   domain).
8. After the Apple Team ID and Play fingerprint exist, set `APPLE_TEAM_ID` /
   `ANDROID_CERT_SHA256` and rebuild.

### As built - MT10 verifier fixes (directory)

Branch `m2/mt10-directory` now has `main` merged in (MT7a, MT7/MT8, MT9 and
the MT11/MT12 docs). Conflicts in `app/main.py`, the public route list, the
name allowlist and this file were resolved by keeping both sides. Fixes, each
with a test that failed first:
- **Name test after the merge:** the directory had its own `<blocked-domain>`
  literal; it now imports `app.config.REFUSED_DOMAIN` (the one literal).
  Two tests set environment variables after `Settings` was cached; they now
  patch `Settings` (MT7 added the `DIRECTORY_*` / owner-id fields).
- **Worker/service parity:** the code is trimmed of ASCII whitespace only
  in both (Python `strip()` and JS `trim()` disagree on `\x1c`-`\x1f`,
  U+0085 and U+FEFF). An encoded `/` (`%2F`) in a lookup or `/s/` path is
  `404 not_found` in both. A trailing slash is `404 not_found` in the
  service too, not a `307` built from the Host header (FastAPI's
  `redirect_slashes` is off).
- **Rate-limit key:** with `DIRECTORY_CLIENT_IP_HEADER=X-Forwarded-For` the
  service keyed on the first entry, which the client writes; it uses the last
  entry (the one the proxy appended).
- **Association files claim only `/s/*`:** `/c/*` was claimed, but the app
  has no `/c/` route (MT11 As built), so with Associated Domains on, public
  checklist links would have opened in the app instead of the browser
  (section 4.4 updated).
- **`--refresh-branding`** validates the stores file before fetching, so an
  `http://` or legacy-domain `api_base_url` is never contacted.
- New test: the example stores file matches MT7's fake-store branding
  fixtures (`tests/fixtures/stores/*.branding.json`).

Note for the owner: the app calls the directory on every cold start and on
resume when the saved record is over 1 hour old. Staff on one store Wi-Fi
share an IP, so a large store can approach 10/minute (the Cloudflare
binding) at shift start. A refused refresh is harmless (the saved record is
kept), but a new device entering the store code in that minute sees "Too many
tries". Raise the limits, or exempt the store's IP in the WAF rule, if that
happens.

Tests after the verifier fixes: `tests/test_directory.py` 57 passed; Worker
`node --test` 7 passed; full backend suite 2622 passed, 19 skipped on both
Python 3.9 and 3.12 (run before the trailing-slash test was added; that file
was re-run on both afterwards).

## As built - MT13 prep (DEMO store, CSV-only connector, legal and AI notice)

Branches: server `m2/mt13-demo` (worktree `~/storeops/worktrees/mt13`),
app `m2/mt13-legal` (worktree `~/storeops/worktrees/mt13-app`).
Nothing is submitted or created with Apple, Google or Firebase.

**CSV-only connector** (`backend/app/pos/adapters/csv_only/`, plan 3.1a / 4.5):
- `CsvOnlyConnector`: `source="csv"`, `display_name="CSV upload"`,
  `capabilities = CSV_CAPABILITIES` (read with `getattr` from
  `app/pos/capabilities.py`, falling back to `{csv_catalog_import}` until MT7
  adds the constant). Every other connector method raises `NotSupported`;
  `health()` is always "configured". The package imports no HTTP client
  (tested).
- `parse_catalog_export(filename, content)` -> `CatalogRow`s for `.csv` /
  `.xlsx` (canonical template headers + common aliases: SKU, UPC, Qty on
  hand, Vendor ...); `canonical_csv(...)` renames aliased headers so a file
  goes through today's Inventory upload unchanged. `template.py`:
  `TEMPLATE_HEADERS` (all already in the upload page's `COLUMN_MAP`, tested),
  `template_csv(rows)`.
- Registered in `app/pos/registry.py` (`PROVIDERS["csv"]`, a 5-line factory -
  more than the one-line rule, noted here). With it, the M1 gating already
  gives a CSV store: `pos_name()` "CSV upload", capabilities
  `["csv_catalog_import"]`, Open Checks / Sales insights dropped, and the
  16 POS jobs not registered (seen in a live run of the DEMO server).
- **Fix outside MT13's files (one hunk):** `services/items_sold.get_items_sold`
  called the POS live for "today" on every store; a store without
  `live_today_sales` now serves today from stored orders (`source "stored"`).
  Toast stores unchanged (they have the capability). Found by the DEMO smoke
  check (HTTP 500 on `/api/sales/items-sold`).

**DEMO_MODE** (`app/demo.py`, contract 1.1): `demo_mode()` reads
`getattr(settings, "demo_mode", False)`; `is_demo_user()` is True for every
login when it is on (the read-only guard, flyers/PO/AI refusals all follow).
Which rows count as demo-made (`demo_user_ids`, `demo_made_clause`,
`hidden_from`) still follows `DEMO_USERNAMES`, so the store's made-up data
stays visible to the reviewer.

**Legal + AI notice** (server):
- `app/legal.py` + `GET /api/app/legal` (`app/routers/legal.py`, public, never
  401, `Cache-Control: public, max-age=300`; registered in `app/main.py` with
  one import + one include line; added to the PUBLIC list of
  `tests/test_review_route_gates.py`). Body:
  `{"schema": 1, "product", "company", "privacy_url", "terms_url"|null,
  "support_url", "ai": {"enabled", "provider": "Anthropic", "service":
  "Claude", "notice", "notice_version"}, "demo"}`. `notice_version` = first
  12 hex of sha256(notice) (the app uses the same rule).
- Settings read with `getattr` and the 1.1 defaults: `company_name`,
  `product_name`, `privacy_url`, `terms_url`, `support_url`, `ai_enabled`,
  `demo_mode`, and the **new row `ai_consent_notice` (`AI_CONSENT_NOTICE`)
  added to 1.1 in this batch** - MT7 adds the field to `config.py` and
  `.env.example`.

**DEMO store** (`backend/scripts/demo/`, `backend/packs/demo.json`, `deploy/demo/`, `docs/DEMO_STORE.md`):
- `generate_demo_data.py --out NEW_DIR [--seed --days 90 --items 400 --end-date --timezone]`:
  fresh SQLite at Alembic head (subprocess), the migration-seeded rows of
  `checklist_definitions(+versions)`, `category_overrides`, `whatsapp_groups`
  cleared, then `python -m app.store init --code DEMO --pack packs/demo.json`
  **when MT8's `app/store/__main__.py` exists** (else the same pack rows
  directly), then made-up data: 9 role logins + `appreview`, ~400 items
  (EAN-13 in the 20-29 in-store range), 90 days of sales lines
  (`orders.source = "csv"`), weekly catalog CSV uploads through the real
  `inventory_parser.parse_and_store` (snapshots + item_vendors), schedules,
  contacts (`.example`), purchase log, placed orders, cart, ~50 sample
  invoice PDFs (`fake_pdf.py`, each marked SAMPLE; channels recorded as
  `DEMO_NOT_SENT`), 2 checklists x 45 days, tasks + 3 templates, team chat,
  announcements, and a `setting_changes` row `demo_dataset` (marker).
  Full size: ~14 s, 39.7k order lines, 27 MB DB. Same seed + end date = same
  data (tested digest), apart from password hashes.
- Review login `appreview`: all features except `marketing_flyers`,
  `category_edits`, `sales_insights`; `can_assign` off. Password from
  `DEMO_REVIEW_PASSWORD` (env or the DEMO checkout's `.env`) or a random one
  kept in `<data-dir>/review_password` (mode 600, survives resets). Never in git.
- `smoke_demo.py --data DIR`: boots the app on the data with the DEMO
  settings, signs in as `appreview` (bearer), checks `/api/auth/me`, every
  parameterless GET (113) has no 5xx, 10 main pages non-empty, a write is
  403, POS-only routes 403/409.
- `reset_demo.py --data-dir DIR [--swap]`: data folder must be new/empty
  (only `logs/` allowed) or carry `.storeops-demo-data`; builds `next/`,
  smoke-checks a copy, swaps `live -> old -> deleted` only when the folder
  being replaced is a generated DEMO data set (marker file + marker row).
- `run_demo.py --data-dir DIR --port 8030 --reset-at 03:30`: one process
  for server + nightly reset (build while serving, stop, swap, start). It
  forces `STORE_CODE=DEMO`, `POS_PROVIDER=csv`, `DEMO_MODE=true`, blank
  credentials and discard-port base URLs (`SAFE_OVERRIDES`), refuses another
  `STORE_CODE` or `DEMO_MODE=false`, and exits non-zero if the server dies.
  The nightly reset is **out of process**, so `POS_NEUTRAL_JOB_FUNCS` is not
  touched.
- `deploy/demo/com.kloudc.storeops.demo.plist` (LaunchDaemon template,
  `__PLACEHOLDERS__`, Background priority), `deploy/demo/docker-compose.demo.yml`
  (service `demo`, volume `demo-data`, data in `/data/demo`, `127.0.0.1:8030`;
  uses MT7a's store image - `/app/backend`, its entrypoint runs the runner as a
  one-off command), `deploy/demo/demo.env.example` (no secrets).
- `packs/demo.json`: pack v1 per section 3.1 (DELI kitchen group, PO-section
  rules, 2 leads, 2 checklists in items form, role `store`, generic channels
  and flyer categories). Checklists validate with `checklist_engine`; the
  test also runs MT8's `load_pack_file` when it exists.

**Docs:** `docs/APP_REVIEW_NOTES.md` (draft notes with store code `DEMO`,
~2,700 characters), `docs/OWNER_CHECKLIST_STORE_RECORDS.md` (Apple App ID /
Team ID / APNs key / ASC record, Play signing fingerprints, Firebase project +
per-store FCM-only service accounts, domain/terms; nothing created),
`docs/DEMO_STORE.md` (runbook).

**App (`storeops-app`, minimal; MT11/MT12 own the rest of `lib/`):**
- `lib/core/config/legal_links.dart`: `productName`, `companyName`,
  `privacyPolicyUrl`, `supportUrl` (now `--dart-define`s with the same
  defaults), `termsUrl` (null until `TERMS_URL` is defined), `copyrightLine()`,
  and `class LegalLinks {company, privacyUrl, supportUrl, termsUrl?}` with
  `LegalLinks.fromJson(branding.legal)` (https only, blank -> defaults) and
  `LegalLinks.defaults` - **the type `TenantConfig.legal` (5.2) should use.**
- About: Terms row when set; footer "StoreOps · © <year> KloudC LLC".
  Login footer: "For store staff - accounts are issued by your manager." +
  Privacy + Terms (when set).
- `lib/core/legal/ai_consent.dart`: `defaultAiConsentNotice` (identical to
  the server's), `aiNoticeVersion()`, `AiConsentStore` (shared_preferences
  `ai_consent_accepted[.<scope>].<version>`; MT11 can pass the store code as
  scope), `ensureAiConsent(context, notice:, scope:)`, `AiConsentDialog`
  ("Before you ask" / "Not now" / "I agree" + Privacy link). Wired into
  `ask_bot_screen.dart` `_send` (3 lines): nothing is sent before agreement;
  "Not now" keeps the typed question. MT12 may pass the store's
  `/api/app/legal` notice later.

**Tests:** server `test_csv_only_connector.py` (18), `test_legal_and_demo_mode.py`
(7), `test_demo_data.py` (13: counts/marker, no forbidden names and only
`.example` e-mails in any text column, EAN-13, sample PDFs + hashes,
determinism, refusals, smoke on the data, pack, reset folder safety, swap,
runner env, reset time across DST, reset end to end). App
`test/core/legal/ai_consent_test.dart`, `test/core/legal/legal_links_test.dart`;
`audit_c_ask_bot_test.dart` pre-agrees the notice in `setUp`.
Full runs (rebased on `main` @ `6d2cc7f` / app `main` @ `783033a`): backend
3.9 and 3.12 each 2488 passed, 19 skipped; app `flutter analyze` clean,
`flutter test --concurrency=2` 5351 passed, 6 skipped, 2 failed - both in
`final_ios_metadata_test.dart`, fixed on app `main` by `93c51bf`; after
rebasing onto it that file, the new tests and the App Review journey pass.

**Deferred / for other batches:**
- MT7: add `ai_consent_notice`, `demo_mode` and the legal fields to
  `config.py` / `.env.example`; `CSV_CAPABILITIES` in `capabilities.py`;
  `gating.PROVIDERS["csv"]` fallback (the registered adapter already answers).
- MT8: when `app.store init` lands, the generator uses it automatically;
  re-run `tests/test_demo_data.py` then.
- MT12: `_exampleQuestions` in `ask_bot_screen.dart` still names a real
  vendor; Info.plist permission strings (MT7a app) still name the old store -
  `docs/APP_REVIEW_NOTES.md` "Permissions" must match the final strings.
- Upload page for CSV stores: `parse_and_store` still reads canonical headers
  only; an alias file needs `canonical_csv` first (not wired into the route).
- `is_demo_submission` (invoices/checklists) still keys on `DEMO_USERNAMES`;
  in the DEMO store every outside channel is blank anyway (forced by the runner).

## As built - MT13 verification (adversarial check of the demo track)

Both MT13 branches now carry a merge of `main` (server `2c43df8`, app
`2ce8a2c`), so they merge into `main` without conflicts.

- **Server merge:** `backend/packs/demo.json` is MT13's DEMO pack. On
  `main` it was MT8's placeholder. MT13's pack passes `load_pack_file`.
- **App merge: one `LegalLinks` type.** MT11 and MT13 each built one. The
  merged app keeps `TenantConfig.legal` (MT11) and adds MT13's rules to it:
  only https links are taken from the server, and the product's Terms link
  comes from the `TERMS_URL` define. `legal_links.dart` now holds only the
  product defaults (the `COMPANY_NAME`, `PRIVACY_URL`, `SUPPORT_URL` and
  `TERMS_URL` defines) and `copyrightLine(company)`.
- **App merge: About and login footer.** Both stay store-aware (MT11) and
  gain the Terms link plus "StoreOps · © <build year> <company>".
- **`AI_CONSENT_NOTICE`:** it is now a field in `config.py` and a line in
  `.env.example`. Before, `extra="ignore"` dropped it silently.
- **Blank `COMPANY_NAME`:** `settings_view.company_name()` returns the
  product default, matching `GET /api/app/legal`. A test checks that the
  `legal` block in branding equals the one from `/api/app/legal`.
- **App AI notice, per store:** `aiNoticeProvider` reads the notice from
  the store's `GET /api/app/legal` (`ai.notice`) and falls back to the
  default. `ask_bot_screen` sends `scope: <store code>`, so the saved
  answer key is `ai_consent_accepted.<CODE>.<version>`. Agreeing at one
  store no longer counts for another. The Privacy link in the notice is the
  store's own.
- **Ask bot example question:** the website and the app no longer name one
  of the origin store's vendors.
- **`test_demo_data` determinism:** the test now ignores
  `uploads/.store_identity`. MT8's store init writes a random instance id
  into that file on every build.

## As built - M2 final regression and handoff

Run on 2026-10-18 on the merged code. All work in `~/storeops`;
The pilot store's folders were only read (one read-only production copy for
parity and the migration check, deleted afterwards with its recordings).

**Merges (server `main`):** `6975c85` merges `m2/mt10-directory` (MT10 +
verifier fixes) and `b6518b3` merges `m2/mt13-demo` (MT13 + verifier fixes).
The only conflicts were in this file; every As built section is kept.
**App `main`:** `e5fd24f` merges `m2/mt13-legal` (no conflicts left after
the MT13 verifier's merge of `main`).

**Found and fixed by the simulator run (app):**
- `e90b209` **Sign-in failed on a fresh install.** After the store code,
  every request to the store server failed with "Unable to reach the store
  server": `apiClientProvider` captured `connectivityProvider`'s notifier
  with `ref.read`; the notifier is rebuilt with the store scope a frame after
  the store is chosen, so the client called a disposed notifier ("Tried to
  use ConnectivityNotifier after dispose") on every response, which Dio
  reported as a network error. Widget tests use fake clients and never saw
  it. The client now looks the notifier up at each report and a replaced
  notifier ignores reports. `test/core/network/api_client_connectivity_test.dart`
  (2 tests) fails on the old code.

**Other commits:**
- App `70b8758`: the Dart package is renamed `pilot_app` ->
  `storeops_app` (the single rename commit of section 8); the identity tests
  no longer exempt the old name. `7c3f20b`: staff names out of five `lib/`
  doc comments; the origin store's phone number and customer group out of
  test data; the checklist fixture generator reads the StoreOps website.
  `7189697`: `integration_test/m2_live_stores_test.dart`, the live two-store
  simulator run (skipped unless `E2E_LIVE=1`).
- Server `47dbda3`: the DEMO store joins `frontend/e2e/store/run.sh`
  (`WITH_DEMO=0` leaves it out). `c47fbaa`: Suite A/B use the `storeops_*`
  storage keys and read store names from the server. `c7dd2ae`, `63b9128`:
  the origin store's Google Form links and database names out of comments
  and script usage lines.

**Results:**

| Check | Result |
|---|---|
| Backend full suite, Python 3.9 / 3.12 (merged `main`) | 2687 passed, 20 skipped each |
| Alembic | single head `0062`; fresh DB up -> down to base -> up; production copy 0059 -> 0062 -> 0059 -> 0062, integrity ok |
| Parity (the pilot store pack vs `56e095c`, fresh production copy) | 114 files; 110 endpoints all 200; no number, row, alert or job differences. 10 differences, all in `glossary.json` help text (MT9's store-neutral wording); additions: `/me` `store_code` + `store_features`, table `store_identity` |
| Website `node --test` / `vite build` | 231/231 / OK |
| Directory Worker `node --test` | 7/7 |
| Playwright `frontend/e2e/store/run.sh` (Toast, Clover, DEMO; 1280 + 375) | 766 checks, 0 failures |
| App `flutter analyze` / full `flutter test --concurrency=2` | clean / 5490 passed, 6 skipped, 0 failed |
| Simulator, iPhone 17 and iPad Pro 11 (iOS 26.5), real local servers | store code -> branded sign-in -> gated Home/Store -> Switch store -> other store's branding, no stale data: all steps pass on both |

Simulator notes: Xcode's simulator device support is off on this Mac
(CoreSimulator older than Xcode; fixing it needs an admin "first launch"
install), so the run builds the test as a debug simulator app and drives it
with `simctl`. ML Kit has no arm64 simulator slice, so the simulator build is
made from a throwaway copy with a stub `google_mlkit_text_recognition`
(never committed). The local store directory is a stand-in serving
`http://127.0.0.1` addresses (the real one refuses http). On the iPhone the
test's own checks all pass; flutter_test then reports a SemanticsHandle the
simulator's accessibility left open (not app code).

**Expected, not bugs:** the Clover fake store shows Open Checks (the
contract's Clover list has `open_orders`; the server answers 409 "isn't
connected" until M3). The fake Toast store answers 503 for today's Items
Sold and 500 for `/api/orders/employees` because its POS is unreachable
(M1-era error mapping). 44 "Toast" strings on the Clover store's
notification settings page come from the server (MT7 follow-up).

**Name audit (tracked files):** server - `backend/app/config.py`
(`REFUSED_DOMAIN`), `checklist_builtin.py` (migration 0039 data), 5
migrations, the legacy-pack extractor and the parity README (both about the
origin store by design), 3 docs (this contract, the M1 contract and handoff),
6 e2e safety guards that refuse the production path, 29 test files. App -
`lib/core/tenant/api_host_policy.dart` (refused domain) and 49 test files
(legacy checklist fixtures, OCR fixtures). No staff names in either `lib/`
or `backend/app` except `checklist_builtin.py`'s migration data.
